-- Migration 026: odds_snapshots table for forward collection
-- Stores one row per (fixture, bookmaker, market, capture_time) with no UniqueConstraint.
-- FixtureOdds is unchanged (this is an additive new table).

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id      INTEGER NOT NULL REFERENCES fixtures(id),
    bookmaker_id    INTEGER,
    bookmaker_name  TEXT    NOT NULL,
    market_type     TEXT    NOT NULL,
    captured_at     DATETIME NOT NULL,
    odd_home        REAL,
    odd_draw        REAL,
    odd_away        REAL,
    odd_over        REAL,
    odd_under       REAL,
    odd_btts_yes    REAL,
    odd_btts_no     REAL
);

CREATE INDEX IF NOT EXISTS ix_odds_snapshots_fixture_id  ON odds_snapshots(fixture_id);
CREATE INDEX IF NOT EXISTS ix_odds_snapshots_captured_at ON odds_snapshots(captured_at);
