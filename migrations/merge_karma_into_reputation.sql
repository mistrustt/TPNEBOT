-- Merge karma and reputation into a single score.
--
-- The `reputation` column becomes the canonical social score.
-- Karma-specific columns are dropped and replaced with reputation-earned daily cap columns.

BEGIN;

-- Drop karma-specific columns; the single score lives in `reputation`.
ALTER TABLE reputation DROP COLUMN IF EXISTS karma;
ALTER TABLE reputation DROP COLUMN IF EXISTS karma_earned_today;
ALTER TABLE reputation DROP COLUMN IF EXISTS last_karma_date;

-- Add daily reputation-earned cap tracking for player activity.
ALTER TABLE reputation ADD COLUMN IF NOT EXISTS rep_earned_today INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reputation ADD COLUMN IF NOT EXISTS last_rep_earned_date DATE;

-- Optional index for reputation leaderboards.
CREATE INDEX IF NOT EXISTS idx_reputation_score ON reputation(reputation DESC);

COMMIT;
