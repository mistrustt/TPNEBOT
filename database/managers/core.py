from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, case, text
from typing import List, Optional
from utils.security import raise_if_url
from ..models import (
    BotConfig,
    ServerSettings,
    CommandStatus,
    CommandCooldown,
    CommandUsageDaily,
    CommandLatencyDaily,
    CommandErrorDaily,
    DailyUserExposure,
    Blacklist,
    Task,
    OwnerAuditLog,
)
from datetime import datetime, timezone, timedelta
import discord
import logging

logger = logging.getLogger("discord.client")


class CoreMixin(BaseManager):
    async def load_config(self):
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(BotConfig))
            return result.scalar_one_or_none()

    async def is_initial_setup_complete(self) -> bool:
        """
        Check if the bot's initial setup has been completed.

        Returns:
            bool: True if setup is complete, False otherwise
        """
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(select(BotConfig))
                config = result.scalar_one_or_none()
                return config.setup_complete if config else False
        except SQLAlchemyError as e:
            logging.error(f"Error checking setup status: {e}")
            return False

    async def set_bot_id(self, bot_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(select(BotConfig))
                config = result.scalar_one_or_none()
                if config:
                    config.bot_id = bot_id
                else:
                    config = BotConfig(bot_id=bot_id)
                    session.add(config)
                await session.commit()

    async def get_bot_id(self) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(BotConfig))
            config = result.scalar_one_or_none()

            if not config or not config.bot_id:
                setup_complete = await self.is_initial_setup_complete()
                if not setup_complete:
                    raise ValueError(
                        "Initial bot setup has not been completed. Please run setup first."
                    )
                return None

            return config.bot_id

    async def get_loaded_cogs(self) -> List[str]:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(BotConfig))
            bot_config = result.scalar_one_or_none()
            return bot_config.loaded_cogs if bot_config else []

    async def load_cog(self, cog_name: str):
        """Mark a cog as loaded in the database."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(select(BotConfig))
                bot_config = result.scalar_one_or_none()
                if bot_config:
                    loaded_cogs = bot_config.loaded_cogs or []
                    unloaded_cogs = bot_config.unloaded_cogs or []
                    if cog_name not in loaded_cogs:
                        loaded_cogs.append(cog_name)
                    if cog_name in unloaded_cogs:
                        unloaded_cogs.remove(cog_name)
                    bot_config.loaded_cogs = loaded_cogs
                    bot_config.unloaded_cogs = unloaded_cogs
                else:
                    bot_config = BotConfig(loaded_cogs=[cog_name], unloaded_cogs=[])
                    session.add(bot_config)
                await session.commit()

    async def get_unloaded_cogs(self) -> List[str]:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(BotConfig))
            bot_config = result.scalar_one_or_none()
            return bot_config.unloaded_cogs if bot_config else []

    async def unload_cog(self, cog_name: str):
        """Mark a cog as unloaded in the database."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(select(BotConfig))
                bot_config = result.scalar_one_or_none()
                if bot_config:
                    loaded_cogs = bot_config.loaded_cogs or []
                    unloaded_cogs = bot_config.unloaded_cogs or []
                    if cog_name not in unloaded_cogs:
                        unloaded_cogs.append(cog_name)
                    if cog_name in loaded_cogs:
                        loaded_cogs.remove(cog_name)
                    bot_config.loaded_cogs = loaded_cogs
                    bot_config.unloaded_cogs = unloaded_cogs
                else:
                    bot_config = BotConfig(loaded_cogs=[], unloaded_cogs=[cog_name])
                    session.add(bot_config)
                await session.commit()

    async def record_command_usage(
        self,
        *,
        command_name: str,
        guild_id: Optional[int],
        user_hash: Optional[str],
        is_slash: bool,
        used_at: Optional[datetime] = None,
    ) -> None:
        try:
            used_at = used_at or discord.utils.utcnow()
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            bucket_date = used_at.date()
            async with self.async_sessionmaker() as session:
                stmt = select(CommandUsageDaily).where(
                    CommandUsageDaily.bucket_date == bucket_date,
                    CommandUsageDaily.command_name == command_name,
                    CommandUsageDaily.guild_id == guild_id,
                    CommandUsageDaily.user_hash == user_hash,
                    CommandUsageDaily.is_slash == is_slash,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    row.count += 1
                    row.last_used_at = used_at
                else:
                    session.add(
                        CommandUsageDaily(
                            bucket_date=bucket_date,
                            command_name=command_name,
                            guild_id=guild_id,
                            user_hash=user_hash,
                            is_slash=is_slash,
                            count=1,
                            last_used_at=used_at,
                        )
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error recording command usage: {str(e)}")

    async def record_command_latency(
        self,
        *,
        command_name: str,
        guild_id: Optional[int],
        user_hash: Optional[str],
        is_slash: bool,
        latency_ms: int,
        used_at: Optional[datetime] = None,
    ) -> None:
        try:
            used_at = used_at or discord.utils.utcnow()
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            bucket_date = used_at.date()
            async with self.async_sessionmaker() as session:
                stmt = select(CommandLatencyDaily).where(
                    CommandLatencyDaily.bucket_date == bucket_date,
                    CommandLatencyDaily.command_name == command_name,
                    CommandLatencyDaily.guild_id == guild_id,
                    CommandLatencyDaily.user_hash == user_hash,
                    CommandLatencyDaily.is_slash == is_slash,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    row.latency_ms_sum += latency_ms
                    row.latency_count += 1
                    row.last_used_at = used_at
                else:
                    session.add(
                        CommandLatencyDaily(
                            bucket_date=bucket_date,
                            command_name=command_name,
                            guild_id=guild_id,
                            user_hash=user_hash,
                            is_slash=is_slash,
                            latency_ms_sum=latency_ms,
                            latency_count=1,
                            last_used_at=used_at,
                        )
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error recording command latency: {str(e)}")

    async def record_command_error(
        self,
        *,
        command_name: str,
        guild_id: Optional[int],
        user_hash: Optional[str],
        is_slash: bool,
        error_type: str,
        used_at: Optional[datetime] = None,
    ) -> None:
        try:
            used_at = used_at or discord.utils.utcnow()
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            bucket_date = used_at.date()
            async with self.async_sessionmaker() as session:
                stmt = select(CommandErrorDaily).where(
                    CommandErrorDaily.bucket_date == bucket_date,
                    CommandErrorDaily.command_name == command_name,
                    CommandErrorDaily.guild_id == guild_id,
                    CommandErrorDaily.user_hash == user_hash,
                    CommandErrorDaily.is_slash == is_slash,
                    CommandErrorDaily.error_type == error_type,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    row.count += 1
                    row.last_seen_at = used_at
                else:
                    session.add(
                        CommandErrorDaily(
                            bucket_date=bucket_date,
                            command_name=command_name,
                            guild_id=guild_id,
                            user_hash=user_hash,
                            is_slash=is_slash,
                            error_type=error_type,
                            count=1,
                            last_seen_at=used_at,
                        )
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error recording command error: {str(e)}")

    async def record_user_exposure(
        self,
        *,
        guild_id: Optional[int],
        user_hash: str,
        seen_at: Optional[datetime] = None,
    ) -> None:
        try:
            seen_at = seen_at or discord.utils.utcnow()
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            bucket_date = seen_at.date()
            async with self.async_sessionmaker() as session:
                stmt = select(DailyUserExposure).where(
                    DailyUserExposure.bucket_date == bucket_date,
                    DailyUserExposure.guild_id == guild_id,
                    DailyUserExposure.user_hash == user_hash,
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    return
                session.add(
                    DailyUserExposure(
                        bucket_date=bucket_date,
                        guild_id=guild_id,
                        user_hash=user_hash,
                        first_seen_at=seen_at,
                    )
                )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error recording user exposure: {str(e)}")

    async def record_owner_command(
        self,
        *,
        user_id: int,
        command_name: str,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        args: Optional[dict] = None,
    ) -> None:
        """
        Record a successfully executed owner-only command invocation.

        The ``args`` dict should already be redacted/sanitized; this method does
        not scrub secrets or PII on its own.
        """
        try:
            user_hash = self.hash_user_id(user_id)
            created_at = discord.utils.utcnow()
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            async with self.async_sessionmaker() as session:
                session.add(
                    OwnerAuditLog(
                        user_id=user_hash,
                        command_name=command_name,
                        guild_id=guild_id,
                        channel_id=channel_id,
                        args=args,
                        created_at=created_at,
                    )
                )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error recording owner command audit: {str(e)}")

    async def get_owner_audit_log(
        self,
        *,
        user_id: Optional[int] = None,
        command_name: Optional[str] = None,
        guild_id: Optional[int] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
    ) -> list[OwnerAuditLog]:
        """Return recent owner-only command audit log entries.

        Results are ordered newest first. The ``user_id`` filter matches the
        hashed Discord ID stored in the table.
        """
        try:
            async with self.async_sessionmaker() as session:
                stmt = select(OwnerAuditLog)
                if user_id is not None:
                    stmt = stmt.where(
                        OwnerAuditLog.user_id == self.hash_user_id(user_id)
                    )
                if command_name is not None:
                    stmt = stmt.where(OwnerAuditLog.command_name == command_name)
                if guild_id is not None:
                    stmt = stmt.where(OwnerAuditLog.guild_id == guild_id)
                if before is not None:
                    stmt = stmt.where(OwnerAuditLog.created_at < before)
                if after is not None:
                    stmt = stmt.where(OwnerAuditLog.created_at > after)
                stmt = stmt.order_by(OwnerAuditLog.created_at.desc()).limit(
                    max(1, min(limit, 500))
                )
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except SQLAlchemyError as e:
            logging.error(f"Error fetching owner command audit log: {str(e)}")
            return []

    async def purge_stats_before(self, cutoff_date) -> None:
        try:
            async with self.async_sessionmaker() as session:
                await session.execute(
                    delete(CommandUsageDaily).where(
                        CommandUsageDaily.bucket_date < cutoff_date
                    )
                )
                await session.execute(
                    delete(CommandLatencyDaily).where(
                        CommandLatencyDaily.bucket_date < cutoff_date
                    )
                )
                await session.execute(
                    delete(CommandErrorDaily).where(
                        CommandErrorDaily.bucket_date < cutoff_date
                    )
                )
                await session.execute(
                    delete(DailyUserExposure).where(
                        DailyUserExposure.bucket_date < cutoff_date
                    )
                )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error purging stats before {cutoff_date}: {str(e)}")

    async def get_metrics_overview(
        self, days: int, guild_id: Optional[int] = None
    ) -> dict:
        """Return aggregate metrics for the dashboard overview."""
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                usage_filters = [CommandUsageDaily.bucket_date >= cutoff]
                latency_filters = [CommandLatencyDaily.bucket_date >= cutoff]
                error_filters = [CommandErrorDaily.bucket_date >= cutoff]
                exposure_filters = [DailyUserExposure.bucket_date >= cutoff]
                if guild_id is not None:
                    usage_filters.append(CommandUsageDaily.guild_id == guild_id)
                    latency_filters.append(CommandLatencyDaily.guild_id == guild_id)
                    error_filters.append(CommandErrorDaily.guild_id == guild_id)
                    exposure_filters.append(DailyUserExposure.guild_id == guild_id)

                usage_stmt = (
                    select(
                        func.sum(CommandUsageDaily.count).label("total"),
                        func.sum(
                            case((CommandUsageDaily.is_slash.is_(True), CommandUsageDaily.count), else_=0)
                        ).label("slash"),
                    )
                    .where(*usage_filters)
                )
                latency_stmt = (
                    select(
                        func.sum(CommandLatencyDaily.latency_ms_sum).label("sum_ms"),
                        func.sum(CommandLatencyDaily.latency_count).label("count"),
                    )
                    .where(*latency_filters)
                )
                error_stmt = (
                    select(func.sum(CommandErrorDaily.count).label("total"))
                    .where(*error_filters)
                )
                exposure_stmt = (
                    select(
                        func.count(func.distinct(DailyUserExposure.user_hash)).label(
                            "unique"
                        ),
                        func.count().label("rows"),
                    )
                    .where(*exposure_filters)
                )
                slowest_stmt = (
                    select(
                        CommandLatencyDaily.command_name,
                        (
                            func.sum(CommandLatencyDaily.latency_ms_sum)
                            / func.nullif(func.sum(CommandLatencyDaily.latency_count), 0)
                        ).label("avg_ms"),
                    )
                    .where(*latency_filters)
                    .group_by(CommandLatencyDaily.command_name)
                    .order_by(text("avg_ms DESC"))
                    .limit(1)
                )
                worst_cmd_stmt = (
                    select(
                        CommandErrorDaily.command_name,
                        func.sum(CommandErrorDaily.count).label("err_total"),
                    )
                    .where(*error_filters)
                    .group_by(CommandErrorDaily.command_name)
                    .order_by(text("err_total DESC"))
                    .limit(1)
                )

                usage_row = (await session.execute(usage_stmt)).first()
                latency_row = (await session.execute(latency_stmt)).first()
                error_row = (await session.execute(error_stmt)).first()
                exposure_row = (await session.execute(exposure_stmt)).first()
                slowest_row = (await session.execute(slowest_stmt)).first()
                worst_row = (await session.execute(worst_cmd_stmt)).first()

                total = int(usage_row.total or 0) if usage_row else 0
                slash = int(usage_row.slash or 0) if usage_row else 0
                prefix = total - slash
                lat_sum = int(latency_row.sum_ms or 0) if latency_row else 0
                lat_count = int(latency_row.count or 0) if latency_row else 0
                avg_latency = int(lat_sum / lat_count) if lat_count else 0
                errors = int(error_row.total or 0) if error_row else 0
                unique = int(exposure_row.unique or 0) if exposure_row else 0
                rows = int(exposure_row.rows or 0) if exposure_row else 0

                return {
                    "total_usage": total,
                    "slash_usage": slash,
                    "prefix_usage": prefix,
                    "slash_pct": (slash / total * 100) if total else 0,
                    "avg_latency_ms": avg_latency,
                    "latency_calls": lat_count,
                    "total_errors": errors,
                    "error_rate": (errors / total * 100) if total else 0,
                    "unique_users": unique,
                    "exposure_rows": rows,
                    "slowest_command": slowest_row.command_name if slowest_row else None,
                    "slowest_avg_ms": int(slowest_row.avg_ms or 0) if slowest_row else 0,
                    "worst_command": worst_row.command_name if worst_row else None,
                    "worst_errors": int(worst_row.err_total or 0) if worst_row else 0,
                }
        except SQLAlchemyError as e:
            logger.error(f"Error fetching metrics overview: {e}")
            return {}

    async def get_slash_adoption(
        self, days: int, guild_id: Optional[int] = None
    ) -> list[tuple[str, int, int]]:
        """Return per-day slash vs prefix call counts.

        Returns tuples of (date_label, slash_count, prefix_count).
        """
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                filters = [CommandUsageDaily.bucket_date >= cutoff]
                if guild_id is not None:
                    filters.append(CommandUsageDaily.guild_id == guild_id)
                stmt = (
                    select(
                        CommandUsageDaily.bucket_date,
                        func.sum(
                            case((CommandUsageDaily.is_slash.is_(True), CommandUsageDaily.count), else_=0)
                        ).label("slash"),
                        func.sum(
                            case((CommandUsageDaily.is_slash.is_(False), CommandUsageDaily.count), else_=0)
                        ).label("prefix"),
                    )
                    .where(*filters)
                    .group_by(CommandUsageDaily.bucket_date)
                    .order_by(CommandUsageDaily.bucket_date.asc())
                )
                result = await session.execute(stmt)
                return [
                    (row.bucket_date.strftime("%Y-%m-%d"), int(row.slash or 0), int(row.prefix or 0))
                    for row in result.all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching slash adoption: {e}")
            return []

    async def get_error_rate_by_command(
        self, days: int, guild_id: Optional[int] = None, limit: int = 15
    ) -> list[tuple[str, int, int, float]]:
        """Return command reliability: (command_name, usage, errors, error_rate%)."""
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                usage_filters = [CommandUsageDaily.bucket_date >= cutoff]
                error_filters = [CommandErrorDaily.bucket_date >= cutoff]
                if guild_id is not None:
                    usage_filters.append(CommandUsageDaily.guild_id == guild_id)
                    error_filters.append(CommandErrorDaily.guild_id == guild_id)

                usage_subq = (
                    select(
                        CommandUsageDaily.command_name,
                        func.sum(CommandUsageDaily.count).label("total"),
                    )
                    .where(*usage_filters)
                    .group_by(CommandUsageDaily.command_name)
                    .subquery()
                )
                error_subq = (
                    select(
                        CommandErrorDaily.command_name,
                        func.sum(CommandErrorDaily.count).label("err_total"),
                    )
                    .where(*error_filters)
                    .group_by(CommandErrorDaily.command_name)
                    .subquery()
                )
                stmt = (
                    select(
                        usage_subq.c.command_name,
                        usage_subq.c.total,
                        func.coalesce(error_subq.c.err_total, 0).label("errors"),
                    )
                    .join(
                        error_subq,
                        usage_subq.c.command_name == error_subq.c.command_name,
                        isouter=True,
                    )
                    .order_by(text("errors DESC"))
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()
                return [
                    (
                        row.command_name,
                        int(row.total or 0),
                        int(row.errors or 0),
                        (row.errors / row.total * 100) if row.total else 0,
                    )
                    for row in rows
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching error rates: {e}")
            return []

    async def get_top_erroring_users(
        self, days: int, guild_id: Optional[int] = None, limit: int = 10
    ) -> list[tuple[str, int]]:
        """Return top users by anonymous error count.

        Returns tuples of (short_user_hash, error_count).
        """
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                filters = [CommandErrorDaily.bucket_date >= cutoff]
                if guild_id is not None:
                    filters.append(CommandErrorDaily.guild_id == guild_id)
                stmt = (
                    select(
                        CommandErrorDaily.user_hash,
                        func.sum(CommandErrorDaily.count).label("total"),
                    )
                    .where(*filters)
                    .group_by(CommandErrorDaily.user_hash)
                    .order_by(text("total DESC"))
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [
                    ((row.user_hash or "unknown")[:12], int(row.total or 0))
                    for row in result.all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching top erroring users: {e}")
            return []

    async def get_user_exposure_series(
        self, days: int, guild_id: Optional[int] = None
    ) -> list[tuple[str, int]]:
        """Return daily unique user counts (DAU).

        Returns tuples of (date_label, unique_users).
        """
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                filters = [DailyUserExposure.bucket_date >= cutoff]
                if guild_id is not None:
                    filters.append(DailyUserExposure.guild_id == guild_id)
                stmt = (
                    select(
                        DailyUserExposure.bucket_date,
                        func.count(func.distinct(DailyUserExposure.user_hash)).label(
                            "unique"
                        ),
                    )
                    .where(*filters)
                    .group_by(DailyUserExposure.bucket_date)
                    .order_by(DailyUserExposure.bucket_date.asc())
                )
                result = await session.execute(stmt)
                return [
                    (row.bucket_date.strftime("%Y-%m-%d"), int(row.unique or 0))
                    for row in result.all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching user exposure series: {e}")
            return []

    async def get_command_latency_trend(
        self,
        command_name: str,
        days: int,
        guild_id: Optional[int] = None,
    ) -> list[tuple[str, int, int]]:
        """Return per-day latency for a specific command.

        Returns tuples of (date_label, avg_ms, call_count).
        """
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                filters = [
                    CommandLatencyDaily.bucket_date >= cutoff,
                    CommandLatencyDaily.command_name == command_name,
                ]
                if guild_id is not None:
                    filters.append(CommandLatencyDaily.guild_id == guild_id)
                stmt = (
                    select(
                        CommandLatencyDaily.bucket_date,
                        CommandLatencyDaily.latency_ms_sum,
                        CommandLatencyDaily.latency_count,
                    )
                    .where(*filters)
                    .group_by(CommandLatencyDaily.bucket_date)
                    .order_by(CommandLatencyDaily.bucket_date.asc())
                )
                result = await session.execute(stmt)
                return [
                    (
                        row.bucket_date.strftime("%Y-%m-%d"),
                        int(row.latency_ms_sum / row.latency_count) if row.latency_count else 0,
                        int(row.latency_count or 0),
                    )
                    for row in result.all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching latency trend: {e}")
            return []

    async def get_command_usage_trend(
        self,
        command_name: str,
        days: int,
        guild_id: Optional[int] = None,
    ) -> list[tuple[str, int]]:
        """Return per-day usage for a specific command.

        Returns tuples of (date_label, total_calls).
        """
        try:
            cutoff = (discord.utils.utcnow().date() - timedelta(days=days))
            async with self.async_sessionmaker() as session:
                filters = [
                    CommandUsageDaily.bucket_date >= cutoff,
                    CommandUsageDaily.command_name == command_name,
                ]
                if guild_id is not None:
                    filters.append(CommandUsageDaily.guild_id == guild_id)
                stmt = (
                    select(
                        CommandUsageDaily.bucket_date,
                        func.sum(CommandUsageDaily.count).label("total"),
                    )
                    .where(*filters)
                    .group_by(CommandUsageDaily.bucket_date)
                    .order_by(CommandUsageDaily.bucket_date.asc())
                )
                result = await session.execute(stmt)
                return [
                    (row.bucket_date.strftime("%Y-%m-%d"), int(row.total or 0))
                    for row in result.all()
                ]
        except SQLAlchemyError as e:
            logger.error(f"Error fetching usage trend: {e}")
            return []

    async def set_prefix(self, guild_id: int, prefix: str):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).where(ServerSettings.guild_id == guild_id)
                )
                settings = result.scalar_one_or_none()
                if settings:
                    settings.prefix = prefix
                else:
                    settings = ServerSettings(guild_id=guild_id, prefix=prefix)
                    session.add(settings)
                await session.commit()

    async def get_prefix(self, guild_id: int) -> str:
        """Retrieve the prefix for a specific guild from the database."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.prefix).where(ServerSettings.guild_id == guild_id)
            )
            prefix = result.scalar_one_or_none()

        return prefix if prefix else "!"

    async def fetch_command_data(
        self, user_id: int, command_name: str, channel_id: int = None
    ):
        """Fetch the user blacklist status, command cooldown, and command enabled status in a single query."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            blacklist_subquery = (
                select(Blacklist.user_id).where(Blacklist.user_id == user_id).exists()
            )

            cooldown_subquery = (
                select(CommandCooldown.cooldown_expiry)
                .where(
                    CommandCooldown.user_id == user_id,
                    CommandCooldown.command_name == command_name,
                )
                .scalar_subquery()
            )

            global_command_status_subquery = (
                select(CommandStatus.enabled)
                .where(
                    CommandStatus.command_name == command_name,
                    CommandStatus.channel_id.is_(None),
                )
                .scalar_subquery()
            )

            channel_command_status_subquery = (
                select(CommandStatus.enabled)
                .where(
                    CommandStatus.command_name == command_name,
                    CommandStatus.channel_id == channel_id,
                )
                .scalar_subquery()
            )

            query = select(
                blacklist_subquery.label("is_blacklisted"),
                cooldown_subquery.label("cooldown_expiry"),
                global_command_status_subquery.label("is_command_enabled_globally"),
                channel_command_status_subquery.label("is_command_enabled_channel"),
            )

            result = await session.execute(query)
            row = result.one()

            return {
                "is_blacklisted": row.is_blacklisted,
                "cooldown_expiry": row.cooldown_expiry,
                "is_command_enabled_globally": row.is_command_enabled_globally,
                "is_command_enabled_channel": row.is_command_enabled_channel,
            }

    async def add_task(self, user_id: int, task: str) -> Task:
        raise_if_url(task, "task")
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count()).select_from(Task).where(Task.user_id == user_id)
            )
            count = result.scalar() or 0
            new_order = count + 1

            new_task = Task(
                user_id=user_id, task=task, completed=False, order_index=new_order
            )
            session.add(new_task)
            await session.commit()
            return new_task

    async def get_tasks(self, user_id: int) -> list[Task]:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Task).where(Task.user_id == user_id).order_by(Task.order_index)
            )
            return result.scalars().all()

    async def complete_task(self, user_id: int, task_order: int) -> bool:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            stmt = (
                update(Task)
                .where(Task.user_id == user_id, Task.order_index == task_order)
                .values(completed=True)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_task(self, user_id: int, task_order: int) -> bool:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Task).where(
                    Task.user_id == user_id, Task.order_index == task_order
                )
            )
            task_to_delete = result.scalar_one_or_none()
            if not task_to_delete:
                return False

            await session.delete(task_to_delete)
            await session.commit()

            stmt = (
                update(Task)
                .where(Task.user_id == user_id, Task.order_index > task_order)
                .values(order_index=Task.order_index - 1)
            )
            await session.execute(stmt)
            await session.commit()
            return True

    async def edit_task(self, user_id: int, task_order: int, new_task: str) -> bool:
        raise_if_url(new_task, "task")
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            stmt = (
                update(Task)
                .where(Task.user_id == user_id, Task.order_index == task_order)
                .values(task=new_task)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def clear_tasks(self, user_id: int) -> int:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(delete(Task).where(Task.user_id == user_id))
            await session.commit()
            return result.rowcount
