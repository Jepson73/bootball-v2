"""
Markowitz Mean-Variance Portfolio Optimizer.

Optimizes stake distribution across betting opportunities using:
- Expected return vector (EV)
- Covariance matrix (correlation × volatility)
- Risk aversion parameter

Objective: maximize w^T μ - λ * w^T Σ w
Subject to: sum(w) <= bankroll, w >= 0, w <= max_bet
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from src.alerts.event_bus import event_bus, Events
from src.betting.correlation import get_correlation_engine

logger = logging.getLogger(__name__)


@dataclass
class BetCandidate:
    """Bet candidate for optimization."""
    id: str
    fixture_id: int
    market: str
    outcome: str
    odds: float
    prob: float
    ev: float
    kelly_fraction: float
    correlation_key: str = ""
    
    def __post_init__(self):
        if not self.id:
            self.id = f"{self.fixture_id}_{self.market}_{self.outcome}"


@dataclass
class OptimizedBet:
    """Optimized bet result."""
    bet_id: str
    market: str
    outcome: str
    odds: float
    stake: float
    weight: float
    expected_return: float
    risk_contribution: float


@dataclass
class OptimizationResult:
    """Result of portfolio optimization."""
    bets: list[OptimizedBet]
    bankroll: float
    expected_return: float
    variance: float
    risk: float
    sharpe_proxy: float
    num_bets: int
    status: str


@dataclass
class MarkowitzConfig:
    """Configuration for Markowitz optimization."""
    risk_aversion: float = 0.8
    max_bet_pct: float = 0.05
    min_bet: float = 10.0
    max_total_exposure: float = 0.40
    use_correlation_engine: bool = True


class MarkowitzOptimizer:
    """
    Markowitz mean-variance portfolio optimizer.
    
    Flow:
    1. Accept bet candidates
    2. Build expected return vector (EV)
    3. Build covariance matrix (correlation × volatility)
    4. Solve quadratic program
    5. Apply constraints
    6. Return optimized portfolio
    """
    
    def __init__(self, config: MarkowitzConfig = None):
        self.config = config or MarkowitzConfig()
        self.corr_engine = get_correlation_engine() if self.config.use_correlation_engine else None
        logger.info(f"MarkowitzOptimizer initialized with λ={self.config.risk_aversion}")
    
    def optimize(
        self,
        candidates: list[dict | BetCandidate],
        bankroll: float
    ) -> OptimizationResult:
        """
        Optimize portfolio using Markowitz mean-variance.
        
        Args:
            candidates: List of bet candidates
            bankroll: Current bankroll
            
        Returns:
            OptimizationResult with optimized bets
        """
        if not candidates:
            return self._empty_result(bankroll)
        
        # Convert to BetCandidate
        bets = self._convert_candidates(candidates)
        
        n = len(bets)
        logger.info(f"[MARKOWITZ] Optimizing {n} candidates")
        
        # Build return vector
        mu = self._build_return_vector(bets)
        
        # Build covariance matrix
        sigma = self._build_covariance_matrix(bets)
        
        # Solve optimization
        result = self._solve_qp(bets, mu, sigma, bankroll)
        
        # Log results
        self._log_results(result)
        
        return result
    
    def _convert_candidates(self, candidates: list) -> list[BetCandidate]:
        """Convert dict candidates to BetCandidate."""
        result = []
        for i, c in enumerate(candidates):
            if isinstance(c, BetCandidate):
                result.append(c)
            else:
                result.append(BetCandidate(
                    id=c.get("id", f"bet_{i}"),
                    fixture_id=c.get("fixture_id", 0),
                    market=c.get("market", "h2h"),
                    outcome=c.get("outcome", ""),
                    odds=c.get("odds", 0),
                    prob=c.get("prob", c.get("our_prob", 0.5)),
                    ev=c.get("ev", 0),
                    kelly_fraction=c.get("kelly_fraction", c.get("kelly", 0)),
                    correlation_key=c.get("correlation_key", f"fixture_{c.get('fixture_id', 0)}")
                ))
        return result
    
    def _build_return_vector(self, bets: list[BetCandidate]) -> np.ndarray:
        """Build expected return vector from EV values."""
        return np.array([b.ev for b in bets])
    
    def _volatility(self, bet: BetCandidate) -> float:
        """Compute volatility estimate for a bet."""
        p = bet.prob
        return np.sqrt(p * (1 - p))
    
    def _build_covariance_matrix(self, bets: list[BetCandidate]) -> np.ndarray:
        """Build covariance matrix using correlation × volatility."""
        n = len(bets)
        sigma = np.zeros((n, n))
        
        for i in range(n):
            for j in range(n):
                vol_i = self._volatility(bets[i])
                vol_j = self._volatility(bets[j])
                
                if i == j:
                    sigma[i, j] = vol_i ** 2
                else:
                    # Get correlation from correlation engine
                    corr = self._get_correlation(bets[i], bets[j])
                    sigma[i, j] = corr * vol_i * vol_j
        
        return sigma
    
    def _get_correlation(self, bet_a: BetCandidate, bet_b: BetCandidate) -> float:
        """Get correlation between two bets."""
        # Same fixture = high correlation
        if bet_a.fixture_id == bet_b.fixture_id:
            return 0.8
        
        # Same market = correlation from engine
        if bet_a.market == bet_b.market:
            return 0.5
        
        # Use correlation engine
        if self.corr_engine:
            return self.corr_engine.get_correlation(bet_a.market, bet_b.market)
        
        return 0.1  # Default low correlation
    
    def _solve_qp(
        self,
        bets: list[BetCandidate],
        mu: np.ndarray,
        sigma: np.ndarray,
        bankroll: float
    ) -> OptimizationResult:
        """Solve quadratic program using cvxpy."""
        try:
            import cvxpy as cp
            
            n = len(bets)
            w = cp.Variable(n)
            
            lambda_val = self.config.risk_aversion
            
            # Objective: maximize expected return - λ × variance
            expected_return = mu.T @ w
            variance = cp.quad_form(w, sigma)
            
            objective = cp.Maximize(expected_return - lambda_val * variance)
            
            # Constraints - use fractions (0-1 range)
            max_bet = self.config.max_bet_pct
            max_total = self.config.max_total_exposure
            
            constraints = [
                cp.sum(w) <= max_total,
                w >= 0,
                w <= max_bet
            ]
            
            # Solve
            problem = cp.Problem(objective, constraints)
            result = problem.solve(solver=cp.SCS)
            
            if problem.status not in ["optimal", "optimal_inaccurate"]:
                logger.warning(f"[MARKOWITZ] Optimization status: {problem.status}")
                return self._fallback_optimize(bets, mu, bankroll)
            
            # Extract weights
            weights = w.value
            
        except Exception as e:
            logger.warning(f"[MARKOWITZ] QP failed: {e}, using fallback")
            return self._fallback_optimize(bets, mu, bankroll)
        
        return self._build_result(bets, mu, sigma, weights, bankroll)
    
    def _fallback_optimize(
        self,
        bets: list[BetCandidate],
        mu: np.ndarray,
        bankroll: float
    ) -> OptimizationResult:
        """Fallback: proportional to EV."""
        n = len(bets)
        weights = np.zeros(n)
        
        # Simple proportional allocation
        ev_sum = max(sum(mu), 0.01)
        for i, bet in enumerate(bets):
            if bet.ev > 0.02:  # Minimum EV threshold
                weights[i] = (bet.ev / ev_sum) * bankroll * self.config.max_total_exposure
                weights[i] = min(weights[i], bankroll * self.config.max_bet_pct)
        
        return self._build_result(bets, mu, np.zeros((n, n)), weights, bankroll)
    
    def _build_result(
        self,
        bets: list[BetCandidate],
        mu: np.ndarray,
        sigma: np.ndarray,
        weights: np.ndarray,
        bankroll: float
    ) -> OptimizationResult:
        """Build optimization result from weights."""
        n = len(bets)
        
        optimized_bets = []
        total_stake = 0.0
        
        # Weights are fractions (0-1), convert to actual stake
        stakes = np.maximum(weights, 0) * bankroll
        
        for i, bet in enumerate(bets):
            stake = stakes[i]
            if stake < self.config.min_bet:
                continue
            
            total_stake += stake
            
            # Risk contribution: w_i * (Σw)_i / variance
            portfolio_var = weights @ sigma @ weights
            risk_contrib = 0.0
            if portfolio_var > 0:
                risk_contrib = stake * (sigma[i] @ weights) / portfolio_var
            
            # Use fractional weight for expected_return
            weight = max(weights[i], 0)
            
            optimized_bets.append(OptimizedBet(
                bet_id=bet.id,
                market=bet.market,
                outcome=bet.outcome,
                odds=bet.odds,
                stake=stake,
                weight=stake / bankroll if bankroll > 0 else 0,
                expected_return=bet.ev * weight,
                risk_contribution=risk_contrib
            ))
        
        # Calculate portfolio metrics
        expected_return = mu @ weights
        variance = weights @ sigma @ weights
        risk = np.sqrt(variance) if variance > 0 else 0
        
        sharpe = expected_return / risk if risk > 0 else 0
        
        return OptimizationResult(
            bets=optimized_bets,
            bankroll=bankroll,
            expected_return=expected_return,
            variance=variance,
            risk=risk,
            sharpe_proxy=sharpe,
            num_bets=len(optimized_bets),
            status="optimal"
        )
    
    def _empty_result(self, bankroll: float) -> OptimizationResult:
        """Return empty result."""
        return OptimizationResult(
            bets=[],
            bankroll=bankroll,
            expected_return=0,
            variance=0,
            risk=0,
            sharpe_proxy=0,
            num_bets=0,
            status="empty"
        )
    
    def _log_results(self, result: OptimizationResult) -> None:
        """Log optimization results."""
        logger.info(f"[MARKOWITZ] expected_return={result.expected_return:.3f}")
        logger.info(f"[MARKOWITZ] risk={result.risk:.3f}")
        logger.info(f"[MARKOWITZ] risk_aversion={self.config.risk_aversion}")
        logger.info(f"[MARKOWITZ] selected_bets={result.num_bets}")
        
        # Emit event
        event_bus.emit(Events.PORTFOLIO_OPTIMIZED, {
            "bankroll": result.bankroll,
            "expected_return": result.expected_return,
            "risk": result.risk,
            "sharpe_proxy": result.sharpe_proxy,
            "num_bets": result.num_bets,
            "status": result.status,
        })


# Global instance
_optimizer: Optional[MarkowitzOptimizer] = None


def get_markowitz_optimizer() -> MarkowitzOptimizer:
    """Get global Markowitz optimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = MarkowitzOptimizer()
    return _optimizer
