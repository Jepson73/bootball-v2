-- Baseline migration tracking table.
-- This must be applied before any other migration.
-- The runner (scripts/migrate.py) creates this automatically.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER NOT NULL PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum    TEXT    NOT NULL
);
