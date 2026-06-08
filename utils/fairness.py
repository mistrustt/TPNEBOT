# --- UPDATED PROVABLE FAIRNESS ---
import hmac, hashlib
from decimal import Decimal
from typing import Any, List, Sequence, Tuple, Dict

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


# ══════════════════════════════════════════════════════════════
#  Pure Stateless RNG Helpers
# ══════════════════════════════════════════════════════════════
# These mirror the Casino class async methods but are pure functions.
# Each takes (server_seed, client_seed, nonce, ...) and returns (result, next_nonce).


def fair_randbelow(
    server_seed: str, client_seed: str, nonce: int, upper: int, *, tag: str = "randbelow"
) -> Tuple[int, int]:
    u64 = _u64_from_hmac(server_seed, client_seed, nonce, tag)
    return _rand_below_unbiased(u64, upper), nonce + 1


def fair_random(server_seed: str, client_seed: str, nonce: int) -> Tuple[float, int]:
    u64 = _u64_from_hmac(server_seed, client_seed, nonce, "random")
    return u64 / float(U64_RANGE), nonce + 1


def fair_choice(
    server_seed: str, client_seed: str, nonce: int, seq: Sequence[Any], *, tag: str = "choice"
) -> Tuple[Any, int]:
    idx, next_nonce = fair_randbelow(server_seed, client_seed, nonce, len(seq), tag=tag)
    return seq[idx], next_nonce


def fair_shuffle(
    server_seed: str, client_seed: str, start_nonce: int, deck: List[Any]
) -> int:
    """Shuffle deck in-place via Fisher-Yates. Returns next_nonce."""
    nonce = start_nonce
    for i in range(len(deck) - 1, 0, -1):
        j, nonce = fair_randbelow(server_seed, client_seed, nonce, i + 1, tag=f"shuffle:{i}")
        deck[i], deck[j] = deck[j], deck[i]
    return nonce


def fair_sample(
    server_seed: str, client_seed: str, start_nonce: int, seq: Sequence[Any], k: int
) -> Tuple[List[Any], int]:
    clone = list(seq)
    next_nonce = fair_shuffle(server_seed, client_seed, start_nonce, clone)
    return clone[:k], next_nonce


def fair_uniform(
    server_seed: str, client_seed: str, nonce: int, min_value: float, max_value: float
) -> Tuple[float, int]:
    r, next_nonce = fair_random(server_seed, client_seed, nonce)
    return min_value + (max_value - min_value) * r, next_nonce


# ══════════════════════════════════════════════════════════════
#  Game Constants  (single source of truth)
# ══════════════════════════════════════════════════════════════

# ── Dice ──
DICE_PAYOUTS = {
    2: 34.2, 3: 17.1, 4: 11.4, 5: 8.55, 6: 6.85,
    7: 5.7, 8: 6.85, 9: 8.55, 10: 11.4, 11: 17.1, 12: 34.2,
}
DICE_EVEN_ODD_PAYOUT = 1.9

# ── SuperGamble ──
SUPERGAMBLE_WIN_THRESHOLD = 15
SUPERGAMBLE_MEGA_THRESHOLD = 15
SUPERGAMBLE_RECOVERY_THRESHOLD = 10
SUPERGAMBLE_BASE_MULTIPLIER = Decimal("6.0")
SUPERGAMBLE_BONUS_MULTIPLIER = Decimal("8.0")
SUPERGAMBLE_RECOVERY_MULTIPLIER = Decimal("0.20")

# ── Ladder ──
LADDER_STEP_PROBS = {
    0: 83, 1: 80, 2: 75, 3: 70, 4: 65,
    5: 58, 6: 52, 7: 46, 8: 40, 9: 35,
}
LADDER_STEP_MULTS = {
    0: Decimal("1.00"), 1: Decimal("1.15"), 2: Decimal("1.45"),
    3: Decimal("1.90"), 4: Decimal("2.75"), 5: Decimal("4.20"),
    6: Decimal("7.25"), 7: Decimal("14.00"), 8: Decimal("30.00"),
    9: Decimal("75.00"), 10: Decimal("215.00"),
}
LADDER_MAX_STEP = 10

# ── Crash ──
# Each tuple: (cumulative_threshold, min_value, max_value)
CRASH_RANGES = [
    (0.45, 1.0, 2.0),
    (0.80, 2.0, 5.0),
    (0.95, 5.0, 20.0),
    (1.00, 20.0, 50.0),
]
CRASH_BUCKET_NAMES = ["low", "med_low", "med", "high"]

# ── HiLo ──
HILO_CARDS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
HILO_CARD_VALUES = {card: idx for idx, card in enumerate(HILO_CARDS)}

# ── Roulette ──
ROULETTE_ALL_NUMBERS = list(range(0, 37)) + ["00"]
ROULETTE_RED_NUMBERS = {
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36,
}
ROULETTE_BLACK_NUMBERS = {
    2, 4, 6, 8, 10, 11, 13, 15, 17,
    20, 22, 24, 26, 28, 29, 31, 33, 35,
}

# ── Keno ──
KENO_PAYOUTS = {
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

# ── Slots ──
SLOTS_SYMBOLS = {
    "diamond": {"emoji": "💎", "name": "Diamond", "tier": "high", "payouts": {5: 25, 4: 8}},
    "seven": {"emoji": "7️⃣", "name": "Lucky Seven", "tier": "high", "payouts": {5: 20, 4: 6}},
    "bell": {"emoji": "🔔", "name": "Bell", "tier": "high", "payouts": {5: 15, 4: 4}},
    "star": {"emoji": "⭐", "name": "Star", "tier": "medium", "payouts": {5: 8, 4: 2}},
    "cherry": {"emoji": "🍒", "name": "Cherry", "tier": "medium", "payouts": {5: 5, 4: 1.5}},
    "lemon": {"emoji": "🍋", "name": "Lemon", "tier": "low", "payouts": {5: 3.8, 4: 0.5}},
    "slot_machine": {"emoji": "🎰", "name": "Slot Machine", "tier": "low", "payouts": {5: 2.5, 4: 0.3}},
    "wild": {"emoji": "🃏", "name": "Wild", "tier": "special", "payouts": {5: 25, 4: 10}, "substitutes": True},
    "scatter": {"emoji": "💰", "name": "Scatter", "tier": "special", "payouts": {5: 20, 4: 5}, "scatter_pays": True},
}

SLOTS_PAYLINES = [
    {"id": 1, "name": "Top Row", "coords": [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)], "color": "🔴"},
    {"id": 2, "name": "Upper Middle", "coords": [(1, 0), (1, 1), (1, 2), (1, 3), (1, 4)], "color": "🟡"},
    {"id": 3, "name": "Lower Middle", "coords": [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)], "color": "🟢"},
    {"id": 4, "name": "Bottom Row", "coords": [(3, 0), (3, 1), (3, 2), (3, 3), (3, 4)], "color": "🔵"},
    {"id": 5, "name": "V-Shape Top", "coords": [(0, 0), (1, 1), (2, 2), (1, 3), (0, 4)], "color": "🟣"},
    {"id": 6, "name": "V-Shape Bottom", "coords": [(3, 0), (2, 1), (1, 2), (2, 3), (3, 4)], "color": "🟠"},
    {"id": 7, "name": "W-Shape", "coords": [(0, 0), (2, 1), (0, 2), (2, 3), (0, 4)], "color": "⚪"},
    {"id": 8, "name": "M-Shape", "coords": [(3, 0), (1, 1), (3, 2), (1, 3), (3, 4)], "color": "⚫"},
    {"id": 9, "name": "Diagonal Down", "coords": [(0, 0), (1, 1), (2, 2), (3, 3), (3, 4)], "color": "🟤"},
    {"id": 10, "name": "Diagonal Up", "coords": [(3, 0), (2, 1), (1, 2), (0, 3), (0, 4)], "color": "🔷"},
]

SLOTS_REEL_WEIGHTS = {
    0: {
        "lemon": 30, "slot_machine": 25, "cherry": 15, "star": 10,
        "bell": 8, "seven": 6, "diamond": 4, "wild": 1, "scatter": 1,
    },
    1: {
        "lemon": 45, "slot_machine": 35, "cherry": 7, "star": 4,
        "bell": 3, "seven": 2, "diamond": 1, "wild": 0, "scatter": 1,
    },
    2: {
        "lemon": 55, "slot_machine": 45, "cherry": 4, "star": 2,
        "bell": 1, "seven": 0, "diamond": 0, "wild": 0, "scatter": 1,
    },
    3: {
        "lemon": 55, "slot_machine": 45, "cherry": 3, "star": 1,
        "bell": 1, "seven": 1, "diamond": 0, "wild": 0, "scatter": 1,
    },
    4: {
        "lemon": 60, "slot_machine": 45, "cherry": 1, "star": 1,
        "bell": 0, "seven": 0, "diamond": 0, "wild": 0, "scatter": 1,
    },
}


# ══════════════════════════════════════════════════════════════
#  Outcome Functions  (single source of truth for all games)
# ══════════════════════════════════════════════════════════════
# Pure functions: (server_seed, client_seed, nonce, ...) → deterministic result dict.
# No DB calls, no Discord objects — just math.

def outcome_gamble(server_seed: str, client_seed: str, nonce: int) -> dict:
    roll, n = fair_randbelow(server_seed, client_seed, nonce, 2)
    return {"won": roll == 1, "roll": roll, "nonce_end": n}


def outcome_supergamble(server_seed: str, client_seed: str, nonce: int) -> dict:
    win_roll, n = fair_randbelow(server_seed, client_seed, nonce, 100)
    bonus_roll, n = fair_randbelow(server_seed, client_seed, n, 100)
    return {
        "win_roll": win_roll,
        "bonus_roll": bonus_roll,
        "won": win_roll < SUPERGAMBLE_WIN_THRESHOLD,
        "mega": bonus_roll < SUPERGAMBLE_MEGA_THRESHOLD,
        "recovery_eligible": bonus_roll < SUPERGAMBLE_RECOVERY_THRESHOLD,
        "nonce_end": n,
    }


def outcome_dice(server_seed: str, client_seed: str, nonce: int) -> dict:
    d1, n = fair_randbelow(server_seed, client_seed, nonce, 6)
    d2, n = fair_randbelow(server_seed, client_seed, n, 6)
    d1 += 1
    d2 += 1
    return {"die1": d1, "die2": d2, "total": d1 + d2, "nonce_end": n}


def outcome_roulette(server_seed: str, client_seed: str, nonce: int) -> dict:
    result, n = fair_choice(server_seed, client_seed, nonce, ROULETTE_ALL_NUMBERS)
    is_int = isinstance(result, int)
    is_green = (result == 0) or (result == "00")
    is_red = is_int and result in ROULETTE_RED_NUMBERS
    is_black = is_int and result in ROULETTE_BLACK_NUMBERS
    color = "Green" if is_green else ("Red" if is_red else "Black")
    return {
        "spin_result": result,
        "color": color,
        "is_red": is_red,
        "is_black": is_black,
        "is_green": is_green,
        "is_even": is_int and result != 0 and (result % 2 == 0),
        "is_odd": is_int and (result % 2 == 1),
        "nonce_end": n,
    }


def outcome_crash(
    server_seed: str, client_seed: str, nonce: int, house_edge: float = 0.04
) -> dict:
    """Crash outcome with the same VIP boost as the live game.

    Bucket boundaries are fixed; the house-edge (vs. the 4% base) is applied
    as a multiplier boost *within* the chosen bucket, not as a scaling of
    the bucket thresholds. This keeps the verify path in sync with the live
    game's distribution.
    """
    r, n = fair_random(server_seed, client_seed, nonce)
    base_edge = 0.04
    for i, (threshold, lo, hi) in enumerate(CRASH_RANGES):
        if r < threshold or i == len(CRASH_RANGES) - 1:
            v, n = fair_uniform(server_seed, client_seed, n, lo, hi)
            if 0 < house_edge < base_edge:
                boost = (base_edge - house_edge) / base_edge
                v = v + (hi - v) * boost
            return {"crash_point": round(v, 2), "bucket": CRASH_BUCKET_NAMES[i], "nonce_end": n}


def outcome_double_round(server_seed: str, client_seed: str, nonce: int) -> dict:
    result, n = fair_choice(server_seed, client_seed, nonce, [True, False])
    return {"won": result, "nonce_end": n}


def outcome_ladder_step(
    server_seed: str, client_seed: str, nonce: int, step: int
) -> dict:
    roll, n = fair_randbelow(server_seed, client_seed, nonce, 10000)
    threshold = LADDER_STEP_PROBS.get(step, 0) * 100
    return {"roll": roll, "threshold": threshold, "survived": roll < threshold, "nonce_end": n}


def outcome_hilo_initial(server_seed: str, client_seed: str, nonce: int) -> dict:
    card, n = fair_choice(server_seed, client_seed, nonce, HILO_CARDS[1:-1])
    return {"card": card, "card_index": HILO_CARD_VALUES[card], "nonce_end": n}


def outcome_hilo_draw(
    server_seed: str, client_seed: str, nonce: int, current_card: str = None
) -> dict:
    """Draw the next HiLo card, excluding ``current_card`` from the deck.

    Excluding the current card is what the multiplier math assumes (12
    remaining cards out of which ``favorable`` make the guess correct). If
    ``current_card`` is None we fall back to drawing from the full 13-card
    deck (legacy behaviour).
    """
    if current_card is None or current_card not in HILO_CARDS:
        candidates = HILO_CARDS
    else:
        candidates = [c for c in HILO_CARDS if c != current_card]
    card, n = fair_choice(server_seed, client_seed, nonce, candidates)
    return {"card": card, "card_index": HILO_CARD_VALUES[card], "nonce_end": n}


def outcome_shuffle_deck(
    server_seed: str, client_seed: str, start_nonce: int
) -> Tuple[List[str], int]:
    suits = ["♥", "♦", "♣", "♠"]
    ranks = [str(n) for n in range(2, 11)] + ["J", "Q", "K", "A"]
    deck = [f"{r}{s}" for s in suits for r in ranks]
    next_nonce = fair_shuffle(server_seed, client_seed, start_nonce, deck)
    return deck, next_nonce


def outcome_blackjack(server_seed: str, client_seed: str, start_nonce: int) -> dict:
    deck, n = outcome_shuffle_deck(server_seed, client_seed, start_nonce)
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop()]
    return {"deck": deck, "player": player, "dealer": dealer, "nonce_end": n}


def outcome_poker(server_seed: str, client_seed: str, start_nonce: int) -> dict:
    deck, n = outcome_shuffle_deck(server_seed, client_seed, start_nonce)
    player = [deck.pop(), deck.pop()]
    bot = [deck.pop(), deck.pop()]
    community = [deck.pop() for _ in range(5)]
    return {"deck": deck, "player": player, "bot": bot, "community": community, "nonce_end": n}


def outcome_mines(
    server_seed: str, client_seed: str, start_nonce: int,
    board_size: int = 25, num_bombs: int = 3,
) -> dict:
    positions, n = fair_sample(server_seed, client_seed, start_nonce, list(range(board_size)), num_bombs)
    return {"bomb_positions": sorted(positions), "nonce_end": n}


def outcome_keno(
    server_seed: str, client_seed: str, start_nonce: int,
    num_buttons: int = 30, num_picks: int = 8,
) -> dict:
    positions, n = fair_sample(server_seed, client_seed, start_nonce, list(range(num_buttons)), num_picks)
    return {"winning_positions": sorted(positions), "nonce_end": n}


def outcome_slots_grid(
    server_seed: str, client_seed: str, start_nonce: int,
    reel_weights: dict = None,
) -> dict:
    if reel_weights is None:
        reel_weights = SLOTS_REEL_WEIGHTS
    grid = []
    nonce = start_nonce
    for reel_idx in range(5):
        weights = reel_weights[reel_idx]
        symbols = list(weights.keys())
        weight_values = list(weights.values())
        total_weight = sum(weight_values)
        cumulative = []
        running = 0
        for w in weight_values:
            running += w
            cumulative.append(running)
        reel_symbols = []
        for _row in range(4):
            pos, nonce = fair_randbelow(server_seed, client_seed, nonce, total_weight)
            selected = symbols[0]
            for i, bound in enumerate(cumulative):
                if pos < bound:
                    selected = symbols[i]
                    break
            reel_symbols.append(selected)
        grid.append(reel_symbols)
    return {"grid": grid, "nonce_end": nonce}


# ══════════════════════════════════════════════════════════════
#  Evaluation Helpers  (pure, no RNG)
# ══════════════════════════════════════════════════════════════

def evaluate_slots(
    grid: List[List[str]],
    symbols: dict = None,
    paylines: list = None,
) -> dict:
    if symbols is None:
        symbols = SLOTS_SYMBOLS
    if paylines is None:
        paylines = SLOTS_PAYLINES
    wins = []
    for payline in paylines:
        coords = payline["coords"]
        symbols_on_line = []
        for row, col in coords:
            if col < len(grid) and row < len(grid[col]):
                symbols_on_line.append(grid[col][row])
        if len(symbols_on_line) != 5:
            continue
        first_symbol = None
        match_count = 0
        for sym in symbols_on_line:
            if sym == "scatter":
                break
            if first_symbol is None:
                if sym != "wild":
                    first_symbol = sym
                    match_count = 1
            elif sym == first_symbol or sym == "wild":
                match_count += 1
            else:
                break
        if match_count >= 4 and first_symbol:
            symbol_data = symbols.get(first_symbol, {})
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
                })
    scatter_count = sum(1 for col in grid for sym in col if sym == "scatter")
    scatter_data = symbols.get("scatter", {})
    scatter_payouts = scatter_data.get("payouts", {})
    scatter_payout = scatter_payouts.get(scatter_count, 0)
    total_payout = sum(w["payout"] for w in wins) + scatter_payout
    return {
        "wins": wins,
        "scatter_count": scatter_count,
        "scatter_payout": scatter_payout,
        "total_payout": total_payout,
    }


# ══════════════════════════════════════════════════════════════
#  ProvenFairness Class  (verification wrappers)
# ══════════════════════════════════════════════════════════════

class ProvenFairness:
    """Verification wrappers that delegate to the canonical outcome functions."""

    class TAGS:
        RANDBELOW = "randbelow"
        RANDOM = "random"

        @staticmethod
        def SHUFFLE(i: int) -> str:
            return f"shuffle:{i}"

    # ── legacy helpers (kept for any external callers) ──

    @staticmethod
    def _randbelow(
        server_seed: str, client_seed: str, nonce: int, upper: int, tag: str
    ) -> int:
        result, _ = fair_randbelow(server_seed, client_seed, nonce, upper, tag=tag)
        return result

    @staticmethod
    def _random01(server_seed: str, client_seed: str, nonce: int, tag: str) -> float:
        u64 = _u64_from_hmac(server_seed, client_seed, nonce, tag)
        return u64 / float(U64_RANGE)

    # ── game verifiers ──

    @staticmethod
    def verify_gamble(server_seed: str, client_seed: str, nonce: int) -> bool:
        return outcome_gamble(server_seed, client_seed, nonce)["won"]

    @staticmethod
    def verify_supergamble(server_seed: str, client_seed: str, nonce: int) -> dict:
        r = outcome_supergamble(server_seed, client_seed, nonce)
        return {
            "win_roll": r["win_roll"],
            "bonus_roll": r["bonus_roll"],
            "win": r["won"],
            "mega_win": r["mega"],
        }

    @staticmethod
    def verify_dice(server_seed: str, client_seed: str, nonce: int) -> Tuple[int, int]:
        r = outcome_dice(server_seed, client_seed, nonce)
        return r["die1"], r["die2"]

    @staticmethod
    def verify_ladder(
        server_seed: str, client_seed: str, nonce: int, step: int
    ) -> Tuple[int, int]:
        r = outcome_ladder_step(server_seed, client_seed, nonce, step)
        return r["roll"], r["threshold"]

    @staticmethod
    def verify_slots(
        server_seed: str,
        client_seed: str,
        start_nonce: int,
        reel_weights: dict = None,
        symbols_data: dict = None,
        paylines: list = None,
    ) -> Dict[str, any]:
        grid_result = outcome_slots_grid(server_seed, client_seed, start_nonce, reel_weights)
        eval_result = evaluate_slots(grid_result["grid"], symbols=symbols_data, paylines=paylines)
        return {
            "grid": grid_result["grid"],
            "wins": eval_result["wins"],
            "scatter_count": eval_result["scatter_count"],
            "scatter_payout": eval_result["scatter_payout"],
            "total_payout": eval_result["total_payout"],
            "nonce_end": grid_result["nonce_end"],
        }

    @staticmethod
    def _shuffle_deck(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Tuple[List[str], int]:
        return outcome_shuffle_deck(server_seed, client_seed, start_nonce)

    @staticmethod
    def verify_blackjack(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        r = outcome_blackjack(server_seed, client_seed, start_nonce)
        return {
            "shuffled_deck": r["deck"],
            "player_cards": r["player"],
            "dealer_cards": r["dealer"],
            "next_nonce": r["nonce_end"],
        }

    @staticmethod
    def verify_roulette(server_seed: str, client_seed: str, nonce: int) -> dict:
        r = outcome_roulette(server_seed, client_seed, nonce)
        return {
            "spin_result": r["spin_result"],
            "color": r["color"],
            "is_red": r["is_red"],
            "is_black": r["is_black"],
            "is_green": r["is_green"],
            "is_even": r["is_even"],
            "is_odd": r["is_odd"],
        }

    @staticmethod
    def verify_poker(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        r = outcome_poker(server_seed, client_seed, start_nonce)
        return {
            "player_hand": r["player"],
            "bot_hand": r["bot"],
            "community": r["community"],
            "remaining_deck": r["deck"],
            "next_nonce": r["nonce_end"],
        }

    @staticmethod
    def verify_ridebus(
        server_seed: str, client_seed: str, start_nonce: int
    ) -> Dict[str, List[str]]:
        deck, next_nonce = outcome_shuffle_deck(server_seed, client_seed, start_nonce)
        return {"shuffled_deck": deck, "next_nonce": next_nonce}

    @staticmethod
    def verify_double(server_seed: str, client_seed: str, nonce: int) -> bool:
        return outcome_double_round(server_seed, client_seed, nonce)["won"]

    @staticmethod
    def verify_mines(
        server_seed: str, client_seed: str, start_nonce: int,
        board_size: int = 25, bombs: int = 3,
    ) -> Dict[str, Any]:
        r = outcome_mines(server_seed, client_seed, start_nonce, board_size, bombs)
        return {"bomb_cells": r["bomb_positions"], "next_nonce": r["nonce_end"]}

    @staticmethod
    def verify_crash(
        server_seed: str, client_seed: str, nonce: int, house_edge: float = 0.04
    ) -> float:
        return outcome_crash(server_seed, client_seed, nonce, house_edge)["crash_point"]

    @staticmethod
    def verify_hilo_initial(server_seed: str, client_seed: str, nonce: int) -> dict:
        return outcome_hilo_initial(server_seed, client_seed, nonce)

    @staticmethod
    def verify_hilo_draw(
        server_seed: str, client_seed: str, nonce: int, current_card: str = None
    ) -> dict:
        return outcome_hilo_draw(server_seed, client_seed, nonce, current_card)

    @staticmethod
    def verify_keno(
        server_seed: str, client_seed: str, start_nonce: int,
        num_buttons: int = 30, num_picks: int = 8,
    ) -> dict:
        return outcome_keno(server_seed, client_seed, start_nonce, num_buttons, num_picks)
