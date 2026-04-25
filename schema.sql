-- schema.sql — canonical SQLite schema for the betting application
-- Source of truth. Never ALTER tables directly — write a numbered migration in /migrations/
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Reference / lookup tables ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS leagues (
    id      INTEGER NOT NULL PRIMARY KEY,
    name    VARCHAR(100) NOT NULL,
    country VARCHAR(100) NOT NULL,
    tier    INTEGER NOT NULL,
    flag    VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS teams (
    id       INTEGER NOT NULL PRIMARY KEY,
    name     VARCHAR(200) NOT NULL,
    code     VARCHAR(10),
    country  VARCHAR(100),
    logo_url VARCHAR(500),
    flag     VARCHAR(500)
);

-- ── Fixtures & match data ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fixtures (
    id           INTEGER NOT NULL PRIMARY KEY,
    league_id    INTEGER NOT NULL REFERENCES leagues(id),
    season       INTEGER NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    date         DATETIME,
    venue        VARCHAR(200),
    referee      VARCHAR(200),
    round        VARCHAR(100),
    status       VARCHAR(10),
    goals_home   INTEGER,
    goals_away   INTEGER,
    ht_goals_home INTEGER,
    ht_goals_away INTEGER,
    outcome      VARCHAR(1),      -- H / D / A
    fetched_at   DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fixtures_date    ON fixtures(date);
CREATE INDEX IF NOT EXISTS idx_fixtures_status  ON fixtures(status);
CREATE INDEX IF NOT EXISTS idx_fixtures_league  ON fixtures(league_id, season);
CREATE INDEX IF NOT EXISTS idx_fixtures_teams   ON fixtures(home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS fixture_stats (
    id                    INTEGER NOT NULL PRIMARY KEY,
    fixture_id            INTEGER NOT NULL UNIQUE REFERENCES fixtures(id),
    home_shots_total      INTEGER,
    away_shots_total      INTEGER,
    home_shots_on_goal    INTEGER,
    away_shots_on_goal    INTEGER,
    home_possession       FLOAT,
    away_possession       FLOAT,
    home_corners          INTEGER,
    away_corners          INTEGER,
    home_yellow_cards     INTEGER,
    away_yellow_cards     INTEGER,
    home_red_cards        INTEGER,
    away_red_cards        INTEGER,
    home_passes_total     INTEGER,
    away_passes_total     INTEGER,
    home_passes_accurate  INTEGER,
    away_passes_accurate  INTEGER,
    home_xg               FLOAT,
    away_xg               FLOAT
);

CREATE TABLE IF NOT EXISTS fixture_events (
    id          INTEGER NOT NULL PRIMARY KEY,
    fixture_id  INTEGER NOT NULL REFERENCES fixtures(id),
    minute      INTEGER,
    team_id     INTEGER,
    player_name VARCHAR(200),
    event_type  VARCHAR(50),
    detail      VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS match_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id  INTEGER NOT NULL REFERENCES fixtures(id),
    type        VARCHAR(20) NOT NULL,
    minute      INTEGER NOT NULL,
    team        VARCHAR(10),
    player_name VARCHAR(100),
    result      VARCHAR(50),
    is_home     INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (fixture_id, type, minute)
);

CREATE TABLE IF NOT EXISTS live_match_stats (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id            INTEGER NOT NULL REFERENCES fixtures(id),
    minute                INTEGER NOT NULL,
    home_goals            INTEGER DEFAULT 0,
    away_goals            INTEGER DEFAULT 0,
    score_diff            INTEGER DEFAULT 0,
    home_shots_total      INTEGER DEFAULT 0,
    away_shots_total      INTEGER DEFAULT 0,
    home_shots_on_target  INTEGER DEFAULT 0,
    away_shots_on_target  INTEGER DEFAULT 0,
    home_possession       REAL DEFAULT 50.0,
    away_possession       REAL DEFAULT 50.0,
    home_corners          INTEGER DEFAULT 0,
    away_corners          INTEGER DEFAULT 0,
    home_fouls            INTEGER DEFAULT 0,
    away_fouls            INTEGER DEFAULT 0,
    home_yellow_cards     INTEGER DEFAULT 0,
    away_yellow_cards     INTEGER DEFAULT 0,
    home_red_cards        INTEGER DEFAULT 0,
    away_red_cards        INTEGER DEFAULT 0,
    home_xg               REAL DEFAULT 0.0,
    away_xg               REAL DEFAULT 0.0,
    xg_diff               REAL DEFAULT 0.0,
    home_momentum_10min   REAL DEFAULT 0.0,
    away_momentum_10min   REAL DEFAULT 0.0,
    period                VARCHAR(10) DEFAULT '1H',
    minutes_added         INTEGER DEFAULT 0,
    fetched_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (fixture_id, minute)
);

-- ── Standings & ratings ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS standings (
    id           INTEGER NOT NULL PRIMARY KEY,
    league_id    INTEGER NOT NULL REFERENCES leagues(id),
    season       INTEGER NOT NULL,
    team_id      INTEGER NOT NULL,
    team_name    VARCHAR(200) NOT NULL,
    rank         INTEGER,
    points       INTEGER,
    played       INTEGER,
    won          INTEGER,
    drawn        INTEGER,
    lost         INTEGER,
    goals_for    INTEGER,
    goals_against INTEGER,
    goal_diff    INTEGER,
    fetched_at   DATETIME NOT NULL,
    CONSTRAINT uq_standing UNIQUE (league_id, season, team_id)
);

CREATE TABLE IF NOT EXISTS elo_ratings (
    id           INTEGER NOT NULL PRIMARY KEY,
    team_id      INTEGER NOT NULL REFERENCES teams(id),
    as_of_date   DATETIME NOT NULL,
    rating       FLOAT NOT NULL,
    games_played INTEGER NOT NULL,
    CONSTRAINT uq_elo UNIQUE (team_id, as_of_date)
);

-- ── Players & injuries ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS players (
    id             INTEGER NOT NULL PRIMARY KEY,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    name           VARCHAR(200) NOT NULL,
    position       VARCHAR(10),
    photo_url      VARCHAR(500),
    goals          INTEGER NOT NULL,
    assists        INTEGER NOT NULL,
    yellow_cards   INTEGER NOT NULL,
    red_cards      INTEGER NOT NULL,
    minutes_played INTEGER NOT NULL,
    updated_at     DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS injuries (
    id              INTEGER NOT NULL PRIMARY KEY,
    player_id       INTEGER,
    player_name     VARCHAR(200) NOT NULL,
    fixture_id      INTEGER,
    team_id         INTEGER,
    type            VARCHAR(100) NOT NULL,
    status          VARCHAR(50) NOT NULL,
    start_date      DATETIME NOT NULL,
    end_date        DATETIME,
    player_position VARCHAR(20)
);

-- ── Odds ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fixture_odds (
    id           INTEGER NOT NULL PRIMARY KEY,
    fixture_id   INTEGER NOT NULL REFERENCES fixtures(id),
    bookmaker    VARCHAR(100) NOT NULL,
    bet_type     VARCHAR(50) NOT NULL,
    odd_home     FLOAT,
    odd_draw     FLOAT,
    odd_away     FLOAT,
    odd_over     FLOAT,
    odd_under    FLOAT,
    odd_btts_yes FLOAT,
    odd_btts_no  FLOAT,
    odd_over15   FLOAT,
    odd_under15  FLOAT,
    fetched_at   DATETIME NOT NULL,
    CONSTRAINT uq_odds UNIQUE (fixture_id, bookmaker, bet_type)
);

CREATE TABLE IF NOT EXISTS bookmaker_odds (
    id         INTEGER NOT NULL PRIMARY KEY,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    bookmaker  VARCHAR(100) NOT NULL,
    bet_type   VARCHAR(50) NOT NULL,
    odds_json  TEXT NOT NULL,           -- full raw JSON blob from api-football
    fetched_at DATETIME NOT NULL,
    CONSTRAINT uq_bookmaker_odds UNIQUE (fixture_id, bookmaker, bet_type)
);

-- ── ML — Models & versioning ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS model_versions (
    id                      INTEGER NOT NULL PRIMARY KEY,
    market                  VARCHAR(20) NOT NULL,
    version_number          INTEGER NOT NULL,
    version_name            VARCHAR(100),
    brier_score             FLOAT NOT NULL,
    accuracy                FLOAT NOT NULL,
    sample_size             INTEGER NOT NULL,
    ece                     FLOAT NOT NULL,
    calibration_sample_size INTEGER NOT NULL,
    model_type              VARCHAR(50) NOT NULL,
    features_used           VARCHAR(500),
    is_active               BOOLEAN NOT NULL,
    replaced_by_version     INTEGER,
    trained_at              DATETIME NOT NULL,
    created_at              DATETIME NOT NULL,
    CONSTRAINT uq_model_version UNIQUE (market, version_number)
);

CREATE TABLE IF NOT EXISTS retrain_events (
    id                   INTEGER NOT NULL PRIMARY KEY,
    market               VARCHAR(20) NOT NULL,
    old_version_id       INTEGER,
    new_version_id       INTEGER,
    reason               VARCHAR(200) NOT NULL,
    reason_detail        VARCHAR(500),
    brier_score_before   FLOAT,
    brier_score_after    FLOAT,
    triggered_by_drift   BOOLEAN NOT NULL,
    drift_score          FLOAT,
    created_at           DATETIME NOT NULL,
    CONSTRAINT uq_retrain_event UNIQUE (market, old_version_id)
);

CREATE TABLE IF NOT EXISTS model_drift (
    id                   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    market               VARCHAR(20) NOT NULL,
    period_start         DATETIME NOT NULL,
    period_end           DATETIME NOT NULL,
    total_predictions    INTEGER DEFAULT 0,
    correct_predictions  INTEGER DEFAULT 0,
    expected_wins        FLOAT DEFAULT 0,
    actual_wins          INTEGER DEFAULT 0,
    accuracy_pct         FLOAT DEFAULT 0,
    drift_score          FLOAT DEFAULT 0,
    retrain_recommended  INTEGER DEFAULT 0,
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (market, period_start)
);

CREATE TABLE IF NOT EXISTS model_calibration (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    market               TEXT NOT NULL,
    period_start         TIMESTAMP NOT NULL,
    period_end           TIMESTAMP NOT NULL,
    brier_score          REAL DEFAULT 0,
    ece                  REAL DEFAULT 0,
    sample_size          INTEGER DEFAULT 0,
    reliability_diagram  TEXT,           -- JSON blob of calibration curve data
    is_active            INTEGER DEFAULT 1,
    retrain_recommended  INTEGER DEFAULT 0,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (market, period_start)
);

-- ── Predictions ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS predictions_archive (
    id                     INTEGER NOT NULL PRIMARY KEY,
    fixture_id             INTEGER NOT NULL REFERENCES fixtures(id),
    model_name             VARCHAR(100) NOT NULL,
    prob_home              FLOAT NOT NULL,
    prob_draw              FLOAT NOT NULL,
    prob_away              FLOAT NOT NULL,
    predicted_home_goals   FLOAT,
    predicted_away_goals   FLOAT,
    created_at             DATETIME NOT NULL,
    CONSTRAINT uq_prediction UNIQUE (fixture_id, model_name)
);

CREATE TABLE IF NOT EXISTS prediction_records (
    id                       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    fixture_id               INTEGER NOT NULL REFERENCES fixtures(id),
    market                   VARCHAR(20) NOT NULL,
    model_name               VARCHAR(50) DEFAULT 'ensemble',
    model_version_id         INTEGER REFERENCES model_versions(id),
    run_id                   VARCHAR(36),
    calibration_version_id  VARCHAR(50),
    feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0',
    predicted_outcome        VARCHAR(10) NOT NULL,
    our_prob                 FLOAT NOT NULL,
    calibrated_prob          REAL,
    implied_prob             REAL,
    ev                       REAL,
    edge                     REAL,
    odds_decimal             REAL,
    bookmaker                VARCHAR(50),
    odds_snapshot            VARCHAR(50),
    sweet_spot               INTEGER DEFAULT 0,
    actual_outcome           VARCHAR(10),
    settled                  INTEGER DEFAULT 0,
    won                      INTEGER,
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_at               DATETIME,
    UNIQUE (fixture_id, market)
);

CREATE INDEX IF NOT EXISTS idx_pred_fixture  ON prediction_records(fixture_id);
CREATE INDEX IF NOT EXISTS idx_pred_settled  ON prediction_records(settled);
CREATE INDEX IF NOT EXISTS idx_pred_market   ON prediction_records(market);

-- ── Bankroll & bets ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bankroll_rounds (
    id               INTEGER NOT NULL PRIMARY KEY,
    round_number     INTEGER NOT NULL,
    started_at       DATETIME NOT NULL,
    ended_at         DATETIME,
    initial_bankroll FLOAT NOT NULL,
    ending_balance   FLOAT,
    reason           VARCHAR(100),
    total_bets       INTEGER NOT NULL,
    total_wins       INTEGER NOT NULL,
    total_staked     FLOAT NOT NULL,
    total_pnl        FLOAT NOT NULL,
    roi_pct          FLOAT NOT NULL,
    is_active        BOOLEAN NOT NULL,
    CONSTRAINT uq_round_number UNIQUE (round_number)
);

CREATE TABLE IF NOT EXISTS bankroll (
    id           INTEGER NOT NULL PRIMARY KEY,
    date         DATETIME NOT NULL,
    balance      FLOAT NOT NULL,
    total_staked FLOAT NOT NULL,
    total_won    FLOAT NOT NULL,
    total_lost   FLOAT NOT NULL,
    bet_count    INTEGER NOT NULL,
    win_count    INTEGER NOT NULL,
    notes        VARCHAR(500),
    round_id     INTEGER REFERENCES bankroll_rounds(id),
    CONSTRAINT uq_bankroll_date UNIQUE (date)
);

CREATE TABLE IF NOT EXISTS placed_bets (
    id                       INTEGER NOT NULL PRIMARY KEY,
    round_id                 INTEGER NOT NULL REFERENCES bankroll_rounds(id),
    fixture_id               INTEGER NOT NULL REFERENCES fixtures(id),
    model_version_id         INTEGER REFERENCES model_versions(id),
    run_id                   VARCHAR(36),
    calibration_version_id  VARCHAR(50),
    feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0',
    market                   VARCHAR(20) NOT NULL,
    outcome                  VARCHAR(10) NOT NULL,
    stake                    FLOAT NOT NULL,
    odds                     FLOAT NOT NULL,
    our_prob                 FLOAT NOT NULL,
    ev                       FLOAT NOT NULL,
    kelly_fraction           FLOAT NOT NULL,
    settled                  BOOLEAN NOT NULL,
    actual_result            VARCHAR(10),
    won                      BOOLEAN,
    pnl                      FLOAT,
    placed_at                DATETIME NOT NULL,
    settled_at               DATETIME,
    CONSTRAINT uq_placed_bet_unique UNIQUE (fixture_id, market, outcome, round_id)
);

CREATE TABLE IF NOT EXISTS value_bets_archive (
    id                INTEGER NOT NULL PRIMARY KEY,
    fixture_id        INTEGER NOT NULL REFERENCES fixtures(id),
    model_name        VARCHAR(100) NOT NULL,
    market            VARCHAR(20) DEFAULT 'h2h',
    outcome           VARCHAR(5) NOT NULL,
    our_prob          FLOAT NOT NULL,
    bookmaker_odd     FLOAT NOT NULL,
    implied_prob      FLOAT NOT NULL,
    ev                FLOAT NOT NULL,
    kelly_fraction    FLOAT NOT NULL,
    recommended_stake FLOAT,
    result            VARCHAR(5),
    won               BOOLEAN,
    pnl               FLOAT,
    settled           INTEGER DEFAULT 0,
    created_at        DATETIME NOT NULL
);

-- ── User preferences & watchlist ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_preferences (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT,
    timezone          TEXT NOT NULL DEFAULT 'Europe/Stockholm',
    preferred_markets TEXT,
    preferred_leagues TEXT,
    alerts_enabled    INTEGER NOT NULL DEFAULT 1,
    alerts_min_ev     REAL NOT NULL DEFAULT 0.05,
    alerts_top_n      INTEGER NOT NULL DEFAULT 5,
    default_days      INTEGER NOT NULL DEFAULT 7,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)
);

CREATE TABLE IF NOT EXISTS watched_fixtures (
    id             INTEGER NOT NULL PRIMARY KEY,
    user_id        VARCHAR(100),
    fixture_id     INTEGER NOT NULL REFERENCES fixtures(id),
    market         VARCHAR(20) NOT NULL,
    selection_type VARCHAR(20) NOT NULL,
    status         VARCHAR(20) NOT NULL,
    notes          VARCHAR(500),
    created_at     DATETIME NOT NULL,
    updated_at     DATETIME NOT NULL,
    CONSTRAINT uq_watched_fixture UNIQUE (user_id, fixture_id, market)
);

-- ── Experiment Tracking ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS experiment_runs (
    id                      INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id                  VARCHAR(36) NOT NULL UNIQUE,
    mode                    VARCHAR(20) NOT NULL,
    start_timestamp         DATETIME NOT NULL,
    end_timestamp           DATETIME,
    model_versions_json     TEXT,
    calibrator_versions_json TEXT,
    feature_pipeline_version VARCHAR(20) DEFAULT 'v1.0.0',
    config_hash             VARCHAR(16) NOT NULL,
    total_predictions       INTEGER DEFAULT 0,
    total_bets              INTEGER DEFAULT 0,
    bankroll_snapshot       FLOAT,
    final_metrics_json      TEXT,
    status                  VARCHAR(20) DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_exp_run_id ON experiment_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_exp_mode ON experiment_runs(mode);
CREATE INDEX IF NOT EXISTS idx_exp_status ON experiment_runs(status);
