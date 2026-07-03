import re
import json
import os
import discord
import logging
import asyncio
import datetime
import secrets
import functools
import hashlib
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
from typing import Any, Optional
from utils.fairness import (
    DICE_PAYOUTS, DICE_EVEN_ODD_PAYOUT,
    LADDER_STEP_PROBS, LADDER_STEP_MULTS, LADDER_MAX_STEP,
    HILO_CARDS, HILO_CARD_VALUES,
    ROULETTE_ALL_NUMBERS, ROULETTE_RED_NUMBERS, ROULETTE_BLACK_NUMBERS,
    KENO_PAYOUTS,
    SLOTS_SYMBOLS, SLOTS_PAYLINES, SLOTS_REEL_WEIGHTS,
    SUPERGAMBLE_WIN_THRESHOLD, SUPERGAMBLE_BASE_MULTIPLIER,
    SUPERGAMBLE_BONUS_MULTIPLIER, SUPERGAMBLE_RECOVERY_THRESHOLD,
    SUPERGAMBLE_RECOVERY_MULTIPLIER, SUPERGAMBLE_MEGA_THRESHOLD,
    CRASH_RANGES,
    evaluate_slots,
)
from utils.fairgate import FairGateClient, FairGateError
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
        self.start_event: asyncio.Event = asyncio.Event()
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

        self.start_btn = discord.ui.Button(
            label="Start Game", style=discord.ButtonStyle.blurple
        )
        self.start_btn.callback = self.start_btn_callback

        self.cashout_btn = discord.ui.Button(
            label="Cash Out", style=discord.ButtonStyle.red, disabled=True
        )
        self.cashout_btn.callback = self.cashout_callback

        self.game_message: discord.Message = None

    async def build_container(self) -> discord.ui.Container:
        """Build the game container with current state."""
        if self.game_phase == "starting":
            title = "🚀 Crash – Lobby"
            player_count = len(self.players)
            if player_count == 0:
                content = "Click **Join** to enter. The host can press **Start Game** when ready."
            else:
                content = (
                    f"**{player_count} player{'s' if player_count != 1 else ''} joined.** "
                    "The host can press **Start Game** when ready."
                )
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
        # Start button is only visible/enabled in lobby for the host
        if self.game_phase == "starting":
            self.start_btn.label = "Start Game" if not self.players else f"Start Game ({len(self.players)})"
        else:
            self.start_btn.disabled = True
            self.start_btn.label = "Start Game"

        if show_buttons:
            if self.game_phase == "starting":
                action_row = discord.ui.ActionRow(
                    self.join_btn, self.start_btn, self.cashout_btn
                )
            else:
                action_row = discord.ui.ActionRow(self.join_btn, self.cashout_btn)
            container = discord.ui.Container(
                discord.ui.TextDisplay(f"## {title}"),
                discord.ui.TextDisplay(content),
                discord.ui.Separator(),
                action_row,
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

        if self.game_phase != "starting":
            return await interaction.response.send_message(
                "Too late to join!", ephemeral=True
            )

        uid = interaction.user.id
        if uid in self.players:
            return await interaction.response.send_message(
                "You've already joined!", ephemeral=True
            )

        max_allowed = await self.bot.database.get_max_gamble_amount(uid, False, Decimal("50.0"))  # 50x max payout for crash
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
            self.pf_data[uid] = await self.casino.start_fairgate_proof(uid)
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

    async def start_btn_callback(self, interaction: discord.Interaction):
        """Host-only: advance from lobby to running phase."""
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message(
                "Only the host can start the game.", ephemeral=True
            )
        if self.game_phase != "starting":
            return await interaction.response.send_message(
                "The game has already started.", ephemeral=True
            )
        if not self.players:
            return await interaction.response.send_message(
                "Need at least one player before starting.", ephemeral=True
            )

        self.start_event.set()
        await interaction.response.send_message(
            "Starting now!", ephemeral=True
        )

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
        await self.casino._record_game_outcome(
            uid, "crash", "win", bet, pf
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

        return await self.casino.fairgate_generate_crash_point(
            user_id, self.pf_data[user_id], house_edge
        )

    async def start_game(self, ctx: commands.Context):
        """Start lobby > run > finish."""
        self.is_running = True
        try:
            now = discord.utils.utcnow()
            self.start_time = now
            self.game_phase = "starting"
            self.current_multiplier = Decimal("1.0")
            self.start_event.clear()

            container = await self.build_container()
            self.clear_items()
            self.add_item(container)
            self.game_message = await ctx.send(view=self)
            await self.casino._update_game_session(
                self.session_id, message_id=self.game_message.id
            )

            # Wait for the host to press Start (with a 5-minute safety timeout
            # so the game doesn't hang forever if the host walks away).
            try:
                await asyncio.wait_for(self.start_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                container = discord.ui.Container(
                    discord.ui.TextDisplay("## 🚀 Crash – Cancelled"),
                    discord.ui.TextDisplay("Host never started the game."),
                    accent_color=0xED4245
                )
                self.clear_items()
                self.add_item(container)
                await self.game_message.edit(view=self)
                await self.casino._end_game_session(
                    self.session_id,
                    outcome="cancelled",
                    reason="host_timeout",
                    final_state={"phase": "starting"},
                )
                return

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
                            await self.casino._record_game_outcome(
                                uid, "crash", "loss", self.players[uid], pf,
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
                            await self.casino._record_game_outcome(
                                uid, "crash", "loss", self.players[uid], pf,
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

    async def _record_mines_outcome(self, outcome: str):
        """Persist the game result through FairGate."""
        try:
            await self.casino._record_game_outcome(
                self.user_id, "mines", outcome, self.bet_amount, self.PF
            )
        except Exception as e:
            logger.error(f"Failed to record mines {outcome}: {e}")

    @property
    def _channel_id(self) -> int | None:
        """The channel id this view's message was sent in, if any."""
        if self.message is not None:
            return self.message.channel.id
        return None

    def _cleanup_registry(self) -> None:
        """Pop this view from Casino.active_mines_views, if registered."""
        if self._channel_id is None:
            return
        casino: Casino = self.bot.get_cog("Casino")
        if casino is not None:
            casino._pop_mines_view(self._channel_id)

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
        await self._record_mines_outcome("loss")

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
        self._cleanup_registry()

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
        await self._record_mines_outcome("win")

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
        self._cleanup_registry()

        await interaction.response.edit_message(view=self)

    @staticmethod
    def _compute_mines_multiplier(
        bomb_count: int, gem_count: int, house_edge: float = 0.01
    ) -> float:
        """Compute the mines payout multiplier from probability.

        Uses the binomial probability of revealing ``gem_count`` gems in a row
        on a 5x5 grid with ``bomb_count`` bombs. The fair multiplier is the
        reciprocal of that probability, scaled down by ``house_edge``.

        Args:
            bomb_count: Number of bombs placed on the 25-cell grid (1-24).
            gem_count: Number of gems the player has successfully revealed.
            house_edge: House edge fraction (default 1%).

        Returns:
            The fair multiplier for the current state. Returns 1.0 when no
            gems have been revealed (no risk taken yet).
        """
        total_cells = 25
        if gem_count <= 0:
            return 1.0
        if bomb_count <= 0 or bomb_count >= total_cells:
            return 1.0
        if gem_count > total_cells - bomb_count:
            return 1.0

        # P(success) = C(total_cells - bomb_count, gem_count) / C(total_cells, gem_count)
        gems_left = total_cells - bomb_count
        # Compute C(n, k) for both numerator and denominator
        def comb(n: int, k: int) -> float:
            if k < 0 or k > n:
                return 0.0
            k = min(k, n - k)
            result = 1.0
            for i in range(1, k + 1):
                result *= (n - k + i) / i
            return result

        prob = comb(gems_left, gem_count) / comb(total_cells, gem_count)
        if prob <= 0:
            return 1.0
        return (1.0 - house_edge) / prob

    async def _calculate_multiplier(self) -> float:
        """Calculate the current multiplier.

        Prefers the database value when present, but falls back to a
        probability-based calculation if the DB row is missing or the stored
        value is clearly wrong (e.g. a 22-bomb / 1-gem payout of 825x instead
        of ~8.25x). This keeps the game playable even if the mines_settings
        table is mis-populated.
        """
        bomb_count = len(self.bomb_positions)
        gem_count = self.gems_clicked
        fallback = self._compute_mines_multiplier(bomb_count, gem_count)
        try:
            multiplier = await self.bot.database.get_mines_multiplier(
                bomb_count, gem_count
            )
            if multiplier is None:
                return fallback
            value = float(multiplier)
            # Sanity check: probability-derived value should be within an
            # order of magnitude of the stored value. If the stored value is
            # wildly off (off by >=10x), it's bad data and we trust the math.
            if fallback > 0 and (value > fallback * 10 or value < fallback / 10):
                logger.warning(
                    "Mines multiplier sanity check failed: bomb=%s gem=%s "
                    "db=%s math=%s — using math-derived value",
                    bomb_count, gem_count, value, fallback,
                )
                return fallback
            return value
        except Exception as e:
            logger.error(f"Error calculating multiplier: {str(e)}")
            return fallback

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
        self._cleanup_registry()

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
        await self._record_mines_outcome("win")

        # Process game result for rakeback only if user actually played
        if self.gems_clicked > 0:
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
        self._cleanup_registry()

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
        self._cleanup_registry()


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

        self.PF = await self.casino.bump_fairgate_pf(self.user_id, self.PF)
        side = await self.casino.fairgate_play_coinflip(self.user_id, self.PF, choice="heads")
        success = side == "heads"
        if success:
            self.winnings = Decimal(self.winnings) * 2
            await self.casino._record_game_outcome(
                self.user_id,
                "double",
                "win",
                self.initial_amount,
                self.PF,
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
            await self.casino._record_game_outcome(
                self.user_id,
                "double",
                "loss",
                self.initial_amount,
                self.PF,
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

        # Process game result for rakeback (only if user has risked funds)
        if self.rounds > 0:
            await self.casino.process_game_result(self.user_id, "double", self.initial_amount)

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


# ==================== ROULETTE — Components V2 ====================

ROULETTE_MAX_BETS = 10

ROULETTE_PAYTABLE_TEXT = (
    "```"
    "Bet Type       │ Payout │ Chance\n"
    "───────────────┼────────┼───────\n"
    "Single Number  │  36×   │  2.6%\n"
    "Green (0/00)   │  14×   │  5.3%\n"
    "Column         │   3×   │ 31.6%\n"
    "Dozen          │   3×   │ 31.6%\n"
    "Red / Black    │   2×   │ 47.4%\n"
    "Odd / Even     │   2×   │ 47.4%\n"
    "High / Low     │   2×   │ 47.4%\n"
    "```"
)

ROULETTE_BET_LABELS = {
    "red": "🔴 Red",
    "black": "⚫ Black",
    "green": "🟢 Green",
    "high": "⬆ High",
    "low": "⬇ Low",
    "odd": "Odd",
    "even": "Even",
    "dozen1": "1st 12",
    "dozen2": "2nd 12",
    "dozen3": "3rd 12",
    "column1": "Col 1",
    "column2": "Col 2",
    "column3": "Col 3",
}


class RouletteNumberModal(discord.ui.Modal, title="Pick a Number"):
    number_input = discord.ui.TextInput(
        label="Number (0–36 or 00)",
        placeholder="e.g. 17 or 00",
        required=True,
        max_length=2,
    )

    def __init__(self, roulette_view: "RouletteView"):
        super().__init__()
        self.roulette_view = roulette_view

    async def on_submit(self, interaction: discord.Interaction):
        value = self.number_input.value.strip()
        valid_numbers = {str(i) for i in range(37)} | {"00"}
        if value not in valid_numbers:
            await interaction.response.send_message(
                "Invalid number. Enter 0–36 or 00.", ephemeral=True
            )
            return
        # Toggle: add if not present, remove if present
        if value in self.roulette_view.selected_bets:
            self.roulette_view.selected_bets.discard(value)
        else:
            if len(self.roulette_view.selected_bets) >= ROULETTE_MAX_BETS:
                await interaction.response.send_message(
                    f"Maximum {ROULETTE_MAX_BETS} bets allowed.", ephemeral=True
                )
                return
            self.roulette_view.selected_bets.add(value)
        self.roulette_view._rebuild_container()
        await interaction.response.edit_message(view=self.roulette_view)


class RouletteView(discord.ui.LayoutView):
    """Interactive Roulette game using Components V2 Container system."""

    def __init__(self, bot, cog, user_id: int, bet_amount: Decimal, wallet_id: int,
                 currency_name: str, formatted_bet: str, ctx: Context):
        super().__init__(timeout=120)
        self.bot = bot
        self.cog: Casino = cog
        self.user_id = user_id
        self.bet_amount = bet_amount
        self.wallet_id = wallet_id
        self.currency_name = currency_name
        self.formatted_bet = formatted_bet
        self.ctx = ctx
        self.message: discord.Message | None = None

        self.selected_bets: set[str] = set()
        self.game_phase = "betting"  # "betting" | "result"
        self.session_id = None
        self.lock = asyncio.Lock()

        # ── Bet-type buttons (Row 1) ──
        self.btn_red = discord.ui.Button(label="🔴 Red", style=discord.ButtonStyle.gray, custom_id="roul_red")
        self.btn_red.callback = self._make_bet_callback("red")
        self.btn_black = discord.ui.Button(label="⚫ Black", style=discord.ButtonStyle.gray, custom_id="roul_black")
        self.btn_black.callback = self._make_bet_callback("black")
        self.btn_green = discord.ui.Button(label="🟢 Green", style=discord.ButtonStyle.gray, custom_id="roul_green")
        self.btn_green.callback = self._make_bet_callback("green")
        self.btn_high = discord.ui.Button(label="⬆ High", style=discord.ButtonStyle.gray, custom_id="roul_high")
        self.btn_high.callback = self._make_bet_callback("high")
        self.btn_low = discord.ui.Button(label="⬇ Low", style=discord.ButtonStyle.gray, custom_id="roul_low")
        self.btn_low.callback = self._make_bet_callback("low")

        # ── Bet-type buttons (Row 2) ──
        self.btn_odd = discord.ui.Button(label="Odd", style=discord.ButtonStyle.gray, custom_id="roul_odd")
        self.btn_odd.callback = self._make_bet_callback("odd")
        self.btn_even = discord.ui.Button(label="Even", style=discord.ButtonStyle.gray, custom_id="roul_even")
        self.btn_even.callback = self._make_bet_callback("even")
        self.btn_dozen1 = discord.ui.Button(label="1st 12", style=discord.ButtonStyle.gray, custom_id="roul_dozen1")
        self.btn_dozen1.callback = self._make_bet_callback("dozen1")
        self.btn_dozen2 = discord.ui.Button(label="2nd 12", style=discord.ButtonStyle.gray, custom_id="roul_dozen2")
        self.btn_dozen2.callback = self._make_bet_callback("dozen2")
        self.btn_dozen3 = discord.ui.Button(label="3rd 12", style=discord.ButtonStyle.gray, custom_id="roul_dozen3")
        self.btn_dozen3.callback = self._make_bet_callback("dozen3")

        # ── Bet-type buttons (Row 3) ──
        self.btn_col1 = discord.ui.Button(label="Col 1", style=discord.ButtonStyle.gray, custom_id="roul_col1")
        self.btn_col1.callback = self._make_bet_callback("column1")
        self.btn_col2 = discord.ui.Button(label="Col 2", style=discord.ButtonStyle.gray, custom_id="roul_col2")
        self.btn_col2.callback = self._make_bet_callback("column2")
        self.btn_col3 = discord.ui.Button(label="Col 3", style=discord.ButtonStyle.gray, custom_id="roul_col3")
        self.btn_col3.callback = self._make_bet_callback("column3")
        self.btn_number = discord.ui.Button(label="# Number", style=discord.ButtonStyle.gray, custom_id="roul_number")
        self.btn_number.callback = self._number_callback
        self.btn_paytable = discord.ui.Button(label="ℹ Paytable", style=discord.ButtonStyle.gray, custom_id="roul_paytable")
        self.btn_paytable.callback = self._paytable_callback

        # ── Spin / Play Again / Clear button (Row 4) ──
        self.btn_spin = discord.ui.Button(label="🎰 Spin!", style=discord.ButtonStyle.green, custom_id="roul_spin", disabled=True)
        self.btn_spin.callback = self._spin_callback
        self.btn_play_again = discord.ui.Button(label="🔄 Play Again", style=discord.ButtonStyle.green, custom_id="roul_again")
        self.btn_play_again.callback = self._play_again_callback
        self.btn_clear = discord.ui.Button(label="🗑 Clear", style=discord.ButtonStyle.red, custom_id="roul_clear")
        self.btn_clear.callback = self._clear_callback

        # All bet buttons for easy iteration
        self._bet_buttons = {
            "red": self.btn_red, "black": self.btn_black, "green": self.btn_green,
            "high": self.btn_high, "low": self.btn_low, "odd": self.btn_odd,
            "even": self.btn_even, "dozen1": self.btn_dozen1, "dozen2": self.btn_dozen2,
            "dozen3": self.btn_dozen3, "column1": self.btn_col1, "column2": self.btn_col2,
            "column3": self.btn_col3,
        }

        self._rebuild_container()

    def _get_bets_display(self) -> str:
        """Return a human-readable string of all selected bets."""
        if not self.selected_bets:
            return "None"
        labels = []
        # Show named bets first, then numbers sorted numerically
        named = sorted(b for b in self.selected_bets if b in self._bet_buttons or b in ROULETTE_BET_LABELS)
        numbers = sorted(
            (b for b in self.selected_bets if b not in self._bet_buttons and b not in ROULETTE_BET_LABELS),
            key=lambda x: -1 if x == "00" else int(x),
        )
        for bet in named:
            labels.append(ROULETTE_BET_LABELS.get(bet, bet))
        for bet in numbers:
            labels.append(f"#{bet}")
        return ", ".join(labels)

    @staticmethod
    def _evaluate_single_bet(choice: str, spin_result, is_int: bool, is_red: bool,
                              is_black: bool, is_green: bool, is_odd: bool, is_even: bool) -> Decimal:
        """Return the payout multiplier for a single bet (0 if lost)."""
        if choice == "green" and is_green:
            return Decimal(14)
        elif choice == "red" and is_red:
            return Decimal(2)
        elif choice == "black" and is_black:
            return Decimal(2)
        elif choice == "odd" and is_odd:
            return Decimal(2)
        elif choice == "even" and is_even:
            return Decimal(2)
        elif choice == "high" and is_int and 19 <= spin_result <= 36:
            return Decimal(2)
        elif choice == "low" and is_int and 1 <= spin_result <= 18:
            return Decimal(2)
        elif choice == "dozen1" and is_int and 1 <= spin_result <= 12:
            return Decimal(3)
        elif choice == "dozen2" and is_int and 13 <= spin_result <= 24:
            return Decimal(3)
        elif choice == "dozen3" and is_int and 25 <= spin_result <= 36:
            return Decimal(3)
        elif choice == "column1" and is_int and (spin_result % 3 == 1):
            return Decimal(3)
        elif choice == "column2" and is_int and (spin_result % 3 == 2):
            return Decimal(3)
        elif choice == "column3" and is_int and (spin_result % 3 == 0 and spin_result != 0):
            return Decimal(3)
        elif (choice.isdigit() and is_int and int(choice) == spin_result) or (choice == "00" and spin_result == "00"):
            return Decimal(36)
        return Decimal(0)

    def _rebuild_container(self):
        """Rebuild the container based on current game phase."""
        self.clear_items()

        if self.game_phase == "betting":
            # Highlight all selected bet buttons
            for key, btn in self._bet_buttons.items():
                btn.style = discord.ButtonStyle.blurple if key in self.selected_bets else discord.ButtonStyle.gray
                btn.disabled = False
            # Number button highlighted if any number is selected
            numbers_selected = self.selected_bets - set(self._bet_buttons.keys())
            if numbers_selected:
                self.btn_number.style = discord.ButtonStyle.blurple
                self.btn_number.label = f"# ({len(numbers_selected)})"
            else:
                self.btn_number.style = discord.ButtonStyle.gray
                self.btn_number.label = "# Number"
            self.btn_spin.disabled = len(self.selected_bets) == 0
            self.btn_clear.disabled = len(self.selected_bets) == 0

            num_bets = len(self.selected_bets)
            total_wager = self.bet_amount * num_bets
            total_str = self.cog._fmt_no_sci(total_wager, max_frac=2) if num_bets > 0 else "0"

            status_text = (
                f"**Per Bet:** {self.currency_name} **{self.formatted_bet}** │ "
                f"**Bets:** {num_bets}/{ROULETTE_MAX_BETS} │ "
                f"**Total Wager:** {self.currency_name} **{total_str}**\n"
                f"**Selected:** {self._get_bets_display()}"
            )

            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🎰 Roulette"),
                discord.ui.TextDisplay(status_text),
                discord.ui.Separator(),
                discord.ui.ActionRow(self.btn_red, self.btn_black, self.btn_green, self.btn_high, self.btn_low),
                discord.ui.ActionRow(self.btn_odd, self.btn_even, self.btn_dozen1, self.btn_dozen2, self.btn_dozen3),
                discord.ui.ActionRow(self.btn_col1, self.btn_col2, self.btn_col3, self.btn_number, self.btn_paytable),
                discord.ui.Separator(),
                discord.ui.ActionRow(self.btn_spin, self.btn_clear),
                accent_color=0xFCD34D,  # Gold
            )
            self.add_item(container)

        else:  # result phase
            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🎰 Roulette"),
                discord.ui.TextDisplay(self._result_status),
                discord.ui.TextDisplay(self._result_detail),
                discord.ui.Separator(),
                discord.ui.ActionRow(self.btn_play_again),
                accent_color=self._result_accent,
            )
            self.add_item(container)

    def _make_bet_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("This isn't your game!", ephemeral=True)
            if self.game_phase != "betting":
                return await interaction.response.defer()
            # Toggle: add if not present, remove if present
            if choice in self.selected_bets:
                self.selected_bets.discard(choice)
            else:
                if len(self.selected_bets) >= ROULETTE_MAX_BETS:
                    return await interaction.response.send_message(
                        f"Maximum {ROULETTE_MAX_BETS} bets allowed.", ephemeral=True
                    )
                self.selected_bets.add(choice)
            self._rebuild_container()
            await interaction.response.edit_message(view=self)
        return callback

    async def _number_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if self.game_phase != "betting":
            return await interaction.response.defer()
        await interaction.response.send_modal(RouletteNumberModal(self))

    async def _paytable_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**🎰 Roulette Paytable**\n{ROULETTE_PAYTABLE_TEXT}", ephemeral=True
        )

    async def _clear_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if self.game_phase != "betting":
            return await interaction.response.defer()
        self.selected_bets.clear()
        self._rebuild_container()
        await interaction.response.edit_message(view=self)

    async def _spin_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if self.game_phase != "betting" or not self.selected_bets:
            return await interaction.response.defer()

        async with self.lock:
            if self.game_phase != "betting":
                return await interaction.response.defer()
            self.game_phase = "spinning"

        await interaction.response.defer()

        bets = list(self.selected_bets)
        num_bets = len(bets)
        total_wager = self.bet_amount * num_bets

        # ── Fairness & session ──
        PF = await self.cog.start_fairgate_proof(self.user_id)

        try:
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id, amount=Decimal(-total_wager), description="Roulette Bet"
            )
        except ValueError as e:
            self.game_phase = "betting"
            self._rebuild_container()
            await interaction.followup.edit_message(interaction.message.id, view=self)
            await interaction.followup.send(f"🚫 Transaction failed: {e}", ephemeral=True)
            return

        self.session_id = await self.cog._create_game_session(
            self.ctx, "roulette", owner_id=self.user_id, wager_total=total_wager,
            state={"user_id": self.user_id, "bet": str(self.bet_amount),
                   "wallet_id": str(self.wallet_id), "bets": bets},
            rng=PF,
        )
        await self.cog._add_refund(
            self.session_id, user_id=self.user_id,
            wallet_id=str(self.wallet_id), amount=total_wager, reason="roulette_bet",
        )

        # ── House edge & RTP boost ──
        house_edge = await self.cog.calculate_house_edge(self.user_id)
        base_edge = Decimal("0.04")
        rtp_boost = (
            Decimal("1") + (base_edge - house_edge) / base_edge * Decimal("0.1")
            if house_edge < base_edge else Decimal("1")
        )

        # ── Spin ──
        outcome = await self.cog.fairgate_play_roulette(self.user_id, PF, wheel="american")
        spin_result = outcome["pocket"]
        color_label = outcome.get("color", "Unknown").title()
        is_green = color_label.lower() == "green"
        is_red = color_label.lower() == "red"
        is_black = color_label.lower() == "black"
        is_int = isinstance(spin_result, int)
        is_even = is_int and spin_result != 0 and (spin_result % 2 == 0)
        is_odd = is_int and (spin_result % 2 == 1)

        # ── Evaluate each bet ──
        total_winnings = Decimal(0)
        bet_results = []  # list of (choice_label, multiplier, payout)
        for choice in bets:
            multiplier = self._evaluate_single_bet(
                choice, spin_result, is_int, is_red, is_black, is_green, is_odd, is_even
            )
            payout = Decimal(0)
            if multiplier > 0:
                payout = AmountUtils.round_currency(self.bet_amount * multiplier * rtp_boost)
                total_winnings += payout
            label = ROULETTE_BET_LABELS.get(choice)
            if not label:
                label = f"#{choice}"
            bet_results.append((label, multiplier, payout))

        await self.cog.process_game_result(self.user_id, "roulette", total_wager)

        # ── Record outcome ──
        net_won = total_winnings > 0
        await self.cog._record_game_outcome(
            self.user_id, "roulette", "win" if net_won else "loss", total_wager, PF
        )
        if net_won:
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=self.wallet_id, amount=total_winnings, description="Roulette Win"
                )
            except ValueError:
                pass

        await self.bot.database.set_cooldown(self.user_id, "roulette", 5)

        # ── Build result display ──
        result_lines = []
        for label, multiplier, payout in bet_results:
            if multiplier > 0:
                fmt_payout = await self.cog.formatter(payout)
                result_lines.append(f"✅ {label} → {multiplier}× (+{self.currency_name} {fmt_payout})")
            else:
                result_lines.append(f"❌ {label} → 0×")

        self._result_status = (
            f"The ball landed on **{color_label} {spin_result}**\n\n"
            + "\n".join(result_lines)
        )

        net_profit = total_winnings - total_wager
        if net_won:
            formatted_winnings = await self.cog.formatter(total_winnings)
            formatted_profit = await self.cog.formatter(net_profit)
            self._result_detail = (
                f"🎉 Total payout: {self.currency_name} **{formatted_winnings}** "
                f"(+{self.currency_name} {formatted_profit} profit)"
            )
            self._result_accent = 0x57F287  # Green
        else:
            formatted_loss = await self.cog.formatter(total_wager)
            self._result_detail = f"You lost {self.currency_name} **{formatted_loss}**. Better luck next time!"
            self._result_accent = 0xED4245  # Red

        # Check if Play Again is affordable
        balance = Decimal(str(await self.bot.database.get_wallet_balance(self.wallet_id)))
        self.btn_play_again.disabled = balance < self.bet_amount

        self.game_phase = "result"
        self._rebuild_container()
        await interaction.followup.edit_message(interaction.message.id, view=self)

        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._log_game_event(
            self.session_id, "result",
            {"outcome": "win" if net_won else "loss",
             "total_wager": str(total_wager), "total_winnings": str(total_winnings),
             "bets": bets, "spin": str(spin_result), "color": color_label},
        )
        await self.cog._end_game_session(
            self.session_id, outcome="win" if net_won else "loss",
            final_state={"total_wager": str(total_wager), "total_winnings": str(total_winnings)},
        )

    async def _play_again_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)

        # Check balance
        balance = Decimal(str(await self.bot.database.get_wallet_balance(self.wallet_id)))
        if balance < self.bet_amount:
            return await interaction.response.send_message(
                f"🚫 Insufficient balance. You need {self.currency_name} **{self.formatted_bet}**.",
                ephemeral=True,
            )

        self.selected_bets.clear()
        self.game_phase = "betting"
        self.session_id = None
        self._rebuild_container()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        # Disable everything in the current container
        self.game_phase = "timeout"
        self.clear_items()
        container = discord.ui.Container(
            discord.ui.TextDisplay("## 🎰 Roulette"),
            discord.ui.TextDisplay("Game timed out."),
            accent_color=0xFEE75C,  # Yellow
        )
        self.add_item(container)
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        self.stop()


# ==================== HI-LO — Components V2 ====================

HILO_CARD_EMOJIS = {
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


class HiLoView(discord.ui.LayoutView):
    """HiLo card guessing game using Components V2 Container system."""

    def __init__(self, bot, cog, user: discord.Member, bet_amount: Decimal,
                 wallet_id: int, currency_name: str, current_card: str,
                 house_edge: Decimal, PF: dict, session_id, ctx: Context,
                 deck: list[str] | None = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.cog: Casino = cog
        self.user = user
        self.user_id = user.id
        self.bet_amount = bet_amount
        self.wallet_id = wallet_id
        self.currency_name = currency_name
        self.ctx = ctx

        self.current_card = current_card
        self.house_edge = house_edge
        self.PF = PF
        self.session_id = session_id
        self.message: discord.Message | None = None

        self.deck = list(deck) if deck else []
        self.multiplier = Decimal("1.0")
        self.history: list[str] = []
        self.game_active = True
        self.has_played = False
        self.skips_used = 0
        self.lock = asyncio.Lock()

        # ── Buttons ──
        higher_prob, lower_prob = self._calculate_probs(self.current_card)
        self.btn_higher = discord.ui.Button(
            label=f"Higher ({higher_prob}%)", style=discord.ButtonStyle.gray, custom_id="hilo_higher"
        )
        self.btn_higher.callback = self._higher_callback
        self.btn_lower = discord.ui.Button(
            label=f"Lower ({lower_prob}%)", style=discord.ButtonStyle.gray, custom_id="hilo_lower"
        )
        self.btn_lower.callback = self._lower_callback
        self.btn_skip = discord.ui.Button(
            label="Skip Card", style=discord.ButtonStyle.gray, custom_id="hilo_skip"
        )
        self.btn_skip.callback = self._skip_callback
        self.btn_cashout = discord.ui.Button(
            label=f"Cash Out {self.multiplier:.2f}x", style=discord.ButtonStyle.blurple, custom_id="hilo_cashout"
        )
        self.btn_cashout.callback = self._cashout_callback

        self._rebuild_container()

    # ── Multiplier / probability helpers ──

    def _calculate_multiplier(self, card: str, action: str) -> Decimal:
        current_value = HILO_CARD_VALUES[card]
        total = len(HILO_CARDS) - 1  # exclude current card

        if action == "higher":
            favorable = len([c for c in HILO_CARDS if HILO_CARD_VALUES[c] > current_value])
        else:
            favorable = len([c for c in HILO_CARDS if HILO_CARD_VALUES[c] < current_value])

        if favorable == 0:
            return Decimal("0")

        probability = Decimal(favorable) / Decimal(total)
        multiplier = (Decimal("1") / probability) * (Decimal("1") - self.house_edge)

        return max(multiplier, Decimal("1.01"))

    def _calculate_probs(self, card: str) -> tuple[int, int]:
        current_value = HILO_CARD_VALUES[card]
        higher = len([c for c in HILO_CARDS if HILO_CARD_VALUES[c] > current_value])
        lower = len([c for c in HILO_CARDS if HILO_CARD_VALUES[c] < current_value])
        total = len(HILO_CARDS) - 1

        higher_prob = max(8, min(92, round((higher / total) * 100)))
        lower_prob = max(8, min(92, round((lower / total) * 100)))
        return higher_prob, lower_prob

    async def _calculate_profits(self, card: str) -> tuple[str, str, str]:
        higher_mult = self._calculate_multiplier(card, "higher")
        lower_mult = self._calculate_multiplier(card, "lower")

        profit_higher = (self.bet_amount * self.multiplier * higher_mult) - self.bet_amount
        profit_lower = (self.bet_amount * self.multiplier * lower_mult) - self.bet_amount
        total_profit = (self.bet_amount * self.multiplier) - self.bet_amount

        return (
            await self.cog.formatter(profit_higher),
            await self.cog.formatter(profit_lower),
            await self.cog.formatter(total_profit),
        )

    # ── Container rendering ──

    def _rebuild_container(self, status: str = "Playing", result_text: str | None = None):
        self.clear_items()

        card_emoji = HILO_CARD_EMOJIS.get(self.current_card, HILO_CARD_EMOJIS["back"])

        # History string
        history_parts = []
        for card in self.history:
            history_parts.append(f"{HILO_CARD_EMOJIS.get(card, '🃏')} {card}")
        history_parts.append(f"{card_emoji} {self.current_card}")
        history_parts.append(HILO_CARD_EMOJIS["back"])
        history_str = " → ".join(history_parts)

        if status == "Playing":
            accent_color = 0x5865F2  # Blurple
        elif status == "Cashed Out":
            accent_color = 0x57F287  # Green
        else:
            accent_color = 0xED4245  # Red

        children = [
            discord.ui.TextDisplay(f"## {HILO_CARD_EMOJIS['back']} HiLo — {status}"),
            discord.ui.TextDisplay(f"**Current Card:** {card_emoji} **{self.current_card}**"),
        ]

        if status == "Playing":
            higher_mult = self._calculate_multiplier(self.current_card, "higher")
            lower_mult = self._calculate_multiplier(self.current_card, "lower")
            children.append(discord.ui.TextDisplay(
                f"**Higher** ({higher_mult:.2f}x) │ "
                f"**Lower** ({lower_mult:.2f}x) │ "
                f"**Total** ({self.multiplier:.2f}x)"
            ))

        children.append(discord.ui.TextDisplay(f"**History:** {history_str}"))

        if result_text:
            children.append(discord.ui.Separator())
            children.append(discord.ui.TextDisplay(result_text))

        if status == "Playing":
            children.append(discord.ui.Separator())
            children.append(discord.ui.ActionRow(
                self.btn_higher, self.btn_lower, self.btn_skip, self.btn_cashout
            ))

        container = discord.ui.Container(*children, accent_color=accent_color)
        self.add_item(container)

    def _update_button_labels(self):
        higher_prob, lower_prob = self._calculate_probs(self.current_card)
        self.btn_higher.label = f"Higher ({higher_prob}%)"
        self.btn_lower.label = f"Lower ({lower_prob}%)"
        self.btn_cashout.label = f"Cash Out {self.multiplier:.2f}x"

    # ── Game lifecycle ──

    async def _next_fairgate_card(self) -> str:
        """Pop the next card from the pre-shuffled deck, drawing a fresh deck if needed."""
        if not self.deck:
            self.PF = await self.cog.bump_fairgate_pf(self.user_id, self.PF)
            self.deck = await self.cog.fairgate_play_hilo_deck(self.user_id, self.PF)
        return self.deck.pop(0)

    async def _end_game(self, win: bool):
        self.game_active = False

        if self.user_id in self.cog.active_players:
            self.cog.active_players.discard(self.user_id)

        await self.cog.process_game_result(self.user_id, "hilo", self.bet_amount)

        outcome = "win" if win else "loss"
        await self.cog._record_game_outcome(
            self.user_id, "hilo", outcome, self.bet_amount, self.PF
        )
        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._end_game_session(
            self.session_id, outcome=outcome,
            final_state={
                "winnings": str(self.bet_amount * self.multiplier) if win else None,
                "loss": str(self.bet_amount) if not win else None,
            },
        )
        return self.bet_amount * self.multiplier if win else None

    async def _show_result(self, interaction: discord.Interaction, win: bool, *, auto_cashout_card: str | None = None):
        if win:
            formatted = await self.cog.formatter(self.bet_amount * self.multiplier)
            if auto_cashout_card:
                result_text = (
                    f"Drew a **{auto_cashout_card}** — auto cashout at **{self.multiplier:.2f}x**\n"
                    f"Won **{formatted}** **{self.currency_name}**"
                )
            else:
                result_text = (
                    f"Cashed out with a **{self.multiplier:.2f}x** multiplier\n"
                    f"Won **{formatted}** **{self.currency_name}**"
                )
            status = "Cashed Out"
        else:
            formatted = await self.cog.formatter(self.bet_amount)
            result_text = (
                f"Lost with a possible multiplier of **{self.multiplier:.2f}x**\n"
                f"Bet: **{formatted}** **{self.currency_name}**"
            )
            status = "Lost"

        self._rebuild_container(status=status, result_text=result_text)
        try:
            await interaction.followup.edit_message(interaction.message.id, view=self)
        except Exception as e:
            self.bot.logger.error(f"Error showing hilo result: {e}")

    # ── Button callbacks ──

    async def _higher_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if not self.game_active:
            return await interaction.response.defer()

        async with self.lock:
            if not self.game_active:
                return await interaction.response.defer()

            await interaction.response.defer()
            self.has_played = True
            self.history.append(self.current_card)

            next_card = await self._next_fairgate_card()

            multiplier_increase = self._calculate_multiplier(self.history[-1], "higher")

            current_value = HILO_CARD_VALUES[self.history[-1]]
            next_value = HILO_CARD_VALUES[next_card]

            if next_value > current_value:
                self.multiplier *= multiplier_increase
                self.current_card = next_card
                if next_card in ["A", "K"]:
                    winnings = self.bet_amount * self.multiplier
                    try:
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=self.wallet_id, amount=winnings, description="HiLo Win"
                        )
                    except ValueError as e:
                        await interaction.followup.send(f"\U0001f6ab Transaction failed: {e}", ephemeral=True)
                        return
                    await self._end_game(True)
                    await self._show_result(interaction, True, auto_cashout_card=next_card)
                    return
            else:
                # next_value < current_value (same-card is impossible now that
                # current_card is excluded from the draw).
                self.current_card = next_card
                await self._end_game(False)
                await self._show_result(interaction, False)
                return

            self._update_button_labels()
            self._rebuild_container()
            await interaction.followup.edit_message(interaction.message.id, view=self)

    async def _lower_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if not self.game_active:
            return await interaction.response.defer()

        async with self.lock:
            if not self.game_active:
                return await interaction.response.defer()

            await interaction.response.defer()
            self.has_played = True
            self.history.append(self.current_card)

            next_card = await self._next_fairgate_card()

            multiplier_increase = self._calculate_multiplier(self.history[-1], "lower")

            current_value = HILO_CARD_VALUES[self.history[-1]]
            next_value = HILO_CARD_VALUES[next_card]

            if next_value < current_value:
                self.multiplier *= multiplier_increase
                self.current_card = next_card
                if next_card in ["A", "K"]:
                    winnings = self.bet_amount * self.multiplier
                    try:
                        await self.bot.database.process_treasury_transaction(
                            wallet_id=self.wallet_id, amount=winnings, description="HiLo Win"
                        )
                    except ValueError as e:
                        await interaction.followup.send(f"\U0001f6ab Transaction failed: {e}", ephemeral=True)
                        return
                    await self._end_game(True)
                    await self._show_result(interaction, True, auto_cashout_card=next_card)
                    return
            else:
                # next_value > current_value (same-card is impossible now
                # that current_card is excluded from the draw).
                self.current_card = next_card
                await self._end_game(False)
                await self._show_result(interaction, False)
                return

            self._update_button_labels()
            self._rebuild_container()
            await interaction.followup.edit_message(interaction.message.id, view=self)

    async def _skip_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if not self.game_active:
            return await interaction.response.defer()

        if self.skips_used >= 3:
            return await interaction.response.send_message(
                "🚫 Limit reached! You can only skip 3 times per game.", ephemeral=True
            )

        async with self.lock:
            if not self.game_active:
                return await interaction.response.defer()

            await interaction.response.defer()
            self.skips_used += 1
            self.history.append(self.current_card)
            self.current_card = await self._next_fairgate_card()
            self._update_button_labels()
            self._rebuild_container()
            await interaction.followup.edit_message(interaction.message.id, view=self)

    async def _cashout_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        if not self.game_active:
            return await interaction.response.send_message("Game is not active!", ephemeral=True)
        if not self.has_played:
            return await interaction.response.send_message(
                "🚫 You must make at least one guess (Higher/Lower) before cashing out!", ephemeral=True
            )

        async with self.lock:
            if not self.game_active:
                return await interaction.response.defer()

            await interaction.response.defer()

            winnings = self.bet_amount * self.multiplier
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=self.wallet_id, amount=winnings, description="HiLo Win"
                )
            except ValueError as e:
                await interaction.followup.send(f"🚫 Transaction failed: {e}", ephemeral=True)
                return

            await self._end_game(True)
            await self._show_result(interaction, True)

    # ── Force end / timeout ──

    async def force_end(self, refund: bool = False):
        self.game_active = False
        if self.user_id in self.cog.active_players:
            self.cog.active_players.discard(self.user_id)

        if refund:
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=self.wallet_id, amount=self.bet_amount, description="HiLo Refund"
                )
            except Exception:
                pass

        self._rebuild_container(status="Timed Out", result_text="Game ended. Your bet was refunded." if refund else "Game ended.")
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._end_game_session(
            self.session_id, outcome="forced_end", final_state={"refund": refund}
        )
        self.stop()

    async def on_timeout(self):
        await self.force_end(refund=True)


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
        player_ties = player_rank == bot_rank

        if player_wins:
            await self.cog._record_game_outcome(
                self.user_id, "poker", "win", self.bet, self.PF
            )
            payout = self.bet * Decimal("2")
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id, amount=payout, description="Poker Win"
            )
            formatted = await self.cog.formatter(payout)
            result = f"You win! You won **{formatted}**."
            color = discord.Color.green()
        elif player_ties:
            # Tied hand → push. Record the round as a push (counts toward
            # total wagered but not toward wins or losses) and refund the bet.
            await self.cog._record_game_outcome(
                self.user_id, "poker", "push", self.bet, self.PF
            )
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id, amount=self.bet, description="Poker Push"
            )
            formatted = await self.cog.formatter(self.bet)
            result = f"It's a tie! Your bet of **{formatted}** has been refunded."
            color = discord.Color.greyple()
        else:
            await self.cog._record_game_outcome(
                self.user_id, "poker", "loss", self.bet, self.PF
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

        await self.cog._record_game_outcome(
            self.user_id, "poker", "loss", self.bet, self.PF
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


class LadderView(discord.ui.LayoutView):
    """Lucky Ladder game view using Components V2 Container system.

    Rebalanced for ~95.5% RTP with EV-neutral climbing at every step.
    """

    STEP_PROBS = LADDER_STEP_PROBS
    STEP_MULTS = LADDER_STEP_MULTS
    MAX_STEP = LADDER_MAX_STEP

    def __init__(
        self, *, bot, cog, user_id: int, bet: Decimal, wallet_id,
        PF: dict, session_id, currency_name: str,
    ):
        super().__init__(timeout=60)
        self.bot = bot
        self.cog: Casino = cog
        self.user_id = user_id
        self.bet = bet
        self.wallet_id = wallet_id
        self.PF = PF
        self.session_id = session_id
        self.currency_name = currency_name
        self.step = 0
        self.current_multiplier = self.STEP_MULTS[0]
        self.start_time = discord.utils.utcnow()
        self.message: discord.Message | None = None

        # Buttons
        self.climb_button = discord.ui.Button(
            label="🪜 Climb", style=discord.ButtonStyle.green,
        )
        self.climb_button.callback = self._climb_callback

        self.cashout_button = discord.ui.Button(
            label="💰 Cash Out", style=discord.ButtonStyle.red,
        )
        self.cashout_button.callback = self._cashout_callback

    # ── visual helpers ──────────────────────────────────────────

    def _build_ladder_visual(self) -> str:
        lines = []
        for s in range(self.MAX_STEP, -1, -1):
            mult = self.STEP_MULTS[s]
            prob = self.STEP_PROBS.get(s, "")
            prob_str = f"({prob}%)" if prob != "" else "(TOP)"
            if s == self.step:
                marker = "▶"
            elif s < self.step:
                marker = "✅"
            else:
                marker = "⬜"
            lines.append(f"`{marker} Step {s:>2} │ {mult:>7.2f}x │ {prob_str:>6}`")
        return "\n".join(lines)

    def _build_container(
        self, *, accent_color: int, content: str, show_buttons: bool = True,
    ):
        ladder_visual = self._build_ladder_visual()
        children = [
            discord.ui.TextDisplay("## 🪜 Lucky Ladder"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(ladder_visual),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content),
        ]
        if show_buttons:
            children.append(discord.ui.Separator())
            children.append(
                discord.ui.ActionRow(self.climb_button, self.cashout_button),
            )
        container = discord.ui.Container(*children, accent_color=accent_color)
        self.clear_items()
        self.add_item(container)

    async def build_initial_container(self):
        formatted_bet = await self.cog.formatter(self.bet)
        next_prob = self.STEP_PROBS.get(0, 0)
        next_mult = self.STEP_MULTS.get(1, Decimal("0"))
        content = (
            f"**Bet:** {self.currency_name} **{formatted_bet}**\n"
            f"**Current Step:** 0 • **Multiplier:** 1.00x\n"
            f"**Next Climb:** {next_prob}% chance → {next_mult}x\n\n"
            "Click **Climb** to risk it or **Cash Out** to secure winnings.\n"
            "*You must climb at least once before cashing out.*"
        )
        self._build_container(accent_color=0x5865F2, content=content)

    # ── callbacks ────────────────────────────────────────────────

    async def _climb_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "This is not your game!", ephemeral=True,
            )
        if (discord.utils.utcnow() - self.start_time).total_seconds() > 300:
            self._build_container(
                accent_color=0xFEE75C,
                content="Game expired. Your bet has been refunded.",
                show_buttons=False,
            )
            await interaction.response.edit_message(view=self)
            await self._refund_and_end("expired")
            return

        success_chance = self.STEP_PROBS.get(self.step, 0)
        threshold = success_chance * 100
        roll = (await self.cog.fairgate_play_numbers(
            self.user_id, self.PF, pool=10000, pick=1, replacement=True
        ))[0]

        if roll < threshold:
            # ── success ──
            self.step += 1
            self.current_multiplier = self.STEP_MULTS[self.step]
            current_winnings = self.bet * self.current_multiplier
            formatted_winnings = await self.cog.formatter(current_winnings)

            if self.step >= self.MAX_STEP:
                # Reached the top — auto cash-out
                await self.cog.process_game_result(self.user_id, "ladder", self.bet)
                await self.cog._record_game_outcome(
                    self.user_id, "ladder", "win", self.bet, self.PF,
                )
                await self.bot.database.process_treasury_transaction(
                    wallet_id=self.wallet_id,
                    amount=current_winnings,
                    description="Lucky Ladder Max Win!",
                )
                content = (
                    f"🏆 **You reached the top!**\n\n"
                    f"**Step:** {self.step} • **Multiplier:** {self.current_multiplier}x\n"
                    f"**Winnings:** {self.currency_name} **{formatted_winnings}**"
                )
                self._build_container(
                    accent_color=0xFEE75C, content=content, show_buttons=False,
                )
                await interaction.response.edit_message(view=self)
                await self.cog._remove_refund(self.session_id, user_id=self.user_id)
                await self.cog._end_game_session(
                    self.session_id, outcome="win",
                    final_state={"step": self.step, "winnings": str(current_winnings)},
                )
                self.stop()
                return

            # Still climbing — allocate the next nonce for the following step.
            self.PF = await self.cog.bump_fairgate_pf(self.user_id, self.PF)

            # Still climbing
            next_prob = self.STEP_PROBS.get(self.step, 0)
            next_mult = self.STEP_MULTS.get(self.step + 1, Decimal("0"))
            content = (
                f"🎉 **Climbed to step {self.step}!**\n\n"
                f"**Multiplier:** {self.current_multiplier}x • "
                f"**Winnings:** {self.currency_name} **{formatted_winnings}**\n"
                f"**Next Climb:** {next_prob}% chance → {next_mult}x"
            )
            self._build_container(accent_color=0x57F287, content=content)
            await interaction.response.edit_message(view=self)
            await self.cog._log_game_event(
                self.session_id, "climb",
                {"step": self.step, "multiplier": str(self.current_multiplier)},
            )
        else:
            # ── fell ──
            await self.cog.process_game_result(self.user_id, "ladder", self.bet)
            await self.cog._record_game_outcome(
                self.user_id, "ladder", "loss", self.bet, self.PF,
            )
            formatted_bet = await self.cog.formatter(self.bet)
            content = (
                f"💥 **You fell from step {self.step}!**\n\n"
                f"**Lost:** {self.currency_name} **{formatted_bet}**\n"
                f"Success chance was {success_chance}% — you rolled {roll / 100:.2f}"
            )
            self._build_container(
                accent_color=0xED4245, content=content, show_buttons=False,
            )
            await interaction.response.edit_message(view=self)
            await self.cog._remove_refund(self.session_id, user_id=self.user_id)
            await self.cog._end_game_session(
                self.session_id, outcome="loss",
                final_state={"step": self.step, "loss": str(self.bet)},
            )
            self.stop()

    async def _cashout_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "This is not your game!", ephemeral=True,
            )
        if self.step == 0:
            return await interaction.response.send_message(
                "You must climb at least once before cashing out!", ephemeral=True,
            )

        final_reward = self.bet * self.current_multiplier
        await self.cog.process_game_result(self.user_id, "ladder", self.bet)
        await self.cog._record_game_outcome(
            self.user_id, "ladder", "win", self.bet, self.PF,
        )
        await self.bot.database.process_treasury_transaction(
            wallet_id=self.wallet_id,
            amount=final_reward,
            description=f"Lucky Ladder Cashout (Step {self.step})",
        )
        formatted_reward = await self.cog.formatter(final_reward)
        content = (
            f"💰 **Cashed Out!**\n\n"
            f"**Step:** {self.step} • **Multiplier:** {self.current_multiplier}x\n"
            f"**Winnings:** {self.currency_name} **{formatted_reward}**"
        )
        self._build_container(
            accent_color=0x57F287, content=content, show_buttons=False,
        )
        await interaction.response.edit_message(view=self)
        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._end_game_session(
            self.session_id, outcome="win",
            final_state={"step": self.step, "winnings": str(final_reward)},
        )
        self.stop()

    # ── cleanup ──────────────────────────────────────────────────

    async def _refund_and_end(self, reason: str):
        await self.bot.database.process_treasury_transaction(
            wallet_id=self.wallet_id,
            amount=self.bet,
            description="Lucky Ladder Refund",
        )
        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._end_game_session(
            self.session_id, outcome="forced_end",
            final_state={"reason": reason, "refund": True},
        )
        self.stop()

    async def force_end(self, *, refund: bool = False):
        self.climb_button.disabled = True
        self.cashout_button.disabled = True
        self._build_container(
            accent_color=0xFEE75C,
            content="Game ended (forced).",
            show_buttons=False,
        )
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        if refund:
            await self.bot.database.process_treasury_transaction(
                wallet_id=self.wallet_id,
                amount=self.bet,
                description="Lucky Ladder Refund",
            )
        await self.cog._remove_refund(self.session_id, user_id=self.user_id)
        await self.cog._end_game_session(
            self.session_id, outcome="forced_end",
            final_state={"reason": "force_end", "refund": refund},
        )
        self.stop()

    async def on_timeout(self):
        try:
            self._build_container(
                accent_color=0xFEE75C,
                content="Game timed out. Your bet has been refunded.",
                show_buttons=False,
            )
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass
            await self._refund_and_end("timeout")
        except Exception:
            pass


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
        self.free_spin_retriggers = 0  # Track free spin retriggers (max 1)
        
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
            PF = await self.cog.start_fairgate_proof(user_id)
            grid, verification, final_PF = await self.cog.fairgate_generate_slots_grid(user_id, PF)

            # Evaluate results
            winning_lines = self.cog._evaluate_paylines(grid)
            scatter_count = self.cog._count_scatters(grid)
            scatter_payout = self.cog._calculate_scatter_payout(scatter_count)

            # Calculate winnings (payout multipliers * bet amount)
            line_winnings = sum(Decimal(str(line["payout"])) for line in winning_lines)
            total_multiplier = line_winnings + scatter_payout
            total_winnings = self.bet * total_multiplier * self.multiplier

            # Apply house edge (8%)
            if total_winnings > 0:
                total_winnings = AmountUtils.round_currency(total_winnings * Decimal("0.92"))

            # Award winnings
            if total_winnings > 0:
                wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=total_winnings,
                    description="Slots Win"
                )

            # Record this spin in game_history (spin-again was previously
            # missing the increment call, so all but the first spin in a
            # session were absent from history and from wager metrics).
            await self.cog._record_game_outcome(
                user_id, "slots",
                "win" if total_winnings > 0 else "loss",
                self.bet, final_PF,
            )
            
            # Check for free spins trigger (max 1 retrigger)
            new_free_spins = 0
            if scatter_count >= 3 and self.free_spin_retriggers < 1:
                if scatter_count == 3:
                    new_free_spins = 7
                elif scatter_count == 4:
                    new_free_spins = 10
                else:  # 5 scatters
                    new_free_spins = 15
                self.free_spins += new_free_spins
                self.free_spin_retriggers += 1
            
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
            symbols_by_tier[tier].sort(key=lambda x: x[1].get("payouts", {}).get(5, 0), reverse=True)
        
        # Special symbols (Wild & Scatter)
        special_lines = []
        for sym, data in symbols_by_tier["special"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            
            if data.get("wild") or data.get("substitutes"):
                special_lines.append(f"{emoji} **{name}** (Wild)")
                special_lines.append(f"└ Substitutes for all symbols except Scatter")
                special_lines.append(f"└ 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
            elif data.get("scatter"):
                special_lines.append(f"{emoji} **{name}** (Scatter)")
                special_lines.append(f"└ Pays anywhere on the reels!")
                special_lines.append(f"└ 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if special_lines:
            embed.add_field(name="✨ Special Symbols", value="\n".join(special_lines), inline=False)
        
        # High value symbols
        high_lines = []
        for sym, data in symbols_by_tier["high"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            high_lines.append(f"{emoji} **{name}**")
            high_lines.append(f"└ 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if high_lines:
            embed.add_field(name="💎 High Value", value="\n".join(high_lines), inline=True)
        
        # Medium value symbols
        medium_lines = []
        for sym, data in symbols_by_tier["medium"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            medium_lines.append(f"{emoji} **{name}**")
            medium_lines.append(f"└ 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if medium_lines:
            embed.add_field(name="⭐ Medium Value", value="\n".join(medium_lines), inline=True)
        
        # Low value symbols
        low_lines = []
        for sym, data in symbols_by_tier["low"]:
            emoji = data.get("emoji", "❓")
            name = data.get("name", sym)
            payouts = data.get("payouts", {})
            low_lines.append(f"{emoji} **{name}**")
            low_lines.append(f"└ 4× = {payouts.get(4, 0)}× | 5× = {payouts.get(5, 0)}×")
        
        if low_lines:
            embed.add_field(name="🍋 Low Value", value="\n".join(low_lines), inline=True)
        
        # Game info
        embed.add_field(
            name="📋 Game Info",
            value="**Paylines:** 10 fixed lines\n**Min Win:** 4-of-a-kind\n**Free Spins:** 3+ Scatters → 7/10/15 spins (max 1 retrigger)\n**House Edge:** 8%",
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


class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.utils = MiscUtils(self)
        self.currency_name = "<:coin:1359823671581085847>"
        self.active_players = set()
        self.active_games: dict[int, CrashView] = {}
        self.active_mines_views: dict[int, "MinesGridLayout"] = {}
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
            "keno",
        ]

        # FairGate integration (all casino games now use FairGate).
        self.fairgate_client: FairGateClient | None = None
        self._fairgate_backfill_task: asyncio.Task | None = None
        self._last_backfilled_hash: str | None = None
        try:
            self.fairgate_client = FairGateClient.from_env()
            logger.info(
                f"FairGate middleware initialized; all casino games now use FairGate"
            )
            # Always backfill seeds when FairGate is available.
            if self.fairgate_client.api_key:
                self._fairgate_backfill_task = asyncio.create_task(
                    self._fairgate_backfill_loop()
                )
        except FairGateError as e:
            logger.warning(f"FairGate middleware is configured but init failed: {e}")

        # SLOTS constants imported from fairness.py
        self.SLOTS_SYMBOLS = SLOTS_SYMBOLS
        self.SLOTS_PAYLINES = SLOTS_PAYLINES
        self.SLOTS_REEL_WEIGHTS = SLOTS_REEL_WEIGHTS

    def cog_unload(self) -> None:
        if self._fairgate_backfill_task:
            self._fairgate_backfill_task.cancel()
        if self.fairgate_client:
            asyncio.create_task(self.fairgate_client.close())

    async def _fairgate_backfill_loop(self) -> None:
        """Poll FairGate for revealed seeds and back-fill pending game rows."""
        if not self.fairgate_client:
            return
        # Stagger the first poll to avoid hammering the API on cog load.
        await asyncio.sleep(30)
        while True:
            try:
                await self._fairgate_poll_and_backfill()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"FairGate backfill poll failed: {e}")
            await asyncio.sleep(300)

    async def _fairgate_poll_and_backfill(self) -> None:
        """Fetch the current seed status and back-fill any revealed seed pair."""
        seed = await self.fairgate_client.get_seed(force=True)
        revealed_seed = (
            seed.get("previous_server_seed")
            or seed.get("revealed_server_seed")
            or seed.get("server_seed")
        )
        revealed_hash = (
            seed.get("previous_server_seed_hash")
            or seed.get("revealed_server_seed_hash")
            or seed.get("server_seed_hash")
        )
        if not revealed_seed or not revealed_hash:
            return
        if revealed_hash == self._last_backfilled_hash:
            return
        updated = await self.bot.database.backfill_fairgate_seed(
            revealed_hash, revealed_seed
        )
        if updated:
            logger.info(
                f"Back-filled {updated} FairGate game rows for seed "
                f"{revealed_hash[:12]}..."
            )
            self._last_backfilled_hash = revealed_hash

    async def start_fairgate_proof(self, user_id: int) -> dict:
        """Capture a FairGate proof bundle for a single game.

        Returns ``server_seed_hash`` from the active app seed, plus the user's
        ``client_seed`` and the next unused nonce.
        """
        client_seed, _nonce = await self.bot.database.get_client_seed(user_id)
        nonce = await self.bot.database.bump_fairgate_nonce(user_id)
        seed = await self.fairgate_client.get_seed()
        return {
            "server_seed_hash": seed["server_seed_hash"],
            "server_seed": None,
            "client_seed": client_seed,
            "nonce": nonce,
        }

    async def bump_fairgate_pf(self, user_id: int, PF: dict) -> dict:
        """Return a new FairGate proof bundle reusing the same seed hash.

        Used by interactive games that need multiple FairGate draws within a
        single user session (e.g., double-or-nothing rounds). The wallet nonce
        is bumped while the active server seed hash stays the same.
        """
        nonce = await self.bot.database.bump_fairgate_nonce(user_id)
        return {
            "server_seed_hash": PF["server_seed_hash"],
            "server_seed": None,
            "client_seed": PF["client_seed"],
            "nonce": nonce,
        }

    async def fairgate_play_mines(
        self,
        user_id: int,
        client_seed: str,
        nonce: int,
        server_seed_hash: str | None = None,
        *,
        rows: int = 5,
        cols: int = 5,
        mines: int = 3,
    ) -> list[int]:
        """Resolve a mines board through FairGate."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="mines",
            params={"rows": rows, "cols": cols, "mines": mines},
            client_seed=client_seed,
            nonce=nonce,
            server_seed_hash=server_seed_hash,
        )
        return result["outcome"]["bombs"]

    async def _record_game_outcome(
        self,
        user_id: int,
        game_name: str,
        outcome: str,
        bet_amount,
        PF: dict,
    ) -> None:
        """Persist a win/loss through FairGate.

        ``outcome`` must be ``"win"`` or ``"loss"``.
        """
        await self.bot.database.record_fairgate_game(
            user_id,
            game_name,
            outcome,
            bet_amount,
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            hash_hex=PF["server_seed_hash"],
        )

    async def fairgate_play_coinflip(
        self, user_id: int, PF: dict, *, choice: str = "heads"
    ) -> str:
        """Resolve a coinflip through FairGate; returns the winning side."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="coinflip",
            params={"choice": choice},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]["side"]

    @staticmethod
    def _map_supergamble_roll(roll: float) -> tuple[str, Decimal, str]:
        """Map a uniform [0, 100) roll to the supergamble outcome distribution.

        Preserves the local two-roll probabilities using a single roll so the
        result is verifiable through FairGate's dice-target engine:
          - mega win: 2.25%  (0.15 * 0.15)
          - normal win: 12.75% (0.85 * 0.15)
          - recovery: 8.5%   (0.85 * 0.10)
          - loss: 76.5%
        """
        if roll < 2.25:
            return (
                "mega_win",
                SUPERGAMBLE_BONUS_MULTIPLIER,
                "\n🌟 **MEGA WIN!** Extra multiplier applied!",
            )
        if roll < 15.0:
            return "win", SUPERGAMBLE_BASE_MULTIPLIER, ""
        if roll < 23.5:
            return "recovery", SUPERGAMBLE_RECOVERY_MULTIPLIER, ""
        return "loss", Decimal("0"), ""

    async def fairgate_play_supergamble(
        self, user_id: int, PF: dict
    ) -> tuple[str, Decimal, str]:
        """Resolve supergamble through FairGate's dice target engine."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="dice",
            params={"mode": "target", "target": 50.0, "over": True},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        roll = float(result["outcome"]["roll"])
        return self._map_supergamble_roll(roll)

    async def fairgate_play_dice_sum(
        self, user_id: int, PF: dict, *, dice: int = 2, sides: int = 6
    ) -> int:
        """Resolve a dice sum through FairGate; returns the integer total."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="dice",
            params={"mode": "sum", "dice": dice, "sides": sides},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return int(result["outcome"]["roll"])

    async def fairgate_play_roulette(
        self, user_id: int, PF: dict, *, wheel: str = "american"
    ) -> dict:
        """Resolve a roulette spin through FairGate."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="roulette",
            params={"wheel": wheel},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]

    async def fairgate_play_crash(
        self, user_id: int, PF: dict, *, target: float, max_crash: float, buckets: list
    ) -> dict:
        """Resolve a crash point through FairGate bucket mode."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="crash",
            params={
                "mode": "buckets",
                "target": target,
                "max_crash": max_crash,
                "buckets": buckets,
            },
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]

    async def fairgate_generate_crash_point(
        self, user_id: int, PF: dict, house_edge: Decimal
    ) -> Decimal:
        """Generate a per-user crash point using FairGate ``numbers`` draws.

        Mirrors the local bucket logic: draw a bucket from a 10,000-space
        distribution (45/35/15/5), then draw a cent-precise value inside the
        bucket, and apply the same VIP boost used locally.
        """
        base_edge = 0.04

        # 1) Bucket draw (one nonce).
        bucket_roll = (await self.fairgate_play_numbers(
            user_id=user_id, PF=PF, pool=10000, pick=1, replacement=True
        ))[0]

        if bucket_roll < 4500:
            lo, hi = 1.0, 2.0
        elif bucket_roll < 8000:
            lo, hi = 2.0, 5.0
        elif bucket_roll < 9500:
            lo, hi = 5.0, 20.0
        else:
            lo, hi = 20.0, 50.0

        # 2) Value draw inside the bucket (next nonce).
        PF = await self.bump_fairgate_pf(user_id, PF)
        cents_pool = int((hi - lo) * 100)
        value_roll = (await self.fairgate_play_numbers(
            user_id=user_id, PF=PF, pool=cents_pool, pick=1, replacement=True
        ))[0]
        v = lo + (value_roll / 100.0)

        # 3) Advance the wallet nonce so the next game starts at the same
        #    offset as the local path (two consumed nonces total).
        await self.bot.database.bump_fairgate_nonce(user_id)

        # 4) VIP boost (same formula as local generate_crash_point).
        user_edge = float(house_edge)
        if 0 < user_edge < base_edge:
            boost = (base_edge - user_edge) / base_edge
            v = v + (hi - v) * boost

        return Decimal(str(round(v, 2)))

    async def fairgate_play_ladder(
        self,
        user_id: int,
        PF: dict,
        *,
        step: int,
        probability: float,
        multiplier: float,
    ) -> dict:
        """Resolve a single ladder step through FairGate."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="ladder",
            params={"step": step, "probability": probability, "multiplier": multiplier},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]

    async def fairgate_play_numbers(
        self,
        user_id: int,
        PF: dict,
        *,
        pool: int,
        pick: int,
        replacement: bool = False,
    ) -> list[int]:
        """Resolve a number draw through FairGate; returns the drawn numbers."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="numbers",
            params={"pool": pool, "pick": pick, "replacement": replacement},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]["numbers"]

    async def fairgate_play_ridebus(
        self, user_id: int, PF: dict, *, deck_count: int = 1
    ) -> list[str]:
        """Resolve a shuffled deck through FairGate (ridebus engine)."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="ridebus",
            params={"deck_count": deck_count},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]["deck"]

    async def fairgate_play_hilo(
        self, user_id: int, PF: dict, *, deck_count: int = 1
    ) -> dict:
        """Resolve the first two cards for Hi-Lo through FairGate."""
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="hilo",
            params={"deck_count": deck_count},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]

    async def fairgate_play_hilo_deck(
        self, user_id: int, PF: dict
    ) -> list[str]:
        """Draw a full shuffled 13-rank abstract deck for multi-round Hi-Lo.

        The ``numbers`` engine returns a permutation of ``0..12`` which maps
        directly onto ``HILO_CARDS`` (A, 2 … K). The caller consumes cards
        from the returned list and refreshes it when exhausted.
        """
        numbers = await self.fairgate_play_numbers(
            user_id=user_id, PF=PF, pool=13, pick=13, replacement=False
        )
        return [HILO_CARDS[n] for n in numbers]

    async def fairgate_play_slots(
        self, user_id: int, PF: dict, *, reels: int = 5, rows: int = 4
    ) -> dict:
        """Resolve a slot spin through FairGate.

        Uses the visible window result; the caller evaluates paylines.
        """
        result = await self.fairgate_client.play(
            user_id=user_id,
            game="slots",
            params={"reels": reels, "rows": rows, "visible": True},
            client_seed=PF["client_seed"],
            nonce=PF["nonce"],
            server_seed_hash=PF["server_seed_hash"],
        )
        return result["outcome"]

    @staticmethod
    def _is_hash(value) -> bool:
        """Return True if a stored user ID value is a HMAC-SHA256 hex hash."""
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdefABCDEF" for c in value)
        )

    async def _resolve_id(self, value):
        """Resolve a stored user ID to a raw Discord ID when it is a hash."""
        if value is None or isinstance(value, int):
            return value
        if self._is_hash(value):
            return await self.bot.database.resolve_user_hash(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _resolve_ids(self, values):
        """Batch-resolve stored user IDs, leaving raw IDs unchanged."""
        if not values:
            return {}
        unique = list(dict.fromkeys(v for v in values if v is not None))
        hashes = [v for v in unique if self._is_hash(v)]
        resolved = await self.bot.database.resolve_user_hashes(hashes) if hashes else {}
        mapping = {}
        for v in unique:
            if isinstance(v, int):
                mapping[v] = v
            elif self._is_hash(v):
                mapping[v] = resolved.get(v)
            else:
                try:
                    mapping[v] = int(v)
                except (TypeError, ValueError):
                    mapping[v] = None
        return mapping

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

    def _pop_mines_view(self, channel_id: int) -> None:
        """Remove a live mines view from the per-channel registry."""
        self.active_mines_views.pop(channel_id, None)

    async def force_end_session(self, session_id, *, refund: bool = False) -> bool:
        handler = self.session_registry.get(str(session_id))
        if not handler:
            return False
        await handler(refund=refund)
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

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
            resolved_winners = await self._resolve_ids([uid for uid, _ in top_winners])
            for idx, (user_id, wins) in enumerate(top_winners):
                raw_id = resolved_winners.get(user_id)
                if raw_id:
                    user = (
                        ctx.guild.get_member(raw_id)
                        or self.bot.get_user(raw_id)
                        or await self.bot.fetch_user(raw_id)
                    )
                    display_name = user.display_name if user else f"Unknown {raw_id}"
                else:
                    display_name = "Unknown user"
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
        self, ctx: commands.Context, game: str, *args: str
    ):
        """
        Usage examples:
          !casino verify gamble 42
          !casino verify gamble @user 42
          !casino verify supergamble @user 7
          !casino verify dice @user 5
          !casino verify ladder 3 2
          !casino verify ladder @user 3 2
          !casino verify slots @user 10
          !casino verify blackjack @user 15
          !casino verify poker @user 12
          !casino verify roulette @user 9
          !casino verify mines @user 12

        The user mention is optional and defaults to the command author.
        For ladder, supply the step number as the last argument.
        """
        # ── parse the trailing arguments: optional user + nonce + game extras
        # Two valid shapes (plus ladder's extra step):
        #   <game> <nonce> [step]
        #   <game> <user> <nonce> [step]
        # Disambiguation: a bare integer (e.g. "42") is always a nonce. To
        # verify another user's game by ID, the caller must use the <@id>
        # mention form (or a username/display name).
        game_key = (game or "").lower()
        is_ladder = game_key == "ladder"
        member: discord.Member | discord.User = ctx.author
        nonce: int | None = None
        extra_args: list[str] = []

        if not args:
            return await ctx.reply(
                f"Usage: `!casino verify {game} [@user] <nonce>`"
                + (" `<step>`" if is_ladder else ""),
                mention_author=False,
            )

        def _looks_like_user_mention(token: str) -> bool:
            """A token is treated as a user mention only if it has explicit
            mention syntax (`<@id>` / `<@!id>`) or a non-numeric name. A
            bare integer is always a nonce.
            """
            if not token:
                return False
            stripped = token.strip().strip("<>@!").strip()
            # `<@123>` or `<@!123>` => user. bare "123" => nonce.
            return bool(token.strip().startswith("<@")) or not stripped.isdigit()

        async def _resolve_user(token: str):
            if not token:
                return None
            cleaned = token.strip().strip("<>@!").strip()
            if not cleaned:
                return None
            if cleaned.isdigit():
                uid = int(cleaned)
                resolved = None
                if ctx.guild:
                    resolved = ctx.guild.get_member(uid)
                if resolved is None:
                    resolved = self.bot.get_user(uid)
                if resolved is not None:
                    return resolved
                try:
                    return await self.bot.fetch_user(uid)
                except (discord.NotFound, discord.HTTPException):
                    return None
            if ctx.guild:
                for mm in ctx.guild.members:
                    if (
                        str(mm) == cleaned
                        or mm.display_name == cleaned
                        or mm.name == cleaned
                    ):
                        return mm
            return None

        first = args[0]
        if _looks_like_user_mention(first):
            resolved = await _resolve_user(first)
            if resolved is not None:
                member = resolved
                remainder = list(args[1:])
            else:
                # Looked like a user mention but didn't resolve. Tell the
                # caller instead of silently dropping it on the floor.
                return await ctx.reply(
                    f"Could not resolve user `{first}`. "
                    "Use a `@mention`, a username, or `<@id>`.",
                    mention_author=False,
                )
        else:
            remainder = list(args)

        if not remainder:
            return await ctx.reply(
                f"Provide a nonce to verify, e.g. `!casino verify {game} 42`.",
                mention_author=False,
            )

        try:
            nonce = int(remainder[0])
        except (TypeError, ValueError):
            return await ctx.reply(
                f"Nonce must be an integer, got `{remainder[0]}`.",
                mention_author=False,
            )
        extra_args = remainder[1:]

        user_id = member.id

        record = await self.bot.database.fetch_game_for_user(user_id, game_key, nonce)
        if not record:
            embed = discord.Embed(
                title="🔎 Verification — Error",
                description=(
                    f"No record found for `{game_key}` nonce `{nonce}` for "
                    f"{member.display_name}."
                ),
                color=discord.Color.red(),
            )
            return await ctx.reply(embed=embed, delete_after=8, mention_author=False)

        provider = getattr(record, "provider", "local")

        if provider != "fairgate":
            return await ctx.reply(
                "This record is from the deprecated local fairness system and can no longer be verified.",
                mention_author=False,
            )

        return await self._verify_fairgate_game(
            ctx, record, member, game_key, nonce, extra_args
        )

    async def _verify_fairgate_game(
        self,
        ctx: commands.Context,
        record: Any,
        member: discord.Member | discord.User,
        game_key: str,
        nonce: int,
        extra_args: list[str],
    ):
        """Verify a FairGate-backed game outcome against the remote verify endpoint."""
        server_seed = record.used_server_seed
        client_seed = record.client_seed
        server_seed_hash = record.hash

        embed = discord.Embed(
            title=f"🔒 FairGate Verified — {game_key.title()}",
            color=discord.Color.blurple(),
        )
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except (AttributeError, discord.HTTPException):
            pass

        if not server_seed:
            embed.description = (
                f"User: {member.display_name}\n"
                f"Nonce: `{nonce}` • Client Seed: `{client_seed}`\n"
                f"Server Seed Hash: `{server_seed_hash}`\n\n"
                "The server seed for this FairGate session has not been revealed yet. "
                "Verification will be available once the active seed is rotated."
            )
            return await ctx.reply(embed=embed, mention_author=False)

        # Map TPNEBOT game names to the actual FairGate engine game and the
        # params that were passed to ``/play`` for the recorded nonce.
        params = None
        verify_game = game_key
        if game_key == "mines":
            bombs = await self.bot.database.fetch_mines_bomb_count(member.id, nonce)
            if bombs is None:
                bombs = 3
                embed.add_field(
                    name="⚠️ Note",
                    value=(
                        "Could not locate the session bomb count, falling back to 3. "
                        "If the board below doesn't match, the session row may have been pruned."
                    ),
                    inline=False,
                )
            params = {"rows": 5, "cols": 5, "mines": bombs}
        elif game_key in ("gamble", "double"):
            verify_game = "coinflip"
            params = {"choice": "heads"}
        elif game_key == "supergamble":
            verify_game = "dice"
            params = {"mode": "target", "target": 50.0, "over": True}
        elif game_key == "dice":
            verify_game = "dice"
            params = {"mode": "sum", "dice": 2, "sides": 6}
        elif game_key == "roulette":
            verify_game = "roulette"
            params = {"wheel": "american"}
        elif game_key in ("blackjack", "poker"):
            verify_game = "ridebus"
            params = {"deck_count": 1}
        elif game_key == "hilo":
            verify_game = "numbers"
            params = {"pool": 13, "pick": 13, "replacement": False}
        elif game_key == "ladder":
            verify_game = "numbers"
            params = {"pool": 10000, "pick": 1, "replacement": True}
        elif game_key == "keno":
            verify_game = "numbers"
            params = {"pool": 30, "pick": 8, "replacement": False}
        elif game_key == "crash":
            # Crash points are generated via two ``numbers`` draws; the
            # recorded nonce is the first (bucket) draw.
            verify_game = "numbers"
            params = {"pool": 10000, "pick": 1, "replacement": True}
            embed.add_field(
                name="⚠️ Note",
                value=(
                    "Crash uses two FairGate draws (bucket + value). "
                    "This verifies the bucket draw only."
                ),
                inline=False,
            )
        elif game_key == "slots":
            # Slots uses one ``numbers`` draw per reel; the recorded nonce is
            # the final reel's draw. We don't store the reel index, so verify
            # against the first reel's weight distribution as a best-effort.
            verify_game = "numbers"
            first_weights = self.SLOTS_REEL_WEIGHTS[0]
            params = {
                "pool": sum(first_weights.values()),
                "pick": 4,
                "replacement": True,
            }
            embed.add_field(
                name="⚠️ Note",
                value=(
                    "Slots uses one FairGate draw per reel; this verifies "
                    "the first reel's parameters only."
                ),
                inline=False,
            )

        try:
            proof = await self.fairgate_client.verify(
                server_seed=server_seed,
                server_seed_hash=server_seed_hash,
                client_seed=client_seed,
                nonce=nonce,
                game=verify_game,
                params=params,
            )
        except Exception as e:
            logger.exception("FairGate verification failed")
            embed.color = discord.Color.red()
            embed.description = f"FairGate verification failed: `{e}`"
            return await ctx.reply(embed=embed, mention_author=False)

        embed.description = (
            f"User: {member.display_name}\n"
            f"Nonce: `{nonce}` • Client Seed: `{client_seed}`\n"
            f"Server Seed Hash: `{server_seed_hash}`\n"
            f"Algorithm: `{proof.get('algorithm', 'sha256_tag')}`"
        )

        outcome = proof.get("outcome")
        if game_key == "mines" and outcome is not None:
            bomb_cells = sorted(outcome.get("bombs", []))
            bomb_emoji = "<:bombs:1278849752301309994>"
            gem_emoji = "<:gems:1278849818025918497>"
            bomb_set = set(bomb_cells)
            grid = ""
            for row in range(5):
                grid += "".join(
                    bomb_emoji if (row * 5 + col) in bomb_set else gem_emoji
                    for col in range(5)
                ) + "\n"
            embed.add_field(
                name="💣 Mines — Revealed Board",
                value=(
                    f"Bombs: **{len(bomb_cells)}** • Safe cells: **{25 - len(bomb_cells)}**\n"
                    f"Positions: `{bomb_cells}`\n"
                    f"{grid}"
                ),
                inline=False,
            )
        else:
            pretty = (
                json.dumps(outcome, indent=2)
                if not isinstance(outcome, str)
                else outcome
            )
            embed.add_field(
                name="🎲 Outcome",
                value=f"```json\n{shorten(pretty, width=900, placeholder='…')}```",
                inline=False,
            )

        await ctx.reply(embed=embed, mention_author=False)

    @casino.command(
        name="seed", help="View your current client seed and the active FairGate server seed hash."
    )
    async def casino_seed(self, ctx: commands.Context):
        """
        Usage:
          !casino seed
        """
        client_seed, nonce = await self.bot.database.get_client_seed(ctx.author.id)

        if self.fairgate_client:
            seed = await self.fairgate_client.get_seed()
            server_hash = seed.get("server_seed_hash", "n/a")
        else:
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

            PF = await self.start_fairgate_proof(user_id)

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
            side = await self.fairgate_play_coinflip(user_id, PF, choice="heads")
            is_winner = side == "heads"

            # Process game result for rakeback
            await self.process_game_result(user_id, "gamble", amount)

            if is_winner:
                winnings = Decimal(amount) * win_multiplier
                await self._record_game_outcome(
                    user_id, "gamble", "win", amount, PF
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
                await self._record_game_outcome(
                    user_id, "gamble", "loss", amount, PF
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

            PF = await self.start_fairgate_proof(user_id)

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

            base_multiplier = SUPERGAMBLE_BASE_MULTIPLIER
            bonus_multiplier = SUPERGAMBLE_BONUS_MULTIPLIER

            sg_outcome, _multiplier, bonus_text = await self.fairgate_play_supergamble(
                user_id, PF
            )

            # Process game result for rakeback
            await self.process_game_result(user_id, "supergamble", amount)

            if sg_outcome == "win":
                raw_multiplier = base_multiplier
                winnings = (amount * raw_multiplier).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                await self._record_game_outcome(
                    user_id, "supergamble", "win", amount, PF
                )
            elif sg_outcome == "mega_win":
                raw_multiplier = bonus_multiplier
                winnings = (amount * raw_multiplier).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                await self._record_game_outcome(
                    user_id, "supergamble", "win", amount, PF
                )
            elif sg_outcome == "recovery":
                raw_multiplier = SUPERGAMBLE_RECOVERY_MULTIPLIER
                winnings = AmountUtils.round_currency(amount * raw_multiplier)
            else:
                raw_multiplier = Decimal("0")
                winnings = Decimal("0")

            if sg_outcome in ("win", "mega_win"):
                if winnings > treasury:
                    winnings = treasury
                    bonus_text += (
                        "\n💸 Treasury couldn't pay full amount — payout capped."
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
            elif sg_outcome == "recovery":
                if winnings > treasury:
                    winnings = treasury
                try:
                    await self.bot.database.process_treasury_transaction(
                        wallet_id, winnings, "SuperGamble Loss Recovery"
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
                        final_state={"recovery": str(winnings)},
                    )
                    await ctx.reply(embed=embed, delete_after=5)
                    return
                embed = discord.Embed(
                    description=f"You lost, but recovered **{await self.formatter(winnings)} {self.currency_name}**!",
                    color=discord.Color.blurple(),
                )
                await self._record_game_outcome(
                    user_id, "supergamble", "loss", amount, PF
                )
                outcome = "loss"
                outcome_amount = winnings
            else:
                await self._record_game_outcome(
                    user_id, "supergamble", "loss", amount, PF
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

    async def fairgate_generate_slots_grid(
        self, user_id: int, PF: dict
    ) -> tuple[list[list[str]], dict, dict]:
        """Generate a 5×4 slots grid through FairGate using the local reel weights.

        Each reel draws 4 independent positions from its weighted strip via the
        FairGate ``numbers`` engine. One nonce is consumed per reel.
        Returns ``(grid, verification, final_PF)``.
        """
        grid = []
        verification = {"reel_positions": [], "nonce_start": PF["nonce"], "provider": "fairgate"}
        current_PF = PF

        for reel_idx in range(5):
            weights = self.SLOTS_REEL_WEIGHTS[reel_idx]
            symbols = list(weights.keys())
            weight_values = list(weights.values())
            total_weight = sum(weight_values)

            positions = await self.fairgate_play_numbers(
                user_id=user_id,
                PF=current_PF,
                pool=total_weight,
                pick=4,
                replacement=True,
            )

            reel_symbols = []
            for pos in positions:
                cumulative = 0
                selected = symbols[0]
                for sym, w in zip(symbols, weight_values):
                    cumulative += w
                    if pos < cumulative:
                        selected = sym
                        break
                reel_symbols.append(selected)

            grid.append(reel_symbols)
            verification["reel_positions"].append(positions)

            # Each reel consumes one wallet nonce.
            if reel_idx < 4:
                current_PF = await self.bump_fairgate_pf(user_id, current_PF)

        return grid, verification, current_PF

    def _evaluate_paylines(self, grid: list[list[str]]) -> list[dict]:
        """Evaluate all 10 paylines against the grid.
        
        Args:
            grid: 5x4 grid as list of columns (reels)
        
        Returns:
            list of winning paylines, each with:
            - payline_id: int
            - symbol: str
            - count: int (4 or 5 matching)
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
            
            # Check if we have a winning combination (4+ matches)
            if match_count >= 4 and first_symbol:
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
        PF = await self.start_fairgate_proof(user_id)
        grid, verification, final_PF = await self.fairgate_generate_slots_grid(user_id, PF)

        # Evaluate paylines
        payline_wins = self._evaluate_paylines(grid)
        scatter_count = self._count_scatters(grid)
        scatter_payout = self._calculate_scatter_payout(scatter_count)

        # Calculate total multiplier
        total_multiplier = Decimal("0")
        for win in payline_wins:
            total_multiplier += win["payout"]
        total_multiplier += scatter_payout

        # Calculate winnings (apply 8% house edge)
        winnings = AmountUtils.round_currency(stake * total_multiplier * Decimal("0.92"))

        # Process game result for rakeback
        await self.process_game_result(user_id, "slots", stake)

        # Process outcome
        if winnings > 0:
            await self.bot.database.process_treasury_transaction(
                wallet_id, winnings, "Slots Win"
            )
        await self._record_game_outcome(
            user_id, "slots",
            "win" if winnings > 0 else "loss",
            stake, final_PF,
        )

        # Check for free spins trigger
        free_spins_awarded = 0
        if scatter_count >= 3:
            free_spins_awarded = {3: 7, 4: 10, 5: 15}.get(scatter_count, 15)

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
            multiplier=Decimal("1"),  # Removed 2x multiplier for treasury safety
            pf_data=final_PF,
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

        PF = await self.start_fairgate_proof(user_id)

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

        total = await self.fairgate_play_dice_sum(user_id, PF, dice=2, sides=6)
        die1 = die2 = None  # FairGate sum mode does not expose individual dice
        even_or_odd = "E" if total % 2 == 0 else "O"

        self.roll_history.setdefault(user_id, []).append(even_or_odd)
        if len(self.roll_history[user_id]) > 5:
            self.roll_history[user_id].pop(0)

        # Calculate house edge for RTP tracking and bonus
        house_edge = await self.calculate_house_edge(user_id)

        payout_multipliers = DICE_PAYOUTS
        even_odd_payout = DICE_EVEN_ODD_PAYOUT
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

        roll_desc = f"total **{total}**"

        if (normalized_guess in ["even", "evens"] and total % 2 == 0) or (
            normalized_guess in ["odd", "odds"] and total % 2 == 1
        ):
            winnings = Decimal(amount) * Decimal(even_odd_payout) * rtp_boost
            result = f"🎲 You rolled {roll_desc}\nYou guessed correctly and won {currency_name} **{await self.formatter(winnings)}**!"
        elif normalized_guess.isdigit() and int(normalized_guess) == total:
            multiplier = payout_multipliers[total]
            winnings = Decimal(amount) * Decimal(multiplier) * rtp_boost
            result = f"🎲 You rolled {roll_desc}\nExact match! You won {currency_name} **{await self.formatter(winnings)}** with a {multiplier}x payout!"
        else:
            result = f"🎲 You rolled {roll_desc}\nYou lost {currency_name} **{await self.formatter(amount)}**."

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
            await self._record_game_outcome(
                user_id, "dice", "win", amount, PF
            )
        else:
            await self._record_game_outcome(
                user_id, "dice", "loss", amount, PF
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
        description="Play roulette — interactive betting with Components V2.",
    )
    async def roulette(self, ctx: Context, bet_amount: str = None):
        if bet_amount is None:
            embed = discord.Embed(
                title="Roulette",
                description=(
                    "Usage: `!roulette <amount>`\n\n"
                    "An interactive roulette table will appear where you can place **multiple bets** "
                    f"(up to {ROULETTE_MAX_BETS}) on different positions. Each bet costs the specified amount.\n\n"
                    "Click bet types to toggle them on/off, then spin!\n\n"
                    f"**Paytable:**\n{ROULETTE_PAYTABLE_TEXT}"
                ),
                color=discord.Color.blue(),
            )
            await ctx.reply(embed=embed, delete_after=20)
            return

        user_id = ctx.author.id
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

        formatted_bet = await self.formatter(amount)
        view = RouletteView(
            bot=self.bot, cog=self, user_id=user_id, bet_amount=amount,
            wallet_id=wallet_id, currency_name=self.currency_name,
            formatted_bet=formatted_bet, ctx=ctx,
        )
        view.message = await ctx.reply(view=view)

    @commands.command(
        name="double",
        aliases=["don", "doubleornothing"],
        description="Start a double or nothing game",
    )
    async def double_or_nothing(self, ctx: Context, bet_amount: str):
        """Start a double or nothing game. Uses Components V2 Container system."""
        user_id = ctx.author.id
        session_id = None

        PF = await self.start_fairgate_proof(user_id)

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

        PF = await self.start_fairgate_proof(user_id)

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
                "insurance_bet": "0",
                "insurance_taken": False,
                "insurance_offered": False,
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

        deck = await self.fairgate_play_ridebus(user_id, PF, deck_count=1)

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
                await self._record_game_outcome(
                    user_id, "blackjack", "loss", hand_bet, PF
                )
            elif dealer_score > 21 or player_score > dealer_score:
                outcome = "win"
                await self._record_game_outcome(
                    user_id, "blackjack", "win", hand_bet, PF
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
                    await self._record_game_outcome(
                        user_id, "blackjack", "push", hand_bet, PF
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
                await self._record_game_outcome(
                    user_id, "blackjack", "loss", hand_bet, PF
                )
                result = f"Dealer wins! You lost {self.currency_name} **{await self.formatter(hand_bet)}**."

            return outcome, result, winnings

        async def build_game_container(accent_color: int, content_text: str, buttons_disabled: bool = False, show_insurance: bool = False) -> discord.ui.Container:
            """Build a Container with game state and action buttons."""
            if buttons_disabled:
                hit_button.disabled = True
                stay_button.disabled = True
                double_button.disabled = True
                split_button.disabled = True
                insurance_button.disabled = True

            # Build action row with optional insurance button
            if show_insurance and not insurance_taken:
                action_row = discord.ui.ActionRow(hit_button, stay_button, double_button, split_button, insurance_button)
            else:
                action_row = discord.ui.ActionRow(hit_button, stay_button, double_button, split_button)

            container = discord.ui.Container(
                discord.ui.TextDisplay("## 🃏 Blackjack"),
                discord.ui.TextDisplay(content_text),
                discord.ui.Separator(),
                action_row,
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

            # Insurance settlement
            insurance_result = ""
            if insurance_taken:
                dealer_has_blackjack = dealer_score == 21 and len(dealer_cards) == 2
                if dealer_has_blackjack:
                    # Insurance wins: 2:1 payout
                    insurance_payout = insurance_bet * Decimal(2)
                    await self.bot.database.process_treasury_transaction(
                        wallet_id=wallet_id,
                        amount=insurance_payout,
                        description="Blackjack Insurance Win",
                    )
                    total_winnings += insurance_payout
                    insurance_result = f"\n🎰 **Insurance Win!** Dealer had blackjack. You won {self.currency_name} **{await self.formatter(insurance_payout)}**"
                else:
                    # Insurance loses
                    insurance_result = f"\n❌ **Insurance Lost:** Dealer doesn't have blackjack. You lost {self.currency_name} **{await self.formatter(insurance_bet)}**"

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
                f"Bots cards: {hits_text} (Total: **{dealer_score}**){insurance_result}\n\n"
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

        # Insurance state tracking
        insurance_bet = Decimal(0)
        insurance_taken = False
        insurance_offered = False

        # Check if dealer's up-card is an Ace (offer insurance)
        # Card format: "RS" where R is rank (A, 2-10, J, Q, K) and S is suit
        dealer_upcard = dealer_cards[0]
        dealer_upcard_rank = dealer_upcard[:-1]  # Extract rank
        dealer_has_ace_up = dealer_upcard_rank == "A"

        # Define buttons first so they can be referenced in build_game_container
        hit_button = Button(label="Hit", style=discord.ButtonStyle.primary)
        stay_button = Button(label="Stay", style=discord.ButtonStyle.secondary)
        double_button = Button(label="Double Down", style=discord.ButtonStyle.success)
        split_button = Button(
            label="Split",
            style=discord.ButtonStyle.success,
            disabled=not can_split(player_cards),
        )
        insurance_button = Button(
            label="Insurance (Half Bet)",
            style=discord.ButtonStyle.secondary,
            disabled=True,  # Enabled only when insurance is offered
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

        async def insurance_callback(interaction: Interaction):
            nonlocal insurance_bet, insurance_taken
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)

            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This is not your game!", ephemeral=True
                )
                return

            # Check if insurance is still available
            if insurance_taken:
                await interaction.followup.send(
                    "You've already taken insurance!", ephemeral=True
                )
                return

            if not dealer_has_ace_up:
                await interaction.followup.send(
                    "Insurance is only available when dealer shows an Ace!", ephemeral=True
                )
                return

            # Insurance costs half the original bet
            insurance_amount = current_bet / Decimal(2)

            # Check if player has enough balance for insurance
            current_balance = await self.bot.database.get_wallet_balance(wallet_id)
            if insurance_amount > Decimal(str(current_balance)):
                await interaction.followup.send(
                    "Insufficient balance for insurance!", ephemeral=True
                )
                return

            # Deduct insurance bet from wallet
            try:
                await self.bot.database.process_treasury_transaction(
                    wallet_id=wallet_id,
                    amount=-insurance_amount,
                    description="Blackjack Insurance",
                )
            except ValueError as e:
                await interaction.followup.send(
                    f"🚫 Transaction failed: {e}", ephemeral=True
                )
                return

            # Update insurance state
            insurance_bet = insurance_amount
            insurance_taken = True
            insurance_button.disabled = True

            # Update game session state
            await self._update_game_session(
                session_id,
                state={
                    "user_id": user_id,
                    "bet": str(current_bet),
                    "wallet_id": str(wallet_id),
                    "insurance_bet": str(insurance_bet),
                    "insurance_taken": True,
                    "insurance_offered": True,
                }
            )

            await update_game_view(interaction)

        hit_button.callback = hit_callback
        stay_button.callback = stay_callback
        double_button.callback = double_callback
        split_button.callback = split_callback
        insurance_button.callback = insurance_callback

        # Enable insurance button if dealer shows Ace
        if dealer_has_ace_up:
            insurance_button.disabled = False
            insurance_offered = True
            # Update session state to reflect insurance was offered
            await self._update_game_session(
                session_id,
                state={
                    "user_id": user_id,
                    "bet": str(current_bet),
                    "wallet_id": str(wallet_id),
                    "insurance_bet": "0",
                    "insurance_taken": False,
                    "insurance_offered": True,
                }
            )

        # Build initial game view
        initial_content = (
            f"Your cards: {', '.join(player_cards)} (Total: **{player_score}**)\n"
            f"Bots visible card: {dealer_cards[0]}\n"
            f"Current bet: {self.currency_name} **{await self.formatter(current_bet)}**"
        )

        container = await build_game_container(0x5865F2, initial_content, show_insurance=dealer_has_ace_up)  # Discord blurple

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
        PF = await self.start_fairgate_proof(user_id)
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

        deck = await self.fairgate_play_ridebus(user_id, PF, deck_count=1)

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
        name="hilo", description="Play Hi-Lo — a card guessing game! Uses Components V2."
    )
    async def hilo(self, ctx: Context, bet_amount: str):
        """Play HiLo - guess if the next card will be higher or lower (Stake-style)"""
        user = ctx.author
        user_id = user.id

        if user_id in self.active_players:
            await ctx.reply("🚫 You already have an active game running! Finish it first.", delete_after=5)
            return

        self.active_players.add(user_id)

        try:
            PF = await self.start_fairgate_proof(user_id)

            wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
            balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

            try:
                bet_amount = await self.amount_handler(bet_amount, balance)
            except ValueError as e:
                embed = discord.Embed(description=str(e), color=discord.Color.red())
                await ctx.reply(embed=embed, delete_after=5)
                self.active_players.discard(user_id)
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
                self.active_players.discard(user_id)
                return

            session_id = await self._create_game_session(
                ctx, "hilo", owner_id=user_id, wager_total=bet_amount,
                state={"user_id": user_id, "bet": str(bet_amount), "wallet_id": str(wallet_id)},
                rng=PF,
            )
            await self._add_refund(
                session_id, user_id=user_id, wallet_id=str(wallet_id),
                amount=bet_amount, reason="hilo_bet",
            )

            house_edge = await self.calculate_house_edge(user_id)
            deck = await self.fairgate_play_hilo_deck(user_id, PF)
            # First card must be between 2 and Q (local parity).
            first_idx = next(
                (i for i, c in enumerate(deck) if c in HILO_CARDS[1:-1]),
                None,
            )
            if first_idx is None:
                raise RuntimeError("FairGate Hi-Lo deck contained no valid starting card")
            current_card = deck.pop(first_idx)

            view = HiLoView(
                bot=self.bot, cog=self, user=user, bet_amount=bet_amount,
                wallet_id=wallet_id, currency_name=self.currency_name,
                current_card=current_card, house_edge=house_edge,
                PF=PF, session_id=session_id, ctx=ctx,
                deck=deck,
            )
            view.message = await ctx.reply(view=view)
            self._register_session_handler(session_id, view.force_end)

        except Exception as e:
            self.active_players.discard(user_id)
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
        """Start climbing the Lucky Ladder with a bet. Uses Components V2 Container system."""
        user_id = ctx.author.id
        PF = await self.start_fairgate_proof(user_id)

        wallet_id = await self.bot.database.get_wallet_id_for_user(user_id)
        balance = Decimal(str(await self.bot.database.get_wallet_balance(wallet_id)))

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

        view = LadderView(
            bot=self.bot, cog=self, user_id=user_id, bet=amount,
            wallet_id=wallet_id, PF=PF, session_id=session_id,
            currency_name=self.currency_name,
        )
        await view.build_initial_container()

        await self.bot.database.set_cooldown(user_id, ctx.command.qualified_name, 5)
        msg = await ctx.reply(view=view)
        view.message = msg
        self._register_session_handler(session_id, view.force_end)

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

    @commands.command(name="minesadmin", hidden=True)
    @commands.is_owner()
    async def mines_admin(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ):
        channel = channel or ctx.channel
        view = self.active_mines_views.get(channel.id)
        if not view:
            return await ctx.send("No active mines game here.")

        multiplier = await view._calculate_multiplier()
        potential_payout = view.bet_amount * Decimal(str(multiplier))
        pf = view.PF or {}

        embed = discord.Embed(
            title=f"💣 Mines Admin — #{channel.name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Player", value=f"<@{await self._resolve_id(view.user_id)}>", inline=True)
        embed.add_field(
            name="Bet",
            value=f"{self.currency_name} **{await self.formatter(view.bet_amount)}**",
            inline=True,
        )
        embed.add_field(
            name="Bombs",
            value=str(len(view.bomb_positions)),
            inline=True,
        )
        embed.add_field(
            name="Gems Clicked",
            value=str(view.gems_clicked),
            inline=True,
        )
        embed.add_field(
            name="Current Multiplier",
            value=f"{multiplier:.2f}×",
            inline=True,
        )
        embed.add_field(
            name="Potential Payout",
            value=f"{self.currency_name} **{await self.formatter(potential_payout)}**",
            inline=True,
        )
        embed.add_field(
            name="Bomb Positions",
            value=" · ".join(str(p) for p in sorted(view.bomb_positions)),
            inline=False,
        )
        embed.add_field(
            name="Clicked Positions",
            value=(
                " · ".join(str(p) for p in sorted(view.clicked_positions))
                or "(none)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Provably Fair",
            value=(
                f"**server_seed_hash:** `{pf.get('server_seed_hash', 'n/a')}`\n"
                f"**client_seed:** `{pf.get('client_seed', 'n/a')}`\n"
                f"**nonce:** `{pf.get('nonce', 'n/a')}`"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)
        try:
            await ctx.message.add_reaction("✅")
        except discord.HTTPException:
            pass

    @commands.command(name="fairgateadmin", hidden=True)
    @commands.is_owner()
    async def fairgate_admin(
        self,
        ctx: commands.Context,
        action: str,
        name: str | None = None,
        allowed_games: str = "",
        rotation_policy: str = "after_each_bet",
        algorithm: str = "sha256_tag",
    ):
        """Owner-only FairGate administration.

        Actions:
          create <name> [games] [rotation_policy] [algorithm]
          rotate <app_id>
          get <app_id>
          games
          patch <app_id> [games] [rotation_policy] [algorithm]
          health

        Examples:
          !fairgateadmin create "TPNEBOT Casino" mines,dice,roulette after_each_bet sha256_tag
          !fairgateadmin rotate 66083062d68ec7a091a45ae3bf55ef23
          !fairgateadmin get 66083062d68ec7a091a45ae3bf55ef23
          !fairgateadmin games
          !fairgateadmin patch 66083062d68ec7a091a45ae3bf55ef23 mines,dice,roulette
          !fairgateadmin health
        """
        if not self.fairgate_client:
            return await ctx.send(
                "FairGate client is not initialized. Check FAIRGATE_BASE_URL and FAIRGATE_API_KEY.",
                delete_after=10,
            )

        action = action.lower().strip()

        try:
            if action == "create":
                if not name:
                    return await ctx.send(
                        "Usage: `!fairgateadmin create <name> [games] [policy] [algorithm]`",
                        delete_after=10,
                    )
                games = (
                    [g.strip().lower() for g in allowed_games.split(",") if g.strip()]
                    if allowed_games
                    else None
                )
                result = await self.fairgate_client.create_app(
                    name=name,
                    allowed_games=games,
                    rotation_policy=rotation_policy,
                    algorithm=algorithm,
                )
                app_id = result.get("id", "unknown")
                api_key = result.get("api_key", "unknown")

                embed = discord.Embed(
                    title="🔧 FairGate App Created",
                    description=f"App **`{name}`** registered successfully.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="App ID", value=f"`{app_id}`", inline=False)
                embed.add_field(
                    name="Allowed Games",
                    value=", ".join(games) if games else "(all built-in games)",
                    inline=False,
                )
                embed.add_field(name="Rotation Policy", value=rotation_policy, inline=True)
                embed.add_field(name="Algorithm", value=algorithm, inline=True)

                await ctx.send(embed=embed)

                # DM the API key to the owner so it does not sit in a channel.
                try:
                    await ctx.author.send(
                        f"FairGate app `{name}` (`{app_id}`) API key:\n```\n{api_key}\n```\n"
                        "Store this in Infisical as FAIRGATE_API_KEY for the bot to use it."
                    )
                    await ctx.send("API key sent via DM.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send(
                        "Could not DM the API key. Copy it from the server logs or Infisical.",
                        delete_after=10,
                    )
                return

            if action == "rotate":
                if not name:
                    return await ctx.send(
                        "Usage: `!fairgateadmin rotate <app_id>`", delete_after=10
                    )
                result = await self.fairgate_client.rotate_app_seed(name)
                embed = discord.Embed(
                    title="🔄 FairGate Seed Rotated",
                    color=discord.Color.blurple(),
                )
                embed.add_field(
                    name="New Server Seed Hash",
                    value=f"`{result.get('server_seed_hash', 'n/a')}`",
                    inline=False,
                )
                if "expires_at" in result:
                    embed.add_field(
                        name="Expires At", value=result["expires_at"], inline=True
                    )
                if "max_usage" in result:
                    embed.add_field(
                        name="Max Usage", value=result["max_usage"], inline=True
                    )
                return await ctx.send(embed=embed)

            if action == "get":
                if not name:
                    return await ctx.send(
                        "Usage: `!fairgateadmin get <app_id>`", delete_after=10
                    )
                result = await self.fairgate_client.get_app(name)
                embed = discord.Embed(
                    title="📋 FairGate App Info", color=discord.Color.blurple()
                )
                for key in ("id", "name", "allowed_games", "rotation_policy", "algorithm", "created_at"):
                    if key in result:
                        value = result[key]
                        if isinstance(value, list):
                            value = ", ".join(str(v) for v in value)
                        embed.add_field(name=key.replace("_", " ").title(), value=value, inline=False)
                return await ctx.send(embed=embed)

            if action == "games":
                result = await self.fairgate_client.list_games()
                games = result.get("games", result)
                if isinstance(games, dict):
                    lines = [f"**{k}**: {v}" for k, v in games.items()]
                elif isinstance(games, list):
                    lines = [f"• {g}" for g in games]
                else:
                    lines = [str(games)]
                embed = discord.Embed(
                    title="🎮 FairGate Supported Games",
                    description="\n".join(lines) if lines else "No games returned.",
                    color=discord.Color.blurple(),
                )
                return await ctx.send(embed=embed)

            if action == "patch":
                if not name:
                    return await ctx.send(
                        "Usage: `!fairgateadmin patch <app_id> [games] [policy] [algorithm]`",
                        delete_after=10,
                    )
                games = (
                    [g.strip().lower() for g in allowed_games.split(",") if g.strip()]
                    if allowed_games
                    else None
                )
                kwargs: dict[str, Any] = {}
                if games is not None:
                    kwargs["allowed_games"] = games
                if rotation_policy != "after_each_bet":
                    kwargs["rotation_policy"] = rotation_policy
                if algorithm != "sha256_tag":
                    kwargs["algorithm"] = algorithm
                if not kwargs:
                    return await ctx.send(
                        "Provide at least one field to patch (games, policy, or algorithm).",
                        delete_after=10,
                    )
                result = await self.fairgate_client.patch_app(name, **kwargs)
                embed = discord.Embed(
                    title="🔧 FairGate App Updated",
                    description=f"App **`{name}`** patched successfully.",
                    color=discord.Color.green(),
                )
                for key in ("id", "name", "allowed_games", "rotation_policy", "algorithm"):
                    if key in result:
                        value = result[key]
                        if isinstance(value, list):
                            value = ", ".join(str(v) for v in value)
                        embed.add_field(name=key.replace("_", " ").title(), value=value, inline=False)
                return await ctx.send(embed=embed)

            if action == "health":
                result = await self.fairgate_client.health()
                status = result.get("status", "unknown")
                embed = discord.Embed(
                    title="💓 FairGate Health",
                    description=f"Status: **`{status}`**",
                    color=discord.Color.green() if status == "ok" else discord.Color.yellow(),
                )
                for key, value in result.items():
                    if key == "status":
                        continue
                    embed.add_field(name=key.replace("_", " ").title(), value=str(value), inline=False)
                return await ctx.send(embed=embed)

            await ctx.send(
                f"Unknown action `{action}`. Use `create`, `rotate`, `get`, `games`, `patch`, or `health`.",
                delete_after=10,
            )

        except FairGateError as e:
            logger.exception("FairGate admin command failed")
            await ctx.send(
                f"FairGate admin command failed ({e.status or 'no status'}): `{e}`",
                delete_after=10,
            )
        except Exception as e:
            logger.exception("Unexpected error in fairgate admin command")
            await ctx.send(f"Unexpected error: `{e}`", delete_after=10)

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

            PF = await self.start_fairgate_proof(user_id)

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
            bomb_positions = await self.fairgate_play_mines(
                user_id,
                client_seed=PF["client_seed"],
                nonce=PF["nonce"],
                server_seed_hash=PF["server_seed_hash"],
                rows=grid_size,
                cols=grid_size,
                mines=num_bombs,
            )
            bomb_positions = set(bomb_positions)

            remaining_safe_cells = grid_size * grid_size - num_bombs
            init_multi = await self.bot.database.get_mines_multiplier(num_bombs, 0)
            db_multi = float(init_multi) if init_multi else 1.0
            math_multi = MinesGridLayout._compute_mines_multiplier(num_bombs, 0)
            # Prefer DB value when sane, fall back to math-derived value.
            if math_multi > 0 and (db_multi > math_multi * 10 or db_multi < math_multi / 10):
                multiplier = math_multi
            else:
                multiplier = db_multi

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
                game_view.message = main_message
                self.active_mines_views[ctx.channel.id] = game_view
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

            PF = await self.start_fairgate_proof(user_id)

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
                self, amount, formatted_bet, wallet_id, PF, session_id=session_id,
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


keno_payouts = KENO_PAYOUTS


class GameUI(discord.ui.LayoutView):
    def __init__(
        self,
        cog,
        player_bet: int,
        formatted_bet,
        player_wallet,
        PF: dict,
        session_id=None,
    ):
        super().__init__(timeout=None)
        self.container = GameUIContainer(
            cog, player_bet, formatted_bet, player_wallet, PF, session_id=session_id,
        )
        self.add_item(self.container)


class GameUIContainer(discord.ui.Container):
    def __init__(
        self,
        cog: Casino,
        player_bet: int,
        formatted_bet,
        player_wallet,
        PF: dict,
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
    def __init__(self, cog: Casino, PF: dict):
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

        drawn = await self.cog.fairgate_play_numbers(
            user_id=table_ui_view.player.id,
            PF=self.PF,
            pool=len(all_buttons),
            pick=table_ui_view.max_picks,
            replacement=False,
        )
        selected = [all_buttons[i] for i in drawn]
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
                await self.cog._record_game_outcome(
                    table_ui_view.player.id,
                    "keno",
                    "win" if total_win >= player_bet else "loss",
                    player_bet,
                    self.PF,
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

        pf = await self.cog.start_fairgate_proof(table_ui_view.player.id)
        drawn = await self.cog.fairgate_play_numbers(
            user_id=table_ui_view.player.id,
            PF=pf,
            pool=len(all_buttons),
            pick=table_ui_view.max_picks,
            replacement=False,
        )
        selected = [all_buttons[i] for i in drawn]

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
