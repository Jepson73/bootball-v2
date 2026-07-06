# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: late goal probability analysis; potential future BTTS/OU in-play extension
"""
src/models/late_goals.py - Late goal analysis

Identifies leagues and teams with high late-match scoring (75-90+ minutes).
Useful for:
- In-play betting
- BTTS predictions (late goals often secure BTTS Yes)
- Over 2.5 predictions
"""
from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import select, func

from src.storage.db import get_session
from src.storage.models import FixtureEvent, Fixture


@dataclass
class LateGoalStats:
    late_goal_pct: float  # % of goals scored after 75'
    avg_late_goals_per_match: float
    btts_late_factor: float  # How often BTTS secured in last 15 min


def get_league_late_goal_stats(league_id: int) -> LateGoalStats:
    """
    Calculate late goal statistics for a league.
    Based on historical fixture events data.
    """
    with get_session() as s:
        fixtures = s.execute(
            select(Fixture.id).where(Fixture.league_id == league_id)
        ).scalars().all()

        if not fixtures:
            return LateGoalStats(late_goal_pct=0.25, avg_late_goals_per_match=0.5, btts_late_factor=1.0)

        fixture_ids = fixtures

        all_goals = s.execute(
            select(FixtureEvent).where(
                FixtureEvent.fixture_id.in_(fixture_ids),
                FixtureEvent.event_type == 'Goal'
            )
        ).scalars().all()

        if not all_goals:
            return LateGoalStats(late_goal_pct=0.25, avg_late_goals_per_match=0.5, btts_late_factor=1.0)

        total_goals = len(all_goals)
        late_goals = sum(1 for g in all_goals if g.minute and g.minute >= 75)
        very_late_goals = sum(1 for g in all_goals if g.minute and g.minute >= 85)

        late_goal_pct = late_goals / total_goals if total_goals > 0 else 0.25
        avg_late_per_match = late_goals / len(fixture_ids) if fixture_ids else 0.5

        btts_late_factor = 1.0 + (very_late_goals / total_goals * 0.5) if total_goals > 0 else 1.0

        return LateGoalStats(
            late_goal_pct=late_goal_pct,
            avg_late_goals_per_match=avg_late_per_match,
            btts_late_factor=btts_late_factor,
        )


def get_team_late_goal_stats(team_id: int) -> LateGoalStats:
    """Calculate late goal statistics for a specific team."""
    with get_session() as s:
        fixtures = s.execute(
            select(Fixture.id).where(
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id)
            )
        ).scalars().all()

        if not fixtures:
            return LateGoalStats(late_goal_pct=0.25, avg_late_goals_per_match=0.5, btts_late_factor=1.0)

        goals = s.execute(
            select(FixtureEvent).where(
                FixtureEvent.fixture_id.in_(fixtures),
                FixtureEvent.event_type == 'Goal',
                FixtureEvent.team_id == team_id
            )
        ).scalars().all()

        if not goals:
            return LateGoalStats(late_goal_pct=0.25, avg_late_goals_per_match=0.5, btts_late_factor=1.0)

        total_goals = len(goals)
        late_goals = sum(1 for g in goals if g.minute and g.minute >= 75)
        very_late_goals = sum(1 for g in goals if g.minute and g.minute >= 85)

        late_goal_pct = late_goals / total_goals if total_goals > 0 else 0.25
        avg_late_per_match = late_goals / len(fixtures) if fixtures else 0.5
        btts_late_factor = 1.0 + (very_late_goals / total_goals * 0.3) if total_goals > 0 else 1.0

        return LateGoalStats(
            late_goal_pct=late_goal_pct,
            avg_late_goals_per_match=avg_late_per_match,
            btts_late_factor=btts_late_factor,
        )


HIGH_LATE_GOAL_LEAGUES = {
    98: {"name": "J1 League", "late_factor": 1.15, "note": "26% late goals (75+)"},
    140: {"name": "La Liga", "late_factor": 1.12, "note": "25.2% late goals (75+)"},
    203: {"name": "Süper Lig", "late_factor": 1.10, "note": "24.6% late goals (75+)"},
    88: {"name": "Eredivisie", "late_factor": 1.05, "note": "24.2% late goals (75+)"},
    253: {"name": "MLS", "late_factor": 1.05, "note": "23.1% late goals (75+)"},
    188: {"name": "A-League", "late_factor": 1.0, "note": "now has event data"},
}

DEFAULT_LATE_FACTOR = 1.0


def get_late_goal_factor(league_id: int) -> float:
    """Get late goal factor for a league (1.0 = average, >1 = more late goals)."""
    return HIGH_LATE_GOAL_LEAGUES.get(league_id, {}).get("late_factor", DEFAULT_LATE_FACTOR)


def apply_late_goal_adjustment(base_prob: float, league_id: int, market: str) -> float:
    """Apply late goal factor to adjust probability for over/under markets.
    
    Args:
        base_prob: Base probability from model (0-1)
        league_id: League ID for late goal factor
        market: "over25", "over15", "btts", "under25", "under15"
        
    Returns:
        Adjusted probability
    """
    factor = get_late_goal_factor(league_id)
    
    if market in ["over25", "over15", "btts"]:
        # Higher late factor = more goals expected
        # Apply 30% of the factor adjustment
        adjustment = 1.0 + (factor - 1.0) * 0.3
        return min(1.0, base_prob * adjustment)
    elif market in ["under25", "under15"]:
        # Lower late factor = less goals expected
        adjustment = 1.0 - (1.0 - factor) * 0.3
        return max(0.0, base_prob * adjustment)
    
    return base_prob


def has_late_goal_indicator(league_id: int) -> tuple[bool, str]:
    """
    Check if a league has late goal tendency.
    Returns (has_indicator, description)
    """
    if league_id in HIGH_LATE_GOAL_LEAGUES:
        info = HIGH_LATE_GOAL_LEAGUES[league_id]
        return True, f"Late goals ({info['note']})"

    try:
        stats = get_league_late_goal_stats(league_id)
        if stats.late_goal_pct > 0.30:
            return True, f"Late goals ({stats.late_goal_pct:.0%} after 75')"
    except:
        pass

    return False, ""
