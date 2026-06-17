from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from typing import List, Optional
from ..models import (
    CaseNote,
    Punishment,
    PunishmentType,
    WatchdogLog,
    JailedUser,
)
from datetime import datetime, timedelta
import discord
import logging

logger = logging.getLogger("discord_bot")


class ModerationMixin(BaseManager):
    async def count_punishments(
        self,
        user_id: int,
        guild_id: int,
        punishment_type: Optional[PunishmentType] = None,
        days: int = 0,
    ) -> int:
        """Count punishments for a user. If punishment_type is None, counts all types."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(func.count(Punishment.id)).where(
                    Punishment.user_id == user_id, Punishment.guild_id == guild_id
                )
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                if days > 0:
                    cutoff = discord.utils.utcnow() - timedelta(days=days)
                    query = query.where(Punishment.created_at > cutoff)
                result = await session.execute(query)
                return result.scalar_one()
        except SQLAlchemyError as e:
            logging.error(
                f"Error counting punishments for user {user_id} in guild {guild_id}: {e}"
            )
            return 0
    async def get_punishment_history(
        self,
        user_id: int,
        guild_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
        punishment_type: Optional[PunishmentType] = None,
    ) -> List[Punishment]:
        """Get punishment history for a user with optional filtering and pagination."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(Punishment).where(
                    Punishment.user_id == user_id, Punishment.guild_id == guild_id
                )
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                query = query.order_by(Punishment.created_at.desc())
                if offset > 0:
                    query = query.offset(offset)
                if limit:
                    query = query.limit(limit)
                result = await session.execute(query)
                return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(
                f"Error getting punishment history for user {user_id} in guild {guild_id}: {e}"
            )
            return []
    async def get_punishment(self, case_id: int, guild_id: int) -> Optional[Punishment]:
        """Retrieve a specific punishment by case ID in a guild."""
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(Punishment).where(
                        Punishment.case_id == case_id, Punishment.guild_id == guild_id
                    )
                )
                return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logging.error(
                f"Error retrieving punishment case {case_id} in guild {guild_id}: {e}"
            )
            return None
    async def get_punishment_by_id(self, punishment_id: int) -> Optional[Punishment]:
        """Retrieve a punishment by its database ID (not case_id)."""
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(Punishment).where(Punishment.id == punishment_id)
                )
                return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving punishment by id {punishment_id}: {e}")
            return None
    async def get_next_case_id(self, guild_id: int) -> int:
        """Get the next available case ID for a guild."""
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(func.max(Punishment.case_id)).where(
                        Punishment.guild_id == guild_id
                    )
                )
                max_case_id = result.scalar_one_or_none()
                return (max_case_id + 1) if max_case_id else 1
        except SQLAlchemyError as e:
            logging.error(f"Error getting next case ID for guild {guild_id}: {e}")
            return 1
    async def count_punishment_usage(
        self,
        moderator_id: int,
        guild_id: int,
        punishment_type: Optional[PunishmentType] = None,
        days: int = 0,
    ) -> int:
        """Count how many times a moderator used punishment commands."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(func.count(Punishment.id)).where(
                    Punishment.moderator_id == moderator_id,
                    Punishment.guild_id == guild_id,
                )
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                if days > 0:
                    cutoff = discord.utils.utcnow() - timedelta(days=days)
                    query = query.where(Punishment.created_at > cutoff)
                result = await session.execute(query)
                return result.scalar_one()
        except SQLAlchemyError as e:
            logging.error(
                f"Error counting punishment usage for moderator {moderator_id}: {e}"
            )
            return 0
    async def add_punishment(
        self,
        user_id: int,
        guild_id: int,
        moderator_id: int,
        punishment_type: PunishmentType,
        reason: str,
        duration: Optional[int] = None,
    ) -> Optional[int]:
        """Add a punishment record and return the case_id."""
        try:
            async with self.async_sessionmaker() as session:
                case_id = await self.get_next_case_id(guild_id)
                punishment = Punishment(
                    case_id=case_id,
                    user_id=user_id,
                    guild_id=guild_id,
                    moderator_id=moderator_id,
                    type=punishment_type,
                    reason=reason,
                    duration=duration,
                    created_at=discord.utils.utcnow(),
                )
                session.add(punishment)
                await session.commit()
                logger.info(
                    f"Added punishment case {case_id} for user {user_id} in guild {guild_id}"
                )
                return case_id
        except SQLAlchemyError as e:
            logging.error(f"Error adding punishment for user {user_id}: {e}")
            return None
    async def remove_punishment(self, case_id: int, guild_id: int) -> bool:
        """Remove a punishment by case_id. Returns True if successful."""
        try:
            async with self.async_sessionmaker() as session:
                stmt = delete(Punishment).where(
                    Punishment.case_id == case_id, Punishment.guild_id == guild_id
                )
                result = await session.execute(stmt)
                await session.commit()
                deleted = result.rowcount > 0
                if deleted:
                    logger.info(
                        f"Removed punishment case {case_id} from guild {guild_id}"
                    )
                return deleted
        except SQLAlchemyError as e:
            logging.error(f"Error removing punishment case {case_id}: {e}")
            return False
    async def update_punishment_moderator(
        self, case_id: int, guild_id: int, new_moderator_id: int
    ) -> bool:
        """Update the moderator for a punishment case. Returns True if successful."""
        try:
            async with self.async_sessionmaker() as session:
                stmt = (
                    update(Punishment)
                    .where(
                        Punishment.case_id == case_id, Punishment.guild_id == guild_id
                    )
                    .values(moderator_id=new_moderator_id)
                )
                result = await session.execute(stmt)
                await session.commit()
                updated = result.rowcount > 0
                if updated:
                    logger.info(
                        f"Updated moderator for case {case_id} to {new_moderator_id}"
                    )
                return updated
        except SQLAlchemyError as e:
            logging.error(f"Error updating moderator for case {case_id}: {e}")
            return False
    async def log_punishment_command(
        self, moderator_id: int, guild_id: int, punishment_type: PunishmentType
    ):
        try:
            async with self.async_sessionmaker() as session:
                log_entry = WatchdogLog(
                    moderator_id=moderator_id,
                    guild_id=guild_id,
                    punishment_type=punishment_type,
                )
                session.add(log_entry)
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error logging moderation command: {str(e)}")
    async def update_punishment_reason(
        self, case_id: int, guild_id: int, new_reason: str
    ) -> bool:
        """Update the reason for a punishment case. Returns True if successful."""
        try:
            async with self.async_sessionmaker() as session:
                stmt = (
                    update(Punishment)
                    .where(
                        Punishment.case_id == case_id, Punishment.guild_id == guild_id
                    )
                    .values(reason=new_reason)
                )
                result = await session.execute(stmt)
                await session.commit()
                updated = result.rowcount > 0
                if updated:
                    logger.info(f"Updated reason for case {case_id}")
                return updated
        except SQLAlchemyError as e:
            logging.error(f"Error updating reason for case {case_id}: {e}")
            return False
    async def add_case_note(
        self, case_id: int, guild_id: int, moderator_id: int, note: str
    ) -> bool:
        """Add a note to a punishment case. Returns True if successful."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Punishment).where(
                            Punishment.case_id == case_id,
                            Punishment.guild_id == guild_id,
                        )
                    )
                    punishment = result.scalar_one_or_none()

                    if not punishment:
                        logging.warning(
                            f"Punishment case {case_id} not found in guild {guild_id}"
                        )
                        return False

                    case_note = CaseNote(
                        punishment_id=punishment.id,
                        moderator_id=moderator_id,
                        note=note,
                        created_at=discord.utils.utcnow(),
                    )
                    session.add(case_note)
                    await session.commit()
                    logger.info(
                        f"Added note to case {case_id} by moderator {moderator_id}"
                    )
                    return True
        except SQLAlchemyError as e:
            logging.error(f"Error adding note to case {case_id}: {e}")
            return False
    async def get_case_notes(self, case_id: int, guild_id: int) -> List[CaseNote]:
        """Get all notes for a punishment case."""
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(Punishment).where(
                        Punishment.case_id == case_id, Punishment.guild_id == guild_id
                    )
                )
                punishment = result.scalar_one_or_none()

                if not punishment:
                    logging.warning(
                        f"Punishment case {case_id} not found in guild {guild_id}"
                    )
                    return []

                notes_result = await session.execute(
                    select(CaseNote)
                    .where(CaseNote.case_id == punishment.id)
                    .order_by(CaseNote.created_at.desc())
                )
                return notes_result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving notes for case {case_id}: {e}")
            return []
    async def get_active_punishments(
        self, guild_id: int, punishment_type: Optional[PunishmentType] = None
    ) -> List[Punishment]:
        """Get all active (non-expired) punishments in a guild."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(Punishment).where(Punishment.guild_id == guild_id)
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                query = query.order_by(Punishment.created_at.desc())
                result = await session.execute(query)
                return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error getting active punishments for guild {guild_id}: {e}")
            return []
    async def get_moderator_stats(
        self, moderator_id: int, guild_id: int, days: int = 0
    ) -> dict:
        """Get statistics about a moderator's punishment actions."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(
                    Punishment.type, func.count(Punishment.id).label("count")
                ).where(
                    Punishment.moderator_id == moderator_id,
                    Punishment.guild_id == guild_id,
                )
                if days > 0:
                    cutoff = discord.utils.utcnow() - timedelta(days=days)
                    query = query.where(Punishment.created_at > cutoff)
                query = query.group_by(Punishment.type)
                result = await session.execute(query)

                stats = {row.type.value: row.count for row in result}
                stats["total"] = sum(stats.values())
                return stats
        except SQLAlchemyError as e:
            logging.error(f"Error getting moderator stats for {moderator_id}: {e}")
            return {"total": 0}
    async def get_guild_punishment_stats(self, guild_id: int, days: int = 0) -> dict:
        """Get overall punishment statistics for a guild."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(
                    Punishment.type, func.count(Punishment.id).label("count")
                ).where(Punishment.guild_id == guild_id)
                if days > 0:
                    cutoff = discord.utils.utcnow() - timedelta(days=days)
                    query = query.where(Punishment.created_at > cutoff)
                query = query.group_by(Punishment.type)
                result = await session.execute(query)

                stats = {row.type.value: row.count for row in result}
                stats["total"] = sum(stats.values())
                return stats
        except SQLAlchemyError as e:
            logging.error(f"Error getting guild punishment stats for {guild_id}: {e}")
            return {"total": 0}
    async def bulk_add_punishments(self, punishments: List[dict]) -> int:
        """
        Bulk add multiple punishments at once.
        Each dict should contain: user_id, guild_id, moderator_id, punishment_type, reason, duration (optional)
        Returns the number of punishments successfully added.
        """
        try:
            async with self.async_sessionmaker() as session:
                added = 0
                for p in punishments:
                    case_id = await self.get_next_case_id(p["guild_id"])
                    punishment = Punishment(
                        case_id=case_id,
                        user_id=p["user_id"],
                        guild_id=p["guild_id"],
                        moderator_id=p["moderator_id"],
                        type=p["punishment_type"],
                        reason=p["reason"],
                        duration=p.get("duration"),
                        created_at=discord.utils.utcnow(),
                    )
                    session.add(punishment)
                    added += 1
                await session.commit()
                logger.info(f"Bulk added {added} punishments")
                return added
        except SQLAlchemyError as e:
            logging.error(f"Error bulk adding punishments: {e}")
            return 0
    async def search_punishments(
        self,
        guild_id: int,
        user_id: Optional[int] = None,
        moderator_id: Optional[int] = None,
        punishment_type: Optional[PunishmentType] = None,
        reason_contains: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Punishment]:
        """Advanced search for punishments with multiple filters."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(Punishment).where(Punishment.guild_id == guild_id)

                if user_id:
                    query = query.where(Punishment.user_id == user_id)
                if moderator_id:
                    query = query.where(Punishment.moderator_id == moderator_id)
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                if reason_contains:
                    query = query.where(Punishment.reason.ilike(f"%{reason_contains}%"))
                if start_date:
                    query = query.where(Punishment.created_at >= start_date)
                if end_date:
                    query = query.where(Punishment.created_at <= end_date)

                query = query.order_by(Punishment.created_at.desc()).limit(limit)
                result = await session.execute(query)
                return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error searching punishments: {e}")
            return []
    async def update_punishment_duration(
        self, case_id: int, guild_id: int, new_duration: int
    ) -> bool:
        """Update the duration of a punishment case. Returns True if successful."""
        try:
            async with self.async_sessionmaker() as session:
                stmt = (
                    update(Punishment)
                    .where(
                        Punishment.case_id == case_id, Punishment.guild_id == guild_id
                    )
                    .values(duration=new_duration)
                )
                result = await session.execute(stmt)
                await session.commit()
                updated = result.rowcount > 0
                if updated:
                    logger.info(
                        f"Updated duration for case {case_id} to {new_duration}s"
                    )
                return updated
        except SQLAlchemyError as e:
            logging.error(f"Error updating duration for case {case_id}: {e}")
            return False
    async def get_user_latest_punishment(
        self,
        user_id: int,
        guild_id: int,
        punishment_type: Optional[PunishmentType] = None,
    ) -> Optional[Punishment]:
        """Get the most recent punishment for a user."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(Punishment).where(
                    Punishment.user_id == user_id, Punishment.guild_id == guild_id
                )
                if punishment_type:
                    query = query.where(Punishment.type == punishment_type)
                query = query.order_by(Punishment.created_at.desc()).limit(1)
                result = await session.execute(query)
                return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logging.error(f"Error getting latest punishment for user {user_id}: {e}")
            return None
    async def get_punishment_caseid(self, guild_id: int) -> int:
        """Deprecated: Use get_next_case_id instead."""
        return await self.get_next_case_id(guild_id)
    async def change_punishment_moderator(
        self, case_id: int, new_moderator_id: int, guild_id: int = None
    ) -> bool:
        """Deprecated: Use update_punishment_moderator instead."""
        if guild_id is None:
            punishment = await self.get_punishment_by_id(case_id)
            if punishment:
                guild_id = punishment.guild_id
            else:
                logging.error(
                    f"Cannot change moderator: case {case_id} not found and guild_id not provided"
                )
                return False
        return await self.update_punishment_moderator(
            case_id, guild_id, new_moderator_id
        )
    async def add_jailed_user(
        self,
        guild_id: int,
        user_id: int,
        jailed_until: datetime = None,
        roles: list[int] = None,
    ):
        """
        If there is no existing JailedUser for (guild_id, user_id), INSERT a new row.
        If there *is* at least one, TAKE the most‐recent (by created_at), UPDATE its fields,
        and DELETE any other duplicates.
        """
        async with self.get_session() as session:
            stmt = (
                select(JailedUser)
                .where(JailedUser.guild_id == guild_id, JailedUser.user_id == user_id)
                .order_by(JailedUser.created_at.desc())
            )
            result = await session.execute(stmt)
            existing_rows = result.scalars().all()

            if existing_rows:
                newest = existing_rows[0]
                newest.jailed_until = jailed_until
                newest.roles = roles

                for duplicate in existing_rows[1:]:
                    await session.delete(duplicate)

                await session.commit()
                return newest

            new_entry = JailedUser(
                guild_id=guild_id,
                user_id=user_id,
                jailed_until=jailed_until,
                roles=roles,
            )
            session.add(new_entry)
            await session.commit()
            return new_entry
    async def remove_jailed_user(self, guild_id: int, user_id: int):
        """
        Delete _every_ row for (guild_id, user_id).
        If there were duplicates, they all get removed in one go.
        """
        async with self.get_session() as session:
            await session.execute(
                delete(JailedUser).where(
                    JailedUser.guild_id == guild_id, JailedUser.user_id == user_id
                )
            )
            await session.commit()
    async def get_jailed_user(self, guild_id: int, user_id: int) -> JailedUser | None:
        """
        Return the single “most recent” JailedUser row for (guild_id, user_id).
        If more than one exists, delete all but the newest, then return the newest.
        """
        async with self.get_session() as session:
            stmt = (
                select(JailedUser)
                .where(JailedUser.guild_id == guild_id, JailedUser.user_id == user_id)
                .order_by(JailedUser.created_at.desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            if not rows:
                return None

            newest = rows[0]
            for duplicate in rows[1:]:
                await session.delete(duplicate)

            if len(rows) > 1:
                await session.commit()

            return newest
