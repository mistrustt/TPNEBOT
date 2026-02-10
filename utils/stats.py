import hashlib
import logging
import os

logger = logging.getLogger("discord_bot")
_warned_missing_salt = False


def hash_user_id(user_id: int) -> str:
    global _warned_missing_salt
    salt = os.getenv("STATS_SALT", "")
    if not salt and not _warned_missing_salt:
        logger.warning("STATS_SALT not set; user hashes are unsalted.")
        _warned_missing_salt = True
    payload = f"{salt}:{user_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
