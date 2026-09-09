from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    Index,
    Boolean,
    Numeric,
    String,
    Text,
    ForeignKey,
    TIMESTAMP,
    Enum,
    JSON,
    func,
    CheckConstraint,
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
import enum

Base = declarative_base()


class UserIdentity(Base):
    __tablename__ = "user_identities"

    user_hash = Column(String(64), primary_key=True)
    user_id = Column(BigInteger, nullable=False, unique=True)

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
    watchdog_pii_filter = Column(Boolean, default=False)
    watchdog_card_filter = Column(Boolean, default=False)
    watchdog_member_tracking = Column(Boolean, default=False)
    watchdog_message_tracking = Column(Boolean, default=False)
    watchdog_voice_tracking = Column(Boolean, default=False)
    snipe_enabled = Column(Boolean, default=False)
    auto_role_ids = Column(ARRAY(BigInteger), nullable=True)
    nuke_msg = Column(String, nullable=True)
    jail_role_id = Column(BigInteger, nullable=True)
    mute_role_id = Column(BigInteger, nullable=True)
    imute_role_id = Column(BigInteger, nullable=True)
    rmute_role_id = Column(BigInteger, nullable=True)
    watchdog_channel_id = Column(BigInteger, nullable=True)
    member_count_channel_id = Column(BigInteger, nullable=True)
    report_channel_id = Column(BigInteger, nullable=True)
    spam_channel_id = Column(BigInteger, nullable=True)
    spam_message = Column(String, nullable=True)
    jail_channel_id = Column(BigInteger, nullable=True)


class Punishment(Base):
    __tablename__ = "punishments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, nullable=False)
    user_id = Column(String(64), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    moderator_id = Column(String(64), nullable=True)
    type = Column(Enum(PunishmentType), nullable=False)
    reason = Column(String, nullable=False)
    duration = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (CheckConstraint("case_id >= 0", name="case_id_non_negative"),)


class CaseNote(Base):
    __tablename__ = "case_notes"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("punishments.id"), nullable=False)
    moderator_id = Column(String(64), nullable=False)
    note = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    punishment = relationship("Punishment", back_populates="notes")


Punishment.notes = relationship(
    "CaseNote", order_by=CaseNote.created_at, back_populates="punishment"
)


class WatchdogLog(Base):
    __tablename__ = "watchdog_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    moderator_id = Column(String(64), nullable=False)
    guild_id = Column(BigInteger, nullable=False)
    punishment_type = Column(Enum(PunishmentType), nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)


class JailedUser(Base):
    __tablename__ = "jailed_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    user_id = Column(String(64), nullable=False)
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
    user_id = Column(String(64), nullable=False)
    role_id = Column(BigInteger, nullable=False)


class CommandStatus(Base):
    __tablename__ = "command_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    command_name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    channel_id = Column(BigInteger, nullable=True)
    # guild_id scopes a serverwide disable (channel_id NULL, guild_id set).
    # NULL => bot-wide (channel_id NULL) or channel-scoped (channel_id set).
    guild_id = Column(BigInteger, nullable=True)


class CommandCooldown(Base):
    __tablename__ = "command_cooldowns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    command_name = Column(String, nullable=False)
    cooldown_expiry = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "command_name", name="uq_command_cooldowns_user_command"),
    )

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
    user_hash = Column(String(64), nullable=True, index=True)
    is_slash = Column(Boolean, default=False, nullable=False)
    latency_ms_sum = Column(BigInteger, default=0, nullable=False)
    latency_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "command_name",
            "guild_id",
            "user_hash",
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
    user_hash = Column(String(64), nullable=True, index=True)
    is_slash = Column(Boolean, default=False, nullable=False)
    error_type = Column(String, nullable=False)
    count = Column(Integer, default=0, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "bucket_date",
            "command_name",
            "guild_id",
            "user_hash",
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

    discord_id = Column(String(64), primary_key=True)
    lastfm_username = Column(String, nullable=True)
    embed_color = Column(String, default="#1DB954")


class LastFMvotes(Base):
    __tablename__ = "lastfm_votes"

    discord_id = Column(String(64), primary_key=True, nullable=False)
    command = Column(String, primary_key=True, nullable=False)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)


class BoosterRole(Base):
    __tablename__ = "booster_roles"

    guild_id = Column(BigInteger, primary_key=True)
    user_id = Column(String(64), nullable=False)
    role_id = Column(BigInteger, nullable=False)


class Wallet(Base):
    __tablename__ = "wallets"

    wallet_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(64), nullable=False, unique=True)
    balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    bank_balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    wallet_frozen = Column(Boolean, default=False, server_default="false")
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
    from_user_id = Column(String(64), nullable=True)
    to_user_id = Column(String(64), nullable=True)
    amount = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    description = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    def __repr__(self):
        return (
            f"<Transaction(id={self.id}, from_user_id={self.from_user_id}, "
            f"to_user_id={self.to_user_id}, amount={self.amount}, "
            f"description='{self.description}', timestamp={self.timestamp}, "
        )


class ItemType(enum.Enum):
    COLLECTIBLE = "collectible"
    REDEEMABLE = "redeemable"
    CONSUMABLE = "consumable"
    DEFENSIVE = "defensive"
    OFFENSIVE = "offensive"


class ItemCategory(enum.Enum):
    CONSUMABLE = "consumable"
    DEFENSIVE = "defensive"
    OFFENSIVE = "offensive"
    UTILITY = "utility"
    COSMETIC = "cosmetic"
    REDEEMABLE = "redeemable"
    COLLECTIBLE = "collectible"


class ItemRarity(enum.Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    MYTHICAL = "mythical"


class EffectType(enum.Enum):
    CURRENCY = "currency"  # Direct currency grant
    GAMBLING_MULTIPLIER = "gambling_multiplier"  # Multiplier on gambling wins
    LUCK_BOOST = "luck_boost"  # Better RNG outcomes
    EARNING_BOOST = "earning_boost"  # General earning multiplier
    COOLDOWN_REDUCTION = "cooldown_reduction"  # Reduce cooldown times
    RTP_BOOST = "rtp_boost"  # Temporary RTP percentage boost
    ANTI_ROB = "anti_rob"  # Block wallet robbery attempts
    ANTI_BANK_ROB = "anti_bank_rob"  # Block bank robbery attempts
    ROB_SHIELD = "rob_shield"  # Single-use robbery shield
    ROBBERY_DEBUFF = "robbery_debuff"  # Reduce target robbery success/payout
    FEE_INCREASE = "fee_increase"  # Increase fees target pays
    COOLDOWN_INCREASE = "cooldown_increase"  # Increase target cooldowns
    EARNING_DEBUFF = "earning_debuff"  # Reduce target earnings
    LUCK_SHIELD = "luck_shield"  # Block luck-based second chances for target


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), ForeignKey("wallets.user_id"))
    name = Column(String, nullable=False)
    serial_number = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    # native_enum=False stores enum values as VARCHAR instead of PostgreSQL
    # native enums. The deployed DB has these columns as TEXT/VARCHAR, so
    # SQLAlchemy must bind values as strings to avoid "text = itemtype" errors.
    item_type = Column(
        Enum(ItemType, native_enum=False), nullable=False, default=ItemType.COLLECTIBLE
    )
    category = Column(
        Enum(ItemCategory, native_enum=False),
        nullable=True,
        default=ItemCategory.COLLECTIBLE,
    )
    rarity = Column(
        Enum(ItemRarity, native_enum=False),
        nullable=True,
        default=ItemRarity.COMMON,
    )
    effect = Column(String, nullable=True)
    effect_value = Column(Numeric(10, 4), nullable=True)
    effect_duration = Column(Integer, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)
    targetable = Column(Boolean, default=False, nullable=False)
    daily_limit = Column(Integer, nullable=True)  # Per-user daily purchase cap
    global_daily_limit = Column(Integer, nullable=True)  # Server-wide daily stock
    tradable = Column(Boolean, default=True, nullable=False)
    durability = Column(Integer, nullable=True)  # Optional durability/hits remaining
    max_uses = Column(Integer, nullable=True)  # Optional max uses before consumed

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
    # native_enum=False stores enum values as VARCHAR instead of PostgreSQL
    # native enums. The deployed DB has these columns as TEXT/VARCHAR, so
    # SQLAlchemy must bind values as strings to avoid "text = itemtype" errors.
    item_type = Column(
        Enum(ItemType, native_enum=False), nullable=False, default=ItemType.COLLECTIBLE
    )
    category = Column(
        Enum(ItemCategory, native_enum=False),
        nullable=True,
        default=ItemCategory.COLLECTIBLE,
    )
    rarity = Column(
        Enum(ItemRarity, native_enum=False),
        nullable=True,
        default=ItemRarity.COMMON,
    )
    effect = Column(String, nullable=True)
    effect_value = Column(Numeric(10, 4), nullable=True)
    effect_duration = Column(Integer, nullable=True)
    cooldown_seconds = Column(Integer, nullable=True)
    targetable = Column(Boolean, default=False, nullable=False)
    daily_limit = Column(Integer, nullable=True)
    global_daily_limit = Column(Integer, nullable=True)
    tradable = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return (
            f"<ShopItem(name='{self.name}', price={self.price}, "
            f"quantity={self.quantity}, description='{self.description}')>"
        )


class ItemCooldown(Base):
    """Track per-user, per-item cooldowns."""

    __tablename__ = "item_cooldowns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    item_name = Column(String, nullable=False)
    cooldown_expiry = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "item_name", name="uq_item_cooldown_user_item"),
    )

    def __repr__(self):
        return f"<ItemCooldown(user_id={self.user_id}, item_name='{self.item_name}', cooldown_expiry={self.cooldown_expiry})>"


class ActiveEffect(Base):
    """Track timed effects applied to users."""

    __tablename__ = "active_effects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    effect_type = Column(String, nullable=False)
    effect_value = Column(Numeric(10, 4), nullable=False)
    source_item_name = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (Index("ix_active_effects_user_expires", "user_id", "expires_at"),)

    def __repr__(self):
        return f"<ActiveEffect(user_id={self.user_id}, effect_type='{self.effect_type}', effect_value={self.effect_value}, expires_at={self.expires_at})>"


class TradeLog(Base):
    """Audit trail for item trades."""

    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_user_id = Column(String(64), nullable=False, index=True)
    to_user_id = Column(String(64), nullable=False, index=True)
    item_id = Column(Integer, nullable=False)
    item_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(
        String, nullable=False, default="pending"
    )  # pending, completed, cancelled
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<TradeLog(id={self.id}, from={self.from_user_id}, to={self.to_user_id}, item={self.item_name}, status={self.status})>"


class ShopPurchaseLog(Base):
    """Track per-user shop purchases for daily purchase limits."""

    __tablename__ = "shop_purchase_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    shop_item_id = Column(Integer, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    purchase_date = Column(Date, nullable=False, default=discord.utils.utcnow().date)

    def __repr__(self):
        return (
            f"<ShopPurchaseLog(user_id={self.user_id}, shop_item_id={self.shop_item_id}, "
            f"quantity={self.quantity}, purchase_date={self.purchase_date})>"
        )


class Bounty(Base):
    __tablename__ = "bounties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(String(64), nullable=False)
    issuer_id = Column(String(64), nullable=False)
    claimer_id = Column(String(64), nullable=True)
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
    user_id = Column(String(64), nullable=False)
    principal = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    interest_rate = Column(
        Numeric(precision=5, scale=2), nullable=False, default=Decimal("0.00")
    )
    total_repay = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    amount_paid = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    defaulted_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="active")

    payments = relationship(
        "LoanPayment", back_populates="loan", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return (
            f"<Loan id={self.id} user_id={self.user_id} "
            f"principal={self.principal} interest_rate={self.interest_rate} due_date={self.due_date} total_repay={self.total_repay} amount_paid={self.amount_paid} status={self.status}>"
        )


class LoanPayment(Base):
    __tablename__ = "loan_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    loan_id = Column(
        Integer, ForeignKey("loans.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(String(64), nullable=False)
    payment_amount = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    payment_date = Column(
        DateTime(timezone=True), default=discord.utils.utcnow, nullable=False
    )
    payment_method = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    loan = relationship("Loan", back_populates="payments")

    def __repr__(self):
        return f"<LoanPayment id={self.id} loan_id={self.loan_id} user_id={self.user_id} amount={self.payment_amount} date={self.payment_date}>"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, unique=True)
    title = Column(String, nullable=False)
    base_salary = Column(
        Numeric(precision=38, scale=2), nullable=False, default=Decimal("0.00")
    )
    days_employed = Column(Integer, default=1, nullable=False)
    streak = Column(Integer, default=0, nullable=False)
    last_worked = Column(DateTime(timezone=True), nullable=True)
    hired_at = Column(
        DateTime(timezone=True), default=discord.utils.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<Job user_id={self.user_id} title='{self.title}' base_salary={self.base_salary} days_employed={self.days_employed} streak={self.streak}>"


class UserRoleHistory(Base):
    __tablename__ = "user_role_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    roles = Column(ARRAY(BigInteger), nullable=False)
    timestamp = Column(
        DateTime(timezone=True), default=discord.utils.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<UserRoleHistory user_id={self.user_id} roles={self.roles}>"


class Reputation(Base):
    __tablename__ = "reputation"

    discord_id = Column(String(64), primary_key=True)
    reputation = Column(Integer, default=0, nullable=False, server_default="0")

    # Daily cap on reputation earned from player activity (reactions, games, etc.)
    rep_earned_today = Column(Integer, default=0, nullable=False, server_default="0")
    last_rep_earned_date = Column(Date, nullable=True)

    # Peer reputation vote tracking and anti-abuse
    good_reps_received = Column(Integer, default=0, nullable=False, server_default="0")
    bad_reps_received = Column(Integer, default=0, nullable=False, server_default="0")
    reps_given_today = Column(Integer, default=0, nullable=False, server_default="0")
    last_rep_date = Column(Date, nullable=True)
    last_rep_targets = Column(JSON, nullable=False, default=list, server_default="[]")


class Sobs(Base):
    __tablename__ = "sobs"

    discord_id = Column(String(64), primary_key=True)
    sobs_tx = Column(Integer, default=0, nullable=False)
    sobs_rx = Column(Integer, default=0, nullable=False)


class Skulls(Base):
    __tablename__ = "skulls"

    discord_id = Column(String(64), primary_key=True)
    skulls_tx = Column(Integer, default=0, nullable=False)
    skulls_rx = Column(Integer, default=0, nullable=False)


class Flames(Base):
    __tablename__ = "flames"

    discord_id = Column(String(64), primary_key=True)
    flames_tx = Column(Integer, default=0, nullable=False)
    flames_rx = Column(Integer, default=0, nullable=False)


class Hearts(Base):
    __tablename__ = "hearts"

    discord_id = Column(String(64), primary_key=True)
    hearts_tx = Column(Integer, default=0, nullable=False)
    hearts_rx = Column(Integer, default=0, nullable=False)


class Clowns(Base):
    __tablename__ = "clowns"

    discord_id = Column(String(64), primary_key=True)
    clowns_tx = Column(Integer, default=0, nullable=False)
    clowns_rx = Column(Integer, default=0, nullable=False)


class ReactionSettings(Base):
    __tablename__ = "reaction_settings"

    guild_id = Column(BigInteger, primary_key=True)
    self_reactions_enabled = Column(Boolean, default=False)


class Blacklist(Base):
    __tablename__ = "blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, unique=True)
    admin_id = Column(String(64), nullable=False)
    reason = Column(String, nullable=False, default="No reason provided")
    added_at = Column(
        DateTime(timezone=True), default=discord.utils.utcnow, nullable=False
    )


class FavoriteSongs(Base):
    __tablename__ = "favorite_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    song_title = Column(String, nullable=False)


class UserTimezone(Base):
    __tablename__ = "user_timezones"

    user_id = Column(String(64), primary_key=True)
    timezone = Column(String, nullable=True)


class UserLocation(Base):
    __tablename__ = "user_locations"

    user_id = Column(String(64), primary_key=True)
    location_encrypted = Column(Text, nullable=True)


class Block(Base):
    __tablename__ = "blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index = Column(Integer, nullable=False)
    block_hash = Column(String(64), nullable=False, unique=True)
    previous_hash = Column(String(64), nullable=False)
    transactions = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    validator_id = Column(String(64), nullable=True)
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


class EconomicMetricsHistory(Base):
    __tablename__ = "economic_metrics_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    timestamp = Column(TIMESTAMP(timezone=True), default=func.now(), nullable=False)

    # Supply metrics
    total_supply = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    circulating_supply = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    treasury_balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))

    # Economic ratios
    treasury_health = Column(Numeric(precision=10, scale=4), default=Decimal("0.0000"))
    liquidity_ratio = Column(Numeric(precision=10, scale=4), default=Decimal("0.0000"))
    velocity_of_money = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.0000")
    )
    volatility_index = Column(Numeric(precision=10, scale=4), default=Decimal("0.0000"))

    # Activity metrics
    transaction_volume = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))
    active_users = Column(Integer, default=0)
    avg_wallet_balance = Column(Numeric(precision=38, scale=2), default=Decimal("0.00"))

    # Rates
    fee_rate = Column(Numeric(precision=10, scale=4), default=Decimal("0.0000"))
    passive_income_rate = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.0000")
    )

    # Auto-rebalance tracking
    auto_minted_today = Column(
        Numeric(precision=38, scale=2), default=Decimal("0.00"), nullable=True
    )
    auto_burned_today = Column(
        Numeric(precision=38, scale=2), default=Decimal("0.00"), nullable=True
    )
    rebalance_target = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.5000"), nullable=True
    )

    __table_args__ = (UniqueConstraint("date", name="uq_economic_metrics_date"),)


class UserEconomicPreferences(Base):
    __tablename__ = "user_economic_preferences"

    user_id = Column(String(64), primary_key=True)
    # Notification preferences
    economic_alerts_enabled = Column(Boolean, default=True)
    velocity_alerts_enabled = Column(Boolean, default=True)
    liquidity_alerts_enabled = Column(Boolean, default=True)
    volatility_alerts_enabled = Column(Boolean, default=True)

    # Alert thresholds
    velocity_low_threshold = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.1")
    )
    liquidity_low_threshold = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.2")
    )
    volatility_high_threshold = Column(
        Numeric(precision=10, scale=4), default=Decimal("0.05")
    )

    # Personalized recommendations
    risk_tolerance = Column(String, default="moderate")  # "low", "moderate", "high"
    investment_style = Column(
        String, default="balanced"
    )  # "conservative", "balanced", "aggressive"

    # Last notification timestamps
    last_velocity_alert = Column(TIMESTAMP(timezone=True), nullable=True)
    last_liquidity_alert = Column(TIMESTAMP(timezone=True), nullable=True)
    last_volatility_alert = Column(TIMESTAMP(timezone=True), nullable=True)


class MinesSettings(Base):
    __tablename__ = "mines_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bomb_count = Column(Integer, nullable=False)
    gem_count = Column(Integer, nullable=False)
    multiplier = Column(Numeric(precision=10, scale=2), nullable=False)


class HeardleGameStats(Base):
    __tablename__ = "heardle_game_stats"

    user_id = Column(String(64), primary_key=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    streak = Column(Integer, default=0)


class GameHistory(Base):
    __tablename__ = "game_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    game_name = Column(String, nullable=False)
    outcome = Column(String, nullable=False)  # 'win', 'loss', 'tie', etc.
    wagered = Column(Numeric(precision=38, scale=2), nullable=False)
    payout_multiplier = Column(Numeric(precision=38, scale=10), nullable=True)
    payout_amount = Column(Numeric(precision=38, scale=2), nullable=True)
    client_seed = Column(String(64), nullable=False)  # widened
    used_server_seed = Column(
        String(64), nullable=True
    )  # widened + nullable (back-fill later)
    nonce = Column(Integer, nullable=False)
    hash = Column(String(64), nullable=False)  # sha256 hex is 64 chars
    provider = Column(
        String(16), nullable=False, default="fairgate", server_default="fairgate"
    )  # 'fairgate' is the only supported provider
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
    owner_id = Column(String(64), nullable=True)
    participants = Column(ARRAY(String(64)), nullable=True)
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


class Task(Base):
    __tablename__ = "tasks"
    task_id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False)
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
    owner_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)


class UserNameHistory(Base):
    __tablename__ = "user_name_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False)
    old_name = Column(String, nullable=False)
    new_name = Column(String, nullable=False)
    change_type = Column(String, nullable=False)
    timestamp = Column(
        DateTime(timezone=True), default=discord.utils.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "change_type IN ('username', 'nickname')",
            name="user_name_history_change_type_check",
        ),
    )


class CryptoAsset(Base):
    __tablename__ = "crypto_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), ForeignKey("wallets.user_id"), nullable=False)
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
    holder_id = Column(String(64), nullable=True)
    hits = Column(Integer, default=0)
    passes = Column(Integer, default=0)
    steals = Column(Integer, default=0)
    locked = Column(Boolean, default=False)
    flavor = Column(String, default="classic", nullable=False)


class UserAlt(Base):
    __tablename__ = "user_alts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    main_user_id = Column(String(64), nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    alt_user_id = Column(String(64), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "main_user_id", "guild_id", "alt_user_id", name="uix_user_alts"
        ),
    )




class OwnerAuditLog(Base):
    """Tamper-resistant log of successfully executed owner-only commands."""

    __tablename__ = "owner_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    command_name = Column(String, nullable=False)
    guild_id = Column(BigInteger, nullable=True)
    channel_id = Column(BigInteger, nullable=True)
    args = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (
        Index("ix_owner_audit_user_created", "user_id", "created_at"),
        Index("ix_owner_audit_command_created", "command_name", "created_at"),
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
    user_id = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("guild_id", "role_id", name="unique_guild_role"),
    )


class VIPTier(Base):
    """VIP tier levels with escalating benefits."""

    __tablename__ = "vip_tiers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # Bronze, Silver, Gold, etc.
    level = Column(Integer, nullable=False, unique=True)
    min_wagered = Column(Numeric(38, 2), nullable=False)  # Threshold to qualify
    rakeback_rate = Column(Numeric(5, 4), nullable=False)  # 0.0100 = 1%
    rtp_bonus = Column(Numeric(5, 4), default=Decimal("0"))  # RTP % added
    color = Column(String, default="#FFFFFF")
    icon = Column(String, nullable=True)

    def __repr__(self):
        return f"<VIPTier(level={self.level}, name='{self.name}', rakeback={self.rakeback_rate}, rtp_bonus={self.rtp_bonus})>"


class UserVIP(Base):
    """Track user VIP status. Total wagered is computed from GameHistory."""

    __tablename__ = "user_vip"

    user_id = Column(String(64), primary_key=True)
    tier_id = Column(Integer, ForeignKey("vip_tiers.id"), default=1)
    total_rakeback_earned = Column(Numeric(38, 2), default=Decimal("0.00"))

    tier = relationship("VIPTier", backref="users")

    def __repr__(self):
        return f"<UserVIP(user_id={self.user_id}, tier_id={self.tier_id})>"


class RakebackBalance(Base):
    """Track accumulated unclaimed rakeback."""

    __tablename__ = "rakeback_balances"

    user_id = Column(String(64), primary_key=True)
    accumulated = Column(Numeric(38, 2), default=Decimal("0.00"))
    last_claim = Column(DateTime(timezone=True), nullable=True)
    total_claimed = Column(Numeric(38, 2), default=Decimal("0.00"))

    def __repr__(self):
        return (
            f"<RakebackBalance(user_id={self.user_id}, accumulated={self.accumulated})>"
        )


class RakebackTransaction(Base):
    """Audit trail for rakeback accumulation."""

    __tablename__ = "rakeback_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    game_name = Column(String, nullable=False)
    wagered_amount = Column(Numeric(38, 2), nullable=False)
    rakeback_rate = Column(Numeric(5, 4), nullable=False)
    rakeback_amount = Column(Numeric(38, 2), nullable=False)
    vip_tier_id = Column(Integer, ForeignKey("vip_tiers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=discord.utils.utcnow)

    __table_args__ = (Index("ix_rakeback_user_created", "user_id", "created_at"),)

    def __repr__(self):
        return f"<RakebackTransaction(user_id={self.user_id}, game='{self.game_name}', amount={self.rakeback_amount})>"
