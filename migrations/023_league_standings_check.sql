-- Migration 023: track per-league standings-check outcomes.
--
-- Records what fix_orphaned_fixtures found (or didn't find) for each league,
-- so it (a) doesn't keep re-querying leagues already confirmed to have no
-- standings data every cycle, and (b) the "orphaned fixtures" diagnostic can
-- distinguish "never checked" from "checked, provider has no table for this
-- competition" — a knockout cup or one-off final has no standings to give.
CREATE TABLE IF NOT EXISTS league_standings_check (
    league_id       INTEGER PRIMARY KEY,
    status          TEXT NOT NULL,   -- fixed | no_data | no_standings_expected | empty
    season_checked  INTEGER NOT NULL,
    checked_at      TEXT NOT NULL
);
