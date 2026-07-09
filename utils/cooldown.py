import asyncio
import functools
import inspect
import logging
import time
from collections import defaultdict
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import BucketType, Cooldown

logger = logging.getLogger("discord.client")


class CooldownUtils:
    @staticmethod
    async def get_cooldown_embed(remaining_cooldown: float) -> discord.Embed:
        """Generates an embed message for the remaining cooldown time."""

        def format_time_unit(value: int, unit: str) -> str:
            """Returns formatted time unit (e.g., '1 second', '2 minutes')."""
            if value > 0:
                return f"{value} {unit}{'s' if value > 1 else ''}"
            return ""

        minutes, seconds = divmod(remaining_cooldown, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        weeks, days = divmod(days, 7)
        months, weeks = divmod(weeks, 4)

        time_components = [
            format_time_unit(round(months), "month"),
            format_time_unit(round(weeks), "week"),
            format_time_unit(round(days), "day"),
            format_time_unit(round(hours), "hour"),
            format_time_unit(round(minutes), "minute"),
            format_time_unit(round(seconds), "second"),
        ]

        time_message = " ".join(filter(None, time_components))

        if not time_message:
            time_message = "less than a second"

        cooldown_message = (
            f"**Please slow down** - You can use this command again in: {time_message}."
        )

        embed = discord.Embed(description=cooldown_message, color=discord.Color.red())
        return embed


class UnifiedCooldownManager:
    """
    Shared cooldown backend for prefix and slash/app commands.

    Cooldowns are persisted in the database so long cooldowns (e.g. monthly)
    survive bot restarts. A per-(user, command) asyncio.Lock prevents race
    conditions where the same user could invoke a command through both its
    prefix and slash form at the same moment.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        # In-flight slash interactions per (user_id, command_name). Used to
        # distinguish a legitimate second invocation from a Discord retry that
        # fires when the first invocation has not responded within ~3 seconds.
        self._in_flight: dict[tuple[int, str], int] = {}
        self._cooldown_started_at: dict[tuple[int, str], float] = {}
        # Recently processed slash interaction IDs. Discord can (and does) send
        # the same interaction more than once if the first response is slow or
        # if commands are registered multiple times. Tracking interaction IDs
        # lets us drop duplicate invocations safely.
        self._processed_interactions: dict[int, float] = {}
        self._processed_ttl = 300.0  # 5 minutes

    def _key(self, user_id: int, command_name: str) -> tuple[int, str]:
        return (user_id, command_name)

    def _cleanup_processed_interactions(self) -> None:
        """Remove stale processed interaction IDs to prevent unbounded growth."""
        now = time.perf_counter()
        cutoff = now - self._processed_ttl
        stale = [iid for iid, ts in self._processed_interactions.items() if ts < cutoff]
        for iid in stale:
            self._processed_interactions.pop(iid, None)

    def lock(self, user_id: int, command_name: str):
        """Return the asyncio.Lock for a specific user/command pair."""
        return self._locks[self._key(user_id, command_name)]

    def set_in_flight(
        self, user_id: int, command_name: str, interaction_id: int
    ) -> None:
        """Mark a slash interaction as currently processing this command."""
        self._cleanup_processed_interactions()
        self._in_flight[self._key(user_id, command_name)] = interaction_id

    def clear_in_flight(self, user_id: int, command_name: str) -> None:
        """Clear the in-flight marker for a user/command pair."""
        self._in_flight.pop(self._key(user_id, command_name), None)

    def is_in_flight(self, user_id: int, command_name: str) -> bool:
        """Return True if a slash interaction is still processing this command."""
        return self._key(user_id, command_name) in self._in_flight

    def mark_interaction_processed(self, interaction_id: int) -> bool:
        """
        Record that a slash interaction has been processed.
        Returns True if this is a duplicate and should be ignored.
        """
        self._cleanup_processed_interactions()
        if interaction_id in self._processed_interactions:
            return True
        self._processed_interactions[interaction_id] = time.perf_counter()
        return False

    def is_interaction_processed(self, interaction_id: int) -> bool:
        """Return True if a slash interaction has already been handled."""
        self._cleanup_processed_interactions()
        return interaction_id in self._processed_interactions

    async def get_remaining(self, user_id: int, command_name: str) -> float:
        """Return the remaining cooldown in seconds for a user and command."""
        return await self.bot.database.get_cooldown(user_id, command_name)

    async def is_on_cooldown(self, user_id: int, command_name: str) -> bool:
        return await self.get_remaining(user_id, command_name) > 0

    def _resolve_command_name(
        self,
        target: Union[commands.Context, discord.Interaction],
        fallback: Optional[str] = None,
    ) -> Optional[str]:
        if isinstance(target, commands.Context):
            return fallback or (
                target.command.qualified_name if target.command else None
            )
        if isinstance(target, discord.Interaction):
            if fallback:
                return fallback
            command = target.command
            if command is None:
                return None
            return getattr(command, "qualified_name", None) or getattr(
                command, "name", None
            )
        return fallback

    async def set_cooldown(
        self,
        target: Union[commands.Context, discord.Interaction, int],
        seconds: float,
        command_name: Optional[str] = None,
    ) -> None:
        """
        Persist a cooldown for a user/command pair.

        ``target`` may be a Context, Interaction, or a raw user_id. When a
        Context/Interaction is provided the command name is inferred from the
        invoked command unless ``command_name`` is supplied explicitly.
        """
        if isinstance(target, commands.Context):
            user_id = target.author.id
            name = self._resolve_command_name(target, command_name)
        elif isinstance(target, discord.Interaction):
            user_id = target.user.id
            name = self._resolve_command_name(target, command_name)
        else:
            user_id = int(target)
            name = command_name

        if not name:
            raise ValueError("Could not determine command name for cooldown")

        await self.bot.database.set_cooldown(user_id, name, int(seconds))
        self._cooldown_started_at[self._key(user_id, name)] = time.perf_counter()

    async def get_cooldown_embed(self, remaining_cooldown: float) -> discord.Embed:
        return await CooldownUtils.get_cooldown_embed(remaining_cooldown)


def prefix_cooldown(seconds: float, *, cooldown_name: Optional[str] = None):
    """
    ``commands.check`` that enforces the unified cooldown before a prefix
    command is invoked. The command body is still responsible for calling
    ``bot.cooldowns.set_cooldown()`` after a successful invocation.
    """

    async def predicate(ctx: commands.Context) -> bool:
        name = cooldown_name or (
            ctx.command.qualified_name if ctx.command else None
        )
        if not name:
            return True

        async with ctx.bot.cooldowns.lock(ctx.author.id, name):
            remaining = await ctx.bot.cooldowns.get_remaining(ctx.author.id, name)
            if remaining > 0:
                raise commands.CommandOnCooldown(
                    Cooldown(1, seconds), remaining, BucketType.user
                )
        return True

    return commands.check(predicate)


def slash_cooldown(seconds: float, *, cooldown_name: Optional[str] = None):
    """
    Decorator for app/slash commands that checks the unified cooldown,
    runs the command, and persists the cooldown afterwards.

    Because the same database key is used for prefix and slash invocations,
    a command that exists in both forms cannot be double-dipped by switching
    between them.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            interaction: Optional[discord.Interaction] = None
            for arg in args:
                if isinstance(arg, discord.Interaction):
                    interaction = arg
                    break
            if interaction is None:
                for value in kwargs.values():
                    if isinstance(value, discord.Interaction):
                        interaction = value
                        break

            if interaction is None:
                raise RuntimeError(
                    "slash_cooldown could not locate the discord.Interaction argument"
                )

            name = cooldown_name or (
                getattr(interaction.command, "qualified_name", None)
                or getattr(interaction.command, "name", None)
            )

            if name:
                cooldowns = interaction.client.cooldowns
                user_id = interaction.user.id
                async with cooldowns.lock(user_id, name):
                    remaining = await cooldowns.get_remaining(user_id, name)
                    if remaining > 0:
                        # If the same user/command is already being processed,
                        # this interaction is likely a Discord retry. Tell the
                        # user to wait instead of showing a raw cooldown error.
                        if cooldowns.is_in_flight(user_id, name):
                            try:
                                if not interaction.response.is_done():
                                    await interaction.response.send_message(
                                        "⏳ Your request is still being processed. "
                                        "Please wait a moment.",
                                        ephemeral=True,
                                    )
                            except Exception:
                                pass
                            return

                        logger.debug(
                            "Slash cooldown blocked user %s for /%s (%.2fs remaining)",
                            user_id,
                            name,
                            remaining,
                        )
                        raise app_commands.CommandOnCooldown(
                            app_commands.Cooldown(1, seconds), remaining
                        )

                    # Set the cooldown immediately so that Discord interaction
                    # retries (which can fire if the command does not respond
                    # within ~3 seconds) see that an invocation is already in
                    # progress instead of racing into the command body.
                    await cooldowns.set_cooldown(user_id, name, seconds)
                    cooldowns.set_in_flight(user_id, name, interaction.id)

                logger.debug("Set slash cooldown for user %s /%s", user_id, name)
                try:
                    return await func(*args, **kwargs)
                finally:
                    cooldowns.clear_in_flight(user_id, name)

            return await func(*args, **kwargs)

        wrapper._unified_cooldown = True
        return wrapper

    return decorator


def unified_cooldown(seconds: float, *, cooldown_name: Optional[str] = None):
    """
    Hybrid-friendly cooldown decorator.

    Works on:
    - ``@commands.command`` / ``@commands.hybrid_command`` callbacks that receive
      a ``commands.Context`` (the wrapper sees the same Context for both prefix
      and hybrid-slash invocations).
    - ``@app_commands.command`` callbacks that receive a
      ``discord.Interaction``.

    The command name used as the cooldown key is inferred from the Context or
    Interaction, so prefix and slash forms of the same command share one
    persistent cooldown entry.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx_or_interaction: Union[commands.Context, discord.Interaction, None] = (
                None
            )
            for arg in args:
                if isinstance(arg, (commands.Context, discord.Interaction)):
                    ctx_or_interaction = arg
                    break
            if ctx_or_interaction is None:
                for value in kwargs.values():
                    if isinstance(value, (commands.Context, discord.Interaction)):
                        ctx_or_interaction = value
                        break

            if ctx_or_interaction is None:
                raise RuntimeError(
                    "unified_cooldown could not locate a Context or Interaction argument"
                )

            if isinstance(ctx_or_interaction, commands.Context):
                bot = ctx_or_interaction.bot
                user_id = ctx_or_interaction.author.id
                name = cooldown_name or (
                    ctx_or_interaction.command.qualified_name
                    if ctx_or_interaction.command
                    else None
                )
                # A hybrid command invoked via slash still passes a Context,
                # but the underlying Interaction is exposed through ctx.interaction.
                is_slash = ctx_or_interaction.interaction is not None
            else:
                bot = ctx_or_interaction.client
                user_id = ctx_or_interaction.user.id
                name = cooldown_name or (
                    getattr(ctx_or_interaction.command, "qualified_name", None)
                    or getattr(ctx_or_interaction.command, "name", None)
                )
                is_slash = True

            if not name:
                return await func(*args, **kwargs)

            async with bot.cooldowns.lock(user_id, name):
                # Deduplicate slash interactions by ID. Discord can send the same
                # interaction multiple times, and hybrid commands can also be
                # dispatched twice if command registrations overlap. Drop exact
                # duplicates silently to avoid "already acknowledged" errors.
                if is_slash:
                    interaction = (
                        ctx_or_interaction.interaction
                        if isinstance(ctx_or_interaction, commands.Context)
                        else ctx_or_interaction
                    )
                    if bot.cooldowns.mark_interaction_processed(interaction.id):
                        logger.debug(
                            "Dropping duplicate slash interaction %s for %s",
                            interaction.id,
                            name,
                        )
                        return

                remaining = await bot.cooldowns.get_remaining(user_id, name)
                if remaining > 0:
                    # If the same user/command is already being processed by an
                    # active slash interaction, this is likely a Discord retry
                    # rather than a deliberate second invocation. Respond with a
                    # friendly "still processing" message instead of a cooldown
                    # error to avoid confusing users.
                    if is_slash and bot.cooldowns.is_in_flight(user_id, name):
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(
                                    "⏳ Your request is still being processed. "
                                    "Please wait a moment.",
                                    ephemeral=True,
                                )
                        except Exception:
                            pass
                        return

                    logger.debug(
                        "Unified cooldown blocked user %s for %s (%.2fs remaining, slash=%s)",
                        user_id,
                        name,
                        remaining,
                        is_slash,
                    )
                    if is_slash:
                        raise app_commands.CommandOnCooldown(
                            app_commands.Cooldown(1, seconds), remaining
                        )
                    raise commands.CommandOnCooldown(
                        Cooldown(1, seconds), remaining, BucketType.user
                    )

                # Set the cooldown immediately so that Discord interaction
                # retries (which can fire if the command does not respond within
                # ~3 seconds) see that an invocation is already in progress
                # instead of racing into the command body.
                await bot.cooldowns.set_cooldown(ctx_or_interaction, seconds, name)
                if is_slash:
                    bot.cooldowns.set_in_flight(user_id, name, interaction.id)

            logger.debug(
                "Set unified cooldown for user %s command %s (slash=%s)",
                user_id,
                name,
                is_slash,
            )
            try:
                return await func(*args, **kwargs)
            finally:
                if is_slash:
                    bot.cooldowns.clear_in_flight(user_id, name)

        wrapper._unified_cooldown = True
        return wrapper

    return decorator
