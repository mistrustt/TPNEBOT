import re
import json
import discord
import logging
import asyncio
import datetime
import secrets
import functools
import hmac, hashlib
from itertools import combinations
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from discord import ui, ButtonStyle, Interaction
from discord.ui import View, Button
from discord.ext import commands
from discord.ext.commands import Context
from utils.misc import MiscUtils
from collections import defaultdict
from decimal import Decimal
from typing import Sequence, List, Any, Optional
from utils.fairness import ProvenFairness
from textwrap import shorten

logger = logging.getLogger("discord_bot")

class CrashView(discord.ui.View):
    def __init__(self, bot: commands.Bot, host_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.host_id = host_id
        self.channel_id = channel_id

        self.game_task: asyncio.Task | None = None
        self.is_running = False
        self.start_time: datetime.datetime = None
        self.countdown_end: int = None  
        self.game_phase: str = None      
        self.current_multiplier = Decimal('1.0')

        self.players: dict[int, Decimal]      = {}  
        self.crash_points: dict[int, Decimal] = {}
        self.cashed_out: dict[int, Decimal]  = {}
        self.crashed_out: dict[int, Decimal] = {}

        self.join_btn = discord.ui.Button(label="Join Crash", style=discord.ButtonStyle.green)
        self.join_btn.callback = self.join_callback
        self.add_item(self.join_btn)

        self.cashout_btn = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.red, disabled=True)
        self.cashout_btn.callback = self.cashout_callback
        self.add_item(self.cashout_btn)

        self.game_message: discord.Message = None

    async def join_callback(self, interaction: Interaction):
        """Show the bet modal when someone clicks Join."""

        now = discord.utils.utcnow()
        if self.game_phase != "starting" or now.timestamp() >= self.countdown_end:
            return await interaction.response.send_message(
                "Too late to join!", ephemeral=True
            )

        uid = interaction.user.id
        if uid in self.players:
            return await interaction.response.send_message(
                "You've already joined!", ephemeral=True
            )

        casino: Casino = self.bot.get_cog("Casino")
        max_allowed = await self.bot.database.get_max_gamble_amount(uid, False)
        formatted_max = await casino.short_formatter(max_allowed)

        modal = discord.ui.Modal(title="Join Crash Game")
        amount_input = discord.ui.TextInput(
            label="Bet Amount",
            placeholder=f"e.g. 40m, half, all  (max {formatted_max})",
            required=True
        )
        modal.add_item(amount_input)

        async def on_submit(sub_int: discord.Interaction):

            wallet = await self.bot.database.get_wallet_id_for_user(uid)
            balance = await self.bot.database.get_wallet_balance(wallet)

            try:
                bet = await casino.amount_handler(amount_input.value, balance)
            except ValueError as e:
                return await sub_int.response.send_message(
                    f"Invalid bet: {e}", ephemeral=True
                )

            if bet <= 0:
                return await sub_int.response.send_message(
                    "Bet must be positive.", ephemeral=True
                )
            if bet > balance:
                return await sub_int.response.send_message(
                    "Insufficient funds.", ephemeral=True
                )
            if bet > max_allowed:
                bet = max_allowed  
                await sub_int.response.send_message(
                    f"Bet capped to max {formatted_max}.", ephemeral=True
                )

            await self.bot.database.process_treasury_transaction(
                wallet, -bet, "Crash Game Bet"
            )

            self.players[uid] = bet
            self.crash_points[uid] = await self.generate_crash_point(uid)

            self.cashout_btn.disabled = False
            await sub_int.followup.send(
                f"You joined with **{await casino.formatter(bet)}** {casino.currency_name}", ephemeral=True
            )
            await self.update_game_message()

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def cashout_callback(self, interaction: discord.Interaction):
        """Cash out for the clicking user."""
        uid = interaction.user.id
        if uid not in self.players or uid in self.cashed_out or uid in self.crashed_out:
            return await interaction.response.send_message(
                "Cannot cash out.", ephemeral=True
            )

        bet = self.players[uid]
        mult = self.current_multiplier
        win  = (bet * mult).quantize(Decimal('0.01'))

        wallet = await self.bot.database.get_wallet_id_for_user(uid)
        await self.bot.database.process_treasury_transaction(
            wallet, win, "Crash Game Payout"
        )

        self.cashed_out[uid] = self.current_multiplier
        casino: Casino = self.bot.get_cog("Casino")
        await interaction.response.send_message(
            f"Cashed out @ {mult:.2f}× for **{await casino.formatter(win)}** {casino.currency_name}", ephemeral=True
        )
        await self.update_game_message()

    async def generate_crash_point(self, user_id: int) -> Decimal:
        """Per-user provable fairness"""
        casino: Casino = self.bot.get_cog("Casino")
        r = await casino.fair_random(user_id)
        if r < 0.55:
            v = await casino.fair_uniform(user_id, 1.0, 2.0)
        elif r < 0.80:
            v = await casino.fair_uniform(user_id, 2.0, 5.0)
        elif r < 0.95:
            v = await casino.fair_uniform(user_id, 5.0, 20.0)
        elif r < 0.992:
            v = await casino.fair_uniform(user_id, 20.0, 100.0)
        elif r < 0.998:
            v = await casino.fair_uniform(user_id, 100.0, 1000.0)
        else:
            v = await casino.fair_uniform(user_id, 1000.0, 20000.0)
        return Decimal(str(round(v, 2)))

    async def start_game(self, ctx: commands.Context):
        """Start lobby > run > finish."""
        self.is_running = True
        now = discord.utils.utcnow()
        self.start_time = now
        self.game_phase = "starting"
        self.current_multiplier = Decimal('1.0')

        self.countdown_end = int((now + datetime.timedelta(seconds=20)).timestamp())

        embed = discord.Embed(
            title="🚀 Crash – Lobby",
            description=f"Click **Join** starting <t:{self.countdown_end}:R>",
            color=discord.Color.green()
        )
        self.game_message = await ctx.send(embed=embed, view=self)

        while discord.utils.utcnow().timestamp() < self.countdown_end:
            await asyncio.sleep(2)
            await self.update_game_message()

        if not self.players:
            await self.game_message.edit(
                embed=discord.Embed(
                    title="🚀 Crash – Cancelled",
                    description="No players joined.",
                    color=discord.Color.red()
                ),
                view=None
            )
            self.is_running = False
            return

        self.game_phase = "running"
        self.join_btn.disabled = True

        self.cashout_btn.disabled = not any(
            uid not in self.cashed_out and uid not in self.crashed_out for uid in self.players
        )
        await self.update_game_message()

        while len(self.cashed_out | self.crashed_out) < len(self.players):
            self.current_multiplier += self.calculate_increment()

            for uid, cp in self.crash_points.items():
                if uid not in self.cashed_out and uid not in self.crashed_out:
                    if self.current_multiplier >= cp:
                        self.crashed_out[uid] = self.crash_points[uid]
            await self.update_game_message()
            await asyncio.sleep(1)

        self.game_phase = "ended"

        self.cashout_btn.disabled = True
        await self.game_message.edit(
            embed=await self.make_embed(), view=None
        )
        self.is_running = False

    def calculate_increment(self) -> Decimal:
        m = self.current_multiplier
        if m < 2:   return Decimal('0.1')
        if m < 4:   return Decimal('0.2')
        if m < 10:  return Decimal('0.5')
        if m < 20:  return Decimal('1.0')
        if m < 50:  return Decimal('2.0')
        if m < 100: return Decimal('4.0')
        if m < 200: return Decimal('8.0')
        if m < 500: return Decimal('12.0')
        if m < 1000:return Decimal('15.0')
        if m < 2000:return Decimal('25.0')
        if m < 5000:return Decimal('50.0')
        return Decimal('100.0')

    async def update_game_message(self):

        active_can_cash = any(uid not in self.cashed_out and uid not in self.crashed_out for uid in self.players)
        self.cashout_btn.disabled = not (self.game_phase == "running" and active_can_cash)
        await self.game_message.edit(embed=await self.make_embed(), view=self)

    async def make_embed(self) -> discord.Embed:
        casino: Casino = self.bot.get_cog("Casino")
        title = "🚀 Crash – Running" if self.game_phase=="running" else "🚀 Crash"
        embed = discord.Embed(title=title, color=discord.Color.blue())

        if self.game_phase=="starting":
            embed.description = f"Starting <t:{self.countdown_end}:R>"
        else:
            embed.add_field(
                name="Multiplier", value=f"{self.current_multiplier:.2f}×", inline=False
            )

        lines = []
        for uid, bet in self.players.items():
            cp = self.crash_points.get(uid, Decimal('0.00'))

            if uid in self.cashed_out:
                status = f"💰 cashed @ {self.cashed_out[uid]:.2f}×"
            elif uid in self.crashed_out:
                status = f"💥 crashed @ {self.crashed_out[uid]:.2f}×"
            else:

                if self.game_phase=="running":
                    val = (bet*self.current_multiplier).quantize(Decimal('0.01'))
                    status = f"🟢 playing → {await casino.formatter(val)} {casino.currency_name}"
                else:
                    status = "🟢 playing"

            if self.game_phase == "ended":
                status += f" ( could have reached {cp:.2f}× )"

            lines.append(f"<@{uid}> {status}")

        embed.add_field(name="Players", value="\n".join(lines), inline=False)
        return embed

class MinesView(discord.ui.View):
    def __init__(self, grid, bomb_positions, main_message, user_id, bet_amount, bot, PF: dict):
        super().__init__(timeout=600)
        self.grid = grid
        self.bomb_positions = bomb_positions
        self.main_message = main_message
        self.user_id = user_id
        self.bet_amount = bet_amount
        # Use the same emoji set as embeds for consistency
        self.bomb_emoji = "<:bombs:1278849752301309994>"
        self.gem_emoji = "<:gems:1278849818025918497>"
        self.bot = bot
        self.PF = PF
        self.cashout_message: Optional[discord.Message] = None  # linked after cashout view send
        self.remaining_safe_cells = 25 - len(bomb_positions)
        self.gems_clicked = 0
        self.clicked_positions = set()
        self.game_over = False
        self.error_count = 0  

        for i in range(5):
            for j in range(5):
                button = discord.ui.Button(label="\u200B", style=discord.ButtonStyle.secondary, custom_id=f"{i}_{j}")
                button.callback = self.button_callback
                self.add_item(button)

    async def button_callback(self, interaction: Interaction):
        try:
            if self.game_over:
                await interaction.response.send_message("The game has already ended.", ephemeral=True)
                return

            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't your Mines game.", ephemeral=True)
                return

            i, j = map(int, interaction.data.get('custom_id', '0_0').split('_'))
            pos = i * 5 + j

            if pos in self.clicked_positions:
                await interaction.response.send_message("This gem has already been clicked!", ephemeral=True)
                return

            # Update the existing game message instead of sending new embeds
            if pos in self.bomb_positions:
                await self.handle_bomb_click(interaction, pos)
            else:
                await self.handle_safe_click(interaction, pos)

        except discord.errors.InteractionResponded:

            pass
        except Exception as e:
            logger.error(f"Error in mines game: {str(e)}")
            self.error_count += 1
            if self.error_count >= 3:

                await self.emergency_end_game(interaction)
            else:
                await self.send_error_message(interaction)

    async def handle_bomb_click(self, interaction: Interaction, pos):
        try:
            self.game_over = True
            self.children[pos].emoji = self.bomb_emoji
            self.children[pos].style = discord.ButtonStyle.danger

            for child in self.children:
                child.disabled = True

            final_grid = self.create_final_grid()
            # Record provably-fair loss in game history
            try:
                await self.bot.database.increment_loss(
                    self.user_id, "mines", self.bet_amount,
                    client_seed=self.PF['client_seed'], seed_used=None,
                    nonce=self.PF['nonce'], hash_hex=self.PF['server_seed_hash']
                )
            except Exception as e:
                logger.error(f"Failed to record mines loss: {e}")
            embed = await self.create_loss_embed(final_grid)
            await interaction.response.edit_message(embed=embed, view=self)
            # Delete cashout message if present (loss state)
            if self.cashout_message:
                try:
                    await self.cashout_message.delete()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error handling bomb click: {str(e)}")
            await self.send_error_message(interaction)

    async def handle_safe_click(self, interaction: Interaction, pos):
        try:
            self.children[pos].emoji = self.gem_emoji
            self.children[pos].style = discord.ButtonStyle.success
            self.clicked_positions.add(pos)
            self.remaining_safe_cells -= 1
            self.gems_clicked += 1

            embed = await self.update_game_embed(interaction.message.embeds[0])
            # If this click cleared the board, perform automatic cashout and show final embed
            if self.remaining_safe_cells == 0:
                await self.automatic_cashout(interaction)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Error handling safe click: {str(e)}")
            await self.send_error_message(interaction)

    async def calculate_multiplier(self):
        try:
            multiplier = await self.bot.database.get_mines_multiplier(len(self.bomb_positions), self.gems_clicked)
            return float(multiplier) if multiplier else 1.0
        except Exception as e:
            logger.error(f"Error calculating multiplier: {str(e)}")
            return 1.0

    async def emergency_end_game(self, interaction: Interaction):
        """Handle critical errors by ending the game and refunding the bet."""
        try:
            self.game_over = True
            wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=self.bet_amount,
                description="Mines game refund due to error"
            )

            embed = discord.Embed(
                title="Game Error",
                description="The game encountered an error and has been cancelled. Your bet has been refunded.",
                color=discord.Color.red()
            )

            for child in self.children:
                child.disabled = True

            # Update the main game message with an error notice
            await self.main_message.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Critical error in emergency end game: {str(e)}")

    async def send_error_message(self, interaction: Interaction):
        """Send a generic error message to the user."""
        try:
            embed = discord.Embed(
                title="Error",
                description="An error occurred. Please try again or contact support if the issue persists.",
                color=discord.Color.red()
            )
            # Prefer followup ephemeral if possible; otherwise, post to channel
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                await interaction.channel.send(embed=embed)
        except:
            pass

    def create_final_grid(self):
        """Create the final grid display."""
        final_grid = ""
        for row in range(5):
            final_grid += "".join(
                [self.bomb_emoji if (row * 5 + col) in self.bomb_positions 
                 else self.gem_emoji for col in range(5)]
            ) + "\n"
        return final_grid

    async def update_game_embed(self, embed):
        """Update the game embed with current state."""
        try:
            embed.set_field_at(2, name="Remaining Gems", value=f"{self.remaining_safe_cells}", inline=False)
            multiplier = await self.calculate_multiplier()
            embed.set_field_at(3, name="Multiplier", value=f"x{multiplier:.3g}", inline=False)
            return embed
        except Exception as e:
            logger.error(f"Error updating game embed: {str(e)}")
            return embed

    async def create_loss_embed(self, final_grid):
        """Create an embed for when the player loses by hitting a bomb."""
        casino: Casino = self.bot.get_cog("Casino")
        embed = discord.Embed(
            title="💥 BOOM! Game Over",
            description=f"You clicked on a bomb and lost your bet of **{await casino.formatter(self.bet_amount)}** **{casino.currency_name}**.\n\n{final_grid}",
            color=discord.Color.red()
        )
        return embed

    async def automatic_cashout(self, interaction: Interaction):
        """Automatically cash out when all safe cells are cleared."""
        self.game_over = True
        casino: Casino = self.bot.get_cog("Casino")

        multiplier = await self.calculate_multiplier()
        winnings = self.bet_amount * Decimal(multiplier)

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=winnings,
            description="Mines game win - all gems cleared"
        )
        await self.bot.database.increment_win(self.user_id, "mines")

        for child in self.children:
            child.disabled = True
        final_grid = self.create_final_grid()
        embed = discord.Embed(
            title="Game Results",
            description=f"🎉 PERFECT! You cleared all gems and won {await casino.formatter(winnings)} **{casino.currency_name}** at {multiplier}x multiplier!\n\n{final_grid}",
            color=discord.Color.gold()
        )
        try:
            await self.bot.database.increment_win(
                self.user_id, "mines", self.bet_amount,
                client_seed=self.PF['client_seed'], seed_used=None,
                nonce=self.PF['nonce'], hash_hex=self.PF['server_seed_hash']
            )
        except Exception as e:
            logger.error(f"Failed to record mines win: {e}")
        await interaction.response.edit_message(embed=embed, view=self)
        # Delete cashout message if present (perfect clear)
        if self.cashout_message:
            try:
                await self.cashout_message.delete()
            except Exception:
                pass

    async def send_final_grid(self, interaction: Interaction, message_text):
        """Update the main game message with the final grid and message."""
        final_grid = self.create_final_grid()
        embed = discord.Embed(
            title="Game Results",
            description=f"{message_text}\n\n{final_grid}",
            color=discord.Color.gold()
        )
        await self.main_message.edit(embed=embed, view=self)

class CashoutView(discord.ui.View):
    def __init__(self, main_message, game_view, user_id, bet_amount, bot):
        super().__init__(timeout=600)
        self.main_message = main_message
        self.game_view = game_view
        self.user_id = user_id
        self.bet_amount = bet_amount
        self.bot = bot

        cashout_button = discord.ui.Button(label="Cashout", style=discord.ButtonStyle.success, custom_id="cashout")
        cashout_button.callback = self.cashout_callback
        self.add_item(cashout_button)

    async def cashout_callback(self, interaction: discord.Interaction):
        casino: Casino = self.bot.get_cog("Casino")

        if interaction.user.id != self.game_view.user_id:
            await interaction.response.send_message("This isn't your Mines game.", ephemeral=True)
            return

        if self.game_view.game_over:
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            await interaction.response.send_message("You cannot cashout after the game has ended!", ephemeral=True)
            return

        if hasattr(self.game_view, 'game_result') and self.game_view.game_result == "Lost":
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            await interaction.response.send_message("You cannot cashout after losing!", ephemeral=True)
            return

        if not hasattr(self.game_view, 'gems_clicked'):
            await interaction.response.send_message("There was an error with the game state. Please try again.", ephemeral=True)
            return

        if self.game_view.gems_clicked == 0:
            await interaction.response.send_message("You need to click a gem before cashing out!", ephemeral=True)
            return

        self.game_view.game_over = True
        self.game_view.game_result = "Won"

        multiplier = await self.game_view.calculate_multiplier()
        winnings = self.bet_amount * Decimal(multiplier)

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.game_view.user_id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=winnings,
            description="Mines game cashout"
        )
        # Record provably-fair win in game history using the game view's PF
        PF = getattr(self.game_view, 'PF', None)
        if PF:
            try:
                await self.bot.database.increment_win(
                    self.game_view.user_id, "mines", self.bet_amount,
                    client_seed=PF['client_seed'], seed_used=None,
                    nonce=PF['nonce'], hash_hex=PF['server_seed_hash']
                )
            except Exception as e:
                logger.error(f"Failed to record mines cashout win: {e}")

        # Disable all game buttons and update the main game message with final state
        for child in self.game_view.children:
            child.disabled = True
        final_grid = self.game_view.create_final_grid()
        final_embed = discord.Embed(
            title="Game Results",
            description=f"Cashed out with **{await casino.formatter(winnings)}** **{casino.currency_name}** at {multiplier}x multiplier!\n\n{final_grid}",
            color=discord.Color.gold()
        )
        await self.game_view.main_message.edit(embed=final_embed, view=self.game_view)

        # Disable cashout button view and acknowledge the interaction ephemerally
        for child in self.children:
            child.disabled = True
        # Acknowledge then delete the cashout message, leaving only the mines board
        await interaction.response.send_message(
            f"Cashed out with **{await casino.formatter(winnings)}** **{casino.currency_name}** at {multiplier}x.",
            ephemeral=True
        )
        try:
            await interaction.message.delete()
        except Exception:
            pass
        # Clear reference so MinesView does not attempt double delete
        self.game_view.cashout_message = None

class DoubleOrNothingView(View):
    def __init__(self, bot, initial_user, initial_amount, winnings, currency_name, user_id, PF):
        super().__init__(timeout=60)
        self.bot = bot
        self.initial_user = initial_user
        self.initial_amount = initial_amount
        self.winnings = winnings
        self.currency_name = currency_name
        self.user_id = user_id
        self.rounds = 0
        self.PF = PF

    @discord.ui.button(label="Double", style=discord.ButtonStyle.green)
    async def double_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        casino: Casino = self.bot.get_cog("Casino")

        if interaction.user.id != self.initial_user.id:
            await interaction.response.send_message("This game is not for you!", ephemeral=True)
            return

        success = await casino.fair_choice(self.user_id, [True, False])
        if success:

            self.winnings = Decimal(self.winnings) * 2
            revealed_seed, new_hash = await self.bot.database.increment_win(
                self.user_id, "double", self.initial_amount,
                client_seed=self.PF['client_seed'],
                seed_used=None,  
                nonce=self.PF['nonce'],
                hash_hex=self.PF['server_seed_hash']
            )
            self.rounds += 1

            embed = discord.Embed(
                description=(
                    f"Current Winnings: {self.currency_name} **{await casino.formatter(self.winnings)}**\n"
                    "Would you like to double again?"
                ),
                color=discord.Color.green(),
            )
            embed.set_author(name="Double Or Nothing", icon_url=self.initial_user.display_avatar.url)
            embed.set_footer(text=f"Round {self.rounds}")
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                self.user_id, "double", self.initial_amount,
                client_seed=self.PF['client_seed'],
                seed_used=None,  
                nonce=self.PF['nonce'],
                hash_hex=self.PF['server_seed_hash']
            )
            embed = discord.Embed(
                description="You lost everything! Better luck next time.",
                color=discord.Color.red(),
            )
            embed.set_author(name="Double Or Nothing", icon_url=self.initial_user.display_avatar.url)
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.red)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        casino: Casino = self.bot.get_cog("Casino")

        if interaction.user.id != self.initial_user.id:
            await interaction.response.send_message("This game is not for you!", ephemeral=True)
            return

        embed = discord.Embed(
            description=(
                f"You cashed out with **{await casino.formatter(self.winnings)}** **{self.currency_name}**!"
            ),
            color=discord.Color.blue(),
        )
        embed.set_author(name="Double Or Nothing", icon_url=self.initial_user.display_avatar.url)
        await interaction.response.edit_message(embed=embed, view=None)

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.initial_user.id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=self.winnings,  
            description="Double or Nothing Winnings"
        )
        self.stop()

    async def on_timeout(self):
        try:
            for child in self.children:
                child.disabled = True

        finally:
            self.stop()

class PokerView(View):
    def __init__(self, bot, cog, initial_user, player_hand, bot_hand, community_cards, bet, wallet_id, user_id, PF):
        super().__init__(timeout=60)
        self.bot = bot
        self.cog = cog
        self.initial_user = initial_user
        self.player_hand = player_hand
        self.bot_hand = bot_hand
        self.community = community_cards
        self.bet = bet
        self.wallet_id = wallet_id
        self.user_id = user_id
        self.PF = PF

        self.play_button = Button(label="Play", style=discord.ButtonStyle.green)
        self.fold_button = Button(label="Fold", style=discord.ButtonStyle.red)
        self.play_button.callback = self.play_callback
        self.fold_button.callback = self.fold_callback
        self.add_item(self.play_button)
        self.add_item(self.fold_button)

    def card_value(self, card: str) -> int:
        """Return a numeric value for a card. 2–10 as numbers; J=11, Q=12, K=13, A=14."""
        rank = card[:-1]
        face_map = {"J": 11, "Q": 12, "K": 13, "A": 14}
        if rank in face_map:
            return face_map[rank]
        return int(rank)

    def _evaluate_5(self, cards5: tuple) -> tuple:

        ranks = [self.card_value(c) for c in cards5]
        suits = [c[-1] for c in cards5]
        counts = Counter(ranks)
        freq_sorted = sorted(counts.items(), key=lambda x: (-x[1], -x[0]))
        is_flush = len(set(suits)) == 1

        unique = sorted(set(ranks), reverse=True)
        is_straight = False
        top_straight = None

        for i in range(len(unique) - 4):
            window = unique[i:i+5]
            if window[0] - window[4] == 4:
                is_straight = True
                top_straight = window[0]
                break

        if not is_straight and set([14,5,4,3,2]).issubset(unique):
            is_straight = True
            top_straight = 5

        if is_straight and is_flush:
            category = 9
            tiebreakers = (top_straight,)
        elif freq_sorted[0][1] == 4:
            category = 8
            four = freq_sorted[0][0]
            kicker = [r for r in ranks if r != four][0]
            tiebreakers = (four, kicker)
        elif freq_sorted[0][1] == 3 and freq_sorted[1][1] >= 2:
            category = 7
            three = freq_sorted[0][0]
            pair = freq_sorted[1][0]
            tiebreakers = (three, pair)
        elif is_flush:
            category = 6
            tiebreakers = tuple(sorted(ranks, reverse=True))
        elif is_straight:
            category = 5
            tiebreakers = (top_straight,)
        elif freq_sorted[0][1] == 3:
            category = 4
            three = freq_sorted[0][0]
            kickers = sorted((r for r in ranks if r != three), reverse=True)[:2]
            tiebreakers = (three, *kickers)
        elif freq_sorted[0][1] == 2 and freq_sorted[1][1] == 2:
            category = 3
            high_pair, low_pair = freq_sorted[0][0], freq_sorted[1][0]
            kicker = [r for r in ranks if r not in (high_pair, low_pair)][0]
            tiebreakers = (high_pair, low_pair, kicker)
        elif freq_sorted[0][1] == 2:
            category = 2
            pair = freq_sorted[0][0]
            kickers = sorted((r for r in ranks if r != pair), reverse=True)[:3]
            tiebreakers = (pair, *kickers)
        else:
            category = 1
            tiebreakers = tuple(sorted(ranks, reverse=True))

        return (category, *tiebreakers)

    def evaluate_hand(self, cards: list) -> tuple:
        """Return the best 5-card poker hand rank from 7 cards."""
        best = (0,)
        for combo in combinations(cards, 5):
            rank = self._evaluate_5(combo)
            if rank > best:
                best = rank
        return best

    async def play_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your game!", ephemeral=True)

        player_rank = self.evaluate_hand(self.player_hand + self.community)
        bot_rank    = self.evaluate_hand(self.bot_hand    + self.community)
        player_wins = player_rank > bot_rank

        if player_wins:
            revealed_seed, new_hash = await self.bot.database.increment_win(
                self.user_id, "poker", self.bet,
                client_seed=self.PF['client_seed'],
                seed_used=None,  
                nonce=self.PF['nonce'],
                hash_hex=self.PF['server_seed_hash']
            )
            payout = self.bet * Decimal("2")
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id, amount=payout, description="Poker Win"
            )
            formatted = await self.cog.formatter(payout)
            result = f"You win! You won **{formatted}**."
            color  = discord.Color.green()
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                self.user_id, "poker", self.bet,
                client_seed=self.PF['client_seed'],
                seed_used=None,  
                nonce=self.PF['nonce'],
                hash_hex=self.PF['server_seed_hash']
            )
            formatted = await self.cog.formatter(self.bet)
            result = f"You lose! You lost **{formatted}**."
            color  = discord.Color.red()

        self.play_button.disabled = True
        self.fold_button.disabled = True
        embed = discord.Embed(
            title="Heads-Up Poker — Result",
            description=(
                f"Your hand: {', '.join(self.player_hand)}\n"
                f"Bots hand: {', '.join(self.bot_hand)}\n"
                f"Community: {', '.join(self.community)}\n\n"
                f"{result}"
            ),
            color=color
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def fold_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your game!", ephemeral=True)

        revealed_seed, new_hash = await self.bot.database.increment_loss(
            self.user_id, "poker", self.bet,
            client_seed=self.PF['client_seed'],
            seed_used=None,  
            nonce=self.PF['nonce'],
            hash_hex=self.PF['server_seed_hash']
        )
        formatted = await self.cog.formatter(self.bet)
        result = f"You folded. You lost **{formatted}**."

        self.play_button.disabled = True
        self.fold_button.disabled = True
        embed = discord.Embed(
            title="Heads-Up Poker — Folded",
            description=result,
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=self)

class LadderView(View):
    def __init__(self, user_id, cog):
        super().__init__(timeout=60)  
        self.user_id = user_id
        self.cog = cog

    @discord.ui.button(label="Climb", style=discord.ButtonStyle.green)
    async def climb_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game!", ephemeral=True)
            return

        await self.cog.climb_ladder(interaction, self.user_id)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.red)
    async def cashout_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game!", ephemeral=True)
            return

        await self.cog.cashout(interaction, self.user_id)

class GameHistoryPaginator(discord.ui.View):
    def __init__(self, cog, history, member, requester):
        super().__init__(timeout=60)
        self.cog = cog
        self.history = history
        self.member = member
        self.requester = requester
        self.per_page = 5
        self.current_page = 0

        lc_outcomes = [(getattr(r, "outcome", "") or "").lower() for r in history]
        self.total_games = len(history)
        self.wins = sum(1 for o in lc_outcomes if o in ("win", "won", "victory", "success"))
        self.losses = sum(1 for o in lc_outcomes if o in ("loss", "lost", "defeat", "failure"))
        self.ties = self.total_games - (self.wins + self.losses)

        self.prev_btn: discord.ui.Button | None = next((c for c in self.children if getattr(c, "label", "").startswith("⬅")), None)
        self.next_btn: discord.ui.Button | None = next((c for c in self.children if getattr(c, "label", "").startswith("➡")), None)
        self._update_button_states()

    def _update_button_states(self):
        if self.prev_btn:
            self.prev_btn.disabled = (self.current_page == 0)
        if self.next_btn:
            self.next_btn.disabled = ((self.current_page + 1) * self.per_page >= len(self.history))

    @discord.ui.button(label="⬅ Previous", style=discord.ButtonStyle.gray, emoji="⬅️")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message("This isn't your history!", ephemeral=True)

        self.current_page = max(0, self.current_page - 1)
        self._update_button_states()
        new_embed = await self.get_page_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.gray, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message("This isn't your history!", ephemeral=True)

        max_page = (len(self.history) - 1) // self.per_page
        self.current_page = min(max_page, self.current_page + 1)
        self._update_button_states()
        new_embed = await self.get_page_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

    async def get_page_embed(self) -> discord.Embed:
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_items = self.history[start:end]

        color = (
            self.member.top_role.color
            if hasattr(self.member, "top_role") and self.member.top_role
            else discord.Color.blurple()
        )

        embed = discord.Embed(
            title=f"{self.member.display_name}'s Game History",
            color=color,
            description=f"📊 Total: **{self.total_games}** • 🏆 Wins: **{self.wins}** • 💀 Losses: **{self.losses}** • 🔁 Ties: **{self.ties}**"
        )

        try:
            avatar_url = self.member.avatar.url if self.member.avatar else None
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
        except Exception:
            pass

        emoji_map = {
            "win": "🏆",
            "won": "🏆",
            "victory": "🏆",
            "success": "🏆",
            "loss": "💀",
            "lost": "💀",
            "defeat": "💀",
            "failure": "💀",
            "tie": "🔁",
            "draw": "🔁",
        }

        for row in page_items:

            gamename = str(getattr(row, "game_name", "Unknown")).capitalize()
            outcome = str(getattr(row, "outcome", "Unknown"))
            outcome_lc = outcome.lower()
            emoji = emoji_map.get(outcome_lc, "🔹")
            wagered = getattr(row, "wagered", 0)
            try:
                wager_text = f"{self.cog.currency_name} **{await self.cog.formatter(Decimal(wagered))}**"
            except Exception:
                wager_text = f"{self.cog.currency_name} **{wagered}**"
            created_at = getattr(row, "created_at", None)
            timestamp = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "Unknown time"
            client_seed = getattr(row, "client_seed", "—")
            nonce = getattr(row, "nonce", "—")

            field_name = f"{emoji} {gamename} • {timestamp}"
            field_value = (
                f"**Result:** {outcome.capitalize()}\n"
                f"**Wager:** {wager_text}\n"
                f"**Seed:** `{client_seed}` • **Nonce:** {nonce}"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        total_pages = max(1, (len(self.history) + self.per_page - 1) // self.per_page)
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • Showing {len(page_items)} of {len(self.history)} entries")

        return embed

U64_RANGE = 1 << 64

def _u64_from_hmac(server_seed: str, client_seed: str, nonce: int, tag: str) -> int:
    """
    Produce a 64-bit unsigned int via HMAC(server_seed, f"{client_seed}:{nonce}:{tag}").
    'tag' provides domain-separation across functions so the same nonce doesn't correlate outputs.
    """
    msg = f"{client_seed}:{nonce}:{tag}".encode()
    digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")  

def _rehash_u64(u64: int) -> int:
    """Deterministically 'stretch' to a fresh 64-bit value for rejection sampling."""
    return int.from_bytes(hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big")

def _rand_below_unbiased(u64: int, n: int) -> int:
    """
    Rejection sampling to remove modulo bias. Returns x in [0, n).
    """
    if n <= 0:
        raise ValueError("upper bound must be positive")
    limit = U64_RANGE - (U64_RANGE % n)
    while u64 >= limit:
        u64 = _rehash_u64(u64)
    return u64 % n

class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.utils = MiscUtils(self)
        self.currency_name = "<:coin:1359823671581085847>"
        self.ladder_games = {}
        self.active_players = set()
        self.active_games: dict[int, CrashView] = {}
        self.cooldowns = {}
        self.defaultpot = 10000.0
        self.roll_history = defaultdict(list)
        self.games = ["gamble", "supergamble", "dice", "slots", "blackjack", "roulette", "mines", "double", "ladder", "poker", "crash", "hilo", "baccarat"]
        self.fair = ProvenFairness()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    async def _next_u64(self, user_id: int, *, tag: str) -> tuple[int, dict]:
        server_seed, client_seed, nonce = await self.bot.database.bump_and_get(user_id)  
        msg = f"{client_seed}:{nonce}:{tag}".encode()
        digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
        u64 = int.from_bytes(digest[:8], "big")
        proof = {
            "server_seed_hash": hashlib.sha256(server_seed.encode()).hexdigest(),
            "client_seed": client_seed,
            "nonce": nonce,
            "tag": tag,
            "u64_hex": digest[:8].hex(),
        }
        return u64, proof

    async def fair_randbelow(self, user_id: int, upper: int, *, tag: str = "randbelow") -> int:
        if upper <= 0:
            raise ValueError("upper must be > 0")
        u64, _ = await self._next_u64(user_id, tag=tag)

        limit = U64_RANGE - (U64_RANGE % upper)
        while u64 >= limit:
            u64 = int.from_bytes(hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big")
        return u64 % upper
    async def fair_random(self, user_id: int) -> float:
        u64, _ = await self._next_u64(user_id, tag="random")
        return u64 / float(U64_RANGE)  

    async def fair_sample(self, user_id: int, seq: Sequence[Any], k: int) -> List[Any]:
        """
        Sample k elements without replacement using a Fisher–Yates shuffle.
        We increment the nonce inside fair_randbelow per swap; there is NO extra increment here.
        """
        if k < 0 or k > len(seq):
            raise ValueError("Sample size cannot exceed sequence length and must be non-negative.")
        clone = list(seq)
        await self.fair_shuffle(user_id, clone)
        return clone[:k]

    async def fair_choice(self, user_id: int, seq: Sequence[Any], *, tag: str = "choice"):
        if not seq:
            raise ValueError("sequence must be non-empty")
        idx = await self.fair_randbelow(user_id, len(seq), tag=tag)
        return seq[idx]

    async def fair_shuffle(self, user_id: int, deck: List[Any]) -> None:

        for i in range(len(deck) - 1, 0, -1):
            j = await self.fair_randbelow(user_id, i + 1, tag=f"shuffle:{i}")
            deck[i], deck[j] = deck[j], deck[i]

    async def fair_uniform(self, user_id: int, min_value: float, max_value: float) -> float:
        if min_value >= max_value:
            raise ValueError("min_value must be less than max_value")
        r = await self.fair_random(user_id)
        return min_value + (max_value - min_value) * r

    async def fair_systemrandom(self, user_id: int, min_value: int, max_value: int, *, tag: str = "systemrandom") -> int:
        if min_value > max_value:
            raise ValueError("min_value must be <= max_value")
        span = (max_value - min_value) + 1
        idx = await self.fair_randbelow(user_id, span, tag=tag)
        return min_value + idx

    async def prove_fairness(self, user_id: int) -> dict:
        """
        Publish commitment only; reveal raw seed only after rotation.
        """
        server_seed = await self.bot.database.get_server_seed(user_id)
        client_seed, nonce = await self.bot.database.get_client_seed(user_id)
        return {
            "server_seed_hash": hashlib.sha256(server_seed.encode()).hexdigest(),
            "client_seed": client_seed,
            "nonce": nonce,
        }

    def _fmt_no_sci(self, x: Decimal, *, max_frac: int = 2, rounding=ROUND_HALF_UP) -> str:
        """
        Format a Decimal without scientific notation, with commas,
        and trim trailing zeros up to max_frac places.
        """
        if max_frac < 0:
            max_frac = 0
        quant = Decimal(1).scaleb(-max_frac) if max_frac else Decimal(1)
        xq = x.quantize(quant, rounding=rounding)
        s = f"{xq:,.{max_frac}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    async def formatter(self, value: Decimal) -> str:
        """Currency-friendly long format (e.g., 1000000 -> '1 million')."""
        negative = value < 0
        value = abs(value)

        suffixes = [
            (Decimal('1e33'), " decillion"),
            (Decimal('1e30'), " nonillion"),
            (Decimal('1e27'), " octillion"),
            (Decimal('1e24'), " septillion"),
            (Decimal('1e21'), " sextillion"),
            (Decimal('1e18'), " quintillion"),
            (Decimal('1e15'), " quadrillion"),
            (Decimal('1e12'), " trillion"),
            (Decimal('1e9'),  " billion"),
            (Decimal('1e6'),  " million"),
            (Decimal('1e3'),  " thousand"),
        ]

        for threshold, suffix in suffixes:
            if value >= threshold:
                num = self._fmt_no_sci(value / threshold, max_frac=2)
                out = f"{num}{suffix}"
                return f"-{out}" if negative else out

        num = self._fmt_no_sci(value, max_frac=2)
        return f"-{num}" if negative else num

    async def short_formatter(self, value: Decimal) -> str:
        """Currency-friendly short format (e.g., 1000000 -> '1 mil')."""
        negative = value < 0
        value = abs(value)

        suffixes = [
            (Decimal('1e33'), " dec"),
            (Decimal('1e30'), " non"),
            (Decimal('1e27'), " oct"),
            (Decimal('1e24'), " sept"),
            (Decimal('1e21'), " sext"),
            (Decimal('1e18'), " quin"),
            (Decimal('1e15'), " quad"),
            (Decimal('1e12'), " tril"),
            (Decimal('1e9'),  " bil"),
            (Decimal('1e6'),  " mil"),
            (Decimal('1e3'),  "k"),
        ]

        for threshold, suffix in suffixes:
            if value >= threshold:
                num = self._fmt_no_sci(value / threshold, max_frac=2)
                out = f"{num}{suffix}"
                return f"-{out}" if negative else out

        num = self._fmt_no_sci(value, max_frac=2)
        return f"-{num}" if negative else num

    async def amount_handler(self, amount_input: str, user_balance: Decimal) -> Decimal:
        """
        Process the bet input and return the corresponding bet amount.
        Supports keywords ('all', 'half', 'quarter'), percentages, and suffixed values.
        The returned amount is truncated (not rounded) to two decimal places.
        """

        if not isinstance(amount_input, str):
            raise ValueError("Invalid amount input type.")

        amount_input = amount_input.strip().lower()

        if amount_input == "all" or amount_input == "max":
            amount = user_balance
        elif amount_input == "half":
            amount = user_balance / Decimal('2')
        elif amount_input == "quarter":
            amount = user_balance / Decimal('4')

        elif amount_input.endswith('%'):
            percentage_match = re.match(r'^([0-9]+(\.[0-9]+)?)%$', amount_input)
            if percentage_match:
                try:
                    percentage = Decimal(percentage_match.group(1))
                    if Decimal('1') <= percentage <= Decimal('100'):
                        amount = user_balance * (percentage / Decimal('100'))
                    else:
                        raise ValueError("Percentage must be between 1% and 100%.")
                except InvalidOperation:
                    raise ValueError("Invalid percentage value.")
            else:
                raise ValueError("Invalid percentage format.")
        else:

            multipliers = {
                'k': Decimal('1000'),
                'm': Decimal('1000000'),
                'b': Decimal('1000000000'),
                't': Decimal('1000000000000'),
                'q': Decimal('1000000000000000'),
                'qu': Decimal('1000000000000000000'),
                's': Decimal('1000000000000000000000'),
            }

            multiplier_match = re.match(r'^([0-9]+(\.[0-9]+)?)(k|m|b|t|q|qu|s)?$', amount_input)
            if not multiplier_match:
                raise ValueError("Invalid amount format.")

            try:
                number = Decimal(multiplier_match.group(1))
                if multiplier_match.group(3):  
                    multiplier = multipliers[multiplier_match.group(3)]
                    amount = number * multiplier
                else:
                    amount = number
            except (InvalidOperation, KeyError):
                raise ValueError("Invalid amount.")

        if amount.is_nan():
            raise ValueError("Invalid amount.")

        try:
            amount = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValueError("Invalid amount.")

        if amount > user_balance:
            raise ValueError("Insufficient Funds.")
        if amount <= Decimal('0'):
            raise ValueError("Amount must be greater than 0.")

        return amount

    @commands.group(name="casino", invoke_without_command=True, help="Casino command group. Use !casino help for subcommands.")
    async def casino(self, ctx: commands.Context):
        """Root for casino commands. Lists available subcommands."""
        prefix = (await self.bot.get_prefix(ctx.message))
        if isinstance(prefix, list):
            prefix = prefix[0]

        subcmds = getattr(ctx.command, "commands", []) or []
        lines = []
        for cmd in sorted(subcmds, key=lambda c: c.name):
            name = cmd.name
            aliases = f" (or: {', '.join(cmd.aliases)})" if getattr(cmd, "aliases", None) else ""
            desc = (cmd.help or cmd.description or "").strip()
            if desc:
                lines.append(f"`{prefix}casino {name}`{aliases} — {desc}")
            else:
                lines.append(f"`{prefix}casino {name}`{aliases}")

        if not lines:
            description = "No subcommands available."
        else:
            description = "\n".join(lines)

        embed = discord.Embed(
            title="Casino — Available Commands",
            description=description,
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Use {prefix}casino <subcommand> for details.")

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(name="stats", help="Check your win/loss statistics for a specific game.")
    async def casino_stats(
        self,
        ctx: commands.Context,
        game_name: str = "gamble",
    ):
        """
        Usage:
          !casino stats [game_name] [@member]
        """
        member = ctx.author
        game_key = (game_name or "gamble").lower()

        if game_key not in self.games:
            valid = ", ".join(g.title() for g in sorted(self.games))
            embed = discord.Embed(
                title="Invalid Game",
                description=f"Choose one of: {valid}.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, mention_author=False)

        wins, losses = await self.bot.database.get_game_stats(member.id, game_key)
        total_games = wins + losses
        win_rate = (wins / total_games * 100) if total_games else 0.0

        total_wagered, win_wagered, loss_wagered = await self.bot.database.get_wager_stats(
            member.id, game_key
        )

        average_bet  = (total_wagered / total_games) if total_games else Decimal("0")
        average_win  = (win_wagered   / wins      ) if wins       else Decimal("0")
        average_loss = (loss_wagered  / losses    ) if losses     else Decimal("0")

        color = (
            member.top_role.color
            if hasattr(member, "top_role") and member.top_role
            else discord.Color.blurple()
        )
        embed = discord.Embed(
            title=f"{member.display_name}'s {game_key.title()} Stats",
            color=color
        )
        embed.add_field(name="Wins",               value=str(wins),          inline=True)
        embed.add_field(name="Losses",             value=str(losses),        inline=True)
        embed.add_field(name="Total Games",        value=str(total_games),   inline=True)
        embed.add_field(name="Win Rate",           value=f"{win_rate:.2f}%", inline=False)
        embed.add_field(
            name="Total Wagered",
            value=f"{self.currency_name} **{await self.formatter(total_wagered)}**",
            inline=True
        )
        embed.add_field(
            name="Avg Bet",
            value=f"{self.currency_name} **{await self.short_formatter(average_bet)}**",
            inline=True
        )
        embed.add_field(
            name="Avg Wager (Wins)",
            value=f"{self.currency_name} **{await self.short_formatter(average_win)}**",
            inline=True
        )
        embed.add_field(
            name="Avg Wager (Losses)",
            value=f"{self.currency_name} **{await self.short_formatter(average_loss)}**",
            inline=True
        )

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(name="leaderboard", aliases=["lb"], help="View the top winners and losers for a specific game.")
    async def casino_leaderboard(
        self,
        ctx: commands.Context,
        game_name: str = "gamble",
        limit: int = 10
    ):
        """
        Usage:
          !casino leaderboard [game_name] [limit]
        """
        game_key = (game_name or "gamble").lower()

        if game_key not in self.games:
            valid = ", ".join(g.title() for g in sorted(self.games))
            embed = discord.Embed(
                title="Invalid Game",
                description=f"Choose one of: {valid}.",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, mention_author=False)

        top_winners = await self.bot.database.get_top_game_winners(game_key, limit)

        embed = discord.Embed(
            color=ctx.author.top_role.color if getattr(ctx.author, "top_role", None) else discord.Color.blurple()
        )
        embed.set_author(name=f"Casino Leaderboard - {game_name.title()}", icon_url=self.utils.get_avatar_url(ctx.author))
        if top_winners:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [f"{idx}." for idx in range(2, 11)]
            for idx, (user_id, wins) in enumerate(top_winners):
                user = ctx.guild.get_member(user_id) or self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                display_name = user.display_name if user else f"Unknown {user_id}"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{wins:,} wins`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)
        await ctx.send(embed=embed)

    @casino.command(name="history", aliases=["games", "ghistory"], help="View your game history.")
    async def casino_history(
        self,
        ctx: commands.Context,
        limit: int = 100
    ):
        """
        Usage:
          !casino history [@member] [limit]
          !casino games
          !casino ghistory
        """
        member = ctx.author
        limit = max(1, min(int(limit), 1000))  

        history = await self.bot.database.get_user_game_history(member.id, limit)
        if not history:
            embed = discord.Embed(
                title="Game History",
                description="No game history found.",
                color=discord.Color.red()
            )
            return await ctx.reply(embed=embed, delete_after=5, mention_author=False)

        view = GameHistoryPaginator(
            cog=self,
            history=history,
            member=member,
            requester=ctx.author
        )
        embed = await view.get_page_embed()
        await ctx.reply(embed=embed, view=view, mention_author=False)

    @casino.command(name="verify", aliases=["v",'verif','check'], help="verify a provably-fair game outcome", hidden=True)
    async def casino_verify(
        self,
        ctx: commands.Context,
        game: str,
        nonce: int,
        *extra_args: str
    ):
        """
        Usage examples:
          !casino verify gamble @user 42
          !casino verify supergamble @user 7
          !casino verify dice @user 5
          !casino verify ladder @user 3 2     
          !casino verify slots @user 10
          !casino verify blackjack @user 15
          !casino verify poker @user 12
          !casino verify roulette @user 9
        """

        game_key = (game or "").lower()
        user_id = ctx.author.id

        record = await self.bot.database.fetch_game_for_user(user_id, game_key, nonce)
        if not record:
            embed = discord.Embed(
                title="🔎 Verification — Error",
                description=f"No record found for `{game_key}` nonce `{nonce}` for {ctx.author.name}.",
                color=discord.Color.red()
            )
            return await ctx.reply(embed=embed, delete_after=8, mention_author=False)

        server_seed = record.used_server_seed
        client_seed = record.client_seed
        server_hash_short = hashlib.sha256(server_seed.encode()).hexdigest()[:12] + "…"

        pf = self.fair
        title = f"🔒 Provably Fair — {game_key.title()}"
        desc = (
            f"User: {ctx.author.name}\n"
            f"Nonce: `{nonce}` • Client Seed: `{client_seed}`\n"
            f"Server Hash: `{server_hash_short}`"
        )
        embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
        embed.set_thumbnail(url=ctx.author.display_avatar.url)

        try:
            if game_key == "gamble":
                result = pf.verify_gamble(server_seed, client_seed, nonce)
                pretty = json.dumps(result, indent=2) if not isinstance(result, str) else result
                embed.add_field(name="🎰 Gamble Result", value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```", inline=False)

            elif game_key in ("supergamble", "sgamble"):
                result = pf.verify_supergamble(server_seed, client_seed, nonce)
                pretty = json.dumps(result, indent=2) if not isinstance(result, str) else result
                embed.add_field(name="💥 SuperGamble", value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```", inline=False)

            elif game_key in ("dice", "roll"):
                die1, die2 = pf.verify_dice(server_seed, client_seed, nonce)
                total = die1 + die2
                embed.add_field(name="🎲 Dice", value=f"Die 1: **{die1}** • Die 2: **{die2}**\nTotal: **{total}**", inline=False)

            elif game_key == "ladder":
                if not extra_args:
                    return await ctx.reply("For ladder, supply both nonce and step.", mention_author=False)
                step = int(extra_args[0])
                roll_pct, threshold = pf.verify_ladder(server_seed, client_seed, nonce, step)

                bar_len = 20
                filled = int(min(max(roll_pct, 0), 100) / 100 * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                embed.add_field(name="🪜 Ladder Roll", value=f"Step: **{step}**\nRoll: `{roll_pct:.2f}%` • Threshold: `{threshold:.2f}%`\n`{bar}`", inline=False)

            elif game_key == "slots":
                slots_str = pf.verify_slots(server_seed, client_seed, nonce)
                embed.add_field(name="🎰 Slots Shuffle / Outcome", value=f"```{shorten(str(slots_str), width=900, placeholder='…')}```", inline=False)

            elif game_key == "blackjack" or game_key == "bj":
                bj = pf.verify_blackjack(server_seed, client_seed, nonce)
                shuffled = bj.get("shuffled_deck", [])
                player_cards = bj.get("player_cards", [])
                dealer_cards = bj.get("dealer_cards", [])
                embed.add_field(name="🃏 Blackjack — Player", value=f"{', '.join(player_cards)}", inline=False)
                embed.add_field(name="🃏 Blackjack — Dealer", value=f"{', '.join(dealer_cards)}", inline=False)
                embed.add_field(name="🔀 Shuffled Deck (full)", value=f"```{', '.join(shuffled)}```", inline=False)

            elif game_key in ("ridebus", "bus"):
                result = pf.verify_ridebus(server_seed, client_seed, nonce)
                pretty = json.dumps(result, indent=2) if not isinstance(result, str) else result
                embed.add_field(name="🚌 Ridebus", value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```", inline=False)

            elif game_key == "poker":
                pk = pf.verify_poker(server_seed, client_seed, nonce)
                player_hand = pk.get("player_hand", [])
                bot_hand = pk.get("bot_hand", [])
                community = pk.get("community", [])
                embed.add_field(name="🂡 Poker — Player", value=f"{', '.join(player_hand)}", inline=False)
                embed.add_field(name="🤖 Poker — Bot", value=f"{', '.join(bot_hand)}", inline=False)
                embed.add_field(name="🃏 Community", value=f"{', '.join(community)}", inline=False)

            elif game_key == "roulette":
                r = pf.verify_roulette(server_seed, client_seed, nonce)
                color = r.get("color", "Unknown")
                spin_result = r.get("spin_result", "—")
                emoji = "🟢" if color.lower() == "green" else ("🔴" if color.lower() == "red" else "⚫")
                embed.add_field(name="🎡 Roulette", value=f"{emoji} {color.title()} • **{spin_result}**", inline=False)

            else:
                return await ctx.reply(f"Unknown game '{game_key}'.", mention_author=False)

        except Exception as e:
            logger.exception("Error while verifying PF data")
            embed = discord.Embed(title="⚠️ Verification Error", description=f"An error occurred while verifying: {e}", color=discord.Color.red())
            return await ctx.reply(embed=embed, mention_author=False)

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(name="seed", help="View your current client seed and the hashed server seed.")
    async def casino_seed(self, ctx: commands.Context):
        """
        Usage:
          !casino seed
        """
        client_seed, nonce = await self.bot.database.get_client_seed(ctx.author.id)
        server_seed = await self.bot.database.get_server_seed(ctx.author.id)
        server_hash = hashlib.sha256(server_seed.encode()).hexdigest()

        color = (
            ctx.author.top_role.color
            if not isinstance(ctx.channel, discord.DMChannel) and getattr(ctx.author, "top_role", None)
            else discord.Color.blurple()
        )

        embed = discord.Embed(title="Seed Info", color=color)
        embed.add_field(name="Client Seed", value=f"`{client_seed}`", inline=False)
        embed.add_field(name="Nonce", value=f"`{nonce}`", inline=False)
        embed.add_field(name="Server Seed Hash", value=f"`{server_hash}`", inline=False)
        embed.set_footer(text="These seeds are used for provable fairness in games.")

        await self.bot.database.set_cooldown(ctx.author.id, "casino seed", 5)
        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(name="setseed", aliases=["newseed"], help="Update your client seed for provable fairness.")
    async def casino_setseed(self, ctx: commands.Context, *, seed: Optional[str] = None):
        """
        Usage:
          !casino setseed [seed]
          (if no seed is supplied, a random one is generated)
        """
        if not seed:
            seed = secrets.token_hex(16)

        await self.bot.database.set_client_seed(ctx.author.id, seed)
        embed = discord.Embed(
            description=f"Your client seed has been updated to: `{seed}`",
            color=discord.Color.green()
        )

        await self.bot.database.set_cooldown(ctx.author.id, "casino setseed", 5)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="gamble", description="Gamble your money for a chance to win big!")
    async def gamble(self, ctx: Context, bet_amount: str):
        try:
            user_id = ctx.author.id

            PF = await self.prove_fairness(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = Decimal(str(balance)).quantize(Decimal('0.01'))

            try:
                amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if amount > max_allowed:
                amount = max_allowed
                await ctx.reply(embed=discord.Embed(
                    description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                                f"**{await self.formatter(amount)} {self.currency_name}**.",
                    color=discord.Color.orange()),
                    delete_after=5)
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-Decimal(amount),
                    description="Gamble Bet"
                )
            except ValueError as e:
                embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            win_multiplier = Decimal('2.0')
            is_winner = await self.fair_randbelow(user_id, 2) == 1

            if is_winner:
                winnings = Decimal(amount) * win_multiplier
                revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "gamble", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=winnings,
                        description="Gamble Win"
                    )
                except ValueError as e:
                    embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                embed = discord.Embed(
                    description=f"You won {self.currency_name} **{await self.formatter(winnings)}**!",
                    color=discord.Color.green()
                )
            else:
                revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "gamble", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                embed = discord.Embed(
                    description=f"You lost {self.currency_name} **{await self.formatter(amount)}**",
                    color=discord.Color.red()
                )

            await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)
            await ctx.reply(embed=embed)

        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)

    @commands.command(name="supergamble", aliases=["sg","sgamble"], description="Gamble at a 15% chance to win for amazing rewards!")
    async def supergamble(self, ctx: Context, bet_amount: str):
        try:
            user_id = ctx.author.id

            PF = await self.prove_fairness(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = Decimal(str(balance)).quantize(Decimal('0.01'))

            try:
                amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if amount > max_allowed:
                amount = max_allowed
                await ctx.reply(embed=discord.Embed(
                    description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                                f"**{await self.formatter(amount)} {self.currency_name}**.",
                    color=discord.Color.orange()), 
                    delete_after=5)
            try:
                await self.bot.database.process_treasury_transaction(wallet_id, -amount, "SuperGamble Bet")
            except ValueError as e:
                embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            win_roll   = await self.fair_randbelow(user_id, 100)
            bonus_roll = await self.fair_randbelow(user_id, 100)

            win_chance = 10  
            win = win_roll < win_chance
            base_multiplier = Decimal("8.0")
            bonus_multiplier = Decimal("12.0")

            supply = await self.bot.database.get_supply_record()
            treasury = supply.treasury
            circulating = supply.circulating
            total_supply = supply.total_supply

            treasury_ratio = treasury / total_supply if total_supply > 0 else Decimal("0")

            if win:
                raw_multiplier = bonus_multiplier if bonus_roll < 15 else base_multiplier
                bonus_text = "\n🌟 **MEGA WIN!** Extra multiplier applied!" if bonus_roll < 15 else ""

                winnings = (amount * raw_multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if winnings > treasury:
                    winnings = treasury
                    bonus_text += "\n💸 Treasury couldn't pay full amount — payout capped."

                revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "supergamble", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                try:
                    await self.bot.database.process_treasury_transaction(wallet_id, winnings, "SuperGamble Win")
                except ValueError as e:
                    embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                embed=discord.Embed(
                    description=f"🎉 You hit the jackpot and won **{await self.formatter(winnings)} {self.currency_name}**! {bonus_text}",
                    color=discord.Color.gold()
                )

            else:

                recovery_roll = bonus_roll
                recovery_allowed = recovery_roll < 10 and treasury_ratio >= Decimal("0.1")
                if recovery_allowed:
                    recovery = (amount * Decimal("0.20")).quantize(Decimal("0.01"))
                    if recovery > treasury:
                        recovery = treasury

                    try:
                        await self.bot.database.process_treasury_transaction(wallet_id, recovery, "SuperGamble Loss Recovery")
                    except ValueError as e:
                        embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                        await ctx.reply(embed=embed, delete_after=5)
                        return
                    embed=discord.Embed(
                        description=f"You lost, but recovered **{await self.formatter(recovery)} {self.currency_name}**!",
                        color=discord.Color.blurple()
                    )
                else:
                    revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "supergamble", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                    embed=discord.Embed(
                        description=f"You lost **{await self.formatter(amount)} {self.currency_name}**.",
                        color=discord.Color.red()
                        )

            await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 60)
            await ctx.reply(embed=embed)

        except ValueError as e:
            await ctx.reply(embed=discord.Embed(description=str(e), color=discord.Color.red()), delete_after=5)

    @commands.command(
        name="slots",
        aliases=["slot"],
        description="Play the slots and win (or almost win) big – provably fair!"
    )
    async def slots(self, ctx: Context, bet_amount: str):
        user_id     = ctx.author.id
        currency    = self.currency_name

        PF = await self.prove_fairness(user_id)        

        wallet_id   = await self.bot.database.get_wallet_id_for_user(user_id)
        bal_raw     = await self.bot.database.get_wallet_balance(wallet_id)
        balance     = Decimal(str(bal_raw))

        try:
            stake = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=str(e), color=discord.Color.red()), delete_after=5)

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id)
        if stake > max_allowed:
            stake = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"High-roller bet capped at **{await self.formatter(stake)} {currency}**.",
                color=discord.Color.orange()), delete_after=5)

        try:
            await self.bot.database.process_treasury_transaction(wallet_id, -stake, "Slots Bet")
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()), delete_after=5)

        symbols = [":cherries:", ":lemon:", ":seven:", ":bell:", ":beers:", ":gem:"]
        weights = [4.61, 3.81, 3.03, 2.22, 1.44, 1.08]        

        SCALE   = 10_000                                      
        scaled  = [int(w * SCALE) for w in weights]           
        cum_int = []
        total_i = 0
        for s in scaled:
            total_i += s
            cum_int.append(total_i)                           

        async def weighted_choice_int() -> str:
            rnd = await self.fair_randbelow(user_id, total_i)  
            for sym, bound in zip(symbols, cum_int):
                if rnd < bound:
                    return sym
            return symbols[-1]  

        reel = [await weighted_choice_int() for _ in range(6)]

        PAY = {

            (":gem:",   6): 800,
            (":bell:",  6):  60,
            (":beers:", 6):  80,
            (":seven:", 6):  40,
            (":lemon:", 6):  10,
            (":cherries:", 6):  5,

            (":gem:",   5): 100,
            (":bell:",  5):  30,
            (":beers:", 5):  40,
            (":seven:", 5):  20,
            (":lemon:", 5):   5,
            (":cherries:", 5):  2.5,

            (":gem:",   4):  20,
            (":bell:",  4):   8,
            (":beers:", 4):  10,
            (":seven:", 4):   6,
            (":lemon:", 4):   2,
            (":cherries:", 4): 1,

            (":gem:",   3):   5,
            (":bell:",  3): 2.5,
            (":beers:", 3):   3,
            (":seven:", 3):   2,
            (":lemon:", 3): Decimal("0.8"),
            (":cherries:", 3): Decimal("0.5"),

            (":cherries:", 2): Decimal("0.2"),
        }

        counts     = Counter(reel)
        sym, qty   = counts.most_common(1)[0]
        multiplier = Decimal(PAY.get((sym, qty), 0))
        winnings   = (stake * multiplier).quantize(Decimal("0.01"))

        if winnings:
            await self.bot.database.process_treasury_transaction(wallet_id, winnings, "Slots Win")
            revealed_seed, new_hash = await self.bot.database.increment_win(
                user_id, "slots", stake,
                client_seed=PF["client_seed"], seed_used=None, nonce=PF["nonce"], hash_hex=PF["server_seed_hash"]
            )
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                user_id, "slots", stake,
                client_seed=PF["client_seed"], seed_used=None, nonce=PF["nonce"], hash_hex=PF["server_seed_hash"]
            )

        if multiplier >= 1:
            title = "Win!"
            col   = discord.Color.blurple() if multiplier < 5 else discord.Color.green()
        elif multiplier > 0:
            title = "Small Return"
            col   = discord.Color.orange()
        else:
            title = "Loss"
            col   = discord.Color.red()

        outcome = (
            f"{' '.join(reel)}\n\n"
            f"**{title}** – Payout {multiplier}×\n\n"
            f"{'Won' if winnings else 'Lost'} **{await self.formatter(winnings or stake)} {currency}**"
        )

        embed = discord.Embed(description=outcome, color=col)
        embed.set_author(name="Slots", icon_url=self.utils.get_avatar_url(ctx.author))
        footer = f"clientSeed={PF['client_seed']} | serverHash={PF['server_seed_hash'][:12]}… | nonceStart={PF['nonce']}"

        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed)

    @commands.command(name="dice", aliases=["diceroll","roll"], description="Roll two dice and bet on the outcome.")
    async def roll(self, ctx: Context, bet_amount: str, guess: str):
        user_id = ctx.author.id

        PF = await self.prove_fairness(user_id)  

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))
        currency_name = self.currency_name

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(amount)} {self.currency_name}**.",
                color=discord.Color.orange()), delete_after=5)

        normalized_guess = guess.lower()
        if normalized_guess not in ["even", "evens", "odd", "odds"] and not normalized_guess.isdigit():
            await ctx.reply("Invalid guess. Please use 'even', 'odd', or a number between 2 and 12.", delete_after=5)
            return
        if normalized_guess.isdigit() and (int(normalized_guess) < 2 or int(normalized_guess) > 12):
            await ctx.reply("Invalid guess. The total of two dice can only be between 2 and 12.", delete_after=5)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-Decimal(amount),
                description="Dice Bet"
            )
        except ValueError as e:
            embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        die1 = await self.fair_randbelow(user_id, 6) + 1
        die2 = await self.fair_randbelow(user_id, 6) + 1
        total = die1 + die2
        even_or_odd = "E" if total % 2 == 0 else "O"

        self.roll_history.setdefault(user_id, []).append(even_or_odd)
        if len(self.roll_history[user_id]) > 5:
            self.roll_history[user_id].pop(0)

        payout_multipliers = {
            2: 10,
            3: 7,
            4: 5,
            5: 4,
            6: 3,
            7: 2,
            8: 3,
            9: 4,
            10: 5,
            11: 7,
            12: 10
        }
        even_odd_payout = 1.5
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = ctx.author.top_role.color if ctx.author.top_role else discord.Color.blurple()
        embed = discord.Embed(color=color)
        embed.set_author(name="Diceroll", icon_url=self.utils.get_avatar_url(ctx.author))

        winnings = 0
        if (normalized_guess in ["even", "evens"] and total % 2 == 0) or (normalized_guess in ["odd", "odds"] and total % 2 == 1):
            winnings = Decimal(amount) * Decimal(even_odd_payout)
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nYou guessed correctly and won {currency_name} **{await self.formatter(winnings)}**!"
        elif normalized_guess.isdigit() and int(normalized_guess) == total:
            multiplier = payout_multipliers[total]
            winnings = Decimal(amount) * Decimal(multiplier)
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nExact match! You won {currency_name} **{await self.formatter(winnings)}** with a {multiplier}x payout!"
        else:
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nYou lost {currency_name} **{await self.formatter(amount)}**."

        if winnings > 0:

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=winnings,
                    description="Dice Win"
                )
            except ValueError as e:
                embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return
            revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "dice", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "dice", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])

        embed.description = result
        await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed)

    @commands.command(name="roulette", aliases=["roul","rou"], description="Play roulette and bet on a number or color. If no arguments are given, shows bet options.")
    async def roulette(self, ctx: Context, bet_amount: str = None, choice: str = None):

        if bet_amount is None or choice is None:
            embed = discord.Embed(
                title="Roulette Betting Options",
                description=(
                    "Usage: `!roulette <amount> <bet>`\n\n"
                    "**Columns:**\n"
                    "`column1`: 1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34\n"
                    "`column2`: 2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35\n"
                    "`column3`: 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36\n\n"
                    "**Other bets:** `red`, `black`, `odd`, `even`, `high`, `low`, `dozen1`, `dozen2`, `dozen3`\n"
                    "**Single numbers:** `0`–`36`, or `00` (payout ×36).\n\n"
                    "Example: `!roulette 100 red`"
                ),
                color=discord.Color.blue()
            )
            await ctx.reply(embed=embed, delete_after=20)
            return

        user_id = ctx.author.id

        PF = await self.prove_fairness(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        raw_balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(raw_balance))

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(amount)} {self.currency_name}**.",
                color=discord.Color.orange()), delete_after=5)

        valid_choices = {
            "green", "red", "black", "odd", "even", "high", "low",
            "dozen1", "dozen2", "dozen3", "column1", "column2", "column3"
        } | {str(i) for i in range(37)} | {"00"}
        choice = choice.lower()
        if choice not in valid_choices:
            embed = discord.Embed(
                description=(
                    "Invalid bet choice. Please choose a valid bet type "
                    "(`red`, `black`, `odd`, `even`, `high`, `low`, `dozen1`, `dozen2`, `dozen3`, "
                    "`column1`, `column2`, `column3`), a number `0–36`, or `00`."
                ),
                color=discord.Color.red()
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=Decimal(-amount),
                description="Roulette Bet"
            )
        except ValueError as e:
            embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        black_numbers = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}
        green_numbers = {0, "00"}  
        all_numbers = list(range(0, 37)) + ["00"]

        spin_result = await self.fair_choice(user_id, all_numbers)

        is_red = isinstance(spin_result, int) and spin_result in red_numbers
        is_black = isinstance(spin_result, int) and spin_result in black_numbers
        is_green = (spin_result == 0) or (spin_result == "00")
        is_even = isinstance(spin_result, int) and (spin_result % 2 == 0)
        is_odd  = isinstance(spin_result, int) and (spin_result % 2 == 1)
        color_label = "Green" if is_green else ("Red" if is_red else "Black")

        winnings = Decimal(0)
        outcome_description = f"The ball landed on **{color_label} {spin_result}**."

        if choice == "green" and is_green:
            winnings = amount * Decimal(36)
            outcome_description += " You bet on Green."
        elif choice == "red" and is_red:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Red."
        elif choice == "black" and is_black:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Black."
        elif choice == "odd" and is_odd:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Odd."
        elif choice == "even" and is_even:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Even."
        elif choice == "high" and isinstance(spin_result, int) and 19 <= spin_result <= 36:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on High (19–36)."
        elif choice == "low" and isinstance(spin_result, int) and 1 <= spin_result <= 18:
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Low (1–18)."
        elif choice == "dozen1" and isinstance(spin_result, int) and 1 <= spin_result <= 12:
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 1 (1–12)."
        elif choice == "dozen2" and isinstance(spin_result, int) and 13 <= spin_result <= 24:
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 2 (13–24)."
        elif choice == "dozen3" and isinstance(spin_result, int) and 25 <= spin_result <= 36:
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 3 (25–36)."
        elif choice == "column1" and isinstance(spin_result, int) and (spin_result % 3 == 1):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 1."
        elif choice == "column2" and isinstance(spin_result, int) and (spin_result % 3 == 2):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 2."
        elif choice == "column3" and isinstance(spin_result, int) and (spin_result % 3 == 0 and spin_result != 0):

            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 3."
        elif choice.isdigit() and isinstance(spin_result, int) and int(choice) == spin_result:
            winnings = amount * Decimal(36)
            outcome_description += " 🎉 You bet on that number!"
        elif choice == "00" and spin_result == "00":
            winnings = amount * Decimal(36)
            outcome_description += " 🎉 You bet on that number!"
        else:
            outcome_description += " Better luck next time!"

        if winnings > 0:
            revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "roulette", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=winnings,
                    description="Roulette Win"
                )
            except ValueError as e:
                embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return
            result_msg = (
                f"{outcome_description}\n"
                f"You won {self.currency_name} **{await self.formatter(winnings)}**!"
            )
            embed_color = discord.Color.green()
            await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "roulette", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
            result_msg = (
                f"{outcome_description}\n"
                f"You lost your bet of {self.currency_name} **{await self.formatter(amount)}**."
            )
            embed_color = discord.Color.red()
            await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)

        embed = discord.Embed(description=result_msg, color=embed_color)
        embed.set_author(
            name="Roulette",
            icon_url=self.utils.get_avatar_url(ctx.author)
        )
        embed.add_field(name="Your Bet",     value=choice.capitalize(), inline=True)
        embed.add_field(name="Spin Result",  value=f"{color_label} {spin_result}", inline=True)
        await ctx.reply(embed=embed)

    @commands.command(name="double", aliases=["don","doubleornothing"], description="Start a double or nothing game with a specified bet amount.")
    async def double_or_nothing(self, ctx: Context, bet_amount: str):
        """Start a double or nothing game with the specified bet amount."""
        user_id = ctx.author.id

        PF = await self.prove_fairness(user_id)  

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(amount)} {self.currency_name}**.",
                color=discord.Color.orange()),
                delete_after=5)

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-amount,  
                description="Double or Nothing Initial Bet"
            )
        except ValueError as e:
            embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        amount_formatted = await self.formatter(amount)
        embed = discord.Embed(
            description=(
                f"Starting bet: {self.currency_name} **{amount_formatted}**\n"
                f"Winnings: {self.currency_name} **{amount_formatted}**"
            ),
            color=discord.Color.green(),
        )
        embed.set_author(name="Double Or Nothing", icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text="Choose to Double or Cash Out.")

        view = DoubleOrNothingView(
            bot=self.bot,
            initial_user=ctx.author,
            initial_amount=amount,
            winnings=amount,
            currency_name=self.currency_name,
            user_id=user_id,
            PF=PF
        )
        await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed, view=view)

    @commands.command(name="blackjack", aliases=["bj","21"], description="Play a game of blackjack")
    async def blackjack(self, ctx: Context, bet_amount: str):
        """
        Play Blackjack with a fresh deck for each game.
        """
        user_id = ctx.author.id

        PF = await self.prove_fairness(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(amount)} {self.currency_name}**.",
                color=discord.Color.orange()),
                delete_after=5)

        if amount <= 0 or amount > balance:
            embed = discord.Embed(
                description="Invalid bet amount. Please bet within your balance.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-amount,
                description="Blackjack Bet"
            )
        except ValueError as e:
            embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)
            return

        suits = ['♥', '♦', '♣', '♠']
        ranks = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
        deck = [f"{r}{s}" for s in suits for r in ranks]
        await self.fair_shuffle(user_id, deck)

        def draw_card():
            return deck.pop()

        def card_value(card: str) -> int:
            rank = card[:-1]
            if rank in ["J", "Q", "K"]:
                return 10
            if rank == "A":
                return 11
            return int(rank)

        def calculate_score(cards: list[str]) -> int:
            values = [card_value(c) for c in cards]
            total  = sum(values)
            aces   = sum(1 for c in cards if c[:-1] == "A")
            while total > 21 and aces:
                total -= 10
                aces  -= 1
            return total

        async def finalize_game(interaction, player_score, dealer_score):
            nonlocal dealer_cards
            winnings = Decimal(0)

            if player_score > 21:
                outcome = "loss"
                result = f"Bust! You lost {self.currency_name} **{await self.formatter(amount)}**."
                revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "blackjack", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
            elif dealer_score > 21 or player_score > dealer_score or player_score == 21:
                outcome = "win"
                revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "blackjack", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                winnings = Decimal(amount) * Decimal(2.5)
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=winnings,
                        description="Blackjack Win"
                    )
                except ValueError as e:
                    embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                result = f"You win {self.currency_name} **{await self.formatter(winnings)}**!"
            elif player_score == dealer_score:
                outcome = "tie"
                winnings = Decimal(amount)
                try:
                    revealed_seed, new_hash = await self.bot.database.increment_win(user_id, "blackjack", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=winnings,
                        description="Blackjack Tie"
                    )
                except ValueError as e:
                    embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                result = (f"It's a tie! Your bet of {self.currency_name} "
                          f"**{await self.formatter(amount)}** has been returned.")
            else:
                outcome = "loss"
                revealed_seed, new_hash = await self.bot.database.increment_loss(user_id, "blackjack", amount, client_seed=PF['client_seed'], seed_used=None, nonce=PF['nonce'], hash_hex=PF['server_seed_hash'])
                result = f"Dealer wins! You lost {self.currency_name} **{await self.formatter(amount)}**."

            for item in view.children:
                item.disabled = True

            dealer_initial = dealer_cards[0]
            dealer_hits = dealer_cards[1:]
            if dealer_hits:
                hits_text = f"{', '.join([dealer_initial] + dealer_hits)}"
            else:
                hits_text = dealer_initial

            if outcome == "win":
                embed_color = discord.Color.green()
            elif outcome == "tie":
                embed_color = discord.Color.orange()
            else:
                embed_color = discord.Color.red()

            embed = discord.Embed(
                title="Blackjack Result",
                description=(
                    f"Your final cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
                    f"Bots cards: {hits_text} (Total: **{dealer_score}**)\n\n"
                    f"{result}"
                ),
                color=embed_color
            )
            await interaction.edit_original_response(embed=embed, view=view)

        player_cards = [draw_card(), draw_card()]
        dealer_cards = [draw_card()]
        player_score = calculate_score(player_cards)
        dealer_score = calculate_score(dealer_cards)

        embed = discord.Embed(
            title="Blackjack",
            description=(
                f"Your cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
                f"Bots visible card: {dealer_cards[0]}"
            ),
            color=discord.Color.blurple()
        )

        view = ui.View(timeout=120)

        async def hit_callback(interaction: Interaction):
            nonlocal player_score, dealer_score
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)  

            if interaction.user.id != user_id:
                await interaction.response.send_message("This is not your game!", ephemeral=True)
                return

            player_cards.append(draw_card())
            player_score = calculate_score(player_cards)

            if player_score == 21:
                while dealer_score < 17:
                    dealer_cards.append(draw_card())
                    dealer_score = calculate_score(dealer_cards)
                await finalize_game(interaction, player_score, dealer_score)
                return
            elif player_score > 21:
                await finalize_game(interaction, player_score, dealer_score)
                return
            else:
                embed.description = (
                    f"Your cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
                    f"Bots visible card: {dealer_cards[0]}"
                )
                await interaction.edit_original_response(embed=embed, view=view)

        async def stay_callback(interaction: Interaction):
            nonlocal dealer_score
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)  

            if interaction.user.id != user_id:
                await interaction.response.send_message("This is not your game!", ephemeral=True)
                return

            while dealer_score < 17:
                dealer_cards.append(draw_card())
                dealer_score = calculate_score(dealer_cards)

            await finalize_game(interaction, player_score, dealer_score)

        hit_button = Button(label="Hit", style=discord.ButtonStyle.primary)
        hit_button.callback = hit_callback
        stay_button = Button(label="Stay", style=discord.ButtonStyle.secondary)
        stay_button.callback = stay_callback

        view.add_item(hit_button)
        view.add_item(stay_button)
        await self.bot.database.set_cooldown(ctx.author.id, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed, view=view)

    @commands.command(name="poker", aliases=["headsup"], description="Play a game of Heads-Up Poker")
    async def poker(self, ctx: commands.Context, bet_amount: str):
        user_id = ctx.author.id
        PF = await self.prove_fairness(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

        try:
            bet = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=str(e), color=discord.Color.red()), delete_after=5)

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if bet > max_allowed:
            bet = max_allowed
            await ctx.reply(
                embed=discord.Embed(
                    description=(
                        "You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(bet)} {self.currency_name}**"
                    ),
                    color=discord.Color.orange()
                ),
                delete_after=5
            )

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-bet,
                description="Poker Bet"
            )
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()), delete_after=5)

        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)

        suits = ['♥', '♦', '♣', '♠']
        ranks = ['2','3','4','5','6','7','8','9','10','J','Q','K','A']
        deck = [f"{r}{s}" for s in suits for r in ranks]
        await self.fair_shuffle(user_id, deck)

        player_hand = [deck.pop(), deck.pop()]
        bot_hand    = [deck.pop(), deck.pop()]
        community   = [deck.pop() for _ in range(5)]

        description = (
            f"**Your Hole Cards:** {', '.join(player_hand)}\n"
            "Do you want to **Play** or **Fold**?"
        )
        embed = discord.Embed(title="Heads-Up Poker", description=description, color=discord.Color.blurple())

        view = PokerView(self.bot, self, ctx.author, player_hand, bot_hand, community, bet, wallet_id, user_id, PF)
        await ctx.reply(embed=embed, view=view)

    @commands.command(
        name="baccarat",
        aliases=["bac", "punto"],
        description="Bet on Player, Banker, or Tie – provably fair Baccarat!"
    )
    async def baccarat(self, ctx: Context, side: str, amount: str):
        """
        side: 'player', 'banker', or 'tie'
        amount: number | 'all' | etc (handled by self.amount_handler)
        """

        side = side.lower()
        if side not in ("player", "banker", "tie"):
            return await ctx.reply("Valid sides: `player`, `banker`, `tie`")

        uid      = ctx.author.id
        currency = self.currency_name

        PF = await self.prove_fairness(uid)          

        wallet_id = await self.bot.database.get_wallet_id_for_user(uid)
        bal_raw   = await self.bot.database.get_wallet_balance(wallet_id)
        balance   = Decimal(str(bal_raw))

        try:
            stake = await self.amount_handler(amount, balance)
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=str(e), color=discord.Color.red()), delete_after=5)

        max_allowed = await self.bot.database.get_max_gamble_amount(uid)
        if stake > max_allowed:
            stake = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"Bet capped at **{await self.formatter(stake)} {currency}** due to limits.",
                color=discord.Color.orange()), delete_after=5)

        try:
            await self.bot.database.process_treasury_transaction(wallet_id, -stake, "Baccarat Bet")
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()), delete_after=5)

        ranks = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
        card_val = {r:i for i,r in enumerate(ranks, start=1)} | {"10":0,"J":0,"Q":0,"K":0}  
        suits = ["♥","♦","♣","♠"]

        shoe = [f"{r}{s}" for _ in range(6) for s in suits for r in ranks]
        await self.fair_shuffle(uid, shoe)           

        draw = shoe.pop                         

        def total(hand: list[str]) -> int:
            return sum(card_val[c[:-1]] for c in hand) % 10

        player = [draw(), draw()]
        banker = [draw(), draw()]

        p_total = total(player)
        b_total = total(banker)

        player_draws = p_total <= 5
        if player_draws:
            player.append(draw())
            p_total = total(player)

        banker_draws = (
            (b_total <= 2) or
            (b_total == 3 and (not player_draws or player[-1][:-1] != "8")) or
            (b_total == 4 and player_draws and 2 <= card_val[player[-1][:-1]] <= 7) or
            (b_total == 5 and player_draws and 4 <= card_val[player[-1][:-1]] <= 7) or
            (b_total == 6 and player_draws and 6 <= card_val[player[-1][:-1]] <= 7)
        )

        if banker_draws:
            banker.append(draw())
            b_total = total(banker)

        result = "player" if p_total > b_total else "banker" if b_total > p_total else "tie"

        payout_mult = Decimal("0")
        if side == "player" and result == "player":
            payout_mult = Decimal("1")
        elif side == "banker" and result == "banker":
            payout_mult = Decimal("1")               
            if b_total == 6 and len(banker) == 3:    
                payout_mult = Decimal("0.5")
        elif side == "tie" and result == "tie":
            payout_mult = Decimal("8")

        winnings = (stake * payout_mult).quantize(Decimal("0.01"))

        if winnings:
            await self.bot.database.process_treasury_transaction(wallet_id, winnings, "Baccarat Win")
            revealed_seed, new_hash = await self.bot.database.increment_win(uid, "baccarat", stake,
                client_seed=PF["client_seed"], seed_used=None, nonce=PF["nonce"], hash_hex=PF["server_seed_hash"])
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(uid, "baccarat", stake,
                client_seed=PF["client_seed"], seed_used=None, nonce=PF["nonce"], hash_hex=PF["server_seed_hash"])

        def prettify(hand):  
            return " ".join(hand)

        title = f"🏦 Banker {b_total} – 👤 Player {p_total}"
        color = discord.Color.green() if winnings else discord.Color.red()
        desc  = (
            f"**Result:** {result.capitalize()}\n"
            f"**Your Bet:** {side.capitalize()}\n\n"
            f"👤 Player {prettify(player)}\n"
            f"🏦 Banker {prettify(banker)}\n\n"
            f"{'Won' if winnings else 'Lost'} **{await self.formatter(winnings or stake)} {currency}** "
            f"({payout_mult}×)"
        )

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_author(name="Baccarat", icon_url=self.utils.get_avatar_url(ctx.author))

        await self.bot.database.set_cooldown(uid, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed)

    @commands.command(name="hilo", description="Play Hi-Lo - a simple card guessing game!")
    async def hilo(self, ctx: Context, bet_amount: str):
        """Play HiLo - guess if the next card will be higher or lower (Stake-style)"""
        user = ctx.author
        try:
            card_emojis = {
                'A': '<:ace:1361825338539376651>',
                '2': '<:two:1361825398974971904>',
                '3': '<:three:1361825438732779672>',
                '4': '<:four:1361825468784836778>',
                '5': '<:five:1361825513416687697>',
                '6': '<:six:1361825565690036435>',
                '7': '<:seven:1361825612070912302>',
                '8': '<:eight:1361825649383182558>',
                '9': '<:nine:1361825685311721593>',
                '10': '<:ten:1361825715665764514>',
                'J': '<:jack:1361825819478982686>',
                'Q': '<:queen:1361825774461653084>',
                'K': '<:king:1361825747051741475>',
                'back': '<:uncovered:1361825843525194029>'
            }

            user_id = ctx.author.id

            PF = await self.prove_fairness(user_id)  

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = Decimal(str(balance))

            try:
                bet_amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if bet_amount > max_allowed:
                bet_amount = max_allowed
                await ctx.reply(embed=discord.Embed(
                    description=(
                        "You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(bet_amount)} {self.currency_name}**."
                    ),
                    color=discord.Color.orange()),
                    delete_after=5)

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-bet_amount,
                    description="HiLo Bet"
                )

            except ValueError as e:
                embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
            card_values = {card: idx for idx, card in enumerate(cards)}
            current_card = await self.fair_choice(ctx.author.id, cards[1:-1])

            game_state = {
                'multiplier': Decimal('1.0'),
                'current_card': current_card,
                'game_active': True,
                'history': [],
                'bet_amount': bet_amount,
                'message': None,
                'view': None,
                'has_played': False
            }

            def calculate_multiplier(current_card, action):
                current_value = card_values[current_card]
                if action == 'higher':
                    favorable = len([c for c in cards if card_values[c] > current_value])
                    total = len([c for c in cards if card_values[c] >= current_value])
                elif action == 'lower':
                    favorable = len([c for c in cards if card_values[c] < current_value])
                    total = len([c for c in cards if card_values[c] <= current_value])

                if favorable == 0:
                    return Decimal('0')

                probability = Decimal(favorable) / Decimal(total)
                return Decimal('1.0') / probability if probability != 0 else Decimal('0')

            def calculate_probs(card):
                current_value = card_values[card]
                higher = len([c for c in cards if card_values[c] > current_value])
                lower = len([c for c in cards if card_values[c] < current_value])

                higher_prob = round((higher / (len(cards)-1)) * 100)
                lower_prob = round((lower / (len(cards)-1)) * 100)

                higher_prob = max(8, min(92, higher_prob))
                lower_prob = max(8, min(92, lower_prob))

                return higher_prob, lower_prob

            def calculate_profits(card, current_multiplier, bet_amount):
                current_value = card_values[card]
                higher_mult = calculate_multiplier(card, 'higher')
                lower_mult = calculate_multiplier(card, 'lower')

                profit_higher = (bet_amount * current_multiplier * higher_mult) - bet_amount
                profit_lower = (bet_amount * current_multiplier * lower_mult) - bet_amount
                total_profit = (bet_amount * current_multiplier) - bet_amount

                return profit_higher, profit_lower, total_profit

            async def create_embed(status="Playing"):
                higher_prob, lower_prob = calculate_probs(game_state['current_card'])
                profit_higher, profit_lower, total_profit = calculate_profits(
                    game_state['current_card'],
                    game_state['multiplier'],
                    game_state['bet_amount']
                )

                formatted_bet = await self.formatter(game_state['bet_amount'])
                formatted_potential = await self.formatter(game_state['bet_amount'] * game_state['multiplier'])
                formatted_profit_higher = await self.formatter(profit_higher)
                formatted_profit_lower = await self.formatter(profit_lower)
                formatted_total_profit = await self.formatter(total_profit)

                embed = discord.Embed(
                    title=f"<:uncovered:1361825843525194029> HiLo - {status}",
                    color=discord.Color.blurple()
                )

                embed.add_field(
                    name="Current Card",
                    value=f"{card_emojis.get(game_state['current_card'], card_emojis['back'])} **{game_state['current_card']}**",
                    inline=False
                )

                history_display = []
                for card in game_state['history']:
                    history_display.append(f"{card_emojis.get(card, '🃏')} {card}")
                history_display.append(f"{card_emojis.get(game_state['current_card'], '🃏')} {game_state['current_card']}")
                history_display.append(f"{card_emojis['back']}")

                if status == "Playing":
                    embed.add_field(
                        name="Profit on Next Move",
                        value=(
                            f"Profit Higher **({calculate_multiplier(game_state['current_card'], 'higher'):.2f}x):** "
                            f"\n{self.currency_name} {formatted_profit_higher}\n"
                            f"Profit Lower **({calculate_multiplier(game_state['current_card'], 'lower'):.2f}x):** "
                            f"\n{self.currency_name} {formatted_profit_lower}\n"
                            f"Total Profit **({game_state['multiplier']:.2f}x):** "
                            f"\n{self.currency_name} {formatted_total_profit}"
                        ),
                        inline=False
                    )

                embed.add_field(
                    name="History",
                    value=' → '.join(history_display),
                    inline=False
                )

                return embed

            async def end_game(win: bool = False):
                game_state['game_active'] = False
                if game_state['view']:
                    for child in game_state['view'].children:
                        child.disabled = True

                if win:
                    revealed_seed, new_hash = await self.bot.database.increment_win(
                        user_id, "hilo", game_state["bet_amount"],
                        client_seed=PF["client_seed"], seed_used=None, nonce=PF["nonce"], hash_hex=PF["server_seed_hash"]
                    )
                    return game_state['bet_amount'] * game_state['multiplier']
                return None

            async def update_display(interaction: discord.Interaction | None = None):
                if not game_state['game_active']:
                    return

                higher_prob, lower_prob = calculate_probs(game_state['current_card'])
                new_embed = await create_embed()

                for child in game_state['view'].children:
                    if child.custom_id == "higher":
                        child.label = f"Higher ({higher_prob}%)"
                    elif child.custom_id == "lower":
                        child.label = f"Lower ({lower_prob}%)"
                    elif child.custom_id == "cashout":
                        child.label = f"Cash Out {game_state['multiplier']:.2f}x"

                try:

                    if interaction and interaction.response.is_done():
                        await interaction.followup.edit_message(
                            game_state['message'].id,
                            embed=new_embed,
                            view=game_state['view']
                        )
                    elif interaction and not interaction.response.is_done():
                        await interaction.response.edit_message(
                            embed=new_embed,
                            view=game_state['view']
                        )
                    else:
                        await game_state['message'].edit(embed=new_embed, view=game_state['view'])
                except Exception as e:
                    logger.error(f"Error updating display: {e}")

            async def show_result(win: bool):
                result_color = discord.Color.green() if win else discord.Color.red()

                if win:
                    result_text = (
                        f"Cashed out with a **{game_state['multiplier']:.2f}x multiplier**\n"
                        f"Won **{await self.formatter(game_state['bet_amount'] * game_state['multiplier'])}** **{self.currency_name}**"
                    )
                else:
                    result_text = (
                        f"Lost with a possible multiplier of **{game_state['multiplier']:.2f}x**\n"
                        f"Bet: **{await self.formatter(game_state['bet_amount'])}** **{self.currency_name}**"
                    )

                status = "Cashed Out" if win else "Lost"
                embed = await create_embed(status)
                embed.color = result_color
                embed.add_field(
                    name="Result",
                    value=result_text,
                    inline=False
                )

                try:
                    if game_state['message']:
                        await game_state['message'].edit(embed=embed, view=game_state['view'])
                except Exception as e:
                    logger.error(f"Error showing result: {e}")

            async def handle_interaction_response(interaction: Interaction, message=None, ephemeral=True):
                try:
                    if message:
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(message, ephemeral=ephemeral)
                            else:
                                await ctx.reply(message, ephemeral=ephemeral)
                        except discord.errors.InteractionResponded:
                            await ctx.reply(message, ephemeral=ephemeral)
                    else:
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.defer()
                        except discord.errors.InteractionResponded:
                            pass
                except Exception as e:
                    logger.error(f"Error handling interaction response: {e}")
                    return False
                return True

            async def higher_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await interaction.response.send_message("This isn't your game!", ephemeral=True)
                    return

                if not game_state['game_active']:
                    return

                try:
                    await interaction.response.defer()
                    game_state['has_played'] = True
                    game_state['history'].append(game_state['current_card'])
                    next_card = await self.fair_choice(user.id, cards)
                    multiplier_increase = calculate_multiplier(game_state['current_card'], 'higher')

                    current_value = card_values[game_state['history'][-1]]
                    next_value = card_values[next_card]

                    if next_value > current_value:
                        game_state['multiplier'] *= multiplier_increase
                        game_state['current_card'] = next_card
                    elif next_value == current_value:
                        game_state['current_card'] = next_card
                    else:
                        game_state['current_card'] = next_card
                        await end_game(False)
                        await show_result(False)

                    await update_display(interaction)

                except Exception as e:
                    logger.error(f"Error in higher callback: {e}")

            async def lower_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await interaction.response.send_message("This isn't your game!", ephemeral=True)
                    return
                if not game_state['game_active']:
                    return

                try:
                    await interaction.response.defer()
                    game_state['has_played'] = True
                    game_state['history'].append(game_state['current_card'])
                    next_card = await self.fair_choice(user.id, cards)
                    multiplier_increase = calculate_multiplier(game_state['current_card'], 'lower')

                    current_value = card_values[game_state['history'][-1]]
                    next_value = card_values[next_card]

                    if next_value < current_value:
                        game_state['multiplier'] *= multiplier_increase
                        game_state['current_card'] = next_card
                    elif next_value == current_value:
                        game_state['current_card'] = next_card
                    else:
                        game_state['current_card'] = next_card
                        await end_game(False)
                        await show_result(False)

                    await update_display(interaction)

                except Exception as e:
                    logger.error(f"Error in lower callback: {e}")

            async def skip_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await handle_interaction_response(interaction, "This isn't your game!")
                    return
                if not game_state['game_active']:
                    return

                try:
                    await handle_interaction_response(interaction)

                    game_state['history'].append(game_state['current_card'])
                    next_card = await self.fair_choice(user.id, cards)
                    game_state['current_card'] = next_card
                    await update_display(interaction)

                except Exception as e:
                    logger.error(f"Error in skip callback: {e}")

            async def cashout_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await handle_interaction_response(interaction, "This isn't your game!")
                    return
                if not game_state['game_active']:
                    await handle_interaction_response(interaction, "Game is not active!", ephemeral=True)
                    return
                if not game_state['has_played']:
                    await handle_interaction_response(
                        interaction,
                        "🚫 You must make at least one guess (Higher/Lower) before cashing out!",
                        ephemeral=True
                    )
                    return

                try:
                    await handle_interaction_response(interaction)

                    bet = game_state['bet_amount']
                    current_multiplier = game_state['multiplier']
                    winnings = bet * current_multiplier
                    wallet_id = await self.bot.database.get_wallet_id_for_user(interaction.user.id)

                    try:    
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=wallet_id,
                            amount=winnings,
                            description="HiLo Win"
                        )
                    except ValueError as e:
                        embed = discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red())
                        await ctx.reply(embed=embed, delete_after=5)
                        return

                    await end_game(True)
                    await show_result(True)

                except Exception as e:
                    logger.error(f"Error in cashout callback: {e}")
                    await handle_interaction_response(interaction, "An error occurred while cashing out!", ephemeral=True)

            higher_prob, lower_prob = calculate_probs(current_card)

            view = discord.ui.View(timeout=300.0)
            game_state['view'] = view

            view.add_item(discord.ui.Button(label=f"Higher ({higher_prob}%)", style=discord.ButtonStyle.gray, custom_id="higher"))
            view.add_item(discord.ui.Button(label=f"Lower ({lower_prob}%)", style=discord.ButtonStyle.gray, custom_id="lower"))
            view.add_item(discord.ui.Button(label="Skip Card", style=discord.ButtonStyle.gray, custom_id="skip"))
            view.add_item(discord.ui.Button(label=f"Cash Out ({game_state['multiplier']:.2f}x)", style=discord.ButtonStyle.blurple, custom_id="cashout"))

            view.children[0].callback = higher_callback
            view.children[1].callback = lower_callback
            view.children[2].callback = skip_callback
            view.children[3].callback = cashout_callback

            embed = await create_embed()
            game_state['message'] = await ctx.reply(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Error in hilo command: {e}", exc_info=True)
            error_embed = discord.Embed(
                title="⚠️ Error",
                description="An error occurred while starting the game.",
                color=discord.Color.red()
            )
            await ctx.reply(embed=error_embed, delete_after=5)

    @commands.command(name="ladder", aliases=["luckyladder"], description="Play Ladder - a high-risk, high-reward game!")
    async def luckyladder(self, ctx: Context, bet_amount: str):
        """Start climbing the Lucky Ladder with a bet."""
        user_id = ctx.author.id
        PF = await self.prove_fairness(user_id)  

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))
        currency_name = self.currency_name

        try:
            amount = await self.amount_handler(bet_amount, balance)
            amount = Decimal(amount)
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=str(e), color=discord.Color.red()), delete_after=5)

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(embed=discord.Embed(
                description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(amount)} {self.currency_name}**.",
                color=discord.Color.orange()),
                delete_after=5)

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-amount,
                description="Lucky Ladder bet"
            )
        except ValueError as e:
            return await ctx.reply(embed=discord.Embed(description=f"🚫 Transaction failed: {e}", color=discord.Color.red()), delete_after=5)

        self.ladder_games[user_id] = {
            "step": 0,
            "bet": amount,
            "current_multiplier": Decimal("1.00"),
            "start_time": discord.utils.utcnow(),
            "PF": PF
        }

        embed = discord.Embed(
            title="Lucky Ladder",
            description=(
                f"You've started climbing with a bet of {currency_name} **{await self.formatter(amount)}**.\n\n"
                "**How to Play:**\n"
                "• Click **Climb** to attempt going up a step\n"
                "• Higher steps = Higher multipliers but lower success chance\n"
                "• Click **Cash Out** anytime to secure your winnings\n"
                "• Falling = Lose everything!"
            ),
            color=discord.Color.blurple()
        )
        embed.add_field(name="Status", value=(
            "**Current Step:** 0\n"
            "**Success Chance:** 80%\n"
            "**Current Multiplier:** 1.00x\n"
            "**Potential Next Multiplier:** 1.15x"
        ), inline=False)

        view = LadderView(user_id, self)  
        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed, view=view)

    async def climb_ladder(self, interaction: discord.Interaction, user_id: int):
        """Handles the player's climb action with provably-fair RNG."""
        game = self.ladder_games.get(user_id)
        if not game:
            return await interaction.response.send_message("You are not currently playing Lucky Ladder.", ephemeral=True)

        if (discord.utils.utcnow() - game["start_time"]).total_seconds() > 300:
            del self.ladder_games[user_id]
            return await interaction.response.send_message("Your game has expired. Please start a new one.", ephemeral=True)

        PF = game["PF"]
        step = game["step"]
        bet = game["bet"]

        step_probs = {0:80,1:75,2:70,3:65,4:60,5:55,6:50,7:45,8:40,9:35,10:30}
        step_mults = {0:1.00,1:1.15,2:1.30,3:1.50,4:1.75,5:2.05,6:2.40,7:2.80,8:3.25,9:3.75,10:4.20}

        success_chance = step_probs.get(step, 5)  
        threshold = success_chance * 100          

        roll = await self.fair_randbelow(user_id, 10000)

        if roll < threshold:

            game["step"] += 1
            game["current_multiplier"] = Decimal(str(step_mults[game["step"]]))

            current_winnings = bet * game["current_multiplier"]
            next_step = game["step"] + 1
            next_chance = step_probs.get(next_step, 0)
            next_mult = step_mults.get(next_step, "MAX")

            embed = discord.Embed(
                title="Lucky Ladder",
                description=f"🎉 Success! You've climbed to step {game['step']}!",
                color=discord.Color.green()
            )
            embed.add_field(name="Status", value=(
                f"**Current Step:** {game['step']}\n"
                f"**Current Multiplier:** {game['current_multiplier']:.2f}x\n"
                f"**Current Winnings:** {await self.formatter(current_winnings)}\n"
                f"**Next Step Chance:** {next_chance}%\n"
                f"**Next Multiplier:** {next_mult}x"
            ), inline=False)

            if game["step"] == 10:

                wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=current_winnings,
                        description="Lucky Ladder Max Win!"
                    )
                except ValueError as e:
                    return await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()), delete_after=5)
                embed.description = (
                    f"🏆 **Congratulations!!** You've reached the top!\n"
                    f"You won **{await self.formatter(current_winnings)}**!"
                )
                del self.ladder_games[user_id]
                return await interaction.response.edit_message(embed=embed, view=None)

            view = LadderView(user_id, self)
            await interaction.response.edit_message(embed=embed, view=view)

        else:

            revealed_seed, new_hash = await self.bot.database.increment_loss(
                user_id, "ladder", bet,
                client_seed=PF['client_seed'],
                seed_used=None,
                nonce=PF['nonce'],
                hash_hex=PF['server_seed_hash']
            )
            embed = discord.Embed(
                title="Lucky Ladder",
                description=(
                    f"💥 Oh no! You fell from step {step}!\n"
                    f"You lost {await self.formatter(bet)}\n\n"
                    f"Success chance: {success_chance}%\n"
                    f"You rolled: {(roll/100):.2f}"
                ),
                color=discord.Color.red()
            )
            del self.ladder_games[user_id]
            await interaction.response.edit_message(embed=embed, view=None)

    async def cashout(self, interaction: discord.Interaction, user_id: int):
        """Handles the player's cash out action with PF logging."""
        game = self.ladder_games.get(user_id)
        if not game:
            return await interaction.response.send_message("You are not currently playing Lucky Ladder.", ephemeral=True)

        if game["step"] == 0:
            return await interaction.response.send_message("You cannot cash out your initial bet!", ephemeral=True)

        if (discord.utils.utcnow() - game["start_time"]).total_seconds() > 300:
            del self.ladder_games[user_id]
            return await interaction.response.send_message("Your game has expired. Please start a new one.", ephemeral=True)

        PF = game["PF"]
        bet = game["bet"]
        final_reward = bet * game["current_multiplier"]

        revealed_seed, new_hash = await self.bot.database.increment_win(
            user_id, "ladder", bet,
            client_seed=PF['client_seed'],
            seed_used=None,
            nonce=PF['nonce'],
            hash_hex=PF['server_seed_hash']
        )

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=final_reward,
                description=f"Lucky Ladder Cashout (Step {game['step']})"
            )
        except ValueError as e:
            return await interaction.response.send_message(embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()), delete_after=5)

        embed = discord.Embed(
            title="Lucky Ladder",
            description=(
                f"💰 **Cashed Out!**\n\n"
                f"Final Step: {game['step']}\n"
                f"Multiplier: {game['current_multiplier']:.2f}x\n"
                f"Winnings: {await self.formatter(final_reward)}"
            ),
            color=discord.Color.gold()
        )
        del self.ladder_games[user_id]
        await interaction.response.edit_message(embed=embed, view=None)

    @commands.command(name="crash", description="Start a crash game in the current channel.")
    async def crash(self, ctx: Context):
        cid = ctx.channel.id
        if cid in self.active_games and self.active_games[cid].is_running:
            return await ctx.reply("A crash game is already running here.", delete_after=5)

        view = CrashView(self.bot, ctx.author.id, cid)
        self.active_games[cid] = view

        view.game_task = asyncio.create_task(view.start_game(ctx))

        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 10
        )

    def cleanup_after_game(self, channel_id: int):

        self.active_players.discard(channel_id)
        self.active_games.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):

        if not interaction.data or not interaction.data.get("custom_id", "").startswith("crash_"):
            return

        channel_id = interaction.channel_id
        current_game = self.active_games.get(channel_id)
        if not current_game:
            return  

        casino_cog = self.bot.get_cog("Casino")
        custom_id = interaction.data["custom_id"]

        if custom_id == "crash_join":

            formatted_max = await casino_cog.short_formatter(current_game.max_allowed_bet)

            modal = discord.ui.Modal(title="Join Crash Game")

            amount_input = discord.ui.TextInput(
                label="Bet Amount",
                placeholder=f"Enter amount to bet (Max: {formatted_max})",
                required=True
            )
            modal.add_item(amount_input)

            async def on_submit(modal_inter: discord.Interaction):

                uid = modal_inter.user.id
                elapsed = (discord.utils.utcnow() - current_game.start_time).total_seconds()
                if current_game.game_phase != "starting" or elapsed >= 20:
                    return await modal_inter.response.send_message("Too late to join!", ephemeral=True)

                wallet_id = await self.bot.database.get_wallet_id_for_user(uid)
                balance   = await self.bot.database.get_wallet_balance(wallet_id)
                amt = Decimal(amount_input.value)
                if amt <= 0 or amt > balance:
                    return await modal_inter.response.send_message("Invalid amount.", ephemeral=True)

                await self.bot.database.process_treasury_transaction(wallet_id, -amt, "Crash Bet")
                current_game.players[uid] = amt

                current_game.crash_points[uid] = await current_game.generate_crash_point(uid)

                await modal_inter.response.send_message(f"Joined at {amt}", ephemeral=True)
                await current_game.update_game_message()

            modal.on_submit = on_submit
            return await interaction.response.send_modal(modal)

        elif custom_id == "crash_cashout":
            uid = interaction.user.id

            if uid not in current_game.players or uid in current_game.cashed_out:
                return await interaction.response.send_message("Can't cash out.", ephemeral=True)

            bet = current_game.players[uid]
            mult = current_game.current_multiplier
            win = (bet * mult).quantize(Decimal('0.01'))
            wallet = await self.bot.database.get_wallet_id_for_user(uid)
            await self.bot.database.process_treasury_transaction(wallet, win, "Crash Win")

            current_game.cashed_out[uid] = mult
            await interaction.response.send_message(f"Cashed out @ {mult}× for {self.currency_name} **{win}**", ephemeral=True)
            await current_game.update_game_message()

    @commands.command(name="crashadmin", hidden=True)
    @commands.is_owner()
    async def crashadmin(self, ctx: commands.Context, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        view = self.active_games.get(channel.id)
        if not view or not view.is_running:
            return await ctx.send("No active crash game here.")

        embed = discord.Embed(
            title=f"🚀 Crash Status — #{channel.name}", color=discord.Color.blue()
        )
        embed.add_field(
            name="Current Multiplier",
            value=f"{view.current_multiplier:.2f}×", inline=False
        )

        lines = []
        for uid, bet in view.players.items():
            cp = view.crash_points.get(uid, Decimal("0"))
            if uid in view.cashed_out:
                status = f"💰 Cashed @ {view.cashed_out[uid]:.2f}×"
            elif uid in view.crashed_out:
                status = f"💥 Crashed @ {view.crashed_out[uid]:.2f}×"
            else:
                status = "🟢 Playing"
            lines.append(
                f"<@{uid}> — Bet: {bet} | Target: {cp:.2f}× → {status}"
            )

        embed.add_field(
            name="Players",
            value="\n".join(lines),
            inline=False
        )

        admin_id = ctx.author.id
        admin_view = discord.ui.View(timeout=None)

        crash_btn = discord.ui.Button(
            label="Force Crash", style=discord.ButtonStyle.danger
        )
        async def crash_cb(inter: discord.Interaction):
            if inter.user.id != admin_id:
                return await inter.response.send_message("Not allowed.", ephemeral=True)
            for pid in view.players:
                if pid not in view.cashed_out and pid not in view.crashed_out:
                    view.crashed_out[pid] = view.crash_points[pid]
            view.is_running = False
            if view.game_task:
                view.game_task.cancel()
            await view.game_message.edit(embed=await view.make_embed(), view=None)
            await inter.response.send_message("All players have been forced to crash.", ephemeral=True)
        crash_btn.callback = crash_cb
        admin_view.add_item(crash_btn)

        win_btn = discord.ui.Button(
            label="Force Cash Out", style=discord.ButtonStyle.success
        )
        async def win_cb(inter: discord.Interaction):
            if inter.user.id != admin_id:
                return await inter.response.send_message("Not allowed.", ephemeral=True)
            for pid, bet in view.players.items():
                if pid not in view.cashed_out and pid not in view.crashed_out:
                    mult = view.current_multiplier
                    view.cashed_out[pid] = mult
                    win_amt = (bet * mult).quantize(Decimal('0.01'))
                    wallet = await self.bot.database.get_wallet_id_for_user(pid)
                    await self.bot.database.process_treasury_transaction(
                        wallet, win_amt, "Crash Force Payout"
                    )
            view.is_running = False
            if view.game_task:
                view.game_task.cancel()
            await view.game_message.edit(embed=await view.make_embed(), view=None)
            await inter.response.send_message("All players have been forced to cash out.", ephemeral=True)
        win_btn.callback = win_cb
        admin_view.add_item(win_btn)

        await ctx.author.send(embed=embed, view=admin_view)
        await ctx.message.add_reaction("✅")

    @commands.command(name='mines')
    async def mines(self, ctx: commands.Context, num_bombs: int = None, bet_amount: str = None):
        try:
            if num_bombs is None or bet_amount is None:
                embed = discord.Embed(
                    title="Missing required arguments",
                    description="Syntax: !mines (bomb amount) (bet amount)\nUsage: !mines 5 5000",
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
                return

            user_id = ctx.author.id
            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)

            try:
                parsed_bet_amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            bet_amount = parsed_bet_amount

            if not isinstance(num_bombs, int):
                embed = discord.Embed(
                    description='Please provide a valid integer number of bombs.',
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
                return

            if num_bombs < 1 or num_bombs > 24:
                embed = discord.Embed(
                    description='Please provide a number of bombs between 1 and 24.',
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
                return

            if bet_amount <= 0:
                embed = discord.Embed(
                    description='Please provide a valid bet amount greater than 0.',
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
                return

            if bet_amount > balance:
                embed = discord.Embed(
                    description=f'You do not have enough balance to place this bet. Your current balance is **{await self.formatter(balance)}** **{self.currency_name}**.',
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
                return

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-bet_amount,
                    description="Mines game bet"
                )
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed)
                return

            grid_size = 5
            grid = [[' ' for _ in range(grid_size)] for _ in range(grid_size)]
            
            # Provably fair bomb selection aligned with other games
            bomb_positions = await self.fair_sample(user_id, list(range(grid_size * grid_size)), num_bombs)

            # Grid contents are rendered via button emojis and final grid; no prefill needed

            remaining_safe_cells = grid_size * grid_size - num_bombs
            init_multi = await self.bot.database.get_mines_multiplier(num_bombs, 0)
            multiplier = float(init_multi) if init_multi else 1.0

            avatar_url = ctx.author.avatar.url if ctx.author.avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
            embed = discord.Embed(
                title=f"Mines",
                description="Click the gems to avoid bombs!",
                color=discord.Color.gold()
            )
            embed.add_field(name="Number of bombs", value=f"{num_bombs}", inline=False)
            embed.add_field(name="Bet amount", value=f"**{await self.formatter(bet_amount)}** **{self.currency_name}**", inline=False)
            embed.add_field(name="Remaining Gems", value=f"{remaining_safe_cells}", inline=False)
            embed.add_field(name="Multiplier", value=f"x{multiplier:.3g}", inline=False)
            embed.set_author(name=ctx.author.display_name, icon_url=avatar_url)

            try:
                main_message = await ctx.reply(embed=embed)
                # Capture provable fairness context for Mines
                PF = await self.prove_fairness(user_id)
                game_view = MinesView(grid, bomb_positions, main_message, user_id, bet_amount, self.bot, PF)
                await main_message.edit(view=game_view)

                cashout_view = CashoutView(main_message, game_view, user_id, bet_amount, self.bot)
                cashout_msg = await ctx.reply(view=cashout_view)
                game_view.cashout_message = cashout_msg
            except discord.errors.NotFound:
                pass

        except Exception as e:
            embed = discord.Embed(
                description=f"An unexpected error occurred: {str(e)}",
                color=discord.Color.red()
            )
            await ctx.reply(embed=embed)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
    logger.debug("Casino cog initialized successfully")