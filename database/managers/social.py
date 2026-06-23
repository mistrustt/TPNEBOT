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
                    stmt = (
                        update(Reputation)
                        .where(Reputation.discord_id == discord_id)
                        .values(reputation=Reputation.reputation + amount)
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
        return [(mapping.get(discord_id), reputation) for discord_id, reputation in rows]
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
        return [(mapping.get(discord_id), reputation) for discord_id, reputation in rows]
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
