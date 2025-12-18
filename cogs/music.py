import os
import discord
import logging
import aiohttp
import asyncio
from io import BytesIO
from datetime import datetime
from discord.ext import commands
from colorthief import ColorThief
from discord.ext.commands import Context
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from urllib.parse import quote
import utils.embeds as utils
from itertools import product
from moviepy import *
import re
import random

logger = logging.getLogger("discord_bot")

JUICEWRLD_API = "https://juicewrldapi.com"

DOWNLOAD_CACHE_FOLDER_NAME = "__download_cache"
HEARDLE_GAME_DURATION = 20
HEARDLE_CLIP_DURATION = 10
DEFAULT_SNIPPET_DURATION = 15
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
MAX_NAME_TRANSFORMATIONS = 6

class Music(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.default_avatar_url = "https://cdn.discordapp.com/embed/avatars/1.png"
        self.font_path = "DejaVuSans-ExtraLight.ttf"
        self.default_font = ImageFont.truetype(self.font_path, 24)
        self.font_small = ImageFont.truetype(self.font_path, 20)
        self.font_large = ImageFont.truetype(self.font_path, 40)
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
        self.heardle_whitelist = [
            1120028461713608834,  # WARITH
        ]
        self.heardle_blacklist = [
            657182369240973312  # chaos
        ]
        self.ongoing_heardle = []
        self.heardle_answers = {}
        self.snippet_debounce = {}

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

        self.track_name_transformations = {
            check_parantheses: [remove_parentheses],
            check_brackets: [remove_brackets],
            check_semi_brackets: [remove_semi_brackets],
            check_apostrophes: [remove_apostrophes],
            check_periods: [remove_periods, period_to_spaces],
            check_commas: [remove_commas, comma_to_spaces],
            check_question_marks: [remove_question_marks, question_mark_to_spaces],
        }

    async def cog_unload(self):
        await self.session.close()

    def can_test(ctx: commands.Context, cog=None):
        if cog is None:
            cog = ctx.cog

        return ctx.author.id in cog.testing_ids

    def can_heardle(ctx: commands.Context, cog=None):
        if cog is None:
            cog = ctx.cog

        return ctx.author.id in cog.heardle_whitelist

    def assert_download_cache(self):
        if not os.path.exists(DOWNLOAD_CACHE_FOLDER_NAME):
            os.makedirs(DOWNLOAD_CACHE_FOLDER_NAME)

    def special_url_encode(self, str):
        return quote(str).replace("/", "%2F").replace("%28", "(").replace("%29", ")")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

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
                                    elapsed_duration = (
                                        discord.utils.utcnow()
                                        - start_time.replace(tzinfo=None)
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

    async def request_filename(self, file_name: str, length: str):
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/files/browse/", params={"search": file_name}
        ) as response:
            if response.status != 200:
                return None
            data = await response.json()

        target_seconds = self.duration_to_seconds(length)
        if target_seconds == 0:
            return None

        return [
            song["path"]
            for song in data.get("items", [])
            if abs(self.duration_to_seconds(song.get("duration")) - target_seconds) <= 1
        ]

    async def check_file_name(self, song: dict):
        name = song.get("name", "Untitled").replace("*", "")
        raw_file_name = song.get("file_names") or name

        match = re.search(r"File Name:\s*(.+?)(?:\n|$)", raw_file_name)
        file_name = (match.group(1) if match else raw_file_name) + "."

        length = song.get("length", "0:00")

        downloads = await self.request_filename(file_name, length)
        og = bool(downloads) and file_name != name + "."

        if not downloads:
            downloads = await self.request_filename(name + ".", length)
            og = False

        return downloads, og

    def duration_to_seconds(self, duration: str):
        try:
            m, s = duration.split(":")
            return int(m) * 60 + int(s)
        except Exception:
            return 0

    async def create_song_view(self, song_data: dict, random_leak: bool = False):
        class SongContainer(discord.ui.Container):
            ALBUMS = {
                "JUTE": {"name": "JUICED UP THE EP", "color": "#FFE602"},
                "LND": {"name": "Legends Never Die", "color": "#F700FF"},
                "AFF": {"name": "affliction", "color": "#000000"},
                "HIH 9 9 9": {
                    "name": "Heartbroken In Hollywood 9 9 9",
                    "color": "#FF653E",
                },
                "JW 9 9 9": {"name": "JuiceWRLD 9 9 9", "color": "#FF2C2C"},
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

            FIELDS = {
                "file_names": "**File Name**",
                "session_titles": "**Session Title**",
                "session_tracking": "**Session Tracking**",
                "instrumentals": "**Instrumentals**",
                "recording_locations": "**Recording Location**",
                "record_dates": "**Recorded**",
                "preview_date": "**Previewed**",
                "date_leaked": "**Surfaced**",
                "release_date": "**Released**",
                "length": "**Length**",
                "leak_type": "**Category**",
                "bitrate": "**True Bitrate**",
            }

            RANDOM_LEAK_FIELDS = {
                "record_dates": "**Recorded**",
                "preview_date": "**Previewed**",
                "date_leaked": "**Surfaced**",
                "release_date": "**Released**",
                "length": "**Length**",
                "bitrate": "**True Bitrate**",
            }

            def __init__(
                self,
                song: dict,
                downloads=None,
                og: bool = False,
                random_leak: bool = False,
            ):
                name = song.get("name")
                track_titles = [t for t in song.get("track_titles", []) if t != name]
                producers = song.get("producers")
                engineers = song.get("engineers")
                era_name = song.get("era", []).get("name", "N/A")
                image_url = song.get("image_url")

                header = discord.ui.Section(
                    accessory=discord.ui.Button(
                        label="Tracker",
                        emoji="<:fart:1445127619744890911>",
                        url="https://juicewrldapi.com/",
                    )
                )
                header.add_item(
                    discord.ui.TextDisplay(f"### {name}\n{', '.join(track_titles)}")
                )

                thumb = discord.ui.Section(
                    accessory=discord.ui.Thumbnail(media=JUICEWRLD_API + image_url)
                )

                album = self.ALBUMS.get(era_name)
                accent_color = (
                    int(album["color"].lstrip("#"), 16) if album else 0x2B2D31
                )
                super().__init__(accent_color=accent_color)

                thumb.add_item(
                    discord.ui.TextDisplay(
                        f"Producer(s): **{producers}**\nEngineer(s): **{engineers}**"
                    )
                )
                thumb.add_item(
                    discord.ui.TextDisplay(
                        f'**Era**\n{album['name'] if album else era_name}\n'
                    )
                )

                self.add_item(header)
                self.add_item(discord.ui.Separator())
                self.add_item(thumb)

                field = self.FIELDS if not random_leak else self.RANDOM_LEAK_FIELDS

                for field_key, field_label in field.items():
                    value = song.get(field_key)
                    if value:
                        value = (
                            value.replace("Recorded", "")
                            .replace("First Previewed", "")
                            .replace("Surfaced", "")
                            .replace("Released", "")
                            .strip()
                        )
                        if value in ("N/A", "Unavailable"):
                            continue
                        self.add_item(discord.ui.TextDisplay(f"{field_label}\n{value}"))

                if (
                    downloads
                    and "session" not in str(song.get("leak_type", "")).lower()
                ):
                    main_url = (
                        "https://juicewrldapi.com/juicewrld/files/download/?path="
                    )

                    filtered_files = [
                        f
                        for f in downloads
                        if og
                        and "Unreleased Discography" not in f
                        or not og
                        and "Original Files" not in f
                    ]

                    row = discord.ui.ActionRow()

                    for i, path in enumerate(filtered_files):
                        ext = (
                            "OG " + path.split(".")[-1].upper()
                            if "Original Files" in path
                            else path.split(".")[-1].upper()
                        )

                        url = main_url + quote(path)
                        button = discord.ui.Button(label=ext, url=url)
                        row.add_item(button)

                        if (i + 1) % 5 == 0:
                            self.add_item(row)
                            row = discord.ui.ActionRow()

                    if len(row.children) > 0:
                        self.add_item(row)

        downloads, og = await self.check_file_name(song_data)

        layout_view = discord.ui.LayoutView(timeout=None)
        layout_view.add_item(SongContainer(song_data, downloads, og, random_leak))

        return layout_view

    @commands.command("leak", description="Search for a Juice WRLD leak by name")
    async def leak(self, ctx: commands.Context, *, query: str):
        # lets save eli some sanity and do this a bit nicer... haha maybe some other people will get the idea hahahahahahahahahahahah @ENVY
        async with self.session.get(
            JUICEWRLD_API + "/juicewrld/songs/", params={"search": query}
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

        # filter out the dogshit sessions cuz why are they even there we dgaf
        song_list = [
            song
            for song in data.get("results", [])
            if "session" not in song.get("leak_type").lower()
        ]

        if len(song_list) == 1:
            layout_view = await self.create_song_view(song_list[0])
            await ctx.send(view=layout_view)

        elif len(song_list) > 1:
            results = sorted(song_list, key=lambda s: s.get("track_titles"))[:25]
            song_map = {str(song["id"]): song for song in results}

            options = [
                discord.SelectOption(
                    label=(
                        lambda t: f'{t[0]} ({', '.join(t[1:])})' if len(t) > 1 else t[0]
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
            return await utils.Embeds.send_warning_embed(
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

    @commands.command(name="cdc")
    @commands.check_any(commands.is_owner(), commands.check(can_test))
    async def cleardownloadcache(self, ctx: commands.Context):
        self.ongoing_heardle = []
        self.snippet_debounce = {}

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
            await utils.Embeds.send_info_embed(
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
    ):
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as download_response:
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
            await utils.Embeds.send_error_embed(
                ctx.channel,
                ctx.author,
                "You do not have permission to use this command.",
            )
            return

        if (
            member.id not in self.heardle_answers
            or self.heardle_answers[member.id] is None
        ):
            await utils.Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                f"{member.display_name} does not have an ongoing Heardle game.",
            )
            return

        answer = self.heardle_answers[member.id]
        await utils.Embeds.send_info_embed(
            ctx.channel,
            ctx.author,
            f"The answer to {member.display_name}'s ongoing Heardle game is: **{answer}**",
        )

    @commands.command(
        name="heardle", help="Play a game of Heardle. Juice WRLD songs only."
    )
    async def heardle(self, ctx: commands.Context):
        if any(member.id in self.heardle_blacklist for member in ctx.message.mentions):
            await ctx.reply("nah nigga stick to your shitty heardle")
            return

        if ctx.author.id == 567401702190350347 and random.random() < 0.01:
            await utils.Embeds.send_error_embed(
                ctx.channel,
                ctx.author,
                f"You are too old for this command. Age detected: {random.randint(30, 40)}",
            )
            return

        if ctx.author.id in self.ongoing_heardle:
            await utils.Embeds.send_error_embed(
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
                    # TODO: make function for downloading temp mp3s for other methods (snippet, etc)
                    download_url = f"{JUICEWRLD_API}/juicewrld/files/download-compressed/?path={self.special_url_encode(path)}"
                    image_file_name = f"{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_temp_image_heardle.png"
                    image_url = ctx.author.display_avatar.url
                    async with session.get(image_url) as image_response:
                        if image_response.status == 200:
                            image_data = await image_response.read()
                            with open(image_file_name, "wb") as img_file:
                                img_file.write(image_data)

                    result, payload = await self.make_snippet(
                        image_file_name,
                        download_url,
                        f"{ctx.author.id}_heardle",
                        HEARDLE_CLIP_DURATION,
                    )
                    if result == False:
                        await handle_request_failed(ctx, payload)
                        self.handle_user_done_heardle(ctx.author.id)
                        return

                    embed = discord.Embed(
                        description=f"🎵 {ctx.author.mention}: Here is your clip, you have {HEARDLE_GAME_DURATION} seconds to guess. Please send a message of a song title to guess the song or type `exit` to quit the game."
                    )
                    message = await ctx.channel.send(
                        file=discord.File(payload), embed=embed
                    )

                best_track_title = self.get_most_acceptable_track_name(
                    song_data.get("name", "Unknown Title")
                )

                has_guessed = False
                attempt = 1
                acceptable_answers = []
                self.heardle_answers[ctx.author.id] = best_track_title
                for title in track_tiles:
                    acceptable_answers.extend(self.get_acceptable_track_names(title))

                async def update_timer_message(
                    message: discord.Message, full_name, start_time
                ):
                    try:
                        while True:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            hint_chars = int(
                                elapsed // 3
                            )  # reveal a character every 3 seconds, max 3 as curteousy of silmar
                            hint = full_name[:hint_chars]
                            for i in range(len(full_name) - hint_chars):
                                if full_name[i + hint_chars] == " ":
                                    hint += " "
                                else:
                                    hint += "?"
                            await message.edit(
                                content=f"Hint ({round(hint_chars)}/3): {hint}"
                            )

                            if hint_chars >= 3:
                                raise asyncio.CancelledError

                            await asyncio.sleep(1)
                    except asyncio.CancelledError:
                        # Task cancelled normally when game ends
                        return

                start_time = asyncio.get_event_loop().time()
                update_task = asyncio.create_task(
                    update_timer_message(message, best_track_title, start_time)
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
                        await utils.Embeds.send_warning_embed(
                            ctx.channel,
                            ctx.author,
                            f"Time's up! You didn't guess the song ({best_track_title}) in time.",
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
                        return
                    elif guess in acceptable_answers:
                        has_guessed = True
                    else:
                        attempt += 1
                await utils.Embeds.send_success_embed(
                    ctx.channel,
                    ctx.author,
                    f"Congratulations! You guessed the song correctly: **{best_track_title}**!",
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
            await utils.Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                "Please wait a bit before making another snippet.",
            )
            return
        self.snippet_debounce[ctx.author.id] = True

        async with self.session.get(
            f"{JUICEWRLD_API}/juicewrld/files/browse/?search={self.special_url_encode(query)}"
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
                await utils.Embeds.send_error_embed(
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
            download_url = f"{JUICEWRLD_API}/juicewrld/files/download-compressed/?path={self.special_url_encode(path)}"

            # TODO: Make the image something else
            image_file_name = (
                f"{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_temp_image_snippet.png"
            )
            image_url = ctx.author.display_avatar.url
            async with self.session.get(image_url) as image_response:
                if image_response.status == 200:
                    image_data = await image_response.read()
                    with open(image_file_name, "wb") as img_file:
                        img_file.write(image_data)

            result, payload = await self.make_snippet(
                image_file_name, download_url, f"{ctx.author.id}_snippet"
            )
            if result == False:
                await handle_request_failed(ctx, payload)
                self.handle_user_done_snippet(ctx.author.id)
                return

            message = await ctx.channel.send(file=discord.File(payload))

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
            await utils.Embeds.send_warning_embed(
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
        
        await utils.Embeds.send_success_embed(
            ctx.channel,
            ctx.author,
            f"Total pledges counted in {selected_channel.mention}: **${pledge_count}**",
            delete_after=None
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    logger.debug("Music cog initialized successfully")
