import aiohttp
import asyncio
import discord
import logging
import os
import re
from discord.ext import commands, tasks
from discord.ext.commands import Context
from urllib.parse import urlparse
from utils.misc import MiscUtils
from utils.cooldown import unified_cooldown
from utils.guardrails import check_slash_guardrails


class VoiceControlView(discord.ui.View):
    """Interactive control panel for managing temporary VCs."""

    def __init__(self, bot, vc: discord.VoiceChannel, owner: discord.Member):
        super().__init__(timeout=None)
        self.bot = bot
        self.vc = vc
        self.owner = owner

    async def check_ownership(self, interaction: discord.Interaction):
        """Ensure only the current owner can use controls."""
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "🚫 You are not the owner of this voice channel!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.danger)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_ownership(interaction):
            return

        perms = self.vc.overwrites_for(self.vc.guild.default_role)
        if perms.connect is False:
            return await interaction.response.send_message(
                "⚠️ This voice channel is already locked.", ephemeral=True
            )

        await self.vc.set_permissions(self.vc.guild.default_role, connect=False)
        await interaction.response.send_message(
            "🔒 Voice channel locked successfully.", ephemeral=True
        )

    @discord.ui.button(label="🔓 Unlock", style=discord.ButtonStyle.success)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_ownership(interaction):
            return

        perms = self.vc.overwrites_for(self.vc.guild.default_role)
        if perms.connect is None or perms.connect is True:
            return await interaction.response.send_message(
                "⚠️ This voice channel is already unlocked.", ephemeral=True
            )

        await self.vc.set_permissions(self.vc.guild.default_role, connect=True)
        await interaction.response.send_message(
            "🔓 Voice channel unlocked successfully.", ephemeral=True
        )

    @discord.ui.button(label="👻 Ghost", style=discord.ButtonStyle.grey)
    async def ghost(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ghost the voice channel (hide from everyone except the owner)."""
        if not await self.check_ownership(interaction):
            return

        perms = self.vc.overwrites_for(self.vc.guild.default_role)
        if perms.view_channel is False:
            return await interaction.response.send_message(
                "⚠️ This voice channel is already ghosted.", ephemeral=True
            )

        await self.vc.set_permissions(self.vc.guild.default_role, view_channel=False)
        await interaction.response.send_message(
            "👻 Your voice channel is now hidden from others.", ephemeral=True
        )

    @discord.ui.button(label="👁️ Reveal", style=discord.ButtonStyle.grey)
    async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reveal the voice channel (make visible to everyone)."""
        if not await self.check_ownership(interaction):
            return

        perms = self.vc.overwrites_for(self.vc.guild.default_role)
        if perms.view_channel is None or perms.view_channel is True:
            return await interaction.response.send_message(
                "⚠️ This voice channel is already visible.", ephemeral=True
            )

        await self.vc.set_permissions(self.vc.guild.default_role, view_channel=True)
        await interaction.response.send_message(
            "👁️ Your voice channel is now visible to everyone.", ephemeral=True
        )

    @discord.ui.button(label="➕ Increase Limit", style=discord.ButtonStyle.primary)
    async def increase_limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Increase the user limit for the voice channel."""
        if not await self.check_ownership(interaction):
            return

        if self.vc.user_limit >= 99:
            return await interaction.response.send_message(
                "⚠️ The user limit cannot be increased further.", ephemeral=True
            )

        new_limit = self.vc.user_limit + 1 if self.vc.user_limit else 1
        await self.vc.edit(user_limit=new_limit)
        await interaction.response.send_message(
            f"🔼 Increased user limit to {new_limit}.", ephemeral=True
        )

    @discord.ui.button(label="➖ Decrease Limit", style=discord.ButtonStyle.primary)
    async def decrease_limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Decrease the user limit for the voice channel."""
        if not await self.check_ownership(interaction):
            return

        if self.vc.user_limit == 0 or self.vc.user_limit is None:
            return await interaction.response.send_message(
                "⚠️ The user limit is already at its minimum.", ephemeral=True
            )

        new_limit = self.vc.user_limit - 1 if self.vc.user_limit > 1 else 0
        await self.vc.edit(user_limit=new_limit)
        await interaction.response.send_message(
            f"🔽 Decreased user limit to {new_limit}.", ephemeral=True
        )

    @discord.ui.button(label="👢 Kick User", style=discord.ButtonStyle.danger)
    async def kick_user(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Kick a user from the voice channel."""
        if not await self.check_ownership(interaction):
            return

        members = self.vc.members
        if len(members) <= 1:
            return await interaction.response.send_message(
                "⚠️ No other members to kick.", ephemeral=True
            )

        class KickDropdown(discord.ui.Select):
            def __init__(self, vc, owner):
                self.vc = vc
                self.owner = owner

                options = [
                    discord.SelectOption(
                        label=member.display_name, value=str(member.id)
                    )
                    for member in vc.members
                    if member.id != owner.id
                ]
                super().__init__(placeholder="Select a user to kick", options=options)

            async def callback(self, interaction: discord.Interaction):
                member_id = int(self.values[0])
                member = discord.utils.get(self.vc.members, id=member_id)

                if member:
                    await member.move_to(None)
                    await interaction.response.send_message(
                        f"👢 Kicked {member.display_name} from the voice channel.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "⚠️ User is no longer in the voice channel.", ephemeral=True
                    )

        dropdown_view = discord.ui.View()
        dropdown_view.add_item(KickDropdown(self.vc, self.owner))

        await interaction.response.send_message(
            "👢 Select a user to kick:", view=dropdown_view, ephemeral=True
        )

    @discord.ui.button(label="✅ Claim Ownership", style=discord.ButtonStyle.success)
    async def claim_ownership(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Allows another user to claim the channel if the owner leaves."""
        if interaction.user.id == self.owner.id:
            return await interaction.response.send_message(
                "⚠️ You are already the owner of this channel.", ephemeral=True
            )

        if self.owner in self.vc.members:
            return await interaction.response.send_message(
                "⚠️ The current owner is still in the channel.", ephemeral=True
            )

        self.owner = interaction.user
        await self.vc.set_permissions(
            interaction.user, connect=True, manage_channels=True, move_members=True
        )
        await interaction.response.send_message(
            "✅ You are now the owner of this voice channel!", ephemeral=True
        )

    @discord.ui.button(label="💥 Nuke Channel", style=discord.ButtonStyle.danger)
    async def nuke_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Deletes the voice channel."""
        if not await self.check_ownership(interaction):
            return

        await self.vc.delete()
        await interaction.response.send_message(
            "💥 Voice channel has been deleted.", ephemeral=True
        )


logger = logging.getLogger("discord.client")

ALLOWED_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav")
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)


class Voicechat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.utils = MiscUtils(self)
        self.voice_clients = {}
        self.current_files = {}
        self.current_volume = {}
        self.queues = {}
        self.session = aiohttp.ClientSession()
        self.check_empty_vc.start()

    async def cog_unload(self):
        await self.session.close()

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

    @staticmethod
    def _is_valid_audio_url(url: str) -> bool:
        """Return True if the URL is an HTTP(S) link to an allowed audio extension."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in ALLOWED_AUDIO_EXTENSIONS)

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Return a filesystem-safe filename for a downloaded audio file."""
        safe = re.sub(r"[^a-zA-Z0-9._-]", "", name)
        if not safe or safe.startswith("."):
            safe = "audio.mp3"
        return safe[:128]

    async def _download_audio_url(
        self, ctx: Context, url: str
    ) -> tuple[str, str] | None:
        """Download a direct audio URL after validation.

        Returns (file_path, display_name) on success, or None if validation or
        the download fails. Cleans up any partial file on error.
        """
        if not self._is_valid_audio_url(url):
            await ctx.send(
                "Only direct links to `.mp3`, `.m4a`, or `.wav` files over HTTP/HTTPS are supported.",
                delete_after=10,
            )
            return None

        parsed = urlparse(url)
        raw_name = os.path.basename(parsed.path) or "audio.mp3"
        display_name = self._sanitize_filename(raw_name)
        os.makedirs("music_uploads", exist_ok=True)
        file_path = os.path.join("music_uploads", f"{ctx.guild.id}_{display_name}")

        headers = {"User-Agent": "TPNEBOT/1.0 (Discord voice music player)"}
        try:
            async with self.session.get(
                url, headers=headers, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    await ctx.send(
                        f"Could not download audio: server returned `{resp.status}`.",
                        delete_after=10,
                    )
                    return None

                content_type = resp.headers.get("content-type", "").lower()
                if not content_type.startswith("audio/"):
                    await ctx.send(
                        "The URL did not return an audio file.", delete_after=10
                    )
                    return None

                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        size = int(content_length)
                    except ValueError:
                        size = 0
                    if size > MAX_DOWNLOAD_SIZE:
                        await ctx.send(
                            f"File too large. Maximum size is `{MAX_DOWNLOAD_SIZE // (1024 * 1024)}MB`.",
                            delete_after=10,
                        )
                        return None

                downloaded = 0
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_SIZE:
                            raise ValueError("Download exceeded maximum allowed size")
                        f.write(chunk)

        except asyncio.TimeoutError:
            await ctx.send(
                "Download timed out. The file may be too large or the server too slow.",
                delete_after=10,
            )
            return None
        except aiohttp.ClientError as e:
            logger.warning("Audio download failed for %s: %s", url, e)
            await ctx.send(
                "Failed to download the audio file. Please check the URL and try again.",
                delete_after=10,
            )
            return None
        except ValueError as e:
            logger.warning("Audio download rejected for %s: %s", url, e)
            await ctx.send(
                f"File too large. Maximum size is `{MAX_DOWNLOAD_SIZE // (1024 * 1024)}MB`.",
                delete_after=10,
            )
            return None
        except Exception:
            logger.exception("Unexpected error downloading audio from %s", url)
            await ctx.send(
                "An unexpected error occurred while downloading the audio file.",
                delete_after=10,
            )
            return None
        finally:
            # If we failed after partially writing the file, remove it so we don't leak disk.
            if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            await ctx.send(
                "The downloaded file was empty. Please check the URL and try again.",
                delete_after=10,
            )
            return None

        return file_path, display_name

    async def _enqueue_audio_file(
        self, ctx: Context, file_path: str, display_name: str
    ) -> None:
        """Add a local audio file to the guild queue and start playback if idle."""
        vc = await self.ensure_voice(ctx)
        if not vc:
            # couldn't join; delete the file we just saved so we don't leak disk space
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                logging.exception("failed to remove orphaned upload %s", file_path)
            return

        if ctx.guild.id not in self.queues:
            self.queues[ctx.guild.id] = []

        self.queues[ctx.guild.id].append(file_path)

        if not vc.is_playing():
            await self.play_next(ctx)
            embed = discord.Embed(
                description=f"🎵 - `{display_name}`",
                color=discord.Color.blurple(),
            )
            embed.set_author(
                name="Now Playing", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
        else:
            embed = discord.Embed(
                description=f"🎵 - `{display_name}`", color=discord.Color.green()
            )
            embed.set_author(
                name="Added to Queue", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)

        embed.set_footer(
            text=f"Queued by {ctx.author.name} in {vc.channel.name} - {vc.channel.bitrate / 1000}Kbps"
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

    @commands.hybrid_command(
        name="jtcsetup", description="Set up the Join To Create system in your server."
    )
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def jtc_setup(self, ctx: Context):
        """Creates the Join To Create system for the server (only once)."""
        guild = ctx.guild

        existing = await self.bot.database.get_jtc_channels(guild.id)
        if existing:
            embed = discord.Embed(
                title="Join To Create",
                description="JTC is already set up in this server!",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        jtc_channel = discord.utils.get(guild.voice_channels, name="Join To Create")

        if not jtc_channel:
            jtc_channel = await guild.create_voice_channel(
                name="Join To Create",
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        connect=True,
                        view_channel=True,
                        speak=False,
                        send_messages=False,
                    ),
                },
            )

        await self.bot.database.add_jtc_setup(guild.id, jtc_channel.id)
        embed = discord.Embed(
            description=f"Join To Create has been set up in this server!\n\nUse {jtc_channel.mention} to create temporary VCs.",
            color=discord.Color.blurple(),
        )
        await ctx.reply(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handles user joining 'Join To Create' and creating private VCs."""
        jtc_channels = await self.bot.database.get_jtc_channels(member.guild.id)
        if not jtc_channels:
            return

        guild = member.guild
        jtc_channel_id = jtc_channels.jtc_channel_id

        if after.channel and after.channel.id == jtc_channels.jtc_channel_id:
            category = after.channel.category

            max_bitrate = guild.bitrate_limit

            temp_channel = await guild.create_voice_channel(
                name=f"{member.display_name}'s Channel",
                category=category,
                bitrate=max_bitrate,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(connect=False),
                    member: discord.PermissionOverwrite(
                        connect=True, mute_members=True, move_members=True
                    ),
                },
            )

            await member.move_to(temp_channel)
            await self.bot.database.add_temp_channel(
                guild.id, member.id, temp_channel.id
            )

            text_channel = temp_channel.guild.get_channel(temp_channel.id)
            if text_channel is None:
                logging.warning(f"Failed to find text channel for {temp_channel.name}")
                return

            await text_channel.send(f"Welcome {member.mention}!")
            embed = discord.Embed(
                title="Control Panel",
                description="Use the buttons below to manage your voice channel.",
                color=discord.Color.blurple(),
            )
            view = VoiceControlView(self.bot, temp_channel, member)
            await text_channel.send(embed=embed, view=view)

        if before.channel and before.channel.id == jtc_channel_id:
            return

        if before.channel and before.channel != after.channel:
            temp_owner_id = await self.bot.database.get_temp_channel_owner(
                before.channel.id
            )
            temp_owner_id = await self._resolve_id(temp_owner_id)

            if temp_owner_id is None:
                return

            if len(before.channel.members) == 0:
                await before.channel.delete()
                await self.bot.database.remove_temp_channel(before.channel.id)

    async def get_vc(self, member: discord.Member):
        """Helper function to retrieve the user's temporary VC."""
        if not member.voice or not member.voice.channel:
            return None
        return member.voice.channel

    async def is_owner(self, member: discord.Member):
        """Checks if the user is the owner of their current VC."""
        vc = await self.get_vc(member)
        if not vc:
            return None

        owner_id = await self.bot.database.get_temp_channel_owner(vc.id)
        owner_id = await self._resolve_id(owner_id)
        return vc if owner_id == member.id else None

    @commands.hybrid_group(
        name="vc",
        invoke_without_command=True,
        description="Voice channel management commands",
    )
    @unified_cooldown(5)
    async def vc(self, ctx: Context):
        """Lists all available voice channel commands."""
        prefix = "/"
        if ctx.message:
            prefix = await self.bot.get_prefix(ctx.message)
            if isinstance(prefix, list):
                prefix = prefix[0]

        subcmds = getattr(ctx.command, "commands", []) or []
        lines = []
        for cmd in sorted(subcmds, key=lambda c: c.name):
            name = cmd.name
            aliases = (
                f" (or: {', '.join(cmd.aliases)})"
                if getattr(cmd, "aliases", None)
                else ""
            )
            desc = (cmd.help or cmd.description or "").strip()
            if desc:
                lines.append(f"`{prefix}vc {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}vc {name}`{aliases}")

        description = "\n".join(lines) if lines else "No subcommands available."

        embed = discord.Embed(
            title="Voice Channel Management — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}vc <subcommand> for details.")
        await ctx.reply(embed=embed, mention_author=False)

    @vc.command(
        name="allow",
        aliases=["permit"],
        description="Allow a user to join your private VC",
    )
    @unified_cooldown(5)
    async def allow(self, ctx: Context, user: discord.Member):
        """Allows a specific user to join the VC."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(user, connect=True)
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"✅ {user.mention} can now join your voice channel.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to allow this user.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(
        name="kick", aliases=["boot"], description="Kick a user from your private VC"
    )
    @unified_cooldown(5)
    async def kick(self, ctx: Context, user: discord.Member):
        """Kicks a user from the voice channel."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            if user not in vc.members:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="⚠️ That user is not in your voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)

            await user.move_to(None)
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"👢 {user.mention} has been kicked from the channel.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to kick this user.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="ban", description="Ban a user from your private VC")
    @unified_cooldown(5)
    async def ban(self, ctx: Context, user: discord.Member):
        """Bans a user from the voice channel."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(user, connect=False)
            await user.move_to(None)
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"🚫 {user.mention} has been banned from the channel.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to ban this user.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="unban", description="Unban a user from your private VC")
    @unified_cooldown(5)
    async def unban(self, ctx: Context, user: discord.Member):
        """Unbans a user from the voice channel."""
        vc = await self.is_owner(ctx.author)
        if not vc:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="🚫 You are not the owner of a voice channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        await vc.set_permissions(user, connect=True)
        embed = discord.Embed(
            title="Voice Channel Management",
            description=f"✅ {user.mention} has been unbanned from the channel.",
            color=discord.Color.green(),
        )
        await ctx.reply(embed=embed, delete_after=5)

    @vc.command(name="lock", description="Lock your voice channel")
    @unified_cooldown(5)
    async def lock(self, ctx: Context):
        """Locks the voice channel to prevent others from joining."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(vc.guild.default_role, connect=False)
            embed = discord.Embed(
                title="Voice Channel Management",
                description="🔒 Your voice channel is now locked.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to lock the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="unlock", description="Unlock your voice channel")
    @unified_cooldown(5)
    async def unlock(self, ctx: Context):
        """Unlocks the voice channel."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(vc.guild.default_role, connect=True)
            embed = discord.Embed(
                title="Voice Channel Management",
                description="🔓 Your voice channel is now unlocked.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to unlock the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(
        name="ghost",
        aliases=["hide"],
        description="Make your VC invisible to everyone except you",
    )
    @unified_cooldown(5)
    async def ghost(self, ctx: Context):
        """Hides the voice channel from everyone except the owner."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(vc.guild.default_role, view_channel=False)
            embed = discord.Embed(
                title="Voice Channel Management",
                description="👻 Your voice channel is now hidden.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to hide the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(
        name="reveal", aliases=["show"], description="Make your VC visible again"
    )
    @unified_cooldown(5)
    async def reveal(self, ctx: Context):
        """Makes the voice channel visible again."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.set_permissions(vc.guild.default_role, view_channel=True)
            embed = discord.Embed(
                title="Voice Channel Management",
                description="👁️ Your voice channel is now visible.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to reveal the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(
        name="setlimit", aliases=["limit"], description="Set the user limit for your VC"
    )
    @unified_cooldown(5)
    async def set_limit(self, ctx: Context, limit: int):
        """Allows the owner to set a user limit."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            if limit < 0 or limit > 99:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="⚠️ Please provide a valid limit between 0-99.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.edit(user_limit=limit)
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"✅ User limit set to {limit}.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to set the limit.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="rename", description="Rename your private VC")
    @unified_cooldown(5)
    async def rename(self, ctx: Context, *, new_name: str):
        """Allows the owner to rename their voice channel."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            if len(new_name) > 32:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="⚠️ The name must be **32 characters or less**.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await vc.edit(name=new_name)
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"✅ Your voice channel has been renamed to **{new_name}**.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to rename the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="claim", description="Claim ownership of an empty private VC")
    @unified_cooldown(5)
    async def claim(self, ctx: Context):
        """Allows a user to claim a voice channel if the owner has left."""
        try:
            vc = await self.get_vc(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            owner_id = await self.bot.database.get_temp_channel_owner(vc.id)
            owner_id = await self._resolve_id(owner_id)
            owner = discord.utils.get(vc.members, id=owner_id)

            if (
                ctx.author.guild_permissions.administrator
                or ctx.author.guild_permissions.manage_channels
            ):
                await self.bot.database.set_temp_channel_owner(vc.id, ctx.author.id)
                await vc.set_permissions(
                    ctx.author, connect=True, manage_channels=True, move_members=True
                )
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description=f"✅ You are now the owner of **{vc.name}**!",
                    color=discord.Color.green(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            if owner:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 The owner is still in the channel, you cannot claim it.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            await self.bot.database.set_temp_channel_owner(vc.id, ctx.author.id)
            await vc.set_permissions(
                ctx.author, connect=True, manage_channels=True, move_members=True
            )
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"✅ You are now the owner of **{vc.name}**!",
                color=discord.Color.green(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"⚠️ I don't have the necessary permissions to claim the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    @vc.command(name="nuke", description="Delete your private voice channel")
    @unified_cooldown(5)
    async def nuke(self, ctx: Context):
        """Deletes the owner's voice channel immediately."""
        try:
            vc = await self.is_owner(ctx.author)
            if not vc:
                embed = discord.Embed(
                    title="Voice Channel Management",
                    description="🚫 You are not the owner of a voice channel.",
                    color=discord.Color.red(),
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            channel_name = vc.name

            await vc.delete()

            await self.bot.database.remove_temp_channel(vc.id)

            embed = discord.Embed(
                title="Voice Channel Management",
                description=f"💥 Your voice channel **{channel_name}** has been nuked.",
                color=discord.Color.blurple(),
            )
            await ctx.reply(embed=embed, delete_after=5)
        except discord.Forbidden:
            embed = discord.Embed(
                title="Voice Channel Management",
                description="⚠️ I don't have the necessary permissions to delete the channel.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

    async def ensure_voice(self, ctx):
        """Ensure the bot joins the user's voice channel if not already connected and deafens itself.

        Discord's voice subsystem can raise ``discord.errors.ConnectionClosed``
        (4017 in the logs) when the gateway/voice handshake fails.  That
        error is coming from the library and usually indicates a temporary
        failure on Discord's side (invalid region, token, etc).  If we do not
        catch it the exception bubbles up and the calling command will crash
        out of its coroutine.  ``ensure_voice`` is used by every command that
        needs a voice connection, so we guard the connect call and clean up
        any state when we hit a problem.
        """
        # if we already have a valid client, just return it
        existing = self.voice_clients.get(ctx.guild.id)
        if existing and existing.is_connected():
            return existing

        # user must be in voice in order to connect
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(
                "You must be in a voice channel to use this command.",
                delete_after=5,
            )
            return None

        voice_channel = ctx.author.voice.channel
        try:
            vc = await voice_channel.connect()
        except discord.errors.ConnectionClosed as exc:
            # discord.py surfaces websocket close codes via the exception; 4017
            # is commonly hit when the channel is *E2EE only* which the library
            # doesn't support.  The user-facing message should explain that.
            logging.error(
                "Failed to connect to voice in guild %s (%s): %s",
                ctx.guild.id,
                ctx.guild.name,
                exc,
            )
            # make sure we don't keep a stale entry around
            self.voice_clients.pop(ctx.guild.id, None)

            # pick a friendly explanation based on the code/message
            msg = (
                "I couldn't connect to the voice channel. "
                "This might be a Discord outage or a temporary network issue; "
                "please try again later."
            )
            if getattr(exc, "code", None) == 4017 or "E2EE" in str(exc):
                msg = (
                    "That channel requires end-to-end encryption (E2EE), "
                    "which is not supported by bot clients. "
                    "Try using a non-E2EE channel or disable the setting."
                )
            await ctx.send(msg, delete_after=10)
            return None
        except Exception as exc:  # cover any other unexpected errors
            logging.exception(
                "Unexpected error while connecting to voice in guild %s",
                ctx.guild.id,
            )
            self.voice_clients.pop(ctx.guild.id, None)
            await ctx.send(
                "An unexpected error occurred when trying to join voice."
                " Please contact an administrator.",
                delete_after=5,
            )
            return None

        # success path: store and deafen
        self.voice_clients[ctx.guild.id] = vc
        try:
            await ctx.guild.me.edit(deafen=True)
        except discord.Forbidden:
            # permission problems shouldn't prevent playback, just warn
            logging.warning(
                "Unable to deafen bot in guild %s (missing permission)",
                ctx.guild.id,
            )
        return vc

    async def play_next(self, ctx):
        """Plays the next song in the queue if available"""
        if ctx.guild.id not in self.queues or not self.queues[ctx.guild.id]:
            await self.disconnect(ctx)
            return

        next_song = self.queues[ctx.guild.id].pop(0)
        file_path = next_song

        vc = self.voice_clients.get(ctx.guild.id)
        if not vc or not vc.is_connected():
            return

        self.play_audio(ctx, file_path)

    def play_audio(self, ctx, file_path):
        """Handles audio playback with volume control"""
        vc = self.voice_clients.get(ctx.guild.id)
        if not vc:
            return

        volume = self.current_volume.get(ctx.guild.id, 100)
        ffmpeg_options = f"-af 'volume={volume / 100.0}'"

        def after_playback(error):
            """Deletes file after playback & moves to the next song"""
            if os.path.exists(file_path):
                os.remove(file_path)
            self.current_files.pop(ctx.guild.id, None)

            self.bot.loop.create_task(self.play_next(ctx))

        vc.play(
            discord.FFmpegPCMAudio(file_path, options=ffmpeg_options),
            after=after_playback,
        )

    async def disconnect(self, ctx):
        """Disconnects the bot from the voice channel"""
        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_connected():
            await vc.disconnect()
            del self.voice_clients[ctx.guild.id]
            self.queues.pop(ctx.guild.id, None)
            embed = discord.Embed(
                description="Disconnected from the voice channel, queue finished. 🔇",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="play",
        aliases=["p"],
        description="Plays an attached audio file or a direct audio URL in the voice channel",
    )
    @unified_cooldown(10)
    async def play(
        self,
        ctx: Context,
        attachment: Optional[discord.Attachment] = None,
        *,
        query: Optional[str] = None,
    ):
        """Plays an attached audio file or direct audio URL and queues it if another song is playing."""
        # Attachment path: preserve prefix behavior using the invoking message.
        if ctx.message and ctx.message.attachments:
            attachment = ctx.message.attachments[0]
        if attachment:
            if not any(
                attachment.filename.endswith(ext) for ext in ALLOWED_AUDIO_EXTENSIONS
            ):
                await ctx.send(
                    "Only `.mp3`, `.m4a`, and `.wav` files are supported.", delete_after=5
                )
                return

            os.makedirs("music_uploads", exist_ok=True)
            file_path = os.path.join(
                "music_uploads", f"{ctx.guild.id}_{attachment.filename}"
            )
            await attachment.save(file_path)
            await self._enqueue_audio_file(ctx, file_path, attachment.filename)
            return

        # URL path: allow direct links to audio files.
        if not query:
            await ctx.send(
                "Please attach an audio file (.mp3, .m4a, .wav) or provide a direct audio URL.",
                delete_after=5,
            )
            return

        url = query.strip().split()[0]
        result = await self._download_audio_url(ctx, url)
        if not result:
            return

        file_path, display_name = result
        await self._enqueue_audio_file(ctx, file_path, display_name)

    @commands.hybrid_command(name="skip", description="Skips the currently playing song")
    @unified_cooldown(5)
    async def skip(self, ctx):
        """Skips the currently playing song"""
        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_playing():
            vc.stop()
            embed = discord.Embed(
                description="Skipped current song.", color=discord.Color.red()
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.send(embed=embed, delete_after=5)

    @commands.hybrid_command(name="queue", description="Displays the current music queue")
    @unified_cooldown(5)
    async def queue(self, ctx):
        """Displays the current music queue"""
        if ctx.guild.id not in self.queues or not self.queues[ctx.guild.id]:
            embed = discord.Embed(
                description="The queue is empty.", color=discord.Color.red()
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.send(embed=embed, delete_after=5)
            return

        queue_list = "\n".join(
            [
                f"{i+1}. {song[1]} - {song[2]}"
                for i, song in enumerate(self.queues[ctx.guild.id])
            ]
        )
        embed = discord.Embed(
            title="Music Queue", description=queue_list, color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="stop",
        aliases=["dc", "disconnect"],
        description="Stops playback and disconnects from the voice channel",
    )
    @unified_cooldown(5)
    async def stop(self, ctx):
        """Stops playback and disconnects, deleting only the file associated with this guild"""
        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_connected():
            vc.stop()
            await vc.disconnect()
            del self.voice_clients[ctx.guild.id]

            if ctx.guild.id in self.current_files:
                file_path = self.current_files.pop(ctx.guild.id)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.info(f"Deleted {file_path} after stop command.")
            embed = discord.Embed(
                description="Stopped playback and disconnected.",
                color=discord.Color.red(),
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.send(embed=embed, delete_after=5)
        else:
            await ctx.send("I am not connected to any voice channel.", delete_after=5)

    @commands.hybrid_command(name="pause", description="Pauses the currently playing audio.")
    @unified_cooldown(5)
    async def pause(self, ctx):
        """Pauses the current playback"""
        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_playing():
            vc.pause()
            embed = discord.Embed(
                description="Playback paused.", color=discord.Color.blurple()
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.reply(embed=embed, delete_after=5)

    @commands.hybrid_command(name="resume", description="Resumes the currently paused audio.")
    @unified_cooldown(5)
    async def resume(self, ctx):
        """Resumes the paused playback"""
        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_paused():
            vc.resume()
            embed = discord.Embed(
                description="Playback resumed.", color=discord.Color.blurple()
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.reply(embed=embed, delete_after=5)

    @commands.hybrid_command(
        name="volume",
        aliases=["vol"],
        description="Adjusts the volume of the current audio (1-100%)",
    )
    @unified_cooldown(5)
    async def volume(self, ctx, volume: str):
        """Adjusts the volume of the current playback (1-100%)"""
        volume = volume.replace("%", "")

        if not volume.isdigit():
            await ctx.reply(
                "Please enter a valid number between 1 and 100 (or 1% to 100%).",
                delete_after=5,
            )
            return

        volume = int(volume)

        if volume < 1 or volume > 100:
            await ctx.send(
                "Volume must be between 1 and 100 (or 1% to 100%).", delete_after=5
            )
            return

        self.current_volume[ctx.guild.id] = volume

        vc = self.voice_clients.get(ctx.guild.id)
        if vc and vc.is_playing():
            file_path = self.current_files.get(ctx.guild.id)
            if file_path:
                vc.stop()
                self.play_audio(ctx, file_path)
                embed = discord.Embed(
                    description=f"Volume set to `{volume}%`.",
                    color=discord.Color.blurple(),
                )
                embed.set_author(
                    name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                embed.set_footer(text="Action requested by: " + ctx.author.name)
                await ctx.reply(embed=embed, delete_after=5)
        else:
            embed = discord.Embed(
                description=f"Volume set to `{volume}%`. It will take effect on the next playback.",
                color=discord.Color.blurple(),
            )
            embed.set_author(
                name="Music Player", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            embed.set_footer(text="Action requested by: " + ctx.author.name)
            await ctx.reply(embed=embed, delete_after=5)

    @tasks.loop(seconds=30)
    async def check_empty_vc(self):
        """Automatically leaves the voice channel if no humans are present"""
        for guild_id, vc in list(self.voice_clients.items()):
            if vc.is_connected():
                members = vc.channel.members
                human_members = [m for m in members if not m.bot]

                if len(human_members) == 0:
                    await vc.disconnect()
                    del self.voice_clients[guild_id]
                    logging.info(
                        f"Left voice channel in {vc.channel.name} due to inactivity."
                    )

    @check_empty_vc.before_loop
    async def before_check_empty_vc(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Voicechat(bot))
    logger.debug("Voicechat cog initialized successfully")
