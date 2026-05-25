# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: half-time score prediction models; potential future HT/FT market support
"""
src/models/halftime.py - Half-time prediction models

Uses half-time goal data to predict:
- BTTS 1H: Both teams score in first half
- BTTS 2H: Both teams score in second half
- Goals 1H: Total goals in first half
- Goals 2H: Total goals in second half

Key insight: Late-season A-League and MLS have high 80-90 min scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from sqlalchemy import select

from src.features.xg_features import XGEngine
from src.features.form import FormEngine, get_team_results
from src.storage.db import get_session
from src.storage.models import Fixture


HALFTIME_HOME_ADVANTAGE = 0.15


@dataclass
class HalfTimeResult:
    btts_1h_yes: float
    btts_1h_no: float
    btts_2h_yes: float
    btts_2h_no: float
    goals_1h: float
    goals_2h: float


def get_ht_stats(home_id: int, away_id: int, limit: int = 10) -> dict:
    """
    Get half-time scoring stats from recent matches.
    Returns average HT goals for/against for both teams.
    """
    with get_session() as s:
        fixtures = s.execute(
            select(Fixture).where(
                Fixture.status == 'FT',
                Fixture.ht_goals_home != None,
                ((Fixture.home_team_id == home_id) | (Fixture.away_team_id == away_id)),
            )
        ).scalars().all()

    if not fixtures:
        return {
            "home_ht_for": 0.5,
            "home_ht_against": 0.5,
            "away_ht_for": 0.5,
            "away_ht_against": 0.5,
        }

    home_ht_scored = []
    home_ht_conceded = []
    away_ht_scored = []
    away_ht_conceded = []

    for f in fixtures:
        if f.home_team_id == home_id:
            home_ht_scored.append(f.ht_goals_home or 0)
            home_ht_conceded.append(f.ht_goals_away or 0)
        if f.away_team_id == away_id:
            away_ht_scored.append(f.ht_goals_away or 0)
            away_ht_conceded.append(f.ht_goals_home or 0)

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0.5

    return {
        "home_ht_for": avg(home_ht_scored) + HALFTIME_HOME_ADVANTAGE,
        "home_ht_against": avg(home_ht_conceded),
        "away_ht_for": avg(away_ht_scored),
        "away_ht_against": avg(away_ht_conceded),
    }


def predict_halftime(home_id: int, away_id: int) -> HalfTimeResult:
    """
    Predict half-time outcomes using Poisson model.

    BTTS 1H = P(home_ht > 0) * P(away_ht > 0)
    BTTS 2H = P(2h_home > 0) * P(2h_away > 0)
              where 2H goals = FT goals - HT goals
    """
    stats = get_ht_stats(home_id, away_id)

    home_ht_lambda = stats["home_ht_for"]
    away_ht_lambda = stats["away_ht_for"]

    # BTTS 1H
    prob_home_ht_scored = 1.0 - np.exp(-home_ht_lambda)
    prob_away_ht_scored = 1.0 - np.exp(-away_ht_lambda)
    btts_1h_yes = prob_home_ht_scored * prob_away_ht_scored
    btts_1h_no = 1.0 - btts_1h_yes

    # 2H goals estimated as ~55% of full game goals (second half often more goals)
    home_2h_lambda = home_ht_lambda * 0.6
    away_2h_lambda = away_ht_lambda * 0.6

    # BTTS 2H
    prob_home_2h_scored = 1.0 - np.exp(-home_2h_lambda)
    prob_away_2h_scored = 1.0 - np.exp(-away_2h_lambda)
    btts_2h_yes = prob_home_2h_scored * prob_away_2h_scored
    btts_2h_no = 1.0 - btts_2h_yes

    # Expected goals per half
    goals_1h = home_ht_lambda + away_ht_lambda
    goals_2h = home_2h_lambda + away_2h_lambda

    return HalfTimeResult(
        btts_1h_yes=btts_1h_yes,
        btts_1h_no=btts_1h_no,
        btts_2h_yes=btts_2h_yes,
        btts_2h_no=btts_2h_no,
        goals_1h=goals_1h,
        goals_2h=goals_2h,
    )


def predict_btts_1h(home_id: int, away_id: int) -> tuple[float, float]:
    """Predict BTTS in first half. Returns (prob_yes, prob_no)."""
    result = predict_halftime(home_id, away_id)
    return result.btts_1h_yes, result.btts_1h_no


def predict_btts_2h(home_id: int, away_id: int) -> tuple[float, float]:
    """Predict BTTS in second half. Returns (prob_yes, prob_no)."""
    result = predict_halftime(home_id, away_id)
    return result.btts_2h_yes, result.btts_2h_no
