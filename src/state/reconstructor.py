"""
State Reconstructor - rebuild system state from events.

This is the CORE of the replay system:
- Consumes ordered event log
- Reconstructs deterministic state
- No dependency on live pipeline state
"""

import logging
from datetime import datetime
from typing import Any, Optional

from src.state.models import BettingState, HealthState, ModelState, SystemState
from src.events.event_store import get_event_store

logger = logging.getLogger(__name__)


class StateReconstructor:
    """
    Reconstructs system state from events.
    
    Applies events in order to build deterministic state.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
    
    def rebuild_from_events(
        self,
        events: Optional[list[dict]] = None,
        since: Optional[datetime] = None,
        run_id: Optional[str] = None
    ) -> SystemState:
        """
        Rebuild complete system state from events.
        
        Args:
            events: Pre-provided events (optional)
            since: Only events after this time
            run_id: Only events for this run
            
        Returns:
            SystemState with all reconstructions
        """
        if events is None:
            events = self.event_store.get_events(since=since, run_id=run_id)
        
        # Sort by timestamp
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        # Initialize states
        system = SystemState()
        
        # Apply each event
        for event in events:
            self.apply_event(system, event)
            system.events_processed += 1
        
        # Calculate derived metrics
        self._calculate_derived_metrics(system)
        
        logger.info(f"Reconstructed state from {len(events)} events")
        return system
    
    def rebuild_incremental(
        self,
        events: Optional[list[dict]] = None,
        snapshot=None
    ) -> SystemState:
        """
        Rebuild state incrementally from snapshot + new events.
        
        This is the PERFORMANCE OPTIMIZED path:
        - If snapshot exists: resume from snapshot position
        - If no snapshot: full replay from scratch
        
        Args:
            events: Events to apply (optional, loads from store if not provided)
            snapshot: Optional starting snapshot
            
        Returns:
            SystemState with reconstructed values
        """
        from src.state.snapshot_store import get_snapshot_store
        
        store = get_snapshot_store()
        
        # Load snapshot if not provided
        if snapshot is None:
            snapshot = store.get_latest_snapshot()
        
        # Determine starting position
        start_id = 0
        if snapshot:
            start_id = snapshot.last_event_id
            logger.info(f"Resuming from snapshot {snapshot.id} (event {start_id})")
        
        # Load events if not provided
        if events is None:
            events = self.event_store.get_all_events()
        
        # Filter to only new events
        if start_id > 0:
            events = events[start_id:]
        
        logger.info(f"Applying {len(events)} new events (skipped {start_id})")
        
        # Initialize system from snapshot if available
        if snapshot:
            system = self._system_from_snapshot(snapshot)
        else:
            system = SystemState()
        
        # Apply events
        for event in events:
            self.apply_event(system, event)
            system.events_processed += 1
        
        # Calculate derived metrics
        self._calculate_derived_metrics(system)
        
        logger.info(f"Incremental reconstruction complete: {len(events)} events applied")
        return system
    
    def _system_from_snapshot(self, snapshot) -> SystemState:
        """Reconstruct SystemState from snapshot data."""
        from src.state.models import BettingState, HealthState, ModelState
        
        # Rebuild betting state
        bs = snapshot.betting_state
        betting = BettingState(
            balance=bs.get("balance", 0),
            roi=bs.get("roi", 0),
            pending_count=bs.get("pending_count", 0),
            wins=bs.get("wins", 0),
            losses=bs.get("losses", 0),
            pending_stake=bs.get("pending_stake", 0),
            total_pnl=bs.get("total_pnl", 0),
            bets=bs.get("bets", []),
            rounds=bs.get("rounds", []),
        )
        
        # Rebuild health state
        hs = snapshot.health_state
        health = HealthState(
            active_runs=hs.get("active_runs", []),
            completed_runs=hs.get("completed_runs", []),
            health_score=hs.get("health_score", 100),
            error_rate=hs.get("error_rate", 0),
            avg_duration=hs.get("avg_duration", 0),
            total_runs=hs.get("total_runs", 0),
            failed_runs=hs.get("failed_runs", 0),
        )
        
        # Rebuild model state
        ms = snapshot.model_state
        model = ModelState(
            model_versions=ms.get("model_versions", []),
            market_performance=ms.get("market_performance", {}),
            calibration_drift=ms.get("calibration_drift", {}),
            roi_by_model=ms.get("roi_by_model", {}),
            active_versions=ms.get("active_versions", []),
            retrain_signals=ms.get("retrain_signals", []),
        )
        
        return SystemState(betting=betting, health=health, model=model)
    
    def apply_event(self, system: SystemState, event: dict) -> None:
        """
        Apply a single event to system state.
        
        Args:
            system: SystemState to update
            event: Event dict
        """
        event_type = event.get("event_type")
        payload = event.get("payload", event)
        
        # Remove event_type/payload wrapper if present
        if "payload" in event:
            payload = event["payload"]
        
        handlers = {
            "bets_generated": self._handle_bets_generated,
            "bets_settled": self._handle_bets_settled,
            "bet_settled": self._handle_bets_settled,
            "run_started": self._handle_run_started,
            "run_finished": self._handle_run_finished,
            "health_update": self._handle_health_update,
            "model_trend": self._handle_model_trend,
            "predictions_generated": self._handle_predictions_generated,
        }
        
        handler = handlers.get(event_type)
        if handler:
            handler(system, payload)
        else:
            logger.debug(f"No handler for event type: {event_type}")
    
    def _handle_bets_generated(self, system: SystemState, payload: dict) -> None:
        """Handle bets_generated - add pending bets."""
        bets = payload.get("bets", [])
        run_id = payload.get("run_id")
        
        for bet in bets:
            system.betting.bets.append({
                "run_id": run_id,
                "fixture_id": bet.get("fixture_id"),
                "market": bet.get("market"),
                "outcome": bet.get("outcome"),
                "odds": bet.get("odds"),
                "ev": bet.get("ev"),
                "stake": bet.get("stake"),
                "timestamp": bet.get("timestamp"),
                "settled": False,
                "won": None,
                "pnl": None,
            })
            system.betting.pending_count += 1
            system.betting.pending_stake += bet.get("stake", 0)
    
    def _handle_bets_settled(self, system: SystemState, payload: dict) -> None:
        """Handle bets_settled - settle pending bets."""
        settled_count = payload.get("settled_count", 0)
        pnl_total = payload.get("pnl_total", 0)
        wins = payload.get("wins", 0)
        losses = payload.get("losses", 0)
        
        # Mark all pending bets as settled
        for bet in system.betting.bets:
            if not bet.get("settled"):
                bet["settled"] = True
                bet["won"] = pnl_total > 0
                bet["pnl"] = pnl_total / settled_count if settled_count > 0 else 0
        
        system.betting.wins += wins
        system.betting.losses += losses
        system.betting.total_pnl += pnl_total
        system.betting.pending_count = max(0, system.betting.pending_count - settled_count)
        system.betting.pending_stake = 0  # Reset after settlement
        
        # Recalculate balance
        system.betting.balance = (
            system.betting.initial_bankroll 
            + system.betting.total_pnl 
            - system.betting.pending_stake
        )
    
    def _handle_run_started(self, system: HealthState, payload: dict) -> None:
        """Handle run_started - register active run."""
        run_id = payload.get("run_id")
        mode = payload.get("mode", "unknown")
        
        system.health.active_runs.append({
            "run_id": run_id,
            "mode": mode,
            "started_at": payload.get("timestamp"),
        })
    
    def _handle_run_finished(self, system: SystemState, payload: dict) -> None:
        """Handle run_finished - update health and model."""
        run_id = payload.get("run_id")
        mode = payload.get("mode", "unknown")
        duration = payload.get("duration", 0)
        errors = payload.get("errors", [])
        
        # Remove from active runs
        system.health.active_runs = [
            r for r in system.health.active_runs 
            if r.get("run_id") != run_id
        ]
        
        # Add to completed runs
        system.health.completed_runs.append({
            "run_id": run_id,
            "mode": mode,
            "duration": duration,
            "errors": len(errors),
            "finished_at": payload.get("timestamp"),
        })
        
        # Keep only last 100
        system.health.completed_runs = system.health.completed_runs[-100:]
        
        # Update totals
        system.health.total_runs += 1
        if errors:
            system.health.failed_runs += 1
    
    def _handle_health_update(self, system: HealthState, payload: dict) -> None:
        """Handle health_update - direct health score update."""
        if "health_score" in payload:
            system.health.health_score = payload["health_score"]
        if "error_rate" in payload:
            system.health.error_rate = payload["error_rate"]
    
    def _handle_model_trend(self, system: ModelState, payload: dict) -> None:
        """Handle model_trend - update model metrics."""
        market = payload.get("market", "unknown")
        version = payload.get("model_version", "unknown")
        
        if market not in system.model.market_performance:
            system.model.market_performance[market] = []
        
        system.model.market_performance[market].append({
            "version": version,
            "brier_score": payload.get("brier_score"),
            "ece": payload.get("ece"),
            "accuracy": payload.get("accuracy"),
            "timestamp": payload.get("timestamp"),
        })
        
        # Track calibration drift
        if market not in system.model.calibration_drift:
            system.model.calibration_drift[market] = []
        
        ece = payload.get("ece")
        if ece is not None:
            system.model.calibration_drift[market].append({
                "ece": ece,
                "timestamp": payload.get("timestamp"),
            })
    
    def _handle_predictions_generated(self, system: SystemState, payload: dict) -> None:
        """Handle predictions_generated - track prediction count."""
        # Predictions don't affect core state, just log
        logger.debug(f"Predictions generated: {payload.get('prediction_count', 0)}")
    
    def _calculate_derived_metrics(self, system: SystemState) -> None:
        """Calculate derived metrics after applying all events."""
        # Betting ROI
        if system.betting.initial_bankroll > 0:
            system.betting.roi = (
                system.betting.total_pnl / system.betting.initial_bankroll * 100
            )
        
        # Health metrics
        total = len(system.health.completed_runs)
        if total > 0:
            errors = sum(r.get("errors", 0) for r in system.health.completed_runs)
            system.health.error_rate = errors / total
            
            durations = [r.get("duration", 0) for r in system.health.completed_runs]
            system.health.avg_duration = sum(durations) / total
            
            system.health.health_score = max(0, 100 - (system.health.error_rate * 50))
        
        # Last updated
        system.last_updated = datetime.utcnow()


def rebuild_betting_state(events: Optional[list[dict]] = None) -> BettingState:
    """Convenience function to rebuild just betting state."""
    system = StateReconstructor().rebuild_from_events(events)
    return system.betting


def rebuild_health_state(events: Optional[list[dict]] = None) -> HealthState:
    """Convenience function to rebuild just health state."""
    system = StateReconstructor().rebuild_from_events(events)
    return system.health


def rebuild_model_state(events: Optional[list[dict]] = None) -> ModelState:
    """Convenience function to rebuild just model state."""
    system = StateReconstructor().rebuild_from_events(events)
    return system.model
