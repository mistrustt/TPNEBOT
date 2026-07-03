-- Track whether a game was resolved via FairGate.
-- 'fairgate' is the only supported provider after the FairGate transition.
ALTER TABLE game_history
    ADD COLUMN provider VARCHAR(16) NOT NULL DEFAULT 'fairgate';
