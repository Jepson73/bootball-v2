#!/usr/bin/env python3
"""
scripts/daily_run.py

Daily automation script:
1. Fetch today's fixtures + odds
2. Generate predictions (all markets) using pickle models
3. Find value bets using Shin method
4. Calculate Kelly stakes
5. Log to value_bets table

Usage:
    python scripts/daily_run.py              # Full run
    python scripts/daily_run.py --dry-run     # No betting suggestions
    python scripts/daily_run.py --leagues 39,140  # Specific leagues
    python scripts/daily_run.py --markets btts,ou25  # Specific markets
"""
import argparse
import logging
import pickle
import os
import sys
import warnings
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

sys.path.insert(0, '/opt/projects/bootball')

import numpy as np
from sqlalchemy import select

from config.leagues import ALL_LEAGUE_IDS, LEAGUES
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, Standing, Team, PredictionRecord, ModelVersion
from src.betting.ev import expected_value
from src.betting.kelly import fractional_kelly
from src.betting.shin import shin_probabilities
from src.betting.alerts import BettingAlerts, BetAlert
from src.betting.prediction import get_model_prediction, MARKET_OUTCOMES
from src.models.calibrator import calibrate_prediction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

EV_THRESHOLD = 0.05
KELLY_FRACTION = 0.25

def find_value_bets(
    model_probs: dict[str, float],
    odds_row,
    market: str,
    fixture_id: int,
    ev_threshold: float = EV_THRESHOLD,
) -> list[dict]:
    """Find value bets for a fixture and market.

    Returns list of value bet candidates sorted by EV descending.
    """
    candidates = []

    field_map = {
        "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
        "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
        "ou25": {"Over": "odd_over", "Under": "odd_under"},
        "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
    }

    market_odds = {}
    for outcome, field in field_map.get(market, {}).items():
        value = getattr(odds_row, field, None)
        if value:
            market_odds[outcome] = value

    if len(market_odds) < 2:
        return candidates

    outcomes = list(market_odds.keys())
    odds_values = [market_odds[o] for o in outcomes]

    try:
        shin_probs = shin_probabilities(odds_values)
    except Exception:
        shin_probs = [1 / o for o in odds_values]

    for i, outcome in enumerate(outcomes):
        model_prob = model_probs.get(outcome, 0.0)
        decimal_odd = market_odds[outcome]

        if decimal_odd <= 0:
            continue

        ev = expected_value(model_prob, decimal_odd)
        if ev < ev_threshold:
            continue

        kf = fractional_kelly(model_prob, decimal_odd, KELLY_FRACTION)
        implied_raw = 1.0 / decimal_odd
        shin_implied = shin_probs[i] if i < len(shin_probs) else implied_raw

        candidates.append({
            'fixture_id': fixture_id,
            'market': market,
            'outcome': outcome,
            'our_prob': model_prob,
            'bookmaker': odds_row.bookmaker,
            'decimal_odd': decimal_odd,
            'implied_prob_raw': implied_raw,
            'implied_prob_shin': shin_implied,
            'ev': ev,
            'kelly_fraction': kf,
        })

    candidates.sort(key=lambda x: x['ev'], reverse=True)
    return candidates


def ensure_fixture(
    s,
    fixture_id: int,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    date: datetime,
    season: int,
    team_names: dict = None
) -> bool:
    """Ensure fixture exists in DB. Returns True if inserted."""
    existing = s.execute(
        select(Fixture).where(Fixture.id == fixture_id)
    ).scalars().first()
    if existing:
        return False

    home_team = s.execute(select(Team).where(Team.id == home_team_id)).scalars().first()
    if not home_team:
        home_team = Team(id=home_team_id, name=team_names.get(home_team_id, f"Team {home_team_id}"))
        s.add(home_team)

    away_team = s.execute(select(Team).where(Team.id == away_team_id)).scalars().first()
    if not away_team:
        away_team = Team(id=away_team_id, name=team_names.get(away_team_id, f"Team {away_team_id}"))
        s.add(away_team)

    s.add(Fixture(
        id=fixture_id,
        league_id=league_id,
        season=season,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        date=date,
        status="NS",
    ))
    return True


class DailyPipeline:
    def __init__(
        self,
        dry_run: bool = False,
        league_ids: list[int] | None = None,
        markets: list[str] | None = None,
        send_alerts: bool = True,
        alert_min_ev: float = 5.0,
        catchup_days: int = 0,
    ):
        self.client = APIFootballClient()
        self.dry_run = dry_run
        self.league_ids = league_ids or ALL_LEAGUE_IDS
        self.markets = markets or ["h2h", "btts", "ou25", "ou15"]
        self.value_bets_found = 0
        self.send_alerts = send_alerts
        self.alert_min_ev = alert_min_ev
        self.catchup_days = catchup_days
        self.errors = []
        self.value_bets_list = []

        if send_alerts:
            self.alerts = BettingAlerts(
                channels=["discord"],
                min_ev=alert_min_ev,
                min_odds=1.5,
                min_kelly=0.03,
            )
        else:
            self.alerts = None

    def run(self):
        """Main pipeline.

        Workflow:
        1. Fetch past 7 days COMPLETED fixtures (status=FT) - for settling predictions
        2. Fetch next 7 days UPCOMING fixtures (status=NS) - for new predictions
        3. Generate predictions for upcoming
        4. Find value bets
        """
        logger.info("Daily pipeline starting")
        logger.info(f"  Leagues: {self.league_ids}")
        logger.info(f"  Markets: {self.markets}")
        logger.info(f"  API calls remaining: {calls_remaining_today()}")

        now = datetime.now(ZoneInfo("UTC"))
        current_season = now.year if now.month >= 7 else now.year - 1

        days_back = self.catchup_days if self.catchup_days > 0 else 1
        logger.info(f"[STEP 1] Fetching completed fixtures (last {days_back} day(s))...")
        past_fixtures = []
        for league_id in self.league_ids:
            try:
                from_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
                to_date = now.strftime("%Y-%m-%d")
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=current_season,
                    from_date=from_date,
                    to_date=to_date,
                    status="FT",
                )
                if raw:
                    past_fixtures.extend(raw)
                    logger.info(f"  {LEAGUES.get(league_id, {}).get('name', league_id)}: {len(raw)} completed")
            except Exception as e:
                self.errors.append(f"Error fetching completed for {league_id}: {e}")
                logger.warning(f"  {self.errors[-1]}")

        updated_count = 0
        for raw in past_fixtures:
            if self._save_completed_fixture(raw, current_season):
                updated_count += 1

        logger.info(f"  Updated {updated_count} completed fixtures")

        logger.info("[STEP 2] Fetching upcoming fixtures...")
        all_fixtures = []
        for league_id in self.league_ids:
            try:
                from_date = now.strftime("%Y-%m-%d")
                to_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=current_season,
                    from_date=from_date,
                    to_date=to_date,
                    status="NS",
                )
                if raw:
                    all_fixtures.extend(raw)
                    logger.info(f"  {LEAGUES.get(league_id, {}).get('name', league_id)}: {len(raw)} upcoming")
            except Exception as e:
                self.errors.append(f"Error fetching upcoming for {league_id}: {e}")
                logger.warning(f"  {self.errors[-1]}")

        if not all_fixtures:
            logger.info("No upcoming fixtures today")
            self._send_completion_alert()
            return

        logger.info(f"Total: {len(all_fixtures)} upcoming fixtures to process")

        for raw in all_fixtures:
            self._process_fixture(raw, current_season)

        logger.info(f"Done! Found {self.value_bets_found} value bet opportunities")
        self._send_completion_alert()

    def _send_completion_alert(self):
        """Send Discord summary when daily_run completes with value bets organized by date."""
        mode = "CATCHUP" if self.catchup_days > 0 else "DAILY"
        error_msg = self.errors[0] if self.errors else None

        try:
            from src.betting.alerts import BettingAlerts

            bets_by_date = {}
            for bet in self.value_bets_list:
                date_key = bet['date']
                if date_key not in bets_by_date:
                    bets_by_date[date_key] = []
                bets_by_date[date_key].append(bet)

            sorted_dates = sorted(bets_by_date.keys())

            msg = f"💻 **{mode} RUN COMPLETE**\n"
            msg += f"Value bets found: {len(self.value_bets_list)}\n"

            if self.errors:
                msg += f"Errors: {len(self.errors)}\n"

            msg += "\n" + "="*50 + "\n\n"

            for date_key in sorted_dates:
                bets = bets_by_date[date_key]
                bets.sort(key=lambda x: x['ev'], reverse=True)
                top_bets = bets[:2]

                if not top_bets:
                    continue

                msg += f"📅 **{date_key}**\n"

                for bet in top_bets:
                    sweet = " 🚀" if bet.get('sweet_spot') else ""
                    msg += f"🎯 **{bet['home']}** vs **{bet['away']}**{sweet}\n"
                    msg += f"   └ {bet['country']} - {bet['league']}\n"
                    msg += f"   └ {bet['market'].upper()} **{bet['outcome']}** @ {bet['odds']:.2f}\n"
                    msg += f"   └ P={bet['prob']:.0%} | EV={bet['ev']:.1f}% | Kelly={bet['kelly']:.0%}\n"
                    msg += f"   └ Time: {bet['time']} | Stake: £{bet['stake']:.2f}\n\n"

                if len(bets) > 2:
                    msg += f"   +{len(bets) - 2} more bets that day\n\n"

                msg += "-"*30 + "\n\n"

            alerts = BettingAlerts(channels=["discord"], min_ev=5.0, min_odds=1.5, min_kelly=0.03)
            alerts.send_message(msg)
            logger.info(f"Sent Discord completion alert with {len(self.value_bets_list)} value bets")

        except Exception as e:
            logger.warning(f"Failed to send completion alert: {e}")

    def _save_completed_fixture(self, raw: dict, season: int) -> bool:
        """Save/update a completed fixture (status=FT) with final score.

        Returns True if fixture was updated, False if already up to date.
        """
        fixture = raw.get("fixture", {})
        teams = raw.get("teams", {})
        goals = raw.get("goals", {})
        score = raw.get("score", {})
        ht = score.get("halftime", {})

        fixture_id = fixture.get("id")
        if not fixture_id:
            return False

        home_goals = goals.get("home")
        away_goals = goals.get("away")

        with get_session() as s:
            existing = s.execute(
                select(Fixture).where(Fixture.id == fixture_id)
            ).scalar_one_or_none()

            if existing:
                if existing.goals_home is not None:
                    return False
                if existing.status == "FT" and home_goals is None:
                    return False
                existing.status = "FT"
                existing.goals_home = home_goals
                existing.goals_away = away_goals
                existing.ht_goals_home = ht.get("home")
                existing.ht_goals_away = ht.get("away")
                if home_goals is not None:
                    if home_goals > away_goals:
                        existing.outcome = "H"
                    elif home_goals < away_goals:
                        existing.outcome = "A"
                    else:
                        existing.outcome = "D"
                return True
            else:
                s.add(Fixture(
                    id=fixture_id,
                    league_id=raw.get("league", {}).get("id"),
                    season=season,
                    home_team_id=teams.get("home", {}).get("id"),
                    away_team_id=teams.get("away", {}).get("id"),
                    date=datetime.fromisoformat(fixture.get("date").replace("Z", "+00:00")) if fixture.get("date") else None,
                    status="FT",
                    goals_home=home_goals,
                    goals_away=away_goals,
                    ht_goals_home=ht.get("home"),
                    ht_goals_away=ht.get("away"),
                ))
                return True

    def _fetch_odds_for_fixture(self, s, fixture_id: int):
        """Fetch and store odds for a fixture from API-Football."""
        bet_types = {
            "h2h": 1,
            "btts": 8,
            "over_under": 5,
        }

        for bet_name, bet_id in bet_types.items():
            try:
                odds_data = self.client.get_odds(fixture_id=fixture_id, bet_type=bet_id)
            except Exception as e:
                logger.warning(f"  Odds fetch failed for fixture {fixture_id} ({bet_name}): {e}")
                continue

            if not odds_data:
                continue

            for odd_row in odds_data:
                bookmaker = odd_row.get('bookmaker', {}).get('name', 'Unknown')
                values = odd_row.get('values', [])

                if not values:
                    continue

                odds_dict = {}
                for v in values:
                    label = v.get('label', '')
                    odd_value = v.get('odd')
                    if label and odd_value:
                        odds_dict[label] = float(odd_value)

                if not odds_dict:
                    continue

                existing = s.execute(
                    select(FixtureOdds).where(
                        FixtureOdds.fixture_id == fixture_id,
                        FixtureOdds.bookmaker == bookmaker,
                        FixtureOdds.bet_type == bet_name,
                    )
                ).scalars().first()

                update_data = {
                    'odd_home': odds_dict.get('1'),
                    'odd_draw': odds_dict.get('X'),
                    'odd_away': odds_dict.get('2'),
                    'odd_over': odds_dict.get('Over 2.5'),
                    'odd_under': odds_dict.get('Under 2.5'),
                    'odd_btts_yes': odds_dict.get('Yes'),
                    'odd_btts_no': odds_dict.get('No'),
                    'odd_over15': odds_dict.get('Over 1.5'),
                    'odd_under15': odds_dict.get('Under 1.5'),
                }

                if existing:
                    for field, value in update_data.items():
                        if value is not None:
                            setattr(existing, field, value)
                    existing.fetched_at = datetime.utcnow()
                else:
                    s.add(FixtureOdds(
                        fixture_id=fixture_id,
                        bookmaker=bookmaker,
                        bet_type=bet_name,
                        **{k: v for k, v in update_data.items() if v is not None},
                    ))

    def _process_fixture(self, raw: dict, season: int):
        """Process single fixture for all markets."""
        fixture = raw.get("fixture", {})
        teams = raw.get("teams", {})

        fixture_id = fixture.get("id")
        league_id = raw.get("league", {}).get("id")
        home_team_id = teams.get("home", {}).get("id")
        away_team_id = teams.get("away", {}).get("id")
        home_name = teams.get("home", {}).get("name", "Home")
        away_name = teams.get("away", {}).get("name", "Away")

        if not all([fixture_id, league_id, home_team_id, away_team_id]):
            return

        date_utc_str = fixture.get("date")
        if date_utc_str:
            if isinstance(date_utc_str, str):
                date_utc = datetime.fromisoformat(date_utc_str.replace('Z', '+00:00'))
            else:
                date_utc = date_utc_str
        else:
            date_utc = datetime.utcnow()

        team_names = {
            home_team_id: home_name,
            away_team_id: away_name,
        }

        with get_session() as s:
            ensure_fixture(s, fixture_id, league_id, home_team_id, away_team_id, date_utc, season, team_names)
            self._fetch_odds_for_fixture(s, fixture_id)

        logger.info(f"  {home_name} vs {away_name}:")

        for market in self.markets:
            self._find_value_bets_for_market(
                raw, fixture_id, league_id, home_team_id, away_team_id,
                home_name, away_name, market
            )

    def _find_value_bets_for_market(
        self,
        raw: dict,
        fixture_id: int,
        league_id: int,
        home_id: int,
        away_id: int,
        home_name: str,
        away_name: str,
        market: str,
    ):
        """Find value bets for a specific market."""
        model_probs = get_model_prediction(market, home_id, away_id)
        if model_probs is None:
            logger.warning(f"    {market}: prediction failed")
            return

        outcome_str = ", ".join(f"{k}={v:.0%}" for k, v in model_probs.items())
        logger.info(f"    {market}: {outcome_str}")

        bet_type_map = {
            "h2h": "h2h",
            "btts": "btts",
            "ou25": "over_under",
            "ou15": "over_under",
        }
        bet_type = bet_type_map.get(market, market)

        if not self.dry_run:
            best_outcome = max(model_probs.items(), key=lambda x: x[1])
            predicted_outcome = best_outcome[0]
            prob = best_outcome[1]

            sweet_spot = False
            if market == "btts" and best_outcome[0] == "Yes":
                pass
            elif market in ("ou25", "ou15") and best_outcome[0] == "Over":
                sweet_spot = True

            with get_session() as ps:
                existing_pred = ps.execute(
                    select(PredictionRecord).where(
                        PredictionRecord.fixture_id == fixture_id,
                        PredictionRecord.market == market,
                    )
                ).scalars().first()

                odds_row = ps.execute(
                    select(FixtureOdds).where(
                        FixtureOdds.fixture_id == fixture_id,
                        FixtureOdds.bet_type == bet_type,
                    )
                ).scalars().first()

                odds_decimal = None
                ev = None
                calibrated_prob = prob
                implied_prob = None
                edge = None
                bookmaker = None

                if odds_row:
                    field_map = {
                        "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
                        "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
                        "ou25": {"Over": "odd_over", "Under": "odd_under"},
                        "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
                    }
                    market_fields = field_map.get(market, {})
                    odd_value = market_fields.get(predicted_outcome)
                    if odd_value:
                        odds_decimal = getattr(odds_row, odd_value, None)

                    if odds_decimal and odds_decimal > 0:
                        ev = expected_value(prob, odds_decimal)
                        implied_prob = 1.0 / odds_decimal
                        edge = (prob - implied_prob) * 100
                        bookmaker = odds_row.bookmaker

                        calibration = calibrate_prediction(market, prob)
                        calibrated_prob = calibration.calibrated_prob

                active_version = ps.execute(
                    select(ModelVersion).where(
                        ModelVersion.market == market,
                        ModelVersion.is_active == True
                    )
                ).scalar_one_or_none()
                model_version_id = active_version.id if active_version else None

                if existing_pred:
                    existing_pred.odds_decimal = odds_decimal
                    existing_pred.ev = ev
                    existing_pred.calibrated_prob = calibrated_prob
                    existing_pred.implied_prob = implied_prob
                    existing_pred.edge = edge
                    existing_pred.bookmaker = bookmaker
                else:
                    ps.add(PredictionRecord(
                        fixture_id=fixture_id,
                        market=market,
                        model_version_id=model_version_id,
                        model_name="lgbm",
                        predicted_outcome=predicted_outcome,
                        our_prob=prob,
                        sweet_spot=sweet_spot,
                        odds_decimal=odds_decimal,
                        ev=ev,
                        calibrated_prob=calibrated_prob,
                        implied_prob=implied_prob,
                        edge=edge,
                        bookmaker=bookmaker,
                    ))
                ps.commit()

                if not odds_row or not odds_decimal:
                    return

                model_probs = get_model_prediction(market, home_id, away_id)
                if model_probs is None:
                    return

                candidates = find_value_bets(
                    model_probs=model_probs,
                    odds_row=odds_row,
                    market=market,
                    fixture_id=fixture_id,
                    ev_threshold=0.02,
                )

                for candidate in candidates:
                    self.value_bets_found += 1

                    stake = candidate['kelly_fraction'] * 1000
                    logger.info(
                        f"    *** VALUE BET: {market} {candidate['outcome']} @ {candidate['decimal_odd']:.2f} | "
                        f"EV={candidate['ev']*100:.1f}% | Kelly={candidate['kelly_fraction']:.1%} | "
                        f"Stake=£{stake:.2f}"
                    )

                    league_info = LEAGUES.get(league_id, {})
                    league_name = league_info.get("name", "")
                    country = league_info.get("country", "")

                    fixture_date_str = raw.get("fixture", {}).get("date", "")
                    if fixture_date_str:
                        try:
                            fix_date = datetime.fromisoformat(fixture_date_str.replace("Z", "+00:00"))
                            date_key = fix_date.strftime("%Y-%m-%d")
                            fixture_time = fix_date.strftime("%H:%M")
                        except:
                            date_key = fixture_date_str[:10]
                            fixture_time = fixture_date_str[11:16]
                    else:
                        date_key = "Unknown"
                        fixture_time = ""

                    self.value_bets_list.append({
                        'date': date_key,
                        'time': fixture_time,
                        'home': home_name,
                        'away': away_name,
                        'country': country,
                        'league': league_name,
                        'market': market,
                        'outcome': candidate['outcome'],
                        'odds': candidate['decimal_odd'],
                        'prob': candidate['our_prob'],
                        'ev': candidate['ev'] * 100,
                        'kelly': candidate['kelly_fraction'],
                        'stake': stake,
                        'edge': (candidate['our_prob'] - candidate['implied_prob_shin']) * 100,
                    })

                if not self.dry_run:
                    pass  # ValueBet table archived, value bets tracked via PredictionRecord


def main():
    parser = argparse.ArgumentParser(description="Daily prediction pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--leagues", type=str, help="Comma-separated league IDs")
    parser.add_argument("--markets", type=str, help="Comma-separated markets (h2h,btts,ou25,ou15)")
    parser.add_argument("--catchup", type=int, default=0, help="Days to catch up on settled fixtures (default: 1, max: 7)")
    args = parser.parse_args()

    catchup_days = min(max(args.catchup, 0), 7)

    league_ids = None
    if args.leagues:
        league_ids = [int(x) for x in args.leagues.split(",")]

    markets = None
    if args.markets:
        markets = [x.strip() for x in args.markets.split(",")]

    init_db()

    pipeline = DailyPipeline(
        dry_run=args.dry_run,
        league_ids=league_ids,
        markets=markets,
        catchup_days=catchup_days,
    )
    pipeline.run()


if __name__ == "__main__":
    main()
