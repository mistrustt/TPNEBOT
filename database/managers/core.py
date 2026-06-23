from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from typing import List, Optional
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
)
from datetime import datetime, timezone
import discord
import logging

logger = logging.getLogger("discord_bot")


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
