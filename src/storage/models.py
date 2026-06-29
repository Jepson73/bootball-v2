"""
src/storage/models.py

SQLAlchemy ORM models.
Schema mirrors API-Football data shapes but is normalised for querying.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Reference / Dimension tables ─────────────────────────────────────────────

class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # API-Football league ID
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    flag: Mapped[str | None] = mapped_column(String(500))  # URL to country flag

    fixtures: Mapped[list["Fixture"]] = relationship(back_populates="league")
    standings: Mapped[list["Standing"]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # API-Football team ID
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str | None] = mapped_column(String(100))
    logo_url: Mapped[str | None] = mapped_column(String(500))

    home_fixtures: Mapped[list["Fixture"]] = relationship(
        foreign_keys="Fixture.home_team_id", back_populates="home_team"
    )
    away_fixtures: Mapped[list["Fixture"]] = relationship(
        foreign_keys="Fixture.away_team_id", back_populates="away_team"
    )
    elo_ratings: Mapped[list["EloRating"]] = relationship(back_populates="team")


# ── Player data ───────────────────────────────────────────────────────────

class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # API-Football player ID
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[str | None] = mapped_column(String(10))  # G, D, M, F
    photo_url: Mapped[str | None] = mapped_column(String(500))

    # Season stats (updated periodically)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    minutes_played: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PlayerSeasonStats(Base):
    __tablename__ = "player_season_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, index=True)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    season: Mapped[int] = mapped_column(Integer)
    league_id: Mapped[int] = mapped_column(Integer)
    player_name: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[str | None] = mapped_column(String(10))
    photo_url: Mapped[str | None] = mapped_column(String(500))

    appearances: Mapped[int] = mapped_column(Integer, default=0)
    lineups: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)

    shots_total: Mapped[int] = mapped_column(Integer, default=0)
    shots_on: Mapped[int] = mapped_column(Integer, default=0)

    passes_total: Mapped[int] = mapped_column(Integer, default=0)
    passes_key: Mapped[int] = mapped_column(Integer, default=0)
    pass_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)

    tackles_total: Mapped[int] = mapped_column(Integer, default=0)
    duels_total: Mapped[int] = mapped_column(Integer, default=0)
    duels_won: Mapped[int] = mapped_column(Integer, default=0)
    dribbles_attempts: Mapped[int] = mapped_column(Integer, default=0)
    dribbles_success: Mapped[int] = mapped_column(Integer, default=0)

    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    fouls_drawn: Mapped[int] = mapped_column(Integer, default=0)
    fouls_committed: Mapped[int] = mapped_column(Integer, default=0)

    pens_scored: Mapped[int] = mapped_column(Integer, default=0)
    pens_missed: Mapped[int] = mapped_column(Integer, default=0)
    pens_saved: Mapped[int] = mapped_column(Integer, default=0)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("player_id", "team_id", "season", "league_id"),)


class PlayerFetchLog(Base):
    __tablename__ = "player_fetch_log"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    row_count: Mapped[int] = mapped_column(Integer, default=0)


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, nullable=True)  # FK optional - players may not be in DB
    player_name: Mapped[str] = mapped_column(String(200))
    player_position: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Goalkeeper, Defender, Midfielder, Attacker, Forward
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    type: Mapped[str] = mapped_column(String(100))  # e.g., "Leg Injury", "Illness"
    status: Mapped[str] = mapped_column(String(50))  # "injured", "suspended", "doubt", "recovered"
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── Core match table ──────────────────────────────────────────────────────────

class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # API-Football fixture ID
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    season: Mapped[int] = mapped_column(Integer)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    date: Mapped[datetime | None] = mapped_column(DateTime)
    venue: Mapped[str | None] = mapped_column(String(200))
    referee: Mapped[str | None] = mapped_column(String(200))
    round: Mapped[str | None] = mapped_column(String(100))

    # Result
    status: Mapped[str | None] = mapped_column(String(10))   # FT, NS, 1H, HT, …
    elapsed: Mapped[int | None] = mapped_column(Integer)      # Live match minute (45, 60, 90, etc.)
    goals_home: Mapped[int | None] = mapped_column(Integer)
    goals_away: Mapped[int | None] = mapped_column(Integer)
    ht_goals_home: Mapped[int | None] = mapped_column(Integer)
    ht_goals_away: Mapped[int | None] = mapped_column(Integer)

    # Outcome (derived, stored for fast querying)
    outcome: Mapped[str | None] = mapped_column(String(1))    # H / D / A

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    league: Mapped["League"] = relationship(back_populates="fixtures")
    home_team: Mapped["Team"] = relationship(
        foreign_keys=[home_team_id], back_populates="home_fixtures"
    )
    away_team: Mapped["Team"] = relationship(
        foreign_keys=[away_team_id], back_populates="away_fixtures"
    )
    stats: Mapped["FixtureStats | None"] = relationship(back_populates="fixture", uselist=False)
    events: Mapped[list["FixtureEvent"]] = relationship(back_populates="fixture")
    odds: Mapped[list["FixtureOdds"]] = relationship(back_populates="fixture")


# ── Match statistics (shots, corners, cards, possession …) ───────────────────

class FixtureStats(Base):
    __tablename__ = "fixture_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), unique=True)

    # Shots
    home_shots_total: Mapped[int | None] = mapped_column(Integer)
    away_shots_total: Mapped[int | None] = mapped_column(Integer)
    home_shots_on_goal: Mapped[int | None] = mapped_column(Integer)
    away_shots_on_goal: Mapped[int | None] = mapped_column(Integer)

    # Possession
    home_possession: Mapped[float | None] = mapped_column(Float)   # 0–100
    away_possession: Mapped[float | None] = mapped_column(Float)

    # Set pieces
    home_corners: Mapped[int | None] = mapped_column(Integer)
    away_corners: Mapped[int | None] = mapped_column(Integer)

    # Discipline
    home_yellow_cards: Mapped[int | None] = mapped_column(Integer)
    away_yellow_cards: Mapped[int | None] = mapped_column(Integer)
    home_red_cards: Mapped[int | None] = mapped_column(Integer)
    away_red_cards: Mapped[int | None] = mapped_column(Integer)

    # Passes
    home_passes_total: Mapped[int | None] = mapped_column(Integer)
    away_passes_total: Mapped[int | None] = mapped_column(Integer)
    home_passes_accurate: Mapped[int | None] = mapped_column(Integer)
    away_passes_accurate: Mapped[int | None] = mapped_column(Integer)

    # xG (if returned by API)
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)

    fixture: Mapped["Fixture"] = relationship(back_populates="stats")


# ── Match events (goals, cards, subs) ────────────────────────────────────────

class FixtureEvent(Base):
    __tablename__ = "fixture_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))

    minute: Mapped[int | None] = mapped_column(Integer)
    team_id: Mapped[int | None] = mapped_column(Integer)
    player_name: Mapped[str | None] = mapped_column(String(200))
    event_type: Mapped[str | None] = mapped_column(String(50))    # Goal, Card, subst
    detail: Mapped[str | None] = mapped_column(String(100))       # Normal Goal, Yellow Card, …

    fixture: Mapped["Fixture"] = relationship(back_populates="events")


# ── Odds ──────────────────────────────────────────────────────────────────────

class FixtureOdds(Base):
    __tablename__ = "fixture_odds"
    __table_args__ = (
        UniqueConstraint("fixture_id", "bookmaker", "bet_type", name="uq_odds_fixture_bookmaker_bettype"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    bookmaker: Mapped[str] = mapped_column(String(100))
    bet_type: Mapped[str] = mapped_column(String(50))   # h2h | over_under | btts

    odd_home: Mapped[float | None] = mapped_column(Float)    # 1X2: home win
    odd_draw: Mapped[float | None] = mapped_column(Float)    # 1X2: draw
    odd_away: Mapped[float | None] = mapped_column(Float)    # 1X2: away win
    odd_over: Mapped[float | None] = mapped_column(Float)    # over 2.5 goals
    odd_under: Mapped[float | None] = mapped_column(Float)   # under 2.5 goals
    odd_btts_yes: Mapped[float | None] = mapped_column(Float)
    odd_btts_no: Mapped[float | None] = mapped_column(Float)
    odd_over15: Mapped[float | None] = mapped_column(Float)
    odd_under15: Mapped[float | None] = mapped_column(Float)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship(back_populates="odds")


# ── League standings snapshot ─────────────────────────────────────────────────

class Standing(Base):
    __tablename__ = "standings"
    __table_args__ = (
        UniqueConstraint("league_id", "season", "team_id", name="uq_standing"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    season: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[int] = mapped_column(Integer)
    team_name: Mapped[str] = mapped_column(String(200))
    rank: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    played: Mapped[int | None] = mapped_column(Integer)
    won: Mapped[int | None] = mapped_column(Integer)
    drawn: Mapped[int | None] = mapped_column(Integer)
    lost: Mapped[int | None] = mapped_column(Integer)
    goals_for: Mapped[int | None] = mapped_column(Integer)
    goals_against: Mapped[int | None] = mapped_column(Integer)
    goal_diff: Mapped[int | None] = mapped_column(Integer)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    league: Mapped["League"] = relationship(back_populates="standings")


# ── Model outputs: Elo ratings ────────────────────────────────────────────────

class EloRating(Base):
    __tablename__ = "elo_ratings"
    __table_args__ = (
        UniqueConstraint("team_id", "as_of_date", name="uq_elo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    as_of_date: Mapped[datetime] = mapped_column(DateTime)
    rating: Mapped[float] = mapped_column(Float)
    games_played: Mapped[int] = mapped_column(Integer, default=0)

    team: Mapped["Team"] = relationship(back_populates="elo_ratings")


# ── Prediction tracking: all predictions with outcomes ───────────────────────

class PredictionRecord(Base):
    __tablename__ = "prediction_records"
    __table_args__ = (
        UniqueConstraint("fixture_id", "market", name="uq_pred_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    market: Mapped[str] = mapped_column(String(20))   # h2h, btts, ou25, ou15
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(50), default="ensemble")
    
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    calibration_version_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    feature_pipeline_version: Mapped[str] = mapped_column(String(20), default="v1.0.0")
    blend_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    predicted_outcome: Mapped[str] = mapped_column(String(10))   # H/D/A, Yes/No, Over/Under
    raw_outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    our_prob: Mapped[float] = mapped_column(Float)
    calibrated_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_prob: Mapped[float | None] = mapped_column(Float, nullable=True)   # de-vigged (Shin) market-implied prob
    blended_prob: Mapped[float | None] = mapped_column(Float, nullable=True)  # final — drives EV/Kelly/betting
    implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Full h2h probability vector — required by evaluate_track_a() for h2h scoring.
    # Keys map to API-Football notation: "1"=Home, "X"=Draw, "2"=Away.
    # NULL for binary markets (btts, ou25, ou15).
    prob_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_decimal: Mapped[float | None] = mapped_column(Float, nullable=True)
    bookmaker: Mapped[str | None] = mapped_column(String(50), nullable=True)
    odds_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)

    sweet_spot: Mapped[bool] = mapped_column(Boolean, default=False)

    actual_outcome: Mapped[str | None] = mapped_column(String(10))
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    won: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False)

    model_version: Mapped["ModelVersion | None"] = relationship("ModelVersion", foreign_keys=[model_version_id])


# ── Model drift tracking ─────────────────────────────────────────────────────


# ── Model drift tracking ─────────────────────────────────────────────────────

class ModelDrift(Base):
    __tablename__ = "model_drift"
    __table_args__ = (
        UniqueConstraint("market", "period_start", name="uq_model_drift"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20))

    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)

    total_predictions: Mapped[int] = mapped_column(Integer, default=0)
    correct_predictions: Mapped[int] = mapped_column(Integer, default=0)
    expected_wins: Mapped[float] = mapped_column(Float, default=0)
    actual_wins: Mapped[int] = mapped_column(Integer, default=0)

    accuracy_pct: Mapped[float] = mapped_column(Float, default=0)
    drift_score: Mapped[float] = mapped_column(Float, default=0)  # positive = over-performing, negative = under-performing

    retrain_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Model calibration tracking ─────────────────────────────────────────────────

class ModelCalibration(Base):
    """Tracks calibration metrics per market over time.

    Used to detect when calibrator needs retraining.
    """
    __tablename__ = "model_calibration"
    __table_args__ = (
        UniqueConstraint("market", "period_start", name="uq_model_calibration"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20))

    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)

    # Calibration metrics
    brier_score: Mapped[float] = mapped_column(Float, default=0)
    ece: Mapped[float] = mapped_column(Float, default=0)  # Expected Calibration Error
    sample_size: Mapped[int] = mapped_column(Integer, default=0)

    # Calibration curve data (JSON string of bin accuracies)
    reliability_diagram: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    retrain_recommended: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Model versioning & iteration tracking ─────────────────────────────────────

class ModelVersion(Base):
    """Tracks each model version/iteration per market.

    Every retrain creates a new version with its metrics.
    Used for graphs showing model lifecycle.
    """
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("market", "version_number", name="uq_model_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20))

    # Sequential row counter — always increments (satisfies unique constraint).
    version_number: Mapped[int] = mapped_column(Integer)
    version_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # vXX_cYY label parts.
    # model_number (vXX): increments on every retrain, constant across recalibrations.
    # calibration_number (cYY): resets to 0 on retrain, increments on recalibrate.
    # version_label: "v{model_number:02d}_c{calibration_number:02d}" — human-readable key.
    model_number: Mapped[int] = mapped_column(Integer, default=1)
    calibration_number: Mapped[int] = mapped_column(Integer, default=0)
    version_label: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Metrics at time of training
    brier_score: Mapped[float] = mapped_column(Float, default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)

    # Calibration metrics
    ece: Mapped[float] = mapped_column(Float, default=0)
    calibration_sample_size: Mapped[int] = mapped_column(Integer, default=0)

    # Model metadata
    model_type: Mapped[str] = mapped_column(String(50), default="ensemble")
    features_used: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Status — only one is_active=True per market at any time.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    replaced_by_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    trained_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RetrainEvent(Base):
    """Records when models were retrained and why.

    Links old version to new version, tracks reason for retraining.
    """
    __tablename__ = "retrain_events"
    __table_args__ = (
        UniqueConstraint("market", "old_version_id", name="uq_retrain_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20))

    old_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str] = mapped_column(String(200))
    reason_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Metrics delta
    brier_score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    triggered_by_drift: Mapped[bool] = mapped_column(Boolean, default=False)
    drift_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── League-level calibration ──────────────────────────────────────────────────

class LeagueCalibration(Base):
    """Platt-scaling calibration fitted per (market, league_id).

    Stores the logistic regression slope/intercept that maps raw model logit
    to a calibrated probability for a specific league.  The comparison fields
    (brier_score_global vs brier_score) let us decide whether to activate the
    league-specific calibration or fall back to the global one.

    league_id=NULL is the global calibration (L0000) — applies when no
    league-specific calibration is active for a fixture's league.
    """
    __tablename__ = "league_calibrations"
    __table_args__ = (
        UniqueConstraint("market", "league_id", "version_label", name="uq_league_cal_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20))
    league_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leagues.id"), nullable=True)

    # Full version label e.g. "v01_c02_l0188"
    version_label: Mapped[str] = mapped_column(String(30))

    # Platt-scaling parameters: p_cal = sigmoid(slope * logit(p_raw) + intercept)
    slope: Mapped[float] = mapped_column(Float, default=1.0)
    intercept: Mapped[float] = mapped_column(Float, default=0.0)

    # Hold-out Brier scores for the league (lower = better calibration)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_score_global: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Positive = league cal improves on global, negative = global is better
    brier_improvement: Mapped[float | None] = mapped_column(Float, nullable=True)

    sample_size: Mapped[int] = mapped_column(Integer, default=0)

    # Only one is_active=True per (market, league_id) at any time
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    league: Mapped["League | None"] = relationship("League", foreign_keys=[league_id])


# ── Bankroll tracking ─────────────────────────────────────────────────────────

class Bankroll(Base):
    __tablename__ = "bankroll"
    __table_args__ = (
        UniqueConstraint("date", "round_id", name="uq_bankroll_date_round"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("bankroll_rounds.id"), nullable=True)
    balance: Mapped[float] = mapped_column(Float)           # Running balance
    total_staked: Mapped[float] = mapped_column(Float, default=0)
    total_won: Mapped[float] = mapped_column(Float, default=0)
    total_lost: Mapped[float] = mapped_column(Float, default=0)
    bet_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)

    notes: Mapped[str | None] = mapped_column(String(500))

    round: Mapped["BankrollRound | None"] = relationship("BankrollRound", foreign_keys=[round_id])


# ── Betting rounds (for tracking resets) ───────────────────────────────────────

class BankrollRound(Base):
    __tablename__ = "bankroll_rounds"
    __table_args__ = (
        UniqueConstraint("round_number", name="uq_round_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    initial_bankroll: Mapped[float] = mapped_column(Float, default=1000.0)
    ending_balance: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(100))
    total_bets: Mapped[int] = mapped_column(Integer, default=0)
    total_wins: Mapped[int] = mapped_column(Integer, default=0)
    total_staked: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ── Automatic bet placements ─────────────────────────────────────────────────────

class PlacedBet(Base):
    __tablename__ = "placed_bets"
    __table_args__ = (
        UniqueConstraint("fixture_id", "market", "outcome", "round_id",
                         name="uq_placed_bet_unique"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("bankroll_rounds.id"))
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    market: Mapped[str] = mapped_column(String(20))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True)
    prediction_record_id: Mapped[int | None] = mapped_column(ForeignKey("prediction_records.id"), nullable=True)

    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    calibration_version_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    feature_pipeline_version: Mapped[str] = mapped_column(String(20), default="v1.0.0")
    
    outcome: Mapped[str] = mapped_column(String(10))
    stake: Mapped[float] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    our_prob: Mapped[float] = mapped_column(Float)          # raw Vxx — for C-calibration training
    calibrated_prob: Mapped[float | None] = mapped_column(Float, nullable=True)  # VCL final
    ev: Mapped[float] = mapped_column(Float)
    kelly_fraction: Mapped[float] = mapped_column(Float)

    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_result: Mapped[str | None] = mapped_column(String(10))
    won: Mapped[bool | None] = mapped_column(Boolean)
    pnl: Mapped[float | None] = mapped_column(Float)

    # Closing Line Value — odds/implied-prob captured near kickoff, and the
    # resulting edge vs. our bet price. Fast same-day signal of whether a
    # claimed edge was real foresight or model error (see odds_poll.py capture).
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    closing_implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    placed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)

    settle_confirmations: Mapped[int] = mapped_column(Integer, default=0)
    settle_pending_result: Mapped[str | None] = mapped_column(String(10), nullable=True)

    round: Mapped["BankrollRound"] = relationship("BankrollRound", foreign_keys=[round_id])
    fixture: Mapped["Fixture"] = relationship()
    model_version: Mapped["ModelVersion | None"] = relationship("ModelVersion", foreign_keys=[model_version_id])
    prediction_record: Mapped["PredictionRecord | None"] = relationship("PredictionRecord", foreign_keys=[prediction_record_id])


# ── User preferences (future multi-user ready) ─────────────────────────────────

class UserPreference(Base):
    """User preferences for personalization.

    Currently user_id is nullable - works in single-user mode.
    When multi-user is implemented, each user gets their own preferences.
    """
    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_preferences"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # NULL = default/preferences

    # Timezone (IANA timezone database name)
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Stockholm")

    # Preferred markets (comma-separated: "btts,ou25,ou15,h2h")
    preferred_markets: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Preferred leagues (comma-separated league IDs)
    preferred_leagues: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Alert settings
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    alerts_min_ev: Mapped[float] = mapped_column(Float, default=0.05)
    alerts_top_n: Mapped[int] = mapped_column(Integer, default=5)

    # Display preferences
    default_days: Mapped[int] = mapped_column(Integer, default=7)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── User fixture following (Phase 7) ─────────────────────────────────────────

class WatchedFixture(Base):
    """Tracks which fixtures a user is following/watching.

    Currently user_id is nullable - works in single-user mode.
    When multi-user is implemented, each user gets their own watched fixtures.
    """
    __tablename__ = "watched_fixtures"
    __table_args__ = (
        UniqueConstraint("user_id", "fixture_id", "market", name="uq_watched_fixture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # NULL = default/global

    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    market: Mapped[str] = mapped_column(String(20))  # btts, h2h, ou25, ou15

    selection_type: Mapped[str] = mapped_column(String(20), default="watch")  # watch | auto | manual
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | live | settled

    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship()


# ── System Governance Tables ─────────────────────────────────────────────────

class LayerGovernanceMetrics(Base):
    """
    Tracks long-term utility per layer across runs.
    Used for layer promotion/demotion decisions.
    """
    __tablename__ = "layer_governance_metrics"
    __table_args__ = (
        UniqueConstraint("layer_name", "run_id", name="uq_layer_governance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    layer_name: Mapped[str] = mapped_column(String(50), nullable=False)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False)

    ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    roi_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    stability_score: Mapped[float] = mapped_column(Float, default=0.0)
    fragility_score: Mapped[float] = mapped_column(Float, default=0.0)
    redundancy_index: Mapped[float] = mapped_column(Float, default=0.0)
    failure_correlation: Mapped[float] = mapped_column(Float, default=0.0)
    convergence_score: Mapped[float] = mapped_column(Float, default=0.0)

    promotion_recommended: Mapped[int] = mapped_column(Integer, default=0)
    demotion_recommended: Mapped[int] = mapped_column(Integer, default=0)

    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LayerAblationResults(Base):
    """
    Stores counterfactual layer ablation simulation results.
    Tracks EV delta, calibration delta, and risk delta when each layer is removed.
    """
    __tablename__ = "layer_ablation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "layer_removed", name="uq_layer_ablation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    layer_removed: Mapped[str] = mapped_column(String(50), nullable=False)

    baseline_ev: Mapped[float] = mapped_column(Float, default=0.0)
    ablated_ev: Mapped[float] = mapped_column(Float, default=0.0)
    ev_delta: Mapped[float] = mapped_column(Float, default=0.0)

    baseline_calibration: Mapped[float] = mapped_column(Float, default=0.0)
    ablated_calibration: Mapped[float] = mapped_column(Float, default=0.0)
    calibration_delta: Mapped[float] = mapped_column(Float, default=0.0)

    baseline_risk: Mapped[float] = mapped_column(Float, default=0.0)
    ablated_risk: Mapped[float] = mapped_column(Float, default=0.0)
    risk_delta: Mapped[float] = mapped_column(Float, default=0.0)

    prediction_count: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[str] = mapped_column(String(20), default="keep")

    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PredictionAttribution(Base):
    """
    Per-prediction causal decomposition by system layer.
    Populated by AttributionEngine after each prediction commit.
    Required by GovernanceEngine for ablation analysis and architecture evolution.
    """
    __tablename__ = "prediction_attribution"
    __table_args__ = (UniqueConstraint("prediction_id", name="uq_attribution_prediction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(Integer, ForeignKey("prediction_records.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)

    # Model layer
    model_prob_raw: Mapped[float] = mapped_column(Float, nullable=False)

    # Calibration layer
    calibration_delta: Mapped[float] = mapped_column(Float, default=0.0)
    calibration_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # League/feature layer
    league_delta: Mapped[float] = mapped_column(Float, default=0.0)
    league_adjusted_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Latent state layer
    latent_delta: Mapped[float] = mapped_column(Float, default=0.0)
    latent_adjusted_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Drift layer
    drift_delta: Mapped[float] = mapped_column(Float, default=0.0)
    drift_adjusted_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Risk layer
    risk_delta: Mapped[float] = mapped_column(Float, default=0.0)
    risk_filtered: Mapped[int] = mapped_column(Integer, default=0)
    final_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Per-layer EV contributions
    model_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    calibration_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    league_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    latent_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    drift_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    risk_ev_contribution: Mapped[float] = mapped_column(Float, default=0.0)

    # Per-layer decisions
    model_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    calibration_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    league_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    latent_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    drift_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    final_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Outcome
    actual_outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    settled: Mapped[int] = mapped_column(Integer, default=0)
    won: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Architecture Evolution Tables ─────────────────────────────────────────────

class ArchitectureVersions(Base):
    """
    Immutable architecture snapshots for version control.
    """
    __tablename__ = "architecture_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    architecture_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    active_layers: Mapped[str] = mapped_column(Text, default="[]")
    layer_weights: Mapped[str] = mapped_column(Text, default="{}")
    feature_set: Mapped[str] = mapped_column(Text, default="{}")
    calibration_stack: Mapped[str] = mapped_column(Text, default="{}")
    
    governance_score: Mapped[float] = mapped_column(Float, default=0.0)
    ev_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    validation_score: Mapped[float] = mapped_column(Float, default=0.0)
    
    is_candidate: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ArchitectureTransitions(Base):
    """
    Architecture transition history for audit and rollback.
    """
    __tablename__ = "architecture_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_architecture: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_architecture: Mapped[str] = mapped_column(String(50), nullable=False)

    ev_delta: Mapped[float] = mapped_column(Float, default=0.0)
    risk_delta: Mapped[float] = mapped_column(Float, default=0.0)
    calibration_delta: Mapped[float] = mapped_column(Float, default=0.0)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    rolled_back: Mapped[int] = mapped_column(Integer, default=0)

    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    transition_type: Mapped[str] = mapped_column(String(20), default="upgrade")


# ── Forward Collection ────────────────────────────────────────────────────────

class OddsSnapshot(Base):
    """
    Time-series odds captures for forward-collection leagues.

    Unlike FixtureOdds (one row per fixture/bookmaker/market, always overwritten),
    each row here is one capture at a specific point in time — allowing open→close
    trajectory analysis later. No UniqueConstraint; dedup is done at the script
    level by checking for captures within the last 2 hours before inserting.
    """
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    bookmaker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bookmaker_name: Mapped[str] = mapped_column(String(100), nullable=False)
    market_type: Mapped[str] = mapped_column(String(20), nullable=False)  # h2h | ou25 | btts
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    # 1X2
    odd_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    odd_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    odd_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    # O/U 2.5
    odd_over: Mapped[float | None] = mapped_column(Float, nullable=True)
    odd_under: Mapped[float | None] = mapped_column(Float, nullable=True)

    # BTTS
    odd_btts_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    odd_btts_no: Mapped[float | None] = mapped_column(Float, nullable=True)
