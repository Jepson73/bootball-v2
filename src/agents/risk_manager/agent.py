"""
Risk Manager Agent - computes risk profile and constraints.

STATEFUL VERSION - accepts PortfolioState for drawdown/volatility.

Responsibilities:
- Compute drawdown, volatility, bankroll state
- Compute lambda (risk aversion)
- Determine regime (bull/neutral/defensive)
- Output correlation penalties

Hard rules:
- Cannot select bets
- Cannot access execution engine
"""

import logging
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus
from src.agents.shared.events import AgentEvents, RISK_PROFILE_PAYLOAD
from src.agents.shared.state_store import get_state_store
from src.portfolio.state.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)


# Configuration
LAMBDA_BASE = 1.0
ALPHA_DRAWDOWN = 2.0  # drawdown multiplier
BETA_VOLATILITY = 1.5  # volatility multiplier
DRAWDOWN_THRESHOLD_DEFENSIVE = 0.10  # 10% drawdown triggers defensive
VOLATILITY_THRESHOLD_DEFENSIVE = 0.05  # 5% daily volatility triggers defensive


class RiskManagerAgent:
    """
    Agent responsible for computing risk profile.
    
    Flow:
    1. Calculate current drawdown and volatility
    2. Compute lambda based on market conditions
    3. Determine regime
    4. Calculate risk constraints
    5. Emit RISK_PROFILE_UPDATED event
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self._event_bus = event_bus
        self._current_regime = "neutral"
        self._current_lambda = LAMBDA_BASE
        
        # Subscribe to events
        self._event_bus.subscribe(AgentEvents.PREDICTIONS_READY, self.handle_predictions_ready)
        
        logger.info("[RISK] Agent initialized")
    
    def handle_predictions_ready(self, payload: dict) -> None:
        """Handle predictions ready event."""
        self.run()
    
    def run(self, portfolio_state: PortfolioState = None) -> dict:
        """
        Compute risk profile - STATEFUL VERSION.
        
        Args:
            portfolio_state: Optional PortfolioState for stateful risk calculation
            
        Returns:
            Risk profile dict
        """
        logger.info("[RISK] Computing risk profile")
        
        # Step 1: Calculate metrics - prefer PortfolioState if provided
        if portfolio_state is not None:
            drawdown = portfolio_state.drawdown
            volatility = portfolio_state.volatility
            logger.info(f"[RISK] Using stateful metrics: drawdown={drawdown:.2%}, volatility={volatility:.2%}")
        else:
            drawdown = self.state_store.get_drawdown()
            volatility = self.state_store.get_volatility()
        
        # Step 2: Compute lambda
        lambda_val = self._compute_lambda(drawdown, volatility)
        
        # Step 3: Determine regime
        regime = self._determine_regime(drawdown, volatility)
        
        # Step 4: Calculate constraints
        profile = self._build_profile(lambda_val, regime, drawdown, volatility)
        
        # Step 5: Record state
        self.state_store.record_regime(regime)
        self.state_store.record_lambda(lambda_val)
        
        # Step 6: Emit risk profile
        self._emit_risk_profile(profile)
        
        logger.info(f"[RISK] regime={regime} λ={lambda_val:.2f} drawdown={drawdown:.2%}")
        return profile
    
    def _compute_lambda(self, drawdown: float, volatility: float) -> float:
        """
        Compute risk aversion lambda.
        
        λ(t) = λ_base × (1 + α·drawdown + β·volatility)
        """
        return LAMBDA_BASE * (1 + ALPHA_DRAWDOWN * drawdown + BETA_VOLATILITY * volatility)
    
    def _determine_regime(self, drawdown: float, volatility: float) -> str:
        """
        Determine market regime.
        
        Rules:
        - Defensive: high drawdown OR high volatility
        - Bull: low drawdown AND low volatility
        - Neutral: normal conditions
        """
        if drawdown >= DRAWDOWN_THRESHOLD_DEFENSIVE or volatility >= VOLATILITY_THRESHOLD_DEFENSIVE:
            return "defensive"
        elif drawdown < 0.02 and volatility < 0.01:
            return "bull"
        else:
            return "neutral"
    
    def _build_profile(self, lambda_val: float, regime: str, drawdown: float, volatility: float) -> dict:
        """Build risk profile with constraints."""
        
        # Regime-based constraints
        if regime == "defensive":
            max_exposure = 0.02  # 2% max per fixture
            max_total = 0.10    # 10% total max
        elif regime == "bull":
            max_exposure = 0.05  # 5% per fixture
            max_total = 0.40    # 40% total max
        else:  # neutral
            max_exposure = 0.03  # 3% per fixture
            max_total = 0.25     # 25% total max
        
        # Correlation penalties - use tuples for proper key handling
        correlation_penalties = {
            ("btts", "ou25"): 0.65,
            ("ou25", "ou15"): 0.70,
            ("h2h", "btts"): 0.20,
        }
        
        return {
            "lambda": lambda_val,
            "regime": regime,
            "max_exposure_per_fixture": max_exposure,
            "max_total_risk": max_total,
            "correlation_penalties": correlation_penalties,
            "drawdown": drawdown,
            "volatility": volatility,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def _emit_risk_profile(self, profile: dict) -> None:
        """Emit risk profile updated event."""
        self._event_bus.emit(AgentEvents.RISK_PROFILE_UPDATED, profile)


# Global instance
_agent: Optional[RiskManagerAgent] = None


def get_risk_manager_agent() -> RiskManagerAgent:
    """Get global risk manager agent."""
    global _agent
    if _agent is None:
        _agent = RiskManagerAgent()
    return _agent
