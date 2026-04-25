"""
Backtest Engine - replay events with configurable strategy.

Deterministic simulation engine for:
- Historical performance evaluation
- Strategy comparison
- What-if scenario testing

NO side effects - fully replayable.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Any

from src.events.event_store import get_event_store

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "model_version_override": None,
    "min_ev_threshold": 0.05,
    "kelly_multiplier": 0.25,
    "market_filter": None,  # None = all markets
    "simulate_no_bets": False,
    "risk_scaling": "balanced",  # conservative, balanced, aggressive
    "initial_bankroll": 1000.0,
    "max_stake_pct": 0.10,  # Max 10% of bankroll per bet
    "stop_loss_pct": 0.20,  # Stop if 20% drawdown
}


class BacktestEngine:
    """
    Backtest simulation engine.
    
    Replays historical events with optional strategy modifications.
    """
    
    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.event_store = get_event_store()
    
    def run_backtest(
        self,
        events: Optional[list[dict]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        run_id: Optional[str] = None
    ) -> dict:
        """
        Run backtest simulation.
        
        Args:
            events: Pre-provided events (loads from store if not provided)
            since: Filter events after this time
            until: Filter events before this time
            run_id: Filter by specific run
            
        Returns:
            Full simulation report
        """
        # Load events if not provided
        if events is None:
            events = self.event_store.get_events(
                since=since,
                until=until,
                run_id=run_id,
                event_types=["bets_generated", "bet_settled", "bets_settled", "run_finished"]
            )
        
        # Sort by timestamp
        events = sorted(events, key=lambda e: e.get("timestamp", ""))
        
        # Initialize simulation state
        bankroll = self.config["initial_bankroll"]
        max_bankroll = bankroll
        min_bankroll = bankroll
        drawdown = 0
        
        # Track bets and outcomes
        simulated_bets = []
        settled_bets = []
        
        # Run simulation
        for event in events:
            if event.get("event_type") == "bets_generated":
                # Process generated bets
                payload = event.get("payload", event)
                run_id = payload.get("run_id")
                
                for bet in payload.get("bets", []):
                    # Apply market filter
                    if self.config["market_filter"]:
                        if bet.get("market") not in self.config["market_filter"]:
                            continue
                    
                    # Apply decision logic override
                    if self._should_place_bet(bet):
                        # Calculate stake
                        stake = self._calculate_stake(bet, bankroll)
                        
                        if stake > 0:
                            simulated_bets.append({
                                "bet": bet,
                                "stake": stake,
                                "generated_at": event.get("timestamp"),
                                "run_id": run_id,
                            })
            
            elif event.get("event_type") in ["bet_settled", "bets_settled"]:
                # Settle pending bets
                payload = event.get("payload", event)
                settled_count = payload.get("settled_count", 0)
                pnl = payload.get("pnl_total", 0)
                wins = payload.get("wins", 0)
                losses = payload.get("losses", 0)
                
                # Apply settlement to simulated bets
                for i in range(min(settled_count, len(simulated_bets))):
                    sb = simulated_bets[i]
                    
                    # Simulate outcome (simplified - use historical if available)
                    won = pnl > 0 if settled_count > 0 else False
                    
                    # Calculate PnL
                    if won:
                        bet_pnl = sb["stake"] * (sb["bet"].get("odds", 1) - 1)
                    else:
                        bet_pnl = -sb["stake"]
                    
                    bankroll += bet_pnl
                    
                    # Track drawdown
                    max_bankroll = max(max_bankroll, bankroll)
                    min_bankroll = min(min_bankroll, bankroll)
                    drawdown = (max_bankroll - bankroll) / max_bankroll * 100 if max_bankroll > 0 else 0
                    
                    # Check stop loss
                    if drawdown >= self.config["stop_loss_pct"] * 100:
                        logger.info(f"Stop loss triggered: {drawdown:.1f}% drawdown")
                        break
                    
                    settled_bets.append({
                        "bet": sb["bet"],
                        "stake": sb["stake"],
                        "won": won,
                        "pnl": bet_pnl,
                        "settled_at": event.get("timestamp"),
                    })
        
        # Compute final metrics
        return self._compute_metrics(
            simulated_bets,
            settled_bets,
            bankroll,
            max_bankroll,
            min_bankroll,
            drawdown
        )
    
    def _should_place_bet(self, bet: dict) -> bool:
        """
        Determine if bet should be placed (with strategy override).
        
        Applies EV threshold and strategy settings.
        """
        if self.config["simulate_no_bets"]:
            return False
        
        ev = bet.get("ev", 0)
        return ev >= self.config["min_ev_threshold"]
    
    def _calculate_stake(self, bet: dict, current_bankroll: float) -> float:
        """
        Calculate stake using modified Kelly criterion.
        
        Applies risk scaling settings.
        """
        kelly_mult = self.config["kelly_multiplier"]
        
        # Get odds and probability
        odds = bet.get("odds", 2.0)
        ev = bet.get("ev", 0)
        prob = bet.get("our_prob", 0.5)
        
        # Simple Kelly calculation
        b = odds - 1
        q = 1 - prob
        
        if b <= 0 or prob <= 0:
            return 0
        
        kelly = (b * prob - q) / b
        kelly = max(0, kelly) * kelly_mult
        
        # Apply risk scaling
        risk_scale = {
            "conservative": 0.5,
            "balanced": 1.0,
            "aggressive": 1.5,
        }.get(self.config["risk_scaling"], 1.0)
        
        kelly *= risk_scale
        
        # Apply max stake constraint
        max_stake = current_bankroll * self.config["max_stake_pct"]
        
        return min(kelly * current_bankroll, max_stake)
    
    def _compute_metrics(
        self,
        simulated_bets: list,
        settled_bets: list,
        final_bankroll: float,
        max_bankroll: float,
        min_bankroll: float,
        drawdown: float
    ) -> dict:
        """Compute simulation metrics."""
        
        initial = self.config["initial_bankroll"]
        total_pnl = final_bankroll - initial
        total_bets = len(settled_bets)
        
        if total_bets == 0:
            return {
                "total_bets": 0,
                "settled_bets": 0,
                "total_pnl": 0,
                "roi": 0,
                "win_rate": 0,
                "avg_stake": 0,
                "max_drawdown": 0,
                "bankroll_curve": [],
                "market_breakdown": {},
            }
        
        wins = sum(1 for b in settled_bets if b.get("won"))
        losses = total_bets - wins
        
        roi = (total_pnl / initial * 100) if initial > 0 else 0
        win_rate = (wins / total_bets * 100) if total_bets > 0 else 0
        avg_stake = sum(b["stake"] for b in settled_bets) / total_bets
        
        # Market breakdown
        market_stats = defaultdict(lambda: {"bets": 0, "pnl": 0})
        for b in settled_bets:
            market = b["bet"].get("market", "unknown")
            market_stats[market]["bets"] += 1
            market_stats[market]["pnl"] += b.get("pnl", 0)
        
        market_breakdown = {}
        for market, stats in market_stats.items():
            market_breakdown[market] = {
                "bets": stats["bets"],
                "pnl": stats["pnl"],
                "roi": (stats["pnl"] / initial * 100) if initial > 0 else 0,
            }
        
        # Bankroll curve (simplified - just start and end)
        bankroll_curve = [
            {"timestamp": "start", "bankroll": initial},
            {"timestamp": "end", "bankroll": final_bankroll},
        ]
        
        return {
            "total_bets": len(simulated_bets),
            "settled_bets": total_bets,
            "total_pnl": total_pnl,
            "final_bankroll": final_bankroll,
            "roi": roi,
            "win_rate": win_rate,
            "wins": wins,
            "losses": losses,
            "avg_stake": avg_stake,
            "max_bankroll": max_bankroll,
            "min_bankroll": min_bankroll,
            "max_drawdown": drawdown,
            "bankroll_curve": bankroll_curve,
            "market_breakdown": market_breakdown,
            "config": self.config,
        }


# Convenience functions
def run_backtest(
    days: int = 30,
    config: Optional[dict] = None,
    model_version: Optional[str] = None
) -> dict:
    """Run backtest over time period."""
    since = datetime.utcnow() - timedelta(days=days)
    
    cfg = {**(config or {}), "model_version_override": model_version}
    
    engine = BacktestEngine(cfg)
    return engine.run_backtest(since=since)


def run_scenario(
    scenario_name: str,
    days: int = 30
) -> dict:
    """Run predefined scenario."""
    scenarios = {
        "baseline": {"min_ev_threshold": 0.05, "kelly_multiplier": 0.25},
        "conservative": {"min_ev_threshold": 0.10, "kelly_multiplier": 0.15, "risk_scaling": "conservative"},
        "aggressive": {"min_ev_threshold": 0.02, "kelly_multiplier": 0.40, "risk_scaling": "aggressive"},
    }
    
    cfg = scenarios.get(scenario_name, scenarios["baseline"])
    return run_backtest(days=days, config=cfg)
