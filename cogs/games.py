import os
import random
import discord
import aiohttp
import asyncio
import logging
from discord_games import button_games
from discord.ext import commands
from discord.ext.commands import Context
from discord.ui import View, button, Button
from utils.misc import MiscUtils
from utils.cooldown import unified_cooldown
from utils.embeds import Embeds
from utils.guardrails import check_slash_guardrails

logger = logging.getLogger("discord.client")


class RateLimitError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Rate limited")
        self.retry_after = retry_after

class Games(commands.Cog, name="Games"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.utils = MiscUtils(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await check_slash_guardrails(self, interaction)

    @commands.hybrid_command(name="rps", description="Play Rock Paper Scissors.")
    @unified_cooldown(5)
    async def rps(self, ctx: Context[commands.Bot], player: discord.User = None):
        game = button_games.BetaRockPaperScissors(player)
        await game.start(ctx)

    @commands.hybrid_command(
        name="tictactoe", aliases=["ttt"], description="Play Tic-Tac-Toe with another user."
    )
    @unified_cooldown(5)
    async def tictactoe(self, ctx: Context[commands.Bot], member: discord.User):
        await ctx.defer()
        game = button_games.BetaTictactoe(cross=ctx.author, circle=member)
        await game.start(ctx)

    @commands.hybrid_command(name="wordle", description="Play a game of Wordle.")
    @unified_cooldown(5)
    async def wordle(self, ctx: Context[commands.Bot]):
        await ctx.defer()
        game = button_games.BetaWordle()
        await game.start(ctx)

    @commands.command(name="memory", description="Play the memory matching game.")
    async def memory_game(self, ctx: Context[commands.Bot]):
        game = button_games.MemoryGame()
        await game.start(ctx)

    @commands.hybrid_command(
        name="hangman", aliases=["hm"], description="Play a game of Hangman."
    )
    @unified_cooldown(5)
    async def hangman(self, ctx: Context[commands.Bot]):
        await ctx.defer()
        game = button_games.BetaHangman()
        await game.start(ctx)

    @commands.hybrid_command(
        name="connect4", aliases=["c4"], description="Play a game of Connect 4."
    )
    @unified_cooldown(5)
    async def connect4(self, ctx: Context[commands.Bot], member: discord.User):
        await ctx.defer()
        game = button_games.BetaConnectFour(player1=ctx.author, player2=member)
        await game.start(ctx)

    @commands.hybrid_command(
        name="battleship", aliases=["bs"], description="Play a game of Battleship."
    )
    @unified_cooldown(5)
    async def battleship(self, ctx: Context[commands.Bot], member: discord.User):
        await ctx.defer()
        game = button_games.BetaBattleShip(player1=ctx.author, player2=member)
        await game.start(ctx)

    @commands.hybrid_command(
        name="coinflip", aliases=["cf"], description="Flip a coin!"
    )
    @unified_cooldown(3)
    async def coinflip(self, ctx: Context):
        """Flip a coin, not really much else to it."""
        frames = [
            "<:coin:1359823671581085847>", "🪙",
            "<:coin:1359823671581085847>", "🪙",
            "<:coin:1359823671581085847>", "🪙",
            "<:coin:1359823671581085847>", "🪙",
            "<:coin:1359823671581085847>", "🪙",
            "<:coin:1359823671581085847>", "🪙",
        ]
        embed = discord.Embed(description="Flipping...", color=discord.Color.gold())
        msg = await ctx.reply(embed=embed)

        for i, frame in enumerate(frames):
            # slow down towards the end to simulate the coin losing momentum cuz thats fkn tuff
            delay = 0.2 + (i * 0.07)
            await asyncio.sleep(delay)
            embed.description = f"{frame} Flipping..."
            await msg.edit(embed=embed)

        result = random.choice(["Heads", "Tails"])
        emoji = "<:coin:1359823671581085847>" if result == "Heads" else "🪙"
        embed.description = f"{emoji} **{result}!**"
        embed.color = discord.Color.green()
        await msg.edit(embed=embed)

async def setup(bot):
    await bot.add_cog(Games(bot))
