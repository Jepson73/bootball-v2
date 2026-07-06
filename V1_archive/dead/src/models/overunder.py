"""
src/models/overunder.py - Over/Under 2.5 Goals prediction

Uses Poisson goal model to calculate probability of total goals >= 2.5.
Builds on xG features for team strength estimates.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy import stats as scipy_stats

from config.leagues import LEAGUES
from src.features.xg_features import XGEngine
from src.features.form import FormEngine, get_team_results
from src.storage.db import get_session
from src.storage.models import Fixture


@dataclass
class OU25Result:
    prob_over: float    # P(Over 2.5)
    prob_under: float    # P(Under 2.5)


DEFAULT_HOME_XG = 1.0
DEFAULT_AWAY_XG = 1.0


def _get_league_over25_weight(league_id: int) -> float:
    """Get league-specific Over 2.5 weight. 1.0 = baseline."""
    if league_id in LEAGUES:
        return LEAGUES[league_id].get('over25_weight', 1.0)
    return 1.0


class OUPredictor:
    """Predict O/U 2.5 probability using Poisson goal model."""
    
    def __init__(self):
        self.xg_engine = XGEngine()
        self.form_engine = FormEngine()
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime | None = None,
        league_id: int | None = None,
    ) -> OU25Result:
        """
        Calculate P(Over 2.5) using Poisson.
        
        Over 2.5 = P(total >= 3)
        Returns neutral (0.5, 0.5) if insufficient data.
        """
        xg_values = self._get_adjusted_xg(home_team_id, away_team_id, match_date)
        
        if xg_values is None:
            return OU25Result(prob_over=0.5, prob_under=0.5)
        
        home_xg, away_xg = xg_values
        
        # Total expected goals
        total_xg = home_xg + away_xg
        
        # P(Over 2.5) = 1 - P(0) - P(1) - P(2)
        prob_under = (
            np.exp(-total_xg) +
            np.exp(-total_xg) * total_xg +
            np.exp(-total_xg) * (total_xg ** 2) / 2
        )
        prob_over = 1.0 - prob_under
        
        if league_id is not None:
            weight = _get_league_over25_weight(league_id)
            if weight != 1.0:
                adjusted = prob_over * weight
                adjusted = max(0.05, min(0.95, adjusted))
                prob_over = adjusted
                prob_under = 1.0 - prob_over
        
        return OU25Result(
            prob_over=prob_over,
            prob_under=prob_under,
        )
    
    def _get_adjusted_xg(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime | None = None,
    ) -> tuple[float, float] | None:
        """Get xG estimates with form adjustment. Returns None if insufficient data."""
        xg_feats = self.xg_engine.get_features(home_team_id, away_team_id, match_date)
        
        # Check if we have meaningful data (not just zeros from empty DB)
        home_xg = xg_feats.get("home_xg_for_5", 0)
        away_xg = xg_feats.get("away_xg_for_5", 0)
        
        if home_xg == 0 and away_xg == 0:
            return None
        
        # Home advantage adjustment
        home_xg += 0.35
        
        # Adjust with recent form
        home_results = get_team_results(home_team_id, limit=3, as_of_date=match_date)
        away_results = get_team_results(away_team_id, limit=3, as_of_date=match_date)
        
        home_form = sum(r.goals_for for r in home_results) / max(len(home_results), 1)
        away_form = sum(r.goals_for for r in away_results) / max(len(away_results), 1)
        
        form_mult = 1.0 + 0.08 * (home_form + away_form)
        
        return home_xg * form_mult, away_xg * form_mult


def ou_features(
    home_team_id: int,
    away_team_id: int,
    match_date: datetime | None = None,
) -> dict:
    """Get features for O/U model training."""
    xg = XGEngine()
    form = FormEngine()
    
    xg_feats = xg.get_features(home_team_id, away_team_id, match_date)
    
    return {
        "home_xg_5": xg_feats.get("home_xg_for_5", 0),
        "away_xg_5": xg_feats.get("away_xg_for_5", 0),
        "home_xg_10": xg_feats.get("home_xg_for_10", 0),
        "away_xg_10": xg_feats.get("away_xg_for_10", 0),
        "home_xga_5": xg_feats.get("home_xg_against_5", 0),
        "away_xga_5": xg_feats.get("away_xg_against_5", 0),
        "home_form": sum(r.goals_for for r in get_team_results(home_team_id, limit=3, as_of_date=match_date)) / 3,
        "away_form": sum(r.goals_for for r in get_team_results(away_team_id, limit=3, as_of_date=match_date)) / 3,
    }


# Simple function interface
def predict_ou25(home_id: int, away_id: int, league_id: int | None = None) -> tuple[float, float]:
    """Predict O/U 2.5: returns (prob_over, prob_under)."""
    predictor = OUPredictor()
    result = predictor.predict_proba(home_id, away_id, league_id=league_id)
    return result.prob_over, result.prob_under


def predict_ou15(home_id: int, away_id: int) -> tuple[float, float]:
    """Predict O/U 1.5: returns (prob_over, prob_under). Safer option ~70%+ hit rate."""
    predictor = OUPredictor()
    xg_values = predictor._get_adjusted_xg(home_id, away_id, None)
    
    if xg_values is None:
        return 0.5, 0.5
    
    home_xg, away_xg = xg_values
    total_xg = home_xg + away_xg
    
    prob_under = np.exp(-total_xg)
    prob_over = 1.0 - prob_under
    
    return prob_over, prob_under