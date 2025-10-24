import re
import discord
import logging
import asyncio
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from discord.ext.commands import Context
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("discord_bot")

class Watchdog(commands.Cog, name="Watchdog"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.log_queue = defaultdict(list)  
        self.max_queue_size = 10  
        self.process_interval = 10  
        self.guild_settings_cache = {}  
        self.pii_filter = True  # Enable or disable PII filtering
        self.discord_patterns = [
            # Discord gift link pattern
            re.compile(r'(https?://)?discord((app)?.com/gifts|.gifts)/[a-zA-Z0-9-]+/?'),

            # Discord invite link pattern
            re.compile(r'(https?://)?(www\.)?(discord\.gg|discord\.com/invite)/[a-zA-Z0-9-]+/?')

            # Add more patterns as needed
        ]
        self.pii_patterns = [
            # Street addresses pattern
            re.compile(
            r'\b\d{1,5}(?:\s+\w+)*\s+' 
            r'(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr)\b',
            re.IGNORECASE
            ),

            # Email address pattern
            re.compile(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
            re.IGNORECASE
            ),

            # Phone number patterns (international and US)
            re.compile(
            r'\b(?:\+?\d{1,2}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b'
            ),

            # Credit card number pattern (simplest version tbh)
            re.compile(r'\b(?:\d[ -]*?){13,16}\b'),

            # Discord authentication token pattern
            re.compile(
            r'([a-zA-Z0-9]{24}\.[a-zA-Z0-9]{6}\.[a-zA-Z0-9_\-]{27}|mfa\.[a-zA-Z0-9_\-]{84})'
            ),

            # Social Security Number pattern
            re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),  # SSN format

            # Social Insurance Number pattern
            re.compile(r'\b\d{3} \d{3} \d{3}\b'),  # SIN format

            # Add more patterns as needed
        ]
        self.process_log_queue.start()

    def cog_unload(self):
        self.process_log_queue.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    async def get_guild_settings(self, guild_id: int) -> Optional[dict]:
        """Get cached guild settings or fetch from database"""
        if guild_id not in self.guild_settings_cache or (datetime.now() - self.guild_settings_cache.get(guild_id, {}).get('last_updated', datetime.min)).total_seconds() > 300:
            settings = await self.bot.database.get_server_settings(guild_id)
            if settings:
                self.guild_settings_cache[guild_id] = {
                    'enabled': settings.watchdog_enabled,
                    'channel_id': settings.watchdog_channel_id,
                    'last_updated': datetime.now()
                }
            else:
                self.guild_settings_cache[guild_id] = {
                    'enabled': False,
                    'channel_id': None,
                    'last_updated': datetime.now()
                }

        return self.guild_settings_cache.get(guild_id)

    async def is_logging_enabled(self, guild_id: int) -> bool:
        """Check if logging is enabled in the guild using cached settings."""
        settings = await self.get_guild_settings(guild_id)
        return settings and settings.get('enabled', False)

    async def add_log_entry(self, guild_id: int, description: str):
        """Add a log entry to the queue if logging is enabled."""
        if await self.is_logging_enabled(guild_id):
            self.log_queue[guild_id].append({
                'description': description,
                'timestamp': discord.utils.utcnow()
            })

            if len(self.log_queue[guild_id]) >= self.max_queue_size:
                await self.process_logs_for_guild(guild_id)

    @tasks.loop(seconds=15)
    async def process_log_queue(self):
        """Process all log queues at regular intervals."""
        try:
            guilds_to_process = list(self.log_queue.keys())
            for guild_id in guilds_to_process:
                if self.log_queue[guild_id]:  
                    await self.process_logs_for_guild(guild_id)
        except Exception as e:
            logger.error(f"Error processing log queue: {e}")

    async def process_logs_for_guild(self, guild_id: int):
        """Process and send log entries for a specific guild."""
        if not self.log_queue[guild_id]:
            return

        settings = await self.get_guild_settings(guild_id)
        if not settings or not settings.get('enabled') or not settings.get('channel_id'):
            self.log_queue[guild_id] = []  
            return

        channel = self.bot.get_channel(settings.get('channel_id'))
        if not channel:
            self.log_queue[guild_id] = []  
            return

        logs = self.log_queue[guild_id][:25]  
        self.log_queue[guild_id] = self.log_queue[guild_id][25:]  

        if len(logs) == 1:

            log = logs[0]
            embed = discord.Embed(
                description=log['description'][:4096],
                color=discord.Color.blurple(),
                timestamp=log['timestamp']
            )
            embed.set_author(name="Watchdog Log")

            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                logger.error(f"Failed to send watchdog log to channel {channel.id}: {e}")

        elif logs:

            combined_embed = discord.Embed(
                title="Watchdog Logs",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )

            for i, log in enumerate(logs[:10]):  

                desc = log['description']
                if len(desc) > 1024:
                    desc = desc[:1021] + "..."

                combined_embed.add_field(
                    name=f"Log Entry {i+1} - {log['timestamp'].strftime('%H:%M:%S')}",
                    value=desc,
                    inline=False
                )

            try:
                await channel.send(embed=combined_embed)

                if len(logs) > 10:
                    for i in range(1, (len(logs) - 1) // 10 + 1):
                        next_embed = discord.Embed(
                            title=f"Watchdog Logs (Continued {i})",
                            color=discord.Color.blurple(),
                            timestamp=discord.utils.utcnow()
                        )

                        for j, log in enumerate(logs[i*10:min((i+1)*10, len(logs))]):
                            desc = log['description']
                            if len(desc) > 1024:
                                desc = desc[:1021] + "..."

                            next_embed.add_field(
                                name=f"Log Entry {i*10+j+1} - {log['timestamp'].strftime('%H:%M:%S')}",
                                value=desc,
                                inline=False
                            )

                        await channel.send(embed=next_embed)
                        await asyncio.sleep(1)  

            except discord.HTTPException as e:
                logger.error(f"Failed to send watchdog logs to channel {channel.id}: {e}")

    @process_log_queue.before_loop
    async def before_process_log_queue(self):
        await self.bot.wait_until_ready()

    @commands.group(name='watchdog', invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def watchdog_cmd(self, ctx: Context):
        """Main command group for managing Watchdog logging."""
        embed = discord.Embed(
            title='Watchdog Commands',
            description='Available subcommands: `setchannel`, `toggle`, `status`\nUse each subcommand to manage logging settings.',
            color=discord.Color.green()
        )
        await ctx.reply(embed=embed)

    @watchdog_cmd.command(name='setchannel')
    @commands.has_permissions(administrator=True)
    async def set_channel(self, ctx: Context, channel: discord.TextChannel):
        """Set the logging channel for this server."""
        guild_id = ctx.guild.id
        await self.bot.database.set_watchdog_channel(guild_id, channel.id)

        if guild_id in self.guild_settings_cache:
            self.guild_settings_cache[guild_id]['channel_id'] = channel.id

        await ctx.send(f'Log channel has been set to {channel.mention}.')

    @watchdog_cmd.command(name='toggle')
    @commands.has_permissions(administrator=True)
    async def toggle_listener(self, ctx: Context):
        """Enable or disable logging for this server."""
        guild_id = ctx.guild.id
        settings = await self.bot.database.get_server_settings(guild_id)

        if not settings:
            return await ctx.send("Server settings not found. Please setup a log channel first.")

        enabled = not settings.watchdog_enabled
        await self.bot.database.set_watchdog_enabled(guild_id, enabled)

        if guild_id in self.guild_settings_cache:
            self.guild_settings_cache[guild_id]['enabled'] = enabled

        status = "enabled" if enabled else "disabled"
        await ctx.send(f"Watchdog logging has been {status}.")

    @watchdog_cmd.command(name='status')
    @commands.has_permissions(administrator=True)
    async def status(self, ctx: Context):
        """Displays the current logging settings for the server."""
        guild_id = ctx.guild.id
        settings = await self.bot.database.get_server_settings(guild_id)

        if not settings:
            return await ctx.send("Watchdog has not been configured for this server.")

        status_text = "enabled" if settings.watchdog_enabled else "disabled"
        channel_text = f"<#{settings.watchdog_channel_id}>" if settings.watchdog_channel_id else "Not set"

        embed = discord.Embed(
            title="Watchdog Status",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Log Channel", value=channel_text, inline=True)

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self.add_log_entry(guild.id, f'{user} was banned from the server.')

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self.add_log_entry(guild.id, f'{user} was unbanned from the server.')

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.bot:
            return

        if before.name != after.name:

            await self.bot.database.log_name_change(
                user_id=after.id,
                old_name=before.name,
                new_name=after.name,
                change_type="username"
            )

            for guild in self.bot.guilds:
                if member := guild.get_member(after.id):
                    await self.add_log_entry(
                        guild.id, 
                        f"User {after.name} (ID: {after.id}) changed username from '{before.name}' to '{after.name}'."
                    )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        guild_id = after.guild.id
        changes = []

        if before.roles != after.roles:
            added_roles = [role.mention for role in after.roles if role not in before.roles]
            removed_roles = [role.mention for role in before.roles if role not in after.roles]

            if added_roles:
                changes.append(f"**Roles Added**: {', '.join(added_roles)}")

            if removed_roles:
                changes.append(f"**Roles Removed**: {', '.join(removed_roles)}")

        if before.nick != after.nick:
            old_name = before.nick if before.nick else before.name
            new_name = after.nick if after.nick else after.name

            await self.bot.database.log_name_change(
                user_id=after.id,
                old_name=old_name,
                new_name=new_name,
                change_type="nickname"
            )

            changes.append(f"**Nickname Change**: '{old_name}' → '{new_name}'")

        if changes:
            description = f"{after.display_name} (`{after.id}`) profile updated.\n" + "\n".join(changes)
            await self.add_log_entry(guild_id, description)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or not member.guild:
            return

        changes = []

        if before.channel != after.channel:
            if before.channel and after.channel:
                changes.append(f"**Moved Channels**: {before.channel.mention} → {after.channel.mention}")
            elif after.channel:
                changes.append(f"**Joined Channel**: {after.channel.mention}")
            elif before.channel:
                changes.append(f"**Left Channel**: {before.channel.mention}")

        if before.self_mute != after.self_mute:
            mute_status = "Muted" if after.self_mute else "Unmuted"
            changes.append(f"**Self-Mute**: {mute_status}")

        if before.self_deaf != after.self_deaf:
            deaf_status = "Deafened" if after.self_deaf else "Undeafened"
            changes.append(f"**Self-Deafen**: {deaf_status}")

        if changes:
            description = f"{member.display_name} (`{member.id}`) voice state changed.\n" + "\n".join(changes)
            await self.add_log_entry(member.guild.id, description)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        if message.author.bot or not message.guild or not message.content:
            return

        content = message.content
        for pattern in self.pii_patterns:
            m = pattern.search(content)
            if m:
                snippet = m.group(0)
                desc = (
                    f"⚠️ Identifying info detected in {message.channel.mention} sent by "
                    f"{message.author} (`{message.author.id}`): `{snippet}`"
                )
                try:
                    if self.pii_filter:
                        await message.delete()
                except discord.Forbidden:
                    desc += "\n*Failed to delete the message due to insufficient permissions.*"
                await self.add_log_entry(message.guild.id, desc)
                break  

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        description = f"Message by {message.author.display_name} (`{message.author.id}`) in {message.channel.mention} was deleted."

        if message.content:
            if len(message.content) > 1000:
                description += f"\n**Content**: {message.content[:997]}..."
            else:
                description += f"\n**Content**: {message.content}"

        if message.attachments:
            attachment_count = len(message.attachments)
            if attachment_count == 1:
                description += f"\n**Attachment**: {message.attachments[0].url}"
            else:
                description += f"\n**{attachment_count} Attachments deleted**"

        await self.add_log_entry(message.guild.id, description)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not member.guild:
            return

        join_time = datetime.now(timezone.utc)
        account_age = join_time - member.created_at

        description = f"{member.display_name} (`{member.id}`) has joined the server."

        if account_age < timedelta(days=60):
            description += f"\n:warning: **New Account** - Created {account_age.days} days ago"

        description += f"\n**Creation**: {member.created_at.strftime('%Y-%m-%d')}"

        await self.add_log_entry(member.guild.id, description)

async def setup(bot: commands.Bot):
    await bot.add_cog(Watchdog(bot))