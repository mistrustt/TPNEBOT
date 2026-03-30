from sqlalchemy.future import select
from sqlalchemy import update, delete, text, exists, case, literal_column, distinct
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
    CommandUsageDaily,
    CommandLatencyDaily,
    CommandErrorDaily,
    DailyUserExposure,
    Blacklist,
    BoosterRole,
    Transaction,
    CryptoAsset,
    CryptoPrice,
    Supply,
    EconomicMetricsHistory,
    UserEconomicPreferences,
    Reputation,
    Wallet,
    Block,
    Loan,
    LoanPayment,
    Item,
    ItemType,
    EffectType,
    ShopItem,
    ItemCooldown,
    ActiveEffect,
    TradeLog,
    Bounty,
    Skulls,
    Flames,
    Hearts,
    Clowns,
    Sobs,
    ReactionSettings,
    HeardleGameStats,
    GameHistory,
    GameSession,
    GameSessionEvent,
    UserRoleHistory,
    Task,
    JTCSettings,
    TempVoiceChannel,
    UserNameHistory,
    MinesSettings,
    LockdownChannel,
    Juul,
    UserAlt,
    SuspiciousActivityLog,
    SuspiciousActivityType,
    TransferHistory,
    Job,
    VIPTier,
    UserVIP,
    RakebackBalance,
    RakebackTransaction,
)
from datetime import datetime, timedelta, timezone
import discord
import uuid
import logging
from decimal import Decimal, ROUND_HALF_UP
from utils.amount import AmountUtils

logger = logging.getLogger("discord_bot")

ADMIN_IDS = {284439598422163476, 538773310704582666, 657182369240973312}  # Owner IDs

_LAST_REBALANCE_AT: Optional[datetime] = None  # module-level memo

# Wealth tier thresholds (percentage of total supply)
WEALTH_TIERS = {
    "tier_1": {"threshold": Decimal("0.005"), "label": "mild"},       # 0.5%
    "tier_2": {"threshold": Decimal("0.01"), "label": "moderate"},    # 1%
    "tier_3": {"threshold": Decimal("0.02"), "label": "severe"},     # 2%
    "tier_4": {"threshold": Decimal("0.05"), "label": "extreme"},    # 5%
}

# Penalty multipliers per tier (applied to different transaction types)
TIER_PENALTIES = {
    0: {"bet": Decimal("1.0"), "loan": Decimal("1.0"), "fee": Decimal("1.0"), "transfer": Decimal("1.0")},
    1: {"bet": Decimal("0.8"), "loan": Decimal("0.7"), "fee": Decimal("1.5"), "transfer": Decimal("0.8")},
    2: {"bet": Decimal("0.5"), "loan": Decimal("0.4"), "fee": Decimal("2.0"), "transfer": Decimal("0.5")},
    3: {"bet": Decimal("0.25"), "loan": Decimal("0.2"), "fee": Decimal("3.0"), "transfer": Decimal("0.3")},
    4: {"bet": Decimal("0.1"), "loan": Decimal("0.05"), "fee": Decimal("5.0"), "transfer": Decimal("0.1")},
}

class DatabaseManager:

    
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, echo=False)
        self.async_sessionmaker = sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._latest_metrics = {}

    async def initialize(self):
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

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

    async def create_game_session(
        self,
        game_name: str,
        *,
        guild_id: int | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        owner_id: int | None = None,
        participants: list[int] | None = None,
        wager_total: Decimal | None = None,
        state: dict | None = None,
        rng: dict | None = None,
        errors: list | None = None,
    ) -> uuid.UUID:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = GameSession(
                    game_name=game_name,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    participants=participants,
                    wager_total=wager_total,
                    state=state,
                    rng=rng,
                    errors=errors,
                )
                session.add(gs)
            return gs.id

    async def get_game_session(self, session_id: uuid.UUID) -> GameSession | None:
        async with self.async_sessionmaker() as session:
            return await session.get(GameSession, session_id)

    async def list_game_sessions(
        self,
        *,
        game_name: str | None = None,
        guild_id: int | None = None,
        owner_id: int | None = None,
        limit: int = 50,
    ) -> list[GameSession]:
        async with self.async_sessionmaker() as session:
            stmt = select(GameSession).order_by(GameSession.created_at.desc())
            if game_name:
                stmt = stmt.where(GameSession.game_name == game_name)
            if guild_id:
                stmt = stmt.where(GameSession.guild_id == guild_id)
            if owner_id:
                stmt = stmt.where(GameSession.owner_id == owner_id)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_game_session(
        self,
        session_id: uuid.UUID,
        *,
        status: str | None = None,
        message_id: int | None = None,
        participants: list[int] | None = None,
        wager_total: Decimal | None = None,
        state: dict | None = None,
        rng: dict | None = None,
        errors: list | None = None,
    ) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                if status is not None:
                    gs.status = status
                if message_id is not None:
                    gs.message_id = message_id
                if participants is not None:
                    gs.participants = participants
                if wager_total is not None:
                    gs.wager_total = wager_total
                if state is not None:
                    current = gs.state or {}
                    current.update(state)
                    gs.state = current
                if rng is not None:
                    current = gs.rng or {}
                    current.update(rng)
                    gs.rng = current
                if errors is not None:
                    current = list(gs.errors or [])
                    current.extend(errors)
                    gs.errors = current
            return True

    async def add_game_session_event(
        self, session_id: uuid.UUID, event_type: str, payload: dict | None = None
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameSessionEvent(
                        session_id=session_id,
                        event_type=event_type,
                        payload=payload or {},
                    )
                )

    async def get_game_session_events(
        self, session_id: uuid.UUID, limit: int = 50
    ) -> list[GameSessionEvent]:
        async with self.async_sessionmaker() as session:
            stmt = (
                select(GameSessionEvent)
                .where(GameSessionEvent.session_id == session_id)
                .order_by(GameSessionEvent.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add_game_session_refund(
        self,
        session_id: uuid.UUID,
        *,
        user_id: int,
        wallet_id: str,
        amount: str,
        reason: str | None = None,
    ) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                state = gs.state or {}
                refunds = list(state.get("refunds", []))
                refunds.append(
                    {
                        "user_id": user_id,
                        "wallet_id": wallet_id,
                        "amount": amount,
                        "reason": reason or "refund",
                    }
                )
                state["refunds"] = refunds
                gs.state = state
            return True

    async def remove_game_session_refund(
        self, session_id: uuid.UUID, *, user_id: int
    ) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                state = gs.state or {}
                refunds = [r for r in state.get("refunds", []) if r.get("user_id") != user_id]
                state["refunds"] = refunds
                gs.state = state
            return True

    async def end_game_session(
        self,
        session_id: uuid.UUID,
        *,
        outcome: str | None = None,
        reason: str | None = None,
        final_state: dict | None = None,
    ) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                payload = {
                    "outcome": outcome,
                    "reason": reason,
                    "final_state": final_state or gs.state or {},
                }
                session.add(
                    GameSessionEvent(
                        session_id=session_id, event_type="ended", payload=payload
                    )
                )
                await session.delete(gs)
            return True

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

    async def set_watchdog_feature(self, guild_id: int, feature: str, enabled: bool):
        """Set individual watchdog feature toggle"""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    setattr(settings, f"watchdog_{feature}", enabled)
                else:
                    settings = ServerSettings(guild_id=guild_id)
                    setattr(settings, f"watchdog_{feature}", enabled)
                    session.add(settings)
                await session.commit()

    async def get_server_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def add_auto_role(self, guild_id: int, role_id: int) -> None:
        """Adds a role ID to the ServerSettings.auto_role_ids array for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    ids = setting.auto_role_ids or []
                    if role_id not in ids:
                        ids.append(role_id)
                        setting.auto_role_ids = ids
                else:
                    setting = ServerSettings(guild_id=guild_id, auto_role_ids=[role_id])
                    session.add(setting)
                await session.commit()

    async def remove_auto_role(self, guild_id: int, role_id: int) -> None:
        """Removes a role ID from ServerSettings.auto_role_ids for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if not setting or not setting.auto_role_ids:
                    return
                ids = list(setting.auto_role_ids)
                if role_id in ids:
                    ids.remove(role_id)
                    setting.auto_role_ids = ids
                await session.commit()

    async def get_auto_roles(self, guild_id: int) -> list:
        """Returns a list of role IDs configured as autoroles for the guild."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.auto_role_ids).filter_by(guild_id=guild_id)
            )
            ids = result.scalar_one_or_none()
            return ids or []

    async def clear_auto_roles(self, guild_id: int) -> None:
        """Clears autorole configuration for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.auto_role_ids = []
                await session.commit()

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
    

    async def wipe_economy(self, caller_id: int, *, confirm: bool = False, dry_run: bool = False) -> dict:
        """
        Comprehensive economy wipe - resets ALL economy-related tables.
        
        This completely wipes the economy and starts fresh:
        - All wallets, transactions, and supply reset
        - All items, shops, and trade logs cleared
        - All loans, bounties, and payments cleared
        - All game history and sessions cleared
        - All VIP/rakeback data cleared
        - All social currency (reputation, sobs, etc.) cleared
        - All crypto assets and prices cleared
        - All transfer tracking and suspicious activity cleared
        
        Preserved (NOT wiped):
        - Bot configuration and server settings
        - Moderation data (punishments, jails, watchdog)
        - Command statistics and cooldowns
        - User preferences (timezones, locations)
        - Music/LastFM data
        - Role management data
        
        Args:
            caller_id: Discord ID of the caller (must be in ADMIN_IDS)
            confirm: Safety flag - must be True to execute
            dry_run: If True, returns what would be wiped without actually wiping
            
        Returns:
            dict with 'wiped_tables' list and 'dry_run' boolean
        """
        if caller_id not in ADMIN_IDS:
            raise PermissionError("You do not have permission to wipe the economy.")

        if not confirm:
            raise ValueError("`confirm=True` is required as a safety flag.")

        # All economy-related tables to wipe, organized by category
        economy_tables = [
            # Core economy
            "wallets",
            "transactions", 
            "supply",
            
            # Items and trading
            "item_cooldowns",
            "active_effects",
            "trade_logs",
            
            # Loans and bounties
            "bounties",
            "loans",
            "loan_payments",
            
            # Jobs system
            "jobs",
            
            # Games and gambling
            "game_history",
            "game_sessions",
            "game_session_events",
            
            # Crypto
            "crypto_assets",
            
            # Transfer tracking
            "transfer_history",
            "suspicious_activity_log",
            
            # User economy data
            "user_economic_preferences",
            
            # VIP system
            "user_vip",
            "rakeback_balances",
            "rakeback_transactions",
            
            # Economic metrics
            "economic_metrics_history",
        ]

        if dry_run:
            return {
                "dry_run": True,
                "wiped_tables": economy_tables,
                "message": f"Would wipe {len(economy_tables)} economy tables"
            }

        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Build TRUNCATE statement for all economy tables
                tables_str = ", ".join(economy_tables)
                await session.execute(
                    text(f"TRUNCATE TABLE {tables_str} RESTART IDENTITY CASCADE")
                )
            
            # Re-initialize supply record
            await self.initialize_supply_record()

        return {
            "dry_run": False,
            "wiped_tables": economy_tables,
            "message": f"Successfully wiped {len(economy_tables)} economy tables"
        }

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

                new_wallet = Wallet(
                    user_id=user_id,
                    balance=Decimal("0.00"),
                    bank_balance=Decimal("0.00"),
                    client_seed=secrets.token_hex(16),
                    nonce=0,
                )
                session.add(new_wallet)
                await session.flush()

                logging.info(f"Created wallet for user {user_id}.")

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
        return f"{wallet.wallet_id}"

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
                wallet.seed_rotated_at = discord.utils.utcnow()
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
            w.seed_rotated_at = discord.utils.utcnow()
            new_hash = hashlib.sha256(w.server_seed.encode()).hexdigest()
            return None, new_hash

        old_hash = hashlib.sha256(old_seed.encode()).hexdigest()

        # Generate new seed (256-bit recommended)
        new_seed = secrets.token_hex(32)
        w.previous_server_seed = old_seed
        w.server_seed = new_seed
        w.seed_rotated_at = discord.utils.utcnow()

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
                    w.seed_rotated_at = discord.utils.utcnow()

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
            wallet = await session.get(Wallet, wallet_id)
            return wallet.bank_balance if wallet else Decimal("0.00")

    async def deposit_to_bank(self, wallet_id: str, amount: Decimal, description: str):
        """Transfer funds from wallet to bank without affecting treasury."""
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)

                # 1) load + gate
                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                # 2) ensure bank row exists
                bank = await session.get(Wallet, wallet_id)
                if not bank:
                    bank = Wallet(wallet_id=wallet_id, bank_balance=Decimal("0.00"))
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
                    session, "wallets", "wallet_id", wallet_id, +amount, balance_col="bank_balance"
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
                        timestamp=discord.utils.utcnow(),
                    )
                )

            return txid

    async def withdraw_from_bank(
        self, wallet_id: str, amount: Decimal, description: str
    ):
        """Transfer funds from bank to wallet without affecting treasury."""
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)

                wallet = await session.get(Wallet, wallet_id)
                if not wallet:
                    raise ValueError("Wallet not found.")
                if wallet.wallet_frozen:
                    raise ValueError("Wallet is frozen.")

                bank = await session.get(Wallet, wallet_id)
                if not bank:
                    raise ValueError("Bank account missing.")

                await self._atomic_balance_change(
                    session, "wallets", "wallet_id", wallet_id, -amount, balance_col="bank_balance"
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
                        timestamp=discord.utils.utcnow(),
                    )
                )

            return txid

    async def process_p2p_transaction(
        self,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
        description: str,
        guild_id: int = None,
    ):
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        # Calculate wealth-adjusted fee for P2P transfer
        # Base fee rate from dynamic economic factors
        base_fee_rate = await self.get_enhanced_fee_rate("standard")
        base_fee = AmountUtils.round_currency(amount * base_fee_rate)
        
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

                # Apply wealth-adjusted fee based on sender's tier
                adjusted_fee = await self.calculate_wealth_adjusted_fee(sender.user_id, base_fee)
                net_amt = AmountUtils.round_currency(amount)
                total_deduction = net_amt + adjusted_fee

                # 1) atomic moves - sender pays amount + fee, receiver gets amount
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    sender_wallet_id,
                    -total_deduction,
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
                # Fee goes to treasury
                await self._atomic_balance_change(
                    session,
                    "supply",
                    "id",
                    1,
                    +adjusted_fee,
                    balance_col="treasury",
                )

                # 2) record DB txs - main transfer and fee
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
                            timestamp=discord.utils.utcnow(),
                        ),
                        Transaction(
                            id=txid_fee,
                            from_user_id=sender.user_id,
                            to_user_id=0,  # Treasury
                            amount=adjusted_fee,
                            description=f"P2P transfer fee (base: {base_fee_rate:.2%}, wealth-adjusted)",
                            timestamp=discord.utils.utcnow(),
                        ),
                    ]
                )

            await self.update_supply()

        # Anti-cheat logging (outside transaction to avoid blocking)
        if guild_id is not None:
            # Log the transfer
            await self.log_transfer(
                transaction_id=txid_main,
                sender_id=sender.user_id,
                receiver_id=receiver.user_id,
                amount=net_amt,
                guild_id=guild_id,
            )

            # Check for alt transfer
            is_alt_transfer = await self.check_alt_transfer(
                sender.user_id, receiver.user_id, guild_id
            )
            if is_alt_transfer:
                await self.log_suspicious_activity(
                    activity_type=SuspiciousActivityType.ALT_TRANSFER,
                    user_id=sender.user_id,
                    guild_id=guild_id,
                    related_user_ids=[receiver.user_id],
                    amount=net_amt,
                    details={
                        "transaction_id": txid_main,
                        "sender_id": sender.user_id,
                        "receiver_id": receiver.user_id,
                        "description": description,
                    },
                )

            # Check for circular transfers (only if transfer amount is large enough)
            # Only run detection for large transfers to avoid noise
            if net_amt >= Decimal("5000"):
                try:
                    cycles = await self.detect_circular_transfers(
                        user_id=sender.user_id,
                        depth=2,
                        hours=2,
                        min_amount=Decimal("5000"),
                        amount_similarity_threshold=0.8,
                        guild_id=guild_id,
                    )
                    if cycles:
                        for cycle in cycles:
                            await self.log_suspicious_activity(
                                activity_type=SuspiciousActivityType.CIRCULAR_TRANSFER,
                                user_id=sender.user_id,
                                guild_id=guild_id,
                                related_user_ids=cycle["path"],
                                amount=cycle["amount_returned"],
                                details={
                                    "transaction_id": txid_main,
                                    "cycle_path": cycle["path"],
                                    "similarity": cycle["similarity"],
                                    "total_sent": str(cycle["total_sent"]),
                                    "amount_returned": str(cycle["amount_returned"]),
                                },
                            )
                except Exception as e:
                    logging.warning(f"Failed to detect circular transfers: {e}")

        return txid_main

    async def process_treasury_transaction(
        self, wallet_id: str, amount: Decimal, description: str, transaction_type: str = "standard",
        guild_id: int = None,
    ):
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        # Get wallet to determine user for wealth-adjusted fee
        async with self.async_sessionmaker() as session:
            wallet = await session.get(Wallet, wallet_id)
            if not wallet:
                raise ValueError("Wallet missing.")
            user_id = wallet.user_id

        # Calculate base fee rate and apply wealth adjustment
        base_fee_rate = await self.get_enhanced_fee_rate(transaction_type)
        base_fee = AmountUtils.round_currency(abs(amount) * base_fee_rate)
        adjusted_fee = await self.calculate_wealth_adjusted_fee(user_id, base_fee)

        amount = AmountUtils.round_currency(amount)
        if amount == 0:
            raise ValueError("Cannot process zero-amount transaction.")

        gross = abs(amount)
        fee = adjusted_fee
        net = gross - fee

        fee_rate = fee / gross if gross > 0 else Decimal("0.00")

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
                            timestamp=discord.utils.utcnow(),
                        ),
                        Transaction(
                            id=tid_fee,
                            from_user_id=from_uid,
                            to_user_id=to_uid if from_uid == 0 else 0,
                            amount=fee,
                            description=f"{description} (fee @ {fee_rate:.2%})",
                            timestamp=discord.utils.utcnow(),
                        ),
                    ]
                )

            await self.update_supply()

        # Anti-cheat logging (outside transaction to avoid blocking)
        if guild_id is not None:
            # Log the treasury transaction
            await self.log_transfer(
                transaction_id=tid_main,
                sender_id=from_uid,
                receiver_id=to_uid,
                amount=net,
                guild_id=guild_id,
            )

            # Check for suspicious patterns on large treasury transactions
            # Only check for outgoing treasury payments (rewards, gambling wins, etc.)
            if amount > 0 and net >= Decimal("5000"):
                # Check if receiver has suspicious activity patterns
                # This helps detect potential exploits or abuse of treasury systems
                user_id = to_uid

                # Check for rapid successive large treasury receipts
                recent_large_receipts = await self.get_recent_large_treasury_receipts(
                    user_id=user_id,
                    hours=1,
                    min_amount=Decimal("5000"),
                    guild_id=guild_id,
                )

                if len(recent_large_receipts) >= 5:
                    await self.log_suspicious_activity(
                        activity_type=SuspiciousActivityType.RAPID_SUCCESSIVE_TRANSACTIONS,
                        user_id=user_id,
                        guild_id=guild_id,
                        related_user_ids=[],
                        amount=net,
                        details={
                            "transaction_id": tid_main,
                            "description": description,
                            "recent_receipts_count": len(recent_large_receipts),
                            "time_window_hours": 1,
                            "threshold_amount": "5000",
                        },
                    )

        return tid_main

    async def refund_transaction(self, txid: str, reason: str, treasury_fallback: bool = True):
        """
        Refund a transaction.

        Args:
            txid: The transaction ID to refund
            reason: The reason for the refund
            treasury_fallback: If True, treasury will cover if receiver has insufficient funds

        Returns:
            The refund transaction ID
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                tx = await session.get(Transaction, txid)
                if not tx:
                    raise ValueError("Transaction not found.")
                if tx.amount == 0:
                    raise ValueError("Cannot refund zero-amount transaction.")

                from_wallet_id = await self.get_wallet_id_for_user(tx.from_user_id)
                to_wallet_id = await self.get_wallet_id_for_user(tx.to_user_id)

                # Always give money back to the original sender
                await self._atomic_balance_change(
                    session,
                    "wallets",
                    "wallet_id",
                    from_wallet_id,
                    +tx.amount,
                )

                # Try to take money from the receiver
                try:
                    await self._atomic_balance_change(
                        session,
                        "wallets",
                        "wallet_id",
                        to_wallet_id,
                        -tx.amount,
                    )
                except ValueError as e:
                    # Receiver has insufficient funds
                    if treasury_fallback:
                        # Treasury will cover the shortfall
                        supply = await session.get(Supply, 1)
                        if supply and supply.treasury >= tx.amount:
                            supply.treasury -= tx.amount
                            logger.warning(
                                f"Refund {txid}: Receiver had insufficient funds. "
                                f"Treasury covered {tx.amount}. Reason: {reason}"
                            )
                        else:
                            # Create money (mint) if treasury is also insufficient
                            logger.warning(
                                f"Refund {txid}: Treasury insufficient. Minting {tx.amount}. "
                                f"Reason: {reason}"
                            )
                    else:
                        raise ValueError(
                            f"Cannot refund: receiver has insufficient funds and treasury fallback is disabled."
                        ) from e

                # Record refund transaction
                refund_txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=refund_txid,
                        from_user_id=tx.to_user_id,
                        to_user_id=tx.from_user_id,
                        amount=-tx.amount,
                        description=f"Refund for {txid}: {reason}",
                        timestamp=discord.utils.utcnow(),
                    )
                )

            await self.update_supply()
        return refund_txid

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

                # Ensure supply fields are initialized (handle NULL values)
                if supply.treasury is None:
                    supply.treasury = Decimal("0.00")
                if supply.circulating is None:
                    supply.circulating = Decimal("0.00")
                if supply.total_supply is None:
                    supply.total_supply = Decimal("0.00")

                wallet_total_result = await session.execute(
                    select(func.sum(Wallet.balance))
                )
                wallet_total = wallet_total_result.scalar() or Decimal("0.00")

                cryptocurrency_total_result = await session.execute(
                    select(func.sum(CryptoAsset.amount * CryptoPrice.price))
                    .join(CryptoPrice, CryptoAsset.symbol == CryptoPrice.symbol)
                )
                cryptocurrency_total = cryptocurrency_total_result.scalar() or Decimal("0.00")

                bank_total_result = await session.execute(
                    select(func.sum(Wallet.bank_balance))
                )
                bank_total = bank_total_result.scalar() or Decimal("0.00")

                circulating_supply = AmountUtils.round_currency(wallet_total + bank_total + cryptocurrency_total)

                total_supply = AmountUtils.round_currency(circulating_supply + supply.treasury)

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
                        (Wallet.balance + func.coalesce(Wallet.bank_balance, 0)).label("total_balance"),
                    )
                    .order_by((Wallet.balance + func.coalesce(Wallet.bank_balance, 0)).desc())
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
                        (Wallet.balance + func.coalesce(Wallet.bank_balance, 0)).label(
                            "total_balance"
                        ),
                    )
                    .select_from(Wallet)
                    .outerjoin(Wallet, Wallet.wallet_id == Wallet.wallet_id)
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
                    select(Wallet.user_id, Wallet.bank_balance)
                    .order_by(Wallet.bank_balance.desc())
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
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)
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
                    timestamp=discord.utils.utcnow(),
                )
                session.add(transaction_db)

            await self.update_supply()

    async def burn_currency(self, amount: Decimal, description: str):
        """
        Burns currency by removing it from treasury, effectively reducing total supply.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                amount = AmountUtils.round_currency(amount)
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
                    timestamp=discord.utils.utcnow(),
                )
                session.add(transaction_db)

            await self.update_supply()

    async def validate_economy(self):
        """
        Cross-check that:
        - sum of all wallets & banks == supply.circulating
        - supply.circulating + supply.treasury == supply.total_supply
        Returns True if all checks out, False otherwise.
        """

        await self.update_supply()

        async with self.async_sessionmaker() as session:
            result_wallet = await session.execute(select(func.sum(Wallet.balance)))
            total_wallet = result_wallet.scalar() or Decimal("0.00")

            result_bank = await session.execute(select(func.sum(Wallet.bank_balance)))
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
        supply = await self.get_supply_record()
        treasury, total_supply = supply.treasury, supply.total_supply

        if total_supply <= 0:
            return {
                "treasury_health": Decimal("0"),
                "risk_scalar": Decimal("0"),
                "fee_rate": Decimal("0"),
                "passive_income_rate": Decimal("0"),
                "treasury_balance": Decimal("0"),
                "total_supply": Decimal("0"),
                "velocity_of_money": Decimal("0"),
                "liquidity_ratio": Decimal("0"),
                "volatility_index": Decimal("0"),
            }

        treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))

        # Load cached/latest metrics
        metrics = getattr(self, "_latest_metrics", {})

        # Try to get pre-calculated metrics, fallback to direct calculation
        if metrics and metrics.get("liquidity_ratio") is not None and metrics.get("velocity_of_money") is not None:
            liquidity_ratio = metrics.get("liquidity_ratio", Decimal("0"))
            velocity_of_money = metrics.get("velocity_of_money", Decimal("0"))
            volatility_index = metrics.get("volatility_index", Decimal("0.02"))
        else:
            # Fallback calculations when metrics are not available
            logger.debug("Using fallback calculations for economic metrics")

            # Calculate liquidity ratio
            liquidity_ratio = (supply.circulating / total_supply).quantize(Decimal("0.0001")) if total_supply > 0 else Decimal("0")

            # Get volatility index from metrics or use default
            volatility_index = metrics.get("volatility_index", Decimal("0.02"))

            # Calculate velocity of money with protection against division by zero
            async with self.async_sessionmaker() as session:
                # Transaction volume (last 24 hours)
                yesterday = discord.utils.utcnow() - timedelta(days=1)
                volume_stmt = select(func.sum(Transaction.amount)).where(
                    Transaction.timestamp >= yesterday
                )
                volume_result = await session.execute(volume_stmt)
                transaction_volume = volume_result.scalar() or Decimal("0.00")

                if supply.circulating > 0:
                    velocity_of_money = (transaction_volume / supply.circulating).quantize(Decimal("0.0001"))
                else:
                    velocity_of_money = Decimal("0")

        TARGET = Decimal("0.50")
        MIN_HW = Decimal("0.30")
        MAX_HW = Decimal("0.90")
        STEP = Decimal("0.05")
        CAP = total_supply * Decimal("0.02")
        COOLDOWN = timedelta(hours=1)

        global _LAST_REBALANCE_AT
        now = discord.utils.utcnow()

        need_rebalance = (
            (treasury_health < MIN_HW or treasury_health > MAX_HW)
            and (_LAST_REBALANCE_AT is None or now - _LAST_REBALANCE_AT > COOLDOWN)
        )

        if need_rebalance:
            gap = (TARGET * total_supply) - treasury
            adj = AmountUtils.round_currency(min(abs(gap) * STEP, CAP))

            if adj > 0:
                try:
                    if gap > 0:
                        pass
                    else:
                        await self.burn_currency(adj, f"Auto-burn {adj} (health {treasury_health:.2%})")
                        logger.info(f"[AUTO-REBALANCE] Burning {adj} units due to high treasury health.")
                    _LAST_REBALANCE_AT = now
                    supply = await self.get_supply_record()
                    treasury, total_supply = supply.treasury, supply.total_supply
                    treasury_health = (treasury / total_supply).quantize(Decimal("0.0001"))
                except Exception as e:
                    logger.error(f"[AUTO-REBALANCE ERROR]: {e}")

        BASE_FEE = Decimal("0.01")
        BASE_PASS = Decimal("0.005")
        MAX_FEE_RATE = Decimal("0.10")
        MIN_FEE_RATE = Decimal("0.001")

        if treasury_health < TARGET:
            d = TARGET - treasury_health
            fee_base = (BASE_FEE * (1 + d**2)).quantize(Decimal("0.0001"))
            passive = (BASE_PASS * (1 - d)).quantize(Decimal("0.0001"))
            risk = (treasury_health / TARGET).quantize(Decimal("0.0001"))
        else:
            fee_base = BASE_FEE
            passive = BASE_PASS
            risk = (Decimal("1.0") + (treasury_health - TARGET)).quantize(Decimal("0.0001"))

        # Adjust fee based on volatility
        fee = fee_base * (1 + volatility_index * Decimal("0.5"))
        fee = max(MIN_FEE_RATE, min(MAX_FEE_RATE, fee))

        return {
            "treasury_health": treasury_health,
            "risk_scalar": risk,
            "fee_rate": fee,
            "passive_income_rate": passive,
            "treasury_balance": treasury,
            "total_supply": total_supply,
            "target_ratio": TARGET,
            "min_health_threshold": MIN_HW,
            "max_health_threshold": MAX_HW,
            "velocity_of_money": velocity_of_money,
            "liquidity_ratio": liquidity_ratio,
            "volatility_index": volatility_index,
        }

    async def get_user_wealth_tier(self, user_id: int) -> int:
        """
        Calculate the user's wealth tier based on their percentage of total supply.
        
        Wealth is calculated on-demand as: wallet + bank + crypto at current prices.
        
        Returns:
            int: Tier 0-4 (0 = no penalty, 1-4 = escalating penalties)
        """
        async with self.async_sessionmaker() as session:
            # Get user's wallet and bank
            result = await session.execute(select(Wallet).where(Wallet.user_id == user_id))
            wallet = result.scalar_one_or_none()
            if not wallet:
                return 0
            
            user_wealth = wallet.balance + wallet.bank_balance
            
            # Get user's crypto holdings at current prices
            crypto_result = await session.execute(
                select(CryptoAsset).where(CryptoAsset.user_id == user_id)
            )
            crypto_holdings = crypto_result.scalars().all()
            
            for holding in crypto_holdings:
                # Get latest price for this symbol
                price_result = await session.execute(
                    select(CryptoPrice)
                    .where(CryptoPrice.symbol == holding.symbol)
                    .order_by(CryptoPrice.timestamp.desc())
                    .limit(1)
                )
                price_entry = price_result.scalar_one_or_none()
                if price_entry:
                    user_wealth += holding.amount * price_entry.price
            
            # Get total supply from economy snapshot
            snapshot = await self.get_economy_snapshot()
            total_supply = snapshot["total_supply"]
            
            if total_supply <= 0:
                return 0
            
            # Calculate user's percentage of total supply
            user_ratio = user_wealth / total_supply
            
            # Determine tier (check from highest to lowest)
            if user_ratio >= self.WEALTH_TIERS["tier_4"]["threshold"]:
                return 4
            elif user_ratio >= self.WEALTH_TIERS["tier_3"]["threshold"]:
                return 3
            elif user_ratio >= self.WEALTH_TIERS["tier_2"]["threshold"]:
                return 2
            elif user_ratio >= self.WEALTH_TIERS["tier_1"]["threshold"]:
                return 1
            else:
                return 0

    def get_wealth_penalty_multipliers(self, tier: int) -> dict:
        """
        Get penalty multipliers for a given wealth tier.
        
        Args:
            tier: Wealth tier (0-4)
            
        Returns:
            dict: Multipliers for 'bet', 'loan', 'fee', 'transfer'
        """
        return self.TIER_PENALTIES.get(tier, self.TIER_PENALTIES[0])

    async def get_wealth_tier_info(self, user_id: int) -> dict:
        """
        Get comprehensive wealth tier information for a user.
        
        Args:
            user_id: The user's ID
            
        Returns:
            dict: Contains tier, wealth, percentage, thresholds, and multipliers
        """
        async with self.async_sessionmaker() as session:
            # Get user's wallet and bank
            wallet = await session.get(Wallet, user_id)
            if not wallet:
                return {
                    "tier": 0,
                    "wealth": Decimal("0"),
                    "percentage": Decimal("0"),
                    "next_tier_threshold": self.WEALTH_TIERS["tier_1"]["threshold"],
                    "multipliers": self.TIER_PENALTIES[0],
                }
            
            user_wealth = wallet.balance + wallet.bank_balance
            
            # Get user's crypto holdings at current prices
            crypto_result = await session.execute(
                select(CryptoAsset).where(CryptoAsset.user_id == user_id)
            )
            crypto_holdings = crypto_result.scalars().all()
            
            for holding in crypto_holdings:
                # Get latest price for this symbol
                price_result = await session.execute(
                    select(CryptoPrice)
                    .where(CryptoPrice.symbol == holding.symbol)
                    .order_by(CryptoPrice.timestamp.desc())
                    .limit(1)
                )
                price_entry = price_result.scalar_one_or_none()
                if price_entry:
                    user_wealth += holding.amount * price_entry.price
            
            # Get total supply from economy snapshot
            snapshot = await self.get_economy_snapshot()
            total_supply = snapshot["total_supply"]
            
            if total_supply <= 0:
                return {
                    "tier": 0,
                    "wealth": user_wealth,
                    "percentage": Decimal("0"),
                    "next_tier_threshold": self.WEALTH_TIERS["tier_1"]["threshold"],
                    "multipliers": self.TIER_PENALTIES[0],
                }
            
            # Calculate user's percentage of total supply
            user_ratio = user_wealth / total_supply
            
            # Determine tier
            tier = await self.get_user_wealth_tier(user_id)
            
            # Determine next tier threshold
            next_tier_threshold = None
            if tier == 0:
                next_tier_threshold = self.WEALTH_TIERS["tier_1"]["threshold"]
            elif tier == 1:
                next_tier_threshold = self.WEALTH_TIERS["tier_2"]["threshold"]
            elif tier == 2:
                next_tier_threshold = self.WEALTH_TIERS["tier_3"]["threshold"]
            elif tier == 3:
                next_tier_threshold = self.WEALTH_TIERS["tier_4"]["threshold"]
            # tier 4 has no next tier
            
            return {
                "tier": tier,
                "wealth": user_wealth,
                "percentage": user_ratio,
                "next_tier_threshold": next_tier_threshold,
                "multipliers": self.get_wealth_penalty_multipliers(tier),
            }

    async def calculate_wealth_adjusted_fee(self, user_id: int, base_fee: Decimal) -> Decimal:
        """
        Calculate a wealth-adjusted fee for high-wealth users.
        
        Args:
            user_id: The user's ID
            base_fee: The base fee amount
            
        Returns:
            Decimal: Adjusted fee based on user's wealth tier
        """
        user_tier = await self.get_user_wealth_tier(user_id)
        if user_tier == 0:
            return base_fee
        
        multipliers = self.get_wealth_penalty_multipliers(user_tier)
        return AmountUtils.round_currency(base_fee * multipliers["fee"])

    async def get_max_gamble_amount(
        self, user_id: int, raise_if_limited: bool = False, max_payout_multiplier: Decimal = Decimal("1.0")
    ) -> Decimal:
        """
        Calculates the maximum amount a user can gamble based on dynamic risk controls.

        Risk model includes:
        - Dynamic base bet size scaled to treasury health.
        - Absolute cap on treasury exposure.
        - Whale mitigation (players exceeding 1% of total supply).
        - Minimum floor for newcomers.
        - Adaptive adjustments based on:
            - Market volatility
            - Liquidity levels
            - Recent transaction volume
            - Active user engagement
        - Max payout multiplier adjustment for high-payout games.

        Args:
            user_id: The user's ID
            raise_if_limited: If True, raises ValueError when user exceeds limit
            max_payout_multiplier: Maximum payout multiplier for the game (e.g., 50.0 for crash)
        """

        # ---- constants -------------------------------------------------------
        MAX_TREASURY_EXPOSURE = Decimal("0.02")  # 2% of treasury
        MIN_ABSOLUTE_FLOOR = Decimal("100.00")   # Floor value for small players

        # ---- fetch user data -------------------------------------------------
        wallet = await self.get_wallet_by_user_id(user_id)
        wallet_bal = await self.get_wallet_balance(wallet.wallet_id)
        bank_bal = await self.get_bank_balance(wallet.wallet_id)
        crypto_assets = await self.get_crypto_assets(user_id)
        
        # Calculate crypto value from assets
        crypto_bal = Decimal("0.00")
        if crypto_assets:
            # Get current prices for all symbols held
            symbols = [asset.symbol for asset in crypto_assets]
            async with self.async_sessionmaker() as session:
                price_result = await session.execute(
                    select(CryptoPrice.symbol, CryptoPrice.price).where(
                        CryptoPrice.symbol.in_(symbols)
                    )
                )
                prices = {sym: Decimal(str(price)) for sym, price in price_result.fetchall()}
            
            # Sum up each asset's value (amount * current_price)
            for asset in crypto_assets:
                price = prices.get(asset.symbol, Decimal("0.00"))
                crypto_bal += Decimal(str(asset.amount)) * price
        
        user_total = wallet_bal + bank_bal + crypto_bal

        # ---- economy snapshot -----------------------------------------------
        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")  # Economy not initialized or broken

        # ---- dynamic economic factors ----------------------------------------
        factors = await self.get_economic_factors()
        health = factors["treasury_health"]
        volatility_index = factors.get("volatility_index", Decimal("0.02"))
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.50"))
        transaction_volume = factors.get("transaction_volume", Decimal("0.00"))
        active_users = factors.get("active_users", 1)

        # ---- dynamic base coefficient ---------------------------------------
        # Scale base coefficient inversely with volatility
        VOLATILITY_SENSITIVITY = Decimal("0.5")
        adjusted_health = max(Decimal("0.0"), health - (volatility_index * VOLATILITY_SENSITIVITY))

        if adjusted_health >= Decimal("0.60"):
            base_coeff = Decimal("0.01")  # 1%
        elif adjusted_health >= Decimal("0.30"):
            t = (adjusted_health - Decimal("0.30")) / Decimal("0.30")
            base_coeff = Decimal("0.0025") + (Decimal("0.01") - Decimal("0.0025")) * (t ** 2)
        else:
            base_coeff = Decimal("0.00125")  # 0.125%

        # ---- apply wealth tier penalty --------------------------------------
        user_tier = await self.get_user_wealth_tier(user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers["bet"]  # Apply tier-based bet multiplier

        # ---- adjust for liquidity scarcity ----------------------------------
        LIQUIDITY_ADJUSTMENT_FACTOR = Decimal("0.8")
        if liquidity_ratio < Decimal("0.3"):  # Less than 30% circulating
            base_coeff *= LIQUIDITY_ADJUSTMENT_FACTOR  # Tighten betting limits

        # ---- adjust for surge in activity -----------------------------------
        AVG_VOLUME_EXPECTED = Decimal("50000")  # Expected daily volume baseline
        if transaction_volume > AVG_VOLUME_EXPECTED * Decimal("1.5"):
            base_coeff *= Decimal("0.9")  # Lower bet size during high traffic

        # ---- adjust for low engagement --------------------------------------
        MIN_ACTIVE_USERS = 10
        if active_users < MIN_ACTIVE_USERS:
            base_coeff *= Decimal("0.9")  # Discourage gambling during low participation

        # ---- calculate tentative limit --------------------------------------
        by_treasury = AmountUtils.round_currency(treasury * base_coeff)
        hard_cap = AmountUtils.round_currency(treasury * MAX_TREASURY_EXPOSURE)
        # Adjust hard cap by max payout multiplier to limit treasury exposure
        # For games with 50x max payout, this ensures max_bet * 50 <= hard_cap
        adjusted_hard_cap = hard_cap / max_payout_multiplier if max_payout_multiplier > Decimal("1.0") else hard_cap
        provisional = min(by_treasury, adjusted_hard_cap, user_total)

        # ---- enforce adaptive minimum floor ---------------------------------
        adaptive_floor = min(
            MIN_ABSOLUTE_FLOOR,
            AmountUtils.round_currency(user_total * Decimal("0.02")),
        )
        final_limit = max(provisional, adaptive_floor)

        # ---- optional enforcement -------------------------------------------
        if raise_if_limited and user_total > final_limit:
            raise ValueError(
                f"You’re limited to **{final_limit} {self.currency_name}** "
                f"this hand by risk management."
            )

        return final_limit

    async def get_max_loan_amount(self, user_id: int) -> Decimal:
        """
        Calculate the maximum safe loan amount a user can take based on their balance and economic factors.
        Uses a similar risk model to gambling limits but with more leniency.
        """
        wallet = await self.get_wallet_by_user_id(user_id)
        wallet_bal = await self.get_wallet_balance(wallet.wallet_id)
        bank_bal = await self.get_bank_balance(wallet.wallet_id)
        user_total = wallet_bal + bank_bal

        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")

        factors = await self.get_economic_factors()
        health = factors["treasury_health"]

        if health < Decimal("0.25"):
            base_coeff = Decimal("0.0005")  # 0.05% of treasury
        elif health < Decimal("0.50"):
            base_coeff = Decimal("0.001")  # 0.1% of treasury 
        else:
            base_coeff = Decimal("0.002")  # 0.2% of treasury

        # ---- apply wealth tier penalty --------------------------------------
        user_tier = await self.get_user_wealth_tier(user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers["loan"]  # Apply tier-based loan multiplier

        max_loan = AmountUtils.round_currency(treasury * base_coeff)
        return min(max_loan, user_total * Decimal("1.5"))  # Cap at 1.5x user's total balance

    async def get_max_transfer_amount(self, user_id: int) -> Decimal:
        """
        Calculate the maximum amount a user can transfer in a single transaction.
        Uses wealth tier penalties to limit high-wealth users' transfer capabilities.
        
        Args:
            user_id: The user's ID
            
        Returns:
            Decimal: Maximum transfer amount
        """
        wallet = await self.get_wallet_by_user_id(user_id)
        wallet_bal = await self.get_wallet_balance(wallet.wallet_id)
        bank_bal = await self.get_bank_balance(wallet.wallet_id)
        
        # Get user's crypto holdings at current prices
        crypto_assets = await self.get_crypto_assets(user_id)
        crypto_bal = Decimal("0.00")
        if crypto_assets:
            symbols = [asset.symbol for asset in crypto_assets]
            async with self.async_sessionmaker() as session:
                price_result = await session.execute(
                    select(CryptoPrice.symbol, CryptoPrice.price).where(
                        CryptoPrice.symbol.in_(symbols)
                    )
                )
                prices = {sym: Decimal(str(price)) for sym, price in price_result.fetchall()}
            
            for asset in crypto_assets:
                price = prices.get(asset.symbol, Decimal("0.00"))
                crypto_bal += Decimal(str(asset.amount)) * price
        
        user_total = wallet_bal + bank_bal + crypto_bal

        supply = await self.get_supply_record()
        treasury = supply.treasury
        total_supply = supply.total_supply

        if treasury <= 0 or total_supply <= 0:
            return Decimal("0.00")

        factors = await self.get_economic_factors()
        health = factors["treasury_health"]

        # Base transfer coefficient based on treasury health
        if health < Decimal("0.25"):
            base_coeff = Decimal("0.05")  # 5% of user's wealth
        elif health < Decimal("0.50"):
            base_coeff = Decimal("0.10")  # 10% of user's wealth
        elif health < Decimal("0.75"):
            base_coeff = Decimal("0.15")  # 15% of user's wealth
        else:
            base_coeff = Decimal("0.20")  # 20% of user's wealth

        # Apply wealth tier penalty
        user_tier = await self.get_user_wealth_tier(user_id)
        if user_tier > 0:
            multipliers = self.get_wealth_penalty_multipliers(user_tier)
            base_coeff *= multipliers["transfer"]  # Apply tier-based transfer multiplier

        # Calculate max transfer as percentage of user's wealth
        max_transfer = AmountUtils.round_currency(user_total * base_coeff)
        
        # Absolute cap based on treasury (max 5% of treasury in single transfer)
        treasury_cap = AmountUtils.round_currency(treasury * Decimal("0.05"))
        
        return min(max_transfer, treasury_cap)

    async def collect_daily_economy_snapshot(self):
        """
        Collects a snapshot of key economy metrics using existing tables.
        Can be used for logging, alerting, or feeding into predictive models.
        """
        today = discord.utils.utcnow().date()
        yesterday_start = today - timedelta(days=1)
        yesterday_end = today

        async with self.async_sessionmaker() as session:
            # Supply info
            supply = await session.get(Supply, 1)
            treasury_balance = supply.treasury
            total_supply = supply.total_supply
            circulating_supply = supply.circulating

            # Wallet summary
            wallet_sum_result = await session.execute(select(func.sum(Wallet.balance)))
            wallet_total = wallet_sum_result.scalar() or Decimal("0.00")

            bank_sum_result = await session.execute(select(func.sum(Wallet.bank_balance)))
            bank_total = bank_sum_result.scalar() or Decimal("0.00")

            avg_wallet_balance = Decimal("0.00")
            wallet_count_result = await session.execute(select(func.count(Wallet.wallet_id)))
            wallet_count = wallet_count_result.scalar()
            if wallet_count and wallet_count > 0:
                avg_wallet_balance = AmountUtils.round_currency(wallet_total / wallet_count)

            # Transaction volume (yesterday)
            volume_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.timestamp >= yesterday_start,
                Transaction.timestamp < yesterday_end
            )
            volume_result = await session.execute(volume_stmt)
            transaction_volume = volume_result.scalar() or Decimal("0.00")

            # Active users (distinct senders/receivers yesterday)
            active_users_stmt = select(
                func.count(distinct(Transaction.from_user_id)).label('senders'),
                func.count(distinct(Transaction.to_user_id)).label('receivers')
            ).where(
                Transaction.timestamp >= yesterday_start,
                Transaction.timestamp < yesterday_end
            )
            active_users_result = await session.execute(active_users_stmt)
            row = active_users_result.fetchone()
            active_users = (row.senders or 0) + (row.receivers or 0)

            # Volatility estimate using Gini coefficient (bounded 0-1)
            # Gini = 0 means perfect equality, Gini = 1 means maximum inequality
            balances_stmt = select(Wallet.balance)
            balances_result = await session.execute(balances_stmt)
            balances = [float(r[0]) for r in balances_result.fetchall()]

            if len(balances) > 1:
                # Calculate Gini coefficient for wealth distribution
                sorted_balances = sorted(balances)
                n = len(sorted_balances)
                mean = sum(sorted_balances) / n

                if mean > 0:
                    # Gini formula: G = sum(|x_i - x_j|) / (2 * n^2 * mean)
                    # Efficient formula: G = (2 * sum(i * x_i)) / (n * sum(x_i)) - (n + 1) / n
                    # Simplified: G = cumsum / (n^2 * mean) where cumsum = sum((2i - n - 1) * x_i)
                    cumsum = sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(sorted_balances))
                    gini = cumsum / (n * n * mean)
                    # Clamp to [0, 1] for safety (floating point edge cases)
                    volatility_index = Decimal(str(max(0.0, min(1.0, gini)))).quantize(Decimal("0.0001"))
                else:
                    volatility_index = Decimal("0.00")
            else:
                volatility_index = Decimal("0.00")

            # Log or persist as needed
            logger.info({
                "date": str(today),
                "treasury_balance": float(treasury_balance),
                "total_supply": float(total_supply),
                "circulating_supply": float(circulating_supply),
                "avg_wallet_balance": float(avg_wallet_balance),
                "transaction_volume": float(transaction_volume),
                "active_users": active_users,
                "volatility_index": float(volatility_index),
            })

            # Calculate additional economic metrics
            liquidity_ratio = (circulating_supply / total_supply).quantize(Decimal("0.0001")) if total_supply > 0 else Decimal("0")

            # Calculate velocity of money with protection against division by zero
            if circulating_supply > 0:
                velocity_of_money = (transaction_volume / circulating_supply).quantize(Decimal("0.0001"))
            else:
                velocity_of_money = Decimal("0")

            # Get current economic factors for additional metrics
            economic_factors = await self.get_economic_factors()

            # Store in historical table
            historical_record = EconomicMetricsHistory(
                date=today,
                total_supply=total_supply,
                circulating_supply=circulating_supply,
                treasury_balance=treasury_balance,
                treasury_health=economic_factors.get("treasury_health", Decimal("0")),
                liquidity_ratio=liquidity_ratio,
                velocity_of_money=velocity_of_money,
                volatility_index=volatility_index,
                transaction_volume=transaction_volume,
                active_users=active_users,
                avg_wallet_balance=avg_wallet_balance,
                fee_rate=economic_factors.get("fee_rate", Decimal("0")),
                passive_income_rate=economic_factors.get("passive_income_rate", Decimal("0"))
            )

            # Check if record already exists for today
            existing_stmt = select(EconomicMetricsHistory).where(
                EconomicMetricsHistory.date == today
            )
            existing_result = await session.execute(existing_stmt)
            existing_record = existing_result.scalar_one_or_none()

            if existing_record:
                # Update existing record
                for key, value in historical_record.__dict__.items():
                    if not key.startswith('_') and key != 'id':
                        setattr(existing_record, key, value)
            else:
                # Insert new record
                session.add(historical_record)

            await session.commit()

            # Optionally store in Redis/file/local cache for use in dynamic adjustments
            self._latest_metrics = {
                "date": today,
                "treasury_balance": treasury_balance,
                "total_supply": total_supply,
                "circulating_supply": circulating_supply,
                "avg_wallet_balance": avg_wallet_balance,
                "transaction_volume": transaction_volume,
                "active_users": active_users,
                "volatility_index": volatility_index,
                "liquidity_ratio": liquidity_ratio,
                "velocity_of_money": velocity_of_money,
            }

        logger.info("[DAILY SNAPSHOT] Economy metrics collected.")

    async def get_economy_snapshot(self) -> dict:
        """
        Get the current economy snapshot.
        Returns cached metrics if available, otherwise calculates fresh snapshot.

        Returns:
            Dictionary with economy metrics including total_supply, circulating_supply, etc.
        """
        # Return cached metrics if available
        if hasattr(self, '_latest_metrics') and self._latest_metrics:
            return self._latest_metrics

        # Otherwise calculate fresh snapshot
        async with self.async_sessionmaker() as session:
            # Get supply info
            supply = await session.get(Supply, 1)
            if not supply:
                return {
                    "total_supply": Decimal("0"),
                    "circulating_supply": Decimal("0"),
                    "treasury_balance": Decimal("0"),
                }

            return {
                "total_supply": supply.total_supply,
                "circulating_supply": supply.circulating,
                "treasury_balance": supply.treasury,
            }

    async def get_or_create_user_economic_preferences(self, user_id: int) -> UserEconomicPreferences:
        """
        Get user economic preferences, creating default ones if they don't exist.

        Args:
            user_id: Discord user ID

        Returns:
            UserEconomicPreferences object
        """
        async with self.async_sessionmaker() as session:
            # Try to get existing preferences
            stmt = select(UserEconomicPreferences).where(UserEconomicPreferences.user_id == user_id)
            result = await session.execute(stmt)
            preferences = result.scalar_one_or_none()

            # If no preferences exist, create default ones
            if not preferences:
                preferences = UserEconomicPreferences(user_id=user_id)
                session.add(preferences)
                await session.commit()

            return preferences

    async def update_user_economic_preferences(self, user_id: int, **kwargs) -> None:
        """
        Update user economic preferences.

        Args:
            user_id: Discord user ID
            **kwargs: Preference fields to update
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(UserEconomicPreferences).where(UserEconomicPreferences.user_id == user_id)
                result = await session.execute(stmt)
                preferences = result.scalar_one_or_none()

                if not preferences:
                    preferences = UserEconomicPreferences(user_id=user_id)
                    session.add(preferences)

                # Update provided fields
                for key, value in kwargs.items():
                    if hasattr(preferences, key):
                        setattr(preferences, key, value)

                await session.commit()

    async def get_users_for_economic_alerts(self) -> list:
        """
        Get all users who have economic alerts enabled.

        Returns:
            List of user IDs
        """
        async with self.async_sessionmaker() as session:
            stmt = select(UserEconomicPreferences.user_id).where(
                UserEconomicPreferences.economic_alerts_enabled == True
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def check_and_send_economic_alerts(self) -> dict:
        """
        Check economic conditions and send alerts to users who have them enabled.

        Returns:
            Dictionary with alert statistics
        """
        # Get current economic factors
        factors = await self.get_economic_factors()
        velocity = float(factors.get("velocity_of_money", 0))
        liquidity = float(factors.get("liquidity_ratio", 0))
        volatility = float(factors.get("volatility_index", 0))

        # Get users with alerts enabled
        user_ids = await self.get_users_for_economic_alerts()

        alerts_sent = {
            "velocity": 0,
            "liquidity": 0,
            "volatility": 0,
            "total_users": len(user_ids)
        }

        # For now, we'll just return the stats without actually sending DMs
        # In a real implementation, we would send DMs to users through the bot

        return alerts_sent

    async def get_personalized_economic_recommendations(self, user_id: int) -> dict:
        """
        Get personalized economic recommendations based on user preferences and current conditions.

        Args:
            user_id: Discord user ID

        Returns:
            Dictionary with recommendations
        """
        # Get user preferences
        preferences = await self.get_or_create_user_economic_preferences(user_id)

        # Get current economic factors
        factors = await self.get_economic_factors()
        health_score = await self.get_economic_health_score()

        recommendations = {
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_risk_profile": preferences.risk_tolerance,
            "user_investment_style": preferences.investment_style,
            "economic_health_score": health_score["score"],
            "economic_health_status": health_score["status"],
            "current_conditions": {
                "treasury_health": float(factors.get("treasury_health", 0)),
                "liquidity_ratio": float(factors.get("liquidity_ratio", 0)),
                "velocity_of_money": float(factors.get("velocity_of_money", 0)),
                "volatility_index": float(factors.get("volatility_index", 0)),
                "fee_rate": float(factors.get("fee_rate", 0))
            },
            "recommendations": []
        }

        # Generate recommendations based on conditions
        liquidity_ratio = float(factors.get("liquidity_ratio", 0))
        velocity_of_money = float(factors.get("velocity_of_money", 0))
        volatility_index = float(factors.get("volatility_index", 0))

        # Liquidity-based recommendations (tiered to match scoring)
        # Scoring: 0.3-0.8 = 25pts (perfect), <0.3 scales down: 25 * (ratio/0.3)
        # So: 0.25 → 21pts (yellow), 0.15 → 12.5pts (orange), <0.15 → red
        if liquidity_ratio < 0.15:
            recommendations["recommendations"].append({
                "type": "liquidity",
                "priority": "high",
                "message": "Critical liquidity shortage. Economy is tight - cash is valuable.",
                "action": "hold_cash"
            })
        elif liquidity_ratio < 0.3:
            recommendations["recommendations"].append({
                "type": "liquidity",
                "priority": "medium",
                "message": "Low liquidity detected. Consider holding cash as opportunities may arise.",
                "action": "hold_cash"
            })
        elif liquidity_ratio > 0.85:
            recommendations["recommendations"].append({
                "type": "liquidity",
                "priority": "medium",
                "message": "Very high liquidity. Consider investments or large transactions.",
                "action": "consider_investing"
            })

        # Velocity-based recommendations (tiered to match scoring)
        # Scoring: velocity * 50, capped at 25 points
        # So: 0.5 → 25pts, 0.3 → 15pts, 0.1 → 5pts
        if velocity_of_money < 0.1:
            recommendations["recommendations"].append({
                "type": "velocity",
                "priority": "high",
                "message": "Very low economic activity. Transaction volumes are critically low.",
                "action": "reduce_trading_activity"
            })
        elif velocity_of_money < 0.2:
            recommendations["recommendations"].append({
                "type": "velocity",
                "priority": "medium",
                "message": "Low economic activity. Transaction volumes are below normal.",
                "action": "reduce_trading_activity"
            })
        elif velocity_of_money > 0.6:
            recommendations["recommendations"].append({
                "type": "velocity",
                "priority": "info",
                "message": "High economic activity. Markets are very active.",
                "action": "increase_activity"
            })

        # Volatility-based recommendations using Gini (0-1 scale)
        # Gini > 0.6 = high inequality, > 0.4 = moderate
        if volatility_index > 0.6:
            recommendations["recommendations"].append({
                "type": "volatility",
                "priority": "high",
                "message": "High wealth inequality detected. Consider economic balancing.",
                "action": "monitor_distribution"
            })
        elif volatility_index > 0.4:
            recommendations["recommendations"].append({
                "type": "volatility",
                "priority": "medium",
                "message": "Moderate wealth inequality present.",
                "action": "track_distribution"
            })

        # Risk tolerance based recommendations
        if preferences.risk_tolerance == "low":
            recommendations["recommendations"].append({
                "type": "risk",
                "priority": "info",
                "message": "Conservative risk profile detected. Focus on stable assets and avoid speculative trades.",
                "action": "focus_stable_assets"
            })
        elif preferences.risk_tolerance == "high":
            recommendations["recommendations"].append({
                "type": "risk",
                "priority": "info",
                "message": "Aggressive risk profile detected. You may consider higher-risk opportunities.",
                "action": "consider_opportunities"
            })

        return recommendations

    async def get_economic_trends(self, days: int = 30) -> dict:
        """
        Get economic trends over the specified number of days.

        Args:
            days: Number of days to analyze (default: 30)

        Returns:
            Dictionary containing trend analysis for key metrics
        """
        cutoff_date = discord.utils.utcnow().date() - timedelta(days=days)

        async with self.async_sessionmaker() as session:
            stmt = select(EconomicMetricsHistory).where(
                EconomicMetricsHistory.date >= cutoff_date
            ).order_by(EconomicMetricsHistory.date)

            result = await session.execute(stmt)
            records = result.scalars().all()

            if not records:
                return {"error": "No historical data available"}

            # Calculate trends
            trends = {
                "period_days": days,
                "data_points": len(records),
                "metrics": {}
            }

            # Define metrics to analyze
            metrics_to_analyze = [
                "treasury_health", "liquidity_ratio", "velocity_of_money",
                "volatility_index", "transaction_volume", "active_users",
                "fee_rate", "passive_income_rate"
            ]

            for metric in metrics_to_analyze:
                values = [getattr(record, metric) for record in records if getattr(record, metric) is not None]
                if values:
                    # Calculate basic statistics
                    current = values[-1] if values else Decimal("0")
                    previous = values[-2] if len(values) > 1 else Decimal("0")

                    if len(values) > 1:
                        avg = sum(values) / len(values)
                        # Calculate trend (slope approximation)
                        trend_slope = (values[-1] - values[0]) / len(values)

                        trends["metrics"][metric] = {
                            "current": float(current),
                            "average": float(avg),
                            "change_from_previous": float(current - previous) if previous != 0 else 0,
                            "change_percent": float(((current - previous) / previous) * 100) if previous != 0 else 0,
                            "trend_slope": float(trend_slope),
                            "min": float(min(values)),
                            "max": float(max(values))
                        }
                    else:
                        trends["metrics"][metric] = {
                            "current": float(current),
                            "average": float(current),
                            "change_from_previous": 0,
                            "change_percent": 0,
                            "trend_slope": 0,
                            "min": float(current),
                            "max": float(current)
                        }

            return trends

    async def get_economic_health_score(self) -> dict:
        """
        Calculate an overall economic health score based on multiple factors.

        Returns:
            Dictionary containing health score and contributing factors
        """
        factors = await self.get_economic_factors()

        # Extract key metrics
        treasury_health = float(factors.get("treasury_health", 0))
        liquidity_ratio = float(factors.get("liquidity_ratio", 0))
        velocity_of_money = float(factors.get("velocity_of_money", 0))
        volatility_index = float(factors.get("volatility_index", 0))

        # Weighted scoring system
        # Treasury health (30% weight)
        treasury_score = treasury_health * 30

        # Liquidity ratio (25% weight) - ideal is around 0.5-0.8
        if 0.3 <= liquidity_ratio <= 0.8:
            liquidity_score = 25  # Perfect score for healthy liquidity
        elif liquidity_ratio > 0.8:
            liquidity_score = 25 * (0.8 / liquidity_ratio)  # Decrease score for too much liquidity
        else:
            liquidity_score = 25 * (liquidity_ratio / 0.3)  # Decrease score for too little liquidity

        # Velocity of money (25% weight) - higher is generally better
        velocity_score = min(25, velocity_of_money * 50)  # Cap at 25 points

        # Volatility index (20% weight) - Gini coefficient (0-1), lower is better
        # Score decreases linearly from 20 to 0 as inequality increases from 0 to 1
        volatility_score = 20 * (1 - volatility_index)

        # Calculate total score (0-100)
        total_score = treasury_score + liquidity_score + velocity_score + volatility_score

        # Normalize to 0-100 range
        health_score = max(0, min(100, total_score))

        # Determine health status
        if health_score >= 80:
            status = "Excellent"
        elif health_score >= 60:
            status = "Good"
        elif health_score >= 40:
            status = "Fair"
        elif health_score >= 20:
            status = "Poor"
        else:
            status = "Critical"

        return {
            "score": round(health_score, 2),
            "status": status,
            "components": {
                "treasury_health": {
                    "value": round(treasury_health * 100, 2),
                    "score": round(treasury_score, 2),
                    "weight": 30
                },
                "liquidity_ratio": {
                    "value": round(liquidity_ratio * 100, 2),
                    "score": round(liquidity_score, 2),
                    "weight": 25
                },
                "velocity_of_money": {
                    "value": round(velocity_of_money, 4),
                    "score": round(velocity_score, 2),
                    "weight": 25
                },
                "volatility_index": {
                    "value": round(volatility_index * 100, 2),
                    "score": round(volatility_score, 2),
                    "weight": 20
                }
            }
        }

    async def get_historical_economic_metrics(self, days: int = 30) -> list:
        """
        Get raw historical economic metrics for the specified number of days.

        Args:
            days: Number of days to retrieve (default: 30)

        Returns:
            List of historical metrics dictionaries
        """
        cutoff_date = discord.utils.utcnow().date() - timedelta(days=days)

        async with self.async_sessionmaker() as session:
            stmt = select(EconomicMetricsHistory).where(
                EconomicMetricsHistory.date >= cutoff_date
            ).order_by(EconomicMetricsHistory.date.desc())

            result = await session.execute(stmt)
            records = result.scalars().all()

            # Convert to dictionary format for easy consumption
            metrics_list = []
            for record in records:
                metrics_list.append({
                    "date": record.date.isoformat(),
                    "treasury_health": float(record.treasury_health),
                    "liquidity_ratio": float(record.liquidity_ratio),
                    "velocity_of_money": float(record.velocity_of_money),
                    "volatility_index": float(record.volatility_index),
                    "transaction_volume": float(record.transaction_volume),
                    "active_users": record.active_users,
                    "fee_rate": float(record.fee_rate),
                    "passive_income_rate": float(record.passive_income_rate)
                })

            return metrics_list

    async def add_loan_record(
        self, user_id: int, principal: Decimal, interest_rate: Decimal, total_repay: Decimal, due_date: datetime, status: str = "active"
    ):
        """
        Add a new loan record to the database for a user. Ensure one loan per user for simplicity.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                existing_loan = await session.execute(
                    select(Loan).where(Loan.user_id == user_id, Loan.status == "active")
                )
                if existing_loan.scalar_one_or_none():
                    raise ValueError("User already has an active loan.")

                new_loan = Loan(
                    user_id=user_id,
                    principal=principal,
                    interest_rate=interest_rate,
                    total_repay=total_repay,
                    due_date=due_date,
                    status=status,
                )
                session.add(new_loan)
            await session.commit()

    async def get_active_loans_for_user(self, user_id: int) -> list[Loan]:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Loan).where(Loan.user_id == user_id, Loan.status.in_(["active", "overdue", "defaulted"]))
            )
            return result.scalars().all()
        
    async def update_loan_for_user(self, user_id: int, new_status: str, new_principal: Decimal = None, new_interest_rate: Decimal = None, new_total_repay: Decimal = None, new_due_date: datetime = None):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Loan).where(Loan.user_id == user_id, Loan.status == "active")
                )
                active_loan = result.scalar_one_or_none()
                if not active_loan:
                    raise ValueError("No active loan found for user.")
                active_loan.status = new_status
                if new_principal is not None:
                    active_loan.principal = new_principal
                if new_interest_rate is not None:
                    active_loan.interest_rate = new_interest_rate
                if new_total_repay is not None:
                    active_loan.total_repay = new_total_repay
                if new_due_date is not None:
                    active_loan.due_date = new_due_date
            await session.commit()

    async def make_loan_payment(
        self, 
        user_id: int, 
        payment_amount: Decimal, 
        notes: str = None
    ):
        """
        Record a partial or full loan payment. Updates the loan's amount_paid field
        and creates a LoanPayment record. Automatically marks loan as 'paid' if fully repaid.
        
        Args:
            user_id: Discord user ID
            payment_amount: Amount to pay (must be > 0 and <= remaining balance)
            notes: Optional notes about the payment
            
        Returns:
            dict with payment details and new balance
            
        Raises:
            ValueError: If no active loan exists or payment amount is invalid
        """
        if payment_amount <= 0:
            raise ValueError("Payment amount must be greater than 0.")
        
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Loan).where(Loan.user_id == user_id, Loan.status.in_(["active", "overdue"]))
                )
                active_loan = result.scalar_one_or_none()
                if not active_loan:
                    raise ValueError("No active loan found for user.")
                
                remaining_balance = active_loan.total_repay - active_loan.amount_paid
                if payment_amount > remaining_balance:
                    raise ValueError(f"Payment amount exceeds remaining balance of {remaining_balance}.")
                
                # Create payment record
                payment = LoanPayment(
                    loan_id=active_loan.id,
                    user_id=user_id,
                    payment_method="manual",  # Could be extended to support different methods
                    payment_amount=payment_amount,
                    notes=notes
                )
                session.add(payment)
                
                # Update loan amount paid
                active_loan.amount_paid += payment_amount
                
                # Check if fully paid
                if active_loan.amount_paid >= active_loan.total_repay:
                    active_loan.status = "paid"
                    active_loan.amount_paid = active_loan.total_repay  # Ensure exact match
                    
            await session.commit()
            
            return {
                "payment_amount": payment_amount,
                "previous_paid": active_loan.amount_paid - payment_amount,
                "new_amount_paid": active_loan.amount_paid,
                "remaining_balance": active_loan.total_repay - active_loan.amount_paid,
                "loan_status": active_loan.status
            }

    async def get_loan_payment_history(self, user_id: int) -> list[LoanPayment]:
        """
        Retrieve all payment records for a user's loans.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            List of LoanPayment records ordered by payment_date descending
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(LoanPayment)
                .join(Loan, LoanPayment.loan_id == Loan.id)
                .where(Loan.user_id == user_id)
                .order_by(LoanPayment.payment_date.desc())
            )
            return result.scalars().all()

    async def get_loan_remaining_balance(self, user_id: int) -> Decimal:
        """
        Calculate the remaining balance on a user's active loan.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            Decimal representing remaining balance (total_repay - amount_paid)
            
        Raises:
            ValueError: If no active loan exists
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Loan).where(Loan.user_id == user_id, Loan.status.in_(["active", "overdue"]))
            )
            active_loan = result.scalar_one_or_none()
            if not active_loan:
                raise ValueError("No active loan found for user.")
            
            return active_loan.total_repay - active_loan.amount_paid

    async def date_check_loans(self):
        """
        Check all active loans and mark those past due as 'defaulted'.
        Add penalty of 10% of loan amount to the total_repay amount for defaulted loans each day it is not repaid.
        If the loan is not paid back in 7 days after the due date, freeze the user's wallet.
        Automatically unfreeze the wallet 7 days after defaulting.
        This can be scheduled to run periodically (e.g., every hour).
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                now = discord.utils.utcnow()
                
                # Process overdue loans
                result = await session.execute(
                    select(Loan).where(Loan.status == "active", Loan.due_date < now)
                )
                overdue_loans = result.scalars().all()
                for loan in overdue_loans:
                    days_overdue = (now - loan.due_date).days
                    if days_overdue > 0:
                        loan.status = "overdue"
                        loan.total_repay += (loan.principal * Decimal("0.1") * days_overdue).quantize(Decimal("0.1"))
                    if days_overdue >= 7:
                        loan.status = "defaulted"
                        loan.defaulted_date = now
                        wallet = await self.get_wallet_by_user_id(loan.user_id)
                        if wallet and not wallet.wallet_frozen:
                            await self.freeze_wallet(wallet.wallet_id)
                
                # Unfreeze wallets 7 days after defaulting
                result = await session.execute(
                    select(Loan).join(Wallet, Loan.user_id == Wallet.user_id).where(
                        Loan.status == "defaulted",
                        Loan.defaulted_date != None,
                        Wallet.wallet_frozen == True,
                    )
                )
                defaulted_loans = result.scalars().all()
                for loan in defaulted_loans:
                    days_defaulted = (now - loan.defaulted_date).days
                    if days_defaulted >= 7:
                        wallet = await self.get_wallet_by_user_id(loan.user_id)
                        if wallet and wallet.wallet_frozen:
                            await self.unfreeze_wallet(wallet.wallet_id)


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
                    CommandCooldown.cooldown_expiry < discord.utils.utcnow()
                )
            )
            await session.commit()
    
    async def clear_all_cooldowns(self):
        """Wipes all cooldowns from the database."""
        async with self.async_sessionmaker() as session:
            await session.execute(delete(CommandCooldown))
            await session.commit()
        
    async def set_cooldown(
        self, user_id: int, command_name: str, cooldown_seconds: int
    ) -> None:
        """Sets a cooldown for both prefix and slash commands for a user."""
        expiry_time = discord.utils.utcnow() + timedelta(seconds=cooldown_seconds)

        async with self.async_sessionmaker() as session:
            # Try to update existing cooldown first
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
                # No existing cooldown, create new one
                cooldown = CommandCooldown(
                    user_id=user_id,
                    command_name=command_name,
                    cooldown_expiry=expiry_time,
                )
                session.add(cooldown)

            await session.commit()

    async def get_cooldown(self, user_id: int, command_name: str) -> float:
        """Returns the remaining cooldown time in seconds. Returns 0 if expired or not found."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandCooldown).filter_by(
                    user_id=user_id,
                    command_name=command_name,
                )
            )
            cooldown = result.scalar_one_or_none()

            if not cooldown:
                return 0

            now = discord.utils.utcnow()
            if cooldown.cooldown_expiry <= now:
                # Cooldown expired, clean it up
                await session.delete(cooldown)
                await session.commit()
                return 0

            remaining_time = (cooldown.cooldown_expiry - now).total_seconds()
            return max(0, remaining_time)

    async def add_to_blacklist(self, user_id: str, admin_id: str, reason: str) -> None:
        try:
            async with self.async_sessionmaker() as session:
                await session.execute(
                    delete(Blacklist).where(Blacklist.user_id == user_id)
                )
                blacklist_entry = Blacklist(user_id=user_id, admin_id=admin_id, reason=reason)
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
                        ).order_by(CryptoAsset.purchase_date.desc())
                    )
                    assets = result.scalars().all()

                    # Handle duplicate entries - keep newest, delete oldest
                    if len(assets) > 1:
                        asset = assets[0]  # Newest (due to desc order)
                        for old_asset in assets[1:]:
                            await session.delete(old_asset)
                    elif len(assets) == 1:
                        asset = assets[0]
                    else:
                        asset = None

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

    async def transfer_crypto_asset(
        self,
        sender_user_id: int,
        receiver_user_id: int,
        symbol: str,
        amount: Decimal,
        description: str = "Crypto transfer",
    ):
        """Transfer crypto assets from one user to another."""
        symbol = symbol.upper()
        amount = amount.quantize(Decimal("0.00000000"), ROUND_HALF_UP)

        if amount <= 0:
            raise ValueError("Transfer amount must be positive.")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get sender and receiver crypto assets
                sender_result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == sender_user_id,
                        CryptoAsset.symbol == symbol,
                    )
                )
                sender_asset = sender_result.scalar_one_or_none()

                receiver_result = await session.execute(
                    select(CryptoAsset).where(
                        CryptoAsset.user_id == receiver_user_id,
                        CryptoAsset.symbol == symbol,
                    )
                )
                receiver_asset = receiver_result.scalar_one_or_none()

                if not sender_asset:
                    raise ValueError(f"No {symbol} asset found for sender")
                if sender_asset.amount < amount:
                    raise ValueError("Insufficient crypto balance")

                # Create receiver asset if doesn't exist
                if not receiver_asset:
                    receiver_asset = CryptoAsset(
                        user_id=receiver_user_id,
                        symbol=symbol,
                        amount=Decimal("0"),
                    )
                    session.add(receiver_asset)
                    await session.flush()

                # Transfer amount
                sender_asset.amount -= amount
                receiver_asset.amount += amount

                # Record transaction
                txid = str(uuid.uuid4())
                session.add(
                    Transaction(
                        id=txid,
                        from_user_id=sender_user_id,
                        to_user_id=receiver_user_id,
                        amount=amount,
                        description=description,
                        timestamp=discord.utils.utcnow(),
                    )
                )

            await self.update_supply()
        return txid

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
        """Get the price of a crypto asset using FreeCryptoAPI."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CryptoPrice).where(CryptoPrice.symbol == symbol.upper())
            )
            price_record = result.scalar_one_or_none()

            now = discord.utils.utcnow()
            if (
                price_record
                and price_record.timestamp
                and price_record.timestamp >= now - timedelta(minutes=15)
            ):
                return price_record.price

            try:
                async with aiohttp.ClientSession() as api_session:
                    params = {"symbol": symbol.upper()}
                    headers = {"Authorization": f"Bearer {os.getenv('FREECRYPTOAPI_API_KEY')}"}

                    async with api_session.get(
                        "https://api.freecryptoapi.com/v1/getData",
                        params=params,
                        headers=headers,
                    ) as response:
                        if response.status == 429:
                            if price_record:
                                return price_record.price
                            raise Exception("Rate limit exceeded")

                        data = await response.json()
                        if data.get("status") != "success" or not data.get("symbols"):
                            raise Exception("Invalid API response")

                        crypto_data = data["symbols"][0]
                        price = Decimal(str(crypto_data["last"]))

                        if price_record:
                            price_record.price = price
                            price_record.timestamp = discord.utils.utcnow()
                        else:
                            new_record = CryptoPrice(
                                symbol=symbol.upper(),
                                price=price,
                                timestamp=discord.utils.utcnow(),
                            )
                            session.add(new_record)

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
        effect: str = None,
        effect_value: int = None,
        effect_duration: int = None,
        cooldown_seconds: int = None,
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
                    effect=effect,
                    effect_value=effect_value,
                    effect_duration=effect_duration,
                    cooldown_seconds=cooldown_seconds,
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
                    wallet_id, -total_cost, f"Purchased {quantity}x {shop_item.name}", "standard"
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
                        wallet_id, Decimal(item.effect_value), f"Used {item.name}", "standard"
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
                        wallet_id, Decimal(item.effect_value), f"Redeemed {item.name}", "standard"
                    )
                async with session.begin():
                    await session.delete(item)
                await session.commit()
                return message
            elif item.item_type == ItemType.COLLECTIBLE:
                return f"You are now showcasing your collectible {item.name}."
            else:
                raise ValueError("Unknown item type.")

    # ==================== Item Cooldown Methods ====================

    async def set_item_cooldown(
        self, user_id: int, item_name: str, cooldown_seconds: int
    ) -> None:
        """Set a cooldown for a user on a specific item."""
        expiry = discord.utils.utcnow() + timedelta(seconds=cooldown_seconds)

        async with self.async_sessionmaker() as session:
            # Check if cooldown already exists
            stmt = select(ItemCooldown).where(
                ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.cooldown_expiry = expiry
            else:
                cooldown = ItemCooldown(
                    user_id=user_id, item_name=item_name, cooldown_expiry=expiry
                )
                session.add(cooldown)

            await session.commit()

    async def get_item_cooldown(self, user_id: int, item_name: str) -> int:
        """
        Get remaining cooldown seconds for a user's item.
        Returns 0 if no cooldown or if expired.
        """
        async with self.async_sessionmaker() as session:
            stmt = select(ItemCooldown).where(
                ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
            )
            cooldown = (await session.execute(stmt)).scalar_one_or_none()
            if not cooldown:
                return 0
            now = discord.utils.utcnow()
            if cooldown.cooldown_expiry <= now:
                # Cooldown expired, clean it up
                await session.delete(cooldown)
                await session.commit()
                return 0
            remaining = (cooldown.cooldown_expiry - now).total_seconds()
            return int(remaining)

    async def is_item_on_cooldown(self, user_id: int, item_name: str) -> bool:
        """Check if an item is on cooldown for a user."""
        remaining = await self.get_item_cooldown(user_id, item_name)
        return remaining > 0

    async def clear_item_cooldown(self, user_id: int, item_name: str) -> bool:
        """Clear a cooldown for a user's item. Returns True if cooldown was cleared."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(ItemCooldown).where(
                    ItemCooldown.user_id == user_id, ItemCooldown.item_name == item_name
                )
                cooldown = (await session.execute(stmt)).scalar_one_or_none()
                if cooldown:
                    await session.delete(cooldown)
                    await session.commit()
                    return True
            return False

    # ==================== Active Effect Methods ====================

    async def create_active_effect(
        self,
        user_id: int,
        effect_type: str,
        effect_value: Decimal,
        duration_seconds: int,
        source_item_name: str,
    ) -> ActiveEffect:
        """Create a timed effect for a user."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                expires_at = discord.utils.utcnow() + timedelta(seconds=duration_seconds)
                effect = ActiveEffect(
                    user_id=user_id,
                    effect_type=effect_type,
                    effect_value=effect_value,
                    source_item_name=source_item_name,
                    expires_at=expires_at,
                )
                session.add(effect)
            await session.commit()
            return effect

    async def get_user_active_effects(self, user_id: int) -> List[ActiveEffect]:
        """Get all non-expired effects for a user."""
        async with self.async_sessionmaker() as session:
            now = discord.utils.utcnow()
            stmt = (
                select(ActiveEffect)
                .where(ActiveEffect.user_id == user_id, ActiveEffect.expires_at > now)
                .order_by(ActiveEffect.expires_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_active_effects_by_type(
        self, user_id: int, effect_type: str
    ) -> List[ActiveEffect]:
        """Get all non-expired effects of a specific type for a user."""
        async with self.async_sessionmaker() as session:
            now = discord.utils.utcnow()
            stmt = (
                select(ActiveEffect)
                .where(
                    ActiveEffect.user_id == user_id,
                    ActiveEffect.effect_type == effect_type,
                    ActiveEffect.expires_at > now,
                )
                .order_by(ActiveEffect.expires_at)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def cleanup_expired_effects(self) -> int:
        """Remove all expired effects. Returns count of removed effects."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                now = discord.utils.utcnow()
                stmt = delete(ActiveEffect).where(ActiveEffect.expires_at <= now)
                result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_effect_multiplier(
        self, user_id: int, effect_type: str
    ) -> Decimal:
        """
        Get the combined multiplier value for a specific effect type.
        Returns Decimal('1.0') if no active effects.
        For multipliers, returns the product of all active multipliers.
        """
        effects = await self.get_active_effects_by_type(user_id, effect_type)
        if not effects:
            return Decimal("1.0")
        combined = Decimal("1.0")
        for effect in effects:
            combined *= effect.effect_value
        return combined

    async def remove_active_effect(self, effect_id: int) -> bool:
        """Remove a specific active effect by ID. Returns True if removed."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(ActiveEffect).where(ActiveEffect.id == effect_id)
                effect = (await session.execute(stmt)).scalar_one_or_none()
                if effect:
                    await session.delete(effect)
                    await session.commit()
                    return True
            return False

    # ==================== Trade Methods ====================

    async def create_trade_request(
        self, from_user_id: int, to_user_id: int, item_id: int, quantity: int = 1
    ) -> TradeLog:
        """
        Create a pending trade request.
        Validates that the sender owns the item.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Verify ownership
                stmt = select(Item).where(Item.id == item_id)
                item = (await session.execute(stmt)).scalar_one_or_none()
                if not item:
                    raise ValueError("Item not found.")
                if item.user_id != from_user_id:
                    raise ValueError("You don't own this item.")
                if item.quantity < quantity:
                    raise ValueError(
                        f"Insufficient quantity. You have {item.quantity}, need {quantity}."
                    )

                trade = TradeLog(
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    item_id=item_id,
                    item_name=item.name,
                    quantity=quantity,
                    status="pending",
                )
                session.add(trade)
            await session.commit()
            return trade

    async def accept_trade_request(self, trade_id: int) -> TradeLog:
        """
        Accept a pending trade request.
        Transfers the item to the recipient and marks the trade as completed.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(TradeLog).where(TradeLog.id == trade_id)
                trade = (await session.execute(stmt)).scalar_one_or_none()
                if not trade:
                    raise ValueError("Trade not found.")
                if trade.status != "pending":
                    raise ValueError(f"Trade is already {trade.status}.")

                # Get the item
                item_stmt = select(Item).where(Item.id == trade.item_id)
                item = (await session.execute(item_stmt)).scalar_one_or_none()
                if not item:
                    raise ValueError("Item no longer exists.")
                if item.user_id != trade.from_user_id:
                    raise ValueError("Sender no longer owns this item.")
                if item.quantity < trade.quantity:
                    raise ValueError("Insufficient item quantity.")

                # Handle quantity transfer
                if item.quantity == trade.quantity:
                    # Transfer full ownership
                    item.user_id = trade.to_user_id
                else:
                    # Split the item - create new item for recipient
                    item.quantity -= trade.quantity
                    new_item = Item(
                        user_id=trade.to_user_id,
                        name=item.name,
                        serial_number=f"{item.serial_number}-{trade.to_user_id}",
                        description=item.description,
                        quantity=trade.quantity,
                        item_type=item.item_type,
                        effect=item.effect,
                        effect_value=item.effect_value,
                        effect_duration=item.effect_duration,
                        cooldown_seconds=item.cooldown_seconds,
                    )
                    session.add(new_item)

                # Update trade status
                trade.status = "completed"
                trade.completed_at = discord.utils.utcnow()
            await session.commit()
            return trade

    async def decline_trade_request(self, trade_id: int) -> TradeLog:
        """Decline a pending trade request."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = select(TradeLog).where(TradeLog.id == trade_id)
                trade = (await session.execute(stmt)).scalar_one_or_none()
                if not trade:
                    raise ValueError("Trade not found.")
                if trade.status != "pending":
                    raise ValueError(f"Trade is already {trade.status}.")
                trade.status = "cancelled"
            await session.commit()
            return trade

    async def get_pending_trades(self, user_id: int) -> List[TradeLog]:
        """Get all pending trades where the user is either sender or recipient."""
        async with self.async_sessionmaker() as session:
            stmt = (
                select(TradeLog)
                .where(
                    TradeLog.status == "pending",
                    (TradeLog.to_user_id == user_id)
                    | (TradeLog.from_user_id == user_id),
                )
                .order_by(TradeLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_trade_by_id(self, trade_id: int) -> Optional[TradeLog]:
        """Get a trade by its ID."""
        async with self.async_sessionmaker() as session:
            stmt = select(TradeLog).where(TradeLog.id == trade_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_pending_trades_for_user(self, user_id: int) -> List[TradeLog]:
        """Get all pending trades where the user is the recipient."""
        async with self.async_sessionmaker() as session:
            stmt = (
                select(TradeLog)
                .where(TradeLog.to_user_id == user_id, TradeLog.status == "pending")
                .order_by(TradeLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ==================== Enhanced use_inventory_item ====================

    async def use_inventory_item_with_effects(
        self, user_id: int, item_id: int
    ) -> dict:
        """
        Enhanced version of use_inventory_item that handles cooldowns,
        effect types, and effect durations.

        Returns a dict with:
        - message: str - result message
        - effect_type: str (optional)
        - effect_applied: bool
        - cooldown_seconds: int (optional)
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Item).where(Item.user_id == user_id, Item.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                raise ValueError("Item not found in inventory.")

            # Check cooldown
            if item.cooldown_seconds:
                remaining = await self.get_item_cooldown(user_id, item.name)
                if remaining > 0:
                    raise ValueError(
                        f"This item is on cooldown. {remaining} seconds remaining."
                    )

            response = {"message": "", "effect_applied": False}

            # Apply effect based on type
            effect = item.effect
            effect_value = item.effect_value
            effect_duration = item.effect_duration

            if effect == "currency" and effect_value:
                # Direct currency grant
                wallet_id = await self.get_wallet_id_for_user(user_id)
                await self.process_treasury_transaction(
                    wallet_id, Decimal(effect_value), f"Used {item.name}", "standard"
                )
                response["message"] = f"You received {effect_value} coins from {item.name}!"
                response["effect_applied"] = True

            elif effect in (
                "gambling_multiplier",
                "luck_boost",
                "earning_boost",
                "cooldown_reduction",
                "rtp_boost",
            ):
                # Create timed effect
                if effect_duration:
                    await self.create_active_effect(
                        user_id=user_id,
                        effect_type=effect,
                        effect_value=Decimal(str(effect_value)),
                        duration_seconds=effect_duration,
                        source_item_name=item.name,
                    )
                    duration_mins = effect_duration // 60
                    duration_secs = effect_duration % 60
                    duration_str = f"{duration_mins}m {duration_secs}s" if duration_mins else f"{duration_secs}s"

                    effect_names = {
                        "gambling_multiplier": f"{effect_value}x gambling multiplier",
                        "luck_boost": f"{effect_value}x luck boost",
                        "earning_boost": f"{effect_value}x earning boost",
                        "cooldown_reduction": f"{effect_value}% cooldown reduction",
                        "rtp_boost": f"{effect_value}% RTP boost",
                    }
                    response["message"] = (
                        f"Activated {effect_names.get(effect, effect)} for {duration_str}!"
                    )
                    response["effect_applied"] = True
                else:
                    response["message"] = f"Used {item.name} but no duration was specified."

            elif item.item_type == ItemType.COLLECTIBLE:
                response["message"] = f"You are showcasing your collectible {item.name}."
            else:
                response["message"] = f"You used {item.name}."

            # Set cooldown if applicable
            if item.cooldown_seconds and item.item_type != ItemType.COLLECTIBLE:
                await self.set_item_cooldown(user_id, item.name, item.cooldown_seconds)
                response["cooldown_seconds"] = item.cooldown_seconds

            # Handle quantity reduction based on item type
            if item.item_type == ItemType.CONSUMABLE:
                async with session.begin():
                    item.quantity -= 1
                    if item.quantity <= 0:
                        await session.delete(item)
                await session.commit()
            elif item.item_type == ItemType.REDEEMABLE:
                async with session.begin():
                    await session.delete(item)
                await session.commit()

            return response

    async def place_bounty(
        self, issuer_id: int, target_id: int, reward: Decimal
    ) -> Bounty:
        """Place (or increase) a bounty on a user."""
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet_id = await self.get_wallet_id_for_user(issuer_id)
                wallet_balance = await self.get_wallet_balance(wallet_id)
                if wallet_balance < reward:
                    raise ValueError("Insufficient balance to place bounty.")

                await self.process_treasury_transaction(
                    wallet_id, -reward, f"Placed bounty on {target_id}", "high_value"
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
        # Check economic circuit breaker before processing
        circuit_breaker = await self.check_economic_circuit_breaker()
        if circuit_breaker["triggered"]:
            reasons = ", ".join(circuit_breaker["reasons"])
            raise ValueError(f"Economic circuit breaker triggered: {reasons}")

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
                    claimer_wallet, reward_amount, f"Claimed bounty on {target_id}", "high_value"
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
        timestamp = discord.utils.utcnow()
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
                user_id=user_id, roles=roles, timestamp=discord.utils.utcnow()
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

    async def set_juul_flavor(self, guild_id: int, flavor: str):
        """
        Set the flavor of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.flavor = flavor
                else:
                    juul = Juul(guild_id=guild_id, flavor=flavor)
                    session.add(juul)

    async def get_juul_flavor(self, guild_id: int) -> str:
        """
        Read-only helper. Returns the flavor of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul.flavor).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none() or "classic"

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

    async def get_dynamic_reward_multiplier(self) -> Decimal:
        """
        Calculate dynamic reward multiplier based on economic conditions.

        When liquidity is low, rewards are increased to encourage circulation.
        When liquidity is high, rewards are normalized.
        """
        factors = await self.get_economic_factors()
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))

        # Base multiplier
        BASE_MULTIPLIER = Decimal("1.0")

        # Increase rewards when liquidity is low
        if liquidity_ratio < Decimal("0.3"):
            # Low liquidity - increase rewards significantly
            return BASE_MULTIPLIER * Decimal("1.5")
        elif liquidity_ratio < Decimal("0.5"):
            # Moderate liquidity - modest reward increase
            return BASE_MULTIPLIER * Decimal("1.2")
        elif liquidity_ratio > Decimal("0.8"):
            # High liquidity - reduce rewards slightly
            return BASE_MULTIPLIER * Decimal("0.9")
        else:
            # Normal liquidity - standard rewards
            return BASE_MULTIPLIER

    async def check_economic_circuit_breaker(self) -> dict:
        """
        Check if economic circuit breakers should be triggered based on velocity and other metrics.

        Returns a dictionary with breaker status and reason.
        """
        factors = await self.get_economic_factors()
        velocity = factors.get("velocity_of_money", Decimal("0"))
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))
        #volatility = factors.get("volatility_index", Decimal("0.02"))

        # Define thresholds
        VELOCITY_CRISIS_THRESHOLD = Decimal("0.05")  # Very low money velocity
        LIQUIDITY_CRISIS_THRESHOLD = Decimal("0.1")  # Very low liquidity
        #VOLATILITY_CRISIS_THRESHOLD = Decimal("0.9")  # High inequality (Gini 0-1 scale)

        circuit_breaker_triggered = False
        reason = []

        if velocity < VELOCITY_CRISIS_THRESHOLD:
            circuit_breaker_triggered = True
            reason.append("Low velocity of money")

        if liquidity_ratio < LIQUIDITY_CRISIS_THRESHOLD:
            circuit_breaker_triggered = True
            reason.append("Low liquidity")

        #if volatility > VOLATILITY_CRISIS_THRESHOLD:
        #    circuit_breaker_triggered = False
        #    reason.append("High volatility")

        return {
            "triggered": circuit_breaker_triggered,
            "reasons": reason,
            "velocity": velocity,
            "liquidity_ratio": liquidity_ratio,
            #"volatility": volatility
        }

    async def get_enhanced_fee_rate(self, transaction_type: str = "standard") -> Decimal:
        """
        Calculate enhanced fee rate based on multiple economic factors.

        Args:
            transaction_type: Type of transaction ('standard', 'high_value', 'gambling')
        """
        factors = await self.get_economic_factors()
        base_fee = factors.get("fee_rate", Decimal("0.01"))
        volatility = factors.get("volatility_index", Decimal("0.02"))
        liquidity_ratio = factors.get("liquidity_ratio", Decimal("0.5"))
        velocity = factors.get("velocity_of_money", Decimal("0.1"))

        # Adjust fee based on transaction type
        if transaction_type == "high_value":
            # Higher fees for high-value transactions during volatile times
            fee = base_fee * (1 + volatility * Decimal("2.0"))
        elif transaction_type == "gambling":
            # Special fee structure for gambling
            fee = base_fee * (1 + (Decimal("1.0") - liquidity_ratio))
        else:
            # Standard transaction fees
            fee = base_fee * (1 + volatility * Decimal("0.5"))

        # Additional adjustment based on velocity
        if velocity < Decimal("0.05"):
            # Low velocity indicates economic slowdown - reduce fees to stimulate activity
            fee *= Decimal("0.8")
        elif velocity > Decimal("0.5"):
            # High velocity indicates hot market - increase fees to cool down
            fee *= Decimal("1.2")

        # Ensure fee stays within reasonable bounds
        MAX_FEE = Decimal("0.20")  # 20%
        MIN_FEE = Decimal("0.001")  # 0.1%

        return max(MIN_FEE, min(MAX_FEE, fee))

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

    # =====================
    # Anti-Cheat Methods
    # =====================

    async def log_transfer(
        self,
        transaction_id: str,
        sender_id: int,
        receiver_id: int,
        amount: Decimal,
        guild_id: int,
    ) -> None:
        """Log a P2P transfer in the transfer history table."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    TransferHistory(
                        transaction_id=transaction_id,
                        sender_id=sender_id,
                        receiver_id=receiver_id,
                        amount=amount,
                        guild_id=guild_id,
                    )
                )

    async def check_alt_transfer(
        self, sender_id: int, receiver_id: int, guild_id: int
    ) -> bool:
        """
        Check if a transfer is between linked alternate accounts.
        Returns True if the users are linked alts, False otherwise.
        """
        linked_ids = await self.get_all_linked_user_ids(sender_id, guild_id)
        return receiver_id in linked_ids

    async def log_suspicious_activity(
        self,
        activity_type: SuspiciousActivityType,
        user_id: int,
        guild_id: int,
        related_user_ids: List[int] = None,
        amount: Decimal = None,
        details: dict = None,
    ) -> int:
        """
        Log a suspicious activity for owner review.
        Returns the ID of the created log entry.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                log = SuspiciousActivityLog(
                    activity_type=activity_type,
                    user_id=user_id,
                    guild_id=guild_id,
                    related_user_ids=related_user_ids or [],
                    amount=amount,
                    details=details or {},
                )
                session.add(log)
                await session.flush()
                return log.id

    async def get_suspicious_activities(
        self,
        activity_type: SuspiciousActivityType = None,
        reviewed: bool = None,
        guild_id: int = None,
        user_id: int = None,
        limit: int = 50,
    ) -> List[SuspiciousActivityLog]:
        """Query suspicious activity logs with filters."""
        async with self.async_sessionmaker() as session:
            stmt = select(SuspiciousActivityLog).order_by(
                SuspiciousActivityLog.created_at.desc()
            )
            if activity_type is not None:
                stmt = stmt.where(SuspiciousActivityLog.activity_type == activity_type)
            if reviewed is not None:
                stmt = stmt.where(SuspiciousActivityLog.reviewed == reviewed)
            if guild_id is not None:
                stmt = stmt.where(SuspiciousActivityLog.guild_id == guild_id)
            if user_id is not None:
                stmt = stmt.where(SuspiciousActivityLog.user_id == user_id)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def review_suspicious_activity(
        self, log_id: int, reviewed_by: int, notes: str = None
    ) -> bool:
        """Mark a suspicious activity log as reviewed."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                log = await session.get(SuspiciousActivityLog, log_id)
                if not log:
                    return False
                log.reviewed = True
                log.reviewed_by = reviewed_by
                log.review_notes = notes
                return True

    async def get_recent_large_treasury_receipts(
        self, user_id: int, hours: int = 1, min_amount: Decimal = None, guild_id: int = None
    ) -> List[TransferHistory]:
        """
        Get recent large treasury receipts for a user.
        Used for detecting potential treasury exploitation patterns.
        """
        if min_amount is None:
            min_amount = Decimal("5000")  # Default threshold

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        async with self.async_sessionmaker() as session:
            stmt = (
                select(TransferHistory)
                .where(TransferHistory.receiver_id == user_id)
                .where(TransferHistory.amount >= min_amount)
                .where(TransferHistory.created_at >= cutoff_time)
            )
            if guild_id is not None:
                stmt = stmt.where(TransferHistory.guild_id == guild_id)
            stmt = stmt.order_by(TransferHistory.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ==================== Hoarding Detection Methods ====================

    async def get_aggregated_balance(
        self, user_id: int, guild_id: int = None
    ) -> dict:
        """
        Get aggregated balance across all linked alt accounts (wallet + bank + crypto).

        Returns dict with:
        - main_user_id: the queried user
        - linked_user_ids: list of linked alt user IDs
        - individual_balances: dict mapping user_id to {wallet, bank, crypto, total}
        - total_balance: sum of all balances (wallet + bank + crypto)
        - total_wallet: sum of wallet balances only
        - total_bank: sum of bank balances only
        - total_crypto: sum of crypto values only
        - account_count: number of accounts in network
        """
        # Get all linked users
        linked_ids = await self.get_all_linked_user_ids(user_id, guild_id) if guild_id else []
        all_user_ids = [user_id] + linked_ids

        async with self.async_sessionmaker() as session:
            # Query wallet balances for all users in the network
            wallet_result = await session.execute(
                select(Wallet.wallet_id, Wallet.user_id, Wallet.balance).where(
                    Wallet.user_id.in_(all_user_ids)
                )
            )
            wallet_rows = wallet_result.fetchall()

            # Build wallet_id to user_id mapping and get wallet balances
            wallet_to_user = {}
            individual_balances = {
                uid: {"wallet": Decimal("0"), "bank": Decimal("0"), "crypto": Decimal("0"), "total": Decimal("0")}
                for uid in all_user_ids
            }

            for row in wallet_rows:
                wallet_id, uid, balance = row
                wallet_to_user[wallet_id] = uid
                individual_balances[uid]["wallet"] = Decimal(str(balance))

            # Query bank balances using wallet_id mapping
            if wallet_to_user:
                bank_result = await session.execute(
                    select(Wallet.wallet_id, Wallet.bank_balance).where(
                        Wallet.wallet_id.in_(wallet_to_user.keys())
                    )
                )
                bank_rows = bank_result.fetchall()

                for row in bank_rows:
                    wallet_id, bank_balance = row
                    uid = wallet_to_user.get(wallet_id)
                    if uid:
                        individual_balances[uid]["bank"] = Decimal(str(bank_balance))

            # Query crypto assets for all users
            crypto_result = await session.execute(
                select(CryptoAsset.user_id, CryptoAsset.symbol, CryptoAsset.amount).where(
                    CryptoAsset.user_id.in_(all_user_ids)
                )
            )
            crypto_rows = crypto_result.fetchall()

            # Get all unique symbols and their current prices
            symbols = list(set(row[1] for row in crypto_rows))
            crypto_prices = {}
            if symbols:
                price_result = await session.execute(
                    select(CryptoPrice.symbol, CryptoPrice.price).where(
                        CryptoPrice.symbol.in_(symbols)
                    )
                )
                for sym, price in price_result.fetchall():
                    crypto_prices[sym] = Decimal(str(price))

            # Calculate crypto value per user
            for row in crypto_rows:
                uid, symbol, amount = row
                price = crypto_prices.get(symbol, Decimal("0"))
                value = Decimal(str(amount)) * price
                individual_balances[uid]["crypto"] += value

            # Calculate totals per user
            for uid in all_user_ids:
                individual_balances[uid]["total"] = (
                    individual_balances[uid]["wallet"]
                    + individual_balances[uid]["bank"]
                    + individual_balances[uid]["crypto"]
                )

        total_wallet = sum(b["wallet"] for b in individual_balances.values())
        total_bank = sum(b["bank"] for b in individual_balances.values())
        total_crypto = sum(b["crypto"] for b in individual_balances.values())
        total_balance = total_wallet + total_bank + total_crypto

        return {
            "main_user_id": user_id,
            "linked_user_ids": linked_ids,
            "individual_balances": individual_balances,
            "total_balance": total_balance,
            "total_wallet": total_wallet,
            "total_bank": total_bank,
            "total_crypto": total_crypto,
            "account_count": len(all_user_ids),
        }

    async def get_net_flow(
        self, user_id: int, days: int = 30, guild_id: int = None
    ) -> dict:
        """
        Analyze net flow for a user (received vs spent over time period).

        Returns dict with:
        - total_received: sum of all incoming amounts
        - total_sent: sum of all outgoing amounts
        - net_flow: received - sent
        - transaction_count_in: number of incoming transactions
        - transaction_count_out: number of outgoing transactions
        - ratio: received/sent ratio (None if no sends)
        """
        cutoff = discord.utils.utcnow() - timedelta(days=days)

        async with self.async_sessionmaker() as session:
            # Sum of incoming transactions (to_user_id = user_id)
            incoming_stmt = (
                select(func.coalesce(func.sum(Transaction.amount), Decimal("0")))
                .where(Transaction.to_user_id == user_id)
                .where(Transaction.timestamp >= cutoff)
            )
            incoming_result = await session.execute(incoming_stmt)
            total_received = Decimal(str(incoming_result.scalar() or "0"))

            # Sum of outgoing transactions (from_user_id = user_id)
            outgoing_stmt = (
                select(func.coalesce(func.sum(Transaction.amount), Decimal("0")))
                .where(Transaction.from_user_id == user_id)
                .where(Transaction.timestamp >= cutoff)
            )
            outgoing_result = await session.execute(outgoing_stmt)
            total_sent = Decimal(str(outgoing_result.scalar() or "0"))

            # Count transactions
            count_in_stmt = (
                select(func.count(Transaction.id))
                .where(Transaction.to_user_id == user_id)
                .where(Transaction.timestamp >= cutoff)
            )
            count_in_result = await session.execute(count_in_stmt)
            transaction_count_in = count_in_result.scalar() or 0

            count_out_stmt = (
                select(func.count(Transaction.id))
                .where(Transaction.from_user_id == user_id)
                .where(Transaction.timestamp >= cutoff)
            )
            count_out_result = await session.execute(count_out_stmt)
            transaction_count_out = count_out_result.scalar() or 0

        net_flow = total_received - total_sent
        ratio = float(total_received / total_sent) if total_sent > 0 else None

        return {
            "user_id": user_id,
            "days": days,
            "total_received": total_received,
            "total_sent": total_sent,
            "net_flow": net_flow,
            "transaction_count_in": transaction_count_in,
            "transaction_count_out": transaction_count_out,
            "ratio": ratio,
        }

    async def calculate_hoarding_score(
        self, user_id: int, guild_id: int = None, days: int = 30
    ) -> dict:
        """
        Calculate a hoarding score combining multiple factors.

        Factors (each 0-20 points, total 0-100):
        1. Balance concentration (high balance relative to activity)
        2. Net flow ratio (receives much but sends little)
        3. Alt network size (many linked alts)
        4. Aggregated balance (large total across alts including bank)
        5. Transaction pattern (low outgoing activity)

        Returns dict with:
        - score: 0-100 hoarding score
        - factors: breakdown of each factor score
        - risk_level: "low", "medium", "high", "critical"
        - details: supporting data
        """
        # Get aggregated balance (wallet + bank + crypto)
        balance_data = await self.get_aggregated_balance(user_id, guild_id)

        # Get net flow
        flow_data = await self.get_net_flow(user_id, days, guild_id)

        # Get main user's total balance (wallet + bank + crypto)
        main_balance = balance_data["individual_balances"].get(user_id, {}).get("total", Decimal("0"))
        main_wallet = balance_data["individual_balances"].get(user_id, {}).get("wallet", Decimal("0"))
        main_bank = balance_data["individual_balances"].get(user_id, {}).get("bank", Decimal("0"))
        main_crypto = balance_data["individual_balances"].get(user_id, {}).get("crypto", Decimal("0"))

        factors = {}
        details = {
            "aggregated_balance": balance_data,
            "net_flow": flow_data,
            "main_balance": main_balance,
            "main_wallet": main_wallet,
            "main_bank": main_bank,
            "main_crypto": main_crypto,
        }

        # Factor 1: Balance concentration (high balance with low activity)
        # Score high if balance is large relative to outgoing transactions
        if flow_data["total_sent"] > 0:
            balance_to_spent_ratio = float(main_balance / flow_data["total_sent"])
            # Cap at 20 for ratio > 20
            factors["balance_concentration"] = min(20, int(balance_to_spent_ratio))
        else:
            # Never sends anything but has balance - suspicious
            if main_balance > Decimal("1000"):
                factors["balance_concentration"] = 20
            elif main_balance > Decimal("100"):
                factors["balance_concentration"] = 15
            else:
                factors["balance_concentration"] = 10

        # Factor 2: Net flow ratio (receives much, sends little)
        if flow_data["ratio"] is not None:
            # ratio > 10 = receives 10x more than sends
            if flow_data["ratio"] > 10:
                factors["net_flow_ratio"] = 20
            elif flow_data["ratio"] > 5:
                factors["net_flow_ratio"] = 15
            elif flow_data["ratio"] > 2:
                factors["net_flow_ratio"] = 10
            elif flow_data["ratio"] > 1:
                factors["net_flow_ratio"] = 5
            else:
                factors["net_flow_ratio"] = 0
        else:
            # No outgoing transactions but has incoming
            if flow_data["total_received"] > Decimal("1000"):
                factors["net_flow_ratio"] = 20
            else:
                factors["net_flow_ratio"] = 10

        # Factor 3: Alt network size
        alt_count = balance_data["account_count"] - 1
        if alt_count >= 5:
            factors["alt_network_size"] = 20
        elif alt_count >= 3:
            factors["alt_network_size"] = 15
        elif alt_count >= 2:
            factors["alt_network_size"] = 10
        elif alt_count == 1:
            factors["alt_network_size"] = 5
        else:
            factors["alt_network_size"] = 0

        # Factor 4: Aggregated balance across alts (wallet + bank)
        total_balance = balance_data["total_balance"]
        if total_balance >= Decimal("10000000"):  # 10M+
            factors["aggregated_balance"] = 20
        elif total_balance >= Decimal("5000000"):  # 5M+
            factors["aggregated_balance"] = 15
        elif total_balance >= Decimal("1000000"):  # 1M+
            factors["aggregated_balance"] = 10
        elif total_balance >= Decimal("100000"):  # 100K+
            factors["aggregated_balance"] = 5
        else:
            factors["aggregated_balance"] = 0

        # Factor 5: Transaction pattern (low outgoing activity)
        outgoing_count = flow_data["transaction_count_out"]
        incoming_count = flow_data["transaction_count_in"]
        if outgoing_count == 0 and incoming_count > 5:
            factors["transaction_pattern"] = 20  # Never sends, only receives
        elif outgoing_count == 0 and incoming_count > 0:
            factors["transaction_pattern"] = 15
        elif outgoing_count > 0 and incoming_count / max(1, outgoing_count) > 5:
            factors["transaction_pattern"] = 15  # Receives 5x more often than sends
        elif outgoing_count > 0 and incoming_count / max(1, outgoing_count) > 2:
            factors["transaction_pattern"] = 10
        else:
            factors["transaction_pattern"] = 0

        total_score = sum(factors.values())

        # Determine risk level
        if total_score >= 70:
            risk_level = "critical"
        elif total_score >= 50:
            risk_level = "high"
        elif total_score >= 30:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "user_id": user_id,
            "score": total_score,
            "factors": factors,
            "risk_level": risk_level,
            "details": details,
        }

    async def scan_for_hoarding(
        self,
        guild_id: int = None,
        min_balance: Decimal = Decimal("100000"),
        min_score: int = 30,
        limit: int = 50,
    ) -> List[dict]:
        """
        Scan for users with high hoarding scores.

        Returns list of dicts with:
        - user_id
        - score
        - risk_level
        - total_balance (across alts)
        - alt_count
        """
        async with self.async_sessionmaker() as session:
            # Get wallets with minimum balance
            stmt = (
                select(Wallet.user_id, Wallet.balance)
                .where(Wallet.balance >= min_balance)
                .order_by(Wallet.balance.desc())
                .limit(limit * 2)  # Get more than needed, filter by score
            )
            result = await session.execute(stmt)
            candidates = result.fetchall()

        hoarding_candidates = []

        for user_id, balance in candidates:
            score_data = await self.calculate_hoarding_score(
                user_id, guild_id, days=30
            )

            if score_data["score"] >= min_score:
                hoarding_candidates.append({
                    "user_id": user_id,
                    "score": score_data["score"],
                    "risk_level": score_data["risk_level"],
                    "total_balance": score_data["details"]["aggregated_balance"]["total_balance"],
                    "alt_count": score_data["details"]["aggregated_balance"]["account_count"] - 1,
                    "factors": score_data["factors"],
                })

            if len(hoarding_candidates) >= limit:
                break

        # Sort by score descending
        hoarding_candidates.sort(key=lambda x: x["score"], reverse=True)
        return hoarding_candidates

    async def get_recent_transfers(
        self,
        user_id: int,
        hours: int = 24,
        guild_id: int = None,
        limit: int = 100,
    ) -> List[TransferHistory]:
        """Get recent transfers for a user."""
        async with self.async_sessionmaker() as session:
            cutoff = discord.utils.utcnow() - timedelta(hours=hours)
            stmt = (
                select(TransferHistory)
                .where(
                    (TransferHistory.sender_id == user_id)
                    | (TransferHistory.receiver_id == user_id)
                )
                .where(TransferHistory.created_at >= cutoff)
                .order_by(TransferHistory.created_at.desc())
            )
            if guild_id is not None:
                stmt = stmt.where(TransferHistory.guild_id == guild_id)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def detect_circular_transfers(
        self,
        user_id: int,
        depth: int = 2,
        hours: int = 2,
        min_amount: Decimal = Decimal("5000"),
        amount_similarity_threshold: float = 0.8,
        guild_id: int = None,
    ) -> List[dict]:
        """
        Detect suspicious circular transfer patterns indicating hidden alt accounts.

        A suspicious circular transfer requires:
        1. Money sent out returns within a SHORT time window (default 2 hours)
        2. The amount returned is SIMILAR to amount sent (default 80%+)
        3. Large enough amounts to be worth exploiting (default $5000+)

        Normal commerce does NOT produce similar amounts in short timeframes.
        If Alice sends Bob $100 for a game, Bob sending $5 back later is normal.
        But Alice sending $10000 and Bob sending $9500 back in 30 minutes is suspicious.

        Returns list of suspicious cycles, each with:
        - path: list of user IDs in the cycle
        - amounts_sent: amounts going out at each step
        - amounts_received: amounts coming back
        - similarity: ratio of returned/sent (higher = more suspicious)
        - total_sent: total amount sent by originator
        """
        cutoff = discord.utils.utcnow() - timedelta(hours=hours)

        async with self.async_sessionmaker() as session:
            # Query transactions with actual amounts
            stmt = (
                select(Transaction)
                .where(Transaction.timestamp >= cutoff)
                .where(Transaction.amount >= min_amount)
                .where(Transaction.from_user_id.isnot(None))
                .where(Transaction.to_user_id.isnot(None))
            )
            result = await session.execute(stmt)
            transactions = list(result.scalars().all())

        # Build a directed graph with amount tracking
        # graph[A][B] = list of (amount, timestamp) transactions from A to B
        graph: dict[int, dict[int, list]] = {}
        for t in transactions:
            sender_id = t.from_user_id
            receiver_id = t.to_user_id
            if sender_id and receiver_id and sender_id != receiver_id:
                if sender_id not in graph:
                    graph[sender_id] = {}
                if receiver_id not in graph[sender_id]:
                    graph[sender_id][receiver_id] = []
                graph[sender_id][receiver_id].append((t.amount, t.timestamp))

        if user_id not in graph:
            return []

        suspicious_cycles = []
        visited_cycles = set()

        def find_cycles_dfs(
            start: int,
            current: int,
            path: List[int],
            amounts_out: List[Decimal],
            visited: set,
        ) -> None:
            """DFS to find cycles where similar amounts return to originator."""
            if len(path) > depth + 2:  # path includes start, so +2 for depth limit
                return

            if current not in graph:
                return

            for neighbor, txns in graph[current].items():
                # Get the amounts this user sent to neighbor
                for amt_sent, ts_sent in txns:
                    if neighbor == start and len(path) >= 2:
                        # Direct return: current sent back to start
                        # Check if amount is similar to what start sent out
                        total_sent_out = amounts_out[0]  # What originator sent

                        # Amount similarity check
                        ratio = float(amt_sent / total_sent_out) if total_sent_out > 0 else 0

                        if ratio >= amount_similarity_threshold:
                            cycle_key = tuple(path)
                            if cycle_key not in visited_cycles:
                                suspicious_cycles.append({
                                    "path": path + [start],
                                    "amounts_sent": amounts_out,
                                    "amount_returned": amt_sent,
                                    "similarity": round(ratio, 3),
                                    "total_sent": total_sent_out,
                                })
                                visited_cycles.add(cycle_key)

                    elif neighbor not in visited and len(path) <= depth:
                        # Continue searching along the path
                        find_cycles_dfs(
                            start,
                            neighbor,
                            path + [neighbor],
                            amounts_out,
                            visited | {neighbor},
                        )

        # For each outgoing transaction from the user, trace if similar amounts return
        for first_hop, txns in graph[user_id].items():
            for amt_sent, ts_sent in txns:
                # Only trace this specific amount pattern
                find_cycles_dfs(user_id, first_hop, [user_id, first_hop], [amt_sent], {user_id, first_hop})

        return suspicious_cycles

    # ==================== Job Methods ====================

    async def get_job(self, user_id: int) -> Optional[Job]:
        """Get a user's current job, if any."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Job).where(Job.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def apply_for_job(
        self, user_id: int, job_title: str, base_salary: Decimal
    ) -> Job:
        """Apply for a job. Creates a new job record for the user."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Check if user already has a job
                existing = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                if existing.scalar_one_or_none():
                    raise ValueError("You already have a job. Quit your current job first.")

                job = Job(
                    user_id=user_id,
                    title=job_title,
                    base_salary=base_salary,
                    days_employed=1,
                    streak=0,
                    hired_at=discord.utils.utcnow(),
                )
                session.add(job)
            await session.commit()
            return job

    async def quit_job(self, user_id: int) -> bool:
        """Remove a user's job record."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if not job:
                    raise ValueError("You don't have a job to quit.")
                await session.delete(job)
            await session.commit()
            return True

    async def work_job(self, user_id: int) -> Tuple[Job, Decimal]:
        """
        Process a user's work action.
        Returns the updated job and the calculated salary.
        Raises ValueError if cooldown hasn't expired, no job found, or user was fired for absence.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if not job:
                    raise ValueError("You don't have a job. Apply for one first!")

                now = discord.utils.utcnow()

                # Check if user missed a day (more than 48 hours since last work)
                # They get fired for not showing up
                if job.last_worked:
                    time_since_last = (now - job.last_worked).total_seconds()
                    if time_since_last > 172800:  # 48 hours = 172800 seconds
                        # Fire the employee for missing work
                        await session.delete(job)
                        await session.commit()
                        raise ValueError(
                            "You were fired for missing work! You didn't show up for more than 48 hours. "
                            "You'll need to apply for a new job."
                        )

                    # Check if 24 hours have passed since last work (normal cooldown)
                    cooldown_remaining = 86400 - time_since_last  # 24 hours = 86400 seconds
                    if cooldown_remaining > 0:
                        hours = int(cooldown_remaining // 3600)
                        minutes = int((cooldown_remaining % 3600) // 60)
                        raise ValueError(
                            f"You need to wait {hours}h {minutes}m before working again."
                        )

                # Calculate salary with tenure bonus
                weeks_employed = job.days_employed / 7
                salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))
                current_salary = job.base_salary * Decimal(str(salary_multiplier))
                current_salary = current_salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Update job record
                job.last_worked = now
                job.days_employed += 1
                job.streak += 1

            await session.commit()
            return job, current_salary

    async def calculate_salary(self, job: Job) -> Decimal:
        """Calculate current salary based on tenure."""
        weeks_employed = job.days_employed / 7
        salary_multiplier = min(2.0, 1.0 + (weeks_employed * 0.05))
        current_salary = job.base_salary * Decimal(str(salary_multiplier))
        return current_salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def get_employees_for_firing(self) -> List[Job]:
        """
        Get employees who haven't worked in 48+ hours.
        These users should be fired.
        """
        threshold = discord.utils.utcnow() - timedelta(hours=48)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Job).where(
                    (Job.last_worked != None) & (Job.last_worked < threshold)
                )
            )
            return result.scalars().all()

    async def fire_employee(self, user_id: int) -> bool:
        """Remove a job record for an inactive employee."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
                job = result.scalar_one_or_none()
                if job:
                    await session.delete(job)
            await session.commit()
            return True

    async def get_all_jobs(self) -> List[Job]:
        """Get all job records (for admin purposes)."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Job))
            return result.scalars().all()

    # ==================== VIP System Methods ====================

    async def ensure_default_vip_tiers(self) -> None:
        """Ensure default VIP tiers exist in the database."""
        default_tiers = [
            {"name": "Unranked", "level": 0, "min_wagered": Decimal("0"), "rakeback_rate": Decimal("0"), "rtp_bonus": Decimal("0")},
            {"name": "Bronze", "level": 1, "min_wagered": Decimal("100000000000"), "rakeback_rate": Decimal("0.0100"), "rtp_bonus": Decimal("0")},
            {"name": "Silver", "level": 2, "min_wagered": Decimal("100000000000000"), "rakeback_rate": Decimal("0.0200"), "rtp_bonus": Decimal("0.0050")},
            {"name": "Gold", "level": 3, "min_wagered": Decimal("100000000000000000"), "rakeback_rate": Decimal("0.0300"), "rtp_bonus": Decimal("0.0100")},
            {"name": "Platinum", "level": 4, "min_wagered": Decimal("100000000000000000000"), "rakeback_rate": Decimal("0.0500"), "rtp_bonus": Decimal("0.0150")},
            {"name": "Diamond", "level": 5, "min_wagered": Decimal("100000000000000000000000"), "rakeback_rate": Decimal("0.1000"), "rtp_bonus": Decimal("0.0200")},
        ]

        async with self.async_sessionmaker() as session:
            async with session.begin():
                for tier_data in default_tiers:
                    # Check both by level AND by name to catch any corruption
                    existing_by_level = await session.execute(
                        select(VIPTier).where(VIPTier.level == tier_data["level"])
                    )
                    existing_by_name = await session.execute(
                        select(VIPTier).where(VIPTier.name == tier_data["name"])
                    )

                    tier_by_level = existing_by_level.scalar_one_or_none()
                    tier_by_name = existing_by_name.scalar_one_or_none()

                    if not tier_by_level and not tier_by_name:
                        # Neither exists - create new tier
                        tier = VIPTier(**tier_data)
                        session.add(tier)
                    elif tier_by_level and not tier_by_name:
                        # Tier exists at this level but wrong name - update it
                        tier_by_level.name = tier_data["name"]
                        tier_by_level.min_wagered = tier_data["min_wagered"]
                        tier_by_level.rakeback_rate = tier_data["rakeback_rate"]
                        tier_by_level.rtp_bonus = tier_data["rtp_bonus"]
                    elif not tier_by_level and tier_by_name:
                        # Tier exists with this name but wrong level - this is a conflict
                        # The tier with correct name takes precedence, fix the level
                        tier_by_name.level = tier_data["level"]
                        tier_by_name.min_wagered = tier_data["min_wagered"]
                        tier_by_name.rakeback_rate = tier_data["rakeback_rate"]
                        tier_by_name.rtp_bonus = tier_data["rtp_bonus"]
                    # If both exist and are different, the level-based one is authoritative
                    # (tier_by_level exists and tier_by_name exists - they should be the same tier)
            await session.commit()

    async def upgrade_user_vip(self, user_id: int) -> UserVIP:
        """Upgrade user's VIP tier based on total wagered from GameHistory."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get or create user VIP record
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()
                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=1)
                    session.add(user_vip)
                # Calculate total wagered from GameHistory
                total_wagered = await self.get_total_wagered_all_games(user_id)
                # Determine appropriate tier based on total wagered
                new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id
            await session.commit()
            await session.refresh(user_vip)
            return user_vip
    
    async def upgrade_all_users_vip(self) -> None:
        """Upgrade VIP tiers for all users based on their total wagered."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get all user VIP records
                result = await session.execute(select(UserVIP))
                user_vips = result.scalars().all()

                for user_vip in user_vips:
                    # Calculate total wagered for each user
                    total_wagered = await self.get_total_wagered_all_games(user_vip.user_id)
                    # Determine appropriate tier based on total wagered
                    new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                    if new_tier and new_tier.id != user_vip.tier_id:
                        user_vip.tier_id = new_tier.id

            await session.commit()

    async def get_user_vip(self, user_id: int) -> UserVIP:
        """Get or create user VIP record with tier info."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = result.scalar_one_or_none()

            if not user_vip:
                # Create new VIP record with default tier
                user_vip = UserVIP(user_id=user_id, tier_id=1)
                session.add(user_vip)
                await session.commit()
                await session.refresh(user_vip)

            return user_vip

    async def get_vip_tier(self, tier_id: int) -> Optional[VIPTier]:
        """Get a specific VIP tier by ID."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(VIPTier).where(VIPTier.id == tier_id)
            )
            return result.scalar_one_or_none()

    async def get_all_vip_tiers(self) -> List[VIPTier]:
        """Get all VIP tiers ordered by level."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(VIPTier).order_by(VIPTier.level)
            )
            return list(result.scalars().all())

    async def get_total_wagered_all_games(self, user_id: int) -> Decimal:
        """Calculate total wagered across all games from GameHistory."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(GameHistory.wagered), Decimal("0")))
                .where(GameHistory.user_id == user_id)
            )
            return result.scalar() or Decimal("0")

    async def get_vip_tier_by_wagered(self, total_wagered: Decimal) -> VIPTier:
        """Determine VIP tier based on total wagered amount."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(VIPTier)
                .where(VIPTier.min_wagered <= total_wagered)
                .order_by(VIPTier.level.desc())
                .limit(1)
            )
            tier = result.scalar_one_or_none()
            if not tier:
                # Return Bronze tier as default
                result = await session.execute(
                    select(VIPTier).where(VIPTier.level == 1)
                )
                tier = result.scalar_one_or_none()
            return tier

    async def get_vip_tier_by_user(self, user_id: int) -> VIPTier:
        """Determine VIP tier based on total wagered from GameHistory."""
        total_wagered = await self.get_total_wagered_all_games(user_id)
        return await self.get_vip_tier_by_wagered(total_wagered)

    async def record_rakeback(self, user_id: int, wagered: Decimal, game_name: str) -> Decimal:
        """
        Record rakeback after game. Returns rakeback amount.
        Does NOT update total_wagered (computed from GameHistory instead).
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get or create user VIP record
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=1)
                    session.add(user_vip)

                # Get current tier and calculate rakeback
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()
                rakeback_rate = tier.rakeback_rate if tier else Decimal("0.01")
                rakeback_amount = AmountUtils.round_currency(wagered * rakeback_rate)

                # Add rakeback to user's accumulated balance
                rakeback_result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                rakeback_balance = rakeback_result.scalar_one_or_none()

                if not rakeback_balance:
                    rakeback_balance = RakebackBalance(user_id=user_id, accumulated=rakeback_amount)
                    session.add(rakeback_balance)
                else:
                    rakeback_balance.accumulated = (rakeback_balance.accumulated or Decimal("0")) + rakeback_amount

                # Update total rakeback earned
                user_vip.total_rakeback_earned = (user_vip.total_rakeback_earned or Decimal("0")) + rakeback_amount

                # Create rakeback transaction record
                transaction = RakebackTransaction(
                    user_id=user_id,
                    game_name=game_name,
                    wagered_amount=wagered,
                    rakeback_rate=rakeback_rate,
                    rakeback_amount=rakeback_amount,
                    vip_tier_id=user_vip.tier_id,
                )
                session.add(transaction)

                # Check for tier upgrade based on GameHistory
                total_wagered = await self.get_total_wagered_all_games(user_id)
                new_tier = await self.get_vip_tier_by_wagered(total_wagered + wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id

            await session.commit()
            return rakeback_amount

    # Alias for backward compatibility with casino.py
    async def update_user_wagered(self, user_id: int, amount: Decimal, game_name: str) -> Decimal:
        """Alias for record_rakeback for backward compatibility."""
        return await self.record_rakeback(user_id, amount, game_name)

    async def recalculate_user_vip_tier(self, user_id: int) -> Optional[VIPTier]:
        """Recalculate and update user's VIP tier based on total wagered from GameHistory."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    return None

                # Get total wagered from GameHistory
                total_wagered = await self.get_total_wagered_all_games(user_id)
                new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id
                    await session.commit()
                    return new_tier

                current_tier = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                return current_tier.scalar_one_or_none()

    # ==================== Rakeback Methods ====================

    async def get_rakeback_balance(self, user_id: int) -> Decimal:
        """Get accumulated unclaimed rakeback."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(RakebackBalance).where(RakebackBalance.user_id == user_id)
            )
            balance = result.scalar_one_or_none()
            return balance.accumulated if balance else Decimal("0")

    async def add_rakeback(
        self, user_id: int, amount: Decimal, game_name: str, wagered: Decimal, rate: Decimal
    ) -> None:
        """Add rakeback to user's accumulated balance."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = result.scalar_one_or_none()

                if not balance:
                    balance = RakebackBalance(user_id=user_id, accumulated=amount)
                    session.add(balance)
                else:
                    balance.accumulated = (balance.accumulated or Decimal("0")) + amount

                # Create transaction record
                transaction = RakebackTransaction(
                    user_id=user_id,
                    game_name=game_name,
                    wagered_amount=wagered,
                    rakeback_rate=rate,
                    rakeback_amount=amount,
                )
                session.add(transaction)

            await session.commit()

    async def claim_rakeback(self, user_id: int) -> Decimal:
        """Claim all accumulated rakeback. Returns amount claimed."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = result.scalar_one_or_none()

                if not balance or balance.accumulated <= Decimal("0"):
                    return Decimal("0")

                claim_amount = balance.accumulated
                balance.accumulated = Decimal("0")
                balance.last_claim = discord.utils.utcnow()
                balance.total_claimed = (balance.total_claimed or Decimal("0")) + claim_amount

                # Credit to wallet
                wallet_id = await self.get_wallet_id_for_user(user_id)
                await self.process_treasury_transaction(
                    wallet_id, claim_amount, "Rakeback Claim", "standard"
                )

            await session.commit()
            return claim_amount

    async def get_rakeback_history(self, user_id: int, limit: int = 50) -> List[RakebackTransaction]:
        """Get rakeback transaction history for a user."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(RakebackTransaction)
                .where(RakebackTransaction.user_id == user_id)
                .order_by(RakebackTransaction.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_rakeback_info(self, user_id: int) -> dict:
        """Get complete rakeback information for a user."""
        # Get total wagered from GameHistory
        total_wagered = await self.get_total_wagered_all_games(user_id)

        async with self.async_sessionmaker() as session:
            # Get VIP info
            vip_result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = vip_result.scalar_one_or_none()

            # Get tier
            tier = None
            if user_vip:
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()

            # Get rakeback balance
            balance_result = await session.execute(
                select(RakebackBalance).where(RakebackBalance.user_id == user_id)
            )
            balance = balance_result.scalar_one_or_none()

            # Get next tier
            next_tier = None
            if tier:
                next_result = await session.execute(
                    select(VIPTier).where(VIPTier.level == tier.level + 1)
                )
                next_tier = next_result.scalar_one_or_none()

            return {
                "total_wagered": total_wagered,
                "total_rakeback_earned": user_vip.total_rakeback_earned if user_vip else Decimal("0"),
                "accumulated": balance.accumulated if balance else Decimal("0"),
                "total_claimed": balance.total_claimed if balance else Decimal("0"),
                "last_claim": balance.last_claim if balance else None,
                "current_tier": tier,
                "next_tier": next_tier,
                "rakeback_rate": tier.rakeback_rate if tier else Decimal("0.01"),
            }

    # ==================== RTP Methods ====================

    async def get_effective_rtp(self, user_id: int) -> Decimal:
        """
        Calculate effective RTP adjustment from VIP tier and active RTP boosts.
        Returns percentage points (e.g., 1.5 = 1.5% RTP boost).
        """
        async with self.async_sessionmaker() as session:
            # Get VIP tier RTP bonus
            vip_result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = vip_result.scalar_one_or_none()

            vip_rtp_bonus = Decimal("0")
            if user_vip:
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()
                if tier:
                    vip_rtp_bonus = tier.rtp_bonus or Decimal("0")

            # Get active RTP boost effects
            now = discord.utils.utcnow()
            effects_result = await session.execute(
                select(ActiveEffect)
                .where(
                    ActiveEffect.user_id == user_id,
                    ActiveEffect.effect_type == "rtp_boost",
                    ActiveEffect.expires_at > now,
                )
            )
            active_effects = effects_result.scalars().all()

            boost_rtp = sum(effect.effect_value for effect in active_effects)

            return vip_rtp_bonus + boost_rtp

    async def get_adjusted_house_edge(self, user_id: int, base_edge: Decimal = Decimal("0.04")) -> Decimal:
        """
        Get house edge adjusted for VIP tier and active RTP boosts.
        Minimum 1% house edge to ensure sustainability.
        """
        rtp_adjustment = await self.get_effective_rtp(user_id)
        # Convert RTP percentage points to edge reduction
        # e.g., 2% RTP boost means we reduce house edge by 2%
        adjusted = base_edge - (rtp_adjustment / 100)

        # Ensure minimum 1% house edge
        MIN_HOUSE_EDGE = Decimal("0.01")
        return max(MIN_HOUSE_EDGE, adjusted)

    async def get_vip_leaderboard(self, limit: int = 10) -> List[dict]:
        """Get top users by total wagered (aggregated from GameHistory)."""
        async with self.async_sessionmaker() as session:
            # Aggregate total wagered from GameHistory
            stmt = (
                select(
                    GameHistory.user_id,
                    func.coalesce(func.sum(GameHistory.wagered), Decimal("0")).label("total_wagered")
                )
                .group_by(GameHistory.user_id)
                .order_by(func.sum(GameHistory.wagered).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

            leaderboard = []
            for row in rows:
                user_id = row.user_id
                total_wagered = row.total_wagered
                # Get user's VIP tier
                user_vip = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                vip = user_vip.scalar_one_or_none()
                tier = None
                if vip:
                    tier_result = await session.execute(
                        select(VIPTier).where(VIPTier.id == vip.tier_id)
                    )
                    tier = tier_result.scalar_one_or_none()

                leaderboard.append({
                    "user_id": user_id,
                    "total_wagered": total_wagered,
                    "tier": tier,
                })

            return leaderboard

    async def set_user_vip_tier(self, user_id: int, tier_id: int) -> bool:
        """Manually set a user's VIP tier (admin only)."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Verify tier exists
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == tier_id)
                )
                if not tier_result.scalar_one_or_none():
                    return False

                # Get or create user VIP
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=tier_id)
                    session.add(user_vip)
                else:
                    user_vip.tier_id = tier_id

            await session.commit()
            return True

    async def reset_user_vip(self, user_id: int) -> bool:
        """Reset user's VIP progress to default (admin only)."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if user_vip:
                    user_vip.tier_id = 1
                    user_vip.total_rakeback_earned = Decimal("0")

                # Reset rakeback balance
                balance_result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = balance_result.scalar_one_or_none()
                if balance:
                    balance.accumulated = Decimal("0")
                    balance.total_claimed = Decimal("0")

            await session.commit()
            return True
