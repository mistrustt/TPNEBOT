import os
import discord
import aiohttp
import asyncio
import logging
from discord_games import button_games
from discord.ext import commands
from discord.ext.commands import Context
from discord.ui import View, button, Button
from utils.misc import MiscUtils

logger = logging.getLogger("discord_bot")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


class RateLimitError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Rate limited")
        self.retry_after = retry_after


class WouldYouRatherView(View):
    def __init__(self, timeout: float = 15.0):
        super().__init__(timeout=timeout)
        self.votes = {"A": 0, "B": 0}
        self.voted_users: set[int] = set()
        self.message: discord.Message | None = None

    @button(label="Option A", style=discord.ButtonStyle.primary)
    async def option_a(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in self.voted_users:
            return await interaction.response.send_message(
                "🚫 You have already voted!", ephemeral=True
            )
        self.votes["A"] += 1
        self.voted_users.add(interaction.user.id)
        await interaction.response.send_message(
            "You voted for **Option A**!", ephemeral=True
        )

    @button(label="Option B", style=discord.ButtonStyle.primary)
    async def option_b(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in self.voted_users:
            return await interaction.response.send_message(
                "🚫 You have already voted!", ephemeral=True
            )
        self.votes["B"] += 1
        self.voted_users.add(interaction.user.id)
        await interaction.response.send_message(
            "You voted for **Option B**!", ephemeral=True
        )


async def fetch_wyr_question() -> str | None:
    """
    Returns the full AI-generated WYR question as a single string,
    or raises RateLimitError, or returns None on other failures.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",  # the key
        "Content-Type": "application/json",
    }
    payload = {
        "model": "google/gemini-2.0-flash-exp:free",  # model name
        "messages": [
            {
                "role": "user",
                "content": """
                You are the “Funny WYR Bot.” Your sole task is to generate exactly one short, snarky “Would You Rather” question in the format below—no more, no less:

                A) Option A  
                B) Option B

                Requirements:
                - Maximum 30 words total.
                - Tone: edgy, semi-toxic humor (mild insults and sarcasm OK; no hate speech or slurs).
                - Funny and entertaining.

                Important:
                Never include explanations, disclaimers, metadata or any other context outside of the question format supplied.
                - Do not use the word "question" or "would you rather" in the output.
                - Do not use any emojis or special characters.
                - Ensure responses relate to each other in a way that makes sense.

                Output example:
                A) Keep reusing your high-school gym socks forever  
                B) Bathe in a pool of lukewarm soda for life
                You should not deviate from this format.
                """,
            }
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 429:
                retry = int(resp.headers.get("Retry-After", "15"))
                logger.warning(f"WYR rate-limited, retry in {retry}s")
                raise RateLimitError(retry)
            if resp.status != 200:
                logger.error(f"WYR fetch failed: HTTP {resp.status}")
                return None

            data = await resp.json()
            # grab the raw content
            try:
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.exception("Malformed WYR response")
                return None


class Games(commands.Cog, name="Games"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.utils = MiscUtils(self)

    @commands.command(name="rps")
    async def rps(self, ctx: Context[commands.Bot], player: discord.User = None):
        game = button_games.BetaRockPaperScissors(player)
        await game.start(ctx)

    @commands.command(name="tictactoe", aliases=["ttt"])
    async def tictactoe(self, ctx: Context[commands.Bot], member: discord.User):
        await ctx.defer()
        game = button_games.BetaTictactoe(cross=ctx.author, circle=member)
        await game.start(ctx)

    @commands.command(name="wordle")
    async def wordle(self, ctx: Context[commands.Bot]):
        await ctx.defer()
        game = button_games.BetaWordle()
        await game.start(ctx)

    @commands.command(name="memory")
    async def memory_game(self, ctx: Context[commands.Bot]):
        await ctx.defer()
        game = button_games.MemoryGame()
        await game.start(ctx)

    @commands.command(name="wouldyourather", aliases=["wyr"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def would_you_rather(self, ctx: commands.Context):
        # 1) Fetch

        async with ctx.typing():
            try:
                question_text = await fetch_wyr_question()
            except RateLimitError as e:
                return await ctx.send(
                    embed=discord.Embed(
                        description=f"🚫 Rate limit exceeded. Try again in **{e.retry_after}** seconds.",
                        color=discord.Color.red(),
                    )
                )

            if not question_text:
                return await ctx.send(
                    embed=discord.Embed(
                        description="⚠️ Could not fetch a question right now. Please try again later.",
                        color=discord.Color.red(),
                    )
                )

            # 2) Show question + buttons
            view = WouldYouRatherView()
            ask_embed = discord.Embed(
                title="🤔 Would You Rather…?",
                description=question_text,
                color=discord.Color.purple(),
            )
            msg = await ctx.send(embed=ask_embed, view=view)
            view.message = msg

            # 3) After 15 seconds, disable buttons & post results
            async def end_game():
                await asyncio.sleep(15)
                # disable all buttons
                for child in view.children:
                    child.disabled = True
                await msg.edit(view=view)

                # show results
                results = discord.Embed(
                    title="🏁 Results",
                    description=(
                        f"**Option A** — {view.votes['A']} vote(s)\n"
                        f"**Option B** — {view.votes['B']} vote(s)"
                    ),
                    color=discord.Color.orange(),
                )
                results.set_footer(text="Game by chaos. :)")
                await msg.reply(embed=results)

            # schedule without blocking
            ctx.bot.loop.create_task(end_game())


async def setup(bot):
    await bot.add_cog(Games(bot))
