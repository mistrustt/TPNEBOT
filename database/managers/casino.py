from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, case, literal_column
from sqlalchemy import func
from typing import Any, List, Optional
import hashlib
import secrets
from ..models import (
    Wallet,
    ActiveEffect,
    GameHistory,
    GameSession,
    GameSessionEvent,
    MinesSettings,
    VIPTier,
    UserVIP,
    RakebackBalance,
    RakebackTransaction,
)
import discord
import uuid
import logging
from decimal import Decimal
from utils.amount import AmountUtils

logger = logging.getLogger("discord.client")


class CasinoMixin(BaseManager):
    async def create_game_session(
        self,
        game_name: str,
        *,
        guild_id: int | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        owner_id: int | None = None,
        participants: list[int] | None = None,
        wager_total: Decimal | None = None,
        state: dict | None = None,
        rng: dict | None = None,
        errors: list | None = None,
    ) -> uuid.UUID:
        if owner_id is not None:
            await self.ensure_user_identity(owner_id)
            owner_id = self.hash_user_id(owner_id)
        if participants is not None:
            for uid in participants:
                await self.ensure_user_identity(uid)
            participants = [self.hash_user_id(uid) for uid in participants]
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = GameSession(
                    game_name=game_name,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    participants=participants,
                    wager_total=wager_total,
                    state=state,
                    rng=rng,
                    errors=errors,
                )
                session.add(gs)
            return gs.id

    async def get_game_session(self, session_id: uuid.UUID) -> GameSession | None:
        async with self.async_sessionmaker() as session:
            return await session.get(GameSession, session_id)

    async def list_game_sessions(
        self,
        *,
        game_name: str | None = None,
        guild_id: int | None = None,
        owner_id: int | None = None,
        limit: int = 50,
    ) -> list[GameSession]:
        if owner_id is not None:
            owner_id = self.hash_user_id(owner_id)
        async with self.async_sessionmaker() as session:
            stmt = select(GameSession).order_by(GameSession.created_at.desc())
            if game_name:
                stmt = stmt.where(GameSession.game_name == game_name)
            if guild_id:
                stmt = stmt.where(GameSession.guild_id == guild_id)
            if owner_id:
                stmt = stmt.where(GameSession.owner_id == owner_id)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_game_session(
        self,
        session_id: uuid.UUID,
        *,
        status: str | None = None,
        message_id: int | None = None,
        participants: list[int] | None = None,
        wager_total: Decimal | None = None,
        state: dict | None = None,
        rng: dict | None = None,
        errors: list | None = None,
    ) -> bool:
        if participants is not None:
            participants = [self.hash_user_id(uid) for uid in participants]
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                if status is not None:
                    gs.status = status
                if message_id is not None:
                    gs.message_id = message_id
                if participants is not None:
                    gs.participants = participants
                if wager_total is not None:
                    gs.wager_total = wager_total
                if state is not None:
                    current = gs.state or {}
                    current.update(state)
                    gs.state = current
                if rng is not None:
                    current = gs.rng or {}
                    current.update(rng)
                    gs.rng = current
                if errors is not None:
                    current = list(gs.errors or [])
                    current.extend(errors)
                    gs.errors = current
            return True

    async def add_game_session_event(
        self, session_id: uuid.UUID, event_type: str, payload: dict | None = None
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameSessionEvent(
                        session_id=session_id,
                        event_type=event_type,
                        payload=payload or {},
                    )
                )

    async def get_game_session_events(
        self, session_id: uuid.UUID, limit: int = 50
    ) -> list[GameSessionEvent]:
        async with self.async_sessionmaker() as session:
            stmt = (
                select(GameSessionEvent)
                .where(GameSessionEvent.session_id == session_id)
                .order_by(GameSessionEvent.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add_game_session_refund(
        self,
        session_id: uuid.UUID,
        *,
        user_id: int,
        wallet_id: str,
        amount: str,
        reason: str | None = None,
    ) -> bool:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                state = gs.state or {}
                refunds = list(state.get("refunds", []))
                refunds.append(
                    {
                        "user_id": user_id,
                        "wallet_id": wallet_id,
                        "amount": amount,
                        "reason": reason or "refund",
                    }
                )
                state["refunds"] = refunds
                gs.state = state
            return True

    async def remove_game_session_refund(
        self, session_id: uuid.UUID, *, user_id: int
    ) -> bool:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                state = gs.state or {}
                refunds = [
                    r for r in state.get("refunds", []) if r.get("user_id") != user_id
                ]
                state["refunds"] = refunds
                gs.state = state
            return True

    async def end_game_session(
        self,
        session_id: uuid.UUID,
        *,
        outcome: str | None = None,
        reason: str | None = None,
        final_state: dict | None = None,
    ) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                payload = {
                    "outcome": outcome,
                    "reason": reason,
                    "final_state": final_state or gs.state or {},
                }
                session.add(
                    GameSessionEvent(
                        session_id=session_id, event_type="ended", payload=payload
                    )
                )
                await session.delete(gs)
            return True

    async def get_client_seed(self, user_id: int) -> tuple[str, int]:
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(raw_user_id)
            if not wallet.client_seed:
                wallet.client_seed = secrets.token_hex(16)
                wallet.nonce = 0
                session.add(wallet)
                await session.commit()
            return wallet.client_seed, wallet.nonce

    async def get_server_seed(self, user_id: int) -> str:
        """
        Return this user's server_seed, generating one if missing.
        """
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(raw_user_id)
            if not wallet.server_seed:
                # first‐time generation
                wallet.server_seed = secrets.token_hex(16)
                wallet.previous_server_seed = None
                wallet.seed_rotated_at = discord.utils.utcnow()
                await session.commit()
            return wallet.server_seed

    async def set_client_seed(self, user_id: int, seed: str) -> None:
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        await self.get_wallet_by_user_id(raw_user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                wallet = result.scalar_one()
                wallet.client_seed = seed

    async def reveal_and_rotate(self, user_id: int) -> tuple[Optional[str], str]:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                return await self._reveal_and_rotate_in_tx(session, user_id)

    async def _reveal_and_rotate_in_tx(
        self, session, user_id: int
    ) -> tuple[Optional[str], str]:
        # Lock wallet row
        result = await session.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )
        w = result.scalar_one()

        old_seed = w.server_seed
        if not old_seed:
            # First-time init; no reveal yet
            w.server_seed = secrets.token_hex(32)
            w.seed_rotated_at = discord.utils.utcnow()
            new_hash = hashlib.sha256(w.server_seed.encode()).hexdigest()
            return None, new_hash

        old_hash = hashlib.sha256(old_seed.encode()).hexdigest()

        # Generate new seed (256-bit recommended)
        new_seed = secrets.token_hex(32)
        w.previous_server_seed = old_seed
        w.server_seed = new_seed
        w.seed_rotated_at = discord.utils.utcnow()

        # Back-fill revealed seed into history tied to old_hash
        await session.execute(
            update(GameHistory)
            .where(GameHistory.user_id == user_id)
            .where(GameHistory.hash == old_hash)  # consider renaming this column later
            .where(GameHistory.used_server_seed.is_(None))
            .values(used_server_seed=old_seed)
        )

        new_hash = hashlib.sha256(new_seed.encode()).hexdigest()
        return old_seed, new_hash

    async def bump_and_get(self, user_id: int) -> tuple[str, str, int]:
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                w = result.scalar_one_or_none()
                if w is None:
                    # create on demand if that’s your policy:
                    await session.rollback()
                    await self.create_wallet(raw_user_id)
                    async with session.begin():
                        result = await session.execute(
                            select(Wallet)
                            .where(Wallet.user_id == user_id)
                            .with_for_update()
                        )
                        w = result.scalar_one()

                if not getattr(w, "server_seed", None):
                    w.server_seed = secrets.token_hex(32)  # 256-bit seed
                    w.previous_server_seed = None
                    w.seed_rotated_at = discord.utils.utcnow()

                if not getattr(w, "client_seed", None):
                    w.client_seed = secrets.token_hex(16)
                    w.nonce = 0

                nonce_before = w.nonce or 0
                w.nonce = nonce_before + 1
                return w.server_seed, w.client_seed, nonce_before

    async def get_previous_server_seed(self, user_id: int) -> Optional[str]:
        """
        Return this user's previous_server_seed, or None if unset.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one_or_none()
            if not wallet:
                return None
            return wallet.previous_server_seed

    async def increment_nonce(self, user_id: int) -> int:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                wallet = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                wallet = wallet.scalar_one()  # must exist
                wallet.nonce = (wallet.nonce or 0) + 1  # bump in-place
                new_nonce = wallet.nonce  # keep to return
            # transaction auto-commits here
        return new_nonce

    async def set_mines_multi(self, data: list):
        """
        Insert a list of bomb_count, gem_count, and multiplier values into the database.
        :param session: AsyncSession
        :param data: List of tuples (bomb_count, gem_count, multiplier)
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                for bomb, gem, multiplier in data:
                    record = MinesSettings(
                        bomb_count=bomb, gem_count=gem, multiplier=multiplier
                    )
                    session.add(record)
                await session.commit()

    async def get_mines_multiplier(self, bomb_count: int, gem_count: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(MinesSettings.multiplier)
                .where(MinesSettings.bomb_count == bomb_count)
                .where(MinesSettings.gem_count == gem_count)
            )
            return result.scalar_one_or_none()

    async def record_game(
        self,
        user_id: int,
        game_name: str,
        outcome: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
        *,
        payout_multiplier=None,
        payout_amount=None,
    ) -> tuple[str, str]:
        """Insert a game history entry with the given outcome and rotate the seed.

        ``outcome`` is one of ``"win"``, ``"loss"``, or ``"push"``. Pushes (ties
        in poker/blackjack) are recorded so the row exists for audit and
        wager-volume metrics, but they are intentionally excluded from win
        and loss counts by the aggregate queries elsewhere in this module
        (which all filter on ``outcome == "win"|"loss"``).

        ``seed_used`` should be the raw server seed that was live for this
        game (i.e. the value returned by ``start_game_proof`` in the cog).
        Writing it at insert time means the most-recent game is verifiable
        immediately, without waiting for the next game's rotation to
        back-fill via ``_reveal_and_rotate_in_tx``.

        ``payout_multiplier`` and ``payout_amount`` are optional. When supplied,
        they describe the actual payout returned to the player for this round.
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameHistory(
                        user_id=user_id,
                        game_name=game_name,
                        outcome=outcome,
                        wagered=bet,
                        payout_multiplier=payout_multiplier,
                        payout_amount=payout_amount,
                        client_seed=client_seed,
                        used_server_seed=seed_used,
                        nonce=nonce,
                        hash=hash_hex,
                    )
                )
                revealed_seed, new_hash = await self._reveal_and_rotate_in_tx(
                    session, user_id
                )
                return revealed_seed, new_hash

    async def bump_fairgate_nonce(self, user_id: int) -> int:
        """Return the current wallet nonce and increment it for the next draw.

        FairGate caller-supplied nonces reuse the wallet nonce column so the
        bot does not need a second counter. We delegate to ``bump_and_get``
        and discard the local server seed it creates.
        """
        _server_seed, _client_seed, nonce_before = await self.bump_and_get(user_id)
        return nonce_before

    async def record_fairgate_game(
        self,
        user_id: int,
        game_name: str,
        outcome: str,
        bet,
        client_seed: str,
        nonce: int,
        hash_hex: str,
        *,
        payout_multiplier=None,
        payout_amount=None,
    ) -> None:
        """Insert a FairGate-resolved game history entry.

        The raw server seed is not known until FairGate rotates/reveals it, so
        ``used_server_seed`` is left NULL and must be back-filled later via
        ``backfill_fairgate_seed``.

        ``payout_multiplier`` and ``payout_amount`` are optional. When supplied,
        they describe the actual payout returned to the player for this round.
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameHistory(
                        user_id=user_id,
                        game_name=game_name,
                        outcome=outcome,
                        wagered=bet,
                        payout_multiplier=payout_multiplier,
                        payout_amount=payout_amount,
                        client_seed=client_seed,
                        used_server_seed=None,
                        nonce=nonce,
                        hash=hash_hex,
                        provider="fairgate",
                    )
                )
                # Retain the small karma reward for wins.
                if outcome == "win":
                    try:
                        await self.add_reputation_score(user_id, 1)
                    except Exception:
                        pass

    async def backfill_fairgate_seed(self, hash_hex: str, revealed_seed: str) -> int:
        """Back-fill the revealed server seed for FairGate rows matching ``hash_hex``.

        Returns the number of rows updated.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GameHistory)
                    .where(GameHistory.provider == "fairgate")
                    .where(GameHistory.hash == hash_hex)
                    .where(GameHistory.used_server_seed.is_(None))
                    .values(used_server_seed=revealed_seed)
                )
                return result.rowcount

    async def increment_win(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
        *,
        payout_multiplier=None,
        payout_amount=None,
    ) -> tuple[str, str]:
        """Insert a win entry into game history."""
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        result = await self.record_game(
            user_id=raw_user_id,
            game_name=game_name,
            outcome="win",
            bet=bet,
            client_seed=client_seed,
            seed_used=seed_used,
            nonce=nonce,
            hash_hex=hash_hex,
            payout_multiplier=payout_multiplier,
            payout_amount=payout_amount,
        )
        # Award a small player-earned karma point for winning (capped by daily karma system).
        try:
            await self.add_reputation_score(raw_user_id, 1)
        except Exception:
            pass
        return result

    async def increment_loss(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
        *,
        payout_multiplier=None,
        payout_amount=None,
    ) -> tuple[str, str]:
        """Insert a loss entry into game history."""
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        return await self.record_game(
            user_id=raw_user_id,
            game_name=game_name,
            outcome="loss",
            bet=bet,
            client_seed=client_seed,
            seed_used=seed_used,
            nonce=nonce,
            hash_hex=hash_hex,
            payout_multiplier=payout_multiplier,
            payout_amount=payout_amount,
        )

    async def get_total_wins(self, user_id: int) -> int:
        """Count the total wins for a user based on game history."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.user_id == user_id, GameHistory.outcome == "win")
            )
            return result.scalar_one()

    async def get_global_wins(self) -> int:
        """Count total wins across all users and games."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.outcome == "win")
            )
            return result.scalar_one()

    async def get_total_losses(self, user_id: int) -> int:
        """Count the total losses for a user based on game history."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.user_id == user_id, GameHistory.outcome == "loss")
            )
            return result.scalar_one()

    async def get_global_losses(self) -> int:
        """Count total losses across all users and games."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(GameHistory)
                .where(GameHistory.outcome == "loss")
            )
            return result.scalar_one()

    async def get_user_game_history(self, user_id: int, limit: int = 10) -> list:
        """Retrieve the game history for a specific user."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory)
                .where(GameHistory.user_id == user_id)
                .order_by(GameHistory.created_at.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def get_top_game_winners(self, game_name: str, limit: int = 10) -> list:
        """Retrieve the top users with the most wins by game."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory.user_id, func.count().label("win_count"))
                .where(GameHistory.game_name == game_name, GameHistory.outcome == "win")
                .group_by(GameHistory.user_id)
                .order_by(func.count().desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [row.user_id for row in rows]
        resolved = await self.resolve_user_hashes(hashes)
        return [(resolved.get(row.user_id, row.user_id), row.win_count) for row in rows]

    async def get_top_game_losers(self, game_name: str, limit: int = 10) -> list:
        """Retrieve the top users with the most losses by game."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory.user_id, func.count().label("loss_count"))
                .where(
                    GameHistory.game_name == game_name, GameHistory.outcome == "loss"
                )
                .group_by(GameHistory.user_id)
                .order_by(func.count().desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [row.user_id for row in rows]
        resolved = await self.resolve_user_hashes(hashes)
        return [
            (resolved.get(row.user_id, row.user_id), row.loss_count) for row in rows
        ]

    async def fetch_game_for_user(
        self, user_id: int, game_name: str, nonce: int
    ) -> GameHistory | None:
        """Fetch a specific game history entry for a user by game name and nonce."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameHistory).where(
                    GameHistory.user_id == user_id,
                    GameHistory.game_name == game_name,
                    GameHistory.nonce == nonce,
                )
            )
            return result.scalar_one_or_none()

    async def fetch_mines_bomb_count(
        self, user_id: int, nonce: int, *, lookback: int = 200
    ) -> int | None:
        """Return the ``bombs`` count used for a given mines game.

        Mines has a per-game parameter (number of bombs) that is not stored in
        ``GameHistory``. The session row that *does* record it (``state``
        JSON column, ``state->>'bombs'``) is created alongside the game, so
        we walk back through the user's most recent mines sessions and match
        on the ``nonce`` recorded in the ``rng`` JSON column.

        Returns ``None`` when no matching session is found (caller should
        fall back to a sensible default and surface the gap to the user).
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameSession)
                .where(
                    GameSession.owner_id == user_id,
                    GameSession.game_name == "mines",
                )
                .order_by(GameSession.created_at.desc())
                .limit(lookback)
            )
            sessions = result.scalars().all()

        for gs in sessions:
            rng = gs.rng or {}
            if rng.get("nonce") != nonce:
                continue
            state = gs.state or {}
            bombs = state.get("bombs")
            if bombs is None:
                continue
            try:
                return int(bombs)
            except (TypeError, ValueError):
                continue
        return None

    async def fetch_roulette_fairgate_params(
        self, user_id: int, nonce: int, *, lookback: int = 200
    ) -> dict[str, Any] | None:
        """Return the FairGate roulette params used for a given spin.

        The params (``wheel``, ``bet_type``, and optionally ``number``) are
        stored in the session ``state`` JSON column. We walk back through the
        user's most recent roulette sessions and match on the ``nonce`` recorded
        in the ``rng`` JSON column.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameSession)
                .where(
                    GameSession.owner_id == user_id,
                    GameSession.game_name == "roulette",
                )
                .order_by(GameSession.created_at.desc())
                .limit(lookback)
            )
            sessions = result.scalars().all()

        for gs in sessions:
            rng = gs.rng or {}
            if rng.get("nonce") != nonce:
                continue
            state = gs.state or {}
            verify_params = state.get("fairgate_verify_params")
            if isinstance(verify_params, dict):
                params = verify_params.get("params")
                if isinstance(params, dict):
                    return dict(params)
            fg_bet = state.get("fairgate_bet")
            if not isinstance(fg_bet, dict):
                continue
            return dict(fg_bet)
        return None

    async def fetch_fairgate_verify_params(
        self,
        user_id: int,
        game_name: str,
        nonce: int,
        *,
        lookback: int = 200,
    ) -> dict[str, Any] | None:
        """Return the exact FairGate verify params stored for a game session.

        The ``GameSession.state`` JSON column stores ``fairgate_verify_params``
        (a dict with ``game`` and ``params`` keys) at creation time. We match
        sessions by ``rng.nonce`` so the verify path can replay the exact call
        that was made to ``/play``.

        Returns ``None`` when no matching session is found or when the session
        predates this storage (caller should fall back to heuristics).
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameSession)
                .where(
                    GameSession.owner_id == user_id,
                    GameSession.game_name == game_name,
                )
                .order_by(GameSession.created_at.desc())
                .limit(lookback)
            )
            sessions = result.scalars().all()

        for gs in sessions:
            rng = gs.rng or {}
            if rng.get("nonce") != nonce:
                continue
            state = gs.state or {}
            verify_params = state.get("fairgate_verify_params")
            if isinstance(verify_params, dict):
                return dict(verify_params)
        return None

    async def fetch_ladder_final_step(
        self, user_id: int, nonce: int, *, lookback: int = 200
    ) -> int | None:
        """Return the final ladder step recorded for a session matching ``nonce``.

        The step is stored in ``final_state.step`` when the ladder session ends.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(GameSession)
                .where(
                    GameSession.owner_id == user_id,
                    GameSession.game_name == "ladder",
                )
                .order_by(GameSession.created_at.desc())
                .limit(lookback)
            )
            sessions = result.scalars().all()

        for gs in sessions:
            rng = gs.rng or {}
            if rng.get("nonce") != nonce:
                continue
            final_state = gs.state or {}
            # final_state may be nested under a 'final_state' key or flattened
            if "final_state" in final_state and isinstance(final_state["final_state"], dict):
                final_state = final_state["final_state"]
            step = final_state.get("step")
            if step is not None:
                try:
                    return int(step)
                except (TypeError, ValueError):
                    continue
        return None

    async def get_wager_stats(
        self, user_id: int, game_name: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Returns (total_wagered,
                 total_wagered_on_wins,
                 total_wagered_on_losses)
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            # make a 0 of the same NUMERIC type
            zero = literal_column("0", type_=GameHistory.wagered.type)

            win_case = case(
                (GameHistory.outcome == "win", GameHistory.wagered), else_=zero
            )
            loss_case = case(
                (GameHistory.outcome == "loss", GameHistory.wagered), else_=zero
            )

            totals = await session.execute(
                select(
                    func.coalesce(func.sum(GameHistory.wagered), zero).label("total"),
                    func.coalesce(func.sum(win_case), zero).label("win_total"),
                    func.coalesce(func.sum(loss_case), zero).label("loss_total"),
                ).where(
                    GameHistory.user_id == user_id, GameHistory.game_name == game_name
                )
            )
            total, win_total, loss_total = totals.one()
            return total, win_total, loss_total

    async def get_game_stats(self, user_id: int, game_name: str) -> tuple[int, int]:
        """Count wins and losses for a specific game and user."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(
                    func.sum(case((GameHistory.outcome == "win", 1), else_=0)).label(
                        "wins"
                    ),
                    func.sum(case((GameHistory.outcome == "loss", 1), else_=0)).label(
                        "losses"
                    ),
                ).where(
                    GameHistory.user_id == user_id, GameHistory.game_name == game_name
                )
            )
            wins, losses = result.first()
            return (wins or 0, losses or 0)

    async def ensure_default_vip_tiers(self) -> None:
        """Ensure default VIP tiers exist in the database."""
        default_tiers = [
            {
                "name": "Unranked",
                "level": 0,
                "min_wagered": Decimal("0"),
                "rakeback_rate": Decimal("0"),
                "rtp_bonus": Decimal("0"),
            },
            {
                "name": "Bronze",
                "level": 1,
                "min_wagered": Decimal("100000000000"),
                "rakeback_rate": Decimal("0.0100"),
                "rtp_bonus": Decimal("0"),
            },
            {
                "name": "Silver",
                "level": 2,
                "min_wagered": Decimal("100000000000000"),
                "rakeback_rate": Decimal("0.0200"),
                "rtp_bonus": Decimal("0.0050"),
            },
            {
                "name": "Gold",
                "level": 3,
                "min_wagered": Decimal("100000000000000000"),
                "rakeback_rate": Decimal("0.0300"),
                "rtp_bonus": Decimal("0.0100"),
            },
            {
                "name": "Platinum",
                "level": 4,
                "min_wagered": Decimal("100000000000000000000"),
                "rakeback_rate": Decimal("0.0500"),
                "rtp_bonus": Decimal("0.0150"),
            },
            {
                "name": "Diamond",
                "level": 5,
                "min_wagered": Decimal("100000000000000000000000"),
                "rakeback_rate": Decimal("0.1000"),
                "rtp_bonus": Decimal("0.0200"),
            },
        ]

        async with self.async_sessionmaker() as session:
            async with session.begin():
                for tier_data in default_tiers:
                    # Check both by level AND by name to catch any corruption
                    existing_by_level = await session.execute(
                        select(VIPTier).where(VIPTier.level == tier_data["level"])
                    )
                    existing_by_name = await session.execute(
                        select(VIPTier).where(VIPTier.name == tier_data["name"])
                    )

                    tier_by_level = existing_by_level.scalar_one_or_none()
                    tier_by_name = existing_by_name.scalar_one_or_none()

                    if not tier_by_level and not tier_by_name:
                        # Neither exists - create new tier
                        tier = VIPTier(**tier_data)
                        session.add(tier)
                    elif tier_by_level and not tier_by_name:
                        # Tier exists at this level but wrong name - update it
                        tier_by_level.name = tier_data["name"]
                        tier_by_level.min_wagered = tier_data["min_wagered"]
                        tier_by_level.rakeback_rate = tier_data["rakeback_rate"]
                        tier_by_level.rtp_bonus = tier_data["rtp_bonus"]
                    elif not tier_by_level and tier_by_name:
                        # Tier exists with this name but wrong level - this is a conflict
                        # The tier with correct name takes precedence, fix the level
                        tier_by_name.level = tier_data["level"]
                        tier_by_name.min_wagered = tier_data["min_wagered"]
                        tier_by_name.rakeback_rate = tier_data["rakeback_rate"]
                        tier_by_name.rtp_bonus = tier_data["rtp_bonus"]
                    # If both exist and are different, the level-based one is authoritative
                    # (tier_by_level exists and tier_by_name exists - they should be the same tier)
            await session.commit()

    async def upgrade_user_vip(self, user_id: int) -> UserVIP:
        """Upgrade user's VIP tier based on total wagered from GameHistory."""
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get or create user VIP record
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()
                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=1)
                    session.add(user_vip)
                # Calculate total wagered from GameHistory
                total_wagered = await self.get_total_wagered_all_games(raw_user_id)
                # Determine appropriate tier based on total wagered
                new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id
            await session.commit()
            await session.refresh(user_vip)
            return user_vip

    async def upgrade_all_users_vip(self) -> None:
        """Upgrade VIP tiers for all users based on their total wagered."""
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get all user VIP records
                result = await session.execute(select(UserVIP))
                user_vips = result.scalars().all()

                for user_vip in user_vips:
                    # Resolve stored hash to raw Discord ID for public helpers
                    raw_user_id = await self.resolve_user_hash(user_vip.user_id)
                    if raw_user_id is None:
                        continue
                    # Calculate total wagered for each user
                    total_wagered = await self.get_total_wagered_all_games(raw_user_id)
                    # Determine appropriate tier based on total wagered
                    new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                    if new_tier and new_tier.id != user_vip.tier_id:
                        user_vip.tier_id = new_tier.id

            await session.commit()

    async def get_user_vip(self, user_id: int) -> UserVIP:
        """Get or create user VIP record with tier info."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = result.scalar_one_or_none()

            if not user_vip:
                # Create new VIP record with default tier
                user_vip = UserVIP(user_id=user_id, tier_id=1)
                session.add(user_vip)
                await session.commit()
                await session.refresh(user_vip)

            return user_vip

    async def get_vip_tier(self, tier_id: int) -> Optional[VIPTier]:
        """Get a specific VIP tier by ID."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(VIPTier).where(VIPTier.id == tier_id))
            return result.scalar_one_or_none()

    async def get_all_vip_tiers(self) -> List[VIPTier]:
        """Get all VIP tiers ordered by level."""
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(VIPTier).order_by(VIPTier.level))
            return list(result.scalars().all())

    async def get_total_wagered_all_games(self, user_id: int) -> Decimal:
        """Calculate total wagered across all games from GameHistory."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(
                    func.coalesce(func.sum(GameHistory.wagered), Decimal("0"))
                ).where(GameHistory.user_id == user_id)
            )
            return result.scalar() or Decimal("0")

    async def get_vip_tier_by_wagered(self, total_wagered: Decimal) -> VIPTier:
        """Determine VIP tier based on total wagered amount."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(VIPTier)
                .where(VIPTier.min_wagered <= total_wagered)
                .order_by(VIPTier.level.desc())
                .limit(1)
            )
            tier = result.scalar_one_or_none()
            if not tier:
                # Return Bronze tier as default
                result = await session.execute(
                    select(VIPTier).where(VIPTier.level == 1)
                )
                tier = result.scalar_one_or_none()
            return tier

    async def get_vip_tier_by_user(self, user_id: int) -> VIPTier:
        """Determine VIP tier based on total wagered from GameHistory."""
        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        total_wagered = await self.get_total_wagered_all_games(raw_user_id)
        return await self.get_vip_tier_by_wagered(total_wagered)

    async def record_rakeback(
        self, user_id: int, wagered: Decimal, game_name: str
    ) -> Decimal:
        """
        Record rakeback after game. Returns rakeback amount.
        Does NOT update total_wagered (computed from GameHistory instead).
        """
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        # Defensive: on a fresh/empty database the vip_tiers table may not have
        # been seeded yet. Make sure the default tiers exist before we try to
        # create a UserVIP row that references tier_id=1.
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get or create user VIP record
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=1)
                    session.add(user_vip)

                # Get current tier and calculate rakeback
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()
                rakeback_rate = tier.rakeback_rate if tier else Decimal("0.01")
                rakeback_amount = AmountUtils.round_currency(wagered * rakeback_rate)

                # Add rakeback to user's accumulated balance
                rakeback_result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                rakeback_balance = rakeback_result.scalar_one_or_none()

                if not rakeback_balance:
                    rakeback_balance = RakebackBalance(
                        user_id=user_id, accumulated=rakeback_amount
                    )
                    session.add(rakeback_balance)
                else:
                    rakeback_balance.accumulated = (
                        rakeback_balance.accumulated or Decimal("0")
                    ) + rakeback_amount

                # Update total rakeback earned
                user_vip.total_rakeback_earned = (
                    user_vip.total_rakeback_earned or Decimal("0")
                ) + rakeback_amount

                # Create rakeback transaction record
                transaction = RakebackTransaction(
                    user_id=user_id,
                    game_name=game_name,
                    wagered_amount=wagered,
                    rakeback_rate=rakeback_rate,
                    rakeback_amount=rakeback_amount,
                    vip_tier_id=user_vip.tier_id,
                )
                session.add(transaction)

                # Check for tier upgrade based on GameHistory
                total_wagered = await self.get_total_wagered_all_games(raw_user_id)
                new_tier = await self.get_vip_tier_by_wagered(total_wagered + wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id

            await session.commit()
            return rakeback_amount

    async def update_user_wagered(
        self, user_id: int, amount: Decimal, game_name: str
    ) -> Decimal:
        """Alias for record_rakeback for backward compatibility."""
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        return await self.record_rakeback(raw_user_id, amount, game_name)

    async def recalculate_user_vip_tier(self, user_id: int) -> Optional[VIPTier]:
        """Recalculate and update user's VIP tier based on total wagered from GameHistory."""
        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    return None

                # Get total wagered from GameHistory
                total_wagered = await self.get_total_wagered_all_games(raw_user_id)
                new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id
                    await session.commit()
                    return new_tier

                current_tier = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                return current_tier.scalar_one_or_none()

    async def get_rakeback_balance(self, user_id: int) -> Decimal:
        """Get accumulated unclaimed rakeback."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(RakebackBalance).where(RakebackBalance.user_id == user_id)
            )
            balance = result.scalar_one_or_none()
            return balance.accumulated if balance else Decimal("0")

    async def add_rakeback(
        self,
        user_id: int,
        amount: Decimal,
        game_name: str,
        wagered: Decimal,
        rate: Decimal,
    ) -> None:
        """Add rakeback to user's accumulated balance."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = result.scalar_one_or_none()

                if not balance:
                    balance = RakebackBalance(user_id=user_id, accumulated=amount)
                    session.add(balance)
                else:
                    balance.accumulated = (balance.accumulated or Decimal("0")) + amount

                # Create transaction record
                transaction = RakebackTransaction(
                    user_id=user_id,
                    game_name=game_name,
                    wagered_amount=wagered,
                    rakeback_rate=rate,
                    rakeback_amount=amount,
                )
                session.add(transaction)

            await session.commit()

    async def claim_rakeback(self, user_id: int) -> Decimal:
        """Claim all accumulated rakeback. Returns amount claimed."""
        raw_user_id = user_id
        await self.ensure_user_identity(raw_user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = result.scalar_one_or_none()

                if not balance or balance.accumulated <= Decimal("0"):
                    return Decimal("0")

                claim_amount = balance.accumulated
                balance.accumulated = Decimal("0")
                balance.last_claim = discord.utils.utcnow()
                balance.total_claimed = (
                    balance.total_claimed or Decimal("0")
                ) + claim_amount

                # Credit to wallet
                wallet_id = await self.get_wallet_id_for_user(raw_user_id)
                await self.process_treasury_transaction(
                    wallet_id, claim_amount, "Rakeback Claim", "standard"
                )

            await session.commit()
            return claim_amount

    async def get_rakeback_history(
        self, user_id: int, limit: int = 50
    ) -> List[RakebackTransaction]:
        """Get rakeback transaction history for a user."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(RakebackTransaction)
                .where(RakebackTransaction.user_id == user_id)
                .order_by(RakebackTransaction.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_rakeback_info(self, user_id: int) -> dict:
        """Get complete rakeback information for a user."""
        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        # Get total wagered from GameHistory
        total_wagered = await self.get_total_wagered_all_games(raw_user_id)

        async with self.async_sessionmaker() as session:
            # Get VIP info
            vip_result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = vip_result.scalar_one_or_none()

            # Get tier
            tier = None
            if user_vip:
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()

            # Get rakeback balance
            balance_result = await session.execute(
                select(RakebackBalance).where(RakebackBalance.user_id == user_id)
            )
            balance = balance_result.scalar_one_or_none()

            # Get next tier
            next_tier = None
            if tier:
                next_result = await session.execute(
                    select(VIPTier).where(VIPTier.level == tier.level + 1)
                )
                next_tier = next_result.scalar_one_or_none()

            return {
                "total_wagered": total_wagered,
                "total_rakeback_earned": user_vip.total_rakeback_earned
                if user_vip
                else Decimal("0"),
                "accumulated": balance.accumulated if balance else Decimal("0"),
                "total_claimed": balance.total_claimed if balance else Decimal("0"),
                "last_claim": balance.last_claim if balance else None,
                "current_tier": tier,
                "next_tier": next_tier,
                "rakeback_rate": tier.rakeback_rate if tier else Decimal("0.01"),
            }

    async def get_effective_rtp(self, user_id: int) -> Decimal:
        """
        Calculate effective RTP adjustment from VIP tier and active RTP boosts.
        Returns percentage points (e.g., 1.5 = 1.5% RTP boost).
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            # Get VIP tier RTP bonus
            vip_result = await session.execute(
                select(UserVIP).where(UserVIP.user_id == user_id)
            )
            user_vip = vip_result.scalar_one_or_none()

            vip_rtp_bonus = Decimal("0")
            if user_vip:
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == user_vip.tier_id)
                )
                tier = tier_result.scalar_one_or_none()
                if tier:
                    vip_rtp_bonus = tier.rtp_bonus or Decimal("0")

            # Get active RTP boost effects
            now = discord.utils.utcnow()
            effects_result = await session.execute(
                select(ActiveEffect).where(
                    ActiveEffect.user_id == user_id,
                    ActiveEffect.effect_type == "rtp_boost",
                    ActiveEffect.expires_at > now,
                )
            )
            active_effects = effects_result.scalars().all()

            boost_rtp = sum(effect.effect_value for effect in active_effects)

            return vip_rtp_bonus + boost_rtp

    async def get_adjusted_house_edge(
        self, user_id: int, base_edge: Decimal = Decimal("0.04")
    ) -> Decimal:
        """
        Get house edge adjusted for VIP tier and active RTP boosts.
        Minimum 1% house edge to ensure sustainability.
        """
        raw_user_id = user_id
        user_id = self.hash_user_id(user_id)
        rtp_adjustment = await self.get_effective_rtp(raw_user_id)
        # Convert RTP percentage points to edge reduction
        # e.g., 2% RTP boost means we reduce house edge by 2%
        adjusted = base_edge - (rtp_adjustment / 100)

        # Ensure minimum 1% house edge
        MIN_HOUSE_EDGE = Decimal("0.01")
        return max(MIN_HOUSE_EDGE, adjusted)

    async def get_vip_leaderboard(self, limit: int = 10) -> List[dict]:
        """Get top users by total wagered (aggregated from GameHistory)."""
        async with self.async_sessionmaker() as session:
            # Aggregate total wagered from GameHistory
            stmt = (
                select(
                    GameHistory.user_id,
                    func.coalesce(func.sum(GameHistory.wagered), Decimal("0")).label(
                        "total_wagered"
                    ),
                )
                .group_by(GameHistory.user_id)
                .order_by(func.sum(GameHistory.wagered).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

            hashes = [row.user_id for row in rows]
            resolved = await self.resolve_user_hashes(hashes)

            leaderboard = []
            for row in rows:
                user_hash = row.user_id
                total_wagered = row.total_wagered
                # Get user's VIP tier
                user_vip = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_hash)
                )
                vip = user_vip.scalar_one_or_none()
                tier = None
                if vip:
                    tier_result = await session.execute(
                        select(VIPTier).where(VIPTier.id == vip.tier_id)
                    )
                    tier = tier_result.scalar_one_or_none()

                leaderboard.append(
                    {
                        "user_id": resolved.get(user_hash, user_hash),
                        "total_wagered": total_wagered,
                        "tier": tier,
                    }
                )

            return leaderboard

    async def set_user_vip_tier(self, user_id: int, tier_id: int) -> bool:
        """Manually set a user's VIP tier (admin only)."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Verify tier exists
                tier_result = await session.execute(
                    select(VIPTier).where(VIPTier.id == tier_id)
                )
                if not tier_result.scalar_one_or_none():
                    return False

                # Get or create user VIP
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    user_vip = UserVIP(user_id=user_id, tier_id=tier_id)
                    session.add(user_vip)
                else:
                    user_vip.tier_id = tier_id

            await session.commit()
            return True

    async def reset_user_vip(self, user_id: int) -> bool:
        """Reset user's VIP progress to default (admin only)."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        await self.ensure_default_vip_tiers()
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if user_vip:
                    user_vip.tier_id = 1
                    user_vip.total_rakeback_earned = Decimal("0")
                else:
                    user_vip = UserVIP(user_id=user_id, tier_id=1)
                    session.add(user_vip)

                # Reset rakeback balance
                balance_result = await session.execute(
                    select(RakebackBalance).where(RakebackBalance.user_id == user_id)
                )
                balance = balance_result.scalar_one_or_none()
                if balance:
                    balance.accumulated = Decimal("0")
                    balance.total_claimed = Decimal("0")

            await session.commit()
            return True
