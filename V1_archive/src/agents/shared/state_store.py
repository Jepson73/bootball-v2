"""
Shared state store for multi-agent system.

Provides read-only access to historical state for all agents.
No agent directly modifies another agent's state.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RunState:
    """State for current run."""
    run_id: str = ""
    started_at: Optional[datetime] = None
    predictions_generated: int = 0
    bets_placed: int = 0
    total_stake: float = 0.0


@dataclass
class HistoricalState:
    """Historical state for risk calculations."""
    bankroll_history: list[float] = field(default_factory=list)
    returns_history: list[float] = field(default_factory=list)
    drawdown_history: list[float] = field(default_factory=list)
    regime_history: list[str] = field(default_factory=list)
    lambda_history: list[float] = field(default_factory=list)


class StateStore:
    """
    Centralized state for multi-agent system.
    
    Provides:
    - Current run state
    - Historical state for risk calculations
    - Volatility windows
    """
    
    def __init__(self):
        self.run_state = RunState()
        self.historical = HistoricalState()
        from config.settings import settings
        self._current_bankroll: float = settings.initial_bankroll
        self._initial_bankroll: float = settings.initial_bankroll
        
    def start_run(self, run_id: str) -> None:
        """Start a new run."""
        self.run_state = RunState(run_id=run_id, started_at=datetime.utcnow())
        logger.info(f"[STATE] Run started: {run_id}")
    
    def end_run(self) -> None:
        """End current run."""
        if self.run_state.started_at:
            duration = (datetime.utcnow() - self.run_state.started_at).total_seconds()
            logger.info(f"[STATE] Run ended: {self.run_state.run_id}, duration={duration:.1f}s")
    
    def update_bankroll(self, new_balance: float) -> None:
        """Update current bankroll and record history."""
        if self._current_bankroll > 0:
            daily_return = (new_balance - self._current_bankroll) / self._current_bankroll
            self.historical.returns_history.append(daily_return)
            
        self.historical.bankroll_history.append(new_balance)
        self._current_bankroll = new_balance
        
    def get_current_bankroll(self) -> float:
        """Get current bankroll."""
        return self._current_bankroll
    
    def get_drawdown(self) -> float:
        """Calculate current drawdown from peak."""
        if not self.historical.bankroll_history:
            return 0.0
            
        peak = max(self.historical.bankroll_history)
        current = self._current_bankroll
        drawdown = (peak - current) / peak if peak > 0 else 0.0
        return drawdown
    
    def get_volatility(self, window: int = 30) -> float:
        """Calculate volatility (std dev of returns)."""
        import numpy as np
        
        returns = self.historical.returns_history[-window:]
        if len(returns) < 2:
            return 0.0
            
        return float(np.std(returns))
    
    def get_average_return(self, window: int = 30) -> float:
        """Get average return over window."""
        returns = self.historical.returns_history[-window:]
        if not returns:
            return 0.0
        return sum(returns) / len(returns)
    
    def record_regime(self, regime: str) -> None:
        """Record regime decision."""
        self.historical.regime_history.append(regime)
    
    def record_lambda(self, lambda_val: float) -> None:
        """Record lambda value."""
        self.historical.lambda_history.append(lambda_val)
    
    def get_predictions_generated(self) -> int:
        """Get number of predictions generated in current run."""
        return self.run_state.predictions_generated
    
    def increment_predictions(self, count: int = 1) -> None:
        """Increment prediction count."""
        self.run_state.predictions_generated += count
    
    def get_bets_placed(self) -> int:
        """Get bets placed in current run."""
        return self.run_state.bets_placed
    
    def record_bets_placed(self, count: int, total_stake: float) -> None:
        """Record bets placed."""
        self.run_state.bets_placed += count
        self.run_state.total_stake += total_stake


# Global state store
_store: Optional[StateStore] = None


def get_state_store() -> StateStore:
    """Get global state store."""
    global _store
    if _store is None:
        _store = StateStore()
    return _store
