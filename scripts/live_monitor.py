#!/usr/bin/env python3
"""
scripts/live_monitor.py

Monitor live matches and send Discord alerts for in-play opportunities.

Usage:
    python scripts/live_monitor.py              # Run once
    python scripts/live_monitor.py --continuous # Continuous monitoring
    python scripts/live_monitor.py --interval 60 # Check every 60 seconds

Alerts sent:
- Over/Under value when odds shift
- BTTS opportunities (first goal early/late)
- Live goal value (odds on current score)
"""
import argparse
import logging
import sys
from pathlib import Path
import time
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from config.leagues import LEAGUES, ALL_LEAGUE_IDS
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Fixture
from src.betting.prediction import get_model_prediction
from src.betting.ev import expected_value
from src.betting.alerts import BettingAlerts, BetAlert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

LIVE_MARKETS = ["h2h", "btts", "ou25", "ou15"]


def get_live_fixtures(client: APIFootballClient) -> list[dict]:
    """Get currently live matches."""
    try:
        raw = client.get_fixtures(status="1H-2H-HT")
        return raw or []
    except Exception as e:
        logger.error(f"Error fetching live fixtures: {e}")
        return []


def get_live_odds(client: APIFootballClient, fixture_id: int) -> dict:
    """Get live odds for a fixture."""
    try:
        raw = client.get('odds/live', {"fixture": fixture_id})
        return raw[0] if raw else {}
    except Exception as e:
        logger.warning(f"Error fetching live odds for {fixture_id}: {e}")
        return {}


def analyze_live_opportunity(
    fixture: dict,
    live_odds: dict,
    client: APIFootballClient,
) -> list[BetAlert]:
    """Analyze a live match for betting opportunities."""
    alerts = []

    fix_id = fixture.get("fixture", {}).get("id")
    teams = fixture.get("teams", {})
    home_name = teams.get("home", {}).get("name", "Home")
    away_name = teams.get("away", {}).get("name", "Away")
    home_id = teams.get("home", {}).get("id")
    away_id = teams.get("away", {}).get("id")
    league = fixture.get("league", {})
    league_name = league.get("name", "")

    goals = fixture.get("goals", {})
    home_goals = goals.get("home")
    away_goals = goals.get("away")
    elapsed = fixture.get("fixture", {}).get("time", {}).get("elapsed", 0)

    if home_goals is None or away_goals is None:
        return alerts

    total_goals = home_goals + away_goals

    try:
        market_odds = live_odds.get("bookmakers", [{}])[0].get("bets", [])
    except (IndexError, KeyError):
        return alerts

    for bet in market_odds:
        bet_name = bet.get("name", "")
        values = {v["value"]: float(v["odd"]) for v in bet.get("values", [])}

        if bet_name == "Goals Over/Under" and values:
            if "Over 2.5" in values:
                odd = values["Over 2.5"]
                if total_goals > 2.5:
                    ev = (1.0 - 0.95) * 100
                else:
                    prob_over = max(0.1, 1.0 - elapsed / 90)
                    ev = expected_value(prob_over, odd) * 100

                if ev > 10:
                    alerts.append(BetAlert(
                        market="live_ou25",
                        home_team=home_name,
                        away_team=away_name,
                        outcome="Over 2.5",
                        odds=odd,
                        ev=ev,
                        kelly=0.05,
                        league=league_name,
                        fixture_date=f"Live {elapsed}'",
                    ))

        elif bet_name == "Both Teams To Score" and values:
            if "Yes" in values:
                odd = values["Yes"]
                btts_happened = home_goals > 0 and away_goals > 0

                if btts_happened:
                    ev = (odd - 1) * 100
                else:
                    prob_btts = max(0.1, (90 - elapsed) / 90 * 0.5)
                    ev = expected_value(prob_btts, odd) * 100

                if ev > 10 and odd >= 1.5:
                    alerts.append(BetAlert(
                        market="live_btts",
                        home_team=home_name,
                        away_team=away_name,
                        outcome="Yes",
                        odds=odd,
                        ev=ev,
                        kelly=0.05,
                        league=league_name,
                        fixture_date=f"Live {elapsed}' ({home_goals}-{away_goals})",
                    ))

    return alerts


def run_monitor(continuous: bool = False, interval: int = 60):
    """Run the live monitor."""
    client = APIFootballClient()

    try:
        alerts = BettingAlerts(
            channels=["discord"],
            min_ev=8.0,
            min_odds=1.5,
            min_kelly=0.03,
        )
    except:
        alerts = None
        logger.warning("Discord not configured, alerts disabled")

    if continuous:
        logger.info(f"Starting continuous monitoring (interval: {interval}s)")
        while True:
            check_live_matches(client, alerts)
            time.sleep(interval)
    else:
        check_live_matches(client, alerts)


def check_live_matches(client: APIFootballClient, alerts):
    """Check all live matches for opportunities."""
    live_fixtures = get_live_fixtures(client)

    if not live_fixtures:
        logger.info("No live matches found")
        return

    logger.info(f"Checking {len(live_fixtures)} live matches...")

    for fixture in live_fixtures:
        fix_id = fixture.get("fixture", {}).get("id")
        live_odds = get_live_odds(client, fix_id)

        if not live_odds:
            continue

        opp_list = analyze_live_opportunity(fixture, live_odds, client)

        for opp in opp_list:
            logger.info(
                f"LIVE OPPORTUNITY: {opp.home_team} vs {opp.away_team} "
                f"({opp.fixture_date}) - {opp.market} {opp.outcome} @ {opp.odds:.2f} "
                f"EV: {opp.ev:.1f}%"
            )

            if alerts:
                alerts.send_bet_alert(opp)


def main():
    parser = argparse.ArgumentParser(description="Live match monitor")
    parser.add_argument("--continuous", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds")
    args = parser.parse_args()

    run_monitor(continuous=args.continuous, interval=args.interval)


if __name__ == "__main__":
    main()
