# src/features/xg_features.py - xG rolling averages
"""
Expected Goals (xG) features based on historical performance.

If API provides xG data directly, use it. Otherwise, use shots on target as proxy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture, FixtureStats, Team, Injury


# Position impact weights on goals (research-based)
POSITION_GOAL_IMPACT = {
    "Goalkeeper": -0.15,
    "Defender": -0.10,
    "Midfielder": -0.20,
    "Attacker": -0.40,
    "Forward": -0.40,
}
DEFAULT_POSITION_IMPACT = -0.10


@dataclass
class XGFeatures:
    """xG-based features for a team."""
    xg_for_5: float      # Avg xG for, last 5 matches
    xg_against_5: float  # Avg xG against, last 5
    xg_for_10: float
    xg_against_10: float
    shots_on_target_5: float  # Proxy if xG not available
    shots_on_target_against_5: float


def get_xg_for_team(
    team_id: int,
    is_home: bool | None = None,
    last_n: int = 5,
    as_of_date: datetime | None = None,
) -> list[float]:
    """Get xG for (goals scored) for last N matches.
    Uses actual goals as xG proxy if xG data unavailable."""
    with get_session() as session:
        # Use LEFT OUTER JOIN so we get fixtures even without stats
        query = (
            select(Fixture, FixtureStats)
            .outerjoin(FixtureStats, Fixture.id == FixtureStats.fixture_id)
            .where(
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
            )
            .where(Fixture.status == "FT")
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
        )
        
        if as_of_date:
            query = query.where(Fixture.date < as_of_date)
        
        results = session.execute(
            query.order_by(Fixture.date.desc()).limit(last_n * 2)
        ).all()
        
        xg_values = []
        for fixture, stats in results:
            if stats is None:
                # No stats - use actual goals as proxy
                if fixture.home_team_id == team_id:
                    xg = fixture.goals_home
                else:
                    xg = fixture.goals_away
            else:
                if is_home is not None:
                    if is_home and fixture.home_team_id != team_id:
                        continue
                    if not is_home and fixture.away_team_id != team_id:
                        continue
                
                # Priority: xG > shots proxy > actual goals
                if fixture.home_team_id == team_id:
                    # Team is HOME
                    if stats.home_xg is not None:
                        xg = stats.home_xg
                    elif stats.home_shots_on_goal is not None:
                        xg = shots_to_xg(stats.home_shots_on_goal)
                    else:
                        xg = fixture.goals_home
                else:
                    # Team is AWAY
                    if stats.away_xg is not None:
                        xg = stats.away_xg
                    elif stats.away_shots_on_goal is not None:
                        xg = shots_to_xg(stats.away_shots_on_goal)
                    else:
                        xg = fixture.goals_away
            
            if xg is not None:
                xg_values.append(float(xg))
            
            if len(xg_values) >= last_n:
                break
        
        return xg_values


def get_xg_against_team(
    team_id: int,
    is_home: bool | None = None,
    last_n: int = 5,
    as_of_date: datetime | None = None,
) -> list[float]:
    """Get xG against (goals conceded) for last N matches.
    Uses actual goals as xG proxy if xG data unavailable."""
    with get_session() as session:
        # Use LEFT OUTER JOIN so we get fixtures even without stats
        query = (
            select(Fixture, FixtureStats)
            .outerjoin(FixtureStats, Fixture.id == FixtureStats.fixture_id)
            .where(
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
            )
            .where(Fixture.status == "FT")
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
        )
        
        if as_of_date:
            query = query.where(Fixture.date < as_of_date)
        
        results = session.execute(
            query.order_by(Fixture.date.desc()).limit(last_n * 2)
        ).all()
        
        xg_values = []
        for fixture, stats in results:
            # Handle case where stats is None (no stats for this fixture)
            if stats is None:
                # Use actual goals as proxy
                if fixture.home_team_id == team_id:
                    xg = fixture.goals_away  # conceded (away team scored)
                else:
                    xg = fixture.goals_home  # conceded (home team scored)
            else:
                if is_home is not None:
                    if is_home and fixture.home_team_id != team_id:
                        continue
                    if not is_home and fixture.away_team_id != team_id:
                        continue
                
                # Priority: xG > shots proxy > actual goals
                if fixture.home_team_id == team_id:
                    if stats.away_xg is not None:
                        xg = stats.away_xg
                    elif stats.away_shots_on_goal is not None:
                        xg = shots_to_xg(stats.away_shots_on_goal)
                    else:
                        xg = fixture.goals_away
                else:
                    if stats.home_xg is not None:
                        xg = stats.home_xg
                    elif stats.home_shots_on_goal is not None:
                        xg = shots_to_xg(stats.home_shots_on_goal)
                    else:
                        xg = fixture.goals_home
            
            if xg is not None:
                xg_values.append(float(xg))
            
            if len(xg_values) >= last_n:
                break
        
        return xg_values


def get_shots_on_target(
    team_id: int,
    is_home: bool | None = None,
    last_n: int = 5,
    as_of_date: datetime | None = None,
    for_team: bool = True,
) -> list[float]:
    """Get shots on target (proxy for xG if xG not available)."""
    with get_session() as session:
        # Use LEFT OUTER JOIN so we get fixtures even without stats
        query = (
            select(Fixture, FixtureStats)
            .outerjoin(FixtureStats, Fixture.id == FixtureStats.fixture_id)
            .where(
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
            )
            .where(Fixture.status == "FT")
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
        )
        
        if as_of_date:
            query = query.where(Fixture.date < as_of_date)
        
        results = session.execute(
            query.order_by(Fixture.date.desc()).limit(last_n * 2)
        ).all()
        
        shot_values = []
        for fixture, stats in results:
            if stats is None:
                # No stats available - skip (we can't get shots without API data)
                continue
                
            if is_home is not None:
                if is_home and fixture.home_team_id != team_id:
                    continue
                if not is_home and fixture.away_team_id != team_id:
                    continue
            
            if for_team:
                if fixture.home_team_id == team_id:
                    shots = stats.home_shots_on_goal
                else:
                    shots = stats.away_shots_on_goal
            else:
                if fixture.home_team_id == team_id:
                    shots = stats.away_shots_on_goal
                else:
                    shots = stats.home_shots_on_goal
            
            if shots is not None:
                shot_values.append(float(shots))
            
            if len(shot_values) >= last_n:
                break
        
        return shot_values


CONVERSION_RATE = 0.33  # goals per shot on target (empirically derived)


def shots_to_xg(shots: float) -> float:
    """Convert shots on target to xG proxy.
    
    Mathematically sound: xG = shots_on_target × probability_of_goal_per_shot
    Empirically, probability ≈ 33% (from 8154 goals / 24810 shots)
    
    This is a proper xG proxy when API xG is unavailable.
    """
    return shots * CONVERSION_RATE


class XGEngine:
    """Compute xG-based features."""
    
    def __init__(self):
        self.window_small = 5
        self.window_large = 10
    
    def get_features(
        self,
        home_team_id: int,
        away_team_id: int,
        match_date: datetime | None = None,
    ) -> dict:
        """Get all xG features for a matchup."""
        # Home team xG for
        home_xg_for_5 = get_xg_for_team(home_team_id, last_n=self.window_small, as_of_date=match_date)
        home_xg_for_10 = get_xg_for_team(home_team_id, last_n=self.window_large, as_of_date=match_date)
        
        # Home team xG against
        home_xg_against_5 = get_xg_against_team(home_team_id, last_n=self.window_small, as_of_date=match_date)
        home_xg_against_10 = get_xg_against_team(home_team_id, last_n=self.window_large, as_of_date=match_date)
        
        # Away team xG for
        away_xg_for_5 = get_xg_for_team(away_team_id, last_n=self.window_small, as_of_date=match_date)
        away_xg_for_10 = get_xg_for_team(away_team_id, last_n=self.window_large, as_of_date=match_date)
        
        # Away team xG against
        away_xg_against_5 = get_xg_against_team(away_team_id, last_n=self.window_small, as_of_date=match_date)
        away_xg_against_10 = get_xg_against_team(away_team_id, last_n=self.window_large, as_of_date=match_date)
        
        # Shots on target (proxy)
        home_sot_5 = get_shots_on_target(home_team_id, is_home=True, last_n=self.window_small, as_of_date=match_date)
        away_sot_5 = get_shots_on_target(away_team_id, is_home=False, last_n=self.window_small, as_of_date=match_date)
        
        # Compute averages
        def avg(lst):
            return sum(lst) / len(lst) if lst else 0.0
        
        return {
            # Home team
            "home_xg_for_5": avg(home_xg_for_5),
            "home_xg_against_5": avg(home_xg_against_5),
            "home_xg_for_10": avg(home_xg_for_10),
            "home_xg_against_10": avg(home_xg_against_10),
            "home_shots_on_target_5": avg(home_sot_5),
            
            # Away team
            "away_xg_for_5": avg(away_xg_for_5),
            "away_xg_against_5": avg(away_xg_against_5),
            "away_xg_for_10": avg(away_xg_for_10),
            "away_xg_against_10": avg(away_xg_against_10),
            "away_shots_on_target_5": avg(away_sot_5),
            
            # Differential
            "xg_diff_5": avg(home_xg_for_5) - avg(away_xg_for_5),
            "xg_diff_10": avg(home_xg_for_10) - avg(away_xg_for_10),
            "xga_diff_5": avg(home_xg_against_5) - avg(away_xg_against_5),
        }


def check_xg_data_available():
    """Check if xG data is available in the database."""
    with get_session() as session:
        # Sample some stats
        stats = session.execute(
            select(FixtureStats)
            .limit(10)
        ).all()
        
        print("Checking xG availability in fixture_stats:")
        for s in stats:
            print(f"  Home xG: {s[0].home_xg}, Away xG: {s[0].away_xg}")
        
        # Count non-null
        total = session.execute(select(FixtureStats)).all()
        with_xg = session.execute(
            select(FixtureStats).where(FixtureStats.home_xg != None)
        ).all()
        
        return len(with_xg) > 0


def get_injury_impact(team_id: int) -> float:
    """
    Calculate goal impact from injured players.
    
    Uses position-based weights:
    - Goalkeeper: -0.15 (key saves)
    - Defender: -0.10 (organization)
    - Midfielder: -0.20 (creativity)
    - Attacker/Forward: -0.40 (goals)
    """
    with get_session() as session:
        injuries = session.query(Injury).filter(
            Injury.team_id == team_id,
            Injury.status.in_(["injured", "doubt"])
        ).all()
        
        if not injuries:
            return 0.0
            
        impact = 0.0
        for inj in injuries:
            # Check if we have player position info, otherwise use default
            player_pos = getattr(inj, 'player_position', None)
            if player_pos:
                impact += POSITION_GOAL_IMPACT.get(player_pos, DEFAULT_POSITION_IMPACT)
            else:
                impact += DEFAULT_POSITION_IMPACT
        
        return max(impact, -0.5)  # Cap at -0.5


def get_team_strength_with_injuries(team_id: int, xg_for: float, xg_against: float) -> tuple[float, float]:
    """
    Adjust xG based on injuries.
    
    Returns: (adjusted_xg_for, adjusted_xg_against)
    """
    injury_impact = get_injury_impact(team_id)
    # Negative impact reduces expected goals
    adjusted_for = max(0.1, xg_for + injury_impact)
    return adjusted_for, xg_against