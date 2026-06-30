-- Add player-earned karma columns to the existing reputation table.
--
-- Run this before starting the updated bot if your deployment does not rely
-- on SQLAlchemy create_all() to add columns automatically.
--
-- All new integer columns default to 0 so existing rows stay valid.

BEGIN;

ALTER TABLE reputation
    ADD COLUMN IF NOT EXISTS karma INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS karma_earned_today INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_karma_date DATE,
    ADD COLUMN IF NOT EXISTS good_reps_received INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS bad_reps_received INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reps_given_today INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_rep_date DATE,
    ADD COLUMN IF NOT EXISTS last_rep_targets JSONB NOT NULL DEFAULT '[]';

-- Optional index for fast karma leaderboards on large servers.
CREATE INDEX IF NOT EXISTS idx_reputation_karma ON reputation(karma DESC);

COMMIT;
