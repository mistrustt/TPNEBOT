import discord


class Embeds:
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
