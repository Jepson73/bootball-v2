"""
Betting State Builder.

Reconstructs betting dashboard state from events.
No SQL logic - purely event-driven reconstruction.
Uses snapshot optimization for performance.
"""

import logging
from typing import Optional

from src.state.models import BettingState
from src.state.reconstructor import StateReconstructor
from src.state.snapshot_store import get_snapshot_store

logger = logging.getLogger(__name__)


def build_betting_state(
    events: Optional[list[dict]] = None,
    since: Optional[str] = None,
    use_snapshot: bool = True
) -> BettingState:
    """
    Build betting state for dashboard.
    
    Args:
        events: Pre-provided events (optional)
        since: ISO timestamp string to filter events after
        use_snapshot: Use incremental rebuild (default True)
        
    Returns:
        BettingState with reconstructed values
    """
    from datetime import datetime
    
    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
    
    reconstructor = StateReconstructor()
    
    # Try incremental with snapshot first
    if use_snapshot:
        try:
            snapshot_store = get_snapshot_store()
            snapshot = snapshot_store.get_latest_snapshot()
            
            if snapshot:
                logger.debug(f"Using snapshot {snapshot.id} for incremental rebuild")
                system = reconstructor.rebuild_incremental(events, snapshot)
                state = system.betting
            else:
                # Fallback to full replay
                logger.debug("No snapshot available, using full replay")
                system = reconstructor.rebuild_from_events(events, since_dt)
                state = system.betting
        except Exception as e:
            logger.warning(f"Snapshot rebuild failed: {e}, falling back to full replay")
            system = reconstructor.rebuild_from_events(events, since_dt)
            state = system.betting
    else:
        # Force full replay
        system = reconstructor.rebuild_from_events(events, since_dt)
        state = system.betting
    
    # Map to expected dashboard format
    return BettingState(
        balance=state.balance,
        roi=state.roi,
        pending_count=state.pending_count,
        wins=state.wins,
        losses=state.losses,
        pending_stake=state.pending_stake,
        total_pnl=state.total_pnl,
        bets=state.bets,
        rounds=state.rounds,
        active_round_id=state.active_round_id,
        active_round_number=state.active_round_number,
        initial_bankroll=state.initial_bankroll,
    )


def get_current_balance() -> float:
    """Get current balance from events (with snapshot optimization)."""
    state = build_betting_state()
    return state.balance


def get_pending_bets() -> list[dict]:
    """Get pending bets from events."""
    state = build_betting_state()
    return [b for b in state.bets if not b.get("settled")]


def get_settled_bets() -> list[dict]:
    """Get settled bets from events."""
    state = build_betting_state()
    return [b for b in state.bets if b.get("settled")]
