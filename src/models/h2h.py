"""
src/models/h2h.py - Head-to-Head history model

Stores and calculates historical results between teams.

Usage:
    from src.models.h2h import get_h2h_stats

    stats = get_h2h_stats(home_id, away_id)
    # Returns: {h2h_home_wins, h2h_away_wins, h2h_draws, h2h_home_goals, h2h_away_goals}
"""
from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import select, func

from src.storage.db import get_session
from src.storage.models import Fixture


@dataclass
class H2HStats:
    """Head-to-head statistics between two teams."""
    total_matches: int
    home_wins: int
    away_wins: int
    draws: int
    home_goals: int
    away_goals: int
    avg_total_goals: float
    btts_rate: float
    over25_rate: float


def get_h2h_stats(
    team1_id: int,
    team2_id: int,
    limit: int = 20,
    as_of_date=None,
) -> H2HStats:
    """
    Get head-to-head statistics between two teams.

    Args:
        team1_id: First team ID (treat as "home" perspective)
        team2_id: Second team ID (treat as "away" perspective)
        limit: Maximum number of matches to consider
        as_of_date: Only consider matches before this date

    Returns:
        H2HStats with all relevant statistics
    """
    with get_session() as s:
        query = select(
            Fixture.home_team_id,
            Fixture.away_team_id,
            Fixture.goals_home,
            Fixture.goals_away,
        ).where(
            Fixture.status == 'FT',
            Fixture.goals_home.isnot(None),
            (
                ((Fixture.home_team_id == team1_id) & (Fixture.away_team_id == team2_id)) |
                ((Fixture.home_team_id == team2_id) & (Fixture.away_team_id == team1_id))
            )
        )

        if as_of_date:
            query = query.where(Fixture.date < as_of_date)

        query = query.order_by(Fixture.date.desc()).limit(limit)

        rows = s.execute(query).all()

    if not rows:
        return H2HStats(
            total_matches=0,
            home_wins=0,
            away_wins=0,
            draws=0,
            home_goals=0,
            away_goals=0,
            avg_total_goals=0.0,
            btts_rate=0.0,
            over25_rate=0.0,
        )

    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals = 0
    away_goals = 0
    btts_count = 0
    over25_count = 0

    for row in rows:
        h_tid, a_tid, h_goals, a_goals = row
        h_goals = h_goals or 0
        a_goals = a_goals or 0

        # Normalize: team1 is "home" perspective
        if h_tid == team1_id:
            home_goals += h_goals
            away_goals += a_goals
            if h_goals > a_goals:
                home_wins += 1
            elif a_goals > h_goals:
                away_wins += 1
            else:
                draws += 1
        else:
            # Swap: team2 was home in this match
            home_goals += a_goals
            away_goals += h_goals
            if a_goals > h_goals:
                home_wins += 1
            elif h_goals > a_goals:
                away_wins += 1
            else:
                draws += 1

        # BTTS
        if h_goals > 0 and a_goals > 0:
            btts_count += 1

        # Over 2.5
        if h_goals + a_goals > 2.5:
            over25_count += 1

    n = len(rows)
    return H2HStats(
        total_matches=n,
        home_wins=home_wins,
        away_wins=away_wins,
        draws=draws,
        home_goals=home_goals,
        away_goals=away_goals,
        avg_total_goals=(home_goals + away_goals) / n if n > 0 else 0.0,
        btts_rate=btts_count / n if n > 0 else 0.0,
        over25_rate=over25_count / n if n > 0 else 0.0,
    )


def get_h2h_form_score(
    team1_id: int,
    team2_id: int,
    as_home: bool = True,
    limit: int = 5,
) -> float:
    """
    Get H2H form score for a team against another.

    Returns:
        Score from -1 (lost all) to +1 (won all)
    """
    stats = get_h2h_stats(team1_id, team2_id, limit=limit)
    if stats.total_matches == 0:
        return 0.0

    if as_home:
        # team1 plays at home
        wins = stats.home_wins
        losses = stats.away_wins
    else:
        # team1 plays away
        wins = stats.away_wins
        losses = stats.home_wins

    # Form score: (wins - losses) / total
    total = stats.total_matches
    return (wins - losses) / total if total > 0 else 0.0


def get_h2h_advantage(team1_id: int, team2_id: int) -> tuple[str, float]:
    """
    Determine which team has H2H advantage.

    Returns:
        (team_name, advantage_score)
        team_name: 'home', 'away', or 'neutral'
        advantage_score: -1 to 1 (negative = team2 advantage)
    """
    stats = get_h2h_stats(team1_id, team2_id)
    if stats.total_matches < 3:
        return "neutral", 0.0

    # Calculate weighted advantage
    home_win_rate = stats.home_wins / stats.total_matches
    away_win_rate = stats.away_wins / stats.total_matches

    if home_win_rate > away_win_rate + 0.1:
        return "home", home_win_rate - away_win_rate
    elif away_win_rate > home_win_rate + 0.1:
        return "away", away_win_rate - home_win_rate
    else:
        return "neutral", 0.0
