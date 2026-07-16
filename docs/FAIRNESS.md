# Provable Fairness

This document describes how TPNEBOT proves that casino game outcomes are deterministic and tamper-evident.

## Goal

Provide deterministic, verifiable game outcomes to users and auditors. Every game result is derived from:

- A **server seed** that FairGate commits to before the bet (via its SHA-256 hash).
- A **client seed** supplied by the player.
- A **nonce** that increments per draw so every HMAC message is unique.
- A per-operation **tag** that namespaces different draw types.

Once the server seed is revealed, anyone can reproduce the exact result independently.

## Algorithm

TPNEBOT targets FairGate's **`sha256_tag`** algorithm. Each draw computes:

```text
HMAC_SHA256(key=server_seed, message=f"{client_seed}:{nonce}:{tag}")
```

The first 8 bytes of the digest produce a 64-bit unsigned integer. Rejection sampling removes bias for modulo operations:

```text
limit = 2^64 - (2^64 % n)
if u64 >= limit:
    rehash and retry
return u64 % n
```

Tags vary by draw type: `randbelow`, `random`, `choice`, `shuffle:{i}`.

## Bot verification commands

### `!casino verify`

Look up a recorded game and recompute its outcome against FairGate's public `/fairness/verify` endpoint.

```text
!casino verify <game> <nonce> [@user] [step]
```

Examples:

```text
!casino verify mines 42
!casino verify mines 42 @Alice
!casino verify ladder 7 5
```

The command reads the stored `GameHistory` row, waits for the server seed to be revealed, then calls `/fairness/verify`. The embed shows:

- Server seed hash and client seed.
- Algorithm (`sha256_tag`).
- Top-level `win`, `payout_multiplier`, and `raw_float` from the endpoint.
- The game-specific `result` (mines board, roulette pocket, card shuffle, etc.).
- A "Reproduce" footer with the exact URL that was called.

Records from the old per-wallet local fairness system cannot be verified this way; the bot will tell you that the local system is deprecated.

### `!casino verifyraw`

Manually call `/fairness/verify` with arbitrary inputs. This is useful for auditors or for verifying a game that was not recorded in the bot's history.

```text
!casino verifyraw <server_seed> <server_seed_hash> <client_seed> <nonce> <game> [params_json] [algorithm]
```

Example:

```text
!casino verifyraw a1b2c3 d4e5f6 my-seed 7 mines '{"mines":3}'
```

Inputs are validated (hex seeds, non-negative nonce, valid JSON params) before the endpoint is called.

### `!casino verifyurl`

Same inputs as `verifyraw`, but returns only the constructed `/fairness/verify` URL without calling the endpoint.

### `!casino seed` and `!casino setseed`

- `!casino seed` shows your current client seed, wallet nonce, and the active FairGate server seed hash.
- `!casino setseed [seed]` lets you change your client seed. If no seed is supplied, a random one is generated.

## Endpoint reference

The public endpoint is:

```text
GET /fairness/verify?server_seed=...&server_seed_hash=...&client_seed=...&nonce=...&game=...&params=...&algorithm=...
```

Required query parameters: `server_seed`, `server_seed_hash`, `client_seed`, `nonce`, `game`.
Optional: `params` (JSON object, default `{}`), `algorithm` (default `sha512` on the server, but TPNEBOT always passes `sha256_tag`).

The endpoint first verifies that `SHA-256(server_seed) == server_seed_hash`, then recomputes the outcome using the same tagged HMAC-SHA256 RNG used during play.

Response shape:

```json
{
  "game": "ladder",
  "result": { "step": 0, "roll": 1234, "threshold": 8300, "survived": true, "multiplier": 1.15 },
  "win": true,
  "payout_multiplier": 1.15,
  "raw_float": 0.1234
}
```

The `result` field is game-specific; `win`, `payout_multiplier`, and `raw_float` are computed by the game engine.

## Legacy local verifier

`utils/fairness.py` still contains the old `ProvenFairness` class. It is kept as a reference implementation for pre-FairGate seeds but is **deprecated** for new games. Use `!casino verify` and `/fairness/verify` for all current records.

## Security considerations

- The server seed hash is published before the bet; the raw seed is only revealed later.
- The client seed lets the player contribute entropy.
- The nonce guarantees the same seed + client_seed pair cannot produce identical messages for different draws.
- Client seed changes must not reset the nonce unexpectedly; doing so could open a partial prediction window.

## Exact params storage

For every FairGate-backed game, the bot now stores `state.fairgate_verify_params` inside the `GameSession` row at creation time. This dict contains the exact `game` engine name and `params` that were passed to `/play`, keyed by the wallet nonce recorded in `rng.nonce`.

`!casino verify` reads these stored params through `fetch_fairgate_verify_params`. If a session predates this storage (e.g., created before this Phase 2 change), the command falls back to the heuristic param reconstruction used earlier and adds a best-effort note.

Games that still require best-effort handling:

- **Crash** — one shared session per channel, but each player has a separate draw with a per-user house edge. The base numbers params and per-player metadata are stored; the exact house edge at play time may still need to be supplied manually for a fully independent recompute.
- **Slots** — one FairGate draw per reel; only the first reel's parameters are verified with the recorded nonce.
- **Ladder** — the recorded nonce corresponds to the final step; the step number is resolved from the session's `final_state.step`.

## Extending fairness

When adding a new FairGate-backed game:

1. Define the exact `game` engine name and `params` used by `/play`.
2. Add the mapping to `Casino._fairgate_verify_params`.
3. Pass `state={"fairgate_verify_params": self._fairgate_verify_params(game_key, ...), ...}` when creating the game session.
4. Add a formatter in `Casino._format_verify_outcome` so the embed presents the result clearly.
5. For dynamic params (e.g., ladder step, crash house edge), store the additional metadata in the session and add a lookup helper in `database.managers.casino.CasinoMixin`.

## Common pitfalls

- Modulo without rejection sampling introduces bias for non-power-of-two ranges.
- Reusing a nonce for multiple distinct draws with the same tag risks correlated outputs.
- Logging or revealing the raw server seed before rotation compromises fairness integrity.
- Forgetting to pass `algorithm=sha256_tag` to `/fairness/verify` will produce a different deterministic outcome.

End of fairness documentation.
