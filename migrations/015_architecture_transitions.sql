-- Migration: Add architecture transition history

CREATE TABLE IF NOT EXISTS architecture_transitions (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    from_architecture        VARCHAR(50),
    to_architecture         VARCHAR(50) NOT NULL,
    ev_delta                 REAL DEFAULT 0.0,
    risk_delta               REAL DEFAULT 0.0,
    calibration_delta        REAL DEFAULT 0.0,
    reason                   TEXT,
    approved                 INTEGER DEFAULT 0,
    rolled_back              INTEGER DEFAULT 0,
    timestamp                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transition_type          VARCHAR(20) DEFAULT 'upgrade'
);

CREATE INDEX IF NOT EXISTS idx_trans_to ON architecture_transitions(to_architecture);
CREATE INDEX IF NOT EXISTS idx_trans_rollback ON architecture_transitions(rolled_back);
CREATE INDEX IF NOT EXISTS idx_trans_timestamp ON architecture_transitions(timestamp);