"""
Execution Engine - Single authority for bet execution and bankroll mutations.

Consumes allocation decisions and executes bets against simulated bankroll.
Replaces ad-hoc betting logic.
"""

import logging
from datetime import datetime
from typing import Optional

from src.alerts.event_bus import event_bus, Events
from src.betting.bankroll import BankrollManager, get_bankroll_manager

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Executes bets from event bus and manages bankroll state.
    
    Flow:
    1. Subscribes to BETS_GENERATED events
    2. Evaluates each bet with risk guards
    3. Places approved bets (reserves stake)
    4. Emits BET_PLACED events
    5. Handles settlement via BET_SETTLED events
    """
    
    def __init__(self, bankroll_manager: BankrollManager = None):
        self.bankroll = bankroll_manager or get_bankroll_manager()
        self.event_bus = event_bus
        self._event_bus = event_bus
        
        # Subscribe to events
        self._event_bus.subscribe(Events.BETS_GENERATED, self.handle_bets_generated)
        self._event_bus.subscribe(Events.BET_SETTLED, self.handle_bet_settled)
        self._event_bus.subscribe(Events.BETS_SETTLED, self.handle_bets_settled)
        
        logger.info("ExecutionEngine initialized")
    
    def handle_bets_generated(self, event) -> None:
        """Entry point from event bus - handle BETS_GENERATED event."""
        data = event.data if hasattr(event, 'data') else event
        bets = data.get("bets", [])
        run_id = data.get("run_id")
        
        if not bets:
            logger.debug("No bets to execute")
            return
        
        logger.info(f"Evaluating {len(bets)} bets for execution")
        
        executed = []
        rejected = []
        
        for bet in bets:
            decision = self._evaluate_bet(bet)
            
            if not decision["approved"]:
                rejected.append({**bet, "reason": decision["reason"]})
                logger.debug(f"Bet rejected: {decision['reason']}")
                continue
            
            result = self._place_bet(bet, decision["stake"])
            executed.append(result)
        
        # Emit results
        if executed:
            total_stake = sum(b["stake"] for b in executed)
            self._event_bus.emit(Events.BET_PLACED, {
                "run_id": run_id,
                "bets": executed,
                "total_stake": total_stake,
                "timestamp": datetime.utcnow().isoformat()
            })
            logger.info(f"Executed {len(executed)} bets, total stake: {total_stake:.2f}")
        
        if rejected:
            self._event_bus.emit(Events.BET_REJECTED, {
                "run_id": run_id,
                "bets": rejected,
                "timestamp": datetime.utcnow().isoformat()
            })
            logger.info(f"Rejected {len(rejected)} bets")
        
        # Emit summary
        self._event_bus.emit(Events.EXECUTION_SUMMARY, {
            "run_id": run_id,
            "approved": len(executed),
            "rejected": len(rejected),
            "total_staked": sum(b["stake"] for b in executed),
            "bankroll_after": self.bankroll.get_balance()
        })
    
    def _evaluate_bet(self, bet: dict) -> dict:
        """Evaluate a single bet for execution."""
        bankroll = self.bankroll.get_balance()
        
        kelly = bet.get("kelly_fraction", bet.get("kelly", 0))
        odds = bet.get("odds", 0)
        
        # Basic guards
        if kelly <= 0:
            return {"approved": False, "reason": "zero_kelly"}
        
        if odds < 1.5:
            return {"approved": False, "reason": "low_odds"}
        
        # Stake calculation (fractional Kelly already applied)
        stake = bankroll * kelly
        
        # Risk cap: max 5% bankroll per bet
        max_stake = bankroll * 0.05
        if stake > max_stake:
            stake = max_stake
        
        # Minimum stake threshold
        if stake < 1:
            return {"approved": False, "reason": "stake_too_small"}
        
        # Check sufficient balance
        if stake > bankroll:
            return {"approved": False, "reason": "insufficient_balance"}
        
        return {
            "approved": True,
            "stake": stake
        }
    
    def _place_bet(self, bet: dict, stake: float) -> dict:
        """Place a bet by reserving stake."""
        success = self.bankroll.reserve(stake)
        
        if not success:
            raise RuntimeError(f"Failed to reserve stake: {stake}")
        
        return {
            "fixture_id": bet.get("fixture_id"),
            "market": bet.get("market"),
            "outcome": bet.get("outcome"),
            "odds": bet.get("odds"),
            "stake": stake,
            "ev": bet.get("ev"),
            "our_prob": bet.get("our_prob"),
            "placed_at": datetime.utcnow().isoformat(),
            "status": "open"
        }
    
    def handle_bet_settled(self, event) -> None:
        """Handle single bet settlement."""
        data = event.data if hasattr(event, 'data') else event
        bet = data.get("bet", {})
        
        if bet:
            self._settle_single_bet(bet)
    
    def handle_bets_settled(self, event) -> None:
        """Handle batch bet settlements."""
        data = event.data if hasattr(event, 'data') else event
        bets = data.get("bets", data.get("settled_bets", []))
        
        results = []
        for bet in bets:
            pnl = self._settle_single_bet(bet)
            results.append(pnl)
        
        total_pnl = sum(r["pnl"] for r in results)
        wins = sum(1 for r in results if r["won"])
        losses = len(results) - wins
        
        logger.info(f"Settled {len(results)} bets: {wins} wins, {losses} losses, PnL: {total_pnl:.2f}")
        
        # Emit settlement summary
        self._event_bus.emit(Events.BETS_SETTLED, {
            "settled_count": len(results),
            "wins": wins,
            "losses": losses,
            "total_pnl": total_pnl,
            "bankroll": self.bankroll.get_balance()
        })
    
    def _settle_single_bet(self, bet: dict) -> dict:
        """Settle a single bet."""
        stake = bet.get("stake", 0)
        odds = bet.get("odds", 0)
        won = bet.get("won", False)
        
        pnl = self.bankroll.settle_bet(stake, odds, won)
        
        return {
            "fixture_id": bet.get("fixture_id"),
            "market": bet.get("market"),
            "outcome": bet.get("outcome"),
            "stake": stake,
            "odds": odds,
            "won": won,
            "pnl": pnl
        }
    
    def get_bankroll_state(self) -> dict:
        """Get current bankroll state."""
        state = self.bankroll.get_state()
        return {
            "balance": state.balance,
            "reserved": state.reserved,
            "total_staked": state.total_staked,
            "total_profit": state.total_profit,
            "total_wins": state.total_wins,
            "total_losses": state.total_losses,
            "roi": self.bankroll.get_roi()
        }


# Global instance
_engine: Optional[ExecutionEngine] = None


def get_execution_engine() -> ExecutionEngine:
    """Get global execution engine."""
    global _engine
    if _engine is None:
        _engine = ExecutionEngine()
    return _engine
