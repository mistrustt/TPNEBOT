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

logger = logging.getLogger("discord_bot")

JUICEWRLD_API = 'https://juicewrldapi.com'
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
            117: "#CC00FF", # THE PARTY NEVER ENDS
            # TODO: IM NOT DOING THE REST
        }

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
                                title='Error',
                                description='Your Discord account is not linked to JuiceWRLD API.\n\nUse the link command to connect your account.',
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
                        embed = discord.Embed(
                            title='Now Playing on JuiceWRLD API',
                            description=f'[{title}]({track_url})' if track_url else title,
                            color=discord.Color.green()
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

    @commands.command(aliases=['rleak'])
    async def randomleak(self, ctx: commands.Context) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{JUICEWRLD_API}/juicewrld/radio/random/') as response:
                async def handle_request_failed(ctx):
                    embed = discord.Embed(
                        description="Request failed. Please try again later.",
                        color=discord.Color.red()
                    )
                    embed.set_image(url="https://http.cat/429")
                    await ctx.reply(embed=embed, delete_after=5)

                if response.status != 200:
                    await handle_request_failed(ctx)
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
                embed.set_author(name=f'{ctx.author.display_name} - Random Juice WRLD Song', icon_url=ctx.author.display_avatar.url)
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
                    value=era_name,
                    inline=False
                )
                embed.add_field(
                    name='Producer(s)',
                    value=producers,
                    inline=False
                )
                embed.add_field(
                    name='Length',
                    value=f'{length}',
                    inline=False
                )

                def special_url_encode(str):
                    return quote(str).replace("/", "%2F").replace("%28", "(").replace("%29", ")")

                last_slash_index = path.rfind('/')
                file_name = path[last_slash_index + 1:] if last_slash_index != -1 else path
                path = path[:last_slash_index] if last_slash_index != -1 else path

                download_url = f'{JUICEWRLD_API}/files/{special_url_encode(path)}?highlight={special_url_encode(file_name)}'
                view = SongView(download_url)
                message = await ctx.reply(embed=embed, view=view)
                await message.add_reaction("👍")
                await message.add_reaction("👎")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
    logger.debug('Music cog initialized successfully')