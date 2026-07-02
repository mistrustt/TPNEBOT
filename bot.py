import logging.handlers
import os, random
import time
import discord
import logging
import platform
import asyncio
import inspect
import traceback
import urllib.parse
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import Context
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pathlib import Path
from utils.cooldown import CooldownUtils
from utils.infisical import InfisicalSecretsManager
from database.manager import DatabaseManager
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from utils.cache import Cache
from utils.stats import hash_user_id

_DB_ERROR_TYPES: tuple[type[Exception], ...] = (SQLAlchemyError,)
try:
    import asyncpg.exceptions as _apg_exc

    _DB_ERROR_TYPES += (_apg_exc.PostgresError,)
except Exception:
    pass


def _root_cause_is_db_error(error) -> bool:
    """Return True if *error* or its wrapped original is a DB connection failure."""
    if isinstance(error, _DB_ERROR_TYPES):
        return True
    original = getattr(error, "original", None)
    if isinstance(original, _DB_ERROR_TYPES):
        return True
    if original is not None and DatabaseManager._is_retryable_db_error(original):
        return True
    return False

class LoggingFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\x1b[38;1m",
        logging.INFO: "\x1b[34;1m",
        logging.WARNING: "\x1b[33;1m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[31;1m",
    }
    FORMAT = "{asctime} {levelname:<8} {name}: {message}"
    DATE_FORMAT = "%Y-%m-%d %I:%M:%S %p"

    def format(self, record):
        if hasattr(self, "_style"):
            self._style._fmt = self.FORMAT

        record.levelname = (
            f"\x1b[30;1m{self.COLORS[record.levelno]}{record.levelname}\x1b[0m"
        )
        record.name = f"\x1b[32;1m{record.name}\x1b[0m"
        return super().format(record)


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    console_formatter = LoggingFormatter(style="{")
    file_formatter = logging.Formatter(
        LoggingFormatter.FORMAT, LoggingFormatter.DATE_FORMAT, style="{"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        "data/discord.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8", mode="a"
    )
    file_handler.setFormatter(file_formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


setup_logging()
logger = logging.getLogger("discord_bot")


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        self.logger = logger
        # Database connection. Defaults preserve the original `localhost` / `postgres` /
        # `postgres` behavior for bare-metal runs; Docker compose sets these to reach the
        # `db` service. Password is URL-quoted so special characters don't break the DSN.
        self.db_user = os.getenv("DB_USER", "postgres")
        self.db_name = os.getenv("DB_NAME", "postgres")
        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_port = os.getenv("DB_PORT", "5432")
        self.db_pw = urllib.parse.quote_plus(os.getenv("DB_PW", ""))
        self.database = DatabaseManager(
            f"postgresql+asyncpg://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"
        )
        self.config = self.database.load_config()
        self.debug_mode_active = False
        self.cool_guys = [
            284439598422163476,
            782529966666678283,
            975220499233788006,
            1160736856384753694,
            597490815299878922,
            404096862857986048,
            1166140569861496853,
            736148885055078431,
            538773310704582666,
            657182369240973312,
            1173579369399210120,
            1099696209637167145,  # toxic
            1382196396190470215,  # FLOW (GOATED ASF)
            1282494458339922033,  # jwa
        ]
        self.version = "2026.06.30"
        # Discord privileged intents we require and why:
        # - message_content: spam-channel enforcement, automated moderation
        #   (PII/card/token detection), message delete/edit logging, attachment
        #   filtering, and interactive wait-for command prompts.
        # - members: server statistics, member lookup by name, role management,
        #   on_member_join handling (autorole/jail reapply), and voice-channel
        #   ownership tracking.
        # - presences: online/idle/dnd/offline breakdown in server stats and
        #   Spotify activity lookup for music features.
        intents = discord.Intents.default()
        intents.members = True
        intents.presences = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned_or(self.get_prefix),
            intents=intents,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False),
        )

    async def get_prefix(self, message: discord.Message) -> str:
        if not message.guild:
            return "!"
        return await self.database.get_prefix(message.guild.id) or "!"

    async def load_cogs(self) -> None:
        cogs_path = Path(__file__).parent / "cogs"
        config = await self.config
        loaded_cogs = config.loaded_cogs if config else []
        unloaded_cogs = config.unloaded_cogs if config else []
        for file in cogs_path.glob("*.py"):
            extension = f"cogs.{file.stem}"

            if loaded_cogs and extension not in loaded_cogs:
                self.logger.info(f"Skipping extension '{extension}' not in loaded_cogs")
                continue
            try:
                await self.load_extension(extension)
                self.logger.info(f"Loaded extension '{extension}'")
            except Exception as e:
                self.logger.error(
                    f"Failed to load extension {extension}\n{type(e).__name__}: {e}"
                )

    @tasks.loop(hours=1)
    async def cache_songs(self):
        await self.wait_until_ready()
        await Cache.fetch_songs()

    @tasks.loop(minutes=0.25)
    async def status_task(self) -> None:
        await self.wait_until_ready()
        statuses = [
            f".gg/TPNE",
            f"over {len(set(self.get_all_members())):,} members",
            f"{len(self.guilds):,} servers",
            f"for {len(self.commands):,} commands",
        ]
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=random.choice(statuses)
            )
        )

    @tasks.loop(hours=24)
    async def stats_retention_task(self) -> None:
        await self.wait_until_ready()
        cutoff = discord.utils.utcnow() - timedelta(days=365)
        await self.database.purge_stats_before(cutoff.date())

    @tasks.loop(hours=24)
    async def economic_metrics_collection_task(self) -> None:
        await self.wait_until_ready()
        try:
            await self.database.collect_daily_economy_snapshot()
            self.logger.info("Economic metrics collected successfully")
        except Exception as e:
            self.logger.error(f"Error collecting economic metrics: {e}")

    @tasks.loop(hours=4)
    async def economic_rebalance_task(self) -> None:
        await self.wait_until_ready()
        try:
            result = await self.database.perform_economic_rebalance()
            action = result.get("action", "unknown")
            if action in ("mint", "burn"):
                self.logger.info(
                    f"[REBALANCE] {action.upper()} {result.get('amount', 0)} — "
                    f"health {result.get('health_before', '?')} → {result.get('health_after', '?')} "
                    f"(target {result.get('target', '?')})"
                )
            elif action == "error":
                self.logger.error(f"[REBALANCE] Error: {result.get('reason', 'unknown')}")
            else:
                self.logger.debug(f"[REBALANCE] Skipped: {result.get('reason', 'no action needed')}")
        except Exception as e:
            self.logger.error(f"Error in economic rebalance task: {e}")

    def is_coolguy(self, user_id: int):
        return user_id in self.cool_guys

    @staticmethod
    def _is_transient_discord_api_error(error: Exception) -> bool:
        """Return True if *error* is a Discord server-side API failure (5xx)."""
        if isinstance(error, discord.errors.DiscordServerError):
            return True
        if isinstance(error, discord.HTTPException) and getattr(error, "status", 0) >= 500:
            return True
        return False

    async def setup_hook(self) -> None:
        try:
            self.logger.info(f"Logged in as {self.user.name}")
            self.logger.info(f"discord.py API version: {discord.__version__}")
            self.logger.info(f"Python version: {platform.python_version()}")
            self.logger.info(
                f"Running on: {platform.system()} {platform.release()} ({os.name})"
            )
            self.logger.info("-------------------")

            self.logger.info("Checking database connection...")
            db_init_time = discord.utils.utcnow()
            async with self.database.async_sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            db_connected_time = discord.utils.utcnow() - db_init_time
            self.logger.info(
                f"Database responded in {db_connected_time.total_seconds()}s."
            )
            self.logger.info("Database connection established successfully.")

            self.logger.info("Initializing database tables...")
            await self.database.initialize()
            self.logger.info("Database Tables initialized successfully.")

            # Collect initial economic metrics
            try:
                await self.database.collect_daily_economy_snapshot()
                self.logger.info("Initial economic metrics collected successfully")
            except Exception as e:
                self.logger.error(f"Error collecting initial economic metrics: {e}")

            self.logger.info("Loading cogs...")
            await self.load_cogs()

            await self.tree.sync()
            self.logger.info(
                "Commands have been synced successfully to the global command tree."
            )

            self.status_task.start()
            self.cache_songs.start()
            self.stats_retention_task.start()
            self.economic_metrics_collection_task.start()
            self.economic_rebalance_task.start()
            self.logger.info("Status task started successfully.")
            self.logger.info("-------------------")
            self.logger.info(f"Bot is ready. Awaiting gateway connection...")

        except Exception as e:
            self.logger.error(f"An error occurred during setup: {e}")
            raise

    async def invoke(self, ctx: Context) -> None:
        try:
            if ctx.command is None:
                return

            if self.debug_mode_active and not self.is_coolguy(ctx.author.id):
                embed = discord.Embed(
                    description="The bot is currently in maintenance mode. Please try again later.",
                    color=discord.Color.red(),
                )
                return await ctx.reply(embed=embed, delete_after=5)

            is_blacklisted = await self.database.is_user_blacklisted(ctx.author.id)
            if is_blacklisted and ctx.author.id not in self.owner_ids:
                embed = discord.Embed(
                    description="You are blacklisted from using this bot.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            # Block DM commands (owners exempt)
            if ctx.guild is None and ctx.author.id not in self.owner_ids:
                embed = discord.Embed(
                    description="Commands can only be used in a server.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            # Block commands for accounts newer than 30 days
            account_age_threshold = timedelta(days=30)
            account_age = datetime.now(timezone.utc) - ctx.author.created_at
            if account_age < account_age_threshold and ctx.author.id not in self.owner_ids:
                days_remaining = 30 - account_age.days
                embed = discord.Embed(
                    description=f"Your account must be at least 30 days old to use commands. "
                                f"Please wait {days_remaining} more day{'s' if days_remaining != 1 else ''}.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=10)
                return

            user_id = ctx.author.id
            command_name = ctx.command.qualified_name
            channel_id = ctx.channel.id

            command_enabled = await self.database.get_command_status(
                command_name, channel_id
            )
            if command_enabled is False:
                embed = discord.Embed(
                    title="Error!",
                    description=f"The `{command_name}` command is disabled in this channel by staff.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, delete_after=5)
                return

            command_enabled_global = await self.database.get_command_status(
                command_name
            )
            if command_enabled_global is False:
                embed = discord.Embed(
                    title="Error!",
                    description=f"The `{command_name}` command is currently disabled for maintenance.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed, delete_after=5)
                return

            remaining_cooldown = await self.database.get_cooldown(user_id, command_name)
            if remaining_cooldown > 0:
                embed = await CooldownUtils.get_cooldown_embed(remaining_cooldown)
                await ctx.send(embed=embed, delete_after=5)
                return

            if ctx.guild:
                command_names_to_check = [ctx.command.name.lower()]
                if hasattr(ctx.command, "aliases") and ctx.command.aliases:
                    command_names_to_check.extend(
                        [alias.lower() for alias in ctx.command.aliases]
                    )

                has_permission = True
                for cmd_name in command_names_to_check:
                    permission_result = (
                        await self.database.check_command_role_restriction(
                            ctx.guild.id, cmd_name, ctx.author.roles
                        )
                    )
                    if not permission_result:
                        has_permission = False
                        break

                if not has_permission:
                    embed = discord.Embed(
                        title="",
                        description=f"{ctx.author.mention}: You don't have the required role to use `{command_name}`",
                        color=discord.Color.red(),
                    )
                    await ctx.send(embed=embed, delete_after=5)
                    return

            ctx._stats_started_at = time.perf_counter()
            await super().invoke(ctx)
        except commands.CommandInvokeError as exc:
            if _root_cause_is_db_error(exc):
                self.logger.warning(
                    f"Database error while invoking {ctx.command.qualified_name}: "
                    f"{type(exc.original).__name__}: {exc.original}"
                )
                embed = discord.Embed(
                    title="⚠️ Database Temporarily Unavailable",
                    description=(
                        "The database connection dropped. Your command was not processed. "
                        "Please try again in a moment."
                    ),
                    color=discord.Color.orange(),
                )
                return await ctx.reply(embed=embed, delete_after=10)
            if self._is_transient_discord_api_error(exc.original):
                self.logger.warning(
                    f"Discord API error while invoking {ctx.command.qualified_name}: "
                    f"{type(exc.original).__name__}: {exc.original}"
                )
                return
            raise
        except SQLAlchemyError as exc:
            self.logger.warning(
                f"Database error during command pre-checks: {type(exc).__name__}: {exc}"
            )
            embed = discord.Embed(
                title="⚠️ Database Temporarily Unavailable",
                description=(
                    "The database connection dropped. Your command was not processed. "
                    "Please try again in a moment."
                ),
                color=discord.Color.orange(),
            )
            return await ctx.reply(embed=embed, delete_after=10)
        except (discord.errors.DiscordServerError, discord.HTTPException):
            return

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type == discord.InteractionType.application_command:
            interaction._stats_started_at = time.perf_counter()
            # Ensure the mapping table has this user so hashes can be resolved later.
            try:
                await self.database.ensure_user_identity(interaction.user.id)
            except Exception:
                pass

        base = super()
        if hasattr(base, "on_interaction"):
            await base.on_interaction(interaction)
            return

        if interaction.type != discord.InteractionType.application_command:
            return

        if hasattr(self, "process_application_commands"):
            await self.process_application_commands(interaction)
            return

        tree = getattr(self, "tree", None)
        if tree is not None:
            if hasattr(tree, "process_interaction"):
                result = tree.process_interaction(interaction)
                if inspect.isawaitable(result):
                    await result
                return
            if hasattr(tree, "_from_interaction"):
                result = tree._from_interaction(interaction)
                if inspect.isawaitable(result):
                    await result
                return

    async def process_commands(self, message: discord.Message) -> None:
        """Ensure the user identity is recorded before running prefix commands."""
        if not message.author.bot:
            try:
                await self.database.ensure_user_identity(message.author.id)
            except Exception:
                pass
        await super().process_commands(message)

    async def on_command_completion(self, ctx: Context) -> None:
        command_name = ctx.command.qualified_name
        user = ctx.author
        channel = ctx.channel
        guild = ctx.guild

        try:
            used_at = discord.utils.utcnow()
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            user_hash = hash_user_id(user.id)
            guild_id = guild.id if guild else None
            latency_ms = None
            started_at = getattr(ctx, "_stats_started_at", None)
            if started_at is not None:
                latency_ms = int((time.perf_counter() - started_at) * 1000)

            await self.database.record_command_usage(
                command_name=command_name,
                guild_id=guild_id,
                user_hash=user_hash,
                is_slash=False,
                used_at=used_at,
            )
            await self.database.record_user_exposure(
                guild_id=guild_id,
                user_hash=user_hash,
                seen_at=used_at,
            )
            if latency_ms is not None:
                await self.database.record_command_latency(
                    command_name=command_name,
                    guild_id=guild_id,
                    is_slash=False,
                    latency_ms=latency_ms,
                    used_at=used_at,
                )
        except Exception as e:
            self.logger.warning(f"Stats tracking failed: {e}")

        self.logger.info(
            f"Command '{command_name}' executed by {user} (ID: {user.id}) "
            f"in channel '{channel}' (ID: {channel.id}) "
            f"{'in guild ' + guild.name + ' (ID: ' + str(guild.id) + ')' if guild else 'in DMs'}."
        )

    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command
    ):
        try:
            used_at = discord.utils.utcnow()
            if used_at.tzinfo is None:
                used_at = used_at.replace(tzinfo=timezone.utc)
            user_hash = hash_user_id(interaction.user.id)
            guild_id = interaction.guild.id if interaction.guild else None
            latency_ms = None
            started_at = getattr(interaction, "_stats_started_at", None)
            if started_at is not None:
                latency_ms = int((time.perf_counter() - started_at) * 1000)

            await self.database.record_command_usage(
                command_name=command.qualified_name,
                guild_id=guild_id,
                user_hash=user_hash,
                is_slash=True,
                used_at=used_at,
            )
            await self.database.record_user_exposure(
                guild_id=guild_id,
                user_hash=user_hash,
                seen_at=used_at,
            )
            if latency_ms is not None:
                await self.database.record_command_latency(
                    command_name=command.qualified_name,
                    guild_id=guild_id,
                    is_slash=True,
                    latency_ms=latency_ms,
                    used_at=used_at,
                )
        except Exception as e:
            self.logger.warning(f"Stats tracking failed: {e}")

        self.logger.info(
            f"Slash /{command.name} used by {interaction.user} "
            f"(ID:{interaction.user.id}) in #{interaction.channel} "
            f"(ID:{interaction.channel_id})"
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error
    ) -> None:
        try:
            if not isinstance(error, app_commands.CommandOnCooldown):
                command = getattr(interaction, "command", None)
                command_name = command.qualified_name if command else "unknown"
                used_at = discord.utils.utcnow()
                if used_at.tzinfo is None:
                    used_at = used_at.replace(tzinfo=timezone.utc)
                guild_id = interaction.guild.id if interaction.guild else None
                await self.database.record_command_error(
                    command_name=command_name,
                    guild_id=guild_id,
                    is_slash=True,
                    error_type=type(error).__name__,
                    used_at=used_at,
                )
                started_at = getattr(interaction, "_stats_started_at", None)
                if started_at is not None:
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    await self.database.record_command_latency(
                        command_name=command_name,
                        guild_id=guild_id,
                        is_slash=True,
                        latency_ms=latency_ms,
                        used_at=used_at,
                    )
        except Exception as e:
            self.logger.warning(f"Stats tracking failed: {e}")

        command = getattr(interaction, "command", None)
        command_name = getattr(command, "qualified_name", "unknown")

        if _root_cause_is_db_error(error):
            original = getattr(error, "original", error)
            self.logger.warning(
                f"Database error in slash /{command_name}: "
                f"{type(original).__name__}: {original}"
            )
            message = (
                "⚠️ Database Temporarily Unavailable\n\n"
                "The database connection dropped. Your command was not processed. Please try again in a moment."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await interaction.followup.send(message, ephemeral=True)
            return
        if self._is_transient_discord_api_error(error):
            self.logger.warning(
                f"Discord API error in slash /{command_name}: "
                f"{type(error).__name__}: {error}"
            )
            return
        if (
            isinstance(error, app_commands.CommandInvokeError)
            and self._is_transient_discord_api_error(error.original)
        ):
            original = error.original
            self.logger.warning(
                f"Discord API error in slash /{command_name}: "
                f"{type(original).__name__}: {original}"
            )
            return

        if isinstance(error, app_commands.CommandOnCooldown):
            retry = error.retry_after
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"⏳ Try again in {retry:.1f}s.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"⏳ Try again in {retry:.1f}s.", ephemeral=True
                )
        else:
            # fallback for any other errors
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Something went wrong.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Something went wrong.", ephemeral=True
                )
            # and log it
            self.logger.exception(error)

    async def on_command_error(self, ctx: Context, error) -> None:
        try:
            if ctx.command and not isinstance(
                error, (commands.CommandNotFound, commands.CommandOnCooldown)
            ):
                used_at = discord.utils.utcnow()
                if used_at.tzinfo is None:
                    used_at = used_at.replace(tzinfo=timezone.utc)
                guild_id = ctx.guild.id if ctx.guild else None
                await self.database.record_command_error(
                    command_name=ctx.command.qualified_name,
                    guild_id=guild_id,
                    is_slash=False,
                    error_type=type(error).__name__,
                    used_at=used_at,
                )
                started_at = getattr(ctx, "_stats_started_at", None)
                if started_at is not None:
                    latency_ms = int((time.perf_counter() - started_at) * 1000)
                    await self.database.record_command_latency(
                        command_name=ctx.command.qualified_name,
                        guild_id=guild_id,
                        is_slash=False,
                        latency_ms=latency_ms,
                        used_at=used_at,
                    )
        except Exception as e:
            self.logger.warning(f"Stats tracking failed: {e}")

        if isinstance(error, commands.CommandOnCooldown):
            embed = await CooldownUtils.get_cooldown_embed(error.retry_after)
            await ctx.reply(embed=embed, delete_after=error.retry_after)
        elif isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.errors.UnexpectedQuoteError):
            return
        elif isinstance(error, commands.NoPrivateMessage):
            embed = discord.Embed(
                description="This command cannot be used in DMs!",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="You are missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to execute this command!",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                description="I am missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to fully perform this command!",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.MemberNotFound):
            embed = discord.Embed(
                title="Error!",
                description=f"**Member not found:** {error}",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.MissingRequiredArgument):
            prefix = await self.database.get_prefix(ctx.guild.id)
            usage = f"`{prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
            embed = discord.Embed(
                title="Error!",
                description=f"**Missing argument:** {error.param.name}\n**Usage:** {usage}",
                color=discord.Color.red(),
            )
            ctx.command.reset_cooldown(ctx)
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                title="Error!",
                description=f"**Bad argument:** {error}",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5)
        elif isinstance(error, commands.NotOwner):
            if ctx.guild:
                self.logger.warning(
                    f"{ctx.author} (ID: {ctx.author.id}) tried to execute an owner command in {ctx.guild.name} (ID: {ctx.guild.id})."
                )
            else:
                self.logger.warning(
                    f"{ctx.author} (ID: {ctx.author.id}) tried to execute an owner command in DMs."
                )
            return
        elif isinstance(error, commands.CheckFailure):
            pass
        elif isinstance(error, commands.CheckAnyFailure):
            pass
        elif _root_cause_is_db_error(error):
            original = getattr(error, "original", error)
            self.logger.warning(
                f"Database error in command {ctx.command.qualified_name}: "
                f"{type(original).__name__}: {original}"
            )
            embed = discord.Embed(
                title="⚠️ Database Temporarily Unavailable",
                description=(
                    "The database connection dropped. Your command was not processed. "
                    "Please try again in a moment."
                ),
                color=discord.Color.orange(),
            )
            return await ctx.reply(embed=embed, delete_after=10)
        elif self._is_transient_discord_api_error(error):
            self.logger.warning(
                f"Discord API error in command {ctx.command.qualified_name}: "
                f"{type(error).__name__}: {error}"
            )
            return
        elif (
            isinstance(error, commands.CommandInvokeError)
            and self._is_transient_discord_api_error(error.original)
        ):
            original = error.original
            self.logger.warning(
                f"Discord API error in command {ctx.command.qualified_name}: "
                f"{type(original).__name__}: {original}"
            )
            return
        elif isinstance(error, Exception):
            dev_channel_id = int(os.getenv("DEVELOPER_CHANNEL_ID"))
            dev_channel = self.get_channel(dev_channel_id)
            detailed_error = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )

            prefix = await self.database.get_prefix(ctx.guild.id) if ctx.guild else "!"
            invoked_with = ctx.invoked_with or ctx.command.name
            command_display = f"`{prefix}{invoked_with}`"
            error_type = type(error).__name__
            error_message = str(error) or "No message provided."

            if dev_channel:
                base_embed = discord.Embed(
                    title="⚠️ Unhandled Exception",
                    color=discord.Color.from_rgb(237, 66, 69),
                    timestamp=discord.utils.utcnow(),
                )
                base_embed.set_thumbnail(url=ctx.author.display_avatar.url)
                base_embed.add_field(
                    name="🛠️ Command",
                    value=command_display,
                    inline=True,
                )
                base_embed.add_field(
                    name="❌ Error Type",
                    value=f"`{error_type}`",
                    inline=True,
                )
                base_embed.add_field(
                    name="👤 User",
                    value=f"{ctx.author.mention}\n`{ctx.author.id}`",
                    inline=True,
                )
                base_embed.add_field(
                    name="📍 Channel",
                    value=f"{ctx.channel.mention}\n`{ctx.channel.id}`",
                    inline=True,
                )
                base_embed.add_field(
                    name="🏠 Guild",
                    value=f"{ctx.guild.name}\n`{ctx.guild.id}`"
                    if ctx.guild
                    else "Direct Message",
                    inline=True,
                )
                base_embed.add_field(
                    name="📝 Reason",
                    value=f"```{error_message[:1000]}```"
                    if len(error_message) <= 1000
                    else f"```{error_message[:997]}...```",
                    inline=False,
                )
                if ctx.args or ctx.kwargs:
                    args_str = " ".join(repr(a) for a in ctx.args[2:])  # skip self, ctx
                    kwargs_str = " ".join(f"{k}={v!r}" for k, v in ctx.kwargs.items())
                    invocation = " ".join(filter(None, [args_str, kwargs_str]))
                    base_embed.add_field(
                        name="📨 Arguments",
                        value=f"```{invocation[:1000]}```" or "```None```",
                        inline=False,
                    )
                base_embed.set_footer(
                    text=f"v{self.version} • {ctx.command.qualified_name}",
                    icon_url=self.user.display_avatar.url if self.user else None,
                )

                traceback_prefix = f"```py\n{error_type}: {error_message}\n"
                if len(detailed_error) <= (4000 - len(traceback_prefix) - 3):
                    base_embed.description = (
                        f"**Full traceback for** {command_display}:\n"
                        f"{traceback_prefix}{detailed_error}```"
                    )
                    await dev_channel.send(embed=base_embed)
                else:
                    base_embed.description = (
                        f"**Traceback exceeds Discord limits.** Summary above; full traceback follows in separate messages."
                    )
                    await dev_channel.send(embed=base_embed)

                    chunks = [
                        detailed_error[i : i + 3900]
                        for i in range(0, len(detailed_error), 3900)
                    ]
                    for i, chunk in enumerate(chunks):
                        part_embed = discord.Embed(
                            title=f"📄 Traceback ({i + 1}/{len(chunks)})",
                            description=f"```py\n{chunk}```",
                            color=discord.Color.from_rgb(237, 66, 69),
                            timestamp=discord.utils.utcnow(),
                        )
                        part_embed.set_footer(
                            text=f"{ctx.command.qualified_name} • {error_type}"
                        )
                        await dev_channel.send(embed=part_embed)
            else:
                if isinstance(error, commands.errors.CommandInvokeError) and ctx.author.id in self.cool_guys:
                    tb = error.original.__traceback__
                    stack_summary = traceback.extract_tb(tb)
                    last_call = stack_summary[-1]

                    file_name = last_call.filename[last_call.filename.rfind(os.sep)+1:]
                    line_num = last_call.lineno
                    last_line = last_call.line.strip() if last_call.line else "Unknown"
                    error = error.original

                    embed = discord.Embed(
                        title="💥 Command Error",
                        description=(
                            f"```py\n"
                            f"File: {file_name}\n"
                            f"Line: {line_num}\n"
                            f"{last_line}\n"
                            f"{type(error).__name__}: {error}"
                            f"```"
                        ),
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_footer(text=f"v{self.version}")
                    await ctx.reply(embed=embed, delete_after=10)

                self.logger.error(
                    "Developer channel not found. Full error:\n" + detailed_error
                )
                user_embed = discord.Embed(
                    title="Error!",
                    description="An unexpected error occurred. Please try again later.",
                    color=discord.Color.red(),
                )
                user_embed.set_footer(text="The developer has been notified.")
                await ctx.send(embed=user_embed, delete_after=10)
                ctx.command.reset_cooldown(ctx)
                raise error
        else:
            return

    async def close(self) -> None:
        await super().close()


async def main() -> None:
    """Load non-sensitive config from .env and all secrets from Infisical."""
    load_dotenv()

    infisical = InfisicalSecretsManager.from_env()
    if not infisical.is_configured:
        raise RuntimeError(
            "Infisical is not configured. Set INFISICAL_CLIENT_ID and "
            "INFISICAL_CLIENT_SECRET in .env. All secrets must be retrieved from Infisical."
        )

    logger.info("Infisical is configured; fetching secrets from Infisical")
    await infisical.authenticate()
    await infisical.load_secrets_into_environ(
        {
            # Core bot / database
            "TOKEN": "TOKEN",
            "DB_PW": "DB_PW",
            # Security / hashing
            "USER_ID_HASH_KEY": "USER_ID_HASH_KEY",
            "STATS_SALT": "STATS_SALT",
            "LOCATION_ENCRYPTION_KEY": "LOCATION_ENCRYPTION_KEY",
            # Third-party API keys
            "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
            "API_NINJAS_KEY": "API_NINJAS_KEY",
            "COINMARKETCAP_API_KEY": "COINMARKETCAP_API_KEY",
            "NASA_API_KEY": "NASA_API_KEY",
            "WEATHER_API_KEY": "WEATHER_API_KEY",
            "LASTFM_API_KEY": "LASTFM_API_KEY",
            "GENIUS_API_KEY": "GENIUS_API_KEY",
            "FREECRYPTOAPI_API_KEY": "FREECRYPTOAPI_API_KEY",
        }
    )
    asyncio.create_task(infisical.refresh_loop())

    bot = DiscordBot()
    await bot.start(os.environ["TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
