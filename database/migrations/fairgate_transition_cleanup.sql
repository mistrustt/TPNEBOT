-- FairGate transition cleanup migration
-- Run once after all casino game commands have been switched to FairGate.
--
-- WARNING: This permanently deletes every legacy local-fairness game/session
-- record and reconfigures the schema for FairGate-only operation. Back up the
-- database before running this migration.

-- 1) Remove all game history resolved by the deprecated local fairness system.
DELETE FROM game_history WHERE provider = 'local';

-- 2) Remove active and historical game sessions that pre-date FairGate.
--    game_session_events are cascade-deleted if the FK is set up that way;
--    delete explicitly to be safe.
DELETE FROM game_session_events;
DELETE FROM game_sessions;

-- 3) Reset wallet server_seed columns. They are no longer used for RNG now
--    that FairGate supplies app-level seeds. client_seed and nonce are kept
--    because users still rotate their client seed and the wallet nonce is
--    reused as the FairGate nonce counter.
UPDATE wallets
   SET server_seed = NULL,
       previous_server_seed = NULL,
       seed_rotated_at = NULL;

-- 4) Change the default provider for new game_history rows to FairGate.
ALTER TABLE game_history
    ALTER COLUMN provider SET DEFAULT 'fairgate';

-- 5) Update any remaining rows that still have the old default.
UPDATE game_history SET provider = 'fairgate' WHERE provider IS NULL OR provider = '';
