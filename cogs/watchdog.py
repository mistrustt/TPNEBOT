import re
import discord
import logging
import asyncio
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from discord.ext.commands import Context
from collections import defaultdict
from typing import Optional
import humanfriendly
import base64
from utils.cooldown import unified_cooldown
from utils.embeds import Embeds
from utils.guardrails import check_slash_guardrails

logger = logging.getLogger("discord.client")


class Watchdog(commands.Cog, name="Watchdog"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.log_queue = defaultdict(list)
        self.max_queue_size = 10
        self.process_interval = 10
        self.guild_settings_cache = {}
        self.voice_sessions = {}  # {(guild_id, user_id): {"channel_id": int, "joined_at": datetime}}
        self.discord_patterns = [
            # Discord gift link pattern
            re.compile(r"(https?://)?discord((app)?.com/gifts|.gifts)/[a-zA-Z0-9-]+/?"),
            # Discord invite link pattern
            re.compile(
                r"(https?://)?(www\.)?(discord\.gg|discord\.com/invite)/[a-zA-Z0-9-]+/?"
            ),
            # Add more patterns as needed
        ]
        self.pii_patterns = {
            # Street addresses pattern
            "Street Address": re.compile(
                r"\b\d{1,5}(?:\s+\w+)*\s+"
                r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|"
                r"Circle|Cir|Court|Ct|Place|Pl|Terrace|Ter|Way|Square|Sq|"
                r"Parkway|Pkwy|Highway|Hwy|Route|Rt|Alley|Plaza|Crescent|Cres|"
                r"Trail|Loop|Path|Walk|Row|Close|Grove|Heights|Hts|Ridge|"
                r"Valley|View|Mill|Creek|Park|Commons|Gardens|Estates)\b",
                re.IGNORECASE,
            ),
            # Email address pattern
            "Email Address": re.compile(
                r'^(([^<>()\[\]\\.,;:\s@"]+(\.[^<>()\[\]\\.,;:\s@"]+)*)|(".+"))@'
                r"((\[(\d{1,3}\.){3}\d{1,3}\])|(([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}))$",
                re.IGNORECASE,
            ),
            # Phone number patterns (international and US)
            "Phone Number": re.compile(
                r"\b(?:\+?\d{1,2}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
            ),
            # Discord authentication token pattern
            "Discord Token": re.compile(
                r"([a-zA-Z0-9]{24}\.[a-zA-Z0-9]{6}\.[a-zA-Z0-9_\-]{27}|mfa\.[a-zA-Z0-9_\-]{84})"
            ),
            # Add more patterns as needed
        }
        self.card_patterns = {
            # we verify relevant cards with Luhn algorithm to reduce false positives
            # --- American Express ---
            "American Express Card": re.compile(r"\b3[47][0-9]{13}\b"),
            # --- China T-Union ---
            "China T-Union Card": re.compile(r"\b31[0-9]{17}\b"),
            # --- China UnionPay ---
            "China UnionPay Card": re.compile(r"\b62[0-9]{14,17}\b"),
            # --- Diners Club ---
            "Diners Club enRoute Card": re.compile(r"\b(2014|2149)[0-9]{11}\b"),
            "Diners Club International Card": re.compile(
                r"\b3(?:0[0-5]|[689][0-9])[0-9]{11,16}\b"
            ),
            "Diners Club United States & Canada Card": re.compile(r"\b55[0-9]{14}\b"),
            # --- Discover ---
            "Discover Card": re.compile(
                r"\b(6011|65[0-9]{2}|64[4-9][0-9]|62212[6-9]|622[2-8][0-9]{2}|6229[01][0-9]|62292[0-5])[0-9]{10,13}\b"
            ),
            # --- RuPay ---
            "RuPay Card": re.compile(r"\b(60|65|81|82|508|353|356)[0-9]{10,13}\b"),
            # --- InterPayment ---
            "InterPayment Card": re.compile(r"\b636[0-9]{13,16}\b"),
            # --- JCB ---
            "JCB Card": re.compile(r"\b35(2[8-9]|[3-8][0-9])[0-9]{12,15}\b"),
            # --- Maestro ---
            "Maestro UK": re.compile(r"\b(6759|676770|676774)[0-9]{6,13}\b"),
            "Maestro": re.compile(
                r"\b(5018|5020|5038|5893|6304|6759|676[1-3])[0-9]{6,13}\b"
            ),
            # --- Dankort ---
            "Dankort Card": re.compile(r"\b5019[0-9]{12}\b"),
            # --- Visa/Dankort ---
            "Dankort (Visa) Card": re.compile(r"\b4571[0-9]{12}\b"),
            # --- Mir ---
            "Mir Card": re.compile(r"\b220[0-4][0-9]{12,15}\b"),
            # --- Mastercard ---
            "Mastercard Card": re.compile(
                r"\b(5[1-5][0-9]{14}|2(2[2-9][0-9]{12}|[3-6][0-9]{13}|7[01][0-9]{12}|720[0-9]{12}))\b"
            ),
            # --- Troy ---
            "Troy Card": re.compile(r"\b(65|9792)[0-9]{12,15}\b"),
            # --- Visa ---
            "Visa Card": re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?(?:[0-9]{3})?\b"),
            # --- UATP ---
            "UATP Card": re.compile(r"\b1[0-9]{14}\b"),
            # --- Verve ---
            "Verve Card": re.compile(
                r"\b(506099|5061[0-9]{2}|6500(0[2-9]|1[0-9]|2[0-7])|5078(6[5-9]|7[0-9]|8[0-9]|9[0-4]))[0-9]{10,13}\b"
            ),
            # --- GPN ---
            "GPN Card": re.compile(r"\b(1946|50|56|58|6[0-3])[0-9]{12,15}\b"),
        }

        self.process_log_queue.start()

    def cog_unload(self):
        self.process_log_queue.cancel()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

    def luhn(self, cn: str) -> bool:
        """
        Validate a card number using the Luhn algorithm.

        Args:
            cn (str): The card number to validate

        Returns:
            bool: True if valid, False otherwise
        """
        cn = str(cn).replace(" ", "")

        if not cn.isdigit():
            return False

        di = [int(d) for d in cn]

        for i in range(len(di) - 2, -1, -2):
            di[i] *= 2
            if di[i] > 9:
                di[i] -= 9

        t = sum(di)

        return t % 10 == 0  # https://github.com/mmcloughlin/luhn/blob/master/luhn.py

    def _get_log_style(self, event_type: str) -> dict:
        """Return visual styling metadata for a log event type."""
        styles = {
            "member_ban": {
                "color": discord.Color.red(),
                "emoji": "🚫",
                "label": "Member Banned",
                "severity": 4,
            },
            "member_unban": {
                "color": discord.Color.green(),
                "emoji": "🔓",
                "label": "Member Unbanned",
                "severity": 1,
            },
            "member_join": {
                "color": discord.Color.green(),
                "emoji": "👤",
                "label": "Member Joined",
                "severity": 1,
            },
            "member_update": {
                "color": discord.Color.blue(),
                "emoji": "🏷️",
                "label": "Member Updated",
                "severity": 1,
            },
            "username_change": {
                "color": discord.Color.blue(),
                "emoji": "🏷️",
                "label": "Username Changed",
                "severity": 1,
            },
            "voice_state": {
                "color": discord.Color.purple(),
                "emoji": "🔊",
                "label": "Voice State Changed",
                "severity": 2,
            },
            "message_delete": {
                "color": discord.Color.greyple(),
                "emoji": "📝",
                "label": "Message Deleted",
                "severity": 1,
            },
            "pii_detected": {
                "color": discord.Color.orange(),
                "emoji": "⚠️",
                "label": "PII Detected",
                "severity": 3,
            },
            "card_detected": {
                "color": discord.Color.red(),
                "emoji": "💳",
                "label": "Credit Card Detected",
                "severity": 4,
            },
            "discord_token": {
                "color": discord.Color.red(),
                "emoji": "🔑",
                "label": "Discord Token Detected",
                "severity": 4,
            },
        }
        return styles.get(
            event_type,
            {
                "color": discord.Color.blurple(),
                "emoji": "🔎",
                "label": "Watchdog Log",
                "severity": 0,
            },
        )

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    async def get_guild_settings(self, guild_id: int) -> Optional[dict]:
        """Get cached guild settings or fetch from database"""
        cache = self.guild_settings_cache.get(guild_id)
        now = discord.utils.utcnow()
        if cache and (now - cache.get("last_updated", datetime.min)).total_seconds() <= 300:
            return cache

        try:
            settings = await self.bot.database.get_server_settings(guild_id)
        except Exception as exc:
            if self.bot.database._is_retryable_db_error(exc):
                logger.debug(
                    "Guild settings fetch skipped for guild %s due to DB outage: %s",
                    guild_id,
                    exc,
                )
                # Return stale cache if we have it, otherwise a safe default
                # that disables logging so we don't pile up more DB work.
                if cache:
                    cache["last_updated"] = now
                    return cache
                return {
                    "enabled": False,
                    "channel_id": None,
                    "pii_filter": True,
                    "card_filter": True,
                    "member_tracking": True,
                    "message_tracking": True,
                    "voice_tracking": True,
                    "last_updated": now,
                }
            raise

        if settings:
            self.guild_settings_cache[guild_id] = {
                "enabled": settings.watchdog_enabled,
                "channel_id": settings.watchdog_channel_id,
                "pii_filter": settings.watchdog_pii_filter,
                "card_filter": settings.watchdog_card_filter,
                "member_tracking": settings.watchdog_member_tracking,
                "message_tracking": settings.watchdog_message_tracking,
                "voice_tracking": settings.watchdog_voice_tracking,
                "last_updated": now,
            }
        else:
            self.guild_settings_cache[guild_id] = {
                "enabled": False,
                "channel_id": None,
                "pii_filter": True,
                "card_filter": True,
                "member_tracking": True,
                "message_tracking": True,
                "voice_tracking": True,
                "last_updated": now,
            }

        return self.guild_settings_cache.get(guild_id)

    async def is_logging_enabled(self, guild_id: int) -> bool:
        """Check if logging is enabled in the guild using cached settings."""
        settings = await self.get_guild_settings(guild_id)
        return settings and settings.get("enabled", False)

    async def add_log_entry(
        self,
        guild_id: int,
        description: str,
        *,
        event_type: str = "log",
        avatar_url: Optional[str] = None,
    ):
        """Add a log entry to the queue if logging is enabled."""
        if await self.is_logging_enabled(guild_id):
            self.log_queue[guild_id].append(
                {
                    "description": description,
                    "timestamp": discord.utils.utcnow(),
                    "event_type": event_type,
                    "avatar_url": avatar_url,
                }
            )

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
        if (
            not settings
            or not settings.get("enabled")
            or not settings.get("channel_id")
        ):
            self.log_queue[guild_id] = []
            return

        channel = self.bot.get_channel(settings.get("channel_id"))
        if not channel:
            self.log_queue[guild_id] = []
            return

        logs = self.log_queue[guild_id][:25]
        self.log_queue[guild_id] = self.log_queue[guild_id][25:]

        guild_icon = channel.guild.icon.url if channel.guild.icon else self.bot.user.display_avatar.url

        if len(logs) == 1:
            log = logs[0]
            style = self._get_log_style(log.get("event_type", "log"))
            embed = discord.Embed(
                description=log["description"][:4096],
                color=style["color"],
                timestamp=log["timestamp"],
            )
            embed.set_author(
                name=f"{style['emoji']} {style['label']}",
                icon_url=guild_icon,
            )
            if log.get("avatar_url"):
                embed.set_thumbnail(url=log["avatar_url"])

            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                logger.error(
                    f"Failed to send watchdog log to channel {channel.id}: {e}"
                )

        elif logs:
            styles = [
                self._get_log_style(log.get("event_type", "log")) for log in logs
            ]
            batch_style = max(styles, key=lambda s: (s["severity"], s["label"]))
            total_pages = (len(logs) - 1) // 10 + 1

            combined_embed = discord.Embed(
                title=f"🔎 Watchdog Logs ({len(logs)} events)",
                color=batch_style["color"],
                timestamp=discord.utils.utcnow(),
            )
            combined_embed.set_author(name="Watchdog", icon_url=guild_icon)
            combined_embed.set_footer(text=f"Page 1/{total_pages} • {len(logs)} events")

            for i, log in enumerate(logs[:10]):
                style = styles[i]
                desc = log["description"]
                if len(desc) > 1024:
                    desc = desc[:1021] + "..."

                combined_embed.add_field(
                    name=f"{style['emoji']} {style['label']} — {log['timestamp'].strftime('%H:%M:%S')}",
                    value=desc,
                    inline=False,
                )

            try:
                await channel.send(embed=combined_embed)

                if len(logs) > 10:
                    for page_idx in range(1, total_pages):
                        next_embed = discord.Embed(
                            title=f"🔎 Watchdog Logs (Continued)",
                            color=batch_style["color"],
                            timestamp=discord.utils.utcnow(),
                        )
                        next_embed.set_author(name="Watchdog", icon_url=guild_icon)
                        next_embed.set_footer(
                            text=f"Page {page_idx + 1}/{total_pages} • {len(logs)} events"
                        )

                        start = page_idx * 10
                        end = min((page_idx + 1) * 10, len(logs))
                        for j, log in enumerate(logs[start:end]):
                            style = styles[start + j]
                            desc = log["description"]
                            if len(desc) > 1024:
                                desc = desc[:1021] + "..."

                            next_embed.add_field(
                                name=f"{style['emoji']} {style['label']} — {log['timestamp'].strftime('%H:%M:%S')}",
                                value=desc,
                                inline=False,
                            )

                        await channel.send(embed=next_embed)
                        await asyncio.sleep(1)

            except discord.HTTPException as e:
                logger.error(
                    f"Failed to send watchdog logs to channel {channel.id}: {e}"
                )

    @process_log_queue.before_loop
    async def before_process_log_queue(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_group(
        name="watchdog", aliases=["modlog", "ml"], invoke_without_command=True,
        description="Main command group for managing Watchdog logging."
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def watchdog_cmd(self, ctx: Context):
        """Main command group for managing Watchdog logging."""
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
                lines.append(f"`{prefix}watchdog {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}watchdog {name}`{aliases}")

        if not lines:
            description = "No subcommands available."
        else:
            description = "\n".join(lines)

        embed = discord.Embed(
            title="🔎 Watchdog — Available Commands",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Use {prefix}watchdog <subcommand> for details.")

        await ctx.reply(embed=embed, mention_author=False)

    @watchdog_cmd.command(name="channel", aliases=["c"], description="Set the logging channel for this server.")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def set_channel(self, ctx: Context, channel: discord.TextChannel):
        """Set the logging channel for this server."""
        guild_id = ctx.guild.id
        await self.bot.database.set_watchdog_channel(guild_id, channel.id)

        if guild_id in self.guild_settings_cache:
            self.guild_settings_cache[guild_id]["channel_id"] = channel.id

        await ctx.send(f"Log channel has been set to {channel.mention}.")

    @watchdog_cmd.command(name="toggle", aliases=["t"], description="Enable or disable logging for this server or a specific feature.")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def toggle_listener(self, ctx: Context, feature: str = None):
        """Enable or disable logging for this server or a specific feature.
        
        Features:
        - pii_filter: PII (Personal Identifiable Information) detection
        - card_filter: Credit card detection
        - member_tracking: Member join/leave, role changes, nickname changes
        - message_tracking: Message deletions
        - voice_tracking: Voice state changes
        
        Usage:
        !watchdog toggle - Toggle entire watchdog on/off
        !watchdog toggle pii_filter - Toggle PII filter only
        """
        guild_id = ctx.guild.id
        settings = await self.bot.database.get_server_settings(guild_id)

        if not settings:
            return await ctx.send(
                "Server settings not found. Please setup a log channel first."
            )

        valid_features = ["pii_filter", "card_filter", "member_tracking", "message_tracking", "voice_tracking"]
        
        if feature:
            # Toggle individual feature
            if feature not in valid_features:
                return await ctx.send(
                    f"Invalid feature. Valid features are: {', '.join(valid_features)}"
                )
            
            current_value = getattr(settings, f"watchdog_{feature}", True)
            new_value = not current_value
            await self.bot.database.set_watchdog_feature(guild_id, feature, new_value)
            
            # Update cache
            if guild_id in self.guild_settings_cache:
                self.guild_settings_cache[guild_id][feature] = new_value
            
            status = "enabled" if new_value else "disabled"
            feature_name = feature.replace("_", " ").title()
            await ctx.send(f"Watchdog {feature_name} has been {status}.")
        else:
            # Toggle entire watchdog
            enabled = not settings.watchdog_enabled
            await self.bot.database.set_watchdog_enabled(guild_id, enabled)

            if guild_id in self.guild_settings_cache:
                self.guild_settings_cache[guild_id]["enabled"] = enabled

            status = "enabled" if enabled else "disabled"
            await ctx.send(f"Watchdog logging has been {status}.")

    @watchdog_cmd.command(name="status", description="Displays the current logging settings for the server.")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.default_permissions(administrator=True)
    @unified_cooldown(10)
    async def status(self, ctx: Context):
        """Displays the current logging settings for the server."""
        guild_id = ctx.guild.id
        settings = await self.bot.database.get_server_settings(guild_id)

        if not settings:
            return await ctx.send("Watchdog has not been configured for this server.")

        status_text = "✅ Enabled" if settings.watchdog_enabled else "❌ Disabled"
        channel_text = (
            f"<#{settings.watchdog_channel_id}>"
            if settings.watchdog_channel_id
            else "Not set"
        )

        color = (
            discord.Color.green()
            if settings.watchdog_enabled
            else discord.Color.red()
        )

        embed = discord.Embed(
            title="🔎 Watchdog Status",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Overall Status", value=status_text, inline=True)
        embed.add_field(name="Log Channel", value=channel_text, inline=True)

        # Add individual feature statuses
        features = [
            ("PII Filter", settings.watchdog_pii_filter),
            ("Card Filter", settings.watchdog_card_filter),
            ("Member Tracking", settings.watchdog_member_tracking),
            ("Message Tracking", settings.watchdog_message_tracking),
            ("Voice Tracking", settings.watchdog_voice_tracking),
        ]

        feature_status = "\n".join(
            f"{'✅' if enabled else '❌'} {name}"
            for name, enabled in features
        )

        embed.add_field(name="Features", value=feature_status, inline=True)
        embed.set_footer(text=f"Guild ID: {guild_id}")

        await ctx.send(embed=embed)

        @commands.Cog.listener()
        async def on_guild_join(self, guild: discord.Guild):
            """Leave servers with less than 10 members (excluding bots)."""
            human_members = [member for member in guild.members if not member.bot]
            
            if len(human_members) < 10:
                logger.info(
                    f"Left guild '{guild.name}' ({guild.id}) - Only {len(human_members)} human members (minimum required: 10)"
                )
                
                # Try to send a message to the system channel or owner
                try:
                    message = (
                        f"👋 Hello! I'm leaving this server because it doesn't meet the minimum member requirement.\n\n"
                        f"**Reason**: This server has only {len(human_members)} human member(s), but I require at least 10 human members to operate.\n\n"
                        f"If you'd like to invite me back in the future, please make sure your server has at least 10 human members. Thank you for understanding!"
                    )
                    
                    # Try to send to system channel first
                    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                        await guild.system_channel.send(message)
                    # Otherwise try to DM the owner
                    elif guild.owner:
                        await guild.owner.send(message)
                        
                except discord.HTTPException as e:
                    logger.warning(f"Could not notify guild {guild.id} about leaving: {e}")
                
                # Leave the guild
                try:
                    await guild.leave()
                except discord.HTTPException as e:
                    logger.error(f"Failed to leave guild {guild.id}: {e}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        settings = await self.get_guild_settings(guild.id)
        if settings and settings.get("member_tracking", True):
            await self.add_log_entry(
                guild.id,
                f"{user} was banned from the server.",
                event_type="member_ban",
                avatar_url=user.display_avatar.url,
            )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        settings = await self.get_guild_settings(guild.id)
        if settings and settings.get("member_tracking", True):
            await self.add_log_entry(
                guild.id,
                f"{user} was unbanned from the server.",
                event_type="member_unban",
                avatar_url=user.display_avatar.url,
            )

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.bot:
            return

        if before.name != after.name:
            await self.bot.database.log_name_change(
                user_id=after.id,
                old_name=before.name,
                new_name=after.name,
                change_type="username",
            )

            for guild in self.bot.guilds:
                if member := guild.get_member(after.id):
                    settings = await self.get_guild_settings(guild.id)
                    if settings and settings.get("member_tracking", True):
                        await self.add_log_entry(
                            guild.id,
                            f"User {after.name} (ID: {after.id}) changed username from '{before.name}' to '{after.name}'.",
                            event_type="username_change",
                            avatar_url=member.display_avatar.url,
                        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot or not after.guild:
            return

        guild_id = after.guild.id
        settings = await self.get_guild_settings(guild_id)
        if not settings or not settings.get("member_tracking", True):
            return

        changes = []

        if before.roles != after.roles:
            added_roles = [
                role.mention for role in after.roles if role not in before.roles
            ]
            removed_roles = [
                role.mention for role in before.roles if role not in after.roles
            ]

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
                change_type="nickname",
            )

            changes.append(f"**Nickname Change**: '{old_name}' → '{new_name}'")

        if changes:
            description = (
                f"{after.display_name} (`{after.id}`) profile updated.\n"
                + "\n".join(changes)
            )
            await self.add_log_entry(
                guild_id,
                description,
                event_type="member_update",
                avatar_url=after.display_avatar.url,
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or not member.guild:
            return

        settings = await self.get_guild_settings(member.guild.id)
        if not settings or not settings.get("voice_tracking", True):
            return

        session_key = (member.guild.id, member.id)
        changes = []

        if before.channel != after.channel:
            if before.channel and after.channel:
                # User moved between channels - end old session and start new one
                if session_key in self.voice_sessions:
                    joined_at = self.voice_sessions[session_key]["joined_at"]
                    duration = discord.utils.utcnow() - joined_at
                    duration_str = humanfriendly.format_timespan(duration.total_seconds())
                    changes.append(
                        f"**Left Channel**: {before.channel.mention} (Duration: {duration_str})"
                    )
                
                changes.append(f"**Joined Channel**: {after.channel.mention}")
                
                # Start new session
                self.voice_sessions[session_key] = {
                    "channel_id": after.channel.id,
                    "joined_at": discord.utils.utcnow()
                }
            elif after.channel:
                # User joined a channel
                changes.append(f"**Joined Channel**: {after.channel.mention}")
                
                # Start tracking session
                self.voice_sessions[session_key] = {
                    "channel_id": after.channel.id,
                    "joined_at": discord.utils.utcnow()
                }
            elif before.channel:
                # User left a channel
                duration_str = None
                if session_key in self.voice_sessions:
                    joined_at = self.voice_sessions[session_key]["joined_at"]
                    duration = discord.utils.utcnow() - joined_at
                    duration_str = humanfriendly.format_timespan(duration.total_seconds())
                    del self.voice_sessions[session_key]
                
                if duration_str:
                    changes.append(f"**Left Channel**: {before.channel.mention} (Duration: {duration_str})")
                else:
                    changes.append(f"**Left Channel**: {before.channel.mention}")

        if before.self_mute != after.self_mute:
            mute_status = "Muted" if after.self_mute else "Unmuted"
            changes.append(f"**Self-Mute**: {mute_status}")

        if before.self_deaf != after.self_deaf:
            deaf_status = "Deafened" if after.self_deaf else "Undeafened"
            changes.append(f"**Self-Deafen**: {deaf_status}")

        if changes:
            description = (
                f"{member.display_name} (`{member.id}`) voice state changed.\n"
                + "\n".join(changes)
            )
            await self.add_log_entry(
                member.guild.id,
                description,
                event_type="voice_state",
                avatar_url=member.display_avatar.url,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return

        settings = await self.get_guild_settings(message.guild.id)
        if not settings:
            return

        content = message.content

        # Check PII filter
        if settings.get("pii_filter", True):
            for pattern_name, pattern in self.pii_patterns.items():
                m = pattern.search(content)
                if m:
                    snippet = m.group(0)
                    event_type = (
                        "discord_token"
                        if pattern_name == "Discord Token"
                        else "pii_detected"
                    )
                    desc = (
                        f"{pattern_name} detected in {message.channel.mention} sent by "
                        f"{message.author} (`{message.author.id}`).\n"
                        f"**Matched snippet**:\n```\n{snippet}\n```"
                    )

                    if pattern_name == "Discord Token":
                        try:
                            token_parts = snippet.split(".")
                            if len(token_parts) >= 1:
                                user_id = base64.b64decode(token_parts[0] + "==").decode(
                                    "utf-8"
                                )
                                desc += f"\n**Token User ID**: {user_id}"
                        except:
                            pass

                    try:
                        await message.delete()
                    except discord.Forbidden:
                        desc += "\n*Failed to delete the message due to insufficient permissions.*"

                    await self.add_log_entry(
                        message.guild.id,
                        desc,
                        event_type=event_type,
                        avatar_url=message.author.display_avatar.url,
                    )
                    return

        # Check card filter
        if settings.get("card_filter", True):
            NON_LUHN = {"Diners Club enRoute Card"}

            for pattern_name, pattern in self.card_patterns.items():
                for m in pattern.finditer(content):
                    card_number = re.sub(r"\D", "", m.group(0))

                    if pattern_name not in NON_LUHN and not self.luhn(card_number):
                        continue

                    desc = (
                        f"Genuine {pattern_name} detected in {message.channel.mention} sent by "
                        f"{message.author} (`{message.author.id}`)"
                    )
                    try:
                        await message.delete()
                    except discord.Forbidden:
                        desc += "\n*Failed to delete the message due to insufficient permissions.*"

                    await self.add_log_entry(
                        message.guild.id,
                        desc,
                        event_type="card_detected",
                        avatar_url=message.author.display_avatar.url,
                    )
                    return

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        settings = await self.get_guild_settings(message.guild.id)
        if not settings or not settings.get("message_tracking", True):
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

        await self.add_log_entry(
            message.guild.id,
            description,
            event_type="message_delete",
            avatar_url=message.author.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not member.guild:
            return

        settings = await self.get_guild_settings(member.guild.id)
        if not settings or not settings.get("member_tracking", True):
            return

        join_time = discord.utils.utcnow()
        account_age = join_time - member.created_at

        description = f"{member.display_name} (`{member.id}`) has joined the server."

        age_str = humanfriendly.format_timespan(account_age.total_seconds())

        if account_age < timedelta(days=60):
            description += f"\n:warning: **New Account** - Created {age_str} ago"

        description += (
            f"\n**Creation**: {member.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        await self.add_log_entry(
            member.guild.id,
            description,
            event_type="member_join",
            avatar_url=member.display_avatar.url,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Watchdog(bot))
