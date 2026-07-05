"""One-way hashing for analytics user IDs.

This module deliberately uses a different key than ``utils.security`` so
analytics hashes cannot be resolved back to a Discord user via the
``user_identities`` mapping table. The result is deterministic within a
single deployment, which lets us count daily unique users, but it is not
reversible without the secret ``STATS_SALT``.
"""

import hashlib
import hmac
import logging
import os

logger = logging.getLogger("discord_bot")
_warned_missing_salt = False


def hash_user_id(user_id: int) -> str:
    """Return a deterministic HMAC-SHA256 hex hash of a Discord user ID.

    Uses ``STATS_SALT`` as the HMAC key. This hash is intentionally separate
    from the operational hash in ``utils.security`` so analytics data stays
    anonymous and cannot be joined to ``user_identities``.
    """
    global _warned_missing_salt
    salt = os.getenv("STATS_SALT", "")
    if not salt and not _warned_missing_salt:
        logger.warning(
            "STATS_SALT not set; analytics user hashes are unsalted and easily "
            "reversible by anyone who can guess the user ID. Set STATS_SALT in "
            "production."
        )
        _warned_missing_salt = True
    key = salt.encode("utf-8")
    return hmac.new(key, str(int(user_id)).encode("utf-8"), hashlib.sha256).hexdigest()
