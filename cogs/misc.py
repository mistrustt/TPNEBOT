import discord
from discord.ext import commands
from discord.ext.commands import Context
import logging
import os
import aiohttp
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import pytz
from datetime import datetime
from utils.misc import MiscUtils
import asyncio
from faker import Faker
from typing import Union

logger = logging.getLogger("discord_bot")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")


class Misc(commands.Cog, name="Misc"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.utils = MiscUtils(self)
        self.geolocator = Nominatim(user_agent="timezone_bot")
        self.timezone_finder = TimezoneFinder()
        self.fake = Faker()
        
        # Centralized Juul flavor bank
        self.JUUL_FLAVORS = ["classic", "mint", "fruit", "berry", "tropical", "ice", "thc", "dessert"]
        self.JUUL_FLAVOR_EMOJIS = {
            "classic": "🚬",
            "mint": "🍃",
            "fruit": "🍓",
            "berry": "🫐",
            "tropical": "🍍",
            "ice": "❄️",
            "thc": "🍀",
            "dessert": "🍰"
        }
        self.JUUL_FLAVOR_RESPONSES = {
            "classic": "You take a hit from the classic juul. 🚬😮‍💨",
            "mint": "You take a refreshing mint hit. 🍃😮‍💨",
            "fruit": "You enjoy a sweet fruit flavor. 🍓😮‍💨",
            "berry": "You savor the berry blast. 🫐😮‍💨",
            "tropical": "You taste the tropical paradise. 🍍😮‍💨",
            "ice": "You take a refreshing ice hit. ❄️😮‍💨",
            "thc": "You hit the cart and feel the effects. 🫨😮‍💨",
            "dessert": "You enjoy a hit of dessert flavors. 🍰😮‍💨"
        }

    @staticmethod
    def _is_hash(value) -> bool:
        """Return True if a stored user ID value is a HMAC-SHA256 hex hash."""
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdefABCDEF" for c in value)
        )

    async def _resolve_id(self, value):
        """Resolve a stored user ID to a raw Discord ID when it is a hash."""
        if value is None or isinstance(value, int):
            return value
        if self._is_hash(value):
            return await self.bot.database.resolve_user_hash(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _resolve_ids(self, values):
        """Batch-resolve stored user IDs, leaving raw IDs unchanged."""
        if not values:
            return {}
        unique = list(dict.fromkeys(v for v in values if v is not None))
        hashes = [v for v in unique if self._is_hash(v)]
        resolved = await self.bot.database.resolve_user_hashes(hashes) if hashes else {}
        mapping = {}
        for v in unique:
            if isinstance(v, int):
                mapping[v] = v
            elif self._is_hash(v):
                mapping[v] = resolved.get(v)
            else:
                try:
                    mapping[v] = int(v)
                except (TypeError, ValueError):
                    mapping[v] = None
        return mapping

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    def calculate_aura(self, reactions_received: int, reactions_given: int) -> str:
        aura_score = reactions_received - reactions_given

        negative_intervals = [
            (-10000, -9001, "Illegal Aura"),
            (-9000, -8001, "Demonic Aura"),
            (-8000, -7001, "Infernal Aura"),
            (-7000, -6001, "Hellish Aura"),
            (-6000, -5001, "Fiendish Aura"),
            (-5000, -4001, "Sinful Aura"),
            (-4000, -3001, "Diabolical Aura"),
            (-3000, -2001, "Vile Aura"),
            (-2000, -1001, "Infamous Aura"),
            (-1000, -901, "Abyssal Aura"),
            (-900, -801, "Cursed Aura"),
            (-800, -701, "Malevolent Aura"),
            (-700, -601, "Evil Aura"),
            (-600, -501, "Twisted Aura"),
            (-500, -401, "Corrupt Aura"),
            (-400, -301, "Sinister Aura"),
            (-300, -201, "Ominous Aura"),
            (-200, -101, "Terrible Aura"),
            (-100, -51, "Dark Aura"),
            (-50, -1, "Negative Aura"),
        ]

        positive_intervals = [
            (0, 50, "Weak Aura"),
            (51, 100, "Strong Aura"),
            (101, 250, "Immense Aura"),
            (251, 350, "Insane Aura"),
            (351, 450, "Unfathomable Aura"),
            (451, 550, "Godlike Aura"),
            (551, 650, "Celestial Aura"),
            (651, 750, "Astral Aura"),
            (751, 850, "Cosmic Aura"),
            (851, 900, "Divine Aura"),
            (901, 950, "Omnipotent Aura"),
            (951, 1000, "Transcendent Aura"),
            (1001, 2000, "Ethereal Aura"),
            (2001, 3000, "Angelic Aura"),
            (3001, 4000, "Mythical Aura"),
            (4001, 5000, "Legendary Aura"),
            (5001, 6000, "Boundless Aura"),
            (6001, 7000, "Untold Aura"),
            (7001, 8000, "Eminent Aura"),
            (8001, 9000, "Eternal Aura"),
            (9001, 10000, "Infinite Aura"),
        ]

        if aura_score <= -10001:
            return "Null Aura"
        elif aura_score < 0:
            for low, high, level in negative_intervals:
                if low <= aura_score <= high:
                    return level
        elif aura_score == 0:
            return "No Aura"
        else:
            for low, high, level in positive_intervals:
                if low <= aura_score <= high:
                    return level

        return "Hes too powerful..."

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        for attempt in range(3):
            try:
                await self._handle_reaction_add(reaction, user)
                break
            except discord.HTTPException as e:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                else:
                    raise e

    async def _handle_reaction_add(
        self, reaction: discord.Reaction, user: discord.User
    ):
        user_id = int(user.id)
        author_id = int(reaction.message.author.id)

        if reaction.emoji in ["😭", "💀", "🔥", "❤️", "🤡"]:
            if reaction.emoji == "😭":
                logging.debug(
                    f"Processing sob reaction for user: {user_id}, author: {author_id}"
                )
                await self.bot.database.update_sobs(author_id, sobs_rx_delta=1)
                await self.bot.database.update_sobs(user_id, sobs_tx_delta=1)
                await self.bot.database.add_reputation_score(author_id, 3)

            elif reaction.emoji == "💀":
                logging.debug(
                    f"Processing skull reaction for user: {user_id}, author: {author_id}"
                )
                await self.bot.database.update_skulls(author_id, skulls_rx_delta=1)
                await self.bot.database.update_skulls(user_id, skulls_tx_delta=1)
                await self.bot.database.add_reputation_score(author_id, 1)

            elif reaction.emoji == "🔥":
                logging.debug(
                    f"Processing flame reaction for user: {user_id}, author: {author_id}"
                )
                await self.bot.database.update_flames(author_id, flames_rx_delta=1)
                await self.bot.database.update_flames(user_id, flames_tx_delta=1)
                await self.bot.database.add_reputation_score(author_id, 1)

            elif reaction.emoji == "❤️":
                logging.debug(
                    f"Processing heart reaction for user: {user_id}, author: {author_id}"
                )
                await self.bot.database.update_hearts(author_id, hearts_rx_delta=1)
                await self.bot.database.update_hearts(user_id, hearts_tx_delta=1)
                await self.bot.database.add_reputation_score(author_id, 2)

            elif reaction.emoji == "🤡":
                logging.debug(
                    f"Processing clown reaction for user: {user_id}, author: {author_id}"
                )
                await self.bot.database.update_clowns(author_id, clowns_rx_delta=1)
                await self.bot.database.update_clowns(user_id, clowns_tx_delta=1)
                await self.bot.database.add_reputation_score(author_id, -3)

    @commands.group(
        name="sobs",
        help="Shows the number of sob reactions a user has received and given.",
        invoke_without_command=True,
    )
    async def sobs(self, ctx: Context, member: discord.Member = None) -> None:
        if ctx.invoked_subcommand is None:
            member = member or ctx.author
            sobs_rx, sobs_tx = await self.bot.database.get_reaction_stats(
                member.id, "sobs"
            )
            aura = self.calculate_aura(sobs_rx, sobs_tx)
            embed = discord.Embed(
                title="Sobs :sob:", description=f"{aura}", color=discord.Color.gold()
            )
            embed.add_field(name="Received", value=f"{sobs_rx:,}", inline=True)
            embed.add_field(name="Given", value=f"{sobs_tx:,}", inline=True)
            embed.add_field(
                name="Sobworth", value=f"{sobs_rx - sobs_tx:,}", inline=True
            )
            embed.set_author(
                name=f"{member.display_name}",
                icon_url=self.utils.get_avatar_url(member),
            )
            await ctx.reply(embed=embed, delete_after=30)

    @sobs.command(
        name="leaderboard",
        aliases=["lb"],
        description="Display the top and bottom 10 users by sobs received.",
    )
    async def sobs_leaderboard(self, ctx: Context) -> None:
        await ctx.defer()
        top_users = await self.bot.database.get_top_sobs_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_sobs_users(limit=10)

        embed = discord.Embed(
            title="Sobs Leaderboard :sob:", color=discord.Color.gold()
        )

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, sobs) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{sobs:,} sobs`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, sobs) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** (`{sobs:,} sobs`)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        await ctx.send(embed=embed)

    @commands.group(
        name="skulls",
        help="Shows the number of skull reactions a user has received and given.",
        invoke_without_command=True,
    )
    async def skulls(self, ctx: Context, member: discord.Member = None) -> None:
        if ctx.invoked_subcommand is None:
            member = member or ctx.author
            skulls_rx, skulls_tx = await self.bot.database.get_reaction_stats(
                member.id, "skulls"
            )
            aura = self.calculate_aura(skulls_rx, skulls_tx)
            embed = discord.Embed(
                title="Skulls :skull:",
                description=f"{aura}",
                color=discord.Color.dark_gray(),
            )
            embed.add_field(name="Received", value=f"{skulls_rx:,}", inline=True)
            embed.add_field(name="Given", value=f"{skulls_tx:,}", inline=True)
            embed.add_field(
                name="Skullworth", value=f"{skulls_rx - skulls_tx:,}", inline=True
            )
            embed.set_author(
                name=f"{member.display_name}",
                icon_url=self.utils.get_avatar_url(member),
            )
            await ctx.reply(embed=embed, delete_after=30)

    @skulls.command(
        name="leaderboard",
        aliases=["lb"],
        description="Display the top and bottom 10 users by skulls received.",
    )
    async def skulls_leaderboard(self, ctx: Context) -> None:
        await ctx.defer()
        top_users = await self.bot.database.get_top_skulls_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_skulls_users(limit=10)

        embed = discord.Embed(title="Skulls Leaderboard :skull:")

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, skulls) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{skulls:,} skulls`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, skulls) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** (`{skulls:,} skulls`)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        await ctx.send(embed=embed)

    @commands.group(
        name="flames",
        aliases=["fires"],
        help="Shows the number of flame reactions a user has received and given.",
        invoke_without_command=True,
    )
    async def flames(self, ctx: Context, member: discord.Member = None) -> None:
        if ctx.invoked_subcommand is None:
            member = member or ctx.author
            flames_rx, flames_tx = await self.bot.database.get_reaction_stats(
                member.id, "flames"
            )
            aura = self.calculate_aura(flames_rx, flames_tx)
            embed = discord.Embed(
                title="Flames :fire:", description=f"{aura}", color=0xFFB02E
            )
            embed.add_field(name="Received", value=f"{flames_rx:,}", inline=True)
            embed.add_field(name="Given", value=f"{flames_tx:,}", inline=True)
            embed.add_field(
                name="Flameworth", value=f"{flames_rx - flames_tx:,}", inline=True
            )
            embed.set_author(
                name=f"{member.display_name}",
                icon_url=self.utils.get_avatar_url(member),
            )
            await ctx.reply(embed=embed, delete_after=30)

    @flames.command(
        name="leaderboard",
        aliases=["lb"],
        description="Display the top and bottom 10 users by flames received.",
    )
    async def flames_leaderboard(self, ctx: Context) -> None:
        await ctx.defer()
        top_users = await self.bot.database.get_top_flames_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_flames_users(limit=10)

        embed = discord.Embed(title="Flame Leaderboard :fire:", color=0xFFB02E)

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, flames) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{flames:,} flames`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, flames) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** (`{flames:,} flames`)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        await ctx.send(embed=embed)

    @commands.group(
        name="hearts",
        help="Shows the number of heart reactions a user has received and given.",
        invoke_without_command=True,
    )
    async def hearts(self, ctx: Context, member: discord.Member = None) -> None:
        if ctx.invoked_subcommand is None:
            member = member or ctx.author
            hearts_rx, hearts_tx = await self.bot.database.get_reaction_stats(
                member.id, "hearts"
            )
            aura = self.calculate_aura(hearts_rx, hearts_tx)
            embed = discord.Embed(
                title="Hearts :heart:", description=f"{aura}", color=discord.Color.red()
            )
            embed.add_field(name="Received", value=f"{hearts_rx:,}", inline=True)
            embed.add_field(name="Given", value=f"{hearts_tx:,}", inline=True)
            embed.add_field(
                name="Heartworth", value=f"{hearts_rx - hearts_tx:,}", inline=True
            )
            embed.set_author(
                name=f"{member.display_name}",
                icon_url=self.utils.get_avatar_url(member),
            )
            await ctx.reply(embed=embed, delete_after=30)

    @hearts.command(
        name="leaderboard",
        aliases=["lb"],
        description="Display the top and bottom 10 users by hearts received.",
    )
    async def hearts_leaderboard(self, ctx: Context) -> None:
        await ctx.defer()
        top_users = await self.bot.database.get_top_hearts_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_hearts_users(limit=10)

        embed = discord.Embed(
            title="Hearts Leaderboard :heart:", color=discord.Color.red()
        )

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, hearts) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{hearts:,} hearts`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, hearts) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** (`{hearts:,} hearts`)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        await ctx.send(embed=embed)

    @commands.group(
        name="clowns",
        help="Shows the number of clown reactions a user has received and given.",
        invoke_without_command=True,
    )
    async def clowns(self, ctx: Context, member: discord.Member = None) -> None:
        if ctx.invoked_subcommand is None:
            member = member or ctx.author
            clowns_rx, clowns_tx = await self.bot.database.get_reaction_stats(
                member.id, "clowns"
            )
            aura = self.calculate_aura(clowns_rx, clowns_tx)
            embed = discord.Embed(
                title="Clowns :clown:", description=f"{aura}", color=0xFFFFFF
            )
            embed.add_field(name="Received", value=f"{clowns_rx:,}", inline=True)
            embed.add_field(name="Given", value=f"{clowns_tx:,}", inline=True)
            embed.add_field(
                name="Clownworth", value=f"{clowns_rx - clowns_tx:,}", inline=True
            )
            embed.set_author(
                name=f"{member.display_name}",
                icon_url=self.utils.get_avatar_url(member),
            )
            await ctx.reply(embed=embed, delete_after=30)

    @clowns.command(
        name="leaderboard",
        aliases=["lb"],
        description="Display the top and bottom 10 users by clowns received.",
    )
    async def clowns_leaderboard(self, ctx: Context) -> None:
        await ctx.defer()
        top_users = await self.bot.database.get_top_clowns_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_clowns_users(limit=10)

        embed = discord.Embed(title="Clowns Leaderboard :clown:", color=0xFFFFFF)

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, clowns) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{clowns:,} clowns`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, clowns) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** (`{clowns:,} clowns`)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        await ctx.send(embed=embed)

    REACTIONS = {
        "sobs": "update_sobs",
        "skulls": "update_skulls",
        "flames": "update_flames",
        "hearts": "update_hearts",
        "clowns": "update_clowns",
    }
    DIRECTIONS = {
        "rx": "rx_delta",
        "received": "rx_delta",
        "tx": "tx_delta",
        "transmitted": "tx_delta",
    }

    @commands.command(
        name="setreact",
        aliases=["setsobs", "setskulls", "setflames", "sethearts"],
        help=(
            "Adjust a user's reactions.\n"
            "Usage: `!setreact @user|user_id <sobs|skulls|flames|hearts> <rx|tx> <amount>`"
        ),
        hidden=True,
    )
    @commands.is_owner()
    async def setreact(
        self,
        ctx: commands.Context,
        member: Union[discord.Member, discord.User],
        reaction_type: str,
        direction: str,
        amount: int,
    ):
        """Adjusts the given reaction counter for a user."""
        rt = reaction_type.lower()
        dr = direction.lower()

        if rt not in self.REACTIONS:
            valid = ", ".join(self.REACTIONS.keys())
            await ctx.send(f"🚫 Invalid reaction type. Choose one of: {valid}")
            return

        if dr not in self.DIRECTIONS:
            await ctx.send(
                "🚫 Invalid direction. Use `rx` (received) or `tx` (transmitted)."
            )
            return

        db_method = getattr(self.bot.database, self.REACTIONS[rt], None)
        if not db_method:
            await ctx.send("🚫 Database method not found. Contact the devs.")
            return

        delta_field = f"{rt}_{self.DIRECTIONS[dr]}"
        kwargs = {delta_field: amount}

        try:
            await db_method(member.id, **kwargs)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to update: `{e}`")
            return

        await ctx.send(
            f"✅ {rt.capitalize()} for {member.mention}: “{dr.upper()}” changed by {amount}."
        )

    @commands.group(
        name="rep",
        description="View or update a user's reputation score.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def reputation(self, ctx: Context, member: discord.Member = None) -> None:
        member = member or ctx.author
        info = await self.bot.database.get_reputation_full(member.id)

        embed = discord.Embed(
            title=f"{member.display_name}'s Reputation",
            color=discord.Color.blurple(),
        )
        embed.set_author(
            name=member.display_name, icon_url=self.utils.get_avatar_url(member)
        )
        embed.add_field(
            name="Score",
            value=f"**{info['reputation']:,}** — *{info['title']}*",
            inline=False,
        )
        embed.add_field(
            name="Votes",
            value=f"+{info['good_reps_received']} good / -{info['bad_reps_received']} bad",
            inline=False,
        )
        embed.add_field(
            name="Earned Today",
            value=f"{info['rep_earned_today']:,} / 500",
            inline=False,
        )

        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 900
        )
        await ctx.reply(embed=embed)

    @commands.command(name="karma", description="View a user's reputation score.")
    @commands.guild_only()
    async def karma(self, ctx: Context, member: discord.Member = None) -> None:
        """Alias for !rep. Karma and reputation are the same score."""
        await self.reputation(ctx, member)

    async def change_reputation(
        self, ctx: Context, member: discord.Member, amount: int
    ) -> None:
        if not member or member == ctx.author:
            await ctx.reply("Please specify a valid user other than yourself.")
            return
        if member.bot:
            await ctx.reply("You cannot vote for bots.")
            return

        result = await self.bot.database.give_rep(ctx.author.id, member.id, amount)
        if not result.get("ok"):
            await ctx.reply(f"{result['error']}", delete_after=10)
            return

        action = "good" if amount > 0 else "bad"
        embed = discord.Embed(
            description=f"{ctx.author.mention} gave {abs(amount)} {action} rep to {member.mention}",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{member.display_name}: {result['reputation']:,} rep • "
                 f"{result['reps_remaining']} votes left today"
        )
        await ctx.reply(embed=embed, delete_after=15)

    @reputation.command(
        name="good", description="Give a user a positive reputation point."
    )
    async def upvote(self, ctx: Context, member: discord.Member = None) -> None:
        await self.change_reputation(ctx, member, 1)

    @reputation.command(
        name="bad", description="Give a user a negative reputation point."
    )
    async def downvote(self, ctx: Context, member: discord.Member = None) -> None:
        await self.change_reputation(ctx, member, -1)

    @reputation.command(
        name="leaderboard",
        aliases=["lb"],
        description="Shows the top 10 users by reputation.",
    )
    async def rep_leaderboard(self, ctx: Context):
        top_users = await self.bot.database.get_top_reputation_users(limit=10)
        bottom_users = await self.bot.database.get_bottom_reputation_users(limit=10)

        embed = discord.Embed(
            color=ctx.author.top_role.color
            if ctx.author.top_role
            else discord.Color.blurple()
        )

        if top_users:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            resolved_top = await self._resolve_ids([uid for uid, _ in top_users])
            for idx, (user_id, rep) in enumerate(top_users):
                raw_id = resolved_top.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
                emoji = rank_emojis[idx] if idx <= 10 else f"{idx}."
                top_list.append(f"{emoji} **{display_name}** ({rep:,} rep)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)

        if bottom_users:
            bottom_list = []
            rank_emojis = [":poop:"] + [f"{idx}." for idx in range(2, 11)]
            resolved_bottom = await self._resolve_ids([uid for uid, _ in bottom_users])
            for idx, (user_id, rep) in enumerate(bottom_users):
                raw_id = resolved_bottom.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    emoji = rank_emojis[idx] if idx <= 10 else f"{idx}."
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    emoji = rank_emojis[idx] if idx <= 10 else f"{idx}."
                    display_name = "Unknown user"
                bottom_list.append(f"{emoji} **{display_name}** ({rep:,} rep)")
            embed.add_field(
                name="Bottom 10 Users", value="\n".join(bottom_list), inline=True
            )
        else:
            embed.add_field(
                name="Bottom 10 Users", value="No data available", inline=True
            )

        embed.set_author(
            name="Reputation Leaderboard",
            icon_url=self.utils.get_avatar_url(ctx.author),
        )
        embed.set_footer(
            text=f"Your position: {await self.bot.database.get_reputation_user_rank(ctx.author.id)}"
        )
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        await ctx.send(embed=embed)

    def get_continent_from_country(country: str) -> str:
        """
        Return a continent code for a given country.
        For now, this is a simplified placeholder dictionary.
        """
        mapping = {
            "United States": "NA",
            "Canada": "NA",
            "Mexico": "NA",
            "Brazil": "SA",
            "Argentina": "SA",
            "Chile": "SA",
            "United Kingdom": "EU",
            "Germany": "EU",
            "France": "EU",
            "Spain": "EU",
            "Italy": "EU",
            "China": "AS",
            "India": "AS",
            "Japan": "AS",
            "South Korea": "AS",
            "Nigeria": "AF",
            "South Africa": "AF",
            "Egypt": "AF",
            "Australia": "AU",
            "New Zealand": "AU",
        }
        return mapping.get(country, "Unknown")

    def get_continent_emoji(continent_code: str) -> str:
        """
        Return a placeholder emoji for a continent code.
        """
        emoji_mapping = {
            "AS": "🌏",
            "AF": "🌍",
            "AU": "🇦🇺",
            "EU": "🇪🇺",
            "SA": "🇧🇷",
            "NA": "🇺🇸",
            "Unknown": "❓",
        }
        return emoji_mapping.get(continent_code, "❓")

    @commands.command(name="setrep", hidden=True)
    @commands.is_owner()
    async def admin_rep(
        self, ctx: Context, member: discord.Member = None, rep: int = 0
    ):
        member = member or ctx.author
        new_rep = await self.bot.database.increment_reputation(member.id, rep)
        if rep == 0:
            action = "no"
        elif rep > 0:
            action = "good"
        else:
            action = "bad"
        await ctx.reply(
            f"{ctx.author.mention} applied {abs(rep)} {action} rep to {member.mention}. "
            f"Current rep: {new_rep:,}",
            delete_after=15,
        )

    @commands.group(name="juul", invoke_without_command=True)
    @commands.guild_only()
    async def juul(self, ctx: Context):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
        if juul and juul.holder_id:
            raw_holder_id = await self._resolve_id(juul.holder_id)
            user = ctx.guild.get_member(raw_holder_id) or await self.bot.fetch_user(
                raw_holder_id
            )
            holder = user.display_name if user else f"User {raw_holder_id}"
        else:
            holder = "Nobody"

        # Use centralized flavor bank
        flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(flavor, "classic")

        embed = discord.Embed(
            title=f"{ctx.guild.name} Juul Stats {flavor_emoji}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Holder", value=holder, inline=True)
        embed.add_field(name="Flavor", value=f"{flavor.capitalize()} {flavor_emoji}", inline=True)
        embed.add_field(name="Hits", value=juul.hits if juul else 0, inline=True)
        embed.add_field(name="Passes", value=juul.passes if juul else 0, inline=True)
        embed.add_field(name="Steals", value=juul.steals if juul else 0, inline=True)
        embed.add_field(name="Status", value="Locked 🔒" if (juul and juul.locked) else "Unlocked 🔓", inline=True)
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        await ctx.reply(embed=embed)

    @juul.command(name="hit")
    async def juul_hit(self, ctx: Context):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
        if not juul or await self._resolve_id(juul.holder_id) != ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You need to be holding the juul to take a hit.",
                    color=discord.Color.red(),
                )
            )

        await self.bot.database.increment_juul_hits(ctx.guild.id)
        
        # Flavor-specific responses
        response = self.JUUL_FLAVOR_RESPONSES.get(flavor, "You take a hit from the juul. 😮‍💨")
        
        await ctx.send(
            embed=discord.Embed(
                description=response,
                color=discord.Color.green(),
            )
        )

    @juul.command(name="pass")
    async def juul_pass(self, ctx: Context, member: discord.Member):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
        if member.id == ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You can't pass the juul to yourself.",
                    color=discord.Color.red(),
                )
            )

        if not juul or await self._resolve_id(juul.holder_id) != ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="You don't have the juul to pass.",
                    color=discord.Color.red(),
                )
            )

        await self.bot.database.set_juul_holder(ctx.guild.id, member.id)
        await self.bot.database.increment_juul_passes(ctx.guild.id)
        
        # Use centralized flavor bank
        flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(flavor, "-cigarette")
        
        await ctx.send(
            embed=discord.Embed(
                description=f"You passed the {flavor} juul {flavor_emoji} to {member.mention}.",
                color=discord.Color.green(),
            )
        )

    @juul.command(name="steal")
    async def juul_steal(self, ctx: Context):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
        if juul and await self._resolve_id(juul.holder_id) == ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You already have the juul.",
                    color=discord.Color.red(),
                )
            )

        locked = await self.bot.database.get_juul_lock(ctx.guild.id)
        if locked:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 The juul is locked! You can't steal it.",
                    color=discord.Color.red(),
                )
            )

        await self.bot.database.set_juul_holder(ctx.guild.id, ctx.author.id)
        await self.bot.database.increment_juul_steals(ctx.guild.id)
        
        # Use centralized flavor bank
        flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(flavor, "-cigarette")
        
        await ctx.send(
            embed=discord.Embed(
                description=f"{ctx.author.mention} has stolen the {flavor} juul {flavor_emoji} from {ctx.guild.get_member(await self._resolve_id(juul.holder_id)).mention if juul and juul.holder_id else 'nobody'}!",
                color=discord.Color.green(),
            )
        )

    @juul.command(name="lock")
    @commands.has_permissions(manage_messages=True)
    async def juul_lock(self, ctx: Context):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
        if not juul or await self._resolve_id(juul.holder_id) != ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You need to be holding the juul to lock it.",
                    color=discord.Color.red(),
                )
            )

        await self.bot.database.set_juul_lock(ctx.guild.id, True)
        
        # Use centralized flavor bank
        flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(flavor, "-cigarette")
        
        await ctx.send(
            embed=discord.Embed(
                description=f"The {flavor} juul {flavor_emoji} has been locked! No one can steal it now.",
                color=discord.Color.green(),
            )
        )

    @juul.command(name="unlock")
    @commands.has_permissions(manage_messages=True)
    async def juul_unlock(self, ctx: Context):
        juul = await self.bot.database.get_juul(ctx.guild.id)
        if not juul or await self._resolve_id(juul.holder_id) != ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You need to be holding the juul to unlock it.",
                    color=discord.Color.red(),
                )
            )

        await self.bot.database.set_juul_lock(ctx.guild.id, False)
        await ctx.send(
            embed=discord.Embed(
                description="The juul has been unlocked! Anyone can steal it now.",
                color=discord.Color.green(),
            )
        )

    @juul.command(name="flavor", aliases=["flavour"])
    async def juul_flavor(self, ctx: Context, flavor: str = None):
        """Check or change the Juul flavor."""
        if flavor is None:
            # Just show current flavor
            current_flavor = await self.bot.database.get_juul_flavor(ctx.guild.id)
            
            # Use centralized flavor bank
            flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(current_flavor, "-cigarette")
            
            embed = discord.Embed(
                title="Juul Flavor",
                description=f"Current flavor: **{current_flavor.capitalize()}** {flavor_emoji}",
                color=discord.Color.blurple()
            )
            embed.add_field(
                name="Available Flavors",
                value="`classic`, `mint`, `fruit`, `berry`, `tropical`, `cool`, `spicy`, `dessert`",
                inline=False
            )
            await ctx.reply(embed=embed)
            return

        # Check if user is holding the juul
        juul = await self.bot.database.get_juul(ctx.guild.id)
        if not juul or await self._resolve_id(juul.holder_id) != ctx.author.id:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 You need to be holding the juul to change its flavor.",
                    color=discord.Color.red(),
                )
            )

        # Normalize flavor input
        flavor = flavor.lower()
        
        # Use centralized flavor bank
        if flavor not in self.JUUL_FLAVORS:
            embed = discord.Embed(
                title="Invalid Flavor",
                description=f"🚫 That flavor doesn't exist!\n\nAvailable flavors: `{'`, `'.join(self.JUUL_FLAVORS)}`",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Set the new flavor
        await self.bot.database.set_juul_flavor(ctx.guild.id, flavor)
        
        # Use centralized flavor bank
        flavor_emoji = self.JUUL_FLAVOR_EMOJIS.get(flavor, "-cigarette")
        
        embed = discord.Embed(
            title="Flavor Changed!",
            description=f"The juul flavor has been changed to **{flavor.capitalize()}** {flavor_emoji}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command(
        name="tz", aliases=["timezone", "time"], help="Show your current timezone."
    )
    @commands.guild_only()
    async def show_time(self, ctx: Context):
        try:
            user_timezone = await self.bot.database.get_user_timezone(ctx.author.id)
            if not user_timezone:
                await ctx.reply(
                    "You haven't set your timezone yet! Use `!setloc <location>` to set it.",
                    delete_after=10,
                )
                return
            tz = pytz.timezone(user_timezone)
            current_time = discord.utils.utcnow().astimezone(tz)

            formatted_time = current_time.strftime("%I:%M %p")

            embed = discord.Embed(
                title="Your Local Time",
                description=f"Your current local time is: **{formatted_time}**",
                color=discord.Color.blurple(),
            )
            embed.set_author(
                name=ctx.author.display_name,
                icon_url=self.utils.get_avatar_url(ctx.author),
            )
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
            await ctx.reply(embed=embed)

        except Exception as e:
            await ctx.reply(f"An error occurred. lol.", delete_after=10)

    @commands.command(
        name="setlocation",
        aliases=["setloc", "settz"],
        help="Set your location for weather commands. (use in DMs for privacy)",
    )
    async def set_location(self, ctx, *, location: str = None):
        if location is None:
            await self.bot.database.set_user_location(ctx.author.id, None)
            await self.bot.database.set_user_timezone(ctx.author.id, None)
            embed = discord.Embed(
                title="Location Cleared",
                description="📍 Your location has been cleared.",
                color=discord.Color.blurple(),
            )
            return await ctx.send(embed=embed)

        timezone_str = None

        if "," in location:
            try:
                lat_str, lon_str = [p.strip() for p in location.split(",")]
                lat = round(float(lat_str), 1)
                lon = round(float(lon_str), 1)
            except ValueError:
                embed = discord.Embed(
                    description="🚫 Invalid coordinates format! Use `latitude,longitude`.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=embed)
            standardized_location = f"{lat},{lon}"
            timezone_str = self.timezone_finder.timezone_at(lng=lon, lat=lat)

        elif location.startswith(("iata:", "metar:", "auto:ip", "id:")):
            standardized_location = location

        else:
            location_data = self.geolocator.geocode(location, exactly_one=True)
            if location_data is None:
                embed = discord.Embed(
                    description=f"🚫 I couldn't find “{location}”. Please try a different place.",
                    color=discord.Color.red(),
                )
                return await ctx.send(embed=embed)
            lat = round(location_data.latitude, 1)
            lon = round(location_data.longitude, 1)
            standardized_location = f"{lat},{lon}"
            timezone_str = self.timezone_finder.timezone_at(lng=lon, lat=lat)

        if timezone_str and timezone_str in pytz.all_timezones:
            await self.bot.database.set_user_timezone(ctx.author.id, timezone_str)

        await self.bot.database.set_user_location(ctx.author.id, standardized_location)
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 10
        )
        await ctx.reply(f"📍 Your location has been set.")

    @commands.command(
        name="weather", aliases=["temp", "wind"], help="Check your local weather."
    )
    @commands.guild_only()
    async def weather(self, ctx):
        """Fetch and display weather based on stored location."""
        user_data = await self.bot.database.get_user_location(ctx.author.id)
        prefix = await self.bot.database.get_prefix(ctx.guild.id)
        show_location = False

        if not user_data:
            embed = discord.Embed(
                description=f"🚫 You haven't set a location! Use `{prefix}setloc <location>` first.",
                color=discord.Color.red(),
            )
            return await ctx.send(embed=embed)

        location = user_data

        url = f"https://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q={location}&aqi=no"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    embed = discord.Embed(
                        description=f"🚫 Couldn't retrieve weather data from API. Try again later. (HTTP {response.status})",
                        color=discord.Color.red(),
                    )
                    return await ctx.send(embed=embed)
                else:
                    weather_data = await response.json()

                    if not weather_data:
                        return await ctx.send(
                            f"🚫 Couldn't retrieve weather for the location you set. Try setting a new location."
                        )

                    region = weather_data["location"]["region"]
                    country = weather_data["location"]["country"]
                    temp_c = weather_data["current"]["temp_c"]
                    temp_f = weather_data["current"]["temp_f"]
                    feels_like_f = weather_data["current"]["feelslike_f"]
                    feels_like_c = weather_data["current"]["feelslike_c"]
                    weather_desc = weather_data["current"]["condition"]["text"]
                    humidity = weather_data["current"]["humidity"]
                    wind_speed_kph = weather_data["current"]["wind_kph"]
                    wind_speed_mph = weather_data["current"]["wind_mph"]
                    wind_dir = weather_data["current"]["wind_dir"]
                    icon_url = f"https:{weather_data['current']['condition']['icon']}"
                    localtime = weather_data["location"]["localtime"]

                    if show_location:
                        location_str = f" - {region}, {country}"
                    else:
                        location_str = ""

                    embed = discord.Embed(
                        description=f"**{weather_desc + location_str}**",
                        color=discord.Color.blurple(),
                    )
                    embed.set_thumbnail(url=icon_url)
                    embed.add_field(
                        name="🌡 Temperature",
                        value=f"{temp_f}°F / {temp_c}°C\nFeels like {feels_like_f}°F / {feels_like_c}°C",
                        inline=False,
                    )
                    embed.add_field(
                        name="💨 Wind",
                        value=f"{wind_speed_mph}mph / {wind_speed_kph}kph ({wind_dir})",
                        inline=False,
                    )
                    embed.add_field(
                        name="💧 Humidity", value=f"{humidity}%", inline=False
                    )
                    embed.set_footer(text=f"Local Time: {localtime}")
                    await ctx.reply(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Misc(bot))
    logging.debug("Misc cog initialized successfully")
