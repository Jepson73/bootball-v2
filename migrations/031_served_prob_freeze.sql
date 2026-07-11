-- Migration 031: freeze the served probability at kickoff.
--
-- Phase 33b acceptance found a settlement display discontinuity: served_prob
-- (the live-recalibrated number Phase 33 Task 4 shows on predictions_v2 /
-- explorer_v2 for unsettled rows) was never persisted anywhere. Once a row
-- settled, the explorer fell back to raw our_prob -- so a fixture shown at
-- "61%" pre-match could display "94% X" once settled, with no record that
-- 61% was ever what a user actually saw. served_prob is recomputed live
-- against TODAY's active calibration on every request, so the number a user
-- saw pre-match is unrecoverable once time (and any intervening refit) has
-- passed -- there is no query that can reconstruct it after the fact.
--
-- served_prob / served_calibration_version are written once, at the moment a
-- fixture is first observed live (see src/settlement.py's
-- update_pending_fixture_scores(), with settle_predictions() as a fallback
-- for rows that somehow reach settlement without ever being caught live) --
-- see v2/db_v2.py::freeze_served_probs_for_fixture(). NULL for any row
-- settled before this migration (settled history predating this phase has no
-- served_prob to recover, matching this project's existing era-boundary
-- convention of not rewriting settled history).

ALTER TABLE prediction_records ADD COLUMN served_prob REAL;
ALTER TABLE prediction_records ADD COLUMN served_calibration_version TEXT;
