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

JUICEWRLD_API = 'https://juicewrldapi.com'
MAX_SEARCH_COUNT = 10 # Max number of search results for user to choose from. If more than this command fails
DOWNLOAD_CACHE_FOLDER_NAME = '__download_cache'
HEARDLE_GAME_DURATION = 20
HEARDLE_CLIP_DURATION = 10
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')
class Music(commands.Cog, name="Music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
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
        self.album_colors = {
            101: "#FFE602", #JUICE UP EP
            102: "#F700FF", #LEGENDS NEVER DIE
            103: "#000000", #AFFLICTION
            104: "#FF653E", #HEARTBROKEN IN HOLLYWOOD 999
            105: "#FF2C2C", #JUICE WRLD 999
            107: "#FF8800", #NOTHINGS DIFFERENT
            108: "#008CFF", #GBGR
            109: "#00FF94", #WOD
            110: "#FF9900", #DRFL
            111: "#2B2B2B", #OUTSIDERS
            112: "#00CCFF", #POST TODO: make this accurate
            113: "#EA00FF", # PRE PARTY EP
            114: "#EA00FF", # PRE PARTY EP EXTENDED
            115: "#2E2E2E", # FIGHTING DEMONS
            116: "#2E2E2E", # FIGHTING DEMONS DELUXE
            117: "#CC01FF", # THE PARTY NEVER ENDS
            # TODO: IM NOT DOING THE REST
        }
        self.testing_ids = [
            1095747082599530627, # ENVY
            1219090700407279656 # DUMB IDIOT
        ]
        self.ongoing_heardle = []

    def can_test(ctx: commands.Context, cog=None):
        if cog is None:
            cog = ctx.cog

        return ctx.author.id in cog.testing_ids
 
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

                return recent_tracks['recenttracks']['track'] if 'recenttracks' in recent_tracks else []

    @commands.group(name='lf', invoke_without_command=True)
    async def lastfm(self, ctx: Context) -> None:
        """Last.fm command group"""
        embed = discord.Embed(title='Last.fm', description='List of available subcommands')
        subcommands = [subcommand.name for subcommand in ctx.command.commands]
        if subcommands:
            embed.add_field(
                name="Subcommands",
                value=", ".join(subcommands),
                inline=False
            )
        await ctx.reply(embed=embed)

    @lastfm.command(name='set')
    async def set_lastfm(self, ctx: Context, username: str) -> None:
        """Set your Last.fm username"""
        user_id = int(ctx.author.id)
        await self.bot.database.set_lastfm_username(user_id, username)
        await ctx.reply(embed=discord.Embed(title='Last.fm Username Set', description=f'Your Last.fm username has been set to `{username}`.'))

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

    @lastfm.command(name='color')
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
        elif not (color.startswith('#') and len(color) == 7 and all(c in '0123456789ABCDEFabcdef' for c in color[1:])):
            await ctx.reply(embed=discord.Embed(
                title='Error',
                description='Please provide a valid color. Use HEX format (e.g., `#FF5733`) or a color name (e.g., `red`).',
                color=0x36393E
            ))
            return

        user_id = int(ctx.author.id)
        try:
            await self.bot.database.set_lastfm_embed_color(user_id, color)
            await ctx.reply(embed=discord.Embed(
                title='Color Set',
                description=f'Your embed color has been set to `{color}`.',
                color=int(color.replace("#", "0x"), 16)
            ))
        except ValueError as e:
            await ctx.reply(embed=discord.Embed(
                title='Error',
                description=str(e),
                color=discord.Color.red()
            ))

    @lastfm.command(name='votes')
    async def lastfm_stats(self, ctx: Context):
        """See your Last.fm vote statistics"""
        user_id = int(ctx.author.id)
        np_upvotes, np_downvotes = await self.bot.database.get_vote_stats(user_id, 'np')
        snp_upvotes, snp_downvotes = await self.bot.database.get_vote_stats(user_id, 'snp')
        jnp_upvotes, jnp_downvotes = await self.bot.database.get_vote_stats(user_id, 'jnp')
        embed_color = await self.bot.database.get_lastfm_embed_color(user_id)

        np_ratio = (np_upvotes - np_downvotes) / max(np_downvotes, 1) if np_downvotes >= np_downvotes else -(np_downvotes - np_downvotes) / max(np_downvotes, 1)
        snp_ratio = (snp_upvotes - snp_downvotes) / max(snp_downvotes, 1) if snp_upvotes >= snp_downvotes else -(snp_downvotes - snp_upvotes) / max(snp_upvotes, 1)
        jnp_ratio = (jnp_upvotes - jnp_downvotes) / max(jnp_downvotes, 1) if jnp_upvotes >= jnp_downvotes else -(jnp_downvotes - jnp_upvotes) / max(jnp_upvotes, 1)

        description = (
            f"**Now Playing (np)**:\n"
            f"Upvotes: {np_upvotes}\nDownvotes: {np_downvotes}\nRatio: {np_ratio:.3g}\n\n"
            f"**Spotify Now Playing (snp)**:\n"
            f"Upvotes: {snp_upvotes}\nDownvotes: {snp_downvotes}\nRatio: {snp_ratio:.3g}\n\n"
            f"**JuiceWRLD Now Playing (jnp)**:\n"
            f"Upvotes: {jnp_upvotes}\nDownvotes: {jnp_downvotes}\nRatio: {jnp_ratio:.3g}"
        )

        await ctx.reply(embed=discord.Embed(title='Last.fm Vote Statistics', description=description, color=embed_color))

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
                await ctx.reply(embed=discord.Embed(title='Error', description=f'You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.', color=0x36393E))
                return

        recent_tracks = await self.update_user_index(lastfm_username)
        if not recent_tracks:
            await ctx.reply("No recent tracks found.")
            return

        track = recent_tracks[0]
        track_name = track['name']
        artist_name = track['artist']['#text']

        async def get_track_playcount(lastfm_username, artist_name, track_name):
            url = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    track_info = await response.json()
                    return track_info.get('track', {}).get('userplaycount', 'N/A')

        playcount = await get_track_playcount(lastfm_username, artist_name, track_name)

        embed = discord.Embed(
            title="Current Song Plays",
            description=f"You've listened to **{track_name}** by **{artist_name}** {playcount} times.", 
            color=embed_color
        )
        await ctx.reply(embed=embed)

    @lastfm.command(name="toptentracks", aliases=['ttt'])
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
                await ctx.reply(embed=discord.Embed(title='Error', description=f'You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.', color=0x36393E))
                return

        url = f"http://ws.audioscrobbler.com/2.0/?method=user.gettoptracks&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json&limit=10"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()

                if "toptracks" not in data or "track" not in data["toptracks"]:
                    await ctx.reply("Couldn't retrieve top tracks. Please try again later.")
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
                    color=embed_color
                )
                embed.set_footer(text="Data from Last.fm")
                await ctx.reply(embed=embed)

    @lastfm.command(name="topartists", aliases=['tar'])
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
                await ctx.reply(embed=discord.Embed(title='Error', description=f'You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.', color=0x36393E))
                return

            url = f"http://ws.audioscrobbler.com/2.0/?method=user.gettopartists&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json&limit=10"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    top_artists_data = await response.json()

                    artists = top_artists_data.get('topartists', {}).get('artist', [])
                    if not artists:
                        await ctx.reply("Couldn't retrieve top artists. Please try again later.")
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
                        color=embed_color
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
                    return int(data.get("artist", {}).get("stats", {}).get("userplaycount", 0))

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
            description = "\n".join([
                f"**[{lfm_user}](https://www.last.fm/user/{lfm_user})**: `{playcount}`"
                for lfm_user, playcount in top_listening_users
            ])
        else:
            description = f"No one in the server has listened to **__{artist_name}__**."

        embed = discord.Embed(title=f"Who Knows {artist_name}?", description=description, color=embed_color)
        embed.set_thumbnail(url=await fetch_artist_image(lastfm_username))
        embed.add_field(name="Your Playcount", value=f"`{await fetch_playcount(lastfm_username)}`", inline=False)
        await ctx.reply(embed=embed)

    @commands.command(name='np')
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
                await ctx.reply(embed=discord.Embed(title='Error', description=f'You have not set your Last.fm username. Use `{prefix}lf set <username>` to set it.', color=0x36393E))
                return

            async def get_user_data(lastfm_username: str):
                url = f"https://ws.audioscrobbler.com/2.0/?method=user.getinfo&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        data = await response.json()
                        return data

            async def get_image_url(data, size='medium'):
                images = data.get('user', {}).get('image', [])
                for image in images:
                    if image['size'] == size:
                        return image['#text']
                return None

            async def get_recent_tracks(lastfm_username: str):
                url_recent = f'http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={lastfm_username}&api_key={LASTFM_API_KEY}&format=json'
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_recent) as response:
                        recent_tracks = await response.json()
                        return recent_tracks

            async def get_track_info(artist_name: str, track_name: str, lastfm_username: str):
                url_info = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url_info) as response:
                        recent_track_info = await response.json()

                        if 'track' not in recent_track_info:
                            logger.error(f"Last.fm API did not return track info: {recent_track_info}")
                            return None  

                        return recent_track_info

            user_data = await get_user_data(lastfm_username)

            lastfm_avatar_url = await get_image_url(user_data, 'large')

            recent_tracks = await get_recent_tracks(lastfm_username)
            if not recent_tracks['recenttracks']['track']:
                await ctx.reply(embed=discord.Embed(title='Error', description='No recent tracks found for the user.', color=0x36393E))
                return

            track = recent_tracks['recenttracks']['track'][0]
            track_name = track['name']
            artist_name = track['artist']['#text']
            album_name = track['album']['#text']
            track_url = track['url']

            track_info = await get_track_info(artist_name, track_name, lastfm_username)

            if not track_info or 'track' not in track_info:
                await ctx.reply(embed=discord.Embed(title='Error', description='Could not retrieve track information from Last.fm.', color=0x36393E))
                return

            playcount = track_info['track'].get('userplaycount', 'N/A')
            total_scrobbles = user_data['user']['playcount'] if 'playcount' in user_data['user'] else 'N/A'

            embed = discord.Embed(
                title='Now Playing',
                color=embed_color,
            )

            if 'image' in track and track['image']:
                thumbnail_url = track['image'][-1]['#text']
                embed.set_thumbnail(url=thumbnail_url)

            embed.add_field(name='Track:', value=f'[{track_name}]({track_url}) by {artist_name}', inline=False)
            embed.add_field(name='Album:', value=f'{album_name}', inline=False)
            embed.set_footer(text=f'Playcount: {playcount} - Total Scrobbles: {total_scrobbles}')
            embed.set_author(name=f"{lastfm_username} on Last.fm:", url=f'https://www.last.fm/user/{lastfm_username}', icon_url=lastfm_avatar_url)

        message = await ctx.reply(embed=embed)
        await message.add_reaction('👍')
        await asyncio.sleep(0.5)  # Small delay to ensure reactions are added correctly
        await message.add_reaction('👎')

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ['👍', '👎']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=15.0, check=check)
            if str(reaction.emoji) == '👍':
                await self.bot.database.log_vote(user.id, 'np', 'up')
            elif str(reaction.emoji) == '👎':
                await self.bot.database.log_vote(user.id, 'np', 'down')
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
        glow_radius: int = 8
    ):
        """
        Draws text onto base_image with a glow effect, 
        ensuring the *glyphs* start exactly at `position`, not the glow outline.
        """
        x, y = position

        glow_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        glow_draw  = ImageDraw.Draw(glow_layer)

        glow_draw.text((x, y), text, font=font, fill=glow_color)

        blurred = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        offset = glow_radius // 2

        base_image.paste(blurred, (-offset, -offset), blurred)

        draw = ImageDraw.Draw(base_image)
        draw.text((x, y), text, font=font, fill=text_color)

    def draw_glowing_rectangle(self, base_image, xy, radius, outline, fill, width=1, glow_color=None, glow_radius=8):
        """
        Draws a rounded rectangle with a glow effect onto base_image.
        xy = [x1, y1, x2, y2], the bounding box
        """

        glow_layer = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)

        if glow_color:
            glow_draw.rounded_rectangle(xy, radius=radius, fill=glow_color, outline=glow_color)

        blurred_layer = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        base_image.alpha_composite(blurred_layer)

        final_draw = ImageDraw.Draw(base_image)
        final_draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

    def dynamic_font(self, text, max_width, font_path, max_font_size):
        font_size = max_font_size
        font = ImageFont.truetype(font_path, font_size)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))  

        while draw.textbbox((0, 0), text, font=font)[2] > max_width and font_size > 8:
            font_size -= 1
            font = ImageFont.truetype(font_path, font_size)

        return font

    def glow_behind_image(self, base_image, cover_image, cover_mask, position, glow_color, glow_radius=10):
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

        temp_layer = Image.new("RGBA", base_image.size, (0,0,0,0))

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
            activity = next((activity for activity in member.activities if isinstance(activity, discord.Spotify)), None)
            if activity:
                return activity
            await asyncio.sleep(delay)
        return None

    @commands.command(name='snp')
    async def spotify_now_playing(self, ctx: commands.Context, member: discord.Member = None) -> None:
        """View what you are currently playing on Spotify."""
        member = member or ctx.author

        try:
            async with ctx.typing():
                activity = await self.find_spotify_activity(member)
                if activity is None:
                    await ctx.reply(embed=discord.Embed(
                        title='Error',
                        description='No Spotify activity found.\n\nMake sure your Spotify is connected to Discord and you are sharing activity status in privacy settings.\n\n**Note:** This command only works if you are listening to a song that is on Spotify. __Not local files.__',
                        color=0x36393E
                    ))
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
                    gradient_bg = self.create_vertical_gradient(width, height, color1, color2)
                    img = gradient_bg.convert("RGBA")
                else:

                    img = Image.new("RGBA", (800, 400), (30, 215, 96))

                if cover_url:
                    cover_image = Image.open(BytesIO(cover_data)).resize((200, 200))
                    rounded_cover = rounded_cover = Image.new("RGBA", cover_image.size, (0,0,0,0))
                    mask = Image.new("L", cover_image.size, 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, *cover_image.size),
                        radius=20,  
                        fill=255
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
                        glow_radius=12
                    )
                else:
                    text_color = "white"  

                max_text_width = width - 350

                title_font = self.dynamic_font(activity.title, max_text_width, self.font_path, max_font_size=40)
                artist_font = self.dynamic_font(activity.artist, max_text_width, self.font_path, max_font_size=30)
                album_font = self.dynamic_font(activity.album, max_text_width, self.font_path, max_font_size=24)
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
                    glow_radius=text_glow_radius
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=activity.artist,
                    position=(300, 150),
                    font=artist_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius
                )
                self.draw_glowing_text(
                    base_image=img,
                    text=activity.album,
                    position=(300, 200),
                    font=album_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius  
                )

                total_duration = activity.duration.total_seconds()
                elapsed_duration = (discord.utils.utcnow() - activity.start).total_seconds()
                progress = min(1, elapsed_duration / total_duration) if total_duration > 0 else 0

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
                    glow_radius=rectangle_glow_radius
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
                        glow_radius=rectangle_glow_radius
                    )

                minutes_elapsed, seconds_elapsed = divmod(int(elapsed_duration), 60)
                minutes_total, seconds_total = divmod(int(total_duration), 60)
                duration_text = f"{minutes_elapsed}:{seconds_elapsed:02} / {minutes_total}:{seconds_total:02}"
                time_font = self.dynamic_font(duration_text, max_text_width, self.font_path, max_font_size=20)
                self.draw_glowing_text(
                    base_image=img,
                    text=duration_text,
                    position=(300, 280),
                    font=time_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius
                )

                async def get_track_playcount(lastfm_username, artist_name, track_name):
                    url = f"http://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key={LASTFM_API_KEY}&artist={artist_name}&track={track_name}&username={lastfm_username}&format=json"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as response:
                            track_info = await response.json()
                            return track_info.get('track', {}).get('userplaycount', 'N/A')

                user_id = int(member.id)
                try:
                    lastfm_username = await self.bot.database.get_lastfm_username(user_id)

                    if lastfm_username:
                        playcount = await get_track_playcount(lastfm_username, activity.artist, activity.title)
                    else:
                        playcount = "N/A"
                except ValueError:
                    playcount = "N/A"

                playcount_text = f"Plays: {playcount}"
                playcount_font = self.dynamic_font(playcount_text, max_text_width, self.font_path, max_font_size=20)
                self.draw_glowing_text(
                    base_image=img,
                    text=playcount_text,
                    position=(300, 320),
                    font=playcount_font,
                    text_color=text_color,
                    glow_color=glow_color,
                    glow_radius=text_glow_radius
                )

                image_bytes = BytesIO()
                img.save(image_bytes, format='PNG')
                image_bytes.seek(0)
                file = discord.File(fp=image_bytes, filename='now_playing.png')
                embed = discord.Embed(
                    title='Now Playing on Spotify',
                    description=f'[{activity.title}]({activity.track_url})',
                    color=discord.Color.green()
                )
                embed.set_image(url="attachment://now_playing.png")
            message = await ctx.reply(embed=embed, file=file)
            await message.add_reaction('👍')
            await asyncio.sleep(0.5)  # Small delay to ensure reactions are added correctly
            await message.add_reaction('👎')

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ['👍', '👎']

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=15.0, check=check)
                if str(reaction.emoji) == '👍':
                    await self.bot.database.log_vote(user.id, 'np', 'up')
                elif str(reaction.emoji) == '👎':
                    await self.bot.database.log_vote(user.id, 'np', 'down')
            except asyncio.TimeoutError:
                logger.debug("No reaction received within the timeout period.")
            except asyncio.CancelledError:
                logger.debug("Reaction task was cancelled.")

        except aiohttp.ClientError:
            await ctx.reply(embed=discord.Embed(
                title='Error',
                description='There was an error fetching the cover art for the currently playing song. This should not happen. Please try again later.',
                color=0x36393E
            ))

    @commands.command(name='jnp')
    async def juicewrld_now_playing(self, ctx: commands.Context, member: discord.Member = None) -> None:
        """View what you are currently playing on JuiceWRLD API desktop app."""
        member = member or ctx.author

        try:
            async with ctx.typing():
                async with aiohttp.ClientSession() as session:
                    async with session.get('https://m.juicewrldapi.com/analytics/now-playing/discord', params={'discord_user_id': member.id}) as response:
                        if response.status == 404:
                            try:
                                error_data = await response.json()
                                if not error_data.get('is_linked'):
                                    await ctx.reply(embed=discord.Embed(
                                        title='Account Not Linked',
                                        description=f'{member.mention}\'s Discord account is not linked to JuiceWRLD API.\n\n**To link your account:**\n1. Open the JuiceWRLD API desktop app\n2. Go to account and generate a device pairing code\n3. Then use the jlink command with your code!',
                                        color=0x36393E
                                    ))
                                    return
                            except:
                                pass
                        
                        if response.status != 200:
                            await ctx.reply(embed=discord.Embed(
                                title='Error',
                                description='Failed to fetch now playing data from JuiceWRLD API.',
                                color=0x36393E
                            ))
                            return

                        data = await response.json()

                        if not data.get('is_linked'):
                            await ctx.reply(embed=discord.Embed(
                                title='Account Not Linked',
                                description=f'🚫 {member.mention}\'s Discord account is not linked to JuiceWRLD API.\n\n**To link your account:**\n1. Open the JuiceWRLD API desktop app\n2. Go to settings and connect your Discord account\n3. Then try this command again!',
                                color=0x36393E
                            ))
                            return

                        now_playing = data.get('now_playing')
                        if not now_playing or not now_playing.get('is_playing'):
                            await ctx.reply(embed=discord.Embed(
                                title='Now Playing',
                                description='No music currently playing.\n\nStart playing music on your desktop app to see it here!',
                                color=0x808080
                            ))
                            return

                        title = now_playing.get('track') or now_playing.get('title', 'Unknown Title')
                        artist = now_playing.get('artist', 'Unknown Artist')
                        album = now_playing.get('album', 'Unknown Album')
                        cover_url = now_playing.get('album_art_url') or now_playing.get('cover_url') or now_playing.get('image_url')
                        track_url = now_playing.get('track_url') or now_playing.get('url', '')
                        duration_raw = now_playing.get('duration', 0)
                        duration_as_seconds = duration_raw
                        duration_as_milliseconds = duration_raw / 1000.0
                        if duration_raw > 600 and duration_as_milliseconds <= 600:
                            duration_seconds = duration_as_milliseconds
                        else:
                            duration_seconds = duration_as_seconds
                        start_time_str = now_playing.get('timestamp') or now_playing.get('start_time')
                        position = now_playing.get('position', 0)

                        if cover_url:
                            async with aiohttp.ClientSession() as cover_session:
                                async with cover_session.get(cover_url) as cover_response:
                                    cover_data = await cover_response.read()

                            cover_image = Image.open(BytesIO(cover_data)).resize((200, 200))
                            color_thief = ColorThief(BytesIO(cover_data))

                            palette = color_thief.get_palette(color_count=2)
                            if len(palette) < 2:
                                palette = [color_thief.get_color(quality=10)] * 2
                            color1, color2 = palette[0], palette[1]

                            width, height = 800, 400
                            gradient_bg = self.create_vertical_gradient(width, height, color1, color2)
                            img = gradient_bg.convert("RGBA")
                        else:
                            width, height = 800, 400
                            img = Image.new("RGBA", (800, 400), (30, 215, 96))
                            color1 = (30, 215, 96)

                        if cover_url:
                            cover_image = Image.open(BytesIO(cover_data)).resize((200, 200))
                            rounded_cover = Image.new("RGBA", cover_image.size, (0,0,0,0))
                            mask = Image.new("L", cover_image.size, 0)
                            ImageDraw.Draw(mask).rounded_rectangle(
                                (0, 0, *cover_image.size),
                                radius=20,
                                fill=255
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
                                glow_radius=12
                            )
                        else:
                            text_color = "white"

                        max_text_width = width - 350

                        title_font = self.dynamic_font(title, max_text_width, self.font_path, max_font_size=40)
                        artist_font = self.dynamic_font(artist, max_text_width, self.font_path, max_font_size=30)
                        album_font = self.dynamic_font(album, max_text_width, self.font_path, max_font_size=24)
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
                            glow_radius=text_glow_radius
                        )
                        self.draw_glowing_text(
                            base_image=img,
                            text=artist,
                            position=(300, 150),
                            font=artist_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius
                        )
                        self.draw_glowing_text(
                            base_image=img,
                            text=album,
                            position=(300, 200),
                            font=album_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius
                        )

                        elapsed_duration = 0
                        if duration_seconds > 0:
                            if position > 0:
                                elapsed_duration = position / 1000.0
                            elif start_time_str:
                                try:
                                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                                    elapsed_duration = (discord.utils.utcnow() - start_time.replace(tzinfo=None)).total_seconds()
                                except:
                                    elapsed_duration = 0
                            progress = min(1, elapsed_duration / duration_seconds) if duration_seconds > 0 else 0
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
                            glow_radius=rectangle_glow_radius
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
                                glow_radius=rectangle_glow_radius
                            )

                        if duration_seconds > 0:
                            minutes_elapsed, seconds_elapsed = divmod(int(elapsed_duration), 60)
                            minutes_total, seconds_total = divmod(int(duration_seconds), 60)
                            duration_text = f"{minutes_elapsed}:{seconds_elapsed:02} / {minutes_total}:{seconds_total:02}"
                        else:
                            duration_text = "0:00 / 0:00"

                        time_font = self.dynamic_font(duration_text, max_text_width, self.font_path, max_font_size=20)
                        self.draw_glowing_text(
                            base_image=img,
                            text=duration_text,
                            position=(300, 280),
                            font=time_font,
                            text_color=text_color,
                            glow_color=glow_color,
                            glow_radius=text_glow_radius
                        )

                        image_bytes = BytesIO()
                        img.save(image_bytes, format='PNG')
                        image_bytes.seek(0)
                        file = discord.File(fp=image_bytes, filename='now_playing.png')
                        embed_color = int('{:02x}{:02x}{:02x}'.format(color1[0], color1[1], color1[2]), 16)
                        embed = discord.Embed(
                            title='Now Playing on JuiceWRLD API',
                            description=f'[{title}]({track_url})' if track_url else title,
                            color=embed_color
                        )
                        embed.set_image(url="attachment://now_playing.png")

            message = await ctx.reply(embed=embed, file=file)
            await message.add_reaction('👍')
            await asyncio.sleep(0.5)
            await message.add_reaction('👎')

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ['👍', '👎']

            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=15.0, check=check)
                if str(reaction.emoji) == '👍':
                    await self.bot.database.log_vote(user.id, 'jnp', 'up')
                elif str(reaction.emoji) == '👎':
                    await self.bot.database.log_vote(user.id, 'jnp', 'down')
            except asyncio.TimeoutError:
                logger.debug("No reaction received within the timeout period.")
            except asyncio.CancelledError:
                logger.debug("Reaction task was cancelled.")

        except aiohttp.ClientError:
            await ctx.reply(embed=discord.Embed(
                title='Error',
                description='There was an error fetching data from JuiceWRLD API. Please try again later.',
                color=0x36393E
            ))
        except Exception as e:
            logger.error(f"Error in jnp command: {e}")
            await ctx.reply(embed=discord.Embed(
                title='Error',
                description='An unexpected error occurred. Please try again later.',
                color=0x36393E
            ))

    @commands.command(name='jlink')
    async def juicewrld_link(self, ctx: commands.Context, code: str = None) -> None:
        """Link your Discord account to JuiceWRLD API using a pairing code."""
        prefix = await self.bot.database.get_prefix(ctx.guild.id) if ctx.guild else "!"

        if code and code.lower() == 'help':
            embed = discord.Embed(
                title='Link Account Command',
                description='Link your Discord account to your JuiceWRLDAPI account using a pairing code',
                color=0x5865f2
            )
            embed.add_field(name='Usage', value=f'`{prefix}jlink <code>` - Link account with pairing code', inline=False)
            embed.add_field(name='Examples', value=f'`{prefix}jlink ABC12345` - Link using code ABC12345', inline=False)
            embed.add_field(
                name='How to get a code',
                value='Generate a pairing code from your JuiceWRLDAPI desktop app\nCodes expire after 10 minutes',
                inline=False
            )
            await ctx.reply(embed=embed)
            return

        if not code:
            await ctx.reply(embed=discord.Embed(
                title='Missing Code',
                description='Please provide a pairing code.',
                color=discord.Color.red()
            ))
            return

        pairing_code = code.upper()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://m.juicewrldapi.com/auth/discord/bot-link',
                    json={'code': pairing_code, 'discord_user_id': ctx.author.id}
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and data.get('user'):
                            user_data = data['user']
                            embed = discord.Embed(
                                title='Account Linked Successfully',
                                description=f'Your Discord account has been linked to **{user_data.get("username", "Unknown")}**',
                                color=0x00ff00
                            )
                            embed.add_field(name='Username', value=user_data.get('username', 'Unknown'), inline=True)
                            embed.add_field(name='Status', value='Active ✓', inline=True)
                            embed.set_footer(text=f'You can now use {prefix}jnp to show your currently playing song!')
                            await ctx.reply(embed=embed)
                            return

                    error_message = 'Invalid or expired pairing code'
                    try:
                        error_data = await response.json()
                        if error_data and error_data.get('error'):
                            error_message = error_data['error']
                    except:
                        if response.status == 400:
                            error_message = 'Bad request. Please check your pairing code and try again.'
                        elif response.status >= 500:
                            error_message = 'Server error. Please try again later.'

                    embed = discord.Embed(
                        title='Link Failed',
                        description=error_message,
                        color=discord.Color.red()
                    )
                    embed.add_field(
                        name='Troubleshooting',
                        value='• Make sure the code is correct\n• Codes expire after 10 minutes\n• Generate a new code from your desktop app',
                        inline=False
                    )
                    await ctx.reply(embed=embed)

        except aiohttp.ClientError:
            await ctx.reply(embed=discord.Embed(
                title='Link Failed',
                description='There was an error connecting to JuiceWRLD API. Please try again later.',
                color=discord.Color.red()
            ))
        except Exception as e:
            logger.error(f"Error in jlink command: {e}")
            await ctx.reply(embed=discord.Embed(
                title='Link Failed',
                description='An unexpected error occurred. Please try again later.',
                color=discord.Color.red()
            ))

    @commands.command(name='leak', description="Search for a Juice WRLD leak by name")
    async def leak(self, ctx: commands.Context, *, query: str) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{JUICEWRLD_API}/juicewrld/songs/?search={self.special_url_encode(query)}') as response:
                async def handle_request_failed(ctx, code=None):
                    embed = discord.Embed(description="Request failed. Please try again later.", color=discord.Color.red())
                    if code:
                        embed.set_image(url=f"https://http.cat/{code}")
                    await ctx.reply(embed=embed, delete_after=5)

                if response.status != 200:
                    await handle_request_failed(ctx, response.status)
                    return

                data = await response.json()

                results = data.get("results", [])
                count = data.get("count", 0)

                if count == 0:
                    await utils.Embeds.send_warning_embed(ctx.channel, ctx.author, f"I couldnt find a song with the name: `{query}`")
                    return
                
                class InformationView(discord.ui.LayoutView):
                    def __init__(self, song: dict, downloads = None):
                        super().__init__(timeout=None)
                        self.persistent = True
                        self.container = Information(song, downloads)
                        self.add_item(self.container)

                class Information(discord.ui.Container):
                    def __init__(self, song: dict, downloads = None):
                        name = song.get('name')
                        producers = song.get('producers')
                        engineers = song.get('engineers')
                        era = song.get('era')
                        fileName = song.get('file_names')
                        sessionTitle = song.get('session_titles')
                        sessionTrack = song.get('session_tracking')
                        instrumentals = song.get('instrumentals')
                        location = song.get('recording_locations')
                        recorded = song.get('record_dates').replace("Recorded", "").strip() if song.get('record_dates') else None
                        previewed = song.get('preview_date').replace("First Previewed", "").strip() if song.get('preview_date') else None
                        surfaced = song.get('date_leaked').replace("Surfaced", "").strip() if song.get('date_leaked') else None
                        released = song.get('release_date').replace("Released", "").strip() if song.get('release_date') else None
                        length = song.get('length')
                        category = song.get('leak_type')
                        image_url = song.get('image_url')
                        bitrate = song.get('bitrate')

                        Header = discord.ui.Section(accessory=discord.ui.Button(label="Tracker", url="https://juicewrldapi.com/"))
                        Header.add_item(discord.ui.TextDisplay(f"### {name}\n{", ".join([t for t in song.get('track_titles', []) if t != name])}"))

                        Separate = discord.ui.Separator()
                        
                        Thumb = discord.ui.Section(accessory=discord.ui.Thumbnail(media=JUICEWRLD_API + image_url))
                        Thumb.add_item(discord.ui.TextDisplay(f"Producer(s): **{producers}**\nEngineer(s): **{engineers}**"))

                        era = song.get('era', {})
                        era_name = era.get('name', 'N/A')

                        albums = {
                            "JUTE": {"name": "JUICED UP THE EP", "color": "#FFE602"},
                            "LND": {"name": "Legends Never Die", "color": "#F700FF"},
                            "AFF": {"name": "affliction", "color": "#000000"},
                            "HIH 9 9 9": {"name": "Heartbroken In Hollywood 9 9 9", "color": "#FF653E"},
                            "JW 9 9 9": {"name": "JuiceWRLD 9 9 9", "color": "#FF2C2C"},
                            "ND </3": {"name": "NOTHING'S DIFFERENT </3", "color": "#FF8800"},
                            "GB&GR": {"name": "Goodbye & Good Riddance", "color": "#008CFF"},
                            "GB&GR (AE)": {"name": "Goodbye & Good Riddance (Anniversary Edition)", "color": "#008CFF"},
                            "GB&GR (5YAE)": {"name": "Goodbye & Good Riddance (5 Year Anniversary Edition)", "color": "#008CFF"},
                            "WOD": {"name": "WRLD ON DRUGS", "color": "#00FF94"},
                            "DRFL": {"name": "Death Race For Love", "color": "#FF9900"},
                            "DRFL (BTV)": {"name": "Death Race For Love (Bonus Track Version)", "color": "#FF9900"},
                            "OUT": {"name": "Outsiders", "color": "#2B2B2B"},
                            "POST": {"name": "Posthumous", "color": "#00CCFF"},
                            "TPP": {"name": "The Pre-Party", "color": "#EA00FF"},
                            "TPP (EE)": {"name": "The Pre-Party (Extended Edition)", "color": "#EA00FF"},
                            "FD": {"name": "Fighting Demons", "color": "#2E2E2E"},
                            "FD (CE)": {"name": "Fighting Demons (Complete Edition)", "color": "#2E2E2E"},
                            "FD (EE)": {"name": "Fighting Demons (Extended Edition)", "color": "#2E2E2E"},
                            "FD (DDE)": {"name": "Fighting Demons (Digital Deluxe Edition)", "color": "#2E2E2E"},
                            "TPNE": {"name": "The Party Never Ends", "color": "#CC00FF"},
                        }

                        def getAlbum(era: str):
                            album = albums.get(era)
                            if album is None:
                                return None, era
                            return album["color"], album["name"]

                        color, era_formatted = getAlbum(era_name)

                        accent_color = int(color.lstrip("#"), 16) if color else 0x2B2D31
                        super().__init__(accent_color=accent_color)

                        if not era:
                            Thumb.add_item(discord.ui.TextDisplay(f"**Project**\n{era_formatted}"))
                        else:
                            Thumb.add_item(discord.ui.TextDisplay(f"**Era**\n{era_formatted}"))

                        FileName = discord.ui.TextDisplay(f"**File Name**\n{fileName}")
                        SessionTitle = discord.ui.TextDisplay(f"**Session Title**\n{sessionTitle}")
                        SessionTrack = discord.ui.TextDisplay(f"**Session Tracking**\n{sessionTrack}")
                        Instrumental = discord.ui.TextDisplay(f"**Instrumentals**\n{instrumentals}")
                        Location = discord.ui.TextDisplay(f"**Recording Location**\n{location}")
                        Recorded = discord.ui.TextDisplay(f"**Recorded**\n{recorded}")
                        Previewed = discord.ui.TextDisplay(f"**Previewed**\n{previewed}")
                        Surfaced = discord.ui.TextDisplay(f"**Surfaced**\n{surfaced}")
                        Released = discord.ui.TextDisplay(f"**Released**\n{released}")
                        Length = discord.ui.TextDisplay(f"**Length**\n{length}")
                        Category = discord.ui.TextDisplay(f"**Category**\n{category}")
                        Bitrate = discord.ui.TextDisplay(f"**True Bitrate**\n{bitrate}")

                        self.add_item(Header)
                        self.add_item(Separate)
                        self.add_item(Thumb)

                        if fileName and "N/A" not in str(fileName):
                            self.add_item(FileName)
                        if sessionTitle and "N/A" not in str(sessionTitle):
                            self.add_item(SessionTitle)
                        if sessionTrack and "N/A" not in str(sessionTrack):
                            self.add_item(SessionTrack)
                        if instrumentals and "N/A" not in str(instrumentals):
                            self.add_item(Instrumental)
                        if location:
                            self.add_item(Location)
                        if recorded:
                            self.add_item(Recorded)
                        if previewed:
                            self.add_item(Previewed)
                        if surfaced:
                            self.add_item(Surfaced)
                        if released:
                            self.add_item(Released)
                        if length:
                            self.add_item(Length)
                        if category:
                            self.add_item(Category)
                        if bitrate and "Unavailable" not in bitrate:
                            self.add_item(Bitrate)

                        if downloads:
                            main = "https://juicewrldapi.com/juicewrld/files/download/?path="
                            action_rows = []
                            current_row = discord.ui.ActionRow()

                            if length:
                                target_seconds = duration_to_seconds(length)

                                for i, file in enumerate(downloads):
                                    file_duration = duration_to_seconds(file.get("duration", "0"))

                                    if fileName and "N/A" not in fileName:
                                        if "Unreleased Discography" in file.get('path', ''):
                                            continue
                                    else:
                                        if "Original Files" in file.get('path', ''):
                                            continue

                                    if abs(file_duration - target_seconds) > 1:
                                        continue

                                    path = file["path"]
                                    ext = path.split('.')[-1].upper()
                                    encoded_path = quote(path)
                                    url = main + encoded_path
                                    button = discord.ui.Button(label=ext, url=url)
                                    current_row.add_item(button)

                                    if (i + 1) % 5 == 0:
                                        action_rows.append(current_row)
                                        current_row = discord.ui.ActionRow()

                                if len(current_row.children) > 0:
                                    action_rows.append(current_row)

                                for row in action_rows:
                                    self.add_item(row)

                results = data.get('results', [])

                def extract_file_name(text: str) -> str | None:
                    match = re.search(r"File Name:\s*(.+?)(?:\n|$)", text)
                    return match.group(1).strip() if match else None

                async def checkFileName(song: dict):
                    fileName = song.get('file_names')
                    if "File Name:" in fileName:
                        fileName = extract_file_name(fileName)
                    if fileName == "N/A" or not fileName:
                        fileNames = song.get('track_titles', [])
                        fileName = str(fileNames[0]).replace('*', '')
                    fileName += "."
                    downloads = await request_filename(fileName)
                    return downloads if downloads else None

                async def request_filename(filename: str) -> list[dict[str, str]] | None:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(f"https://juicewrldapi.com/juicewrld/files/browse/?search={filename}") as response:
                                if response.status == 200:
                                    data = await response.json()
                                    
                                    paths = []
                                    for item in data.get('items', []):
                                        path = item.get('path')
                                        duration = item.get('duration')
                                        if path and duration:
                                            paths.append({'path': path, 'duration': duration})
                                    return paths
                                else:
                                    return None
                    except Exception:
                        return None

                def duration_to_seconds(duration: str) -> int:
                    if ":" in duration:
                        parts = duration.strip().split(":")
                        if len(parts) == 2:
                            minutes, seconds = map(int, parts)
                            return minutes * 60 + seconds
                        elif len(parts) == 3:
                            hours, minutes, seconds = map(int, parts)
                            return hours * 3600 + minutes * 60 + seconds
                    try:
                        return int(float(duration))
                    except:
                        return -1

                if count != 1:
                    options = []
                    bullshit_map = {}

                    sorted_songs = sorted(results, key=lambda s: (s.get("track_titles") or ["Untitled"])[0].lower())

                    for song in sorted_songs[:25]:

                        track_titles = song.get("track_titles", [])

                        first_track = track_titles[0] if track_titles else "Untitled"
                        aliases = track_titles[1:]

                        if aliases:
                            label = f"{first_track} ({', '.join(aliases)})"
                        else:
                            label = first_track

                        if len(label) > 100:
                            label = label[:97] + "..."

                        song_id = str(song["id"])

                        options.append(discord.SelectOption(label=label, value=song_id))
                        bullshit_map[song_id] = [(None, song)]

                    class SongSelect(discord.ui.Select):
                        def __init__(self, options, annoying_dogshit):
                            self.bullshit_map = annoying_dogshit
                            super().__init__(placeholder="Select a song...", min_values=1, max_values=1, options=options)

                        async def callback(self, interaction: discord.Interaction):
                            song_id = self.values[0]
                            song = self.bullshit_map[song_id][0][1]
                            downloads = await checkFileName(song)
                            view = InformationView(song, downloads)
                            return await interaction.response.edit_message(embed=None, view=view)

                    view = discord.ui.View(timeout=None)
                    view.add_item(SongSelect(options, bullshit_map))

                    embed = discord.Embed(description=f"{ctx.author.mention}: Multiple **selections** found with your **search**")
                    return await ctx.reply(embed=embed, view=view)
                else:
                    downloads = await checkFileName(results[0])
                    await ctx.reply(view=InformationView(results[0], downloads))

    @commands.command(aliases=['rleak'], description="Get a random Juice WRLD leak")
    async def randomleak(self, ctx: commands.Context) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{JUICEWRLD_API}/juicewrld/radio/random/') as response:
                async def handle_request_failed(ctx, code=None):
                    embed = discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red()
                    )
                    if code:
                        embed.set_image(url=f"https://http.cat/{code}")
                    await ctx.reply(embed=embed, delete_after=5)

                if response.status != 200:
                    await handle_request_failed(ctx, response.status)
                    return

                data = await response.json()

                song_data = data.get('song', None)
                if not song_data:
                    await handle_request_failed(ctx)
                    return

                song_data = data.get('song', {})
                song_name = song_data.get('name', 'Unknown Title')
                era = song_data.get('era', {})
                era_id = era.get('id', 0)
                era_name = era.get('name', 'Unknown Era')
                era_description = era.get('description', '')
                alt_names = [name for name in song_data.get('track_titles', []) if name != song_name]
                image_url = song_data.get('image_url', '')
                producers = song_data.get('producers', "N/A")
                length = song_data.get('length', 0)
                path = data.get('path', '')

                color = self.album_colors.get(era_id, "#FFFFFF")
                color_int = int(color.replace("#", "0x"), 16)

                # TODO: make this somewhere else
                class SongView(discord.ui.View):
                    def __init__(self, download_url):
                        super().__init__()
                        self.add_item(discord.ui.Button(label="Download", url=download_url))

                embed = discord.Embed(
                    title=f'**{song_name}**',
                    color=discord.Color(value=color_int)
                )
                embed.set_author(name=f'{ctx.author.display_name} - Random Juice WRLD Leak', icon_url=ctx.author.display_avatar.url)
                if len(alt_names) > 0:
                    embed.add_field(
                        name='Alternative Name(s)',
                        value=', '.join(alt_names) if alt_names else 'N/A',
                        inline=False
                        )
                if len(image_url) > 0:
                    embed.set_thumbnail(url=JUICEWRLD_API + image_url)

                embed.add_field(
                    name='Era',
                    value=f'{era_description} ({era_name})',
                    inline=False
                )
                embed.add_field(
                    name='Producer(s)',
                    value=producers,
                    inline=False
                )
                if len(length) > 0:
                    embed.add_field(
                        name='Length',
                        value=f"{length}",
                        inline=False
                    )

                last_slash_index = path.rfind('/')
                file_name = path[last_slash_index + 1:] if last_slash_index != -1 else path
                path = path[:last_slash_index] if last_slash_index != -1 else path

                download_url = f'{JUICEWRLD_API}/files/{self.special_url_encode(path)}?highlight={self.special_url_encode(file_name)}'
                view = SongView(download_url)
                message = await ctx.reply(embed=embed, view=view)
                await message.add_reaction("👍")
                await message.add_reaction("👎")

    @commands.command(name="hcc")
    @commands.check_any(commands.has_permissions(manage_guild=True), commands.check(can_test))
    async def heardleclearcache(self, ctx: commands.Context):
        self.ongoing_heardle = []

        num_file_deleted = 0
        self.assert_download_cache()
        for file in os.listdir(DOWNLOAD_CACHE_FOLDER_NAME):
            if "_heardle" in file:
                file_path = os.path.join(DOWNLOAD_CACHE_FOLDER_NAME, file)
                try:
                    num_file_deleted += 1
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")

        await utils.Embeds.send_success_embed(
            ctx.channel,
            ctx.author,
            f"Deleted {num_file_deleted} file(s)."
        )

    def get_acceptable_track_names(self, orig_name: str):
        name = orig_name.lower().strip()
        
        def remove_parentheses(s):
            return re.sub(r'\(.*?\)', '', s).strip()
        
        def remove_brackets(s):
            return re.sub(r'\[.*?\]', '', s).strip()
        
        def remove_apostrophes(s):
            return s.replace("'", "")
        
        def apostrophes_to_spaces(s):
            return s.replace("'", " ")
        
        def remove_periods(s):
            return s.replace(".", "")

        def period_to_spaces(s):
            return s.replace(".", " ")
        
        def remove_commas(s):
            return s.replace(",", "")

        def comma_to_spaces(s):
            return s.replace(",", " ")

        transformations = [
            lambda x: x,
            remove_parentheses,
            remove_brackets,
            remove_apostrophes,
            #apostrophes_to_spaces,
            remove_periods,
            period_to_spaces,
            remove_commas,
            #comma_to_spaces
        ]

        # try all combos
        results = set()
        for r in range(1, len(transformations)+1):
            for combo in product(transformations, repeat=r):
                temp = name
                for func in combo:
                    temp = func(temp)
                results.add(temp.strip())
        
        return sorted(results)

    def handle_user_done_heardle(self, user_id: int):
        if user_id in self.ongoing_heardle:
            self.ongoing_heardle.remove(user_id)

        # Delete user related files
        self.assert_download_cache()
        for file in os.listdir(DOWNLOAD_CACHE_FOLDER_NAME):
            if f"{user_id}_" in file and "_heardle" in file:
                file_path = os.path.join(DOWNLOAD_CACHE_FOLDER_NAME, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")

    @commands.command(aliases=["hstats"])
    async def heardlestats(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        
        embed = discord.Embed(
            description=f"Heardle Stats for {member.display_name}"
        )
        embed.add_field(
            name="Wins",
            value=999, # RIP JUICE WRLD
            inline=True
        )
        embed.add_field(
            name="Losses",
            value=999, # RIP JUICE WRLD
            inline=True
        )
        embed.add_field(
            name="W/L Ratio",
            value=999 / 1400, # shoutout juice wrld x trippie redd freestyle
            inline=True
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        await ctx.reply(embed=embed)

    @commands.command(name="heardle", help="Play a game of Heardle. Juice WRLD songs only.")
    @commands.check_any(commands.has_permissions(manage_guild=True), commands.check(can_test), commands.has_role(1414742766386413590))
    async def heardle(self, ctx: commands.Context):
        if ctx.author.id in self.ongoing_heardle:
            await utils.Embeds.send_warning_embed(
                ctx.channel,
                ctx.author,
                "You already have an ongoing game of Heardle!"
            )
            return
        
        # clear existing files
        self.handle_user_done_heardle(ctx.author.id)
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{JUICEWRLD_API}/juicewrld/radio/random/') as response:
                async def handle_request_failed(ctx, code=None):
                    embed = discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red()
                    )
                    if code:
                        embed.set_image(url=f"https://http.cat/{code}")
                    await ctx.reply(embed=embed, delete_after=5)

                if response.status != 200:
                    await handle_request_failed(ctx, response.status)
                    return

                data = await response.json()

                song_data = data.get('song', None)
                if not song_data:
                    await handle_request_failed(ctx)
                    return
                
                self.ongoing_heardle.append(ctx.author.id)
                track_tiles = song_data.get('track_titles', [])
                path = data.get('path', '')

                async with ctx.typing():
                    # TODO: make function for downloading temp mp3s for other methods (snippet, etc)
                    download_url = f"{JUICEWRLD_API}/juicewrld/files/download/?path={self.special_url_encode(path)}"
                    async with session.get(download_url) as download_response:
                        if download_response.status != 200:
                            await handle_request_failed(ctx, download_response.status)
                            self.handle_user_done_heardle(ctx.author.id)
                            return

                        self.assert_download_cache()
                        song_bytes = await download_response.read()
                        temp_file_path = DOWNLOAD_CACHE_FOLDER_NAME + f"/{ctx.author.id}_mp3_heardle.mp3"
                        with open(temp_file_path, 'wb') as f:
                            f.write(song_bytes)

                        image_file_name = f'{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_temp_image_heardle.png'
                        image_url = ctx.author.display_avatar.url
                        async with session.get(image_url) as image_response:
                            if image_response.status == 200:
                                image_data = await image_response.read()
                                with open(image_file_name, 'wb') as img_file:
                                    img_file.write(image_data)

                        acceptable_answers = []
                        for title in track_tiles:
                            acceptable_answers.extend(self.get_acceptable_track_names(title))

                        #print(f'{ctx.author.mention} (@{ctx.author.name}) is playing Heardle. Answer: {correct_answer}')
                        orig_clip = AudioFileClip(temp_file_path)
                        random_start_point = random.randint(0, int(orig_clip.duration) - HEARDLE_CLIP_DURATION*2)
                        duration = HEARDLE_CLIP_DURATION

                        orig_clip = AudioFileClip(temp_file_path)
                        sub_clip = orig_clip.subclipped(random_start_point, random_start_point + duration)

                        final_clip = ImageClip(image_file_name).with_audio(sub_clip)

                        output_path = f"{DOWNLOAD_CACHE_FOLDER_NAME}/{ctx.author.id}_mp4_heardle.mp4"
                        final_clip.duration = duration
                        final_clip.fps = 1
                        final_clip.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
                        message = await ctx.channel.send(f"🎵 {ctx.author.mention}: you have {HEARDLE_GAME_DURATION} seconds to guess. Here's your clip:", file=discord.File(output_path))
                        
                        await utils.Embeds.send_info_embed(
                            ctx.channel,
                            ctx.author,
                            f"Please reply to the message above to guess the song title or type `exit` to quit the game.",
                        )

                has_guessed = False
                attempt = 1

                while has_guessed == False:
                    def check_guess(m):
                        return (
                            m.author == ctx.author
                            and m.channel == ctx.channel
                        )
                    
                    try:
                        guess_msg = await self.bot.wait_for('message', check=check_guess, timeout=HEARDLE_GAME_DURATION)
                    except TimeoutError:
                        await utils.Embeds.send_warning_embed(
                            ctx.channel,
                            ctx.author,
                            f"Time's up! You didn't guess the song in time."
                        )
                        try:
                            await message.delete()
                        except:
                            pass
                        self.handle_user_done_heardle(ctx.author.id)
                        return
                    
                    guess = guess_msg.content.strip().lower()
                    if guess == 'exit':
                        await guess_msg.add_reaction('👋')
                        try:
                            await message.delete()
                        except:
                            pass
                        self.handle_user_done_heardle(ctx.author.id)
                        return
                    elif guess in acceptable_answers:
                        has_guessed = True
                    else:
                        attempt += 1
                await utils.Embeds.send_success_embed(
                    ctx.channel,
                    ctx.author,
                    f'Congratulations! You guessed the song correctly: **{song_data.get("name", "Unknown Title")}**!'
                )
                try:
                    await message.delete()
                except:
                    pass
                self.handle_user_done_heardle(ctx.author.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    logger.debug('Music cog initialized successfully')