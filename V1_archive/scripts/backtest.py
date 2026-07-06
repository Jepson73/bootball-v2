#!/usr/bin/env python3
# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: historical backtesting framework (EV, Kelly, per-market/league analysis)
"""
scripts/backtest.py

Comprehensive historical backtesting for all betting markets.

Usage:
    python scripts/backtest.py                    # Full backtest (all markets)
    python scripts/backtest.py --market btts     # Single market
    python scripts/backtest.py --league 78       # Single league
    python scripts/backtest.py --ev 0.05         # Custom EV threshold
    python scripts/backtest.py --kelly 0.25      # Custom Kelly fraction
    python scripts/backtest.py --min-odds 1.5    # Minimum odds filter
    python scripts/backtest.py --seasons 2024    # Specific season
"""
import argparse
import logging
import sys
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sqlalchemy import select, func

from config.leagues import LEAGUES
from src.storage.db import get_session
from src.storage.models import Fixture
from src.prediction.lib.prediction import get_model_prediction, MARKET_OUTCOMES
from src.prediction.lib.ev import expected_value
from src.betting.kelly import fractional_kelly
from src.evaluation.sharpe import risk_metrics_from_pnl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class BetRecord:
    fixture_id: int
    league_id: int
    market: str
    outcome: str
    our_prob: float
    odds: float
    ev: float
    kelly_frac: float
    stake: float
    actual_result: str
    won: bool
    pnl: float


@dataclass
class MarketResult:
    market: str
    n_bets: int
    n_wins: int
    win_rate: float
    total_staked: float
    total_returned: float
    profit: float
    roi_pct: float
    avg_odds: float
    avg_ev: float
    sharpe: float
    max_drawdown: float


@dataclass
class LeagueResult:
    league_id: int
    league_name: str
    market: str
    n_bets: int
    n_wins: int
    win_rate: float
    profit: float
    roi_pct: float


@dataclass
class BacktestReport:
    start_date: str
    end_date: str
    n_fixtures: int
    markets: dict[str, MarketResult]
    by_league: list[LeagueResult]
    all_bets: list[BetRecord] = field(default_factory=list)
    overall_profit: float = 0.0
    overall_roi: float = 0.0


def get_market_result(fixture: Fixture, market: str) -> Optional[str]:
    """Get actual result for a market from fixture goals."""
    if fixture.goals_home is None or fixture.odds is None:
        return None

    if market == "h2h":
        if fixture.goals_home > fixture.goals_away:
            return "1"
        elif fixture.goals_home < fixture.goals_away:
            return "2"
        else:
            return "X"

    elif market == "btts":
        home_scored = fixture.goals_home > 0
        away_scored = fixture.goals_away > 0
        return "Yes" if (home_scored and away_scored) else "No"

    elif market in ("ou25", "ou15"):
        total = fixture.goals_home + fixture.goals_away
        threshold = 2.5 if market == "ou25" else 1.5
        return "Over" if total > threshold else "Under"

    return None


def get_market_result_from_dict(fixture: dict, market: str) -> Optional[str]:
    """Get actual result for a market from fixture dict."""
    goals_home = fixture.get("goals_home")
    goals_away = fixture.get("goals_away")

    if goals_home is None or goals_away is None:
        return None

    if market == "h2h":
        if goals_home > goals_away:
            return "1"
        elif goals_home < goals_away:
            return "2"
        else:
            return "X"

    elif market == "btts":
        return "Yes" if (goals_home > 0 and goals_away > 0) else "No"

    elif market in ("ou25", "ou15"):
        total = goals_home + goals_away
        threshold = 2.5 if market == "ou25" else 1.5
        return "Over" if total > threshold else "Under"

    return None


def get_odds_for_market_from_dict(fixture: dict, market: str, outcome: str) -> Optional[float]:
    """Get simulated odds based on league historical rates."""
    league_id = fixture.get("league_id")
    league_data = LEAGUES.get(league_id, {})

    base_rates = {
        "h2h": {
            "1": 0.45,
            "X": 0.27,
            "2": 0.28,
        },
        "btts": {
            "Yes": league_data.get("btts", 50) / 100,
            "No": 1 - league_data.get("btts", 50) / 100,
        },
        "ou25": {
            "Over": league_data.get("over25", 50) / 100,
            "Under": league_data.get("under25", 50) / 100,
        },
        "ou15": {
            "Over": 0.70,
            "Under": 0.30,
        },
    }

    base_rates = base_rates.get(market, {})
    base_prob = base_rates.get(outcome, 0.5)

    variance = np.random.uniform(-0.05, 0.05)
    adj_prob = np.clip(base_prob + variance, 0.05, 0.95)

    return 1.0 / adj_prob * np.random.uniform(0.90, 0.96)


def simulate_market(
    fixtures: list,
    market: str,
    ev_threshold: float = 0.05,
    kelly_fraction: float = 0.25,
    min_odds: float = 1.0,
    max_odds: float = 100.0,
    starting_bankroll: float = 1000.0,
) -> tuple[list[BetRecord], MarketResult]:
    """
    Simulate betting on a market across fixtures.

    Returns (bet_records, market_result)
    """
    bets = []
    pnl_history = []

    for f in fixtures:
        try:
            probs = get_model_prediction(market, f.home_team_id, f.away_team_id)
        except Exception as e:
            continue

        actual_result = get_market_result_from_dict(f, market)
        if actual_result is None:
            continue

        for outcome, our_prob in probs.items():
            odd = get_odds_for_market_from_dict(f, market, outcome)
            if odd is None or odd < min_odds or odd > max_odds:
                continue

            ev = expected_value(our_prob, odd)
            if ev < ev_threshold:
                continue

            kelly = fractional_kelly(our_prob, odd, kelly_fraction)
            stake = kelly * starting_bankroll * 0.1  # Use 10% of bankroll per bet

            won = outcome == actual_result
            pnl = (odd - 1) * stake if won else -stake

            bets.append(BetRecord(
                fixture_id=f["id"],
                league_id=f["league_id"],
                market=market,
                outcome=outcome,
                our_prob=our_prob,
                odds=odd,
                ev=ev,
                kelly_frac=kelly,
                stake=stake,
                actual_result=actual_result,
                won=won,
                pnl=pnl,
            ))
            pnl_history.append(pnl)

    if not bets:
        return [], MarketResult(
            market=market, n_bets=0, n_wins=0, win_rate=0.0,
            total_staked=0, total_returned=0, profit=0, roi_pct=0,
            avg_odds=0, avg_ev=0, sharpe=0, max_drawdown=0
        )

    n_wins = sum(1 for b in bets if b.won)
    total_staked = sum(b.stake for b in bets)
    total_returned = sum(b.stake * b.odds if b.won else 0 for b in bets)
    profit = total_returned - total_staked
    roi_pct = (profit / total_staked * 100) if total_staked > 0 else 0

    risk = risk_metrics_from_pnl(pnl_history)

    return bets, MarketResult(
        market=market,
        n_bets=len(bets),
        n_wins=n_wins,
        win_rate=n_wins / len(bets),
        total_staked=total_staked,
        total_returned=total_returned,
        profit=profit,
        roi_pct=roi_pct,
        avg_odds=np.mean([b.odds for b in bets]),
        avg_ev=np.mean([b.ev for b in bets]) * 100,
        sharpe=risk.sharpe_ratio,
        max_drawdown=risk.max_drawdown_pct * 100,
    )


def simulate_market_from_dict(
    fixtures: list[dict],
    market: str,
    ev_threshold: float = 0.05,
    kelly_fraction: float = 0.25,
    min_odds: float = 1.0,
    max_odds: float = 100.0,
    starting_bankroll: float = 1000.0,
) -> tuple[list[BetRecord], MarketResult]:
    """Simulate betting using dict fixtures (for backtesting)."""
    bets = []
    pnl_history = []

    for f in fixtures:
        try:
            probs = get_model_prediction(market, f["home_team_id"], f["away_team_id"])
        except Exception:
            continue

        actual_result = get_market_result_from_dict(f, market)
        if actual_result is None:
            continue

        for outcome, our_prob in probs.items():
            # Skip if model is uncertain (returns ~0.5)
            if abs(our_prob - 0.5) < 0.05:
                continue

            odd = get_odds_for_market_from_dict(f, market, outcome)
            if odd is None or odd < min_odds or odd > max_odds:
                continue

            ev = expected_value(our_prob, odd)
            if ev < ev_threshold:
                continue

            kelly = fractional_kelly(our_prob, odd, kelly_fraction)
            stake = kelly * starting_bankroll * 0.1

            won = outcome == actual_result
            pnl = (odd - 1) * stake if won else -stake

            bets.append(BetRecord(
                fixture_id=f["id"],
                league_id=f["league_id"],
                market=market,
                outcome=outcome,
                our_prob=our_prob,
                odds=odd,
                ev=ev,
                kelly_frac=kelly,
                stake=stake,
                actual_result=actual_result,
                won=won,
                pnl=pnl,
            ))
            pnl_history.append(pnl)

    if not bets:
        return [], MarketResult(
            market=market, n_bets=0, n_wins=0, win_rate=0.0,
            total_staked=0, total_returned=0, profit=0, roi_pct=0,
            avg_odds=0, avg_ev=0, sharpe=0, max_drawdown=0
        )

    n_wins = sum(1 for b in bets if b.won)
    total_staked = sum(b.stake for b in bets)
    total_returned = sum(b.stake * b.odds if b.won else 0 for b in bets)
    profit = total_returned - total_staked
    roi_pct = (profit / total_staked * 100) if total_staked > 0 else 0

    risk = risk_metrics_from_pnl(pnl_history)

    return bets, MarketResult(
        market=market,
        n_bets=len(bets),
        n_wins=n_wins,
        win_rate=n_wins / len(bets),
        total_staked=total_staked,
        total_returned=total_returned,
        profit=profit,
        roi_pct=roi_pct,
        avg_odds=np.mean([b.odds for b in bets]),
        avg_ev=np.mean([b.ev for b in bets]) * 100,
        sharpe=risk.sharpe_ratio,
        max_drawdown=risk.max_drawdown_pct * 100,
    )


def get_odds_for_market(fixture: Fixture, market: str, outcome: str) -> Optional[float]:
    """Get simulated odds based on historical hit rates."""
    league_data = LEAGUES.get(fixture.league_id, {})

    base_rates = {
        "h2h": {
            "1": league_data.get("btts", 50) / 100,  # Use as proxy
            "X": 0.25,
            "2": 1 - league_data.get("btts", 50) / 100 - 0.25,
        },
        "btts": {
            "Yes": league_data.get("btts", 50) / 100,
            "No": 1 - league_data.get("btts", 50) / 100,
        },
        "ou25": {
            "Over": league_data.get("over25", 50) / 100,
            "Under": league_data.get("under25", 50) / 100,
        },
        "ou15": {
            "Over": 0.70,
            "Under": 0.30,
        },
    }

    base_rates = base_rates.get(market, {})
    base_prob = base_rates.get(outcome, 0.5)

    # Add some variance (simulate bookmaker margin)
    variance = np.random.uniform(-0.03, 0.03)
    adj_prob = np.clip(base_prob + variance, 0.01, 0.99)

    return 1.0 / adj_prob * np.random.uniform(0.92, 0.97)


def run_backtest(
    markets: list[str] | None = None,
    league_ids: list[int] | None = None,
    seasons: list[int] | None = None,
    ev_threshold: float = 0.05,
    kelly_fraction: float = 0.25,
    min_odds: float = 1.5,
    max_odds: float = 10.0,
) -> BacktestReport:
    """Run full backtest across specified markets and leagues."""
    if markets is None:
        markets = ["h2h", "btts", "ou25", "ou15"]

    with get_session() as s:
        query = select(Fixture).where(
            Fixture.status == "FT",
            Fixture.goals_home != None,
        )

        if league_ids:
            query = query.where(Fixture.league_id.in_(league_ids))
        if seasons:
            query = query.where(Fixture.season.in_(seasons))

        fixtures_raw = s.execute(query).scalars().all()

        # Extract data within session to avoid DetachedInstanceError
        fixtures = []
        start_dates = []
        end_dates = []
        for f in fixtures_raw:
            fixtures.append({
                "id": f.id,
                "league_id": f.league_id,
                "home_team_id": f.home_team_id,
                "away_team_id": f.away_team_id,
                "date": f.date,
                "goals_home": f.goals_home,
                "goals_away": f.goals_away,
                "status": f.status,
            })
            if f.date:
                start_dates.append(f.date)
                end_dates.append(f.date)

    if not fixtures:
        logger.warning("No fixtures found for backtest")
        return BacktestReport(
            start_date="", end_date="",
            n_fixtures=0, markets={}, by_league=[]
        )

    logger.info(f"Backtesting {len(fixtures)} fixtures across {len(markets)} markets")

    all_bets = []
    market_results = {}

    for market in markets:
        logger.info(f"  Simulating {market}...")
        bets, result = simulate_market_from_dict(
            fixtures, market, ev_threshold, kelly_fraction,
            min_odds, max_odds
        )
        all_bets.extend(bets)
        market_results[market] = result
        logger.info(f"    {result.n_bets} bets, ROI: {result.roi_pct:+.1f}%")

    # Calculate league-level stats
    league_results = []
    if all_bets:
        by_league_market = {}
        for bet in all_bets:
            key = (bet.league_id, bet.market)
            if key not in by_league_market:
                by_league_market[key] = []
            by_league_market[key].append(bet)

        for (lid, market), bets in by_league_market.items():
            n_wins = sum(1 for b in bets if b.won)
            total_staked = sum(b.stake for b in bets)
            total_returned = sum(b.stake * b.odds if b.won else 0 for b in bets)
            profit = total_returned - total_staked
            roi = (profit / total_staked * 100) if total_staked > 0 else 0

            league_results.append(LeagueResult(
                league_id=lid,
                league_name=LEAGUES.get(lid, {}).get("name", str(lid)),
                market=market,
                n_bets=len(bets),
                n_wins=n_wins,
                win_rate=n_wins / len(bets) if bets else 0,
                profit=profit,
                roi_pct=roi,
            ))

    # Overall stats
    total_staked = sum(m.total_staked for m in market_results.values())
    total_returned = sum(m.total_returned for m in market_results.values())
    overall_profit = total_returned - total_staked
    overall_roi = (overall_profit / total_staked * 100) if total_staked > 0 else 0

    start_str = min(start_dates).strftime("%Y-%m-%d") if start_dates else ""
    end_str = max(end_dates).strftime("%Y-%m-%d") if end_dates else ""

    return BacktestReport(
        start_date=start_str,
        end_date=end_str,
        n_fixtures=len(fixtures),
        markets=market_results,
        by_league=sorted(league_results, key=lambda x: x.profit, reverse=True),
        all_bets=all_bets,
        overall_profit=overall_profit,
        overall_roi=overall_roi,
    )


def print_report(report: BacktestReport):
    """Print backtest report."""
    print("\n" + "=" * 70)
    print("BACKTEST REPORT")
    print("=" * 70)
    print(f"Period: {report.start_date} to {report.end_date}")
    print(f"Fixtures analyzed: {report.n_fixtures}")
    print()

    print("-" * 70)
    print(f"{'MARKET':<12} {'BETS':<8} {'WIN%':<8} {'STAKED':<12} {'ROI%':<10} {'PROFIT':<12}")
    print("-" * 70)

    for market, result in report.markets.items():
        if result.n_bets > 0:
            print(
                f"{market:<12} {result.n_bets:<8} {result.win_rate*100:>5.1f}%   "
                f"${result.total_staked:<11.1f} {result.roi_pct:>+8.1f}%   "
                f"${result.profit:>+10.1f}"
            )

    print("-" * 70)
    print(
        f"{'TOTAL':<12} {sum(r.n_bets for r in report.markets.values()):<8} "
        f"{sum(r.n_wins for r in report.markets.values())/max(sum(r.n_bets for r in report.markets.values()),1)*100:>5.1f}%   "
        f"${sum(r.total_staked for r in report.markets.values()):<11.1f} "
        f"{report.overall_roi:>+8.1f}%   "
        f"${report.overall_profit:>+10.1f}"
    )
    print("=" * 70)

    if report.by_league:
        print("\nTOP LEAGUES BY PROFIT:")
        print("-" * 70)
        for league in report.by_league[:10]:
            if league.n_bets > 0:
                print(
                    f"  {league.league_name:<25} {league.market:<8} "
                    f"{league.n_bets:>4} bets, ROI: {league.roi_pct:>+6.1f}%, "
                    f"Profit: ${league.profit:>+8.1f}"
                )


def compute_league_weights_from_results() -> dict:
    """
    Compute league weights from settled prediction records.
    
    Analyzes historical predictions vs actual outcomes to derive
    per-league, per-market performance weights.
    
    Returns dict with league_id -> {btts_weight, over25_weight, under25_weight}
    """
    from src.storage.db import get_session
    from src.storage.models import PredictionRecord
    
    with get_session() as s:
        records = s.execute(
            select(PredictionRecord)
            .where(PredictionRecord.settled == True)
        ).scalars().all()
    
    if len(records) < 100:
        print(f"Warning: Only {len(records)} settled predictions. Need more data.")
        return {}
    
    league_stats = {}
    for rec in records:
        lid = rec.league_id
        if lid not in league_stats:
            league_stats[lid] = {
                'btts_total': 0, 'btts_correct': 0,
                'ou25_total': 0, 'ou25_correct': 0,
                'ou15_total': 0, 'ou15_correct': 0,
            }
        
        market = rec.market or ''
        correct = rec.correct or False
        
        if 'btts' in market.lower():
            league_stats[lid]['btts_total'] += 1
            if correct:
                league_stats[lid]['btts_correct'] += 1
        elif '2.5' in market or 'over' in market.lower():
            league_stats[lid]['ou25_total'] += 1
            if correct:
                league_stats[lid]['ou25_correct'] += 1
        elif '1.5' in market:
            league_stats[lid]['ou15_total'] += 1
            if correct:
                league_stats[lid]['ou15_correct'] += 1
    
    baseline_btts = 0.50
    baseline_ou25 = 0.50
    
    weights = {}
    for lid, stats in league_stats.items():
        if stats['btts_total'] >= 20:
            btts_acc = stats['btts_correct'] / stats['btts_total']
            btts_weight = btts_acc / baseline_btts
            btts_weight = max(0.5, min(2.0, btts_weight))
        else:
            btts_weight = 1.0
        
        if stats['ou25_total'] >= 20:
            ou25_acc = stats['ou25_correct'] / stats['ou25_total']
            over25_weight = ou25_acc / baseline_ou25
            over25_weight = max(0.5, min(2.0, over25_weight))
            under25_weight = 1.0 / over25_weight if over25_weight > 0 else 1.0
        else:
            over25_weight = 1.0
            under25_weight = 1.0
        
        weights[lid] = {
            'btts_weight': round(btts_weight, 2),
            'over25_weight': round(over25_weight, 2),
            'under25_weight': round(under25_weight, 2),
            'sample_size': stats['btts_total'] + stats['ou25_total'],
        }
    
    return weights


def print_league_weights_report(weights: dict):
    """Print league weights in config format."""
    print("\n" + "=" * 60)
    print("COMPUTED LEAGUE WEIGHTS FROM SETTLED PREDICTIONS")
    print("=" * 60)
    print("""
# Add these to config/leagues.py in each league entry
# Generated by compute_league_weights_from_results()
# Format: league_id: {'btts_weight': X.X, 'over25_weight': X.X, 'under25_weight': X.X}
""")
    
    for lid in sorted(weights.keys()):
        w = weights[lid]
        name = LEAGUES.get(lid, {}).get('name', f'League {lid}')
        print(f"# {name} (ID: {lid}, n={w['sample_size']})")
        print(f"{lid}: btts_weight={w['btts_weight']}, over25_weight={w['over25_weight']}, under25_weight={w['under25_weight']}")


def main():
    parser = argparse.ArgumentParser(description="Historical betting backtest")
    parser.add_argument("--market", type=str, help="Single market (h2h, btts, ou25, ou15)")
    parser.add_argument("--markets", type=str, help="Comma-separated markets")
    parser.add_argument("--league", type=int, help="Single league ID")
    parser.add_argument("--leagues", type=str, help="Comma-separated league IDs")
    parser.add_argument("--seasons", type=str, help="Comma-separated seasons")
    parser.add_argument("--ev", type=float, default=0.05, help="EV threshold (default: 0.05)")
    parser.add_argument("--kelly", type=float, default=0.25, help="Kelly fraction (default: 0.25)")
    parser.add_argument("--min-odds", type=float, default=1.5, help="Minimum odds (default: 1.5)")
    parser.add_argument("--max-odds", type=float, default=10.0, help="Maximum odds (default: 10.0)")
    parser.add_argument("--compute-weights", action="store_true", help="Compute league weights from settled predictions")
    args = parser.parse_args()

    markets = None
    if args.market:
        markets = [args.market]
    elif args.markets:
        markets = [m.strip() for m in args.markets.split(",")]

    league_ids = None
    if args.league:
        league_ids = [args.league]
    elif args.leagues:
        league_ids = [int(l) for l in args.leagues.split(",")]

    seasons = None
    if args.seasons:
        seasons = [int(s) for s in args.seasons.split(",")]

    if args.compute_weights:
        weights = compute_league_weights_from_results()
        print_league_weights_report(weights)
        return

    report = run_backtest(
        markets=markets,
        league_ids=league_ids,
        seasons=seasons,
        ev_threshold=args.ev,
        kelly_fraction=args.kelly,
        min_odds=args.min_odds,
        max_odds=args.max_odds,
    )

    print_report(report)


if __name__ == "__main__":
    main()
