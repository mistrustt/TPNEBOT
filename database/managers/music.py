from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import SQLAlchemyError
from ..models import (
    LastFMusers,
    LastFMvotes,
    HeardleGameStats,
)
import discord
import logging

logger = logging.getLogger("discord_bot")


class MusicMixin(BaseManager):
    async def get_lastfm_usernames(self):
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(LastFMusers.lastfm_username))
            return result.scalars().all()
    async def get_lastfm_username(self, discord_id: str) -> str:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers.lastfm_username).filter_by(discord_id=discord_id)
                )
                username = result.scalar_one_or_none()
                if username is None:
                    logging.info(
                        f"User {discord_id} has no last.fm account and will not be indexed."
                    )
                    raise ValueError(f"User {discord_id} has no last.fm account.")
                return username
        except SQLAlchemyError as e:
            logging.error(f"Error fetching LastFM username for {discord_id}: {e}")
            raise
    async def set_lastfm_username(self, discord_id: str, username: str) -> None:
        await self.ensure_user_identity(discord_id)
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(LastFMusers).where(LastFMusers.discord_id == discord_id)
                    )

                    new_user = LastFMusers(
                        discord_id=discord_id, lastfm_username=username
                    )
                    session.add(new_user)
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error setting LastFM username: {str(e)}")
    async def get_lastfm_embed_color(self, discord_id: str) -> discord.Color:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers.embed_color).filter_by(discord_id=discord_id)
                )
                color = result.scalar_one_or_none()
                if color:
                    logging.info(f"Retrieved color for {discord_id}: {color}")
                    return discord.Color(int(color.replace("#", "0x"), 16))
                else:
                    logging.info(f"No color set for {discord_id}, using default green.")
                    return discord.Color.green()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving color: {str(e)}")
            return discord.Color.green()
    async def set_lastfm_embed_color(self, discord_id: str, color: str) -> None:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMusers).filter_by(discord_id=discord_id)
                )
                user = result.scalar_one_or_none()

                if user and user.lastfm_username:
                    user.embed_color = color
                    logging.info(
                        f"Updated Last.fm embed color for {discord_id} to {color}"
                    )
                else:
                    raise ValueError(
                        "Please set your Last.fm username before setting an embed color."
                    )

                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error setting LastFM embed color: {str(e)}")
    async def log_vote(self, user_id: str, command: str, vote: str):
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    if vote == "up":
                        stmt = (
                            update(LastFMvotes)
                            .where(
                                LastFMvotes.discord_id == user_id,
                                LastFMvotes.command == command,
                            )
                            .values(upvotes=LastFMvotes.upvotes + 1)
                        )
                    elif vote == "down":
                        stmt = (
                            update(LastFMvotes)
                            .where(
                                LastFMvotes.discord_id == user_id,
                                LastFMvotes.command == command,
                            )
                            .values(downvotes=LastFMvotes.downvotes + 1)
                        )

                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_vote = LastFMvotes(discord_id=user_id, command=command)
                        if vote == "up":
                            new_vote.upvotes = 1
                        elif vote == "down":
                            new_vote.downvotes = 1
                        session.add(new_vote)

                await session.commit()
        except SQLAlchemyError as e:
            return 0
    async def get_vote_stats(self, user_id: str, command: str):
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(LastFMvotes.upvotes, LastFMvotes.downvotes).filter_by(
                        discord_id=user_id, command=command
                    )
                )
                stats = result.first()
                return stats if stats else (0, 0)
        except SQLAlchemyError as e:
            return 0
    async def get_heardle_stats(self, discord_id: int) -> int:
        discord_id = self.hash_user_id(discord_id)
        try:
            async with self.async_sessionmaker() as session:
                result = await session.execute(
                    select(HeardleGameStats).filter_by(user_id=discord_id)
                )
                stats = result.scalar_one_or_none()
                return stats
        except SQLAlchemyError as e:
            return HeardleGameStats()
    async def add_heardle_win(self, discord_id: int, amount: int = 1) -> int:
        raw_discord_id = discord_id
        await self.ensure_user_identity(raw_discord_id)
        discord_id = self.hash_user_id(raw_discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(HeardleGameStats)
                        .where(HeardleGameStats.user_id == discord_id)
                        .values(
                            wins=HeardleGameStats.wins + amount,
                            streak=HeardleGameStats.streak + amount,
                        )
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = HeardleGameStats(
                            user_id=discord_id, wins=amount, losses=0, streak=amount
                        )
                        session.add(new_user)

                    await session.commit()
                    return await self.get_heardle_stats(raw_discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0
    async def add_heardle_loss(self, discord_id: int, amount: int = 1) -> int:
        raw_discord_id = discord_id
        await self.ensure_user_identity(raw_discord_id)
        discord_id = self.hash_user_id(raw_discord_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    stmt = (
                        update(HeardleGameStats)
                        .where(HeardleGameStats.user_id == discord_id)
                        .values(losses=HeardleGameStats.losses + amount, streak=0)
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        new_user = HeardleGameStats(
                            user_id=discord_id, wins=0, losses=amount, streak=0
                        )
                        session.add(new_user)

                    await session.commit()
                    return await self.get_heardle_stats(raw_discord_id)
        except SQLAlchemyError as e:
            logging.error(f"Error incrementing reputation: {str(e)}")
            return 0
