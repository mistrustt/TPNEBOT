-- Drop tables whose models were removed from database/models.py.
-- These settings are now stored in server_settings (jail/report/watchdog channels and flags).
DROP TABLE IF EXISTS watchdog_settings;
DROP TABLE IF EXISTS jail_settings;
DROP TABLE IF EXISTS report_settings;
