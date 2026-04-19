# src/features/strength.py - Dixon-Coles attack/defense strengths
"""
Team attack/defense strength parameters estimated from historical goals.

This is a simplified version of Dixon-Coles team parameters that can be used
as features for ML models. It uses Maximum Likelihood Estimation to fit
Poisson model parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture, Team


@dataclass
class TeamStrengths:
    """Attack and defense strength parameters for a team."""
    team_id: int
    attack: float
    defense: float  # Lower is better (more defensive)
    home_adv: float  # Additional home boost


def poisson_log_likelihood(
    params: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    home_team_idx: np.ndarray,
    away_team_idx: np.ndarray,
    n_teams: int,
) -> float:
    """
    Compute negative log-likelihood for Poisson model.
    
    params: [attack_0, ..., attack_n-1, defense_0, ..., defense_n-1, home_adv]
    """
    # Split params
    attack = params[:n_teams]
    defense = params[n_teams:2*n_teams]
    home_adv = params[-1]
    
    # Expected goals
    lambda_home = np.exp(attack[home_team_idx] + defense[away_team_idx] + home_adv)
    lambda_away = np.exp(attack[away_team_idx] + defense[home_team_idx])
    
    # Poisson log-likelihood
    ll_home = np.sum(home_goals * np.log(lambda_home) - lambda_home)
    ll_away = np.sum(away_goals * np.log(lambda_away) - lambda_away)
    
    return -(ll_home + ll_away)


def fit_team_strengths(fixtures: list[Fixture], team_ids: list[int]) -> dict[int, TeamStrengths]:
    """Fit attack/defense strengths using MLE."""
    n_teams = len(team_ids)
    team_idx = {tid: i for i, tid in enumerate(team_ids)}
    
    # Extract goals
    home_goals = np.array([f.goals_home for f in fixtures if f.goals_home is not None])
    away_goals = np.array([f.goals_away for f in fixtures if f.goals_away is not None])
    
    valid_idx = [
        i for i, f in enumerate(fixtures) 
        if f.goals_home is not None and f.goals_away is not None
    ]
    
    if len(valid_idx) < 50:
        print(f"Warning: only {len(valid_idx)} matches, using defaults")
        return {tid: TeamStrengths(tid, 0.0, 0.0, 0.1) for tid in team_ids}
    
    home_idx = np.array([team_idx[fixtures[i].home_team_id] for i in valid_idx])
    away_idx = np.array([team_idx[fixtures[i].away_team_id] for i in valid_idx])
    
    # Initial params: small random values
    np.random.seed(42)
    initial = np.random.randn(2 * n_teams + 1) * 0.1
    
    # Constrain: sum of attack = 0, sum of defense = 0
    # Use simple unconstrained with soft constraint
    bounds = [(None, None)] * (2 * n_teams + 1)
    
    result = minimize(
        poisson_log_likelihood,
        initial,
        args=(home_goals, away_goals, home_idx, away_idx, n_teams),
        method="L-BFGS-B",
        options={"maxiter": 1000},
    )
    
    if not result.success:
        print(f"Warning: optimization failed: {result.message}")
    
    attack = result.x[:n_teams]
    defense = result.x[n_teams:2*n_teams]
    home_adv = result.x[-1]
    
    # Normalize (center at 0)
    attack = attack - np.mean(attack)
    defense = defense - np.mean(defense)
    
    return {
        team_ids[i]: TeamStrengths(
            team_id=team_ids[i],
            attack=attack[i],
            defense=defense[i],
            home_adv=home_adv,
        )
        for i in range(n_teams)
    }


class StrengthEngine:
    """Compute team strength features."""
    
    def __init__(self, league_id: int | None = None, season: int | None = None):
        self.league_id = league_id
        self.season = season
        self._strengths: dict[int, TeamStrengths] | None = None
    
    def fit(self) -> dict[int, TeamStrengths]:
        """Fit strength model on historical data."""
        with get_session() as session:
            query = select(Fixture).where(Fixture.status == "FT")
            
            if self.league_id:
                query = query.where(Fixture.league_id == self.league_id)
            if self.season:
                query = query.where(Fixture.season == self.season)
            
            fixtures = session.execute(query).scalars().all()
            
            # Get all teams
            team_ids = set()
            for f in fixtures:
                team_ids.add(f.home_team_id)
                team_ids.add(f.away_team_id)
            
            team_ids = sorted(team_ids)
            
            self._strengths = fit_team_strengths(fixtures, team_ids)
            return self._strengths
    
    def get_strengths(self, team_id: int) -> TeamStrengths:
        """Get team strengths (fits if not yet fitted)."""
        if self._strengths is None:
            self.fit()
        return self._strengths.get(team_id, TeamStrengths(team_id, 0.0, 0.0, 0.1))
    
    def get_features(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> dict:
        """Get strength-based features for a matchup."""
        home = self.get_strengths(home_team_id)
        away = self.get_strengths(away_team_id)
        
        return {
            "home_attack": home.attack,
            "home_defense": home.defense,
            "away_attack": away.attack,
            "away_defense": away.defense,
            "home_advantage": home.home_adv,
            
            # Differential features
            "attack_diff": home.attack - away.attack,
            "defense_diff": home.defense - away.defense,
            "expected_goals_home": np.exp(home.attack + away.defense + home.home_adv),
            "expected_goals_away": np.exp(away.attack + home.defense),
        }


def compute_all_strengths():
    """Compute and print strength parameters for all teams."""
    engine = StrengthEngine()
    strengths = engine.fit()
    
    print("\nTeam Strengths (attack +, defense + = weaker):")
    print("-" * 60)
    
    sorted_teams = sorted(strengths.items(), key=lambda x: x[1].attack - x[1].defense, reverse=True)
    
    with get_session() as session:
        for team_id, s in sorted_teams:
            team = session.get(Team, team_id)
            name = team.name if team else f"Team {team_id}"
            print(f"{name:20} Attack: {s.attack:+.3f}  Defense: {s.defense:+.3f}")
    
    print(f"\nHome advantage: {next(iter(strengths.values())).home_adv:+.3f}")
    return strengths