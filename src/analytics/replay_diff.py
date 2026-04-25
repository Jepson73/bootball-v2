"""
Replay Diff Tool - compare two runs or time periods.

Provides:
- ROI delta
- bet count difference
- model decision divergence
- divergence point identification
"""

import logging
from datetime import datetime
from typing import Optional

from src.events.event_store import get_event_store
from src.state.reconstructor import StateReconstructor
from src.analytics.audit_exporter import AuditExporter

logger = logging.getLogger(__name__)


class ReplayDiffer:
    """
    Compare replay results from different runs or time periods.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        self.reconstructor = StateReconstructor()
        self.exporter = AuditExporter()
    
    def compare_runs(self, run_a: str, run_b: str) -> dict:
        """
        Compare two runs.
        
        Args:
            run_a: First run ID
            run_b: Second run ID
            
        Returns:
            Comparison dict with deltas and divergence points
        """
        # Export both runs
        data_a = self.exporter.export_run(run_a)
        data_b = self.exporter.export_run(run_b)
        
        if "error" in data_a:
            return {"error": f"Run A not found: {data_a['error']}"}
        if "error" in data_b:
            return {"error": f"Run B not found: {data_b['error']}"}
        
        # Compare betting
        betting_a = data_a["summary"]["betting"]
        betting_b = data_b["summary"]["betting"]
        
        betting_delta = {
            "roi": betting_b["roi"] - betting_a["roi"],
            "balance": betting_b["balance"] - betting_a["balance"],
            "total_pnl": betting_b["total_pnl"] - betting_a["total_pnl"],
            "wins": betting_b["wins"] - betting_a["wins"],
            "losses": betting_b["losses"] - betting_a["losses"],
            "pending_count": betting_b["pending_count"] - betting_a["pending_count"]
        }
        
        # Compare health
        health_a = data_a["summary"]["health"]
        health_b = data_b["summary"]["health"]
        
        health_delta = {
            "health_score": health_b["health_score"] - health_a["health_score"],
            "error_rate": health_b["error_rate"] - health_a["error_rate"],
            "avg_duration": health_b["avg_duration"] - health_a["avg_duration"],
            "total_runs": health_b["total_runs"] - health_a["total_runs"]
        }
        
        # Find divergence point
        divergence = self._find_divergence_point(
            data_a["events"],
            data_b["events"]
        )
        
        # Determine winner
        if betting_delta["total_pnl"] > 0:
            winner = run_b
            reason = f"+{betting_delta['total_pnl']:.2f} higher PnL"
        elif betting_delta["total_pnl"] < 0:
            winner = run_a
            reason = f"+{-betting_delta['total_pnl']:.2f} higher PnL"
        else:
            winner = "tie"
            reason = "Equal PnL"
        
        return {
            "run_a": {
                "run_id": run_a,
                "event_count": data_a["metadata"]["event_count"],
                "betting": betting_a,
                "health": health_a
            },
            "run_b": {
                "run_id": run_b,
                "event_count": data_b["metadata"]["event_count"],
                "betting": betting_b,
                "health": health_b
            },
            "deltas": {
                "betting": betting_delta,
                "health": health_delta
            },
            "divergence": divergence,
            "winner": winner,
            "reason": reason
        }
    
    def compare_date_ranges(
        self,
        start_a: datetime,
        end_a: datetime,
        start_b: datetime,
        end_b: datetime
    ) -> dict:
        """
        Compare two date ranges.
        
        Args:
            start_a, end_a: First period
            start_b, end_b: Second period
            
        Returns:
            Comparison dict
        """
        data_a = self.exporter.export_date_range(start_a, end_a)
        data_b = self.exporter.export_date_range(start_b, end_b)
        
        if "error" in data_a:
            return {"error": f"Period A not found: {data_a['error']}"}
        if "error" in data_b:
            return {"error": f"Period B not found: {data_b['error']}"}
        
        # Same comparison logic
        betting_a = data_a["summary"]["betting"]
        betting_b = data_b["summary"]["betting"]
        
        return {
            "period_a": {
                "start": start_a.isoformat(),
                "end": end_a.isoformat(),
                "betting": betting_a
            },
            "period_b": {
                "start": start_b.isoformat(),
                "end": end_b.isoformat(),
                "betting": betting_b
            },
            "deltas": {
                "roi": betting_b["roi"] - betting_a["roi"],
                "balance": betting_b["balance"] - betting_a["balance"],
                "total_pnl": betting_b["total_pnl"] - betting_a["total_pnl"]
            }
        }
    
    def compare_model_versions(self, model_a: str, model_b: str) -> dict:
        """
        Compare two model versions.
        
        Args:
            model_a: First model version ID
            model_b: Second model version ID
            
        Returns:
            Comparison dict
        """
        data_a = self.exporter.export_model_version(model_a)
        data_b = self.exporter.export_model_version(model_b)
        
        if "error" in data_a:
            return {"error": f"Model A not found: {data_a['error']}"}
        if "error" in data_b:
            return {"error": f"Model B not found: {data_b['error']}"}
        
        betting_a = data_a["summary"]["betting"]
        betting_b = data_b["summary"]["betting"]
        
        # Model comparison
        return {
            "model_a": {
                "version": model_a,
                "event_count": data_a["metadata"]["event_count"],
                "betting": betting_a
            },
            "model_b": {
                "version": model_b,
                "event_count": data_b["metadata"]["event_count"],
                "betting": betting_b
            },
            "deltas": {
                "roi": betting_b["roi"] - betting_a["roi"],
                "total_pnl": betting_b["total_pnl"] - betting_a["total_pnl"],
                "wins": betting_b["wins"] - betting_a["wins"],
                "losses": betting_b["losses"] - betting_a["losses"]
            },
            "winner": model_b if betting_b["roi"] > betting_a["roi"] else model_a,
            "win_margin": abs(betting_b["roi"] - betting_a["roi"])
        }
    
    def _find_divergence_point(
        self,
        events_a: list[dict],
        events_b: list[dict]
    ) -> Optional[dict]:
        """
        Find the first event where behavior diverges.
        
        Args:
            events_a: Events from run A
            events_b: Events from run B
            
        Returns:
            Divergence info or None
        """
        # Build event sequences
        seq_a = {i: e.get("event_type") for i, e in enumerate(events_a)}
        seq_b = {i: e.get("event_type") for i, e in enumerate(events_b)}
        
        # Find first divergence
        for i in range(max(len(seq_a), len(seq_b))):
            type_a = seq_a.get(i)
            type_b = seq_b.get(i)
            
            if type_a != type_b:
                return {
                    "event_index": i,
                    "run_a_event": type_a,
                    "run_b_event": type_b,
                    "timestamp_a": events_a[i].get("timestamp") if i < len(events_a) else None,
                    "timestamp_b": events_b[i].get("timestamp") if i < len(events_b) else None
                }
        
        return None


def compare_runs(run_a: str, run_b: str) -> dict:
    """Convenience function to compare two runs."""
    differ = ReplayDiffer()
    return differ.compare_runs(run_a, run_b)


def compare_model_versions(model_a: str, model_b: str) -> dict:
    """Convenience function to compare model versions."""
    differ = ReplayDiffer()
    return differ.compare_model_versions(model_a, model_b)
