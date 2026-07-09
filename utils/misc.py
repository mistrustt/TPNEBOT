import logging

import humanfriendly
from discord.ext import commands

logger = logging.getLogger("discord.client")

class MiscUtils:
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    default_avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"

    def get_avatar_url(self, member):
        return member.avatar.url if member.avatar else self.default_avatar_url

    async def parse_duration(self, duration_str: str) -> int:
        """Parse a user-supplied duration string and return the duration in seconds."""
        try:
            return int(humanfriendly.parse_timespan(duration_str))
        except humanfriendly.InvalidTimespan as exc:
            raise ValueError("Invalid duration format. Use something like `1h`, `30m`, or `2 days`.") from exc
