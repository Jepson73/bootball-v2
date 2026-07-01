-- Migration 028: data_context on prediction_records + pool column on elo_ratings
--
-- data_context taxonomy (all values defined here):
--   full         -- standings-based prediction (existing records)
--   elo_both     -- club Elo, both teams have real ratings
--   elo_partial  -- one team unrated (default-1500)
--   flat_prior   -- no meaningful ratings (uniform H43/D27/A30 prior)
--   national_elo -- RESERVED for Part B
--
-- pool on elo_ratings enables club/national namespace separation.

ALTER TABLE prediction_records ADD COLUMN data_context TEXT;
UPDATE prediction_records SET data_context = 'full';

ALTER TABLE elo_ratings ADD COLUMN pool TEXT NOT NULL DEFAULT 'club';
