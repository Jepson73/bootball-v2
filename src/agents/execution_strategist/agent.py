"""
Execution Strategist Agent - builds optimized portfolio.

Responsibilities:
- Take predictions (EV signals)
- Take risk profile (lambda, regime, constraints)
- Apply correlation matrix
- Solve Markowitz-style portfolio optimization
- Emit PORTFOLIO_ALLOCATED and EXECUTION_REQUESTED events

Hard rules:
- Cannot execute bets directly
- Must use risk constraints from Risk Manager
"""

import logging
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus
from src.agents.shared.events import AgentEvents, PORTFOLIO_PAYLOAD
from src.agents.shared.state_store import get_state_store
from src.betting.portfolio import get_markowitz_optimizer
from src.betting.correlation import get_correlation_engine

logger = logging.getLogger(__name__)


class ExecutionStrategistAgent:
    """
    Agent responsible for portfolio construction.
    
    Flow:
    1. Receive predictions (PREDICTIONS_READY)
    2. Receive risk profile (RISK_PROFILE_UPDATED)
    3. Apply correlation constraints
    4. Solve portfolio optimization
    5. Emit PORTFOLIO_ALLOCATED
    6. Emit EXECUTION_REQUESTED
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self._event_bus = event_bus
        self._markowitz = get_markowitz_optimizer()
        self._correlation = get_correlation_engine()
        
        self._last_predictions: list[dict] = []
        self._last_risk_profile: Optional[dict] = None
        
        # Subscribe to events
        self._event_bus.subscribe(AgentEvents.PREDICTIONS_READY, self.handle_predictions_ready)
        self._event_bus.subscribe(AgentEvents.RISK_PROFILE_UPDATED, self.handle_risk_profile_updated)
        
        logger.info("[EXECUTION] Agent initialized")
    
    def handle_predictions_ready(self, payload: dict) -> None:
        """Store predictions for later use."""
        self._last_predictions = payload.get("predictions", [])
        logger.info(f"[EXECUTION] Received {len(self._last_predictions)} predictions")
        
        # If we have risk profile, run optimization now
        if self._last_risk_profile:
            self.run()
    
    def handle_risk_profile_updated(self, payload: dict) -> None:
        """Store risk profile for later use."""
        self._last_risk_profile = payload
        logger.info(f"[EXECUTION] Received risk profile: regime={payload.get('regime')}")
        
        # If we have predictions, run optimization now
        if self._last_predictions:
            self.run()
    
    def run(self) -> list[dict]:
        """
        Build optimized portfolio.
        
        Returns:
            List of allocated bets
        """
        if not self._last_predictions or not self._last_risk_profile:
            logger.warning("[EXECUTION] Missing predictions or risk profile")
            return []
        
        logger.info("[EXECUTION] Building optimized portfolio")
        
        # Step 1: Apply risk constraints to optimizer
        self._apply_risk_constraints()
        
        # Step 2: Convert predictions to candidates
        candidates = self._prepare_candidates()
        
        # Step 3: Run optimization
        bankroll = self.state_store.get_current_bankroll()
        result = self._markowitz.optimize(candidates, bankroll)
        
        # Step 4: Build portfolio
        portfolio = self._build_portfolio(result)
        
        # Step 5: Record bets placed
        self.state_store.record_bets_placed(
            len(portfolio), 
            sum(b["stake"] for b in portfolio)
        )
        
        # Step 6: Emit events
        self._emit_portfolio_allocated(portfolio, result)
        self._emit_execution_requested(portfolio)
        
        logger.info(f"[EXECUTION] Allocated {len(portfolio)} bets, total stake={sum(b['stake'] for b in portfolio):.2f}")
        
        return portfolio
    
    def _apply_risk_constraints(self) -> None:
        """Apply risk constraints from risk profile to optimizer."""
        if not self._last_risk_profile:
            return
        
        profile = self._last_risk_profile
        
        # Update Markowitz config
        self._markowitz.config.risk_aversion = profile["lambda"]
        self._markowitz.config.max_bet_pct = profile["max_exposure_per_fixture"]
        self._markowitz.config.max_total_exposure = profile["max_total_risk"]
        
        # Update correlation penalties
        penalties = profile.get("correlation_penalties", {})
        for (market_a, market_b), corr in penalties.items():
            self._correlation.correlation_matrix[(market_a, market_b)] = corr
            self._correlation.correlation_matrix[(market_b, market_a)] = corr
    
    def _prepare_candidates(self) -> list[dict]:
        """Prepare candidates for optimization."""
        candidates = []
        
        for pred in self._last_predictions:
            candidates.append({
                "id": f"{pred['fixture_id']}_{pred['market']}_{pred['outcome']}",
                "fixture_id": pred["fixture_id"],
                "market": pred["market"],
                "outcome": pred["outcome"],
                "odds": pred["odds"],
                "our_prob": pred["probabilities"].get("home", 0.5),
                "ev": pred["ev"],
                "kelly": pred["kelly"],
            })
        
        return candidates
    
    def _build_portfolio(self, result) -> list[dict]:
        """Build portfolio from optimization result."""
        portfolio = []
        
        for bet in result.bets:
            portfolio.append({
                "bet_id": bet.bet_id,
                "fixture_id": 0,  # Would need to parse from bet_id
                "market": bet.market,
                "outcome": bet.outcome,
                "stake": bet.stake,
                "expected_return": bet.expected_return,
                "risk_contribution": bet.risk_contribution,
            })
        
        return portfolio
    
    def _emit_portfolio_allocated(self, portfolio: list[dict], result) -> None:
        """Emit portfolio allocated event."""
        payload = {
            "bets": portfolio,
            "total_stake": sum(b["stake"] for b in portfolio),
            "expected_return": result.expected_return,
            "risk": result.risk,
            "sharpe_proxy": result.sharpe_proxy,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._event_bus.emit(AgentEvents.PORTFOLIO_ALLOCATED, payload)
    
    def _emit_execution_requested(self, portfolio: list[dict]) -> None:
        """Emit execution requested event."""
        self._event_bus.emit(AgentEvents.EXECUTION_REQUESTED, {
            "portfolio": portfolio,
            "timestamp": datetime.utcnow().isoformat(),
        })


# Global instance
_agent: Optional[ExecutionStrategistAgent] = None


def get_execution_strategist_agent() -> ExecutionStrategistAgent:
    """Get global execution strategist agent."""
    global _agent
    if _agent is None:
        _agent = ExecutionStrategistAgent()
    return _agent
