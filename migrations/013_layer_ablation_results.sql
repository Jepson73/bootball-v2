-- Migration: Add layer ablation results for counterfactual simulation

CREATE TABLE IF NOT EXISTS layer_ablation_results (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                   VARCHAR(50) NOT NULL,
    layer_removed            VARCHAR(50) NOT NULL,
    baseline_ev              REAL DEFAULT 0.0,
    ablated_ev              REAL DEFAULT 0.0,
    ev_delta                 REAL DEFAULT 0.0,
    baseline_calibration    REAL DEFAULT 0.0,
    ablated_calibration    REAL DEFAULT 0.0,
    calibration_delta       REAL DEFAULT 0.0,
    baseline_risk            REAL DEFAULT 0.0,
    ablated_risk            REAL DEFAULT 0.0,
    risk_delta              REAL DEFAULT 0.0,
    prediction_count        INTEGER DEFAULT 0,
    recommendation           VARCHAR(20) DEFAULT 'keep',
    recorded_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, layer_removed)
);

CREATE INDEX IF NOT EXISTS idx_layer_ablation_run ON layer_ablation_results(run_id);
CREATE INDEX IF NOT EXISTS idx_layer_ablation_layer ON layer_ablation_results(layer_removed);
CREATE INDEX IF NOT EXISTS idx_layer_ablation_rec ON layer_ablation_results(recommendation);