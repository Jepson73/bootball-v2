# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: Dixon-Coles model with time decay; classical football prediction baseline
# src/models/dixon_coles.py - Dixon-Coles with time decay
"""
Dixon-Coles model implementation for football match prediction.

Based on: Dixon & Coles (1997) - "Modelling association football scores and 
inefficiencies in the football betting market"

Key components:
- Bivariate Poisson with rho correction for low-scoring draws
- Time decay weighting (xi parameter ~0.002-0.006)
- MLE optimization to estimate team attack/defense parameters
"""
from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from scipy.optimize import minimize
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture


@dataclass
class DCParams:
    """Dixon-Coles model parameters."""
    attack: dict[int, float]      # Team attack strengths
    defense: dict[int, float]     # Team defense strengths (lower = better)
    home_adv: float               # Home advantage
    rho: float                    # Correlation parameter


def rho_correction(x: int, y: int, lambda_x: float, mu_y: float, rho: float) -> float:
    """
    Dixon-Coles tau correction for low-scoring draws.
    Adds dependence between home and away goals.
    """
    if x == 0 and y == 0:
        return 1 - (lambda_x * mu_y * rho)
    elif x == 0 and y == 1:
        return 1 + (lambda_x * rho)
    elif x == 1 and y == 0:
        return 1 + (mu_y * rho)
    elif x == 1 and y == 1:
        return 1 - rho
    return 1.0


def time_decay(days_ago: int, xi: float = 0.005) -> float:
    """Exponential time decay weight."""
    return np.exp(-xi * days_ago)


_FACTORIALS = [math.log(math.factorial(i)) for i in range(20)]


def dc_log_likelihood(
    params: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    home_team_idx: np.ndarray,
    away_team_idx: np.ndarray,
    days_ago: np.ndarray,
    n_teams: int,
    xi: float = 0.005,
) -> float:
    """
    Vectorized negative log-likelihood for Dixon-Coles model.
    """
    attack = params[:n_teams]
    defense = params[n_teams:2*n_teams]
    home_adv = params[2*n_teams]
    rho = np.clip(params[2*n_teams + 1], -0.5, 0.5)
    
    lmb_home = np.exp(attack[home_team_idx] + defense[away_team_idx] + home_adv)
    lmb_away = np.exp(attack[away_team_idx] + defense[home_team_idx])
    weights = time_decay(days_ago, xi)
    
    h_goals = home_goals.astype(int)
    a_goals = away_goals.astype(int)
    
    ll_h = h_goals * np.log(np.maximum(lmb_home, 1e-10)) - lmb_home - np.array([_FACTORIALS[g] for g in h_goals])
    ll_a = a_goals * np.log(np.maximum(lmb_away, 1e-10)) - lmb_away - np.array([_FACTORIALS[g] for g in a_goals])
    
    tau = np.ones(len(home_goals))
    mask_h0a0 = (h_goals == 0) & (a_goals == 0)
    mask_h0a1 = (h_goals == 0) & (a_goals == 1)
    mask_h1a0 = (h_goals == 1) & (a_goals == 0)
    mask_h1a1 = (h_goals == 1) & (a_goals == 1)
    
    tau[mask_h0a0] = 1 - lmb_home[mask_h0a0] * lmb_away[mask_h0a0] * rho
    tau[mask_h0a1] = 1 + lmb_home[mask_h0a1] * rho
    tau[mask_h1a0] = 1 + lmb_away[mask_h1a0] * rho
    tau[mask_h1a1] = 1 - rho
    
    tau = np.maximum(tau, 1e-10)
    log_tau = np.log(tau)
    
    ll = np.sum(weights * (ll_h + ll_a + log_tau))
    
    return -ll


def fit_dixon_coles(
    fixtures: list[Fixture],
    team_ids: list[int],
    xi: float = 0.005,
) -> DCParams:
    """Fit Dixon-Coles model using MLE."""
    n_teams = len(team_ids)
    team_idx = {tid: i for i, tid in enumerate(team_ids)}
    
    # Filter valid fixtures
    valid = [(f, team_idx.get(f.home_team_id), team_idx.get(f.away_team_id))
             for f in fixtures
             if f.goals_home is not None and f.goals_away is not None
             and f.home_team_id in team_idx and f.away_team_id in team_idx]
    
    if len(valid) < 50:
        print(f"Warning: only {len(valid)} matches, using defaults")
        return DCParams(
            attack={t: 0.0 for t in team_ids},
            defense={t: 0.0 for t in team_ids},
            home_adv=0.1,
            rho=-0.1,
        )
    
    home_goals = np.array([f.goals_home for f, _, _ in valid])
    away_goals = np.array([f.goals_away for f, _, _ in valid])
    home_idx = np.array([idx for _, idx, _ in valid])
    away_idx = np.array([idx for _, _, idx in valid])
    
    # Days ago (reference date = latest match)
    latest_date = max(f.date for f, _, _ in valid)
    days_ago = np.array([(latest_date - f.date).days for f, _, _ in valid])
    
    # Initial params
    np.random.seed(42)
    initial = np.random.randn(2 * n_teams + 2) * 0.1
    initial[2*n_teams] = 0.1  # home_adv
    initial[2*n_teams + 1] = -0.1  # rho
    
    # Optimize
    result = minimize(
        dc_log_likelihood,
        initial,
        args=(home_goals, away_goals, home_idx, away_idx, days_ago, n_teams, xi),
        method="L-BFGS-B",
        options={"maxiter": 10000, "maxfun": 50000},
    )
    
    if not result.success:
        print(f"Warning: optimization failed: {result.message}")
    
    attack = result.x[:n_teams]
    defense = result.x[n_teams:2*n_teams]
    home_adv = result.x[2*n_teams]
    rho = np.clip(result.x[2*n_teams + 1], -0.5, 0.5)
    
    # Normalize
    attack = attack - np.mean(attack)
    defense = defense - np.mean(defense)
    
    return DCParams(
        attack={team_ids[i]: attack[i] for i in range(n_teams)},
        defense={team_ids[i]: defense[i] for i in range(n_teams)},
        home_adv=home_adv,
        rho=rho,
    )


class DixonColesModel:
    """Dixon-Coles model for match prediction."""
    
    def __init__(self, league_id: int | None = None, xi: float = 0.005):
        self.league_id = league_id
        self.xi = xi
        self._params: Optional[DCParams] = None
        self._team_ids: list[int] = []
        self._fitted = False
    
    def fit(self) -> "DixonColesModel":
        """Fit model on historical data."""
        with get_session() as session:
            query = select(Fixture).where(Fixture.status == "FT")
            if self.league_id:
                query = query.where(Fixture.league_id == self.league_id)
            
            fixtures = session.execute(query).scalars().all()
            
            # Get teams
            self._team_ids = sorted(set(
                f.home_team_id for f in fixtures
            ) | set(f.away_team_id for f in fixtures))
            
            self._params = fit_dixon_coles(fixtures, self._team_ids, self.xi)
            self._fitted = True
            
            print(f"Dixon-Coles fitted on {len(fixtures)} matches")
            print(f"  Home advantage: {self._params.home_adv:.3f}")
            print(f"  Rho (correlation): {self._params.rho:.3f}")
        
        return self
    
    def _expected_goals(self, home_team_id: int, away_team_id: int) -> tuple[float, float]:
        """Calculate expected goals for home and away."""
        if not self._fitted:
            self.fit()
        
        attack_h = self._params.attack.get(home_team_id, 0.0)
        defense_h = self._params.defense.get(home_team_id, 0.0)
        attack_a = self._params.attack.get(away_team_id, 0.0)
        defense_a = self._params.defense.get(away_team_id, 0.0)
        
        lambda_home = np.exp(attack_h + defense_a + self._params.home_adv)
        lambda_away = np.exp(attack_a + defense_h)
        
        return lambda_home, lambda_away
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        max_goals: int = 6,
    ) -> tuple[float, float, float]:
        """
        Predict probability of (home win, draw, away win).
        """
        if not self._fitted:
            self.fit()
        
        lambda_home, lambda_away = self._expected_goals(home_team_id, away_team_id)
        rho = self._params.rho
        
        # Calculate scoreline probabilities with rho correction
        prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
        
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                # Poisson
                p_home = np.exp(-lambda_home) * (lambda_home ** i) / math.factorial(i)
                p_away = np.exp(-lambda_away) * (lambda_away ** j) / math.factorial(j)
                
                # Rho correction
                tau = rho_correction(i, j, lambda_home, lambda_away, rho)
                
                prob_matrix[i, j] = p_home * p_away * tau
        
        # Normalize
        total = prob_matrix.sum()
        if total > 0:
            prob_matrix /= total
        
        # Outcome probabilities
        prob_home = prob_matrix[np.triu_indices(max_goals + 1, k=1)].sum()
        prob_away = prob_matrix[np.tril_indices(max_goals + 1, k=-1)].sum()
        prob_draw = prob_matrix.diagonal().sum()
        
        # Renormalize
        total = prob_home + prob_draw + prob_away
        return (
            prob_home / total,
            prob_draw / total,
            prob_away / total,
        )
    
    def predict(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> str:
        """Predict match outcome: 'H', 'D', or 'A'."""
        probs = self.predict_proba(home_team_id, away_team_id)
        outcomes = ['H', 'D', 'A']
        return outcomes[np.argmax(probs)]
    
    def predict_score(
        self,
        home_team_id: int,
        away_team_id: int,
    ) -> tuple[int, int]:
        """Predict most likely scoreline."""
        if not self._fitted:
            self.fit()
        
        lambda_home, lambda_away = self._expected_goals(home_team_id, away_team_id)
        rho = self._params.rho
        
        max_goals = 6
        prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
        
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p_home = np.exp(-lambda_home) * (lambda_home ** i) / math.factorial(i)
                p_away = np.exp(-lambda_away) * (lambda_away ** j) / math.factorial(j)
                tau = rho_correction(i, j, lambda_home, lambda_away, rho)
                prob_matrix[i, j] = p_home * p_away * tau
        
        # Find most likely
        idx = np.unravel_index(prob_matrix.argmax(), prob_matrix.shape)
        return idx[0], idx[1]


def evaluate_rps(model, test_fixtures: list[Fixture]) -> float:
    """Calculate Ranked Probability Score on test set."""
    total_rps = 0.0
    
    for f in test_fixtures:
        probs = model.predict_proba(f.home_team_id, f.away_team_id)
        
        # Actual outcome
        if f.goals_home > f.goals_away:
            actual = 0
        elif f.goals_home == f.goals_away:
            actual = 1
        else:
            actual = 2
        
        # RPS
        for i in range(3):
            cum_pred = sum(probs[:i+1])
            cum_actual = 1.0 if actual <= i else 0.0
            total_rps += (cum_pred - cum_actual) ** 2
    
    return total_rps / (3 * len(test_fixtures))


def train_test_split(fixtures: list[Fixture], test_size: float = 0.2):
    """Split fixtures into train/test by time."""
    fixtures = sorted(fixtures, key=lambda f: f.date)
    split_idx = int(len(fixtures) * (1 - test_size))
    return fixtures[:split_idx], fixtures[split_idx:]


@dataclass
class BayesianDCResult:
    """Bayesian Dixon-Coles results with uncertainty."""
    attack: dict[int, float]
    attack_std: dict[int, float]
    defense: dict[int, float]
    defense_std: dict[int, float]
    home_adv: float
    home_adv_std: float
    rho: float
    rho_std: float
    n_samples: int
    log_marginal_likelihood: float


def fit_bayesian_dixon_coles(
    fixtures: list[Fixture],
    team_ids: list[int],
    xi: float = 0.005,
) -> BayesianDCResult:
    """
    Fit Bayesian Dixon-Coles: MLE point estimate + Fisher info for uncertainty.
    
    Uses the fast MLE Dixon-Coles for the point estimate, then computes
    posterior uncertainty via the observed Fisher information. This is
    the standard "empirical Bayes" / "approx posterior" approach.
    """
    n_teams = len(team_ids)
    team_idx = {tid: i for i, tid in enumerate(team_ids)}
    
    valid = [(f, team_idx.get(f.home_team_id), team_idx.get(f.away_team_id))
             for f in fixtures
             if f.goals_home is not None and f.goals_away is not None
             and f.home_team_id in team_idx and f.away_team_id in team_idx]
    
    if len(valid) < 50:
        return BayesianDCResult(
            attack={t: 0.0 for t in team_ids},
            attack_std={t: 1.0 for t in team_ids},
            defense={t: 0.0 for t in team_ids},
            defense_std={t: 1.0 for t in team_ids},
            home_adv=0.1,
            home_adv_std=0.5,
            rho=-0.1,
            rho_std=0.3,
            n_samples=len(valid),
            log_marginal_likelihood=float('nan'),
        )
    
    mle_params = fit_dixon_coles(fixtures, team_ids, xi)
    
    home_goals = np.array([f.goals_home for f, _, _ in valid])
    away_goals = np.array([f.goals_away for f, _, _ in valid])
    home_idx = np.array([idx for _, idx, _ in valid])
    away_idx = np.array([idx for _, _, idx in valid])
    latest_date = max(f.date for f, _, _ in valid)
    days_ago = np.array([(latest_date - f.date).days for f, _, _ in valid])
    
    map_params = np.array([
        *[mle_params.attack.get(tid, 0.0) for tid in team_ids],
        *[mle_params.defense.get(tid, 0.0) for tid in team_ids],
        mle_params.home_adv,
        mle_params.rho,
    ])
    
    try:
        posterior_cov = _compute_fisher_fast(
            map_params, home_goals, away_goals, home_idx, away_idx, days_ago, n_teams, xi
        )
        stds = np.sqrt(np.diag(posterior_cov))
        stds = np.clip(stds, 0.05, 2.0)
    except Exception:
        stds = np.ones_like(map_params) * 0.3
    
    attack_map = np.array([mle_params.attack.get(tid, 0.0) for tid in team_ids])
    defense_map = np.array([mle_params.defense.get(tid, 0.0) for tid in team_ids])
    attack_map = attack_map - np.mean(attack_map)
    defense_map = defense_map - np.mean(defense_map)
    
    return BayesianDCResult(
        attack={team_ids[i]: attack_map[i] for i in range(n_teams)},
        attack_std={team_ids[i]: stds[i] for i in range(n_teams)},
        defense={team_ids[i]: defense_map[i] for i in range(n_teams)},
        defense_std={team_ids[i]: stds[n_teams + i] for i in range(n_teams)},
        home_adv=mle_params.home_adv,
        home_adv_std=stds[2*n_teams],
        rho=mle_params.rho,
        rho_std=stds[2*n_teams + 1],
        n_samples=len(valid),
        log_marginal_likelihood=float('nan'),
    )


class BayesianDixonColesModel:
    """Bayesian Dixon-Coles model with uncertainty quantification."""
    
    def __init__(self, league_id: int | None = None, xi: float = 0.005, n_simulations: int = 1000):
        self.league_id = league_id
        self.xi = xi
        self.n_simulations = n_simulations
        self._result: BayesianDCResult | None = None
        self._team_ids: list[int] = []
        self._fitted = False
    
    def fit(self) -> "BayesianDixonColesModel":
        """Fit Bayesian model on historical data."""
        with get_session() as session:
            query = select(Fixture).where(Fixture.status == "FT")
            if self.league_id:
                query = query.where(Fixture.league_id == self.league_id)
            
            fixtures = session.execute(query).scalars().all()
            
            self._team_ids = sorted(set(
                f.home_team_id for f in fixtures
            ) | set(f.away_team_id for f in fixtures))
            
            self._result = fit_bayesian_dixon_coles(fixtures, self._team_ids, self.xi)
            self._fitted = True
            
            print(f"Bayesian Dixon-Coles fitted on {self._result.n_samples} matches")
            print(f"  Home advantage: {self._result.home_adv:.3f} ± {self._result.home_adv_std:.3f}")
            print(f"  Rho (correlation): {self._result.rho:.3f} ± {self._result.rho_std:.3f}")
            print(f"  Log marginal likelihood: {self._result.log_marginal_likelihood:.2f}")
        
        return self
    
    def _expected_goals(
        self, home_team_id: int, away_team_id: int
    ) -> tuple[float, float, float, float, float, float]:
        """Return (exp_home, exp_away, std_home, std_away, corr_rho)."""
        if not self._fitted or self._result is None:
            self.fit()
        
        attack_h = self._result.attack.get(home_team_id, 0.0)
        defense_h = self._result.defense.get(home_team_id, 0.0)
        attack_a = self._result.attack.get(away_team_id, 0.0)
        defense_a = self._result.defense.get(away_team_id, 0.0)
        
        std_ah = self._result.attack_std.get(home_team_id, 1.0)
        std_dh = self._result.defense_std.get(home_team_id, 1.0)
        std_aa = self._result.attack_std.get(away_team_id, 1.0)
        std_da = self._result.defense_std.get(away_team_id, 1.0)
        
        exp_home = np.exp(attack_h + defense_a + self._result.home_adv)
        exp_away = np.exp(attack_a + defense_h)
        
        var_home = std_ah**2 + std_da**2 + self._result.home_adv_std**2
        var_away = std_aa**2 + std_dh**2
        
        std_home = np.sqrt(var_home)
        std_away = np.sqrt(var_away)
        
        return exp_home, exp_away, std_home, std_away, self._result.rho
    
    def predict_proba(
        self,
        home_team_id: int,
        away_team_id: int,
        n_simulations: int | None = None,
    ) -> tuple[float, float, float]:
        """
        Predict probability of (home win, draw, away win) using Monte Carlo.
        
        Samples team parameters from posterior and averages predictions.
        Provides uncertainty via credible intervals.
        """
        if not self._fitted or self._result is None:
            self.fit()
        
        n_sim = n_simulations or self.n_simulations
        
        lambda_home, lambda_away, std_home, std_away, rho = self._expected_goals(
            home_team_id, away_team_id
        )
        
        home_wins = 0
        draws = 0
        away_wins = 0
        
        for _ in range(n_sim):
            lm = np.random.lognormal(mean=np.log(lambda_home) - 0.5*std_home**2, sigma=std_home)
            la = np.random.lognormal(mean=np.log(lambda_away) - 0.5*std_away**2, sigma=std_away)
            lm = max(lm, 0.01)
            la = max(la, 0.01)
            
            max_goals = 7
            prob_matrix = np.zeros((max_goals + 1, max_goals + 1))
            for i in range(max_goals + 1):
                for j in range(max_goals + 1):
                    p_home = np.exp(-lm) * (lm ** i) / math.factorial(i)
                    p_away = np.exp(-la) * (la ** j) / math.factorial(j)
                    tau = rho_correction(i, j, lm, la, rho)
                    prob_matrix[i, j] = p_home * p_away * tau
            
            total = prob_matrix.sum()
            if total > 0:
                prob_matrix /= total
            
            home_prob = prob_matrix[np.triu_indices(max_goals + 1, k=1)].sum()
            away_prob = prob_matrix[np.tril_indices(max_goals + 1, k=-1)].sum()
            draw_prob = prob_matrix.diagonal().sum()
            
            home_wins += home_prob
            draws += draw_prob
            away_wins += away_prob
        
        total = home_wins + draws + away_wins
        if total > 0:
            return home_wins / n_sim, draws / n_sim, away_wins / n_sim
        
        return 1/3, 1/3, 1/3
    
    def predict_uncertainty(
        self,
        home_team_id: int,
        away_team_id: int,
        n_simulations: int | None = None,
    ) -> dict[str, float]:
        """
        Get prediction with uncertainty bounds.
        
        Returns dict with mean probabilities and 5-95% credible intervals.
        """
        if not self._fitted or self._result is None:
            self.fit()
        
        n_sim = n_simulations or self.n_simulations
        
        home_probs = []
        draw_probs = []
        away_probs = []
        
        for _ in range(n_sim):
            ph, pd, pa = self.predict_proba(home_team_id, away_team_id, n_simulations=1)
            home_probs.append(ph)
            draw_probs.append(pd)
            away_probs.append(pa)
        
        home_probs = np.array(home_probs)
        draw_probs = np.array(draw_probs)
        away_probs = np.array(away_probs)
        
        return {
            "P_home": np.mean(home_probs),
            "P_home_low": np.percentile(home_probs, 5),
            "P_home_high": np.percentile(home_probs, 95),
            "P_draw": np.mean(draw_probs),
            "P_draw_low": np.percentile(draw_probs, 5),
            "P_draw_high": np.percentile(draw_probs, 95),
            "P_away": np.mean(away_probs),
            "P_away_low": np.percentile(away_probs, 5),
            "P_away_high": np.percentile(away_probs, 95),
            "spread": np.mean(home_probs) - np.mean(away_probs),
            "uncertainty": np.std(home_probs) + np.std(away_probs),
        }
    
    def calibration_score(self, test_fixtures: list[Fixture], n_bins: int = 10) -> float:
        """Calculate Brier score on test set."""
        from sklearn.metrics import brier_score_loss
        
        true_labels = []
        pred_probs = []
        
        for f in test_fixtures:
            ph, pd, pa = self.predict_proba(f.home_team_id, f.away_team_id)
            
            if f.goals_home > f.goals_away:
                label = 0
            elif f.goals_home == f.goals_away:
                label = 1
            else:
                label = 2
            
            true_labels.append(label)
            pred_probs.append([ph, pd, pa])
        
        true_labels = np.array(true_labels)
        pred_probs = np.array(pred_probs)
        
        brier = 0.0
        for i, label in enumerate(true_labels):
            for j in range(3):
                brier += (pred_probs[i, j] - (1 if label == j else 0)) ** 2
        return brier / (3 * len(true_labels))


_bayesian_cache: dict[int, BayesianDixonColesModel] = {}
_CACHE_TTL = 3600


def predict_bayesian_h2h(home_id: int, away_id: int, league_id: int | None = None) -> dict[str, float]:
    """
    Bayesian 1X2 prediction using Laplace-approximated Dixon-Coles.
    
    Uses Monte Carlo sampling from posterior for calibrated probabilities.
    Model is cached per league_id for performance.
    """
    model = _get_cached_bayesian_model(league_id)
    ph, pd, pa = model.predict_proba(home_id, away_id)
    return {"1": ph, "X": pd, "2": pa}


def predict_bayesian_h2h_with_uncertainty(
    home_id: int, away_id: int, league_id: int | None = None
) -> dict:
    """
    Bayesian 1X2 prediction with credible intervals.
    """
    model = _get_cached_bayesian_model(league_id)
    return model.predict_uncertainty(home_id, away_id)


def _get_cached_bayesian_model(league_id: int | None = None) -> BayesianDixonColesModel:
    """Get or create a cached Bayesian model for a league."""
    import time
    cache_key = league_id if league_id is not None else 0
    
    if cache_key not in _bayesian_cache:
        _bayesian_cache[cache_key] = BayesianDixonColesModel(league_id=league_id, n_simulations=500)
        _bayesian_cache[cache_key].fit()
        _bayesian_cache[cache_key]._cached_at = time.time()
    
    model = _bayesian_cache[cache_key]
    if time.time() - model._cached_at > _CACHE_TTL:
        model.fit()
        model._cached_at = time.time()
    
    return model