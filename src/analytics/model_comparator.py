"""
Model Comparator - compare performance between model versions.

Enables A/B testing of model versions using event history.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from src.events.event_store import get_event_store
from src.analytics.model_evaluator import ModelEvaluator
from src.analytics.market_analysis import MarketAnalyzer

logger = logging.getLogger(__name__)


class ModelComparator:
    """
    Compares model versions using event history.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        self.evaluator = ModelEvaluator()
        self.market_analyzer = MarketAnalyzer()
    
    def compare_models(
        self,
        model_a: str,
        model_b: str,
        events: Optional[list[dict]] = None
    ) -> dict:
        """
        Compare two model versions.
        
        Args:
            model_a: First model version ID
            model_b: Second model version ID  
            events: Pre-provided events
            
        Returns:
            Dict with comparison metrics
        """
        # Load events if not provided
        if events is None:
            events = self.event_store.get_events(
                event_types=["bets_generated", "bet_settled", "bets_settled"]
            )
        
        # Filter events by model version
        events_a = [e for e in events if e.get("model_version") == model_a]
        events_b = [e for e in events if e.get("model_version") == model_b]
        
        # Evaluate each
        metrics_a = self.evaluator.evaluate_model(events=events_a)
        metrics_b = self.evaluator.evaluate_model(events=events_b)
        
        # Compute deltas
        return self._compute_comparison(model_a, model_b, metrics_a, metrics_b)
    
    def _compute_comparison(
        self,
        model_a: str,
        model_b: str,
        metrics_a: dict,
        metrics_b: dict
    ) -> dict:
        """Compute comparison deltas."""
        
        # ROI delta
        roi_delta = metrics_b["roi"] - metrics_a["roi"]
        
        # Win rate delta
        win_rate_delta = metrics_b["win_rate"] - metrics_a["win_rate"]
        
        # PnL delta
        pnl_delta = metrics_b["total_pnl"] - metrics_a["total_pnl"]
        
        # Market breakdown comparison
        market_comparison = {}
        all_markets = set(metrics_a.get("market_breakdown", {}).keys())
        all_markets.update(metrics_b.get("market_breakdown", {}).keys())
        
        for market in all_markets:
            a_market = metrics_a.get("market_breakdown", {}).get(market, {})
            b_market = metrics_b.get("market_breakdown", {}).get(market, {})
            
            market_comparison[market] = {
                "model_a_roi": a_market.get("roi", 0),
                "model_b_roi": b_market.get("roi", 0),
                "delta": b_market.get("roi", 0) - a_market.get("roi", 0),
            }
        
        # Determine winner
        if roi_delta > 0:
            winner = model_b
            winner_reason = f"+{roi_delta:.2f}% higher ROI"
        elif roi_delta < 0:
            winner = model_a
            winner_reason = f"+{-roi_delta:.2f}% higher ROI"
        else:
            winner = "tie"
            winner_reason = "Equal ROI"
        
        return {
            "model_a": {
                "version": model_a,
                "roi": metrics_a["roi"],
                "win_rate": metrics_a["win_rate"],
                "total_pnl": metrics_a["total_pnl"],
                "total_bets": metrics_a["total_bets"],
            },
            "model_b": {
                "version": model_b,
                "roi": metrics_b["roi"],
                "win_rate": metrics_b["win_rate"],
                "total_pnl": metrics_b["total_pnl"],
                "total_bets": metrics_b["total_bets"],
            },
            "comparison": {
                "roi_delta": roi_delta,
                "win_rate_delta": win_rate_delta,
                "pnl_delta": pnl_delta,
                "bet_count_delta": metrics_b["total_bets"] - metrics_a["total_bets"],
            },
            "market_comparison": market_comparison,
            "winner": winner,
            "winner_reason": winner_reason,
        }
    
    def compare_by_time_period(
        self,
        period_a_start: datetime,
        period_a_end: datetime,
        period_b_start: datetime,
        period_b_end: datetime
    ) -> dict:
        """
        Compare model performance across two time periods.
        
        Useful for: before/after retraining analysis
        """
        # Get events for each period
        events_a = self.event_store.get_events(since=period_a_start, until=period_a_end)
        events_b = self.event_store.get_events(since=period_b_start, until=period_b_end)
        
        metrics_a = self.evaluator.evaluate_model(events=events_a)
        metrics_b = self.evaluator.evaluate_model(events=events_b)
        
        return self._compute_comparison(
            f"period_{period_a_start.date()}",
            f"period_{period_b_start.date()}",
            metrics_a,
            metrics_b
        )
    
    def find_optimal_model(
        self,
        events: Optional[list[dict]] = None
    ) -> dict:
        """
        Find which model version had best performance.
        
        Returns:
            Dict with best model recommendation
        """
        if events is None:
            events = self.event_store.get_events(
                event_types=["bets_generated", "model_trend"]
            )
        
        # Extract model versions from events
        versions = set()
        for event in events:
            if event.get("event_type") == "model_trend":
                payload = event.get("payload", event)
                version = payload.get("model_version")
                if version:
                    versions.add(version)
        
        if not versions:
            return {"error": "No model versions found in events"}
        
        # Compare each version
        best_version = None
        best_roi = float("-inf")
        best_metrics = None
        
        for version in versions:
            version_events = [e for e in events if e.get("model_version") == version]
            if version_events:
                metrics = self.evaluator.evaluate_model(events=version_events)
                if metrics["roi"] > best_roi:
                    best_roi = metrics["roi"]
                    best_version = version
                    best_metrics = metrics
        
        return {
            "recommended_version": best_version,
            "expected_roi": best_roi,
            "metrics": best_metrics,
            "candidates": list(versions),
        }


# Convenience functions
def compare_model_versions(model_a: str, model_b: str) -> dict:
    """Compare two model versions."""
    comparator = ModelComparator()
    return comparator.compare_models(model_a, model_b)


def find_best_model() -> dict:
    """Find the best performing model version."""
    comparator = ModelComparator()
    return comparator.find_optimal_model()
