-- Migration 030: persistent dedup for the live-drift calibration monitor,
-- and an audit log for Elo rebuild invocations.
--
-- Phase 28. calibration_drift_state replaces the in-memory
-- `_calibration_seen_bet_ids` dedup (Phase 27b found it reset on every
-- process restart, replaying the same 25 frozen PlacedBet rows as "new"
-- forever). One row per market. last_seen_prediction_id is a PredictionRecord
-- high-water mark so a restart never reprocesses an already-consumed
-- settlement.
--
-- elo_rebuild_log closes the governance gap from Phase 27b: the club-pool
-- Elo rebuild that ran inside the corruption window (latest-fixture ceiling
-- 2026-07-01 02:30) had no record of who invoked it or when. Every call to
-- update_all_ratings() now writes one row here.

CREATE TABLE IF NOT EXISTS calibration_drift_state (
    market                  TEXT PRIMARY KEY,
    last_seen_prediction_id INTEGER NOT NULL DEFAULT 0,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS elo_rebuild_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    pool                  TEXT NOT NULL,
    invoked_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    invoked_by            TEXT,
    fixtures_processed    INTEGER,
    latest_fixture_ceiling DATETIME
);
