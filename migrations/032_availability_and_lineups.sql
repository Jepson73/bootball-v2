-- Migration 032: extend `injuries` for the availability tier, add `lineups` +
-- `lineup_players` for the (conditional) confirmed-XI tier.
--
-- Phase 37 Part A.2. Context: `scripts/fetch_player_data.py` (dead code, never
-- called from the live pipeline) is the only prior injuries writer, and it has
-- a field-mapping bug -- it reads `team.get("reason")` for `status`, but the
-- API-Football /injuries response nests `reason` under `player`, not `team`
-- (`player: {type: "Missing Fixture", reason: "Calf Injury", ...}`). The 586
-- existing rows are consequently near-useless: `type` is always the literal
-- string "Missing Fixture" and `status` always falls through to the "injured"
-- default. This migration adds the columns needed to capture the response
-- correctly going forward without rewriting that fossil (era-boundary
-- convention: NULL for existing rows, not backfilled).
--
-- `fetched_at` is the leakage-relevant column for FORWARD collection (Part
-- A.4): a feature for an NS fixture may only use injury rows already fetched
-- before the prediction was made. It is NOT the leakage boundary for
-- historical backfill of SETTLED fixtures (Part A.3) -- there, `/injuries?
-- fixture=X` returns that fixture's pre-match "Missing Fixture" team news as
-- a historical fact tied to the match itself, independent of when we
-- happened to pull it today. See Part B.1's feature doc for the full
-- reasoning and the belt-and-suspenders date check applied on top.
ALTER TABLE injuries ADD COLUMN reason TEXT;
ALTER TABLE injuries ADD COLUMN league_id INTEGER;
ALTER TABLE injuries ADD COLUMN season INTEGER;
ALTER TABLE injuries ADD COLUMN fetched_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_injuries_fixture ON injuries(fixture_id);
CREATE INDEX IF NOT EXISTS idx_injuries_team_league_season ON injuries(team_id, league_id, season);

-- `lineups` / `lineup_players` — schema only in this phase. Part A.3
-- deliberately does NOT bulk-backfill these tables yet: they only matter for
-- Part C (confirmed-XI tier), which is gated on a Part B pass. Building the
-- schema now avoids a second migration cycle if/when Part C proceeds, without
-- spending backfill budget on a feature tier that may never ship.
CREATE TABLE IF NOT EXISTS lineups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id      INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    is_home         INTEGER NOT NULL,
    formation       TEXT,
    coach_id        INTEGER,
    coach_name      TEXT,
    fetched_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    minutes_before_kickoff INTEGER,  -- (fixture.date - fetched_at) in minutes, NULL for historical backfill where this isn't meaningful
    UNIQUE(fixture_id, team_id)
);

CREATE TABLE IF NOT EXISTS lineup_players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lineup_id   INTEGER NOT NULL REFERENCES lineups(id),
    player_id   INTEGER,
    player_name TEXT,
    position    TEXT,
    grid        TEXT,
    is_starter  INTEGER NOT NULL  -- 1 = startXI, 0 = substitute
);

CREATE INDEX IF NOT EXISTS idx_lineups_fixture ON lineups(fixture_id);
CREATE INDEX IF NOT EXISTS idx_lineup_players_lineup ON lineup_players(lineup_id);
