-- Migration 017: Add observability semantic snapshots table
-- Stores semantic version snapshots for reproducibility

CREATE TABLE IF NOT EXISTS observability_semantic_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version VARCHAR(20) NOT NULL UNIQUE,
    rules_hash VARCHAR(16) NOT NULL,
    description TEXT,
    activation_timestamp DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Insert initial semantic version
INSERT OR IGNORE INTO observability_semantic_snapshots 
(version, rules_hash, description, activation_timestamp)
VALUES (
    '1.0.0',
    '1a2b3c4d5e6f7a8b',
    'Initial semantic versioning for observability layer',
    '2026-04-25 06:30:00'
);