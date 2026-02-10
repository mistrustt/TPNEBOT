import logging.handlers
import os, random
import time
import discord
import logging
import platform
import inspect
import traceback
import urllib.parse
import uuid
from discord import app_commands, Webhook
from discord.ext import commands, tasks
from discord.ext.commands import Context
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path
from utils.cooldown import CooldownUtils
from utils.admin_api import AdminAPIServer
from database.manager import DatabaseManager
from sqlalchemy import text
from utils.cache import Cache
from utils.stats import hash_user_id

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
        "discord.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8", mode="a"
    )
    file_handler.setFormatter(file_formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


setup_logging()
logger = logging.getLogger("discord_bot")


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        self.logger = logger
        self.db_pw = urllib.parse.quote_plus(os.getenv("DB_PW"))
        self.database = DatabaseManager(
            f"postgresql+asyncpg://postgres:{self.db_pw}@localhost/postgres"
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
            1219090700407279656,  # toxic
            1095747082599530627,  # ENVY
            1382196396190470215,  # FLOW (GOATED ASF)
            1282494458339922033,  # jwa
        ]
        self.version = "20251024a"
        self.admin_api_server = None
        self.admin_api_secret = None
        super().__init__(
            command_prefix=commands.when_mentioned_or(self.get_prefix),
            intents=discord.Intents.all(),
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False),
        )

    async def get_prefix(self, message: discord.Message) -> str:
        if not message.guild:
            return "!"
        return await self.database.get_prefix(message.guild.id) or "!"

    async def load_cogs(self) -> None:
        await self.load_extension("jishaku")
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
            f".gg/WRLD",
            f"{len(set(self.get_all_members())):,} members",
            f"{len(self.guilds):,} servers",
            "for commands",
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

    def is_coolguy(self, user_id: int):
        return user_id in self.cool_guys

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
            db_init_time = datetime.now()
            async with self.database.async_sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            db_connected_time = datetime.now() - db_init_time
            self.logger.info(
                f"Database responded in {db_connected_time.total_seconds()}s."
            )
            self.logger.info("Database connection established successfully.")

            self.logger.info("Initializing database tables & blockchain...")
            await self.database.initialize()
            self.logger.info("Blockchain initialized successfully.")
            self.logger.info("Database Tables initialized successfully.")

            self.logger.info("Loading cogs...")
            await self.load_cogs()

            await self.tree.sync()
            self.logger.info(
                "Commands have been synced successfully to the global command tree."
            )

            self.status_task.start()
            self.cache_songs.start()
            self.stats_retention_task.start()
            self.logger.info("Status task started successfully.")
            self.logger.info("-------------------")
            self.logger.info(f"Bot is ready. Awaiting gateway connection...")

            # Start Admin API server if configured via environment variables
            try:
                host = os.getenv("ADMIN_API_HOST", "127.0.0.1")
                port = int(os.getenv("ADMIN_API_PORT", "8080"))
                secret = os.getenv("ADMIN_API_SECRET")
                if not secret:
                    self.logger.warning("ADMIN_API_SECRET not set; Admin API will not be started.")
                else:
                    api_server = AdminAPIServer(self, host=host, port=port, secret=secret)
                    await api_server.start()
                    self.admin_api_server = api_server
                    self.admin_api_secret = secret
                    self.logger.info(f"Admin API running on port {port}")
            except Exception as e:
                self.logger.error(f"Failed to start Admin API: {e}")

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
            if is_blacklisted:
                embed = discord.Embed(
                    description="You are blacklisted from using this bot.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
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
        except discord.errors.DiscordServerError:
            return
        except discord.HTTPException:
            return

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type == discord.InteractionType.application_command:
            interaction._stats_started_at = time.perf_counter()

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

    async def on_command_completion(self, ctx: Context) -> None:
        command_name = ctx.command.qualified_name
        user = ctx.author
        channel = ctx.channel
        guild = ctx.guild

        try:
            used_at = discord.utils.utcnow()
            if used_at.tzinfo is not None:
                used_at = used_at.replace(tzinfo=None)
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
            if used_at.tzinfo is not None:
                used_at = used_at.replace(tzinfo=None)
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
                if used_at.tzinfo is not None:
                    used_at = used_at.replace(tzinfo=None)
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
                if used_at.tzinfo is not None:
                    used_at = used_at.replace(tzinfo=None)
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
        elif isinstance(error, commands.errors.CommandInvokeError):
            if ctx.author.id in self.cool_guys:
                tb = error.original.__traceback__
                stack_summary = traceback.extract_tb(tb)
                last_call = stack_summary[-1]

                file_name = last_call.filename[last_call.filename.rfind(os.sep)+1:]
                line_num = last_call.lineno
                last_line = last_call.line.strip() if last_call.line else "Unknown"
                error = error.original

                embed = discord.Embed(
                    title="An error occurred while executing the command!",
                    description=f"`File \"{file_name}\":{line_num}\n{last_line}\n{type(error).__name__}: {error}`",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=10)
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
        elif isinstance(error, Exception):
            dev_channel_id = int(os.getenv("DEVELOPER_CHANNEL_ID"))
            dev_channel = self.get_channel(dev_channel_id)
            detailed_error = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
            if dev_channel:
                if len(detailed_error) <= 4000:
                    dev_embed = discord.Embed(
                        title="Unhandled Error",
                        description=f"Error in command `{ctx.command.qualified_name}`:\n```{detailed_error}```",
                        color=discord.Color.dark_red(),
                    )
                    dev_embed.add_field(
                        name="Command", value=f"`{ctx.command.qualified_name}`"
                    )
                    dev_embed.add_field(
                        name="User", value=f"{ctx.author} (ID: {ctx.author.id})"
                    )
                    dev_embed.add_field(
                        name="Channel", value=f"{ctx.channel} (ID: {ctx.channel.id})"
                    )
                    dev_embed.add_field(
                        name="Guild",
                        value=f"{ctx.guild.name} (ID: {ctx.guild.id})"
                        if ctx.guild
                        else "DM",
                    )
                    # dev_embed.set_footer(text=f"Arguments: {ctx.args} | Keyword Arguments: {ctx.kwargs}")
                    await dev_channel.send(embed=dev_embed)
                else:
                    dev_embed = discord.Embed(
                        title="Unhandled Error",
                        description=f"Error in command `{ctx.command.qualified_name}`:\n",
                        color=discord.Color.dark_red(),
                    )
                    dev_embed.add_field(
                        name="Command", value=f"`{ctx.command.qualified_name}`"
                    )
                    dev_embed.add_field(
                        name="User", value=f"{ctx.author} (ID: {ctx.author.id})"
                    )
                    dev_embed.add_field(
                        name="Channel", value=f"{ctx.channel} (ID: {ctx.channel.id})"
                    )
                    dev_embed.add_field(
                        name="Guild",
                        value=f"{ctx.guild.name} (ID: {ctx.guild.id})"
                        if ctx.guild
                        else "DM",
                    )
                    # dev_embed.set_footer(text=f"Arguments: {ctx.args} | Keyword Arguments: {ctx.kwargs}")
                    await dev_channel.send(embed=dev_embed)

                    chunks = [
                        detailed_error[i : i + 3900]
                        for i in range(0, len(detailed_error), 3900)
                    ]
                    for i, chunk in enumerate(chunks):
                        part_embed = discord.Embed(
                            title=f"Error Details (Part {i+1}/{len(chunks)})",
                            description=f"```{chunk}```",
                            color=discord.Color.dark_red(),
                        )
                        await dev_channel.send(embed=part_embed)
            else:
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
        # Stop admin API server if running, then close the bot
        try:
            if getattr(self, "admin_api_server", None):
                try:
                    await self.admin_api_server.stop()
                    self.logger.info("Admin API stopped cleanly.")
                except Exception as e:
                    self.logger.error(f"Error stopping Admin API: {e}")
        finally:
            await super().close()

    load_dotenv()


bot = DiscordBot()
bot.run(os.getenv("TOKEN"))
