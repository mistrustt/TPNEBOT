-- Track whether a game was resolved locally or via FairGate.
-- 'local'  -> utils/fairness.py + per-wallet server_seed
-- 'fairgate' -> FairGate backend with sha256_tag algorithm
ALTER TABLE game_history
    ADD COLUMN provider VARCHAR(16) NOT NULL DEFAULT 'local';
