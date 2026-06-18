-- Migration: hash Discord user IDs in operational tables.
--
-- This script converts every user-facing identifier column from BIGINT to a
-- deterministic HMAC-SHA256 hex string (64 characters). It also creates a
-- single `user_identities` mapping table that stores `user_hash -> user_id`
-- so the bot can resolve a hash back to a raw Discord ID when it needs to
-- interact with the Discord API (mentions, DMs, display, etc.).
--
-- Before running:
--   1. Set the secret key below to the same value you will put in the
--      USER_ID_HASH_KEY environment variable.
--   2. Run this during a maintenance window; large tables may lock.
--   3. Take a backup first.
--
-- After running:
--   1. Set USER_ID_HASH_KEY in your environment to the same key.
--   2. Restart the bot so the new models and hashing logic take effect.

\set key 'supersecretpasswordnigga'

BEGIN;

-- Cryptographic helpers.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION hash_user_id(user_id BIGINT, key TEXT)
RETURNS VARCHAR(64) AS $$
BEGIN
    RETURN encode(hmac(user_id::TEXT, key, 'sha256'), 'hex');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION hash_int_array(arr BIGINT[], key TEXT)
RETURNS VARCHAR(64)[] AS $$
DECLARE
    result VARCHAR(64)[] := ARRAY[]::VARCHAR(64)[];
    i INT;
BEGIN
    IF arr IS NULL THEN
        RETURN NULL;
    END IF;
    IF array_length(arr, 1) IS NULL THEN
        RETURN result;
    END IF;
    FOR i IN 1..array_length(arr, 1) LOOP
        result := array_append(result, hash_user_id(arr[i], key));
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;


-- Mapping table: the only place raw Discord IDs remain in the database.
CREATE TABLE IF NOT EXISTS user_identities (
    user_hash VARCHAR(64) PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE
);

-- Populate the mapping table with every distinct Discord user ID currently
-- stored anywhere in the schema. Add more UNION branches if new tables are
-- introduced later.
INSERT INTO user_identities (user_hash, user_id)
SELECT DISTINCT
    hash_user_id(user_id, :'key') AS user_hash,
    user_id
FROM (
    SELECT user_id FROM wallets WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM punishments WHERE user_id IS NOT NULL
    UNION SELECT moderator_id AS user_id FROM punishments WHERE moderator_id IS NOT NULL
    UNION SELECT user_id FROM jailed_users WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM image_mute_settings WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM command_cooldowns WHERE user_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM lastfm_users WHERE discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM lastfm_votes WHERE discord_id IS NOT NULL
    UNION SELECT user_id FROM booster_roles WHERE user_id IS NOT NULL
    UNION SELECT from_user_id AS user_id FROM transactions WHERE from_user_id IS NOT NULL
    UNION SELECT to_user_id AS user_id FROM transactions WHERE to_user_id IS NOT NULL
    UNION SELECT user_id FROM items WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM item_cooldowns WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM active_effects WHERE user_id IS NOT NULL
    UNION SELECT from_user_id AS user_id FROM trade_logs WHERE from_user_id IS NOT NULL
    UNION SELECT to_user_id AS user_id FROM trade_logs WHERE to_user_id IS NOT NULL
    UNION SELECT target_id AS user_id FROM bounties WHERE target_id IS NOT NULL
    UNION SELECT issuer_id AS user_id FROM bounties WHERE issuer_id IS NOT NULL
    UNION SELECT claimer_id AS user_id FROM bounties WHERE claimer_id IS NOT NULL
    UNION SELECT user_id FROM loans WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM loan_payments WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM jobs WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM user_role_history WHERE user_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM reputation WHERE discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM sobs WHERE discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM skulls WHERE skulls.discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM flames WHERE flames.discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM hearts WHERE hearts.discord_id IS NOT NULL
    UNION SELECT discord_id AS user_id FROM clowns WHERE clowns.discord_id IS NOT NULL
    UNION SELECT user_id FROM blacklist WHERE user_id IS NOT NULL
    UNION SELECT admin_id AS user_id FROM blacklist WHERE admin_id IS NOT NULL
    UNION SELECT user_id FROM favorite_songs WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM user_timezones WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM user_locations WHERE user_id IS NOT NULL
    UNION SELECT validator_id AS user_id FROM blocks WHERE validator_id IS NOT NULL
    UNION SELECT user_id FROM user_economic_preferences WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM heardle_game_stats WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM game_history WHERE user_id IS NOT NULL
    UNION SELECT owner_id AS user_id FROM game_sessions WHERE owner_id IS NOT NULL
    UNION SELECT user_id FROM tasks WHERE user_id IS NOT NULL
    UNION SELECT owner_id AS user_id FROM temp_voice_channels WHERE owner_id IS NOT NULL
    UNION SELECT user_id FROM user_name_history WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM crypto_assets WHERE user_id IS NOT NULL
    UNION SELECT holder_id AS user_id FROM juuls WHERE holder_id IS NOT NULL
    UNION SELECT main_user_id AS user_id FROM user_alts WHERE main_user_id IS NOT NULL
    UNION SELECT alt_user_id AS user_id FROM user_alts WHERE alt_user_id IS NOT NULL
    UNION SELECT user_id FROM suspicious_activity_log WHERE user_id IS NOT NULL
    UNION SELECT reviewed_by AS user_id FROM suspicious_activity_log WHERE reviewed_by IS NOT NULL
    UNION SELECT sender_id AS user_id FROM transfer_history WHERE sender_id IS NOT NULL
    UNION SELECT receiver_id AS user_id FROM transfer_history WHERE receiver_id IS NOT NULL
    UNION SELECT user_id FROM force_roles WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM user_vip WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM rakeback_balances WHERE user_id IS NOT NULL
    UNION SELECT user_id FROM rakeback_transactions WHERE user_id IS NOT NULL
) AS all_users
ON CONFLICT (user_hash) DO NOTHING;


-- Helper: drop any index that includes a specific column.
CREATE OR REPLACE FUNCTION drop_indexes_for_column(p_tablename TEXT, p_colname TEXT)
RETURNS void AS $$
DECLARE
    idx RECORD;
BEGIN
    FOR idx IN
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = p_tablename
          AND indexdef LIKE '%(' || p_colname || '%'
    LOOP
        EXECUTE format('DROP INDEX IF EXISTS %I', idx.indexname);
    END LOOP;
END;
$$ LANGUAGE plpgsql;


-- ========================================================================
-- Moderation tables
-- ========================================================================

ALTER TABLE punishments
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key'),
    ALTER COLUMN moderator_id TYPE VARCHAR(64) USING hash_user_id(moderator_id, :'key');

ALTER TABLE case_notes
    ALTER COLUMN moderator_id TYPE VARCHAR(64) USING hash_user_id(moderator_id, :'key');

ALTER TABLE watchdog_log
    ALTER COLUMN moderator_id TYPE VARCHAR(64) USING hash_user_id(moderator_id, :'key');

ALTER TABLE jailed_users
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE image_mute_settings
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE command_cooldowns
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');


-- ========================================================================
-- Social / LastFM tables
-- ========================================================================

ALTER TABLE lastfm_users DROP CONSTRAINT IF EXISTS lastfm_users_pkey;
ALTER TABLE lastfm_users
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE lastfm_users ADD PRIMARY KEY (discord_id);

ALTER TABLE lastfm_votes DROP CONSTRAINT IF EXISTS lastfm_votes_pkey;
DROP INDEX IF EXISTS ix_lastfm_votes_discord_id_command;
ALTER TABLE lastfm_votes
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE lastfm_votes ADD PRIMARY KEY (discord_id, command);

ALTER TABLE booster_roles
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');


-- ========================================================================
-- Economy core
-- ========================================================================

-- wallets is referenced by items and crypto_assets, so handle FKs first.
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_user_id_fkey;
ALTER TABLE crypto_assets DROP CONSTRAINT IF EXISTS crypto_assets_user_id_fkey;
ALTER TABLE wallets DROP CONSTRAINT IF EXISTS wallets_user_id_key;
ALTER TABLE wallets DROP CONSTRAINT IF EXISTS uq_wallets_user_id;

ALTER TABLE wallets
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE wallets ADD CONSTRAINT wallets_user_id_key UNIQUE (user_id);

ALTER TABLE transactions
    ALTER COLUMN from_user_id TYPE VARCHAR(64) USING hash_user_id(from_user_id, :'key'),
    ALTER COLUMN to_user_id TYPE VARCHAR(64) USING hash_user_id(to_user_id, :'key');

ALTER TABLE items
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE items ADD CONSTRAINT items_user_id_fkey FOREIGN KEY (user_id) REFERENCES wallets(user_id);

ALTER TABLE item_cooldowns DROP CONSTRAINT IF EXISTS uq_item_cooldown_user_item;
SELECT drop_indexes_for_column('item_cooldowns', 'user_id');
ALTER TABLE item_cooldowns
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE item_cooldowns ADD CONSTRAINT uq_item_cooldown_user_item UNIQUE (user_id, item_name);

SELECT drop_indexes_for_column('active_effects', 'user_id');
ALTER TABLE active_effects
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
CREATE INDEX ix_active_effects_user_expires ON active_effects(user_id, expires_at);

ALTER TABLE trade_logs
    ALTER COLUMN from_user_id TYPE VARCHAR(64) USING hash_user_id(from_user_id, :'key'),
    ALTER COLUMN to_user_id TYPE VARCHAR(64) USING hash_user_id(to_user_id, :'key');

ALTER TABLE bounties
    ALTER COLUMN target_id TYPE VARCHAR(64) USING hash_user_id(target_id, :'key'),
    ALTER COLUMN issuer_id TYPE VARCHAR(64) USING hash_user_id(issuer_id, :'key'),
    ALTER COLUMN claimer_id TYPE VARCHAR(64) USING hash_user_id(claimer_id, :'key');

ALTER TABLE loans
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE loan_payments
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_user_id_key;
ALTER TABLE jobs
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE jobs ADD CONSTRAINT jobs_user_id_key UNIQUE (user_id);


-- ========================================================================
-- Casino / games
-- ========================================================================

ALTER TABLE user_role_history
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE reputation DROP CONSTRAINT IF EXISTS reputation_pkey;
ALTER TABLE reputation
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE reputation ADD PRIMARY KEY (discord_id);

ALTER TABLE sobs DROP CONSTRAINT IF EXISTS sobs_pkey;
ALTER TABLE sobs
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE sobs ADD PRIMARY KEY (discord_id);

ALTER TABLE skulls DROP CONSTRAINT IF EXISTS skulls_pkey;
ALTER TABLE skulls
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE skulls ADD PRIMARY KEY (discord_id);

ALTER TABLE flames DROP CONSTRAINT IF EXISTS flames_pkey;
ALTER TABLE flames
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE flames ADD PRIMARY KEY (discord_id);

ALTER TABLE hearts DROP CONSTRAINT IF EXISTS hearts_pkey;
ALTER TABLE hearts
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE hearts ADD PRIMARY KEY (discord_id);

ALTER TABLE clowns DROP CONSTRAINT IF EXISTS clowns_pkey;
ALTER TABLE clowns
    ALTER COLUMN discord_id TYPE VARCHAR(64) USING hash_user_id(discord_id, :'key');
ALTER TABLE clowns ADD PRIMARY KEY (discord_id);

ALTER TABLE blacklist DROP CONSTRAINT IF EXISTS blacklist_user_id_key;
ALTER TABLE blacklist
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key'),
    ALTER COLUMN admin_id TYPE VARCHAR(64) USING hash_user_id(admin_id, :'key');
ALTER TABLE blacklist ADD CONSTRAINT blacklist_user_id_key UNIQUE (user_id);

ALTER TABLE favorite_songs
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE user_timezones DROP CONSTRAINT IF EXISTS user_timezones_pkey;
ALTER TABLE user_timezones
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE user_timezones ADD PRIMARY KEY (user_id);

ALTER TABLE user_locations DROP CONSTRAINT IF EXISTS user_locations_pkey;
ALTER TABLE user_locations
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE user_locations ADD PRIMARY KEY (user_id);

ALTER TABLE blocks
    ALTER COLUMN validator_id TYPE VARCHAR(64) USING hash_user_id(validator_id, :'key');

ALTER TABLE user_economic_preferences DROP CONSTRAINT IF EXISTS user_economic_preferences_pkey;
ALTER TABLE user_economic_preferences
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE user_economic_preferences ADD PRIMARY KEY (user_id);

ALTER TABLE heardle_game_stats DROP CONSTRAINT IF EXISTS heardle_game_stats_pkey;
ALTER TABLE heardle_game_stats
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE heardle_game_stats ADD PRIMARY KEY (user_id);

SELECT drop_indexes_for_column('game_history', 'user_id');
ALTER TABLE game_history
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
DROP INDEX IF EXISTS ix_game_history_user_created;
DROP INDEX IF EXISTS ix_game_history_hash;
CREATE INDEX ix_game_history_user_created ON game_history(user_id, created_at);
CREATE INDEX ix_game_history_hash ON game_history(hash);

ALTER TABLE game_sessions
    ALTER COLUMN owner_id TYPE VARCHAR(64) USING hash_user_id(owner_id, :'key'),
    ALTER COLUMN participants TYPE VARCHAR(64)[] USING hash_int_array(participants, :'key');

ALTER TABLE tasks
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE temp_voice_channels
    ALTER COLUMN owner_id TYPE VARCHAR(64) USING hash_user_id(owner_id, :'key');

ALTER TABLE user_name_history
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE crypto_assets
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE crypto_assets ADD CONSTRAINT crypto_assets_user_id_fkey FOREIGN KEY (user_id) REFERENCES wallets(user_id);

ALTER TABLE juuls
    ALTER COLUMN holder_id TYPE VARCHAR(64) USING hash_user_id(holder_id, :'key');

ALTER TABLE user_alts DROP CONSTRAINT IF EXISTS uix_user_alts;
SELECT drop_indexes_for_column('user_alts', 'main_user_id');
SELECT drop_indexes_for_column('user_alts', 'alt_user_id');
ALTER TABLE user_alts
    ALTER COLUMN main_user_id TYPE VARCHAR(64) USING hash_user_id(main_user_id, :'key'),
    ALTER COLUMN alt_user_id TYPE VARCHAR(64) USING hash_user_id(alt_user_id, :'key');
ALTER TABLE user_alts ADD CONSTRAINT uix_user_alts UNIQUE (main_user_id, guild_id, alt_user_id);

SELECT drop_indexes_for_column('suspicious_activity_log', 'user_id');
ALTER TABLE suspicious_activity_log
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key'),
    ALTER COLUMN related_user_ids TYPE VARCHAR(64)[] USING hash_int_array(related_user_ids, :'key'),
    ALTER COLUMN reviewed_by TYPE VARCHAR(64) USING hash_user_id(reviewed_by, :'key');
CREATE INDEX ix_suspicious_activity_user ON suspicious_activity_log(user_id);

SELECT drop_indexes_for_column('transfer_history', 'sender_id');
SELECT drop_indexes_for_column('transfer_history', 'receiver_id');
ALTER TABLE transfer_history
    ALTER COLUMN sender_id TYPE VARCHAR(64) USING hash_user_id(sender_id, :'key'),
    ALTER COLUMN receiver_id TYPE VARCHAR(64) USING hash_user_id(receiver_id, :'key');
CREATE INDEX ix_transfer_history_sender_time ON transfer_history(sender_id, created_at);
CREATE INDEX ix_transfer_history_receiver_time ON transfer_history(receiver_id, created_at);

ALTER TABLE force_roles
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');

ALTER TABLE user_vip DROP CONSTRAINT IF EXISTS user_vip_pkey;
ALTER TABLE user_vip
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE user_vip ADD PRIMARY KEY (user_id);

ALTER TABLE rakeback_balances DROP CONSTRAINT IF EXISTS rakeback_balances_pkey;
ALTER TABLE rakeback_balances
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
ALTER TABLE rakeback_balances ADD PRIMARY KEY (user_id);

SELECT drop_indexes_for_column('rakeback_transactions', 'user_id');
ALTER TABLE rakeback_transactions
    ALTER COLUMN user_id TYPE VARCHAR(64) USING hash_user_id(user_id, :'key');
CREATE INDEX ix_rakeback_user_created ON rakeback_transactions(user_id, created_at);


-- ========================================================================
-- Cleanup
-- ========================================================================

DROP FUNCTION IF EXISTS drop_indexes_for_column(TEXT, TEXT);

-- Verify user_identities was populated and operational tables have hashes.
-- A quick sanity check: the mapping table count should match distinct hashes in wallets.
-- SELECT COUNT(*) FROM user_identities;
-- SELECT COUNT(DISTINCT user_id) FROM wallets;

COMMIT;
