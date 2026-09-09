from .base import BaseManager, db_safe

from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import List, Optional
from utils.security import raise_if_url
from ..models import (
    ServerSettings,
    CommandStatus,
    CommandCooldown,
    JTCSettings,
    TempVoiceChannel,
    LockdownChannel,
    Juul,
)
from datetime import timedelta
import discord
import logging

logger = logging.getLogger("discord.client")


class GuildMixin(BaseManager):
    async def set_watchdog_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.watchdog_channel_id = channel_id
                else:
                    setting = ServerSettings(
                        guild_id=guild_id, watchdog_channel_id=channel_id
                    )
                    session.add(setting)
                await session.commit()

    async def set_spam_channel(
        self,
        guild_id: int,
        channel_id: int,
        spam_message: Optional[str] = None,
    ):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.spam_channel_id = channel_id
                    if spam_message is not None:
                        setting.spam_message = spam_message
                else:
                    setting = ServerSettings(
                        guild_id=guild_id, spam_channel_id=channel_id
                    )
                    if spam_message is not None:
                        setting.spam_message = spam_message
                    session.add(setting)
                await session.commit()

    @db_safe(default=None)
    async def get_spam_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.spam_channel_id).filter_by(guild_id=guild_id)
            )
            return result.scalar_one_or_none()

    @db_safe(default="999")
    async def get_spam_message(self, guild_id: int) -> str:
        """Return the configured spam-channel allowed message (defaults to '999')."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.spam_message).filter_by(guild_id=guild_id)
            )
            value = result.scalar_one_or_none()
            return value or "999"

    async def set_watchdog_enabled(self, guild_id: int, bool: str):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    settings.watchdog_enabled = bool
                else:
                    settings = ServerSettings(guild_id=guild_id, watchdog_enabled=bool)
                    session.add(settings)
                await session.commit()

    async def set_watchdog_feature(self, guild_id: int, feature: str, enabled: bool):
        """Set individual watchdog feature toggle"""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    setattr(settings, f"watchdog_{feature}", enabled)
                else:
                    settings = ServerSettings(guild_id=guild_id)
                    setattr(settings, f"watchdog_{feature}", enabled)
                    session.add(settings)
                await session.commit()

    async def get_snipe_enabled(self, guild_id: int) -> bool:
        """Return whether snipe is enabled for a guild. Off by default."""
        async with self.async_sessionmaker() as session:
            settings = await session.get(ServerSettings, guild_id)
            return bool(settings.snipe_enabled) if settings else False

    async def set_snipe_enabled(self, guild_id: int, enabled: bool):
        """Enable or disable snipe for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    settings.snipe_enabled = enabled
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, snipe_enabled=enabled
                    )
                    session.add(settings)
                await session.commit()

    @db_safe(default=None)
    async def get_server_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def clear_auto_roles(self, guild_id: int) -> None:
        """Clears autorole configuration for a guild."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                setting = await session.get(ServerSettings, guild_id)
                if setting:
                    setting.auto_role_ids = []
                await session.commit()

    async def set_report_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()

                if settings:
                    settings.report_channel_id = channel_id
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, report_channel_id=channel_id
                    )
                    session.add(settings)

            await session.commit()

    async def set_member_count_channel(self, guild_id: int, channel_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).filter_by(guild_id=guild_id)
                )
                settings = result.scalar_one_or_none()

                if settings:
                    settings.member_count_channel_id = channel_id
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, member_count_channel_id=channel_id
                    )
                    session.add(settings)

            await session.commit()

    async def get_member_count_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings).filter_by(guild_id=guild_id)
            )
            settings = result.scalar_one_or_none()

            return settings.member_count_channel_id if settings else None

    async def get_report_channel(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings).filter_by(guild_id=guild_id)
            )
            report_setting = result.scalar_one_or_none()

            return report_setting.report_channel_id if report_setting else None

    async def set_jail_settings(
        self, guild_id: int, role_id: int, channel_id: int
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                jail_setting = await session.get(ServerSettings, guild_id)
                if jail_setting:
                    jail_setting.jail_role_id = role_id
                    jail_setting.jail_channel_id = channel_id
                else:
                    jail_setting = ServerSettings(
                        guild_id=guild_id,
                        jail_role_id=role_id,
                        jail_channel_id=channel_id,
                    )
                    session.add(jail_setting)
                await session.commit()

    async def get_jail_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def toggle_antiaudio(self, guild_id: int, enabled: bool):
        async with self.get_session() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings:
                    settings.antimp3_enabled = enabled
                else:
                    settings = ServerSettings(
                        guild_id=guild_id, antimp3_enabled=enabled
                    )
                    session.add(settings)
                await session.commit()

    @db_safe(default=False)
    async def get_antiaudio_status(self, guild_id: int) -> bool:
        async with self.get_session() as session:
            settings = await session.get(ServerSettings, guild_id)
            return settings.antimp3_enabled if settings else False

    async def set_mute_settings(
        self, guild_id: int, mute_role_id: int, imute_role_id: int, rmute_role_id: int
    ) -> None:
        async with self.async_sessionmaker() as session:
            async with session.begin():
                mute_setting = await session.get(ServerSettings, guild_id)
                if mute_setting:
                    mute_setting.mute_role_id = mute_role_id
                    mute_setting.imute_role_id = imute_role_id
                    mute_setting.rmute_role_id = rmute_role_id
                else:
                    mute_setting = ServerSettings(
                        guild_id=guild_id,
                        mute_role_id=mute_role_id,
                        imute_role_id=imute_role_id,
                        rmute_role_id=rmute_role_id,
                    )
                    session.add(mute_setting)
                await session.commit()

    async def get_mute_settings(self, guild_id: int) -> ServerSettings:
        async with self.async_sessionmaker() as session:
            return await session.get(ServerSettings, guild_id)

    async def set_command_status(
        self, command_name: str, enabled: bool, channel_id: int = None, guild_id: int = None
    ) -> None:
        async with self.get_session() as session:
            async with session.begin():
                result = await session.execute(
                    select(CommandStatus).where(
                        CommandStatus.command_name == command_name,
                        CommandStatus.channel_id == channel_id,
                        CommandStatus.guild_id == guild_id,
                    )
                )
                command_status = result.scalar_one_or_none()
                if command_status:
                    command_status.enabled = enabled
                else:
                    new_status = CommandStatus(
                        command_name=command_name,
                        enabled=enabled,
                        channel_id=channel_id,
                        guild_id=guild_id,
                    )
                    session.add(new_status)

    @db_safe(default=True)
    async def get_command_status(
        self, command_name: str, channel_id: int = None, guild_id: int = None
    ) -> bool:
        """Retrieve the status of a command for both prefix and slash commands.

        Scopes:
        - channel_id set, guild_id None  -> channel-scoped row
        - channel_id None, guild_id None -> bot-wide row
        - channel_id None, guild_id set  -> per-guild serverwide row
        """
        async with self.async_sessionmaker() as session:
            query = select(CommandStatus).filter_by(
                command_name=command_name,
            )

            if channel_id:
                query = query.filter_by(channel_id=channel_id)
            else:
                query = query.filter(CommandStatus.channel_id.is_(None))

            if guild_id is None:
                query = query.filter(CommandStatus.guild_id.is_(None))
            else:
                query = query.filter(CommandStatus.guild_id == guild_id)

            result = await session.execute(query)
            status = result.scalars().first()

            return status.enabled if status else True

    @db_safe(default=[])
    async def get_disabled_commands(self) -> list:
        """Return every command currently disabled, across all scopes.

        Each entry is a dict with ``command_name`` and a human-readable
        ``reason`` describing the scope:
        - channel_id set  -> channel-scoped disable
        - guild_id set    -> server-wide disable
        - both None       -> bot-wide (global) disable
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandStatus).where(CommandStatus.enabled.is_(False))
            )
            rows = result.scalars().all()

        disabled = []
        for row in rows:
            if row.channel_id is not None:
                reason = f"disabled in channel {row.channel_id}"
            elif row.guild_id is not None:
                reason = f"disabled server-wide (guild {row.guild_id})"
            else:
                reason = "disabled globally (bot-wide)"
            disabled.append(
                {"command_name": row.command_name, "reason": reason}
            )
        return disabled

    async def clear_expired_cooldowns(self):
        """Clears expired cooldowns from the database."""
        async with self.async_sessionmaker() as session:
            await session.execute(
                delete(CommandCooldown).where(
                    CommandCooldown.cooldown_expiry < discord.utils.utcnow()
                )
            )
            await session.commit()

    async def clear_all_cooldowns(self):
        """Wipes all cooldowns from the database."""
        async with self.async_sessionmaker() as session:
            await session.execute(delete(CommandCooldown))
            await session.commit()

    async def set_cooldown(
        self, user_id: int, command_name: str, cooldown_seconds: int
    ) -> None:
        """Sets a cooldown for both prefix and slash commands for a user."""
        await self.ensure_user_identity(user_id)
        user_hash = self.hash_user_id(user_id)
        expiry_time = discord.utils.utcnow() + timedelta(seconds=cooldown_seconds)

        async with self.async_sessionmaker() as session:
            async with session.begin():
                try:
                    stmt = (
                        pg_insert(CommandCooldown)
                        .values(
                            user_id=user_hash,
                            command_name=command_name,
                            cooldown_expiry=expiry_time,
                        )
                        .on_conflict_do_update(
                            index_elements=["user_id", "command_name"],
                            set_=dict(cooldown_expiry=expiry_time),
                        )
                    )
                    await session.execute(stmt)
                except Exception as exc:
                    # If the unique constraint/index is missing, fall back to a
                    # read-modify-write so the command still works.
                    await session.rollback()
                    result = await session.execute(
                        select(CommandCooldown).filter_by(
                            user_id=user_hash, command_name=command_name
                        )
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        existing.cooldown_expiry = expiry_time
                    else:
                        session.add(
                            CommandCooldown(
                                user_id=user_hash,
                                command_name=command_name,
                                cooldown_expiry=expiry_time,
                            )
                        )

    async def get_cooldown(self, user_id: int, command_name: str) -> float:
        """Returns the remaining cooldown time in seconds. Returns 0 if expired or not found."""
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandCooldown).filter_by(
                    user_id=user_id,
                    command_name=command_name,
                )
            )
            cooldown = result.scalar_one_or_none()

            if not cooldown:
                return 0

            now = discord.utils.utcnow()
            if cooldown.cooldown_expiry <= now:
                # Cooldown expired, clean it up
                await session.delete(cooldown)
                await session.commit()
                return 0

            remaining_time = (cooldown.cooldown_expiry - now).total_seconds()
            return max(0, remaining_time)

    async def add_command_role_restriction(
        self, guild_id: int, command_name: str, role_id: int
    ):
        """Add a role restriction for a command in a guild."""
        from ..models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id,
                        CommandRoleRestriction.command_name == command_name,
                        CommandRoleRestriction.role_id == role_id,
                    )
                )
                existing = result.scalar_one_or_none()

                if not existing:
                    restriction = CommandRoleRestriction(
                        guild_id=guild_id, command_name=command_name, role_id=role_id
                    )
                    session.add(restriction)

    async def remove_command_role_restriction(
        self, guild_id: int, command_name: str, role_id: int
    ) -> bool:
        """Remove a role restriction for a command in a guild."""
        from ..models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id,
                        CommandRoleRestriction.command_name == command_name,
                        CommandRoleRestriction.role_id == role_id,
                    )
                )
                return result.rowcount > 0

    async def get_command_restrictions(self, guild_id: int):
        """Get all command restrictions for a guild."""
        from ..models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandRoleRestriction).where(
                    CommandRoleRestriction.guild_id == guild_id
                )
            )
            return result.scalars().all()

    async def clear_all_command_restrictions(self, guild_id: int) -> int:
        """Clear all command restrictions for a guild."""
        from ..models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(CommandRoleRestriction).where(
                        CommandRoleRestriction.guild_id == guild_id
                    )
                )
                return result.rowcount

    @db_safe(default=False)
    async def check_command_role_restriction(
        self, guild_id: int, command_name: str, user_roles: list
    ) -> bool:
        """Check if a user has permission to use a restricted command."""
        from ..models import CommandRoleRestriction

        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(CommandRoleRestriction).where(
                    CommandRoleRestriction.guild_id == guild_id,
                    CommandRoleRestriction.command_name == command_name,
                )
            )
            restrictions = result.scalars().all()

            if not restrictions:
                return True

            user_role_ids = []
            for role in user_roles:
                if hasattr(role, "id"):
                    user_role_ids.append(role.id)
                elif isinstance(role, int):
                    user_role_ids.append(role)

            required_role_ids = [restriction.role_id for restriction in restrictions]

            has_permission = any(
                role_id in user_role_ids for role_id in required_role_ids
            )

            return has_permission

    async def add_jtc_setup(self, guild_id: int, jtc_channel_id: int):
        """Save JTC setup for a server. Prevent duplicate setups."""
        async with self.async_sessionmaker() as session:
            existing = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            if existing.scalar_one_or_none():
                return False

            setting = JTCSettings(guild_id=guild_id, jtc_channel_id=jtc_channel_id)
            session.add(setting)
            await session.commit()
            return True

    async def get_jtc_channels(self, guild_id: int):
        """Retrieve the JTC channel IDs for a guild."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            return result.scalar_one_or_none()

    async def add_temp_channel(self, guild_id: int, user_id: int, channel_id: int):
        """Log a temporary voice channel."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            temp_channel = TempVoiceChannel(
                guild_id=guild_id, owner_id=user_id, channel_id=channel_id
            )
            session.add(temp_channel)
            await session.commit()

    async def remove_temp_channel(self, channel_id: int):
        """Removes a temporary voice channel entry from the database."""
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    delete(TempVoiceChannel).where(
                        TempVoiceChannel.channel_id == channel_id
                    )
                )
                await session.commit()

    async def get_temp_channel_owner(self, channel_id: int):
        """Retrieve the owner of a temporary voice channel."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(TempVoiceChannel).where(
                    TempVoiceChannel.channel_id == channel_id
                )
            )
            channel = result.scalar_one_or_none()
            if channel is None:
                return None
            return await self.resolve_user_hash(channel.owner_id)

    async def set_temp_channel_owner(self, channel_id: int, user_id: int):
        """Set the owner of a temporary voice channel."""
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                await session.execute(
                    update(TempVoiceChannel)
                    .where(TempVoiceChannel.channel_id == channel_id)
                    .values(owner_id=user_id)
                )
                await session.commit()

    async def store_control_panel_message(self, guild_id: int, message_id: int):
        """Stores the control panel message ID so it's not sent twice."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings).where(JTCSettings.guild_id == guild_id)
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.control_panel_message_id = message_id
                await session.commit()

    async def get_control_panel_message(self, guild_id: int):
        """Retrieves the stored control panel message ID."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(JTCSettings.control_panel_message_id).where(
                    JTCSettings.guild_id == guild_id
                )
            )
            return result.scalar_one_or_none()

    async def is_lockdown_channel(self, guild_id: int, channel_id: int) -> bool:
        """
        Checks if a channel is in the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: True if the channel is locked down, False otherwise
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                return result.scalars().first() is not None

            except Exception as e:
                print(
                    f"Error checking lockdown status for channel {channel_id} "
                    f"in guild {guild_id}: {e}"
                )
                return False

    async def add_lockdown_channel(
        self, guild_id: int, channel_id: int
    ) -> Optional[LockdownChannel]:
        """
        Adds a channel to the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: The LockdownChannel entry, or None on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                existing = result.scalars().first()
                if existing:
                    return existing

                lock = LockdownChannel(guild_id=guild_id, channel_id=channel_id)
                session.add(lock)
                await session.commit()
                return lock

            except Exception as e:
                print(
                    f"Error adding lockdown channel {channel_id} "
                    f"for guild {guild_id}: {e}"
                )
                return None

    async def remove_lockdown_channel(self, guild_id: int, channel_id: int) -> bool:
        """
        Removes a channel from the lockdown list.

        :param guild_id: Discord guild ID
        :param channel_id: Discord channel ID
        :return: True if deleted, False if not found or on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel).where(
                        LockdownChannel.guild_id == guild_id,
                        LockdownChannel.channel_id == channel_id,
                    )
                )
                existing = result.scalars().first()
                if not existing:
                    return False

                await session.delete(existing)
                await session.commit()
                return True

            except Exception as e:
                print(
                    f"Error removing lockdown channel {channel_id} "
                    f"for guild {guild_id}: {e}"
                )
                return False

    async def get_lockdown_channels(self, guild_id: int) -> List[int]:
        """
        Retrieves all channel IDs in the lockdown list for a guild.

        :param guild_id: Discord guild ID
        :return: List of channel IDs, or empty list on error
        """
        async with self.async_sessionmaker() as session:
            try:
                result = await session.execute(
                    select(LockdownChannel.channel_id)
                    .where(LockdownChannel.guild_id == guild_id)
                    .order_by(LockdownChannel.channel_id)
                )
                return result.scalars().all()

            except Exception as e:
                print(f"Error retrieving lockdown channels for guild {guild_id}: {e}")
                return []

    async def get_juul(self, guild_id: int) -> Optional[Juul]:
        """
        Read-only helper. Returns a Juul (or None) by opening its own session.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none()

    async def set_juul_holder(self, guild_id: int, user_id: int):
        """
        Pass or Steal: update holder_id (and always reset locked=False) in a single session.
        """
        await self.ensure_user_identity(user_id)
        user_id = self.hash_user_id(user_id)
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.holder_id = user_id
                    juul.locked = False
                else:
                    juul = Juul(guild_id=guild_id, holder_id=user_id)
                    session.add(juul)

    async def set_juul_lock(self, guild_id: int, locked: bool):
        """
        Lock or Unlock: simply flip the locked flag in one transaction.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.locked = locked
                else:
                    juul = Juul(guild_id=guild_id, locked=locked)
                    session.add(juul)

    async def get_juul_lock(self, guild_id: int) -> bool:
        """
        Read-only helper. Returns the locked status of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul.locked).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none() or False

    async def increment_juul_hits(self, guild_id: int):
        """
        (You can leave these “increment” helpers as they are,
        since they don’t rely on get_juul().)
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(hits=Juul.hits + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, hits=1))

    async def increment_juul_passes(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(passes=Juul.passes + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, passes=1))

    async def increment_juul_steals(self, guild_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                stmt = (
                    update(Juul)
                    .where(Juul.guild_id == guild_id)
                    .values(steals=Juul.steals + 1)
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    session.add(Juul(guild_id=guild_id, steals=1))

    async def set_juul_flavor(self, guild_id: int, flavor: str):
        """
        Set the flavor of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(Juul).where(Juul.guild_id == guild_id)
                )
                juul = result.scalar_one_or_none()

                if juul:
                    juul.flavor = flavor
                else:
                    juul = Juul(guild_id=guild_id, flavor=flavor)
                    session.add(juul)

    async def get_juul_flavor(self, guild_id: int) -> str:
        """
        Read-only helper. Returns the flavor of the Juul for a guild.
        """
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(Juul.flavor).where(Juul.guild_id == guild_id)
            )
            return result.scalar_one_or_none() or "classic"

    async def get_auto_roles(self, guild_id: int) -> list[int]:
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.auto_role_ids).where(
                    ServerSettings.guild_id == guild_id
                )
            )
            return result.scalar_one_or_none() or []

    async def add_auto_role(self, guild_id: int, role_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                current = list(settings.auto_role_ids or []) if settings else []
                if role_id not in current:
                    current.append(role_id)
                # Reassign the whole list rather than .append()'ing in place:
                # ARRAY columns don't track in-place mutations, so SQLAlchemy
                # would never mark the attribute dirty and the change wouldn't
                # persist.
                if settings:
                    settings.auto_role_ids = current
                else:
                    session.add(
                        ServerSettings(guild_id=guild_id, auto_role_ids=current)
                    )

    async def remove_auto_role(self, guild_id: int, role_id: int):
        async with self.async_sessionmaker() as session:
            async with session.begin():
                settings = await session.get(ServerSettings, guild_id)
                if settings and settings.auto_role_ids:
                    settings.auto_role_ids = [
                        r for r in settings.auto_role_ids if r != role_id
                    ]

    async def set_nuke_msg(self, guild_id: int, new_message: str):
        """Change the nuke confirmation message for a guild."""
        raise_if_url(new_message, "nuke message")
        async with self.async_sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    select(ServerSettings).where(ServerSettings.guild_id == guild_id)
                )
                settings = result.scalar_one_or_none()
                if settings:
                    settings.nuke_msg = new_message
                else:
                    settings = ServerSettings(guild_id=guild_id, nuke_msg=new_message)
                    session.add(settings)

    async def get_nuke_msg(self, guild_id: int) -> str:
        """Retrieve the nuke confirmation message for a guild."""
        async with self.async_sessionmaker() as session:
            result = await session.execute(
                select(ServerSettings.nuke_msg).where(
                    ServerSettings.guild_id == guild_id
                )
            )
            nuke_msg = result.scalar_one_or_none()
            return (
                nuke_msg
                if nuke_msg is not None
                else "Channel nuked successfully!"
            )
