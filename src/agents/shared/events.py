"""
Shared event definitions for multi-agent architecture.

All inter-agent communication MUST use these events.
"""

from src.alerts.event_bus import Events as BaseEvents


class AgentEvents(BaseEvents):
    """Events for multi-agent coordination."""
    
    # Predictor -> Risk Manager, Execution Strategist
    PREDICTIONS_READY = "predictions_ready"
    
    # Risk Manager -> Execution Strategist
    RISK_PROFILE_UPDATED = "risk_profile_updated"
    
    # Execution Strategist -> Adversarial Agent -> Execution Engine
    PORTFOLIO_ALLOCATED = "portfolio_allocated"
    EXECUTION_REQUESTED = "execution_requested"
    
    # Adversarial Agent
    PORTFOLIO_STRESSED = "portfolio_stressed"
    PORTFOLIO_VETOED = "portfolio_vetoed"
    
    # Agent lifecycle
    AGENT_INITIALIZED = "agent_initialized"
    AGENT_ERROR = "agent_error"
    
    # Run lifecycle
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"


# Event payload schemas
PREDICTION_PAYLOAD = {
    "fixture_id": int,
    "market": str,
    "outcome": str,
    "probabilities": dict,  # {"home": 0.45, "draw": 0.30, "away": 0.25}
    "ev": float,
    "kelly": float,
    "odds": float,
    "timestamp": str,
}

RISK_PROFILE_PAYLOAD = {
    "lambda": float,
    "regime": str,  # "bull", "neutral", "defensive"
    "max_exposure_per_fixture": float,
    "max_total_risk": float,
    "correlation_penalties": dict,
    "drawdown": float,
    "volatility": float,
    "timestamp": str,
}

PORTFOLIO_PAYLOAD = {
    "bets": list,  # [{"bet_id": str, "fixture_id": int, "market": str, "stake": float, ...}]
    "total_stake": float,
    "expected_return": float,
    "risk": float,
    "sharpe_proxy": float,
    "timestamp": str,
}
