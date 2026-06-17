from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, case, literal_column
from sqlalchemy import func
from typing import List, Optional
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

logger = logging.getLogger("discord_bot")


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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                gs = await session.get(GameSession, session_id)
                if not gs:
                    return False
                state = gs.state or {}
                refunds = [r for r in state.get("refunds", []) if r.get("user_id") != user_id]
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
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(user_id)
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
        async with self.async_sessionmaker() as session:
            wallet = await self.get_wallet_by_user_id(user_id)
            if not wallet.server_seed:
                # first‐time generation
                wallet.server_seed = secrets.token_hex(16)
                wallet.previous_server_seed = None
                wallet.seed_rotated_at = discord.utils.utcnow()
                await session.commit()
            return wallet.server_seed
    async def set_client_seed(self, user_id: int, seed: str) -> None:
        await self.get_wallet_by_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                wallet = result.scalar_one()
                wallet.client_seed = seed
    async def reveal_and_rotate(self, user_id: int) -> tuple[Optional[str], str]:
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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Wallet).where(Wallet.user_id == user_id).with_for_update()
                )
                w = result.scalar_one_or_none()
                if w is None:
                    # create on demand if that’s your policy:
                    await session.rollback()
                    await self.create_wallet(user_id)
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            wallet = result.scalar_one_or_none()
            if not wallet:
                return None
            return wallet.previous_server_seed
    async def increment_nonce(self, user_id: int) -> int:
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
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                session.add(
                    GameHistory(
                        user_id=user_id,
                        game_name=game_name,
                        outcome=outcome,
                        wagered=bet,
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
    async def increment_win(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
    ) -> tuple[str, str]:
        """Insert a win entry into game history."""
        return await self.record_game(
            user_id=user_id,
            game_name=game_name,
            outcome="win",
            bet=bet,
            client_seed=client_seed,
            seed_used=seed_used,
            nonce=nonce,
            hash_hex=hash_hex,
        )
    async def increment_loss(
        self,
        user_id: int,
        game_name: str,
        bet,
        client_seed: str,
        seed_used: str,
        nonce: int,
        hash_hex: str,
    ) -> tuple[str, str]:
        """Insert a loss entry into game history."""
        return await self.record_game(
            user_id=user_id,
            game_name=game_name,
            outcome="loss",
            bet=bet,
            client_seed=client_seed,
            seed_used=seed_used,
            nonce=nonce,
            hash_hex=hash_hex,
        )
    async def get_total_wins(self, user_id: int) -> int:
        """Count the total wins for a user based on game history."""
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
            return result.all()
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
            return result.all()
    async def fetch_game_for_user(
        self, user_id: int, game_name: str, nonce: int
    ) -> GameHistory | None:
        """Fetch a specific game history entry for a user by game name and nonce."""
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
    async def get_wager_stats(
        self, user_id: int, game_name: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Returns (total_wagered,
                 total_wagered_on_wins,
                 total_wagered_on_losses)
        """
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
            {"name": "Unranked", "level": 0, "min_wagered": Decimal("0"), "rakeback_rate": Decimal("0"), "rtp_bonus": Decimal("0")},
            {"name": "Bronze", "level": 1, "min_wagered": Decimal("100000000000"), "rakeback_rate": Decimal("0.0100"), "rtp_bonus": Decimal("0")},
            {"name": "Silver", "level": 2, "min_wagered": Decimal("100000000000000"), "rakeback_rate": Decimal("0.0200"), "rtp_bonus": Decimal("0.0050")},
            {"name": "Gold", "level": 3, "min_wagered": Decimal("100000000000000000"), "rakeback_rate": Decimal("0.0300"), "rtp_bonus": Decimal("0.0100")},
            {"name": "Platinum", "level": 4, "min_wagered": Decimal("100000000000000000000"), "rakeback_rate": Decimal("0.0500"), "rtp_bonus": Decimal("0.0150")},
            {"name": "Diamond", "level": 5, "min_wagered": Decimal("100000000000000000000000"), "rakeback_rate": Decimal("0.1000"), "rtp_bonus": Decimal("0.0200")},
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
                total_wagered = await self.get_total_wagered_all_games(user_id)
                # Determine appropriate tier based on total wagered
                new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id
            await session.commit()
            await session.refresh(user_vip)
            return user_vip
    async def upgrade_all_users_vip(self) -> None:
        """Upgrade VIP tiers for all users based on their total wagered."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Get all user VIP records
                result = await session.execute(select(UserVIP))
                user_vips = result.scalars().all()

                for user_vip in user_vips:
                    # Calculate total wagered for each user
                    total_wagered = await self.get_total_wagered_all_games(user_vip.user_id)
                    # Determine appropriate tier based on total wagered
                    new_tier = await self.get_vip_tier_by_wagered(total_wagered)
                    if new_tier and new_tier.id != user_vip.tier_id:
                        user_vip.tier_id = new_tier.id

            await session.commit()
    async def get_user_vip(self, user_id: int) -> UserVIP:
        """Get or create user VIP record with tier info."""
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
            result = await session.execute(
                select(VIPTier).where(VIPTier.id == tier_id)
            )
            return result.scalar_one_or_none()
    async def get_all_vip_tiers(self) -> List[VIPTier]:
        """Get all VIP tiers ordered by level."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(VIPTier).order_by(VIPTier.level)
            )
            return list(result.scalars().all())
    async def get_total_wagered_all_games(self, user_id: int) -> Decimal:
        """Calculate total wagered across all games from GameHistory."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(GameHistory.wagered), Decimal("0")))
                .where(GameHistory.user_id == user_id)
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
        total_wagered = await self.get_total_wagered_all_games(user_id)
        return await self.get_vip_tier_by_wagered(total_wagered)
    async def record_rakeback(self, user_id: int, wagered: Decimal, game_name: str) -> Decimal:
        """
        Record rakeback after game. Returns rakeback amount.
        Does NOT update total_wagered (computed from GameHistory instead).
        """
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
                    rakeback_balance = RakebackBalance(user_id=user_id, accumulated=rakeback_amount)
                    session.add(rakeback_balance)
                else:
                    rakeback_balance.accumulated = (rakeback_balance.accumulated or Decimal("0")) + rakeback_amount

                # Update total rakeback earned
                user_vip.total_rakeback_earned = (user_vip.total_rakeback_earned or Decimal("0")) + rakeback_amount

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
                total_wagered = await self.get_total_wagered_all_games(user_id)
                new_tier = await self.get_vip_tier_by_wagered(total_wagered + wagered)
                if new_tier and new_tier.id != user_vip.tier_id:
                    user_vip.tier_id = new_tier.id

            await session.commit()
            return rakeback_amount
    async def update_user_wagered(self, user_id: int, amount: Decimal, game_name: str) -> Decimal:
        """Alias for record_rakeback for backward compatibility."""
        return await self.record_rakeback(user_id, amount, game_name)
    async def recalculate_user_vip_tier(self, user_id: int) -> Optional[VIPTier]:
        """Recalculate and update user's VIP tier based on total wagered from GameHistory."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if not user_vip:
                    return None

                # Get total wagered from GameHistory
                total_wagered = await self.get_total_wagered_all_games(user_id)
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
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(RakebackBalance).where(RakebackBalance.user_id == user_id)
            )
            balance = result.scalar_one_or_none()
            return balance.accumulated if balance else Decimal("0")
    async def add_rakeback(
        self, user_id: int, amount: Decimal, game_name: str, wagered: Decimal, rate: Decimal
    ) -> None:
        """Add rakeback to user's accumulated balance."""
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
                balance.total_claimed = (balance.total_claimed or Decimal("0")) + claim_amount

                # Credit to wallet
                wallet_id = await self.get_wallet_id_for_user(user_id)
                await self.process_treasury_transaction(
                    wallet_id, claim_amount, "Rakeback Claim", "standard"
                )

            await session.commit()
            return claim_amount
    async def get_rakeback_history(self, user_id: int, limit: int = 50) -> List[RakebackTransaction]:
        """Get rakeback transaction history for a user."""
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
        # Get total wagered from GameHistory
        total_wagered = await self.get_total_wagered_all_games(user_id)

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
                "total_rakeback_earned": user_vip.total_rakeback_earned if user_vip else Decimal("0"),
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
                select(ActiveEffect)
                .where(
                    ActiveEffect.user_id == user_id,
                    ActiveEffect.effect_type == "rtp_boost",
                    ActiveEffect.expires_at > now,
                )
            )
            active_effects = effects_result.scalars().all()

            boost_rtp = sum(effect.effect_value for effect in active_effects)

            return vip_rtp_bonus + boost_rtp
    async def get_adjusted_house_edge(self, user_id: int, base_edge: Decimal = Decimal("0.04")) -> Decimal:
        """
        Get house edge adjusted for VIP tier and active RTP boosts.
        Minimum 1% house edge to ensure sustainability.
        """
        rtp_adjustment = await self.get_effective_rtp(user_id)
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
                    func.coalesce(func.sum(GameHistory.wagered), Decimal("0")).label("total_wagered")
                )
                .group_by(GameHistory.user_id)
                .order_by(func.sum(GameHistory.wagered).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

            leaderboard = []
            for row in rows:
                user_id = row.user_id
                total_wagered = row.total_wagered
                # Get user's VIP tier
                user_vip = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                vip = user_vip.scalar_one_or_none()
                tier = None
                if vip:
                    tier_result = await session.execute(
                        select(VIPTier).where(VIPTier.id == vip.tier_id)
                    )
                    tier = tier_result.scalar_one_or_none()

                leaderboard.append({
                    "user_id": user_id,
                    "total_wagered": total_wagered,
                    "tier": tier,
                })

            return leaderboard
    async def set_user_vip_tier(self, user_id: int, tier_id: int) -> bool:
        """Manually set a user's VIP tier (admin only)."""
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
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserVIP).where(UserVIP.user_id == user_id)
                )
                user_vip = result.scalar_one_or_none()

                if user_vip:
                    user_vip.tier_id = 1
                    user_vip.total_rakeback_earned = Decimal("0")

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
