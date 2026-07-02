from sqlalchemy.future import select
from sqlalchemy import update, delete, text, exists, case, literal_column, distinct
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.exc import (
    SQLAlchemyError,
    OperationalError,
    DBAPIError,
    DisconnectionError,
    TimeoutError as SATimeoutError,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
import asyncio
from contextlib import asynccontextmanager
import functools
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
    OwnerAuditLog,
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

from .managers import (
    BaseManager,
    CoreMixin,
    GuildMixin,
    UserMixin,
    ModerationMixin,
    EconomyMixin,
    CasinoMixin,
    InventoryMixin,
    SocialMixin,
    MusicMixin,
)

logger = logging.getLogger("discord_bot")

ADMIN_IDS = {284439598422163476, 1099696209637167145, 881692219260665867}  # Owner IDs


class DatabaseManager(
    CoreMixin,
    GuildMixin,
    UserMixin,
    ModerationMixin,
    EconomyMixin,
    CasinoMixin,
    InventoryMixin,
    SocialMixin,
    MusicMixin,
    BaseManager,
):
    pass
