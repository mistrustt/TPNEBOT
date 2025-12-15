import discord
import logging
from discord.ext import commands

logger = logging.getLogger("discord_bot")


class CooldownUtils:
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
