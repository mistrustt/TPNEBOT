import asyncio
import functools
import inspect
import logging
from collections import defaultdict
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import BucketType, Cooldown

logger = logging.getLogger("discord_bot")


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

    def _key(self, user_id: int, command_name: str) -> tuple[int, str]:
        return (user_id, command_name)

    def lock(self, user_id: int, command_name: str):
        """Return the asyncio.Lock for a specific user/command pair."""
        return self._locks[self._key(user_id, command_name)]

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
                async with interaction.client.cooldowns.lock(
                    interaction.user.id, name
                ):
                    remaining = await interaction.client.cooldowns.get_remaining(
                        interaction.user.id, name
                    )
                    if remaining > 0:
                        raise app_commands.CommandOnCooldown(
                            app_commands.Cooldown(1, seconds), remaining
                        )

                    result = await func(*args, **kwargs)
                    await interaction.client.cooldowns.set_cooldown(
                        interaction.user.id, name, seconds
                    )
                    return result

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
                remaining = await bot.cooldowns.get_remaining(user_id, name)
                if remaining > 0:
                    if is_slash:
                        raise app_commands.CommandOnCooldown(
                            app_commands.Cooldown(1, seconds), remaining
                        )
                    raise commands.CommandOnCooldown(
                        Cooldown(1, seconds), remaining, BucketType.user
                    )

                result = await func(*args, **kwargs)
                await bot.cooldowns.set_cooldown(ctx_or_interaction, seconds, name)
                return result

        wrapper._unified_cooldown = True
        return wrapper

    return decorator
