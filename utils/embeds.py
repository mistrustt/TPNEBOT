import discord
from discord.ext import commands


class Embeds:
    # ------------------------------------------------------------------
    # Low-level embed factory
    # ------------------------------------------------------------------
    @staticmethod
    def embed(
        description=None,
        *,
        title=None,
        color=discord.Color.blue(),
        timestamp=None,
        footer=None,
        author=None,
        thumbnail=None,
        image=None,
        url=None,
    ) -> discord.Embed:
        """Build a `discord.Embed` with common fields in one call."""
        e = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=timestamp,
            url=url,
        )
        if author is not None:
            if isinstance(author, dict):
                e.set_author(**author)
            else:
                e.set_author(name=author)
        if footer is not None:
            if isinstance(footer, dict):
                e.set_footer(**footer)
            else:
                e.set_footer(text=footer)
        if thumbnail is not None:
            e.set_thumbnail(url=thumbnail)
        if image is not None:
            e.set_image(url=image)
        return e

    # ------------------------------------------------------------------
    # Universal send dispatcher
    # ------------------------------------------------------------------
    @staticmethod
    async def _send(
        ctx_or_channel,
        description=None,
        *,
        title=None,
        color=discord.Color.blue(),
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        """Send an embed through a Context, Interaction, or TextChannel."""
        embed = Embeds.embed(
            description=description,
            title=title,
            color=color,
            **embed_kwargs,
        )

        # Interaction
        if isinstance(ctx_or_channel, discord.Interaction):
            interaction = ctx_or_channel
            kwargs = {}
            if ephemeral:
                kwargs["ephemeral"] = True
            if interaction.response.is_done():
                return await interaction.followup.send(embed=embed, **kwargs)
            return await interaction.response.send_message(embed=embed, **kwargs)

        # Context (commands.Context / hybrid context)
        if isinstance(ctx_or_channel, commands.Context):
            ctx = ctx_or_channel
            if reply:
                try:
                    return await ctx.reply(
                        embed=embed,
                        delete_after=delete_after,
                        mention_author=mention_author,
                    )
                except discord.HTTPException:
                    # Original message likely deleted while we were busy.
                    pass
            return await ctx.send(embed=embed, delete_after=delete_after)

        # Fallback: treat as a sendable channel
        return await ctx_or_channel.send(embed=embed, delete_after=delete_after)

    @staticmethod
    async def safe_reply(
        ctx: commands.Context,
        content=None,
        *,
        embed=None,
        view=None,
        delete_after=None,
        mention_author=False,
    ):
        """Reply to a context message, falling back to a plain send on failure.

        Use this for any `ctx.reply(...)` call that includes a view or other
        payload not handled by the standard `_send` helper.
        """
        try:
            return await ctx.reply(
                content=content,
                embed=embed,
                view=view,
                delete_after=delete_after,
                mention_author=mention_author,
            )
        except discord.HTTPException:
            return await ctx.send(
                content=content,
                embed=embed,
                view=view,
                delete_after=delete_after,
            )

    # ------------------------------------------------------------------
    # New high-level helpers (context/interaction/channel agnostic)
    # ------------------------------------------------------------------
    @staticmethod
    async def error(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.red(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def warning(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.orange(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def success(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.green(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def info(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.blurple(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def blurple(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.blurple(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def gold(
        ctx_or_channel,
        description,
        *,
        title=None,
        color=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color or discord.Color.gold(),
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def custom(
        ctx_or_channel,
        description,
        *,
        color=discord.Color.blue(),
        title=None,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        """Send an embed with a caller-supplied color."""
        return await Embeds._send(
            ctx_or_channel,
            description,
            title=title,
            color=color,
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def not_found(
        ctx_or_channel,
        item,
        *,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds.error(
            ctx_or_channel,
            f"🔍 {item} not found.",
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    @staticmethod
    async def permission(
        ctx_or_channel,
        description,
        *,
        delete_after=7,
        ephemeral=False,
        reply=False,
        mention_author=False,
        **embed_kwargs,
    ):
        return await Embeds.error(
            ctx_or_channel,
            f"🚫 {description}",
            delete_after=delete_after,
            ephemeral=ephemeral,
            reply=reply,
            mention_author=mention_author,
            **embed_kwargs,
        )

    # ------------------------------------------------------------------
    # Legacy helpers (kept stable for existing callers)
    # ------------------------------------------------------------------
    @staticmethod
    async def send_embed(
        channel: discord.TextChannel,
        description,
        title=None,
        delete_after=7,
        color=discord.Color.blue(),
    ):
        embed = discord.Embed(title=title, description=description, color=color)
        return await channel.send(embed=embed, delete_after=delete_after)

    @staticmethod
    async def send_warning_embed(
        channel: discord.TextChannel, user: discord.Member, description, delete_after=7
    ):
        return await Embeds.send_embed(
            channel,
            f"⚠️ {user.mention}: {description}",
            title=None,
            delete_after=delete_after,
            color=discord.Color.yellow(),
        )

    @staticmethod
    async def send_error_embed(
        channel: discord.TextChannel, user: discord.Member, description, delete_after=7
    ):
        return await Embeds.send_embed(
            channel,
            f"❌ {user.mention}: {description}",
            title=None,
            delete_after=delete_after,
            color=discord.Color.red(),
        )

    @staticmethod
    async def send_success_embed(
        channel: discord.TextChannel, user: discord.Member, description, delete_after=7
    ):
        return await Embeds.send_embed(
            channel,
            f"✅ {user.mention}: {description}",
            title=None,
            delete_after=delete_after,
            color=discord.Color.green(),
        )

    @staticmethod
    async def send_info_embed(
        channel: discord.TextChannel,
        user: discord.Member,
        description,
        delete_after=7,
        title=None,
    ):
        return await Embeds.send_embed(
            channel,
            f"ℹ️ {user.mention}: {description}",
            title=title,
            delete_after=delete_after,
            color=discord.Color.blue(),
        )
