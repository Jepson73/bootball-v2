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
    DUMB EXECUTOR - Stateless execution layer.
    
    Receives allocation vectors from Portfolio Engine and executes.
    NO decision logic, NO EV filtering, NO Kelly calculation.
    
    Flow:
    1. Receives PORTFOLIO_ALLOCATED event with allocation vector
    2. Executes each allocation as-is
    3. Emits BET_PLACED events
    4. Handles settlement via BET_SETTLED events
    """
    
    def __init__(self, bankroll_manager: BankrollManager = None):
        self.bankroll = bankroll_manager or get_bankroll_manager()
        self.event_bus = event_bus
        self._event_bus = event_bus
        
        # Subscribe to events - PORTFOLIO_ALLOCATED is the primary path
        self._event_bus.subscribe(Events.PORTFOLIO_ALLOCATED, self.handle_portfolio_allocation)
        self._event_bus.subscribe(Events.BET_SETTLED, self.handle_bet_settled)
        self._event_bus.subscribe(Events.BETS_SETTLED, self.handle_bets_settled)
        
        # Legacy support - still handle BETS_GENERATED for backwards compatibility
        self._event_bus.subscribe(Events.BETS_GENERATED, self.handle_bets_generated)
        
        logger.info("ExecutionEngine initialized - DUMB EXECUTOR MODE")
    
    def handle_portfolio_allocation(self, event) -> None:
        """
        PRIMARY ENTRY POINT - Execute portfolio allocations.
        
        This is the ONLY decision path after refactor.
        Allocation vector comes pre-computed from Portfolio Engine.
        """
        data = event.data if hasattr(event, 'data') else event
        allocations = data.get("bets", [])
        run_id = data.get("run_id", "unknown")
        
        if not allocations:
            logger.info("[EXECUTION] No allocations to execute")
            return
        
        logger.info(f"[EXECUTION] Received {len(allocations)} allocations from portfolio engine")
        
        executed = []
        
        for alloc in allocations:
            result = self._execute_allocation(alloc)
            if result:
                executed.append(result)
        
        # Emit results
        if executed:
            total_stake = sum(b["stake"] for b in executed)
            self._event_bus.emit(Events.BET_PLACED, {
                "run_id": run_id,
                "bets": executed,
                "total_stake": total_stake,
                "source": "portfolio_engine",
                "timestamp": datetime.utcnow().isoformat()
            })
            logger.info(f"[EXECUTION] {len(executed)} bets executed from portfolio, total stake: {total_stake:.2f}")
        
        # Emit summary
        self._event_bus.emit(Events.EXECUTION_SUMMARY, {
            "run_id": run_id,
            "executed": len(executed),
            "total_staked": sum(b["stake"] for b in executed),
            "bankroll_after": self.bankroll.get_balance(),
            "source": "portfolio_engine"
        })
    
    def _execute_allocation(self, allocation: dict) -> Optional[dict]:
        """
        Execute a single allocation from portfolio engine.
        
        NO DECISION LOGIC - just place the bet as instructed.
        """
        stake = allocation.get("stake", 0)
        bankroll = self.bankroll.get_balance()
        
        # Simple balance check only
        if stake > bankroll:
            logger.warning(f"[EXECUTION] Insufficient balance for {allocation.get('bet_id')}")
            return None
        
        if stake < 1:
            logger.warning(f"[EXECUTION] Stake too small: {stake}")
            return None
        
        # Reserve stake
        success = self.bankroll.reserve(stake)
        
        if not success:
            logger.error(f"[EXECUTION] Failed to reserve stake: {stake}")
            return None
        
        return {
            "bet_id": allocation.get("bet_id", ""),
            "fixture_id": allocation.get("fixture_id", 0),
            "market": allocation.get("market", ""),
            "outcome": allocation.get("outcome", ""),
            "odds": allocation.get("odds", 0),
            "stake": stake,
            "expected_return": allocation.get("expected_return", 0),
            "placed_at": datetime.utcnow().isoformat(),
            "status": "open",
            "source": "portfolio_engine"
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
