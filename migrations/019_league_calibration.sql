-- Migration 019: add league_calibrations table
-- Stores per-(market, league_id) Platt-scaling calibration parameters.
-- version_label format: "v{model_number:02d}_c{calibration_number:02d}_l{league_id:04d}"
-- brier_improvement > 0 means league-specific cal beats global.

CREATE TABLE IF NOT EXISTS league_calibrations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market              VARCHAR(20)  NOT NULL,
    league_id           INTEGER      NOT NULL REFERENCES leagues(id),
    version_label       VARCHAR(30)  NOT NULL,

    slope               FLOAT        NOT NULL DEFAULT 1.0,
    intercept           FLOAT        NOT NULL DEFAULT 0.0,

    brier_score         FLOAT,
    brier_score_global  FLOAT,
    brier_improvement   FLOAT,

    sample_size         INTEGER      NOT NULL DEFAULT 0,
    is_active           BOOLEAN      NOT NULL DEFAULT 0,

    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_league_cal_version UNIQUE (market, league_id, version_label)
);

CREATE INDEX IF NOT EXISTS ix_league_cal_market_league
    ON league_calibrations (market, league_id, is_active);
