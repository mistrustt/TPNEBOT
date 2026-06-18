-- Follow-up: normalize treasury sentinel values after secure_user_ids.sql.
--
-- The economy code historically uses raw user ID 0 to mean "treasury/system".
-- After hashing, new code stores hash(0) in user ID columns. Any rows that were
-- created before the migration will have the literal string '0' instead.
-- This script converts those legacy '0' strings to the same hash(0) sentinel
-- so the data is uniform.
--
-- Run this after secure_user_ids.sql and after updating the bot code, but
-- BEFORE starting the bot if you want a completely clean state.

\set key '__CHANGE_THIS_TO_A_LONG_RANDOM_SECRET__'

BEGIN;

UPDATE transactions
SET from_user_id = encode(hmac('0', :'key', 'sha256'), 'hex')
WHERE from_user_id = '0';

UPDATE transactions
SET to_user_id = encode(hmac('0', :'key', 'sha256'), 'hex')
WHERE to_user_id = '0';

UPDATE transfer_history
SET sender_id = encode(hmac('0', :'key', 'sha256'), 'hex')
WHERE sender_id = '0';

UPDATE transfer_history
SET receiver_id = encode(hmac('0', :'key', 'sha256'), 'hex')
WHERE receiver_id = '0';

-- If a wallet with user_id '0' exists from the old schema, remove it.
-- The treasury is represented by the supply table, not a wallet row.
DELETE FROM wallets WHERE user_id = '0';

COMMIT;
