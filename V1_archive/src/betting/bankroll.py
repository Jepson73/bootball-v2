"""
Bankroll Manager - Manages simulated bankroll operations.

Provides deterministic bankroll state mutations.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BankrollState:
    """Current bankroll state."""
    balance: float = 1000.0
    reserved: float = 0.0  # Pending bets stake
    total_staked: float = 0.0
    total_profit: float = 0.0
    total_wins: int = 0
    total_losses: int = 0


class BankrollManager:
    """
    Manages simulated bankroll operations.
    
    Provides:
    - get_balance(): Available balance
    - reserve(): Reserve stake for pending bet
    - release(): Release reserved stake (bet cancelled)
    - consume(): Mark stake as lost
    - add_profit(): Add winnings
    """
    
    def __init__(self, initial_balance: float = 1000.0):
        self._state = BankrollState(balance=initial_balance)
        logger.info(f"BankrollManager initialized with {initial_balance} SEK")
    
    def get_balance(self) -> float:
        """Get available balance (not reserved)."""
        return self._state.balance
    
    def get_total_balance(self) -> float:
        """Get total balance including reserved."""
        return self._state.balance + self._state.reserved
    
    def get_state(self) -> BankrollState:
        """Get full bankroll state."""
        return self._state
    
    def set_balance(self, balance: float) -> None:
        """Set balance (for loading from DB)."""
        self._state.balance = balance
    
    def reserve(self, amount: float) -> bool:
        """Reserve stake for a pending bet.
        
        Returns True if successful, False if insufficient funds.
        """
        if amount <= 0:
            return False
        
        if self._state.balance < amount:
            logger.warning(f"Insufficient balance to reserve {amount}")
            return False
        
        self._state.balance -= amount
        self._state.reserved += amount
        logger.debug(f"Reserved: {amount}, balance: {self._state.balance}")
        return True
    
    def release(self, amount: float) -> bool:
        """Release reserved stake (bet cancelled/voided).
        
        Returns the stake to available balance.
        """
        if amount <= 0:
            return False
        
        # Can't release more than reserved
        if amount > self._state.reserved:
            amount = self._state.reserved
        
        self._state.balance += amount
        self._state.reserved -= amount
        logger.debug(f"Released: {amount}, balance: {self._state.balance}")
        return True
    
    def consume(self, amount: float) -> bool:
        """Consume reserved stake (bet lost).
        
        Marks the stake as lost and removes from reserved.
        """
        if amount <= 0:
            return False
        
        # Can't consume more than reserved
        if amount > self._state.reserved:
            amount = self._state.reserved
        
        self._state.reserved -= amount
        self._state.total_staked += amount
        self._state.total_losses += 1
        logger.debug(f"Consumed: {amount}, total staked: {self._state.total_staked}")
        return True
    
    def add_profit(self, amount: float) -> bool:
        """Add profit from winning bet.
        
        Adds winnings (payout - stake) to balance.
        """
        if amount < 0:
            return False
        
        self._state.balance += amount
        self._state.total_profit += amount
        self._state.total_wins += 1
        logger.debug(f"Profit added: {amount}, balance: {self._state.balance}")
        return True
    
    def settle_bet(self, stake: float, odds: float, won: bool) -> float:
        """Settle a bet and update bankroll.
        
        Args:
            stake: Original stake amount
            odds: Decimal odds
            won: Whether bet won
            
        Returns:
            PnL (positive for win, negative for loss)
        """
        # Release the reserved stake first
        self.release(stake)
        
        if won:
            payout = stake * (odds - 1)  # Profit only
            self.add_profit(payout)
            self._state.total_staked += stake
            return payout
        else:
            self.consume(stake)
            self._state.total_staked += stake
            return -stake
    
    def get_roi(self) -> float:
        """Calculate ROI percentage."""
        if self._state.total_staked == 0:
            return 0.0
        return (self._state.total_profit / self._state.total_staked) * 100


# Global instance
_bankroll_manager: Optional[BankrollManager] = None


def get_bankroll_manager() -> BankrollManager:
    """Get global bankroll manager."""
    global _bankroll_manager
    if _bankroll_manager is None:
        _bankroll_manager = BankrollManager()
    return _bankroll_manager
