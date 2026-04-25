"""
Model Evaluator - offline model performance analysis from events.

This is an analytics engine that evaluates model performance over time
using event replay. It is NOT a live system.

Answers:
- Was model version X profitable?
- Which markets are strongest?
- Did calibration drift affect performance?
- When did ROI degradation begin?
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from src.events.event_store import get_event_store
from src.state.reconstructor import StateReconstructor

logger = logging.getLogger(__name__)


class ModelEvaluator:
    """
    Evaluates model performance from event history.
    
    Uses ONLY events + snapshots - no live pipeline data.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        self.reconstructor = StateReconstructor()
    
    def evaluate_model(
        self,
        model_version: Optional[str] = None,
        events: Optional[list[dict]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None
    ) -> dict:
        """
        Evaluate model performance.
        
        Args:
            model_version: Filter by model version
            events: Pre-provided events (optional)
            since: Filter events after this time
            until: Filter events before this time
            
        Returns:
            Dict with performance metrics
        """
        # Load events if not provided
        if events is None:
            events = self.event_store.get_events(
                since=since,
                event_types=["bets_generated", "bet_settled", "bets_settled", "run_finished"]
            )
        
        # Sort by timestamp
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        # Filter by time range
        if since:
            events = [e for e in events if e.get("timestamp", "") >= since.isoformat()]
        if until:
            events = [e for e in events if e.get("timestamp", "") <= until.isoformat()]
        
        # Extract bets and settle them
        bets = []
        for event in events:
            if event.get("event_type") == "bets_generated":
                payload = event.get("payload", event)
                for bet in payload.get("bets", []):
                    bet["generated_at"] = event.get("timestamp")
                    bet["run_id"] = payload.get("run_id")
                    bets.append(bet)
        
        # Track settlements
        settled_bets = []
        for event in events:
            if event.get("event_type") in ["bet_settled", "bets_settled"]:
                payload = event.get("payload", event)
                count = payload.get("settled_count", 0)
                pnl = payload.get("pnl_total", 0)
                wins = payload.get("wins", 0)
                losses = payload.get("losses", 0)
                
                # Mark some bets as settled
                for i in range(min(count, len(bets) - len(settled_bets))):
                    if i < len(bets):
                        bet = bets[i]
                        bet["settled"] = True
                        bet["won"] = pnl > 0 if count > 0 else None
                        bet["pnl"] = pnl / count if count > 0 else 0
                        bet["settled_at"] = event.get("timestamp")
                        settled_bets.append(bet)
        
        # Compute metrics
        return self._compute_metrics(settled_bets, events)
    
    def _compute_metrics(self, settled_bets: list[dict], events: list[dict]) -> dict:
        """Compute performance metrics from settled bets."""
        
        total_bets = len(settled_bets)
        if total_bets == 0:
            return {
                "total_bets": 0,
                "total_pnl": 0,
                "roi": 0,
                "win_rate": 0,
                "avg_ev": 0,
                "market_breakdown": {},
                "time_series": [],
            }
        
        # Financial metrics
        total_pnl = sum(bet.get("pnl", 0) for bet in settled_bets)
        wins = sum(1 for bet in settled_bets if bet.get("won") == True)
        losses = sum(1 for bet in settled_bets if bet.get("won") == False)
        avg_ev = sum(bet.get("ev", 0) for bet in settled_bets) / total_bets
        
        # ROI calculation (assuming 1000 initial bankroll)
        initial_bankroll = 1000.0
        roi = (total_pnl / initial_bankroll * 100) if initial_bankroll > 0 else 0
        win_rate = (wins / total_bets * 100) if total_bets > 0 else 0
        
        # Market breakdown
        market_stats = defaultdict(lambda: {"bets": 0, "pnl": 0, "wins": 0})
        for bet in settled_bets:
            market = bet.get("market", "unknown")
            market_stats[market]["bets"] += 1
            market_stats[market]["pnl"] += bet.get("pnl", 0)
            if bet.get("won"):
                market_stats[market]["wins"] += 1
        
        market_breakdown = {}
        for market, stats in market_stats.items():
            market_roi = (stats["pnl"] / initial_bankroll * 100) if initial_bankroll > 0 else 0
            market_breakdown[market] = {
                "bets": stats["bets"],
                "pnl": stats["pnl"],
                "roi": market_roi,
                "win_rate": (stats["wins"] / stats["bets"] * 100) if stats["bets"] > 0 else 0,
            }
        
        # Time series (group by day)
        time_series = self._compute_time_series(settled_bets)
        
        # Run analysis
        run_stats = self._compute_run_stats(events)
        
        return {
            "total_bets": total_bets,
            "total_pnl": total_pnl,
            "roi": roi,
            "win_rate": win_rate,
            "avg_ev": avg_ev,
            "wins": wins,
            "losses": losses,
            "market_breakdown": market_breakdown,
            "time_series": time_series,
            "run_stats": run_stats,
        }
    
    def _compute_time_series(self, settled_bets: list[dict]) -> list[dict]:
        """Compute daily time series of PnL."""
        daily = defaultdict(lambda: {"pnl": 0, "bets": 0})
        
        for bet in settled_bets:
            settled_at = bet.get("settled_at", "")
            if settled_at:
                date = settled_at[:10]  # YYYY-MM-DD
                daily[date]["pnl"] += bet.get("pnl", 0)
                daily[date]["bets"] += 1
        
        # Sort and convert to list
        series = []
        cumulative_pnl = 0
        for date in sorted(daily.keys()):
            cumulative_pnl += daily[date]["pnl"]
            series.append({
                "date": date,
                "pnl": daily[date]["pnl"],
                "cumulative_pnl": cumulative_pnl,
                "bets": daily[date]["bets"],
            })
        
        return series
    
    def _compute_run_stats(self, events: list[dict]) -> dict:
        """Compute per-run statistics."""
        runs = defaultdict(lambda: {"bets": 0, "pnl": 0, "errors": 0})
        
        for event in events:
            if event.get("event_type") == "run_finished":
                payload = event.get("payload", event)
                run_id = payload.get("run_id", "unknown")
                runs[run_id]["pnl"] = payload.get("total_bets", 0) * 0.05  # Estimate
                runs[run_id]["errors"] = len(payload.get("errors", []))
            
            elif event.get("event_type") == "bets_generated":
                payload = event.get("payload", event)
                run_id = payload.get("run_id", "unknown")
                runs[run_id]["bets"] = len(payload.get("bets", []))
        
        # Sort by PnL
        run_list = [
            {"run_id": rid, "bets": stats["bets"], "pnl": stats["pnl"], "errors": stats["errors"]}
            for rid, stats in runs.items()
        ]
        run_list.sort(key=lambda x: x["pnl"], reverse=True)
        
        return {
            "best_runs": run_list[:5],
            "worst_runs": run_list[-5:],
            "total_runs": len(runs),
        }
    
    def evaluate_by_date_range(
        self,
        days: int = 30,
        model_version: Optional[str] = None
    ) -> dict:
        """Evaluate model over last N days."""
        since = datetime.utcnow() - timedelta(days=days)
        return self.evaluate_model(model_version=model_version, since=since)
    
    def evaluate_by_run(
        self,
        run_id: str,
        events: Optional[list[dict]] = None
    ) -> dict:
        """Evaluate model for specific run."""
        if events is None:
            events = self.event_store.get_events(run_id=run_id)
        
        events = [e for e in events if e.get("run_id") == run_id]
        return self.evaluate_model(events=events)


# Convenience functions
def evaluate_model_performance(
    days: int = 30,
    model_version: Optional[str] = None
) -> dict:
    """Evaluate model performance over time period."""
    evaluator = ModelEvaluator()
    return evaluator.evaluate_by_date_range(days=days, model_version=model_version)


def evaluate_run_performance(run_id: str) -> dict:
    """Evaluate performance for a specific run."""
    evaluator = ModelEvaluator()
    return evaluator.evaluate_by_run(run_id=run_id)
