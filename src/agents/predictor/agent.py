"""
Predictor Agent - generates predictions and EV signals.

Responsibility:
- Generate probabilities per fixture/market
- Compute EV signals
- Emit predictions ONLY

Hard rules:
- No stake sizing
- No risk logic
- No portfolio logic
"""

import logging
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events
from src.agents.shared.events import AgentEvents, PREDICTION_PAYLOAD
from src.agents.shared.state_store import get_state_store

logger = logging.getLogger(__name__)


class PredictorAgent:
    """
    Agent responsible for generating predictions and EV signals.
    
    Flow:
    1. Fetch upcoming fixtures
    2. Run model predictions
    3. Calculate EV vs bookmaker odds
    4. Emit PREDICTIONS_READY event
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self._event_bus = event_bus
        self._event_bus.subscribe(AgentEvents.RUN_STARTED, self.handle_run_started)
        logger.info("[PREDICTOR] Agent initialized")
    
    def handle_run_started(self, payload: dict) -> None:
        """Handle run started event."""
        self.state_store.start_run(payload.get("run_id", ""))
    
    def run(self) -> list[dict]:
        """
        Run prediction pipeline.
        
        Returns:
            List of prediction dicts
        """
        logger.info("[PREDICTOR] Running prediction pipeline")
        
        # Step 1: Fetch upcoming fixtures with odds
        predictions = self._generate_predictions()
        
        # Step 2: Record predictions
        self.state_store.increment_predictions(len(predictions))
        
        # Step 3: Emit predictions ready event
        self._emit_predictions(predictions)
        
        logger.info(f"[PREDICTOR] Generated {len(predictions)} signals")
        return predictions
    
    def _generate_predictions(self) -> list[dict]:
        """Generate predictions for upcoming fixtures."""
        from src.storage.models import PredictionRecord, Fixture
        from src.storage.connection import get_session
        from sqlalchemy import select, func
        from src.betting.prediction import get_model_prediction
        
        predictions = []
        
        with get_session() as s:
            # Get upcoming fixtures with odds
            upcoming = s.execute(
                select(Fixture)
                .where(Fixture.status == 'NS')
                .where(Fixture.date >= datetime.utcnow())
                .limit(50)
            ).scalars().all()
            
            for fixture in upcoming:
                # Get existing prediction records for this fixture
                existing = s.execute(
                    select(PredictionRecord)
                    .where(PredictionRecord.fixture_id == fixture.id)
                ).scalars().all()
                
                for pred in existing:
                    if pred.ev and pred.ev > 0.02:  # Only positive EV
                        predictions.append({
                            "fixture_id": fixture.id,
                            "market": pred.market,
                            "outcome": pred.predicted_outcome,
                            "probabilities": {
                                "home": pred.our_prob,
                                "draw": 0.0,
                                "away": 1.0 - pred.our_prob
                            },
                            "ev": pred.ev,
                            "kelly": pred.kelly_fraction or 0.0,
                            "odds": pred.odds_decimal or 0.0,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
        
        return predictions
    
    def _emit_predictions(self, predictions: list[dict]) -> None:
        """Emit predictions ready event."""
        payload = {
            "predictions": predictions,
            "count": len(predictions),
            "timestamp": datetime.utcnow().isoformat(),
            "avg_ev": sum(p["ev"] for p in predictions) / len(predictions) if predictions else 0.0,
        }
        
        self._event_bus.emit(AgentEvents.PREDICTIONS_READY, payload)
        logger.info(f"[PREDICTOR] Emitted PREDICTIONS_READY with {len(predictions)} predictions")


# Global instance
_agent: Optional[PredictorAgent] = None


def get_predictor_agent() -> PredictorAgent:
    """Get global predictor agent."""
    global _agent
    if _agent is None:
        _agent = PredictorAgent()
    return _agent
