-- Migration: Add system mode change tracking

CREATE TABLE IF NOT EXISTS system_mode_changes (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    old_mode                VARCHAR(20) NOT NULL,
    new_mode                VARCHAR(20) NOT NULL,
    reason                  VARCHAR(200),
    changed_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mode_changes_at ON system_mode_changes(changed_at);