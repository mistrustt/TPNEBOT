import logging
from discord.ext import commands

logger = logging.getLogger("discord.client")


class MiscUtils:
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    default_avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"

    def get_avatar_url(self, member):
        return member.avatar.url if member.avatar else self.default_avatar_url

    async def parse_duration(self, duration_str: str) -> int:
        """Parses a duration string like '1h', '2d' and returns the duration in seconds"""
        duration_map = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        unit = duration_str[-1]
        if unit not in duration_map:
            raise ValueError("Invalid duration unit. Use s, m, h, d, or w.")
        try:
            duration_value = int(duration_str[:-1])
        except ValueError:
            raise ValueError("Invalid duration value.")
        return duration_value * duration_map[unit]
