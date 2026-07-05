-- Remove the unused booster_role_id column from server_settings.
-- The booster role feature uses the booster_roles table instead.
ALTER TABLE server_settings DROP COLUMN IF EXISTS booster_role_id;
