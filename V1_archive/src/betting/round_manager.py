"""
src/betting/round_manager.py - Shared bankroll round management.

Used by settle_fixtures, auto_bet, and daily_run to ensure they all use the same active round.

Usage:
    from src.betting.round_manager import get_active_round_id

    round_id = get_active_round_id(session)
"""
import os
from datetime import datetime

from sqlalchemy import select, func

from src.storage.models import BankrollRound

ROUND_SIZE = 20

ROUND_ID_FILE = '/var/run/bootball/active_round_id'


def _ensure_dir():
    """Ensure the directory exists."""
    os.makedirs('/var/run/bootball', exist_ok=True)


def get_active_round_id(session, create: bool = True) -> int | None:
    """Get the active round ID, creating a new round if needed.

    First checks file cache for round ID persistence across processes,
    then falls back to database lookup.
    """
    _ensure_dir()

    # Check file cache first
    if os.path.exists(ROUND_ID_FILE):
        try:
            round_id = int(open(ROUND_ID_FILE).read().strip())
            # Verify round still exists and is active
            round_obj = session.execute(
                select(BankrollRound).where(BankrollRound.id == round_id)
            ).scalars().first()
            if round_obj and round_obj.is_active:
                return round_id
        except (ValueError, FileNotFoundError):
            pass

    # Fall back to database lookup
    active = session.execute(
        select(BankrollRound)
        .where(BankrollRound.is_active == True)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    if active:
        _write_round_id(active.id)
        return active.id

    if not create:
        return None

    # Create new round
    last_round = session.execute(
        select(BankrollRound)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    next_num = (last_round.round_number + 1) if last_round else 1

    new_round = BankrollRound(
        round_number=next_num,
        initial_bankroll=1000.0,
        is_active=True,
    )
    session.add(new_round)
    session.commit()
    session.refresh(new_round)

    _write_round_id(new_round.id)
    return new_round.id


def _write_round_id(round_id: int) -> None:
    """Write round ID to file cache."""
    _ensure_dir()
    with open(ROUND_ID_FILE, 'w') as f:
        f.write(str(round_id))


def archive_and_create_new_round(session, reason: str = "manual_reset") -> BankrollRound:
    """Archive the current active round and create a new one.

    Returns the new BankrollRound.
    """
    global _active_round_id

    active = session.execute(
        select(BankrollRound)
        .where(BankrollRound.is_active == True)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    if active:
        from src.storage.models import PlacedBet

        settled = session.execute(
            select(PlacedBet).where(PlacedBet.round_id == active.id).where(PlacedBet.settled == True)
        ).scalars().all()

        active.is_active = False
        active.ended_at = datetime.utcnow()
        active.ending_balance = active.initial_bankroll + sum(b.pnl or 0 for b in settled)
        active.total_bets = len(settled)
        active.total_wins = sum(1 for b in settled if b.won)
        active.total_staked = sum(b.stake for b in settled)
        active.total_pnl = sum(b.pnl or 0 for b in settled)
        # Return on bankroll (matches BettingState.roi and the history-table display),
        # not pnl/turnover "yield" — the latter reads as a wildly different number for
        # the same round once capital gets recycled across many bets.
        active.roi_pct = (active.total_pnl / active.initial_bankroll * 100) if active.initial_bankroll > 0 else 0
        active.reason = reason

    last_round = session.execute(
        select(BankrollRound)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    next_num = (last_round.round_number + 1) if last_round else 1

    new_round = BankrollRound(
        round_number=next_num,
        initial_bankroll=1000.0,
        is_active=True,
    )
    session.add(new_round)
    session.commit()
    session.refresh(new_round)

    _write_round_id(new_round.id)
    return new_round


def close_round_if_full(session) -> BankrollRound | None:
    """Close the active round and open a new one if it has reached ROUND_SIZE bets.

    Returns the new round if one was created, None if the active round is still open.
    """
    from src.storage.models import PlacedBet

    active = session.execute(
        select(BankrollRound)
        .where(BankrollRound.is_active == True)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    if not active:
        return None

    bet_count = session.execute(
        select(func.count()).select_from(PlacedBet).where(PlacedBet.round_id == active.id)
    ).scalar() or 0

    if bet_count < ROUND_SIZE:
        return None

    all_bets = session.execute(
        select(PlacedBet).where(PlacedBet.round_id == active.id)
    ).scalars().all()
    settled = [b for b in all_bets if b.settled]

    active.is_active = False
    active.ended_at = datetime.utcnow()
    active.total_bets = len(all_bets)
    active.total_wins = sum(1 for b in settled if b.won)
    active.total_staked = sum(b.stake for b in all_bets)
    active.total_pnl = sum(b.pnl or 0 for b in settled)
    active.ending_balance = active.initial_bankroll + active.total_pnl
    # Return on bankroll — see comment in reset_round/archive above.
    active.roi_pct = (active.total_pnl / active.initial_bankroll * 100) if active.initial_bankroll > 0 else 0
    active.reason = 'auto_20_bets'

    new_round = BankrollRound(
        round_number=active.round_number + 1,
        initial_bankroll=active.initial_bankroll,
        is_active=True,
    )
    session.add(new_round)
    session.commit()
    session.refresh(new_round)

    _write_round_id(new_round.id)
    return new_round


def update_closed_round_stats(session) -> int:
    """Refresh stats for all closed rounds that still have unsettled bets.

    Called after every settlement run so final ROI and win rate converge
    as bets resolve over the days following round close.
    Returns the number of rounds updated.
    """
    from src.storage.models import PlacedBet

    closed = session.execute(
        select(BankrollRound)
        .where(BankrollRound.is_active == False)
        .where(BankrollRound.ended_at.is_not(None))
    ).scalars().all()

    updated = 0
    for rnd in closed:
        all_bets = session.execute(
            select(PlacedBet).where(PlacedBet.round_id == rnd.id)
        ).scalars().all()

        if not all_bets:
            continue

        settled = [b for b in all_bets if b.settled]
        total_pnl = sum(b.pnl or 0 for b in settled)
        total_wins = sum(1 for b in settled if b.won)
        total_staked = sum(b.stake for b in all_bets)
        ending_balance = rnd.initial_bankroll + total_pnl
        # Return on bankroll — see comment in reset_round/archive above.
        roi_pct = (total_pnl / rnd.initial_bankroll * 100) if rnd.initial_bankroll > 0 else 0

        if (rnd.total_wins != total_wins
                or abs((rnd.total_pnl or 0) - total_pnl) > 0.001
                or abs((rnd.ending_balance or 0) - ending_balance) > 0.001):
            rnd.total_bets = len(all_bets)
            rnd.total_wins = total_wins
            rnd.total_staked = total_staked
            rnd.total_pnl = total_pnl
            rnd.ending_balance = ending_balance
            rnd.roi_pct = roi_pct
            updated += 1

    if updated:
        session.commit()
    return updated


def set_active_round_id(round_id: int) -> None:
    """Set the active round ID manually."""
    _write_round_id(round_id)


def clear_active_round_id() -> None:
    """Clear the cached round ID."""
    if os.path.exists(ROUND_ID_FILE):
        os.remove(ROUND_ID_FILE)
