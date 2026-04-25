-- Migration: Add architecture versioning system

CREATE TABLE IF NOT EXISTS architecture_versions (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    architecture_id           VARCHAR(50) NOT NULL UNIQUE,
    parent_id                VARCHAR(50),
    active_layers            TEXT NOT NULL DEFAULT '[]',
    layer_weights            TEXT NOT NULL DEFAULT '{}',
    feature_set              TEXT NOT NULL DEFAULT '{}',
    calibration_stack        TEXT NOT NULL DEFAULT '{}',
    governance_score         REAL DEFAULT 0.0,
    ev_score                 REAL DEFAULT 0.0,
    risk_score               REAL DEFAULT 0.0,
    validation_score         REAL DEFAULT 0.0,
    is_candidate             INTEGER DEFAULT 0,
    is_active                INTEGER DEFAULT 0,
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description              TEXT
);

CREATE INDEX IF NOT EXISTS idx_arch_id ON architecture_versions(architecture_id);
CREATE INDEX IF NOT EXISTS idx_arch_parent ON architecture_versions(parent_id);
CREATE INDEX IF NOT EXISTS idx_arch_active ON architecture_versions(is_active);
CREATE INDEX IF NOT EXISTS idx_arch_candidate ON architecture_versions(is_candidate);