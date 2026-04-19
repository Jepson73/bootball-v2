"""
src/models/injuries.py - Injuries and Lineups Impact Model

Calculates goal impact from:
- Injured players (key positions)
- Suspended players
- Returning players (not 100% match fit)
- Missing lineups

Usage:
    from src.models.injuries import get_injury_adjustment

    adjustment = get_injury_adjustment(home_id, away_id)
    # Returns: {home_xg_adjust, away_xg_adjust, summary}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Injury


POSITION_IMPACT = {
    "Goalkeeper": -0.15,
    "Defender": -0.12,
    "Midfielder": -0.20,
    "Attacker": -0.40,
    "Forward": -0.40,
}
DEFAULT_IMPACT = -0.15

RETURNING_PLAYER_IMPACT = 0.1

SUSPENSION_IMPACT = -0.20


@dataclass
class InjuryInfo:
    """Information about a single injury/suspension."""
    player_name: str
    position: str
    impact: float
    reason: str
    days_out: int | None = None


@dataclass
class InjuryAdjustment:
    """Combined injury/suspension impact for both teams."""
    home_xg_adjustment: float
    away_xg_adjustment: float
    home_injuries: list[InjuryInfo] = field(default_factory=list)
    away_injuries: list[InjuryInfo] = field(default_factory=list)
    home_suspensions: list[InjuryInfo] = field(default_factory=list)
    away_suspensions: list[InjuryInfo] = field(default_factory=list)
    returning_players: list[InjuryInfo] = field(default_factory=list)
    total_impact_home: float = 0.0
    total_impact_away: float = 0.0


def get_team_injuries(team_id: int) -> list[InjuryInfo]:
    """Get current injuries for a team."""
    with get_session() as s:
        injuries = s.execute(
            select(Injury).where(Injury.team_id == team_id)
        ).scalars().all()

    result = []
    for inj in injuries:
        status = getattr(inj, 'status', None)
        if status in ("injured", "doubt"):
            pos = getattr(inj, 'player_position', 'Unknown')
            name = getattr(inj, 'player_name', 'Unknown Player')

            if status == "doubt":
                impact = POSITION_IMPACT.get(pos, DEFAULT_IMPACT) * 0.5
                reason = "doubtful"
            else:
                impact = POSITION_IMPACT.get(pos, DEFAULT_IMPACT)
                reason = "injured"

            result.append(InjuryInfo(
                player_name=name,
                position=pos,
                impact=impact,
                reason=reason,
            ))

    return result


def get_team_suspensions(team_id: int) -> list[InjuryInfo]:
    """Get current suspensions for a team."""
    with get_session() as s:
        injuries = s.execute(
            select(Injury).where(Injury.team_id == team_id)
        ).scalars().all()

    result = []
    for inj in injuries:
        status = getattr(inj, 'status', None)
        if status == "suspended":
            pos = getattr(inj, 'player_position', 'Unknown')
            name = getattr(inj, 'player_name', 'Unknown Player')

            result.append(InjuryInfo(
                player_name=name,
                position=pos,
                impact=SUSPENSION_IMPACT,
                reason="suspended",
            ))

    return result


def get_returning_players(team_id: int) -> list[InjuryInfo]:
    """Get returning players (recently recovered from injury)."""
    with get_session() as s:
        injuries = s.execute(
            select(Injury).where(Injury.team_id == team_id)
        ).scalars().all()

    result = []
    for inj in injuries:
        status = getattr(inj, 'status', None)
        if status == "recovered":
            pos = getattr(inj, 'player_position', 'Unknown')
            name = getattr(inj, 'player_name', 'Unknown Player')

            result.append(InjuryInfo(
                player_name=name,
                position=pos,
                impact=RETURNING_PLAYER_IMPACT,
                reason="returning",
            ))

    return result


def get_injury_adjustment(home_id: int, away_id: int) -> InjuryAdjustment:
    """
    Calculate total injury/suspension impact for a match.

    Returns:
        InjuryAdjustment with impact values for both teams
    """
    home_injuries = get_team_injuries(home_id)
    away_injuries = get_team_injuries(away_id)
    home_suspensions = get_team_suspensions(home_id)
    away_suspensions = get_team_suspensions(away_id)
    returning = get_returning_players(home_id) + get_returning_players(away_id)

    home_xg = sum(i.impact for i in home_injuries) + sum(i.impact for i in home_suspensions)
    away_xg = sum(i.impact for i in away_injuries) + sum(i.impact for i in away_suspensions)

    total_home = home_xg + sum(r.impact for r in returning if r in get_returning_players(home_id))
    total_away = away_xg + sum(r.impact for r in returning if r in get_returning_players(away_id))

    return InjuryAdjustment(
        home_xg_adjustment=home_xg,
        away_xg_adjustment=away_xg,
        home_injuries=home_injuries,
        away_injuries=away_injuries,
        home_suspensions=home_suspensions,
        away_suspensions=away_suspensions,
        returning_players=returning,
        total_impact_home=total_home,
        total_impact_away=total_away,
    )


def apply_injury_to_prediction(
    base_prob_over: float,
    base_prob_btts: float,
    home_xg_adjust: float,
    away_xg_adjust: float,
) -> tuple[float, float]:
    """
    Adjust probabilities based on injury impact.

    Args:
        base_prob_over: Base Over 2.5 probability
        base_prob_btts: Base BTTS probability
        home_xg_adjust: Home team xG adjustment (negative = weaker)
        away_xg_adjust: Away team xG adjustment

    Returns:
        (adjusted_prob_over, adjusted_prob_btts)
    """
    total_adjust = home_xg_adjust + away_xg_adjust

    over_adjust = total_adjust * 0.5
    btts_adjust = total_adjust * 0.3

    new_over = max(0.1, min(0.9, base_prob_over + over_adjust))
    new_btts = max(0.1, min(0.9, base_prob_btts + btts_adjust))

    return new_over, new_btts
