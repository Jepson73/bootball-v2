# src/evaluation/backtesting.py - Historical ROI simulation
"""
Backtesting framework for simulating betting strategy performance.

Computes:
- Total bets placed
- Win rate
- ROI percentage
- Profit/Loss
- By outcome type (home/draw/away)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture, ValueBet


@dataclass
class BacktestResult:
    """Summary of backtest results."""
    n_bets: int
    n_wins: int
    win_rate: float
    total_staked: float
    total_returned: float
    profit: float
    roi_pct: float
    by_outcome: dict[str, dict]


def simulate_bets(
    fixtures: list[Fixture],
    model,  # Any model with predict_proba(home_id, away_id) -> (pH, pD, pA)
    odds_provider: Optional[callable] = None,
    ev_threshold: float = 0.05,
    kelly_fraction: float = 0.25,
    bankroll: float = 1000.0,
) -> BacktestResult:
    """
    Simulate betting on fixtures using a model and optional odds.
    
    Args:
        fixtures: List of completed fixtures with known outcomes
        model: Model with predict_proba(home_id, away_id) method
        odds_provider: Function(home_id, away_id) -> (odd_H, odd_D, odd_A)
                      If None, uses model probabilities for "fair" odds
        ev_threshold: Minimum EV to place a bet
        kelly_fraction: Fractional Kelly to use
        bankroll: Starting bankroll
    
    Returns:
        BacktestResult with performance metrics
    """
    n_bets = 0
    n_wins = 0
    total_staked = 0.0
    total_returned = 0.0
    
    by_outcome = {
        "H": {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
        "D": {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
        "A": {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0},
    }
    
    for f in fixtures:
        if f.goals_home is None:
            continue
        
        # Get model probabilities
        probs = model.predict_proba(f.home_team_id, f.away_team_id)
        pH, pD, pA = probs
        
        # Get odds
        if odds_provider:
            odds = odds_provider(f.home_team_id, f.away_team_id)
            if odds is None:
                continue
            odd_H, odd_D, odd_A = odds
        else:
            # Use fair odds from model (1/p - 5% margin)
            odd_H = 1 / pH * 0.95 if pH > 0.01 else None
            odd_D = 1 / pD * 0.95 if pD > 0.01 else None
            odd_A = 1 / pA * 0.95 if pA > 0.01 else None
        
        # Check each outcome for value
        outcomes = [("H", pH, odd_H), ("D", pD, odd_D), ("A", pA, odd_A)]
        
        for outcome, prob, odd in outcomes:
            if odd is None or odd <= 1.0:
                continue
            
            # Calculate EV
            ev = prob * odd - 1.0
            
            if ev >= ev_threshold:
                # Calculate Kelly stake
                b = odd - 1
                q = 1 - prob
                kelly = max((prob * b - q) / b, 0) * kelly_fraction
                
                stake = min(kelly * bankroll, bankroll * 0.05)  # Max 5%
                
                # Did we win?
                actual = "H" if f.goals_home > f.goals_away else ("D" if f.goals_home == f.goals_away else "A")
                won = outcome == actual
                
                n_bets += 1
                total_staked += stake
                
                if won:
                    n_wins += 1
                    payout = stake * odd
                    total_returned += payout
                else:
                    total_returned += 0
                
                by_outcome[outcome]["bets"] += 1
                by_outcome[outcome]["staked"] += stake
                if won:
                    by_outcome[outcome]["wins"] += 1
                    by_outcome[outcome]["returned"] += stake * odd
    
    profit = total_returned - total_staked
    roi_pct = (profit / total_staked * 100) if total_staked > 0 else 0.0
    
    # Calculate by-outcome stats
    for o in by_outcome:
        if by_outcome[o]["staked"] > 0:
            by_outcome[o]["roi"] = ((by_outcome[o]["returned"] - by_outcome[o]["staked"]) / 
                                     by_outcome[o]["staked"] * 100)
            by_outcome[o]["win_rate"] = by_outcome[o]["wins"] / by_outcome[o]["bets"]
    
    return BacktestResult(
        n_bets=n_bets,
        n_wins=n_wins,
        win_rate=n_wins / n_bets if n_bets > 0 else 0,
        total_staked=total_staked,
        total_returned=total_returned,
        profit=profit,
        roi_pct=roi_pct,
        by_outcome=by_outcome,
    )


def print_backtest(result: BacktestResult) -> None:
    """Print backtest results in readable format."""
    print(f"\n{'='*50}")
    print("BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"Total Bets:      {result.n_bets}")
    print(f"Wins:            {result.n_wins}")
    print(f"Win Rate:        {result.win_rate*100:.1f}%")
    print(f"Total Staked:    ${result.total_staked:.2f}")
    print(f"Total Returned:  ${result.total_returned:.2f}")
    print(f"Profit/Loss:     ${result.profit:.2f}")
    print(f"ROI:             {result.roi_pct:.2f}%")
    
    print(f"\nBy Outcome:")
    print(f"{'Outcome':<8} {'Bets':<6} {'Wins':<6} {'Win%':<8} {'Staked':<10} {'ROI%':<8}")
    print("-" * 56)
    for o in ["H", "D", "A"]:
        s = result.by_outcome[o]
        if s["bets"] > 0:
            print(f"{o:<8} {s['bets']:<6} {s['wins']:<6} {s.get('win_rate', 0)*100:.1f}%   "
                  f"${s['staked']:<9.2f} {s.get('roi', 0):.1f}%")
    print(f"{'='*50}\n")