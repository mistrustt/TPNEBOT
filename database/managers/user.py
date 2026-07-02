from .base import BaseManager

from sqlalchemy.future import select
from sqlalchemy import delete, exists, update, func
from sqlalchemy.exc import SQLAlchemyError
from typing import List
from utils.security import encrypt_location, decrypt_location
from ..models import (
    LastFMusers,
    UserTimezone,
    UserLocation,
    FavoriteSongs,
    LastFMvotes,
    Blacklist,
    BoosterRole,
    Transaction,
    Reputation,
    Wallet,
    Item,
    Skulls,
    Flames,
    Hearts,
    Sobs,
    Clowns,
    HeardleGameStats,
    UserRoleHistory,
    Task,
    TempVoiceChannel,
    UserNameHistory,
    UserAlt,
    CommandCooldown,
    ImageMuteSetting,
    CommandUsageDaily,
    CommandLatencyDaily,
    CommandErrorDaily,
    DailyUserExposure,
    ActiveEffect,
    ItemCooldown,
    TradeLog,
    Bounty,
    Loan,
    LoanPayment,
    Job,
    UserVIP,
    RakebackBalance,
    RakebackTransaction,
    GameHistory,
    GameSession,
    GameSessionEvent,
    UserEconomicPreferences,
    ForceRole,
    CryptoAsset,
    Juul,
    TransferHistory,
)
from datetime import timezone
import discord
import logging

logger = logging.getLogger("discord_bot")


class UserMixin(BaseManager):
    async def add_to_blacklist(self, user_id: str, admin_id: str, reason: str) -> None:
        await self.ensure_user_identity(user_id)
        await self.ensure_user_identity(admin_id)
        user_id = self.hash_user_id(user_id)
        admin_id = self.hash_user_id(admin_id)
        try:
            async with self.async_sessionmaker() as session:
                await session.execute(
                    delete(Blacklist).where(Blacklist.user_id == user_id)
                )
                blacklist_entry = Blacklist(user_id=user_id, admin_id=admin_id, reason=reason)
                session.add(blacklist_entry)
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error adding to blacklist: {str(e)}")

    async def remove_from_blacklist(self, user_id: str) -> None:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            await session.execute(delete(Blacklist).where(Blacklist.user_id == user_id))
            await session.commit()

    async def clear_blacklist(self) -> None:
        """Clear the entire blacklist."""
        async with self.async_sessionmaker() as session:
            for entry in await session.execute(select(Blacklist)):
                await session.delete(entry)
            await session.commit()

    async def is_user_blacklisted(self, user_id: str) -> bool:
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Blacklist).filter_by(user_id=user_id))
            return result.scalar_one_or_none() is not None

    async def get_blacklisted_users(self) -> list:
        async with self.async_sessionmaker() as session:
            result = await session.execute(select(Blacklist))
            return result.scalars().all()

    async def add_favorite_song(self, user_id: int, song_title: str):
        """Add a favorite song for the user."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    favorite_song = FavoriteSongs(
                        user_id=user_id, song_title=song_title
                    )
                    session.add(favorite_song)
                await session.commit()
        except SQLAlchemyError as e:
            logging.info(f"Error adding favorite song: {str(e)}")

    async def get_favorite_songs(self, user_id: int) -> list:
        """Retrieve all favorite songs for a specific user."""
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                query = select(FavoriteSongs).filter_by(user_id=user_id)
                result = await session.execute(query)
                return result.scalars().all()
        except SQLAlchemyError as e:
            logging.error(f"Error retrieving favorite songs: {str(e)}")
            return []

    async def remove_favorite_song(self, user_id: int, song_title: str):
        """Remove a favorite song for a user."""
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(FavoriteSongs).where(
                            FavoriteSongs.user_id == user_id,
                            FavoriteSongs.song_title == song_title,
                        )
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error removing favorite song: {str(e)}")

    async def clear_favorite_songs(self, user_id: int):
        """Clear all favorite songs for a user."""
        user_id = self.hash_user_id(user_id)
        try:
            async with self.async_sessionmaker() as session:
                async with session.begin():
                    await session.execute(
                        delete(FavoriteSongs).where(FavoriteSongs.user_id == user_id)
                    )
                await session.commit()
        except SQLAlchemyError as e:
            logging.error(f"Error clearing favorite songs: {str(e)}")

    async def set_user_timezone(self, user_id: int, timezone: str):
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserTimezone).where(UserTimezone.user_id == user_id)
                )

                user_timezone = UserTimezone(user_id=user_id, timezone=timezone)
                session.add(user_timezone)
            await session.commit()

    async def get_user_timezone(self, user_id: int):
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserTimezone.timezone).filter_by(user_id=user_id)
            )
            timezone = result.scalar_one_or_none()
            return timezone

    async def set_user_location(self, user_id: int, location: str):
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserLocation).where(UserLocation.user_id == user_id)
                )

                encrypted = encrypt_location(location) if location is not None else None
                user_location = UserLocation(user_id=user_id, location_encrypted=encrypted)
                session.add(user_location)
            await session.commit()

    async def get_user_location(self, user_id: int):
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserLocation.location_encrypted).filter_by(user_id=user_id)
            )
            encrypted = result.scalar_one_or_none()
            if encrypted is None:
                return None
            return decrypt_location(encrypted)

    async def log_name_change(
        self, user_id: int, old_name: str, new_name: str, change_type: str
    ):
        """
        Logs a username or nickname change for a user.

        :param user_id: Discord user ID
        :param old_name: The old username or nickname
        :param new_name: The new username or nickname
        :param change_type: Type of change - "username" or "nickname"
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        timestamp = discord.utils.utcnow()
        async with self.async_sessionmaker() as session:
            try:
                new_entry = UserNameHistory(
                    user_id=user_id,
                    old_name=old_name,
                    new_name=new_name,
                    change_type=change_type,
                    timestamp=timestamp,
                )
                session.add(new_entry)
                await session.commit()
                print(
                    f"{change_type.capitalize()} change logged: {old_name} -> {new_name}"
                )
            except Exception as e:
                print(f"Error logging name change: {e}")

    async def get_name_history(self, user_id: int) -> List[dict]:
        """
        Retrieves the username and nickname history for a specified user.

        Uses a column-only query + .mappings() to avoid creating full ORM objects,
        which reduces memory overhead and improves performance for large histories.
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            try:
                stmt = (
                    select(
                        UserNameHistory.old_name.label("old_name"),
                        UserNameHistory.new_name.label("new_name"),
                        UserNameHistory.change_type.label("change_type"),
                        UserNameHistory.timestamp.label("timestamp"),
                    )
                    .where(UserNameHistory.user_id == user_id)
                    .order_by(UserNameHistory.timestamp.desc())
                )
                result = await session.execute(stmt)
                # .mappings().all() returns a list of dict-like Mapping objects
                rows = result.mappings().all()
                return [dict(row) for row in rows]
            except SQLAlchemyError as e:
                logger.error(f"Error retrieving name history for {user_id}: {e}")
                return []

    async def has_name_history(self, user_id: int) -> bool:
        """
        Checks if a user has any name change history.

        :param user_id: Discord user ID
        :return: True if history exists, False otherwise
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserNameHistory).where(UserNameHistory.user_id == user_id)
            )
            return result.scalars().first() is not None

    async def clear_name_history(self, user_id: int):
        """
        Clears the name change history for a specified user.

        :param user_id: Discord user ID
        """
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            try:
                await session.execute(
                    delete(UserNameHistory).where(UserNameHistory.user_id == user_id)
                )
                await session.commit()
                print(f"Name history cleared for user {user_id}")
            except Exception as e:
                print(f"Error clearing name history: {e}")

    async def log_user_roles(self, user_id: int, roles: List[int]):
        """Logs the user's roles to the database."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.get_session() as session:
            user_role_history = UserRoleHistory(
                user_id=user_id, roles=roles, timestamp=discord.utils.utcnow()
            )
            session.add(user_role_history)
            await session.commit()

    async def get_user_roles(self, user_id: int) -> List[int]:
        """Retrieves the user's roles from the database."""
        user_id = self.hash_user_id(user_id)
        async with self.get_session() as session:
            result = await session.execute(
                select(UserRoleHistory).where(UserRoleHistory.user_id == user_id)
            )
            record = result.scalars().first()
            if record:
                return record.roles
            else:
                return []

    async def remove_user_roles(self, user_id: int):
        """Removes the user's role records from the database."""
        user_id = self.hash_user_id(user_id)
        async with self.get_session() as session:
            await session.execute(
                delete(UserRoleHistory).where(UserRoleHistory.user_id == user_id)
            )
            await session.commit()

    async def add_user_alt(self, main_id: int, guild_id: int, alt_id: int) -> None:
        """
        Add a new alt-user mapping. Does nothing if the mapping already exists.
        """
        await self.ensure_user_identity(main_id)
        await self.ensure_user_identity(alt_id)
        main_id = self.hash_user_id(main_id)
        alt_id = self.hash_user_id(alt_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                exists = await session.execute(
                    select(UserAlt).where(
                        UserAlt.main_user_id == main_id,
                        UserAlt.guild_id == guild_id,
                        UserAlt.alt_user_id == alt_id,
                    )
                )
                if exists.scalars().first():
                    return
                session.add(
                    UserAlt(main_user_id=main_id, guild_id=guild_id, alt_user_id=alt_id)
                )

    async def remove_user_alt(self, main_id: int, guild_id: int, alt_id: int) -> None:
        """
        Remove a specific alt-user mapping.
        """
        main_id = self.hash_user_id(main_id)
        alt_id = self.hash_user_id(alt_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserAlt).where(
                        UserAlt.main_user_id == main_id,
                        UserAlt.guild_id == guild_id,
                        UserAlt.alt_user_id == alt_id,
                    )
                )

    async def clear_user_alts(self, main_id: int, guild_id: int) -> None:
        """
        Remove all alt-user mappings for a given main user in a guild.
        """
        main_id = self.hash_user_id(main_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(UserAlt).where(
                        UserAlt.main_user_id == main_id, UserAlt.guild_id == guild_id
                    )
                )

    async def get_user_alts(self, main_id: int, guild_id: int) -> List[int]:
        """
        Get a list of alt user IDs for a given main user in a guild.
        """
        main_id = self.hash_user_id(main_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(UserAlt.alt_user_id).where(
                    UserAlt.main_user_id == main_id, UserAlt.guild_id == guild_id
                )
            )
            hashes = result.scalars().all()
        mapping = await self.resolve_user_hashes(hashes)
        return [mapping[h] for h in hashes if mapping.get(h) is not None]

    async def get_all_linked_user_ids(self, user_id: int, guild_id: int) -> List[int]:
        """
        Get all user IDs linked to the given user (both mains and alts) within the same guild.
        Traverses relationships iteratively in Python to avoid recursion errors in SQL.
        """
        user_hash = self.hash_user_id(user_id)
        hash_seen = {user_hash}
        raw_seen = {user_id}
        queue = [user_hash]
        async with self.async_sessionmaker() as session:
            while queue:
                current_hash = queue.pop(0)
                # Find direct alts where current is main
                result_alt = await session.execute(
                    select(UserAlt.alt_user_id).where(
                        UserAlt.main_user_id == current_hash, UserAlt.guild_id == guild_id
                    )
                )
                alt_hashes = [row[0] for row in result_alt]
                # Find mains where current is an alt
                result_main = await session.execute(
                    select(UserAlt.main_user_id).where(
                        UserAlt.alt_user_id == current_hash, UserAlt.guild_id == guild_id
                    )
                )
                main_hashes = [row[0] for row in result_main]
                for linked_hash in alt_hashes + main_hashes:
                    if linked_hash not in hash_seen:
                        hash_seen.add(linked_hash)
                        queue.append(linked_hash)
                        raw_id = await self.resolve_user_hash(linked_hash)
                        if raw_id is not None:
                            raw_seen.add(raw_id)
        # Remove the original user
        linked = list(raw_seen)
        linked.remove(user_id)
        return linked

    async def delete_all_data_for_user(self, user_id: int):
        """
        Delete all personal, economy, game, and social data for a user.

        Moderation records (Punishment, CaseNote, WatchdogLog, JailedUser,
        Blacklist, SuspiciousActivityLog) are intentionally retained for
        community safety and anti-abuse purposes. The UserIdentity mapping
        row is also retained so those moderation records remain resolvable.
        """
        user_hash = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                # Social / profile data
                await session.execute(
                    delete(Reputation).where(Reputation.discord_id == user_hash)
                )
                await session.execute(
                    delete(UserLocation).where(UserLocation.user_id == user_hash)
                )
                await session.execute(
                    delete(UserTimezone).where(UserTimezone.user_id == user_hash)
                )
                await session.execute(
                    delete(LastFMusers).where(LastFMusers.discord_id == user_hash)
                )
                await session.execute(
                    delete(LastFMvotes).where(LastFMvotes.discord_id == user_hash)
                )
                await session.execute(
                    delete(FavoriteSongs).where(FavoriteSongs.user_id == user_hash)
                )
                await session.execute(
                    delete(UserNameHistory).where(UserNameHistory.user_id == user_hash)
                )
                await session.execute(
                    delete(UserRoleHistory).where(UserRoleHistory.user_id == user_hash)
                )
                await session.execute(
                    delete(BoosterRole).where(BoosterRole.user_id == user_hash)
                )
                await session.execute(
                    delete(ForceRole).where(ForceRole.user_id == user_hash)
                )
                await session.execute(
                    delete(ImageMuteSetting).where(ImageMuteSetting.user_id == user_hash)
                )

                # Reaction counters
                await session.execute(delete(Sobs).where(Sobs.discord_id == user_hash))
                await session.execute(delete(Skulls).where(Skulls.discord_id == user_hash))
                await session.execute(delete(Flames).where(Flames.discord_id == user_hash))
                await session.execute(delete(Hearts).where(Hearts.discord_id == user_hash))
                await session.execute(
                    delete(Clowns).where(Clowns.discord_id == user_hash)
                )

                # Economy / inventory
                await session.execute(
                    delete(CryptoAsset).where(CryptoAsset.user_id == user_hash)
                )
                await session.execute(delete(Item).where(Item.user_id == user_hash))
                await session.execute(
                    delete(ItemCooldown).where(ItemCooldown.user_id == user_hash)
                )
                await session.execute(
                    delete(ActiveEffect).where(ActiveEffect.user_id == user_hash)
                )
                await session.execute(
                    delete(Wallet).where(Wallet.user_id == user_hash)
                )
                await session.execute(delete(Job).where(Job.user_id == user_hash))
                await session.execute(
                    delete(UserVIP).where(UserVIP.user_id == user_hash)
                )
                await session.execute(
                    delete(RakebackBalance).where(RakebackBalance.user_id == user_hash)
                )
                await session.execute(
                    delete(RakebackTransaction).where(
                        RakebackTransaction.user_id == user_hash
                    )
                )
                await session.execute(
                    delete(UserEconomicPreferences).where(
                        UserEconomicPreferences.user_id == user_hash
                    )
                )

                # Financial transaction records (Option A: delete records where the
                # user is a party; these are shared records but the user requested
                # complete removal).
                await session.execute(
                    delete(Transaction).where(Transaction.from_user_id == user_hash)
                )
                await session.execute(
                    delete(Transaction).where(Transaction.to_user_id == user_hash)
                )
                await session.execute(
                    delete(TransferHistory).where(TransferHistory.sender_id == user_hash)
                )
                await session.execute(
                    delete(TransferHistory).where(
                        TransferHistory.receiver_id == user_hash
                    )
                )
                await session.execute(
                    delete(TradeLog).where(TradeLog.from_user_id == user_hash)
                )
                await session.execute(
                    delete(TradeLog).where(TradeLog.to_user_id == user_hash)
                )
                await session.execute(
                    delete(Bounty).where(Bounty.target_id == user_hash)
                )
                await session.execute(
                    delete(Bounty).where(Bounty.issuer_id == user_hash)
                )
                await session.execute(
                    delete(Bounty).where(Bounty.claimer_id == user_hash)
                )
                await session.execute(delete(Loan).where(Loan.user_id == user_hash))
                await session.execute(
                    delete(LoanPayment).where(LoanPayment.user_id == user_hash)
                )

                # Games
                await session.execute(
                    delete(HeardleGameStats).where(HeardleGameStats.user_id == user_hash)
                )
                await session.execute(
                    delete(GameHistory).where(GameHistory.user_id == user_hash)
                )
                # Remove sessions the user owns; for sessions they only participate
                # in, remove their hash from the participants array.
                await session.execute(
                    delete(GameSession).where(GameSession.owner_id == user_hash)
                )
                await session.execute(
                    update(GameSession)
                    .where(GameSession.owner_id != user_hash)
                    .where(GameSession.participants.any(user_hash))
                    .values(participants=func.array_remove(GameSession.participants, user_hash))
                )

                # Tasks / cooldowns / utility state
                await session.execute(delete(Task).where(Task.user_id == user_hash))
                await session.execute(
                    delete(CommandCooldown).where(CommandCooldown.user_id == user_hash)
                )
                await session.execute(
                    delete(TempVoiceChannel).where(TempVoiceChannel.owner_id == user_hash)
                )

                # Alt relationships
                await session.execute(
                    delete(UserAlt).where(UserAlt.main_user_id == user_hash)
                )
                await session.execute(
                    delete(UserAlt).where(UserAlt.alt_user_id == user_hash)
                )

                # Owner/admin command audit log is intentionally NOT deleted. It is
                # a compliance/security record tied to bot administrators and is
                # not personal data of the requesting user.

                # Aggregated analytics
                await session.execute(
                    delete(CommandUsageDaily).where(
                        CommandUsageDaily.user_hash == user_hash
                    )
                )
                await session.execute(
                    delete(CommandLatencyDaily).where(
                        CommandLatencyDaily.user_hash == user_hash
                    )
                )
                await session.execute(
                    delete(CommandErrorDaily).where(
                        CommandErrorDaily.user_hash == user_hash
                    )
                )
                await session.execute(
                    delete(DailyUserExposure).where(
                        DailyUserExposure.user_hash == user_hash
                    )
                )

                # Guild-scoped fun objects the user currently holds
                await session.execute(
                    update(Juul)
                    .where(Juul.holder_id == user_hash)
                    .values(holder_id=None)
                )
