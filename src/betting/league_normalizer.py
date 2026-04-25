import logging
from typing import Optional
from dataclasses import dataclass
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)


@dataclass
class LeagueBaseline:
    """League-level baseline statistics."""
    league_id: int
    league_name: str
    avg_goals: float
    btts_rate: float
    ou15_rate: float
    ou25_rate: float
    home_advantage: float
    variance_goals: float
    total_matches: int


def get_league_baseline(league_id: int) -> Optional[LeagueBaseline]:
    """Get league baseline for a specific league."""
    with get_session() as s:
        result = s.execute(
            text("""
                SELECT league_id, league_name, avg_goals, btts_rate, ou15_rate, ou25_rate,
                       home_advantage, variance_goals, total_matches
                FROM league_baselines
                WHERE league_id = :league_id
            """),
            {"league_id": league_id}
        ).fetchone()
        
        if not result:
            logger.warning(f"No baseline for league {league_id}, using global defaults")
            return None
        
        return LeagueBaseline(
            league_id=result[0],
            league_name=result[1],
            avg_goals=result[2] or 2.5,
            btts_rate=result[3] or 0.5,
            ou15_rate=result[4] or 0.5,
            ou25_rate=result[5] or 0.5,
            home_advantage=result[6] or 0.25,
            variance_goals=result[7] or 2.0,
            total_matches=result[8] or 0,
        )


def get_default_baseline() -> LeagueBaseline:
    """Get default baseline (global average) when league not found."""
    return LeagueBaseline(
        league_id=0,
        league_name="DEFAULT",
        avg_goals=2.7,
        btts_rate=0.52,
        ou15_rate=0.72,
        ou25_rate=0.50,
        home_advantage=0.30,
        variance_goals=2.5,
        total_matches=0,
    )


def normalize_goals_by_league(team_goals: float, league_baseline: LeagueBaseline) -> float:
    """Normalize goals to league-relative deviation."""
    if league_baseline and league_baseline.avg_goals > 0:
        return (team_goals - league_baseline.avg_goals) / (league_baseline.avg_goals + 0.1)
    return team_goals / 2.5


def normalize_rank_by_league(rank: int, league_id: int) -> float:
    """Normalize rank within league context."""
    return rank / 20.0


def get_btts_expectation(league_baseline: LeagueBaseline) -> float:
    """Get expected BTTS rate for league."""
    return league_baseline.btts_rate if league_baseline else 0.5


def get_ou_expectation(league_baseline: LeagueBaseline, threshold: float = 2.5) -> float:
    """Get expected over/under rate for league."""
    if not league_baseline:
        return 0.5
    
    if threshold <= 1.5:
        return league_baseline.ou15_rate
    else:
        return league_baseline.ou25_rate


def compute_league_context(league_id: int) -> dict:
    """Compute full league context for a match."""
    baseline = get_league_baseline(league_id)
    if baseline is None:
        baseline = get_default_baseline()
    
    return {
        "league_id": league_id,
        "avg_goals": baseline.avg_goals,
        "btts_rate": baseline.btts_rate,
        "ou15_rate": baseline.ou15_rate,
        "ou25_rate": baseline.ou25_rate,
        "home_advantage": baseline.home_advantage,
        "variance_goals": baseline.variance_goals,
        "expected_total_goals": baseline.avg_goals,
        "expected_home_goals": (baseline.avg_goals + baseline.home_advantage) / 2,
        "expected_away_goals": (baseline.avg_goals - baseline.home_advantage) / 2,
    }


def get_all_league_baselines() -> dict[int, LeagueBaseline]:
    """Get all league baselines."""
    with get_session() as s:
        results = s.execute(text("SELECT league_id, league_name, avg_goals, btts_rate, ou15_rate, ou25_rate, home_advantage, variance_goals, total_matches FROM league_baselines")).fetchall()
        
        baselines = {}
        for r in results:
            baselines[r[0]] = LeagueBaseline(
                league_id=r[0],
                league_name=r[1],
                avg_goals=r[2] or 2.5,
                btts_rate=r[3] or 0.5,
                ou15_rate=r[4] or 0.5,
                ou25_rate=r[5] or 0.5,
                home_advantage=r[6] or 0.25,
                variance_goals=r[7] or 2.0,
                total_matches=r[8] or 0,
            )
        return baselines


def compute_league_sensitivity(market: str) -> dict:
    """Compute how much a market depends on league identity.
    
    Returns a dict with sensitivity metrics.
    """
    baselines = get_all_league_baselines()
    
    if not baselines:
        return {"sensitivity": "unknown", "reason": "no baselines"}
    
    btts_rates = [b.btts_rate for b in baselines.values() if b.btts_rate > 0]
    ou25_rates = [b.ou25_rate for b in baselines.values() if b.ou25_rate > 0]
    avg_goals = [b.avg_goals for b in baselines.values() if b.avg_goals > 0]
    
    if not btts_rates or not ou25_rates or not avg_goals:
        return {"sensitivity": "unknown", "reason": "insufficient data"}
    
    import numpy as np
    
    btts_variance = np.var(btts_rates)
    ou25_variance = np.var(ou25_rates)
    goals_variance = np.var(avg_goals)
    
    sensitivity_score = 0
    
    if market == "btts":
        sensitivity_score = btts_variance * 10
    elif market in ("ou25", "ou15"):
        sensitivity_score = ou25_variance * 10
    elif market == "h2h":
        sensitivity_score = goals_variance * 2
    
    if sensitivity_score > 0.05:
        status = "high"
    elif sensitivity_score > 0.02:
        status = "medium"
    else:
        status = "low"
    
    return {
        "market": market,
        "sensitivity": status,
        "score": sensitivity_score,
        "btts_variance": btts_variance,
        "ou25_variance": ou25_variance,
        "goals_variance": goals_variance,
        "league_count": len(baselines),
    }