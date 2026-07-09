"""Deterministic one-way hashing for Discord user IDs and location encryption."""

from typing import Any, Dict, Iterable, Optional
import hashlib
import hmac
import logging
import os
import re

from cryptography.fernet import Fernet

logger = logging.getLogger("discord.client")
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

def is_hash(value: Any) -> bool:
    """Return True if a stored user ID value is a HMAC-SHA256 hex hash."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


async def resolve_id(database, value: Any) -> Optional[int]:
    """Resolve a stored user ID to a raw Discord ID when it is a hash."""
    if value is None or isinstance(value, int):
        return value
    if is_hash(value):
        return await database.resolve_user_hash(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def resolve_ids(database, values: Iterable[Any]) -> Dict[Any, Optional[int]]:
    """Batch-resolve stored user IDs, leaving raw IDs unchanged."""
    if not values:
        return {}
    unique = list(dict.fromkeys(v for v in values if v is not None))
    hashes = [v for v in unique if is_hash(v)]
    resolved = await database.resolve_user_hashes(hashes) if hashes else {}
    mapping: Dict[Any, Optional[int]] = {}
    for v in unique:
        if isinstance(v, int):
            mapping[v] = v
        elif is_hash(v):
            mapping[v] = resolved.get(v)
        else:
            try:
                mapping[v] = int(v)
            except (TypeError, ValueError):
                mapping[v] = None
    return mapping

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


# Common URL / invite patterns used to keep user-input fields free of links.
# This is intentionally broad: any URI scheme (http://, https://, discord://,
# tg://, steam://, etc.), discord.gg invites, markdown links, and www. domains
# are rejected to prevent in-app and cross-app redirect attacks.
_URL_SCHEME_RE = re.compile(
    r"[a-zA-Z][a-zA-Z0-9+.-]*://", re.IGNORECASE
)
_DISCORD_INVITE_RE = re.compile(
    r"(?:discord(?:\.com/invite|\.gg|app\.com/invite|\.gg/invite)|gg)/[a-zA-Z0-9-]+",
    re.IGNORECASE,
)
_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(([^)]+)\)", re.IGNORECASE
)
_WWW_RE = re.compile(
    r"(?:^|\s)www\.[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/\S*)?",
    re.IGNORECASE,
)


def contains_url(text: str | None) -> bool:
    """Return True if ``text`` appears to contain a URL or invite link."""
    if not text or not isinstance(text, str):
        return False
    if _URL_SCHEME_RE.search(text):
        return True
    if _DISCORD_INVITE_RE.search(text):
        return True
    if _MARKDOWN_LINK_RE.search(text):
        return True
    if _WWW_RE.search(text):
        return True
    return False


def raise_if_url(text: str | None, field_name: str = "input") -> None:
    """Raise ValueError if ``text`` contains a URL or invite link."""
    if contains_url(text):
        raise ValueError(
            f"URLs are not allowed in {field_name}. Please remove any links, "
            f"invites, or website addresses and try again."
        )
