#!/usr/bin/env python3
"""
Backfill betting rounds from historical bet data.

Groups bets by time gaps and creates round records.
Updates all placed_bets with proper round_id.
"""

import sys
sys.path.insert(0, '/opt/projects/bootball')

from datetime import datetime, timedelta
from src.storage.db import get_session
from sqlalchemy import text


ROUND_GAP_HOURS = 12
DEFAULT_INITIAL_BANKROLL = 1000.0


def backfill_betting_rounds():
    """Backfill betting rounds from historical bet data."""
    print("Starting betting round backfill...")
    
    with get_session() as s:
        existing_rounds = s.execute(text("SELECT COUNT(*) FROM bankroll_rounds")).scalar()
        print(f"Existing rounds: {existing_rounds}")
        
        bets = s.execute(text("""
            SELECT id, placed_at, settled, pnl, stake
            FROM placed_bets 
            ORDER BY placed_at ASC
        """)).fetchall()
        
        if not bets:
            print("No bets found to backfill")
            return
        
        print(f"Found {len(bets)} total bets")
        
        rounds_data = []
        current_round_bets = []
        round_start_time = None
        round_number = 1
        
        for row in bets:
            bet_id = row[0]
            placed_at = row[1]
            settled = row[2]
            pnl = row[3]
            stake = row[4]
            
            if isinstance(placed_at, str):
                placed_at = datetime.fromisoformat(placed_at)
            
            bet_info = {
                'id': bet_id,
                'placed_at': placed_at,
                'settled': settled,
                'pnl': pnl,
                'stake': stake
            }
            
            if round_start_time is None:
                round_start_time = placed_at
                current_round_bets.append(bet_info)
            else:
                time_gap = (placed_at - round_start_time).total_seconds() / 3600
                
                if time_gap > ROUND_GAP_HOURS:
                    rounds_data.append({
                        'round_number': round_number,
                        'start_time': round_start_time,
                        'bets': current_round_bets
                    })
                    round_number += 1
                    round_start_time = placed_at
                    current_round_bets = []
            
            current_round_bets.append(bet_info)
        
        if current_round_bets:
            rounds_data.append({
                'round_number': round_number,
                'start_time': round_start_time,
                'bets': current_round_bets
            })
        
        print(f"Grouped into {len(rounds_data)} potential rounds")
        
        max_existing_round = s.execute(text("SELECT MAX(round_number) FROM bankroll_rounds")).scalar() or 0
        print(f"Max existing round number: {max_existing_round}")
        
        for rd in rounds_data:
            round_num = rd['round_number'] + max_existing_round
            start_time = rd['start_time']
            bet_list = rd['bets']
            
            if round_num <= max_existing_round:
                print(f"Skipping round {round_num} (already exists)")
                continue
            
            total_bets = len(bet_list)
            settled_bets = [b for b in bet_list if b['settled']]
            total_staked = sum(b['stake'] or 0 for b in bet_list)
            total_pnl = sum(b['pnl'] or 0 for b in settled_bets)
            total_wins = sum(1 for b in settled_bets if b['pnl'] and b['pnl'] > 0)
            
            end_time = bet_list[-1]['placed_at']
            roi_pct = (total_pnl / total_staked * 100) if total_staked > 0 else 0
            
            s.execute(text("""
                INSERT INTO bankroll_rounds 
                (round_number, started_at, ended_at, initial_bankroll, ending_balance, 
                 total_bets, total_wins, total_staked, total_pnl, roi_pct, is_active, reason)
                VALUES (:rn, :start, :end, :initial, :final, :tb, :tw, :ts, :tp, :roi, :active, :reason)
            """), {
                'rn': round_num,
                'start': start_time,
                'end': end_time,
                'initial': DEFAULT_INITIAL_BANKROLL,
                'final': DEFAULT_INITIAL_BANKROLL + total_pnl,
                'tb': total_bets,
                'tw': total_wins,
                'ts': total_staked,
                'tp': total_pnl,
                'roi': roi_pct,
                'active': 0,
                'reason': 'backfill'
            })
            
            new_round = s.execute(text("SELECT last_insert_rowid()")).scalar()
            
            for bet in bet_list:
                s.execute(text("""
                    UPDATE placed_bets SET round_id = :rid WHERE id = :bid
                """), {'rid': new_round, 'bid': bet['id']})
            
            print(f"Created round {round_num} with {total_bets} bets, P&L: {total_pnl:.2f}")
        
        s.commit()
        
        total_rounds = s.execute(text("SELECT COUNT(*) FROM bankroll_rounds")).scalar()
        bet_rounds_check = s.execute(text("SELECT COUNT(DISTINCT round_id) FROM placed_bets")).scalar()
        orphan_bets = s.execute(text("SELECT COUNT(*) FROM placed_bets WHERE round_id IS NULL")).scalar()
        
        print(f"\n=== Backfill Complete ===")
        print(f"Total rounds: {total_rounds}")
        print(f"Bets grouped into rounds: {bet_rounds_check}")
        print(f"Orphan bets: {orphan_bets}")
        
        rounds = s.execute(text("SELECT round_number, started_at, total_bets, total_pnl, roi_pct, is_active FROM bankroll_rounds ORDER BY round_number")).fetchall()
        print(f"\n=== All Rounds ===")
        for rn, started, tb, tp, roi, active in rounds:
            status = "ACTIVE" if active else "CLOSED"
            print(f"Round {rn}: {started}, {tb} bets, P&L: {tp:.2f}, ROI: {roi:.1f}%, Status: {status}")


if __name__ == '__main__':
    backfill_betting_rounds()