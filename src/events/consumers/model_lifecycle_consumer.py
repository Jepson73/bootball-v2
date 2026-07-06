"""
Model Lifecycle Consumer - handles retraining events and user feedback.

Emits:
- Discord notifications
- Dashboard updates
- Status broadcasts
"""

import logging
from datetime import datetime
from typing import Optional

from src.events.event_bus import Events, event_bus
from src.events.event_store import get_event_store
from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class ModelLifecycleConsumer(EventConsumer):
    """
    Consumes model lifecycle events and emits user-facing notifications.
    
    Handles:
    - retraining_started
    - retraining_progress
    - retraining_completed
    - model_version_promoted
    - model_version_rejected
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        
        # Register handlers
        event_bus.subscribe(Events.MODEL_TREND, self.on_model_trend)
        
        logger.info("ModelLifecycleConsumer initialized")
    
    def handles(self, event_type: str) -> bool:
        """Handle model trend events."""
        return event_type == Events.MODEL_TREND
    
    def process(self, event: dict) -> None:
        """Process model trend event (batch interface)."""
        self.on_model_trend(event)
    
    def on_model_trend(self, event) -> None:
        """
        Handle model lifecycle events.
        
        Args:
            event: Model trend event (dict or event object)
        """
        # Handle both dict and event object
        if hasattr(event, 'data'):
            data = event.data
        else:
            data = event
        
        status = data.get("status")
        market = data.get("market", "unknown")
        
        # Store in event store for audit
        try:
            self.event_store.append(data)
        except Exception as e:
            logger.debug(f"Could not store event: {e}")
        
        # Route to specific handler
        if status == "started":
            self._handle_started(data)
        elif status == "progress":
            self._handle_progress(data)
        elif status == "completed":
            self._handle_completed(data)
        elif status == "promoted":
            self._handle_promoted(data)
        elif status == "failed":
            self._handle_failed(data)
    
    def _handle_started(self, data: dict) -> None:
        """Handle retraining started."""
        job_id = data.get("job_id")
        market = data.get("market")
        reasons = data.get("reason", [])
        
        message = f"📊 Model Retraining Started\n"
        message += f"   Market: {market}\n"
        message += f"   Job: {job_id}\n"
        if reasons:
            message += f"   Reason: {', '.join(reasons)}"
        
        logger.info(message)
        
        # Emit for Discord (will be consumed by DiscordConsumer)
        event_bus.emit(Events.NOTIFICATION_DISCORD, {
            "title": "Model Retraining Started",
            "description": message,
            "severity": "info",
        })
        
        # Emit for dashboard
        event_bus.emit(Events.STATE_CHANGED, {
            "type": "model_retrain",
            "subtype": "started",
            "market": market,
            "job_id": job_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def _handle_progress(self, data: dict) -> None:
        """Handle retraining progress."""
        progress = data.get("progress", 0)
        market = data.get("market")
        
        message = f"📈 Progress: {progress}%\n   Market: {market}"
        
        logger.debug(message)
        
        # Emit for dashboard only (too noisy for Discord)
        event_bus.emit(Events.STATE_CHANGED, {
            "type": "model_retrain",
            "subtype": "progress",
            "market": market,
            "progress": progress,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def _handle_completed(self, data: dict) -> None:
        """Handle retraining completed."""
        job_id = data.get("job_id")
        market = data.get("market")
        new_version = data.get("new_version")
        metrics = data.get("metrics", {})
        promoted = data.get("promoted", False)
        
        if promoted:
            message = f"✅ Model {new_version} promoted\n"
            message += f"   Market: {market}\n"
            message += f"   ROI change: {metrics.get('roi_delta', 'N/A')}\n"
            message += f"   Brier score: {metrics.get('brier_score', 'N/A')}"
        else:
            message = f"⚠️ Model retraining completed but NOT promoted\n"
            message += f"   Market: {market}\n"
            message += f"   Reason: No improvement over current version"
        
        logger.info(message)
        
        # Emit for Discord
        severity = "success" if promoted else "warning"
        event_bus.emit(Events.NOTIFICATION_DISCORD, {
            "title": "Model Retraining Completed",
            "description": message,
            "severity": severity,
        })
        
        # Emit for dashboard
        event_bus.emit(Events.STATE_CHANGED, {
            "type": "model_retrain",
            "subtype": "completed",
            "market": market,
            "job_id": job_id,
            "new_version": new_version,
            "promoted": promoted,
            "metrics": metrics,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def _handle_promoted(self, data: dict) -> None:
        """Handle version promotion."""
        version_id = data.get("version_id")
        market = data.get("market")
        
        message = f"🚀 Model {version_id} promoted to ACTIVE\n   Market: {market}"
        
        logger.info(message)
        
        event_bus.emit(Events.NOTIFICATION_DISCORD, {
            "title": "Model Version Promoted",
            "description": message,
            "severity": "success",
        })
        
        event_bus.emit(Events.STATE_CHANGED, {
            "type": "model_promoted",
            "market": market,
            "version_id": version_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    def _handle_failed(self, data: dict) -> None:
        """Handle retraining failure."""
        job_id = data.get("job_id")
        market = data.get("market")
        error = data.get("error", "Unknown error")
        
        message = f"❌ Model retraining FAILED\n"
        message += f"   Market: {market}\n"
        message += f"   Job: {job_id}\n"
        message += f"   Error: {error}"
        
        logger.error(message)
        
        event_bus.emit(Events.NOTIFICATION_DISCORD, {
            "title": "Model Retraining Failed",
            "description": message,
            "severity": "error",
        })
        
        event_bus.emit(Events.STATE_CHANGED, {
            "type": "model_retrain",
            "subtype": "failed",
            "market": market,
            "job_id": job_id,
            "error": error,
            "timestamp": datetime.utcnow().isoformat(),
        })


# Global instance
_lifecycle_consumer: Optional[ModelLifecycleConsumer] = None


def get_model_lifecycle_consumer() -> ModelLifecycleConsumer:
    """Get global lifecycle consumer."""
    global _lifecycle_consumer
    if _lifecycle_consumer is None:
        _lifecycle_consumer = ModelLifecycleConsumer()
    return _lifecycle_consumer
