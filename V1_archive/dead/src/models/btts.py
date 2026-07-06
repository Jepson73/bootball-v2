"""
src/models/btts.py - Both Teams To Score prediction

Uses Poisson goal model to calculate probability of both teams scoring.
Builds on xG features for team strength estimates.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select

from config.leagues import LEAGUES
from src.features.xg_features import XGEngine
from src.features.form import FormEngine, get_team_results
from src.storage.db import get_session
from src.storage.models import Fixture


@dataclass
class BTTSResult:
    prob_yes: float    # P(Both Teams Score)
    prob_no: float     # P(One or neither scores)


MIN_MATCHES_FOR_PREDICTION = 3


def _get_league_btts_weight(league_id: int) -> float:
    """Get league-specific BTTS weight. 1.0 = baseline."""
    if league_id in LEAGUES:
        return LEAGUES[league_id].get('btts_weight', 1.0)
    return 1.0


class BTTSPredictor:
    """Predict BTTS probability using Poisson goal model."""
    
    def __init__(self):
        self.xg_engine = XGEngine()
        self.form_engine = FormEngine()
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime | None = None,
        league_id: int | None = None,
    ) -> BTTSResult:
        """
        Calculate P(Both Teams Score) using Poisson.
        
        P(BTTS) = P(Home>0) * P(Away>0) = (1 - P(Home=0)) * (1 - P(Away=0))
        Returns neutral (0.5, 0.5) if insufficient data.
        """
        xg_values = self._get_adjusted_xg(home_team_id, away_team_id, match_date)
        
        if xg_values is None:
            return BTTSResult(prob_yes=0.5, prob_no=0.5)
        
        home_xg, away_xg = xg_values
        
        # P(X=0) = e^(-λ), so P(X>0) = 1 - e^(-λ)
        prob_home_scored = 1.0 - np.exp(-home_xg)
        prob_away_scored = 1.0 - np.exp(-away_xg)
        
        prob_yes = prob_home_scored * prob_away_scored
        prob_no = 1.0 - prob_yes
        
        if league_id is not None:
            weight = _get_league_btts_weight(league_id)
            if weight != 1.0:
                adjusted = prob_yes * weight
                adjusted = max(0.05, min(0.95, adjusted))
                prob_yes = adjusted
                prob_no = 1.0 - prob_yes
        
        return BTTSResult(
            prob_yes=prob_yes,
            prob_no=prob_no,
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
        
        # Use xG from features (built from shots proxy or goals)
        home_xg = xg_feats.get("home_xg_for_5", 0)
        away_xg = xg_feats.get("away_xg_for_5", 0)
        
        # Adjust for home advantage (~0.35 extra goals)
        home_xg += 0.35
        
        # Adjust with recent form (last 3 matches)
        home_form_results = get_team_results(home_team_id, limit=3, as_of_date=match_date)
        away_form_results = get_team_results(away_team_id, limit=3, as_of_date=match_date)
        
        home_form = sum(r.goals_for for r in home_form_results) / max(len(home_form_results), 1)
        away_form = sum(r.goals_for for r in away_form_results) / max(len(away_form_results), 1)
        
        form_mult = 1.0 + 0.1 * (home_form - away_form)
        
        return home_xg * form_mult, away_xg * (2.0 - form_mult)


def btts_features(
    home_team_id: int,
    away_team_id: int,
    match_date: datetime | None = None,
) -> dict:
    """Get features for BTTS model training."""
    xg = XGEngine()
    form = FormEngine()
    
    xg_feats = xg.get_features(home_team_id, away_team_id, match_date)
    
    return {
        "home_xg_5": xg_feats.get("home_xg_for_5", 0),
        "away_xg_5": xg_feats.get("away_xg_for_5", 0),
        "home_xg_10": xg_feats.get("home_xg_for_10", 0),
        "away_xg_10": xg_feats.get("away_xg_for_10", 0),
        "home_form": sum(r.goals_for for r in get_team_results(home_team_id, limit=3, as_of_date=match_date)) / 3,
        "away_form": sum(r.goals_for for r in get_team_results(away_team_id, limit=3, as_of_date=match_date)) / 3,
    }


# Simple function interface
def predict_btts(home_id: int, away_id: int, league_id: int | None = None) -> tuple[float, float]:
    """Predict BTTS: returns (prob_yes, prob_no)."""
    predictor = BTTSPredictor()
    result = predictor.predict_proba(home_id, away_id, league_id=league_id)
    return result.prob_yes, result.prob_no