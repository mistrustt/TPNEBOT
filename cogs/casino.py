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
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, InvalidOperation
from discord import ui, ButtonStyle, Interaction
from discord.ui import View, Button
from discord.ext import commands
from discord.ext.commands import Context
from utils.misc import MiscUtils
from utils.amount import AmountUtils
from collections import defaultdict
from decimal import Decimal
from typing import Sequence, List, Any, Optional
from utils.fairness import ProvenFairness
from textwrap import shorten

logger = logging.getLogger("discord_bot")


class CrashView(discord.ui.LayoutView):
    """Crash game view using Components V2 Container system."""

    def __init__(
        self, bot: commands.Bot, host_id: int, channel_id: int, session_id=None
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.casino: Casino = bot.get_cog("Casino")
        self.host_id = host_id
        self.channel_id = channel_id
        self.session_id = session_id

        self.game_task: asyncio.Task | None = None
        self.is_running = False
        self.start_time: datetime.datetime = None
        self.countdown_end: int = None
        self.game_phase: str = None
        self.current_multiplier = Decimal("1.0")

        self.players: dict[int, Decimal] = {}
        self.crash_points: dict[int, Decimal] = {}
        self.cashed_out: dict[int, Decimal] = {}
        self.crashed_out: dict[int, Decimal] = {}
        self.pf_data: dict[int, dict] = {}  # Provable fairness data per player

        self.join_btn = discord.ui.Button(
            label="Join Crash", style=discord.ButtonStyle.green
        )
        self.join_btn.callback = self.join_callback

        self.cashout_btn = discord.ui.Button(
            label="Cash Out", style=discord.ButtonStyle.red, disabled=True
        )
        self.cashout_btn.callback = self.cashout_callback

        self.game_message: discord.Message = None

    async def build_container(self) -> discord.ui.Container:
        """Build the game container with current state."""
        if self.game_phase == "starting":
            title = "🚀 Crash – Lobby"
            content = f"Click **Join** starting <t:{self.countdown_end}:R>"
            accent_color = 0x57F287  # Green
            show_buttons = True
            buttons_disabled = False
        elif self.game_phase == "running":
            title = "🚀 Crash – Running"
            # Build player list
            lines = []
            for uid, bet in self.players.items():
                cp = self.crash_points.get(uid, Decimal("0.00"))
                if uid in self.cashed_out:
                    status = f"💰 cashed @ {self.cashed_out[uid]:.2f}×"
                elif uid in self.crashed_out:
                    status = f"💥 crashed @ {self.crashed_out[uid]:.2f}×"
                else:
                    val = AmountUtils.round_currency(bet * self.current_multiplier)
                    status = f"🟢 playing → {await self.casino.formatter(val)} {self.casino.currency_name}"
                lines.append(f"<@{uid}> {status}")

            content = f"**Multiplier:** {self.current_multiplier:.2f}×\n\n**Players:**\n" + "\n".join(lines) if lines else f"**Multiplier:** {self.current_multiplier:.2f}×"
            accent_color = 0x5865F2  # Blurple
            show_buttons = True
            active_can_cash = any(
                uid not in self.cashed_out and uid not in self.crashed_out
                for uid in self.players
            )
            buttons_disabled = not active_can_cash
        else:  # ended
            title = "🚀 Crash – Ended"
            lines = []
            for uid, bet in self.players.items():
                cp = self.crash_points.get(uid, Decimal("0.00"))
                if uid in self.cashed_out:
                    status = f"💰 cashed @ {self.cashed_out[uid]:.2f}×"
                elif uid in self.crashed_out:
                    status = f"💥 crashed @ {self.crashed_out[uid]:.2f}×"
                else:
                    status = "🟢 playing"
                status += f" ( could have reached {cp:.2f}× )"
                lines.append(f"<@{uid}> {status}")

            content = "**Players:**\n" + "\n".join(lines) if lines else "No players"
            accent_color = 0xED4245  # Red
            show_buttons = False
            buttons_disabled = True

        # Update button states
        self.join_btn.disabled = self.game_phase != "starting"
        self.cashout_btn.disabled = buttons_disabled if self.game_phase == "running" else True

        if show_buttons:
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"## {title}"),
                discord.ui.TextDisplay(content),
                discord.ui.Separator(),
                discord.ui.ActionRow(self.join_btn, self.cashout_btn),
                accent_color=accent_color
            )
        else:
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"## {title}"),
                discord.ui.TextDisplay(content),
                accent_color=accent_color
            )

        return container

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

        max_allowed = await self.bot.database.get_max_gamble_amount(uid, False)
        formatted_max = await self.casino.short_formatter(max_allowed)

        modal = discord.ui.Modal(title="Join Crash Game")
        amount_input = discord.ui.TextInput(
            label="Bet Amount",
            placeholder=f"e.g. 40m, half, all  (max {formatted_max})",
            required=True,
        )
        modal.add_item(amount_input)

        async def on_submit(sub_int: discord.Interaction):
            wallet = await self.bot.database.get_wallet_id_for_user(uid)
            balance = await self.bot.database.get_wallet_balance(wallet)

            try:
                bet = await self.casino.amount_handler(amount_input.value, balance)
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
                return

            await self.bot.database.process_treasury_transaction(
                wallet, -bet, "Crash Game Bet"
            )

            await self.casino._add_refund(
                self.session_id,
                user_id=uid,
                wallet_id=str(wallet),
                amount=bet,
                reason="crash_bet",
            )

            self.players[uid] = bet
            # Capture PF data before generate_crash_point consumes the nonce
            self.pf_data[uid] = await self.casino.prove_fairness(uid)
            self.crash_points[uid] = await self.generate_crash_point(uid)

            await self.casino._log_game_event(
                self.session_id,
                "join",
                {"user_id": uid, "bet": str(bet)},
            )

            self.cashout_btn.disabled = False
            await sub_int.response.send_message(
                f"You joined with **{await self.casino.formatter(bet)}** {self.casino.currency_name}",
                ephemeral=True,
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
        win = AmountUtils.round_currency(bet * mult)

        wallet = await self.bot.database.get_wallet_id_for_user(uid)
        await self.bot.database.process_treasury_transaction(
            wallet, win, "Crash Game Payout"
        )

        await self.casino._remove_refund(self.session_id, user_id=uid)
        await self.casino._log_game_event(
            self.session_id,
            "cashout",
            {"user_id": uid, "multiplier": str(mult), "win": str(win)},
        )

        # Process game result for rakeback
        await self.casino.process_game_result(uid, "crash", bet)
        pf = self.pf_data.get(uid, {})
        await self.bot.database.increment_win(
            uid, "crash", bet=bet,
            client_seed=pf.get("client_seed"),
            seed_used=None,
            nonce=pf.get("nonce"),
            hash_hex=pf.get("server_seed_hash"),
        )

        self.cashed_out[uid] = self.current_multiplier
        await interaction.response.send_message(
            f"Cashed out @ {mult:.2f}× for **{await self.casino.formatter(win)}** {self.casino.currency_name}",
            ephemeral=True,
        )
        await self.update_game_message()

    async def generate_crash_point(self, user_id: int) -> Decimal:
        """Per-user provable fairness with house edge adjustment"""

        # Get user's house edge (lower for higher VIP tiers)
        house_edge = await self.casino.calculate_house_edge(user_id)

        r = await self.casino.fair_random(user_id)

        # Adjust probability distribution based on house edge
        # Lower house edge = higher chance of better multipliers
        # Base edge is 4%, so scale probabilities accordingly
        edge_factor = float(house_edge) / 0.04  # Ratio relative to standard 4% edge

        # Apply house edge by shifting probability thresholds
        # Higher VIP (lower edge) = more favorable distribution
        thresholds = {
            "low": 0.55 * edge_factor,      # 1-2x multiplier
            "med_low": 0.80 * edge_factor,  # 2-5x multiplier
            "med": 0.95 * edge_factor,      # 5-20x multiplier
            "high": 0.992 * edge_factor,    # 20-100x multiplier
            "vhigh": 0.998 * edge_factor,   # 100-1000x multiplier
        }

        # Cap thresholds at sensible limits
        for key in thresholds:
            thresholds[key] = min(thresholds[key], 0.9999)

        if r < thresholds["low"]:
            v = await self.casino.fair_uniform(user_id, 1.0, 2.0)
        elif r < thresholds["med_low"]:
            v = await self.casino.fair_uniform(user_id, 2.0, 5.0)
        elif r < thresholds["med"]:
            v = await self.casino.fair_uniform(user_id, 5.0, 20.0)
        elif r < thresholds["high"]:
            v = await self.casino.fair_uniform(user_id, 20.0, 100.0)
        elif r < thresholds["vhigh"]:
            v = await self.casino.fair_uniform(user_id, 100.0, 1000.0)
        else:
            v = await self.casino.fair_uniform(user_id, 1000.0, 20000.0)
        return Decimal(str(round(v, 2)))

    async def start_game(self, ctx: commands.Context):
        """Start lobby > run > finish."""
        self.is_running = True
        try:
            now = discord.utils.utcnow()
            self.start_time = now
            self.game_phase = "starting"
            self.current_multiplier = Decimal("1.0")

            self.countdown_end = int((now + datetime.timedelta(seconds=20)).timestamp())

            container = await self.build_container()
            self.clear_items()
            self.add_item(container)
            self.game_message = await ctx.send(view=self)
            await self.casino._update_game_session(
                self.session_id, message_id=self.game_message.id
            )

            while discord.utils.utcnow().timestamp() < self.countdown_end:
                await asyncio.sleep(2)
                await self.update_game_message()

            if not self.players:
                container = discord.ui.Container(
                    discord.ui.TextDisplay("## 🚀 Crash – Cancelled"),
                    discord.ui.TextDisplay("No players joined."),
                    accent_color=0xED4245
                )
                self.clear_items()
                self.add_item(container)
                await self.game_message.edit(view=self)
                await self.casino._end_game_session(
                    self.session_id,
                    outcome="cancelled",
                    reason="no_players",
                    final_state={"phase": "starting"},
                )
                return

            self.game_phase = "running"
            await self.update_game_message()

            running_start = discord.utils.utcnow()
            max_run_seconds = 180

            while len(self.cashed_out | self.crashed_out) < len(self.players):
                if (discord.utils.utcnow() - running_start).total_seconds() >= max_run_seconds:
                    for uid in self.players:
                        if uid not in self.cashed_out and uid not in self.crashed_out:
                            self.crashed_out[uid] = self.crash_points.get(
                                uid, self.current_multiplier
                            )
                            await self.casino._remove_refund(self.session_id, user_id=uid)
                            # Process game result for rakeback
                            await self.casino.process_game_result(uid, "crash", self.players[uid])
                            pf = self.pf_data.get(uid, {})
                            await self.bot.database.increment_loss(
                                uid, "crash", bet=self.players[uid],
                                client_seed=pf.get("client_seed"),
                                seed_used=None,
                                nonce=pf.get("nonce"),
                                hash_hex=pf.get("server_seed_hash"),
                            )
                            await self.casino._log_game_event(
                                self.session_id,
                                "crash",
                                {"user_id": uid, "multiplier": str(self.crashed_out[uid])},
                            )
                    break

                self.current_multiplier += self.calculate_increment()

                for uid, cp in self.crash_points.items():
                    if uid not in self.cashed_out and uid not in self.crashed_out:
                        if self.current_multiplier >= cp:
                            self.crashed_out[uid] = self.crash_points[uid]
                            await self.casino._remove_refund(self.session_id, user_id=uid)
                            # Process game result for rakeback
                            await self.casino.process_game_result(uid, "crash", self.players[uid])
                            pf = self.pf_data.get(uid, {})
                            await self.bot.database.increment_loss(
                                uid, "crash", bet=self.players[uid],
                                client_seed=pf.get("client_seed"),
                                seed_used=None,
                                nonce=pf.get("nonce"),
                                hash_hex=pf.get("server_seed_hash"),
                            )
                            await self.casino._log_game_event(
                                self.session_id,
                                "crash",
                                {"user_id": uid, "multiplier": str(cp)},
                            )
                await self.update_game_message()
                await asyncio.sleep(1)

            self.game_phase = "ended"
            await self.casino._end_game_session(
                self.session_id,
                outcome="completed",
                final_state={
                    "players": {str(k): str(v) for k, v in self.players.items()},
                    "cashed_out": {str(k): str(v) for k, v in self.cashed_out.items()},
                    "crashed_out": {str(k): str(v) for k, v in self.crashed_out.items()},
                },
            )

            container = await self.build_container()
            self.clear_items()
            self.add_item(container)
            await self.game_message.edit(view=self)
        finally:
            self.is_running = False
            self.casino.cleanup_after_game(ctx.channel.id)

    def calculate_increment(self) -> Decimal:
        m = self.current_multiplier
        if m < 2:
            return Decimal("0.1")
        if m < 4:
            return Decimal("0.2")
        if m < 10:
            return Decimal("0.5")
        if m < 20:
            return Decimal("1.0")
        if m < 50:
            return Decimal("2.0")
        if m < 100:
            return Decimal("4.0")
        if m < 200:
            return Decimal("8.0")
        if m < 500:
            return Decimal("12.0")
        if m < 1000:
            return Decimal("15.0")
        if m < 2000:
            return Decimal("25.0")
        if m < 5000:
            return Decimal("50.0")
        return Decimal("100.0")

    async def update_game_message(self):
        """Update the game message with current state."""
        container = await self.build_container()
        self.clear_items()
        self.add_item(container)
        await self.game_message.edit(view=self)


class MinesGridLayout(discord.ui.LayoutView):
    """Layout view for the mines game grid."""

    def __init__(
        self,
        bomb_positions: set,
        user_id: int,
        bet_amount: Decimal,
        bot,
        PF: dict,
        formatted_bet: str,
        currency_name: str,
        session_id=None,
        num_bombs: int = 5,
    ):
        super().__init__(timeout=600)
        self.bomb_positions = bomb_positions
        self.user_id = user_id
        self.bet_amount = bet_amount
        self.bot = bot
        self.PF = PF
        self.session_id = session_id
        self.num_bombs = num_bombs
        self.formatted_bet = formatted_bet
        self.currency_name = currency_name

        self.bomb_emoji = "<:bombs:1278849752301309994>"
        self.gem_emoji = "<:gems:1278849818025918497>"
        self.remaining_safe_cells = 25 - len(bomb_positions)
        self.gems_clicked = 0
        self.clicked_positions: set = set()
        self.game_over = False
        self.error_count = 0

        # Create container
        self.container = MinesContainer(
            num_bombs=num_bombs,
            bet_amount=bet_amount,
            formatted_bet=formatted_bet,
            currency_name=currency_name,
            remaining_safe_cells=self.remaining_safe_cells,
        )
        self.add_item(self.container)

        # Store button references for quick access
        self.grid_buttons: list = []
        self._setup_grid_buttons()

    def _setup_grid_buttons(self):
        """Setup the 5x5 grid buttons within the container's action rows."""
        for row_idx in range(5):
            action_row = self.container.grid_rows[row_idx]
            for col_idx in range(5):
                button = action_row.children[col_idx]
                pos = row_idx * 5 + col_idx
                self.grid_buttons.append(button)

    async def handle_click(self, interaction: Interaction, pos: int):
        """Handle a grid button click."""
        try:
            if self.game_over:
                await interaction.response.send_message(
                    "The game has already ended.", ephemeral=True
                )
                return

            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "This isn't your Mines game.", ephemeral=True
                )
                return

            if pos in self.clicked_positions:
                await interaction.response.send_message(
                    "This gem has already been clicked!", ephemeral=True
                )
                return

            if pos in self.bomb_positions:
                await self._handle_bomb(interaction, pos)
            else:
                await self._handle_safe(interaction, pos)

        except discord.errors.InteractionResponded:
            pass
        except Exception as e:
            logger.error(f"Error in mines game: {str(e)}")
            self.error_count += 1
            if self.error_count >= 3:
                await self._emergency_end(interaction)
            else:
                await self._send_error(interaction)

    async def _handle_bomb(self, interaction: Interaction, pos: int):
        """Handle hitting a bomb."""
        self.game_over = True
        casino: Casino = self.bot.get_cog("Casino")

        # Update button to show bomb
        self.grid_buttons[pos].emoji = self.bomb_emoji
        self.grid_buttons[pos].style = discord.ButtonStyle.danger

        # Disable all buttons
        for button in self.grid_buttons:
            button.disabled = True

        # Reveal all positions
        final_grid = self._create_final_grid()

        # Record loss
        try:
            await self.bot.database.increment_loss(
                self.user_id,
                "mines",
                self.bet_amount,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
        except Exception as e:
            logger.error(f"Failed to record mines loss: {e}")

        # Process game result for rakeback
        await casino.process_game_result(self.user_id, "mines", self.bet_amount)

        # Update container text
        self.container.game_text.content = f"### 💥 BOOM! Game Over\nYou lost **{self.formatted_bet}** {self.currency_name}\n\n{final_grid}"

        # Remove cashout button
        self.container.cashout_row.children[0].disabled = True

        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="loss",
            final_state={"reason": "bomb", "bomb_pos": pos},
        )

        await interaction.response.edit_message(view=self)

    async def _handle_safe(self, interaction: Interaction, pos: int):
        """Handle clicking a safe cell (gem)."""
        casino: Casino = self.bot.get_cog("Casino")

        # Update button to show gem
        self.grid_buttons[pos].emoji = self.gem_emoji
        self.grid_buttons[pos].style = discord.ButtonStyle.success
        self.clicked_positions.add(pos)
        self.remaining_safe_cells -= 1
        self.gems_clicked += 1

        # Calculate new multiplier
        multiplier = await self._calculate_multiplier()

        # Update game stats
        potential_win = await casino.formatter(self.bet_amount * Decimal(str(multiplier)))
        self.container.stats_text.content = (
            f"💎 **Remaining Gems:** {self.remaining_safe_cells}\n"
            f"📈 **Multiplier:** x{multiplier:.3g}\n"
            f"💰 **Potential Win:**{self.currency_name} {potential_win} "
        )

        if self.remaining_safe_cells == 0:
            await self._auto_cashout(interaction)
        else:
            await interaction.response.edit_message(view=self)

    async def _auto_cashout(self, interaction: Interaction):
        """Auto cashout when all gems are cleared."""
        self.game_over = True
        casino: Casino = self.bot.get_cog("Casino")

        multiplier = await self._calculate_multiplier()
        winnings = self.bet_amount * Decimal(str(multiplier))

        # Disable all buttons
        for button in self.grid_buttons:
            button.disabled = True

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=winnings,
            description="Mines game win - all gems cleared",
        )

        # Record win
        try:
            await self.bot.database.increment_win(
                self.user_id,
                "mines",
                self.bet_amount,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
        except Exception as e:
            logger.error(f"Failed to record mines win: {e}")

        # Process game result for rakeback
        await casino.process_game_result(self.user_id, "mines", self.bet_amount)

        final_grid = self._create_final_grid()

        # Update container
        formatted_winnings = await casino.formatter(winnings)
        self.container.game_text.content = (
            f"### 🎉 PERFECT! All Gems Cleared!\n"
            f"You won **{formatted_winnings}** {self.currency_name} at {multiplier:.2f}x!\n\n{final_grid}"
        )
        self.container.cashout_row.children[0].disabled = True

        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="win",
            final_state={"reason": "all_gems", "winnings": str(winnings)},
        )

        await interaction.response.edit_message(view=self)

    async def _calculate_multiplier(self) -> float:
        """Calculate the current multiplier."""
        try:
            multiplier = await self.bot.database.get_mines_multiplier(
                len(self.bomb_positions), self.gems_clicked
            )
            return float(multiplier) if multiplier else 1.0
        except Exception as e:
            logger.error(f"Error calculating multiplier: {str(e)}")
            return 1.0

    def _create_final_grid(self) -> str:
        """Create the final grid display."""
        final_grid = ""
        for row in range(5):
            final_grid += (
                "".join(
                    [
                        self.bomb_emoji
                        if (row * 5 + col) in self.bomb_positions
                        else self.gem_emoji
                        for col in range(5)
                    ]
                )
                + "\n"
            )
        return final_grid

    async def _emergency_end(self, interaction: Interaction):
        """Handle critical errors by refunding."""
        self.game_over = True
        casino: Casino = self.bot.get_cog("Casino")

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=self.bet_amount,
            description="Mines game refund due to error",
        )

        for button in self.grid_buttons:
            button.disabled = True

        self.container.game_text.content = (
            "### ⚠️ Game Error\n"
            "The game encountered an error and has been cancelled. Your bet has been refunded."
        )

        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="cancelled",
            final_state={"reason": "emergency_end"},
        )

        try:
            await interaction.response.edit_message(view=self)
        except:
            pass

    async def _send_error(self, interaction: Interaction):
        """Send an error message."""
        try:
            await interaction.followup.send(
                "An error occurred. Please try again.", ephemeral=True
            )
        except:
            pass

    async def do_cashout(self, interaction: Interaction):
        """Handle cashout button press."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your game!", ephemeral=True
            )
            return

        if self.game_over:
            await interaction.response.send_message(
                "Game already ended!", ephemeral=True
            )
            return

        self.game_over = True
        casino: Casino = self.bot.get_cog("Casino")

        multiplier = await self._calculate_multiplier()
        winnings = self.bet_amount * Decimal(str(multiplier))

        # Disable all buttons
        for button in self.grid_buttons:
            button.disabled = True

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=winnings,
            description="Mines game cashout",
        )

        # Record win
        try:
            await self.bot.database.increment_win(
                self.user_id,
                "mines",
                self.bet_amount,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
        except Exception as e:
            logger.error(f"Failed to record mines win: {e}")

        # Process game result for rakeback
        await casino.process_game_result(self.user_id, "mines", self.bet_amount)

        final_grid = self._create_final_grid()

        # Update container
        formatted_winnings = await casino.formatter(winnings)
        self.container.game_text.content = (
            f"### 💰 Cashed Out!\n"
            f"You won **{formatted_winnings}** {self.currency_name} at {multiplier:.2f}x!\n\n{final_grid}"
        )
        self.container.cashout_row.children[0].disabled = True

        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="win",
            final_state={"reason": "cashout", "winnings": str(winnings)},
        )

        await interaction.response.edit_message(view=self)

    async def force_end(self, *, refund: bool = False):
        """Force end the game."""
        casino: Casino = self.bot.get_cog("Casino")
        self.game_over = True

        for button in self.grid_buttons:
            button.disabled = True

        if refund:
            wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=self.bet_amount,
                description="Mines Refund",
            )

        self.container.cashout_row.children[0].disabled = True

        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="forced_end",
            final_state={"refund": refund},
        )


class MinesContainer(discord.ui.Container):
    """Container for the mines game UI."""

    def __init__(
        self,
        num_bombs: int,
        bet_amount: Decimal,
        formatted_bet: str,
        currency_name: str,
        remaining_safe_cells: int,
    ):
        super().__init__(accent_color=0xFCD34D)  # Gold accent
        self.num_bombs = num_bombs
        self.bet_amount = bet_amount
        self.currency_name = currency_name

        # Title
        self.game_text = discord.ui.TextDisplay(
            f"### 💎 Mines\n"
            f"Avoid the **{num_bombs}** bombs to win!"
        )
        self.add_item(self.game_text)

        # Separator
        self.add_item(discord.ui.Separator())

        # Game stats
        self.stats_text = discord.ui.TextDisplay(
            f"💎 **Remaining Gems:** {remaining_safe_cells}\n"
            f"📈 **Multiplier:** x1.0\n"
            f"💰 **Bet:** {currency_name} {formatted_bet}"
        )
        self.add_item(self.stats_text)

        # Separator
        self.add_item(discord.ui.Separator())

        # Grid rows (5 rows of 5 buttons)
        self.grid_rows: list[discord.ui.ActionRow] = []
        for row_idx in range(5):
            action_row = discord.ui.ActionRow()
            for col_idx in range(5):
                pos = row_idx * 5 + col_idx
                button = MinesGridButton(
                    style=discord.ButtonStyle.secondary,
                    row=row_idx,
                    col=col_idx,
                    pos=pos,
                )
                action_row.add_item(button)
            self.add_item(action_row)
            self.grid_rows.append(action_row)

        # Separator before cashout
        self.add_item(discord.ui.Separator())

        # Cashout button row
        self.cashout_row = discord.ui.ActionRow()
        self.cashout_button = MinesCashoutButton()
        self.cashout_row.add_item(self.cashout_button)
        self.add_item(self.cashout_row)


class MinesGridButton(discord.ui.Button):
    """A single button in the mines grid."""

    def __init__(self, style: discord.ButtonStyle, row: int, col: int, pos: int):
        super().__init__(
            label="\u200b",
            style=style,
            row=row,
            custom_id=f"mines_{pos}",
        )
        self.grid_pos = pos

    async def callback(self, interaction: Interaction):
        # Find the parent layout view
        layout_view: MinesGridLayout = self.view
        while hasattr(layout_view, 'parent') and layout_view.parent:
            layout_view = layout_view.parent

        if isinstance(self.view, MinesGridLayout):
            await self.view.handle_click(interaction, self.grid_pos)
        else:
            await interaction.response.send_message(
                "Game state error. Please start a new game.",
                ephemeral=True
            )


class MinesCashoutButton(discord.ui.Button):
    """Cashout button for mines game."""

    def __init__(self):
        super().__init__(
            label="💰 Cashout",
            style=discord.ButtonStyle.success,
            custom_id="mines_cashout",
        )

    async def callback(self, interaction: Interaction):
        # Navigate to the layout view
        view = self.view
        while hasattr(view, 'parent') and view.parent:
            view = view.parent

        if isinstance(view, MinesGridLayout):
            await view.do_cashout(interaction)
        else:
            await interaction.response.send_message(
                "Game state error. Please start a new game.",
                ephemeral=True
            )


class DoubleOrNothingView(discord.ui.LayoutView):
    """Double or Nothing game view using Components V2 Container system."""

    def __init__(
        self,
        bot,
        initial_user,
        initial_amount,
        winnings,
        currency_name,
        user_id,
        PF,
        session_id=None,
    ):
        super().__init__(timeout=60)
        self.bot = bot
        self.casino: Casino = bot.get_cog("Casino")
        self.initial_user = initial_user
        self.initial_amount = initial_amount
        self.winnings = winnings
        self.currency_name = currency_name
        self.user_id = user_id
        self.rounds = 0
        self.PF = PF
        self.session_id = session_id
        self.message: discord.Message | None = None

        # Create buttons
        self.double_button = discord.ui.Button(
            label="Double", style=discord.ButtonStyle.green
        )
        self.double_button.callback = self._double_callback

        self.cashout_button = discord.ui.Button(
            label="Cash Out", style=discord.ButtonStyle.red
        )
        self.cashout_button.callback = self._cashout_callback

    async def build_initial_container(self):
        """Build the initial game container. Must be called after __init__."""
        formatted_bet = await self.casino.formatter(self.initial_amount)
        formatted_winnings = await self.casino.formatter(self.winnings)
        content = (
            f"**Starting bet:** {self.currency_name} **{formatted_bet}**\n"
            f"**Winnings:** {self.currency_name} **{formatted_winnings}**"
        )
        self._build_container(accent_color=0x57F287, content=content)  # Green

    def _build_container(self, accent_color: int, content: str, show_buttons: bool = True):
        """Build the game container with current state."""
        if show_buttons:
            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🎲 Double Or Nothing"),
                discord.ui.TextDisplay(content),
                discord.ui.Separator(),
                discord.ui.ActionRow(self.double_button, self.cashout_button),
                accent_color=accent_color
            )
        else:
            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🎲 Double Or Nothing"),
                discord.ui.TextDisplay(content),
                accent_color=accent_color
            )

        # Clear existing items and add new container
        self.clear_items()
        self.add_item(container)

    async def _double_callback(self, interaction: discord.Interaction):
        """Handle double button click."""
        if interaction.user.id != self.initial_user.id:
            await interaction.response.send_message(
                "This game is not for you!", ephemeral=True
            )
            return

        # Process game result for rakeback
        await self.casino.process_game_result(self.user_id, "double", self.initial_amount)

        success = await self.casino.fair_choice(self.user_id, [True, False])
        if success:
            self.winnings = Decimal(self.winnings) * 2
            revealed_seed, new_hash = await self.bot.database.increment_win(
                self.user_id,
                "double",
                self.initial_amount,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
            self.rounds += 1

            # Format winnings
            formatted_winnings = await self.casino.formatter(self.winnings)
            content = (
                f"**Current Winnings:** {self.currency_name} **{formatted_winnings}**\n"
                f"Would you like to double again?"
            )

            self._build_container(accent_color=0x57F287, content=content)  # Green
            await interaction.response.edit_message(view=self)

            await self.casino._log_game_event(
                self.session_id,
                "double",
                {"round": self.rounds, "winnings": str(self.winnings)},
            )
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                self.user_id,
                "double",
                self.initial_amount,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )

            # Build loss container (no buttons)
            self._build_container(
                accent_color=0xED4245,  # Red
                content="You lost everything! Better luck next time.",
                show_buttons=False
            )
            await interaction.response.edit_message(view=self)

            await self.casino._remove_refund(self.session_id, user_id=self.user_id)
            await self.casino._end_game_session(
                self.session_id,
                outcome="loss",
                final_state={"reason": "double_loss", "rounds": self.rounds},
            )
            self.stop()

    async def _cashout_callback(self, interaction: discord.Interaction):
        """Handle cash out button click."""
        if interaction.user.id != self.initial_user.id:
            await interaction.response.send_message(
                "This game is not for you!", ephemeral=True
            )
            return

        formatted_winnings = await self.casino.formatter(self.winnings)

        # Build cashout container (no buttons)
        self._build_container(
            accent_color=0x5865F2,  # Blurple
            content=f"You cashed out with **{formatted_winnings}** **{self.currency_name}**!",
            show_buttons=False
        )
        await interaction.response.edit_message(view=self)

        wallet_id = await self.bot.database.get_wallet_id_for_user(self.initial_user.id)
        await self.bot.database.process_treasury_transaction(
            wallet_id=wallet_id,
            amount=self.winnings,
            description="Double or Nothing Winnings",
        )
        await self.casino._remove_refund(self.session_id, user_id=self.user_id)
        await self.casino._end_game_session(
            self.session_id,
            outcome="win",
            final_state={"reason": "cashout", "winnings": str(self.winnings)},
        )
        self.stop()

    async def force_end(self, *, refund: bool = False):
        """Force end the game, optionally refunding the bet."""
        # Disable buttons
        self.double_button.disabled = True
        self.cashout_button.disabled = True
        self._build_container(
            accent_color=0xFEE75C,  # Yellow
            content="Game ended (timeout or forced).",
            show_buttons=False
        )

        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        if refund:
            wallet_id = await self.bot.database.get_wallet_id_for_user(
                self.initial_user.id
            )
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=self.initial_amount,
                description="Double or Nothing Refund",
            )
        await self.casino._remove_refund(self.session_id, user_id=self.user_id)
        await self.casino._end_game_session(
            self.session_id,
            outcome="forced_end",
            final_state={"reason": "force_end", "refund": refund},
        )
        self.stop()

    async def on_timeout(self):
        """Handle view timeout."""
        try:
            self.double_button.disabled = True
            self.cashout_button.disabled = True
            self._build_container(
                accent_color=0xFEE75C,  # Yellow
                content="Game timed out. Your bet has been refunded.",
                show_buttons=False
            )
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass
        finally:
            self.stop()


class PokerView(View):
    def __init__(
        self,
        bot,
        cog,
        initial_user,
        player_hand,
        bot_hand,
        community_cards,
        bet,
        wallet_id,
        user_id,
        PF,
        session_id=None,
    ):
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
        self.session_id = session_id
        self.message: discord.Message | None = None

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
            window = unique[i : i + 5]
            if window[0] - window[4] == 4:
                is_straight = True
                top_straight = window[0]
                break

        if not is_straight and set([14, 5, 4, 3, 2]).issubset(unique):
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
            return await interaction.response.send_message(
                "Not your game!", ephemeral=True
            )

        player_rank = self.evaluate_hand(self.player_hand + self.community)
        bot_rank = self.evaluate_hand(self.bot_hand + self.community)
        player_wins = player_rank > bot_rank

        if player_wins:
            revealed_seed, new_hash = await self.bot.database.increment_win(
                self.user_id,
                "poker",
                self.bet,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
            payout = self.bet * Decimal("2")
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id, amount=payout, description="Poker Win"
            )
            formatted = await self.cog.formatter(payout)
            result = f"You win! You won **{formatted}**."
            color = discord.Color.green()
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                self.user_id,
                "poker",
                self.bet,
                client_seed=self.PF["client_seed"],
                seed_used=None,
                nonce=self.PF["nonce"],
                hash_hex=self.PF["server_seed_hash"],
            )
            formatted = await self.cog.formatter(self.bet)
            result = f"You lose! You lost **{formatted}**."
            color = discord.Color.red()

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
            color=color,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        casino: Casino = self.bot.get_cog("Casino")
        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="win" if player_wins else "loss",
            final_state={"result": "win" if player_wins else "loss"},
        )

    async def fold_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Not your game!", ephemeral=True
            )

        revealed_seed, new_hash = await self.bot.database.increment_loss(
            self.user_id,
            "poker",
            self.bet,
            client_seed=self.PF["client_seed"],
            seed_used=None,
            nonce=self.PF["nonce"],
            hash_hex=self.PF["server_seed_hash"],
        )
        formatted = await self.cog.formatter(self.bet)
        result = f"You folded. You lost **{formatted}**."

        self.play_button.disabled = True
        self.fold_button.disabled = True
        embed = discord.Embed(
            title="Heads-Up Poker — Folded",
            description=result,
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        casino: Casino = self.bot.get_cog("Casino")
        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="loss",
            final_state={"result": "fold"},
        )

    async def force_end(self, *, refund: bool = False):
        casino: Casino = self.bot.get_cog("Casino")
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        if refund:
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id,
                amount=self.bet,
                description="Poker Refund",
            )
        await casino._remove_refund(self.session_id, user_id=self.user_id)
        await casino._end_game_session(
            self.session_id,
            outcome="forced_end",
            final_state={"reason": "force_end", "refund": refund},
        )


class LadderView(View):
    def __init__(self, user_id, cog):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.cog = cog
        self.message: discord.Message | None = None

    @discord.ui.button(label="Climb", style=discord.ButtonStyle.green)
    async def climb_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your game!", ephemeral=True
            )
            return

        await self.cog.climb_ladder(interaction, self.user_id)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.red)
    async def cashout_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your game!", ephemeral=True
            )
            return

        await self.cog.cashout(interaction, self.user_id)


class SlotsButtons(ui.ActionRow):
    """
    ActionRow subclass containing all slots game buttons.
    Uses Discord Components V2 pattern with @ui.button decorators.
    """
    
    def __init__(self, parent_view):
        # Store reference to parent view for accessing game state
        self.__view = parent_view
        super().__init__()
    
    @ui.button(label="Spin Again", style=ButtonStyle.primary, emoji="🎰")
    async def spin_again_btn(self, interaction: Interaction, button: ui.Button):
        """Handle spin again button click."""
        await self.__view._spin_again_callback(interaction)
    
    @ui.button(label="Paytable", style=ButtonStyle.secondary, emoji="📋")
    async def paytable_btn(self, interaction: Interaction, button: ui.Button):
        """Handle paytable button click."""
        await self.__view._paytable_callback(interaction)
    
    def update_states(self):
        """Update button states based on current game state."""
        view = self.__view
        # Spin Again button - disabled only while spinning
        # Users can continue playing with balance even without free spins
        self.spin_again_btn.disabled = view.is_spinning
        
        # Paytable always available unless spinning
        self.paytable_btn.disabled = view.is_spinning


class SlotsView(discord.ui.LayoutView):
    """
    Interactive View for the slots game using Components V2 Container system.
    
    Features:
    - 3-frame spin animation (rate-limit safe)
    - Free spins tracking with 2x multiplier
    - Interactive buttons: Spin Again, Bet +/-, Paytable
    - Components V2 layout with Container-based design
    """
    
    def __init__(self, cog, user_id: int, bet: Decimal, grid: list, payline_wins: list,
                    scatter_count: int, winnings: Decimal, free_spins: int = 0,
                    multiplier: Decimal = Decimal("1"), pf_data: dict = None,
                    scatter_payout: Decimal = Decimal("0"),
                    verification: dict = None):
        super().__init__(timeout=300.0)  # 5 minute timeout
        self.cog = cog
        self.user_id = user_id
        self.bot = cog.bot
        self.bet = bet
        self.grid = grid
        self.payline_wins = payline_wins
        self.scatter_count = scatter_count
        self.scatter_payout = scatter_payout
        self.winnings = winnings
        self.free_spins = free_spins
        self.multiplier = multiplier
        self.pf_data = pf_data or {}
        self.verification = verification or {}
        
        # State tracking
        self.is_spinning = False
        self.lock = asyncio.Lock()
        self.message = None
        self.has_played = False  # Track if game has been spun at least once
        
        # Animation frames (emojis for spinning effect)
        self.spin_frames = ["🎰", "🎲", "🎯", "🎪", "🌟"]
        
        # Create ActionRow with buttons (Components V2 pattern)
        self.buttons = SlotsButtons(self)
    
    def _update_button_states(self):
        """Update button states based on current game state."""
        self.buttons.update_states()
    
    def _get_winning_set(self) -> set:
        """Get set of (row, col) coordinates for winning positions."""
        winning_set = set()
        for line_data in self.payline_wins:
            for coord in line_data.get("coordinates", []):
                winning_set.add(coord)
        return winning_set
    
    def _format_grid_display(self) -> str:
        """Format the grid for display with winning highlights."""
        winning_set = self._get_winning_set()
        lines = []
        
        for row in range(4):
            row_display = []
            for col in range(5):
                sym = self.grid[col][row]
                sym_data = self.cog.SLOTS_SYMBOLS.get(sym, {})
                emoji = sym_data.get("emoji", "❓")
                
                if (row, col) in winning_set:
                    row_display.append(f"【{emoji}】")
                else:
                    row_display.append(f"｜{emoji}｜")
            
            lines.append("".join(row_display))
        
        return "\n".join(lines)
    
    def _format_animation_frame(self, frame_num: int) -> str:
        """Format an animation frame with spinning effect."""
        lines = []
        spin_emoji = self.spin_frames[frame_num % len(self.spin_frames)]
        
        for row in range(4):
            row_display = []
            for col in range(5):
                # Show random symbols during animation
                if frame_num < 2:
                    row_display.append(f"｜{spin_emoji}｜")
                else:
                    # Final frame shows actual result
                    sym = self.grid[col][row]
                    sym_data = self.cog.SLOTS_SYMBOLS.get(sym, {})
                    emoji = sym_data.get("emoji", "❓")
                    winning_set = self._get_winning_set()
                    if (row, col) in winning_set:
                        row_display.append(f"【{emoji}】")
                    else:
                        row_display.append(f"｜{emoji}｜")
            
            lines.append("".join(row_display))
        
        return "\n".join(lines)
    
    async def _build_container(self, is_animation: bool = False, frame_num: int = 0) -> discord.ui.Container:
        """Build the Components V2 container for the current state."""
        container = discord.ui.Container(accent_color=discord.Color.gold() if self.winnings > 0 else discord.Color.dark_theme())
        
        # Title with free spins indicator
        if self.free_spins > 0:
            title = f"🎰 SLOTS - FREE SPINS: {self.free_spins} remaining! (2× Multiplier)"
        else:
            title = "🎰 SLOTS"
        
        container.add_item(discord.ui.TextDisplay(f"## {title}"))
        container.add_item(discord.ui.Separator())
        
        # Grid display
        if is_animation:
            grid_text = self._format_animation_frame(frame_num)
        else:
            grid_text = self._format_grid_display()
        
        container.add_item(discord.ui.TextDisplay(f"```\n{grid_text}\n```"))
        
        # Results section
        if not is_animation:
            container.add_item(discord.ui.Separator())
            
            # Win summary
            if self.winnings > 0:
                win_text = f"💰 **WIN: {await self.cog.formatter(self.winnings)}**"
                if self.multiplier > 1:
                    win_text += f" (×{self.multiplier})"
                container.add_item(discord.ui.TextDisplay(win_text))
            elif self.has_played:
                # Show loss message only after game has been played
                loss_text = f"💔 **No Win**\nYou lost **{await self.cog.formatter(self.bet)}**"
                container.add_item(discord.ui.TextDisplay(loss_text))
            
            # Winning lines
            if self.payline_wins:
                lines_text = "**Winning Lines:**\n"
                for line_data in self.payline_wins[:5]:  # Show max 5 lines
                    lines_text += f"• {line_data['payline_name']}: {line_data['symbol_emoji']} ×{line_data['count']} = {line_data['payout']}×\n"
                if len(self.payline_wins) > 5:
                    lines_text += f"• ...and {len(self.payline_wins) - 5} more"
                container.add_item(discord.ui.TextDisplay(lines_text))
            
            # Scatter wins
            if self.scatter_count >= 3:
                scatter_text = f"🌟 **Scatter Bonus:** {self.scatter_count} Scatters = {await self.cog.formatter(self.scatter_payout)}"
                container.add_item(discord.ui.TextDisplay(scatter_text))
            
            # Free spins won
            if self.free_spins > 0 and self.scatter_count >= 3:
                container.add_item(discord.ui.TextDisplay(f"🎁 **Free Spins Won: {self.free_spins}**"))
            
            # Bet info
            container.add_item(discord.ui.TextDisplay(f"**Bet:** {await self.cog.formatter(self.bet)}"))
        
        return container
    
    async def _spin_again_callback(self, interaction: discord.Interaction):
        """Handle spin again button click."""
        async with self.lock:
            if self.is_spinning:
                await interaction.response.send_message("Already spinning!", ephemeral=True)
                return
            
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't your game!", ephemeral=True)
                return
            
            self.is_spinning = True
            self._update_button_states()
        
        try:
            # Deduct bet (or use free spin)
            if self.free_spins > 0:
                self.free_spins -= 1
                # Free spins don't deduct from balance
            else:
                wallet_id = await self.bot.database.get_wallet_id_for_user(self.user_id)
                balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))
                
                if balance < self.bet:
                    await interaction.response.send_message(
                        f"Insufficient balance! You have {await self.cog.formatter(balance)}",
                        ephemeral=True
                    )
                    self.is_spinning = False
                    self._update_button_states()
                    return
                
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-self.bet,
                    description="Slots Bet"
                )
            
            # Run animation
            await self._run_animation(interaction)
            
            # Get user ID and provable fairness data for this spin
            user_id = interaction.user.id
            PF = await self.cog.prove_fairness(user_id)
            
            # Generate new grid
            grid, verification = await self.cog._async_generate_spin_grid(user_id, PF["nonce"])
            
            # Evaluate results
            winning_lines = self.cog._evaluate_paylines(grid)
            scatter_count = self.cog._count_scatters(grid)
            scatter_payout = self.cog._calculate_scatter_payout(scatter_count)
            
            # Calculate winnings (payout multipliers * bet amount)
            line_winnings = sum(Decimal(str(line["payout"])) for line in winning_lines)
            total_multiplier = line_winnings + scatter_payout
            total_winnings = self.bet * total_multiplier * self.multiplier
            
            # Apply house edge
            if total_winnings > 0:
                total_winnings = AmountUtils.round_currency(total_winnings * Decimal("0.98"))
            
            # Award winnings
            if total_winnings > 0:
                wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=total_winnings,
                    description="Slots Win"
                )
            
            # Check for free spins trigger
            new_free_spins = 0
            if scatter_count >= 3:
                if scatter_count == 3:
                    new_free_spins = 10
                elif scatter_count == 4:
                    new_free_spins = 15
                else:  # 5 scatters
                    new_free_spins = 20
                self.free_spins += new_free_spins
            
            # Update state
            self.grid = grid
            self.winnings = total_winnings
            self.payline_wins = winning_lines
            self.scatter_count = scatter_count
            self.scatter_payout = scatter_payout
            self.has_played = True  # Mark game as played
            
            # Build final container and add to view
            container = await self._build_container()
            
            # Clear old items and add new container with buttons
            self.clear_items()
            container.add_item(self.buttons)
            self.add_item(container)
            
            # Re-enable buttons before sending to Discord
            self.is_spinning = False
            self._update_button_states()
            
            await interaction.edit_original_response(view=self)
            
        except Exception:
            # Ensure buttons are re-enabled on error
            self.is_spinning = False
            self._update_button_states()
            raise
    
    async def _paytable_callback(self, interaction: discord.Interaction):
        """Show the paytable."""
        embed = discord.Embed(
            title="🎰 Slots Paytable",
            color=discord.Color.gold()
        )
        
        # Group symbols by tier
        symbols_by_tier = {"special": [], "high": [], "medium": [], "low": []}
        for sym, data in self.cog.SLOTS_SYMBOLS.items():
            tier = data.get("tier", "low")
            if tier in symbols_by_tier:
                symbols_by_tier[tier].append((sym, data))
        
        # Sort each tier by highest payout
        for tier in symbols_by_tier:
            symbols_by_tier[tier].sort(key=lambda x: max(x[1].get("payouts", {}).get(5, 0), x[1].get("payouts", {}).get(3, 0)), reverse=True)
        
        # Special symbols (Wild & Scatter)
        special_lines = []
        for sym, data in symbols_by_tier["special"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            
            if data.get("wild") or data.get("substitutes"):
                special_lines.append(f"{emoji} **{name}** (Wild)")
                special_lines.append(f"└ Substitutes for all symbols except Scatter")
                special_lines.append(f"└ 3× = {payouts.get(3, 0)}× | 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
            elif data.get("scatter"):
                special_lines.append(f"{emoji} **{name}** (Scatter)")
                special_lines.append(f"└ Pays anywhere on the reels!")
                special_lines.append(f"└ 3× = {payouts.get(3, 0)}× | 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if special_lines:
            embed.add_field(name="✨ Special Symbols", value="\n".join(special_lines), inline=False)
        
        # High value symbols
        high_lines = []
        for sym, data in symbols_by_tier["high"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            high_lines.append(f"{emoji} **{name}**")
            high_lines.append(f"└ 3× = {payouts.get(3, 0)}× | 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if high_lines:
            embed.add_field(name="💎 High Value", value="\n".join(high_lines), inline=True)
        
        # Medium value symbols
        medium_lines = []
        for sym, data in symbols_by_tier["medium"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            medium_lines.append(f"{emoji} **{name}**")
            medium_lines.append(f"└ 3× = {payouts.get(3, 0)}× | 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if medium_lines:
            embed.add_field(name="⭐ Medium Value", value="\n".join(medium_lines), inline=True)
        
        # Low value symbols
        low_lines = []
        for sym, data in symbols_by_tier["low"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            low_lines.append(f"{emoji} **{name}**")
            low_lines.append(f"└ 3× = {payouts.get(3, 0)}× | 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if low_lines:
            embed.add_field(name="🍋 Low Value", value="\n".join(low_lines), inline=True)
        
        # Game info
        embed.add_field(
            name="📋 Game Info",
            value="**Paylines:** 20 fixed lines\n**Free Spins:** 3+ Scatters → 10-20 spins with 2× multiplier\n**House Edge:** 2%",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def _run_animation(self, interaction: discord.Interaction):
        """Run the spin animation (3 frames, 2 edits)."""
        # Frame 1: Show spinning immediately
        container1 = await self._build_container(is_animation=True, frame_num=0)
        self.clear_items()
        self.add_item(container1)
        await interaction.response.edit_message(view=self)
        
        # Frame 2: Continue spinning at 1.5 seconds
        await asyncio.sleep(1.5)
        container2 = await self._build_container(is_animation=True, frame_num=1)
        self.clear_items()
        self.add_item(container2)
        await interaction.edit_original_response(view=self)
        
        # Frame 3: Show result at 3 seconds
        await asyncio.sleep(1.5)
    
    async def on_timeout(self):
        """Handle view timeout - disable all buttons."""
        self.buttons.spin_again_btn.disabled = True
        self.buttons.paytable_btn.disabled = True
        
        if self.message:
            try:
                container = await self._build_container()
                self.clear_items()
                container.add_item(self.buttons)
                self.add_item(container)
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

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
        self.wins = sum(
            1 for o in lc_outcomes if o in ("win", "won", "victory", "success")
        )
        self.losses = sum(
            1 for o in lc_outcomes if o in ("loss", "lost", "defeat", "failure")
        )
        self.ties = self.total_games - (self.wins + self.losses)

        self.prev_btn: discord.ui.Button | None = next(
            (c for c in self.children if getattr(c, "label", "").startswith("⬅")), None
        )
        self.next_btn: discord.ui.Button | None = next(
            (c for c in self.children if getattr(c, "label", "").startswith("➡")), None
        )
        self._update_button_states()

    def _update_button_states(self):
        if self.prev_btn:
            self.prev_btn.disabled = self.current_page == 0
        if self.next_btn:
            self.next_btn.disabled = (self.current_page + 1) * self.per_page >= len(
                self.history
            )

    @discord.ui.button(label="⬅ Previous", style=discord.ButtonStyle.gray, emoji="⬅️")
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                "This isn't your history!", ephemeral=True
            )

        self.current_page = max(0, self.current_page - 1)
        self._update_button_states()
        new_embed = await self.get_page_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(label="Next ➡", style=discord.ButtonStyle.gray, emoji="➡️")
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message(
                "This isn't your history!", ephemeral=True
            )

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
            description=f"📊 Total: **{self.total_games}** • 🏆 Wins: **{self.wins}** • 💀 Losses: **{self.losses}** • 🔁 Ties: **{self.ties}**",
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
            timestamp = (
                created_at.strftime("%Y-%m-%d %H:%M") if created_at else "Unknown time"
            )
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
        embed.set_footer(
            text=f"Page {self.current_page + 1}/{total_pages} • Showing {len(page_items)} of {len(self.history)} entries"
        )

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
        self.session_registry = {}
        self.cooldowns = {}
        self.defaultpot = 10000.0
        self.roll_history = defaultdict(list)
        self.games = [
            "gamble",
            "supergamble",
            "dice",
            "slots",
            "blackjack",
            "roulette",
            "mines",
            "double",
            "ladder",
            "poker",
            "crash",
            "hilo",
            "baccarat",
            "keno",
        ]
        self.fair = ProvenFairness()

        # ========== SLOTS REDESIGN: Symbol Set & Grid Structure ==========
        # 5x4 grid (5 reels × 4 rows = 20 positions)
        # Symbol tiers: High (rare), Medium, Low (common), Special (Wild, Scatter)
        self.SLOTS_SYMBOLS = {
            # High-value symbols (rare, high payouts)
            "diamond": {"emoji": "💎", "name": "Diamond", "tier": "high", "payouts": {5: 100, 4: 25, 3: 8}},
            "seven": {"emoji": "7️⃣", "name": "Lucky Seven", "tier": "high", "payouts": {5: 75, 4: 20, 3: 6}},
            "bell": {"emoji": "🔔", "name": "Bell", "tier": "high", "payouts": {5: 50, 4: 15, 3: 5}},
            # Medium-value symbols
            "star": {"emoji": "⭐", "name": "Star", "tier": "medium", "payouts": {5: 30, 4: 10, 3: 3}},
            "cherry": {"emoji": "🍒", "name": "Cherry", "tier": "medium", "payouts": {5: 20, 4: 8, 3: 2.5}},
            # Low-value symbols (common, frequent small wins)
            "lemon": {"emoji": "🍋", "name": "Lemon", "tier": "low", "payouts": {5: 15, 4: 5, 3: 1.5}},
            "slot_machine": {"emoji": "🎰", "name": "Slot Machine", "tier": "low", "payouts": {5: 10, 4: 4, 3: 1}},
            # Special symbols
            "wild": {"emoji": "🃏", "name": "Wild", "tier": "special", "payouts": {5: 500, 4: 100, 3: 25}, "substitutes": True},
            "scatter": {"emoji": "💰", "name": "Scatter", "tier": "special", "payouts": {5: 50, 4: 20, 3: 5, "scatter_pays": True}},
        }

        # ========== SLOTS REDESIGN: Payline Patterns ==========
        # 10 fixed paylines for 5x4 grid
        # Each payline is a list of (row, col) coordinates for matching left-to-right
        # Grid coordinates: rows 0-3 (top to bottom), cols 0-4 (left to right)
        self.SLOTS_PAYLINES = [
            # Horizontal lines (rows 0-3)
            {"id": 1, "name": "Top Row", "coords": [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)], "color": "🔴"},
            {"id": 2, "name": "Upper Middle", "coords": [(1, 0), (1, 1), (1, 2), (1, 3), (1, 4)], "color": "🟡"},
            {"id": 3, "name": "Lower Middle", "coords": [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)], "color": "🟢"},
            {"id": 4, "name": "Bottom Row", "coords": [(3, 0), (3, 1), (3, 2), (3, 3), (3, 4)], "color": "🔵"},
            # V-shapes
            {"id": 5, "name": "V-Shape Top", "coords": [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)], "color": "🟣"},
            {"id": 6, "name": "V-Shape Bottom", "coords": [(3, 0), (2, 1), (1, 2), (2, 3), (3, 4)], "color": "🟠"},
            # Inverted V-shapes
            {"id": 7, "name": "Inverted V Top", "coords": [(3, 0), (2, 1), (1, 2), (2, 3), (3, 4)], "color": "⚪"},
            {"id": 8, "name": "Inverted V Bottom", "coords": [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)], "color": "⚫"},
            # Diagonal lines
            {"id": 9, "name": "Diagonal Down", "coords": [(0, 0), (1, 1), (2, 2), (3, 3), (3, 4)], "color": "🟤"},
            {"id": 10, "name": "Diagonal Up", "coords": [(3, 0), (2, 1), (1, 2), (0, 3), (0, 4)], "color": "🔷"},
        ]

        # ========== SLOTS REDESIGN: Weighted Reel Strips ==========
        # Each reel has weighted symbol distribution for ~96% RTP
        # Format: {symbol_key: weight} - higher weight = more likely
        # Adjusted for ~90% RTP (more favorable to casino)
        self.SLOTS_REEL_WEIGHTS = {
            0: {  # Reel 1 (leftmost)
                "lemon": 35, "slot_machine": 30, "cherry": 14, "star": 8,
                "bell": 5, "seven": 4, "diamond": 2, "wild": 1, "scatter": 1
            },
            1: {  # Reel 2
                "lemon": 32, "slot_machine": 28, "cherry": 15, "star": 10,
                "bell": 6, "seven": 4, "diamond": 3, "wild": 1, "scatter": 1
            },
            2: {  # Reel 3 (center)
                "lemon": 30, "slot_machine": 26, "cherry": 16, "star": 12,
                "bell": 7, "seven": 5, "diamond": 2, "wild": 1, "scatter": 1
            },
            3: {  # Reel 4
                "lemon": 28, "slot_machine": 25, "cherry": 17, "star": 14,
                "bell": 7, "seven": 5, "diamond": 2, "wild": 1, "scatter": 1
            },
            4: {  # Reel 5 (rightmost)
                "lemon": 25, "slot_machine": 22, "cherry": 18, "star": 15,
                "bell": 9, "seven": 6, "diamond": 3, "wild": 1, "scatter": 1
            },
        }

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [self._json_safe(v) for v in value]
        if isinstance(value, dict):
            return {k: self._json_safe(v) for k, v in value.items()}
        return value

    async def calculate_house_edge(self, user_id: int, base_edge: Decimal = Decimal("0.04")) -> Decimal:
        """
        Get house edge adjusted for VIP tier and active RTP boosts.
        Minimum 1% house edge to ensure sustainability.

        Args:
            user_id: Discord user ID
            base_edge: Base house edge (default 4%)

        Returns:
            Adjusted house edge as a decimal (e.g., 0.03 = 3%)
        """
        try:
            adjusted_edge = await self.bot.database.get_adjusted_house_edge(user_id, base_edge)
            return adjusted_edge
        except Exception as e:
            self.bot.logger.error(f"Error calculating house edge for {user_id}: {e}")
            return base_edge

    async def process_game_result(self, user_id: int, game_name: str, wagered: Decimal) -> Decimal:
        """
        Called after game resolves to accumulate rakeback.
        Updates user's total wagered and adds rakeback to their balance.

        Args:
            user_id: Discord user ID
            game_name: Name of the game played
            wagered: Amount wagered in the game

        Returns:
            Rakeback amount earned from this wager
        """
        if wagered <= 0:
            return Decimal("0")

        try:
            rakeback = await self.bot.database.update_user_wagered(user_id, wagered, game_name)
            return rakeback
        except Exception as e:
            self.bot.logger.error(f"Error processing game result for {user_id}: {e}")
            return Decimal("0")

    async def _create_game_session(
        self,
        ctx: Context | None,
        game_name: str,
        *,
        owner_id: int | None = None,
        participants: list[int] | None = None,
        wager_total: Decimal | None = None,
        state: dict | None = None,
        rng: dict | None = None,
        message_id: int | None = None,
    ):
        db = getattr(self.bot, "database", None)
        if not db:
            return None
        gid = ctx.guild.id if ctx and ctx.guild else None
        cid = ctx.channel.id if ctx and ctx.channel else None
        owner_id = owner_id or (ctx.author.id if ctx else None)
        if participants is None and owner_id:
            participants = [owner_id]
        return await db.create_game_session(
            game_name,
            guild_id=gid,
            channel_id=cid,
            message_id=message_id,
            owner_id=owner_id,
            participants=participants,
            wager_total=wager_total,
            state=self._json_safe(state or {}),
            rng=self._json_safe(rng or {}),
        )

    async def _update_game_session(self, session_id, **kwargs) -> None:
        db = getattr(self.bot, "database", None)
        if not db or not session_id:
            return
        clean = {}
        for key, value in kwargs.items():
            clean[key] = self._json_safe(value)
        await db.update_game_session(session_id, **clean)

    async def _log_game_event(self, session_id, event_type: str, payload: dict | None):
        db = getattr(self.bot, "database", None)
        if not db or not session_id:
            return
        await db.add_game_session_event(session_id, event_type, self._json_safe(payload or {}))

    async def _add_refund(self, session_id, *, user_id: int, wallet_id: str, amount: Decimal, reason: str):
        db = getattr(self.bot, "database", None)
        if not db or not session_id:
            return
        await db.add_game_session_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=str(amount),
            reason=reason,
        )

    async def _remove_refund(self, session_id, *, user_id: int):
        db = getattr(self.bot, "database", None)
        if not db or not session_id:
            return
        await db.remove_game_session_refund(session_id, user_id=user_id)

    async def _end_game_session(
        self, session_id, *, outcome: str | None = None, reason: str | None = None, final_state: dict | None = None
    ):
        db = getattr(self.bot, "database", None)
        if not db or not session_id:
            return
        await db.end_game_session(
            session_id,
            outcome=outcome,
            reason=reason,
            final_state=self._json_safe(final_state or {}),
        )
        self.session_registry.pop(str(session_id), None)

    def _register_session_handler(self, session_id, handler) -> None:
        if session_id:
            self.session_registry[str(session_id)] = handler

    async def force_end_session(self, session_id, *, refund: bool = False) -> bool:
        handler = self.session_registry.get(str(session_id))
        if not handler:
            return False
        await handler(refund=refund)
        return True

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

    async def fair_randbelow(
        self, user_id: int, upper: int, *, tag: str = "randbelow"
    ) -> int:
        if upper <= 0:
            raise ValueError("upper must be > 0")
        u64, _ = await self._next_u64(user_id, tag=tag)

        limit = U64_RANGE - (U64_RANGE % upper)
        while u64 >= limit:
            u64 = int.from_bytes(
                hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big"
            )
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
            raise ValueError(
                "Sample size cannot exceed sequence length and must be non-negative."
            )
        clone = list(seq)
        await self.fair_shuffle(user_id, clone)
        return clone[:k]

    async def fair_choice(
        self, user_id: int, seq: Sequence[Any], *, tag: str = "choice"
    ):
        if not seq:
            raise ValueError("sequence must be non-empty")
        idx = await self.fair_randbelow(user_id, len(seq), tag=tag)
        return seq[idx]

    async def fair_shuffle(self, user_id: int, deck: List[Any]) -> None:
        for i in range(len(deck) - 1, 0, -1):
            j = await self.fair_randbelow(user_id, i + 1, tag=f"shuffle:{i}")
            deck[i], deck[j] = deck[j], deck[i]

    async def fair_uniform(
        self, user_id: int, min_value: float, max_value: float
    ) -> float:
        if min_value >= max_value:
            raise ValueError("min_value must be less than max_value")
        r = await self.fair_random(user_id)
        return min_value + (max_value - min_value) * r

    async def fair_systemrandom(
        self, user_id: int, min_value: int, max_value: int, *, tag: str = "systemrandom"
    ) -> int:
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

    def _fmt_no_sci(
        self, x: Decimal, *, max_frac: int = 2, rounding=ROUND_HALF_UP
    ) -> str:
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
            (Decimal("1e33"), " decillion"),
            (Decimal("1e30"), " nonillion"),
            (Decimal("1e27"), " octillion"),
            (Decimal("1e24"), " septillion"),
            (Decimal("1e21"), " sextillion"),
            (Decimal("1e18"), " quintillion"),
            (Decimal("1e15"), " quadrillion"),
            (Decimal("1e12"), " trillion"),
            (Decimal("1e9"), " billion"),
            (Decimal("1e6"), " million"),
            (Decimal("1e3"), " thousand"),
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
            (Decimal("1e33"), " dec"),
            (Decimal("1e30"), " non"),
            (Decimal("1e27"), " oct"),
            (Decimal("1e24"), " sept"),
            (Decimal("1e21"), " sext"),
            (Decimal("1e18"), " quin"),
            (Decimal("1e15"), " quad"),
            (Decimal("1e12"), " tril"),
            (Decimal("1e9"), " bil"),
            (Decimal("1e6"), " mil"),
            (Decimal("1e3"), "k"),
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
            amount = AmountUtils.truncate_currency(user_balance)
        elif amount_input == "half":
            amount = AmountUtils.round_currency(user_balance / Decimal("2"))
        elif amount_input == "quarter":
            amount = AmountUtils.round_currency(user_balance / Decimal("4"))

        elif amount_input.endswith("%"):
            percentage_match = re.match(r"^([0-9]+(\.[0-9]+)?)%$", amount_input)
            if percentage_match:
                try:
                    percentage = Decimal(percentage_match.group(1))
                    if Decimal("1") <= percentage <= Decimal("100"):
                        amount = user_balance * (percentage / Decimal("100"))
                    else:
                        raise ValueError("Percentage must be between 1% and 100%.")
                except InvalidOperation:
                    raise ValueError("Invalid percentage value.")
            else:
                raise ValueError("Invalid percentage format.")
        else:
            multipliers = {
                "k": Decimal("1000"),
                "m": Decimal("1000000"),
                "b": Decimal("1000000000"),
                "t": Decimal("1000000000000"),
                "q": Decimal("1000000000000000"),
                "qu": Decimal("1000000000000000000"),
                "s": Decimal("1000000000000000000000"),
                "se": Decimal("1000000000000000000000000"),
                "o": Decimal("1000000000000000000000000000"),
                "n": Decimal("1000000000000000000000000000000"),
                "d": Decimal("1000000000000000000000000000000000"),
            }

            multiplier_match = re.match(
                r"^([0-9]+(\.[0-9]+)?)(k|m|b|t|q|qu|s|se|o|n|d)?$", amount_input
            )
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
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValueError("Invalid amount.")

        if amount > user_balance:
            raise ValueError("Insufficient Funds.")
        if amount <= Decimal("0"):
            raise ValueError("Amount must be greater than 0.")

        return amount

    @commands.group(
        name="casino",
        invoke_without_command=True,
        help="Casino command group. Use !casino help for subcommands.",
    )
    async def casino(self, ctx: commands.Context):
        """Root for casino commands. Lists available subcommands."""
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
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Use {prefix}casino <subcommand> for details.")

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(
        name="stats", help="Check your win/loss statistics for a specific game."
    )
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

        (
            total_wagered,
            win_wagered,
            loss_wagered,
        ) = await self.bot.database.get_wager_stats(member.id, game_key)

        average_bet = (total_wagered / total_games) if total_games else Decimal("0")
        average_win = (win_wagered / wins) if wins else Decimal("0")
        average_loss = (loss_wagered / losses) if losses else Decimal("0")

        color = (
            member.top_role.color
            if hasattr(member, "top_role") and member.top_role
            else discord.Color.blurple()
        )
        embed = discord.Embed(
            title=f"{member.display_name}'s {game_key.title()} Stats", color=color
        )
        embed.add_field(name="Wins", value=str(wins), inline=True)
        embed.add_field(name="Losses", value=str(losses), inline=True)
        embed.add_field(name="Total Games", value=str(total_games), inline=True)
        embed.add_field(name="Win Rate", value=f"{win_rate:.2f}%", inline=False)
        embed.add_field(
            name="Total Wagered",
            value=f"{self.currency_name} **{await self.formatter(total_wagered)}**",
            inline=True,
        )
        embed.add_field(
            name="Avg Bet",
            value=f"{self.currency_name} **{await self.short_formatter(average_bet)}**",
            inline=True,
        )
        embed.add_field(
            name="Avg Wager (Wins)",
            value=f"{self.currency_name} **{await self.short_formatter(average_win)}**",
            inline=True,
        )
        embed.add_field(
            name="Avg Wager (Losses)",
            value=f"{self.currency_name} **{await self.short_formatter(average_loss)}**",
            inline=True,
        )

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(
        name="leaderboard",
        aliases=["lb"],
        help="View the top winners and losers for a specific game.",
    )
    async def casino_leaderboard(
        self, ctx: commands.Context, game_name: str = "gamble", limit: int = 10
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
            color=ctx.author.top_role.color
            if getattr(ctx.author, "top_role", None)
            else discord.Color.blurple()
        )
        embed.set_author(
            name=f"Casino Leaderboard - {game_name.title()}",
            icon_url=self.utils.get_avatar_url(ctx.author),
        )
        if top_winners:
            top_list = []
            rank_emojis = ["<:crown:1360657246165537011>"] + [
                f"{idx}." for idx in range(2, 11)
            ]
            for idx, (user_id, wins) in enumerate(top_winners):
                user = (
                    ctx.guild.get_member(user_id)
                    or self.bot.get_user(user_id)
                    or await self.bot.fetch_user(user_id)
                )
                display_name = user.display_name if user else f"Unknown {user_id}"
                emoji = rank_emojis[idx] if idx < len(rank_emojis) else f"{idx+1}."
                top_list.append(f"{emoji} **{display_name}** (`{wins:,} wins`)")
            embed.add_field(name="Top 10 Users", value="\n".join(top_list), inline=True)
        else:
            embed.add_field(name="Top 10 Users", value="No data available", inline=True)
        await ctx.send(embed=embed)

    @casino.command(
        name="history", aliases=["games", "ghistory"], help="View your game history."
    )
    async def casino_history(self, ctx: commands.Context, limit: int = 100):
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
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=5, mention_author=False)

        view = GameHistoryPaginator(
            cog=self, history=history, member=member, requester=ctx.author
        )
        embed = await view.get_page_embed()
        await ctx.reply(embed=embed, view=view, mention_author=False)

    @casino.command(
        name="verify",
        aliases=["v", "verif", "check"],
        help="verify a provably-fair game outcome",
        hidden=True,
    )
    async def casino_verify(
        self, ctx: commands.Context, game: str, nonce: int, *extra_args: str
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
                color=discord.Color.red(),
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
        embed = discord.Embed(
            title=title, description=desc, color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)

        try:
            if game_key == "gamble":
                result = pf.verify_gamble(server_seed, client_seed, nonce)
                pretty = (
                    json.dumps(result, indent=2)
                    if not isinstance(result, str)
                    else result
                )
                embed.add_field(
                    name="🎰 Gamble Result",
                    value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```",
                    inline=False,
                )

            elif game_key in ("supergamble", "sgamble"):
                result = pf.verify_supergamble(server_seed, client_seed, nonce)
                pretty = (
                    json.dumps(result, indent=2)
                    if not isinstance(result, str)
                    else result
                )
                embed.add_field(
                    name="💥 SuperGamble",
                    value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```",
                    inline=False,
                )

            elif game_key in ("dice", "roll"):
                die1, die2 = pf.verify_dice(server_seed, client_seed, nonce)
                total = die1 + die2
                embed.add_field(
                    name="🎲 Dice",
                    value=f"Die 1: **{die1}** • Die 2: **{die2}**\nTotal: **{total}**",
                    inline=False,
                )

            elif game_key == "ladder":
                if not extra_args:
                    return await ctx.reply(
                        "For ladder, supply both nonce and step.", mention_author=False
                    )
                step = int(extra_args[0])
                roll_pct, threshold = pf.verify_ladder(
                    server_seed, client_seed, nonce, step
                )

                bar_len = 20
                filled = int(min(max(roll_pct, 0), 100) / 100 * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                embed.add_field(
                    name="🪜 Ladder Roll",
                    value=f"Step: **{step}**\nRoll: `{roll_pct:.2f}%` • Threshold: `{threshold:.2f}%`\n`{bar}`",
                    inline=False,
                )

            elif game_key == "slots":
                slots_data = pf.verify_slots(server_seed, client_seed, nonce)
                
                # Build display string
                display_parts = []
                
                # Grid display
                grid_display = slots_data.get("grid_display", "")
                if grid_display:
                    display_parts.append(f"Grid:\n{grid_display}")
                
                # Wins
                wins = slots_data.get("wins", [])
                if wins:
                    win_strs = []
                    for win in wins:
                        win_strs.append(
                            f"Line {win['payline_id']}: {win['symbol']} x{win['count']} = {win['payout']:.2f}x"
                        )
                    display_parts.append("Wins:\n" + "\n".join(win_strs))
                
                # Scatters
                scatter_count = slots_data.get("scatter_count", 0)
                scatter_payout = slots_data.get("scatter_payout", 0)
                if scatter_count > 0:
                    display_parts.append(f"Scatters: {scatter_count} = {scatter_payout:.2f}x")
                
                # Total
                total_payout = slots_data.get("total_payout", 0)
                display_parts.append(f"Total Payout: {total_payout:.2f}x")
                
                slots_str = "\n\n".join(display_parts)
                embed.add_field(
                    name="🎰 Slots Outcome",
                    value=f"```{shorten(slots_str, width=900, placeholder='…')}```",
                    inline=False,
                )

            elif game_key == "blackjack" or game_key == "bj":
                bj = pf.verify_blackjack(server_seed, client_seed, nonce)
                shuffled = bj.get("shuffled_deck", [])
                player_cards = bj.get("player_cards", [])
                dealer_cards = bj.get("dealer_cards", [])
                embed.add_field(
                    name="🃏 Blackjack — Player",
                    value=f"{', '.join(player_cards)}",
                    inline=False,
                )
                embed.add_field(
                    name="🃏 Blackjack — Dealer",
                    value=f"{', '.join(dealer_cards)}",
                    inline=False,
                )
                embed.add_field(
                    name="🔀 Shuffled Deck (full)",
                    value=f"```{', '.join(shuffled)}```",
                    inline=False,
                )

            elif game_key in ("ridebus", "bus"):
                result = pf.verify_ridebus(server_seed, client_seed, nonce)
                pretty = (
                    json.dumps(result, indent=2)
                    if not isinstance(result, str)
                    else result
                )
                embed.add_field(
                    name="🚌 Ridebus",
                    value=f"```json\n{shorten(pretty, width=900, placeholder='…')}\n```",
                    inline=False,
                )

            elif game_key == "poker":
                pk = pf.verify_poker(server_seed, client_seed, nonce)
                player_hand = pk.get("player_hand", [])
                bot_hand = pk.get("bot_hand", [])
                community = pk.get("community", [])
                embed.add_field(
                    name="🂡 Poker — Player",
                    value=f"{', '.join(player_hand)}",
                    inline=False,
                )
                embed.add_field(
                    name="🤖 Poker — Bot", value=f"{', '.join(bot_hand)}", inline=False
                )
                embed.add_field(
                    name="🃏 Community", value=f"{', '.join(community)}", inline=False
                )

            elif game_key == "roulette":
                r = pf.verify_roulette(server_seed, client_seed, nonce)
                color = r.get("color", "Unknown")
                spin_result = r.get("spin_result", "—")
                emoji = (
                    "🟢"
                    if color.lower() == "green"
                    else ("🔴" if color.lower() == "red" else "⚫")
                )
                embed.add_field(
                    name="🎡 Roulette",
                    value=f"{emoji} {color.title()} • **{spin_result}**",
                    inline=False,
                )

            else:
                return await ctx.reply(
                    f"Unknown game '{game_key}'.", mention_author=False
                )

        except Exception as e:
            logger.exception("Error while verifying PF data")
            embed = discord.Embed(
                title="⚠️ Verification Error",
                description=f"An error occurred while verifying: {e}",
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, mention_author=False)

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(
        name="seed", help="View your current client seed and the hashed server seed."
    )
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
            if not isinstance(ctx.channel, discord.DMChannel)
            and getattr(ctx.author, "top_role", None)
            else discord.Color.blurple()
        )

        embed = discord.Embed(title="Seed Info", color=color)
        embed.add_field(name="Client Seed", value=f"`{client_seed}`", inline=False)
        embed.add_field(name="Nonce", value=f"`{nonce}`", inline=False)
        embed.add_field(name="Server Seed Hash", value=f"`{server_hash}`", inline=False)
        embed.set_footer(text="These seeds are used for provable fairness in games.")

        await self.bot.database.set_cooldown(ctx.author.id, "casino seed", 5)
        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(
        name="setseed",
        aliases=["newseed"],
        help="Update your client seed for provable fairness.",
    )
    async def casino_setseed(
        self, ctx: commands.Context, *, seed: Optional[str] = None
    ):
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
            color=discord.Color.green(),
        )

        await self.bot.database.set_cooldown(ctx.author.id, "casino setseed", 5)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(
        name="gamble", description="Gamble your money for a chance to win big!"
    )
    async def gamble(self, ctx: Context, bet_amount: str):
        try:
            user_id = ctx.author.id
            session_id = None

            PF = await self.prove_fairness(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = AmountUtils.round_currency(Decimal(str(balance)))

            try:
                amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if amount > max_allowed:
                amount = max_allowed
                await ctx.reply(
                    embed=discord.Embed(
                        description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(amount)} {self.currency_name}**.",
                        color=discord.Color.orange(),
                    ),
                    delete_after=5,
                )
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-Decimal(amount),
                    description="Gamble Bet",
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            session_id = await self._create_game_session(
                ctx,
                "gamble",
                owner_id=user_id,
                wager_total=amount,
                state={
                    "user_id": user_id,
                    "bet": str(amount),
                    "wallet_id": str(wallet_id),
                },
                rng=PF,
            )
            await self._add_refund(
                session_id,
                user_id=user_id,
                wallet_id=str(wallet_id),
                amount=amount,
                reason="gamble_bet",
            )

            win_multiplier = Decimal("2.0")
            is_winner = await self.fair_randbelow(user_id, 2) == 1

            # Process game result for rakeback
            await self.process_game_result(user_id, "gamble", amount)

            if is_winner:
                winnings = Decimal(amount) * win_multiplier
                revealed_seed, new_hash = await self.bot.database.increment_win(
                    user_id,
                    "gamble",
                    amount,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id, amount=winnings, description="Gamble Win"
                    )
                except ValueError as e:
                    embed = discord.Embed(
                        description=f"🚫 Transaction failed: {e}",
                        color=discord.Color.red(),
                    )
                    await self._end_game_session(
                        session_id,
                        outcome="error",
                        reason=str(e),
                        final_state={"winnings": str(winnings)},
                    )
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                embed = discord.Embed(
                    description=f"You won {self.currency_name} **{await self.formatter(winnings)}**!",
                    color=discord.Color.green(),
                )
                await self._remove_refund(session_id, user_id=user_id)
                await self._log_game_event(
                    session_id,
                    "result",
                    {"outcome": "win", "winnings": str(winnings)},
                )
                await self._end_game_session(
                    session_id,
                    outcome="win",
                    final_state={"winnings": str(winnings)},
                )
            else:
                revealed_seed, new_hash = await self.bot.database.increment_loss(
                    user_id,
                    "gamble",
                    amount,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )
                embed = discord.Embed(
                    description=f"You lost {self.currency_name} **{await self.formatter(amount)}**",
                    color=discord.Color.red(),
                )
                await self._remove_refund(session_id, user_id=user_id)
                await self._log_game_event(
                    session_id,
                    "result",
                    {"outcome": "loss", "loss": str(amount)},
                )
                await self._end_game_session(
                    session_id,
                    outcome="loss",
                    final_state={"loss": str(amount)},
                )

            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
            await ctx.reply(embed=embed)

        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)

    @commands.command(
        name="supergamble",
        aliases=["sg", "sgamble"],
        description="Gamble at a 15% chance to win for amazing rewards!",
    )
    async def supergamble(self, ctx: Context, bet_amount: str):
        try:
            user_id = ctx.author.id
            session_id = None

            PF = await self.prove_fairness(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = AmountUtils.round_currency(Decimal(str(balance)))

            try:
                amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if amount > max_allowed:
                amount = max_allowed
                await ctx.reply(
                    embed=discord.Embed(
                        description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(amount)} {self.currency_name}**.",
                        color=discord.Color.orange(),
                    ),
                    delete_after=5,
                )
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id, -amount, "SuperGamble Bet"
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return

            session_id = await self._create_game_session(
                ctx,
                "supergamble",
                owner_id=user_id,
                wager_total=amount,
                state={
                    "user_id": user_id,
                    "bet": str(amount),
                    "wallet_id": str(wallet_id),
                },
                rng=PF,
            )
            await self._add_refund(
                session_id,
                user_id=user_id,
                wallet_id=str(wallet_id),
                amount=amount,
                reason="supergamble_bet",
            )

            win_roll = await self.fair_randbelow(user_id, 100)
            bonus_roll = await self.fair_randbelow(user_id, 100)

            win_chance = 10
            win = win_roll < win_chance
            base_multiplier = Decimal("8.0")
            bonus_multiplier = Decimal("12.0")

            supply = await self.bot.database.get_supply_record()
            treasury = supply.treasury
            circulating = supply.circulating
            total_supply = supply.total_supply

            treasury_ratio = (
                treasury / total_supply if total_supply > 0 else Decimal("0")
            )

            # Process game result for rakeback
            await self.process_game_result(user_id, "supergamble", amount)

            if win:
                raw_multiplier = (
                    bonus_multiplier if bonus_roll < 15 else base_multiplier
                )
                bonus_text = (
                    "\n🌟 **MEGA WIN!** Extra multiplier applied!"
                    if bonus_roll < 15
                    else ""
                )

                winnings = (amount * raw_multiplier).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                if winnings > treasury:
                    winnings = treasury
                    bonus_text += (
                        "\n💸 Treasury couldn't pay full amount — payout capped."
                    )

                revealed_seed, new_hash = await self.bot.database.increment_win(
                    user_id,
                    "supergamble",
                    amount,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id, winnings, "SuperGamble Win"
                    )
                except ValueError as e:
                    embed = discord.Embed(
                        description=f"🚫 Transaction failed: {e}",
                        color=discord.Color.red(),
                    )
                    await self._end_game_session(
                        session_id,
                        outcome="error",
                        reason=str(e),
                        final_state={"winnings": str(winnings)},
                    )
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                embed = discord.Embed(
                    description=f"🎉 You hit the jackpot and won **{await self.formatter(winnings)} {self.currency_name}**! {bonus_text}",
                    color=discord.Color.gold(),
                )
                outcome = "win"
                outcome_amount = winnings

            else:
                recovery_roll = bonus_roll
                recovery_allowed = recovery_roll < 10 and treasury_ratio >= Decimal(
                    "0.1"
                )
                if recovery_allowed:
                    recovery = AmountUtils.round_currency(amount * Decimal("0.20"))
                    if recovery > treasury:
                        recovery = treasury

                    try:
                        await self.bot.database.process_treasury_transaction(
                            wallet_id, recovery, "SuperGamble Loss Recovery"
                        )
                    except ValueError as e:
                        embed = discord.Embed(
                            description=f"🚫 Transaction failed: {e}",
                            color=discord.Color.red(),
                        )
                        await self._end_game_session(
                            session_id,
                            outcome="error",
                            reason=str(e),
                            final_state={"recovery": str(recovery)},
                        )
                        await ctx.reply(embed=embed, delete_after=5)
                        return
                    embed = discord.Embed(
                        description=f"You lost, but recovered **{await self.formatter(recovery)} {self.currency_name}**!",
                        color=discord.Color.blurple(),
                    )
                    outcome = "loss"
                    outcome_amount = recovery
                else:
                    revealed_seed, new_hash = await self.bot.database.increment_loss(
                        user_id,
                        "supergamble",
                        amount,
                        client_seed=PF["client_seed"],
                        seed_used=None,
                        nonce=PF["nonce"],
                        hash_hex=PF["server_seed_hash"],
                    )
                    embed = discord.Embed(
                        description=f"You lost **{await self.formatter(amount)} {self.currency_name}**.",
                        color=discord.Color.red(),
                    )
                    outcome = "loss"
                    outcome_amount = amount

            await self.bot.database.set_cooldown(
                user_id, ctx.command.qualified_name, 60
            )
            await ctx.reply(embed=embed)

            await self._remove_refund(session_id, user_id=user_id)
            await self._log_game_event(
                session_id,
                "result",
                {"outcome": outcome, "amount": str(outcome_amount)},
            )
            await self._end_game_session(
                session_id,
                outcome=outcome,
                final_state={"amount": str(outcome_amount)},
            )

        except ValueError as e:
            await ctx.reply(
                embed=discord.Embed(description=str(e), color=discord.Color.red()),
                delete_after=5,
            )

    def _generate_spin_grid(self, user_id: int, nonce_start: int) -> tuple[list[list[str]], dict]:
        """Generate a 5x4 grid using weighted reel strips with provable fairness.
        
        Returns:
            tuple: (grid, verification_data)
            - grid: 5x4 grid as list of columns (reels), each column is list of 4 symbols
            - verification_data: dict with reel positions for fairness verification
        """
        grid = []
        verification = {"reel_positions": [], "nonce_start": nonce_start}
        
        for reel_idx in range(5):
            weights = self.SLOTS_REEL_WEIGHTS[reel_idx]
            symbols = list(weights.keys())
            weight_values = list(weights.values())
            total_weight = sum(weight_values)
            
            # Generate 4 symbols for this reel (column)
            reel_symbols = []
            for row_idx in range(4):
                # Use fair_randbelow for provable fairness
                # Note: This will be called via async in the actual command
                # For now, we'll use the deterministic selection
                import random
                pos = random.randrange(total_weight)
                cumulative = 0
                selected = symbols[0]
                for sym, w in zip(symbols, weight_values):
                    cumulative += w
                    if pos < cumulative:
                        selected = sym
                        break
                reel_symbols.append(selected)
            
            grid.append(reel_symbols)
            verification["reel_positions"].append(pos if 'pos' in dir() else 0)
        
        return grid, verification

    async def _async_generate_spin_grid(self, user_id: int, nonce_start: int) -> tuple[list[list[str]], dict]:
        """Async version that uses fair_randbelow for provable fairness."""
        grid = []
        verification = {"reel_positions": [], "nonce_start": nonce_start}
        
        for reel_idx in range(5):
            weights = self.SLOTS_REEL_WEIGHTS[reel_idx]
            symbols = list(weights.keys())
            weight_values = list(weights.values())
            total_weight = sum(weight_values)
            
            reel_symbols = []
            for row_idx in range(4):
                # Use provable fairness RNG
                pos = await self.fair_randbelow(user_id, total_weight)
                cumulative = 0
                selected = symbols[0]
                for sym, w in zip(symbols, weight_values):
                    cumulative += w
                    if pos < cumulative:
                        selected = sym
                        break
                reel_symbols.append(selected)
            
            grid.append(reel_symbols)
            verification["reel_positions"].append(pos)
        
        return grid, verification

    def _evaluate_paylines(self, grid: list[list[str]]) -> list[dict]:
        """Evaluate all 20 paylines against the grid.
        
        Args:
            grid: 5x4 grid as list of columns (reels)
        
        Returns:
            list of winning paylines, each with:
            - payline_id: int
            - symbol: str
            - count: int (3, 4, or 5 matching)
            - payout: Decimal multiplier
        """
        wins = []
        
        for payline in self.SLOTS_PAYLINES:
            # Get symbols along this payline
            coords = payline["coords"]  # [(row, col), ...] for 5 positions
            symbols_on_line = []
            
            for row, col in coords:
                # grid[col][row] - grid is column-major
                if col < len(grid) and row < len(grid[col]):
                    symbols_on_line.append(grid[col][row])
            
            if len(symbols_on_line) != 5:
                continue
            
            # Find longest matching sequence from left
            # Wilds can substitute for any symbol except Scatter
            first_symbol = None
            match_count = 0
            
            for i, sym in enumerate(symbols_on_line):
                if sym == "scatter":
                    # Scatter doesn't form paylines
                    break
                
                if first_symbol is None:
                    if sym != "wild":
                        first_symbol = sym
                        match_count = 1
                    # Wild at position 0 - keep looking for actual symbol
                elif sym == first_symbol or sym == "wild":
                    match_count += 1
                else:
                    # Mismatch - stop counting
                    break
            
            # Check if we have a winning combination (3+ matches)
            if match_count >= 3 and first_symbol:
                symbol_data = self.SLOTS_SYMBOLS.get(first_symbol, {})
                payouts = symbol_data.get("payouts", {})
                payout_mult = Decimal(str(payouts.get(match_count, 0)))
                
                if payout_mult > 0:
                    wins.append({
                        "payline_id": payline["id"],
                        "payline_name": payline["name"],
                        "symbol": first_symbol,
                        "symbol_emoji": symbol_data.get("emoji", "❓"),
                        "count": match_count,
                        "payout": payout_mult,
                        "coords": coords[:match_count],
                    })
        
        return wins

    def _count_scatters(self, grid: list[list[str]]) -> int:
        """Count scatter symbols anywhere on the grid."""
        count = 0
        for col in grid:
            for sym in col:
                if sym == "scatter":
                    count += 1
        return count

    def _calculate_scatter_payout(self, scatter_count: int) -> Decimal:
        """Calculate scatter payout multiplier."""
        scatter_data = self.SLOTS_SYMBOLS.get("scatter", {})
        payouts = scatter_data.get("payouts", {})
        return Decimal(str(payouts.get(scatter_count, 0)))

    def _format_grid_display(self, grid: list[list[str]], winning_coords: list[tuple] = None) -> str:
        """Format the 5x4 grid for display.
        
        Args:
            grid: 5x4 grid as list of columns
            winning_coords: Optional list of (row, col) tuples to highlight
        
        Returns:
            Formatted string with emoji grid
        """
        winning_set = set(winning_coords) if winning_coords else set()
        lines = []
        
        # Grid is column-major: grid[col][row]
        # Display row by row
        for row in range(4):
            row_display = []
            for col in range(5):
                sym = grid[col][row]
                sym_data = self.SLOTS_SYMBOLS.get(sym, {})
                emoji = sym_data.get("emoji", "❓")
                
                if (row, col) in winning_set:
                    row_display.append(f"【{emoji}】")
                else:
                    row_display.append(f"｜{emoji}｜")
            
            lines.append("".join(row_display))
        
        return "\n".join(lines)

    @commands.command(
        name="slots",
        aliases=["slot"],
        description="Play the slots with 5x4 grid, 20 paylines, Wilds & Scatters!",
    )
    async def slots(self, ctx: Context, bet_amount: str):
        """Modern 5x4 slot machine with 20 paylines, Wild substitutions, and Scatter pays."""
        user_id = ctx.author.id
        currency = self.currency_name

        # Get wallet and balance
        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        bal_raw = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(bal_raw))

        # Parse bet amount
        try:
            stake = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=str(e), color=discord.Color.red()),
                delete_after=5,
            )

        # Check max bet limit
        max_allowed = await self.bot.database.get_max_gamble_amount(user_id)
        if stake > max_allowed:
            stake = max_allowed
            await ctx.reply(
                embed=discord.Embed(
                    description=f"High-roller bet capped at **{await self.formatter(stake)} {currency}**.",
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        # Deduct initial bet from wallet
        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id, -stake, "Slots Bet"
            )
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()),
                delete_after=5,
            )

        # Get provable fairness data
        PF = await self.prove_fairness(user_id)

        # Generate initial spin grid
        grid, verification = await self._async_generate_spin_grid(user_id, PF["nonce"])

        # Evaluate paylines
        payline_wins = self._evaluate_paylines(grid)
        scatter_count = self._count_scatters(grid)
        scatter_payout = self._calculate_scatter_payout(scatter_count)

        # Calculate total multiplier
        total_multiplier = Decimal("0")
        for win in payline_wins:
            total_multiplier += win["payout"]
        total_multiplier += scatter_payout

        # Calculate winnings (apply 2% house edge)
        winnings = AmountUtils.round_currency(stake * total_multiplier * Decimal("0.98"))

        # Process game result for rakeback
        await self.process_game_result(user_id, "slots", stake)

        # Process outcome
        if winnings > 0:
            await self.bot.database.process_treasury_transaction(
                wallet_id, winnings, "Slots Win"
            )
            await self.bot.database.increment_win(
                user_id, "slots", stake,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
        else:
            await self.bot.database.increment_loss(
                user_id, "slots", stake,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )

        # Check for free spins trigger
        free_spins_awarded = 0
        if scatter_count >= 3:
            free_spins_awarded = {3: 10, 4: 15, 5: 20}.get(scatter_count, 20)

        # Create SlotsView instance
        view = SlotsView(
            cog=self,
            user_id=user_id,
            bet=stake,
            grid=grid,
            payline_wins=payline_wins,
            scatter_count=scatter_count,
            scatter_payout=scatter_payout,
            winnings=winnings,
            free_spins=free_spins_awarded,
            multiplier=Decimal("2") if free_spins_awarded > 0 else Decimal("1"),
            pf_data=PF,
            verification=verification,
        )
        view.has_played = True  # Mark initial spin as played for loss message display

        # Build initial container and add to view
        container = await view._build_container()
        container.add_item(view.buttons)
        view.add_item(container)

        # Set cooldown
        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)

        # Send response with view
        message = await ctx.reply(view=view)
        view.message = message

    @commands.command(
        name="dice",
        aliases=["diceroll", "roll"],
        description="Roll two dice and bet on the outcome.",
    )
    async def roll(self, ctx: Context, bet_amount: str, guess: str):
        user_id = ctx.author.id
        session_id = None

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
            await ctx.reply(
                embed=discord.Embed(
                    description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                    f"**{await self.formatter(amount)} {self.currency_name}**.",
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        normalized_guess = guess.lower()
        if (
            normalized_guess not in ["even", "evens", "odd", "odds"]
            and not normalized_guess.isdigit()
        ):
            await ctx.reply(
                "Invalid guess. Please use 'even', 'odd', or a number between 2 and 12.",
                delete_after=5,
            )
            return
        if normalized_guess.isdigit() and (
            int(normalized_guess) < 2 or int(normalized_guess) > 12
        ):
            await ctx.reply(
                "Invalid guess. The total of two dice can only be between 2 and 12.",
                delete_after=5,
            )
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-Decimal(amount), description="Dice Bet"
            )
        except ValueError as e:
            embed = discord.Embed(
                description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        session_id = await self._create_game_session(
            ctx,
            "dice",
            owner_id=user_id,
            wager_total=amount,
            state={
                "user_id": user_id,
                "bet": str(amount),
                "wallet_id": str(wallet_id),
                "guess": guess,
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=amount,
            reason="dice_bet",
        )

        die1 = await self.fair_randbelow(user_id, 6) + 1
        die2 = await self.fair_randbelow(user_id, 6) + 1
        total = die1 + die2
        even_or_odd = "E" if total % 2 == 0 else "O"

        self.roll_history.setdefault(user_id, []).append(even_or_odd)
        if len(self.roll_history[user_id]) > 5:
            self.roll_history[user_id].pop(0)

        # Calculate house edge for RTP tracking and bonus
        house_edge = await self.calculate_house_edge(user_id)

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
            12: 10,
        }
        even_odd_payout = 1.5
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        embed = discord.Embed(color=color)
        embed.set_author(
            name="Diceroll", icon_url=self.utils.get_avatar_url(ctx.author)
        )

        winnings = 0
        base_edge = Decimal("0.04")
        # Apply RTP boost for VIP players
        rtp_boost = Decimal("1") + (base_edge - house_edge) / base_edge * Decimal("0.1") if house_edge < base_edge else Decimal("1")

        if (normalized_guess in ["even", "evens"] and total % 2 == 0) or (
            normalized_guess in ["odd", "odds"] and total % 2 == 1
        ):
            winnings = Decimal(amount) * Decimal(even_odd_payout) * rtp_boost
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nYou guessed correctly and won {currency_name} **{await self.formatter(winnings)}**!"
        elif normalized_guess.isdigit() and int(normalized_guess) == total:
            multiplier = payout_multipliers[total]
            winnings = Decimal(amount) * Decimal(multiplier) * rtp_boost
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nExact match! You won {currency_name} **{await self.formatter(winnings)}** with a {multiplier}x payout!"
        else:
            result = f"🎲 You rolled {die1} and {die2} (total {total})\nYou lost {currency_name} **{await self.formatter(amount)}**."

        winnings = AmountUtils.round_currency(winnings) if winnings else 0

        # Process game result for rakeback
        await self.process_game_result(user_id, "dice", amount)

        if winnings > 0:
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id, amount=winnings, description="Dice Win"
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return
            revealed_seed, new_hash = await self.bot.database.increment_win(
                user_id,
                "dice",
                amount,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                user_id,
                "dice",
                amount,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )

        embed.description = result
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        await ctx.reply(embed=embed)

        await self._remove_refund(session_id, user_id=user_id)
        await self._log_game_event(
            session_id,
            "result",
            {
                "outcome": "win" if winnings > 0 else "loss",
                "amount": str(winnings or amount),
                "roll": {"die1": die1, "die2": die2, "total": total},
            },
        )
        await self._end_game_session(
            session_id,
            outcome="win" if winnings > 0 else "loss",
            final_state={"amount": str(winnings or amount)},
        )

    @commands.command(
        name="roulette",
        aliases=["roul", "rou"],
        description="Play roulette and bet on a number or color. If no arguments are given, shows bet options.",
    )
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
                    "**Green:** `green` (payout ×14 for 0 or 00)\n"
                    "**Single numbers:** `0`–`36`, or `00` (payout ×36).\n\n"
                    "Example: `!roulette 100 red`"
                ),
                color=discord.Color.blue(),
            )
            await ctx.reply(embed=embed, delete_after=20)
            return

        user_id = ctx.author.id
        session_id = None

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
            await ctx.reply(
                embed=discord.Embed(
                    description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                    f"**{await self.formatter(amount)} {self.currency_name}**.",
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        valid_choices = (
            {
                "green",
                "red",
                "black",
                "odd",
                "even",
                "high",
                "low",
                "dozen1",
                "dozen2",
                "dozen3",
                "column1",
                "column2",
                "column3",
            }
            | {str(i) for i in range(37)}
            | {"00"}
        )
        choice = choice.lower()
        if choice not in valid_choices:
            embed = discord.Embed(
                description=(
                    "Invalid bet choice. Please choose a valid bet type "
                    "(`red`, `black`, `odd`, `even`, `high`, `low`, `dozen1`, `dozen2`, `dozen3`, "
                    "`column1`, `column2`, `column3`), a number `0–36`, or `00`."
                ),
                color=discord.Color.red(),
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=Decimal(-amount), description="Roulette Bet"
            )
        except ValueError as e:
            embed = discord.Embed(
                description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
            )
            await ctx.reply(embed=embed, delete_after=5)
            return

        session_id = await self._create_game_session(
            ctx,
            "roulette",
            owner_id=user_id,
            wager_total=amount,
            state={
                "user_id": user_id,
                "bet": str(amount),
                "wallet_id": str(wallet_id),
                "choice": choice,
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=amount,
            reason="roulette_bet",
        )

        red_numbers = {
            1,
            3,
            5,
            7,
            9,
            12,
            14,
            16,
            18,
            19,
            21,
            23,
            25,
            27,
            30,
            32,
            34,
            36,
        }
        black_numbers = {
            2,
            4,
            6,
            8,
            10,
            11,
            13,
            15,
            17,
            20,
            22,
            24,
            26,
            28,
            29,
            31,
            33,
            35,
        }
        green_numbers = {0, "00"}
        all_numbers = list(range(0, 37)) + ["00"]

        # Calculate house edge for RTP tracking and bonus
        house_edge = await self.calculate_house_edge(user_id)
        base_edge = Decimal("0.04")
        # Apply RTP boost for VIP players (lower house edge = higher RTP)
        rtp_boost = Decimal("1") + (base_edge - house_edge) / base_edge * Decimal("0.1") if house_edge < base_edge else Decimal("1")

        spin_result = await self.fair_choice(user_id, all_numbers)

        is_red = isinstance(spin_result, int) and spin_result in red_numbers
        is_black = isinstance(spin_result, int) and spin_result in black_numbers
        is_green = (spin_result == 0) or (spin_result == "00")
        is_even = isinstance(spin_result, int) and (spin_result % 2 == 0)
        is_odd = isinstance(spin_result, int) and (spin_result % 2 == 1)
        color_label = "Green" if is_green else ("Red" if is_red else "Black")

        winnings = Decimal(0)
        outcome_description = f"The ball landed on **{color_label} {spin_result}**."

        if choice == "green" and is_green:
            winnings = amount * Decimal(14)
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
        elif (
            choice == "high"
            and isinstance(spin_result, int)
            and 19 <= spin_result <= 36
        ):
            winnings = amount * Decimal(2)
            outcome_description += " You bet on High (19–36)."
        elif (
            choice == "low" and isinstance(spin_result, int) and 1 <= spin_result <= 18
        ):
            winnings = amount * Decimal(2)
            outcome_description += " You bet on Low (1–18)."
        elif (
            choice == "dozen1"
            and isinstance(spin_result, int)
            and 1 <= spin_result <= 12
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 1 (1–12)."
        elif (
            choice == "dozen2"
            and isinstance(spin_result, int)
            and 13 <= spin_result <= 24
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 2 (13–24)."
        elif (
            choice == "dozen3"
            and isinstance(spin_result, int)
            and 25 <= spin_result <= 36
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Dozen 3 (25–36)."
        elif (
            choice == "column1"
            and isinstance(spin_result, int)
            and (spin_result % 3 == 1)
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 1."
        elif (
            choice == "column2"
            and isinstance(spin_result, int)
            and (spin_result % 3 == 2)
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 2."
        elif (
            choice == "column3"
            and isinstance(spin_result, int)
            and (spin_result % 3 == 0 and spin_result != 0)
        ):
            winnings = amount * Decimal(3)
            outcome_description += " You bet on Column 3."
        elif (
            (choice.isdigit() and isinstance(spin_result, int) and int(choice) == spin_result)
            or (choice == "00" and spin_result == "00")
        ):
            winnings = amount * Decimal(36)
            outcome_description += " 🎉 You bet on that number!"
        else:
            outcome_description += " Better luck next time!"

        # Apply RTP boost for VIP players
        if winnings > 0:
            winnings = AmountUtils.round_currency(winnings * rtp_boost)

        # Process game result for rakeback
        await self.process_game_result(user_id, "roulette", amount)

        if winnings > 0:
            revealed_seed, new_hash = await self.bot.database.increment_win(
                user_id,
                "roulette",
                amount,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id, amount=winnings, description="Roulette Win"
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)
                return
            result_msg = (
                f"{outcome_description}\n"
                f"You won {self.currency_name} **{await self.formatter(winnings)}**!"
            )
            embed_color = discord.Color.green()
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                user_id,
                "roulette",
                amount,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
            result_msg = (
                f"{outcome_description}\n"
                f"You lost your bet of {self.currency_name} **{await self.formatter(amount)}**."
            )
            embed_color = discord.Color.red()
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )

        embed = discord.Embed(description=result_msg, color=embed_color)
        embed.set_author(
            name="Roulette", icon_url=self.utils.get_avatar_url(ctx.author)
        )
        embed.add_field(name="Your Bet", value=choice.capitalize(), inline=True)
        embed.add_field(
            name="Spin Result", value=f"{color_label} {spin_result}", inline=True
        )
        await ctx.reply(embed=embed)

        await self._remove_refund(session_id, user_id=user_id)
        await self._log_game_event(
            session_id,
            "result",
            {
                "outcome": "win" if winnings > 0 else "loss",
                "amount": str(winnings or amount),
                "spin": str(spin_result),
                "color": color_label,
            },
        )
        await self._end_game_session(
            session_id,
            outcome="win" if winnings > 0 else "loss",
            final_state={"amount": str(winnings or amount)},
        )

    @commands.command(
        name="double",
        aliases=["don", "doubleornothing"],
        description="Start a double or nothing game",
    )
    async def double_or_nothing(self, ctx: Context, bet_amount: str):
        """Start a double or nothing game. Uses Components V2 Container system."""
        user_id = ctx.author.id
        session_id = None

        PF = await self.prove_fairness(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            container = discord.ui.Container(
                discord.ui.TextDisplay(str(e)),
                accent_color=0xED4245
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            container = discord.ui.Container(
                discord.ui.TextDisplay(
                    f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                    f"**{await self.formatter(amount)} {self.currency_name}**."
                ),
                accent_color=0xFEE75C
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=-amount,
                description="Double or Nothing Initial Bet",
            )
        except ValueError as e:
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"🚫 Transaction failed: {e}"),
                accent_color=0xED4245
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)
            return

        session_id = await self._create_game_session(
            ctx,
            "double",
            owner_id=user_id,
            wager_total=amount,
            state={
                "user_id": user_id,
                "bet": str(amount),
                "wallet_id": str(wallet_id),
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=amount,
            reason="double_bet",
        )

        view = DoubleOrNothingView(
            bot=self.bot,
            initial_user=ctx.author,
            initial_amount=amount,
            winnings=amount,
            currency_name=self.currency_name,
            user_id=user_id,
            PF=PF,
            session_id=session_id,
        )
        await view.build_initial_container()

        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        msg = await ctx.reply(view=view)
        view.message = msg
        self._register_session_handler(session_id, view.force_end)

    @commands.command(
        name="blackjack", aliases=["bj", "21"], description="Play a game of blackjack"
    )
    async def blackjack(self, ctx: Context, bet_amount: str):
        """
        Play Blackjack with a fresh deck for each game.
        Uses Discord Components V2 Container system for modern UI.
        """
        user_id = ctx.author.id
        session_id = None

        PF = await self.prove_fairness(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(balance))

        try:
            amount = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            container = discord.ui.Container(
                discord.ui.TextDisplay(str(e)),
                accent_color=0xED4245  # Discord red
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)
            return

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            container = discord.ui.Container(
                discord.ui.TextDisplay(
                    f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                    f"**{await self.formatter(amount)} {self.currency_name}**."
                ),
                accent_color=0xFEE75C  # Discord yellow/orange
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)

        if amount <= 0 or amount > balance:
            container = discord.ui.Container(
                discord.ui.TextDisplay("Invalid bet amount. Please bet within your balance."),
                accent_color=0xED4245
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)
            return

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-amount, description="Blackjack Bet"
            )
        except ValueError as e:
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"🚫 Transaction failed: {e}"),
                accent_color=0xED4245
            )
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view, delete_after=5)
            return

        session_id = await self._create_game_session(
            ctx,
            "blackjack",
            owner_id=user_id,
            wager_total=amount,
            state={
                "user_id": user_id,
                "bet": str(amount),
                "wallet_id": str(wallet_id),
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=amount,
            reason="blackjack_bet",
        )

        suits = ["♥", "♦", "♣", "♠"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        deck = [f"{r}{s}" for s in suits for r in ranks]
        await self.fair_shuffle(user_id, deck)

        # Calculate house edge for RTP tracking
        house_edge = await self.calculate_house_edge(user_id)
        base_edge = Decimal("0.04")
        # RTP boost for VIP players
        rtp_boost = Decimal("1") + (base_edge - house_edge) / base_edge * Decimal("0.05") if house_edge < base_edge else Decimal("1")

        current_bet = amount
        has_doubled = False
        has_split = False
        split_hands = []  # List of split hands: [(cards, bet, is_active), ...]
        active_hand_index = 0

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
            total = sum(values)
            aces = sum(1 for c in cards if c[:-1] == "A")
            while total > 21 and aces:
                total -= 10
                aces -= 1
            return total

        def can_split(cards: list[str]) -> bool:
            """Check if the hand can be split (same rank, two cards only, and not already split)"""
            if has_split:
                return False
            if len(cards) != 2:
                return False
            rank1 = cards[0][:-1]
            rank2 = cards[1][:-1]
            return rank1 == rank2

        def is_blackjack(cards: list[str]) -> bool:
            """Check if the hand is a natural blackjack (21 with exactly 2 cards)"""
            return len(cards) == 2 and calculate_score(cards) == 21

        async def finalize_game(
            interaction, player_score, dealer_score, hand_bet, player_hand=None
        ):
            nonlocal dealer_cards
            winnings = Decimal(0)

            # Process game result for rakeback
            await self.process_game_result(user_id, "blackjack", amount)

            if player_score > 21:
                outcome = "loss"
                result = f"Bust! You lost {self.currency_name} **{await self.formatter(hand_bet)}**."
                revealed_seed, new_hash = await self.bot.database.increment_loss(
                    user_id,
                    "blackjack",
                    hand_bet,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )
            elif dealer_score > 21 or player_score > dealer_score:
                outcome = "win"
                revealed_seed, new_hash = await self.bot.database.increment_win(
                    user_id,
                    "blackjack",
                    hand_bet,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )

                # Check if it's a natural blackjack (21 with 2 cards) for 3:2 payout
                if player_hand and is_blackjack(player_hand):
                    winnings = Decimal(hand_bet) * Decimal(
                        2.5
                    )  # Natural blackjack: 3:2 (2.5x total)
                    result = f"🎉 Blackjack! You win {self.currency_name} **{await self.formatter(winnings)}**!"
                else:
                    winnings = Decimal(hand_bet) * Decimal(
                        2
                    )  # Regular win: 1:1 (2x total)
                    result = f"You win {self.currency_name} **{await self.formatter(winnings)}**!"

                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=winnings,
                        description="Blackjack Win",
                    )
                except ValueError as e:
                    container = discord.ui.Container(
                        discord.ui.TextDisplay(f"🚫 Transaction failed: {e}"),
                        accent_color=0xED4245
                    )
                    view = discord.ui.LayoutView()
                    view.add_item(container)
                    await ctx.reply(view=view, delete_after=5)
                    return
            elif player_score == dealer_score:
                outcome = "tie"
                winnings = Decimal(hand_bet)
                try:
                    revealed_seed, new_hash = await self.bot.database.increment_win(
                        user_id,
                        "blackjack",
                        hand_bet,
                        client_seed=PF["client_seed"],
                        seed_used=None,
                        nonce=PF["nonce"],
                        hash_hex=PF["server_seed_hash"],
                    )
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=winnings,
                        description="Blackjack Tie",
                    )
                except ValueError as e:
                    container = discord.ui.Container(
                        discord.ui.TextDisplay(f"🚫 Transaction failed: {e}"),
                        accent_color=0xED4245
                    )
                    view = discord.ui.LayoutView()
                    view.add_item(container)
                    await ctx.reply(view=view, delete_after=5)
                    return
                result = (
                    f"It's a tie! Your bet of {self.currency_name} "
                    f"**{await self.formatter(hand_bet)}** has been returned."
                )
            else:
                outcome = "loss"
                revealed_seed, new_hash = await self.bot.database.increment_loss(
                    user_id,
                    "blackjack",
                    hand_bet,
                    client_seed=PF["client_seed"],
                    seed_used=None,
                    nonce=PF["nonce"],
                    hash_hex=PF["server_seed_hash"],
                )
                result = f"Dealer wins! You lost {self.currency_name} **{await self.formatter(hand_bet)}**."

            return outcome, result, winnings

        async def build_game_container(accent_color: int, content_text: str, buttons_disabled: bool = False) -> discord.ui.Container:
            """Build a Container with game state and action buttons."""
            if buttons_disabled:
                hit_button.disabled = True
                stay_button.disabled = True
                double_button.disabled = True
                split_button.disabled = True

            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🃏 Blackjack"),
                discord.ui.TextDisplay(content_text),
                discord.ui.Separator(),
                discord.ui.ActionRow(hit_button, stay_button, double_button, split_button),
                accent_color=accent_color
            )

            return container

        async def finalize_all_hands(interaction):
            nonlocal dealer_score

            # Dealer plays
            while dealer_score < 17:
                dealer_cards.append(draw_card())
                dealer_score = calculate_score(dealer_cards)

            results = []
            total_winnings = Decimal(0)
            outcomes = []

            if has_split:
                for idx, (hand_cards, hand_bet, _) in enumerate(split_hands):
                    hand_score = calculate_score(hand_cards)
                    outcome, result, winnings = await finalize_game(
                        interaction, hand_score, dealer_score, hand_bet, hand_cards
                    )
                    outcomes.append(outcome)
                    total_winnings += winnings
                    results.append(
                        f"**Hand {idx + 1}:** {', '.join(hand_cards)} (Total: **{hand_score}**)\n{result}"
                    )
            else:
                player_score = calculate_score(player_cards)
                outcome, result, winnings = await finalize_game(
                    interaction, player_score, dealer_score, current_bet, player_cards
                )
                outcomes.append(outcome)
                total_winnings += winnings
                results.append(
                    f"**Your cards:** {', '.join(player_cards)} (Total: **{player_score}**)\n{result}"
                )

            hit_button.disabled = True
            stay_button.disabled = True
            double_button.disabled = True
            split_button.disabled = True

            dealer_initial = dealer_cards[0]
            dealer_hits = dealer_cards[1:]
            if dealer_hits:
                hits_text = f"{', '.join([dealer_initial] + dealer_hits)}"
            else:
                hits_text = dealer_initial

            if total_winnings > 0:
                accent_color = 0x57F287  # Discord green
            else:
                accent_color = 0xED4245  # Discord red

            result_text = (
                f"{chr(10).join(results)}\n\n"
                f"Bots cards: {hits_text} (Total: **{dealer_score}**)\n\n"
                f"**Total Winnings:** {self.currency_name} **{await self.formatter(total_winnings)}**"
            )

            container = await build_game_container(accent_color, result_text, buttons_disabled=True)

            view = discord.ui.LayoutView()
            view.add_item(container)
            await interaction.edit_original_response(view=view)

            if outcomes and all(o == "tie" for o in outcomes):
                final_outcome = "tie"
            elif any(o == "win" for o in outcomes):
                final_outcome = "win"
            else:
                final_outcome = "loss"

            await self._remove_refund(session_id, user_id=user_id)
            await self._end_game_session(
                session_id,
                outcome=final_outcome,
                final_state={"total_winnings": str(total_winnings)},
            )

        async def update_game_view(interaction):
            """Update the game view with current state."""
            if has_split:
                hands_display = []
                for idx, (h_cards, h_bet, h_active) in enumerate(split_hands):
                    h_score = calculate_score(h_cards)
                    marker = "▶️" if idx == active_hand_index and h_active else "✅"
                    hands_display.append(
                        f"{marker} **Hand {idx + 1}:** {', '.join(h_cards)} (Total: **{h_score}**) "
                        f"Bet: {self.currency_name} **{await self.formatter(h_bet)}**"
                    )

                content_text = (
                    f"{chr(10).join(hands_display)}\n\n"
                    f"Bots visible card: {dealer_cards[0]}"
                )
            else:
                player_score = calculate_score(player_cards)
                content_text = (
                    f"Your cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
                    f"Bots visible card: {dealer_cards[0]}\n"
                    f"Current bet: {self.currency_name} **{await self.formatter(current_bet)}**"
                )

            container = await build_game_container(0x5865F2, content_text)  # Discord blurple

            view = discord.ui.LayoutView()
            view.add_item(container)
            await interaction.edit_original_response(view=view)

        async def move_to_next_hand(interaction):
            nonlocal active_hand_index

            # Mark current hand as inactive
            if has_split and active_hand_index < len(split_hands):
                hand_cards, hand_bet, _ = split_hands[active_hand_index]
                split_hands[active_hand_index] = (hand_cards, hand_bet, False)

            # Find next active hand
            active_hand_index += 1
            if active_hand_index >= len(split_hands):
                # All hands done, finalize
                await finalize_all_hands(interaction)
                return True

            # Update buttons for next hand
            split_button.disabled = True
            double_button.disabled = False

            return False

        player_cards = [draw_card(), draw_card()]
        dealer_cards = [draw_card()]
        player_score = calculate_score(player_cards)
        dealer_score = calculate_score(dealer_cards)

        # Define buttons first so they can be referenced in build_game_container
        hit_button = Button(label="Hit", style=discord.ButtonStyle.primary)
        stay_button = Button(label="Stay", style=discord.ButtonStyle.secondary)
        double_button = Button(label="Double Down", style=discord.ButtonStyle.success)
        split_button = Button(
            label="Split",
            style=discord.ButtonStyle.success,
            disabled=not can_split(player_cards),
        )

        async def hit_callback(interaction: Interaction):
            nonlocal player_score, dealer_score, active_hand_index
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)

            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This is not your game!", ephemeral=True
                )
                return

            if has_split:
                hand_cards, hand_bet, is_active = split_hands[active_hand_index]
                hand_cards.append(draw_card())
                hand_score = calculate_score(hand_cards)
                split_hands[active_hand_index] = (hand_cards, hand_bet, is_active)

                # Disable double down and split after first hit
                double_button.disabled = True
                split_button.disabled = True

                if hand_score >= 21:
                    finished = await move_to_next_hand(interaction)
                    if not finished:
                        await update_game_view(interaction)
                else:
                    await update_game_view(interaction)
            else:
                player_cards.append(draw_card())
                player_score = calculate_score(player_cards)

                # Disable actions after first hit
                double_button.disabled = True
                split_button.disabled = True

                if player_score == 21:
                    while dealer_score < 17:
                        dealer_cards.append(draw_card())
                        dealer_score = calculate_score(dealer_cards)
                    await finalize_all_hands(interaction)
                    return
                elif player_score > 21:
                    await finalize_all_hands(interaction)
                    return
                else:
                    await update_game_view(interaction)

        async def stay_callback(interaction: Interaction):
            nonlocal dealer_score
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)

            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This is not your game!", ephemeral=True
                )
                return

            if has_split:
                finished = await move_to_next_hand(interaction)
                if not finished:
                    await update_game_view(interaction)
            else:
                await finalize_all_hands(interaction)

        async def double_callback(interaction: Interaction):
            nonlocal \
                player_score, \
                dealer_score, \
                current_bet, \
                has_doubled, \
                active_hand_index
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)

            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This is not your game!", ephemeral=True
                )
                return

            bet_to_double = (
                current_bet if not has_split else split_hands[active_hand_index][1]
            )

            # Check if player has enough balance to double
            current_balance = await self.bot.database.get_wallet_balance(wallet_id)
            if bet_to_double > Decimal(str(current_balance)):
                await interaction.followup.send(
                    "Insufficient balance to double down!", ephemeral=True
                )
                return

            # Deduct additional bet
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-bet_to_double,
                    description="Blackjack Double Down",
                )
            except ValueError as e:
                await interaction.followup.send(
                    f"🚫 Transaction failed: {e}", ephemeral=True
                )
                return

            has_doubled = True

            if has_split:
                hand_cards, hand_bet, is_active = split_hands[active_hand_index]
                hand_bet *= Decimal(2)
                hand_cards.append(draw_card())
                split_hands[active_hand_index] = (hand_cards, hand_bet, is_active)

                finished = await move_to_next_hand(interaction)
                if not finished:
                    await update_game_view(interaction)
            else:
                current_bet *= Decimal(2)
                player_cards.append(draw_card())
                player_score = calculate_score(player_cards)
                await finalize_all_hands(interaction)

        async def split_callback(interaction: Interaction):
            nonlocal has_split, split_hands, current_bet
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)

            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This is not your game!", ephemeral=True
                )
                return

            # Check if splitting is still allowed
            if has_split:
                await interaction.followup.send(
                    "You can only split once per game!", ephemeral=True
                )
                return

            # Check if player has enough balance for the split
            current_balance = await self.bot.database.get_wallet_balance(wallet_id)
            if current_bet > Decimal(str(current_balance)):
                await interaction.followup.send(
                    "Insufficient balance to split!", ephemeral=True
                )
                return

            # Deduct additional bet for second hand
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-current_bet,
                    description="Blackjack Split",
                )
            except ValueError as e:
                await interaction.followup.send(
                    f"🚫 Transaction failed: {e}", ephemeral=True
                )
                return

            has_split = True

            # Create two hands from the split
            card1 = player_cards[0]
            card2 = player_cards[1]

            hand1 = [card1, draw_card()]
            hand2 = [card2, draw_card()]

            split_hands = [(hand1, current_bet, True), (hand2, current_bet, False)]

            # Disable split button after splitting
            split_button.disabled = True

            await update_game_view(interaction)

        hit_button.callback = hit_callback
        stay_button.callback = stay_callback
        double_button.callback = double_callback
        split_button.callback = split_callback

        # Build initial game view
        initial_content = (
            f"Your cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
            f"Bots visible card: {dealer_cards[0]}\n"
            f"Current bet: {self.currency_name} **{await self.formatter(current_bet)}**"
        )

        container = await build_game_container(0x5865F2, initial_content)  # Discord blurple

        view = discord.ui.LayoutView()
        view.add_item(container)

        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        await ctx.reply(view=view)

    @commands.command(
        name="poker", aliases=["headsup"], description="Play a game of Heads-Up Poker"
    )
    async def poker(self, ctx: commands.Context, bet_amount: str):
        user_id = ctx.author.id
        PF = await self.prove_fairness(user_id)
        session_id = None

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

        try:
            bet = await self.amount_handler(bet_amount, balance)
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=str(e), color=discord.Color.red()),
                delete_after=5,
            )

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if bet > max_allowed:
            bet = max_allowed
            await ctx.reply(
                embed=discord.Embed(
                    description=(
                        "You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(bet)} {self.currency_name}**"
                    ),
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-bet, description="Poker Bet"
            )
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()),
                delete_after=5,
            )

        session_id = await self._create_game_session(
            ctx,
            "poker",
            owner_id=user_id,
            wager_total=bet,
            state={
                "user_id": user_id,
                "bet": str(bet),
                "wallet_id": str(wallet_id),
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=bet,
            reason="poker_bet",
        )

        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)

        suits = ["♥", "♦", "♣", "♠"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        deck = [f"{r}{s}" for s in suits for r in ranks]
        await self.fair_shuffle(user_id, deck)

        player_hand = [deck.pop(), deck.pop()]
        bot_hand = [deck.pop(), deck.pop()]
        community = [deck.pop() for _ in range(5)]

        description = (
            f"**Your Hole Cards:** {', '.join(player_hand)}\n"
            "Do you want to **Play** or **Fold**?"
        )
        embed = discord.Embed(
            title="Heads-Up Poker",
            description=description,
            color=discord.Color.blurple(),
        )

        view = PokerView(
            self.bot,
            self,
            ctx.author,
            player_hand,
            bot_hand,
            community,
            bet,
            wallet_id,
            user_id,
            PF,
            session_id=session_id,
        )
        msg = await ctx.reply(embed=embed, view=view)
        view.message = msg
        self._register_session_handler(session_id, view.force_end)

    @commands.command(
        name="baccarat",
        aliases=["bac", "punto"],
        description="Bet on Player, Banker, or Tie – provably fair Baccarat!",
    )
    async def baccarat(self, ctx: Context, side: str, amount: str):
        """
        side: 'player', 'banker', or 'tie'
        amount: number | 'all' | etc (handled by self.amount_handler)
        """

        side = side.lower()
        if side not in ("player", "banker", "tie"):
            return await ctx.reply("Valid sides: `player`, `banker`, `tie`")

        uid = ctx.author.id
        currency = self.currency_name
        session_id = None

        PF = await self.prove_fairness(uid)

        house_edge = await self.calculate_house_edge(uid)

        wallet_id = await self.bot.database.get_wallet_id_for_user(uid)
        bal_raw = await self.bot.database.get_wallet_balance(wallet_id)
        balance = Decimal(str(bal_raw))

        try:
            stake = await self.amount_handler(amount, balance)
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=str(e), color=discord.Color.red()),
                delete_after=5,
            )

        max_allowed = await self.bot.database.get_max_gamble_amount(uid)
        if stake > max_allowed:
            stake = max_allowed
            await ctx.reply(
                embed=discord.Embed(
                    description=f"Bet capped at **{await self.formatter(stake)} {currency}** due to limits.",
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id, -stake, "Baccarat Bet"
            )
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()),
                delete_after=5,
            )

        session_id = await self._create_game_session(
            ctx,
            "baccarat",
            owner_id=uid,
            wager_total=stake,
            state={
                "user_id": uid,
                "bet": str(stake),
                "wallet_id": str(wallet_id),
                "side": side,
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=uid,
            wallet_id=str(wallet_id),
            amount=stake,
            reason="baccarat_bet",
        )

        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        card_val = {r: i for i, r in enumerate(ranks, start=1)} | {
            "10": 0,
            "J": 0,
            "Q": 0,
            "K": 0,
        }
        suits = ["♥", "♦", "♣", "♠"]

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
            (b_total <= 2)
            or (b_total == 3 and (not player_draws or player[-1][:-1] != "8"))
            or (b_total == 4 and player_draws and 2 <= card_val[player[-1][:-1]] <= 7)
            or (b_total == 5 and player_draws and 4 <= card_val[player[-1][:-1]] <= 7)
            or (b_total == 6 and player_draws and 6 <= card_val[player[-1][:-1]] <= 7)
        )

        if banker_draws:
            banker.append(draw())
            b_total = total(banker)

        result = (
            "player" if p_total > b_total else "banker" if b_total > p_total else "tie"
        )

        payout_mult = Decimal("0")
        if side == "player" and result == "player":
            payout_mult = Decimal("1")
        elif side == "banker" and result == "banker":
            # Apply house edge to banker commission
            # Standard is 5% commission, adjust based on VIP tier
            # house_edge is already calculated (default 0.04 = 4%)
            # Reduce commission for VIP players (lower house edge = lower commission)
            base_commission = Decimal("0.05")  # 5% standard commission
            commission = base_commission * house_edge / Decimal("0.04")  # Scale with house edge
            commission = min(commission, base_commission)  # Cap at 5%
            payout_mult = Decimal("1") - commission
            # Super six: banker wins with 6 (3 cards) pays only 0.5:1
            if b_total == 6 and len(banker) == 3:
                payout_mult = Decimal("0.5")
        elif side == "tie" and result == "tie":
            payout_mult = Decimal("8")

        winnings = AmountUtils.round_currency(stake * payout_mult)
        profit = AmountUtils.round_currency(stake * payout_mult)

        # Process game result for rakeback
        await self.process_game_result(uid, "baccarat", stake)

        if payout_mult > 0:
            total_return = AmountUtils.round_currency(stake + profit)
            await self.bot.database.process_treasury_transaction(
                wallet_id, total_return, "Baccarat Payout"
            )

            revealed_seed, new_hash = await self.bot.database.increment_win(
                uid,
                "baccarat",
                stake,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                uid,
                "baccarat",
                stake,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )


        def prettify(hand):
            return " ".join(hand)

        title = f"🏦 Banker {b_total} – 👤 Player {p_total}"
        color = discord.Color.green() if winnings else discord.Color.red()
        display_amt = profit if payout_mult > 0 else stake
        label = "Won" if payout_mult > 0 else "Lost"
        desc = (
            f"**Result:** {result.capitalize()}\n"
            f"**Your Bet:** {side.capitalize()}\n\n"
            f"👤 Player {prettify(player)}\n"
            f"🏦 Banker {prettify(banker)}\n\n"
            f"{label} **{await self.formatter(display_amt)} {currency}**"
            f"({payout_mult}×)"
        )

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_author(
            name="Baccarat", icon_url=self.utils.get_avatar_url(ctx.author)
        )

        await self.bot.database.set_cooldown(uid, ctx.command.qualified_name, 5)
        await ctx.reply(embed=embed)

        await self._remove_refund(session_id, user_id=uid)
        await self._log_game_event(
            session_id,
            "result",
            {
                "outcome": "win" if payout_mult > 0 else "loss",
                "amount": str(display_amt),
                "result": result,
            },
        )
        await self._end_game_session(
            session_id,
            outcome="win" if payout_mult > 0 else "loss",
            final_state={"amount": str(display_amt)},
        )

    @commands.command(
            name="hilo", description="Play Hi-Lo - a simple card guessing game!"
        )
    async def hilo(self, ctx: Context, bet_amount: str):
        """Play HiLo - guess if the next card will be higher or lower (Stake-style)"""
        user = ctx.author
        session_id = None

        if user.id in self.active_players:
            await ctx.reply("🚫 You already have an active game running! Finish it first.", delete_after=5)
            return
        
        self.active_players.add(user.id)

        try:
            card_emojis = {
                "A": "<:ace:1361825338539376651>",
                "2": "<:two:1361825398974971904>",
                "3": "<:three:1361825438732779672>",
                "4": "<:four:1361825468784836778>",
                "5": "<:five:1361825513416687697>",
                "6": "<:six:1361825565690036435>",
                "7": "<:seven:1361825612070912302>",
                "8": "<:eight:1361825649383182558>",
                "9": "<:nine:1361825685311721593>",
                "10": "<:ten:1361825715665764514>",
                "J": "<:jack:1361825819478982686>",
                "Q": "<:queen:1361825774461653084>",
                "K": "<:king:1361825747051741475>",
                "back": "<:uncovered:1361825843525194029>",
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

                self.active_players.remove(user.id)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if bet_amount > max_allowed:
                bet_amount = max_allowed
                await ctx.reply(
                    embed=discord.Embed(
                        description=(
                            "You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                            f"**{await self.formatter(bet_amount)} {self.currency_name}**."
                        ),
                        color=discord.Color.orange(),
                    ),
                    delete_after=5,
                )

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id, amount=-bet_amount, description="HiLo Bet"
                )

            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                await ctx.reply(embed=embed, delete_after=5)

                self.active_players.remove(user.id)
                return

            session_id = await self._create_game_session(
                ctx,
                "hilo",
                owner_id=user_id,
                wager_total=bet_amount,
                state={
                    "user_id": user_id,
                    "bet": str(bet_amount),
                    "wallet_id": str(wallet_id),
                },
                rng=PF,
            )
            await self._add_refund(
                session_id,
                user_id=user_id,
                wallet_id=str(wallet_id),
                amount=bet_amount,
                reason="hilo_bet",
            )

            # Calculate dynamic house edge based on VIP tier and active RTP boosts
            house_edge = await self.calculate_house_edge(user_id)

            cards = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
            card_values = {card: idx for idx, card in enumerate(cards)}
            current_card = await self.fair_choice(ctx.author.id, cards[1:-1])

            game_state = {
                "multiplier": Decimal("1.0"),
                "current_card": current_card,
                "game_active": True,
                "history": [],
                "bet_amount": bet_amount,
                "message": None,
                "view": None,
                "has_played": False,
                "skips_used": 0,
                "session_id": session_id,
                "house_edge": house_edge,
            }

            def calculate_multiplier(current_card, action, edge=house_edge):
                current_value = card_values[current_card]
                if action == "higher":
                    favorable = len(
                        [c for c in cards if card_values[c] > current_value]
                    )
                    total = len([c for c in cards if card_values[c] >= current_value])
                elif action == "lower":
                    favorable = len(
                        [c for c in cards if card_values[c] < current_value]
                    )
                    total = len([c for c in cards if card_values[c] <= current_value])

                if favorable == 0 or total == 0:
                    return Decimal("0")

                probability = Decimal(favorable) / Decimal(total)

                # Fair multiplier (1 / probability) before house edge
                fair_mult = Decimal("1.0") / probability if probability != 0 else Decimal("0")

                # Apply dynamic house edge based on VIP tier and RTP boosts
                multiplier = fair_mult * (Decimal("1.0") - edge)

                # Clamp multipliers into typical ranges used by casinos:
                # - Middle / ~50% chances -> ~1.9x-2.0x
                # - Strong favorites (high probability) -> ~1.02x-1.5x
                # - Riskier guesses (low probability) -> ~2.0x-5.0x
                if Decimal("1.95") <= fair_mult <= Decimal("2.05"):
                    min_m, max_m = Decimal("1.90"), Decimal("2.00")
                elif fair_mult <= Decimal("1.5"):
                    min_m, max_m = Decimal("1.02"), Decimal("1.50")
                else:
                    min_m, max_m = Decimal("2.00"), Decimal("5.00")

                if multiplier < min_m:
                    multiplier = min_m
                if multiplier > max_m:
                    multiplier = max_m

                return multiplier

            def calculate_probs(card):
                current_value = card_values[card]
                higher = len([c for c in cards if card_values[c] > current_value])
                lower = len([c for c in cards if card_values[c] < current_value])

                higher_prob = round((higher / (len(cards) - 1)) * 100)
                lower_prob = round((lower / (len(cards) - 1)) * 100)

                higher_prob = max(8, min(92, higher_prob))
                lower_prob = max(8, min(92, lower_prob))

                return higher_prob, lower_prob

            def calculate_profits(card, current_multiplier, bet_amount):
                current_value = card_values[card]
                higher_mult = calculate_multiplier(card, "higher")
                lower_mult = calculate_multiplier(card, "lower")

                profit_higher = (
                    bet_amount * current_multiplier * higher_mult
                ) - bet_amount
                profit_lower = (
                    bet_amount * current_multiplier * lower_mult
                ) - bet_amount
                total_profit = (bet_amount * current_multiplier) - bet_amount

                return profit_higher, profit_lower, total_profit

            async def create_embed(status="Playing"):
                higher_prob, lower_prob = calculate_probs(game_state["current_card"])
                profit_higher, profit_lower, total_profit = calculate_profits(
                    game_state["current_card"],
                    game_state["multiplier"],
                    game_state["bet_amount"],
                )

                formatted_bet = await self.formatter(game_state["bet_amount"])
                formatted_potential = await self.formatter(
                    game_state["bet_amount"] * game_state["multiplier"]
                )
                formatted_profit_higher = await self.formatter(profit_higher)
                formatted_profit_lower = await self.formatter(profit_lower)
                formatted_total_profit = await self.formatter(total_profit)

                embed = discord.Embed(
                    title=f"<:uncovered:1361825843525194029> HiLo - {status}",
                    color=discord.Color.blurple(),
                )

                embed.add_field(
                    name="Current Card",
                    value=f"{card_emojis.get(game_state['current_card'], card_emojis['back'])} **{game_state['current_card']}**",
                    inline=False,
                )

                history_display = []
                for card in game_state["history"]:
                    history_display.append(f"{card_emojis.get(card, '🃏')} {card}")
                history_display.append(
                    f"{card_emojis.get(game_state['current_card'], '🃏')} {game_state['current_card']}"
                )
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
                        inline=False,
                    )

                embed.add_field(
                    name="History", value=" → ".join(history_display), inline=False
                )

                return embed

            async def end_game(win: bool = False):
                game_state["game_active"] = False

                if user.id in self.active_players:
                    self.active_players.remove(user.id)

                if game_state["view"]:
                    for child in game_state["view"].children:
                        child.disabled = True

                # Process rakeback for the wagered amount
                await self.process_game_result(user_id, "hilo", game_state["bet_amount"])

                if win:
                    revealed_seed, new_hash = await self.bot.database.increment_win(
                        user_id,
                        "hilo",
                        game_state["bet_amount"],
                        client_seed=PF["client_seed"],
                        seed_used=None,
                        nonce=PF["nonce"],
                        hash_hex=PF["server_seed_hash"],
                    )
                    await self._remove_refund(session_id, user_id=user_id)
                    await self._end_game_session(
                        session_id,
                        outcome="win",
                        final_state={
                            "winnings": str(
                                game_state["bet_amount"] * game_state["multiplier"]
                            )
                        },
                    )
                    return game_state["bet_amount"] * game_state["multiplier"]
                await self._remove_refund(session_id, user_id=user_id)
                await self._end_game_session(
                    session_id,
                    outcome="loss",
                    final_state={"loss": str(game_state["bet_amount"])},
                )
                return None

            async def update_display(interaction: discord.Interaction | None = None):
                if not game_state["game_active"]:
                    return

                higher_prob, lower_prob = calculate_probs(game_state["current_card"])
                new_embed = await create_embed()

                for child in game_state["view"].children:
                    if child.custom_id == "higher":
                        child.label = f"Higher ({higher_prob}%)"
                    elif child.custom_id == "lower":
                        child.label = f"Lower ({lower_prob}%)"
                    elif child.custom_id == "cashout":
                        child.label = f"Cash Out {game_state['multiplier']:.2f}x"

                try:
                    if interaction and interaction.response.is_done():
                        await interaction.followup.edit_message(
                            game_state["message"].id,
                            embed=new_embed,
                            view=game_state["view"],
                        )
                    elif interaction and not interaction.response.is_done():
                        await interaction.response.edit_message(
                            embed=new_embed, view=game_state["view"]
                        )
                    else:
                        await game_state["message"].edit(
                            embed=new_embed, view=game_state["view"]
                        )
                except Exception as e:
                    self.bot.logger.error(f"Error updating display: {e}")

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
                embed.add_field(name="Result", value=result_text, inline=False)

                try:
                    if game_state["message"]:
                        await game_state["message"].edit(
                            embed=embed, view=game_state["view"]
                        )
                except Exception as e:
                    self.bot.logger.error(f"Error showing result: {e}")

            async def handle_interaction_response(
                interaction: Interaction, message=None, ephemeral=True
            ):
                try:
                    if message:
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(
                                    message, ephemeral=ephemeral
                                )
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
                    self.bot.logger.error(f"Error handling interaction response: {e}")
                    return False
                return True

            async def higher_callback(interaction: discord.Interaction):
                if interaction.user.id != user.id:
                    return await interaction.response.send_message("This isn't your game!", ephemeral=True)
                
                if not game_state["game_active"]:
                    return

                try:
                    await interaction.response.defer()
                    game_state["has_played"] = True
                    game_state["history"].append(game_state["current_card"])
                    
                    next_card = await self.fair_choice(user.id, cards)

                    if next_card in ["A", "K"]:
                        game_state["current_card"] = next_card
                        await end_game(False)
                        await show_result(False) 
                        await update_display(interaction)
                        return

                    multiplier_increase = calculate_multiplier(
                        game_state["current_card"], "higher"
                    )

                    current_value = card_values[game_state["history"][-1]]
                    next_value = card_values[next_card]

                    if next_value > current_value:
                        game_state["multiplier"] *= multiplier_increase
                        game_state["current_card"] = next_card
                    elif next_value == current_value:
                        game_state["current_card"] = next_card
                    else:
                        game_state["current_card"] = next_card
                        await end_game(False)
                        await show_result(False)

                    await update_display(interaction)

                except Exception as e:
                    self.bot.logger.error(f"Error in higher callback: {e}")

            async def lower_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await interaction.response.send_message(
                        "This isn't your game!", ephemeral=True
                    )
                    return
                if not game_state["game_active"]:
                    return

                try:
                    await interaction.response.defer()
                    game_state["has_played"] = True
                    game_state["history"].append(game_state["current_card"])
                    next_card = await self.fair_choice(user.id, cards)

                    if next_card in ["A", "K"]:
                        game_state["current_card"] = next_card
                        await end_game(False)
                        await show_result(False) 
                        await update_display(interaction)
                        return

                    multiplier_increase = calculate_multiplier(
                        game_state["current_card"], "lower"
                    )

                    current_value = card_values[game_state["history"][-1]]
                    next_value = card_values[next_card]

                    if next_value < current_value:
                        game_state["multiplier"] *= multiplier_increase
                        game_state["current_card"] = next_card
                    elif next_value == current_value:
                        game_state["current_card"] = next_card
                    else:
                        game_state["current_card"] = next_card
                        await end_game(False)
                        await show_result(False)

                    await update_display(interaction)

                except Exception as e:
                    self.bot.logger.error(f"Error in lower callback: {e}")

            async def skip_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await handle_interaction_response(
                        interaction, "This isn't your game!"
                    )
                    return
                if not game_state["game_active"]:
                    return

                if game_state["skips_used"] >= 3:
                    await interaction.response.send_message(
                        "🚫 Limit reached! You can only skip 3 times per game.", 
                        ephemeral=True
                    )
                    return

                try:
                    await handle_interaction_response(interaction)

                    game_state["skips_used"] += 1

                    game_state["history"].append(game_state["current_card"])
                    next_card = await self.fair_choice(user.id, cards)
                    game_state["current_card"] = next_card
                    await update_display(interaction)

                except Exception as e:
                    self.bot.logger.error(f"Error in skip callback: {e}")

            async def cashout_callback(interaction: Interaction):
                if interaction.user.id != user.id:
                    await handle_interaction_response(
                        interaction, "This isn't your game!"
                    )
                    return
                if not game_state["game_active"]:
                    await handle_interaction_response(
                        interaction, "Game is not active!", ephemeral=True
                    )
                    return
                if not game_state["has_played"]:
                    await handle_interaction_response(
                        interaction,
                        "🚫 You must make at least one guess (Higher/Lower) before cashing out!",
                        ephemeral=True,
                    )
                    return

                try:
                    await handle_interaction_response(interaction)

                    bet = game_state["bet_amount"]
                    current_multiplier = game_state["multiplier"]
                    winnings = bet * current_multiplier
                    wallet_id = await self.bot.database.get_wallet_id_for_user(
                        interaction.user.id
                    )

                    try:
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=wallet_id, amount=winnings, description="HiLo Win"
                        )
                    except ValueError as e:
                        embed = discord.Embed(
                            description=f"🚫 Transaction failed: {e}",
                            color=discord.Color.red(),
                        )
                        await ctx.reply(embed=embed, delete_after=5)
                        return

                    await end_game(True)
                    await show_result(True)

                except Exception as e:
                    self.bot.logger.error(f"Error in cashout callback: {e}")
                    await handle_interaction_response(
                        interaction,
                        "An error occurred while cashing out!",
                        ephemeral=True,
                    )

            higher_prob, lower_prob = calculate_probs(current_card)

            view = discord.ui.View(timeout=300.0)
            game_state["view"] = view

            view.add_item(
                discord.ui.Button(
                    label=f"Higher ({higher_prob}%)",
                    style=discord.ButtonStyle.gray,
                    custom_id="higher",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label=f"Lower ({lower_prob}%)",
                    style=discord.ButtonStyle.gray,
                    custom_id="lower",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Skip Card", style=discord.ButtonStyle.gray, custom_id="skip"
                )
            )
            view.add_item(
                discord.ui.Button(
                    label=f"Cash Out ({game_state['multiplier']:.2f}x)",
                    style=discord.ButtonStyle.blurple,
                    custom_id="cashout",
                )
            )

            view.children[0].callback = higher_callback
            view.children[1].callback = lower_callback
            view.children[2].callback = skip_callback
            view.children[3].callback = cashout_callback

            embed = await create_embed()
            game_state["message"] = await ctx.reply(embed=embed, view=view)

            async def force_end(refund: bool = False):
                game_state["game_active"] = False
                for child in game_state["view"].children:
                    child.disabled = True
                if refund:
                    wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=game_state["bet_amount"],
                        description="HiLo Refund",
                    )
                try:
                    await game_state["message"].edit(view=game_state["view"])
                except Exception:
                    pass
                await self._remove_refund(session_id, user_id=user_id)
                await self._end_game_session(
                    session_id,
                    outcome="forced_end",
                    final_state={"refund": refund},
                )

            self._register_session_handler(session_id, force_end)

        except Exception as e:
            if user.id in self.active_players:
                self.active_players.remove(user.id)
            
            self.bot.logger.error(f"Error in hilo command: {e}", exc_info=True)
            error_embed = discord.Embed(
                title="⚠️ Error",
                description="An error occurred while starting the game.",
                color=discord.Color.red(),
            )
            await ctx.reply(embed=error_embed, delete_after=5)

    @commands.command(
        name="ladder",
        aliases=["luckyladder"],
        description="Play Ladder - a high-risk, high-reward game!",
    )
    async def luckyladder(self, ctx: Context, bet_amount: str):
        """Start climbing the Lucky Ladder with a bet."""
        user_id = ctx.author.id
        PF = await self.prove_fairness(user_id)
        session_id = None

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))
        currency_name = self.currency_name

        try:
            amount = await self.amount_handler(bet_amount, balance)
            amount = Decimal(amount)
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(description=str(e), color=discord.Color.red()),
                delete_after=5,
            )

        max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
        if amount > max_allowed:
            amount = max_allowed
            await ctx.reply(
                embed=discord.Embed(
                    description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                    f"**{await self.formatter(amount)} {self.currency_name}**.",
                    color=discord.Color.orange(),
                ),
                delete_after=5,
            )

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id, amount=-amount, description="Lucky Ladder bet"
            )
        except ValueError as e:
            return await ctx.reply(
                embed=discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                ),
                delete_after=5,
            )

        session_id = await self._create_game_session(
            ctx,
            "ladder",
            owner_id=user_id,
            wager_total=amount,
            state={
                "user_id": user_id,
                "bet": str(amount),
                "wallet_id": str(wallet_id),
            },
            rng=PF,
        )
        await self._add_refund(
            session_id,
            user_id=user_id,
            wallet_id=str(wallet_id),
            amount=amount,
            reason="ladder_bet",
        )

        self.ladder_games[user_id] = {
            "step": 0,
            "bet": amount,
            "current_multiplier": Decimal("1.00"),
            "start_time": discord.utils.utcnow(),
            "PF": PF,
            "session_id": session_id,
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
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Status",
            value=(
                "**Current Step:** 0\n"
                "**Success Chance:** 80%\n"
                "**Current Multiplier:** 1.00x\n"
                "**Potential Next Multiplier:** 1.15x"
            ),
            inline=False,
        )

        view = LadderView(user_id, self)
        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)
        msg = await ctx.reply(embed=embed, view=view)
        view.message = msg

        async def force_end(refund: bool = False):
            game = self.ladder_games.pop(user_id, None)
            if refund and game:
                wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=game["bet"],
                    description="Lucky Ladder Refund",
                )
            for child in view.children:
                child.disabled = True
            if view.message:
                try:
                    await view.message.edit(view=view)
                except Exception:
                    pass
            await self._remove_refund(session_id, user_id=user_id)
            await self._end_game_session(
                session_id,
                outcome="forced_end",
                final_state={"refund": refund},
            )

        self._register_session_handler(session_id, force_end)

    async def climb_ladder(self, interaction: discord.Interaction, user_id: int):
        """Handles the player's climb action with provably-fair RNG."""
        game = self.ladder_games.get(user_id)
        if not game:
            return await interaction.response.send_message(
                "You are not currently playing Lucky Ladder.", ephemeral=True
            )

        if (discord.utils.utcnow() - game["start_time"]).total_seconds() > 300:
            del self.ladder_games[user_id]
            return await interaction.response.send_message(
                "Your game has expired. Please start a new one.", ephemeral=True
            )

        PF = game["PF"]
        step = game["step"]
        bet = game["bet"]

        step_probs = {
            0: 80,
            1: 75,
            2: 70,
            3: 65,
            4: 60,
            5: 55,
            6: 50,
            7: 45,
            8: 40,
            9: 35,
            10: 30,
        }
        step_mults = {
            0: 1.00,
            1: 1.15,
            2: 1.30,
            3: 1.50,
            4: 1.75,
            5: 2.05,
            6: 2.40,
            7: 2.80,
            8: 3.25,
            9: 3.75,
            10: 4.20,
        }

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
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Status",
                value=(
                    f"**Current Step:** {game['step']}\n"
                    f"**Current Multiplier:** {game['current_multiplier']:.2f}x\n"
                    f"**Current Winnings:** {await self.formatter(current_winnings)}\n"
                    f"**Next Step Chance:** {next_chance}%\n"
                    f"**Next Multiplier:** {next_mult}x"
                ),
                inline=False,
            )

            if game["step"] == 10:
                wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=current_winnings,
                        description="Lucky Ladder Max Win!",
                    )
                except ValueError as e:
                    return await interaction.response.send_message(
                        embed=discord.Embed(
                            description=f"🚫 {e}", color=discord.Color.red()
                        ),
                        delete_after=5,
                    )
                embed.description = (
                    f"🏆 **Congratulations!!** You've reached the top!\n"
                    f"You won **{await self.formatter(current_winnings)}**!"
                )
                await self._remove_refund(game.get("session_id"), user_id=user_id)
                await self._end_game_session(
                    game.get("session_id"),
                    outcome="win",
                    final_state={"winnings": str(current_winnings)},
                )
                del self.ladder_games[user_id]
                return await interaction.response.edit_message(embed=embed, view=None)

            view = LadderView(user_id, self)
            await interaction.response.edit_message(embed=embed, view=view)

        else:
            revealed_seed, new_hash = await self.bot.database.increment_loss(
                user_id,
                "ladder",
                bet,
                client_seed=PF["client_seed"],
                seed_used=None,
                nonce=PF["nonce"],
                hash_hex=PF["server_seed_hash"],
            )
            embed = discord.Embed(
                title="Lucky Ladder",
                description=(
                    f"💥 Oh no! You fell from step {step}!\n"
                    f"You lost {await self.formatter(bet)}\n\n"
                    f"Success chance: {success_chance}%\n"
                    f"You rolled: {(roll/100):.2f}"
                ),
                color=discord.Color.red(),
            )
            await self._remove_refund(game.get("session_id"), user_id=user_id)
            await self._end_game_session(
                game.get("session_id"),
                outcome="loss",
                final_state={"loss": str(bet)},
            )
            del self.ladder_games[user_id]
            await interaction.response.edit_message(embed=embed, view=None)

    async def cashout(self, interaction: discord.Interaction, user_id: int):
        """Handles the player's cash out action with PF logging."""
        game = self.ladder_games.get(user_id)
        if not game:
            return await interaction.response.send_message(
                "You are not currently playing Lucky Ladder.", ephemeral=True
            )

        if game["step"] == 0:
            return await interaction.response.send_message(
                "You cannot cash out your initial bet!", ephemeral=True
            )

        if (discord.utils.utcnow() - game["start_time"]).total_seconds() > 300:
            del self.ladder_games[user_id]
            return await interaction.response.send_message(
                "Your game has expired. Please start a new one.", ephemeral=True
            )

        PF = game["PF"]
        bet = game["bet"]
        final_reward = bet * game["current_multiplier"]

        revealed_seed, new_hash = await self.bot.database.increment_win(
            user_id,
            "ladder",
            bet,
            client_seed=PF["client_seed"],
            seed_used=None,
            nonce=PF["nonce"],
            hash_hex=PF["server_seed_hash"],
        )

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=wallet_id,
                amount=final_reward,
                description=f"Lucky Ladder Cashout (Step {game['step']})",
            )
        except ValueError as e:
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"🚫 {e}", color=discord.Color.red()),
                delete_after=5,
            )

        embed = discord.Embed(
            title="Lucky Ladder",
            description=(
                f"💰 **Cashed Out!**\n\n"
                f"Final Step: {game['step']}\n"
                f"Multiplier: {game['current_multiplier']:.2f}x\n"
                f"Winnings: {await self.formatter(final_reward)}"
            ),
            color=discord.Color.gold(),
        )
        del self.ladder_games[user_id]
        await interaction.response.edit_message(embed=embed, view=None)
        await self._remove_refund(game.get("session_id"), user_id=user_id)
        await self._end_game_session(
            game.get("session_id"),
            outcome="win",
            final_state={"winnings": str(final_reward)},
        )

    @commands.command(
        name="crash", description="Start a crash game in the current channel."
    )
    async def crash(self, ctx: Context):
        cid = ctx.channel.id
        if cid in self.active_games and self.active_games[cid].is_running:
            return await ctx.reply(
                "A crash game is already running here.", delete_after=5
            )

        session_id = await self._create_game_session(
            ctx,
            "crash",
            owner_id=ctx.author.id,
            state={"host_id": ctx.author.id},
        )
        view = CrashView(self.bot, ctx.author.id, cid, session_id=session_id)
        self.active_games[cid] = view

        async def force_end(refund: bool = False):
            for pid, bet in list(view.players.items()):
                if pid not in view.cashed_out and pid not in view.crashed_out:
                    if refund:
                        wallet = await self.bot.database.get_wallet_id_for_user(pid)
                        await self.bot.database.process_treasury_transaction(
                            wallet, bet, "Crash Refund"
                        )
                    view.crashed_out[pid] = view.crash_points.get(pid, Decimal("0"))
            view.is_running = False
            if view.game_task:
                view.game_task.cancel()
            if view.game_message:
                await view.game_message.edit(components=[await view.build_container()], view=None)
            await self._end_game_session(
                session_id,
                outcome="forced_end",
                final_state={"refund": refund},
            )
            self.cleanup_after_game(cid)

        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 10
        )

        await view.start_game(ctx)

    def cleanup_after_game(self, channel_id: int):
        self.active_players.discard(channel_id)
        self.active_games.pop(channel_id, None)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or not interaction.data.get("custom_id", "").startswith(
            "crash_"
        ):
            return

        channel_id = interaction.channel_id
        current_game = self.active_games.get(channel_id)
        if not current_game:
            return

        casino_cog = self.bot.get_cog("Casino")
        custom_id = interaction.data["custom_id"]

        if custom_id == "crash_join":
            formatted_max = await casino_cog.short_formatter(
                current_game.max_allowed_bet
            )

            modal = discord.ui.Modal(title="Join Crash Game")

            amount_input = discord.ui.TextInput(
                label="Bet Amount",
                placeholder=f"Enter amount to bet (Max: {formatted_max})",
                required=True,
            )
            modal.add_item(amount_input)

            async def on_submit(modal_inter: discord.Interaction):
                uid = modal_inter.user.id
                elapsed = (
                    discord.utils.utcnow() - current_game.start_time
                ).total_seconds()
                if current_game.game_phase != "starting" or elapsed >= 20:
                    return await modal_inter.response.send_message(
                        "Too late to join!", ephemeral=True
                    )

                wallet_id = await self.bot.database.get_wallet_id_for_user(uid)
                balance = await self.bot.database.get_wallet_balance(wallet_id)
                amt = Decimal(amount_input.value)
                if amt <= 0 or amt > balance:
                    return await modal_inter.response.send_message(
                        "Invalid amount.", ephemeral=True
                    )

                await self.bot.database.process_treasury_transaction(
                    wallet_id, -amt, "Crash Bet"
                )
                await casino_cog._add_refund(
                    current_game.session_id,
                    user_id=uid,
                    wallet_id=str(wallet_id),
                    amount=amt,
                    reason="crash_bet",
                )
                current_game.players[uid] = amt

                current_game.crash_points[
                    uid
                ] = await current_game.generate_crash_point(uid)

                await modal_inter.response.send_message(
                    f"Joined at {amt}", ephemeral=True
                )
                await casino_cog._log_game_event(
                    current_game.session_id,
                    "join",
                    {"user_id": uid, "bet": str(amt)},
                )
                await current_game.update_game_message()

            modal.on_submit = on_submit
            return await interaction.response.send_modal(modal)

        elif custom_id == "crash_cashout":
            uid = interaction.user.id

            if uid not in current_game.players or uid in current_game.cashed_out:
                return await interaction.response.send_message(
                    "Can't cash out.", ephemeral=True
                )

            bet = current_game.players[uid]
            mult = current_game.current_multiplier
            win = AmountUtils.round_currency(bet * mult)
            wallet = await self.bot.database.get_wallet_id_for_user(uid)
            await self.bot.database.process_treasury_transaction(
                wallet, win, "Crash Win"
            )

            await casino_cog._remove_refund(current_game.session_id, user_id=uid)
            await casino_cog._log_game_event(
                current_game.session_id,
                "cashout",
                {"user_id": uid, "multiplier": str(mult), "win": str(win)},
            )

            current_game.cashed_out[uid] = mult
            await interaction.response.send_message(
                f"Cashed out @ {mult}× for {self.currency_name} **{win}**",
                ephemeral=True,
            )
            await current_game.update_game_message()

    @commands.command(name="crashadmin", hidden=True)
    @commands.is_owner()
    async def crashadmin(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ):
        channel = channel or ctx.channel
        view = self.active_games.get(channel.id)
        if not view or not view.is_running:
            return await ctx.send("No active crash game here.")

        embed = discord.Embed(
            title=f"🚀 Crash Status — #{channel.name}", color=discord.Color.blue()
        )
        embed.add_field(
            name="Current Multiplier",
            value=f"{view.current_multiplier:.2f}×",
            inline=False,
        )

        lines = []
        for uid, bet in view.players.items():
            crashpoints = view.crash_points.get(uid, Decimal("0"))
            if uid in view.cashed_out:
                
                status = f"💰 Cashed @ {view.cashed_out[uid]:.2f}×"
            elif uid in view.crashed_out:
                status = f"💥 Crashed @ {view.crashed_out[uid]:.2f}×"
            else:
                status = "🟢 Playing"
            lines.append(f"<@{uid}> — Bet: {self.currency_name}** {await self.formatter(bet)}** | Target: {crashpoints:.2f}× → {status}")

        embed.add_field(name="Players", value="\n".join(lines), inline=False)

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
            await view.game_message.edit(components=[await view.build_container()], view=None)
            self.cleanup_after_game(channel.id)
            await inter.response.send_message(
                "All players have been forced to crash.", ephemeral=True
            )

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
                    win_amt = AmountUtils.round_currency(bet * mult)
                    wallet = await self.bot.database.get_wallet_id_for_user(pid)
                    await self.bot.database.process_treasury_transaction(
                        wallet, win_amt, "Crash Force Payout"
                    )
            view.is_running = False
            if view.game_task:
                view.game_task.cancel()
            await view.game_message.edit(components=[await view.build_container()], view=None)
            self.cleanup_after_game(channel.id)
            await inter.response.send_message(
                "All players have been forced to cash out.", ephemeral=True
            )

        win_btn.callback = win_cb
        admin_view.add_item(win_btn)

        await ctx.send(embed=embed, view=admin_view)
        await ctx.message.add_reaction("✅")

    @commands.command(name="mines")
    async def mines(
        self, ctx: commands.Context, num_bombs: int = None, bet_amount: str = None
    ):
        try:
            if num_bombs is None or bet_amount is None:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(
                    "### ⚠️ Missing Arguments\n"
                    "Syntax: `!mines (bomb amount) (bet amount)`\n"
                    "Usage: `!mines 5 5000`"
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            user_id = ctx.author.id
            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            session_id = None

            try:
                parsed_bet_amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(f"❌ {str(e)}"))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view, delete_after=5)
                return

            bet_amount = parsed_bet_amount

            if not isinstance(num_bombs, int):
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(
                    "❌ Please provide a valid integer number of bombs."
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            if num_bombs < 1 or num_bombs > 24:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(
                    "❌ Please provide a number of bombs between 1 and 24."
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            if bet_amount <= 0:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(
                    "❌ Please provide a valid bet amount greater than 0."
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            if bet_amount > balance:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(
                    f"❌ You do not have enough balance. Current balance: **{self.currency_name} {await self.formatter(balance)}** "
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if bet_amount > max_allowed:
                bet_amount = max_allowed
                container = discord.ui.Container(accent_color=0xFEF3C7)
                container.add_item(discord.ui.TextDisplay(
                    f"⚠️ High-roller limit applied. Bet adjusted to: **{self.currency_name} {await self.formatter(bet_amount)}**"
                ))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view, delete_after=5)

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-bet_amount,
                    description="Mines game bet",
                )
            except ValueError as e:
                container = discord.ui.Container(accent_color=0xFEE2E2)
                container.add_item(discord.ui.TextDisplay(f"❌ {str(e)}"))
                view = discord.ui.LayoutView()
                view.add_item(container)
                await ctx.reply(view=view)
                return

            PF = await self.prove_fairness(user_id)
            session_id = await self._create_game_session(
                ctx,
                "mines",
                owner_id=user_id,
                wager_total=bet_amount,
                state={
                    "user_id": user_id,
                    "bet": str(bet_amount),
                    "wallet_id": str(wallet_id),
                    "bombs": num_bombs,
                },
                rng=PF,
            )
            await self._add_refund(
                session_id,
                user_id=user_id,
                wallet_id=str(wallet_id),
                amount=bet_amount,
                reason="mines_bet",
            )

            grid_size = 5
            bomb_positions = await self.fair_sample(
                user_id, list(range(grid_size * grid_size)), num_bombs
            )

            remaining_safe_cells = grid_size * grid_size - num_bombs
            init_multi = await self.bot.database.get_mines_multiplier(num_bombs, 0)
            multiplier = float(init_multi) if init_multi else 1.0

            # Calculate house edge for RTP tracking
            house_edge = await self.calculate_house_edge(user_id)
            base_edge = Decimal("0.04")
            # Apply RTP boost for VIP players - multiplier boost
            if house_edge < base_edge:
                rtp_boost = float(1 + (float(base_edge) - float(house_edge)) / float(base_edge) * 0.1)
                multiplier = multiplier * rtp_boost

            # Format bet amount for display (formatter is async)
            formatted_bet = await self.formatter(bet_amount)

            # Create the new container-based game view
            game_view = MinesGridLayout(
                bomb_positions=bomb_positions,
                user_id=user_id,
                bet_amount=bet_amount,
                bot=self.bot,
                PF=PF,
                formatted_bet=formatted_bet,
                currency_name=self.currency_name,
                session_id=session_id,
                num_bombs=num_bombs,
            )

            try:
                main_message = await ctx.reply(view=game_view)
                self._register_session_handler(session_id, game_view.force_end)
            except discord.errors.NotFound:
                pass

        except Exception as e:
            logger.error(f"Error in mines command: {str(e)}")
            container = discord.ui.Container(accent_color=0xFEE2E2)
            container.add_item(discord.ui.TextDisplay(
                f"### ❌ An Error Occurred\n{str(e)}"
            ))
            view = discord.ui.LayoutView()
            view.add_item(container)
            await ctx.reply(view=view)

    @commands.command("keno")
    async def keno(self, ctx: commands.Context, player_bet: str):
        if ctx.guild.id != 1336128367166095380:
            return
        
        try:
            user_id = ctx.author.id

            PF = await self.prove_fairness(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = await self.bot.database.get_wallet_balance(wallet_id)
            balance = AmountUtils.round_currency(Decimal(str(balance)))

            try:
                amount = await self.amount_handler(player_bet, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                return

            max_allowed = await self.bot.database.get_max_gamble_amount(user_id, False)
            if amount > max_allowed:
                amount = max_allowed
                await ctx.reply(
                    embed=discord.Embed(
                        description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.formatter(amount)} {self.currency_name}**.",
                        color=discord.Color.orange(),
                    ),
                    delete_after=5,
                )

            session_id = await self._create_game_session(
                ctx,
                "keno",
                owner_id=user_id,
                wager_total=amount,
                state={
                    "user_id": user_id,
                    "bet": str(amount),
                    "wallet_id": str(wallet_id),
                },
                rng=PF,
            )

            formatted_bet = await self.formatter(amount)
            game_ui_view = GameUI(
                self, amount, formatted_bet, wallet_id, PF, session_id=session_id
            )
            table_ui_view = TableUI(self, session_id=session_id)

            grid_msg = await ctx.reply(view=table_ui_view)
            await ctx.send(view=game_ui_view)

            game_ui_view.container.table_ui_view = table_ui_view
            table_ui_view.message = grid_msg
            table_ui_view.player = ctx.author

            async def force_end(refund: bool = False):
                await self._end_game_session(
                    session_id,
                    outcome="forced_end",
                    final_state={"refund": refund},
                )

            self._register_session_handler(session_id, force_end)

        except ValueError as e:
            embed = discord.Embed(description=str(e), color=discord.Color.red())
            await ctx.reply(embed=embed, delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
    logger.debug("Casino cog initialized successfully")


keno_payouts = {
    "low_stakes": {
        0: {0: 0},
        1: {0: 0.7, 1: 1.8},
        2: {0: 0, 1: 2, 2: 3.5},
        3: {0: 0, 1: 1, 2: 1.3, 3: 20},
        4: {0: 0, 1: 0, 2: 2, 3: 7, 4: 70},
        5: {0: 0, 1: 0, 2: 1.3, 3: 4, 4: 12, 5: 250},
        6: {0: 0, 1: 0, 2: 1, 3: 2, 4: 6, 5: 100, 6: 600},
        7: {0: 0, 1: 0, 2: 1, 3: 1.5, 4: 3, 5: 15, 6: 200, 7: 600},
        8: {0: 0, 1: 0, 2: 1, 3: 1.2, 4: 2, 5: 5, 6: 30, 7: 100, 8: 700},
    },
    "med_stakes": {
        0: {0: 0},
        1: {0: 0.4, 1: 2.5},
        2: {0: 0, 1: 1.7, 2: 4.5},
        3: {0: 0, 1: 0, 2: 2.5, 3: 45},
        4: {0: 0, 1: 0, 2: 1.5, 3: 9, 4: 90},
        5: {0: 0, 1: 0, 2: 1.2, 3: 3.5, 4: 12, 5: 350},
        6: {0: 0, 1: 0, 2: 0, 3: 2.5, 4: 8, 5: 160, 6: 600},
        7: {0: 0, 1: 0, 2: 0, 3: 2, 4: 6, 5: 25, 6: 350, 7: 700},
        8: {0: 0, 1: 0, 2: 0, 3: 1.8, 4: 4, 5: 10, 6: 60, 7: 350, 8: 800},
    },
    "high_stakes": {
        0: {0: 0},
        1: {0: 0, 1: 3.5},
        2: {0: 0, 1: 0, 2: 15},
        3: {0: 0, 1: 0, 2: 0, 3: 70},
        4: {0: 0, 1: 0, 2: 0, 3: 9, 4: 230},
        5: {0: 0, 1: 0, 2: 0, 3: 4, 4: 45, 5: 400},
        6: {0: 0, 1: 0, 2: 0, 3: 0, 4: 10, 5: 320, 6: 600},
        7: {0: 0, 1: 0, 2: 0, 3: 0, 4: 6, 5: 80, 6: 350, 7: 700},
        8: {0: 0, 1: 0, 2: 0, 3: 0, 4: 4.5, 5: 15, 6: 250, 7: 500, 8: 800},
    },
}


class GameUI(discord.ui.LayoutView):
    def __init__(
        self,
        cog,
        player_bet: int,
        formatted_bet,
        player_wallet,
        PF: ProvenFairness,
        session_id=None,
    ):
        super().__init__(timeout=None)
        self.container = GameUIContainer(
            cog, player_bet, formatted_bet, player_wallet, PF, session_id=session_id
        )
        self.add_item(self.container)


class GameUIContainer(discord.ui.Container):
    def __init__(
        self,
        cog: Casino,
        player_bet: int,
        formatted_bet,
        player_wallet,
        PF: ProvenFairness,
        session_id=None,
    ):
        super().__init__(accent_color=0x2B2D31)
        self.table_ui_view: TableUI = None

        self.player_bet = player_bet
        self.player_wallet = player_wallet
        self.player_formatted_bet = formatted_bet
        self.session_id = session_id
        self.game_title = discord.ui.TextDisplay(
            f"### Keno | Select your stake | {cog.currency_name} {self.player_formatted_bet}"
        )

        action_row = discord.ui.ActionRow()
        action_row.add_item(StakeSelect())

        action_row2 = discord.ui.ActionRow()
        action_row2.add_item(BetButton(cog, PF))
        action_row2.add_item(RandomPickButton(cog))
        action_row2.add_item(ClearTableButton())

        self.add_item(self.game_title)  # Header

        self.add_item(discord.ui.Separator())  # Separator

        self.add_item(action_row)  # Stake Select
        self.add_item(action_row2)  # User Controls


class TableUI(discord.ui.LayoutView):
    def __init__(self, cog: Casino, session_id=None):
        super().__init__(timeout=None)
        self.message: discord.Message = None

        # game details

        self.player = None
        self.max_picks = 8  # maximum amount of tiles a user can select

        self.selected_emoji = cog.currency_name  # emoji we use to identify user picks

        self.selected_color = discord.ButtonStyle.blurple  # user tile colors
        self.default_color = discord.ButtonStyle.gray  # default tile color
        self.win_color = discord.ButtonStyle.green  # winning tile color

        self.container = TableUIContainer(self.default_color)
        self.add_item(self.container)
        self.session_id = session_id

    def get_all_buttons(self):
        buttons = []
        for row in self.container.children:
            if isinstance(row, discord.ui.ActionRow):
                for button in row.children:
                    buttons.append(button)
        return buttons

    async def reset_buttons(self):
        for button in self.get_all_buttons():
            button.emoji = None
            button.style = self.default_color

        await self.message.edit(view=self)

    async def get_multiplier(self, stakes: str):
        player_picks = [button for button in self.get_all_buttons() if button.emoji]
        player_hits = [
            button for button in player_picks if button.style == self.win_color
        ]

        bet_multiplier = keno_payouts[stakes][len(player_picks)][len(player_hits)]

        return bet_multiplier


class TableUIContainer(discord.ui.Container):
    def __init__(self, default_color: discord.ButtonStyle):
        super().__init__(accent_color=0x2B2D31)
        self.default_color = default_color

        number = 1

        self.win_loss_text = discord.ui.TextDisplay(f"### Waiting for Bet")
        self.add_item(self.win_loss_text)
        self.add_item(discord.ui.Separator())

        for _ in range(6):
            action_row = discord.ui.ActionRow()
            for _ in range(5):
                action_row.add_item(
                    NumberButton(label=f"{number}", style=default_color)
                )
                number += 1
            self.add_item(action_row)

        self.add_item(discord.ui.Separator())


class StakeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Low Stakes", value="low_stakes"),
            discord.SelectOption(label="Medium Stakes", value="med_stakes"),
            discord.SelectOption(label="High Stakes", value="high_stakes"),
        ]
        super().__init__(
            placeholder="Select your stakes...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, itn: discord.Interaction):
        table_ui_view: TableUI = self.parent.parent.table_ui_view

        if table_ui_view.player != itn.user:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: This is not your game",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        game_ui_container: GameUIContainer = self.parent.parent
        label = next(
            (option.label for option in self.options if option.value == self.values[0]),
            None,
        )
        game_ui_container.game_title.content = f"### Keno | {label} | {table_ui_view.selected_emoji} {game_ui_container.player_formatted_bet}"

        await itn.response.edit_message(view=game_ui_container.view)


class BetButton(discord.ui.Button):
    def __init__(self, cog: Casino, PF: ProvenFairness):
        super().__init__(label="Bet", style=discord.ButtonStyle.green)
        self.bot = cog.bot
        self.cog: Casino = cog
        self.PF = PF

    async def handle_bullshit(self, table_ui_view: TableUI, itn: discord.Interaction):
        if table_ui_view.player != itn.user:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: This is not your game",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        if not table_ui_view or not table_ui_view.message:
            embed = discord.Embed(
                f"🚫 {itn.user.mention}: This shouldnt of happened, try starting a new game",
                color=discord.Color.red(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        all_buttons = table_ui_view.get_all_buttons()
        for button in all_buttons:
            if button.emoji:
                button.style = table_ui_view.selected_color
            else:
                button.style = table_ui_view.default_color

        selected = await self.cog.fair_sample(
            table_ui_view.player.id, all_buttons, table_ui_view.max_picks
        )
        for button in selected:
            button.style = table_ui_view.win_color

        game_ui_container: GameUIContainer = self.parent.parent
        game_ui_action_row: discord.ui.ActionRow = game_ui_container.children[2]
        game_ui_select: discord.ui.Select = game_ui_action_row.children[0]
        selected_stake = game_ui_select.values

        if not selected_stake:
            embed = discord.Embed(
                description=f"⚠️ {itn.user.mention}: You forgot to select the stakes",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        bet_multiplier = await table_ui_view.get_multiplier(selected_stake[0])
        player_bet = game_ui_container.player_bet
        wallet_id = game_ui_container.player_wallet

        return bet_multiplier, player_bet, wallet_id

    async def callback(self, itn: discord.Interaction):
        try:
            table_ui_view: TableUI = self.parent.parent.table_ui_view
            game_ui_container: GameUIContainer = self.parent.parent

            bet_multiplier, player_bet, wallet_id = await self.handle_bullshit(
                table_ui_view, itn
            )

            # Calculate house edge for RTP tracking and bonus
            house_edge = await self.cog.calculate_house_edge(table_ui_view.player.id)
            base_edge = Decimal("0.04")
            # Apply RTP boost for VIP players
            rtp_boost = Decimal("1") + (base_edge - house_edge) / base_edge * Decimal("0.1") if house_edge < base_edge else Decimal("1")

            max_allowed = await self.bot.database.get_max_gamble_amount(
                table_ui_view.player.id, False
            )
            if player_bet > max_allowed:
                player_bet = max_allowed
                await itn.message.reply(
                    embed=discord.Embed(
                        description=f"You are a high-roller, so your bet was auto-adjusted to the max allowed: "
                        f"**{await self.cog.formatter(player_bet)} {self.cog.currency_name}**.",
                        color=discord.Color.orange(),
                    ),
                    delete_after=5,
                )

            total_win = player_bet * Decimal(bet_multiplier) * rtp_boost
            total_win_formatted = await self.cog.formatter(total_win)

            balance = await self.bot.database.get_wallet_balance(wallet_id)
            if balance < player_bet:
                embed = discord.Embed(
                    description=f"🚫 {itn.user.mention}: Insufficient Funds",
                    color=discord.Color.red(),
                )
                return await itn.response.send_message(embed=embed, ephemeral=True)

            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-Decimal(player_bet),
                    description="Keno Bet",
                )
            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                return await itn.response.send_message(embed=embed, delete_after=5)

            session_id = getattr(game_ui_container, "session_id", None) or getattr(
                table_ui_view, "session_id", None
            )
            await self.cog._add_refund(
                session_id,
                user_id=table_ui_view.player.id,
                wallet_id=str(wallet_id),
                amount=Decimal(player_bet),
                reason="keno_bet",
            )

            if total_win == player_bet:
                table_ui_view.container.win_loss_text.content = f"### You broke even {table_ui_view.selected_emoji} {total_win_formatted}"

            elif total_win < player_bet:
                loss_formatted = await self.cog.formatter(player_bet - total_win)
                table_ui_view.container.win_loss_text.content = (
                    f"### You Lost {table_ui_view.selected_emoji} {loss_formatted}"
                )

            else:
                table_ui_view.container.win_loss_text.content = (
                    f"### You WON {table_ui_view.selected_emoji} {total_win_formatted}"
                )

            await table_ui_view.message.edit(view=table_ui_view)

            try:
                # Process game result for rakeback
                await self.cog.process_game_result(table_ui_view.player.id, "keno", player_bet)

                if total_win >= player_bet:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=Decimal(total_win),
                        description=f"Keno Win",
                    )
                    await self.bot.database.increment_win(
                        table_ui_view.player.id,
                        "keno",
                        total_win,
                        client_seed=self.PF["client_seed"],
                        seed_used=None,
                        nonce=self.PF["nonce"],
                        hash_hex=self.PF["server_seed_hash"],
                    )
                else:
                    await self.bot.database.increment_loss(
                        table_ui_view.player.id,
                        "keno",
                        player_bet,
                        client_seed=self.PF["client_seed"],
                        seed_used=None,
                        nonce=self.PF["nonce"],
                        hash_hex=self.PF["server_seed_hash"],
                    )

            except ValueError as e:
                embed = discord.Embed(
                    description=f"🚫 Transaction failed: {e}", color=discord.Color.red()
                )
                return await itn.response.send_message(embed=embed, delete_after=5)

            await self.cog._remove_refund(session_id, user_id=table_ui_view.player.id)
            await self.cog._end_game_session(
                session_id,
                outcome="win" if total_win >= player_bet else "loss",
                final_state={"amount": str(total_win)},
            )

            await itn.response.defer()
        except Exception as e:
            await itn.channel.send(f"{e}")


class RandomPickButton(discord.ui.Button):
    def __init__(self, cog: Casino):
        super().__init__(label="Random Pick", style=discord.ButtonStyle.blurple)
        self.cog = cog

    async def callback(self, itn: discord.Interaction):
        table_ui_view: TableUI = self.parent.parent.table_ui_view

        if table_ui_view.player != itn.user:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: This is not your game",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        if not table_ui_view or not table_ui_view.message:
            embed = discord.Embed(
                f"🚫 {itn.user.mention}: This shouldnt of happened, try starting a new game",
                color=discord.Color.red(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        all_buttons = table_ui_view.get_all_buttons()

        selected = await self.cog.fair_sample(
            table_ui_view.player.id, all_buttons, table_ui_view.max_picks
        )

        for button in all_buttons:
            if button in selected:
                button.style = table_ui_view.selected_color
                button.emoji = table_ui_view.selected_emoji
            else:
                button.style = table_ui_view.default_color
                button.emoji = None

        await table_ui_view.message.edit(view=table_ui_view)
        await itn.response.defer()


class ClearTableButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Clear Table", style=discord.ButtonStyle.gray)

    async def callback(self, itn: discord.Interaction):
        table_ui_view: TableUI = self.parent.parent.table_ui_view

        if table_ui_view.player != itn.user:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: This is not your game",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        if not table_ui_view or not table_ui_view.message:
            embed = discord.Embed(
                f"🚫 {itn.user.mention}: This shouldnt of happened, try starting a new game",
                color=discord.Color.red(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        await table_ui_view.reset_buttons()
        await itn.response.defer()


class NumberButton(discord.ui.Button):
    def __init__(self, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style)

    async def callback(self, itn: discord.Interaction):
        table_ui_view: TableUI = self.view

        if table_ui_view.player != itn.user:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: This is not your game",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        all_buttons = table_ui_view.get_all_buttons()

        user_selects = sum(bool(b.emoji) for b in all_buttons)

        for button in all_buttons:
            if button.emoji:
                button.style = table_ui_view.selected_color
            else:
                button.style = table_ui_view.default_color

        if user_selects >= self.view.max_picks and self.emoji is None:
            embed = discord.Embed(
                f"⚠️ {itn.user.mention}: You selected the max amount of tiles: {self.view.max_picks}",
                color=discord.Color.yellow(),
            )
            return await itn.response.send_message(embed=embed, ephemeral=True)

        if self.style == table_ui_view.default_color:
            self.style = self.view.selected_color
            self.emoji = self.view.selected_emoji
        else:
            self.style = table_ui_view.default_color
            self.emoji = None

        await itn.response.edit_message(view=self.view)
