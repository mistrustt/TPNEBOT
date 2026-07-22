import discord
import logging
import datetime
from discord.ext import commands
from discord.ext.commands import Context
from utils.misc import MiscUtils
from utils.embeds import Embeds
from utils.cooldown import unified_cooldown
from utils.guardrails import check_slash_guardrails

logger = logging.getLogger("discord.client")


class Community(commands.Cog, name="Community"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.utils = MiscUtils(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listens for messages in specific channels and deletes them if they are not exactly '999'."""
        if message.guild is None:
            return

        try:
            channel_id = await self.bot.database.get_spam_channel(message.guild.id)
        except Exception as exc:
            if self.bot.database._is_retryable_db_error(exc):
                logger.debug(
                    "Spam-channel check skipped for guild %s due to DB outage: %s",
                    message.guild.id,
                    exc,
                )
                return
            raise

        if not channel_id:
            return

        if message.channel.id == channel_id:
            if message.content.strip() != "999":
                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.error(
                        "I don't have permission to delete messages in this channel."
                    )
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")
        if not hasattr(self, "spam_channel_task"):
            self.spam_channel_task = self.bot.loop.create_task(
                self.update_spam_channel_description()
            )

    async def update_spam_channel_description(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild in self.bot.guilds:
                channel_id = await self.bot.database.get_spam_channel(guild.id)
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel and isinstance(channel, discord.TextChannel):
                        try:
                            count = 0
                            async for _ in channel.history(limit=None):
                                count += 1
                            new_topic = f"Message count: {count}"
                            if channel.topic != new_topic:
                                await channel.edit(topic=new_topic)
                        except Exception as e:
                            logger.error(
                                f"Failed to update channel topic for {channel.name}: {e}"
                            )
            await discord.utils.sleep_until(
                discord.utils.utcnow() + datetime.timedelta(minutes=10)
            )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None:
            return

        try:
            spam_channel_id = await self.bot.database.get_spam_channel(after.guild.id)
        except Exception as exc:
            if self.bot.database._is_retryable_db_error(exc):
                logger.debug(
                    "Spam-channel edit check skipped for guild %s due to DB outage: %s",
                    after.guild.id,
                    exc,
                )
                return
            raise
        if not spam_channel_id:
            return

        if after.author == self.bot.user:
            return

        if after.channel.id == spam_channel_id:
            if after.content.strip() != "999":
                try:
                    await after.delete()
                    logger.info(
                        f"Deleted edited message from {after.author} in {after.channel.name}"
                    )
                except discord.Forbidden:
                    logger.error("Error: missing message-delete permission.")
                except discord.HTTPException as e:
                    logger.error(f"HTTPException deleting message: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error deleting message: {e}")

    @commands.hybrid_group(
        name="grail",
        aliases=["grails"],
        description="Command to view favourite songs",
        invoke_without_command=True,
    )
    @unified_cooldown(10)
    async def grail(self, ctx: Context, member: discord.Member = None):
        await ctx.defer()
        member = member or ctx.author

        favorite_songs = await self.bot.database.get_favorite_songs(member.id)

        if not favorite_songs:
            await ctx.send("You have no grails yet.")
            return

        grails = [f"{song.song_title.title()}" for song in favorite_songs]
        pages = [grails[i : i + 10] for i in range(0, len(grails), 10)]

        class GrailMenu(discord.ui.View):
            def __init__(self, pages, member, utils):
                super().__init__(timeout=60)
                self.pages = pages
                self.current_page = 0
                self.member = member
                self.utils = utils

            async def update_embed(self, interaction):
                embed = discord.Embed(
                    description="\n".join(self.pages[self.current_page]),
                    color=discord.Color.blurple(),
                )
                embed.set_author(
                    name=f"{self.member.display_name}'s Grail List",
                    icon_url=self.utils.get_avatar_url(self.member),
                )
                embed.set_footer(text=f"Page {self.current_page + 1}/{len(self.pages)}")
                await interaction.response.edit_message(embed=embed, view=self)

            @discord.ui.button(label="⬅", style=discord.ButtonStyle.secondary, emoji="⬅️")
            async def previous_page(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if self.current_page > 0:
                    self.current_page -= 1
                    await self.update_embed(interaction)

            @discord.ui.button(label="➡", style=discord.ButtonStyle.secondary, emoji="➡️")
            async def next_page(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if self.current_page < len(self.pages) - 1:
                    self.current_page += 1
                    await self.update_embed(interaction)

        view = GrailMenu(pages, member, self.utils)
        embed = discord.Embed(
            description="\n".join(pages[0]), color=discord.Color.blurple()
        )
        embed.set_author(
            name=f"{member.display_name}'s Grail List",
            icon_url=self.utils.get_avatar_url(member),
        )
        embed.set_footer(text=f"Page 1/{len(pages)}")
        await ctx.send(embed=embed, view=view)

    @grail.command(name="add")
    @unified_cooldown(10)
    async def grail_add(self, ctx: Context, *, song_title: str):
        """Add a favorite song."""
        await ctx.defer(ephemeral=True)

        if len(song_title) > 64:
            await ctx.send("Song title cannot be longer than 64 characters.")
            return

        await self.bot.database.add_favorite_song(ctx.author.id, song_title)
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        await Embeds.custom(
            ctx,
            f"Added **{song_title}** to your grail list.",
            color=color,
            delete_after=None,
        )

    @grail.command(name="remove")
    @unified_cooldown(10)
    async def grail_remove(self, ctx: Context, *, song_title: str):
        """Remove a favorite song by title."""
        await ctx.defer(ephemeral=True)

        favorite_songs = await self.bot.database.get_favorite_songs(ctx.author.id)
        song_title_lower = song_title.lower()
        song_to_remove = next(
            (
                song
                for song in favorite_songs
                if song.song_title.lower() == song_title_lower
            ),
            None,
        )
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        if song_to_remove:
            await self.bot.database.remove_favorite_song(
                ctx.author.id, song_to_remove.song_title
            )
            await Embeds.custom(
                ctx,
                f"Removed **{song_title}** from your grail list.",
                color=color,
                delete_after=None,
            )
        else:
            await Embeds.error(
                ctx,
                f"Could not find **{song_title}** in your grail list.",
                delete_after=None,
            )

    @grail.command(name="list")
    @unified_cooldown(10)
    async def grail_list(self, ctx: commands.Context, member: discord.Member = None):
        """View your favorite songs."""
        await ctx.defer(ephemeral=True)

        member = member or ctx.author

        favorite_songs = await self.bot.database.get_favorite_songs(member.id)

        if not favorite_songs:
            await ctx.send("You have no grails yet.")
            return

        grails = "\n".join([f"{song.song_title.title()}" for song in favorite_songs])

        embed = discord.Embed(description=grails, color=discord.Color.blurple())
        embed.set_author(
            name=f"{member.display_name}'s Grail List",
            icon_url=self.utils.get_avatar_url(member),
        )

        await ctx.send(embed=embed)

    @grail.command(name="clear", description="Clear your favourite songs.")
    @unified_cooldown(10)
    async def clear_favorite(self, ctx: Context):
        """Clear your favourite songs."""
        await ctx.defer(ephemeral=True)

        await self.bot.database.clear_favorite_songs(ctx.author.id)
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        await Embeds.custom(
            ctx,
            f"Removed **all songs** from your grail list.",
            color=color,
            delete_after=None,
        )

    @commands.hybrid_command(
        name="setspamchannel",
        aliases=["setspam"],
        description="Set the spam channel for the current guild",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    @discord.app_commands.default_permissions(manage_channels=True)
    @unified_cooldown(10)
    async def set_spam_channel(self, ctx, channel: discord.TextChannel):
        """Command to set the spam channel for the current guild."""
        if channel is None:
            await ctx.send("Please specify a channel.")
            return

        await self.bot.database.set_spam_channel(ctx.guild.id, channel.id)
        await Embeds.success(
            ctx,
            f"Spam channel set to: {channel.mention}",
            title="Spam Channel Set",
            reply=True,
            delete_after=None,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Community(bot))
