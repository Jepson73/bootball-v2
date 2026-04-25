"""
Audit Exporter - export full audit trails from event store.

Provides deterministic export of system state for:
- debugging
- forensic audits  
- compliance reporting
- model evaluation
"""

import csv
import json
import logging
from datetime import datetime
from typing import Optional

from src.events.event_store import get_event_store
from src.state.reconstructor import StateReconstructor

logger = logging.getLogger(__name__)


class AuditExporter:
    """
    Export audit trails from event store.
    
    All exports are deterministic and derived ONLY from
    event store + snapshots.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        self.reconstructor = StateReconstructor()
    
    def export_run(self, run_id: str) -> dict:
        """
        Export full audit trail for a run.
        
        Args:
            run_id: The run ID to export
            
        Returns:
            Full audit dict with metadata, events, state, summary
        """
        events = self.event_store.get_events(run_id=run_id)
        
        if not events:
            return {"error": f"No events found for run_id: {run_id}"}
        
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        # Reconstruct final state
        system = self.reconstructor.rebuild_from_events(events)
        
        return {
            "metadata": {
                "run_id": run_id,
                "start": events[0].get("timestamp"),
                "end": events[-1].get("timestamp"),
                "event_count": len(events),
                "exported_at": datetime.utcnow().isoformat()
            },
            "events": events,
            "state_final": {
                "betting": self._serialize_betting(system.betting),
                "health": self._serialize_health(system.health),
                "model": self._serialize_model(system.model)
            },
            "summary": self._build_summary(system, events)
        }
    
    def export_date_range(
        self,
        start: datetime,
        end: datetime,
        model_version: Optional[str] = None,
        market: Optional[str] = None
    ) -> dict:
        """
        Export audit trail for a date range.
        
        Args:
            start: Start datetime
            end: End datetime
            model_version: Optional filter by model version
            market: Optional filter by market
            
        Returns:
            Full audit dict
        """
        events = self.event_store.get_events(since=start, until=end)
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        # Apply filters
        if model_version:
            events = [e for e in events if e.get("model_version") == model_version]
        
        if market:
            events = [
                e for e in events
                if any(b.get("market") == market for b in e.get("payload", {}).get("bets", []))
            ]
        
        if not events:
            return {"error": "No events found in date range"}
        
        # Reconstruct state
        system = self.reconstructor.rebuild_from_events(events)
        
        return {
            "metadata": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "event_count": len(events),
                "model_version": model_version,
                "market": market,
                "exported_at": datetime.utcnow().isoformat()
            },
            "events": events,
            "state_final": {
                "betting": self._serialize_betting(system.betting),
                "health": self._serialize_health(system.health),
                "model": self._serialize_model(system.model)
            },
            "summary": self._build_summary(system, events)
        }
    
    def export_model_version(self, model_version_id: str) -> dict:
        """
        Export audit for a specific model version.
        
        Args:
            model_version_id: The model version ID
            
        Returns:
            Full audit dict
        """
        # Get events for this model version
        all_events = self.event_store.get_all_events()
        
        # Filter to events related to this model
        events = [
            e for e in all_events
            if e.get("model_version") == model_version_id
            or e.get("payload", {}).get("model_version") == model_version_id
        ]
        
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        if not events:
            return {"error": f"No events found for model_version: {model_version_id}"}
        
        # Reconstruct state
        system = self.reconstructor.rebuild_from_events(events)
        
        return {
            "metadata": {
                "model_version": model_version_id,
                "start": events[0].get("timestamp"),
                "end": events[-1].get("timestamp"),
                "event_count": len(events),
                "exported_at": datetime.utcnow().isoformat()
            },
            "events": events,
            "state_final": {
                "betting": self._serialize_betting(system.betting),
                "health": self._serialize_health(system.health),
                "model": self._serialize_model(system.model)
            },
            "summary": self._build_summary(system, events)
        }
    
    def export_to_json(self, data: dict, path: str) -> None:
        """Export audit data to JSON file."""
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Exported audit to {path}")
    
    def export_to_csv(self, events: list[dict], path: str) -> None:
        """Export events to CSV file."""
        if not events:
            logger.warning("No events to export")
            return
        
        # Flatten events for CSV
        rows = []
        for event in events:
            payload = event.get("payload", event)
            row = {
                "timestamp": event.get("timestamp"),
                "event_type": event.get("event_type"),
                "run_id": event.get("run_id"),
            }
            
            # Add payload fields
            if event.get("event_type") == "bets_generated":
                bets = payload.get("bets", [])
                row["bet_count"] = len(bets)
                row["total_ev"] = sum(b.get("ev", 0) for b in bets)
                row["total_stake"] = sum(b.get("stake", 0) for b in bets)
            elif event.get("event_type") in ["bet_settled", "bets_settled"]:
                row["settled_count"] = payload.get("settled_count", 0)
                row["pnl_total"] = payload.get("pnl_total", 0)
                row["wins"] = payload.get("wins", 0)
                row["losses"] = payload.get("losses", 0)
            elif event.get("event_type") == "run_finished":
                row["total_bets"] = payload.get("total_bets", 0)
                row["total_ev"] = payload.get("total_ev", 0)
                row["duration"] = payload.get("duration", 0)
                row["errors"] = len(payload.get("errors", []))
            
            rows.append(row)
        
        # Write CSV
        if rows:
            fieldnames = list(rows[0].keys())
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        
        logger.info(f"Exported {len(rows)} events to {path}")
    
    def _build_summary(self, system, events: list[dict]) -> dict:
        """Build summary metrics."""
        return {
            "betting": {
                "roi": system.betting.roi,
                "balance": system.betting.balance,
                "total_pnl": system.betting.total_pnl,
                "wins": system.betting.wins,
                "losses": system.betting.losses,
                "pending_count": system.betting.pending_count
            },
            "health": {
                "health_score": system.health.health_score,
                "error_rate": system.health.error_rate,
                "avg_duration": system.health.avg_duration,
                "total_runs": system.health.total_runs
            },
            "model": {
                "markets_tracked": len(system.model.market_performance)
            }
        }
    
    def _serialize_betting(self, state) -> dict:
        return {
            "balance": state.balance,
            "roi": state.roi,
            "pending_count": state.pending_count,
            "wins": state.wins,
            "losses": state.losses,
            "total_pnl": state.total_pnl,
            "pending_stake": state.pending_stake
        }
    
    def _serialize_health(self, state) -> dict:
        return {
            "health_score": state.health_score,
            "error_rate": state.error_rate,
            "avg_duration": state.avg_duration,
            "total_runs": state.total_runs,
            "failed_runs": state.failed_runs
        }
    
    def _serialize_model(self, state) -> dict:
        return {
            "markets_tracked": list(state.market_performance.keys()),
            "active_versions": state.active_versions
        }


def export_run(run_id: str, output: str = "json", path: Optional[str] = None) -> dict:
    """Convenience function to export a run."""
    exporter = AuditExporter()
    data = exporter.export_run(run_id)
    
    if "error" in data:
        return data
    
    # Export to file
    if path:
        if output == "json":
            exporter.export_to_json(data, path)
        elif output == "csv":
            exporter.export_to_csv(data["events"], path)
    
    return data


def export_date_range(
    start: datetime,
    end: datetime,
    output: str = "json",
    path: Optional[str] = None
) -> dict:
    """Convenience function to export date range."""
    exporter = AuditExporter()
    data = exporter.export_date_range(start, end)
    
    if "error" in data:
        return data
    
    if path:
        if output == "json":
            exporter.export_to_json(data, path)
        elif output == "csv":
            exporter.export_to_csv(data["events"], path)
    
    return data
