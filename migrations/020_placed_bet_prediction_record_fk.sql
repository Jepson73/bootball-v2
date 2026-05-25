-- Migration 020: add prediction_record_id FK to placed_bets
-- Links each placed bet back to the originating prediction_records row so
-- bet outcomes can be attributed to the exact prediction that triggered them.
-- Nullable: existing rows and bets from the legacy auto_bet path have no link.

ALTER TABLE placed_bets
    ADD COLUMN prediction_record_id INTEGER
        REFERENCES prediction_records(id);

CREATE INDEX IF NOT EXISTS ix_placed_bets_prediction_record_id
    ON placed_bets (prediction_record_id);
