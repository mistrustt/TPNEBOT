-- Migration: encrypt location data at rest and remove exact coordinates.
--
-- This migration converts the `user_locations` table to store only an
-- encrypted location string. The old plaintext `location` column and the
-- exact `lat`/`lon` columns are removed.
--
-- Before running:
--   1. Deploy the code that reads/writes `location_encrypted`.
--   2. Set LOCATION_ENCRYPTION_KEY in your environment / Infisical.
--   3. Take a backup first.
--
-- After running:
--   1. Existing location rows are cleared; users must re-run `!setloc`.
--      We intentionally do NOT re-encrypt old plaintext values because
--      they may contain street-level precision and the bot now stores
--      coarser coordinates.

BEGIN;

ALTER TABLE user_locations
ADD COLUMN IF NOT EXISTS location_encrypted TEXT;

UPDATE user_locations SET location = NULL;

ALTER TABLE user_locations DROP COLUMN IF EXISTS lat;
ALTER TABLE user_locations DROP COLUMN IF EXISTS lon;

ALTER TABLE user_locations DROP COLUMN IF EXISTS location;

COMMIT;
