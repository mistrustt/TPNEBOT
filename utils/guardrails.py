import discord


async def check_slash_guardrails(cog, interaction: discord.Interaction) -> bool:
    """
    Delegate to the bot's centralized slash guardrails check.

    A global tree check enforces these checks for every application command
    before it is invoked. Cog ``interaction_check`` methods continue to call
    this helper as a defensive fallback; duplicate work is avoided by caching
    the result per interaction in the bot.
    """
    if interaction.type != discord.InteractionType.application_command:
        return True

    bot = cog.bot
    check = getattr(bot, "_check_slash_guardrails", None)
    if check is None:
        return True
    return await check(interaction)
