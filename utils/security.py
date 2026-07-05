"""Deterministic one-way hashing for Discord user IDs and location encryption."""

import hashlib
import hmac
import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger("discord_bot")
_warned_missing_key = False
_warned_missing_stats_salt = False


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


def hash_user_id_analytics(user_id: int) -> str:
    """Return a deterministic HMAC-SHA256 hex hash for analytics user IDs.

    This uses ``STATS_SALT`` as the HMAC key, which is intentionally separate
    from ``USER_ID_HASH_KEY`` so analytics hashes cannot be joined back to
    operational records or the ``user_identities`` mapping table. The result
    is still deterministic within a deployment, allowing daily unique-user
    counts without exposing the underlying Discord ID.
    """
    global _warned_missing_stats_salt
    salt = os.getenv("STATS_SALT", "")
    if not salt and not _warned_missing_stats_salt:
        logger.warning(
            "STATS_SALT not set; analytics user hashes are unsalted and easily "
            "reversible by anyone who can guess the user ID. Set STATS_SALT in "
            "production."
        )
        _warned_missing_stats_salt = True
    key = salt.encode("utf-8")
    return hmac.new(key, str(int(user_id)).encode("utf-8"), hashlib.sha256).hexdigest()


_location_fernet: Fernet | None = None


def _get_location_fernet() -> Fernet:
    """Return a Fernet instance configured from LOCATION_ENCRYPTION_KEY."""
    key = os.getenv("LOCATION_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "LOCATION_ENCRYPTION_KEY is not set. "
            "Store a Fernet key in Infisical under LOCATION_ENCRYPTION_KEY."
        )
    try:
        return Fernet(key)
    except ValueError as e:
        raise RuntimeError(
            "LOCATION_ENCRYPTION_KEY is not a valid Fernet key. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from e


def get_location_fernet() -> Fernet:
    """Return a cached Fernet instance for location encryption."""
    global _location_fernet
    if _location_fernet is None:
        _location_fernet = _get_location_fernet()
    return _location_fernet


def encrypt_location(location: str) -> str:
    """Encrypt a location string for storage."""
    return get_location_fernet().encrypt(location.encode("utf-8")).decode("ascii")


def decrypt_location(ciphertext: str) -> str:
    """Decrypt a stored location string."""
    return get_location_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
