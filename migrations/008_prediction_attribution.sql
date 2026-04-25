-- Migration: Add prediction attribution layer
-- Enables causal decomposition of prediction performance by system layer

-- 1. Create prediction_attribution table
CREATE TABLE IF NOT EXISTS prediction_attribution (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    prediction_id            INTEGER NOT NULL REFERENCES prediction_records(id),
    run_id                  VARCHAR(36),
    fixture_id              INTEGER NOT NULL REFERENCES fixtures(id),
    market                  VARCHAR(20) NOT NULL,
    
    -- Model Layer (raw model output)
    model_prob_raw          FLOAT NOT NULL,
    
    -- Calibration Layer (after isotonic calibration)
    calibration_delta       FLOAT DEFAULT 0.0,
    calibration_prob        FLOAT,
    
    -- League/Feature Layer (after league normalization)
    league_delta             FLOAT DEFAULT 0.0,
    league_adjusted_prob     FLOAT,
    
    -- Latent State Layer (environmental adjustment)
    latent_delta             FLOAT DEFAULT 0.0,
    latent_adjusted_prob     FLOAT,
    
    -- Drift Layer (time/regime adaptation)
    drift_delta              FLOAT DEFAULT 0.0,
    drift_adjusted_prob      FLOAT,
    
    -- Risk Layer (bet filtering decision)
    risk_delta               FLOAT DEFAULT 0.0,
    risk_filtered            INTEGER DEFAULT 0,
    final_prob              FLOAT,
    
    -- Layer contributions to EV
    model_ev_contribution    FLOAT DEFAULT 0.0,
    calibration_ev_contribution FLOAT DEFAULT 0.0,
    league_ev_contribution  FLOAT DEFAULT 0.0,
    latent_ev_contribution   FLOAT DEFAULT 0.0,
    drift_ev_contribution    FLOAT DEFAULT 0.0,
    risk_ev_contribution    FLOAT DEFAULT 0.0,
    
    -- Layer contributions to decision
    model_decision          VARCHAR(10),
    calibration_decision    VARCHAR(10),
    league_decision         VARCHAR(10),
    latent_decision        VARCHAR(10),
    drift_decision          VARCHAR(10),
    final_decision          VARCHAR(10),
    
    -- Outcome tracking
    actual_outcome          VARCHAR(10),
    settled                 INTEGER DEFAULT 0,
    won                    INTEGER,
    
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(prediction_id)
);

CREATE INDEX IF NOT EXISTS idx_attr_prediction ON prediction_attribution(prediction_id);
CREATE INDEX IF NOT EXISTS idx_attr_run ON prediction_attribution(run_id);
CREATE INDEX IF NOT EXISTS idx_attr_market ON prediction_attribution(market);
CREATE INDEX IF NOT EXISTS idx_attr_settled ON prediction_attribution(settled);

-- 2. Create experiment_attribution_summary table (per-run aggregation)
CREATE TABLE IF NOT EXISTS experiment_attribution_summary (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                   VARCHAR(36) NOT NULL REFERENCES experiment_runs(run_id),
    market                   VARCHAR(20),
    
    -- Count metrics
    total_predictions        INTEGER DEFAULT 0,
    settled_predictions     INTEGER DEFAULT 0,
    
    -- Model layer stats
    model_avg_prob           FLOAT DEFAULT 0.0,
    model_avg_ev             FLOAT DEFAULT 0.0,
    model_accuracy           FLOAT DEFAULT 0.0,
    
    -- Calibration layer stats
    calibration_total_delta FLOAT DEFAULT 0.0,
    calibration_avg_delta   FLOAT DEFAULT 0.0,
    calibration_ev_impact    FLOAT DEFAULT 0.0,
    calibration_accuracy_change FLOAT DEFAULT 0.0,
    
    -- League layer stats
    league_total_delta       FLOAT DEFAULT 0.0,
    league_avg_delta        FLOAT DEFAULT 0.0,
    league_ev_impact         FLOAT DEFAULT 0.0,
    league_accuracy_change   FLOAT DEFAULT 0.0,
    
    -- Latent state layer stats
    latent_total_delta       FLOAT DEFAULT 0.0,
    latent_avg_delta        FLOAT DEFAULT 0.0,
    latent_ev_impact        FLOAT DEFAULT 0.0,
    latent_accuracy_change  FLOAT DEFAULT 0.0,
    
    -- Drift layer stats
    drift_total_delta        FLOAT DEFAULT 0.0,
    drift_avg_delta         FLOAT DEFAULT 0.0,
    drift_ev_impact          FLOAT DEFAULT 0.0,
    drift_accuracy_change   FLOAT DEFAULT 0.0,
    
    -- Risk layer stats
    risk_total_delta         FLOAT DEFAULT 0.0,
    risk_filtered_count      INTEGER DEFAULT 0,
    risk_bet_acceptance_rate FLOAT DEFAULT 0.0,
    risk_ev_impact           FLOAT DEFAULT 0.0,
    risk_accuracy_change     FLOAT DEFAULT 0.0,
    
    -- Variance metrics
    model_variance           FLOAT DEFAULT 0.0,
    calibration_variance     FLOAT DEFAULT 0.0,
    final_variance           FLOAT DEFAULT 0.0,
    variance_reduction_pct   FLOAT DEFAULT 0.0,
    
    -- Stability metrics
    accuracy_stability       FLOAT DEFAULT 0.0,
    ev_stability            FLOAT DEFAULT 0.0,
    
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(run_id, market)
);

CREATE INDEX IF NOT EXISTS idx_attr_sum_run ON experiment_attribution_summary(run_id);
CREATE INDEX IF NOT EXISTS idx_attr_sum_market ON experiment_attribution_summary(market);