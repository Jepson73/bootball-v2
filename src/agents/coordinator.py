"""
Multi-Agent Coordinator.

Orchestrates the three agents in sequence:
1. Predictor Agent → generates predictions
2. Risk Manager Agent → computes risk profile
3. Execution Strategist Agent → builds portfolio
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus
from src.agents.shared.events import AgentEvents
from src.agents.predictor.agent import get_predictor_agent
from src.agents.risk_manager.agent import get_risk_manager_agent
from src.agents.execution_strategist.agent import get_execution_strategist_agent
from src.agents.shared.state_store import get_state_store
from src.notifications.agent_reporter import get_agent_reporter

logger = logging.getLogger(__name__)


class AgentCoordinator:
    """
    Coordinates multi-agent decision pipeline.
    
    Flow:
    1. Start run (emit RUN_STARTED)
    2. Run Predictor Agent
    3. Run Risk Manager Agent
    4. Run Execution Strategist Agent
    5. End run (emit RUN_COMPLETED)
    6. Generate reports
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self.reporter = get_agent_reporter()
        
        # Get agent instances
        self.predictor = get_predictor_agent()
        self.risk_manager = get_risk_manager_agent()
        self.execution_strategist = get_execution_strategist_agent()
        
        logger.info("[COORDINATOR] Multi-agent system initialized")
    
    def run(self) -> dict:
        """
        Execute full multi-agent pipeline.
        
        Returns:
            Run summary dict
        """
        run_id = str(uuid.uuid4())[:8]
        logger.info(f"[COORDINATOR] Starting run {run_id}")
        
        # Initialize reporter
        self.reporter.start_run()
        
        # Emit run started
        event_bus.emit(AgentEvents.RUN_STARTED, {
            "run_id": run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        try:
            # Step 1: Run Predictor
            logger.info("[COORDINATOR] Step 1: Running Predictor Agent")
            predictions = self.predictor.run()
            
            avg_ev = sum(p["ev"] for p in predictions) / len(predictions) if predictions else 0
            self.reporter.record_predictions(len(predictions), avg_ev)
            
            # Step 2: Run Risk Manager (triggered by event, but run manually for sync)
            logger.info("[COORDINATOR] Step 2: Running Risk Manager Agent")
            risk_profile = self.risk_manager.run()
            
            self.reporter.record_risk(
                risk_profile["regime"],
                risk_profile["lambda"],
                risk_profile["drawdown"]
            )
            
            # Step 3: Run Execution Strategist (triggered by events, but run manually)
            logger.info("[COORDINATOR] Step 3: Running Execution Strategist Agent")
            portfolio = self.execution_strategist.run()
            
            # Get execution results
            total_stake = sum(b["stake"] for b in portfolio)
            expected_return = sum(b["expected_return"] for b in portfolio)
            
            self.reporter.record_execution(
                len(portfolio),
                total_stake,
                expected_return,
                expected_return  # Using as risk proxy
            )
            
            # End run
            self.state_store.end_run()
            event_bus.emit(AgentEvents.RUN_COMPLETED, {
                "run_id": run_id,
                "predictions": len(predictions),
                "bets": len(portfolio),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Save reports
            self.reporter.save_reports()
            
            summary = {
                "run_id": run_id,
                "predictions": len(predictions),
                "bets": len(portfolio),
                "total_stake": total_stake,
                "expected_return": expected_return,
            }
            
            logger.info(f"[COORDINATOR] Run {run_id} completed: {len(portfolio)} bets placed")
            
            return summary
            
        except Exception as e:
            logger.error(f"[COORDINATOR] Run failed: {e}")
            event_bus.emit(AgentEvents.AGENT_ERROR, {
                "run_id": run_id,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            })
            raise


# Global coordinator
_coordinator: Optional[AgentCoordinator] = None


def get_agent_coordinator() -> AgentCoordinator:
    """Get global agent coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator


def run_multi_agent_pipeline() -> dict:
    """Convenience function to run the full pipeline."""
    coordinator = get_agent_coordinator()
    return coordinator.run()
