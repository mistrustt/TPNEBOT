from encodings.aliases import aliases
import discord
import logging
import asyncio
import re
import io
import matplotlib.pyplot as plt
from discord import app_commands
from discord.ext import commands, tasks
from datetime import timedelta
import humanfriendly
from discord.ext.commands import Context
from database.models import PunishmentType
from utils.misc import MiscUtils
from typing import Optional, Union
from matplotlib.ticker import MaxNLocator

logger = logging.getLogger("discord_bot")

def parse_duration(duration_str: str) -> int:
    """
    Parse a duration string and return seconds.
    Supported units: s, m, h, d (e.g. "15m", "2h", "1d").
    """
    seconds = 0
    for amount, unit in re.findall(r'(\d+)([smhd])', duration_str):
        amount = int(amount)
        if unit == 's':
            seconds += amount
        elif unit == 'm':
            seconds += amount * 60
        elif unit == 'h':
            seconds += amount * 3600
        elif unit == 'd':
            seconds += amount * 86400
    if seconds == 0:
        raise ValueError("Invalid duration format")
    return seconds

def format_duration(seconds: int) -> str:
    """Converts a duration in seconds to a human-readable string like '1m', '2h', '3d', etc."""
    weeks, seconds = divmod(seconds, 604800)  
    days, seconds = divmod(seconds, 86400)  
    hours, seconds = divmod(seconds, 3600)  
    minutes, seconds = divmod(seconds, 60)

    if weeks > 0:
        return f"{weeks}w"
    elif days > 0:
        return f"{days}d"
    elif hours > 0:
        return f"{hours}h"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{seconds}s"

class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.utils = MiscUtils(self)
        self.lockdown_channels = []
        self.new_members = []
        self.sync_counts.start()

    def cog_unload(self):
        self.sync_counts.cancel()

    async def _get_settings(self, guild: discord.Guild):
        """Fetch channel_id and name template from DB (fallbacks included)."""
        channel_id = await self.bot.database.get_member_count_channel(guild.id)
        # template could be None in DB; use default if so
        template = "Members: {count:,}"
        return channel_id, template

    async def _ensure_channel(self, guild: discord.Guild, template: str):
        """If channel missing, recreate it and save to DB."""
        channel_id, _ = await self._get_settings(guild)
        if channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                return ch
        name = template.format(count=guild.member_count)
        overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False)}
        vc = await guild.create_voice_channel(name=name, overwrites=overwrites)
        await self.bot.database.set_member_count_channel(guild.id, vc.id, template)
        return vc

    async def _update_channel(self, guild: discord.Guild):
        channel_id, template = await self._get_settings(guild)
        if not channel_id:
            return
        vc = guild.get_channel(channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            # maybe deleted or changed type
            vc = await self._ensure_channel(guild, template)
        new_name = template.format(count=guild.member_count)
        if vc.name != new_name:
            await vc.edit(name=new_name, reason="Member count auto-sync")

    @tasks.loop(minutes=10)
    async def sync_counts(self):
        """Periodic sync in case events were missed."""
        for guild in self.bot.guilds:
            await self._update_channel(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        jailed = await self.bot.database.get_jailed_user(member.guild.id, member.id)
        if jailed:
            role = member.guild.get_role(jailed.jail_role_id)
            if role:
                await member.add_roles(role, reason="Re-adding jail role after rejoin") 

    async def _check_command_exists(self, command_name: str):
        """
        Check if a command or group exists in either normal or slash commands.
        This will return True if either exists (even for subcommands within groups).
        """
        command = self.bot.get_command(command_name)
        if command is not None:
            return True

        slash_command = self.bot.tree.get_command(command_name)
        if slash_command is not None:
            return True

        for cmd in self.bot.tree.walk_commands():
            if isinstance(cmd, app_commands.Group) and cmd.name == command_name:
                return True
            if isinstance(cmd, app_commands.Group):
                for subcommand in cmd.walk_commands():
                    if subcommand.name == command_name:
                        return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message):
        """Deletes MP3 files if the filter is enabled in the server."""
        if message.author.bot or not message.guild:
            return

        enabled = await self.bot.database.get_antimp3_status(message.guild.id)

        if not enabled:
            return

        if enabled:
            for attachment in message.attachments:
                if attachment.filename.lower().endswith((".mp3", ".wav", ".flac", ".m4a")):
                    await message.delete()
                    embed = discord.Embed(
                        description=f"{message.author.mention} audio files are not allowed in this server.",
                        color=discord.Color.red(),
                    )
                    await message.channel.send(embed=embed, delete_after=10)

    @commands.command(
        name="msg",
        aliases=["announce", "pm"],
        help="Send a message to a member or channel in this server.",
        hidden=True,
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def send_message(
        self,
        ctx: Context,
        target: Union[discord.Member, discord.TextChannel],
        *,
        message: str,
    ):
        """Send a message via the bot to a member or text channel in the current server.
        NOTE: cannot contact users or channels outside the server, and cannot be run from DMs.
        """
        # Ensure command executed in a guild (guild_only enforces this, kept as a safety net)
        if ctx.guild is None:
            return await ctx.reply("🚫 This command cannot be used in DMs.", mention_author=False)

        # Disallow targeting users/channels outside of this guild
        if isinstance(target, discord.TextChannel):
            if target.guild != ctx.guild:
                return await ctx.reply("🚫 You may only send messages to channels in this server.", mention_author=False)

            try:
                await target.send(message)
            except discord.Forbidden:
                return await ctx.reply(f"🚫 I don't have permission to send messages in {target.mention}.", mention_author=False)

            return await ctx.reply(f"📢 Message successfully sent in {target.mention}.", mention_author=False)

        # If it's a Member (preferred) ensure they're in the same guild
        # If converter returned a User (not Member), reject to prevent cross-server DMs
        if isinstance(target, discord.Member):
            try:
                await target.send(message)
            except discord.Forbidden:
                return await ctx.reply(f"🚫 I can't DM {target.mention}; they might have DMs disabled.", mention_author=False)

            return await ctx.reply(f"📩 Message successfully sent to {target.mention}.", mention_author=False)

        # Fallback: reject any other user-like object (prevents contacting users outside guild)
        return await ctx.reply("🚫 You may only target members or channels within this server.", mention_author=False)

    @commands.command(name='mcc', description='Create a member count channel')
    @commands.has_permissions(manage_channels=True)
    async def member_count_vc(self, ctx):
        mcc = await self.bot.database.get_member_count_channel(ctx.guild.id)

        if mcc:
            return await ctx.reply(embed=discord.Embed(
                title="🚫 Already Exists",
                description=f"Member count channel already exists: {mcc.mention}",
                color=discord.Color.red()
            ))

        vc = await self._ensure_channel(ctx.guild, "Members: {count:,}")

        await self.bot.database.set_member_count_channel(ctx.guild.id, vc.id)

        await ctx.reply(embed=discord.Embed(
            title="✅ Member Count Channel Created",
            description=f"{vc.mention} now shows your member count.",
            color=discord.Color.green()
        ))

    @commands.command(name='setmcc', help='Set the member count channel')
    @commands.has_permissions(manage_channels=True)
    async def set_member_count_channel(self, ctx, channel: int):
        await self.bot.database.set_member_count_channel(ctx.guild.id, channel)
        await ctx.guild.get_channel(channel).edit(name=f"Members: {ctx.guild.member_count:,}")
        await ctx.reply(embed=discord.Embed(
            title="✅ Member Count Channel Set",
            description=f"Now using {channel} as the member count channel.",
            color=discord.Color.green()
        ))
        await self._update_channel(ctx.guild)

    @commands.command(name='delmcc', help='Delete the member count channel')
    @commands.has_permissions(manage_channels=True)
    async def delete_member_count_vc(self, ctx):
        channel_id, _ = await self._get_settings(ctx.guild)
        if not channel_id:
            return await ctx.reply(embed=discord.Embed(
                title="🚫 None Found",
                description="No member count channel is configured.",
                color=discord.Color.red()
            ))
        vc = ctx.guild.get_channel(channel_id)
        if vc:
            await vc.delete(reason="Member count channel removed")
        await self.bot.database.set_member_count_channel(ctx.guild.id, None)
        await ctx.reply(embed=discord.Embed(
            title="🗑️ Deleted",
            description="Member count channel removed.",
            color=discord.Color.red()
        ))

    @commands.command(name='setreportchannel', aliases=['src'])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def report_channel(self, ctx: Context, *, channel_input: str):
        """Set the channel where reports will be sent."""

        if not channel_input:
            await ctx.send(f"Channel not found in server {ctx.guild.name}. Please provide a valid channel mention, ID, or name.")
            return

        if channel_input.startswith("<#") and channel_input.endswith(">"):
            channel_id = int(channel_input[2:-1])
            channel = ctx.guild.get_channel(channel_id)

        elif channel_input.isdigit():
            channel_id = int(channel_input)
            channel = ctx.guild.get_channel(channel_id)
        else:

            channel = discord.utils.find(lambda c: c.name.lower() == channel_input.lower(), ctx.guild.text_channels)

        if not channel:
            await ctx.send("Channel not found. Please provide a valid channel ID or mention.")
            return

        await self.bot.database.set_report_channel(ctx.guild.id, channel.id)
        await ctx.send(f"Report channel set to {channel.name} for server {ctx.guild.name}.")

    @app_commands.command(name="report", description="Report a user.")
    @app_commands.describe(identifier="User ID or mention", reason="Reason for the report")
    async def report(self, interaction: discord.Interaction, identifier: str, *, reason: str):
        """Allows users to report a member by their user ID. Works in both DMs and guilds."""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = interaction.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = interaction.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), interaction.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if member == interaction.guild.me:
            embed = discord.Embed(
                description="i didnt do anything :(",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if member == interaction.user:
            embed = discord.Embed(
                description="You cannot report yourself!",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        else:
            report_channel_id = await self.bot.database.get_report_channel(interaction.guild.id)
            report_channel = interaction.guild.get_channel(report_channel_id)

            if not report_channel:
                await interaction.response.send_message("Report channel not found. Please contact an administrator to enable this feature.", ephemeral=True)
                return

            case_id = await self.bot.database.add_punishment(
                user_id=member.id,
                guild_id=interaction.guild.id,
                moderator_id=interaction.user.id,
                punishment_type=PunishmentType.REPORT,
                reason=reason,
                duration=None  
            )

            embed = discord.Embed(
                title="New Report!",
                description=f"**Reported User:** {member.name} ({member.id})",
                color=discord.Color.red(),
                timestamp=interaction.created_at
            )
            embed.set_thumbnail(url=self.utils.get_avatar_url(member))
            embed.add_field(name='Report from:', value=f'{interaction.user.name} ({interaction.user.id})')
            embed.add_field(name='Reason:', value=f'{reason}')

            channel_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}"
            embed.add_field(name='Channel:', value=f"[Click here to view channel]({channel_link})", inline=False)

            embed.set_footer(text=f"User ID: {member.id} | Case ID: {case_id}")

            class ReportActionView(discord.ui.View):
                def __init__(self, case_id, member_id, guild_id, bot, reporter_name):
                    super().__init__(timeout=None)
                    self.case_id = case_id
                    self.member_id = member_id
                    self.guild_id = guild_id
                    self.bot = bot
                    self.reporter_name = reporter_name

                @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
                async def accept_report(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await self.bot.database.change_punishment_moderator(
                        case_id=self.case_id,
                        new_moderator_id=interaction.user.id
                    )

                    for item in self.children:
                        item.disabled = True

                    await interaction.response.edit_message(
                        embed=interaction.message.embeds[0].set_footer(
                            text=f"Report accepted by {interaction.user.name}"
                        ), 
                        view=self
                    )

                @discord.ui.button(label="Reject", style=discord.ButtonStyle.red)
                async def reject_report(self, interaction: discord.Interaction, button: discord.ui.Button):

                    await self.bot.database.remove_punishment(self.case_id, self.guild_id, self.member_id)

                    for item in self.children:
                        item.disabled = True

                    embed = discord.Embed(
                        title="Report Rejected",
                        description=f"{self.reporter_name}'s report against <@{self.member_id}> was rejected by {interaction.user.mention}",
                        color=discord.Color.greyple(),
                        timestamp=interaction.created_at
                    )

                    await interaction.response.edit_message(embed=embed, view=self)

            view = ReportActionView(case_id, member.id, interaction.guild.id, self.bot, interaction.user.name)
            await report_channel.send(embed=embed, view=view)

            await self.bot.database.set_cooldown(interaction.user.id, interaction.command.qualified_name, 1800)
            await interaction.response.send_message("Thank you for your report. The moderation team will review it.", ephemeral=True)

    @commands.command(
        name="purge",
        aliases=["clear"],
        description="Deletes user messages from a channel",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx: Context, arg1: str, arg2: str = None):
        """
        Deletes messages from a channel. Can specify amount and/or user.
        Usage: purge <amount> [member] OR purge <member> <amount>
        """
        try:

            member = None
            amount = None

            try:
                amount = int(arg1)

                if arg2:
                    try:

                        if arg2.startswith('<@') and arg2.endswith('>'):
                            user_id = ''.join(filter(str.isdigit, arg2))
                            member = ctx.guild.get_member(int(user_id))
                        else:

                            member = ctx.guild.get_member_named(arg2) or ctx.guild.get_member(int(arg2))
                    except (ValueError, TypeError):
                        return await ctx.send("Invalid member. Please mention a user or provide their ID/name.", delete_after=5)
            except ValueError:

                try:

                    if arg1.startswith('<@') and arg1.endswith('>'):
                        user_id = ''.join(filter(str.isdigit, arg1))
                        member = ctx.guild.get_member(int(user_id))
                    else:

                        member = ctx.guild.get_member_named(arg1) or ctx.guild.get_member(int(arg1))

                    if member and arg2:
                        try:
                            amount = int(arg2)
                        except ValueError:
                            return await ctx.send("Invalid amount. Please provide a number between 1 and 99.", delete_after=5)
                    else:
                        return await ctx.send("Invalid format. Usage: `purge <amount> [member]` OR `purge <member> <amount>`", delete_after=5)
                except (ValueError, TypeError):
                    return await ctx.send("Invalid format. Usage: `purge <amount> [member]` OR `purge <member> <amount>`", delete_after=5)

            if amount is None:
                return await ctx.send("Please provide the number of messages to delete.", delete_after=5)
            if amount <= 0:
                return await ctx.send("Please provide a positive number of messages to delete.", delete_after=5)
            if amount > 99:
                return await ctx.send("You can only delete up to 99 messages at once.", delete_after=5)

            async with ctx.typing():
                if member:
                    def is_user_message(message):
                        return message.author == member
                    deleted = await ctx.channel.purge(limit=amount + 1, check=is_user_message, bulk=True)
                    embed = discord.Embed(
                        description=f"{ctx.author.display_name}: successfully deleted {len(deleted) - 1} messages from {member.display_name}.",
                        color=discord.Color.blurple(),
                    )
                else:

                    deleted = await ctx.channel.purge(limit=amount + 1, bulk=True)
                    embed = discord.Embed(
                        description=f"{ctx.author.display_name}: successfully deleted {len(deleted) - 1} messages.",
                        color=discord.Color.blurple(),
                    )

                confirmation = await ctx.send(embed=embed)
                await asyncio.sleep(5)
                try:
                    await confirmation.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

        except discord.Forbidden:
            await ctx.send("I don't have permission to delete messages.", delete_after=5)
        except discord.HTTPException as e:
            await ctx.send(f"Error deleting messages: {e}", delete_after=5)
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}", delete_after=5)

    @commands.command(name='botclear', aliases=['bc'])
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def app_clear(self, ctx: Context):
        """Clear all bot messages and any invocation of your bot's commands."""
        try:
            async with ctx.typing():
                # 1. figure out your prefixes
                prefix = await self.bot.database.get_prefix(ctx.guild.id)
                prefixes = [prefix, ',']  # add more if you support more

                # 2. gather every command name + alias
                triggers = []
                for cmd in self.bot.commands:
                    triggers.append(cmd.name)
                    triggers.extend(cmd.aliases)

                # 3. prepare full list of <prefix><trigger>
                full_triggers = {f"{p}{t}" for p in prefixes for t in triggers}

                # 4. scan recent history and pick messages to delete
                to_delete = []
                async for msg in ctx.channel.history(limit=500):
                    if msg.author.bot or any(msg.content.startswith(ft) for ft in full_triggers):
                        to_delete.append(msg)

                # 5. bulk-delete in chunks of 100
                for chunk in (to_delete[i:i+100] for i in range(0, len(to_delete), 100)):
                    await ctx.channel.delete_messages(chunk)

                # finally, remove the invoking command
                await ctx.message.delete()
        except discord.Forbidden:
            await ctx.send("I don't have permission to delete messages.", delete_after=5)
        except discord.HTTPException:
            # maybe messages are too old or nothing to delete
            pass
        except discord.NotFound:
            pass

    @commands.command(
        name="kick",
        description="Kick a user out of the server.",
    )
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: Context, identifier: str, *, reason: str = "No reason provided") -> None:
        """Kick a specified user by ID, Name or Mention"""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member == ctx.guild.me:
            embed = discord.Embed(
                description="stop.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member == ctx.author:
            embed = discord.Embed(
                description="You cannot kick yourself!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member.top_role.position >= ctx.author.top_role.position:
            embed = discord.Embed(
                description="🚫 You cannot kick a user with a role higher than or equal to yours!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="🚫 I cannot kick a user with a role higher than or equal to mine!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        else:
            try:
                await self.bot.database.log_punishment_command(
                    moderator_id=ctx.author.id,
                    guild_id=ctx.guild.id,
                    command_name=PunishmentType.KICK
                )
                await self.bot.database.add_punishment(
                    user_id=member.id,
                    guild_id=ctx.guild.id,
                    moderator_id=ctx.author.id,
                    punishment_type=PunishmentType.KICK,
                    reason=reason,
                    duration=None  
                )
                embed = discord.Embed(
                    description=f"**{member}** was kicked for `{reason}`.",
                    color=discord.Color.blurple(),
                )
                embed.set_author(
                    name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                embed.set_footer(text=f"Case ID: {await self.bot.database.get_punishment_caseid(ctx.guild.id)}")
                await ctx.send(embed=embed)

                try:
                    dm_embed = discord.Embed(
                        description=f"You were kicked from **{ctx.guild.name}**!",
                        color=discord.Color.greyple(),
                    )
                    dm_embed.set_author(
                        name=f"Guild: {ctx.guild.name}", icon_url=ctx.guild.icon.url
                    )
                    dm_embed.add_field(name="Reason:", value=reason)
                    dm_embed.set_footer(text=f"Action by: {ctx.author} Case ID: {await self.bot.database.get_punishment_caseid(ctx.guild.id)}")
                    await member.send(embed=dm_embed)
                except:
                    await ctx.reply(
                        embed=discord.Embed(
                            description=f"Could not send user a DM message!",
                            color=discord.Color.red(),
                        )
                    )

                await member.kick(reason=reason)
            except Exception as e:
                embed = discord.Embed(
                    description="An error occurred while trying to kick the user. Please ensure my top role is above theirs.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)

    @commands.command(
        name="ban",
        description="Bans a user from the server.",
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: Context, identifier: str, *, reason: str = "No reason provided") -> None:
        """Ban a specified user by ID, Name or Mention"""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member == ctx.guild.me:
            embed = discord.Embed(
                description="stop.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member == ctx.author:
            embed = discord.Embed(
                description="You cannot ban yourself!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        try:
            if member.top_role.position >= ctx.author.top_role.position:
                embed = discord.Embed(
                    description="🚫 You cannot ban a user with a role higher than or equal to yours!",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return
            if member.top_role.position >= ctx.guild.me.top_role.position:
                embed = discord.Embed(
                    description="🚫 I cannot ban a user with a role higher than or equal to mine!",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return
            else:
                embed = discord.Embed(
                    description=f"**{member}** was banned for `{reason}`.",
                    color=discord.Color.blurple(),
                )
                embed.set_author(
                    name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                embed.set_footer(text=f"Case ID: {await self.bot.database.get_punishment_caseid(ctx.guild.id)}")
                await ctx.send(embed=embed, delete_after=10)
                try:
                    dm_embed = discord.Embed(
                        description=f"You have been **banned** from **{ctx.guild.name}**.",
                        color=discord.Color.greyple(),
                    )
                    dm_embed.set_author(
                        name=f"Guild: {ctx.guild.name}", icon_url=ctx.guild.icon.url
                    )
                    dm_embed.add_field(name="Reason:", value=reason)
                    dm_embed.set_footer(text=f"Action by: {ctx.author} Case ID: {await self.bot.database.get_punishment_caseid(ctx.guild.id) + 1}")
                    await member.send(embed=dm_embed)
                except:
                    embed = discord.Embed(
                        description=f"Could not send user a DM message!", color=0x36393E
                    )
                    await ctx.reply(embed=embed)

                await self.bot.database.log_punishment_command(
                    moderator_id=ctx.author.id,
                    guild_id=ctx.guild.id,
                    command_name=PunishmentType.BAN
                )
                await self.bot.database.add_punishment(
                    user_id=member.id,
                    guild_id=ctx.guild.id,
                    moderator_id=ctx.author.id,
                    punishment_type=PunishmentType.BAN,
                    reason=reason,
                    duration=None  
                )

                await member.ban(reason=reason)
        except Exception as e:
            embed = discord.Embed(
                title="Ban Error",
                description=f"An error occurred while attempting to ban {member.name}. Make sure my top role is above theirs.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.command(
        name="tempban",
        description="Temporarily bans a user for a specified duration."
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx: Context,
        identifier: str,
        duration: str,
        *,
        reason: str = "No reason provided"
    ) -> None:
        """
        Usage:
          !tempban @user 1h spamming    > bans for 1 hour
          !tempban @user 2d harassment  > bans for 2 days
        """

        member = None
        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier)) or await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass
        elif re.match(r'^<@!?(\d+)>$', identifier):
            user_id = int(re.match(r'^<@!?(\d+)>$', identifier).group(1))
            member = ctx.guild.get_member(user_id) or await self.bot.fetch_user(user_id)
        else:
            name = identifier.lower()
            member = discord.utils.find(lambda m: name in m.name.lower(), ctx.guild.members)

        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found for `{identifier}`.", color=discord.Color.red()
            ))

        if member == ctx.guild.me:
            return await ctx.send(embed=discord.Embed(
                description="I refuse to ban myself.", color=discord.Color.red()
            ))
        if member == ctx.author:
            return await ctx.send(embed=discord.Embed(
                description="You cannot tempban yourself.", color=discord.Color.red()
            ))
        if isinstance(member, discord.Member):
            if member.top_role >= ctx.author.top_role:
                return await ctx.send(embed=discord.Embed(
                    description="🚫 You cannot tempban someone with an equal or higher role.", color=discord.Color.red()
                ))
            if member.top_role >= ctx.guild.me.top_role:
                return await ctx.send(embed=discord.Embed(
                    description="🚫 I cannot tempban someone with an equal or higher role than me.", color=discord.Color.red()
                ))

        try:
            secs = humanfriendly.parse_timespan(duration)
        except humanfriendly.InvalidTimespan:
            return await ctx.send("Invalid duration format. Use like `15m`, `2h`, `1d`, etc.")

        await member.ban(reason=reason)
        case_id = await self.bot.database.get_punishment_caseid(ctx.guild.id)

        await self.bot.database.log_punishment_command(
            moderator_id=ctx.author.id,
            guild_id=ctx.guild.id,
            command_name=PunishmentType.TEMPBAN
        )
        await self.bot.database.add_punishment(
            user_id=member.id,
            guild_id=ctx.guild.id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.TEMPBAN,
            reason=reason,
            duration=secs
        )

        embed = discord.Embed(
            description=(
                f"**{member}** has been temp-banned for **{duration}**.\n"
                f"Reason: `{reason}`"
            ),
            color=discord.Color.blurple()
        )
        embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
        embed.set_footer(text=f"Case ID: {case_id}")
        await ctx.send(embed=embed)

        try:
            dm = discord.Embed(
                description=(
                    f"You have been temporarily banned from **{ctx.guild.name}** for **{duration}**.\n"
                    f"Reason: `{reason}`"
                ),
                color=discord.Color.greyple()
            )
            dm.set_footer(text=f"Case ID: {case_id}")
            await member.send(embed=dm)
        except discord.HTTPException:
            pass

        asyncio.create_task(self._auto_unban(ctx.guild.id, member.id, secs))

    async def _auto_unban(self, guild_id: int, user_id: int, delay: int):
        """Waits for `delay` seconds, then unbans and logs the expiration."""
        await asyncio.sleep(delay)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            return

        try:
            await guild.unban(user, reason="Temporary ban expired")
        except discord.NotFound:
            return

        await self.bot.database.add_punishment(
            user_id=user_id,
            guild_id=guild_id,
            moderator_id=self.bot.user.id,
            punishment_type=PunishmentType.UNBAN,
            reason="Temporary ban expired",
            duration=None
        )

        if guild.system_channel:
            embed = discord.Embed(
                description=f"**{user}** has been unbanned (temporary ban expired).",
                color=discord.Color.green()
            )
            await guild.system_channel.send(embed=embed)

        try:
            dm = discord.Embed(
                description=f"Your temporary ban in **{guild.name}** has expired. You may rejoin now.",
                color=discord.Color.green()
            )
            await user.send(embed=dm)
        except discord.HTTPException:
            pass

    @commands.command(
        name="unban",
        description="Unbans a user from the server and sends them an invite.",
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: Context, *, identifier: str) -> None:
        """Unban a specified user by ID, Name or Mention"""
        bans = [entry async for entry in ctx.guild.bans()]
        user_to_unban = None

        if identifier.isdigit():
            user_id = int(identifier)
            for ban_entry in bans:
                if ban_entry.user.id == user_id:
                    user_to_unban = ban_entry.user
                    break

        if not user_to_unban and "#" in identifier:
            for ban_entry in bans:
                username_with_discriminator = (
                    f"{ban_entry.user.name}#{ban_entry.user.discriminator}"
                )
                if identifier.lower() == username_with_discriminator.lower():
                    user_to_unban = ban_entry.user
                    break

        if not user_to_unban:
            identifier_lower = identifier.lower()
            for ban_entry in bans:
                if identifier_lower in ban_entry.user.name.lower():
                    user_to_unban = ban_entry.user
                    break

        if user_to_unban:
            await ctx.guild.unban(user_to_unban)

            invite = await ctx.channel.create_invite(max_uses=1, unique=True)

            embed = discord.Embed(
                description=f"**{user_to_unban}** has been unbanned!",
                color=discord.Color.blurple(),
            )
            embed.set_author(
                name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author)
            )
            await ctx.send(embed=embed)

            try:
                dm_embed = discord.Embed(
                    description=f"You have been unbanned from **{ctx.guild.name}**!",
                    color=discord.Color.green(),
                )
                dm_embed.add_field(name="Invite Link:", value=invite.url)
                dm_embed.set_footer(text="We hope to see you again!")
                await user_to_unban.send(embed=dm_embed)
            except:
                await ctx.reply(
                    embed=discord.Embed(
                        description=f"Could not send the invite to the user via DM.",
                        color=discord.Color.red(),
                    )
                )

        else:
            embed = discord.Embed(
                title="User Not Found",
                description="No matching banned user found with the provided identifier.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.command(name="history", aliases=['ph', 'punishments'], description="View a user's punishment history.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def history(self, ctx: Context, user: discord.Member):
        """View a specific user's punishment history"""
        guild_id = ctx.guild.id

        history = await self.bot.database.get_punishment_history(user.id, guild_id)
        if not history:
            await ctx.send(f"No punishment history found for {user.name}.")
            return

        history.sort(key=lambda x: x.created_at, reverse=True)

        entries_per_page = 5
        pages = [history[i:i + entries_per_page] for i in range(0, len(history), entries_per_page)]

        def generate_entry(punishment):
            moderator = ctx.guild.get_member(punishment.moderator_id)
            formatted_date = punishment.created_at.strftime('%Y-%m-%d %I:%M %p')

            lines = [
                f"**Case ID:** {punishment.case_id} | **Type:** {punishment.type.value.capitalize()}",
                f"**Reason:** `{punishment.reason}`",
            ]

            if punishment.duration:
                formatted_duration = humanfriendly.format_timespan(punishment.duration)
                lines.append(f"**Duration:** {formatted_duration}")

            lines.append(f"**Moderator:** {moderator.name if moderator else 'Unknown'}")
            lines.append(f"**Date:** {formatted_date} UTC")

            return "\n".join(lines)

        async def create_embed(page_num: int):
            embed = discord.Embed(
                title=f"Punishment History for {user.name} (Page {page_num + 1}/{len(pages)})",
                color=discord.Color.blurple()
            )
            entries = [generate_entry(p) for p in pages[page_num]]
            embed.description = "\n\n".join(entries)
            return embed

        class HistoryView(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.page_num = 0

            @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, disabled=True)
            async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page_num -= 1
                self.update_buttons()
                embed = await create_embed(self.page_num)
                await interaction.response.edit_message(embed=embed, view=self)

            @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.page_num += 1
                self.update_buttons()
                embed = await create_embed(self.page_num)
                await interaction.response.edit_message(embed=embed, view=self)

            def update_buttons(self):
                self.previous.disabled = (self.page_num == 0)
                self.next.disabled = (self.page_num == len(pages) - 1)

        view = HistoryView()
        initial_embed = await create_embed(0)
        view.update_buttons()
        await ctx.reply(embed=initial_embed, view=view, mention_author=False)

    @commands.command(name="permissions", aliases=['perms'], description="View the permissions of a member in the server.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def permissions(self, ctx: commands.Context, member: discord.Member = None):
        """View the permissions of a member in the server."""
        member = member or ctx.author

        perms = member.guild_permissions
        allowed = [perm.replace('_', ' ').title() for perm, value in perms if value]
        denied  = [perm.replace('_', ' ').title() for perm, value in perms if not value]

        embed = discord.Embed(
            title=f"{member.display_name}'s Permissions",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name=f"✅ Allowed ({len(allowed)})",
            value="\n".join(allowed) if allowed else "None",
            inline=False
        )
        embed.add_field(
            name=f"❌ Denied ({len(denied)})",
            value="\n".join(denied) if denied else "None",
            inline=False
        )

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(
        name="timeout",
        aliases=["to"]
    )
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        identifier: str,
        time: str,
        *,
        reason: str = "No reason provided"
    ):
        """Times out a member with Discord's built-in timeout feature."""
        # 1) Resolve member
        member = None
        if identifier.isdigit():
            member = ctx.guild.get_member(int(identifier)) or await self.bot.fetch_user(int(identifier))
        elif m := re.match(r"^<@!?(\d+)>$", identifier):
            member = ctx.guild.get_member(int(m.group(1)))
        else:
            member = discord.utils.find(
                lambda m: identifier.lower() in m.name.lower(),
                ctx.guild.members
            )

        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with identifier `{identifier}`.",
                color=discord.Color.red()
            ))

        # 2) Basic role/self checks
        if member.id == ctx.author.id:
            return await ctx.send(embed=discord.Embed(
                description="🚫 You cannot timeout yourself.",
                color=discord.Color.red()
            ))
        if member == ctx.guild.me:
            return await ctx.send(embed=discord.Embed(
                description="🚫 I refuse to timeout myself.",
                color=discord.Color.red()
            ))
        if ctx.author.top_role <= member.top_role:
            return await ctx.send(embed=discord.Embed(
                description="🚫 You cannot timeout someone with an equal or higher role.",
                color=discord.Color.red()
            ))
        if member.top_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=discord.Embed(
                description="🚫 I cannot timeout someone with an equal or higher role.",
                color=discord.Color.red()
            ))

        # 3) Parse the duration
        try:
            seconds = humanfriendly.parse_timespan(time)
        except humanfriendly.InvalidTimespan:
            return await ctx.send(embed=discord.Embed(
                description="🚫 Invalid time format. Please provide something like `10m`, `2h30m`, etc.",
                color=discord.Color.red()
            ), delete_after=10)

        if seconds < 30:
            return await ctx.send(embed=discord.Embed(
                description="🚫 Timeout must be at least 30 seconds.",
                color=discord.Color.red()
            ), delete_after=10)

        max_seconds = 28 * 24 * 3600
        if seconds > max_seconds:
            return await ctx.send(embed=discord.Embed(
                description="🚫 Maximum timeout is 28 days.",
                color=discord.Color.red()
            ))

        duration = timedelta(seconds=seconds)
        timeout_until = discord.utils.utcnow() + duration

        # 4) Format the duration human-readably
        formatted = humanfriendly.format_timespan(seconds)

        # 5) Apply timeout & log
        try:
            # record in DB
            await self.bot.database.count_punishment_usage(
                moderator_id=ctx.author.id,
                guild_id=ctx.guild.id,
                command_name="TIMEOUT"
            )
            await self.bot.database.add_punishment(
                user_id=member.id,
                guild_id=ctx.guild.id,
                moderator_id=ctx.author.id,
                punishment_type="TIMEOUT",
                reason=reason,
                duration=int(seconds)
            )

            # timeout on Discord
            await member.edit(timed_out_until=timeout_until)

            # confirmation embed
            embed = discord.Embed(
                description=f"{member.mention} has been timed out for **{formatted}**.\n**Reason:** {reason}",
                color=discord.Color.blurple()
            )
            embed.set_author(
                name=f"Moderator: {ctx.author}",
                icon_url=self.utils.get_avatar_url(ctx.author)
            )
            await ctx.send(embed=embed)

            # DM the user
            now = discord.utils.utcnow()
            timestamp = now.strftime("%Y/%m/%d %I:%M:%S %p")
            dm = discord.Embed(
                title="You have been timed out",
                color=discord.Color.greyple()
            )
            dm.add_field(name="Server", value=ctx.guild.name, inline=True)
            dm.add_field(name="Moderator", value=ctx.author.name, inline=True)
            dm.add_field(name="Duration", value=formatted, inline=True)
            dm.add_field(name="Reason", value=reason, inline=False)
            dm.set_footer(text=timestamp)
            await member.send(embed=dm)

        except discord.Forbidden:
            await ctx.send(embed=discord.Embed(
                description="🚫 I lack permission to timeout that user.",
                color=discord.Color.red()
            ))
        except Exception as e:
            await ctx.send(embed=discord.Embed(
                description=f"🚫 An error occurred: {e}",
                color=discord.Color.red()
            ))

    @commands.command(name="untimeout", aliases=["uto"])
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self, ctx: commands.Context, identifier: str):
        """Untimeout a user"""
        # 1) Resolve member
        member = None
        if identifier.isdigit():
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass
        elif m := re.match(r"^<@!?(\d+)>$", identifier):
            member = ctx.guild.get_member(int(m.group(1)))
        else:
            member = discord.utils.find(
                lambda m: identifier.lower() in m.name.lower(),
                ctx.guild.members
            )

        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with the identifier `{identifier}`. Please try again.",
                color=discord.Color.red(),
            ))

        # 2) Check current timeout status
        now = discord.utils.utcnow()
        if not member.timed_out_until or member.timed_out_until <= now:
            return await ctx.send(embed=discord.Embed(
                description=f"{ctx.author.mention}: {member.name} is not timed out.",
                color=discord.Color.red(),
            ))

        # 3) Lift timeout
        try:
            await member.edit(timed_out_until=None)
            await ctx.send(embed=discord.Embed(
                description=f"{ctx.author.mention}: {member.name} is no longer timed out.",
                color=discord.Color.blurple(),
            ))

            # 4) DM the user with a humanfriendly-formatted timestamp
            lifted_at = discord.utils.utcnow()
            time_str = humanfriendly.format_date(lifted_at)

            moderator_avatar = self.utils.get_avatar_url(ctx.author)
            dm_embed = discord.Embed(
                title="Timeout Lifted",
                color=discord.Color.blurple()
            )
            dm_embed.add_field(name="Server", value=ctx.guild.name, inline=True)
            dm_embed.add_field(name="Moderator", value=ctx.author.name, inline=True)
            dm_embed.set_thumbnail(url=moderator_avatar)
            dm_embed.set_footer(text=f"Timeout lifted on {time_str}")

            await member.send(embed=dm_embed)

        except discord.Forbidden:
            await ctx.send(embed=discord.Embed(
                description="🚫 I lack permission to lift that timeout.",
                color=discord.Color.red(),
            ))
        except discord.HTTPException as e:
            await ctx.send(embed=discord.Embed(
                description=f"🚫 An error occurred: {e}",
                color=discord.Color.red(),
            ))

    @commands.command(
        name="nick", description="Change the nickname of a user on a server."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick(
        self, ctx: Context, identifier: str, *, nickname: str = None
    ) -> None:
        """Nickname a user"""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(
                description="🚫 You cannot change the nickname of someone with a role higher than or equal to yours!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        if member.top_role >= ctx.guild.me.top_role:
            embed = discord.Embed(
                description="🚫 I cannot change the nickname of someone with a role higher than or equal to mine!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        try:
            await member.edit(nick=nickname)
            embed = discord.Embed(
                description=f"**{member}'s** new nickname is **{nickname}**!",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)
        except:
            embed = discord.Embed(
                description="An error occurred while trying to change the nickname of the user. Make sure my role is above their role.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)

    @commands.command(
        name="warn",
        description="Adds a warning to a user in the server.",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def warning_add(
        self, ctx: Context, identifier: str, *, reason: str = "No reason provided"
    ) -> None:
        """Warn a user for an action"""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if member.top_role >= ctx.author.top_role:
            embed = discord.Embed(
                description="🚫 You cannot warn someone with a role higher than or equal to yours!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return   
        if member.top_role >= ctx.guild.me.top_role:
            embed = discord.Embed(
                description="🚫 I cannot warn someone with a role higher than or equal to mine!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        await self.bot.database.log_punishment_command(
            moderator_id=ctx.author.id,
            guild_id=ctx.guild.id,
            command_name=PunishmentType.WARN
        )
        await self.bot.database.add_punishment(
            user_id=int(member.id),
            guild_id=ctx.guild.id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.WARN,
            reason=reason,
            duration=None
        )
        embed = discord.Embed(
            description=f"**{member}** was warned for `{reason}`.",
            color=discord.Color.blurple(),
        )
        embed.set_author(
            name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author)
        )
        await ctx.send(embed=embed)

    @commands.command(
        name="jailsetup",
        description="Configure the jail role and channel for the server.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def set_jail_role(self, ctx: Context):
        """Sets up jail and unjail commands for usage"""
        guild = ctx.guild

        embed = discord.Embed(title="Jail Setup", description="Setting up jail role and channel...", color=discord.Color.blurple())
        status_message = await ctx.send(embed=embed)

        role = discord.utils.find(lambda r: "jail" in r.name.lower(), guild.roles)
        if role:
            embed.add_field(name="Jail Role", value=f"Found existing jail role: {role.name}. Setting as jail role.", inline=False)
        else:
            role = await guild.create_role(name="Jailed", reason="Created jail role")
            embed.add_field(name="Jail Role", value=f"Created new jail role: {role.name}.", inline=False)
        await status_message.edit(embed=embed)

        jail_channel = discord.utils.find(lambda c: "jail" in c.name.lower(), guild.text_channels)
        if jail_channel:
            embed.add_field(name="Jail Channel", value=f"Found existing jail channel: {jail_channel.name}. Setting as jail channel.", inline=False)
        else:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                role: discord.PermissionOverwrite(read_messages=True),
            }
            jail_channel = await guild.create_text_channel("Jail", overwrites=overwrites)
            embed.add_field(name="Jail Channel", value=f"Created new jail channel: {jail_channel.name}.", inline=False)
        await status_message.edit(embed=embed)

        for channel in guild.text_channels:
            if channel != jail_channel:
                await channel.set_permissions(role, read_messages=False)

        await self.bot.database.set_jail_settings(guild.id, role.id, jail_channel.id)
        embed.add_field(name="Setup Complete", value=f"Jail role and channel have been set. Role: {role.name}, Channel: {jail_channel.name}.", inline=False)
        embed.color = discord.Color.green()
        await status_message.edit(embed=embed)

    @commands.command(name="jail")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self,
                   ctx: Context,
                   identifier: str,
                   duration: str = None,
                   *,
                   reason: str = "No reason provided"):
        """
        Jail a user by assigning them the jail role.

        Usage:
          !jail @user               > indefinite jail
          !jail @user 2h spamming   > 2-hour jail, reason "spamming"
          !jail @user griefing      > indefinite jail, reason "griefing"
        """

        member = None
        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier)) or await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass
        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_id = re.match(r'^<@!?(\d+)>$', identifier).group(1)
            member = ctx.guild.get_member(int(mention_id))
        else:
            identifier_l = identifier.lower()
            member = discord.utils.find(lambda m: identifier_l in m.name.lower(), ctx.guild.members)

        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with `{identifier}`.", color=discord.Color.red()
            ))

        if member.top_role >= ctx.author.top_role:
            return await ctx.send(embed=discord.Embed(
                description="🚫 You cannot jail someone with an equal or higher role.", color=discord.Color.red()
            ))
        if member.top_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=discord.Embed(
                description="🚫 I cannot jail someone with an equal or higher role than me.", color=discord.Color.red()
            ))

        guild_id = ctx.guild.id
        jail_settings = await self.bot.database.get_jail_settings(guild_id)
        if not jail_settings or not jail_settings.jail_role_id:
            return await ctx.send("No jail settings found. Run `!jailsetup` first.")

        jail_role = ctx.guild.get_role(jail_settings.jail_role_id)
        jail_channel = ctx.guild.get_channel(jail_settings.jail_channel_id)
        if not jail_role or not jail_channel:
            return await ctx.send("🚫 Jail role or channel misconfigured. Contact an admin.")

        raw_dur = duration
        raw_reason = reason
        duration_seconds = None
        jailed_until = None

        if raw_dur and re.match(r"^\d+[smhd]$", raw_dur):
            try:
                secs = humanfriendly.parse_timespan(raw_dur)
            except humanfriendly.InvalidTimespan:
                reason_text = f"{raw_dur} {raw_reason}".strip()
            else:
                duration_seconds = secs
                jailed_until = (discord.utils.utcnow() + timedelta(seconds=secs)).replace(tzinfo=None)
                reason_text = raw_reason
        else:
            reason_text = f"{raw_dur} {raw_reason}".strip() if raw_dur else raw_reason

        to_remove = [r for r in member.roles if r != ctx.guild.default_role and r != jail_role]
        removed_ids = [r.id for r in to_remove]

        if jail_role.position >= ctx.guild.me.top_role.position:
            return await ctx.send(
                embed=discord.Embed(
                    description=(
                        "🚫 I cannot assign the jail role because my role is not high enough. "
                        "Please move my bot role above the jail role in the server settings."
                    ),
                    color=discord.Color.red()
                )
            )

        await self.bot.database.add_jailed_user(guild_id, member.id, jailed_until, removed_ids)
        await self.bot.database.log_punishment_command(
            moderator_id=ctx.author.id,
            guild_id=guild_id,
            command_name=PunishmentType.JAIL
        )
        await self.bot.database.add_punishment(
            user_id=member.id,
            guild_id=guild_id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.JAIL,
            reason=reason_text,
            duration=duration_seconds
        )

        if to_remove:
            await member.remove_roles(*to_remove, reason="Jailed by moderator")
        await member.add_roles(jail_role, reason="Jailed by moderator")

        jail_msg = discord.Embed(
            description=f"**{member}**, you have been jailed{' until ' + jailed_until.isoformat() if jailed_until else ''}.",
            color=discord.Color.greyple()
        )
        jail_msg.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
        await jail_channel.send(embed=jail_msg)

        confirm = discord.Embed(
            description=(
                f"{member.name} has been jailed "
                f"{'until ' + jailed_until.isoformat() if jailed_until else 'indefinitely'}."
            ),
            color=discord.Color.blurple()
        )
        await ctx.reply(embed=confirm)

        if duration_seconds:
            asyncio.create_task(self._auto_unjail(guild_id, member.id, duration_seconds))

    async def _auto_unjail(self, guild_id: int, user_id: int, delay: int):
        """Helper to sleep then restore a jailed user."""
        await asyncio.sleep(delay)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return

        jail_settings = await self.bot.database.get_jail_settings(guild_id)
        jail_role = guild.get_role(jail_settings.jail_role_id)
        jailed_record = await self.bot.database.get_jailed_user(guild_id, user_id)

        if jail_role in member.roles and jailed_record:

            await member.remove_roles(jail_role, reason="Jail duration expired")

            restore = [
                guild.get_role(rid)
                for rid in jailed_record.roles
                if guild.get_role(rid)
            ]
            if restore:
                await member.add_roles(*restore, reason="Restoring roles after auto-unjail")

            await self.bot.database.remove_jailed_user(guild_id, user_id)
            await self.bot.database.add_punishment(
                user_id=user_id,
                guild_id=guild_id,
                moderator_id=self.bot.user.id,
                punishment_type=PunishmentType.UNJAIL,
                reason="Jail expired",
                duration=None
            )

            channel = guild.get_channel(jail_settings.jail_channel_id)
            if channel:
                embed = discord.Embed(
                    description=f"{member.mention} has been released (jail time expired).",
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)

    @commands.command(name="unjail")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unjail(self, ctx: Context, identifier: str):
        """
        Release a user from jail by removing the jail role and restoring previous roles.

        Usage:
          !unjail @user
          !unjail 123456789012345678
          !unjail username
        """
        guild_id = ctx.guild.id

        jail_settings = await self.bot.database.get_jail_settings(guild_id)
        if not jail_settings or not jail_settings.jail_role_id:
            return await ctx.send(
                embed=discord.Embed(
                    description="No jail settings found for this server. Use `!jailsetup` to configure.",
                    color=discord.Color.red()
                )
            )

        jail_role = ctx.guild.get_role(jail_settings.jail_role_id)
        if not jail_role:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 The jail role no longer exists or is misconfigured.",
                    color=discord.Color.red()
                )
            )

        member = None
        if re.match(r'^\d+$', identifier):

            uid = int(identifier)
            member = ctx.guild.get_member(uid)
            if not member:
                try:

                    fetched = await self.bot.fetch_user(uid)
                    member = ctx.guild.get_member(fetched.id)
                except discord.NotFound:
                    member = None

        elif re.match(r'^<@!?(\d+)>$', identifier):

            mention_id = int(re.match(r'^<@!?(\d+)>$', identifier).group(1))
            member = ctx.guild.get_member(mention_id)

        else:

            lowered = identifier.lower()
            member = discord.utils.find(
                lambda m: lowered in m.name.lower(),
                ctx.guild.members
            )

        if not member:
            return await ctx.send(
                embed=discord.Embed(
                    description=f"No user found with the identifier: `{identifier}`.",
                    color=discord.Color.red()
                )
            )

        jail_channel = ctx.guild.get_channel(jail_settings.jail_channel_id)
        if not jail_channel:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 The jail channel is misconfigured or no longer exists.",
                    color=discord.Color.red()
                )
            )

        if jail_role not in member.roles:
            return await ctx.send(
                embed=discord.Embed(
                    description=f"**{member.name}** is not currently jailed.",
                    color=discord.Color.red()
                )
            )

        if jail_role.position >= ctx.guild.me.top_role.position:
            return await ctx.send(
                embed=discord.Embed(
                    description=(
                        "🚫 I cannot remove the jail role because my highest role is not above it. "
                        "Please move my bot role above the jail role in the server settings."
                    ),
                    color=discord.Color.red()
                )
            )

        try:
            await member.remove_roles(jail_role, reason="Unjailed by moderator")
        except discord.Forbidden:
            return await ctx.send(
                embed=discord.Embed(
                    description="🚫 Failed to remove the jail role due to missing permissions.",
                    color=discord.Color.red()
                )
            )

        jailed_record = await self.bot.database.get_jailed_user(guild_id, member.id)

        roles_to_restore: list[discord.Role] = []
        skipped_roles: list[str] = []
        if jailed_record and jailed_record.roles:
            for role_id in jailed_record.roles:
                role_obj = ctx.guild.get_role(role_id)
                if role_obj:

                    if role_obj.position < ctx.guild.me.top_role.position:
                        roles_to_restore.append(role_obj)
                    else:
                        skipped_roles.append(role_obj.name)

            if roles_to_restore:
                try:
                    await member.add_roles(
                        *roles_to_restore,
                        reason="Restoring roles after unjail"
                    )
                except discord.Forbidden:

                    skipped_names = [r.name for r in roles_to_restore]
                    return await ctx.send(
                        embed=discord.Embed(
                            description=(
                                "🚫 I could not restore the previous roles due to missing permissions. "
                                f"Roles that failed to reassign: {', '.join(skipped_names)}."
                            ),
                            color=discord.Color.red()
                        )
                    )

        await self.bot.database.remove_jailed_user(guild_id, member.id)

        description = f"**{member.name}** has been released from jail!"
        if skipped_roles:
            description += (
                "\n\n⚠️ I was unable to restore these roles because they are higher "
                f"than my top role: {', '.join(skipped_roles)}."
            )

        confirm_embed = discord.Embed(
            description=description,
            color=discord.Color.blurple()
        )
        await ctx.reply(embed=confirm_embed)

        try:
            announce_embed = discord.Embed(
                description=f"{member.mention} has been released from jail by {ctx.author.mention}.",
                color=discord.Color.green()
            )
            announce_embed.set_author(
                name="Automatic Unjail",
                icon_url=self.utils.get_avatar_url(ctx.author)
            )
            await jail_channel.send(embed=announce_embed)
        except discord.Forbidden:

            pass

    @commands.group(name='antimp3', aliases=['nomp3'])
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def anti_mp3(self, ctx: Context):
        """Check the status of the Anti-MP3 feature"""
        if ctx.invoked_subcommand is None:
            guild_id = ctx.guild.id
            anti_mp3_settings = await self.bot.database.get_antimp3_status(guild_id)
            if not anti_mp3_settings:
                embed = discord.Embed(
                    description=f"The Anti-MP3 feature is currently **disabled** in {ctx.guild.name}.",
                    color=discord.Color.greyple()
                )
                embed.set_footer(text="Use `!antimp3 enable` to enable the feature.")
                embed.set_author(name="Anti-MP3 Status", icon_url=self.utils.get_avatar_url(ctx.author))
                await ctx.send(embed=embed)
            else:
                embed=discord.Embed(
                    description=f"The Anti-MP3 feature is currently **enabled** in {ctx.guild.name}.",
                    color=discord.Color.blurple()
                )
                embed.set_footer(text="Use `!antimp3 disable` to disable the feature.")
                embed.set_author(name="Anti-MP3 Status", icon_url=self.utils.get_avatar_url(ctx.author))
                await ctx.send(embed=embed)

    @anti_mp3.command(name='enable', aliases=['on'], description="Enable the Anti-MP3 feature for the server")
    async def enable_antimp3(self, ctx: Context):
        """Toggle the Anti-MP3 feature"""
        guild_id = ctx.guild.id
        anti_mp3 = await self.bot.database.get_antimp3_status(guild_id)

        if not anti_mp3:
            await self.bot.database.toggle_antimp3(guild_id, True)
            embed=discord.Embed(
                description="Anti-MP3 has been enabled.",
                color=discord.Color.blurple()
            )
            embed.set_author(name="Anti-MP3", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed, delete_after=5)
        else:
            embed=discord.Embed(
                description="The Anti-MP3 feature is already enabled.",
                color=discord.Color.greyple()
            )
            embed.set_author(name="Anti-MP3", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed, delete_after=5)

    @anti_mp3.command(name='disable', aliases=['off'], description="Disable the Anti-MP3 feature for the server")
    async def disable_antimp3(self, ctx: Context):
        """Toggle the Anti-MP3 feature"""
        guild_id = ctx.guild.id
        anti_mp3 = await self.bot.database.get_antimp3_status(guild_id)

        if anti_mp3:
            await self.bot.database.toggle_antimp3(guild_id, False)
            embed=discord.Embed(
                description="Anti-MP3 has been disabled.",
                color=discord.Color.greyple()
            )
            embed.set_author(name="Anti-MP3", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed, delete_after=5)
        else:
            embed=discord.Embed(
                description="The Anti-MP3 feature is already disabled.",
                color=discord.Color.greyple()
            )
            embed.set_author(name="Anti-MP3", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed, delete_after=5)

    @commands.group(name="mutesetup", description="Creates and configures the mute roles for the server.", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_mute_role(self, ctx: Context):
        """Sets up mute roles for usage"""
        guild = ctx.guild
        mute_settings = await self.bot.database.get_mute_settings(guild.id)

        if mute_settings:
            embed = discord.Embed(
                description="Mute roles have already been set up for this server.",
                color=discord.Color.greyple()
            )

        embed = discord.Embed(title="Mute Setup", description="Setting up mute roles...", color=discord.Color.blurple())
        status_message = await ctx.send(embed=embed)

        muted_role = discord.utils.find(lambda r: "muted" in r.name.lower() and "image" not in r.name.lower() and "react" not in r.name.lower() and 'i' not in r.name.lower() and 'r' not in r.name.lower(), guild.roles)
        if muted_role:
            embed.add_field(name="Text Mute Role", value=f"Found existing text mute role: {muted_role.name}. Setting as text mute role.", inline=False)
        else:
            muted_role = await guild.create_role(name="muted", reason="Created text mute role")
            embed.add_field(name="Text Mute Role", value=f"Created new text mute role: {muted_role.name}.", inline=False)

        imuted_role = discord.utils.find(lambda r: "imuted" in r.name.lower() or "image mute" in r.name.lower(), guild.roles)
        if imuted_role:
            embed.add_field(name="Image Mute Role", value=f"Found existing image mute role: {imuted_role.name}. Setting as image mute role.", inline=False)
        else:
            imuted_role = await guild.create_role(name="imuted", reason="Created image mute role")
            embed.add_field(name="Image Mute Role", value=f"Created new image mute role: {imuted_role.name}.", inline=False)

        rmuted_role = discord.utils.find(lambda r: "rmuted" in r.name.lower() or "react mute" in r.name.lower(), guild.roles)
        if rmuted_role:
            embed.add_field(name="React Mute Role", value=f"Found existing react mute role: {rmuted_role.name}. Setting as react mute role.", inline=False)
        else:
            rmuted_role = await guild.create_role(name="rmuted", reason="Created react mute role")
            embed.add_field(name="React Mute Role", value=f"Created new react mute role: {rmuted_role.name}.", inline=False)

        for channel in guild.text_channels:

            overwrite_muted = channel.overwrites_for(muted_role)
            overwrite_muted.send_messages = False
            await channel.set_permissions(muted_role, overwrite=overwrite_muted)

            overwrite_imuted = channel.overwrites_for(imuted_role)
            overwrite_imuted.attach_files = False
            overwrite_imuted.embed_links = False
            await channel.set_permissions(imuted_role, overwrite=overwrite_imuted)

            overwrite_rmuted = channel.overwrites_for(rmuted_role)
            overwrite_rmuted.add_reactions = False
            await channel.set_permissions(rmuted_role, overwrite=overwrite_rmuted)

        await self.bot.database.set_mute_settings(guild.id, mute_role_id=muted_role.id, imute_role_id=imuted_role.id, rmute_role_id=rmuted_role.id)
        embed.add_field(name="Setup Complete", value=f"Mute roles have been set. Text: {muted_role.name}, Image: {imuted_role.name}, React: {rmuted_role.name}.", inline=False)
        embed.color = discord.Color.green()
        await status_message.edit(embed=embed)

    async def _lookup_member(self, ctx: Context, identifier: str) -> Optional[discord.Member]:
        """Helper to resolve a member from various identifier formats."""
        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        return member

    @commands.command(name="mute", description="Mute a user by assigning them the mute role.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def mute_user(self, ctx: Context, identifier: str, *args):
        """
        Usage:
          !mute @user               > indefinite mute (reason defaults)
          !mute @user 30m spamming  > 30-minute mute for "spamming"
          !mute @user spamming      > indefinite mute for "spamming"
        """
        member = await self._lookup_member(ctx, identifier)
        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with identifier `{identifier}`.", color=discord.Color.red()
            ))

        if member.top_role.position >= ctx.author.top_role.position:
            embed = discord.Embed(
                description="🚫 You cannot mute a user with a role higher than or equal to yours!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="🚫 I cannot mute a user with a role higher than or equal to mine!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        duration_seconds: Optional[int] = None
        reason = "No reason provided"
        if args:
            first = args[0]
            if re.match(r"^\d+[smhd]$", first):
                try:
                    duration_seconds = humanfriendly.parse_timespan(first)
                except humanfriendly.InvalidTimespan:

                    reason = " ".join(args)
                else:

                    reason = " ".join(args[1:]) or reason
            else:
                reason = " ".join(args)

        mute_settings = await self.bot.database.get_mute_settings(ctx.guild.id)
        if not mute_settings:
            return await ctx.send("No mute settings found: run `!mutesetup` first.")
        muted_role = ctx.guild.get_role(mute_settings.mute_role_id)
        if not muted_role:
            return await ctx.send("🚫 Cannot find the configured mute role.")

        await member.add_roles(muted_role, reason=f"Muted by {ctx.author} for {reason}")
        await self.bot.database.add_punishment(
            user_id=member.id,
            guild_id=ctx.guild.id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.MUTE,
            reason=reason,
            duration=duration_seconds
        )

        desc = f"**{member}** has been muted."
        if duration_seconds:
            desc += f" (for {first})"
        embed = discord.Embed(description=desc, color=discord.Color.blurple())
        embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
        embed.set_footer(text=f"Reason: {reason}")
        await ctx.send(embed=embed)

        if duration_seconds:
            asyncio.create_task(self._auto_unmute(member, muted_role, duration_seconds, ctx))

    async def _auto_unmute(self, member: discord.Member, role: discord.Role, delay: int, ctx: Context):
        """Sleep for `delay` seconds, then remove the role and log it."""
        await asyncio.sleep(delay)

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Mute duration expired")

                await self.bot.database.add_punishment(
                    user_id=member.id,
                    guild_id=ctx.guild.id,
                    moderator_id=self.bot.user.id,
                    punishment_type=PunishmentType.UNMUTE,
                    reason="Mute expired",
                    duration=None
                )
                embed = discord.Embed(
                    description=f"{member.mention} has been unmuted (duration expired).",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            except Exception as e:
                self.bot.logger.error(f"Failed to auto-unmute {member}: {e}")

    @commands.command(name="unmute", description="Unmute a user by removing the mute role.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def unmute_user(self, ctx: Context, *, identifier: str):
        """Restore a users messaging permissions"""
        guild_id = ctx.guild.id
        mute_settings = await self.bot.database.get_mute_settings(guild_id)

        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if not mute_settings:
            await ctx.send("No mute settings found for this server. Use `!mutesetup` to configure.")
            return

        muted_role = ctx.guild.get_role(mute_settings.mute_role_id)
        if not muted_role:
            await ctx.send("The mute role could not be found. Contact an admin.")
            return

        if muted_role in member.roles:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
            await self.bot.database.add_punishment(
                user_id=member.id,
                guild_id=ctx.guild.id,
                moderator_id=ctx.author.id,
                punishment_type=PunishmentType.UNMUTE,
                reason="N/A",
                duration=None
            )
            embed = discord.Embed(description=f"{member.name} has been unmuted.", color=discord.Color.blurple())
            embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f"{member.name} is not currently muted.", color=discord.Color.red())
            await ctx.send(embed=embed)

    @commands.command(name="rmute", description="Reaction mute a user by assigning them the react mute role.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def react_mute_user(self, ctx: Context, identifier: str, *args):
        """
        Usage:
          !rmute @user               > indefinite react-mute
          !rmute @user 30m spamming  > 30-minute react-mute for "spamming"
          !rmute @user spamming      > indefinite react-mute for "spamming"
        """
        member = await self._lookup_member(ctx, identifier)
        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with identifier `{identifier}`.", color=discord.Color.red()
            ))

        if member.top_role.position >= ctx.author.top_role.position:
            embed = discord.Embed(
                description="🚫 You cannot mute a user with a role higher than or equal to yours!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="🚫 I cannot mute a user with a role higher than or equal to mine!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        duration_seconds: Optional[int] = None
        reason = "No reason provided"
        if args:
            first = args[0]
            if re.match(r"^\d+[smhd]$", first):
                try:
                    duration_seconds = humanfriendly.parse_timespan(first)
                except humanfriendly.InvalidTimespan:
                    reason = " ".join(args)
                else:
                    reason = " ".join(args[1:]) or reason
            else:
                reason = " ".join(args)

        mute_settings = await self.bot.database.get_mute_settings(ctx.guild.id)
        if not mute_settings:
            return await ctx.send("No mute settings found: run `!mutesetup` first.")
        rmuted_role = ctx.guild.get_role(mute_settings.rmute_role_id)
        if not rmuted_role:
            return await ctx.send("🚫 Cannot find the configured react-mute role.")

        await member.add_roles(rmuted_role, reason=f"React-muted by {ctx.author} for {reason}")
        await self.bot.database.add_punishment(
            user_id=member.id,
            guild_id=ctx.guild.id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.RMUTE,
            reason=reason,
            duration=duration_seconds
        )

        desc = f"**{member}** has been react-muted."
        if duration_seconds:
            desc += f" (for {first})"
        embed = discord.Embed(description=desc, color=discord.Color.blurple())
        embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
        embed.set_footer(text=f"Reason: {reason}")
        await ctx.send(embed=embed)

        if duration_seconds:
            asyncio.create_task(self._auto_react_unmute(member, rmuted_role, duration_seconds, ctx))

    async def _auto_react_unmute(self, member: discord.Member, role: discord.Role, delay: int, ctx: Context):
        await asyncio.sleep(delay)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="React-mute duration expired")
                await self.bot.database.add_punishment(
                    user_id=member.id,
                    guild_id=ctx.guild.id,
                    moderator_id=self.bot.user.id,
                    punishment_type=PunishmentType.RUNMUTE,
                    reason="React-mute expired",
                    duration=None
                )
                embed = discord.Embed(
                    description=f"{member.mention} has been unreact-muted (duration expired).",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            except Exception as e:
                self.bot.logger.error(f"Failed to auto-unreact-mute {member}: {e}")

    @commands.command(name="runmute", description="Unmute a user by removing the mute role.",)
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def react_unmute_user(self, ctx: Context, *, identifier: str):
        """Restore a users reaction permissions"""
        guild_id = ctx.guild.id
        mute_settings = await self.bot.database.get_mute_settings(guild_id)

        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if not mute_settings:
            await ctx.send("No mute settings found for this server. Use `!mutesetup` to configure.")
            return

        rmuted_role = ctx.guild.get_role(mute_settings.rmute_role_id)
        if not rmuted_role:
            await ctx.send("The react mute role could not be found. Contact an admin.")
            return

        if rmuted_role in member.roles:
            await member.remove_roles(rmuted_role, reason=f"React unmuted by {ctx.author}")
            await self.bot.database.add_punishment(
                user_id=member.id,
                guild_id=ctx.guild.id,
                moderator_id=ctx.author.id,
                punishment_type=PunishmentType.RUNMUTE,
                reason="N/A",
                duration=None
            )
            embed = discord.Embed(description=f"{member.name} has been react unmuted.", color=discord.Color.blurple())
            embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f"{member.name} is not currently react muted.", color=discord.Color.red())
            await ctx.send(embed=embed)

    @commands.command(name="imute", description="Image mute a user by assigning them the image mute role.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def image_mute_user(self, ctx: Context, identifier: str, *args):
        """
        Usage:
          !imute @user               > indefinite image-mute
          !imute @user 2h spoilers   > 2-hour image-mute for "spoilers"
          !imute @user spoilers      > indefinite image-mute for "spoilers"
        """
        member = await self._lookup_member(ctx, identifier)
        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found with identifier `{identifier}`.", color=discord.Color.red()
            ))

        if member.top_role.position >= ctx.author.top_role.position:
            embed = discord.Embed(
                description="🚫 You cannot mute a user with a role higher than or equal to yours!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        if member.top_role.position >= ctx.guild.me.top_role.position:
            embed = discord.Embed(
                description="🚫 I cannot mute a user with a role higher than or equal to mine!",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        
        duration_seconds: Optional[int] = None
        reason = "No reason provided"
        if args:
            first = args[0]
            if re.match(r"^\d+[smhd]$", first):
                try:
                    duration_seconds = humanfriendly.parse_timespan(first)
                except humanfriendly.InvalidTimespan:
                    reason = " ".join(args)
                else:
                    reason = " ".join(args[1:]) or reason
            else:
                reason = " ".join(args)

        mute_settings = await self.bot.database.get_mute_settings(ctx.guild.id)
        if not mute_settings:
            return await ctx.send("No mute settings found: run `!mutesetup` first.")
        imuted_role = ctx.guild.get_role(mute_settings.imute_role_id)
        if not imuted_role:
            return await ctx.send("🚫 Cannot find the configured image-mute role.")

        await member.add_roles(imuted_role, reason=f"Image-muted by {ctx.author} for {reason}")
        await self.bot.database.add_punishment(
            user_id=member.id,
            guild_id=ctx.guild.id,
            moderator_id=ctx.author.id,
            punishment_type=PunishmentType.IMUTE,
            reason=reason,
            duration=duration_seconds
        )

        desc = f"**{member}** has been image-muted."
        if duration_seconds:
            desc += f" (for {first})"
        embed = discord.Embed(description=desc, color=discord.Color.blurple())
        embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
        embed.set_footer(text=f"Reason: {reason}")
        await ctx.send(embed=embed)

        if duration_seconds:
            asyncio.create_task(self._auto_image_unmute(member, imuted_role, duration_seconds, ctx))

    async def _auto_image_unmute(self, member: discord.Member, role: discord.Role, delay: int, ctx: Context):
        await asyncio.sleep(delay)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Image-mute duration expired")
                await self.bot.database.add_punishment(
                    user_id=member.id,
                    guild_id=ctx.guild.id,
                    moderator_id=self.bot.user.id,
                    punishment_type=PunishmentType.IUNMUTE,
                    reason="Image-mute expired",
                    duration=None
                )
                embed = discord.Embed(
                    description=f"{member.mention} has been unimage-muted (duration expired).",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            except Exception as e:
                self.bot.logger.error(f"Failed to auto-unimage-mute {member}: {e}")

    @commands.command(name="iunmute", description="Unmute a user by removing the mute role.",)
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def image_unmute_user(self, ctx: Context, * identifier: str):
        """Restore a users image permissions"""
        guild_id = ctx.guild.id
        mute_settings = await self.bot.database.get_mute_settings(guild_id)

        member = None

        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier))
                if not member:
                    member = await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass

        elif re.match(r'^<@!?(\d+)>$', identifier):
            mention_match = re.match(r'^<@!?(\d+)>$', identifier)
            mention_id = mention_match.group(1)
            member = ctx.guild.get_member(int(mention_id))

        else:
            identifier = identifier.lower()
            member = discord.utils.find(
            lambda m: identifier in m.name.lower(), ctx.guild.members
            )

        if not member:
            embed = discord.Embed(
                description=f"No user found with the identifier: {identifier}. Please try again.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        if not mute_settings:
            await ctx.send("No mute settings found for this server. Use `!mutesetup` to configure.")
            return

        imuted_role = ctx.guild.get_role(mute_settings.imute_role_id)
        if not imuted_role:
            await ctx.send("The image mute role could not be found. Contact an admin.")
            return

        if imuted_role in member.roles:
            await member.remove_roles(imuted_role, reason=f"Image unmuted by {ctx.author}")
            await self.bot.database.add_punishment(
                user_id=member.id,
                guild_id=ctx.guild.id,
                moderator_id=ctx.author.id,
                punishment_type=PunishmentType.IUNMUTE,
                reason="N/A",
                duration=None
            )
            embed = discord.Embed(description=f"{member.name} has been image unmuted.", color=discord.Color.blurple())
            embed.set_author(name=f"Moderator: {ctx.author}", icon_url=self.utils.get_avatar_url(ctx.author))
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f"{member.name} is not currently image muted.", color=discord.Color.red())
            await ctx.send(embed=embed)

    @commands.command(name='ce', aliases=['enablecommand'], hidden=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def enable_channel_command(self, ctx: Context, command_name: str, channel: discord.TextChannel = None):
        """Enable a command in the specified channel."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await ctx.send(f"The command `{command_name}` does not exist.")
            return

        channel_id = channel.id if channel else ctx.channel.id
        channel_name = channel.mention if channel else ctx.channel.mention
        current_status = await self.bot.database.get_command_status(command_name, channel_id)

        if current_status:
            await ctx.send(f"The `{command_name}` command is already enabled in {channel_name}.")
        else:
            await self.bot.database.set_command_status(command_name, enabled=True, channel_id=channel_id)
            await ctx.send(f"The `{command_name}` command has been enabled in {channel_name}.")

    @commands.command(name='cd', aliases=['disablecommand'], hidden=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def disable_channel_command(self, ctx: Context, command_name: str, channel: discord.TextChannel = None):
        """Disable a command in the specified channel."""
        command_exists = await self._check_command_exists(command_name)

        if not command_exists:
            await ctx.send(f"The command `{command_name}` does not exist.")
            return

        channel_id = channel.id if channel else ctx.channel.id
        channel_name = channel.mention if channel else ctx.channel.mention
        current_status = await self.bot.database.get_command_status(command_name, channel_id)

        if not current_status:
            await ctx.send(f"The `{command_name}` command is already disabled in {channel_name}.")
        else:
            await self.bot.database.set_command_status(command_name, enabled=False, channel_id=channel_id)
            await ctx.send(f"The `{command_name}` command has been disabled in {channel_name}.")

    @commands.command(name='case')
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def case_info(self, ctx: Context, case_id: int):
        """View details of a specific case."""
        guild_id = ctx.guild.id

        punishments = await self.bot.database.get_punishments(case_id, guild_id)

        if not punishments:
            await ctx.send("No punishments found for this case.")
            return

        embed = discord.Embed(
            title=f"Case ID: {case_id}",
            description=f"Punishment details for Case #{case_id}:",
            color=discord.Color.blurple()
        )

        for punishment in punishments:
            moderator = ctx.guild.get_member(punishment.moderator_id) or await self.bot.fetch_user(punishment.moderator_id)
            user = ctx.guild.get_member(punishment.user_id) or await self.bot.fetch_user(punishment.user_id)
            formatted_duration = humanfriendly.format_timespan(punishment.duration) if punishment.duration else 'N/A'
            embed.add_field(
                name=f"{punishment.type.value.capitalize()}",
                value=(
                    f"**User:** {user.name if user else 'Unknown'} ({user.id if user else 'Unknown'})\n"
                    f"**Reason:** {punishment.reason}\n"
                    f"**Moderator:** {moderator.name if moderator else 'Unknown'}\n"
                    f"**Duration:** {formatted_duration}\n"
                    f"**Date:** {punishment.created_at.strftime('%Y-%m-%d %I:%M %p UTC')}"
                ),
                inline=False
            )

        notes = await self.bot.database.get_case_notes(case_id)
        notes_str = "\n".join([f"Note by {ctx.guild.get_member(note.moderator_id).mention if ctx.guild.get_member(note.moderator_id) else 'Unknown'} at {note.created_at.strftime('%Y-%m-%d %I:%M %p UTC')}: {note.note}" for note in notes])

        if notes_str:
            embed.add_field(name="Notes", value=notes_str, inline=False)
        else:
            embed.add_field(name="Notes", value="No notes found for this case.", inline=False)

        await ctx.send(embed=embed)

    @commands.command(name='casenote')
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def add_note(self, ctx: Context, case_id: int, *, note: str):
        """Add a note to a specific case."""
        try:
            await self.bot.database.add_case_note(case_id=case_id, moderator_id=ctx.author.id, note=note)
            await ctx.send(f"Note added to case {case_id}.")
        except Exception as e:
            await ctx.send(f"An error occurred while adding the note: {e}")

    @commands.command(name='reason')
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def update_case(self, ctx: Context, case_id: int, *, new_reason: str):
        """Update the reason for a specific case."""
        try:
            punishment = await self.bot.database.get_punishment(case_id=case_id)
            if not punishment:
                await ctx.send(f"Case {case_id} not found.")
                return

            if punishment.moderator_id != ctx.author.id:
                await ctx.send("You are not authorized to update this case as you were not the original moderator.")
                return

            await self.bot.database.update_punishment_reason(case_id=case_id, new_reason=new_reason)
            await ctx.send(f"Case {case_id} updated with new reason.")
        except Exception as e:
            await ctx.send(f"An error occurred while updating the case reason: {e}")

    @commands.command(name='nukemsg', description='Changes the nuke message for the current server')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_nuke_message(self, ctx: Context, *, nuke_message: str):
        """Sets the nuke message for the server."""
        try:
            await self.bot.database.set_nuke_msg(ctx.guild.id, nuke_message)
            mentions_pattern = r'@everyone|@here|<@&\d+>'
            if re.search(mentions_pattern, nuke_message):
                embed = discord.Embed(
                    description="No mentions are allowed in the nuke message",
                )
                await ctx.send(embed=embed)
                return

            embed = discord.Embed(
                description="Nuke message updated successfully.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.exception("Failed to set nuke message")
            embed = discord.Embed(
                description="An error occurred while updating the nuke message.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

    @commands.command(name='nuke', description='Deletes and recreates the current channel')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx: Context):
        """Nukes a channel with a confirmation via buttons."""
        try:
            logger.info("!!!!!!!!!!!!!!!!!!!!!!!!")
            logger.info(f"NUKE LAUNCH INITIATED BY: {ctx.author.name} ({ctx.author.id})")
            logger.info(f"SERVER: {ctx.guild.name}, CHANNEL: {ctx.channel.name}")
            logger.info("!!!!!!!!!!!!!!!!!!!!!!!!")

            embed = discord.Embed(
                title="NUKE AUTHORIZATION",
                description="Click Confirm to proceed or Cancel to abort. You have 60 seconds.",
                color=discord.Color.red()
            )
            embed.set_author(name=ctx.author.name, icon_url=self.utils.get_avatar_url(ctx.author))
            auth_message = await ctx.reply(embed=embed)

            # Define the actual nuke procedure here so the view can start/cancel it immediately
            async def _do_nuke(cancel_event: asyncio.Event):
                try:
                    # record cooldown
                    try:
                        await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 900)
                    except Exception:
                        logger.exception("Failed to set nuke cooldown")

                    # perform the dramatic countdown in the channel, but abort if cancel_event is set
                    try:
                        countdown_message = await ctx.send("# 🔥 Nuke Incoming! 🔥")
                        await asyncio.sleep(1)
                        for i in range(3, 0, -1):
                            if cancel_event.is_set():
                                try:
                                    await countdown_message.edit(content="# ❌ Nuke Aborted ❌")
                                except Exception:
                                    pass
                                return
                            await countdown_message.edit(content=f"# 💥 NUKE IN {i} SECONDS 💥")
                            await asyncio.sleep(1)
                        if cancel_event.is_set():
                            try:
                                await countdown_message.edit(content="# ❌ Nuke Aborted ❌")
                            except Exception:
                                pass
                            return
                        await countdown_message.edit(content="https://tenor.com/view/nuke-gif-8044239")
                        await asyncio.sleep(3)
                    except (discord.HTTPException, discord.Forbidden):
                        # can't send messages but continue to attempt the channel replacement
                        pass

                    if cancel_event.is_set():
                        return

                    channel = ctx.channel
                    channel_name = channel.name
                    channel_position = channel.position
                    channel_category = channel.category
                    # copy overwrites mapping
                    try:
                        channel_overwrites = channel.overwrites
                    except Exception:
                        # fallback: build overwrites dict manually
                        try:
                            channel_overwrites = {o[0]: o[1] for o in getattr(channel, "overwrites", {}).items()}
                        except Exception:
                            channel_overwrites = None

                    # check lockdown table and remove if necessary
                    try:
                        is_lockdown = await self.bot.database.is_lockdown_channel(ctx.guild.id, channel.id)
                        if is_lockdown:
                            await self.bot.database.remove_lockdown_channel(ctx.guild.id, channel.id)
                    except Exception:
                        is_lockdown = False

                    # delete the channel
                    try:
                        await channel.delete()
                    except Exception as e:
                        logger.exception("Failed to delete channel during nuke")
                        return

                    # recreate in same category (or at guild root if None)
                    try:
                        if channel_category:
                            new_channel = await channel_category.create_text_channel(
                                name=channel_name,
                                overwrites=channel_overwrites
                            )
                        else:
                            # create at guild level
                            new_channel = await ctx.guild.create_text_channel(
                                name=channel_name,
                                overwrites=channel_overwrites
                            )
                    except Exception:
                        logger.exception("Failed to create replacement channel")
                        return

                    # re-add lockdown flag if needed
                    try:
                        if is_lockdown:
                            await self.bot.database.add_lockdown_channel(ctx.guild.id, new_channel.id)
                    except Exception:
                        pass

                    try:
                        await new_channel.edit(position=channel_position)
                    except Exception:
                        pass

                    try:
                        nuke_msg = await self.bot.database.get_nuke_msg(ctx.guild.id)
                        await new_channel.send(f"# {nuke_msg}\n-# This channel was purged to maintain a friendly environment.")
                    except Exception:
                        pass

                except Exception:
                    logger.exception("Unexpected error in nuke task")

            class NukeConfirmView(discord.ui.View):
                def __init__(self, *, timeout=60):
                    super().__init__(timeout=timeout)
                    self.result = None  # "confirm" | "cancel" | None
                    self._nuke_task: Optional[asyncio.Task] = None
                    self._cancel_event = asyncio.Event()

                async def interaction_check(self, interaction: discord.Interaction) -> bool:
                    # Only the command invoker may confirm/cancel
                    if interaction.user.id != ctx.author.id:
                        await interaction.response.send_message("Only the command invoker can use these buttons.", ephemeral=True)
                        return False
                    return True

                @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
                async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Disable buttons and acknowledge
                    for child in self.children:
                        child.disabled = True
                    self.result = "confirm"
                    try:
                        await interaction.response.edit_message(embed=discord.Embed(
                            title="NUKE AUTHORIZATION",
                            description=f"Confirmed by {interaction.user.mention}. Starting countdown...",
                            color=discord.Color.red()
                        ), view=self)
                    except Exception:
                        # If editing fails, at least respond
                        await interaction.response.send_message("Confirmed. Starting countdown...", ephemeral=True)

                    # Start the nuke task immediately
                    if not self._nuke_task:
                        self._nuke_task = asyncio.create_task(_do_nuke(self._cancel_event))

                    # stop the view.wait() as we've handled the action
                    self.stop()

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                    for child in self.children:
                        child.disabled = True
                    self.result = "cancel"
                    # Signal cancellation
                    self._cancel_event.set()

                    # If a nuke task started, attempt to cancel it
                    if self._nuke_task and not self._nuke_task.done():
                        try:
                            self._nuke_task.cancel()
                            # optionally await to let it cleanup; do not block long
                            try:
                                await asyncio.wait_for(self._nuke_task, timeout=2)
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                pass
                        except Exception:
                            pass

                    try:
                        await interaction.response.edit_message(embed=discord.Embed(
                            title="NUKE AUTHORIZATION",
                            description=f"Canceled by {interaction.user.mention}. Nuke aborted.",
                            color=discord.Color.greyple()
                        ), view=self)
                    except Exception:
                        await interaction.response.send_message("Nuke aborted.", ephemeral=True)

                    # stop the view.wait()
                    self.stop()

                async def on_timeout(self):
                    # on timeout, try to edit the message to show timed out
                    try:
                        for child in self.children:
                            child.disabled = True
                        await auth_message.edit(embed=discord.Embed(
                            title="NUKE AUTHORIZATION",
                            description="Timed out. Nuke canceled.",
                            color=discord.Color.greyple()
                        ), view=self)
                    except Exception:
                        pass
                    # ensure any started nuke is cancelled
                    self._cancel_event.set()
                    if self._nuke_task and not self._nuke_task.done():
                        try:
                            self._nuke_task.cancel()
                        except Exception:
                            pass
                    self.stop()

            view = NukeConfirmView()
            await auth_message.edit(view=view)

            # Wait until view finishes (confirm, cancel or timeout)
            await view.wait()

            # After the view ends, if result is not confirm we do nothing.
            # If result was confirm, the _do_nuke task was already started by the view.
            if view.result != "confirm":
                logger.info("NUKE ABORTED OR TIMED OUT.")
                return

            # nothing more to do here; the background task is running

        except discord.HTTPException:
            await ctx.send("I do not have permission to nuke this channel.")
        except discord.Forbidden:
            await ctx.send("I do not have permission to nuke this channel.")

    @commands.command(name='slowmode', description='Set the slowmode for the current channel')
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, state: str, duration: str, channel: discord.TextChannel = None):
        channel = channel or ctx.channel

        if state.lower() not in ['on', 'off']:
            await ctx.send("Invalid state. Use 'on' or 'off'.")
            return

        if state.lower() == 'off':
            await channel.edit(slowmode_delay=0)
            await ctx.send(f"Slowmode turned off in {channel.mention}")
            return

        time_multiplier = {'s': 1, 'm': 60, 'h': 3600}
        if duration[-1] not in time_multiplier:
            await ctx.send("Invalid duration format. Use 's', 'm', or 'h'.")
            return

        try:
            seconds = int(duration[:-1]) * time_multiplier[duration[-1]]
        except ValueError:
            await ctx.send("Invalid duration value.")
            return

        await channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to {duration} in {channel.mention}")

    @commands.command(name="lock", help="Locks a specified channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock_channel(self, ctx: Context, channel: discord.TextChannel = None, *, reason: str = "No reason provided"):
        """Locks down a specified channel."""
        channel = channel or ctx.channel  

        overwrite = channel.overwrites_for(ctx.guild.default_role)

        if overwrite.send_messages is False:
            await ctx.send(f"🔒 {channel.mention} is already locked.")
            return

        overwrite.send_messages = False
        overwrite.send_messages_in_threads = False
        overwrite.create_public_threads = False
        overwrite.create_private_threads = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"🔒 Locked {channel.mention} for the reason: {reason}")

    @commands.command(name="unlock", help="Unlocks a specified channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock_channel(self, ctx: Context, channel: discord.TextChannel = None):
        """Unlocks a specified channel."""
        channel = channel or ctx.channel  

        overwrite = channel.overwrites_for(ctx.guild.default_role)

        if overwrite.send_messages is None or overwrite.send_messages is True:
            await ctx.send(f"🔓 {channel.mention} is not locked.")
            return

        overwrite.send_messages = None
        overwrite.send_messages_in_threads = None
        overwrite.create_public_threads = None
        overwrite.create_private_threads = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send(f"🔓 Unlocked {channel.mention}.")

    @commands.group(
        name="lockchannel", aliases=["lockchan", "lockchanlist", "lockchannels", 'lch'],
        invoke_without_command=True,
        help="Manage which channels are affected by lockdown."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def lockchan(self, ctx: Context):
        embed = discord.Embed(title="Lockdown Channel Management",
                              description="Subcommands: `add`, `remove`, `list`")
        await ctx.send(embed=embed)

    @lockchan.command(name="add", help="Add a channel to the lockdown list.")
    async def lockchan_add(self, ctx: Context,
                           channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await self.bot.database.add_lockdown_channel(ctx.guild.id, channel.id)
        if not channel:
            await ctx.send("Channel not found.")
            return
        embed = discord.Embed(title="Lockdown Channel Management",
                              description=f"✅ Added {channel.mention} to the lockdown list.")
        await ctx.send(embed=embed)

    @lockchan.command(name="remove", help="Remove a channel from the lockdown list.")
    async def lockchan_remove(self, ctx: Context,
                              channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ok = await self.bot.database.remove_lockdown_channel(ctx.guild.id, channel.id)
        if ok:
            embed = discord.Embed(title="Lockdown Channel Management",
                                  description=f"✅ Removed {channel.mention} from the lockdown list.")
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(title="Lockdown Channel Management",
                                  description=f"🚫 {channel.mention} was not in the lockdown list.")
            await ctx.send(embed=embed)

    @lockchan.command(name="list", help="List all channels in the lockdown list.")
    async def lockchan_list(self, ctx: Context):
        ids = await self.bot.database.get_lockdown_channels(ctx.guild.id)
        if not ids:
            embed = discord.Embed(title="Lockdown Channel Management",
                                   description="No channels in the lockdown list.")
            return await ctx.send(embed=embed)
        mentions = []
        for cid in ids:
            ch = ctx.guild.get_channel(cid)
            if ch:
                mentions.append(ch.mention)
        await ctx.send("🔒 **Lockdown channels:**\n" + "\n".join(mentions))

    @commands.command(name="lockdown", aliases=["ld"], help="Lock all channels in the lockdown list.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def lockdown(self, ctx: Context):
        ids = await self.bot.database.get_lockdown_channels(ctx.guild.id)
        if not ids:
            return await ctx.send("No channels configured for lockdown.")
        locked = 0
        for cid in ids:
            ch = ctx.guild.get_channel(cid)
            if not ch:
                continue
            ow = ch.overwrites_for(ctx.guild.default_role)
            if ow.send_messages is False:
                continue
            ow.send_messages = False
            ow.send_messages_in_threads = False
            ow.create_public_threads = False
            ow.create_private_threads = False
            await ch.set_permissions(ctx.guild.default_role, overwrite=ow)
            locked += 1
        await ctx.send(f"🔒 Locked {locked} channel(s).")

    @commands.command(name="unlockdown", aliases=["uld"], help="Unlock all channels in the lockdown list.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def unlockdown(self, ctx: Context):
        ids = await self.bot.database.get_lockdown_channels(ctx.guild.id)
        if not ids:
            return await ctx.send("No channels configured for lockdown.")
        unlocked = 0
        for cid in ids:
            ch = ctx.guild.get_channel(cid)
            if not ch:
                continue
            ow = ch.overwrites_for(ctx.guild.default_role)
            if ow.send_messages is None or ow.send_messages is True:
                continue
            ow.send_messages = None
            ow.send_messages_in_threads = None
            ow.create_public_threads = None
            ow.create_private_threads = None
            await ch.set_permissions(ctx.guild.default_role, overwrite=ow)
            unlocked += 1
        await ctx.send(f"🔓 Unlocked {unlocked} channel(s).")

    @commands.command(name="modstats", description="Shows moderation command usage by a staff member.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def modstats(self, ctx: Context, identifier: str):
        """Interactive graph of mod actions over 7d, 14d or all time."""

        member = None
        if re.match(r'^\d+$', identifier):
            member = ctx.guild.get_member(int(identifier)) or await self.bot.fetch_user(int(identifier))
        elif m := re.match(r'^<@!?(\d+)>$', identifier):
            member = ctx.guild.get_member(int(m.group(1)))
        else:
            identifier_l = identifier.lower()
            member = discord.utils.find(lambda m: identifier_l in m.name.lower(), ctx.guild.members)

        if not member:
            return await ctx.send(embed=discord.Embed(
                description=f"No user found: `{identifier}`", color=discord.Color.red()
            ))

        guild_id = ctx.guild.id

        windows = [("7 Days", 7), ("14 Days", 14), ("All Time", None)]
        types   = [
            ("Warns",    PunishmentType.WARN),
            ("Mutes",    PunishmentType.MUTE),
            ("Jails",    PunishmentType.JAIL),
            ("Kicks",    PunishmentType.KICK),
            ("Bans",     PunishmentType.BAN),
            ("Timeouts", PunishmentType.TIMEOUT),
        ]

        async def fetch_counts(days):
            tasks = []
            for _, ptype in types:
                if days is None:
                    tasks.append(self.bot.database.count_punishment_usage(member.id, guild_id, ptype))
                else:
                    tasks.append(self.bot.database.count_punishment_usage(member.id, guild_id, ptype, days))
            results = await asyncio.gather(*tasks)
            # Ensure integer, non-negative values only
            cleaned = []
            for r in results:
                try:
                    # disallow non-whole values by coercing safely to int
                    if isinstance(r, float) and not r.is_integer():
                        # round to nearest whole number (or you can choose floor/ceil)
                        r = int(round(r))
                    else:
                        r = int(r)
                except Exception:
                    r = 0
                if r < 0:
                    r = 0
                cleaned.append(r)
            return cleaned

        def make_chart_bytes(counts, window_label):
            # ensure counts are ints
            counts = [int(c) for c in counts]
            labels = [t[0] for t in types]
            x = range(len(labels))

            # create chart
            fig, ax = plt.subplots()
            ax.bar(x, counts, width=0.6)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=15, ha="right")
            ax.set_ylabel("Count")
            ax.set_title(f"{member.display_name} – {window_label}")

            # force integer y-ticks
            try:
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            except Exception:
                pass

            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png")
            buf.seek(0)
            data = buf.getvalue()
            plt.close(fig)
            return data

        # simple in-memory cache for this command invocation to speed button presses
        cache: dict[str, bytes] = {}

        class StatsView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.current = windows[0][0]

            async def update_message(self, interaction, window_label, days):
                # if cached, reuse bytes, otherwise generate and cache
                if window_label in cache:
                    img_bytes = cache[window_label]
                else:
                    counts = await fetch_counts(days)
                    img_bytes = make_chart_bytes(counts, window_label)
                    cache[window_label] = img_bytes

                buf = io.BytesIO(img_bytes)
                file = discord.File(buf, filename="modstats.png")
                try:
                    await interaction.response.edit_message(content=None, attachments=[file], view=self)
                except Exception:
                    # fallback: send a new message if edit fails
                    await interaction.channel.send(file=file, view=self)

            @discord.ui.button(label="7 Days", style=discord.ButtonStyle.primary)
            async def seven(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.update_message(interaction, "7 Days", 7)

            @discord.ui.button(label="14 Days", style=discord.ButtonStyle.primary)
            async def fourteen(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.update_message(interaction, "14 Days", 14)

            @discord.ui.button(label="All Time", style=discord.ButtonStyle.primary)
            async def all_time(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.update_message(interaction, "All Time", None)

        # generate initial chart and cache it
        initial_counts = await fetch_counts(windows[0][1])
        initial_bytes = make_chart_bytes(initial_counts, windows[0][0])
        cache[windows[0][0]] = initial_bytes
        file = discord.File(io.BytesIO(initial_bytes), filename="modstats.png")
        await ctx.send(file=file, view=StatsView())

    @commands.group(name="alts", description="Manage and list linked accounts.", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def alts(self, ctx: commands.Context, *, identifier: str):
        """List all accounts linked to a user."""
        # Resolve the user
        member = None
        if re.match(r'^\d+$', identifier):
            try:
                member = ctx.guild.get_member(int(identifier)) or await self.bot.fetch_user(int(identifier))
            except discord.NotFound:
                pass
        elif mention_match := re.match(r'^<@!?(\d+)>$', identifier):
            member = ctx.guild.get_member(int(mention_match.group(1)))
        else:
            name = identifier.lower()
            member = discord.utils.find(lambda m: name in m.name.lower(), ctx.guild.members)

        if not member:
            embed = discord.Embed(
                description=f"No user found with identifier `{identifier}`.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        # Use recursive lookup for all linked accounts
        linked_ids = await self.bot.database.get_all_linked_user_ids(member.id, ctx.guild.id)
        if not linked_ids:
            embed = discord.Embed(
                description="No linked accounts found.",
                color=discord.Color.yellow()
            )
            return await ctx.send(embed=embed)

        # Fetch user objects for display
        linked_members = []
        for uid in linked_ids:
            user = ctx.guild.get_member(uid) or await self.bot.fetch_user(uid)
            if user:
                linked_members.append(user)

        embed = discord.Embed(
            title=f"Linked Accounts of {member.display_name}",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="Accounts",
            value="\n".join(f"{u.display_name or u.name} (`{u.id}`)" for u in linked_members),
            inline=False
        )
        await ctx.send(embed=embed)

    @alts.command(name="add", description="Add an alt for a user via snowflake.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def add(self, ctx: Context, main_id: int, alt_id: int):
        """Add an alt for a user."""
        not_implemented = False
        if not_implemented:
            await ctx.reply("This command is not yet implemented.", delete_after=5)
            return

        main_id = ctx.guild.get_member(main_id) or await self.bot.fetch_user(main_id)
        alt_id = ctx.guild.get_member(alt_id) or await self.bot.fetch_user(alt_id)
        if not main_id or not alt_id:
            return await ctx.send("Both main and alt users must be valid members.")
        if main_id.id == alt_id.id:
            return await ctx.send("You cannot add a user as their own alt.")
        
        await self.bot.database.add_user_alt(main_id.id, ctx.guild.id, alt_id.id)
        await ctx.send(f"Added {alt_id.display_name} as an alt for {main_id.display_name}.")

    @alts.command(name="remove", description="Remove an alt for a user via snowflake.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def remove(self, ctx: Context, member: discord.Member, alt: discord.Member):
        """Remove an alt for a user."""
        not_implemented = False
        if not_implemented:
            await ctx.reply("This command is not yet implemented.", delete_after=5)
            return

        await self.bot.database.remove_user_alt(member.id, ctx.guild.id, alt.id)
        await ctx.send(f"Removed {alt.display_name} as an alt for {member.display_name}.")

    @alts.command(name="clear", description="Clear all known alts for a user.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def clear(self, ctx: Context, member: discord.Member):
        """Clear all alts for a user."""
        not_implemented = False
        if not_implemented:
            await ctx.reply("This command is not yet implemented.", delete_after=5)
            return

        await self.bot.database.clear_user_alts(member.id, ctx.guild.id)
        await ctx.send(f"Cleared all alts for {member.display_name}.")

    @commands.group(name="restrictcommand", aliases=["rc"], description="Manage command restrictions for specific roles.", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def restrictcommand(self, ctx: Context):
        embed = discord.Embed(
            title="Command Restriction Subcommands",
            description="Displays the subcommands for the restrict command",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="Available Subcommands:",
            value=(
                "`add <command> <role>` - Restrict a command to a specific role\n"
                "`remove <command> <role>` - Remove restriction for a role\n"
                "`list [command/role]` - List restricted commands\n"
                "`reset` - Remove all command restrictions"),
            inline=False
        )
        await ctx.send(embed=embed)

    @restrictcommand.command(name="add", description="Allows the specified role exclusive permission to use a command")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def restrictcommand_add(self, ctx: Context, command_name: str, role: discord.Role):
        command_exists = await self._check_command_exists(command_name)
        if not command_exists:
            embed = discord.Embed(
                description=f"The command `{command_name}` does not exist.",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        await self.bot.database.add_command_role_restriction(ctx.guild.id, command_name.lower(), role.id)
        
        embed = discord.Embed(
            title="",
            description=f"{ctx.author.mention}: Now allowing users with {role.mention} to use **{command_name}**.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @restrictcommand.command(name="remove", description="Removes the specified roles exclusive permission to use a command")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def restrictcommand_remove(self, ctx: Context, command_name: str, role: discord.Role):

        success = await self.bot.database.remove_command_role_restriction(ctx.guild.id, command_name.lower(), role.id)
        
        if success:
            embed = discord.Embed(
                title="",
                description=f"No longer allowing users with {role.mention} to use **{command_name}**.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="",
                description=f"No restriction found for `{command_name}` and the {role.mention} role.",
                color=discord.Color.red()
            )
        await ctx.send(embed=embed)

    @restrictcommand.command(name="reset", description="Removes every restricted command")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def restrictcommand_reset(self, ctx: Context):
        count = await self.bot.database.clear_all_command_restrictions(ctx.guild.id)
        
        embed = discord.Embed(
            title="",
            description=f"Removed {count} command restriction(s) from this server.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @restrictcommand.command(name="list", description="View a list of every restricted command")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def restrictcommand_list(self, ctx: Context, filter_arg: str = None):
        restrictions = await self.bot.database.get_command_restrictions(ctx.guild.id)
        
        if not restrictions:
            embed = discord.Embed(
                title="No Restrictions",
                description="No command restrictions are currently active in this server.",
                color=discord.Color.greyple()
            )
            return await ctx.send(embed=embed)

        filtered_restrictions = []
        filter_type = ""
        if filter_arg:
            role = None
            if filter_arg.startswith('<@&') and filter_arg.endswith('>'):
                role_id = int(filter_arg[3:-1])
                role = ctx.guild.get_role(role_id)
            elif filter_arg.isdigit():
                role = ctx.guild.get_role(int(filter_arg))
            else:
                role = discord.utils.find(lambda r: filter_arg.lower() in r.name.lower(), ctx.guild.roles)

            if role:
                filtered_restrictions = [r for r in restrictions if r.role_id == role.id]
                filter_type = f"role {role.mention}"
            else:
                filtered_restrictions = [r for r in restrictions if filter_arg.lower() in r.command_name.lower()]
                filter_type = f"command `{filter_arg}`"
                
            restrictions = filtered_restrictions

        if not restrictions:
            embed = discord.Embed(
                title="No Matching Restrictions",
                description=f"No command restrictions found for {filter_type}.",
                color=discord.Color.greyple()
            )
            return await ctx.send(embed=embed)

        command_restrictions = {}
        for restriction in restrictions:
            if restriction.command_name not in command_restrictions:
                command_restrictions[restriction.command_name] = []
            
            role = ctx.guild.get_role(restriction.role_id)
            if role:
                command_restrictions[restriction.command_name].append(role.mention)

        commands_list = list(command_restrictions.items())
        total_entries = sum(len(roles) for roles in command_restrictions.values())
        
        entries_list = []
        entry_number = 1
        for command_name, roles in commands_list:
            for role in roles:
                entries_list.append(f"`{entry_number}` {role} can use **{command_name}**")
                entry_number += 1

        class RestrictionsView(discord.ui.View):
            def __init__(self, entries_list, total_entries, filter_type):
                super().__init__(timeout=300)
                self.entries_list = entries_list
                self.total_entries = total_entries
                self.filter_type = filter_type
                self.current_page = 0
                self.max_pages = (len(entries_list) - 1) // 10 + 1

            def create_embed(self):
                start_idx = self.current_page * 10
                end_idx = min(start_idx + 10, len(self.entries_list))
                page_entries = self.entries_list[start_idx:end_idx]

                embed = discord.Embed(
                    title="Restricted Commands",
                    description="\n".join(page_entries),
                    color=discord.Color.blurple()
                )
                
                embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages} ({self.total_entries} entries)")
                return embed

            @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
            async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page == 0:
                    self.current_page = self.max_pages - 1
                else:
                    self.current_page -= 1
                
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self)

            @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
            async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page == self.max_pages - 1:
                    self.current_page = 0
                else:
                    self.current_page += 1
                
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self)

        view = RestrictionsView(entries_list, total_entries, filter_type)
        embed = view.create_embed()
        await ctx.send(embed=embed, view=view)

async def setup(bot) -> None:
    await bot.add_cog(Moderation(bot))
    logger.debug('Moderation cog initialized successfully')

