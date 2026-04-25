"""
Backtest Comparator - compare simulation results.

Enables A/B testing of strategies and model versions.
"""

import logging
from typing import Optional

from src.backtesting.backtest_engine import BacktestEngine, run_scenario

logger = logging.getLogger(__name__)


class BacktestComparator:
    """
    Compare backtest results.
    """
    
    def __init__(self):
        pass
    
    def compare_results(
        self,
        result_a: dict,
        result_b: dict,
        names: Optional[tuple] = None
    ) -> dict:
        """
        Compare two backtest results.
        
        Args:
            result_a: First result
            result_b: Second result
            names: Optional tuple of (name_a, name_b)
            
        Returns:
            Comparison dict
        """
        name_a = names[0] if names else "Scenario A"
        name_b = names[1] if names else "Scenario B"
        
        # Compute deltas
        roi_delta = result_b["roi"] - result_a["roi"]
        pnl_delta = result_b["total_pnl"] - result_a["total_pnl"]
        bets_delta = result_b["settled_bets"] - result_a["settled_bets"]
        drawdown_delta = result_b["max_drawdown"] - result_a["max_drawdown"]
        win_rate_delta = result_b["win_rate"] - result_a["win_rate"]
        
        # Determine winner
        if roi_delta > 0:
            winner = name_b
            win_pct = roi_delta
        elif roi_delta < 0:
            winner = name_a
            win_pct = -roi_delta
        else:
            winner = "tie"
            win_pct = 0
        
        # Market comparison
        markets = set(result_a.get("market_breakdown", {}).keys())
        markets.update(result_b.get("market_breakdown", {}).keys())
        
        market_comparison = {}
        for market in markets:
            a_market = result_a.get("market_breakdown", {}).get(market, {})
            b_market = result_b.get("market_breakdown", {}).get(market, {})
            
            market_comparison[market] = {
                f"{name_a}_pnl": a_market.get("pnl", 0),
                f"{name_b}_pnl": b_market.get("pnl", 0),
                "pnl_delta": b_market.get("pnl", 0) - a_market.get("pnl", 0),
            }
        
        return {
            "name_a": name_a,
            "name_b": name_b,
            "metrics_a": {
                "roi": result_a["roi"],
                "total_pnl": result_a["total_pnl"],
                "settled_bets": result_a["settled_bets"],
                "win_rate": result_a["win_rate"],
                "max_drawdown": result_a["max_drawdown"],
            },
            "metrics_b": {
                "roi": result_b["roi"],
                "total_pnl": result_b["total_pnl"],
                "settled_bets": result_b["settled_bets"],
                "win_rate": result_b["win_rate"],
                "max_drawdown": result_b["max_drawdown"],
            },
            "deltas": {
                "roi": roi_delta,
                "pnl": pnl_delta,
                "bets": bets_delta,
                "drawdown": drawdown_delta,
                "win_rate": win_rate_delta,
            },
            "winner": winner,
            "win_margin": win_pct,
            "market_comparison": market_comparison,
        }
    
    def compare_scenarios(
        self,
        scenario_a: str,
        scenario_b: str,
        days: int = 30
    ) -> dict:
        """
        Run and compare two scenarios.
        """
        result_a = run_scenario(scenario_a, days)
        result_b = run_scenario(scenario_b, days)
        
        return self.compare_results(result_a, result_b, (scenario_a, scenario_b))
    
    def rank_strategies(
        self,
        results: list[dict]
    ) -> list[dict]:
        """
        Rank multiple strategies by ROI.
        
        Args:
            results: List of backtest results
            
        Returns:
            Sorted list with rankings
        """
        ranked = []
        
        for result in results:
            ranked.append({
                "name": result.get("name", "Unknown"),
                "roi": result["roi"],
                "total_pnl": result["total_pnl"],
                "max_drawdown": result["max_drawdown"],
                "settled_bets": result["settled_bets"],
                "win_rate": result["win_rate"],
            })
        
        # Sort by ROI
        ranked.sort(key=lambda x: x["roi"], reverse=True)
        
        # Add rank
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        
        return ranked
    
    def compute_risk_adjusted_return(
        self,
        result: dict,
        risk_free_rate: float = 0.02
    ) -> dict:
        """
        Compute risk-adjusted return metrics.
        
        Sharpe-like ratio using drawdown as risk proxy.
        """
        roi = result["roi"] / 100  # Convert to decimal
        max_dd = result["max_drawdown"] / 100
        
        if max_dd > 0:
            return_on_dd = roi / max_dd
        else:
            return_on_dd = roi * 10  # Bonus for no drawdown
        
        # Simple Sharpe-like
        excess_return = roi - risk_free_rate
        
        if max_dd > 0:
            sharpe_like = excess_return / max_dd
        else:
            sharpe_like = excess_return * 10
        
        return {
            "roi": result["roi"],
            "max_drawdown": result["max_drawdown"],
            "return_on_drawdown": return_on_dd,
            "excess_return": excess_return * 100,
            "sharpe_like": sharpe_like,
            "risk_rating": "low" if max_dd < 0.1 else "medium" if max_dd < 0.2 else "high",
        }


# Convenience functions
def compare_scenarios(scenario_a: str, scenario_b: str, days: int = 30) -> dict:
    """Compare two scenarios."""
    comparator = BacktestComparator()
    return comparator.compare_scenarios(scenario_a, scenario_b, days)


def rank_strategies(results: list[dict]) -> list[dict]:
    """Rank strategies by ROI."""
    comparator = BacktestComparator()
    return comparator.rank_strategies(results)


def risk_adjusted_return(result: dict) -> dict:
    """Compute risk-adjusted returns."""
    comparator = BacktestComparator()
    return comparator.compute_risk_adjusted_return(result)
