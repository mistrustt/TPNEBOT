from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.exc import (
    SQLAlchemyError,
    OperationalError,
    DBAPIError,
    DisconnectionError,
    TimeoutError as SATimeoutError,
)
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker
import asyncio
from contextlib import asynccontextmanager
import functools
from typing import Optional
from ..models import Base, UserIdentity
import logging


def _hash_user_id(user_id) -> str:
    """Centralized user ID hashing used by all managers."""
    from utils.security import hash_user_id

    return hash_user_id(user_id)


try:
    import asyncpg.exceptions as _apg_exc
except Exception:
    _apg_exc = None

logger = logging.getLogger("discord_bot")

# Exceptions treated as transient database connection failures.  These are retried
# with exponential backoff so brief outages (restart, network blip, failover)
# don't crash commands or background tasks.
_RETRYABLE_DB_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    DBAPIError,
    DisconnectionError,
    SATimeoutError,
    ConnectionResetError,
    ConnectionRefusedError,
    OSError,
)

if _apg_exc is not None:
    _RETRYABLE_DB_ERRORS += (
        _apg_exc.ConnectionDoesNotExistError,
        _apg_exc.ConnectionFailureError,
        _apg_exc.PostgresConnectionError,
        _apg_exc.InterfaceError,
        _apg_exc.TooManyConnectionsError,
    )


def retry_db(max_retries: int = None, base_delay: float = None):
    """Decorator that retries a DatabaseManager coroutine on transient DB errors."""

    def decorator(coro):
        @functools.wraps(coro)
        async def wrapper(self: "BaseManager", *args, **kwargs):
            return await self._db_retry(
                coro, self, *args, retries=max_retries, base_delay=base_delay, **kwargs
            )

        return wrapper

    return decorator


class BaseManager:
    """Base manager with connection resilience and user ID hashing helpers."""

    @staticmethod
    def hash_user_id(user_id) -> str:
        """Hash a raw Discord user ID for storage or lookup."""
        return _hash_user_id(user_id)

    async def resolve_user_hash(self, user_hash: str) -> int | None:
        """Look up the raw Discord ID for a stored hash, if it exists."""
        if user_hash is None:
            return None
        async with self.async_sessionmaker() as session:
            row = await session.get(UserIdentity, user_hash)
            return row.user_id if row else None

    async def resolve_user_hashes(
        self, user_hashes: list[str]
    ) -> dict[str, int | None]:
        """Batch-resolve hashes to raw Discord IDs."""
        if not user_hashes:
            return {}
        async with self.async_sessionmaker() as session:
            rows = await session.execute(
                select(UserIdentity).where(UserIdentity.user_hash.in_(user_hashes))
            )
            mapping = {r.user_hash: r.user_id for r in rows.scalars().all()}
        return {h: mapping.get(h) for h in user_hashes}

    async def ensure_user_identity(self, user_id: int) -> str:
        """Hash a user and make sure the raw ID is recorded in user_identities.

        Call this whenever the bot sees a Discord user for the first time in a
        session, so later resolve_user_hash() calls can recover the raw ID.
        """
        user_hash = _hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(UserIdentity)
                    .values(user_hash=user_hash, user_id=user_id)
                    .on_conflict_do_nothing(index_elements=["user_hash"])
                )
        return user_hash

    def __init__(self, database_url: str):
        # pool_pre_ping validates connections before checkout, which eliminates
        # most "connection closed unexpectedly" errors. pool_recycle forces old
        # connections to be replaced, and the connect_args set sane timeouts.
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            connect_args={
                "timeout": 10,
                "command_timeout": 60,
                "server_settings": {"application_name": "tpnebot"},
            },
        )
        self.async_sessionmaker = sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._latest_metrics = {}
        self._db_retry_count = 5
        self._db_retry_base_delay = 1.0
        self._db_retry_max_delay = 30.0

    @staticmethod
    def _is_retryable_db_error(exc: Exception) -> bool:
        """Return True if *exc* looks like a transient connection failure."""
        if isinstance(exc, _RETRYABLE_DB_ERRORS):
            return True
        msg = str(exc).lower()
        markers = (
            "connection",
            "connect",
            "closed",
            "reset",
            "refused",
            "timeout",
            "broken pipe",
            "network",
            "08",  # SQLState connection exception class
            "cannot connect",
            "unexpectedly closed",
            "peer closed",
        )
        return any(marker in msg for marker in markers)

    async def _db_retry(
        self,
        coro,
        *args,
        retries: int = None,
        base_delay: float = None,
        **kwargs,
    ):
        """Run *coro* with exponential backoff on transient DB errors."""
        retries = retries if retries is not None else self._db_retry_count
        base_delay = base_delay if base_delay is not None else self._db_retry_base_delay
        last_exc: Optional[Exception] = None

        for attempt in range(retries):
            try:
                return await coro(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable_db_error(exc):
                    raise
                if attempt == retries - 1:
                    break
                delay = min(base_delay * (2**attempt), self._db_retry_max_delay)
                logger.warning(
                    f"Database operation failed (attempt {attempt + 1}/{retries}): {exc}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

        logger.error(f"Database operation failed after {retries} attempts: {last_exc}")
        raise last_exc

    @asynccontextmanager
    async def retrying_session(self, retries: int = None, base_delay: float = None):
        """Yield an AsyncSession, retrying acquisition on transient errors.

        Note: this only retries getting the session/connection.  It does not
        retry work performed inside the ``async with`` block; for that, wrap the
        operation with ``@retry_db`` or ``self._db_retry``.
        """

        async def _acquire():
            return self.async_sessionmaker()

        session = await self._db_retry(_acquire, retries=retries, base_delay=base_delay)
        try:
            yield session
        finally:
            await session.close()

    async def initialize(self):
        """Create tables, retrying on transient connection failures."""

        async def _init():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await self.create_tables()
            await self._repair_daily_user_hash_columns()
            await self._repair_command_cooldowns_constraint()

        try:
            await self._db_retry(_init, retries=self._db_retry_count, base_delay=2.0)
        except SQLAlchemyError as e:
            logger.error(f"Error initializing database: {e}")

    async def _repair_daily_user_hash_columns(self):
        """
        Ensure the daily analytics tables have the user_hash column and the
        matching unique constraint that includes user_hash. Older deployments
        created these tables before user_hash existed, so Base.metadata.create_all()
        skips both the column and the constraint update.
        """
        repairs = [
            {
                "table": "command_usage_daily",
                "column_ddl": "ALTER TABLE command_usage_daily ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
                "index_ddl": "CREATE INDEX IF NOT EXISTS ix_command_usage_daily_user_hash ON command_usage_daily(user_hash)",
                "drop_uq": "ALTER TABLE command_usage_daily DROP CONSTRAINT IF EXISTS uq_command_usage_daily",
                "create_uq": (
                    "ALTER TABLE command_usage_daily ADD CONSTRAINT uq_command_usage_daily "
                    "UNIQUE (bucket_date, command_name, guild_id, user_hash, is_slash)"
                ),
            },
            {
                "table": "command_latency_daily",
                "column_ddl": "ALTER TABLE command_latency_daily ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
                "index_ddl": "CREATE INDEX IF NOT EXISTS ix_command_latency_daily_user_hash ON command_latency_daily(user_hash)",
                "drop_uq": "ALTER TABLE command_latency_daily DROP CONSTRAINT IF EXISTS uq_command_latency_daily",
                "create_uq": (
                    "ALTER TABLE command_latency_daily ADD CONSTRAINT uq_command_latency_daily "
                    "UNIQUE (bucket_date, command_name, guild_id, user_hash, is_slash)"
                ),
            },
            {
                "table": "command_error_daily",
                "column_ddl": "ALTER TABLE command_error_daily ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
                "index_ddl": "CREATE INDEX IF NOT EXISTS ix_command_error_daily_user_hash ON command_error_daily(user_hash)",
                "drop_uq": "ALTER TABLE command_error_daily DROP CONSTRAINT IF EXISTS uq_command_error_daily",
                "create_uq": (
                    "ALTER TABLE command_error_daily ADD CONSTRAINT uq_command_error_daily "
                    "UNIQUE (bucket_date, command_name, guild_id, user_hash, is_slash, error_type)"
                ),
            },
            {
                "table": "daily_user_exposure",
                "column_ddl": "ALTER TABLE daily_user_exposure ADD COLUMN IF NOT EXISTS user_hash VARCHAR(64)",
                "index_ddl": "CREATE INDEX IF NOT EXISTS ix_daily_user_exposure_user_hash ON daily_user_exposure(user_hash)",
                "drop_uq": "ALTER TABLE daily_user_exposure DROP CONSTRAINT IF EXISTS uq_daily_user_exposure",
                "create_uq": (
                    "ALTER TABLE daily_user_exposure ADD CONSTRAINT uq_daily_user_exposure "
                    "UNIQUE (bucket_date, guild_id, user_hash)"
                ),
            },
        ]

        async with self.engine.begin() as conn:
            for repair in repairs:
                table = repair["table"]
                try:
                    await conn.execute(text(repair["column_ddl"]))
                    await conn.execute(text(repair["index_ddl"]))
                    await conn.execute(text(repair["drop_uq"]))
                    await conn.execute(text(repair["create_uq"]))
                    logger.info(f"Repaired schema for table: {table}")
                except SQLAlchemyError as e:
                    logger.warning(f"Schema repair for {table} failed (may be expected): {e}")

    async def _repair_command_cooldowns_constraint(self):
        """
        Ensure command_cooldowns has a unique index on (user_id, command_name).
        Older code updated-then-inserted without a constraint, so duplicate rows
        are possible. Keep the latest expiry per user/command and add a unique
        index so set_cooldown() can use an atomic upsert via ON CONFLICT.
        """
        async with self.engine.begin() as conn:
            try:
                # 1. Keep only the latest expiry per user/command.
                await conn.execute(
                    text(
                        """
                        DELETE FROM command_cooldowns a
                        USING command_cooldowns b
                        WHERE a.id < b.id
                          AND a.user_id = b.user_id
                          AND a.command_name = b.command_name
                        """
                    )
                )

                # 2. Create a unique index. PostgreSQL supports ON CONFLICT on a
                #    unique index even when no named constraint exists.
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "ix_command_cooldowns_user_command "
                        "ON command_cooldowns (user_id, command_name)"
                    )
                )

                # 3. Also create the named constraint for completeness.
                await conn.execute(
                    text(
                        "ALTER TABLE command_cooldowns "
                        "DROP CONSTRAINT IF EXISTS uq_command_cooldowns_user_command"
                    )
                )
                await conn.execute(
                    text(
                        "ALTER TABLE command_cooldowns "
                        "ADD CONSTRAINT uq_command_cooldowns_user_command "
                        "UNIQUE (user_id, command_name)"
                    )
                )
                logger.info("Repaired command_cooldowns unique constraint/index")
            except SQLAlchemyError as e:
                logger.warning(f"command_cooldowns constraint repair failed: {e}")

    def get_session(self):
        """Provide a transactional scope around a series of operations."""
        return self.async_sessionmaker()

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def health_check(self) -> bool:
        """Return True if the database is reachable right now."""
        try:
            async with self.async_sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.warning(f"Database health check failed: {exc}")
            return False
