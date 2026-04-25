"""
Betting State Reconstructor

Builds a deterministic view of the system from raw tables:
- placed_bets
- bankroll_rounds

This is the SINGLE source of truth for the betting dashboard.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

from src.storage.db import get_session
from sqlalchemy import text, select


# =========================================================
# OUTPUT MODEL
# =========================================================

@dataclass
class BettingState:
    """Canonical betting state for dashboard."""
    balance: float
    roi: float
    pending_count: int
    wins: int
    losses: int
    pending_stake: float
    bets: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    active_round_id: Optional[int] = None
    active_round_number: Optional[int] = None


# =========================================================
# CORE RECONSTRUCTOR
# =========================================================

def build_betting_state(active_round_id: int | None = None) -> BettingState:
    """
    Reconstruct betting dashboard state deterministically.
    
    Uses bankroll_rounds as source of truth for rounds,
    and placed_bets for individual bets.
    """
    with get_session() as s:
        # -------------------------
        # 1. Get active round
        # -------------------------
        if active_round_id:
            round_obj = s.execute(
                select(text("bankroll_rounds")).where(text(f"id = {active_round_id}"))
            ).fetchone()
        else:
            round_obj = s.execute(text("""
                SELECT * FROM bankroll_rounds WHERE is_active = 1 
                ORDER BY round_number DESC LIMIT 1
            """)).fetchone()
        
        if round_obj:
            active_round_id = round_obj[0]
            active_round_number = round_obj[1]
            initial_bankroll = round_obj[3] if len(round_obj) > 3 else 1000
        else:
            active_round_id = None
            active_round_number = None
            initial_bankroll = 1000
        
        # -------------------------
        # 2. Get all bets for active round
        # -------------------------
        if active_round_id:
            bets_result = s.execute(text("""
                SELECT pb.*, f.date as fixture_date, f.goals_home, f.goals_away,
                       ht.name as home_team, aw.name as away_team,
                       l.name as league_name
                FROM placed_bets pb
                LEFT JOIN fixtures f ON pb.fixture_id = f.id
                LEFT JOIN teams ht ON f.home_team_id = ht.id
                LEFT JOIN teams aw ON f.away_team_id = aw.id
                LEFT JOIN leagues l ON f.league_id = l.id
                WHERE pb.round_id = :round_id
                ORDER BY f.date DESC
            """), {"round_id": active_round_id}).fetchall()
        else:
            bets_result = []
        
        bets = []
        for row in bets_result:
            bets.append({
                "id": row[0],
                "fixture_id": row[1],
                "round_id": row[2],
                "market": row[3],
                "outcome": row[4],
                "stake": row[5],
                "odds": row[6],
                "our_prob": row[7],
                "ev": row[8],
                "kelly_fraction": row[9],
                "settled": row[10],
                "won": row[11],
                "pnl": row[12],
                "result": row[13],
                "model_version_id": row[14],
                "settled_at": row[15],
                "fixture_date": row[16] if len(row) > 16 else None,
                "goals_home": row[17] if len(row) > 17 else None,
                "goals_away": row[18] if len(row) > 18 else None,
                "home_team": row[19] if len(row) > 19 else None,
                "away_team": row[20] if len(row) > 20 else None,
                "league_name": row[21] if len(row) > 21 else None,
            })
        
        # -------------------------
        # 3. Calculate stats from bets
        # -------------------------
        pending = [b for b in bets if not b.get("settled")]
        settled = [b for b in bets if b.get("settled")]
        
        wins = sum(1 for b in settled if b.get("won") == 1 or b.get("won") is True)
        losses = sum(1 for b in settled if b.get("won") == 0 or b.get("won") is False)
        
        settled_pnl = sum(b.get("pnl", 0) or 0 for b in settled)
        pending_stake = sum(b.get("stake", 0) or 0 for b in pending)
        
        # Ensure initial_bankroll is valid
        if initial_bankroll is None or initial_bankroll <= 0:
            initial_bankroll = 1000
        
        # Balance = initial + settled P/L - pending stake
        balance = initial_bankroll + settled_pnl - pending_stake
        
        # ROI = settled P/L / initial bankroll
        roi = (settled_pnl / initial_bankroll * 100) if initial_bankroll > 0 else 0
        
        # -------------------------
        # 4. Get round history from bankroll_rounds
        # -------------------------
        rounds_result = s.execute(text("""
            SELECT id, round_number, started_at, ended_at, initial_bankroll,
                   ending_balance, total_bets, total_wins, total_staked, total_pnl, 
                   roi_pct, is_active
            FROM bankroll_rounds
            ORDER BY round_number DESC
        """)).fetchall()
        
        rounds = []
        for r in rounds_result:
            status = "Active" if r[11] else ("Closed" if r[3] else "Pending")
            rounds.append({
                "id": r[0],
                "round_number": r[1],
                "started_at": r[2],
                "ended_at": r[3],
                "initial_bankroll": r[4],
                "ending_balance": r[5],
                "total_bets": r[6],
                "total_wins": r[7],
                "total_staked": r[8],
                "total_pnl": r[9],
                "roi_pct": r[10],
                "is_active": r[11],
                "status": status,
            })
        
        return BettingState(
            balance=balance,
            roi=roi,
            pending_count=len(pending),
            wins=wins,
            losses=losses,
            pending_stake=pending_stake,
            bets=bets,
            rounds=rounds,
            active_round_id=active_round_id,
            active_round_number=active_round_number,
        )
