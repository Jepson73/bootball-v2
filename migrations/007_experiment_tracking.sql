-- Migration: Add experiment tracking infrastructure
-- Run this script to enable experiment tracking

-- 1. Create experiment_runs table
CREATE TABLE IF NOT EXISTS experiment_runs (
    id                      INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                  VARCHAR(36) NOT NULL UNIQUE,
    mode                    VARCHAR(20) NOT NULL,
    start_timestamp         DATETIME NOT NULL,
    end_timestamp           DATETIME,
    model_versions_json     TEXT,
    calibrator_versions_json TEXT,
    feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0',
    config_hash             VARCHAR(16) NOT NULL,
    total_predictions       INTEGER DEFAULT 0,
    total_bets              INTEGER DEFAULT 0,
    bankroll_snapshot       FLOAT,
    final_metrics_json      TEXT,
    status                  VARCHAR(20) DEFAULT 'active'
);

-- 2. Add indexes to experiment_runs
CREATE INDEX IF NOT EXISTS idx_exp_run_id ON experiment_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_exp_mode ON experiment_runs(mode);
CREATE INDEX IF NOT EXISTS idx_exp_status ON experiment_runs(status);

-- 3. Add columns to prediction_records
ALTER TABLE prediction_records ADD COLUMN run_id VARCHAR(36);
ALTER TABLE prediction_records ADD COLUMN calibration_version_id VARCHAR(50);
ALTER TABLE prediction_records ADD COLUMN feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0';

-- 4. Add columns to placed_bets
ALTER TABLE placed_bets ADD COLUMN run_id VARCHAR(36);
ALTER TABLE placed_bets ADD COLUMN calibration_version_id VARCHAR(50);
ALTER TABLE placed_bets ADD COLUMN feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0';

-- 5. Add indexes for run_id lookups
CREATE INDEX IF NOT EXISTS idx_pred_run_id ON prediction_records(run_id);
CREATE INDEX IF NOT EXISTS idx_bet_run_id ON placed_bets(run_id);