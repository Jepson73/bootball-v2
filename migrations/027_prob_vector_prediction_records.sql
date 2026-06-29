-- Migration 027: add h2h probability vector columns to prediction_records
-- Enables evaluate_track_a() to score h2h predictions without collapsing to a scalar.
-- Keys: prob_home = P("1"), prob_draw = P("X"), prob_away = P("2") (API-Football notation).
-- NULL for binary markets (btts, ou25, ou15).

ALTER TABLE prediction_records ADD COLUMN prob_home REAL;
ALTER TABLE prediction_records ADD COLUMN prob_draw REAL;
ALTER TABLE prediction_records ADD COLUMN prob_away REAL;
