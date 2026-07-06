"""
Monte Carlo Simulation Engine - Real trajectory simulation.

Simulates thousands of potential outcomes for a given portfolio
to estimate expected return, volatility, max drawdown, and ruin probability.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

import numpy as np

from src.events.event_bus import event_bus, Events

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloConfig:
    """Configuration for Monte Carlo simulation."""
    n_runs: int = 5000
    initial_bankroll: float = 1000.0
    random_seed: Optional[int] = None
    
    def __post_init__(self):
        if self.random_seed is not None:
            np.random.seed(self.random_seed)


@dataclass
class SimulationResult:
    """Results from Monte Carlo simulation."""
    expected_return: float = 0.0
    volatility: float = 0.0
    max_drawdown: float = 0.0
    ruin_probability: float = 0.0
    percentile_5: float = 0.0
    percentile_95: float = 0.0
    median_outcome: float = 0.0
    win_rate: float = 0.0
    trajectories: list = field(default_factory=list)
    n_simulations: int = 0


class MonteCarloEngine:
    """
    Monte Carlo simulation engine for portfolio risk assessment.
    
    Simulates thousands of potential outcomes to estimate:
    - Expected return
    - Volatility
    - Max drawdown
    - Ruin probability
    """
    
    def __init__(self, config: MonteCarloConfig = None):
        self.config = config or MonteCarloConfig()
        self._last_result: Optional[SimulationResult] = None
        
        logger.info(f"[MC] MonteCarloEngine initialized with {self.config.n_runs} runs")
    
    def simulate(
        self,
        portfolio: list[dict],
        bankroll: float,
        risk_profile: dict = None,
        n_runs: int = None
    ) -> SimulationResult:
        """
        Run Monte Carlo simulation on portfolio.
        
        Args:
            portfolio: List of bet dicts with keys: odds, probability, stake
            bankroll: Current bankroll
            risk_profile: Risk profile with lambda, regime
            n_runs: Override number of simulations
            
        Returns:
            SimulationResult with trajectory statistics
        """
        n_runs = n_runs or self.config.n_runs
        
        logger.info(f"[MC] Starting simulation: {n_runs} runs, {len(portfolio)} bets")
        
        if not portfolio:
            logger.warning("[MC] Empty portfolio, returning zero result")
            return SimulationResult()
        
        initial_bankroll = bankroll
        trajectories = []
        final_balances = []
        
        for run_idx in range(n_runs):
            # Simulate this run
            balance = initial_bankroll
            
            for bet in portfolio:
                # Get bet parameters
                odds = bet.get("odds", 0)
                prob = bet.get("our_prob", bet.get("probability", 0.5))
                stake = bet.get("stake", 0)
                
                # Sample outcome
                if np.random.random() < prob:
                    # Win: balance += stake * (odds - 1)
                    balance += stake * (odds - 1)
                else:
                    # Loss: balance -= stake
                    balance -= stake
            
            # Track trajectory
            trajectories.append(balance)
            final_balances.append(balance)
        
        # Convert to numpy for analysis
        final_balances = np.array(final_balances)
        
        # Compute statistics
        expected_return = float(np.mean(final_balances) - initial_bankroll)
        volatility = float(np.std(final_balances))
        
        # Max drawdown (simplified - track peak to trough)
        sorted_balances = np.sort(final_balances)
        max_drawdown = float((initial_bankroll - sorted_balances[0]) / initial_bankroll)
        
        # Ruin probability (balance <= 0)
        ruin_count = np.sum(final_balances <= 0)
        ruin_probability = float(ruin_count / n_runs)
        
        # Percentiles
        percentile_5 = float(np.percentile(final_balances, 5))
        percentile_95 = float(np.percentile(final_balances, 95))
        median_outcome = float(np.median(final_balances))
        
        # Win rate (positive returns)
        win_rate = float(np.sum(final_balances > initial_bankroll) / n_runs)
        
        # Store result
        result = SimulationResult(
            expected_return=expected_return,
            volatility=volatility,
            max_drawdown=max_drawdown,
            ruin_probability=ruin_probability,
            percentile_5=percentile_5,
            percentile_95=percentile_95,
            median_outcome=median_outcome,
            win_rate=win_rate,
            trajectories=trajectories[-100:],  # Keep last 100 for memory
            n_simulations=n_runs
        )
        
        self._last_result = result
        
        logger.info(
            f"[MC] Simulation complete: "
            f"exp_return={expected_return:.2f}, "
            f"vol={volatility:.2f}, "
            f"max_dd={max_drawdown:.2%}, "
            f"ruin_prob={ruin_probability:.2%}"
        )
        
        # Emit event
        event_bus.emit(Events.MONTE_CARLO_COMPLETED, {
            "expected_return": expected_return,
            "volatility": volatility,
            "max_drawdown": max_drawdown,
            "ruin_probability": ruin_probability,
            "n_simulations": n_runs,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        return result
    
    def get_last_result(self) -> Optional[SimulationResult]:
        """Get last simulation result."""
        return self._last_result


# Global instance
_engine: Optional[MonteCarloEngine] = None


def get_monte_carlo_engine() -> MonteCarloEngine:
    """Get global Monte Carlo engine."""
    global _engine
    if _engine is None:
        _engine = MonteCarloEngine()
    return _engine
