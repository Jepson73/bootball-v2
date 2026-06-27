-- Migration 022: per-season player statistics and fetch tracking
CREATE TABLE IF NOT EXISTS player_season_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    season          INTEGER NOT NULL,
    league_id       INTEGER NOT NULL,
    player_name     TEXT,
    position        TEXT,
    photo_url       TEXT,

    -- Appearances
    appearances     INTEGER DEFAULT 0,
    lineups         INTEGER DEFAULT 0,
    minutes         INTEGER DEFAULT 0,
    rating          REAL,

    -- Goals / assists
    goals           INTEGER DEFAULT 0,
    assists         INTEGER DEFAULT 0,
    goals_conceded  INTEGER DEFAULT 0,
    saves           INTEGER DEFAULT 0,

    -- Shots
    shots_total     INTEGER DEFAULT 0,
    shots_on        INTEGER DEFAULT 0,

    -- Passes
    passes_total    INTEGER DEFAULT 0,
    passes_key      INTEGER DEFAULT 0,
    pass_accuracy   REAL,

    -- Tackles / duels / dribbles
    tackles_total   INTEGER DEFAULT 0,
    duels_total     INTEGER DEFAULT 0,
    duels_won       INTEGER DEFAULT 0,
    dribbles_attempts INTEGER DEFAULT 0,
    dribbles_success  INTEGER DEFAULT 0,

    -- Discipline
    yellow_cards    INTEGER DEFAULT 0,
    red_cards       INTEGER DEFAULT 0,
    fouls_drawn     INTEGER DEFAULT 0,
    fouls_committed INTEGER DEFAULT 0,

    -- Penalty
    pens_scored     INTEGER DEFAULT 0,
    pens_missed     INTEGER DEFAULT 0,
    pens_saved      INTEGER DEFAULT 0,

    fetched_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(player_id, team_id, season, league_id)
);

CREATE INDEX IF NOT EXISTS idx_pss_player_season ON player_season_stats(player_id, season);
CREATE INDEX IF NOT EXISTS idx_pss_team_season ON player_season_stats(team_id, season);

-- Tracks which (team_id, season) pairs have been fully fetched
CREATE TABLE IF NOT EXISTS player_fetch_log (
    team_id     INTEGER NOT NULL,
    season      INTEGER NOT NULL,
    fetched_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count   INTEGER DEFAULT 0,
    PRIMARY KEY (team_id, season)
);
