import discord
from datetime import timedelta


async def check_slash_guardrails(cog, interaction: discord.Interaction) -> bool:
    """
    Enforce the same guardrails used for prefix commands (blacklist, DM block,
    account age, command status, role restrictions) on slash/app invocations.

    View callbacks are left to their own ``interaction_check``.
    """
    if interaction.type != discord.InteractionType.application_command:
        return True

    bot = cog.bot
    user = interaction.user
    if getattr(bot, "owner_ids", None) and user.id in bot.owner_ids:
        return True

    try:
        if not interaction.response.is_done():
            if await bot.database.is_user_blacklisted(user.id):
                await interaction.response.send_message(
                    "You are blacklisted from using this bot.", ephemeral=True
                )
                return False
    except Exception:
        pass

    if interaction.guild is None:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Commands can only be used in a server.", ephemeral=True
                )
        except Exception:
            pass
        return False

    account_age = discord.utils.utcnow() - user.created_at
    if account_age < timedelta(days=30):
        days_remaining = 30 - account_age.days
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Your account must be at least 30 days old to use commands. "
                    f"Please wait {days_remaining} more day{'s' if days_remaining != 1 else ''}.",
                    ephemeral=True,
                )
        except Exception:
            pass
        return False

    command = interaction.command
    command_name = (
        getattr(command, "qualified_name", None)
        or getattr(command, "name", None)
        or "unknown"
    )

    try:
        channel_id = interaction.channel_id
        enabled = await bot.database.get_command_status(command_name, channel_id)
        if enabled is False:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"The `/{command_name}` command is disabled in this channel by staff.",
                    ephemeral=True,
                )
            return False
        enabled_global = await bot.database.get_command_status(command_name)
        if enabled_global is False:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"The `/{command_name}` command is currently disabled for maintenance.",
                    ephemeral=True,
                )
            return False
    except Exception:
        pass

    try:
        if interaction.guild and getattr(interaction.user, "roles", None):
            command_names = [command_name]
            aliases = getattr(command, "aliases", None) or []
            command_names.extend(a.lower() for a in aliases)
            has_permission = True
            for cmd_name in command_names:
                if not await bot.database.check_command_role_restriction(
                    interaction.guild.id, cmd_name, interaction.user.roles
                ):
                    has_permission = False
                    break
            if not has_permission:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"You don't have the required role to use `/{command_name}`.",
                        ephemeral=True,
                    )
                return False
    except Exception:
        pass

    return True
