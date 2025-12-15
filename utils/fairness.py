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
        threshold = float(probabilities.get(step, 0))
        return roll_pct, threshold

    @staticmethod
    def verify_slots(server_seed: str, client_seed: str, start_nonce: int) -> List[str]:
        """
        Six symbol picks; each pick uses randbelow(total) with default tag.
        Weights/ordering unchanged from your earlier verifier.
        """
        symbols = [":cherries:", ":lemon:", ":seven:", ":bell:", ":beers:", ":gem:"]
        weights = [4.61, 3.81, 3.03, 2.22, 1.44, 1.08]
        scaled = [int(w * 100) for w in weights]
        total = sum(scaled)
        cum = []
        run = 0
        for w in scaled:
            run += w
            cum.append(run)

        out = []
        nonce = start_nonce
        for _ in range(6):
            r = ProvenFairness._randbelow(
                server_seed, client_seed, nonce, total, ProvenFairness.TAGS.RANDBELOW
            )
            # Map cumulative bucket
            for i, bound in enumerate(cum):
                if r < bound:
                    out.append(symbols[i])
                    break
            nonce += 1
        return out

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
