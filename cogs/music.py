import os
import discord
import logging
import aiohttp
import asyncio
from io import BytesIO
from zoneinfo import ZoneInfo
from colorthief import ColorThief
from discord.ext import commands, tasks
from discord.ext.commands import Context
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from urllib.parse import quote
from utils.cache import Cache
from utils.embeds import Embeds
from itertools import product
from moviepy import *
import re
import random
from moviepy import AudioFileClip, ImageClip

logger = logging.getLogger("discord_bot")

JUICEWRLD_API = "https://juicewrldapi.com"

DOWNLOAD_CACHE_FOLDER_NAME = "__download_cache"
HEARDLE_GAME_DURATION = 20
HEARDLE_CLIP_DURATION = 10
DEFAULT_SNIPPET_DURATION = 15
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
MAX_NAME_TRANSFORMATIONS = 6
GENIUS_API_TOKEN = os.getenv("GENIUS_API_KEY")

class Music(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.default_avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"
        self.font_path = "DejaVuSans-ExtraLight.ttf"
        self.default_font = ImageFont.truetype(self.font_path, 24)
        self.font_small = ImageFont.truetype(self.font_path, 20)
        self.font_large = ImageFont.truetype(self.font_path, 40)

        self.latest_surfaces = []
        self.cache_songs.start()

        self.valid_names = []
        self.producer_counts = {}
        self.ongoing_blacktea = []
        self.ongoing_higherlower = []

        self.standard_colors = {
            "black": "#000000",
            "white": "#FFFFFF",
            "red": "#FF0000",
            "green": "#008000",
            "blue": "#0000FF",
            "yellow": "#FFFF00",
            "cyan": "#00FFFF",
            "magenta": "#FF00FF",
            "gray": "#808080",
            "purple": "#800080",
            "orange": "#FFA500",
            "pink": "#FFC0CB",
        }
        self.testing_ids = [
            1095747082599530627,  # ENVY (DUMB IDIOT)
            1219090700407279656,  # TOXIC (GOAT ASF)
        ]
        self.ongoing_heardle = []
        self.heardle_answers = {}
        self.snippet_debounce = {}

        self.ALBUMS = {
            "jute": {"name": "JUICED UP THE EP", "color": "#FFE602"},
            "LND": {"name": "Legends Never Die", "color": "#F700FF"},
            "afflictions": {"name": "affliction", "color": "#000000"},
            "bdm": {"name": "BINGEDRINKINGMUSIC", "color": "#000000"},
            "HIH 999": {
                "name": "Heartbroken In Hollywood 9 9 9",
                "color": "#FF653E",
            },
            "jw 999": {"name": "JuiceWRLD 9 9 9", "color": "#FF2C2C"},
            "ND": {"name": "NOTHINGS DIFFERENT </3", "color": "#FF8800"},
            "GB&GR": {"name": "Goodbye & Good Riddance", "color": "#008CFF"},
            "GB&GR (AE)": {
                "name": "Goodbye & Good Riddance (Anniversary Edition)",
                "color": "#008CFF",
            },
            "GB&GR (5YAE)": {
                "name": "Goodbye & Good Riddance (5 Year Anniversary Edition)",
                "color": "#008CFF",
            },
            "WOD": {"name": "WRLD ON DRUGS", "color": "#00FF94"},
            "DRFL": {"name": "Death Race For Love", "color": "#FF9900"},
            "DRFL (BTV)": {
                "name": "Death Race For Love (Bonus Track Version)",
                "color": "#FF9900",
            },
            "OUT": {"name": "Outsiders", "color": "#2B2B2B"},
            "POST": {"name": "Posthumous", "color": "#00CCFF"},
            "TPP": {"name": "The Pre-Party", "color": "#EA00FF"},
            "TPP (EE)": {
                "name": "The Pre-Party (Extended Edition)",
                "color": "#EA00FF",
            },
            "FD": {"name": "Fighting Demons", "color": "#2E2E2E"},
            "FD (CE)": {
                "name": "Fighting Demons (Complete Edition)",
                "color": "#2E2E2E",
            },
            "FD (EE)": {
                "name": "Fighting Demons (Extended Edition)",
                "color": "#2E2E2E",
            },
            "FD (DDE)": {
                "name": "Fighting Demons (Digital Deluxe Edition)",
                "color": "#2E2E2E",
            },
            "TPNE": {"name": "The Party Never Ends", "color": "#CC00FF"},
        }

        def check_question_marks(s):
            return "?" in s
        
        def remove_question_marks(s):
            return s.replace("?", "")
        
        def question_mark_to_spaces(s):
            return s.replace("?", " ")

        def check_parantheses(s):
            return "(" in s and ")" in s

        def remove_parentheses(s):
            return re.sub(r"\(.*?\)", "", s).strip()

        def check_brackets(s):
            return "[" in s and "]" in s

        def remove_brackets(s):
            return re.sub(r"\[.*?\]", "", s).strip()
        
        def check_semi_brackets(s):
            return "{" in s and "}" in s

        def remove_semi_brackets(s):
            return re.sub(r"\{.*?\}", "", s).strip()

        def check_apostrophes(s):
            return "'" in s    
    
        def remove_apostrophes(s):
            return s.replace("'", "")

        def check_periods(s):
            return "." in s

        def remove_periods(s):
            return s.replace(".", "")

        def period_to_spaces(s):
            return s.replace(".", " ")

        def check_commas(s):
            return "," in s

        def remove_commas(s):
            return s.replace(",", "")
        
        def comma_to_spaces(s):
            return s.replace(",", " ")

        def check_hyphens(s):
            return "-" in s

        def remove_hyphens(s):
            return s.replace("-", "")

        def hyphen_to_spaces(s):
            return s.replace("-", " ")

        self.track_name_transformations = {
            check_parantheses: [remove_parentheses],
            check_brackets: [remove_brackets],
            check_semi_brackets: [remove_semi_brackets],
            check_apostrophes: [remove_apostrophes],
            check_periods: [remove_periods, period_to_spaces],
            check_commas: [remove_commas, comma_to_spaces],
            check_question_marks: [remove_question_marks, question_mark_to_spaces],
            check_hyphens: [remove_hyphens, hyphen_to_spaces],
        }

    async def cog_unload(self):
        await self.session.close()

    def can_test(ctx: commands.Context, cog=None):
        if cog is None:
            cog = ctx.cog

        return ctx.author.id in cog.testing_ids

    def assert_download_cache(self):
        if not os.path.exists(DOWNLOAD_CACHE_FOLDER_NAME):
            os.makedirs(DOWNLOAD_CACHE_FOLDER_NAME)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")
        await self.sync_blacktea()

    async def update_user_index(self, lastfm_username: str):
        """Fetch and index recent listening data for a user."""
        url_recent = f"http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url_recent) as response:
                recent_tracks = await response.json()

                return (
                    recent_tracks["recenttracks"]["track"]
                    if "recenttracks" in recent_tracks
                    else []
                )

    @commands.group(name="lf", invoke_without_command=True)
    async def lastfm(self, ctx: Context) -> None:
        """Last.fm command group"""
        embed = discord.Embed(
            title="Last.fm", description="List of available subcommands"
        )
        subcommands = [subcommand.name for subcommand in ctx.command.commands]
        if subcommands:
            embed.add_field(
                name="Subcommands", value=", ".join(subcommands), inline=False
            )
        await ctx.reply(embed=embed)

    @lastfm.command(name="set")
    async def set_lastfm(self, ctx: Context, username: str) -> None:
        """Set your Last.fm username"""
        user_id = int(ctx.author.id)
        await self.bot.database.set_lastfm_username(user_id, username)
        await ctx.reply(
            embed=discord.Embed(
                title="Last.fm Username Set",
                description=f"Your Last.fm username has been set to `{username}`.",
            )
        )

    @lastfm.command(name="update")
    async def manual_update_index(self, ctx: Context):
        """Allow users to manually update their recent listening data index."""
        user_id = int(ctx.author.id)
        lastfm_username = await self.bot.database.get_lastfm_username(user_id)

        if not lastfm_username:
            await ctx.reply("You haven't set your Last.fm username.")
            return

        await self.update_user_index(lastfm_username)
        await ctx.reply("Your recent listening data has been updated.")

    @lastfm.command(name="color")
    async def set_color(self, ctx: Context, color: str) -> None:
        """Set your Last.fm embed color with HEX codes or standard color names."""
        color_bank = {
            "black": "#000000",
            "white": "#FFFFFF",
            "red": "#FF0000",
            "green": "#008000",
            "blue": "#0000FF",
            "yellow": "#FFFF00",
            "cyan": "#00FFFF",
            "magenta": "#FF00FF",
            "gray": "#808080",
            "purple": "#800080",
            "orange": "#FFA500",
            "pink": "#FFC0CB",
            "brown": "#A52A2A",
            "lime": "#00FF00",
            "navy": "#000080",
            "teal": "#008080",
            "maroon": "#800000",
            "olive": "#808000",
            "silver": "#C0C0C0",
            "gold": "#FFD700",
            "beige": "#F5F5DC",
            "coral": "#FF7F50",
            "indigo": "#4B0082",
            "violet": "#EE82EE",
        }

        if color.lower() in color_bank:
            color = color_bank[color.lower()]
        elif not (
            color.startswith("#")
            and len(color) == 7
            and all(c in "0123456789ABCDEFabcdef" for c in color[1:])
        ):
            await ctx.reply(
                embed=discord.Embed(
                    title="Error",
                    description="Please provide a valid color. Use HEX format (e.g., `#FF5733`) or a color name (e.g., `red`).",
                    color=0x36393E,
                )
            )
            return

        user_id = int(ctx.author.id)
        try:
            await self.bot.database.set_lastfm_embed_color(user_id, color)
            await ctx.reply(
                embed=discord.Embed(
                    title="Color Set",
                    description=f"Your embed color has been set to `{color}`.",
                    color=int(color.replace("#", "0x"), 16),
                )
            )
        except ValueError as e:
            await ctx.reply(
                embed=discord.Embed(
                    title="Error", description=str(e), color=discord.Color.red()
                )
            )

    @lastfm.command(name="votes")
    async def lastfm_stats(self, ctx: Context):
        """See your Last.fm vote statistics"""
        user_id = int(ctx.author.id)
        np_upvotes, np_downvotes = await self.bot.database.get_vote_stats(user_id, "np")
        snp_upvotes, snp_downvotes = await self.bot.database.get_vote_stats(
            user_id, "snp"
        )
        jnp_upvotes, jnp_downvotes = await self.bot.database.get_vote_stats(
            user_id, "jnp"
        )
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)

        np_ratio = (
            (np_upvotes - np_downvotes) / max(np_downvotes, 1)
            if np_downvotes >= np_downvotes
            else -(np_downvotes - np_downvotes) / max(np_downvotes, 1)
        )
        snp_ratio = (
            (snp_upvotes - snp_downvotes) / max(snp_downvotes, 1)
            if snp_upvotes >= snp_downvotes
            else -(snp_downvotes - snp_upvotes) / max(snp_upvotes, 1)
        )
        jnp_ratio = (
            (jnp_upvotes - jnp_downvotes) / max(jnp_downvotes, 1)
            if jnp_upvotes >= jnp_downvotes
            else -(jnp_downvotes - jnp_upvotes) / max(jnp_upvotes, 1)
        )

        description = (
            f"**Now Playing (np)**:\n"
            f"Upvotes: {np_upvotes}\nDownvotes: {np_downvotes}\nRatio: {np_ratio:.3g}\n\n"
            f"**Spotify Now Playing (snp)**:\n"
            f"Upvotes: {snp_upvotes}\nDownvotes: {snp_downvotes}\nRatio: {snp_ratio:.3g}\n\n"
            f"**JuiceWRLD Now Playing (jnp)**:\n"
            f"Upvotes: {jnp_upvotes}\nDownvotes: {jnp_downvotes}\nRatio: {jnp_ratio:.3g}"
        )

        await ctx.reply(
            embed=discord.Embed(
                title="Last.fm Vote Statistics",
                description=description,
                color=embed_color,
            )
        )

    @lastfm.command(name="plays")
    async def song_plays(self, ctx: Context):
        """Display the play count for the current song on Last.fm."""
        user_id = int(ctx.author.id)
        prefix = await self.bot.database.get_prefix(ctx.guild.id)
        try:
            lastfm_username = await self.bot.database.get_lastfm_username(user_id)
        except ValueError:
            lastfm_username = None
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)

        async with ctx.typing():
            if not lastfm_username:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description=f"You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.",
                        color=0x36393E,
                    )
                )
                return

        recent_tracks = await self.update_user_index(lastfm_username)
        if not recent_tracks:
            await ctx.reply("No recent tracks found.")
            return

        track = recent_tracks[0]
        track_name = track["name"]
        artist_name = track["artist"]["#text"]

        async def get_track_playcount(lastfm_username, artist_name, track_name):
            url = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    track_info = await response.json()
                    return track_info.get("track", {}).get("userplaycount", "N/A")

        playcount = await get_track_playcount(lastfm_username, artist_name, track_name)

        embed = discord.Embed(
            title="Current Song Plays",
            description=f"You've listened to **{track_name}** by **{artist_name}** {playcount} times.",
            color=embed_color,
        )
        await ctx.reply(embed=embed)

    @lastfm.command(name="toptentracks", aliases=["ttt"])
    async def top_tracks(self, ctx: Context):
        """Display the user's top ten tracks on Last.fm."""
        user_id = int(ctx.author.id)
        prefix = await self.bot.database.get_prefix(ctx.guild.id)
        try:
            lastfm_username = await self.bot.database.get_lastfm_username(user_id)
        except ValueError:
            lastfm_username = None
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)

        async with ctx.typing():
            if not lastfm_username:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description=f"You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.",
                        color=0x36393E,
                    )
                )
                return

        url = f"http://ws.audioscrobbler.com/2.0/?method=user.gettoptracks&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json&limit=10"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()

                if "toptracks" not in data or "track" not in data["toptracks"]:
                    await ctx.reply(
                        "Couldn't retrieve top tracks. Please try again later."
                    )
                    return

                tracks = data["toptracks"]["track"]
                description = "\n".join(
                    [
                        f"{i+1}. [{track['name']}]({track['url']}) - `{track['playcount']}` plays"
                        for i, track in enumerate(tracks)
                    ]
                )

                embed = discord.Embed(
                    title=f"{lastfm_username}'s Top 10 Tracks",
                    description=description,
                    color=embed_color,
                )
                embed.set_footer(text="Data from Last.fm")
                await ctx.reply(embed=embed)

    @lastfm.command(name="topartists", aliases=["tar"])
    async def top_artists(self, ctx: Context):
        """Display the user's top artists on Last.fm."""
        user_id = int(ctx.author.id)
        prefix = await self.bot.database.get_prefix(ctx.guild.id)
        try:
            lastfm_username = await self.bot.database.get_lastfm_username(user_id)
        except ValueError:
            lastfm_username = None
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)

        async with ctx.typing():
            if not lastfm_username:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description=f"You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.",
                        color=0x36393E,
                    )
                )
                return

            url = f"http://ws.audioscrobbler.com/2.0/?method=user.gettopartists&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json&limit=10"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    top_artists_data = await response.json()

                    artists = top_artists_data.get("topartists", {}).get("artist", [])
                    if not artists:
                        await ctx.reply(
                            "Couldn't retrieve top artists. Please try again later."
                        )
                        return

                    description = "\n".join(
                        [
                            f"{i+1}. [{artist['name']}](https://www.last.fm/music/{artist['name'].replace(' ', '+')}) - `{artist['playcount']}` plays"
                            for i, artist in enumerate(artists)
                        ]
                    )

                    embed = discord.Embed(
                        title=f"{lastfm_username}'s Top 10 Artists",
                        description=description,
                        color=embed_color,
                    )
                    embed.set_footer(text="Data from Last.fm")
                    await ctx.reply(embed=embed)

    @lastfm.command(name="whoknows", aliases=["wk"])
    async def who_knows(self, ctx: Context, *, artist_name: str):
        """Show who in the server has listened to the specified artist the most."""
        user_id = int(ctx.author.id)
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)
        listening_users = []

        async def fetch_playcount(lastfm_username):
            """Fetch user playcount for an artist from Last.fm."""
            url = f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={artist_name}&username={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()
                    return int(
                        data.get("artist", {}).get("stats", {}).get("userplaycount", 0)
                    )

        async def fetch_artist_image(lastfm_username):
            """Fetch user playcount for an artist from Last.fm."""
            url = f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={artist_name}&username={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()
                    return data["artist"]["image"][3]["#text"]

        tasks = []
        valid_members = []
        usernames = await self.bot.database.get_lastfm_usernames()

        for lastfm_username in usernames:
            if not lastfm_username:
                continue
            valid_members.append(lastfm_username)
            tasks.append(fetch_playcount(lastfm_username))

        results = await asyncio.gather(*tasks)

        listening_users = []
        for lfm_user, playcount in zip(valid_members, results):
            if playcount and playcount > 0:
                listening_users.append((lfm_user, playcount))

        listening_users.sort(key=lambda x: x[1], reverse=True)
        top_listening_users = listening_users[:10]

        if listening_users:
            description = "\n".join(
                [
                    f"**[{lfm_user}](https://www.last.fm/user/{lfm_user})**: `{playcount}`"
                    for lfm_user, playcount in top_listening_users
                ]
            )
        else:
            description = f"No one in the server has listened to **__{artist_name}__**."

        embed = discord.Embed(
            title=f"Who Knows {artist_name}?",
            description=description,
            color=embed_color,
        )
        embed.set_thumbnail(url=await fetch_artist_image(lastfm_username))
        embed.add_field(
            name="Your Playcount",
            value=f"`{await fetch_playcount(lastfm_username)}`",
            inline=False,
        )
        await ctx.reply(embed=embed)

    @commands.command(name="np")
    async def now_playing(self, ctx: Context) -> None:
        """View what you are currently playing on Last.fm"""
        user_id = int(ctx.author.id)
        prefix = await self.bot.database.get_prefix(ctx.guild.id)
        try:
            lastfm_username = await self.bot.database.get_lastfm_username(user_id)
        except ValueError:
            lastfm_username = None
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)
        async with ctx.typing():
            if not lastfm_username:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description=f"You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.",
                        color=0x36393E,
                    )
                )
                return

            async def get_user_data(lastfm_username: str):
                url = f"https://ws.audioscrobbler.com/2.0/?method=user.getinfo&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        data = await response.json()
                        return data

            async def get_image_url(data, size="medium"):
                images = data.get("user", {}).get("image", [])
                for image in images:
                    if image["size"] == size:
                        return image["#text"]
                return None

            async def get_recent_tracks(lastfm_username: str):
                url_recent = f"http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_recent) as response:
                        recent_tracks = await response.json()
                        return recent_tracks

            async def get_track_info(
                artist_name: str, track_name: str, lastfm_username: str
            ):
                url_info = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_info) as response:
                        recent_track_info = await response.json()

                        if "track" not in recent_track_info:
                            logger.error(
                                f"Last.fm API did not return track info: {recent_track_info}"
                            )
                            return None

                        return recent_track_info

            user_data = await get_user_data(lastfm_username)

            lastfm_avatar_url = await get_image_url(user_data, "large")

            recent_tracks = await get_recent_tracks(lastfm_username)
            if not recent_tracks["recenttracks"]["track"]:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description="No recent tracks found for the user.",
                        color=0x36393E,
                    )
                )
                return

            track = recent_tracks["recenttracks"]["track"][0]
            track_name = track["name"]
            artist_name = track["artist"]["#text"]
            album_name = track["album"]["#text"]
            track_url = track["url"]

            track_info = await get_track_info(artist_name, track_name, lastfm_username)

            if not track_info or "track" not in track_info:
                await ctx.reply(
                    embed=discord.Embed(
                        title="Error",
                        description="Could not retrieve track information from Last.fm.",
                        color=0x36393E,
                    )
                )
                return

            playcount = track_info["track"].get("userplaycount", "N/A")
            total_scrobbles = (
                user_data["user"]["playcount"]
                if "playcount" in user_data["user"]
                else "N/A"
            )

            embed = discord.Embed(
                title="Now Playing",
                color=embed_color,
            )

            if "image" in track and track["image"]:
                thumbnail_url = track["image"][-1]["#text"]
                embed.set_thumbnail(url=thumbnail_url)

            embed.add_field(
                name="Track:",
                value=f"[{track_name}]({track_url}) by {artist_name}",
                inline=False,
            )
            embed.add_field(name="Album:", value=f"{album_name}", inline=False)
            embed.set_footer(
                text=f"Playcount: {playcount} - Total Scrobbles: {total_scrobbles}"
            )
            embed.set_author(
                name=f"{lastfm_username} on Last.fm:",
                url=f"https://www.last.fm/user/{lastfm_username}",
                icon_url=lastfm_avatar_url,
            )

        message = await ctx.reply(embed=embed)
        await message.add_reaction("👍")
        await asyncio.sleep(0.5)  # Small delay to ensure reactions are added correctly
        await message.add_reaction("👎")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["👍", "👎"]

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=15.0, check=check
            )
            if str(reaction.emoji) == "👍":
                await self.bot.database.log_vote(user.id, "np", "up")
            elif str(reaction.emoji) == "👎":
                await self.bot.database.log_vote(user.id, "np", "down")
        except asyncio.TimeoutError:
            logger.debug("No reaction received within the timeout period.")
        except asyncio.CancelledError:
            logger.debug("Reaction task was cancelled.")

    def draw_glowing_text(
        self,
        base_image: Image.Image,
        text: str,
        position: tuple[int, int],
        font,
        text_color: str,
        glow_color: str,
        glow_radius: int = 8,
    ):
        """
        Draws text onto base_image with a glow effect,
        ensuring the *glyphs* start exactly at `position`, not the glow outline.
        """
        x, y = position

        glow_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)

        glow_draw.text((x, y), text, font=font, fill=glow_color)

        blurred = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        offset = glow_radius // 2

        base_image.paste(blurred, (-offset, -offset), blurred)

        draw = ImageDraw.Draw(base_image)
        draw.text((x, y), text, font=font, fill=text_color)

    def draw_glowing_rectangle(
        self,
        base_image,
        xy,
        radius,
        outline,
        fill,
        width=1,
        glow_color=None,
        glow_radius=8,
    ):
        """
        Draws a rounded rectangle with a glow effect onto base_image.
        xy = [x1, y1, x2, y2], the bounding box
        """

        glow_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)

        if glow_color:
            glow_draw.rounded_rectangle(
                xy, radius=radius, fill=glow_color, outline=glow_color
            )

        blurred_layer = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        base_image.alpha_composite(blurred_layer)

        final_draw = ImageDraw.Draw(base_image)
        final_draw.rounded_rectangle(
            xy, radius=radius, fill=fill, outline=outline, width=width
        )

    def dynamic_font(self, text, max_width, font_path, max_font_size):
        font_size = max_font_size
        font = ImageFont.truetype(font_path, font_size)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

        while draw.textbbox((0, 0), text, font=font)[2] > max_width and font_size > 8:
            font_size -= 1
            font = ImageFont.truetype(font_path, font_size)

        return font

    def glow_behind_image(
        self, base_image, cover_image, cover_mask, position, glow_color, glow_radius=10
    ):
        """
        Pastes a copy of cover_image's shape in glow_color behind it (blurred)
        to create a glow effect, then pastes the actual cover_image on top.

        :param base_image:  The main RGBA Image where you draw everything.
        :param cover_image: The (already rounded or cropped) cover image.
        :param cover_mask:  The same mask used to shape the cover (rounded corners, etc).
        :param position:    (x, y) coords where the cover should be placed.
        :param glow_color:  The color of the glow (e.g. "white").
        :param glow_radius: How “strong” or wide the glow should be (integer).
        """

        base_image = base_image.convert("RGBA")

        temp_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))

        glow_shape = Image.new("RGBA", cover_image.size, glow_color)
        temp_layer.paste(glow_shape, position, cover_mask)

        blurred_layer = temp_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        base_image.alpha_composite(blurred_layer)

        base_image.paste(cover_image, position, cover_mask)

        return base_image

    def create_vertical_gradient(self, width, height, color1, color2):
        """
        Returns a new Pillow Image (RGB) of size (width, height),
        with a vertical gradient from color1 (top) to color2 (bottom).
        """
        from PIL import Image, ImageDraw

        gradient = Image.new("RGB", (width, height), color=0)
        draw = ImageDraw.Draw(gradient)

        r1, g1, b1 = color1
        r2, g2, b2 = color2

        for y in range(height):
            ratio = y / float(height)
            r = int(r1 * (1 - ratio) + r2 * ratio)
            g = int(g1 * (1 - ratio) + g2 * ratio)
            b = int(b1 * (1 - ratio) + b2 * ratio)

            draw.line([(0, y), (width, y)], fill=(r, g, b))

        return gradient

    def get_text_color(self, background_color):
        r, g, b = background_color
        luminance = 0.299 * r + 0.587 * g + 0.114 * b

        return "black" if luminance > 150 else "white"

    async def find_spotify_activity(self, member, retries=3, delay=2):
        for _ in range(retries):
            activity = next(
                (
                    activity
                    for activity in member.activities
                    if isinstance(activity, discord.Spotify)
                ),
                None,
            )
            if activity:
                return activity
            await asyncio.sleep(delay)
        return None

    @commands.command(name="snp")
    async def spotify_now_playing(
        self, ctx: commands.Context, member: discord.Member = None
    ) -> None:
        """View what you are currently playing on Spotify."""
        member = member or ctx.author

        try:
            async with ctx.typing():
                activity = await self.find_spotify_activity(member)
                if activity is None:
                    await ctx.reply(
                        embed=discord.Embed(
                            title="Error",
                            description="No Spotify activity found.\n\nMake sure your Spotify is connected to Discord and you are sharing activity status in privacy settings.\n\n**Note:** This command only works if you are listening to a song that is on Spotify. __Not local files.__",
                            color=0x36393E,
                        )
                    )
                    return

                cover_url = activity.album_cover_url
                if cover_url:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(cover_url) as response:
                            cover_data = await response.read()

                    cover_image = Image.open(BytesIO(cover_data)).resize((200, 200))
                    color_thief = ColorThief(BytesIO(cover_data))

                    palette = color_thief.get_palette(color_count=2)
                    if len(palette) < 2:
                        palette = [color_thief.get_color(quality=10)] * 2
                    color1, color2 = palette[0], palette[1]

                    width, height = 800, 400
                    gradient_bg = self.create_vertical_gradient(
                        width, height, color1, color2
                    )
                    img = gradient_bg.convert("RGBA")
                else:
                    img = Image.new("RGBA", (800, 400), (30, 215, 96))

                if cover_url:
                    cover_image = Image.open(BytesIO(cover_data)).resize((200, 200))
                    rounded_cover = rounded_cover = Image.new(
                        "RGBA", cover_image.size, (0, 0, 0, 0)
                    )
                    mask = Image.new("L", cover_image.size, 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, *cover_image.size), radius=20, fill=255
                    )

                    text_color = self.get_text_color(color1)
                    glow_color = "white" if text_color == "black" else "black"
                    rounded_cover.paste(cover_image, (0, 0), mask)

                    img = self.glow_behind_image(
                        base_image=img,
                        cover_image=rounded_cover,
                        cover_mask=mask,
                        position=(50, 100),
                        glow_color=glow_color,
                        glow_radius=12,
                    )
                else:
                    text_color = "white"

                max_text_width = width - 350

                title_font = self.dynamic_font(
                    activity.title, max_text_width, self.font_path, max_font_size=40
                )
                artist_font = self.dynamic_font(
                    activity.artist, max_text_width, self.font_path, max_font_size=30
                )
                album_font = self.dynamic_font(
                    activity.album, max_text_width, self.font_path, max_font_size=24
                )
                img = img.convert("RGBA")
                glow_color = "white" if text_color == "black" else "black"
                text_glow_radius = 6

                self.draw_glowing_text(
                    base_image=img,
                    text=activity.title,
                    position=(300, 100),
                    font=title_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius,
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=activity.artist,
                    position=(300, 150),
                    font=artist_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius,
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=activity.album,
                    position=(300, 200),
                    font=album_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius,
                )

                total_duration = activity.duration.total_seconds()
                elapsed_duration = (
                    discord.utils.utcnow() - activity.start
                ).total_seconds()
                progress = (
                    min(1, elapsed_duration / total_duration)
                    if total_duration > 0
                    else 0
                )

                bar_x, bar_y, bar_width, bar_height = 300, 250, 400, 20
                rectangle_glow_radius = 8
                rectangle_radius = 10
                rectangle_width = 2

                self.draw_glowing_rectangle(
                    base_image=img,
                    xy=[bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
                    radius=rectangle_radius,
                    outline=text_color,
                    fill=None,
                    width=rectangle_width,
                    glow_color=text_color,
                    glow_radius=rectangle_glow_radius,
                )

                filled_width = int(bar_width * progress)
                if filled_width < 0:
                    filled_width = 0

                filled_x1 = bar_x + filled_width

                if filled_x1 > bar_x:
                    self.draw_glowing_rectangle(
                        base_image=img,
                        xy=[bar_x, bar_y, filled_x1, bar_y + bar_height],
                        radius=rectangle_radius,
                        outline=text_color,
                        fill=text_color,
                        width=rectangle_width,
                        glow_color=text_color,
                        glow_radius=rectangle_glow_radius,
                    )

                minutes_elapsed, seconds_elapsed = divmod(int(elapsed_duration), 60)
                minutes_total, seconds_total = divmod(int(total_duration), 60)
                duration_text = f"{minutes_elapsed}:{seconds_elapsed:02} / {minutes_total}:{seconds_total:02}"
                time_font = self.dynamic_font(
                    duration_text, max_text_width, self.font_path, max_font_size=20
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=duration_text,
                    position=(300, 280),
                    font=time_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius,
                )

                async def get_track_playcount(lastfm_username, artist_name, track_name):
                    url = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as response:
                            track_info = await response.json()
                            return track_info.get("track", {}).get(
                                "userplaycount", "N/A"
                            )

                user_id = int(member.id)
                try:
                    lastfm_username = await self.bot.database.get_lastfm_username(
                        user_id
                    )

                    if lastfm_username:
                        playcount = await get_track_playcount(
                            lastfm_username, activity.artist, activity.title
                        )
                    else:
                        playcount = "N/A"
                except ValueError:
                    playcount = "N/A"

                playcount_text = f"Plays: {playcount}"
                playcount_font = self.dynamic_font(
                    playcount_text, max_text_width, self.font_path, max_font_size=20
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=playcount_text,
                    position=(300, 320),
                    font=playcount_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius,
                )

                image_bytes = BytesIO()
                img.save(image_bytes, format="PNG")
                image_bytes.seek(0)
                file = discord.File(fp=image_bytes, filename="now_playing.png")
                embed = discord.Embed(
                    title="Now Playing on Spotify",
                    description=f"[{activity.title}]({activity.track_url})",
                    color=discord.Color.green(),
                )
                embed.set_image(url="attachment://now_playing.png")
            message = await ctx.reply(embed=embed, file=file)
            await message.add_reaction("👍")
            await asyncio.sleep(
                0.5
            )  # Small delay to ensure reactions are added correctly
            await message.add_reaction("👎")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["👍", "👎"]

            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add", timeout=15.0, check=check
                )
                if str(reaction.emoji) == "👍":
                    await self.bot.database.log_vote(user.id, "np", "up")
                elif str(reaction.emoji) == "👎":
                    await self.bot.database.log_vote(user.id, "np", "down")
            except asyncio.TimeoutError:
                logger.debug("No reaction received within the timeout period.")
            except asyncio.CancelledError:
                logger.debug("Reaction task was cancelled.")

        except aiohttp.ClientError:
            await ctx.reply(
                embed=discord.Embed(
                    title="Error",
                    description="There was an error fetching the cover art for the currently playing song. This should not happen. Please try again later.",
                    color=0x36393E,
                )
            )

    @commands.command(name="jnp")
    async def juicewrld_now_playing(
        self, ctx: commands.Context, member: discord.Member = None
    ) -> None:
        """View what you are currently playing on JuiceWRLD API desktop app."""
        member = member or ctx.author

        try:
            async with ctx.typing():
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://m.juicewrldapi.com/analytics/now-playing/discord",
                        params={"discord_user_id": member.id},
                    ) as response:
                        if response.status == 404:
                            try:
                                error_data = await response.json()
                                if not error_data.get("is_linked"):
                                    await ctx.reply(
                                        embed=discord.Embed(
                                            title="Account Not Linked",
                                            description=f"{member.mention}'s Discord account is not linked to JuiceWRLD API.\n\n**To link your account:**\n1. Open the JuiceWRLD API desktop app\n2. Go to account and generate a device pairing code\n3. Then use the jlink command with your code!",
                                            color=0x36393E,
                                        )
                                    )
                                    return
                            except:
                                pass

                        if response.status != 200:
                            await ctx.reply(
                                embed=discord.Embed(
                                    title="Error",
                                    description="Failed to fetch now playing data from JuiceWRLD API.",
                                    color=0x36393E,
                                )
                            )
                            return

                        data = await response.json()

                        if not data.get("is_linked"):
                            await ctx.reply(
                                embed=discord.Embed(
                                    title="Account Not Linked",
                                    description=f"🚫 {member.mention}'s Discord account is not linked to JuiceWRLD API.\n\n**To link your account:**\n1. Open the JuiceWRLD API desktop app\n2. Go to settings and connect your Discord account\n3. Then try this command again!",
                                    color=0x36393E,
                                )
                            )
                            return

                        now_playing = data.get("now_playing")
                        if not now_playing or not now_playing.get("is_playing"):
                            await ctx.reply(
                                embed=discord.Embed(
                                    title="Now Playing",
                                    description="No music currently playing.\n\nStart playing music on your desktop app to see it here!",
                                    color=0x808080,
                                )
                            )
                            return

                        title = now_playing.get("track") or now_playing.get(
                            "title", "Unknown Title"
                        )
                        artist = now_playing.get("artist", "Unknown Artist")
                        album = now_playing.get("album", "Unknown Album")
                        cover_url = (
                            now_playing.get("album_art_url")
                            or now_playing.get("cover_url")
                            or now_playing.get("image_url")
                        )
                        track_url = now_playing.get("track_url") or now_playing.get(
                            "url", ""
                        )
                        duration_raw = now_playing.get("duration", 0)
                        duration_as_seconds = duration_raw
                        duration_as_milliseconds = duration_raw / 1000.0
                        if duration_raw > 600 and duration_as_milliseconds <= 600:
                            duration_seconds = duration_as_milliseconds
                        else:
                            duration_seconds = duration_as_seconds
                        start_time_str = now_playing.get(
                            "timestamp"
                        ) or now_playing.get("start_time")
                        position = now_playing.get("position", 0)

                        if cover_url:
                            async with aiohttp.ClientSession() as cover_session:
                                async with cover_session.get(
                                    cover_url
                                ) as cover_response:
                                    cover_data = await cover_response.read()

                            cover_image = Image.open(BytesIO(cover_data)).resize(
                                (200, 200)
                            )
                            color_thief = ColorThief(BytesIO(cover_data))

                            palette = color_thief.get_palette(color_count=2)
                            if len(palette) < 2:
                                palette = [color_thief.get_color(quality=10)] * 2
                            color1, color2 = palette[0], palette[1]

                            width, height = 800, 400
                            gradient_bg = self.create_vertical_gradient(
                                width, height, color1, color2
                            )
                            img = gradient_bg.convert("RGBA")
                        else:
                            width, height = 800, 400
                            img = Image.new("RGBA", (800, 400), (30, 215, 96))
                            color1 = (30, 215, 96)

                        if cover_url:
                            cover_image = Image.open(BytesIO(cover_data)).resize(
                                (200, 200)
                            )
                            rounded_cover = Image.new(
                                "RGBA", cover_image.size, (0, 0, 0, 0)
                            )
                            mask = Image.new("L", cover_image.size, 0)
                            ImageDraw.Draw(mask).rounded_rectangle(
                                (0, 0, *cover_image.size), radius=20, fill=255
                            )

                            text_color = self.get_text_color(color1)
                            glow_color = "white" if text_color == "black" else "black"
                            rounded_cover.paste(cover_image, (0, 0), mask)

                            img = self.glow_behind_image(
                                base_image=img,
                                cover_image=rounded_cover,
                                cover_mask=mask,
                                position=(50, 100),
                                glow_color=glow_color,
                                glow_radius=12,
                            )
                        else:
                            text_color = "white"

                        max_text_width = width - 350

                        title_font = self.dynamic_font(
                            title, max_text_width, self.font_path, max_font_size=40
                        )
                        artist_font = self.dynamic_font(
                            artist, max_text_width, self.font_path, max_font_size=30
                        )
                        album_font = self.dynamic_font(
                            album, max_text_width, self.font_path, max_font_size=24
                        )
                        img = img.convert("RGBA")
                        glow_color = "white" if text_color == "black" else "black"
                        text_glow_radius = 6

                        self.draw_glowing_text(
                            base_image=img,
                            text=title,
                            position=(300, 100),
                            font=title_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius,
                        )
                        self.draw_glowing_text(
                            base_image=img,
                            text=artist,
                            position=(300, 150),
                            font=artist_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius,
                        )
                        self.draw_glowing_text(
                            base_image=img,
                            text=album,
                            position=(300, 200),
                            font=album_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius,
                        )

                        elapsed_duration = 0
                        if duration_seconds > 0:
                            if position > 0:
                                elapsed_duration = position / 1000.0
                            elif start_time_str:
                                try:
                                    start_time = datetime.fromisoformat(
                                        start_time_str.replace("Z", "+00:00")
                                    )
                                    if start_time.tzinfo is None:
                                        start_time = start_time.replace(
                                            tzinfo=timezone.utc
                                        )
                                    elapsed_duration = (
                                        discord.utils.utcnow() - start_time
                                    ).total_seconds()
                                except:
                                    elapsed_duration = 0
                            progress = (
                                min(1, elapsed_duration / duration_seconds)
                                if duration_seconds > 0
                                else 0
                            )
                        else:
                            progress = 0

                        bar_x, bar_y, bar_width, bar_height = 300, 250, 400, 20
                        rectangle_glow_radius = 8
                        rectangle_radius = 10
                        rectangle_width = 2

                        self.draw_glowing_rectangle(
                            base_image=img,
                            xy=[bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
                            radius=rectangle_radius,
                            outline=text_color,
                            fill=None,
                            width=rectangle_width,
                            glow_color=text_color,
                            glow_radius=rectangle_glow_radius,
                        )

                        filled_width = int(bar_width * progress)
                        if filled_width < 0:
                            filled_width = 0

                        filled_x1 = bar_x + filled_width

                        if filled_x1 > bar_x:
                            self.draw_glowing_rectangle(
                                base_image=img,
                                xy=[bar_x, bar_y, filled_x1, bar_y + bar_height],
                                radius=rectangle_radius,
                                outline=text_color,
                                fill=text_color,
                                width=rectangle_width,
                                glow_color=text_color,
                                glow_radius=rectangle_glow_radius,
                            )

                        if duration_seconds > 0:
                            minutes_elapsed, seconds_elapsed = divmod(
                                int(elapsed_duration), 60
                            )
                            minutes_total, seconds_total = divmod(
                                int(duration_seconds), 60
                            )
                            duration_text = f"{minutes_elapsed}:{seconds_elapsed:02} / {minutes_total}:{seconds_total:02}"
                        else:
                            duration_text = "0:00 / 0:00"

                        time_font = self.dynamic_font(
                            duration_text,
                            max_text_width,
                            self.font_path,
                            max_font_size=20,
                        )
                        self.draw_glowing_text(
                            base_image=img,
                            text=duration_text,
                            position=(300, 280),
                            font=time_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius,
                        )

                        image_bytes = BytesIO()
                        img.save(image_bytes, format="PNG")
                        image_bytes.seek(0)
                        file = discord.File(fp=image_bytes, filename="now_playing.png")
                        embed_color = int(
                            "{:02x}{:02x}{:02x}".format(
                                color1[0], color1[1], color1[2]
                            ),
                            16,
                        )
                        embed = discord.Embed(
                            title="Now Playing on JuiceWRLD API",
                            description=f"[{title}]({track_url})"
                            if track_url
                            else title,
                            color=embed_color,
                        )
                        embed.set_image(url="attachment://now_playing.png")

            message = await ctx.reply(embed=embed, file=file)
            await message.add_reaction("👍")
            await asyncio.sleep(0.5)
            await message.add_reaction("👎")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["👍", "👎"]

            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add", timeout=15.0, check=check
                )
                if str(reaction.emoji) == "👍":
                    await self.bot.database.log_vote(user.id, "jnp", "up")
                elif str(reaction.emoji) == "👎":
                    await self.bot.database.log_vote(user.id, "jnp", "down")
            except asyncio.TimeoutError:
                logger.debug("No reaction received within the timeout period.")
            except asyncio.CancelledError:
                logger.debug("Reaction task was cancelled.")

        except aiohttp.ClientError:
            await ctx.reply(
                embed=discord.Embed(
                    title="Error",
                    description="There was an error fetching data from JuiceWRLD API. Please try again later.",
                    color=0x36393E,
                )
            )
        except Exception as e:
            logger.error(f"Error in jnp command: {e}")
            await ctx.reply(
                embed=discord.Embed(
                    title="Error",
                    description="An unexpected error occurred. Please try again later.",
                    color=0x36393E,
                )
            )

    @commands.command(name="jlink")
    async def juicewrld_link(self, ctx: commands.Context, code: str = None) -> None:
        """Link your Discord account to JuiceWRLD API using a pairing code."""
        prefix = await self.bot.database.get_prefix(ctx.guild.id) if ctx.guild else "!"

        if code and code.lower() == "help":
            embed = discord.Embed(
                title="Link Account Command",
                description="Link your Discord account to your JuiceWRLDAPI account using a pairing code",
                color=0x5865F2,
            )
            embed.add_field(
                name="Usage",
                value=f"`{prefix}jlink <code>` - Link account with pairing code",
                inline=False,
            )
            embed.add_field(
                name="Examples",
                value=f"`{prefix}jlink ABC12345` - Link using code ABC12345",
                inline=False,
            )
            embed.add_field(
                name="How to get a code",
                value="Generate a pairing code from your JuiceWRLDAPI desktop app\nCodes expire after 10 minutes",
                inline=False,
            )
            await ctx.reply(embed=embed)
            return

        if not code:
            await ctx.reply(
                embed=discord.Embed(
                    title="Missing Code",
                    description="Please provide a pairing code.",
                    color=discord.Color.red(),
                )
            )
            return

        pairing_code = code.upper()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://m.juicewrldapi.com/auth/discord/bot-link",
                    json={"code": pairing_code, "discord_user_id": ctx.author.id},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and data.get("user"):
                            user_data = data["user"]
                            embed = discord.Embed(
                                title="Account Linked Successfully",
                                description=f'Your Discord account has been linked to **{user_data.get("username", "Unknown")}**',
                                color=0x00FF00,
                            )
                            embed.add_field(
                                name="Username",
                                value=user_data.get("username", "Unknown"),
                                inline=True,
                            )
                            embed.add_field(
                                name="Status", value="Active ✓", inline=True
                            )
                            embed.set_footer(
                                text=f"You can now use {prefix}jnp to show your currently playing song!"
                            )
                            await ctx.reply(embed=embed)
                            return

                    error_message = "Invalid or expired pairing code"
                    try:
                        error_data = await response.json()
                        if error_data and error_data.get("error"):
                            error_message = error_data["error"]
                    except:
                        if response.status == 400:
                            error_message = "Bad request. Please check your pairing code and try again."
                        elif response.status >= 500:
                            error_message = "Server error. Please try again later."

                    embed = discord.Embed(
                        title="Link Failed",
                        description=error_message,
                        color=discord.Color.red(),
                    )
                    embed.add_field(
                        name="Troubleshooting",
                        value="• Make sure the code is correct\n• Codes expire after 10 minutes\n• Generate a new code from your desktop app",
                        inline=False,
                    )
                    await ctx.reply(embed=embed)

        except aiohttp.ClientError:
            await ctx.reply(
                embed=discord.Embed(
                    title="Link Failed",
                    description="There was an error connecting to JuiceWRLD API. Please try again later.",
                    color=discord.Color.red(),
                )
            )
        except Exception as e:
            logger.error(f"Error in jlink command: {e}")
            await ctx.reply(
                embed=discord.Embed(
                    title="Link Failed",
                    description="An unexpected error occurred. Please try again later.",
                    color=discord.Color.red(),
                )
            )

    # start of my beautiful commands

    async def fetch_song(self, ctx: commands.Context, query: str):
        query = query.replace('’', "'")
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/songs/", params={"search": query}
        ) as response:
            if response.status != 200:
                await ctx.reply(
                    embed=discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red(),
                    ).set_image(url=f"https://http.cat/{response.status}"),
                    delete_after=5,
                )
                return None

            data = await response.json()

        song_list = [
            song
            for song in data.get("results", [])
            if "session" not in song.get("leak_type").lower()
        ]

        return song_list

    async def fetch_session(self, ctx: commands.Context, query: str):
        query = query.replace('’', "'")
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/songs/", params={"search": query}
        ) as response:
            if response.status != 200:
                await ctx.reply(
                    embed=discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red(),
                    ).set_image(url=f"https://http.cat/{response.status}"),
                    delete_after=5,
                )
                return None

            data = await response.json()

        song_list = [
            song
            for song in data.get("results", [])
            if "session" in song.get("leak_type").lower()
        ]

        return song_list

    async def fetch_snippet(self, name: str):
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/files/browse/", params={"path": f'Snippets/{name}'}
        ) as response:
            if response.status != 200:
                return None

            data = await response.json()

        valid_snippets = []

        for item in data.get("items", []):
            if item.get("type") != "file":
                continue

            mime = item.get("mime_type", "")

            if mime and mime.startswith("video/"):
                path = item.get("path")
                
                fixed_path = quote(path, safe="/") # lowkey wanted to use envys special_url_encode
                valid_snippets.append(
                    "https://juicewrldapi.com/juicewrld/files/download/?path="
                    + fixed_path
                )

        return valid_snippets

    def get_file_names(self, file_names: str) -> list[str]:
        lines = [line.strip() for line in file_names.splitlines() if line.strip()]

        if not lines or "N/A" in lines:
            return []

        if len(lines) == 1 and ":" not in lines[0]:
            return [lines[0]]

        return [
            line.split(":", 1)[1].strip()
            for line in lines
            if ":" in line
            and any(x in line for x in ("File Name", "Clean", "Explicit"))
        ]
    
    async def get_downloads(self, file_names: list[str], length: str) -> list[str]:
        target_seconds = self.duration_to_seconds(length)
        if target_seconds == 0:
            return []

        results = []

        for file_name in file_names:
            async with self.session.get(JUICEWRLD_API + "/juicewrld/files/browse/", params={"search": file_name + '.'}) as response:
                if response.status != 200:
                    continue

                data = await response.json()

            results.extend(
                song["path"]
                for song in data.get("items", [])
                if abs(self.duration_to_seconds(song.get("duration")) - target_seconds) <= 1
                and "Original Files" in song.get("path", "")
            )

        return results

    def duration_to_seconds(self, duration: str):
        try:
            m, s = duration.split(":")
            return int(m) * 60 + int(s)
        except Exception:
            return 0
        
    def parse_dates(self, text: str) -> datetime | None:
        if not text:
            return None
        
        match = re.search(r'([A-Za-z]+ \d{1,2}, \d{4})', text)
        if not match:
            return None

        try:
            dt = datetime.strptime(match.group(1), '%B %d, %Y')
            dt = dt.replace(hour=14, minute=0, second=0, tzinfo=ZoneInfo("America/New_York"))
            return dt
        except ValueError:
            return None

    async def create_song_view(self, song_data: dict, random_leak: bool = False):
        class SongContainer(discord.ui.Container):
            ALBUMS = {
                'jute':                 {'name': 'JUICED UP THE EP', 'color': '#FFE602'},
                'LND':                  {'name': 'Legends Never Die', 'color': '#F700FF'},
                'afflictions':          {'name': 'affliction', 'color': '#000000'},
                'bdm':                  {'name': 'BINGEDRINKINGMUSIC', 'color': '#000000'},
                'HIH 999':              {'name': 'Heartbroken In Hollywood 9 9 9', 'color': '#FF653E'},
                'jw 999':               {'name': 'JuiceWRLD 9 9 9', 'color': '#FF2C2C'},
                'ND':                   {'name': 'NOTHINGS DIFFERENT </3', 'color': '#FF8800'},
                'GB&GR':                {'name': 'Goodbye & Good Riddance', 'color': '#008CFF'},
                'GB&GR (AE)':           {'name': 'Goodbye & Good Riddance (Anniversary Edition)', 'color': '#008CFF'},
                'GB&GR (5YAE)':         {'name': 'Goodbye & Good Riddance (5 Year Anniversary Edition)', 'color': '#008CFF'},
                'WOD':                  {'name': 'WRLD ON DRUGS', 'color': '#00FF94'},
                'DRFL':                 {'name': 'Death Race For Love', 'color': '#FF9900'},
                'DRFL (BTV)':           {'name': 'Death Race For Love (Bonus Track Version)', 'color': '#FF9900'},
                'OUT':                  {'name': 'Outsiders', 'color': '#2B2B2B'},
                'POST':                 {'name': 'Posthumous', 'color': '#00CCFF'},
                'TPP':                  {'name': 'The Pre-Party', 'color': '#EA00FF'},
                'TPP (EE)':             {'name': 'The Pre-Party (Extended Edition)', 'color': '#EA00FF'},
                'FD':                   {'name': 'Fighting Demons', 'color': '#2E2E2E'},
                'FD (CE)':              {'name': 'Fighting Demons (Complete Edition)', 'color': '#2E2E2E'},
                'FD (EE)':              {'name': 'Fighting Demons (Extended Edition)', 'color': '#2E2E2E'},
                'FD (DDE)':             {'name': 'Fighting Demons (Digital Deluxe Edition)', 'color': '#2E2E2E'},
                'TPNE':                 {'name': 'The Party Never Ends', 'color': '#CC00FF'},
            }

            FIELDS = {
                'file_names':           '**File Name**',
                'session_titles':       '**Session Title**',
                'session_tracking':     '**Session Tracking**',
                'instrumentals':        '**Instrumentals**',
                'recording_locations':  '**Recording Location**',
                'record_dates':         '**Recorded**',
                'preview_date':         '**Previewed**',
                'date_leaked':          '**Surfaced**',
                'release_date':         '**Released**',
                'length':               '**Length**',
                'leak_type':            '**Category**',
                'bitrate':              '**Available Files**',
            }

            RANDOM_LEAK_FIELDS = {
                'record_dates':         '**Recorded**',
                'preview_date':         '**Previewed**',
                'date_leaked':          '**Surfaced**',
                'release_date':         '**Released**',
                'length':               '**Length**',
                'bitrate':              '**Available Files**',
            }

            def __init__(
                self, 
                song: dict, 
                downloads = None, 
                random_leak: bool = False, 
            ):
                name = song.get('name')
                track_titles = [t for t in song.get('track_titles', []) if t != name]
                producers = song.get('producers')
                engineers = song.get('engineers')
                era_name = song.get('era', {}).get('name', 'N/A')
                _image_url = song.get('image_url')
                image_url = f'https://juicewrldapi.com{_image_url}' if _image_url != '' else 'https://discord.com/example.png'

                album = self.ALBUMS.get(era_name)
                accent_color = int(album['color'].lstrip('#'), 16) if album else 0x2B2D31
                
                super().__init__(accent_color=accent_color)

                self._build_container(
                    song, downloads, random_leak, 
                    name, track_titles, producers, engineers, 
                    era_name, album, image_url
                )

            def _build_container(
                self, song, downloads, random_leak, name, track_titles,
                producers, engineers, era_name, album, image_url
            ):
                thumbnail = discord.ui.Section(
                    accessory=discord.ui.Thumbnail(media=image_url)
                )
                thumbnail.add_item(
                    discord.ui.TextDisplay(
                        f'### {name}\n'
                        f'-# Alt Name(s): **{', '.join(track_titles) if track_titles else 'N/A'}**\n'
                        f'-# Engineer(s): **{engineers}**\n'
                        f'-# Producer(s): **{producers}**'
                    )
                )
                self.add_item(thumbnail)
                self.add_item(discord.ui.Separator())

                if era_name != '':
                    self.add_item(
                        discord.ui.TextDisplay(
                            f'**Era**\n{album['name'] if album else era_name}\n'
                        )
                    )

                field_map = self.FIELDS if not random_leak else self.RANDOM_LEAK_FIELDS
                self._add_song_fields(song, field_map)

                MAIN_URL = 'https://juicewrldapi.com/juicewrld/files/download/?path='
                TRACKER = discord.ui.Button(
                    label='Tracker', 
                    emoji='<:fart:1445127619744890911>', 
                    url='https://juicewrldapi.com/'
                )

                rows = self._create_download_rows(downloads, song, MAIN_URL)
                
                rows.append(discord.ui.Separator())
                rows.append(discord.ui.ActionRow())
                rows[-1].add_item(TRACKER)
                
                for row in rows:
                    self.add_item(row)

            def _add_song_fields(self, song: dict, field_map: dict):
                for field_key, field_label in field_map.items():
                    value = song.get(field_key)
                    if value:
                        value = (
                            value
                            .replace('Recorded', '')
                            .replace('First Previewed', '')
                            .replace('Surfaced', '')
                            .replace('Released', '')
                            .strip()
                        )
                        if value in ('N/A', 'Unavailable'):
                            continue

                        self.add_item(discord.ui.TextDisplay(f'{field_label}\n{value}'))

            def _create_download_rows(self, downloads, song, main_url):
                rows = []

                download = song.get('path')
                if download != '':
                    rows.append(discord.ui.Separator())
                    
                    ext = download.rsplit('.', 1)[-1].upper()
                    if ext.lower() in ['zip', 'rar', '7z']:
                        rows.append(discord.ui.TextDisplay('**Session Download(s)**'))
                    else:
                        rows.append(discord.ui.TextDisplay('**Tagged File(s)**'))
                    main_row = discord.ui.ActionRow()
                    main_row.add_item(
                        discord.ui.Button(
                            label=ext,
                            url=main_url + quote(download)
                        )
                    )
                    rows.append(main_row)

                if downloads:
                    rows.append(discord.ui.TextDisplay('**Original File(s)**'))
                    for i in range(0, len(downloads), 5):
                        row = discord.ui.ActionRow()
                        for path in downloads[i:i + 5]:
                            ext = path.rsplit('.', 1)[-1].upper()
                            label = f'OG {ext}'
                            row.add_item(
                                discord.ui.Button(
                                    label=label,
                                    url=main_url + quote(path)
                                )
                            )
                        rows.append(row)

                return rows

        file_names = self.get_file_names(song_data.get('file_names'))
        downloads = await self.get_downloads(file_names, song_data.get('length', ''))

        layout_view = discord.ui.LayoutView(timeout=None)
        layout_view.add_item(SongContainer(song_data, downloads, random_leak))

        return layout_view

    async def create_snippet_view(self, song: dict):
        class Container(discord.ui.Container):
            def __init__(self, song: dict, valid_snippets: dict):
                super().__init__(accent_color=0x2B2D31)

                name = song.get("name")
                engineers = song.get("engineers")
                producers = song.get("producers")

                alt_names = [t for t in song.get("track_titles", []) if t != name]

                image_url = song.get("image_url")

                thumb = discord.ui.Section(
                    accessory=discord.ui.Thumbnail(media=JUICEWRLD_API + image_url)
                )
                thumb.add_item(
                    discord.ui.TextDisplay(
                        f"### {name}\n-# Alt Name(s): **{', '.join(alt_names) if alt_names else 'N/A'}**\n-# Engineer(s): **{engineers}**\n-# Producer(s): **{producers}**"
                    )
                )

                self.add_item(thumb)
                self.add_item(discord.ui.Separator())

                media_gallery = discord.ui.MediaGallery()
                amt = 0
                if valid_snippets:
                    for url in valid_snippets:
                        if amt >= 10:
                            break

                        media_gallery.add_item(media=url)
                        amt += 1

                self.add_item(media_gallery)

        name = song.get("name")

        valid_snippets = await self.fetch_snippet(name)

        layout_view = discord.ui.LayoutView(timeout=None)
        layout_view.add_item(Container(song, valid_snippets))

        return layout_view if valid_snippets else None

    @commands.command('groupbuy', aliases=['gb', 'gbinfo', 'groupbuyinfo'], help='Find a songs groupbuy information')
    async def groupbuy(self, ctx: commands.Context, *, query: str):
        songs = await self.fetch_song(ctx, query)
        if songs is None:
            return

        if len(songs) == 1:
            async with ctx.typing():
                song = songs[0]

                if song['groupbuy_info']['price'] == '':
                    return await Embeds.send_warning_embed(ctx.channel, ctx.author, f'**{song['name']}** has no **groupbuy** information')

                layout_view = discord.ui.LayoutView()
                layout_view.add_item(GroupbuyContainer(self, song))

                msg = await ctx.send(view=layout_view)
                layout_view.message = msg

        elif len(songs) > 1:
            sorted_songs = sorted(songs, key=lambda s: s.get('track_titles'))[:25]
            song_map = {str(song['id']): song for song in sorted_songs}

            options = [
                discord.SelectOption(label=( lambda t: f'{t[0]} ({', '.join(t[1:])})' if len(t) > 1 else t[0])(song.get('track_titles'))[:100], value=str(song['id']))
                for song in sorted_songs
            ]

            view = discord.ui.View(timeout=None)
            view.add_item(GroupbuySongSelect(self, ctx.author, options, song_map))

            embed = discord.Embed(description=f'{ctx.author.mention}: Multiple **selections** found with your **search**')
            await ctx.reply(embed=embed, view=view)
        
        else:
            return await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f'I couldnt find a song with the name: `{query}`',
            )

    @commands.command("leak", description="Search for a Juice WRLD leak by name")
    async def leak(self, ctx: commands.Context, *, query: str):
        song_list = await self.fetch_song(ctx, query)
        if song_list is None:
            return

        if len(song_list) == 1:
            layout_view = await self.create_song_view(song_list[0])
            await ctx.reply(view=layout_view)

        elif len(song_list) > 1:
            results = sorted(song_list, key=lambda s: s.get("track_titles"))[:25]
            song_map = {str(song["id"]): song for song in results}

            options = [
                discord.SelectOption(
                    label=((
                        lambda t: f"{t[0]} ({', '.join(t[1:])})" if len(t) > 1 else t[0]
                    )(song.get("track_titles"))[:100]) if len(song.get("track_titles", [])) > 0 else song.get("name", "Unknown"),
                    value=str(song["id"]),
                )
                for song in results
            ]

            class SongSelect(discord.ui.Select):
                def __init__(self, options, song_map, author, cog):
                    self.song_map = song_map
                    self.author = author
                    self.cog = cog
                    super().__init__(
                        placeholder="Select a song...",
                        min_values=1,
                        max_values=1,
                        options=options,
                    )

                async def callback(self, itn: discord.Interaction):
                    song_id = self.values[0]
                    song = self.song_map[song_id]

                    view = await self.cog.create_song_view(song)

                    if self.author != itn.user:
                        return await itn.response.send_message(
                            view=view, ephemeral=True
                        )

                    await itn.response.edit_message(embed=None, view=view)

            view = discord.ui.View(timeout=None)
            view.add_item(SongSelect(options, song_map, ctx.author, self))

            embed = discord.Embed(
                description=f"{ctx.author.mention}: Multiple **selections** found with your **search**"
            )
            await ctx.reply(embed=embed, view=view)

        else:
            return await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f"I couldnt find a song with the name: `{query}`",
            )

    @commands.command("session", description="Search for a Juice WRLD session by name")
    async def session(self, ctx: commands.Context, *, query: str):
        song_list = await self.fetch_session(ctx, query)
        if song_list is None:
            return

        if len(song_list) == 1:
            layout_view = await self.create_song_view(song_list[0])
            await ctx.reply(view=layout_view)

        elif len(song_list) > 1:
            results = sorted(song_list, key=lambda s: s.get("track_titles"))[:25]
            song_map = {str(song["id"]): song for song in results}

            options = [
                discord.SelectOption(
                    label=(
                        lambda t: f"{t[0]} ({', '.join(t[1:])})" if len(t) > 1 else t[0]
                    )(song.get("track_titles"))[:100],
                    value=str(song["id"]),
                )
                for song in results
            ]

            class SongSelect(discord.ui.Select):
                def __init__(self, options, song_map, author, cog):
                    self.song_map = song_map
                    self.author = author
                    self.cog = cog
                    super().__init__(
                        placeholder="Select a song...",
                        min_values=1,
                        max_values=1,
                        options=options,
                    )

                async def callback(self, itn: discord.Interaction):
                    song_id = self.values[0]
                    song = self.song_map[song_id]

                    view = await self.cog.create_song_view(song)

                    if self.author != itn.user:
                        return await itn.response.send_message(
                            view=view, ephemeral=True
                        )

                    await itn.response.edit_message(embed=None, view=view)

            view = discord.ui.View(timeout=None)
            view.add_item(SongSelect(options, song_map, ctx.author, self))

            embed = discord.Embed(
                description=f"{ctx.author.mention}: Multiple **selections** found with your **search**"
            )
            await ctx.reply(embed=embed, view=view)

        else:
            return await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f"I couldnt find a song with the name: `{query}`",
            )

    @tasks.loop(hours=1)
    async def cache_songs(self):
        songs = Cache.get_songs()
        if songs is not None:
            await self.store_latest_surfaces()
            await self.sync_names()

    async def store_latest_surfaces(self, days: int = 30):
        cutoff = discord.utils.utcnow() - timedelta(days=days)

        hydrated = []

        songs = Cache.get_songs()
        for song in songs:
            dt = self.parse_dates(song.get('date_leaked', ''))
            if not dt:
                continue

            if dt.astimezone(timezone.utc) < cutoff:
                continue

            file_names = self.get_file_names(song['file_names'])
            downloads = await self.get_downloads(file_names, song['length'])
            _downloads = song['path']

            if _downloads and not downloads:
                downloads = [_downloads]

            song['date_leaked_dt'] = dt
            song['downloads'] = downloads

            hydrated.append(song)

        self.latest_surfaces = sorted(hydrated, key=lambda s: s['date_leaked_dt'], reverse=True)
    
    async def build_song_items(self, container: discord.ui.Container, songs: list[dict]):
        MAIN_URL = 'https://juicewrldapi.com/juicewrld/files/download/?path='

        for ii, song in enumerate(songs):
            name = song.get('name', 'N/A')
            engineers = song.get('engineers', 'N/A')
            producers = song.get('producers', 'N/A')
            track_titles = [t for t in song.get('track_titles', []) if t != name]
            image_url = song.get('image_url')
            era_name = song.get('era', {}).get('name', 'N/A')
            album = self.ALBUMS.get(era_name)

            timestamp = f'<t:{int(song['date_leaked_dt'].timestamp())}:R>'

            section = discord.ui.Section(
                accessory=discord.ui.Thumbnail(media=JUICEWRLD_API + image_url)
            )
            section.add_item(discord.ui.TextDisplay(
                f'**{name}** - {timestamp}\n'
                f'-# Era: **{album['name'] if album else era_name}**\n'
                f'-# Alt Name(s): **{', '.join(track_titles) if track_titles else 'N/A'}**\n'
                f'-# Engineer(s): **{engineers}**\n'
                f'-# Producer(s): **{producers}**'
            ))

            container.add_item(section)

            downloads = song.get('downloads')

            rows = []
            
            if downloads:
                for i in range(0, len(downloads), 5):
                    row = discord.ui.ActionRow()
                    for path in downloads[i:i + 5]:
                        ext = path.rsplit('.', 1)[-1].upper()
                        label = f'OG {ext}' if 'Original Files' in path else ext
                        row.add_item(
                            discord.ui.Button(
                                label=label, 
                                url=MAIN_URL + quote(path)
                            )
                        )
                    rows.append(row)
                    
                for row in rows:
                    container.add_item(row)

            if ii < len(songs) - 1:
                container.add_item(discord.ui.Separator())

    @commands.command('syncsurfaces', aliases=['syncleaks'])
    @commands.is_owner()
    async def sync_surfaces(self, ctx: commands.Context):        
        if self.cache_songs.is_running():
            self.cache_songs.cancel()
        
        await ctx.message.add_reaction('🔄')
        status = await Cache.fetch_songs()
        if status != 200:
            return await ctx.reply(embed=discord.Embed(description='Request failed. Please try again later.', color=discord.Color.red()).set_image(url=f'https://http.cat/{status}'), delete_after=5)
        
        await self.store_latest_surfaces()
        await ctx.message.add_reaction('✅')

    @commands.command('surfaces', aliases=['leaks'])
    async def surfaces(self, ctx: commands.Context):
        songs = Cache.get_songs()
        if not songs:
            return
        
        if not self.latest_surfaces:
            await self.store_latest_surfaces()

            if not self.latest_surfaces:
                return

        view = LatestSurfacesView(self, self.latest_surfaces, ctx.author)
        await view.build_page()
        msg = await ctx.reply(view=view)
        view.message = msg
        
    @commands.command("snippet", aliases=["snip"])
    async def snippet(self, ctx: commands.Context, *, query: str):
        song_list = await self.fetch_song(ctx, query)
        if song_list is None:
            return

        if len(song_list) == 1:
            layout_view = await self.create_snippet_view(song_list[0])
            if layout_view is None:
                return await Embeds.send_warning_embed(ctx.channel, ctx.author, f'**{song_list[0]['name']}** has no **snippets** available')
            
            await ctx.reply(view=layout_view)

        elif len(song_list) > 1:
            results = sorted(song_list, key=lambda s: s.get("track_titles"))[:25]
            song_map = {str(song["id"]): song for song in results}

            options = [
                discord.SelectOption(
                    label=(
                        lambda t: f"{t[0]} ({', '.join(t[1:])})" if len(t) > 1 else t[0]
                    )(song.get("track_titles"))[:100],
                    value=str(song["id"]),
                )
                for song in results
            ]

            class SongSelect(discord.ui.Select):
                def __init__(self, options, song_map, author, cog: Music):
                    self.song_map = song_map
                    self.author = author
                    self.cog = cog
                    super().__init__(
                        placeholder="Select a song...",
                        min_values=1,
                        max_values=1,
                        options=options,
                    )

                async def callback(self, itn: discord.Interaction):
                    song_id = self.values[0]
                    song = self.song_map[song_id]

                    layout_view = await self.cog.create_snippet_view(song)

                    if layout_view is None:
                        return await Embeds.send_warning_embed(ctx.channel, ctx.author, f'**{song['name']}** has no **snippets** available')

                    if self.author != itn.user:
                        return await itn.response.send_message(
                            view=layout_view, ephemeral=True
                        )

                    await itn.response.edit_message(embed=None, view=layout_view)

            view = discord.ui.View(timeout=None)
            view.add_item(SongSelect(options, song_map, ctx.author, self))

            embed = discord.Embed(
                description=f"{ctx.author.mention}: Multiple **selections** found with your **search**"
            )
            await ctx.reply(embed=embed, view=view)

        else:
            return await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f"I couldnt find a song with the name: `{query}`",
            )

    @commands.command(
        "randomleak", aliases=["rleak"], description="Get a random Juice WRLD leak"
    )
    async def randomleak(self, ctx: commands.Context):
        # lets save eli some sanity and do this a bit nicer... haha maybe some other people will get the idea hahahahahahahahahahahah @ENVY
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/radio/random/"
        ) as response:
            if response.status != 200:
                return await ctx.send(
                    embed=discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red(),
                    ).set_image(url=f"https://http.cat/{response.status}"),
                    delete_after=5,
                )

            data = await response.json()

        song_data = data.get("song")

        layout_view = await self.create_song_view(song_data, True)

        message = await ctx.send(view=layout_view)

        await message.add_reaction("👍")
        await message.add_reaction("👎")

    # end of my beautiful commands

    def clear_user_cache(self, user_id: int, identifiers: list[str] = []):
        self.assert_download_cache()
        for file in os.listdir(DOWNLOAD_CACHE_FOLDER_NAME):
            is_valid = str(user_id) in file
            if is_valid:
                for identifier in identifiers:
                    if identifier.lower() not in file.lower():
                        is_valid = False
                        break

            if is_valid:
                file_path = os.path.join(DOWNLOAD_CACHE_FOLDER_NAME, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")

    def remove_file(self, file_path: str) -> None:
        if not file_path:
            return
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")

    @commands.command(name="cdc")
    @commands.check_any(commands.is_owner(), commands.check(can_test))
    async def cleardownloadcache(self, ctx: commands.Context):
        self.ongoing_heardle = []
        self.snippet_debounce = {}
        self.ongoing_blacktea = []
        self.ongoing_higherlower = []

        files = []
        self.assert_download_cache()
        for file in os.listdir(DOWNLOAD_CACHE_FOLDER_NAME):
            file_path = os.path.join(DOWNLOAD_CACHE_FOLDER_NAME, file)
            try:
                files.append(file_path)
            except Exception as e:
                logger.error(f"Error deleting file {file_path}: {e}")

        count = len(files)
        if count == 0:
            await Embeds.send_info_embed(
                ctx.channel, ctx.author, "No files found in the download cache."
            )
            return

        class ConfirmView(discord.ui.View):
            def __init__(self, author_id):
                super().__init__(timeout=60)
                self.author_id = author_id
                self.value = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message(
                        "You can't use this.", ephemeral=True
                    )
                    return False
                return True

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
            async def confirm(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                self.value = True
                self.stop()

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                self.value = False
                self.stop()

        confirm_view = ConfirmView(ctx.author.id)
        confirm_message = await ctx.reply(
            embed=discord.Embed(
                title="Confirm Deletion",
                description=f"Are you sure you want to delete {count} file(s)?",
                color=discord.Color.red(),
            ),
            view=confirm_view,
        )
        await confirm_view.wait()
        if confirm_view.value:
            files_string_fmt = ", ".join(files)

            for file_path in files:
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
            await confirm_message.edit(
                embed=discord.Embed(
                    title="Deletion Complete",
                    description=f"Deleted {count} file(s) from the download cache: \nFiles: `{files_string_fmt}`.",
                    color=discord.Color.green(),
                ),
                view=None,
            )
        else:
            await confirm_message.edit(
                embed=discord.Embed(
                    title="Operation Cancelled",
                    description="No files were deleted from the download cache.",
                    color=discord.Color.yellow(),
                ),
                view=None,
            )
            return

    def get_most_acceptable_track_name(self, orig_name: str):
        name = orig_name.strip()
        for funcs in self.track_name_transformations.values():
            for func in funcs:
                name = func(name)
        return name

    def get_acceptable_track_names(self, orig_name: str):
        name = orig_name.lower().strip()

        required_transformations = []

        for check_func, transform_funcs in self.track_name_transformations.items():
            if check_func(name):
                for func in transform_funcs:
                    if len(required_transformations) >= MAX_NAME_TRANSFORMATIONS:
                        break
                    required_transformations.append(func)

        # try all combos
        results = set()
        results.add(name)
        for r in range(1, len(required_transformations) + 1):
            for combo in product(required_transformations, repeat=r):
                temp = name
                for func in combo:
                    temp = func(temp)
                results.add(temp.strip())

        return sorted(results)

    def handle_user_done_heardle(self, user_id: int):
        self.heardle_answers[user_id] = None
        if user_id in self.ongoing_heardle:
            self.ongoing_heardle.remove(user_id)

        # Delete user related files
        self.clear_user_cache(user_id, ["_heardle"])

    def handle_user_done_snippet(self, user_id: int):
        self.snippet_debounce[user_id] = False

        # Delete user related files
        self.clear_user_cache(user_id, ["_snippet"])

    async def make_snippet(
        self,
        image_path: str,
        download_url: str,
        file_name: str,
        duration: int = DEFAULT_SNIPPET_DURATION,
        start_point: int = None,
        path: str = None,
    ):
        temp_file_path = None
        output_path = None
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url, params={"path": path} if path else None) as download_response:
                if download_response.status != 200:
                    return False, download_response.status

                self.assert_download_cache()
                song_bytes = await download_response.read()
                temp_file_path = DOWNLOAD_CACHE_FOLDER_NAME + f"/{file_name}.mp3"
                with open(temp_file_path, "wb") as f:
                    f.write(song_bytes)

                orig_clip = AudioFileClip(temp_file_path)
                if start_point == None:
                    start_point = random.randint(
                        0, int(orig_clip.duration) - duration * 2
                    )

                orig_clip = AudioFileClip(temp_file_path)
                sub_clip = orig_clip.subclipped(start_point, start_point + duration)
                final_clip = ImageClip(image_path).with_audio(sub_clip)

                output_path = f"{DOWNLOAD_CACHE_FOLDER_NAME}/{file_name}.mp4"
                final_clip.duration = duration
                final_clip.fps = 1
                final_clip.write_videofile(
                    output_path, codec="libx264", audio_codec="aac", logger=None
                )

                final_clip.close()
                sub_clip.close()
                orig_clip.close()

        if temp_file_path:
            self.remove_file(temp_file_path)
        if image_path and image_path.startswith(DOWNLOAD_CACHE_FOLDER_NAME):
            self.remove_file(image_path)

        return True, output_path

    @commands.command(aliases=["hstats"])
    async def heardlestats(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author

        stats = await self.bot.database.get_heardle_stats(member.id)

        wins = stats.wins if stats else 0
        losses = stats.losses if stats else 0
        streak = stats.streak if stats else 0
        embed = discord.Embed(
            description=f"Heardle Stats for {member.display_name}", color=member.color
        )
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Losses", value=losses, inline=True)
        embed.add_field(
            name="Winstreak",
            value=streak if streak < 5 else f"**{streak}** 🔥",
            inline=True,
        )
        wl = round(wins / (wins + losses) * 100, 2) if (wins + losses) > 0 else 0.0
        embed.add_field(
            name="Win Rate", value=f"{wl} %" if stats else "N/A", inline=True
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        await ctx.reply(embed=embed)

    @commands.command(
        name="shhheardle", help="Shows the heardle answer for the given user"
    )
    async def shhheardle(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author

        if (
            Music.can_test(ctx, self) == False
            and ctx.author.guild_permissions.manage_guild == False
        ):
            await Embeds.send_error_embed(
                ctx.channel,
                ctx.author,
                "You do not have permission to use this command.",
            )
            return

        if (
            member.id not in self.heardle_answers
            or self.heardle_answers[member.id] is None
        ):
            await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f"{member.display_name} does not have an ongoing Heardle game.",
            )
            return

        answer = self.heardle_answers[member.id]
        await Embeds.send_info_embed(
            ctx.channel,
            ctx.author,
            f"The answer to {member.display_name}'s ongoing Heardle game is: **{answer}**",
        )

    ### TODO: make all blacktea commands under a class or something for better organization

    async def sync_blacktea(self):
        self.valid_names = []
        self.producer_counts = {}

        songs = Cache.get_songs()
        for song in songs:
            producers = song.get("producers", "N/A")
            producers = [p.strip() for p in re.split(r"&|,| and ", producers) if p.strip()]

            era = song.get("era", {})
            era_name = era.get("name", "N/A")
            if era_name == "POST": # ignore posthumus cuz thats gay!
                continue
            if len(producers) > 5: # also ignore songs with a ton of producers
                continue

            for producer in producers:
                if producer in self.producer_counts:
                    self.producer_counts[producer] += 1
                else:
                    self.producer_counts[producer] = 1

            track_titles = song.get("track_titles", [])
            for title in track_titles:
                acceptable_alt_name_list = self.get_acceptable_track_names(title)
                self.valid_names.extend(acceptable_alt_name_list)

    def get_random_song_for_blacktea(self):
        songs = Cache.get_songs()
        if songs is None:
            return None
        random_index = random.randint(0, len(songs) - 1)
        return songs[random_index]

    def get_random_3l_for_blacktea(self, song):
        main_name = self.get_most_acceptable_track_name(song.get("name", ""))
        names = self.get_acceptable_track_names(song.get("name", ""))

        def with_name(name):
            valid_slices = []

            for i in range(len(name) - 2):
                chunk = name[i:i+3]

                if all(c.isalpha() for c in chunk):
                    valid_slices.append(chunk)

            if not valid_slices:
                return None

            return random.choice(valid_slices)
        
        main = with_name(main_name)
        if main:
            return main
        else:
            for name in names:
                result = with_name(name)
                if result:
                    return result
        return None

    def find_songs_by_name(self, name):
        if not hasattr(self, "song_index"):
            self.song_index = {}

        if name in self.song_index:
            return self.song_index[name]
        else:
            songs = Cache.get_songs()
            valid_songs = []
            for song in songs:
                track_titles = song.get("track_titles", [])
                for title in track_titles:
                    for acceptable_name in self.get_acceptable_track_names(title):
                        if acceptable_name == name:
                            valid_songs.append(song)
            self.song_index[name] = valid_songs
            return valid_songs
                        
    def blacktea_check_producer(self, song_name, producer):
        songs = self.find_songs_by_name(song_name)
        if not songs:
            return False

        for song in songs:
            producers = song.get("producers", "N/A")
            producers = [p.strip() for p in re.split(r"&|,| and ", producers) if p.strip()]
            if producer in producers:
                return True

        return False

    def blacktea_check_category(self, song_name, category_era):
        songs = self.find_songs_by_name(song_name)
        if not songs:
            return False
        
        for song in songs:
            category = song.get("category", "")
            era = song.get("era", {})
            era_name = era.get("name", "")
            if f'{category}{era_name}' == category_era:
                return True
        return False
    
    def blacktea_check_leaked(self, song_name, leaked_date):
        songs = self.find_songs_by_name(song_name)
        if not songs:
            return False

        for song in songs:
            date_leaked = song.get("date_leaked", "")
            end_line_index = date_leaked.rfind("\n")
            real_date_leaked = date_leaked[end_line_index:date_leaked.find(".", end_line_index)].strip().replace(",", "").split()
            if real_date_leaked and len(real_date_leaked) < 3:
                return False
            # month = real_date_leaked[0].strip()
            # day = real_date_leaked[1].strip()
            year = real_date_leaked[2].strip()
            if year and year.lower() == leaked_date.lower():
                return True
        return False
    
    def blacktea_check_groupbuy_price(self, song_name, leaked_date):
        songs = self.find_songs_by_name(song_name)
        if not songs:
            return False

        for song in songs:
            groupbuy_info = song.get("groupbuy_info", {})
            price = groupbuy_info.get("price", "")
            numerical_price = ''.join(filter(str.isdigit, price))
            if not numerical_price:
                return False
            
            return int(numerical_price) >= int(leaked_date)
        return False

    # Returns embed description, correct answer, check function
    def get_random_blacktea_category_data(self, song):
        def default_return(song):
            random_3l = self.get_random_3l_for_blacktea(song)
            while not random_3l:
                song = self.get_random_song_for_blacktea()
                random_3l = self.get_random_3l_for_blacktea(song)
            return {
                "description": f"Name a **Juice WRLD** song that contains **{random_3l.lower()}**",
                "check_func": lambda song_name: random_3l.lower() in song_name.lower()
            }
        
        random_index = random.randint(0, 4)
        if random_index == 0:
            producers = song.get("producers", "N/A")
            producers = [
                p.strip()
                for p in re.split(r"&|,| and ", producers)
                if p.strip()
            ]

            producer = random.choice(producers) if producers else None
            if producer in self.producer_counts:
                count = self.producer_counts[producer]
                if count < 6: # Adjust this number to how common you want the producer questions to be, this is just a safeguard to prevent really common producers from dominating the category
                    return self.get_random_blacktea_category_data(song)
            
            return {
                "description": f"Name a **Juice WRLD** song produced by **{producer}**",
                "check_func": lambda song_name: self.blacktea_check_producer(song_name, producer)
            }
        elif random_index == 1:
            ALBUMS = {
                'jute':                 {'name': 'JUICED UP THE EP', 'color': '#FFE602'},
                'LND':                  {'name': 'Legends Never Die', 'color': '#F700FF'},
                'afflictions':          {'name': 'affliction', 'color': '#000000'},
                'bdm':                  {'name': 'BINGEDRINKINGMUSIC', 'color': '#000000'},
                'HIH 999':              {'name': 'Heartbroken In Hollywood 9 9 9', 'color': '#FF653E'},
                'jw 999':               {'name': 'JuiceWRLD 9 9 9', 'color': '#FF2C2C'},
                'ND':                   {'name': 'NOTHINGS DIFFERENT </3', 'color': '#FF8800'},
                'GB&GR':                {'name': 'Goodbye & Good Riddance', 'color': '#008CFF'},
                'GB&GR (AE)':           {'name': 'Goodbye & Good Riddance (Anniversary Edition)', 'color': '#008CFF'},
                'GB&GR (5YAE)':         {'name': 'Goodbye & Good Riddance (5 Year Anniversary Edition)', 'color': '#008CFF'},
                'WOD':                  {'name': 'WRLD ON DRUGS', 'color': '#00FF94'},
                'DRFL':                 {'name': 'Death Race For Love', 'color': '#FF9900'},
                'DRFL (BTV)':           {'name': 'Death Race For Love (Bonus Track Version)', 'color': '#FF9900'},
                'OUT':                  {'name': 'Outsiders', 'color': '#2B2B2B'},
                'POST':                 {'name': 'Posthumous', 'color': '#00CCFF'},
                'TPP':                  {'name': 'The Pre-Party', 'color': '#EA00FF'},
                'TPP (EE)':             {'name': 'The Pre-Party (Extended Edition)', 'color': '#EA00FF'},
                'FD':                   {'name': 'Fighting Demons', 'color': '#2E2E2E'},
                'FD (CE)':              {'name': 'Fighting Demons (Complete Edition)', 'color': '#2E2E2E'},
                'FD (EE)':              {'name': 'Fighting Demons (Extended Edition)', 'color': '#2E2E2E'},
                'FD (DDE)':             {'name': 'Fighting Demons (Digital Deluxe Edition)', 'color': '#2E2E2E'},
                'TPNE':                 {'name': 'The Party Never Ends', 'color': '#CC00FF'},
            }

            category = song.get("category", "")
            era = song.get("era", {})
            era_name = era.get("name", "")

            # TODO: fix this doesnt work
            era_full = ALBUMS.get(era_name, {}).get("name", era_name)

            if category == "recording_session" or era_name == "GB&GR (AE)" or era_name == "GB&GR (5YAE)" or era_name == "MAINSTREAM":
                return default_return(song)

            return {
                "description": f"Name a **Juice WRLD** song that is **{category}** and made during **{era_full.upper()}**",
                "check_func": lambda song_name: self.blacktea_check_category(song_name, f"{category}{era_name}")
            }
        elif random_index == 2:
            date_leaked = song.get("date_leaked", "")
            end_line_index = date_leaked.rfind("\n")
            real_date_leaked = date_leaked[end_line_index:date_leaked.find(".", end_line_index)].strip().replace(",", "").split()
            if not real_date_leaked or len(real_date_leaked) < 3:
                return default_return(song)
            # month = real_date_leaked[0].strip()
            year = real_date_leaked[2].strip()

            return {
                "description": f"Name a **Juice WRLD** song that leaked in **{year}**",
                "check_func": lambda song_name: self.blacktea_check_leaked(song_name, year.lower())
            }
        elif random_index == 3:
            groupbuy_info = song.get("groupbuy_info", {})
            price = groupbuy_info.get("price", "")
            if len(price) == 0:
                return default_return(song)
            
            numerical_price = ''.join(filter(str.isdigit, price))
            if not numerical_price:
                return default_return(song)

            return {
                "description": f"Name a **Juice WRLD** song that was groupbuyed for **{price}** or higher",
                "check_func": lambda song_name: self.blacktea_check_groupbuy_price(song_name, numerical_price)
            }
        else:
            return default_return(song)
    @commands.command(name="syncblacktea", aliases=["sbt"])
    @commands.is_owner()
    async def syncblacktea(self, ctx: commands.Context):
        await ctx.message.add_reaction('🔄')

        songs = Cache.get_songs()
        old_names_length = len(self.valid_names)
        old_prods_length = len(self.producer_counts)
        await self.sync_blacktea()
        await Embeds.send_info_embed(ctx.channel, ctx.author, f"Synced valid track names. Total songs: **{len(songs)}**. Total valid names: **{old_names_length}** -> **{len(self.valid_names)}**")
        await Embeds.send_info_embed(ctx.channel, ctx.author, f"Synced producer counts. Total producers: **{old_prods_length}** -> **{len(self.producer_counts)}**")

        await ctx.message.add_reaction('✅')

    @commands.command(name="blacktea", help="Play blacktea (blacktea from bleed but wit juice wrld songs)")
    @commands.check_any(commands.is_owner(), commands.has_permissions(manage_guild=True))
    async def blacktea(self, ctx: commands.Context):
        if ctx.author.id in self.ongoing_blacktea :
            await Embeds.send_error_embed(ctx.channel, ctx.author, "You already have an ongoing game of Blacktea!")
            return

        players = []
        used_words = []
        created_messages = []
        self.ongoing_blacktea.append(ctx.author.id)
    
        blacktea_embed = discord.Embed(
            description=":alarm_clock: Waiting for **players**, react with ✅ to join. The game will begin in **30** seconds.\n\n`GOAL:` You have **10** seconds to say a **Juice WRLD** song fits the **given category**. Failure to do so within the **15** seconds will lose a life. Each player has **2** lives to begin with.\n\n`NOTES:` A song can only be used **once** through the course of the game.",
            color = discord.Color.green(),
        )
        blacktea_embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        message = await ctx.send(embed=blacktea_embed)
        await message.add_reaction("✅")
        await asyncio.sleep(30)

        created_messages.append(message)

        message = await ctx.fetch_message(message.id)
        for reaction in message.reactions:
            if str(reaction.emoji) == "✅":
                async for user in reaction.users():
                    if not user.bot: 
                        players.append({
                            "id": user.id,
                            "display_name": user.display_name,
                            "avatar_url": ctx.guild.get_member(user.id).display_avatar.url,
                            "color": user.color,
                            "mention": user.mention,
                            "lives": 2,
                        })

        if len(players) <= 1:
            self.ongoing_blacktea.remove(ctx.author.id)
            await Embeds.send_warning_embed(ctx.channel, ctx.author, "Not enough players joined the game. At least 2 players are required.")
            return
        
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 30
        )

        def get_alive_players(players):
            return [p for p in players if p['lives'] > 0]

        alive_players = get_alive_players(players)
        while len(alive_players) > 1:
            for player in alive_players:
                def get_song_recursive(attempt=0):
                    if attempt > 5:
                        return None
                    song = self.get_random_song_for_blacktea()
                    if not song:
                        return get_song_recursive(attempt + 1)
                    main_name = song.get("name", "")
                    if main_name.lower() in used_words:
                        return get_song_recursive(attempt + 1)
                    used_words.append(main_name.lower())
                    return song

                song = get_song_recursive()
                if not song:
                    await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to fetch a valid song for the game. Ending game early.")
                    self.ongoing_blacktea.remove(ctx.author.id)
                    break
                category_data = self.get_random_blacktea_category_data(song)
                embed = discord.Embed(
                    description=category_data["description"],
                    color = player["color"].value if player["color"] else discord.Color.default().value,
                )
                embed.set_author(name=player["display_name"], icon_url=player["avatar_url"])
                message = await ctx.send(player["mention"], embed=embed)
                created_messages.append(message)

                # TODO: add 3, 2, 1 reaction similar to bleed
                def check(m):
                    return m.author.id == player['id'] and m.channel == ctx.channel and m.content.lower().strip() in self.valid_names and category_data["check_func"](m.content.lower().strip())
                try:
                    guess = await self.bot.wait_for('message', check=check, timeout=15)
                    await guess.add_reaction("✅")
                    continue
                except asyncio.TimeoutError:
                    player['lives'] -= 1
                    message = await ctx.send(embed=discord.Embed(
                        description=f"💥 {player['mention']} you now have **{player['lives']}** lives. One correct answer was **{song.get('name', 'N/A')}**",
                        color=discord.Color.red(),
                    ))
                    created_messages.append(message)
                    alive_players = get_alive_players(players)
                    if len(alive_players) <= 1:
                        break
                    else:
                        continue
        if len(alive_players) == 1:
            winner = alive_players[0]
            message = await ctx.send(embed=discord.Embed(
                description=f"🏆 {winner['mention']} is the winner of this game of Blacktea with **{winner['lives']}** lives remaining!",
                color=discord.Color.gold(),
            ), delete_after=15)

        # at the end
        for message in created_messages:
            try:
                await message.delete()
            except Exception as e:
                pass
        self.ongoing_blacktea.remove(ctx.author.id)

    @commands.command(name="heardle", help="Play a game of Heardle. Juice WRLD songs only.")
    async def heardle(self, ctx: commands.Context):
        if ctx.author.id in self.ongoing_heardle:
            await Embeds.send_error_embed(
                ctx.channel, ctx.author, "You already have an ongoing game of Heardle!"
            )
            return

        # clear existing files
        self.handle_user_done_heardle(ctx.author.id)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{JUICEWRLD_API}/juicewrld/radio/random/"
            ) as response:
                async def handle_request_failed(ctx, code=None):
                    embed = discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red(),
                    )
                    if code:
                        embed.set_image(url=f"https://http.cat/{code}")
                    await ctx.reply(embed=embed, delete_after=5)

                if response.status != 200:
                    await handle_request_failed(ctx, response.status)
                    return

                data = await response.json()

                song_data = data.get("song", None)
                if not song_data:
                    await handle_request_failed(ctx)
                    return

                self.ongoing_heardle.append(ctx.author.id)
                track_tiles = song_data.get("track_titles", [])
                path = data.get("path", "")

                async with ctx.typing():
                    raw_track_title = song_data.get("name", "Unknown Title")
                    best_track_title = self.get_most_acceptable_track_name(
                        song_data.get("name", "Unknown Title")
                    )

                    image_file_name = f"{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_temp_image_heardle.png"
                    async with session.get(f"{JUICEWRLD_API}/juicewrld/cover/{best_track_title.lower().replace(" ", "")}.png") as cover_response:
                        async with session.get(f"{JUICEWRLD_API}/juicewrld/files/cover-art/", params={"path": path}) as album_art_response:
                            if cover_response.status == 200:
                                image_data = await cover_response.read()
                                image = Image.open(BytesIO(image_data))
                                blurred_image = image.filter(ImageFilter.GaussianBlur(radius=25))  # Adjust radius for intensity                            
                                blurred_image.save(image_file_name)
                            elif album_art_response.status == 200:
                                image_data = await album_art_response.read()
                                image = Image.open(BytesIO(image_data))
                                blurred_image = image.filter(ImageFilter.GaussianBlur(radius=5))  # Adjust radius for intensity                            
                                blurred_image.save(image_file_name)
                            else: # Last resort: use user's avatar
                                image_url = ctx.author.display_avatar.url
                                async with session.get(image_url) as image_response:
                                    if image_response.status == 200:
                                        image_data = await image_response.read()
                                        with open(image_file_name, "wb") as img_file:
                                            img_file.write(image_data)
                                            
                    download_url = f"{JUICEWRLD_API}/juicewrld/files/download-compressed/"
                    result, payload = await self.make_snippet(
                        image_file_name,
                        download_url,
                        f"{ctx.author.id}_heardle",
                        HEARDLE_CLIP_DURATION,
                        path=path,
                    )
                    if result == False:
                        await handle_request_failed(ctx, payload)
                        self.handle_user_done_heardle(ctx.author.id)
                        return

                    class HeardleContainer(discord.ui.Container):
                        def __init__(self, thumbnail_url, snippet):
                            super().__init__()

                            header = discord.ui.Section(
                                accessory=discord.ui.Thumbnail(media=thumbnail_url)
                            )
                            header.add_item(
                                discord.ui.TextDisplay(f"# Heardle\nPlease send a message of a song title to guess the song\n-# Type `exit` to quit the game.\n-# Duration: {HEARDLE_CLIP_DURATION} seconds")
                            )
                            self.add_item(header)
                            self.add_item(discord.ui.Separator())

                            media_gallery = discord.ui.MediaGallery()
                            media_gallery.add_item(media=snippet)
                            self.add_item(media_gallery)

                    message = await ctx.send(file=discord.File(payload))
                    self.remove_file(payload)
                    # heardle_view = discord.ui.LayoutView(timeout=None)
                    # heardle_view.add_item(HeardleContainer(ctx.guild.icon.url, message.attachments[0].url))
                    # await message.delete()

                    # message = await ctx.send(view=heardle_view)

                has_guessed = False
                attempt = 1
                acceptable_answers = []
                self.heardle_answers[ctx.author.id] = best_track_title
                for title in track_tiles:
                    acceptable_alt_name_list = self.get_acceptable_track_names(title)
                    acceptable_answers.extend(acceptable_alt_name_list)

                async def update_timer_message(
                    message: discord.Message, full_name, start_time, author
                ):
                    try:
                        while True:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            hint_chars = int(
                                elapsed // 3
                            )  # reveal a character every 3 seconds, max 3 as curteousy of silmar

                            if hint_chars > 3:
                                raise asyncio.CancelledError

                            hint = full_name[:hint_chars]
                            for i in range(len(full_name) - hint_chars):
                                if full_name[i + hint_chars] == " ":
                                    hint += " "
                                else:
                                    hint += "?"
                            await message.edit(
                                embed=discord.Embed(title="Heardle", description=f"{author.mention} Hint ({round(hint_chars)}/3): {hint}", color=author.color)
                            )
                            await asyncio.sleep(1)
                    except asyncio.CancelledError:
                        return

                start_time = asyncio.get_event_loop().time()
                update_task = asyncio.create_task(
                    update_timer_message(message, best_track_title, start_time, ctx.author)
                )

                while has_guessed == False:
                    def check_guess(m):
                        return m.author == ctx.author and m.channel == ctx.channel

                    try:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        remaining_time = HEARDLE_GAME_DURATION - elapsed
                        if remaining_time <= 0:
                            raise TimeoutError

                        guess_msg = await self.bot.wait_for(
                            "message", check=check_guess, timeout=remaining_time
                        )
                    except TimeoutError:
                        await Embeds.send_warning_embed(
                            ctx.channel,
                            ctx.author,
                            f"Time's up! You didn't guess the song ({best_track_title} [{raw_track_title}]) in time.",
                        )
                        try:
                            await message.delete()
                        except:
                            pass
                        self.handle_user_done_heardle(ctx.author.id)
                        update_task.cancel()
                        await self.bot.database.add_heardle_loss(ctx.author.id)
                        return

                    guess = guess_msg.content.strip().lower()
                    if guess == "exit":
                        await guess_msg.add_reaction("👋")
                        try:
                            await message.delete()
                        except:
                            pass
                        await self.bot.database.add_heardle_loss(ctx.author.id)
                        self.handle_user_done_heardle(ctx.author.id)
                        update_task.cancel()
                        return
                    elif guess in acceptable_answers:
                        has_guessed = True
                    else:
                        attempt += 1
                elapsed = asyncio.get_event_loop().time() - start_time
                await Embeds.send_success_embed(
                    ctx.channel,
                    ctx.author,
                    f"Congratulations! You guessed the song correctly: **{best_track_title}** in {round(elapsed)} seconds ({attempt} attempts)!",
                )
                await self.bot.database.add_heardle_win(ctx.author.id)
                try:
                    await message.delete()
                except:
                    pass
                self.handle_user_done_heardle(ctx.author.id)
                update_task.cancel()

    @commands.command(aliases=["makesnip"])
    async def makesnippet(self, ctx: commands.Context, *, query: str):
        debounce = self.snippet_debounce.get(ctx.author.id, False)
        if debounce:
            await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                "Please wait a bit before making another snippet.",
            )
            return
        self.snippet_debounce[ctx.author.id] = True

        async with self.session.get(
            f"{JUICEWRLD_API}/juicewrld/files/browse/", params={"search": query}
        ) as response:

            async def handle_request_failed(ctx, code=None):
                embed = discord.Embed(
                    description="Request failed. Please try again later.",
                    color=discord.Color.red(),
                )
                if code:
                    embed.set_image(url=f"https://http.cat/{code}")
                await ctx.reply(embed=embed, delete_after=5)

            if response.status != 200:
                await handle_request_failed(ctx, response.status)
                self.handle_user_done_snippet(ctx.author.id)
                return

            data = await response.json()

            items = data.get("items", [])
            safe_items = {}
            for item in items:
                mime_type = item.get("mime_type", None)
                if mime_type and mime_type == "audio/mpeg":
                    name = item.get("name", "")
                    name = name[: name.rfind(".")] if "." in name else name
                    best_name = self.get_most_acceptable_track_name(name)
                    existing = safe_items.get(best_name)

                    good = True
                    if existing:
                        # this will remove duplicates and also get the lowest size for best performance
                        existing_size = existing.get("size", 0)
                        current_size = item.get("size", 0)
                        if current_size >= existing_size:
                            good = False

                    if good:
                        safe_items[best_name] = item

            count = len(safe_items)
            safe_items_list = list(safe_items.values())

            song = None
            if count == 0:
                await Embeds.send_error_embed(
                    ctx.channel,
                    ctx.author,
                    f"I couldnt find a song with the name: `{query}`",
                )
                self.handle_user_done_snippet(ctx.author.id)
                return
            if count > 1:

                class SongSelect(discord.ui.Select):
                    def __init__(self, view, author, songs):
                        options = []
                        self.real_view = view
                        self.author = author
                        self.avail_options_map = {}
                        for name, song in songs.items():
                            self.avail_options_map[name] = song
                            options.append(discord.SelectOption(label=name))
                        super().__init__(
                            placeholder="Select a song...",
                            min_values=1,
                            max_values=1,
                            options=options,
                        )
                        self.songs = songs

                    async def callback(self, interaction: discord.Interaction):
                        if interaction.user.id != self.author:
                            return await interaction.response.send_message(
                                "You cannot use this select menu.", ephemeral=True
                            )

                        self.chosen_song = self.avail_options_map.get(
                            self.values[0], None
                        )
                        self.real_view.stop()

                class SongView(discord.ui.View):
                    def __init__(self, author, songs):
                        super().__init__(timeout=30)
                        self.add_item(SongSelect(self, author, songs))

                embed = discord.Embed(
                    description=f"{ctx.author.mention}: Multiple **songs** found with your **search**. Please select one from the dropdown below."
                )
                view = SongView(ctx.author.id, safe_items)
                message = await ctx.reply(embed=embed, view=view)
                await view.wait()
                try:
                    await message.delete()
                except:
                    pass

                select = view.children[0]
                if not select or not select.chosen_song:
                    self.handle_user_done_snippet(ctx.author.id)
                    return
                song = select.chosen_song
                try:
                    await message.delete()
                except:
                    pass
            else:
                song = safe_items_list[0]

            path = song.get("path", "")
            download_url = f"{JUICEWRLD_API}/juicewrld/files/download-compressed/"

            best_track_title = self.get_most_acceptable_track_name(
                song.get("name", "Unknown Title").replace(".mp3", "")
            )
            image_file_name = f"{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_temp_image_heardle.png"

            # Try the dedicated cover file first, then the album art endpoint.
            async with self.session.get(
                f"{JUICEWRLD_API}/juicewrld/cover/{best_track_title.lower().replace(' ', '')}.png"
            ) as cover_response:
                async with self.session.get(
                    f"{JUICEWRLD_API}/juicewrld/files/cover-art/", params={"path": path}
                ) as album_art_response:
                    used_image = False

                    # Helper to attempt opening and saving an image safely
                    async def try_use_image(data: bytes, radius: int) -> bool:
                        try:
                            img = Image.open(BytesIO(data))
                            blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
                            blurred.save(image_file_name)
                            return True
                        except Exception:
                            return False

                    # Validate content-type before trying to use it (and catch open errors)
                    if (
                        cover_response.status == 200
                        and cover_response.headers.get("content-type", "").startswith("image")
                    ):
                        image_data = await cover_response.read()
                        used_image = await try_use_image(image_data, 15)

                    if not used_image and album_art_response.status == 200 and album_art_response.headers.get("content-type", "").startswith("image"):
                        image_data = await album_art_response.read()
                        used_image = await try_use_image(image_data, 5)

                    if not used_image:
                        # Last resort: use user's avatar (if available), otherwise create a blank image
                        image_url = ctx.author.display_avatar.url
                        try:
                            async with self.session.get(image_url) as image_response:
                                if image_response.status == 200 and image_response.headers.get("content-type", "").startswith("image"):
                                    image_data = await image_response.read()
                                    with open(image_file_name, "wb") as img_file:
                                        img_file.write(image_data)
                                else:
                                    # create a simple placeholder image so subsequent processing doesn't fail
                                    Image.new("RGBA", (200, 200), (50, 50, 50)).save(image_file_name)
                        except Exception:
                            Image.new("RGBA", (200, 200), (50, 50, 50)).save(image_file_name)

            result, payload = await self.make_snippet(
                image_file_name, download_url, f"{ctx.author.id}_snippet", path=path
            )
            if result == False:
                await handle_request_failed(ctx, payload)
                self.handle_user_done_snippet(ctx.author.id)
                return

            message = await ctx.channel.send(file=discord.File(payload))
            self.remove_file(payload)

            async def debounce_delay(author_id: int):
                await asyncio.sleep(15)  # default snippet debounce
                self.handle_user_done_snippet(author_id)

            asyncio.create_task(debounce_delay(ctx.author.id))

    def find_pledges_channel(self, guild: discord.Guild):
        channels = []
        for channel in guild.text_channels:
            if "pledge" in channel.name.lower():
                channels.append(channel)
        return channels

    @commands.command(name="countpledge", aliases=["pledges", "pledged", "countpledges"])
    @commands.check_any(commands.has_permissions(administrator=True), commands.check(can_test))
    async def countpledge(self, ctx: commands.Context, after_message_id: int = 0):
        pledges_channels = self.find_pledges_channel(ctx.guild)
        count = len(pledges_channels)
        selected_channel = None
        if count == 0:
            await Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                "No pledge channels found in this server.",
            )
            return
        elif count > 1:
            role_list = "\n".join(
                [
                    f"{index + 1}. {role.mention}"
                    for index, role in enumerate(pledges_channels)
                ]
            )
            embed = discord.Embed(
                description=f"Multiple pledge channels found':\n{role_list}\nPlease reply with the number of the role you want."
            )
            msg = await ctx.send(embed=embed)

            def check(m):
                return (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.isdigit()
                )

            try:
                response = await self.bot.wait_for("message", check=check, timeout=30.0)
                selected_index = int(response.content) - 1

                if selected_index < 0 or selected_index >= len(pledges_channels):
                    return None, "Invalid selection. Command cancelled."

                await msg.delete()
                selected_channel = pledges_channels[selected_index]
                await response.delete()
            except (ValueError, IndexError):
                return None, "Invalid selection. Command cancelled."
            except asyncio.TimeoutError:
                return None, "You took too long to respond. Command cancelled."
        else:
            selected_channel = pledges_channels[0]
        
        pledge_count = 0
        async with ctx.typing():
            async for message in selected_channel.history(after=discord.Object(id=after_message_id), limit=None):
                if message.author.bot:
                    continue
                
                numbers = re.findall(r'\d+', message.content)
                numbers = list(map(int, numbers))

                usable_number = None
                while usable_number is None and len(numbers) > 0:
                    candidate = numbers.pop(0)
                    if candidate > 0 and candidate < 10000:  # reasonable pledge range
                        usable_number = candidate
                
                pledge_count += usable_number if usable_number is not None else 0
        
        await Embeds.send_success_embed(
            ctx.channel,
            ctx.author,
            f"Total pledges counted ({selected_channel.mention}): **${pledge_count:,}**",
            delete_after=None
        )
        
    async def get_genius_data(self, song_name: str):
        """Fetch song data from Genius API."""
        search_url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_API_TOKEN}"}
        params = {"q": song_name}

        async with self.session.get(search_url, headers=headers, params=params) as response:
            if response.status != 200:
                return None
            data = await response.json()
            hits = data.get("response", {}).get("hits", [])
            if len(hits) == 0:
                return None
            api_path = hits[0].get("result", {}).get("api_path", "")
            if not api_path:
                return None
            song_url = f"https://api.genius.com{api_path}"
            async with self.session.get(song_url, headers=headers) as song_response:
                if song_response.status != 200:
                    return None
                song_response_json = await song_response.json()
                return song_response_json.get("response", {}).get("song", {})

        return None

    @commands.command(name="higherlower", help="Play a game of Higher or Lower with Juice WRLD song streams or whatever")
    async def higherlower(self, ctx: commands.Context):
        if ctx.author.id in self.ongoing_higherlower:
            await Embeds.send_error_embed(ctx.channel, ctx.author, "You already have an ongoing game of Higher or Lower!")
            return
        
        async with self.session.get(f"{JUICEWRLD_API}/juicewrld/radio/random/") as response1:
            response1_data = await response1.json()

            song1_genius_data = await self.get_genius_data(response1_data.get("song", {}).get("name", ""))
            if not song1_genius_data:
                await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to fetch song data. Please try again later.")
                return
            song1_id = song1_genius_data.get("id", None)
            if not song1_id:
                await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to extract song ID. Please try again later.")
                return
            
            song2 = song1_id
            retry = 0
            while song2 == song1_id:
                async with self.session.get(f"{JUICEWRLD_API}/juicewrld/radio/random/") as response2:
                    response2_data = await response2.json()

                    song2_genius_data = await self.get_genius_data(response2_data.get("song", {}).get("name", ""))
                    if not song2_id:
                        await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to extract second song ID. Please try again later.")
                        return
                    song2_id = song2_genius_data.get("id", None)
                    if not song2_genius_data:
                        await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to fetch second song data. Please try again later.")
                        return
                    if len(song2_id) == 0 and retry > 5:
                        await Embeds.send_error_embed(ctx.channel, ctx.author, "Failed to fetch song data. Please try again later.")
                        return
                    retry += 1

            self.ongoing_higherlower.append(ctx.author.id)
                
            # were using the genius title rather than api title because if we get wrong song from genius,
            # the user can still guess based on song retrieved from geniu  
            song1_title = song1_genius_data.get("full_title", "Unknown Title")
            song2_title = song2_genius_data.get("full_title", "Unknown Title")
            song1_pageviews = song1_genius_data.get("stats", {}).get("pageviews", 0)
            song2_pageviews = song2_genius_data.get("stats", {}).get("pageviews", 0)
            
            embed = discord.Embed(
                description=f"Do you think **{song1_title}** has more (⬆️) or less (⬇️) pageviews than **{song2_title}**?",
                color=ctx.author.color
            )
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text="Data from genius", icon_url=song1_genius_data.get("song_art_image_thumbnail_url", ""))
            message = await ctx.send(embed=embed)
            await message.add_reaction("⬆️")
            await message.add_reaction("⬇️")
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["⬆️", "⬇️"] and reaction.message.id == message.id
            try:    
                # TODO: add data row and stats similar to headle (!hstats for heardle so like !highlowstats or something like that)
                # TODO: make game continue system after: Automatically play again but add an X reaction to quit

                reaction, user = await self.bot.wait_for('reaction_add', check=check, timeout=30)
                if (reaction.emoji == "⬆️" and song1_pageviews > song2_pageviews) or (reaction.emoji == "⬇️" and song1_pageviews < song2_pageviews):
                    await Embeds.send_success_embed(ctx.channel, ctx.author, f"Correct! **{song1_title}** has {song1_pageviews} pageviews while **{song2_title}** has {song2_pageviews} pageviews.")
                else:
                    await Embeds.send_error_embed(ctx.channel, ctx.author, f"Wrong! **{song1_title}** has {song1_pageviews} pageviews while **{song2_title}** has {song2_pageviews} pageviews.")
            except asyncio.TimeoutError:
                await Embeds.send_warning_embed(ctx.channel, ctx.author, "You took too long to react! Please try again.")
                self.ongoing_higherlower.remove(ctx.author.id)
                return

class CoverSearch(commands.Cog, name="Cover", description="Search for song covers from Juice WRLD API"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_url = "https://juicewrldapi.com/juicewrld/cover"
        self.extensions = ["png", "jpg", "jpeg"]
    
    async def check_cover_exists(self, session: aiohttp.ClientSession, song_name: str, extension: str) -> tuple[bool, str]:
        """Check if a Juice WRLD song cover exists."""
        url = f"{self.base_url}/{song_name}.{extension}"
        try:
            async with session.head(url, timeout=5) as response:
                return (response.status == 200, url)
        except:
            return (False, url)

    @commands.command(name="cover", help="Search for available covers of a song")
    async def cover(self, ctx: commands.Context, *, song_name: str = None):
        """Search for song covers in the Juice WRLD API database."""
        if not song_name:
            embed = discord.Embed(
                description="🚫 Please provide a song name to search for covers.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        formatted_song = song_name.lower().replace(" ", "")
        
        embed = discord.Embed(
            description=f"🔍 Searching for covers of **{song_name}**...",
            color=discord.Color.blurple()
        )
        progress_msg = await ctx.send(embed=embed)
        
        variations = [formatted_song]
        for i in range(1, 50):  # 50 just in case
            variations.append(f"{formatted_song}{i}")
        
        extensions = ["png", "jpg", "jpeg"]
        async with aiohttp.ClientSession() as session:
            tasks = []
            for variation in variations:
                for ext in extensions:
                    tasks.append(self.check_cover_exists(session, variation, ext))
            
            results = await asyncio.gather(*tasks)
        
        found_covers = []
        task_index = 0
        for variation in variations:
            for ext in extensions:
                exists, url = results[task_index]
                if exists:
                    found_covers.append((url, ext, variation))
                task_index += 1
        
        if not found_covers:
            embed = discord.Embed(
                description=f"❌ No covers found for **{song_name}**.",
                color=discord.Color.red()
            )
            await progress_msg.edit(embed=embed)
            await asyncio.sleep(10)
            await progress_msg.delete()
            return
        
        await progress_msg.delete()
        
        class CoverPaginationView(discord.ui.LayoutView):
            def __init__(self, covers, song_name, author_id):
                super().__init__(timeout=60)
                self.covers = covers
                self.song_name = song_name
                self.author_id = author_id
                self.current_page = 0
                self.per_page = 9
                self.total_pages = (len(covers) + self.per_page - 1) // self.per_page
                self.update_view()
            
            def update_view(self):
                self.clear_items()
                
                start_idx = self.current_page * self.per_page
                end_idx = min(start_idx + self.per_page, len(self.covers))
                current_covers = self.covers[start_idx:end_idx]
                
                class CoverContainer(discord.ui.Container):
                    def __init__(self, covers, song_name, total_covers, current_page, total_pages):
                        super().__init__(accent_color=0x2B2D31)
                        
                        self.add_item(discord.ui.TextDisplay(
                            f"### 🎵 Found {total_covers} Cover(s) for: {song_name}"
                        ))
                        
                        gallery_items = []
                        for url, ext, variation in covers:
                            item = discord.MediaGalleryItem(
                                media=discord.UnfurledMediaItem(url=url)
                            )
                            gallery_items.append(item)
                        
                        self.add_item(discord.ui.MediaGallery(*gallery_items))
                        self.add_item(discord.ui.Separator())
                        self.add_item(discord.ui.TextDisplay(
                            f"-# Page {current_page + 1}/{total_pages}"
                        ))
                
                self.add_item(CoverContainer(current_covers, self.song_name, len(self.covers), self.current_page, self.total_pages))
                
                if self.total_pages > 1:
                    button_row = discord.ui.ActionRow()
                    
                    prev_button = discord.ui.Button(
                        label="◀ Previous",
                        style=discord.ButtonStyle.secondary,
                        disabled=(self.current_page == 0),
                        custom_id="prev_page"
                    )
                    
                    next_button = discord.ui.Button(
                        label="Next ▶",
                        style=discord.ButtonStyle.secondary,
                        disabled=(self.current_page >= self.total_pages - 1),
                        custom_id="next_page"
                    )
                    
                    button_row.add_item(prev_button)
                    button_row.add_item(next_button)
                    self.add_item(button_row)
            
            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message(
                        "This isn't your embed.",
                        ephemeral=True
                    )
                    return False
                
                if interaction.data['custom_id'] == 'prev_page':
                    if self.current_page > 0:
                        self.current_page -= 1
                        self.update_view()
                        await interaction.response.edit_message(view=self)
                elif interaction.data['custom_id'] == 'next_page':
                    if self.current_page < self.total_pages - 1:
                        self.current_page += 1
                        self.update_view()
                        await interaction.response.edit_message(view=self)
                return True
        
        view = CoverPaginationView(found_covers, song_name, ctx.author.id)
        sent_msg = await ctx.send(view=view)
        
        await asyncio.sleep(60)
        await sent_msg.delete()
        
        await ctx.send(f"🎵 **Covers for `{song_name}` have been deleted.**")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    await bot.add_cog(CoverSearch(bot))
    logger.debug("Music cog initialized successfully")

class LatestSurfacesView(discord.ui.LayoutView):
    def __init__(self, cog, songs, author, per_page: int = 4):
        super().__init__(timeout=30)
        self.cog = cog
        self.author = author
        self.songs = songs
        self.per_page = per_page
        self.page = 0
        self.building = False

    def max_page(self):
        return max((len(self.songs) - 1) // self.per_page, 0)

    def slice(self):
        start = self.page * self.per_page
        return self.songs[start:start + self.per_page]

    async def build_page(self):
        if self.building:
            return
        self.building = True

        self.clear_items()

        container = discord.ui.Container(accent_color=0xffffff)
        container.add_item(discord.ui.TextDisplay('### Latest Surfaces (Last 30 Days)'))
        container.add_item(discord.ui.Separator())

        await self.cog.build_song_items(container, self.slice())
        # container.add_item(discord.ui.TextDisplay(f'-# Total Songs: {len(self.songs):,} • Page {self.page+1}/{self.max_page()+1}'))
        nav = discord.ui.ActionRow()
        nav.add_item(discord.ui.Button(label='Previous', style=discord.ButtonStyle.grey, custom_id='latest_prev'))
        nav.add_item(discord.ui.Button(label='Next', style=discord.ButtonStyle.grey, custom_id='latest_next'))
        nav.add_item(discord.ui.Button(label='Tracker', emoji='<:fart:1445127619744890911>', url='https://juicewrldapi.com/'))
        
        container.add_item(discord.ui.Separator())
        container.add_item(nav)

        self.add_item(container)

        self.building = False

    async def interaction_check(self, itn: discord.Interaction):
        if itn.user != self.author:
            return await itn.response.send_message("this aint ur shit bro", ephemeral=True)

        cid = itn.data.get('custom_id')
        max_page = self.max_page()

        if cid == 'latest_prev':
            self.page = (self.page - 1) % (max_page + 1)
        elif cid == 'latest_next':
            self.page = (self.page + 1) % (max_page + 1)
        else:
            return True

        await self.build_page()
        await itn.response.edit_message(view=self)
        return False
    
    async def on_timeout(self):
        action_row = self.children[0].children[-1]
        action_row.remove_item(action_row.children[0])
        action_row.remove_item(action_row.children[0])

        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            pass

class GroupbuySongSelect(discord.ui.Select):
    """Select menu for choosing a song."""
    
    def __init__(
        self, 
        cog: Music, 
        author: discord.User, 
        options: list[discord.SelectOption], 
        song_map: dict, 
    ):
        self.cog = cog
        self.author = author
        self.song_map = song_map
        
        super().__init__(
            placeholder='Select a song...', 
            min_values=1, 
            max_values=1, 
            options=options
        )

    async def callback(self, itn: discord.Interaction):
        song_id = self.values[0]
        song = self.song_map[song_id]

        layout_view = discord.ui.LayoutView()
        layout_view.add_item(GroupbuyContainer(self.cog, song))

        if self.author != itn.user:
            if song['groupbuy_info']['price'] == '':
                embed = discord.Embed(description=f'⚠️ {itn.user.mention}: **{song['name']}** has no **groupbuy** information', color=discord.Color.yellow())
                return await itn.response.send_message(embed=embed, ephemeral=True)
        
            return await itn.response.send_message(view=layout_view, embed=None, ephemeral=True)

        if song['groupbuy_info']['price'] == '':
            embed = discord.Embed(description=f'⚠️ {itn.user.mention}: **{song['name']}** has no **groupbuy** information', color=discord.Color.yellow())
            return await itn.response.edit_message(embed=embed, view=None)

        await itn.response.edit_message(view=layout_view, embed=None)

class GroupbuyContainer(discord.ui.Container):
    def __init__(self, cog: Music, song: dict):
        name = song['name']
        engineers = song['engineers']
        producers = song['producers']
        track_titles = [t for t in song['track_titles'] if t != name]
        image_url = song['image_url']
        era_name = song['era']['name']
        album = cog.ALBUMS.get(era_name)
        date_leaked = song['date_leaked'].replace('Surfaced', '').strip()

        accent_color = int(album['color'].lstrip('#'), 16) if album else 0x2B2D31
        super().__init__(accent_color=accent_color)

        groupbuy = song['groupbuy_info']
        gb_price = groupbuy['price']

        gb_start_date = groupbuy['start_date'].replace('Start Date', '').strip()
        gb_end_date = groupbuy['end_date'].replace('End Date', '').strip()
        gb_finished = groupbuy['finished']
        if gb_finished == '':
            gb_finished = False

        gb_extra_info = groupbuy['additional_info']

        section = discord.ui.Section(accessory=discord.ui.Thumbnail(media=JUICEWRLD_API + image_url))
        section.add_item(discord.ui.TextDisplay(
            f'### {name}\n'
            f'-# Alt Name(s): **{', '.join(track_titles) if track_titles else 'N/A'}**\n'
            f'-# Engineer(s): **{engineers}**\n'
            f'-# Producer(s): **{producers}**'
        ))

        self.add_item(section)
        self.add_item(discord.ui.Separator())
        self.add_item(discord.ui.TextDisplay(f'**Era**\n{album['name'] if album else era_name}'))
        self.add_item(discord.ui.TextDisplay(f'**Price**\n{gb_price}'))
        self.add_item(discord.ui.TextDisplay(f'**Start Date**\n{gb_start_date}'))
        self.add_item(discord.ui.TextDisplay(f'**End Date**\n{gb_end_date}'))
        
        if date_leaked != '':
            self.add_item(discord.ui.TextDisplay(f'**Surfaced**\n{date_leaked}'))

        self.add_item(discord.ui.TextDisplay(f'**Completed**\n{gb_finished}'))

        if gb_extra_info != '':
            self.add_item(discord.ui.TextDisplay(f'**Notes**\n{gb_extra_info}'))
        
        action_row = discord.ui.ActionRow()
        action_row.add_item(discord.ui.Button(label='Tracker', emoji='<:fart:1445127619744890911>', url='https://juicewrldapi.com/'))

        self.add_item(discord.ui.Separator())
        self.add_item(action_row)