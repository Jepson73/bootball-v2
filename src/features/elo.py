# src/features/elo.py - Rolling Elo ratings
"""
Elo rating system implementation for football teams.

Based on: Hvattum & Arntzen (2010) - "Elo-based rating systems for predicting 
football matches"

Key parameters:
- K-factor: 20-32 (higher for more volatile leagues)
- Home advantage: ~100 Elo points
- Margin of victory adjustment: reduces volatility
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.storage.db import get_session
from src.storage.models import Fixture, Team, EloRating


@dataclass
class EloConfig:
    """Configuration for Elo system."""
    k_factor: float = 32.0          # Learning rate
    home_advantage: float = 100.0   # Elo points for home
    initial_rating: float = 1500.0  # Default starting rating
    mov_weight: float = 0.5         # Margin of victory multiplier
    max_rating_change: float = 50.0  # Cap per game


class EloEngine:
    def __init__(self, config: EloConfig | None = None):
        self.config = config or EloConfig()

    def _get_current_rating(self, session: Session, team_id: int) -> float:
        """Get team's current Elo rating (most recent)."""
        result = session.execute(
            select(EloRating)
            .where(EloRating.team_id == team_id)
            .order_by(EloRating.as_of_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        
        if result:
            return result.rating
        return self.config.initial_rating

    def _expected_score(self, rating_a: float, rating_b: float) -> float:
        """Calculate expected score (probability of winning)."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))

    def _calculate_mov(self, goals_for: int, goals_against: int) -> float:
        """Calculate margin of victory multiplier."""
        diff = goals_for - goals_against
        if diff <= 0:
            return 0.0
        # Log-based MOV to dampen high-scoring games
        import math
        return math.log(abs(diff) + 1) / math.log(2) * self.config.mov_weight

    def update_ratings(
        self,
        fixture: Fixture,
        home_goals: int,
        away_goals: int,
    ) -> tuple[float, float]:
        """
        Update Elo ratings after a match.
        Returns (new_home_rating, new_away_rating).
        """
        with get_session() as session:
            home_rating = self._get_current_rating(session, fixture.home_team_id)
            away_rating = self._get_current_rating(session, fixture.away_team_id)
            
            # Adjust for home advantage
            home_adj = home_rating + self.config.home_advantage
            
            # Calculate expected scores
            exp_home = self._expected_score(home_adj, away_rating)
            exp_away = self._expected_score(away_rating, home_adj)
            
            # Actual score (1 for win, 0.5 for draw, 0 for loss)
            if home_goals > away_goals:
                actual_home, actual_away = 1.0, 0.0
            elif home_goals < away_goals:
                actual_home, actual_away = 0.0, 1.0
            else:
                actual_home, actual_away = 0.5, 0.5
            
            # Calculate margin of victory
            mov = self._calculate_mov(home_goals, away_goals)
            
            # Calculate rating changes
            change_home = min(
                self.config.k_factor * (actual_home - exp_home) * (1 + mov),
                self.config.max_rating_change
            )
            change_away = min(
                self.config.k_factor * (actual_away - exp_away) * (1 + mov),
                self.config.max_rating_change
            )
            
            new_home = home_rating + change_home
            new_away = away_rating + change_away
            
            # Store new ratings
            now = datetime.utcnow()
            session.add(EloRating(
                team_id=fixture.home_team_id,
                as_of_date=now,
                rating=new_home,
                games_played=1,  # Would need to track cumulative
            ))
            session.add(EloRating(
                team_id=fixture.away_team_id,
                as_of_date=now,
                rating=new_away,
                games_played=1,
            ))
            
            return new_home, new_away

    def get_ratings(self, team_ids: list[int] | None = None) -> dict[int, float]:
        """Get current ratings for specified teams or all teams."""
        with get_session() as session:
            if team_ids:
                ratings = {}
                for tid in team_ids:
                    ratings[tid] = self._get_current_rating(session, tid)
                return ratings
            
            # Get all teams with their latest rating
            subq = (
                select(
                    EloRating.team_id,
                    func.max(EloRating.as_of_date).label("max_date")
                )
                .group_by(EloRating.team_id)
                .subquery()
            )
            
            result = session.execute(
                select(EloRating)
                .join(subq, EloRating.team_id == subq.c.team_id)
                .where(EloRating.as_of_date == subq.c.max_date)
            ).scalars().all()
            
            return {r.team_id: r.rating for r in result}

    def predict(self, home_team_id: int, away_team_id: int) -> tuple[float, float, float]:
        """
        Predict match outcome probabilities based on current Elo ratings.
        Returns (prob_home, prob_draw, prob_away).
        """
        with get_session() as session:
            home_rating = self._get_current_rating(session, home_team_id)
            away_rating = self._get_current_rating(session, away_team_id)
            
            # Add home advantage
            home_adj = home_rating + self.config.home_advantage
            
            # Calculate win probabilities
            prob_home_win = self._expected_score(home_adj, away_rating)
            prob_away_win = self._expected_score(away_rating, home_adj)
            
            # Draw probability (using 1 - sum method with home advantage adjustment)
            # Based on research, draws happen ~25-30% of the time
            prob_draw = 1.0 - prob_home_win - prob_away_win
            prob_draw = max(0.0, min(prob_draw, 0.5))  # Bound between 0 and 0.5
            
            # Renormalize
            total = prob_home_win + prob_draw + prob_away_win
            return (
                prob_home_win / total,
                prob_draw / total,
                prob_away_win / total,
            )


def update_all_ratings() -> None:
    """Update Elo ratings for all completed fixtures."""
    engine = EloEngine()
    
    with get_session() as session:
        # Get all completed fixtures without ratings
        fixtures = session.execute(
            select(Fixture)
            .where(Fixture.status == "FT")
            .where(Fixture.goals_home.isnot(None))
        ).scalars().all()
        
        for f in fixtures:
            try:
                engine.update_ratings(f, f.goals_home, f.goals_away)
            except Exception as e:
                print(f"Error updating ratings for fixture {f.id}: {e}")
    
    print(f"Updated Elo ratings for {len(fixtures)} fixtures")