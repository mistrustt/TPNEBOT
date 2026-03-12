from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    Index,
    Boolean,
    Numeric,
    String,
    ForeignKey,
    TIMESTAMP,
    Enum,
    JSON,
    func,
    CheckConstraint,
    LargeBinary,
    UniqueConstraint,
    DateTime,
    Date,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import declarative_base
from decimal import Decimal
import uuid
import hashlib
import json
import discord
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()


class CaseStatus(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    REVIEWED = "reviewed"


class PunishmentType(enum.Enum):
    BAN = "ban"
    UNBAN = "unban"
    KICK = "kick"
    MUTE = "mute"
    UNMUTE = "unmute"
    TIMEOUT = "timeout"
    UNTIMEOUT = "untimeout"
    JAIL = "jail"
    UNJAIL = "unjail"
    IMUTE = "imute"
    IUNMUTE = "unimute"
    RMUTE = "rmute"
    RUNMUTE = "unrmute"
    HBAN = "hackban"
    WARN = "warn"
    REPORT = "report"


class BotConfig(Base):
    __tablename__ = "bot_config"

    id = Column(Integer, primary_key=True)
    bot_id = Column(BigInteger, nullable=False)
    setup_complete = Column(Boolean, default=False)
    loaded_cogs = Column(ARRAY(String), nullable=True)
    unloaded_cogs = Column(ARRAY(String), nullable=True)


class ServerSettings(Base):
    __tablename__ = "server_settings"

    guild_id = Column(BigInteger, primary_key=True)
    member_count = Column(Integer, nullable=False, default=0)
    prefix = Column(String, default="!")
    antimp3_enabled = Column(Boolean, default=False)
    watchdog_enabled = Column(Boolean, default=False)
    watchdog_pii_filter = Column(Boolean, default=True)
    watchdog_card_filter = Column(Boolean, default=True)
    watchdog_member_tracking = Column(Boolean, default=True)
    watchdog_message_tracking = Column(Boolean, default=True)
    watchdog_voice_tracking = Column(Boolean, default=True)
    auto_role_ids = Column(ARRAY(BigInteger), nullable=True)
    nuke_msg = Column(String, nullable=True)
    jail_role_id = Column(BigInteger, nullable=True)
    mute_role_id = Column(BigInteger, nullable=True)
    imute_role_id = Column(BigInteger, nullable=True)
    rmute_role_id = Column(BigInteger, nullable=True)
    booster_role_id = Column(BigInteger, nullable=True)
    watchdog_channel_id = Column(BigInteger, nullable=True)
    member_count_channel_id = Column(BigInteger, nullable=True)
    report_channel_id = Column(BigInteger, nullable=True)
    spam_channel_id = Column(BigInteger, nullable=True)
    jail_channel_id = Column(BigInteger, nullable=True)


class Punishment(Base):
    __tablename__ = "punishments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    moderator_id = Column(BigInteger, nullable=True)
    type = Column(Enum(PunishmentType), nullable=False)
    reason = Column(String, nullable=False)
    duration = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (CheckConstraint("case_id >= 0", name="case_id_non_negative"),)


class CaseNote(Base):
    __tablename__ = "case_notes"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("punishments.id"), nullable=False)
    moderator_id = Column(BigInteger, nullable=False)
    note = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    punishment = relationship("Punishment", back_populates="notes")


Punishment.notes = relationship(
    "CaseNote", order_by=CaseNote.created_at, back_populates="punishment"
)


class WatchdogSetting(Base):
    __tablename__ = "watchdog_settings"

    guild_id = Column(BigInteger, primary_key=True)
    enabled = Column(Boolean, default=True)
    channel_id = Column(BigInteger, nullable=False)


class WatchdogLog(Base):
    __tablename__ = "watchdog_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    moderator_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    punishment_type = Column(Enum(PunishmentType), nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)


class JailSetting(Base):
    __tablename__ = "jail_settings"

    guild_id = Column(BigInteger, primary_key=True)
    role_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)


class JailedUser(Base):
    __tablename__ = "jailed_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    jailed_until = Column(TIMESTAMP(timezone=True), nullable=True)
    roles = Column(ARRAY(BigInteger), nullable=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    def __repr__(self):
        return (
            f"<JailedUser id={self.id} guild_id={self.guild_id} "
            f"user_id={self.user_id} until={self.jailed_until}>"
        )


class ImageMuteSetting(Base):
    __tablename__ = "image_mute_settings"

    guild_id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    role_id = Column(BigInteger, nullable=False)


class CommandStatus(Base):
    __tablename__ = "command_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    command_name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    channel_id = Column(BigInteger, nullable=True)


class CommandCooldown(Base):
    __tablename__ = "command_cooldowns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    command_name = Column(String, nullable=False)
    cooldown_expiry = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<CommandCooldown(user_id={self.user_id}, command_name={self.command_name}, cooldown_expiry={self.cooldown_expiry})>"


class CommandUsageDaily(Base):
    __tablename__ = "command_usage_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bucket_date = Column(Date, nullable=False, index=True)
    command_name = Column(String, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=True, index=True)
    user_hash = Column(String(64), nullable=True, index=True)
    is_slash = Column(Boolean, default=False, nullable=False)
    count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "command_name",
            "guild_id",
            "user_hash",
            "is_slash",
            name="uq_command_usage_daily",
        ),
    )


class CommandLatencyDaily(Base):
    __tablename__ = "command_latency_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bucket_date = Column(Date, nullable=False, index=True)
    command_name = Column(String, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=True, index=True)
    is_slash = Column(Boolean, default=False, nullable=False)
    latency_ms_sum = Column(BigInteger, default=0, nullable=False)
    latency_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "command_name",
            "guild_id",
            "is_slash",
            name="uq_command_latency_daily",
        ),
    )


class CommandErrorDaily(Base):
    __tablename__ = "command_error_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bucket_date = Column(Date, nullable=False, index=True)
    command_name = Column(String, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=True, index=True)
    is_slash = Column(Boolean, default=False, nullable=False)
    error_type = Column(String, nullable=False)
    count = Column(Integer, default=0, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "command_name",
            "guild_id",
            "is_slash",
            "error_type",
            name="uq_command_error_daily",
        ),
    )


class DailyUserExposure(Base):
    __tablename__ = "daily_user_exposure"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bucket_date = Column(Date, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=True, index=True)
    user_hash = Column(String(64), nullable=False, index=True)
    first_seen_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "guild_id",
            "user_hash",
            name="uq_daily_user_exposure",
        ),
    )


class LastFMusers(Base):
    __tablename__ = "lastfm_users"

    discord_id = Column(BigInteger, primary_key=True)
    lastfm_username = Column(String, nullable=True)
    embed_color = Column(String, default="#1DB954")


class LastFMvotes(Base):
    __tablename__ = "lastfm_votes"

    discord_id = Column(BigInteger, primary_key=True, nullable=False)
    command = Column(String, primary_key=True, nullable=False)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)


class BoosterRole(Base):
    __tablename__ = "booster_roles"

    guild_id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    role_id = Column(BigInteger, nullable=False)


class Wallet(Base):
    __tablename__ = "wallets"

    wallet_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, nullable=False, unique=True)
    public_key = Column(LargeBinary, nullable=False, unique=True)
    private_key = Column(LargeBinary, nullable=False, unique=True)
    hashed_key = Column(String, nullable=False, unique=True)
    salt = Column(LargeBinary, nullable=False)
    balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    wallet_frozen = Column(Boolean, default=False)
    client_seed = Column(String(64), nullable=True)  # widened (future-proof)
    nonce = Column(Integer, default=0)
    server_seed = Column(String(64), nullable=True)  # widened
    previous_server_seed = Column(String(64), nullable=True)  # widened
    seed_rotated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
        UniqueConstraint("user_id"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_user_id = Column(BigInteger, nullable=True)
    to_user_id = Column(BigInteger, nullable=True)
    amount = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    description = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=discord.utils.utcnow)
    block_hash = Column(String(64), nullable=True)

    def __repr__(self):
        return (
            f"<Transaction(id={self.id}, from_user_id={self.from_user_id}, "
            f"to_user_id={self.to_user_id}, amount={self.amount}, "
            f"description='{self.description}', timestamp={self.timestamp}, "
            f"block_hash='{self.block_hash}')>"
        )


class ItemType(enum.Enum):
    COLLECTIBLE = "collectible"
    REDEEMABLE = "redeemable"
    CONSUMABLE = "consumable"


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("wallets.user_id"))
    name = Column(String, nullable=False)
    serial_number = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    item_type = Column(Enum(ItemType), nullable=False, default=ItemType.COLLECTIBLE)
    effect = Column(String, nullable=True)
    effect_value = Column(Integer, nullable=True)
    effect_duration = Column(Integer, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)

    def __repr__(self):
        return f"<Item(user_id={self.user_id}, name='{self.name}', quantity={self.quantity})>"


class ShopItem(Base):
    __tablename__ = "shop_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Integer, nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    unlimited = Column(Boolean, default=False)
    item_type = Column(Enum(ItemType), nullable=False, default=ItemType.COLLECTIBLE)
    effect = Column(String, nullable=True)
    effect_value = Column(Integer, nullable=True)
    effect_duration = Column(Integer, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)

    def __repr__(self):
        return (
            f"<ShopItem(name='{self.name}', price={self.price}, "
            f"quantity={self.quantity}, description='{self.description}')>"
        )


class Bounty(Base):
    __tablename__ = "bounties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(BigInteger, nullable=False)
    issuer_id = Column(BigInteger, nullable=False)
    claimer_id = Column(BigInteger, nullable=True)
    reward = Column(
        Numeric(precision=38, scale=8), nullable=False, default=Decimal("0.00")
    )
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    def __repr__(self):
        return (
            f"<Bounty id={self.id} target={self.target_id} issuer={self.issuer_id} "
            f"reward={self.reward} active={self.active}>"
        )

class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    principal = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    interest_rate = Column(Numeric(precision=5, scale=2), nullable=False, default=Decimal("0.00"))
    total_repay = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    due_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="active")

    def __repr__(self):
        return (
            f"<Loan id={self.id} user_id={self.user_id} "
            f"principal={self.principal} interest_rate={self.interest_rate} due_date={self.due_date} total_repay={self.total_repay} status={self.status}>"
        )

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    title = Column(String, nullable=False)
    salary = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    last_worked = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Job user_id={self.user_id} title='{self.title}' salary={self.salary}>"

class UserRoleHistory(Base):
    __tablename__ = "user_role_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    roles = Column(ARRAY(BigInteger), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=discord.utils.utcnow, nullable=False)

    def __repr__(self):
        return f"<UserRoleHistory user_id={self.user_id} roles={self.roles}>"


class Reputation(Base):
    __tablename__ = "reputation"

    discord_id = Column(BigInteger, primary_key=True)
    reputation = Column(Integer, default=0, nullable=False)


class Sobs(Base):
    __tablename__ = "sobs"

    discord_id = Column(BigInteger, primary_key=True)
    sobs_tx = Column(Integer, default=0, nullable=False)
    sobs_rx = Column(Integer, default=0, nullable=False)


class Skulls(Base):
    __tablename__ = "skulls"

    discord_id = Column(BigInteger, primary_key=True)
    skulls_tx = Column(Integer, default=0, nullable=False)
    skulls_rx = Column(Integer, default=0, nullable=False)


class Flames(Base):
    __tablename__ = "flames"

    discord_id = Column(BigInteger, primary_key=True)
    flames_tx = Column(Integer, default=0, nullable=False)
    flames_rx = Column(Integer, default=0, nullable=False)


class Hearts(Base):
    __tablename__ = "hearts"

    discord_id = Column(BigInteger, primary_key=True)
    hearts_tx = Column(Integer, default=0, nullable=False)
    hearts_rx = Column(Integer, default=0, nullable=False)


class Clowns(Base):
    __tablename__ = "clowns"

    discord_id = Column(BigInteger, primary_key=True)
    clowns_tx = Column(Integer, default=0, nullable=False)
    clowns_rx = Column(Integer, default=0, nullable=False)


class ReactionSettings(Base):
    __tablename__ = "reaction_settings"

    guild_id = Column(BigInteger, primary_key=True)
    self_reactions_enabled = Column(Boolean, default=False)


class Blacklist(Base):
    __tablename__ = "blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, unique=True)
    reason = Column(String, nullable=False, default="No reason provided")


class FavoriteSongs(Base):
    __tablename__ = "favorite_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    song_title = Column(String, nullable=False)


class UserTimezone(Base):
    __tablename__ = "user_timezones"

    user_id = Column(BigInteger, primary_key=True)
    timezone = Column(String, nullable=True)


class UserLocation(Base):
    __tablename__ = "user_locations"

    user_id = Column(BigInteger, primary_key=True)
    location = Column(String, nullable=True)
    lat = Column(Numeric(precision=10, scale=6), nullable=True)
    lon = Column(Numeric(precision=10, scale=6), nullable=True)

    def __repr__(self):
        return f"<UserLocation(user_id={self.user_id}, location='{self.location}', lat={self.lat}, lon={self.lon})>"


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    wallet_id = Column(
        UUID(as_uuid=True), ForeignKey("wallets.wallet_id"), primary_key=True
    )
    balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_bank_balance_non_negative"),
    )


class Block(Base):
    __tablename__ = "blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index = Column(Integer, nullable=False)
    block_hash = Column(String(64), nullable=False, unique=True)
    previous_hash = Column(String(64), nullable=False)
    transactions = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    validator_id = Column(BigInteger, nullable=True)
    validator_signature = Column(String, nullable=True)

    def compute_hash(self):
        """
        Compute the SHA-256 hash of the block data.
        Note: validator fields are not included in the hash calculation.
        """
        block_data = {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "created_at": (self.created_at or discord.utils.utcnow()).isoformat(),
        }
        return hashlib.sha256(
            json.dumps(block_data, sort_keys=True).encode()
        ).hexdigest()


class Supply(Base):
    __tablename__ = "supply"
    id = Column(Integer, primary_key=True)
    total_supply = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    circulating = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    treasury = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))

    __table_args__ = (
        CheckConstraint("treasury >= 0", name="ck_supply_treasury_non_negative"),
    )


class MinesSettings(Base):
    __tablename__ = "mines_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bomb_count = Column(Integer, nullable=False)
    gem_count = Column(Integer, nullable=False)
    multiplier = Column(Numeric(precision=10, scale=2), nullable=False)


class GameStats(Base):
    __tablename__ = "game_stats"

    user_id = Column(BigInteger, primary_key=True)
    game_name = Column(String, primary_key=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    total_wagered = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))


class HeardleGameStats(Base):
    __tablename__ = "heardle_game_stats"

    user_id = Column(BigInteger, primary_key=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    streak = Column(Integer, default=0)


class GameHistory(Base):
    __tablename__ = "game_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    game_name = Column(String, nullable=False)
    outcome = Column(String, nullable=False)  # 'win', 'loss', 'tie', etc.
    wagered = Column(Numeric(precision=38, scale=2), nullable=False)
    client_seed = Column(String(64), nullable=False)  # widened
    used_server_seed = Column(
        String(64), nullable=True
    )  # widened + nullable (back-fill later)
    nonce = Column(Integer, nullable=False)
    hash = Column(String(64), nullable=False)  # sha256 hex is 64 chars
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_game_history_user_created", "user_id", "created_at"),
        Index("ix_game_history_hash", "hash"),
    )


class GameSession(Base):
    __tablename__ = "game_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_name = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="active")
    guild_id = Column(BigInteger, nullable=True)
    channel_id = Column(BigInteger, nullable=True)
    message_id = Column(BigInteger, nullable=True)
    owner_id = Column(BigInteger, nullable=True)
    participants = Column(ARRAY(BigInteger), nullable=True)
    wager_total = Column(Numeric(precision=38, scale=2), nullable=True)
    state = Column(JSON, nullable=True)
    rng = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GameSessionEvent(Base):
    __tablename__ = "game_session_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("game_sessions.id", ondelete="CASCADE")
    )
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReportSetting(Base):
    __tablename__ = "report_settings"

    guild_id = Column(BigInteger, primary_key=True)
    channel_id = Column(BigInteger, nullable=False)


class Streak(Base):
    __tablename__ = "streak"

    user_id = Column(BigInteger, primary_key=True)
    streak_count = Column(Integer, default=0, nullable=False)
    last_worked = Column(DateTime(timezone=True), nullable=False, default=discord.utils.utcnow)

    def __repr__(self):
        return f"<Streak(user_id={self.user_id}, streak_count={self.streak_count}, last_worked={self.last_worked})>"


class Task(Base):
    __tablename__ = "tasks"
    task_id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    task = Column(String, nullable=False)
    completed = Column(Boolean, default=False)
    order_index = Column(Integer, nullable=False)


class JTCSettings(Base):
    __tablename__ = "jtc_settings"

    guild_id = Column(BigInteger, primary_key=True)
    setup_complete = Column(Boolean, default=False)
    jtc_channel_id = Column(BigInteger, nullable=False)
    control_panel_message_id = Column(BigInteger, nullable=True)


class TempVoiceChannel(Base):
    __tablename__ = "temp_voice_channels"

    channel_id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, nullable=False)
    owner_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)


class UserNameHistory(Base):
    __tablename__ = "user_name_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    old_name = Column(String, nullable=False)
    new_name = Column(String, nullable=False)
    change_type = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=discord.utils.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "change_type IN ('username', 'nickname')",
            name="user_name_history_change_type_check",
        ),
    )


class CryptoAsset(Base):
    __tablename__ = "crypto_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("wallets.user_id"), nullable=False)
    symbol = Column(String, nullable=False)
    amount = Column(Numeric(precision=38, scale=8), default=Decimal("0.00000000"))
    purchase_price = Column(Numeric(precision=38, scale=8), default=Decimal("0.00"))
    purchase_date = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    def __repr__(self):
        return f"<CryptoAsset(user_id={self.user_id}, symbol='{self.symbol}', amount={self.amount})>"


class CryptoPrice(Base):
    __tablename__ = "crypto_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    price = Column(
        Numeric(precision=38, scale=8), nullable=False, default=Decimal("0.00")
    )
    timestamp = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    def __repr__(self):
        return f"<CryptoPrice(symbol='{self.symbol}', price={self.price}, timestamp={self.timestamp})>"


class LockdownChannel(Base):
    __tablename__ = "lockdown_channels"

    guild_id = Column(BigInteger, primary_key=True)
    channel_id = Column(BigInteger, primary_key=True)


class Juul(Base):
    __tablename__ = "juuls"

    guild_id = Column(BigInteger, primary_key=True)
    holder_id = Column(BigInteger, nullable=True)
    hits = Column(Integer, default=0)
    passes = Column(Integer, default=0)
    steals = Column(Integer, default=0)
    locked = Column(Boolean, default=False)


class UserAlt(Base):
    __tablename__ = "user_alts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    main_user_id = Column(BigInteger, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    alt_user_id = Column(BigInteger, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "main_user_id", "guild_id", "alt_user_id", name="uix_user_alts"
        ),
    )


class CommandRoleRestriction(Base):
    __tablename__ = "command_role_restrictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    command_name = Column(String, nullable=False)
    role_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "guild_id", "command_name", "role_id", name="unique_guild_command_role"
        ),
    )


class ForceRole(Base):
    __tablename__ = "force_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    role_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("guild_id", "role_id", name="unique_guild_role"),
    )
