# scripts/backfill.py - CLI script
"""CLI entry point for backfill."""
import argparse
import logging
import sys
sys.path.insert(0, '/opt/projects/bootball')

from src.ingestion.backfill import Backfiller
from src.ingestion.client import APIFootballClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Backfill historical football data")
    parser.add_argument(
        "--leagues",
        type=int,
        nargs="+",
        help="League IDs to backfill (default: all Tier 1)",
    )
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        help="Seasons to backfill (e.g., 2024)",
    )
    parser.add_argument(
        "--include-odds",
        action="store_true",
        help="Include odds data (expensive on API calls)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without making API calls",
    )
    args = parser.parse_args()

    client = APIFootballClient()
    backfiller = Backfiller(client)

    if args.dry_run:
        print("DRY RUN MODE - No API calls will be made")
        import config.settings
        config.settings.settings.dry_run = True

    backfiller.run_all(
        league_ids=args.leagues,
        seasons=args.seasons,
        include_odds=args.include_odds,
    )


if __name__ == "__main__":
    main()