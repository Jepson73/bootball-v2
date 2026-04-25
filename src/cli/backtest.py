#!/usr/bin/env python3
"""
Backtest CLI - run historical simulations.

Usage:
    python -m src.cli.backtest --scenario baseline
    python -m src.cli.backtest --compare v12 vs v14
    python -m src.cli.backtest --from-date YYYY-MM-DD --to-date YYYY-MM-DD
    python -m src.cli.backtest --export-csv
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, "/opt/projects/bootball")

from src.backtesting.backtest_engine import BacktestEngine, run_scenario
from src.backtesting.scenarios import SCENARIOS, get_scenario, list_scenarios, compare_scenarios as run_compare
from src.backtesting.comparator import BacktestComparator


def format_metrics(result: dict) -> str:
    """Format backtest results for display."""
    wins = result.get("wins", 0)
    losses = result.get("losses", 0)
    
    lines = [
        "=" * 60,
        "BACKTEST RESULTS",
        "=" * 60,
        f"  Total Bets:     {result.get('settled_bets', 0)}",
        f"  Total PnL:      {result.get('total_pnl', 0):+,.2f}",
        f"  ROI:            {result.get('roi', 0):+.2f}%",
        f"  Win Rate:       {result.get('win_rate', 0):.1f}%",
        f"  Wins:           {wins} / {losses}",
        f"  Avg Stake:      {result.get('avg_stake', 0):.2f}",
        "",
        f"  Max Bankroll:   {result.get('max_bankroll', 0):,.2f}",
        f"  Min Bankroll:   {result.get('min_bankroll', 0):,.2f}",
        f"  Max Drawdown:   {result.get('max_drawdown', 0):.1f}%",
    ]
    
    if result.get("market_breakdown"):
        lines.append("")
        lines.append("  Market Breakdown:")
        for market, stats in result["market_breakdown"].items():
            lines.append(
                f"    {market}: {stats['bets']} bets, "
                f"PnL: {stats['pnl']:+,.2f}, ROI: {stats['roi']:+.2f}%"
            )
    
    return "\n".join(lines)


def format_comparison(comp: dict) -> str:
    """Format comparison results."""
    lines = [
        "=" * 60,
        "SCENARIO COMPARISON",
        "=" * 60,
        "",
        f"  {comp['name_a']}:",
        f"    ROI: {comp['metrics_a']['roi']:+.2f}%",
        f"    PnL: {comp['metrics_a']['total_pnl']:+,.2f}",
        f"    Bets: {comp['metrics_a']['settled_bets']}",
        "",
        f"  {comp['name_b']}:",
        f"    ROI: {comp['metrics_b']['roi']:+.2f}%",
        f"    PnL: {comp['metrics_b']['total_pnl']:+,.2f}",
        f"    Bets: {comp['metrics_b']['settled_bets']}",
        "",
        "-" * 60,
        "  DELTAS:",
        f"    ROI Delta:     {comp['deltas']['roi']:+.2f}%",
        f"    PnL Delta:     {comp['deltas']['pnl']:+,.2f}",
        f"    Bets Delta:    {comp['deltas']['bets']:+d}",
        f"    Drawdown Delta: {comp['deltas']['drawdown']:+.1f}%",
        "",
        f"  WINNER: {comp['winner']} (+{comp['win_margin']:.2f}%)",
    ]
    
    return "\n".join(lines)


def export_csv(result: dict, filename: str) -> None:
    """Export results to CSV."""
    rows = []
    
    if result.get("market_breakdown"):
        for market, stats in result["market_breakdown"].items():
            rows.append({
                "type": "market",
                "name": market,
                "bets": stats["bets"],
                "pnl": stats["pnl"],
                "roi": stats["roi"],
            })
    
    rows.append({
        "type": "summary",
        "name": "total",
        "bets": result["settled_bets"],
        "pnl": result["total_pnl"],
        "roi": result["roi"],
    })
    
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["type", "name", "bets", "pnl", "roi"])
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"Exported to {filename}")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest CLI - Run historical simulations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli.backtest --scenario baseline
  python -m src.cli.backtest --scenario conservative --days 90
  python -m src.cli.backtest --compare baseline vs aggressive
  python -m src.cli.backtest --list-scenarios
  python -m src.cli.backtest --config '{"min_ev_threshold": 0.08}' --export-json results.json
        """
    )
    
    parser.add_argument("--scenario", type=str, help="Run specific scenario")
    parser.add_argument("--list-scenarios", action="store_true", help="List available scenarios")
    parser.add_argument("--compare", type=str, help="Compare two scenarios (e.g., 'baseline vs aggressive')")
    parser.add_argument("--days", type=int, default=30, help="Number of days to backtest")
    parser.add_argument("--from-date", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to-date", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--config", type=str, help="JSON config override")
    parser.add_argument("--export-json", type=str, help="Export results to JSON")
    parser.add_argument("--export-csv", type=str, help="Export results to CSV")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output")
    
    args = parser.parse_args()
    
    # List scenarios
    if args.list_scenarios:
        print("Available scenarios:")
        for name, cfg in list_scenarios().items():
            print(f"  {name}: {cfg['description']}")
        return 0
    
    # Compare mode
    if args.compare:
        parts = args.compare.split(" vs ")
        if len(parts) != 2:
            print("Error: Use format 'scenario_a vs scenario_b'")
            return 1
        
        scenario_a, scenario_b = parts[0].strip(), parts[1].strip()
        
        if not args.quiet:
            print(f"Comparing {scenario_a} vs {scenario_b}...")
        
        result = run_compare(scenario_a, scenario_b, args.days)
        print(format_comparison(result))
        return 0
    
    # Single scenario mode
    scenario_name = args.scenario or "baseline"
    
    # Parse config if provided
    config = None
    if args.config:
        config = json.loads(args.config)
    
    # Build config
    if scenario_name in SCENARIOS:
        cfg = {**SCENARIOS[scenario_name], **(config or {})}
    else:
        cfg = config or {}
    
    # Run backtest
    if not args.quiet:
        print(f"Running backtest: {scenario_name}")
        print(f"Days: {args.days}")
    
    # Parse dates
    since = None
    until = None
    if args.from_date:
        since = datetime.strptime(args.from_date, "%Y-%m-%d")
    if args.to_date:
        until = datetime.strptime(args.to_date, "%Y-%m-%d")
    
    engine = BacktestEngine(cfg)
    result = engine.run_backtest(since=since, until=until)
    result["name"] = scenario_name
    
    # Output
    if not args.quiet:
        print(format_metrics(result))
    
    # Export JSON
    if args.export_json:
        with open(args.export_json, "w") as f:
            json.dump(result, f, indent=2, default=str)
        if not args.quiet:
            print(f"Exported to {args.export_json}")
    
    # Export CSV
    if args.export_csv:
        export_csv(result, args.export_csv)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
