-- Migration: Add layer governance metrics tracking

CREATE TABLE IF NOT EXISTS layer_governance_metrics (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    layer_name               VARCHAR(50) NOT NULL,
    run_id                   VARCHAR(50) NOT NULL,
    ev_contribution          REAL DEFAULT 0.0,
    roi_contribution         REAL DEFAULT 0.0,
    stability_score          REAL DEFAULT 0.0,
    fragility_score          REAL DEFAULT 0.0,
    redundancy_index         REAL DEFAULT 0.0,
    failure_correlation      REAL DEFAULT 0.0,
    convergence_score        REAL DEFAULT 0.0,
    promotion_recommended    INTEGER DEFAULT 0,
    demotion_recommended    INTEGER DEFAULT 0,
    recorded_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(layer_name, run_id)
);

CREATE INDEX IF NOT EXISTS idx_layer_gov_metrics_layer ON layer_governance_metrics(layer_name);
CREATE INDEX IF NOT EXISTS idx_layer_gov_metrics_run ON layer_governance_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_layer_gov_metrics_promotion ON layer_governance_metrics(promotion_recommended);
CREATE INDEX IF NOT EXISTS idx_layer_gov_metrics_demotion ON layer_governance_metrics(demotion_recommended);