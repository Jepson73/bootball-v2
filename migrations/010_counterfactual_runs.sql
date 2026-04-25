-- Migration: Add counterfactual runs table for layer ablation analysis

CREATE TABLE IF NOT EXISTS counterfactual_runs (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                   VARCHAR(36) NOT NULL,
    ablation_config          TEXT NOT NULL,
    
    ev                       FLOAT DEFAULT 0.0,
    roi                      FLOAT DEFAULT 0.0,
    calibration_error        FLOAT DEFAULT 0.0,
    acceptance_rate          FLOAT DEFAULT 0.0,
    
    ev_delta                 FLOAT DEFAULT 0.0,
    roi_delta                FLOAT DEFAULT 0.0,
    stability_delta          FLOAT DEFAULT 0.0,
    
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(run_id, ablation_config)
);

CREATE INDEX IF NOT EXISTS idx_cf_run ON counterfactual_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_cf_config ON counterfactual_runs(ablation_config);