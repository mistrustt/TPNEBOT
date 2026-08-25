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

    def get_avatar_asset(self, member):
        if member is None:
            return None
        # display_avatar always returns an Asset (falling back to Discord's
        # default avatar), so this works whether or not the member has a
        # custom avatar set. member.avatar is an Asset and has no
        # .display_avatar attribute, so the previous member.avatar.display_avatar
        # access crashed for any member with a custom avatar.
        return member.display_avatar.with_format("png").with_size(256)

    async def parse_duration(self, duration_str: str) -> int:
        """Parse a user-supplied duration string and return the duration in seconds."""
        try:
            return int(humanfriendly.parse_timespan(duration_str))
        except humanfriendly.InvalidTimespan as exc:
            raise ValueError("Invalid duration format. Use something like `1h`, `30m`, or `2 days`.") from exc
