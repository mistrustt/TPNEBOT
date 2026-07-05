from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from ..models import (
    Reputation,
    Skulls,
    Flames,
    Hearts,
    Clowns,
    Sobs,
    ReactionSettings,
)
import logging
from datetime import date
import discord

logger = logging.getLogger("discord_bot")


class SocialMixin(BaseManager):
    async def get_reputation(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(Reputation.reputation).filter_by(discord_id=discord_id)
                )
                rep = result.scalar_one_or_none()
                return rep if rep is not None else 0
        except SQLAlchemyError as e:
            return 0

    async def increment_reputation(self, discord_id: int, amount: int) -> int:
        raw_discord_id = discord_id
        await self.ensure_user_identity(raw_discord_id)
        discord_id = self.hash_user_id(raw_discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    # Legacy rows may have NULL in the reputation column.
                    stmt = (
                        update(Reputation)
                        .where(Reputation.discord_id == discord_id)
                        .values(
                            reputation=func.coalesce(Reputation.reputation, 0) + amount
                        )
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = Reputation(discord_id=discord_id, reputation=amount)
                        session.add(new_user)

                    await session.commit()
                    return await self.get_reputation(raw_discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0

    async def get_top_reputation_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation.discord_id, Reputation.reputation)
                .order_by(Reputation.reputation.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [
            (mapping.get(discord_id), reputation) for discord_id, reputation in rows
        ]

    async def get_bottom_reputation_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation.discord_id, Reputation.reputation)
                .order_by(Reputation.reputation.asc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [
            (mapping.get(discord_id), reputation) for discord_id, reputation in rows
        ]

    async def get_reputation_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation).order_by(Reputation.reputation.desc())
            )
            reputations = result.scalars().all()
            for rank, rep in enumerate(reputations, start=1):
                if rep.discord_id == discord_id:
                    return rank
            return -1

    # ------------------------------------------------------------------
    # Unified reputation/karma score
    # ------------------------------------------------------------------

    DAILY_REP_EARNED_CAP = 500
    DAILY_REPS_GIVEN_CAP = 5

    def _today(self) -> date:
        return discord.utils.utcnow().date()

    @staticmethod
    def reputation_title(score: int) -> str:
        if score >= 10000:
            return "Legendary"
        if score >= 5000:
            return "Paragon"
        if score >= 2500:
            return "Saint"
        if score >= 1000:
            return "Hero"
        if score >= 500:
            return "Guardian"
        if score >= 250:
            return "Respected"
        if score >= 100:
            return "Helpful"
        if score >= 50:
            return "Friendly"
        if score >= 10:
            return "Newcomer"
        if score <= -100:
            return "Villain"
        if score <= -50:
            return "Troublemaker"
        if score <= -10:
            return "Unpopular"
        return "Neutral"

    async def get_reputation_full(self, discord_id: int) -> dict:
        """Return the unified reputation score and daily counters."""
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Reputation).filter_by(discord_id=discord_id)
            )
            rep = result.scalar_one_or_none()
            if not rep:
                return {
                    "reputation": 0,
                    "good_reps_received": 0,
                    "bad_reps_received": 0,
                    "rep_earned_today": 0,
                    "title": self.reputation_title(0),
                }
            return {
                "reputation": rep.reputation,
                "good_reps_received": rep.good_reps_received,
                "bad_reps_received": rep.bad_reps_received,
                "rep_earned_today": rep.rep_earned_today,
                "title": self.reputation_title(rep.reputation),
            }

    async def add_reputation_score(
        self, discord_id: int, amount: int, *, cap: int = DAILY_REP_EARNED_CAP
    ) -> dict:
        """
        Add to the unified reputation score, respecting the daily cap on positive
        activity gains. Negative amounts (e.g., failed robbery) are uncapped.
        """
        raw_discord_id = discord_id
        await self.ensure_user_identity(raw_discord_id)
        discord_id = self.hash_user_id(raw_discord_id)
        today = self._today()

        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Reputation).filter_by(discord_id=discord_id)
                    )
                    rep = result.scalar_one_or_none()

                    if not rep:
                        rep = Reputation(discord_id=discord_id)
                        session.add(rep)

                    # Legacy rows may have NULL in these columns after migrations.
                    rep.reputation = rep.reputation or 0
                    rep.rep_earned_today = rep.rep_earned_today or 0

                    if rep.last_rep_earned_date != today:
                        rep.rep_earned_today = 0
                        rep.last_rep_earned_date = today

                    applied = amount
                    if amount > 0:
                        room = cap - rep.rep_earned_today
                        if room <= 0:
                            applied = 0
                        else:
                            applied = min(amount, room)

                    rep.reputation += applied
                    if applied > 0:
                        rep.rep_earned_today += applied

                    await session.commit()

                    return {
                        "reputation": rep.reputation,
                        "rep_earned_today": rep.rep_earned_today,
                        "applied": applied,
                        "capped": amount > 0 and applied < amount,
                    }
        except SQLAlchemyError as e:
            logger.error(f"Error adding reputation for {raw_discord_id}: {e}")
            return {
                "reputation": 0,
                "rep_earned_today": 0,
                "applied": 0,
                "capped": False,
            }

    async def give_rep(self, from_id: int, to_id: int, amount: int) -> dict:
        """
        Give reputation to another user. Enforces anti-abuse rules:
        - no self/bot votes
        - max DAILY_REPS_GIVEN_CAP per day from the giver
        - one vote per target per 24 hours
        The receiver's unified reputation score is updated directly.
        """
        if from_id == to_id:
            return {"ok": False, "error": "You cannot vote for yourself."}

        raw_from = from_id
        raw_to = to_id
        await self.ensure_user_identity(raw_from)
        await self.ensure_user_identity(raw_to)

        from_hash = self.hash_user_id(raw_from)
        to_hash = self.hash_user_id(raw_to)
        today = self._today()

        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    from_rep = await session.execute(
                        select(Reputation).filter_by(discord_id=from_hash)
                    )
                    from_rep = from_rep.scalar_one_or_none()
                    if not from_rep:
                        from_rep = Reputation(discord_id=from_hash)
                        session.add(from_rep)

                    if from_rep.last_rep_date != today:
                        from_rep.reps_given_today = 0
                        from_rep.last_rep_targets = []
                        from_rep.last_rep_date = today

                    # Legacy rows may have NULL in these columns after migrations.
                    from_rep.reps_given_today = from_rep.reps_given_today or 0

                    if from_rep.reps_given_today >= self.DAILY_REPS_GIVEN_CAP:
                        return {
                            "ok": False,
                            "error": f"You can only give {self.DAILY_REPS_GIVEN_CAP} rep votes per day.",
                        }

                    if to_hash in (from_rep.last_rep_targets or []):
                        return {
                            "ok": False,
                            "error": "You have already voted for that user today.",
                        }

                    to_rep = await session.execute(
                        select(Reputation).filter_by(discord_id=to_hash)
                    )
                    to_rep = to_rep.scalar_one_or_none()
                    if not to_rep:
                        to_rep = Reputation(discord_id=to_hash)
                        session.add(to_rep)

                    to_rep.reputation = (to_rep.reputation or 0) + amount
                    if amount > 0:
                        to_rep.good_reps_received = (to_rep.good_reps_received or 0) + 1
                    else:
                        to_rep.bad_reps_received = (to_rep.bad_reps_received or 0) + 1

                    from_rep.reps_given_today += 1
                    targets = list(from_rep.last_rep_targets or [])
                    targets.append(to_hash)
                    from_rep.last_rep_targets = targets

                    await session.commit()

                    return {
                        "ok": True,
                        "reputation": to_rep.reputation,
                        "reps_remaining": self.DAILY_REPS_GIVEN_CAP
                        - from_rep.reps_given_today,
                    }
        except SQLAlchemyError as e:
            logger.error(f"Error giving rep from {raw_from} to {raw_to}: {e}")
            return {"ok": False, "error": f"Database error: {e}"}

    async def update_sobs(
        self, discord_id: int, sobs_rx_delta: int = 0, sobs_tx_delta: int = 0
    ):
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Sobs)
                        .where(Sobs.discord_id == discord_id)
                        .values(
                            sobs_rx=Sobs.sobs_rx + sobs_rx_delta,
                            sobs_tx=Sobs.sobs_tx + sobs_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_sobs = Sobs(
                            discord_id=discord_id,
                            sobs_rx=sobs_rx_delta,
                            sobs_tx=sobs_tx_delta,
                        )
                        session.add(new_sobs)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_skulls(
        self, discord_id: int, skulls_rx_delta: int = 0, skulls_tx_delta: int = 0
    ):
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Skulls)
                        .where(Skulls.discord_id == discord_id)
                        .values(
                            skulls_rx=Skulls.skulls_rx + skulls_rx_delta,
                            skulls_tx=Skulls.skulls_tx + skulls_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_skulls = Skulls(
                            discord_id=discord_id,
                            skulls_rx=skulls_rx_delta,
                            skulls_tx=skulls_tx_delta,
                        )
                        session.add(new_skulls)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_flames(
        self, discord_id: int, flames_rx_delta: int = 0, flames_tx_delta: int = 0
    ):
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Flames)
                        .where(Flames.discord_id == discord_id)
                        .values(
                            flames_rx=Flames.flames_rx + flames_rx_delta,
                            flames_tx=Flames.flames_tx + flames_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_flames = Flames(
                            discord_id=discord_id,
                            flames_rx=flames_rx_delta,
                            flames_tx=flames_tx_delta,
                        )
                        session.add(new_flames)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_hearts(
        self, discord_id: int, hearts_rx_delta: int = 0, hearts_tx_delta: int = 0
    ):
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Hearts)
                        .where(Hearts.discord_id == discord_id)
                        .values(
                            hearts_rx=Hearts.hearts_rx + hearts_rx_delta,
                            hearts_tx=Hearts.hearts_tx + hearts_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_hearts = Hearts(
                            discord_id=discord_id,
                            hearts_rx=hearts_rx_delta,
                            hearts_tx=hearts_tx_delta,
                        )
                        session.add(new_hearts)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def update_clowns(
        self, discord_id: int, clowns_rx_delta: int = 0, clowns_tx_delta: int = 0
    ):
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(Clowns)
                        .where(Clowns.discord_id == discord_id)
                        .values(
                            clowns_rx=Clowns.clowns_rx + clowns_rx_delta,
                            clowns_tx=Clowns.clowns_tx + clowns_tx_delta,
                        )
                    )
                    result = await session.execute(stmt)
                    if result.rowcount == 0:
                        new_clowns = Clowns(
                            discord_id=discord_id,
                            clowns_rx=clowns_rx_delta,
                            clowns_tx=clowns_tx_delta,
                        )
                        session.add(new_clowns)
                    await session.commit()
        except SQLAlchemyError as e:
            return 0

    async def get_reaction_stats(
        self, discord_id: int, reaction_type: str
    ) -> tuple[int, int]:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                if reaction_type == "sobs":
                    result = await session.execute(
                        select(Sobs.sobs_rx, Sobs.sobs_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "skulls":
                    result = await session.execute(
                        select(Skulls.skulls_rx, Skulls.skulls_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "flames":
                    result = await session.execute(
                        select(Flames.flames_rx, Flames.flames_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "hearts":
                    result = await session.execute(
                        select(Hearts.hearts_rx, Hearts.hearts_tx).filter_by(
                            discord_id=discord_id
                        )
                    )
                elif reaction_type == "clowns":
                    result = await session.execute(
                        select(Clowns.clowns_rx, Clowns.clowns_tx).filter_by(
                            discord_id=discord_id
                        )
                    )

                stats = result.first()
                if stats:
                    return stats
                else:
                    return 0, 0
        except SQLAlchemyError as e:
            return 0, 0

    async def get_top_sobs_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Sobs.discord_id, Sobs.sobs_rx)
                .order_by(Sobs.sobs_rx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), sobs_rx) for discord_id, sobs_rx in rows]

    async def get_bottom_sobs_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Sobs.discord_id, Sobs.sobs_tx)
                .order_by(Sobs.sobs_tx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), sobs_tx) for discord_id, sobs_tx in rows]

    async def get_sobs_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Sobs).order_by(Sobs.sobs_rx.desc()))
            sobs_list = result.scalars().all()
            for rank, sobs in enumerate(sobs_list, start=1):
                if sobs.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_skulls_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls.discord_id, Skulls.skulls_rx)
                .order_by(Skulls.skulls_rx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), skulls_rx) for discord_id, skulls_rx in rows]

    async def get_bottom_skulls_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls.discord_id, Skulls.skulls_tx)
                .order_by(Skulls.skulls_tx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), skulls_tx) for discord_id, skulls_tx in rows]

    async def get_skulls_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Skulls).order_by(Skulls.skulls_rx.desc())
            )
            skulls_list = result.scalars().all()
            for rank, skulls in enumerate(skulls_list, start=1):
                if skulls.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_flames_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames.discord_id, Flames.flames_rx)
                .order_by(Flames.flames_rx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), flames_rx) for discord_id, flames_rx in rows]

    async def get_bottom_flames_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames.discord_id, Flames.flames_tx)
                .order_by(Flames.flames_tx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), flames_tx) for discord_id, flames_tx in rows]

    async def get_flames_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Flames).order_by(Flames.flames_rx.desc())
            )
            flames_list = result.scalars().all()
            for rank, flames in enumerate(flames_list, start=1):
                if flames.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_hearts_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts.discord_id, Hearts.hearts_rx)
                .order_by(Hearts.hearts_rx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), hearts_rx) for discord_id, hearts_rx in rows]

    async def get_bottom_hearts_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts.discord_id, Hearts.hearts_tx)
                .order_by(Hearts.hearts_tx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), hearts_tx) for discord_id, hearts_tx in rows]

    async def get_hearts_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Hearts).order_by(Hearts.hearts_rx.desc())
            )
            hearts_list = result.scalars().all()
            for rank, hearts in enumerate(hearts_list, start=1):
                if hearts.discord_id == discord_id:
                    return rank
            return -1

    async def get_top_clowns_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns.discord_id, Clowns.clowns_rx)
                .order_by(Clowns.clowns_rx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), clowns_rx) for discord_id, clowns_rx in rows]

    async def get_bottom_clowns_users(self, limit=10):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns.discord_id, Clowns.clowns_tx)
                .order_by(Clowns.clowns_tx.desc())
                .limit(limit)
            )
            rows = result.all()
        hashes = [discord_id for discord_id, _ in rows]
        mapping = await self.resolve_user_hashes(hashes)
        return [(mapping.get(discord_id), clowns_tx) for discord_id, clowns_tx in rows]

    async def get_clowns_user_rank(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Clowns).order_by(Clowns.clowns_rx.desc())
            )
            clowns_list = result.scalars().all()
            for rank, clowns in enumerate(clowns_list, start=1):
                if clowns.discord_id == discord_id:
                    return rank
            return -1

    async def toggle_self_reactions(self, guild_id: int) -> bool:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ReactionSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()
                if not settings:
                    new_settings = ReactionSettings(
                        guild_id=guild_id, self_reactions_enabled=False
                    )
                    session.add(new_settings)
                    return False
                else:
                    settings.self_reactions_enabled = (
                        not settings.self_reactions_enabled
                    )
                    return settings.self_reactions_enabled

    async def is_self_reactions_enabled(self, guild_id: int) -> bool:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ReactionSettings).filter_by(guild_id=guild_id)
            )
            settings = result.scalar_one_or_none()
            return settings.self_reactions_enabled if settings else False
