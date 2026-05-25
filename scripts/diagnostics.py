#!/usr/bin/env python3
# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: standalone API/DB health diagnostic; can be run manually for ops checks
"""
scripts/maintenance.py

Daily maintenance tasks:
- Validate backfill config
- Check API connectivity
- Verify DB integrity
- Alert on stale data

Usage:
    python scripts/maintenance.py              # Run all checks
    python scripts/maintenance.py --check-api  # API check only
    python scripts/maintenance.py --check-db   # DB check only
    python scripts/maintenance.py --verbose    # Detailed output
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

LAST_RUN_FILE = '/var/log/bootball/maintenance_last.log'


def log_expected_action(action: str) -> None:
    """Log expected action."""
    logger.info(f"[EXPECTED] {action}")


def log_actual_action(action: str) -> None:
    """Log actual action."""
    logger.info(f"[ACTUAL] {action}")


def log_result(expected: str, actual: str, status: str = "OK") -> None:
    """Log expected vs actual result."""
    if status == "OK":
        logger.info(f"[RESULT] {status}: Expected={expected}, Actual={actual}")
    else:
        logger.warning(f"[MISMATCH] Expected={expected}, Actual={actual} [{status}]")


def check_api_connectivity(verbose: bool = False) -> bool:
    """Check API-Football connectivity."""
    log_expected_action("Connect to API-Football and fetch remaining calls")
    
    try:
        from src.ingestion.client import APIFootballClient, calls_remaining_today
        
        client = APIFootballClient()
        remaining = calls_remaining_today()
        
        log_actual_action(f"Connected, {remaining} API calls remaining")
        log_result(f"Calls remaining", f"{remaining}", "OK" if remaining > 100 else "LOW")
        
        if remaining < 100:
            logger.warning("[ALERT] API calls running low!")
        
        return True
    except Exception as e:
        logger.error(f"[ALERT] API connectivity failed: {e}")
        return False


def check_backfill_config(verbose: bool = False) -> bool:
    """Validate backfill config against available leagues."""
    log_expected_action("Validate backfill config leagues exist in API")
    
    from config.backfill import get_backfill_leagues, get_backfill_seasons
    from config.leagues import LEAGUES
    
    leagues = get_backfill_leagues()
    seasons = get_backfill_seasons()
    
    log_actual_action(f"Config has {len(leagues)} leagues, {len(seasons)} seasons")
    log_result(f"Config loaded", f"{len(leagues)} leagues, {len(seasons)} seasons")
    
    issues = []
    
    # Check if config leagues exist in LEAGUES
    missing_in_leagues = [l for l in leagues if l not in LEAGUES]
    if missing_in_leagues:
        logger.warning(f"[ISSUE] Config leagues not in LEAGUES: {missing_in_leagues}")
        issues.append(f"Missing in LEAGUES: {missing_in_leagues}")
    
    # Check for new leagues available
    try:
        from src.ingestion.client import APIFootballClient
        client = APIFootballClient()
        all_leagues = client.get_leagues()
        api_ids = {l.get('league', {}).get('id') for l in all_leagues}
        
        known_ids = set(LEAGUES.keys())
        new_in_api = api_ids - known_ids
        if new_in_api:
            logger.info(f"[INFO] New leagues in API not in config: {len(new_in_api)} leagues")
            if verbose:
                for lid in sorted(new_in_api)[:10]:
                    name = next((l.get('league', {}).get('name') for l in all_leagues if l.get('league', {}).get('id') == lid), 'Unknown')
                    logger.info(f"  - {lid}: {name}")
    except Exception as e:
        logger.warning(f"[ISSUE] Could not check for new leagues: {e}")
    
    if not issues:
        logger.info("[OK] Backfill config is valid")
    
    return len(issues) == 0


def check_database_integrity(verbose: bool = False) -> bool:
    """Check database integrity."""
    log_expected_action("Check DB integrity and orphaned records")
    
    from src.storage.db import get_session
    from src.storage.models import Fixture, Team, Standing, PredictionRecord
    from sqlalchemy import select, func
    
    try:
        with get_session() as s:
            # Count records
            fixture_count = s.execute(select(func.count()).select_from(Fixture)).scalar()
            team_count = s.execute(select(func.count()).select_from(Team)).scalar()
            standing_count = s.execute(select(func.count()).select_from(Standing)).scalar()
            pred_count = s.execute(select(func.count()).select_from(PredictionRecord)).scalar()
            
            log_actual_action(f"DB: {fixture_count} fixtures, {team_count} teams, {standing_count} standings, {pred_count} predictions")
            log_result(f"DB has data", f"{fixture_count} fixtures", "OK" if fixture_count > 100 else "LOW")
            
            # Check for orphaned fixtures (no standings)
            orphaned = s.execute(
                select(Fixture)
                .where(Fixture.league_id.notin_(select(Standing.league_id).distinct()))
                .limit(10)
            ).scalars().all()
            
            if orphaned:
                logger.warning(f"[ISSUE] {len(orphaned)} orphaned fixtures (no standings)")
                if verbose:
                    for f in orphaned[:5]:
                        logger.warning(f"  - Fixture {f.id}: league {f.league_id}, date {f.date}")
            else:
                logger.info("[OK] No orphaned fixtures")
            
            # Check for fixtures with null goals but FT status
            null_goals = s.execute(
                select(Fixture)
                .where(Fixture.status == 'FT')
                .where(Fixture.goals_home == None)
                .limit(5)
            ).scalars().all()
            
            if null_goals:
                logger.warning(f"[ISSUE] {len(null_goals)} FT fixtures with null goals")
            else:
                logger.info("[OK] All FT fixtures have goals")
            
            # Check prediction records
            settled = s.execute(
                select(func.count()).select_from(PredictionRecord)
                .where(PredictionRecord.settled == True)
            ).scalar()
            unsettled = s.execute(
                select(func.count()).select_from(PredictionRecord)
                .where(PredictionRecord.settled == False)
            ).scalar()
            
            logger.info(f"[OK] Predictions: {settled} settled, {unsettled} unsettled")
            
            return True
    except Exception as e:
        logger.error(f"[ALERT] DB check failed: {e}")
        return False


def check_last_runs(verbose: bool = False) -> bool:
    """Check when last runs happened."""
    log_expected_action("Check last backfill and daily run times")
    
    log_paths = {
        'daily_run': '/var/log/bootball/daily_run.log',
        'backfill': '/var/log/bootball/backfill.log',
        'settle': '/var/log/bootball/settle.log',
        'predictions': '/var/log/bootball/predictions_settle.log',
    }
    
    stale_threshold = timedelta(days=7)
    now = datetime.now()
    
    all_ok = True
    for name, path in log_paths.items():
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            age = now - mtime
            
            status = "OK" if age < stale_threshold else "STALE"
            log_result(f"Last {name}", f"{age.days}d ago", status)
            
            if age > stale_threshold:
                logger.warning(f"[ALERT] {name} is {age.days} days old (> {stale_threshold.days})")
                all_ok = False
        else:
            logger.warning(f"[ISSUE] {name} log file not found: {path}")
            log_result(f"Last {name}", "NOT FOUND", "MISSING")
            all_ok = False
    
    return all_ok


def check_fixtures_updated(verbose: bool = False) -> bool:
    """Check if fixtures are being updated."""
    log_expected_action("Check recent fixture updates")
    
    from src.storage.db import get_session
    from src.storage.models import Fixture
    from sqlalchemy import select, func
    
    try:
        with get_session() as s:
            # Recent fixtures
            recent = datetime.utcnow() - timedelta(days=7)
            recent_count = s.execute(
                select(func.count()).select_from(Fixture)
                .where(Fixture.fetched_at != None)
                .where(Fixture.fetched_at >= recent)
            ).scalar()
            
            total_count = s.execute(select(func.count()).select_from(Fixture)).scalar()
            
            log_actual_action(f"{recent_count} fixtures updated in last 7 days (of {total_count} total)")
            log_result(f"Recent updates", f"{recent_count}", "OK" if recent_count > 0 else "STALE")
            
            return recent_count > 0
    except Exception as e:
        logger.error(f"[ALERT] Fixture check failed: {e}")
        return False


def update_last_run() -> None:
    """Update last run timestamp."""
    os.makedirs(os.path.dirname(LAST_RUN_FILE), exist_ok=True)
    with open(LAST_RUN_FILE, 'w') as f:
        f.write(datetime.now().isoformat())


def main():
    parser = argparse.ArgumentParser(description="Maintenance checks")
    parser.add_argument("--check-api", action="store_true", help="API check only")
    parser.add_argument("--check-db", action="store_true", help="DB check only")
    parser.add_argument("--check-config", action="store_true", help="Config check only")
    parser.add_argument("--check-runs", action="store_true", help="Last runs check only")
    parser.add_argument("--verbose", action="store_true", help="Detailed output")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Bootball Maintenance Check")
    logger.info(f"Started: {datetime.now().isoformat()}")
    logger.info("=" * 60)
    
    results = {}
    
    if args.check_api:
        results['api'] = check_api_connectivity(args.verbose)
    elif args.check_db:
        results['db'] = check_database_integrity(args.verbose)
    elif args.check_config:
        results['config'] = check_backfill_config(args.verbose)
    elif args.check_runs:
        results['runs'] = check_last_runs(args.verbose)
    else:
        # Run all checks
        results['api'] = check_api_connectivity(args.verbose)
        results['config'] = check_backfill_config(args.verbose)
        results['db'] = check_database_integrity(args.verbose)
        results['fixtures'] = check_fixtures_updated(args.verbose)
        results['runs'] = check_last_runs(args.verbose)
    
    logger.info("=" * 60)
    
    all_ok = all(results.values()) if results else False
    if all_ok:
        logger.info("All checks passed")
        update_last_run()
    else:
        failed = [k for k, v in results.items() if not v]
        logger.warning(f"Failed checks: {', '.join(failed)}")
    
    logger.info("=" * 60)
    
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
