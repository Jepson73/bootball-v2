-- Migration: Add layer performance timeseries tracking
-- Enables cross-run layer intelligence and system evolution analytics

-- 1. Create layer_performance_timeseries table
CREATE TABLE IF NOT EXISTS layer_performance_timeseries (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                   VARCHAR(36) NOT NULL,
    layer_name               VARCHAR(30) NOT NULL,
    
    -- EV contribution metrics
    ev_contribution           FLOAT DEFAULT 0.0,
    ev_contribution_pct       FLOAT DEFAULT 0.0,
    cumulative_ev             FLOAT DEFAULT 0.0,
    
    -- ROI contribution metrics  
    roi_contribution          FLOAT DEFAULT 0.0,
    roi_contribution_pct      FLOAT DEFAULT 0.0,
    
    -- Variance metrics
    variance_contribution     FLOAT DEFAULT 0.0,
    variance_reduction_pct    FLOAT DEFAULT 0.0,
    
    -- Stability metrics
    stability_score           FLOAT DEFAULT 0.0,
    activation_frequency      FLOAT DEFAULT 0.0,
    activation_count          INTEGER DEFAULT 0,
    
    -- Rejection metrics (for risk layer)
    rejection_impact          FLOAT DEFAULT 0.0,
    rejection_rate            FLOAT DEFAULT 0.0,
    bet_acceptance_rate       FLOAT DEFAULT 0.0,
    
    -- Calibration quality
    calibration_improvement   FLOAT DEFAULT 0.0,
    ece_delta                 FLOAT DEFAULT 0.0,
    brier_delta               FLOAT DEFAULT 0.0,
    
    -- Regime sensitivity
    regime                    VARCHAR(20) DEFAULT 'normal',
    regime_sensitivity         FLOAT DEFAULT 0.0,
    
    -- Prediction metrics
    predictions_affected      INTEGER DEFAULT 0,
    decisions_changed         INTEGER DEFAULT 0,
    
    -- Context
    market                    VARCHAR(20),
    league_id                 INTEGER,
    
    -- Timestamps
    run_start                 DATETIME,
    run_end                   DATETIME,
    created_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(run_id, layer_name, market)
);

CREATE INDEX IF NOT EXISTS idx_layer_ts_run ON layer_performance_timeseries(run_id);
CREATE INDEX IF NOT EXISTS idx_layer_ts_layer ON layer_performance_timeseries(layer_name);
CREATE INDEX IF NOT EXISTS idx_layer_ts_market ON layer_performance_timeseries(market);
CREATE INDEX IF NOT EXISTS idx_layer_ts_regime ON layer_performance_timeseries(regime);

-- 2. Create layer_interactions table
CREATE TABLE IF NOT EXISTS layer_interactions (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                   VARCHAR(36) NOT NULL,
    layer_a                  VARCHAR(30) NOT NULL,
    layer_b                  VARCHAR(30) NOT NULL,
    
    -- Interaction metrics
    correlation              FLOAT DEFAULT 0.0,
    interaction_type          VARCHAR(20),  -- reinforcing, canceling, unstable
    joint_activation_rate    FLOAT DEFAULT 0.0,
    ev_synergy               FLOAT DEFAULT 0.0,
    
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(run_id, layer_a, layer_b)
);

CREATE INDEX IF NOT EXISTS idx_layer_int_run ON layer_interactions(run_id);
CREATE INDEX IF NOT EXISTS idx_layer_int_pair ON layer_interactions(layer_a, layer_b);

-- 3. Create system_insights table
CREATE TABLE IF NOT EXISTS system_insights (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    insight_type             VARCHAR(30) NOT NULL,
    category                 VARCHAR(30) NOT NULL,  -- stability, utility, fragility, redundancy
    layer_name               VARCHAR(30),
    
    insight_text             TEXT NOT NULL,
    confidence               FLOAT DEFAULT 0.0,
    supporting_runs          INTEGER DEFAULT 0,
    
    -- Trend data
    trend_direction          VARCHAR(10),  -- improving, degrading, stable
    trend_magnitude          FLOAT DEFAULT 0.0,
    
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at               DATETIME
);

CREATE INDEX IF NOT EXISTS idx_insight_type ON system_insights(insight_type);
CREATE INDEX IF NOT EXISTS idx_insight_category ON system_insights(category);
CREATE INDEX IF NOT EXISTS idx_insight_layer ON system_insights(layer_name);