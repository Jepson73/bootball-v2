# src/features/form.py - Recent form, momentum, fatigue
"""
Form features: recent performance metrics for teams.

Features:
- Last N matches form (win/draw/loss points)
- Home/away split performance
- Fatigue indicator (days rest between matches)
- Momentum (trend in recent results)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.db import get_session
from src.storage.models import Fixture, Team


@dataclass
class MatchResult:
    """Single match result for form calculation."""
    date: datetime
    is_home: bool
    goals_for: int
    goals_against: int
    outcome: str  # H, D, A


def get_team_results(
    team_id: int,
    limit: int = 10,
    as_of_date: datetime | None = None,
) -> list[MatchResult]:
    """Get team's recent match results before a given date."""
    with get_session() as session:
        query = (
            select(Fixture)
            .where(
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
            )
            .where(Fixture.status == "FT")
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
        )
        
        if as_of_date:
            query = query.where(Fixture.date < as_of_date)
        
        fixtures = (
            session.execute(
                query.order_by(Fixture.date.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        
        results = []
        for f in fixtures:
            if f.home_team_id == team_id:
                is_home = True
                goals_for = f.goals_home
                goals_against = f.goals_away
            else:
                is_home = False
                goals_for = f.goals_away
                goals_against = f.goals_home
            
            # Skip if goals are None
            if goals_for is None or goals_against is None:
                continue
            
            # Determine outcome from team perspective
            if goals_for > goals_against:
                outcome = "W"
            elif goals_for == goals_against:
                outcome = "D"
            else:
                outcome = "L"
            
            results.append(MatchResult(
                date=f.date,
                is_home=is_home,
                goals_for=goals_for,
                goals_against=goals_against,
                outcome=outcome,
            ))
        
        return list(reversed(results))  # Chronological order


def calculate_form_points(results: list[MatchResult], last_n: int = 5) -> float:
    """Calculate form points (3 for W, 1 for D, 0 for L) over last N matches."""
    if not results:
        return 0.0
    
    recent = results[-last_n:]
    points = sum(3 if r.outcome == "W" else 1 if r.outcome == "D" else 0 for r in recent)
    return points / (3 * last_n)  # Normalize to 0-1


def calculate_win_rate(results: list[MatchResult], last_n: int = 5) -> float:
    """Calculate win rate over last N matches."""
    if not results:
        return 0.0
    
    recent = results[-last_n:]
    wins = sum(1 for r in recent if r.outcome == "W")
    return wins / len(recent)


def calculate_avg_goals(results: list[MatchResult], last_n: int = 5, is_home: bool | None = None) -> float:
    """Calculate average goals scored (for) or conceded."""
    if not results:
        return 0.0
    
    recent = results[-last_n:]
    if is_home is not None:
        recent = [r for r in recent if r.is_home == is_home]
    
    if not recent:
        return 0.0
    
    return sum(r.goals_for for r in recent) / len(recent)


def calculate_goals_conceded(results: list[MatchResult], last_n: int = 5, is_home: bool | None = None) -> float:
    """Calculate average goals conceded."""
    if not results:
        return 0.0
    
    recent = results[-last_n:]
    if is_home is not None:
        recent = [r for r in recent if r.is_home == is_home]
    
    if not recent:
        return 0.0
    
    return sum(r.goals_against for r in recent) / len(recent)


def calculate_days_rest(
    team_id: int,
    match_date: datetime,
) -> int:
    """Calculate days since team's last match."""
    with get_session() as session:
        prev = (
            session.execute(
                select(Fixture)
                .where(
                    (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
                )
                .where(Fixture.status == "FT")
                .where(Fixture.date < match_date)
                .order_by(Fixture.date.desc())
                .limit(1)
            )
            .scalar_one_or_none()
        )
        
        if not prev:
            return 30  # Default for first match of season
        
        delta = match_date - prev.date
        return delta.days


def calculate_momentum(results: list[MatchResult], last_n: int = 5) -> float:
    """
    Calculate momentum: difference in form between last half and first half of last N.
    Positive = improving, negative = declining.
    """
    if len(results) < last_n:
        return 0.0
    
    recent = results[-last_n:]
    half = last_n // 2
    
    first_half = recent[:half]
    second_half = recent[half:]
    
    first_pts = sum(
        3 if r.outcome == "W" else 1 if r.outcome == "D" else 0 
        for r in first_half
    ) / (3 * half) if half > 0 else 0
    
    second_pts = sum(
        3 if r.outcome == "W" else 1 if r.outcome == "D" else 0 
        for r in second_half
    ) / (3 * (len(recent) - half)) if (len(recent) - half) > 0 else 0
    
    return second_pts - first_pts


class FormEngine:
    """Compute form features for a match."""
    
    def __init__(self):
        self.form_window = 5
        self.form_window_large = 10
    
    def get_features(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime,
    ) -> dict:
        """Get all form features for a matchup."""
        home_results = get_team_results(home_team_id, limit=10, as_of_date=match_date)
        away_results = get_team_results(away_team_id, limit=10, as_of_date=match_date)
        
        return {
            # Home team features
            "home_form_5p": calculate_form_points(home_results, self.form_window),
            "home_form_10p": calculate_form_points(home_results, self.form_window_large),
            "home_win_rate_5": calculate_win_rate(home_results, self.form_window),
            "home_goals_scored_5": calculate_avg_goals(home_results, self.form_window),
            "home_goals_conceded_5": calculate_goals_conceded(home_results, self.form_window),
            "home_goals_scored_home_5": calculate_avg_goals(home_results, self.form_window, is_home=True),
            "home_momentum": calculate_momentum(home_results, self.form_window),
            "home_days_rest": calculate_days_rest(home_team_id, match_date),
            
            # Away team features
            "away_form_5p": calculate_form_points(away_results, self.form_window),
            "away_form_10p": calculate_form_points(away_results, self.form_window_large),
            "away_win_rate_5": calculate_win_rate(away_results, self.form_window),
            "away_goals_scored_5": calculate_avg_goals(away_results, self.form_window),
            "away_goals_conceded_5": calculate_goals_conceded(away_results, self.form_window),
            "away_goals_scored_away_5": calculate_avg_goals(away_results, self.form_window, is_home=False),
            "away_momentum": calculate_momentum(away_results, self.form_window),
            "away_days_rest": calculate_days_rest(away_team_id, match_date),
            
            # Differential features
            "form_diff_5p": (
                calculate_form_points(home_results, self.form_window) -
                calculate_form_points(away_results, self.form_window)
            ),
            "rest_diff": (
                calculate_days_rest(home_team_id, match_date) -
                calculate_days_rest(away_team_id, match_date)
            ),
        }


def compute_all_form_features() -> None:
    """Compute and store form features for all upcoming fixtures."""
    engine = FormEngine()
    
    with get_session() as session:
        # Get all upcoming fixtures
        fixtures = session.execute(
            select(Fixture)
            .where(Fixture.status == "NS")
            .where(Fixture.date.isnot(None))
            .order_by(Fixture.date)
        ).scalars().all()
        
        print(f"Computing form features for {len(fixtures)} upcoming fixtures...")
        
        for f in fixtures:
            try:
                features = engine.get_features(
                    f.home_team_id,
                    f.away_team_id,
                    f.date,
                )
                # Features are returned as dict - could store in Feature table
                # For now, just print (would integrate with prediction pipeline)
                print(f"Fixture {f.id}: {features}")
            except Exception as e:
                print(f"Error computing form for fixture {f.id}: {e}")
    
    print("Done computing form features")