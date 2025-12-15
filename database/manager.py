from sqlalchemy.future import select
from sqlalchemy import update, delete, text, exists, case, literal_column
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from typing import List, Optional, Tuple
import hashlib
import time
import secrets
import os
import aiohttp
from .models import (
    Base,
    BotConfig,
    ServerSettings,
    LastFMusers,
    BankAccount,
    UserTimezone,
    UserLocation,
    FavoriteSongs,
    CaseNote,
    LastFMvotes,
    Punishment,
    PunishmentType,
    WatchdogLog,
    JailedUser,
    CommandStatus,
    CommandCooldown,
    Blacklist,
    BoosterRole,
    Transaction,
    CryptoAsset,
    CryptoPrice,
    Supply,
    Reputation,
    Wallet,
    Block,
    Item,
    ItemType,
    ShopItem,
    Bounty,
    Skulls,
    Flames,
    Hearts,
    Clowns,
    Sobs,
    ReactionSettings,
    GameStats,
    HeardleGameStats,
    GameHistory,
    UserRoleHistory,
    Streak,
    Task,
    JTCSettings,
    TempVoiceChannel,
    UserNameHistory,
    MinesSettings,
    LockdownChannel,
    Juul,
    UserAlt,
)
from .blockchain import Blockchain, KeyManager
from datetime import datetime, timedelta, timezone
import discord
import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("discord_bot")

ADMIN_IDS = {284439598422163476, 538773310704582666, 657182369240973312}  # Owner IDs

_LAST_REBALANCE_AT: Optional[datetime] = None  # module-level memo


class DatabaseManager:
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, echo=False)
        self.async_sessionmaker = sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.blockchain = Blockchain(self.async_sessionmaker)
        self.keymanager = KeyManager()

    async def initialize(self):
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await self.blockchain.create_genesis_block()

            await self.blockchain.load_blockchain_if_exists()
            await self.create_tables()
        except SQLAlchemyError as e:
            logging.error(f"Error initializing database: {str(e)}")

    def get_session(self):
        """Provide a transactional scope around a series of operations."""
        return self.async_sessionmaker()

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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
                    if cog_name not in bot_config.loaded_cogs:
                        bot_config.loaded_cogs.append(cog_name)
                    if cog_name in bot_config.unloaded_cogs:
                        bot_config.unloaded_cogs.remove(cog_name)
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
                    if cog_name not in bot_config.unloaded_cogs:
                        bot_config.unloaded_cogs.append(cog_name)
                    if cog_name in bot_config.loaded_cogs:
                        bot_config.loaded_cogs.remove(cog_name)
                else:
                    bot_config = BotConfig(loaded_cogs=[], unloaded_cogs=[cog_name])
                    session.add(bot_config)
                await session.commit()

    async def get_lastfm_usernames(self):
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(LastFMusers.lastfm_username))
            return result.scalars().all()

    async def get_lastfm_username(self, discord_id: str) -> str:
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers.lastfm_username).filter_by(discord_id=discord_id)
                )
                username = result.scalar_one_or_none()
                if username is None:
                    logging.info(
                        f"User {discord_id} has no last.fm account and will not be indexed."
                    )
                    raise ValueError(f"User {discord_id} has no last.fm account.")
                return username
        except SQLAlchemyError as e:
            logging.error(f"Error fetching LastFM username for {discord_id}: {e}")
            raise

    async def set_lastfm_username(self, discord_id: str, username: str) -> None:
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(LastFMusers).where(LastFMusers.discord_id == discord_id)
                    )

                    new_user = LastFMusers(
                        discord_id=discord_id, lastfm_username=username
                    )
                    session.add(new_user)
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error setting LastFM username: {str(e)}")

    async def get_lastfm_embed_color(self, discord_id: str) -> discord.Color:
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers.embed_color).filter_by(discord_id=discord_id)
                )
                color = result.scalar_one_or_none()
                if color:
                    logging.info(f"Retrieved color for {discord_id}: {color}")
                    return discord.Color(int(color.replace("#", "0x"), 16))
                else:
                    logging.info(f"No color set for {discord_id}, using default green.")
                    return discord.Color.green()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving color: {str(e)}")
            return discord.Color.green()

    async def set_lastfm_embed_color(self, discord_id: str, color: str) -> None:
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers).filter_by(discord_id=discord_id)
                )
                user = result.scalar_one_or_none()

                if user and user.lastfm_username:
                    user.embed_color = color
                    logging.info(
                        f"Updated Last.fm embed color for {discord_id} to {color}"
                    )
                else:
                    raise ValueError(
                        "Please set your Last.fm username before setting an embed color."
                    )

                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error setting LastFM embed color: {str(e)}")

    async def log_vote(self, user_id: str, command: str, vote: str):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    if vote == "up":
                        stmt = (
                            update(LastFMvotes)
                            .where(
                                LastFMvotes.discord_id == user_id,
                                LastFMvotes.command == command,
                            )
                            .values(upvotes=LastFMvotes.upvotes + 1)
                        )
                    elif vote == "down":
                        stmt = (
                            update(LastFMvotes)
                            .where(
                                LastFMvotes.discord_id == user_id,
                                LastFMvotes.command == command,
                            )
                            .values(downvotes=LastFMvotes.downvotes + 1)
                        )

                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_vote = LastFMvotes(discord_id=user_id, command=command)
                        if vote == "up":
                            new_vote.upvotes = 1
                        elif vote == "down":
                            new_vote.downvotes = 1
                        session.add(new_vote)

                await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def get_vote_stats(self, user_id: str, command: str):
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMvotes.upvotes, LastFMvotes.downvotes).filter_by(
                        discord_id=user_id, command=command
                    )
                )
                stats = result.first()
                return stats if stats else (0, 0)
        except SQLAlchemyError as e:
            return 0

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
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
                    created_at=datetime.now(timezone.utc),
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
        self, moderator_id: int, guild_id: int, command_name: PunishmentType
    ):
        try:
            async with self.async_sessionmaker() as session:
                log_entry = WatchdogLog(
                    moderator_id=moderator_id,
                    guild_id=guild_id,
                    command_name=command_name,
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
                        created_at=datetime.now(timezone.utc),
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
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
                        created_at=datetime.now(timezone.utc),
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

    async def set_watchdog_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.watchdog_channel_id = channel_id
                else:
                    setting = ServerSettings(
                        guild_id=guild_id, watchdog_channel_id=channel_id
                    )
                    session.add(setting)
                await session.commit()

    async def set_spam_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.spam_channel_id = channel_id
                else:
                    setting = ServerSettings(
                        guild_id=guild_id, spam_channel_id=channel_id
                    )
                    session.add(setting)
                await session.commit()

    async def get_spam_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.spam_channel_id).filter_by(guild_id=guild_id)
            )
            return result.scalar_one_or_none()

    async def set_watchdog_enabled(self, guild_id: int, bool: str):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    settings.watchdog_enabled = bool
                else:
                    settings = ServerSettings(guild_id=guild_id, watchdog_enabled=bool)
                    session.add(settings)
                await session.commit()

    async def get_server_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def initialize_supply_record(self):
        """
        Creates a default supply record if it doesn't exist.
        This is useful for a fresh economy where no supply record is present.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                supply = await session.get(Supply, 1)
                if not supply:
                    amount = Decimal("1000000000000.00")
                    supply = Supply(
                        id=1,
                        total_supply=Decimal("1000000000000.00"),
                        circulating=Decimal("0.00"),
                        treasury=Decimal("1000000000000.00"),
                    )
                    await self.blockchain.bootstrap_blockchain()
                    session.add(supply)
                    logging.info("Created default supply record.")

    async def _atomic_balance_change(
        self,
        session,
        table,
        pk_field,
        pk_value,
        delta: Decimal,
        *,
        balance_col: str = "balance",
        frozen_field: str | None = None,
    ):
        """
        Atomically add `delta` (can be negative) to `balance` column of `table`
        and guarantee non-negative result. Raises on race or freeze.
        """
        params = {"pk_val": pk_value, "delta": delta}

        frozen_sql = f"AND {frozen_field} IS FALSE" if frozen_field else ""
        stmt = text(f"""
            UPDATE {table}
            SET    {balance_col} = {balance_col} + :delta
            WHERE  {pk_field} = :pk_val
            {frozen_sql}
            AND   {balance_col} + :delta >= 0
            RETURNING {balance_col}
        """)

        result = await session.execute(stmt, params)
        if result.rowcount == 0:
            raise ValueError(
                f"Race or insufficient funds on {table}.{pk_field}={pk_value}"
            )
        return result.scalar_one()

    async def reset_economy(self, caller_id: int, *, confirm: bool = False):
        if caller_id not in ADMIN_IDS:
            raise PermissionError("You do not have permission to reset the economy.")

        if not confirm:
            raise ValueError("`confirm=True` is required as a safety flag.")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    text("""TRUNCATE TABLE
                    supply, wallets, bank_accounts, transactions, crypto_assets,
                    bounties, blocks, game_stats, game_history
                    RESTART IDENTITY CASCADE
                """)
                )
            await self.initialize_supply_record()

    async def get_supply_record(self) -> Supply:
        """
        Retrieve the supply record from the database or create one if it does not exist.
        """
        async with self.async_sessionmaker() as session:
            supply = await session.get(Supply, 1)
            if not supply:
                await self.initialize_supply_record()
                supply = await session.get(Supply, 1)
            return supply

    async def get_treasury_balance(self) -> Decimal:
        """
        Retrieve the current balance of the treasury.
        """
        supply = await self.get_supply_record()
        return supply.treasury

    async def create_wallet(self, user_id: int) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    return

                private_key, public_key = KeyManager.generate_key_pair()
                private_pem = KeyManager.serialize_key(private_key, private=True)
                public_pem = KeyManager.serialize_key(public_key, private=False)
                hashed_key = KeyManager.hash_key(private_pem)
                salt = os.urandom(16)

                new_wallet = Wallet(
                    user_id=user_id,
                    public_key=public_pem,
                    private_key=private_pem,
                    hashed_key=hashed_key,
                    salt=salt,
                    balance=Decimal("0.00"),
                    client_seed=secrets.token_hex(16),
                    nonce=0,
                )
                session.add(new_wallet)
                await session.flush()

                new_bank_account = BankAccount(
                    wallet_id=new_wallet.wallet_id, balance=Decimal("0.00")
                )
                session.add(new_bank_account)

                logging.info(f"Created wallet & bank for user {user_id}.")

    async def get_wallet_by_user_id(self, user_id: int) -> Wallet:
        """
        Return the Wallet row for the given user_id, creating one if needed.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one_or_none()

            if not wallet:
                await self.create_wallet(user_id)

                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id)
                )
                wallet = result.scalar_one_or_none()

            return wallet

    async def get_wallet_id_for_user(self, user_id: int) -> uuid.UUID:
        """
        Return just the wallet_id (the UUID primary key) for the given user_id,
        creating a wallet if needed.
        """
        wallet = await self.get_wallet_by_user_id(user_id)
        return wallet.wallet_id

    async def freeze_wallet(self, wallet_id: str):
        """Freeze a wallet to block outgoing transactions."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet.wallet_frozen = True

    async def unfreeze_wallet(self, wallet_id: str):
        """Unfreeze a wallet, allowing transactions again."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet.wallet_frozen = False

    async def get_wallet_balance(self, wallet_id: str) -> Decimal:
        async with self.async_sessionmaker() as session:
            wallet = await session.get(Wallet, wallet_id)
            return wallet.balance if wallet else Decimal("0.00")

    async def get_wallet_balance_by_user_id(self, user_id: int) -> Decimal:
        """
        Return the wallet balance for a given user, creating wallet if it doesn't exist.
        """
        wallet = await self.get_wallet_by_user_id(user_id)
        return wallet.balance

    async def get_client_seed(self, user_id: int) -> tuple[str, int]:
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(user_id)
            if not wallet.client_seed:
                wallet.client_seed = secrets.token_hex(16)
                wallet.nonce = 0
                session.add(wallet)
                await session.commit()
            return wallet.client_seed, wallet.nonce

    async def get_server_seed(self, user_id: int) -> str:
        """
        Return this user's server_seed, generating one if missing.
        """
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(user_id)
            if not wallet.server_seed:
                # first‐time generation
                wallet.server_seed = secrets.token_hex(16)
                wallet.previous_server_seed = None
                wallet.seed_rotated_at = datetime.now(timezone.utc)
                await session.commit()
            return wallet.server_seed

    async def set_client_seed(self, user_id: int, seed: str) -> None:
        await self.get_wallet_by_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                wallet = result.scalar_one()
                wallet.client_seed = seed
                # wallet.nonce = 0  # reset for independence

    async def reveal_and_rotate(self, user_id: int) -> tuple[Optional[str], str]:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                return await self._reveal_and_rotate_in_tx(session, user_id)

    async def _reveal_and_rotate_in_tx(
        self, session, user_id: int
    ) -> tuple[Optional[str], str]:
        # Lock wallet row
        result = await session.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )
        w = result.scalar_one()

        old_seed = w.server_seed
        if not old_seed:
            # First-time init; no reveal yet
            w.server_seed = secrets.token_hex(32)
            w.seed_rotated_at = datetime.now(timezone.utc)
            new_hash = hashlib.sha256(w.server_seed.encode()).hexdigest()
            return None, new_hash

        old_hash = hashlib.sha256(old_seed.encode()).hexdigest()

        # Generate new seed (256-bit recommended)
        new_seed = secrets.token_hex(32)
        w.previous_server_seed = old_seed
        w.server_seed = new_seed
        w.seed_rotated_at = datetime.now(timezone.utc)

        # Back-fill revealed seed into history tied to old_hash
        await session.execute(
            update(GameHistory)
            .where(GameHistory.user_id == user_id)
            .where(GameHistory.hash == old_hash)  # consider renaming this column later
            .where(GameHistory.used_server_seed.is_(None))
            .values(used_server_seed=old_seed)
        )

        new_hash = hashlib.sha256(new_seed.encode()).hexdigest()
        return old_seed, new_hash

    async def bump_and_get(self, user_id: int) -> tuple[str, str, int]:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                w = result.scalar_one_or_none()
                if w is None:
                    # create on demand if that’s your policy:
                    await session.rollback()
                    await self.create_wallet(user_id)
                    async with session.begin():
                        result = await session.execute(
                            select(Wallet)
                            .where(Wallet.user_id == user_id)
                            .with_for_update()
                        )
                        w = result.scalar_one()

                if not getattr(w, "server_seed", None):
                    w.server_seed = secrets.token_hex(32)  # 256-bit seed
                    w.previous_server_seed = None
                    w.seed_rotated_at = datetime.now(timezone.utc)

                if not getattr(w, "client_seed", None):
                    w.client_seed = secrets.token_hex(16)
                    w.nonce = 0

                nonce_before = w.nonce or 0
                w.nonce = nonce_before + 1
                return w.server_seed, w.client_seed, nonce_before

    async def get_previous_server_seed(self, user_id: int) -> Optional[str]:
        """
        Return this user's previous_server_seed, or None if unset.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one_or_none()
            if not wallet:
                return None
            return wallet.previous_server_seed

    async def increment_nonce(self, user_id: int) -> int:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                wallet = wallet.scalar_one()  # must exist
                wallet.nonce = (wallet.nonce or 0) + 1  # bump in-place
                new_nonce = wallet.nonce  # keep to return
            # transaction auto-commits here
        return new_nonce

    async def get_bank_balance(self, wallet_id: str) -> Decimal:
        async with self.async_sessionmaker() as session:
            bank = await session.get(BankAccount, wallet_id)
            return bank.balance if bank else Decimal("0.00")

    async def deposit_to_bank(self, wallet_id: str, amount: Decimal, description: str):
        """Transfer funds from wallet to bank without affecting treasury."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = amount.quantize(Decimal("0.01"))

                # 1) load + gate
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                # 2) ensure bank row exists
                bank = await session.get(BankAccount, wallet_id)
                if not bank:
                    bank = BankAccount(wallet_id=wallet_id, balance=Decimal("0.00"))
                    session.add(bank)

                # 3) atomic balance moves
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    -amount,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session, "bank_accounts", "wallet_id", wallet_id, +amount
                )

                # 4) record TX
                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=wallet.user_id,
                        to_user_id=wallet.user_id,
                        amount=amount,
                        description=description,
                        timestamp=datetime.now(),
                    )
                )

            return txid

    async def withdraw_from_bank(
        self, wallet_id: str, amount: Decimal, description: str
    ):
        """Transfer funds from bank to wallet without affecting treasury."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = amount.quantize(Decimal("0.01"))

                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                bank = await session.get(BankAccount, wallet_id)
                if not bank:
                    raise ValueError("Bank account missing.")

                await self._atomic_balance_change(
                    session, "bank_accounts", "wallet_id", wallet_id, -amount
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    wallet_id,
                    +amount,
                    frozen_field="wallet_frozen",
                )

                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=wallet.user_id,
                        to_user_id=wallet.user_id,
                        amount=amount,
                        description=description,
                        timestamp=datetime.now(),
                    )
                )

            return txid

    async def process_p2p_transaction(
        self,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
        description: str,
    ):
        factors = await self.get_economic_factors()
        fee_rate = factors["fee_rate"]
        fee = (amount * fee_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        net_amt = amount - fee

        async with self.async_sessionmaker() as session:
            async with session.begin():
                sender = await session.get(Wallet, sender_wallet_id)
                receiver = await session.get(Wallet, receiver_wallet_id)
                if not sender or not receiver:
                    raise ValueError("Invalid sender or receiver wallet.")
                if sender.wallet_frozen:
                    raise ValueError("Sender's wallet is frozen.")
                if receiver.wallet_frozen:
                    raise ValueError("Receiver's wallet is frozen.")

                # 1) atomic moves
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    sender_wallet_id,
                    -amount,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    receiver_wallet_id,
                    +net_amt,
                    frozen_field="wallet_frozen",
                )
                await self._atomic_balance_change(
                    session, "supply", "id", 1, +fee, balance_col="treasury"
                )

                # 2) record DB txs
                txid_main = str(uuid.uuid4())
                txid_fee = str(uuid.uuid4())
                session.add_all(
                    [
                        Transaction(
                            id=txid_main,
                            from_user_id=sender.user_id,
                            to_user_id=receiver.user_id,
                            amount=net_amt,
                            description=description,
                            timestamp=datetime.now(),
                        ),
                        Transaction(
                            id=txid_fee,
                            from_user_id=sender.user_id,
                            to_user_id=0,
                            amount=fee,
                            description=f"{description} (fee @ {fee_rate:.2%})",
                            timestamp=datetime.now(),
                        ),
                    ]
                )

                # 3) on-chain block atomically (validator = sender)
                onchain_txs = [
                    {
                        "id": txid_main,
                        "from_user_id": sender.user_id,
                        "to_user_id": receiver.user_id,
                        "amount": str(net_amt),
                        "description": description,
                        "signer_user_id": sender.user_id,
                    },
                    {
                        "id": txid_fee,
                        "from_user_id": sender.user_id,
                        "to_user_id": 0,
                        "amount": str(fee),
                        "description": f"Fee for P2P: {fee_rate:.2%}",
                        "signer_user_id": sender.user_id,
                    },
                ]
                await self.blockchain.create_block_atomic(
                    session, onchain_txs, validator_user_id=sender.user_id
                )

            await self.update_supply()
        return txid_main

    async def process_treasury_transaction(
        self, wallet_id: str, amount: Decimal, description: str
    ):
        factors = await self.get_economic_factors()
        fee_rate = factors["fee_rate"]

        amount = amount.quantize(Decimal("0.01"), ROUND_HALF_UP)
        if amount == 0:
            raise ValueError("Cannot process zero-amount transaction.")

        gross = abs(amount)
        fee = (gross * fee_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        net = gross - fee

        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet missing.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                if amount > 0:  # payout: treasury → wallet
                    await self._atomic_balance_change(
                        session, "supply", "id", 1, -gross, balance_col="treasury"
                    )
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        wallet_id,
                        +net,
                        frozen_field="wallet_frozen",
                    )
                    await self._atomic_balance_change(
                        session, "supply", "id", 1, +fee, balance_col="treasury"
                    )
                    from_uid, to_uid = 0, wallet.user_id
                else:  # deposit: wallet → treasury
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        wallet_id,
                        -gross,
                        frozen_field="wallet_frozen",
                    )
                    await self._atomic_balance_change(
                        session, "supply", "id", 1, +net + fee, balance_col="treasury"
                    )
                    from_uid, to_uid = wallet.user_id, 0

                # DB tx rows
                tid_main = str(uuid.uuid4())
                tid_fee = str(uuid.uuid4())
                session.add_all(
                    [
                        Transaction(
                            id=tid_main,
                            from_user_id=from_uid,
                            to_user_id=to_uid,
                            amount=net,
                            description=description,
                            timestamp=datetime.now(),
                        ),
                        Transaction(
                            id=tid_fee,
                            from_user_id=from_uid,
                            to_user_id=to_uid if from_uid == 0 else 0,
                            amount=fee,
                            description=f"{description} (fee @ {fee_rate:.2%})",
                            timestamp=datetime.now(),
                        ),
                    ]
                )

                # on-chain block atomically (validator = wallet owner)
                onchain = [
                    {
                        "id": tid_main,
                        "from_user_id": from_uid,
                        "to_user_id": to_uid,
                        "amount": str(net),
                        "description": description,
                        "signer_user_id": wallet.user_id,
                    },
                    {
                        "id": tid_fee,
                        "from_user_id": from_uid,
                        "to_user_id": 0,
                        "amount": str(fee),
                        "description": f"{description} (fee @ {fee_rate:.2%})",
                        "signer_user_id": wallet.user_id,
                    },
                ]
                await self.blockchain.create_block_atomic(
                    session, onchain, validator_user_id=wallet.user_id
                )

            await self.update_supply()
        return tid_main

    async def get_transaction_by_id(self, txid: str) -> Transaction:
        """
        Fetch a single transaction record by its Transaction ID (UUID).

        :param txid: The UUID string of the transaction you want to look up.
        :return: A single Transaction object, or None if not found.
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(Transaction).where(Transaction.id == txid)
                )
                transaction = result.scalar_one_or_none()
                return transaction
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving transaction by id {txid}: {str(e)}")
                return None

    async def get_transactions_by_user_id(self, user_id: int, limit: int = 10) -> list:
        """
        Retrieve the latest transactions for a specific user.
        A transaction is included if the user is either the sender or the receiver.

        :param user_id: The Discord ID (int) of the user.
        :param limit: How many of the most recent transactions to retrieve (default=10).
        :return: A list of Transaction objects sorted by timestamp descending.
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Transaction)
                    .where(
                        (Transaction.from_user_id == user_id)
                        | (Transaction.to_user_id == user_id)
                    )
                    .order_by(Transaction.timestamp.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                transactions = result.scalars().all()
                return transactions
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving user {user_id} transactions: {str(e)}")
                return []

    async def update_supply(self):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing.")

                wallet_total_result = await session.execute(
                    select(func.sum(Wallet.balance))
                )
                wallet_total = wallet_total_result.scalar() or Decimal("0.00")

                bank_total_result = await session.execute(
                    select(func.sum(BankAccount.balance))
                )
                bank_total = bank_total_result.scalar() or Decimal("0.00")

                circulating_supply = (wallet_total + bank_total).quantize(
                    Decimal("0.01")
                )

                total_supply = (circulating_supply + supply.treasury).quantize(
                    Decimal("0.01")
                )

                treasury_health = (
                    supply.treasury / total_supply
                    if total_supply > 0
                    else Decimal("0.00")
                )

                if treasury_health < Decimal("0.25"):
                    logger.warning(
                        "⚠️ Treasury health is low! Consider minting more currency."
                    )

                supply.circulating = circulating_supply
                supply.total_supply = total_supply

            await session.commit()

    async def get_top_balance_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by total balance (wallet + bank combined).

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, total_balance) sorted by total balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(
                        Wallet.user_id,
                        (Wallet.balance + func.coalesce(BankAccount.balance, 0)).label(
                            "total_balance"
                        ),
                    )
                    .select_from(Wallet)
                    .outerjoin(BankAccount, Wallet.wallet_id == BankAccount.wallet_id)
                    .order_by(text("total_balance DESC"))
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return result.all()
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top balance users: {str(e)}")
                return []

    async def get_balance_user_rank(self, user_id: int) -> Optional[int]:
        """
        Retrieve the supplied users leaderboard position by total balance (wallet + bank combined).
        Args:
            user_id: The Discord ID (int) of the user.
        Returns:
            The rank of the user (1-based), or None if the user has no wallet.
        """
        async with self.async_sessionmaker() as session:
            try:
                subquery = (
                    select(
                        Wallet.user_id,
                        (Wallet.balance + func.coalesce(BankAccount.balance, 0)).label(
                            "total_balance"
                        ),
                    )
                    .select_from(Wallet)
                    .outerjoin(BankAccount, Wallet.wallet_id == BankAccount.wallet_id)
                    .subquery()
                )

                ranked_query = select(
                    subquery.c.user_id,
                    func.rank()
                    .over(order_by=subquery.c.total_balance.desc())
                    .label("rank"),
                ).subquery()

                stmt = select(ranked_query.c.rank).where(
                    ranked_query.c.user_id == user_id
                )
                result = await session.execute(stmt)
                rank = result.scalar_one_or_none()
                return rank
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving user {user_id} rank: {str(e)}")
                return None

    async def get_top_wallet_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by wallet balance.

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, wallet_balance) sorted by wallet balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Wallet.user_id, Wallet.balance)
                    .order_by(Wallet.balance.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return result.all()
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top wallet users: {str(e)}")
                return []

    async def get_top_bank_users(self, limit: int = 10) -> list[tuple[int, Decimal]]:
        """
        Retrieve the top users by bank balance.

        Args:
            limit: Number of top users to retrieve (default=10)

        Returns:
            List of tuples containing (user_id, bank_balance) sorted by bank balance descending
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(Wallet.user_id, BankAccount.balance)
                    .join(BankAccount, Wallet.wallet_id == BankAccount.wallet_id)
                    .order_by(BankAccount.balance.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return result.all()
            except SQLAlchemyError as e:
                logging.error(f"Error retrieving top bank users: {str(e)}")
                return []

    async def mint_currency(self, amount: Decimal, description: str):
        """
        Mint new currency and add it to the treasury balance.
        Also logs an on-chain "mint" transaction.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = amount.quantize(Decimal("0.01"))
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing!")

                supply.treasury += amount

                txid = str(uuid.uuid4())
                transaction_db = Transaction(
                    id=txid,
                    from_user_id=None,
                    to_user_id=0,
                    amount=amount,
                    description=description,
                    timestamp=datetime.now(),
                )
                session.add(transaction_db)
                # Atomic on-chain block (validator = owner/admin)
                minted_tx = {
                    "id": txid,
                    "from_user_id": None,
                    "to_user_id": 0,
                    "amount": str(amount),
                    "description": description,
                    "signer_user_id": 284439598422163476,
                }
                await self.blockchain.create_block_atomic(
                    session, [minted_tx], validator_user_id=284439598422163476
                )

            await self.update_supply()

    async def burn_currency(self, amount: Decimal, description: str):
        """
        Burns currency by removing it from treasury, effectively reducing total supply.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = amount.quantize(Decimal("0.01"))
                supply = await session.get(Supply, 1)
                if not supply:
                    raise ValueError("Supply record missing!")

                if supply.treasury < amount:
                    raise ValueError("Not enough treasury balance to burn.")

                supply.treasury -= amount

                txid = str(uuid.uuid4())
                transaction_db = Transaction(
                    id=txid,
                    from_user_id=0,
                    to_user_id=None,
                    amount=amount,
                    description=description,
                    timestamp=datetime.now(),
                )
                session.add(transaction_db)
                # Atomic on-chain block (validator = owner/admin)
                burned_tx = {
                    "id": txid,
                    "from_user_id": 0,
                    "to_user_id": None,
                    "amount": str(amount),
                    "description": description,
                    "signer_user_id": 284439598422163476,
                }
                await self.blockchain.create_block_atomic(
                    session, [burned_tx], validator_user_id=284439598422163476
                )

            await self.update_supply()

    async def validate_economy(self):
        """
        Cross-check that:
        - sum of all wallets & banks == supply.circulating
        - supply.circulating + supply.treasury == supply.total_supply
        - blockchain is consistent
        Returns True if all checks out, False otherwise.
        """

        await self.update_supply()

        is_valid, _ = await self.blockchain.validate_blockchain()
        if not is_valid:
            return False

        async with self.async_sessionmaker() as session:
            result_wallet = await session.execute(select(func.sum(Wallet.balance)))
            total_wallet = result_wallet.scalar() or Decimal("0.00")

            result_bank = await session.execute(select(func.sum(BankAccount.balance)))
            total_bank = result_bank.scalar() or Decimal("0.00")

            supply = await session.get(Supply, 1)
            if not supply:
                return False

            if (total_wallet + total_bank) != supply.circulating:
                logging.error("Circulating mismatch!")
                return False

            if supply.circulating + supply.treasury != supply.total_supply:
                logging.error("Total supply mismatch!")
                return False

        return True

    async def get_economic_factors(self) -> dict:
        """
        No more automatic minting.  We still auto-burn if health > MAX_HW
        to prevent runaway inflation, but we *never* create new coins;
        instead we rely on in-game sinks (see Part 2).
        """
        supply = await self.get_supply_record()
        treasury, total_supply = supply.treasury, supply.total_supply

        if total_supply == 0:
            return {
                "treasury_health": Decimal("0"),
                "risk_scalar": Decimal("0"),
                "fee_rate": Decimal("0"),
                "passive_income_rate": Decimal("0"),
            }

        treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))

        TARGET = Decimal("0.50")
        MIN_HW = Decimal("0.30")  # start minting below this
        MAX_HW = Decimal("0.90")  # burn above 90 %
        STEP = Decimal("0.05")  # burn 5 % of excess
        CAP = total_supply * Decimal("0.02")  #   …max 2 % of supply
        COOLDOWN = timedelta(hours=1)

        global _LAST_REBALANCE_AT
        now = datetime.utcnow()

        need_rebalance = (treasury_health < MIN_HW or treasury_health > MAX_HW) and (
            _LAST_REBALANCE_AT is None or now - _LAST_REBALANCE_AT > COOLDOWN
        )

        # --- gradual mint/burn ------------------------------------------------
        if need_rebalance:
            gap = (TARGET * total_supply) - treasury  # + = need mint
            adj = min(abs(gap) * STEP, CAP).quantize(Decimal("0.01"))

            if adj > 0:
                if gap > 0:  # mint
                    await self.mint_currency(
                        adj, f"Auto-mint {adj} (health {treasury_health:.2%})"
                    )
                else:  # burn
                    await self.burn_currency(
                        adj, f"Auto-burn {adj} (health {treasury_health:.2%})"
                    )
                _LAST_REBALANCE_AT = now
                # refresh values after action
                supply = await self.get_supply_record()
                treasury = supply.treasury
                total_supply = supply.total_supply
                treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))

        # health-dependent fee / passive income (unchanged logic)
        BASE_FEE = Decimal("0.01")
        BASE_PASS = Decimal("0.005")
        if treasury_health < TARGET:
            d = TARGET - treasury_health
            fee = (BASE_FEE * (1 + d**2)).quantize(Decimal("0.0001"))
            passive = (BASE_PASS * (1 - d)).quantize(Decimal("0.0001"))
            risk = (treasury_health / TARGET).quantize(Decimal("0.0001"))  # 0→1
        else:
            fee = BASE_FEE
            passive = BASE_PASS
            risk = (Decimal("1.0") + (treasury_health - TARGET)).quantize(
                Decimal("0.0001")
            )

        return {
            "treasury_health": treasury_health,
            "risk_scalar": risk,
            "fee_rate": fee,
            "passive_income_rate": passive,
        }

    async def get_max_gamble_amount(
        self, user_id: int, raise_if_limited: bool = False
    ) -> Decimal:
        """
        Much stricter risk control:

        • Base budget = 0.25–1.0 % of treasury (depends on health).
        • *Absolute* ceiling = 2 % of treasury.
        • Whales (>1 % of supply) lose 75 % of budget.
        • Newcomer floor = min(100, 2 % of own balance).
        """
        # ---- constants -------------------------------------------------------
        MAX_TREASURY_EXPOSURE = Decimal("0.02")  # 2 %
        WHALE_THRESHOLD = Decimal("0.01")  # 1 % of supply
        WHALE_PENALTY = Decimal("0.75")  # −75 %
        MIN_ABSOLUTE_FLOOR = Decimal("100.00")  # newcomer min (subject to user bal)

        # ---- user balances ---------------------------------------------------
        wallet = await self.get_wallet_by_user_id(user_id)
        wallet_bal = await self.get_wallet_balance(wallet.wallet_id)
        bank_bal = await self.get_bank_balance(wallet.wallet_id)
        user_total = wallet_bal + bank_bal

        # ---- economy snapshot -----------------------------------------------
        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")  # house broke / not initialised

        # ---- dynamic base coefficient ---------------------------------------
        factors = await self.get_economic_factors()
        health = factors["treasury_health"]  # 0-1 Decimal

        # map health ⇒ base coefficient (quadratic taper)
        # ≥ 60 %   → 1 %
        # 30 %     → 0.25 %
        # 25 %↓    → 0.125 %
        if health >= Decimal("0.60"):
            base_coeff = Decimal("0.01")
        elif health >= Decimal("0.30"):
            # quadratic between 0.25 %–1 %
            t = (health - Decimal("0.30")) / Decimal("0.30")  # 0-1
            base_coeff = (
                Decimal("0.0025") + (Decimal("0.01") - Decimal("0.0025")) * t**2
            )
        else:
            base_coeff = Decimal("0.00125")  # 0.125 %

        # ---- whale adjustment -----------------------------------------------
        user_ratio = (user_total / total_supply).quantize(Decimal("0.0001"))
        if user_ratio > WHALE_THRESHOLD:
            base_coeff *= Decimal("1.0") - WHALE_PENALTY  # ×0.25

        # ---- compute limits --------------------------------------------------
        by_treasury = (treasury * base_coeff).quantize(Decimal("0.01"))
        hard_cap = (treasury * MAX_TREASURY_EXPOSURE).quantize(Decimal("0.01"))

        provisional = min(by_treasury, hard_cap, user_total)

        # adaptive floor: 100 or 2 % of player’s own money, whichever is lower
        adaptive_floor = min(
            MIN_ABSOLUTE_FLOOR, (user_total * Decimal("0.02")).quantize(Decimal("0.01"))
        )

        final_limit = max(provisional, adaptive_floor)

        # ---- optional rejection ---------------------------------------------
        if raise_if_limited and user_total > final_limit:
            raise ValueError(
                f"You’re limited to **{final_limit} {self.currency_name}** "
                f"this hand due to risk controls."
            )

        return final_limit

    async def set_mines_multi(self, data: list):
        """
        Insert a list of bomb_count, gem_count, and multiplier values into the database.
        :param session: AsyncSession
        :param data: List of tuples (bomb_count, gem_count, multiplier)
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                for bomb, gem, multiplier in data:
                    record = MinesSettings(
                        bomb_count=bomb, gem_count=gem, multiplier=multiplier
                    )
                    session.add(record)
                await session.commit()

    async def get_mines_multiplier(self, bomb_count: int, gem_count: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(MinesSettings.multiplier)
                .where(MinesSettings.bomb_count == bomb_count)
                .where(MinesSettings.gem_count == gem_count)
            )
            return result.scalar_one_or_none()

    async def set_report_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()

                if settings:
                    settings.report_channel_id = channel_id
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, report_channel_id=channel_id
                    )
                    session.add(settings)

            await session.commit()

    async def set_member_count_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()

                if settings:
                    settings.member_count_channel_id = channel_id
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, member_count_channel_id=channel_id
                    )
                    session.add(settings)

            await session.commit()

    async def get_member_count_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings).filter_by(guild_id=guild_id)
            )
            settings = result.scalar_one_or_none()

            return settings.member_count_channel_id if settings else None

    async def get_report_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings).filter_by(guild_id=guild_id)
            )
            report_setting = result.scalar_one_or_none()

            return report_setting.report_channel_id if report_setting else None

    async def set_jail_settings(
        self, guild_id: int, role_id: int, channel_id: int
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                jail_setting = await session.get(ServerSettings, guild_id)
                if jail_setting:
                    jail_setting.jail_role_id = role_id
                    jail_setting.jail_channel_id = channel_id
                else:
                    jail_setting = ServerSettings(
                        guild_id=guild_id,
                        jail_role_id=role_id,
                        jail_channel_id=channel_id,
                    )
                    session.add(jail_setting)
                await session.commit()

    async def get_jail_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

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

    async def toggle_antimp3(self, guild_id: int, enabled: bool):
        async with self.get_session() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    settings.antimp3_enabled = enabled
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, antimp3_enabled=enabled
                    )
                    session.add(settings)
                await session.commit()

    async def get_antimp3_status(self, guild_id: int) -> bool:
        async with self.get_session() as session:
            settings = await session.get(ServerSettings, guild_id)
            return settings.antimp3_enabled if settings else False

    async def set_mute_settings(
        self, guild_id: int, mute_role_id: int, imute_role_id: int, rmute_role_id: int
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                mute_setting = await session.get(ServerSettings, guild_id)
                if mute_setting:
                    mute_setting.mute_role_id = mute_role_id
                    mute_setting.imute_role_id = imute_role_id
                    mute_setting.rmute_role_id = rmute_role_id
                else:
                    mute_setting = ServerSettings(
                        guild_id=guild_id,
                        mute_role_id=mute_role_id,
                        imute_role_id=imute_role_id,
                        rmute_role_id=rmute_role_id,
                    )
                    session.add(mute_setting)
                await session.commit()

    async def get_mute_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def set_command_status(
        self, command_name: str, enabled: bool, channel_id: int = None
    ) -> None:
        async with self.get_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(CommandStatus).where(
                        CommandStatus.command_name == command_name,
                        CommandStatus.channel_id == channel_id,
                    )
                )
                command_status = result.scalar_one_or_none()
                if command_status:
                    command_status.enabled = enabled
                else:
                    new_status = CommandStatus(
                        command_name=command_name,
                        enabled=enabled,
                        channel_id=channel_id,
                    )
                    session.add(new_status)

    async def get_command_status(
        self, command_name: str, channel_id: int = None
    ) -> bool:
        """Retrieve the status of a command for both prefix and slash commands."""
        async with self.async_sessionmaker() as session:
            query = select(CommandStatus).filter_by(
                command_name=command_name,
            )

            if channel_id:
                query = query.filter_by(channel_id=channel_id)
            else:
                query = query.filter(CommandStatus.channel_id.is_(None))

            result = await session.execute(query)
            status = result.scalars().first()

            return status.enabled if status else True

    async def clear_expired_cooldowns(self):
        """Clears expired cooldowns from the database."""
        async with self.async_sessionmaker() as session:
            await session.execute(
                delete(CommandCooldown).where(
                    CommandCooldown.cooldown_expiry < datetime.now()
                )
            )
            await session.commit()

    async def set_cooldown(
        self, user_id: int, command_name: str, cooldown_seconds: int
    ):
        """Sets a cooldown for both prefix and slash commands for a user."""
        expiry_time = datetime.now() + timedelta(seconds=cooldown_seconds)

        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(CommandCooldown)
                    .where(
                        CommandCooldown.user_id == user_id,
                        CommandCooldown.command_name == command_name,
                    )
                    .values(cooldown_expiry=expiry_time)
                )
                result = await session.execute(stmt)

                if result.rowcount == 0:
                    cooldown = CommandCooldown(
                        user_id=user_id,
                        command_name=command_name,
                        cooldown_expiry=expiry_time,
                    )
                    session.add(cooldown)

    async def get_cooldown(self, user_id: int, command_name: str) -> float:
        """Returns the maximum remaining cooldown time across both command types."""
        async with self.async_sessionmaker() as session:
            max_remaining = 0
            result = await session.execute(
                select(CommandCooldown.cooldown_expiry).filter_by(
                    user_id=user_id,
                    command_name=command_name,
                )
            )
            cooldown_expiry = result.scalar_one_or_none()
            if cooldown_expiry:
                remaining_time = (cooldown_expiry - datetime.now()).total_seconds()
                max_remaining = max(max_remaining, remaining_time)

            return max(0, max_remaining)

    async def add_to_blacklist(self, user_id: str, reason: str) -> None:
        try:
            async with self.async_sessionmaker() as session:
                await session.execute(
                    delete(Blacklist).where(Blacklist.user_id == user_id)
                )
                blacklist_entry = Blacklist(user_id=user_id, reason=reason)
                session.add(blacklist_entry)
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error adding to blacklist: {str(e)}")

    async def remove_from_blacklist(self, user_id: str) -> None:
        async with self.async_sessionmaker() as session:
            await session.execute(delete(Blacklist).where(Blacklist.user_id == user_id))
            await session.commit()

    async def clear_blacklist(self) -> None:
        """Clear the entire blacklist."""
        async with self.async_sessionmaker() as session:
            for entry in await session.execute(select(Blacklist)):
                await session.delete(entry)
            await session.commit()

    async def add_crypto_asset(
        self, user_id: int, symbol: str, amount: Decimal, purchase_price: Decimal
    ):
        """Add a new crypto asset or update existing one for a user."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(
                        select(CryptoAsset).where(
                            CryptoAsset.user_id == user_id,
                            CryptoAsset.symbol == symbol.upper(),
                        )
                    )
                    asset = result.scalar_one_or_none()

                    if asset:
                        new_amount = asset.amount + amount
                        if new_amount < 0:
                            raise ValueError("Cannot reduce asset below 0")

                        if amount > 0:
                            total_value = (asset.amount * asset.purchase_price) + (
                                amount * purchase_price
                            )
                            asset.purchase_price = total_value / new_amount
                        asset.amount = new_amount
                    else:
                        if amount < 0:
                            raise ValueError("Cannot create asset with negative amount")
                        asset = CryptoAsset(
                            user_id=user_id,
                            symbol=symbol.upper(),
                            amount=amount,
                            purchase_price=purchase_price,
                        )
                        session.add(asset)

                    await session.commit()
                    return asset

        except SQLAlchemyError as e:
            logging.error(f"Database error adding crypto asset: {str(e)}")
            raise

    async def get_crypto_assets(self, user_id: int) -> List[CryptoAsset]:
        """Get all crypto assets for a user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(CryptoAsset.user_id == user_id)
            )
            return result.scalars().all()

    async def get_crypto_asset(self, user_id: int, symbol: str) -> CryptoAsset:
        """Get specific crypto asset for a user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoAsset).where(
                    CryptoAsset.user_id == user_id, CryptoAsset.symbol == symbol.upper()
                )
            )
            return result.scalar_one_or_none()

    async def update_crypto_amount(self, user_id: int, symbol: str, amount: Decimal):
        """Update amount of a crypto asset."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == user_id,
                        CryptoAsset.symbol == symbol.upper(),
                    )
                )
                asset = result.scalar_one_or_none()

                if not asset:
                    raise ValueError(f"No {symbol} asset found for user")

                if amount < 0:
                    if abs(amount) > asset.amount:
                        raise ValueError("Insufficient crypto balance")
                    asset.amount += amount
                else:
                    asset.amount += amount

                await session.commit()
                return asset

    async def delete_crypto_asset(self, user_id: int, symbol: str):
        """Delete a crypto asset entry."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(CryptoAsset).where(
                        CryptoAsset.user_id == user_id,
                        CryptoAsset.symbol == symbol.upper(),
                    )
                )
                await session.commit()

    async def get_total_crypto_value(self, user_id: int) -> Decimal:
        """Get total value of all crypto assets at purchase price."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.sum(CryptoAsset.amount * CryptoAsset.purchase_price)).where(
                    CryptoAsset.user_id == user_id
                )
            )
            total = result.scalar_one_or_none()
            return total if total else Decimal("0")

    async def set_crypto_price(self, symbol: str, price: Decimal):
        """Set the price of a crypto asset."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CryptoPrice).where(CryptoPrice.symbol == symbol.upper())
                )
                crypto_price = result.scalar_one_or_none()

                if crypto_price:
                    crypto_price.price = price
                else:
                    crypto_price = CryptoPrice(symbol=symbol.upper(), price=price)
                    session.add(crypto_price)
                await session.commit()

    async def get_crypto_price(self, symbol: str) -> Decimal:
        """Get the price of a crypto asset."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoPrice).where(CryptoPrice.symbol == symbol.upper())
            )
            price_record = result.scalar_one_or_none()

            now = datetime.now()
            if (
                price_record
                and price_record.timestamp
                and price_record.timestamp >= now - timedelta(hours=1)
            ):
                return price_record.price

            try:
                async with aiohttp.ClientSession() as api_session:
                    params = {
                        "symbol": symbol,
                        "convert": "USD",
                    }
                    headers = {"X-CMC_PRO_API_KEY": os.getenv("COINMARKETCAP_API_KEY")}

                    async with api_session.get(
                        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
                        params=params,
                        headers=headers,
                    ) as response:
                        if response.status == 429:
                            if price_record:
                                return price_record.price
                            raise Exception("Rate limit exceeded")

                        data = await response.json()
                        crypto_data = data["data"][symbol]
                        quote = crypto_data["quote"]["USD"]
                        price = Decimal(str(quote["price"]))

                        if price_record:
                            price_record.price = price
                            price_record.timestamp = datetime.now()
                        else:
                            new_record = CryptoPrice(
                                symbol=symbol.upper(),
                                price=price,
                                timestamp=datetime.now(),
                            )
                            session.add(new_record)

                        await session.commit()
                        await session.commit()
                        return price

            except Exception as e:
                logging.error(f"Error fetching crypto price: {str(e)}")

                if price_record:
                    return price_record.price
                return None

    async def get_user_inventory(self, user_id: int) -> List[Item]:
        """
        Retrieve all items owned by a user.

        Args:
            user_id: The Discord ID of the user

        Returns:
            List of Item objects in the user's inventory
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Item).where(Item.user_id == user_id))
            return result.scalars().all()

    async def get_user_inventory_grouped(self, user_id: int) -> List[dict]:
        """Return inventory items grouped by name with aggregated quantity."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item.name, Item.description, func.count(Item.id).label("qty"))
                .where(Item.user_id == user_id)
                .group_by(Item.name, Item.description)
            )
            rows = result.all()
            return [{"name": r[0], "description": r[1], "quantity": r[2]} for r in rows]

    async def get_user_item(self, user_id: int, item_id: int) -> Item:
        """
        Retrieve a specific item from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_id: The ID of the item

        Returns:
            Item object if found, None otherwise
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            return result.scalar_one_or_none()

    async def get_user_items_by_name(self, user_id: int, item_name: str) -> List[Item]:
        """
        Retrieve all items with a specific name from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_name: The name of the items to retrieve

        Returns:
            List of matching Item objects
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.name == item_name)
            )
            return result.scalars().all()

    async def get_user_items_by_type(
        self, user_id: int, item_type: ItemType
    ) -> List[Item]:
        """
        Retrieve all items of a specific type from user's inventory.

        Args:
            user_id: The Discord ID of the user
            item_type: The ItemType to filter by

        Returns:
            List of matching Item objects
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.item_type == item_type)
            )
            return result.scalars().all()

    async def transfer_item(
        self, from_user_id: int, to_user_id: int, item_id: int, quantity: int = 1
    ) -> bool:
        """Transfer items from one user to another."""

        if quantity <= 0:
            raise ValueError("Transfer quantity must be positive")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == from_user_id, Item.id == item_id)
                )
                source_item = result.scalar_one_or_none()

                if not source_item:
                    raise ValueError(
                        f"Item with ID {item_id} not found in user {from_user_id}'s inventory"
                    )

                if quantity > source_item.quantity:
                    raise ValueError(
                        f"Not enough items to transfer (have {source_item.quantity}, need {quantity})"
                    )

                if quantity == source_item.quantity:
                    source_item.user_id = to_user_id
                else:
                    source_item.quantity -= quantity
                    for _ in range(quantity):
                        new_item = Item(
                            user_id=to_user_id,
                            name=source_item.name,
                            serial_number=await self.generate_item_serial_number(),
                            description=source_item.description,
                            quantity=1,
                            item_type=source_item.item_type,
                            effect=source_item.effect,
                            effect_value=source_item.effect_value,
                            effect_duration=source_item.effect_duration,
                            cooldown_seconds=source_item.cooldown_seconds,
                        )
                        session.add(new_item)

                await session.commit()
                return True

    async def update_user_item(self, user_id: int, item_id: int, **kwargs) -> bool:
        """
        Update properties of an item in a user's inventory.

        Args:
            user_id: The Discord ID of the item owner
            item_id: The ID of the item to update
            **kwargs: The properties to update (name, description, etc.)

        Returns:
            True if update succeeded, False otherwise
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    return False

                allowed_props = ["name", "description", "quantity", "item_type"]
                for prop, value in kwargs.items():
                    if prop in allowed_props and hasattr(item, prop):
                        setattr(item, prop, value)

                await session.commit()
                return True

    async def remove_user_item(
        self, user_id: int, item_id: int, quantity: int = None
    ) -> bool:
        """
        Remove an item from a user's inventory.

        Args:
            user_id: The Discord ID of the item owner
            item_id: The ID of the item to remove
            quantity: The quantity to remove (if None, removes all)

        Returns:
            True if removal succeeded, False otherwise
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id, Item.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    return False

                if quantity is None or quantity >= item.quantity:
                    await session.delete(item)
                else:
                    item.quantity -= quantity

                await session.commit()
                return True

    async def generate_item_serial_number(self) -> str:
        """
        Generate a unique serial number for a new item.

        Returns:
            A unique serial number string
        """
        timestamp = str(int(time.time() * 1000))
        random_number = str(secrets.randbelow(90000) + 10000)
        raw_serial = timestamp + random_number
        hash_object = hashlib.sha256(raw_serial.encode("utf-8"))
        hashed_serial = hash_object.hexdigest()
        serial_number = f"{hashed_serial[:8]}-{hashed_serial[8:12]}-{hashed_serial[12:16]}-{hashed_serial[16:20]}-{hashed_serial[20:32]}"
        return serial_number

    async def create_user_item(
        self,
        user_id: int,
        name: str,
        description: str,
        quantity: int = 1,
        item_type: ItemType = ItemType.COLLECTIBLE,
    ) -> Item:
        """
        Create a new item directly in a user's inventory (not from shop).

        Args:
            user_id: The Discord ID of the user
            name: Name of the item
            description: Description of the item
            quantity: Quantity to create (default: 1)
            item_type: Type of the item (default: COLLECTIBLE)

        Returns:
            The created Item object
        """
        async with self.async_sessionmaker() as session:
            serial_number = await self.generate_item_serial_number()
            item = Item(
                user_id=user_id,
                name=name,
                serial_number=serial_number,
                description=description,
                quantity=quantity,
                item_type=item_type,
            )
            session.add(item)
            await session.commit()
            return item

    async def merge_duplicate_items(self, user_id: int) -> int:
        """
        Consolidate duplicate items in a user's inventory.
        Items with the same name will be merged.

        Args:
            user_id: The Discord ID of the user

        Returns:
            Number of items merged
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Item).where(Item.user_id == user_id)
                )
                items = result.scalars().all()

                item_groups = {}
                for item in items:
                    key = item.name
                    if key not in item_groups:
                        item_groups[key] = []
                    item_groups[key].append(item)

                merged_count = 0

                for group in item_groups.values():
                    if len(group) > 1:
                        primary_item = group[0]
                        for other_item in group[1:]:
                            primary_item.quantity += other_item.quantity
                            await session.delete(other_item)
                            merged_count += 1

                await session.commit()
                return merged_count

    async def add_shop_item(
        self,
        name: str,
        description: str,
        price: Decimal,
        quantity: int,
        item_type: ItemType = ItemType.COLLECTIBLE,
        unlimited: bool = False,
    ) -> ShopItem:
        """Create and store a new shop item."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                new_shop_item = ShopItem(
                    name=name,
                    description=description,
                    price=price,
                    quantity=quantity,
                    unlimited=unlimited,
                    item_type=item_type,
                )
                session.add(new_shop_item)
            await session.commit()
            return new_shop_item

    async def update_shop_item_quantity(
        self, item_id: int, quantity_change: int
    ) -> bool:
        """
        Adjust the quantity of a shop item by a given change amount (can be positive or negative).
        Returns True if the change is applied successfully; otherwise False (for example, when stock would be negative).
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    return False
                if shop_item.unlimited:
                    return True

                new_quantity = shop_item.quantity + quantity_change
                if new_quantity < 0:
                    return False
                shop_item.quantity = new_quantity
            await session.commit()
            return True

    async def remove_shop_item(self, item_id: int) -> bool:
        """
        Remove a shop item completely from the shop by its ID.
        Returns True if an item was removed, otherwise False.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ShopItem).where(ShopItem.id == item_id)
                )
                if result.rowcount and result.rowcount > 0:
                    await session.commit()
                    return True
                return False

    async def list_shop_items(self) -> List[ShopItem]:
        """
        Retrieve all available shop items that have a positive stock level.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(
                    (ShopItem.quantity > 0) | (ShopItem.unlimited == True)
                )
            )
            return result.scalars().all()

    async def get_shop_item_by_id(self, item_id: int) -> ShopItem:
        """
        Retrieve a specific shop item using its unique ID.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ShopItem).where(ShopItem.id == item_id)
            )
            return result.scalar_one_or_none()

    async def purchase_shop_item(
        self, user_id: int, shop_item_id: int, quantity: int = 1
    ) -> dict:
        """
        Purchase a shop item by:
          - Verifying available stock.
          - Checking if the buyer’s wallet has enough funds.
          - Deducting the total cost from the user’s wallet.
          - Reducing the shop’s stock.
          - Adding the purchased item to the user’s inventory.
        Returns a dict containing purchase details.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ShopItem).where(ShopItem.id == shop_item_id)
                )
                shop_item = result.scalar_one_or_none()
                if not shop_item:
                    raise ValueError("Shop item not found.")
                if not shop_item.unlimited and shop_item.quantity < quantity:
                    raise ValueError("Insufficient stock available.")

                total_cost = Decimal(str(shop_item.price * quantity))

                wallet_id = await self.get_wallet_id_for_user(user_id)
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError(f"Wallet {wallet_id} not found.")
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < total_cost:
                    raise ValueError("Insufficient funds.")

                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                await self.process_treasury_transaction(
                    wallet_id, -total_cost, f"Purchased {quantity}x {shop_item.name}"
                )

                if not shop_item.unlimited:
                    shop_item.quantity -= quantity

                for _ in range(quantity):
                    new_inventory_item = Item(
                        user_id=user_id,
                        name=shop_item.name,
                        serial_number=await self.generate_item_serial_number(),
                        description=shop_item.description,
                        quantity=1,
                        item_type=shop_item.item_type,
                        effect=shop_item.effect,
                        effect_value=shop_item.effect_value,
                        effect_duration=shop_item.effect_duration,
                        cooldown_seconds=shop_item.cooldown_seconds,
                    )
                    session.add(new_inventory_item)
            await session.commit()
            return {
                "success": True,
                "item_name": shop_item.name,
                "quantity": quantity,
                "cost": total_cost,
            }

    async def use_inventory_item(self, user_id: int, item_id: int) -> str:
        """
        Use an item from the user's inventory according to its type.
          - For a CONSUMABLE, reduce its quantity by one (removing it if depleted).
          - For a REDEEMABLE, remove it after use.
          - For a COLLECTIBLE, simply showcase the item.
        Returns a message describing the result.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                raise ValueError("Item not found in inventory.")

            if item.item_type == ItemType.CONSUMABLE:
                message = f"You have consumed one {item.name}."
                if item.effect == "currency" and item.effect_value:
                    wallet_id = await self.get_wallet_id_for_user(user_id)
                    await self.process_treasury_transaction(
                        wallet_id, Decimal(item.effect_value), f"Used {item.name}"
                    )
                async with session.begin():
                    item.quantity -= 1
                    if item.quantity <= 0:
                        await session.delete(item)
                await session.commit()
                return message
            elif item.item_type == ItemType.REDEEMABLE:
                message = f"You have redeemed {item.name} and received its benefits."
                if item.effect == "currency" and item.effect_value:
                    wallet_id = await self.get_wallet_id_for_user(user_id)
                    await self.process_treasury_transaction(
                        wallet_id, Decimal(item.effect_value), f"Redeemed {item.name}"
                    )
                async with session.begin():
                    await session.delete(item)
                await session.commit()
                return message
            elif item.item_type == ItemType.COLLECTIBLE:
                return f"You are now showcasing your collectible {item.name}."
            else:
                raise ValueError("Unknown item type.")

    async def place_bounty(
        self, issuer_id: int, target_id: int, reward: Decimal
    ) -> Bounty:
        """Place (or increase) a bounty on a user."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet_id = await self.get_wallet_id_for_user(issuer_id)
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < reward:
                    raise ValueError("Insufficient balance to place bounty.")

                await self.process_treasury_transaction(
                    wallet_id, -reward, f"Placed bounty on {target_id}"
                )

                stmt = select(Bounty).where(
                    Bounty.target_id == target_id, Bounty.active == True
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.reward += reward
                    bounty = existing
                else:
                    bounty = Bounty(
                        target_id=target_id,
                        issuer_id=issuer_id,
                        reward=reward,
                        active=True,
                    )
                    session.add(bounty)

            await session.commit()
            return bounty

    async def user_has_bounty(self, target_id: int) -> bool:
        """
        Return True if there is at least one active bounty on target_id.
        Does NOT assume uniqueness—just looks for ANY active row.
        """
        async with self.async_sessionmaker() as session:
            stmt = select(
                exists().where(Bounty.target_id == target_id, Bounty.active == True)
            )
            result = await session.execute(stmt)
            return result.scalar()

    async def get_bounty_amount(self, target_id: int) -> Decimal:
        """
        Returns the total sum of all active bounties for target_id.
        If there are none, returns Decimal('0').
        """
        async with self.async_sessionmaker() as session:
            stmt = select(func.sum(Bounty.reward).label("total_reward")).where(
                Bounty.target_id == target_id, Bounty.active == True
            )
            result = await session.execute(stmt)
            total = result.scalar_one()
            return total if total is not None else Decimal("0")

    async def get_top_bounty_users(self, limit=10) -> List[Tuple[int, Decimal]]:
        """
        Returns a list of (target_id, total_reward) for active bounties,
        ordered descending, limited to `limit`.
        """
        async with self.async_sessionmaker() as session:
            stmt = (
                select(Bounty.target_id, func.sum(Bounty.reward).label("total_reward"))
                .where(Bounty.active == True)
                .group_by(Bounty.target_id)
                .order_by(func.sum(Bounty.reward).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)

            return result.all()

    async def get_active_bounties(self) -> List[Bounty]:
        """Get all active bounties."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Bounty).where(Bounty.active == True))
            return result.scalars().all()

    async def claim_bounty(self, claimer_id: int, target_id: int) -> Bounty:
        """
        Claim and remove the active bounty on target_id.
        Returns the deleted Bounty instance (detached) so you can inspect its data.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(Bounty).where(
                    Bounty.target_id == target_id, Bounty.active == True
                )
                bounty = (await session.execute(stmt)).scalar_one_or_none()
                if not bounty:
                    raise ValueError("No active bounty on this user.")

                reward_amount = bounty.reward

                await session.execute(delete(Bounty).where(Bounty.id == bounty.id))

                claimer_wallet = await self.get_wallet_id_for_user(claimer_id)
                await self.process_treasury_transaction(
                    claimer_wallet, reward_amount, f"Claimed bounty on {target_id}"
                )

            await session.commit()

            bounty.reward = reward_amount
            bounty.active = False
            bounty.claimer_id = claimer_id
            return bounty

    async def get_reputation(self, discord_id: int) -> int:
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(Reputation.reputation).filter_by(discord_id=discord_id)
                )
                rep = result.scalar_one_or_none()
                return rep if rep is not None else 0
        except SQLAlchemyError as e:
            return 0

    async def increment_reputation(self, discord_id: int, amount: int) -> int:
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Reputation)
                        .where(Reputation.discord_id == discord_id)
                        .values(reputation=Reputation.reputation + amount)
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = Reputation(discord_id=discord_id, reputation=amount)
                        session.add(new_user)

                    await session.commit()
                    return await self.get_reputation(discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0

    async def get_heardle_stats(self, discord_id: int) -> int:
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(HeardleGameStats).filter_by(user_id=discord_id)
                )
                stats = result.scalar_one_or_none()
                return stats
        except SQLAlchemyError as e:
            return HeardleGameStats()

    async def add_heardle_win(self, discord_id: int, amount: int = 1) -> int:
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(HeardleGameStats)
                        .where(HeardleGameStats.user_id == discord_id)
                        .values(
                            wins=HeardleGameStats.wins + amount,
                            streak=HeardleGameStats.streak + amount,
                        )
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = HeardleGameStats(
                            user_id=discord_id, wins=amount, losses=0, streak=amount
                        )
                        session.add(new_user)

                    await session.commit()
                    return await self.get_heardle_stats(discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0

    async def add_heardle_loss(self, discord_id: int, amount: int = 1) -> int:
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(HeardleGameStats)
                        .where(HeardleGameStats.user_id == discord_id)
                        .values(losses=HeardleGameStats.losses + amount, streak=0)
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = HeardleGameStats(
                            user_id=discord_id, wins=0, losses=amount, streak=0
                        )
                        session.add(new_user)

                    await session.commit()
                    return await self.get_heardle_stats(discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0

    async def get_top_reputation_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation.discord_id, Reputation.reputation)
                .order_by(Reputation.reputation.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_reputation_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation.discord_id, Reputation.reputation)
                .order_by(Reputation.reputation.asc())
                .limit(limit)
            )
            return result.all()

    async def get_reputation_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation).order_by(Reputation.reputation.desc())
            )
            reputations = result.scalars().all()
            for rank, rep in enumerate(reputations, start=1):
                if rep.discord_id == discord_id:
                    return rank
            return -1

    async def update_sobs(
        self, discord_id: int, sobs_rx_delta: int = 0, sobs_tx_delta: int = 0
    ):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Sobs)
                        .where(Sobs.discord_id == discord_id)
                        .values(
                            sobs_rx=Sobs.sobs_rx + sobs_rx_delta,
                            sobs_tx=Sobs.sobs_tx + sobs_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_sobs = Sobs(
                            discord_id=discord_id,
                            sobs_rx=sobs_rx_delta,
                            sobs_tx=sobs_tx_delta,
                        )
                        session.add(new_sobs)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_skulls(
        self, discord_id: int, skulls_rx_delta: int = 0, skulls_tx_delta: int = 0
    ):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Skulls)
                        .where(Skulls.discord_id == discord_id)
                        .values(
                            skulls_rx=Skulls.skulls_rx + skulls_rx_delta,
                            skulls_tx=Skulls.skulls_tx + skulls_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_skulls = Skulls(
                            discord_id=discord_id,
                            skulls_rx=skulls_rx_delta,
                            skulls_tx=skulls_tx_delta,
                        )
                        session.add(new_skulls)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_flames(
        self, discord_id: int, flames_rx_delta: int = 0, flames_tx_delta: int = 0
    ):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Flames)
                        .where(Flames.discord_id == discord_id)
                        .values(
                            flames_rx=Flames.flames_rx + flames_rx_delta,
                            flames_tx=Flames.flames_tx + flames_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_flames = Flames(
                            discord_id=discord_id,
                            flames_rx=flames_rx_delta,
                            flames_tx=flames_tx_delta,
                        )
                        session.add(new_flames)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_hearts(
        self, discord_id: int, hearts_rx_delta: int = 0, hearts_tx_delta: int = 0
    ):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Hearts)
                        .where(Hearts.discord_id == discord_id)
                        .values(
                            hearts_rx=Hearts.hearts_rx + hearts_rx_delta,
                            hearts_tx=Hearts.hearts_tx + hearts_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_hearts = Hearts(
                            discord_id=discord_id,
                            hearts_rx=hearts_rx_delta,
                            hearts_tx=hearts_tx_delta,
                        )
                        session.add(new_hearts)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_clowns(
        self, discord_id: int, clowns_rx_delta: int = 0, clowns_tx_delta: int = 0
    ):
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Clowns)
                        .where(Clowns.discord_id == discord_id)
                        .values(
                            clowns_rx=Clowns.clowns_rx + clowns_rx_delta,
                            clowns_tx=Clowns.clowns_tx + clowns_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_clowns = Clowns(
                            discord_id=discord_id,
                            clowns_rx=clowns_rx_delta,
                            clowns_tx=clowns_tx_delta,
                        )
                        session.add(new_clowns)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def get_reaction_stats(
        self, discord_id: int, reaction_type: str
    ) -> tuple[int, int]:
        try:
            async with self.async_sessionmaker() as session:
                if reaction_type == "sobs":
                    result = await session.execute(
                        select(Sobs.sobs_rx, Sobs.sobs_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "skulls":
                    result = await session.execute(
                        select(Skulls.skulls_rx, Skulls.skulls_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "flames":
                    result = await session.execute(
                        select(Flames.flames_rx, Flames.flames_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "hearts":
                    result = await session.execute(
                        select(Hearts.hearts_rx, Hearts.hearts_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "clowns":
                    result = await session.execute(
                        select(Clowns.clowns_rx, Clowns.clowns_tx).filter_by(
                            discord_id=discord_id
                        )
                    )

                stats = result.first()
                if stats:
                    return stats
                else:
                    return 0, 0
        except SQLAlchemyError as e:
            return 0, 0

    async def get_top_sobs_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Sobs.discord_id, Sobs.sobs_rx)
                .order_by(Sobs.sobs_rx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_sobs_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Sobs.discord_id, Sobs.sobs_tx)
                .order_by(Sobs.sobs_tx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_sobs_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Sobs).order_by(Sobs.sobs_rx.desc()))
            sobs_list = result.scalars().all()
            for rank, sobs in enumerate(sobs_list, start=1):
                if sobs.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_skulls_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls.discord_id, Skulls.skulls_rx)
                .order_by(Skulls.skulls_rx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_skulls_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls.discord_id, Skulls.skulls_tx)
                .order_by(Skulls.skulls_tx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_skulls_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls).order_by(Skulls.skulls_rx.desc())
            )
            skulls_list = result.scalars().all()
            for rank, skulls in enumerate(skulls_list, start=1):
                if skulls.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_flames_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames.discord_id, Flames.flames_rx)
                .order_by(Flames.flames_rx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_flames_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames.discord_id, Flames.flames_tx)
                .order_by(Flames.flames_tx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_flames_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames).order_by(Flames.flames_rx.desc())
            )
            flames_list = result.scalars().all()
            for rank, flames in enumerate(flames_list, start=1):
                if flames.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_hearts_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts.discord_id, Hearts.hearts_rx)
                .order_by(Hearts.hearts_rx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_hearts_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts.discord_id, Hearts.hearts_tx)
                .order_by(Hearts.hearts_tx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_hearts_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts).order_by(Hearts.hearts_rx.desc())
            )
            hearts_list = result.scalars().all()
            for rank, hearts in enumerate(hearts_list, start=1):
                if hearts.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_clowns_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns.discord_id, Clowns.clowns_rx)
                .order_by(Clowns.clowns_rx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_bottom_clowns_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns.discord_id, Clowns.clowns_tx)
                .order_by(Clowns.clowns_tx.desc())
                .limit(limit)
            )
            return result.all()

    async def get_clowns_user_rank(self, discord_id: int) -> int:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns).order_by(Clowns.clowns_rx.desc())
            )
            clowns_list = result.scalars().all()
            for rank, clowns in enumerate(clowns_list, start=1):
                if clowns.discord_id == discord_id:
                    return rank
            return -1

    async def toggle_self_reactions(self, guild_id: int) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ReactionSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()
                if not settings:
                    new_settings = ReactionSettings(
                        guild_id=guild_id, self_reactions_enabled=False
                    )
                    session.add(new_settings)
                    return False
                else:
                    settings.self_reactions_enabled = (
                        not settings.self_reactions_enabled
                    )
                    return settings.self_reactions_enabled

    async def is_self_reactions_enabled(self, guild_id: int) -> bool:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ReactionSettings).filter_by(guild_id=guild_id)
            )
            settings = result.scalar_one_or_none()
            return settings.self_reactions_enabled if settings else False

    async def is_user_blacklisted(self, user_id: str) -> bool:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Blacklist).filter_by(user_id=user_id))
            return result.scalar_one_or_none() is not None

    async def get_blacklisted_users(self) -> list:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Blacklist))
            return result.scalars().all()

    async def add_command_role_restriction(
        self, guild_id: int, command_name: str, role_id: int
    ):
        """Add a role restriction for a command in a guild."""
        from .models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id,
                        CommandRoleRestriction.command_name == command_name,
                        CommandRoleRestriction.role_id == role_id,
                    )
                )
                existing = result.scalar_one_or_none()

                if not existing:
                    restriction = CommandRoleRestriction(
                        guild_id=guild_id, command_name=command_name, role_id=role_id
                    )
                    session.add(restriction)

    async def remove_command_role_restriction(
        self, guild_id: int, command_name: str, role_id: int
    ) -> bool:
        """Remove a role restriction for a command in a guild."""
        from .models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id,
                        CommandRoleRestriction.command_name == command_name,
                        CommandRoleRestriction.role_id == role_id,
                    )
                )
                return result.rowcount > 0

    async def get_command_restrictions(self, guild_id: int):
        """Get all command restrictions for a guild."""
        from .models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandRoleRestriction).where(
                    CommandRoleRestriction.guild_id == guild_id
                )
            )
            return result.scalars().all()

    async def clear_all_command_restrictions(self, guild_id: int) -> int:
        """Clear all command restrictions for a guild."""
        from .models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id
                    )
                )
                return result.rowcount

    async def check_command_role_restriction(
        self, guild_id: int, command_name: str, user_roles: list
    ) -> bool:
        """Check if a user has permission to use a restricted command."""
        from .models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandRoleRestriction).where(
                    CommandRoleRestriction.guild_id == guild_id,
                    CommandRoleRestriction.command_name == command_name,
                )
            )
            restrictions = result.scalars().all()

            if not restrictions:
                return True

            user_role_ids = []
            for role in user_roles:
                if hasattr(role, "id"):
                    user_role_ids.append(role.id)
                elif isinstance(role, int):
                    user_role_ids.append(role)

            required_role_ids = [restriction.role_id for restriction in restrictions]

            has_permission = any(
                role_id in user_role_ids for role_id in required_role_ids
            )

            return has_permission

    async def fetch_command_data(
        self, user_id: int, command_name: str, channel_id: int = None
    ):
        """Fetch the user blacklist status, command cooldown, and command enabled status in a single query."""
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

    async def add_favorite_song(self, user_id: int, song_title: str):
        """Add a favorite song for the user."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    favorite_song = FavoriteSongs(
                        user_id=user_id, song_title=song_title
                    )
                    session.add(favorite_song)
                await session.commit()
        except SQLAlchemyError as e:
            logging.info(f"Error adding favorite song: {str(e)}")

    async def get_favorite_songs(self, user_id: int) -> list:
        """Retrieve all favorite songs for a specific user."""
        try:
            async with self.async_sessionmaker() as session:
                query = select(FavoriteSongs).filter_by(user_id=user_id)
                result = await session.execute(query)
                return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving favorite songs: {str(e)}")
            return []

    async def remove_favorite_song(self, user_id: int, song_title: str):
        """Remove a favorite song for a user."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(FavoriteSongs).where(
                            FavoriteSongs.user_id == user_id,
                            FavoriteSongs.song_title == song_title,
                        )
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error removing favorite song: {str(e)}")

    async def clear_favorite_songs(self, user_id: int):
        """Clear all favorite songs for a user."""
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(FavoriteSongs).where(FavoriteSongs.user_id == user_id)
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error clearing favorite songs: {str(e)}")

    async def set_user_timezone(self, user_id: int, timezone: str):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserTimezone).where(UserTimezone.user_id == user_id)
                )

                user_timezone = UserTimezone(user_id=user_id, timezone=timezone)
                session.add(user_timezone)
            await session.commit()

    async def get_user_timezone(self, user_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserTimezone.timezone).filter_by(user_id=user_id)
            )
            timezone = result.scalar_one_or_none()
            return timezone

    async def set_user_location(self, user_id: int, location: str):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserLocation).where(UserLocation.user_id == user_id)
                )

                user_location = UserLocation(user_id=user_id, location=location)
                session.add(user_location)
            await session.commit()

    async def get_user_location(self, user_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserLocation.location).filter_by(user_id=user_id)
            )
            location = result.scalar_one_or_none()
            return location

    async def increment_win(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
    ) -> tuple[str, str]:
        """Insert a win entry into game history."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameHistory(
                        user_id=user_id,
                        game_name=game_name,
                        outcome="win",
                        wagered=bet,
                        client_seed=client_seed,
                        used_server_seed=seed_used,  # should be None at insert time
                        nonce=nonce,
                        hash=hash_hex,
                    )
                )
                revealed_seed, new_hash = await self._reveal_and_rotate_in_tx(
                    session, user_id
                )
                return revealed_seed, new_hash

    async def increment_loss(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
    ) -> tuple[str, str]:
        """Insert a loss entry into game history."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameHistory(
                        user_id=user_id,
                        game_name=game_name,
                        outcome="loss",
                        wagered=bet,
                        client_seed=client_seed,
                        used_server_seed=seed_used,  # should be None at insert time
                        nonce=nonce,
                        hash=hash_hex,
                    )
                )
                revealed_seed, new_hash = await self._reveal_and_rotate_in_tx(
                    session, user_id
                )
                return revealed_seed, new_hash

    async def get_total_wins(self, user_id: int) -> int:
        """Count the total wins for a user based on game history."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.user_id == user_id, GameHistory.outcome == "win")
            )
            return result.scalar_one()

    async def get_global_wins(self) -> int:
        """Count total wins across all users and games."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.outcome == "win")
            )
            return result.scalar_one()

    async def get_total_losses(self, user_id: int) -> int:
        """Count the total losses for a user based on game history."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.user_id == user_id, GameHistory.outcome == "loss")
            )
            return result.scalar_one()

    async def get_global_losses(self) -> int:
        """Count total losses across all users and games."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.outcome == "loss")
            )
            return result.scalar_one()

    async def get_user_game_history(self, user_id: int, limit: int = 10) -> list:
        """Retrieve the game history for a specific user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory)
                .where(GameHistory.user_id == user_id)
                .order_by(GameHistory.created_at.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def get_top_game_winners(self, game_name: str, limit: int = 10) -> list:
        """Retrieve the top users with the most wins by game."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory.user_id, func.count().label("win_count"))
                .where(GameHistory.game_name == game_name, GameHistory.outcome == "win")
                .group_by(GameHistory.user_id)
                .order_by(func.count().desc())
                .limit(limit)
            )
            return result.all()

    async def get_top_game_losers(self, game_name: str, limit: int = 10) -> list:
        """Retrieve the top users with the most losses by game."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory.user_id, func.count().label("loss_count"))
                .where(
                    GameHistory.game_name == game_name, GameHistory.outcome == "loss"
                )
                .group_by(GameHistory.user_id)
                .order_by(func.count().desc())
                .limit(limit)
            )
            return result.all()

    async def fetch_game_for_user(
        self, user_id: int, game_name: str, nonce: int
    ) -> GameHistory | None:
        """Fetch a specific game history entry for a user by game name and nonce."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory).where(
                    GameHistory.user_id == user_id,
                    GameHistory.game_name == game_name,
                    GameHistory.nonce == nonce,
                )
            )
            return result.scalar_one_or_none()

    async def get_wager_stats(
        self, user_id: int, game_name: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Returns (total_wagered,
                 total_wagered_on_wins,
                 total_wagered_on_losses)
        """
        async with self.async_sessionmaker() as session:
            # make a 0 of the same NUMERIC type
            zero = literal_column("0", type_=GameHistory.wagered.type)

            win_case = case(
                (GameHistory.outcome == "win", GameHistory.wagered), else_=zero
            )
            loss_case = case(
                (GameHistory.outcome == "loss", GameHistory.wagered), else_=zero
            )

            totals = await session.execute(
                select(
                    func.coalesce(func.sum(GameHistory.wagered), zero).label("total"),
                    func.coalesce(func.sum(win_case), zero).label("win_total"),
                    func.coalesce(func.sum(loss_case), zero).label("loss_total"),
                ).where(
                    GameHistory.user_id == user_id, GameHistory.game_name == game_name
                )
            )
            total, win_total, loss_total = totals.one()
            return total, win_total, loss_total

    async def get_game_stats(self, user_id: int, game_name: str) -> tuple[int, int]:
        """Count wins and losses for a specific game and user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(
                    func.sum(case((GameHistory.outcome == "win", 1), else_=0)).label(
                        "wins"
                    ),
                    func.sum(case((GameHistory.outcome == "loss", 1), else_=0)).label(
                        "losses"
                    ),
                ).where(
                    GameHistory.user_id == user_id, GameHistory.game_name == game_name
                )
            )
            wins, losses = result.first()
            return (wins or 0, losses or 0)

    async def get_work_streak(self, user_id: int) -> int:
        """Retrieve the current work streak for a user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Streak).filter(Streak.user_id == user_id)
            )
            streak = result.scalars().first()

            if streak:
                if (datetime.now() - streak.last_worked).total_seconds() < 24 * 3600:
                    return streak.streak_count
                else:
                    return 0
            else:
                return 0

    async def update_work_streak(self, user_id: int, streak_count: int):
        """Update the work streak for a user."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Streak).filter(Streak.user_id == user_id)
                )
                streak = result.scalars().first()

                if streak:
                    streak.streak_count = streak_count
                    streak.last_worked = datetime.now()
                else:
                    new_streak = Streak(
                        user_id=user_id,
                        streak_count=streak_count,
                        last_worked=datetime.now(),
                    )
                    session.add(new_streak)

                await session.commit()

    async def add_task(self, user_id: int, task: str) -> Task:
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Task).where(Task.user_id == user_id).order_by(Task.order_index)
            )
            return result.scalars().all()

    async def complete_task(self, user_id: int, task_order: int) -> bool:
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(delete(Task).where(Task.user_id == user_id))
            await session.commit()
            return result.rowcount

    async def add_jtc_setup(self, guild_id: int, jtc_channel_id: int):
        """Save JTC setup for a server. Prevent duplicate setups."""
        async with self.async_sessionmaker() as session:
            existing = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            if existing.scalar_one_or_none():
                return False

            setting = JTCSettings(guild_id=guild_id, jtc_channel_id=jtc_channel_id)
            session.add(setting)
            await session.commit()
            return True

    async def get_jtc_channels(self, guild_id: int):
        """Retrieve the JTC channel IDs for a guild."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            return result.scalar_one_or_none()

    async def add_temp_channel(self, guild_id: int, user_id: int, channel_id: int):
        """Log a temporary voice channel."""
        async with self.async_sessionmaker() as session:
            temp_channel = TempVoiceChannel(
                guild_id=guild_id, owner_id=user_id, channel_id=channel_id
            )
            session.add(temp_channel)
            await session.commit()

    async def remove_temp_channel(self, channel_id: int):
        """Removes a temporary voice channel entry from the database."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(TempVoiceChannel).where(
                        TempVoiceChannel.channel_id == channel_id
                    )
                )
                await session.commit()

    async def get_temp_channel_owner(self, channel_id: int):
        """Retrieve the owner of a temporary voice channel."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(TempVoiceChannel).where(
                    TempVoiceChannel.channel_id == channel_id
                )
            )
            channel = result.scalar_one_or_none()
            return channel.owner_id if channel else None

    async def set_temp_channel_owner(self, channel_id: int, user_id: int):
        """Set the owner of a temporary voice channel."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    update(TempVoiceChannel)
                    .where(TempVoiceChannel.channel_id == channel_id)
                    .values(owner_id=user_id)
                )
                await session.commit()

    async def store_control_panel_message(self, guild_id: int, message_id: int):
        """Stores the control panel message ID so it's not sent twice."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.control_panel_message_id = message_id
                await session.commit()

    async def get_control_panel_message(self, guild_id: int):
        """Retrieves the stored control panel message ID."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings.control_panel_message_id).where(
                    JTCSettings.guild_id == guild_id
                )
            )
            return result.scalar_one_or_none()

    async def log_name_change(
        self, user_id: int, old_name: str, new_name: str, change_type: str
    ):
        """
        Logs a username or nickname change for a user.

        :param user_id: Discord user ID
        :param old_name: The old username or nickname
        :param new_name: The new username or nickname
        :param change_type: Type of change - "username" or "nickname"
        """
        timestamp = datetime.now()
        async with self.async_sessionmaker() as session:
            try:
                new_entry = UserNameHistory(
                    user_id=user_id,
                    old_name=old_name,
                    new_name=new_name,
                    change_type=change_type,
                    timestamp=timestamp,
                )
                session.add(new_entry)
                await session.commit()
                print(
                    f"{change_type.capitalize()} change logged: {old_name} -> {new_name}"
                )
            except Exception as e:
                print(f"Error logging name change: {e}")

    async def get_name_history(self, user_id: int) -> List[dict]:
        """
        Retrieves the username and nickname history for a specified user.

        Uses a column-only query + .mappings() to avoid creating full ORM objects,
        which reduces memory overhead and improves performance for large histories.
        """
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(
                        UserNameHistory.old_name.label("old_name"),
                        UserNameHistory.new_name.label("new_name"),
                        UserNameHistory.change_type.label("change_type"),
                        UserNameHistory.timestamp.label("timestamp"),
                    )
                    .where(UserNameHistory.user_id == user_id)
                    .order_by(UserNameHistory.timestamp.desc())
                )
                result = await session.execute(stmt)
                # .mappings().all() returns a list of dict-like Mapping objects
                rows = result.mappings().all()
                return [dict(row) for row in rows]
            except SQLAlchemyError as e:
                logger.error(f"Error retrieving name history for {user_id}: {e}")
                return []

    async def has_name_history(self, user_id: int) -> bool:
        """
        Checks if a user has any name change history.

        :param user_id: Discord user ID
        :return: True if history exists, False otherwise
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserNameHistory).where(UserNameHistory.user_id == user_id)
            )
            return result.scalars().first() is not None

    async def clear_name_history(self, user_id: int):
        """
        Clears the name change history for a specified user.

        :param user_id: Discord user ID
        """
        async with self.async_sessionmaker() as session:
            try:
                await session.execute(
                    delete(UserNameHistory).where(UserNameHistory.user_id == user_id)
                )
                await session.commit()
                print(f"Name history cleared for user {user_id}")
            except Exception as e:
                print(f"Error clearing name history: {e}")

    async def log_user_roles(self, user_id: int, roles: List[int]):
        """Logs the user's roles to the database."""
        async with self.get_session() as session:
            user_role_history = UserRoleHistory(
                user_id=user_id, roles=roles, timestamp=datetime.now()
            )
            session.add(user_role_history)
            await session.commit()

    async def get_user_roles(self, user_id: int) -> List[int]:
        """Retrieves the user's roles from the database."""
        async with self.get_session() as session:
            result = await session.execute(
                select(UserRoleHistory).where(UserRoleHistory.user_id == user_id)
            )
            record = result.scalars().first()
            if record:
                return record.roles
            else:
                return []

    async def remove_user_roles(self, user_id: int):
        """Removes the user's role records from the database."""
        async with self.get_session() as session:
            await session.execute(
                delete(UserRoleHistory).where(UserRoleHistory.user_id == user_id)
            )
            await session.commit()

    async def is_lockdown_channel(self, guild_id: int, channel_id: int) -> bool:
        """
        Checks if a channel is in the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: True if the channel is locked down, False otherwise
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                return result.scalars().first() is not None

            except Exception as e:
                print(
                    f"Error checking lockdown status for channel {channel_id} "
                    f"in guild {guild_id}: {e}"
                )
                return False

    async def add_lockdown_channel(
        self, guild_id: int, channel_id: int
    ) -> Optional[LockdownChannel]:
        """
        Adds a channel to the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: The LockdownChannel entry, or None on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                existing = result.scalars().first()
                if existing:
                    return existing

                lock = LockdownChannel(guild_id=guild_id, channel_id=channel_id)
                session.add(lock)
                await session.commit()
                return lock

            except Exception as e:
                print(
                    f"Error adding lockdown channel {channel_id} "
                    f"for guild {guild_id}: {e}"
                )
                return None

    async def remove_lockdown_channel(self, guild_id: int, channel_id: int) -> bool:
        """
        Removes a channel from the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: True if deleted, False if not found or on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                existing = result.scalars().first()
                if not existing:
                    return False

                await session.delete(existing)
                await session.commit()
                return True

            except Exception as e:
                print(
                    f"Error removing lockdown channel {channel_id} "
                    f"for guild {guild_id}: {e}"
                )
                return False

    async def get_lockdown_channels(self, guild_id: int) -> List[int]:
        """
        Retrieves all channel IDs in the lockdown list for a guild.

        :param guild_id: Discord guild ID
        :return: List of channel IDs, or empty list on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel.channel_id)
                    .where(LockdownChannel.guild_id == guild_id)
                    .order_by(LockdownChannel.channel_id)
                )
                return result.scalars().all()

            except Exception as e:
                print(f"Error retrieving lockdown channels for guild {guild_id}: {e}")
                return []

    async def get_juul(self, guild_id: int) -> Optional[Juul]:
        """
        Read-only helper. Returns a Juul (or None) by opening its own session.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none()

    async def set_juul_holder(self, guild_id: int, user_id: int):
        """
        Pass or Steal: update holder_id (and always reset locked=False) in a single session.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.holder_id = user_id
                    juul.locked = False
                else:
                    juul = Juul(guild_id=guild_id, holder_id=user_id)
                    session.add(juul)

    async def set_juul_lock(self, guild_id: int, locked: bool):
        """
        Lock or Unlock: simply flip the locked flag in one transaction.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.locked = locked
                else:
                    juul = Juul(guild_id=guild_id, locked=locked)
                    session.add(juul)

    async def get_juul_lock(self, guild_id: int) -> bool:
        """
        Read-only helper. Returns the locked status of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul.locked).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none() or False

    async def increment_juul_hits(self, guild_id: int):
        """
        (You can leave these “increment” helpers as they are,
        since they don’t rely on get_juul().)
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(hits=Juul.hits + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, hits=1))

    async def increment_juul_passes(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(passes=Juul.passes + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, passes=1))

    async def increment_juul_steals(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(steals=Juul.steals + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, steals=1))

    async def get_auto_roles(self, guild_id: int) -> list[int]:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.auto_role_ids).where(
                    ServerSettings.guild_id == guild_id
                )
            )
            return result.scalar_one_or_none() or []

    async def add_auto_role(self, guild_id: int, role_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    if settings.auto_role_ids is None:
                        settings.auto_role_ids = []
                    if role_id not in settings.auto_role_ids:
                        settings.auto_role_ids.append(role_id)

    async def remove_auto_role(self, guild_id: int, role_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings and settings.auto_role_ids:
                    settings.auto_role_ids = [
                        r for r in settings.auto_role_ids if r != role_id
                    ]

    async def add_user_alt(self, main_id: int, guild_id: int, alt_id: int) -> None:
        """
        Add a new alt-user mapping. Does nothing if the mapping already exists.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                exists = await session.execute(
                    select(UserAlt).where(
                        UserAlt.main_user_id == main_id,
                        UserAlt.guild_id == guild_id,
                        UserAlt.alt_user_id == alt_id,
                    )
                )
                if exists.scalars().first():
                    return
                session.add(
                    UserAlt(main_user_id=main_id, guild_id=guild_id, alt_user_id=alt_id)
                )

    async def remove_user_alt(self, main_id: int, guild_id: int, alt_id: int) -> None:
        """
        Remove a specific alt-user mapping.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserAlt).where(
                        UserAlt.main_user_id == main_id,
                        UserAlt.guild_id == guild_id,
                        UserAlt.alt_user_id == alt_id,
                    )
                )

    async def clear_user_alts(self, main_id: int, guild_id: int) -> None:
        """
        Remove all alt-user mappings for a given main user in a guild.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserAlt).where(
                        UserAlt.main_user_id == main_id, UserAlt.guild_id == guild_id
                    )
                )

    async def get_user_alts(self, main_id: int, guild_id: int) -> List[int]:
        """
        Get a list of alt user IDs for a given main user in a guild.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserAlt.alt_user_id).where(
                    UserAlt.main_user_id == main_id, UserAlt.guild_id == guild_id
                )
            )
            return [row[0] for row in result.all()]

    async def get_all_linked_user_ids(self, user_id: int, guild_id: int) -> List[int]:
        """
        Get all user IDs linked to the given user (both mains and alts) within the same guild.
        Traverses relationships iteratively in Python to avoid recursion errors in SQL.
        """
        seen = {user_id}
        queue = [user_id]
        async with self.async_sessionmaker() as session:
            while queue:
                current = queue.pop(0)
                # Find direct alts where current is main
                result_alt = await session.execute(
                    select(UserAlt.alt_user_id).where(
                        UserAlt.main_user_id == current, UserAlt.guild_id == guild_id
                    )
                )
                alt_ids = [row[0] for row in result_alt]
                # Find mains where current is an alt
                result_main = await session.execute(
                    select(UserAlt.main_user_id).where(
                        UserAlt.alt_user_id == current, UserAlt.guild_id == guild_id
                    )
                )
                main_ids = [row[0] for row in result_main]
                for uid in alt_ids + main_ids:
                    if uid not in seen:
                        seen.add(uid)
                        queue.append(uid)
        # Remove the original user
        linked = list(seen)
        linked.remove(user_id)
        return linked

    async def set_nuke_msg(self, guild_id: int, new_message: str):
        """Change the nuke confirmation message for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).where(ServerSettings.guild_id == guild_id)
                )
                settings = result.scalar_one_or_none()
                if settings:
                    settings.nuke_msg = new_message
                else:
                    settings = ServerSettings(guild_id=guild_id, nuke_msg=new_message)
                    session.add(settings)

    async def get_nuke_msg(self, guild_id: int) -> str:
        """Retrieve the nuke confirmation message for a guild."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.nuke_msg).where(
                    ServerSettings.guild_id == guild_id
                )
            )
            nuke_msg = result.scalar_one_or_none()
            return (
                nuke_msg
                if nuke_msg is not None
                else "TOXIC HUMANS IS NEVER COMING!!! - DENKOV"
            )

    async def delete_all_data_for_user(self, user_id: int):
        """Deletes all data in the database for a specific user."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(Reputation).where(Reputation.discord_id == user_id)
                )
                await session.execute(
                    delete(UserLocation).where(UserLocation.user_id == user_id)
                )
                await session.execute(
                    delete(GameStats).where(GameStats.user_id == user_id)
                )
                await session.execute(
                    delete(HeardleGameStats).where(HeardleGameStats.user_id == user_id)
                )
                await session.execute(delete(Task).where(Task.user_id == user_id))
                await session.execute(delete(Item).where(Item.user_id == user_id))
                await session.execute(
                    delete(LastFMusers).where(LastFMusers.discord_id == user_id)
                )
                await session.execute(
                    delete(LastFMvotes).where(LastFMvotes.discord_id == user_id)
                )
                await session.execute(delete(Sobs).where(Sobs.discord_id == user_id))
                await session.execute(
                    delete(Skulls).where(Skulls.discord_id == user_id)
                )
                await session.execute(
                    delete(Flames).where(Flames.discord_id == user_id)
                )
                await session.execute(
                    delete(Hearts).where(Hearts.discord_id == user_id)
                )
                await session.execute(
                    delete(FavoriteSongs).where(FavoriteSongs.user_id == user_id)
                )
                await session.execute(delete(Streak).where(Streak.user_id == user_id))
                await session.execute(
                    delete(UserRoleHistory).where(UserRoleHistory.user_id == user_id)
                )
                await session.execute(
                    delete(UserNameHistory).where(UserNameHistory.user_id == user_id)
                )
                await session.execute(
                    delete(BoosterRole).where(BoosterRole.user_id == user_id)
                )
                await session.execute(
                    delete(UserTimezone).where(UserTimezone.user_id == user_id)
                )
                await session.execute(
                    delete(TempVoiceChannel).where(TempVoiceChannel.owner_id == user_id)
                )
                await session.execute(delete(Wallet).where(Wallet.user_id == user_id))
                await session.execute(
                    delete(Transaction).where(Transaction.from_user_id == user_id)
                )
                await session.execute(
                    delete(Transaction).where(Transaction.to_user_id == user_id)
                )
                await session.commit()
