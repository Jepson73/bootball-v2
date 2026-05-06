#!/usr/bin/env python3
"""
Event Replay CLI.

Usage:
    python -m src.cli.event_replay --run-id <id>
    python -m src.cli.event_replay --from-date YYYY-MM-DD
    python -m src.cli.event_replay --last 1000
    python -m src.cli.event_replay --compare-run <run_id>
    python -m src.cli.event_replay --verbose

This is a READ-ONLY debugging tool:
- Does NOT modify state
- Does NOT write to DB
- Does NOT trigger consumers
- ONLY replays events for inspection
"""

import argparse
import sys
from pathlib import Path
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

# Ensure path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.events.event_store import get_event_store
from src.state.reconstructor import StateReconstructor
from src.state.models import BettingState, HealthState, ModelState


def format_betting_state(state: BettingState, verbose: bool = False) -> str:
    """Format betting state for display."""
    lines = [
        "=== BETTING STATE ===",
        f"  Balance:     {state.balance:,.2f}",
        f"  ROI:         {state.roi:+.2f}%",
        f"  Pending:     {state.pending_count} bets ({state.pending_stake:,.2f})",
        f"  Wins:        {state.wins}",
        f"  Losses:      {state.losses}",
        f"  Total PnL:  {state.total_pnl:+,.2f}",
    ]
    
    if state.pending_count > 0 and verbose:
        lines.append("  Pending Bets:")
        for bet in state.bets[:10]:
            if not bet.get("settled"):
                lines.append(
                    f"    - {bet.get('market')}: {bet.get('outcome')} @ {bet.get('odds')} "
                    f"(EV: {bet.get('ev', 0):.2%})"
                )
    
    return "\n".join(lines)


def format_health_state(state: HealthState, verbose: bool = False) -> str:
    """Format health state for display."""
    lines = [
        "=== HEALTH STATE ===",
        f"  Health Score:    {state.health_score:.1f}",
        f"  Error Rate:      {state.error_rate:.2%}",
        f"  Avg Duration:   {state.avg_duration:.1f}s",
        f"  Total Runs:      {state.total_runs}",
        f"  Failed Runs:    {state.failed_runs}",
        f"  Active Runs:    {len(state.active_runs)}",
    ]
    
    if state.active_runs and verbose:
        lines.append("  Active Runs:")
        for run in state.active_runs:
            lines.append(f"    - {run.get('run_id')} ({run.get('mode')})")
    
    if verbose and state.completed_runs:
        lines.append("  Recent Runs:")
        for run in state.completed_runs[-5:]:
            lines.append(
                f"    - {run.get('run_id')}: {run.get('mode')} "
                f"({run.get('duration', 0):.1f}s, {run.get('errors', 0)} errors)"
            )
    
    return "\n".join(lines)


def format_model_state(state: ModelState, verbose: bool = False) -> str:
    """Format model state for display."""
    lines = [
        "=== MODEL STATE ===",
    ]
    
    if state.market_performance:
        for market, entries in state.market_performance.items():
            if not entries:
                continue
            latest = entries[-1]
            lines.append(f"  {market}:")
            lines.append(f"    Latest version: {latest.get('version')}")
            if latest.get('brier_score'):
                lines.append(f"    Brier Score:    {latest.get('brier_score'):.4f}")
            if latest.get('ece'):
                lines.append(f"    ECE:            {latest.get('ece'):.4f}")
    
    if state.calibration_drift and verbose:
        lines.append("  Calibration Drift:")
        for market, entries in state.calibration_drift.items():
            if len(entries) >= 2:
                latest = entries[-1]
                oldest = entries[0]
                drift = latest.get('ece', 0) - oldest.get('ece', 0)
                lines.append(f"    {market}: {drift:+.4f}")
    
    if not lines[1:]:
        lines.append("  (no model data)")
    
    return "\n".join(lines)


def load_events(
    run_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    last: Optional[int] = None,
    event_types: Optional[list[str]] = None
) -> list[dict]:
    """Load events from event store with filters."""
    store = get_event_store()
    
    since = None
    if from_date:
        since = datetime.fromisoformat(from_date)
    
    until = None
    if to_date:
        until = datetime.fromisoformat(to_date)
    
    events = store.get_events(
        run_id=run_id,
        since=since,
        event_types=event_types
    )
    
    # Filter by until if needed
    if until:
        events = [e for e in events if e.get("timestamp", "") <= until.isoformat()]
    
    # Sort by timestamp
    events = sorted(events, key=lambda e: e.get("timestamp", ""))
    
    # Apply limit
    if last:
        events = events[-last:]
    
    return events


def replay_events(events: list[dict], verbose: bool = False) -> dict:
    """Replay events and return final states."""
    reconstructor = StateReconstructor()
    
    for i, event in enumerate(events):
        event_type = event.get("event_type", "unknown")
        timestamp = event.get("timestamp", "")
        run_id = event.get("run_id", "")
        
        # Progress indicator
        progress = f"[{i+1}/{len(events)}]"
        
        # Event-specific summary
        if event_type == "bets_generated":
            bets = event.get("bets", [])
            print(f"{progress} {event_type}: +{len(bets)} bets (run={run_id[:8]})")
            if verbose:
                for bet in bets[:3]:
                    print(f"       - {bet.get('market')}: {bet.get('outcome')} @ {bet.get('odds')} (EV: {bet.get('ev', 0):.2%})")
        
        elif event_type == "bets_settled" or event_type == "bet_settled":
            settled = event.get("settled_count", 0)
            pnl = event.get("pnl_total", 0)
            wins = event.get("wins", 0)
            losses = event.get("losses", 0)
            print(f"{progress} {event_type}: {settled} settled, PnL: {pnl:+.2f}, W/L: {wins}/{losses}")
        
        elif event_type == "run_started":
            print(f"{progress} {event_type}: run_id={run_id[:8]}, mode={event.get('mode')}")
        
        elif event_type == "run_finished":
            mode = event.get("mode", "unknown")
            duration = event.get("duration", 0)
            bets = event.get("total_bets", 0)
            errors = len(event.get("errors", []))
            print(f"{progress} {event_type}: run_id={run_id[:8]}, mode={mode}, bets={bets}, duration={duration:.1f}s, errors={errors}")
        
        elif event_type == "predictions_generated":
            fixtures = event.get("fixture_count", 0)
            predictions = event.get("prediction_count", 0)
            print(f"{progress} {event_type}: {fixtures} fixtures, {predictions} predictions")
        
        elif event_type == "model_trend":
            market = event.get("market", "unknown")
            version = event.get("model_version", "unknown")
            print(f"{progress} {event_type}: {market} v{version}")
        
        elif event_type == "health_update":
            score = event.get("health_score", 0)
            print(f"{progress} {event_type}: health_score={score:.1f}")
        
        else:
            print(f"{progress} {event_type}")
        
        if verbose:
            # Print full payload
            payload = {k: v for k, v in event.items() if k not in ("event_type", "timestamp")}
            if payload:
                import json
                print(f"       payload: {json.dumps(payload)[:200]}")
    
    # Final reconstruction
    system = reconstructor.rebuild_from_events(events)
    
    return system


def compare_runs(events1: list[dict], events2: list[dict]) -> None:
    """Compare two runs and show differences."""
    print("\n" + "="*50)
    print("COMPARING RUNS")
    print("="*50)
    
    # Replay both
    system1 = replay_events(events1)
    system2 = replay_events(events2)
    
    print("\n--- BETTING COMPARISON ---")
    b1, b2 = system1.betting, system2.betting
    print(f"  Balance:      {b1.balance:,.2f} → {b2.balance:,.2f} ({b2.balance - b1.balance:+,.2f})")
    print(f"  ROI:          {b1.roi:+.2f}% → {b2.roi:+.2f}% ({b2.roi - b1.roi:+.2f}%)")
    print(f"  Pending:      {b1.pending_count} → {b2.pending_count}")
    print(f"  Wins/Losses:  {b1.wins}/{b1.losses} → {b2.wins}/{b2.losses}")
    print(f"  Total PnL:    {b1.total_pnl:+,.2f} → {b2.total_pnl:+,.2f}")
    
    print("\n--- HEALTH COMPARISON ---")
    h1, h2 = system1.health, system2.health
    print(f"  Health Score: {h1.health_score:.1f} → {h2.health_score:.1f} ({h2.health_score - h1.health_score:+.1f})")
    print(f"  Error Rate:   {h1.error_rate:.2%} → {h2.error_rate:.2%}")
    print(f"  Avg Duration: {h1.avg_duration:.1f}s → {h2.avg_duration:.1f}s")
    print(f"  Total Runs:   {h1.total_runs} → {h2.total_runs}")
    
    print("\n--- MODEL COMPARISON ---")
    m1, m2 = system1.model, system2.model
    for market in set(list(m1.market_performance.keys()) + list(m2.market_performance.keys())):
        v1 = m1.market_performance.get(market, [])
        v2 = m2.market_performance.get(market, [])
        if v1 or v2:
            print(f"  {market}: {len(v1)} → {len(v2)} entries")


def main():
    parser = argparse.ArgumentParser(
        description="Event Replay CLI - Replay and analyze event history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Replay modes
  python -m src.cli.event_replay                    # Replay last 100 events
  python -m src.cli.event_replay --run-id abc123   # Replay specific run
  python -m src.cli.event_replay --from-date 2025-01-01 --to-date 2025-01-31
  python -m src.cli.event_replay --last 1000
  python -m src.cli.event_replay --market h2h      # Filter by market
  
  # Output modes
  python -m src.cli.event_replay --output json --export results.json
  python -m src.cli.event_replay --output csv --export events.csv
  
  # Compare modes
  python -m src.cli.event_replay --compare-run def456 --run-id abc123
  python -m src.cli.event_replay --compare-model v12 --model-version v14
  
  # Debug
  python -m src.cli.event_replay --verbose
  python -m src.cli.event_replay --debug
        """
    )
    
    # Replay filters
    parser.add_argument("--run-id", type=str, help="Filter by run_id")
    parser.add_argument("--from-date", type=str, help="From date (YYYY-MM-DD)")
    parser.add_argument("--to-date", type=str, help="To date (YYYY-MM-DD)")
    parser.add_argument("--last", type=int, help="Replay last N events")
    parser.add_argument("--market", type=str, help="Filter by market (h2h, btts, ou25)")
    parser.add_argument("--model-version", type=str, help="Filter by model version")
    
    # Compare modes
    parser.add_argument("--compare-run", type=str, help="Compare with another run")
    parser.add_argument("--compare-model", type=str, help="Compare model versions")
    
    # Output
    parser.add_argument("--output", type=str, choices=["console", "json", "csv"], default="console", help="Output format")
    parser.add_argument("--export", type=str, help="Export to file")
    
    # Debug
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--debug", "-d", action="store_true", help="Show event-by-event debug output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    parser.add_argument("--event-types", type=str, help="Comma-separated event types to filter")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s"
    )
    
    # Parse event types
    event_types = None
    if args.event_types:
        event_types = [et.strip() for et in args.event_types.split(",")]
    
    # Handle model version comparison
    if args.compare_model:
        from src.analytics.replay_diff import ReplayDiffer
        differ = ReplayDiffer()
        result = differ.compare_model_versions(args.model_version, args.compare_model)
        
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        
        print(f"\n=== MODEL VERSION COMPARISON ===")
        print(f"Model A: {result['model_a']['version']}")
        print(f"Model B: {result['model_b']['version']}")
        print(f"\nDeltas:")
        print(f"  ROI: {result['deltas']['roi']:+.2f}%")
        print(f"  PnL: {result['deltas']['total_pnl']:+,.2f}")
        print(f"  Wins: {result['deltas']['wins']:+d}")
        print(f"  Winner: {result['winner']} (+{result['win_margin']:.2f}%)")
        return 0
    
    # Handle run comparison
    if args.compare_run:
        events2 = load_events(run_id=args.compare_run)
        if not events2:
            print(f"No events found for run {args.compare_run}")
            return 1
        compare_runs(events, events2)
        return 0
    
    # Load events
    if not args.quiet:
        print("Loading events...")
    
    events = load_events(
        run_id=args.run_id,
        from_date=args.from_date,
        to_date=args.to_date,
        last=args.last,
        event_types=event_types
    )
    
    # Apply market filter
    if args.market:
        events = [
            e for e in events
            if any(b.get("market") == args.market for b in e.get("payload", {}).get("bets", []))
        ]
    
    # Apply model version filter
    if args.model_version:
        events = [e for e in events if e.get("model_version") == args.model_version]
    
    if not events:
        print("No events found matching criteria.")
        return 1
    
    if not args.quiet:
        print(f"Loaded {len(events)} events\n")
    
    # Replay mode
    if not args.quiet:
        print("Replaying events...\n")
    
    system = replay_events(events, verbose=args.verbose or args.debug)
    
    # Export mode
    if args.output == "json" or args.output == "csv":
        from src.analytics.audit_exporter import AuditExporter
        exporter = AuditExporter()
        
        audit_data = {
            "metadata": {
                "run_id": args.run_id,
                "event_count": len(events),
                "filters": {
                    "market": args.market,
                    "model_version": args.model_version,
                    "from_date": args.from_date,
                    "to_date": args.to_date
                }
            },
            "events": events,
            "state_final": {
                "betting": {
                    "balance": system.betting.balance,
                    "roi": system.betting.roi,
                    "wins": system.betting.wins,
                    "losses": system.betting.losses,
                    "total_pnl": system.betting.total_pnl
                },
                "health": {
                    "health_score": system.health.health_score,
                    "error_rate": system.health.error_rate,
                    "total_runs": system.health.total_runs
                }
            }
        }
        
        if args.output == "json":
            if args.export:
                exporter.export_to_json(audit_data, args.export)
                print(f"Exported to {args.export}")
            else:
                print(json.dumps(audit_data, indent=2, default=str))
        else:  # csv
            if args.export:
                exporter.export_to_csv(events, args.export)
                print(f"Exported to {args.export}")
            else:
                exporter.export_to_csv(events, "/dev/stdout")
        
        return 0
    
    # Console output (default)
    print("\n" + "="*50)
    print("FINAL RECONSTRUCTED STATE")
    print("="*50)
    
    print(format_betting_state(system.betting, verbose=args.verbose))
    print()
    print(format_health_state(system.health, verbose=args.verbose))
    print()
    print(format_model_state(system.model, verbose=args.verbose))
    
    print(f"\nEvents processed: {system.events_processed}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
