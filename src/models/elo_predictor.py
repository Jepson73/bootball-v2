# src/models/elo_predictor.py - Elo-based predictor
"""
Simple Elo-based predictor using the Elo features module.

This is a baseline model - not as sophisticated as Dixon-Coles but useful for comparison.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture
from src.features.elo import EloEngine, EloConfig


class EloPredictor:
    """Elo-based match predictor."""
    
    def __init__(self):
        self._engine = EloEngine()
    
    def fit_update_ratings(self, fixtures: list[Fixture]) -> None:
        """Update Elo ratings based on fixtures."""
        for f in fixtures:
            if f.status == "FT" and f.goals_home is not None:
                try:
                    self._engine.update_ratings(f, f.goals_home, f.goals_away)
                except Exception as e:
                    print(f"Error updating Elo for fixture {f.id}: {e}")
        
        print(f"Updated Elo ratings for {len(fixtures)} fixtures")
    
    def fit(self, league_id: int | None = None) -> "EloPredictor":
        """Load historical fixtures and update ratings."""
        with get_session() as session:
            query = select(Fixture).where(Fixture.status == "FT")
            if league_id:
                query = query.where(Fixture.league_id == league_id)
            
            fixtures = session.execute(query).scalars().all()
            self.fit_update_ratings(fixtures)
        
        return self
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> tuple[float, float, float]:
        """Predict probabilities using Elo ratings."""
        return self._engine.predict(home_team_id, away_team_id)
    
    def predict(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> str:
        """Predict match outcome."""
        probs = self.predict_proba(home_team_id, away_team_id)
        outcomes = ['H', 'D', 'A']
        return outcomes[np.argmax(probs)]


def evaluate_rps(model, test_fixtures: list[Fixture]) -> float:
    """Calculate Ranked Probability Score."""
    total_rps = 0.0
    
    for f in test_fixtures:
        probs = model.predict_proba(f.home_team_id, f.away_team_id)
        
        if f.goals_home > f.goals_away:
            actual = 0
        elif f.goals_home == f.goals_away:
            actual = 1
        else:
            actual = 2
        
        for i in range(3):
            cum_pred = sum(probs[:i+1])
            cum_actual = 1.0 if actual <= i else 0.0
            total_rps += (cum_pred - cum_actual) ** 2
    
    return total_rps / (3 * len(test_fixtures)) if test_fixtures else 0.0