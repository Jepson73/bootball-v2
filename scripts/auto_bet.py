#!/usr/bin/env python3
"""
scripts/auto_bet.py

Automatic betting bot that places fictional bets based on value detection.

Features:
- Uses same pickle models as daily_run and web_ui
- Places bets automatically when value detected (EV > threshold)
- Settles bets automatically when matches complete
- Tracks historical rounds for comparison
- Sends Discord alerts for placed bets

Usage:
    python scripts/auto_bet.py              # Full run: bet + settle
    python scripts/auto_bet.py --bet-only   # Only place bets, don't settle
    python scripts/auto_bet.py --settle-only # Only settle pending bets
    python scripts/auto_bet.py --status     # Show current bankroll status
    python scripts/auto_bet.py --new-round  # Force start new round
    python scripts/auto_bet.py --reset BANKROLL  # Reset bankroll to starting amount
    python scripts/auto_bet.py --history   # Show historical rounds
"""
from __future__ import annotations

import argparse
import logging
import pickle
import os
import sys
import warnings
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, '/opt/projects/bootball')

import numpy as np
from sqlalchemy import select, func

from config.leagues import TIER1_LEAGUE_IDS, LEAGUES
from src.ingestion.client import APIFootballClient, calls_remaining_today
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureOdds, Standing, Team, League,
    Bankroll, BankrollRound, PlacedBet, ModelVersion,
)
from src.betting.kelly import fractional_kelly, kelly_stake
from src.betting.alerts import BettingAlerts, BetAlert
from src.betting.ev import expected_value
from src.betting.shin import shin_probabilities
from src.betting.prediction import get_model_prediction, MARKET_OUTCOMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

INITIAL_BANKROLL = 1000.0
EV_THRESHOLD_BET = 0.05
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.05
MIN_STAKE = 1.0
MAX_STAKE = 50.0
MAX_TOTAL_STAKE_PER_DAY = 100.0
MIN_ODDS = 1.5
MAX_ODDS = 10.0
BET_MARKETS = ["h2h", "btts", "ou25", "ou15"]
MAX_BETS_PER_DAY = 5

ODDS_FIELD_MAP = {
    "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
}


def get_odds_for_market(odds_row, market: str) -> dict[str, float]:
    """Extract odds for a specific market from a FixtureOdds row."""
    field_map = ODDS_FIELD_MAP.get(market, {})
    odds = {}
    for outcome, field in field_map.items():
        value = getattr(odds_row, field, None)
        if value:
            odds[outcome] = value
    return odds


def find_value_bets(
    model_probs: dict[str, float],
    odds_row,
    market: str,
    fixture_id: int,
    ev_threshold: float = EV_THRESHOLD_BET,
) -> list[dict]:
    """Find value bets for a fixture and market."""
    candidates = []

    market_odds = get_odds_for_market(odds_row, market)
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


def get_current_round(session) -> BankrollRound | None:
    return session.execute(
        select(BankrollRound)
        .where(BankrollRound.is_active == True)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()


def get_or_create_round(session) -> BankrollRound:
    active = get_current_round(session)
    if active:
        return active

    last_round = session.execute(
        select(BankrollRound)
        .order_by(BankrollRound.round_number.desc())
        .limit(1)
    ).scalars().first()

    next_num = (last_round.round_number + 1) if last_round else 1

    new_round = BankrollRound(
        round_number=next_num,
        initial_bankroll=INITIAL_BANKROLL,
        reason="new_start",
    )
    session.add(new_round)
    session.commit()
    session.refresh(new_round)
    logger.info(f"Started new bankroll round #{next_num}")
    return new_round


def get_current_balance(the_round: BankrollRound, session) -> float:
    if the_round.ending_balance is not None:
        return the_round.ending_balance

    settled_pnl = session.execute(
        select(func.coalesce(func.sum(PlacedBet.pnl), 0))
        .where(PlacedBet.round_id == the_round.id)
        .where(PlacedBet.settled == True)
    ).scalar() or 0.0

    pending_stake = session.execute(
        select(func.coalesce(func.sum(PlacedBet.stake), 0))
        .where(PlacedBet.round_id == the_round.id)
        .where(PlacedBet.settled == False)
    ).scalar() or 0.0

    return the_round.initial_bankroll + settled_pnl - pending_stake


def archive_round(round: BankrollRound, session, reason: str):
    round.is_active = False
    round.ended_at = datetime.utcnow()
    round.ending_balance = get_current_balance(round, session)

    settled = session.execute(
        select(PlacedBet)
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == True)
    ).scalars().all()

    round.total_bets = len(settled)
    round.total_wins = sum(1 for b in settled if b.won)
    round.total_staked = sum(b.stake for b in settled)
    round.total_pnl = sum(b.pnl or 0 for b in settled)
    round.roi_pct = (round.total_pnl / round.total_staked * 100) if round.total_staked > 0 else 0
    round.reason = reason

    logger.info(f"Archived round #{round.round_number}: {reason}, "
                 f"ROI={round.roi_pct:.1f}%, {round.total_bets} bets")


def check_reset_condition(round: BankrollRound, session) -> str | None:
    balance = get_current_balance(round, session)

    pending = session.execute(
        select(func.count(PlacedBet.id))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == False)
    ).scalar() or 0

    if balance <= 0 and pending == 0:
        return "balance_zero"
    if balance <= 0 and pending > 0:
        return None
    if balance < round.initial_bankroll * 0.1:
        return "balance_critical"
    return None


def _get_market_result(fixture: Fixture, market: str) -> str | None:
    gh = fixture.goals_home
    ga = fixture.goals_away
    total = gh + ga

    if market == "h2h":
        if gh > ga:
            return "1"
        elif gh == ga:
            return "X"
        else:
            return "2"
    elif market == "btts":
        return "Yes" if gh > 0 and ga > 0 else "No"
    elif market in ("ou25", "ou15"):
        threshold = 2.5 if market == "ou25" else 1.5
        return "Over" if total > threshold else "Under"
    return None


def place_bets(the_round: BankrollRound, session, league_ids: list[int] | None = None) -> tuple[int, list[dict]]:
    leagues = league_ids or TIER1_LEAGUE_IDS

    today = datetime.now(ZoneInfo("UTC")).date()
    tomorrow = today + timedelta(days=1)

    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=ZoneInfo("UTC"))
    today_end = datetime.combine(tomorrow, datetime.min.time()).replace(tzinfo=ZoneInfo("UTC"))

    existing_today = session.execute(
        select(func.count(PlacedBet.id))
        .where(PlacedBet.round_id == the_round.id)
        .where(PlacedBet.placed_at >= today_start)
    ).scalar() or 0

    if existing_today >= MAX_BETS_PER_DAY:
        logger.info(f"Max bets for today ({MAX_BETS_PER_DAY}) reached")
        return 0, []

    slots_remaining = MAX_BETS_PER_DAY - existing_today

    fixtures = session.execute(
        select(Fixture)
        .join(FixtureOdds, FixtureOdds.fixture_id == Fixture.id)
        .where(Fixture.status == "NS")
        .where(Fixture.date >= today_start)
        .where(Fixture.date < today_end)
        .where(Fixture.league_id.in_(leagues))
        .distinct()  # Avoid duplicate fixtures from multiple bookmaker odds
    ).scalars().all()

    placed_bets = []
    placed = 0
    balance = get_current_balance(the_round, session)
    total_staked_today = 0.0
    client = APIFootballClient()

    for fixture in fixtures:
        if placed >= slots_remaining:
            break
        if total_staked_today >= MAX_TOTAL_STAKE_PER_DAY:
            logger.info(f"Max total stake per day ({MAX_TOTAL_STAKE_PER_DAY}) reached")
            break

        remaining_balance = balance - total_staked_today
        if remaining_balance < MIN_STAKE:
            break

        odds_row = session.execute(
            select(FixtureOdds)
            .where(FixtureOdds.fixture_id == fixture.id)
        ).scalars().first()

        if not odds_row:
            continue

        all_candidates = []
        for market in BET_MARKETS:
            model_probs = get_model_prediction(market, fixture.home_team_id, fixture.away_team_id)
            if not model_probs:
                continue

            candidates = find_value_bets(
                model_probs=model_probs,
                odds_row=odds_row,
                market=market,
                fixture_id=fixture.id,
                ev_threshold=EV_THRESHOLD_BET,
            )
            all_candidates.extend(candidates)

        if not all_candidates:
            continue

        all_candidates.sort(key=lambda x: x['ev'], reverse=True)
        candidate = all_candidates[0]

        if candidate['decimal_odd'] < MIN_ODDS or candidate['decimal_odd'] > MAX_ODDS:
            continue

        ev = candidate['ev']
        if ev < EV_THRESHOLD_BET:
            continue

        kf = candidate['kelly_fraction']
        if kf < 0.01:
            continue

        stake_amount = kelly_stake(
            remaining_balance, candidate['our_prob'], candidate['decimal_odd'],
            KELLY_FRACTION, MAX_STAKE_PCT
        )
        stake_amount = round(max(MIN_STAKE, min(stake_amount, MAX_STAKE, remaining_balance)), 2)

        if stake_amount < MIN_STAKE:
            continue

        existing = session.execute(
            select(PlacedBet)
            .where(PlacedBet.fixture_id == fixture.id)
            .where(PlacedBet.round_id == the_round.id)
            .where(PlacedBet.market == candidate['market'])
            .where(PlacedBet.outcome == candidate['outcome'])
            .where(PlacedBet.settled == False)
        ).scalars().first()

        if existing:
            continue

        home_team = session.execute(select(Team).where(Team.id == fixture.home_team_id)).scalar_one_or_none()
        away_team = session.execute(select(Team).where(Team.id == fixture.away_team_id)).scalar_one_or_none()
        league = session.execute(select(League).where(League.id == fixture.league_id)).scalar_one_or_none()

        active_version = session.execute(
            select(ModelVersion).where(
                ModelVersion.market == candidate['market'],
                ModelVersion.is_active == True
            )
        ).scalar_one_or_none()
        model_version_id = active_version.id if active_version else None

        bet = PlacedBet(
            round_id=the_round.id,
            fixture_id=fixture.id,
            market=candidate['market'],
            model_version_id=model_version_id,
            outcome=candidate['outcome'],
            stake=stake_amount,
            odds=candidate['decimal_odd'],
            our_prob=candidate['our_prob'],
            ev=ev,
            kelly_fraction=kf,
        )
        session.add(bet)
        total_staked_today += stake_amount
        placed += 1

        home_name = home_team.name if home_team else str(fixture.home_team_id)
        away_name = away_team.name if away_team else str(fixture.away_team_id)
        league_name = league.name if league else ""

        placed_bets.append({
            'home': home_name,
            'away': away_name,
            'league': league_name,
            'market': candidate['market'],
            'outcome': candidate['outcome'],
            'odds': candidate['decimal_odd'],
            'prob': candidate['our_prob'],
            'ev': ev,
            'kelly': kf,
            'stake': stake_amount,
            'fixture_date': fixture.date.strftime('%Y-%m-%d %H:%M') if fixture.date else '',
        })

        logger.info(f"BET #{the_round.round_number} | {home_name} vs {away_name} | "
                    f"{candidate['market']}:{candidate['outcome']} @ {candidate['decimal_odd']:.2f} "
                    f"(P={candidate['our_prob']:.0%}, EV={ev:.1%}) | Stake: {stake_amount:.2f}")

    session.commit()
    logger.info(f"Placed {placed} bets, staked £{total_staked_today:.2f}")
    return placed, placed_bets


def settle_bets(the_round: BankrollRound, session) -> tuple[int, float]:
    pending = session.execute(
        select(PlacedBet)
        .where(PlacedBet.round_id == the_round.id)
        .where(PlacedBet.settled == False)
    ).scalars().all()

    settled = 0
    total_pnl = 0.0

    for bet in pending:
        fixture = session.execute(
            select(Fixture).where(Fixture.id == bet.fixture_id)
        ).scalars().first()

        if not fixture or fixture.status not in ("FT", "FTm", "AET", "PEN"):
            continue

        if fixture.goals_home is None or fixture.goals_away is None:
            continue

        actual = _get_market_result(fixture, bet.market)

        bet.actual_result = actual
        bet.won = (actual == bet.outcome)
        bet.pnl = (bet.odds - 1) * bet.stake if bet.won else -bet.stake
        bet.settled = True
        bet.settled_at = datetime.utcnow()
        settled += 1
        total_pnl += bet.pnl

        logger.info(f"SETTLED #{the_round.round_number} | {fixture.home_team_id} vs {fixture.away_team_id} | "
                    f"{bet.market}:{bet.outcome} | {'WIN' if bet.won else 'LOSS'} "
                    f"{bet.pnl:+.2f}")

    session.commit()
    logger.info(f"Settled {settled} bets, P/L: {total_pnl:+.2f}")
    return settled, total_pnl


def check_and_reset(session) -> BankrollRound:
    round = get_or_create_round(session)
    reason = check_reset_condition(round, session)

    if reason:
        archive_round(round, session, reason=reason)
        round = get_or_create_round(session)
        logger.warning(f"Bankroll reset triggered: {reason}. New round #{round.round_number}")

    return round


def show_status(round: BankrollRound | None, session):
    if not round:
        print("No active round")
        return

    balance = get_current_balance(round, session)
    pending = session.execute(
        select(func.count(PlacedBet.id))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == False)
    ).scalar() or 0

    settled = session.execute(
        select(func.count(PlacedBet.id))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == True)
    ).scalars().first() or 0

    wins = session.execute(
        select(func.count(PlacedBet.id))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == True)
        .where(PlacedBet.won == True)
    ).scalars().first() or 0

    total_staked = session.execute(
        select(func.sum(PlacedBet.stake))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == True)
    ).scalar() or 0.0

    total_pnl = session.execute(
        select(func.sum(PlacedBet.pnl))
        .where(PlacedBet.round_id == round.id)
        .where(PlacedBet.settled == True)
    ).scalar() or 0.0

    roi = (total_pnl / total_staked * 100) if total_staked > 0 else 0

    print(f"\n=== Bankroll Round #{round.round_number} ===")
    print(f"  Balance:     {balance:.2f} / {round.initial_bankroll:.2f}")
    print(f"  ROI:         {roi:.1f}%")
    print(f"  Bets:        {settled} settled, {pending} pending ({wins} wins)")
    print(f"  Staked:      {total_staked:.2f} | P&L: {total_pnl:+.2f}")
    print(f"  Started:     {round.started_at}")
    print(f"  Active:      {'Yes' if round.is_active else 'No'}")


def show_history(session, limit: int = 10):
    rounds = session.execute(
        select(BankrollRound)
        .order_by(BankrollRound.round_number.desc())
        .limit(limit)
    ).scalars().all()

    print(f"\n=== Bankroll History (last {len(rounds)} rounds) ===")
    print(f"{'#':>3} | {'Start':>8} | {'End':>8} | {'Staked':>7} | {'P&L':>7} | {'ROI':>6} | {'Bets':>4} | {'Wins':>4} | Reason")
    print("-" * 90)
    for r in reversed(rounds):
        status = "ACTIVE" if r.is_active else "done"
        print(f"#{r.round_number:>3} | {r.initial_bankroll:>8.2f} | "
              f"{r.ending_balance if r.ending_balance else get_current_balance(r, session):>8.2f} | "
              f"{r.total_staked:>7.2f} | {r.total_pnl:>7.2f} | "
              f"{r.roi_pct:>5.1f}% | {r.total_bets:>4} | {r.total_wins:>4} | "
              f"{r.reason or ''} [{status}]")


def send_bets_alert(bets: list[dict], round_num: int):
    """Send Discord alert with nicely formatted placed bets."""
    if not bets:
        return

    from src.betting.alerts import BettingAlerts
    alerts = BettingAlerts(channels=["discord"], min_ev=5.0, min_odds=1.5, min_kelly=0.03)

    total_stake = sum(b['stake'] for b in bets)

    msg = f"🤖 **#{round_num} PLACED {len(bets)} BET(S)**\n\n"

    for i, bet in enumerate(bets, 1):
        market_emoji = {
            'btts': '⚽',
            'ou25': '🥅',
            'ou15': '🥅',
            'h2h': '🏆',
        }.get(bet['market'], '📊')

        outcome_emoji = {
            'Yes': '✅', 'No': '❌',
            'Over': '⬆️', 'Under': '⬇️',
            '1': '🏠', 'X': '⬜', '2': '✈️',
        }.get(bet['outcome'], bet['outcome'])

        msg += f"{market_emoji} **{bet['home']}** vs **{bet['away']}**\n"
        msg += f"   └ {bet['market'].upper()} **{bet['outcome']}** {outcome_emoji} @ {bet['odds']:.2f}\n"
        msg += f"   └ P={bet['prob']:.0%} | EV={bet['ev']:.1%} | Kelly={bet['kelly']:.0%}\n"
        msg += f"   └ Stake: £{bet['stake']:.2f} | {bet['league']} | {bet['fixture_date']}\n"
        msg += "\n"

    msg += f"─────────────────────\n"
    msg += f"Total stake: £{total_stake:.2f}\n"

    try:
        alerts.send_message(msg)
        logger.info(f"Sent Discord alert for {len(bets)} bets")
    except Exception as e:
        logger.warning(f"Failed to send bets alert: {e}")


def run_pipeline(league_ids: list[int] | None = None, bet_only: bool = False, settle_only: bool = False, round_id: int | None = None):
    init_db()
    client = APIFootballClient()

    with get_session() as session:
        # Use provided round_id arg first, else read from file (set by settle_fixtures)
        # else get from DB (creates if needed)
        if round_id is None:
            from src.betting.round_manager import get_active_round_id
            round_id = get_active_round_id(session, create=True)

        the_round = session.execute(
            select(BankrollRound).where(BankrollRound.id == round_id)
        ).scalars().first()

        if not the_round:
            the_round = get_or_create_round(session)
            round_id = the_round.id

        logger.info(f"Auto-bet pipeline | Round #{the_round.round_number} | "
                     f"Balance: {get_current_balance(the_round, session):.2f}")

        placed_bets = []

        if not settle_only:
            placed, placed_bets = place_bets(the_round, session, league_ids)

            if placed_bets:
                send_bets_alert(placed_bets, the_round.round_number)

        if not bet_only:
            settled, total_pnl = settle_bets(the_round, session)
            if settled > 0:
                alerts = BettingAlerts(channels=["discord"], min_ev=5.0, min_odds=1.5, min_kelly=0.03)
                alerts.send_message(
                    f"🔔 **SETTLED {settled} BET(S)**\n"
                    f"P/L: {total_pnl:+.2f}\n"
                    f"Round #{the_round.round_number} Balance: {get_current_balance(the_round, session):.2f}"
                )

            the_round = check_and_reset(session)
            round_id = the_round.id

        balance = get_current_balance(the_round, session)
        logger.info(f"Pipeline complete | Round #{the_round.round_number} | Balance: {balance:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automatic betting bot")
    parser.add_argument("--bet-only", action="store_true", help="Only place bets, don't settle")
    parser.add_argument("--settle-only", action="store_true", help="Only settle pending bets")
    parser.add_argument("--status", action="store_true", help="Show current bankroll status")
    parser.add_argument("--history", action="store_true", help="Show historical rounds")
    parser.add_argument("--new-round", action="store_true", help="Force start new round")
    parser.add_argument("--reset", type=str, help="Reset bankroll to starting amount")
    parser.add_argument("--leagues", type=str, help="Comma-separated league IDs")
    args = parser.parse_args()

    init_db()
    league_ids = [int(x) for x in args.leagues.split(",")] if args.leagues else None

    with get_session() as session:
        if args.status:
            round = get_or_create_round(session)
            show_status(round, session)
        elif args.history:
            show_history(session)
        elif args.new_round:
            round = get_or_create_round(session)
            archive_round(round, session, reason="manual_reset")
            new_round = get_or_create_round(session)
            print(f"New round #{new_round.round_number} started")
        elif args.reset:
            round = get_or_create_round(session)
            archive_round(round, session, reason=f"reset_{args.reset}")
            new_round = get_or_create_round(session)
            print(f"Reset. New round #{new_round.round_number} with balance {INITIAL_BANKROLL}")
        else:
            run_pipeline(league_ids=league_ids, bet_only=args.bet_only, settle_only=args.settle_only)
