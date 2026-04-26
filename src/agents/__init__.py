"""
Multi-Agent Decision Architecture.

Three independent agents communicating via event bus:
- Predictor Agent: generates predictions and EV signals
- Risk Manager Agent: computes risk profile and constraints
- Execution Strategist Agent: builds optimized portfolio
"""

from src.agents.predictor.agent import get_predictor_agent
from src.agents.risk_manager.agent import get_risk_manager_agent
from src.agents.execution_strategist.agent import get_execution_strategist_agent
from src.agents.shared.state_store import get_state_store
from src.agents.shared.events import AgentEvents

__all__ = [
    "get_predictor_agent",
    "get_risk_manager_agent",
    "get_execution_strategist_agent",
    "get_state_store",
    "AgentEvents",
]
