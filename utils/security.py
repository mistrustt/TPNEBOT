"""Deterministic one-way hashing for Discord user IDs."""

import hashlib
import hmac
import logging
import os

logger = logging.getLogger("discord_bot")
_warned_missing_key = False


def _get_hash_key() -> bytes:
    """Return the HMAC key from the environment.

    ``USER_ID_HASH_KEY`` is preferred. ``STATS_SALT`` is accepted as a
    fallback so existing deployments do not break, but a dedicated key is
    strongly recommended.
    """
    global _warned_missing_key
    key = os.getenv("USER_ID_HASH_KEY")
    if not key:
        if not _warned_missing_key:
            logger.error(
                "USER_ID_HASH_KEY is not set; "
                "user ID hashing is insecure. Set USER_ID_HASH_KEY before "
                "running in production."
            )
            _warned_missing_key = True
        # Return a zero-length key for development continuity. In production
        # this must be configured to a high-entropy secret.
        return b""
    return key.encode("utf-8")


def hash_user_id(user_id: int) -> str:
    """Return a deterministic HMAC-SHA256 hex hash of a Discord user ID.

    The same raw ID always produces the same hash, so exact-match database
    lookups and joins continue to work after migrating columns from raw IDs
    to hashes.
    """
    key = _get_hash_key()
    return hmac.new(key, str(int(user_id)).encode("utf-8"), hashlib.sha256).hexdigest()


def hash_user_ids(user_ids: list[int]) -> list[str]:
    """Hash a sequence of Discord user IDs."""
    return [hash_user_id(uid) for uid in user_ids]
