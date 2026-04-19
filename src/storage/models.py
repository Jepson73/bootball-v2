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
    tier: Mapped[int] = mapped_column(Integer, default=3)       # 1/2/3 from config

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
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    market: Mapped[str] = mapped_column(String(20))   # h2h, btts, ou25, ou15
    model_name: Mapped[str] = mapped_column(String(50), default="ensemble")

    predicted_outcome: Mapped[str] = mapped_column(String(10))   # H/D/A, Yes/No, Over/Under
    our_prob: Mapped[float] = mapped_column(Float)
    
    sweet_spot: Mapped[bool] = mapped_column(Boolean, default=False)
    
    actual_outcome: Mapped[str | None] = mapped_column(String(10))
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    won: Mapped[bool | None] = mapped_column(Boolean)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


# ── Betting ledger: value bets found + outcomes ───────────────────────────────

class ValueBet(Base):
    __tablename__ = "value_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    model_name: Mapped[str] = mapped_column(String(100))
    market: Mapped[str] = mapped_column(String(20), default="h2h")  # h2h, btts, ou25, ou15

    outcome: Mapped[str] = mapped_column(String(10))   # H/D/A, Yes/No, Over/Under
    our_prob: Mapped[float] = mapped_column(Float)
    bookmaker_odd: Mapped[float] = mapped_column(Float)
    implied_prob: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)           # Expected Value as fraction
    kelly_fraction: Mapped[float] = mapped_column(Float)
    recommended_stake: Mapped[float | None] = mapped_column(Float)

    # Filled after match
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str | None] = mapped_column(String(10))    # actual outcome
    won: Mapped[bool | None] = mapped_column(Boolean)
    pnl: Mapped[float | None] = mapped_column(Float)         # profit/loss in units

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── Bankroll tracking ─────────────────────────────────────────────────────────

class Bankroll(Base):
    __tablename__ = "bankroll"
    __table_args__ = (
        UniqueConstraint("date", name="uq_bankroll_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(DateTime)
    balance: Mapped[float] = mapped_column(Float)           # Running balance
    total_staked: Mapped[float] = mapped_column(Float, default=0)
    total_won: Mapped[float] = mapped_column(Float, default=0)
    total_lost: Mapped[float] = mapped_column(Float, default=0)
    bet_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)

    notes: Mapped[str | None] = mapped_column(String(500))


# ── Settled bet history (for analysis) ───────────────────────────────────────

class SettledBet(Base):
    __tablename__ = "settled_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"))
    market: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(10))
    stake: Mapped[float] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    our_prob: Mapped[float] = mapped_column(Float)
    result: Mapped[str] = mapped_column(String(10))
    won: Mapped[bool] = mapped_column(Boolean)
    pnl: Mapped[float] = mapped_column(Float)
    settled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    outcome: Mapped[str] = mapped_column(String(10))
    stake: Mapped[float] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    our_prob: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    kelly_fraction: Mapped[float] = mapped_column(Float)

    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_result: Mapped[str | None] = mapped_column(String(10))
    won: Mapped[bool | None] = mapped_column(Boolean)
    pnl: Mapped[float | None] = mapped_column(Float)

    placed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)

    round: Mapped["BankrollRound"] = relationship("BankrollRound", foreign_keys=[round_id])
    fixture: Mapped["Fixture"] = relationship()


BankrollRound.bets: Mapped[list["PlacedBet"]] = relationship("PlacedBet", foreign_keys=[PlacedBet.round_id], viewonly=True)


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
