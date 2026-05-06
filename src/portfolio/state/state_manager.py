"""
StateManager - Stateful feedback loop for portfolio system.

Responsibilities:
1. Load previous PortfolioState
2. Merge new performance results
3. Compute updated state
4. Persist new state snapshot
"""

import logging
from datetime import datetime
from typing import Optional

from src.portfolio.state.portfolio_state import PortfolioState, StateSnapshot
from src.alerts.event_bus import event_bus

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages the stateful feedback loop.
    
    State evolution:
    previous_state + new_results → updated_state
    """
    
    def __init__(self):
        self.current_state: Optional[PortfolioState] = None
        
    def load_previous_state(self) -> PortfolioState:
        """Load or create initial state."""
        state = StateSnapshot.load_latest()
        
        if state is None:
            logger.info("[STATE] No previous state, creating initial state")
            self.current_state = PortfolioState(
                timestamp=datetime.utcnow().isoformat(),
                allocations={},
                exposure_by_market={},
                exposure_by_league={},
                risk_lambda=1.0,
                regime="neutral",
                drawdown=0.0,
                volatility=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                roi=0.0,
                run_count=0,
            )
        else:
            logger.info(f"[STATE] Loaded previous state from run {state.run_count}")
            self.current_state = state
        
        # Emit event
        event_bus.emit("PORTFOLIO_STATE_LOADED", {
            "run_count": self.current_state.run_count,
            "timestamp": self.current_state.timestamp,
        })
        
        return self.current_state
    
    def update_from_execution(
        self,
        allocations: list,
        total_stake: float,
        bet_count: int
    ) -> PortfolioState:
        """Update state after execution."""
        if not self.current_state:
            self.load_previous_state()
        
        # Update allocations
        new_allocations = {}
        market_exposure = {}
        league_exposure = {}
        
        for alloc in allocations:
            bet_id = alloc.get("bet_id", f"{alloc.get('market')}_{alloc.get('fixture_id')}")
            new_allocations[bet_id] = {
                "stake": alloc.get("stake", 0),
                "market": alloc.get("market", ""),
                "odds": alloc.get("odds", 0),
                "expected_return": alloc.get("expected_return", 0),
            }
            
            # Update market exposure
            market = alloc.get("market", "unknown")
            market_exposure[market] = market_exposure.get(market, 0) + alloc.get("stake", 0)
        
        # Calculate total exposure
        total_exposure = sum(market_exposure.values())
        
        # Normalize exposures to fractions
        if total_exposure > 0:
            market_exposure = {k: v/total_exposure for k, v in market_exposure.items()}
        
        self.current_state.allocations = new_allocations
        self.current_state.exposure_by_market = market_exposure
        self.current_state.exposure_by_league = league_exposure
        self.current_state.timestamp = datetime.utcnow().isoformat()
        self.current_state.run_count += 1
        
        return self.current_state
    
    def update_from_settlement(
        self,
        settled_bets: list,
        total_pnl: float,
        win_count: int,
        loss_count: int
    ) -> PortfolioState:
        """Update state after settlement."""
        if not self.current_state:
            self.load_previous_state()
        
        # Update P&L
        self.current_state.realized_pnl += total_pnl
        self.current_state.unrealized_pnl = 0.0  # All bets settled
        
        # Update ROI
        total_staked = sum(a.get("stake", 0) for a in self.current_state.allocations.values())
        if total_staked > 0:
            self.current_state.roi = self.current_state.realized_pnl / total_staked
        
        # Update historical tracking
        self.current_state.historical_roi.append(self.current_state.roi)
        if len(self.current_state.historical_roi) > 50:
            self.current_state.historical_roi = self.current_state.historical_roi[-50:]
        
        # Update drawdown
        if self.current_state.historical_roi:
            peak = max(self.current_state.historical_roi)
            current = self.current_state.roi
            self.current_state.drawdown = max(0, peak - current)
            self.current_state.historical_drawdown.append(self.current_state.drawdown)
            if len(self.current_state.historical_drawdown) > 50:
                self.current_state.historical_drawdown = self.current_state.historical_drawdown[-50:]
        
        self.current_state.timestamp = datetime.utcnow().isoformat()
        
        return self.current_state
    
    def update_from_learning(
        self,
        new_weights: dict,
        regime: str,
        lambda_val: float
    ) -> PortfolioState:
        """Update state after learning."""
        if not self.current_state:
            self.load_previous_state()
        
        # Update allocation weights
        self.current_state.allocation_weights = new_weights
        
        # Update risk context
        self.current_state.risk_lambda = lambda_val
        self.current_state.regime = regime
        
        self.current_state.timestamp = datetime.utcnow().isoformat()
        
        return self.current_state
    
    def persist_state(self, run_id: str, event_type: str) -> None:
        """Persist current state as snapshot."""
        if not self.current_state:
            logger.warning("[STATE] No state to persist")
            return
        
        snapshot = StateSnapshot(
            state=self.current_state,
            run_id=run_id,
            event_type=event_type
        )
        snapshot.save()
        
        # Emit event
        event_bus.emit("PORTFOLIO_STATE_UPDATED", {
            "run_id": run_id,
            "event_type": event_type,
            "run_count": self.current_state.run_count,
            "roi": self.current_state.roi,
            "regime": self.current_state.regime,
            "timestamp": self.current_state.timestamp,
        })
    
    def get_state(self) -> Optional[PortfolioState]:
        """Get current state."""
        return self.current_state


# Global manager
_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get global state manager."""
    global _manager
    if _manager is None:
        _manager = StateManager()
    return _manager
