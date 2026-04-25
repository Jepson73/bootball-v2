"""
Market Analysis - profitability analysis per market.

Analyzes which markets (h2h, btts, ou25, etc.) are most profitable.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from src.events.event_store import get_event_store
from src.analytics.model_evaluator import ModelEvaluator

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    """
    Analyzes market profitability from events.
    """
    
    def __init__(self):
        self.event_store = get_event_store()
        self.evaluator = ModelEvaluator()
    
    def analyze_markets(
        self,
        events: Optional[list[dict]] = None,
        since: Optional[datetime] = None
    ) -> dict:
        """
        Analyze all markets.
        
        Args:
            events: Pre-provided events
            since: Filter events after this time
            
        Returns:
            Dict with market analysis
        """
        # Get all events if not provided
        if events is None:
            events = self.event_store.get_events(
                since=since,
                event_types=["bets_generated", "bet_settled", "bets_settled"]
            )
        
        # Extract bets and settlements
        all_bets = []
        
        for event in events:
            if event.get("event_type") == "bets_generated":
                payload = event.get("payload", event)
                for bet in payload.get("bets", []):
                    bet["run_id"] = payload.get("run_id")
                    bet["generated_at"] = event.get("timestamp")
                    all_bets.append(bet)
        
        # Mark settled bets
        settled_count = 0
        for event in events:
            if event.get("event_type") in ["bet_settled", "bets_settled"]:
                payload = event.get("payload", event)
                count = payload.get("settled_count", 0)
                pnl = payload.get("pnl_total", 0)
                
                for i in range(count):
                    if i < len(all_bets):
                        all_bets[i]["settled"] = True
                        all_bets[i]["pnl"] = pnl / count if count > 0 else 0
                        settled_count += 1
        
        # Group by market
        markets = defaultdict(lambda: {
            "bets": [],
            "total_pnl": 0,
            "wins": 0,
            "losses": 0,
        })
        
        for bet in all_bets:
            market = bet.get("market", "unknown")
            markets[market]["bets"].append(bet)
            
            if bet.get("settled"):
                markets[market]["total_pnl"] += bet.get("pnl", 0)
                if bet.get("won"):
                    markets[market]["wins"] += 1
                elif bet.get("won") == False:
                    markets[market]["losses"] += 1
        
        # Compute per-market metrics
        initial_bankroll = 1000.0
        results = {}
        
        for market, data in markets.items():
            total = len(data["bets"])
            if total == 0:
                continue
            
            pnl = data["total_pnl"]
            wins = data["wins"]
            losses = data["losses"]
            
            # Calculate metrics
            roi = (pnl / initial_bankroll * 100) if initial_bankroll > 0 else 0
            win_rate = (wins / total * 100) if total > 0 else 0
            avg_odds = sum(bet.get("odds", 0) for bet in data["bets"]) / total
            avg_ev = sum(bet.get("ev", 0) for bet in data["bets"]) / total
            
            # EV vs actual correlation
            ev_sum = sum(bet.get("ev", 0) for bet in data["bets"])
            
            results[market] = {
                "total_bets": total,
                "pnl": pnl,
                "roi": roi,
                "win_rate": win_rate,
                "wins": wins,
                "losses": losses,
                "avg_odds": avg_odds,
                "avg_ev": avg_ev,
                "ev_sum": ev_sum,
                "profitability_score": roi * 0.7 + win_rate * 0.3,  # Weighted score
            }
        
        # Sort by profitability
        sorted_markets = sorted(
            results.items(),
            key=lambda x: x[1]["roi"],
            reverse=True
        )
        
        return {
            "markets": dict(sorted_markets),
            "best_market": sorted_markets[0][0] if sorted_markets else None,
            "worst_market": sorted_markets[-1][0] if sorted_markets else None,
            "total_bets": sum(m["total_bets"] for m in results.values()),
        }
    
    def get_market_stability(
        self,
        market: str,
        events: Optional[list[dict]] = None
    ) -> dict:
        """
        Get stability metrics for a specific market.
        
        Returns:
            Dict with stability metrics (volatility, drift, etc.)
        """
        if events is None:
            events = self.event_store.get_events(
                event_types=["bets_generated", "bet_settled", "bets_settled"]
            )
        
        # Filter to market
        market_bets = []
        
        for event in events:
            if event.get("event_type") == "bets_generated":
                payload = event.get("payload", event)
                for bet in payload.get("bets", []):
                    if bet.get("market") == market:
                        market_bets.append(bet)
        
        if not market_bets:
            return {"error": f"No bets found for market: {market}"}
        
        # Calculate volatility (variance of PnL)
        pnls = [b.get("pnl", 0) for b in market_bets if b.get("settled")]
        
        if pnls:
            mean_pnl = sum(pnls) / len(pnls)
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
            volatility = variance ** 0.5
        else:
            volatility = 0
        
        return {
            "market": market,
            "total_bets": len(market_bets),
            "settled_bets": len(pnls),
            "volatility": volatility,
            "consistency_score": 100 - min(volatility * 10, 100),  # Lower volatility = higher score
        }
    
    def rank_markets(
        self,
        events: Optional[list[dict]] = None,
        since: Optional[datetime] = None
    ) -> list[dict]:
        """
        Rank markets by profitability.
        
        Returns:
            List of markets sorted by profitability score
        """
        analysis = self.analyze_markets(events, since)
        
        ranking = []
        for market, metrics in analysis.get("markets", {}).items():
            ranking.append({
                "market": market,
                "roi": metrics["roi"],
                "win_rate": metrics["win_rate"],
                "total_bets": metrics["total_bets"],
                "profitability_score": metrics["profitability_score"],
            })
        
        # Sort by profitability score
        ranking.sort(key=lambda x: x["profitability_score"], reverse=True)
        
        return ranking


# Convenience functions
def analyze_market_performance(days: int = 30) -> dict:
    """Analyze all markets over time period."""
    since = datetime.utcnow() - timedelta(days=days) if days else None
    analyzer = MarketAnalyzer()
    return analyzer.analyze_markets(since=since)


def rank_markets_by_profitability(days: int = 30) -> list[dict]:
    """Get ranked list of markets."""
    since = datetime.utcnow() - timedelta(days=days) if days else None
    analyzer = MarketAnalyzer()
    return analyzer.rank_markets(since=since)
