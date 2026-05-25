-- Migration 021: sanity_check_issues table for daily dedup tracking
CREATE TABLE IF NOT EXISTS sanity_check_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    check_type  TEXT NOT NULL,
    issue_key   TEXT NOT NULL,
    detail      TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(issue_key)
);
