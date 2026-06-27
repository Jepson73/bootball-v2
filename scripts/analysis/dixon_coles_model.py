"""
Dixon-Coles goal model (Dixon & Coles 1997) with time-decay.

Implements per-league MLE fitting of:
  λ = α_i × β_j × γ  (home expected goals)
  μ = α_j × β_i      (away expected goals)
  τ low-score correction for (0,0), (1,0), (0,1), (1,1)
  ξ annual time-decay: w = exp(-ξ × days_ago / 365)

All four markets (h2h, ou25, btts, ou15) are derived from one joint P(i,j) matrix.
Identifiability: log_alpha[0] pinned to 0 (α₀ = 1).
Thin-data fallback: league-mean α/β for unseen teams; global Poisson for no-data leagues.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson as scipy_poisson

MAX_GOALS = 8
MIN_LEAGUE_FIXTURES = 20
MIN_TEAM_APPEARANCES = 3

logger = logging.getLogger(__name__)


# ── Joint probability matrix ──────────────────────────────────────────────────

def joint_prob_matrix(lam: float, mu: float, rho: float,
                      max_goals: int = MAX_GOALS) -> np.ndarray:
    """Return (max_goals+1)×(max_goals+1) matrix P(home=i, away=j) with τ correction."""
    i = np.arange(max_goals + 1)
    mat = np.outer(scipy_poisson.pmf(i, max(lam, 1e-9)),
                   scipy_poisson.pmf(i, max(mu, 1e-9)))
    # τ corrections (Dixon & Coles 1997, Appendix A)
    mat[0, 0] = max(1e-15, mat[0, 0] * (1 - lam * mu * rho))
    mat[1, 0] = max(1e-15, mat[1, 0] * (1 + mu * rho))
    mat[0, 1] = max(1e-15, mat[0, 1] * (1 + lam * rho))
    mat[1, 1] = max(1e-15, mat[1, 1] * (1 - rho))
    mat = np.maximum(mat, 0.0)
    s = mat.sum()
    return mat / s if s > 0 else mat


def derive_markets(mat: np.ndarray) -> Dict[str, object]:
    """Derive all four market probabilities from joint P(i,j)."""
    n = mat.shape[0]
    idx = np.arange(n)
    home_g = idx[:, None]
    away_g = idx[None, :]
    total_g = home_g + away_g

    p_h = float(np.tril(mat, -1).sum())    # home goals > away goals
    p_d = float(np.trace(mat))              # home == away
    p_a = float(np.triu(mat, 1).sum())     # away > home

    p_ou25 = float(mat[total_g > 2].sum())
    p_ou15 = float(mat[total_g > 1].sum())
    p_btts = float(mat[1:, 1:].sum())

    return {
        'p_h2h': [p_h, p_d, p_a],    # P(H), P(D), P(A) — idx 0,1,2
        'p_ou25_over': p_ou25,
        'p_ou15_over': p_ou15,
        'p_btts_yes': p_btts,
    }


# ── Per-league model ──────────────────────────────────────────────────────────

class DixonColesLeague:
    """Dixon-Coles model for a single league."""

    def __init__(self, league_id: int, xi: float = 0.0, use_rho: bool = True):
        self.league_id = league_id
        self.xi = xi
        self.use_rho = use_rho
        # Fitted parameters
        self.teams: Optional[List[int]] = None
        self.log_alpha: Optional[np.ndarray] = None   # shape (N,), α[0]=1 enforced
        self.log_beta: Optional[np.ndarray] = None    # shape (N,)
        self.log_gamma: float = 0.2
        self.rho: float = 0.0
        self.global_log_alpha: float = 0.0            # mean; used for unseen teams
        self.global_log_beta: float = 0.0
        self.n_fixtures: int = 0
        self.converged: bool = False
        self.neg_ll_final: float = float('nan')
        self._team_appearances: Dict[int, int] = {}

    # ── Fitting ──────────────────────────────────────────────────────────────

    def fit(self, all_fixtures: List[dict], cutoff_date: str) -> 'DixonColesLeague':
        data = [
            f for f in all_fixtures
            if f['league_id'] == self.league_id
            and f['date'] < cutoff_date
            and f.get('goals_home') is not None
            and f.get('goals_away') is not None
        ]
        self.n_fixtures = len(data)
        if len(data) < MIN_LEAGUE_FIXTURES:
            return self

        teams = sorted(
            set(f['home_team_id'] for f in data) |
            set(f['away_team_id'] for f in data)
        )
        team_idx = {t: i for i, t in enumerate(teams)}
        N = len(teams)
        self.teams = teams

        appearances: Dict[int, int] = {}
        for f in data:
            appearances[f['home_team_id']] = appearances.get(f['home_team_id'], 0) + 1
            appearances[f['away_team_id']] = appearances.get(f['away_team_id'], 0) + 1
        self._team_appearances = appearances

        hidx = np.array([team_idx[f['home_team_id']] for f in data])
        aidx = np.array([team_idx[f['away_team_id']] for f in data])
        hg   = np.array([f['goals_home'] for f in data], dtype=float)
        ag   = np.array([f['goals_away'] for f in data], dtype=float)

        cutoff_dt = datetime.fromisoformat(cutoff_date[:10])
        days_ago = np.array([
            (cutoff_dt - datetime.fromisoformat(f['date'][:10])).days
            for f in data
        ], dtype=float)
        weights = np.exp(-self.xi * days_ago / 365.0)

        use_rho = self.use_rho
        n_params = (2 * N) if use_rho else (2 * N - 1)

        def neg_ll(params: np.ndarray) -> float:
            la = np.concatenate([[0.0], params[:N - 1]])  # log_alpha, α[0]=1
            lb = params[N - 1: 2 * N - 1]                # log_beta
            lg = params[2 * N - 1]                        # log_gamma
            rho = float(params[2 * N]) if use_rho else 0.0

            alpha = np.exp(la)
            beta  = np.exp(lb)
            gamma = np.exp(lg)

            lam = alpha[hidx] * beta[aidx] * gamma
            mu  = alpha[aidx] * beta[hidx]

            ll_h = hg * np.log(np.maximum(lam, 1e-12)) - lam - gammaln(hg + 1)
            ll_a = ag * np.log(np.maximum(mu,  1e-12)) - mu  - gammaln(ag  + 1)

            tau = np.ones(len(hg))
            m00 = (hg == 0) & (ag == 0)
            m10 = (hg == 1) & (ag == 0)
            m01 = (hg == 0) & (ag == 1)
            m11 = (hg == 1) & (ag == 1)
            tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
            tau[m10] = 1.0 + mu[m10] * rho
            tau[m01] = 1.0 + lam[m01] * rho
            tau[m11] = 1.0 - rho
            tau = np.clip(tau, 1e-12, None)

            return -float(np.dot(weights, np.log(tau) + ll_h + ll_a))

        # param layout: [log_alpha[1..N-1], log_beta[0..N-1], log_gamma, (rho)]
        if use_rho:
            x0 = np.zeros(2 * N + 1)
            x0[2 * N - 1] = 0.2   # log_gamma → γ ≈ 1.22 home advantage
            bounds = [(-5.0, 5.0)] * (N - 1) + [(-5.0, 5.0)] * N + [(-2.0, 2.0)] + [(-0.5, 0.5)]
        else:
            x0 = np.zeros(2 * N)
            x0[2 * N - 1] = 0.2
            bounds = [(-5.0, 5.0)] * (N - 1) + [(-5.0, 5.0)] * N + [(-2.0, 2.0)]

        res = minimize(neg_ll, x0, method='L-BFGS-B', bounds=bounds,
                       options={'maxiter': 500, 'ftol': 1e-9, 'gtol': 1e-5})

        p = res.x
        self.log_alpha = np.concatenate([[0.0], p[:N - 1]])
        self.log_beta  = p[N - 1: 2 * N - 1]
        self.log_gamma = float(p[2 * N - 1])
        self.rho       = float(p[2 * N]) if use_rho else 0.0
        self.converged = bool(res.success)
        self.neg_ll_final = float(res.fun)

        # Per-league priors for unseen teams
        self.global_log_alpha = float(self.log_alpha.mean())
        self.global_log_beta  = float(self.log_beta.mean())
        return self

    # ── Prediction ───────────────────────────────────────────────────────────

    def predict(self, home_team_id: int, away_team_id: int) -> Dict:
        if self.teams is None:
            return _global_fallback()

        ti = {t: i for i, t in enumerate(self.teams)}
        hi = ti.get(home_team_id)
        ai = ti.get(away_team_id)

        la_h = self.log_alpha[hi] if hi is not None else self.global_log_alpha
        la_a = self.log_alpha[ai] if ai is not None else self.global_log_alpha
        lb_h = self.log_beta[hi]  if hi is not None else self.global_log_beta
        lb_a = self.log_beta[ai]  if ai is not None else self.global_log_beta

        lam = float(np.exp(la_h + lb_a + self.log_gamma))
        mu  = float(np.exp(la_a + lb_h))

        mat     = joint_prob_matrix(lam, mu, self.rho)
        markets = derive_markets(mat)
        return {
            **markets,
            'lam': lam, 'mu': mu,
            'h_unknown': hi is None, 'a_unknown': ai is None,
        }

    def nll_on(self, fixtures: List[dict], cutoff_date: str) -> float:
        """Evaluate negative log-likelihood (unweighted) on given fixtures."""
        data = [
            f for f in fixtures
            if f['league_id'] == self.league_id
            and f['date'] >= cutoff_date[:10]   # evaluate on held-out
            and f.get('goals_home') is not None
            and f.get('goals_away') is not None
        ]
        if not data:
            return float('nan')
        total = 0.0
        for f in data:
            pred = self.predict(f['home_team_id'], f['away_team_id'])
            lam, mu = pred['lam'], pred['mu']
            hg, ag  = int(f['goals_home']), int(f['goals_away'])
            # τ correction
            tau = 1.0
            if hg == 0 and ag == 0:
                tau = max(1e-12, 1.0 - lam * mu * self.rho)
            elif hg == 1 and ag == 0:
                tau = max(1e-12, 1.0 + mu * self.rho)
            elif hg == 0 and ag == 1:
                tau = max(1e-12, 1.0 + lam * self.rho)
            elif hg == 1 and ag == 1:
                tau = max(1e-12, 1.0 - self.rho)
            ll = (np.log(tau)
                  + hg * np.log(max(lam, 1e-12)) - lam - gammaln(hg + 1)
                  + ag * np.log(max(mu,  1e-12)) - mu  - gammaln(ag + 1))
            total += ll
        return -total / len(data)   # mean NLL

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'league_id':        self.league_id,
            'xi':               self.xi,
            'use_rho':          self.use_rho,
            'teams':            self.teams,
            'log_alpha':        self.log_alpha.tolist() if self.log_alpha is not None else None,
            'log_beta':         self.log_beta.tolist()  if self.log_beta  is not None else None,
            'log_gamma':        self.log_gamma,
            'rho':              self.rho,
            'global_log_alpha': self.global_log_alpha,
            'global_log_beta':  self.global_log_beta,
            'n_fixtures':       self.n_fixtures,
            'converged':        self.converged,
            'neg_ll_final':     self.neg_ll_final,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'DixonColesLeague':
        m = cls(d['league_id'], d['xi'], d.get('use_rho', True))
        m.teams            = d['teams']
        m.log_alpha        = np.array(d['log_alpha']) if d['log_alpha'] is not None else None
        m.log_beta         = np.array(d['log_beta'])  if d['log_beta']  is not None else None
        m.log_gamma        = d['log_gamma']
        m.rho              = d['rho']
        m.global_log_alpha = d.get('global_log_alpha', 0.0)
        m.global_log_beta  = d.get('global_log_beta',  0.0)
        m.n_fixtures       = d['n_fixtures']
        m.converged        = d['converged']
        m.neg_ll_final     = d.get('neg_ll_final', float('nan'))
        return m


# ── Global fallback ───────────────────────────────────────────────────────────

def _global_fallback() -> Dict:
    """Poisson(1.5, 1.1) with no DC correction — used for zero-data leagues."""
    mat     = joint_prob_matrix(1.5, 1.1, 0.0)
    markets = derive_markets(mat)
    return {**markets, 'lam': 1.5, 'mu': 1.1, 'h_unknown': True, 'a_unknown': True}


# ── League set (one model per league) ────────────────────────────────────────

class DixonColesLeagueSet:
    """Collection of per-league DC models."""

    def __init__(self, xi: float = 0.0, use_rho: bool = True):
        self.xi = xi
        self.use_rho = use_rho
        self._models: Dict[int, DixonColesLeague] = {}

    def fit(self, all_fixtures: List[dict], cutoff_date: str,
            league_ids: Optional[List[int]] = None,
            n_jobs: int = 1) -> 'DixonColesLeagueSet':
        """Fit one model per league. Pre-groups by league to minimise pickling cost."""
        if league_ids is None:
            league_ids = sorted(set(f['league_id'] for f in all_fixtures))
        league_set = set(league_ids)

        # Group by league (only fixtures strictly before cutoff)
        by_league: Dict[int, List[dict]] = {lid: [] for lid in league_ids}
        for f in all_fixtures:
            lid = f['league_id']
            if lid in league_set and f['date'] < cutoff_date:
                by_league[lid].append(f)

        if n_jobs > 1:
            from multiprocessing import Pool
            # Pass only the per-league subset — avoids pickling 800K rows per worker
            args = [(lid, by_league[lid], cutoff_date, self.xi, self.use_rho)
                    for lid in league_ids]
            with Pool(n_jobs) as pool:
                results = pool.map(_fit_one_league, args)
            for m in results:
                self._models[m.league_id] = m
        else:
            for lid in league_ids:
                m = DixonColesLeague(lid, self.xi, self.use_rho)
                # Pass pre-filtered list — fit's league_id filter becomes a no-op
                m.fit(by_league[lid], cutoff_date)
                self._models[lid] = m

        return self

    def predict(self, fixture: dict) -> Dict:
        lid = fixture['league_id']
        m   = self._models.get(lid)
        if m is None or m.teams is None:
            return _global_fallback()
        return m.predict(fixture['home_team_id'], fixture['away_team_id'])

    def avg_nll_on(self, fixtures: List[dict], cutoff_from: str) -> float:
        """Mean NLL over all fixtures at or after cutoff_from."""
        league_ids = set(f['league_id'] for f in fixtures if f['date'] >= cutoff_from[:10])
        nlls = []
        for lid in league_ids:
            m = self._models.get(lid)
            if m is not None and m.teams is not None:
                v = m.nll_on(fixtures, cutoff_from)
                if not np.isnan(v):
                    nlls.append(v)
        return float(np.mean(nlls)) if nlls else float('nan')

    def save(self, path: str) -> None:
        data = {str(lid): m.to_dict() for lid, m in self._models.items()}
        with open(path, 'w') as f:
            json.dump({'xi': self.xi, 'use_rho': self.use_rho, 'models': data}, f)

    @classmethod
    def load(cls, path: str) -> 'DixonColesLeagueSet':
        with open(path) as f:
            data = json.load(f)
        obj = cls(data['xi'], data.get('use_rho', True))
        for lid_s, d in data['models'].items():
            obj._models[int(lid_s)] = DixonColesLeague.from_dict(d)
        return obj

    def summary(self) -> dict:
        fitted = [m for m in self._models.values() if m.teams is not None]
        rhos   = [m.rho for m in fitted if m.use_rho]
        gammas = [np.exp(m.log_gamma) for m in fitted]
        return {
            'n_leagues': len(self._models),
            'n_fitted':  len(fitted),
            'n_no_data': len(self._models) - len(fitted),
            'rho_mean':  float(np.mean(rhos)) if rhos else float('nan'),
            'rho_std':   float(np.std(rhos))  if rhos else float('nan'),
            'rho_min':   float(np.min(rhos))  if rhos else float('nan'),
            'rho_max':   float(np.max(rhos))  if rhos else float('nan'),
            'gamma_mean': float(np.mean(gammas)) if gammas else float('nan'),
            'gamma_std':  float(np.std(gammas))  if gammas else float('nan'),
            'pct_converged': float(np.mean([m.converged for m in fitted])) if fitted else float('nan'),
        }


def _fit_one_league(args: tuple) -> DixonColesLeague:
    """Module-level function for multiprocessing compatibility. Receives pre-filtered fixtures."""
    lid, league_fixtures, cutoff_date, xi, use_rho = args
    m = DixonColesLeague(lid, xi, use_rho)
    # league_fixtures already filtered to this league + before cutoff_date
    m.fit(league_fixtures, cutoff_date)
    return m
