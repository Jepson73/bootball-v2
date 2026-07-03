# src/features/elo.py - Rolling Elo ratings
"""
Elo rating system for football teams.

Draw model (Hvattum & Arntzen 2010 style):
  p_draw = BASE_DRAW * exp(-|home_adj - away| / 400)
  p_home = E(home) * (1 - p_draw)
  p_away = E(away) * (1 - p_draw)
  where E(h) + E(a) == 1 by logistic complement, so the three probs sum to 1.

Pool separation:
  'club'     — populated from domestic-league FT fixtures (country != 'World')
  'national' — reserved for Part B; never populated by Part A code

Ratings are stored as one row per team representing their current state after
all processed fixtures. update_all_ratings() clears the pool and rebuilds from
scratch, so repeated runs are safe.
"""
from __future__ import annotations

import inspect
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.storage.db import get_session
from src.storage.models import EloRating, Fixture, League

logger = logging.getLogger(__name__)


@dataclass
class EloConfig:
    k_factor: float = 32.0
    home_advantage: float = 100.0
    initial_rating: float = 1500.0
    mov_weight: float = 0.5
    max_rating_change: float = 50.0
    draw_base_rate: float = 0.30


class EloEngine:
    def __init__(self, config: EloConfig | None = None):
        self.config = config or EloConfig()

    def _expected_score(self, rating_a: float, rating_b: float) -> float:
        """Logistic win probability for team A vs team B."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))

    def _calculate_mov(self, goals_for: int, goals_against: int) -> float:
        """Log-scale margin-of-victory multiplier (0 for draws/losses)."""
        diff = goals_for - goals_against
        if diff <= 0:
            return 0.0
        return math.log(diff + 1) / math.log(2) * self.config.mov_weight

    def _get_current_rating(self, session: Session, team_id: int, pool: str = "club") -> float:
        """Most recent stored rating for team in pool; default 1500 if none."""
        row = session.execute(
            select(EloRating)
            .where(EloRating.team_id == team_id)
            .where(EloRating.pool == pool)
            .order_by(EloRating.as_of_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row.rating if row else self.config.initial_rating

    def predict(
        self,
        home_team_id: int,
        away_team_id: int,
        pool: str = "club",
    ) -> tuple[float, float, float]:
        """
        Return (prob_home, prob_draw, prob_away) using stored pool ratings.

        Draw model: p_draw peaks at draw_base_rate for equal teams and decays
        exponentially with Elo separation, so prob_draw > 0 for any finite diff.
        """
        with get_session() as session:
            h_r = self._get_current_rating(session, home_team_id, pool)
            a_r = self._get_current_rating(session, away_team_id, pool)

        h_adj = h_r + self.config.home_advantage
        delta = abs(h_adj - a_r)

        p_draw = self.config.draw_base_rate * math.exp(-delta / 400)
        remaining = 1.0 - p_draw
        e_h = self._expected_score(h_adj, a_r)
        p_home = e_h * remaining
        p_away = (1.0 - e_h) * remaining

        return (p_home, p_draw, p_away)

    def predict_from_ratings(
        self,
        home_rating: float,
        away_rating: float,
    ) -> tuple[float, float, float]:
        """Predict without a DB lookup — used when caller already has ratings."""
        h_adj = home_rating + self.config.home_advantage
        delta = abs(h_adj - away_rating)
        p_draw = self.config.draw_base_rate * math.exp(-delta / 400)
        remaining = 1.0 - p_draw
        e_h = self._expected_score(h_adj, away_rating)
        return (e_h * remaining, p_draw, (1.0 - e_h) * remaining)

    def get_ratings(self, team_ids: list[int] | None = None, pool: str = "club") -> dict[int, float]:
        """Return current rating dict for specified teams (or all) in pool."""
        with get_session() as session:
            if team_ids:
                return {tid: self._get_current_rating(session, tid, pool) for tid in team_ids}

            rows = session.execute(
                select(EloRating)
                .where(EloRating.pool == pool)
                .order_by(EloRating.team_id, EloRating.as_of_date.desc())
            ).scalars().all()
            seen: set[int] = set()
            result: dict[int, float] = {}
            for r in rows:
                if r.team_id not in seen:
                    result[r.team_id] = r.rating
                    seen.add(r.team_id)
            return result


# Senior national-team competitions used to build the national Elo pool.
# Excludes: women, youth (U17/U19/U21), club competitions, CHAN (locally-based players).
# Includes Friendlies (10) — 42 of 2492 FT fixtures involve a club-rated team (1.7%).
# Bridge teams are incidental: Hull City, Alanyaspor, etc. in one-off national warmups.
# Their presence does not meaningfully affect national-team ratings.
NATIONAL_POOL_LEAGUES: frozenset[int] = frozenset({
    1,    # World Cup
    4,    # Euro Championship
    5,    # UEFA Nations League
    6,    # Africa Cup of Nations
    7,    # Asian Cup
    9,    # Copa America
    10,   # Friendlies (national — see contamination note above)
    22,   # CONCACAF Gold Cup
    29,   # World Cup - Qualification Africa
    30,   # World Cup - Qualification Asia
    31,   # World Cup - Qualification CONCACAF
    32,   # World Cup - Qualification Europe
    34,   # World Cup - Qualification South America
    35,   # Asian Cup - Qualification
    36,   # Africa Cup of Nations - Qualification
    536,  # CONCACAF Nations League
    858,  # CONCACAF Gold Cup - Qualification
    960,  # Euro Championship - Qualification
})


def update_all_ratings(pool: str = "club") -> int:
    """
    Rebuild Elo ratings for all teams in *pool* from FT fixtures.

    Club pool:     fixtures from leagues with country != 'World' (domestic only).
    National pool: fixtures from NATIONAL_POOL_LEAGUES whitelist.

    Processes fixtures in date order (Elo is path-dependent). Stores one row per
    team representing their final rating + cumulative games_played. Clears the
    pool first (pool-scoped DELETE), so repeated runs are idempotent and the
    other pool is never touched.

    Every call is recorded to elo_rebuild_log (Phase 28 — a prior rebuild ran
    with no traceable invoker, discovered during the Phase 27 settlement audit).
    The caller's module is captured automatically via the call stack so no
    call site needs to remember to pass it.

    Returns the number of FT fixtures processed.
    """
    caller_frame = inspect.stack()[1]
    invoked_by = f"{caller_frame.filename}:{caller_frame.function}"

    config = EloConfig()
    engine = EloEngine(config)

    with get_session() as session:
        session.execute(
            text("DELETE FROM elo_ratings WHERE pool = :pool"),
            {"pool": pool},
        )

        if pool == "club":
            fixtures = session.execute(
                select(Fixture)
                .join(League, Fixture.league_id == League.id)
                .where(Fixture.status == "FT")
                .where(Fixture.goals_home.isnot(None))
                .where(Fixture.goals_away.isnot(None))
                .where(League.country != "World")
                .order_by(Fixture.date)
            ).scalars().all()
        else:
            fixtures = session.execute(
                select(Fixture)
                .join(League, Fixture.league_id == League.id)
                .where(Fixture.status == "FT")
                .where(Fixture.goals_home.isnot(None))
                .where(Fixture.goals_away.isnot(None))
                .where(League.id.in_(NATIONAL_POOL_LEAGUES))
                .order_by(Fixture.date)
            ).scalars().all()

        # In-memory Elo pass — no per-fixture DB writes
        ratings: dict[int, float] = {}
        games: dict[int, int] = {}
        latest_date: dict[int, datetime] = {}

        for f in fixtures:
            h_id, a_id = f.home_team_id, f.away_team_id
            h_r = ratings.get(h_id, config.initial_rating)
            a_r = ratings.get(a_id, config.initial_rating)

            h_adj = h_r + config.home_advantage
            exp_h = engine._expected_score(h_adj, a_r)
            exp_a = 1.0 - exp_h

            if f.goals_home > f.goals_away:
                act_h, act_a = 1.0, 0.0
            elif f.goals_home < f.goals_away:
                act_h, act_a = 0.0, 1.0
            else:
                act_h, act_a = 0.5, 0.5

            mov = engine._calculate_mov(f.goals_home, f.goals_away)

            ch = min(config.k_factor * (act_h - exp_h) * (1 + mov), config.max_rating_change)
            ca = min(config.k_factor * (act_a - exp_a) * (1 + mov), config.max_rating_change)

            ratings[h_id] = h_r + ch
            ratings[a_id] = a_r + ca
            games[h_id] = games.get(h_id, 0) + 1
            games[a_id] = games.get(a_id, 0) + 1
            latest_date[h_id] = f.date
            latest_date[a_id] = f.date

        if not ratings:
            session.execute(
                text(
                    "INSERT INTO elo_rebuild_log (pool, invoked_by, fixtures_processed, latest_fixture_ceiling) "
                    "VALUES (:pool, :invoked_by, 0, NULL)"
                ),
                {"pool": pool, "invoked_by": invoked_by},
            )
            logger.info("update_all_ratings: pool=%s invoked_by=%s — 0 fixtures, nothing to rebuild", pool, invoked_by)
            return 0

        # Bulk insert — one row per team (final state)
        rows = [
            {
                "team_id": tid,
                "as_of_date": latest_date[tid],
                "rating": rat,
                "games_played": games[tid],
                "pool": pool,
            }
            for tid, rat in ratings.items()
        ]
        session.execute(
            text(
                "INSERT INTO elo_ratings (team_id, as_of_date, rating, games_played, pool) "
                "VALUES (:team_id, :as_of_date, :rating, :games_played, :pool)"
            ),
            rows,
        )

        ceiling = max(latest_date.values())
        session.execute(
            text(
                "INSERT INTO elo_rebuild_log (pool, invoked_by, fixtures_processed, latest_fixture_ceiling) "
                "VALUES (:pool, :invoked_by, :n, :ceiling)"
            ),
            {"pool": pool, "invoked_by": invoked_by, "n": len(fixtures), "ceiling": ceiling},
        )
        logger.info(
            "update_all_ratings: pool=%s invoked_by=%s fixtures=%d latest_fixture_ceiling=%s",
            pool, invoked_by, len(fixtures), ceiling,
        )

        return len(fixtures)
