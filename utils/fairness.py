# --- UPDATED PROVABLE FAIRNESS ---
import hmac, hashlib
from typing import List, Tuple, Dict

U64_RANGE = 1 << 64


def _u64_from_hmac(server_seed: str, client_seed: str, nonce: int, tag: str) -> int:
    """
    64-bit unsigned draw from:
      HMAC-SHA256(key=server_seed, msg=f"{client_seed}:{nonce}:{tag}")
    """
    msg = f"{client_seed}:{nonce}:{tag}".encode()
    digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big")


def _rehash_u64(u64: int) -> int:
    # Deterministic 'stretch' for rejection sampling retries
    return int.from_bytes(hashlib.sha256(u64.to_bytes(8, "big")).digest()[:8], "big")


def _rand_below_unbiased(u64: int, n: int) -> int:
    if n <= 0:
        raise ValueError("upper bound must be positive")
    limit = U64_RANGE - (U64_RANGE % n)
    while u64 >= limit:
        u64 = _rehash_u64(u64)
    return u64 % n


class ProvenFairness:
    """
    Mirror the Casino/Economy RNG semantics for verification.
    Adjust TAGS below only if your live game code used different tags.
    """

    class TAGS:
        # Default tags that match your helpers' defaults/usage:
        RANDBELOW = "randbelow"  # fair_randbelow(..., tag default)
        RANDOM = "random"  # fair_random(...)

        # Shuffles use explicit per-swap tags in your cog:
        @staticmethod
        def SHUFFLE(i: int) -> str:
            return f"shuffle:{i}"

        # If you gave specific tags per game draw in your cog,
        # add them here and use them below instead of RANDBELOW.

    # ---------- Core single-draw helpers (for verifier use) ----------
    @staticmethod
    def _randbelow(
        server_seed: str, client_seed: str, nonce: int, upper: int, tag: str
    ) -> int:
        u64 = _u64_from_hmac(server_seed, client_seed, nonce, tag)
        return _rand_below_unbiased(u64, upper)

    @staticmethod
    def _random01(server_seed: str, client_seed: str, nonce: int, tag: str) -> float:
        u64 = _u64_from_hmac(server_seed, client_seed, nonce, tag)
        return u64 / float(U64_RANGE)

    # ---------- Game verifiers ----------
    @staticmethod
    def verify_gamble(server_seed: str, client_seed: str, nonce: int) -> bool:
        """
        Mirrors: fair_randbelow(user, 2) with default tag='randbelow'
        Returns True if roll==1, else False.
        """
        roll = ProvenFairness._randbelow(
            server_seed, client_seed, nonce, 2, ProvenFairness.TAGS.RANDBELOW
        )
        return roll == 1

    @staticmethod
    def verify_supergamble(server_seed: str, client_seed: str, nonce: int) -> dict:
        """
        Two back-to-back draws (same as your previous logic), using default 'randbelow' tag.
        Assumes live code consumed 2 nonces in one game.
        """
        win_roll = ProvenFairness._randbelow(
            server_seed, client_seed, nonce, 100, ProvenFairness.TAGS.RANDBELOW
        )
        bonus_roll = ProvenFairness._randbelow(
            server_seed, client_seed, nonce + 1, 100, ProvenFairness.TAGS.RANDBELOW
        )
        return {
            "win_roll": win_roll,
            "bonus_roll": bonus_roll,
            "win": (win_roll < 15),
            "mega_win": (bonus_roll < 15),
        }

    @staticmethod
    def verify_dice(server_seed: str, client_seed: str, nonce: int) -> Tuple[int, int]:
        """
        Two consecutive fair d6 rolls using 'randbelow' (nonce, nonce+1).
        """
        d1 = (
            ProvenFairness._randbelow(
                server_seed, client_seed, nonce, 6, ProvenFairness.TAGS.RANDBELOW
            )
            + 1
        )
        d2 = (
            ProvenFairness._randbelow(
                server_seed, client_seed, nonce + 1, 6, ProvenFairness.TAGS.RANDBELOW
            )
            + 1
        )
        return d1, d2

    @staticmethod
    def verify_ladder(
        server_seed: str, client_seed: str, nonce: int, step: int
    ) -> Tuple[float, float]:
        """
        Roll in [0,10000) using unbiased randbelow; convert to percent with 2dp.
        Prob thresholds mirror your command implementation table.
        """
        roll_raw = ProvenFairness._randbelow(
            server_seed, client_seed, nonce, 10000, ProvenFairness.TAGS.RANDBELOW
        )
        roll_pct = roll_raw / 100.0
        probabilities = {
            0: 83,
            1: 80,
            2: 75,
            3: 70,
            4: 65,
            5: 58,
            6: 52,
            7: 46,
            8: 40,
            9: 35,
        }
        threshold = float(probabilities.get(step, 0))
        return roll_pct, threshold

    @staticmethod
    def verify_slots(
        server_seed: str,
        client_seed: str,
        start_nonce: int,
        reel_weights: dict = None,
        symbols_data: dict = None,
        paylines: list = None,
    ) -> Dict[str, any]:
        """
        Verify 5x4 slots grid with 20 paylines, wilds, and scatters.
        
        Mirrors the live game's _async_generate_spin_grid logic using static RNG.
        
        Args:
            server_seed: Revealed server seed
            client_seed: Client seed from wallet
            start_nonce: Starting nonce for the spin
            reel_weights: Optional custom reel weights (defaults to standard weights)
            symbols_data: Optional custom symbols data (defaults to standard symbols)
            paylines: Optional custom paylines (defaults to standard 20 paylines)
        
        Returns:
            dict with:
                - grid: 5x4 grid as list of columns (reels)
                - grid_display: Formatted emoji grid string
                - wins: List of winning paylines with payouts
                - scatter_count: Number of scatter symbols
                - scatter_payout: Scatter payout multiplier
                - total_payout: Sum of all win payouts
                - nonce_end: Final nonce after 20 RNG calls
        """
        # Default reel weights (matching casino.py SLOTS_REEL_WEIGHTS)
        if reel_weights is None:
            reel_weights = {
                0: {"lemon": 25, "slot_machine": 22, "cherry": 18, "star": 12,
                    "bell": 8, "seven": 6, "diamond": 5, "wild": 3, "scatter": 1},
                1: {"lemon": 23, "slot_machine": 20, "cherry": 17, "star": 14,
                    "bell": 10, "seven": 7, "diamond": 5, "wild": 3, "scatter": 1},
                2: {"lemon": 20, "slot_machine": 18, "cherry": 16, "star": 15,
                    "bell": 12, "seven": 9, "diamond": 6, "wild": 3, "scatter": 1},
                3: {"lemon": 18, "slot_machine": 16, "cherry": 15, "star": 16,
                    "bell": 14, "seven": 11, "diamond": 7, "wild": 2, "scatter": 1},
                4: {"lemon": 15, "slot_machine": 14, "cherry": 14, "star": 16,
                    "bell": 15, "seven": 13, "diamond": 10, "wild": 2, "scatter": 1},
            }
        
        # Default symbols data (matching casino.py SLOTS_SYMBOLS)
        if symbols_data is None:
            symbols_data = {
                "diamond": {"emoji": "💎", "payouts": {5: 100, 4: 25, 3: 8}},
                "seven": {"emoji": "7️⃣", "payouts": {5: 75, 4: 20, 3: 6}},
                "bell": {"emoji": "🔔", "payouts": {5: 50, 4: 15, 3: 5}},
                "star": {"emoji": "⭐", "payouts": {5: 30, 4: 10, 3: 3}},
                "cherry": {"emoji": "🍒", "payouts": {5: 20, 4: 8, 3: 2.5}},
                "lemon": {"emoji": "🍋", "payouts": {5: 15, 4: 5, 3: 1.5}},
                "slot_machine": {"emoji": "🎰", "payouts": {5: 10, 4: 4, 3: 1}},
                "wild": {"emoji": "🃏", "payouts": {5: 500, 4: 100, 3: 25}},
                "scatter": {"emoji": "💰", "payouts": {5: 50, 4: 20, 3: 5}},
            }
        
        # Default paylines (matching casino.py SLOTS_PAYLINES)
        if paylines is None:
            paylines = [
                {"id": 1, "name": "Top Row", "coords": [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]},
                {"id": 2, "name": "Upper Middle", "coords": [(1, 0), (1, 1), (1, 2), (1, 3), (1, 4)]},
                {"id": 3, "name": "Lower Middle", "coords": [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)]},
                {"id": 4, "name": "Bottom Row", "coords": [(3, 0), (3, 1), (3, 2), (3, 3), (3, 4)]},
                {"id": 5, "name": "V-Shape Top", "coords": [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)]},
                {"id": 6, "name": "V-Shape Bottom", "coords": [(3, 0), (2, 1), (1, 2), (2, 3), (3, 4)]},
                {"id": 7, "name": "Inverted V Top", "coords": [(3, 0), (2, 1), (1, 2), (2, 3), (3, 4)]},
                {"id": 8, "name": "Inverted V Bottom", "coords": [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)]},
                {"id": 9, "name": "Diagonal Down", "coords": [(0, 0), (1, 1), (2, 2), (3, 3), (3, 4)]},
                {"id": 10, "name": "Diagonal Up", "coords": [(3, 0), (2, 1), (1, 2), (0, 3), (0, 4)]},
                {"id": 11, "name": "Zigzag 1", "coords": [(0, 0), (1, 1), (0, 2), (1, 3), (0, 4)]},
                {"id": 12, "name": "Zigzag 2", "coords": [(3, 0), (2, 1), (3, 2), (2, 3), (3, 4)]},
                {"id": 13, "name": "Zigzag 3", "coords": [(1, 0), (0, 1), (1, 2), (0, 3), (1, 4)]},
                {"id": 14, "name": "Zigzag 4", "coords": [(2, 0), (3, 1), (2, 2), (3, 3), (2, 4)]},
                {"id": 15, "name": "W-Shape Top", "coords": [(0, 0), (1, 1), (0, 2), (1, 3), (0, 4)]},
                {"id": 16, "name": "W-Shape Bottom", "coords": [(3, 0), (2, 1), (3, 2), (2, 3), (3, 4)]},
                {"id": 17, "name": "M-Shape Top", "coords": [(1, 0), (0, 1), (1, 2), (0, 3), (1, 4)]},
                {"id": 18, "name": "M-Shape Bottom", "coords": [(2, 0), (3, 1), (2, 2), (3, 3), (2, 4)]},
                {"id": 19, "name": "Step Down", "coords": [(0, 0), (0, 1), (1, 2), (1, 3), (2, 4)]},
                {"id": 20, "name": "Step Up", "coords": [(2, 0), (2, 1), (1, 2), (1, 3), (0, 4)]},
            ]
        
        # Generate 5x4 grid (column-major: grid[col][row])
        grid = []
        nonce = start_nonce
        
        for reel_idx in range(5):
            weights = reel_weights[reel_idx]
            symbols = list(weights.keys())
            weight_values = list(weights.values())
            total_weight = sum(weight_values)
            
            # Build cumulative weights for selection
            cumulative = []
            running = 0
            for w in weight_values:
                running += w
                cumulative.append(running)
            
            reel_symbols = []
            for row_idx in range(4):
                # Get random position using provably fair RNG
                pos = ProvenFairness._randbelow(
                    server_seed, client_seed, nonce, total_weight, ProvenFairness.TAGS.RANDBELOW
                )
                nonce += 1
                
                # Select symbol based on weighted position
                selected = symbols[0]
                for i, bound in enumerate(cumulative):
                    if pos < bound:
                        selected = symbols[i]
                        break
                reel_symbols.append(selected)
            
            grid.append(reel_symbols)
        
        # Evaluate paylines
        wins = []
        for payline in paylines:
            coords = payline["coords"]
            symbols_on_line = []
            
            for row, col in coords:
                if col < len(grid) and row < len(grid[col]):
                    symbols_on_line.append(grid[col][row])
            
            if len(symbols_on_line) != 5:
                continue
            
            # Find longest matching sequence from left (wilds substitute)
            first_symbol = None
            match_count = 0
            
            for i, sym in enumerate(symbols_on_line):
                if sym == "scatter":
                    break  # Scatter doesn't form paylines
                
                if first_symbol is None:
                    if sym != "wild":
                        first_symbol = sym
                        match_count = 1
                    # Wild at position 0 - keep looking for actual symbol
                elif sym == first_symbol or sym == "wild":
                    match_count += 1
                else:
                    break  # Mismatch - stop counting
            
            # Check for winning combination (3+ matches)
            if match_count >= 3 and first_symbol:
                symbol_data = symbols_data.get(first_symbol, {})
                payouts = symbol_data.get("payouts", {})
                payout_mult = payouts.get(match_count, 0)
                
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
        
        # Count scatters
        scatter_count = 0
        for col in grid:
            for sym in col:
                if sym == "scatter":
                    scatter_count += 1
        
        # Calculate scatter payout
        scatter_data = symbols_data.get("scatter", {})
        scatter_payouts = scatter_data.get("payouts", {})
        scatter_payout = scatter_payouts.get(scatter_count, 0)
        
        # Calculate total payout
        total_payout = sum(w["payout"] for w in wins) + scatter_payout
        
        # Format grid display
        winning_coords = []
        for w in wins:
            winning_coords.extend(w.get("coords", []))
        winning_set = set(winning_coords)
        
        grid_lines = []
        for row in range(4):
            row_display = []
            for col in range(5):
                sym = grid[col][row]
                sym_data = symbols_data.get(sym, {})
                emoji = sym_data.get("emoji", "❓")
                
                if (row, col) in winning_set:
                    row_display.append(f"【{emoji}】")
                else:
                    row_display.append(f"｜{emoji}｜")
            grid_lines.append("".join(row_display))
        grid_display = "\n".join(grid_lines)
        
        return {
            "grid": grid,
            "grid_display": grid_display,
            "wins": wins,
            "scatter_count": scatter_count,
            "scatter_payout": scatter_payout,
            "total_payout": total_payout,
            "nonce_end": nonce,
        }

    @staticmethod
    def _shuffle_deck(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Tuple[List[str], int]:
        """
        Fisher–Yates with per-swap tag f"shuffle:{i}" just like your cog.
        NOTE: If your runtime shuffle used default 'randbelow' instead,
        replace TAGS.SHUFFLE(i) with TAGS.RANDBELOW below.
        """
        suits = ["♥", "♦", "♣", "♠"]
        ranks = [str(n) for n in range(2, 11)] + ["J", "Q", "K", "A"]
        deck = [f"{r}{s}" for s in suits for r in ranks]

        nonce = start_nonce
        for i in range(len(deck) - 1, 0, -1):
            # draw j in [0, i]
            u64 = _u64_from_hmac(
                server_seed, client_seed, nonce, ProvenFairness.TAGS.SHUFFLE(i)
            )
            j = _rand_below_unbiased(u64, i + 1)
            deck[i], deck[j] = deck[j], deck[i]
            nonce += 1
        return deck, nonce

    @staticmethod
    def verify_blackjack(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        deck, next_nonce = ProvenFairness._shuffle_deck(
            server_seed, client_seed, start_nonce
        )
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop()]
        return {
            "shuffled_deck": deck,
            "player_cards": player,
            "dealer_cards": dealer,
            "next_nonce": next_nonce,
        }

    @staticmethod
    def verify_roulette(server_seed: str, client_seed: str, nonce: int) -> dict:
        """
        Mirrors roulette spin:
          spin_result = fair_choice(user, [0..36, "00"], tag="choice")
        Returns a dict with the landed value and derived labels.
        """
        # Sequence & tag must match runtime exactly
        all_numbers = list(range(0, 37)) + ["00"]
        tag = "choice"

        # Draw unbiased index in [0, 38)
        u64 = _u64_from_hmac(server_seed, client_seed, nonce, tag)
        idx = _rand_below_unbiased(u64, len(all_numbers))
        spin_result = all_numbers[idx]

        # Color/props, matching your command logic
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

        is_int = isinstance(spin_result, int)
        is_red = is_int and spin_result in red_numbers
        is_black = is_int and spin_result in black_numbers
        is_green = (spin_result == 0) or (spin_result == "00")
        color = "Green" if is_green else ("Red" if is_red else "Black")

        return {
            "spin_result": spin_result,  # int in 0..36 or "00"
            "color": color,  # "Red" | "Black" | "Green"
            "is_red": is_red,
            "is_black": is_black,
            "is_green": is_green,
            "is_even": is_int and (spin_result % 2 == 0),
            "is_odd": is_int and (spin_result % 2 == 1),
        }

    @staticmethod
    def verify_poker(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        deck, next_nonce = ProvenFairness._shuffle_deck(
            server_seed, client_seed, start_nonce
        )
        player = [deck.pop(), deck.pop()]
        bot = [deck.pop(), deck.pop()]
        community = [deck.pop() for _ in range(5)]
        return {
            "player_hand": player,
            "bot_hand": bot,
            "community": community,
            "remaining_deck": deck,
            "next_nonce": next_nonce,
        }

    @staticmethod
    def verify_ridebus(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        deck, next_nonce = ProvenFairness._shuffle_deck(
            server_seed, client_seed, start_nonce
        )
        return {"shuffled_deck": deck, "next_nonce": next_nonce}

    # ------- Optional: add verifiers for games you asked about earlier -------
    @staticmethod
    def verify_double(server_seed: str, client_seed: str, nonce: int) -> bool:
        """
        Double-or-Nothing: 1 draw in [0,2) via randbelow; True==win.
        If your live code used a custom tag, change TAGS.RANDBELOW.
        """
        return (
            ProvenFairness._randbelow(
                server_seed, client_seed, nonce, 2, ProvenFairness.TAGS.RANDBELOW
            )
            == 1
        )

    @staticmethod
    def verify_mines(
        server_seed: str,
        client_seed: str,
        start_nonce: int,
        board_size: int,
        bombs: int,
    ) -> Dict[str, List[int]]:
        """
        Rebuild a deterministic mines layout by selecting `bombs` unique cells
        from range(board_size) with successive unbiased draws.
        NOTE: This mirrors a typical approach: Fisher–Yates style sampling.
        """
        indices = list(range(board_size))
        nonce = start_nonce
        # partial Fisher–Yates to pick `bombs` unique slots
        for i in range(bombs):
            # choose index j in [i, board_size-1]
            u64 = _u64_from_hmac(
                server_seed, client_seed, nonce, ProvenFairness.TAGS.RANDBELOW
            )
            j = i + _rand_below_unbiased(u64, board_size - i)
            indices[i], indices[j] = indices[j], indices[i]
            nonce += 1
        bomb_cells = indices[:bombs]
        return {"bomb_cells": sorted(bomb_cells), "next_nonce": nonce}

    @staticmethod
    def verify_crash(server_seed: str, client_seed: str, nonce: int) -> float:
        """
        Example crash seed → multiplier mapping using a single uniform [0,1).
        Replace with your exact function if different.
        """
        r = ProvenFairness._random01(
            server_seed, client_seed, nonce, ProvenFairness.TAGS.RANDOM
        )  # (0,1)
        # Simple, common crash curve example:
        # Prevent 0 by clamping tiny epsilon
        eps = 1e-12
        r = max(r, eps)
        # Example multiplier formula (tweak to match your live logic):
        # m = floor( (1 / (1 - r)) * 100 ) / 100
        m = 1.0 / (1.0 - r)
        return float(f"{m:.2f}")


# --- END UPDATED PROVABLE FAIRNESS ---
