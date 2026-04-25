"""
src/settlement.py - Shared settlement logic for fixtures and bets.

Used by:
- scripts/settle_fixtures.py (standalone script)
- scripts/web_ui.py (betting dashboard settle)
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select

from config.leagues import ALL_LEAGUE_IDS
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, PlacedBet, Team, League

logger = logging.getLogger(__name__)


def get_market_result(fixture: Fixture, market: str) -> str | None:
    """Determine actual result from fixture goals.
    
    For markets that can be determined before FT, returns the result.
    For markets that require FT (like BTTS No), returns None until FT.
    """
    if fixture.goals_home is None or fixture.goals_away is None:
        return None

    if market == "h2h":
        if fixture.goals_home > fixture.goals_away:
            return "1"
        elif fixture.goals_home < fixture.goals_away:
            return "2"
        return "X"

    total = fixture.goals_home + fixture.goals_away

    if market == "btts":
        # BTTS Yes can be settled early once both have scored
        # BTTS No must wait for FT
        if fixture.goals_home > 0 and fixture.goals_away > 0:
            return "Yes"
        elif fixture.status in ["FT", "FTm", "AET", "PEN"]:
            return "No"
        return None  # Can't determine BTTS No until FT
    elif market == "ou25":
        return "Over" if total > 2.5 else "Under"
    elif market == "ou15":
        return "Over" if total > 1.5 else "Under"

    return None


def can_settle_early(fixture: Fixture, market: str) -> bool:
    """Check if a market outcome is mathematically certain before FT."""
    if fixture.goals_home is None or fixture.goals_away is None:
        return False
    
    if fixture.status in ["FT", "FTm", "AET", "PEN"]:
        return True
    
    total = fixture.goals_home + fixture.goals_away
    
    if market == "btts":
        # Both teams have scored - BTTS Yes is guaranteed
        return fixture.goals_home > 0 and fixture.goals_away > 0
    elif market == "ou25":
        # Already over 2.5 or mathematically impossible to reach
        return total > 2.5 or total < 2.5
    elif market == "ou15":
        return total > 1.5 or total < 1.5
    elif market == "h2h":
        # Can't determine winner until FT
        return False
    
    return False


def update_live_fixture_statuses() -> int:
    """Fetch live/in-play fixtures and update their status in DB."""
    from config.leagues import ALL_LEAGUE_IDS
    client = APIFootballClient()
    from datetime import date as date_type
    today = date_type.today().strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    current_year = date_type.today().year

    updated = 0
    with get_session() as s:
        for lid in ALL_LEAGUE_IDS:
            # Also fetch FT to catch matches that finished since last update
            for status in ['LIVE', '2H', '1H', 'HT', 'FT']:
                try:
                    season = current_year if lid in [1602, 253, 98, 176, 113, 1191] else current_year - 1
                    raw = client.get_fixtures(league_id=lid, season=season, from_date=today, to_date=tomorrow, status=status)
                    if raw:
                        for r in raw:
                            fix_id = r.get('fixture', {}).get('id')
                            if not fix_id:
                                continue
                            fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
                            if not fix:
                                continue
                            goals = r.get('goals', {})
                            new_status = r.get('fixture', {}).get('status', {}).get('short', '')
                            if new_status and new_status != fix.status:
                                fix.status = new_status
                                if goals.get('home') is not None:
                                    fix.goals_home = goals.get('home')
                                if goals.get('away') is not None:
                                    fix.goals_away = goals.get('away')
                                updated += 1
                except Exception:
                    pass
        if updated > 0:
            s.commit()
    return updated


def fetch_and_update_fixtures(days: int = 7, force_refetch_hours: int = 3) -> int:
    """Fetch completed fixtures from API and update DB. Returns count of updated fixtures.

    Args:
        days: How many days back to fetch
        force_refetch_hours: For fixtures older than this that lack FT status, force API fetch.
    """
    client = APIFootballClient()
    from datetime import date as date_type

    cutoff = datetime.utcnow() - timedelta(days=days)
    today_str = date_type.today().strftime("%Y-%m-%d")
    current_year = date_type.today().year

    logger.info(f"Fetching completed fixtures (past {days} days)")

    completed = []
    for lid in ALL_LEAGUE_IDS[:20]:
        season = current_year if lid in [1602, 253, 98, 176, 113, 1191] else current_year - 1
        try:
            results = client.get_fixtures(
                league_id=lid,
                season=season,
                from_date=cutoff.strftime("%Y-%m-%d"),
                to_date=today_str,
                status="FT",
            )
            if results:
                completed.extend(results)
        except Exception as e:
            logger.warning(f"Error fetching league {lid}: {e}")

    logger.info(f"Got {len(completed)} completed fixtures")

    updated = 0
    with get_session() as s:
        for fix_data in completed:
            fix_info = fix_data.get("fixture", {})
            fix_id = fix_info.get("id")
            if not fix_id:
                continue

            goals = fix_data.get("goals", {})
            home_goals = goals.get("home")
            away_goals = goals.get("away")

            if home_goals is None:
                continue

            existing = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()

            if existing:
                needs_update = False
                # Update if not already FT/FTm/AET/PEN
                if existing.status not in ["FT", "FTm", "AET", "PEN"]:
                    existing.status = "FT"
                    needs_update = True
                # Update FTm → FT if API confirms (API never returns FTm)
                elif existing.status == "FTm" and status_short == "FT":
                    existing.status = "FT"
                    needs_update = True
                if needs_update or existing.goals_home != home_goals:
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    if needs_update:
                        updated += 1
                        logger.info(f"Fixture {fix_id} completed: {home_goals}-{away_goals} (was {existing.status})")
                    else:
                        logger.info(f"Fixture {fix_id} score corrected: {home_goals}-{away_goals}")

        if updated > 0:
            s.commit()
            logger.info(f"Updated {updated} fixtures")

    # Refresh NS fixtures that have started (kickoff time has passed)
    started_updated = _refresh_started_fixtures()
    updated += started_updated

    # Force-fetch stale fixtures: not FT but >force_refetch_hours old
    stale_updated = _fetch_stale_fixtures(force_refetch_hours)
    updated += stale_updated

    # Fix stuck fixtures: FTm/PEN/AET already final, 1H/2H stale
    stuck_updated = _fix_stuck_fixtures()
    updated += stuck_updated

    return updated


def _refresh_started_fixtures() -> int:
    """Refresh fixtures that were NS but kickoff time has passed.
    
    Finds fixtures where:
    - status == 'NS'
    - kickoff time < now (already started)
    - fetch from API to get current status
    """
    now = datetime.utcnow()
    client = APIFootballClient()
    updated = 0
    
    # Get fixture IDs first (single session)
    with get_session() as s:
        started = s.execute(
            select(Fixture.id).where(
                Fixture.status == 'NS',
                Fixture.date < now
            )
        ).scalars().all()
    
    if not started:
        return 0
    
    logger.info(f"Checking {len(started)} NS fixtures that have started")
    
    # Update each fixture in single session
    with get_session() as s:
        for fix_id in started:
            try:
                raw = client.get_fixtures(fixture_id=fix_id, force_refresh=True)
                if not raw:
                    continue
                fix_data = raw[0]
                status = fix_data.get('fixture', {}).get('status', {})
                status_short = status.get('short', '') if isinstance(status, dict) else status
                
                if status_short and status_short != 'NS':
                    fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one()
                    fix.status = status_short
                    goals = fix_data.get('goals', {})
                    home_goals = goals.get('home')
                    away_goals = goals.get('away')
                    if home_goals is not None:
                        fix.goals_home = home_goals
                        fix.goals_away = away_goals
                    logger.info(f"Refreshed started fixture {fix_id}: status={status_short}")
                    updated += 1
            except Exception as e:
                logger.warning(f"Failed to refresh fixture {fix_id}: {e}")
        
        if updated > 0:
            s.commit()
    
    logger.info(f"Refreshed {updated} started fixtures")
    return updated


def _fetch_stale_fixtures(hours: int = 3) -> int:
    """Force-fetch fixtures that should be finished but aren't marked FT.

    These are fixtures where:
    - status != 'FT' (not completed)
    - kickoff > hours ago (should be done)
    - goals_home is None (no score data)
    """
    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    fixture_ids = []
    with get_session() as s:
        stale = s.execute(
            select(Fixture).where(
                Fixture.date < cutoff,
                Fixture.status.notin_(["FT", "AET", "PEN", "FTm"]),
                Fixture.goals_home == None,
            )
        ).scalars().all()
        fixture_ids = [f.id for f in stale]

    if not fixture_ids:
        return 0

    logger.info(f"Found {len(fixture_ids)} stale fixtures needing force-fetch")

    updated = 0
    with get_session() as s:
        for fix_id in fixture_ids:
            try:
                fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
                if not fix:
                    continue

                raw = client.get_fixtures(fixture_id=fix_id)
                if not raw:
                    continue
                fix_data = raw[0]
                goals = fix_data.get("goals", {})
                status = fix_data.get("fixture", {}).get("status", {})
                status_short = status.get("short", "") if isinstance(status, dict) else status

                home_goals = goals.get("home")
                away_goals = goals.get("away")

                changed = False
                if home_goals is not None:
                    fix.goals_home = home_goals
                    fix.goals_away = away_goals
                    changed = True
                if status_short:
                    fix.status = status_short
                    changed = True

                if changed and status_short == "FT" and home_goals is not None:
                    logger.info(f"Force-updated fixture {fix_id}: {home_goals}-{away_goals} ({status_short})")
                    updated += 1
            except Exception as e:
                logger.warning(f"Force-fetch failed for fixture {fix_id}: {e}")

        if updated > 0:
            s.commit()
            logger.info(f"Force-updated {updated} stale fixtures")

    return updated


def _fix_stuck_fixtures() -> int:
    """Fix fixtures stuck in non-FT final statuses.

    Handles:
    - FTm, AET, PEN: Already have final scores, treat as settled
    - 1H, 2H with kickoff > 3 hours ago: Force-fetch from API

    Returns count of fixtures fixed.
    """
    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(hours=3)
    updated = 0

    with get_session() as s:
        # Fix FTm, AET, PEN fixtures - they already have scores
        final_statuses = ["FTm", "AET", "PEN"]
        non_ft = s.execute(
            select(Fixture).where(Fixture.status.in_(final_statuses))
        ).scalars().all()

        for fix in non_ft:
            if fix.goals_home is not None:
                # Already has scores, mark as effectively settled
                logger.info(f"Fixture {fix.id} already finished: {fix.goals_home}-{fix.goals_away} ({fix.status})")
                updated += 1

        # Force-fetch 1H/2H fixtures > 3 hours old
        stale_with_scores = s.execute(
            select(Fixture).where(
                Fixture.date < cutoff,
                Fixture.status.in_(["1H", "2H", "HT"]),
            )
        ).scalars().all()

        for fix in stale_with_scores:
            try:
                raw = client.get_fixtures(fixture_id=fix.id)
                if not raw:
                    continue
                fix_data = raw[0]
                goals = fix_data.get("goals", {})
                status = fix_data.get("fixture", {}).get("status", {})
                status_short = status.get("short", "") if isinstance(status, dict) else status

                home_goals = goals.get("home")
                away_goals = goals.get("away")

                if home_goals is not None:
                    fix.goals_home = home_goals
                    fix.goals_away = away_goals
                if status_short:
                    fix.status = status_short
                    logger.info(f"Updated stuck fixture {fix.id}: {fix.goals_home}-{fix.goals_away} ({status_short})")
                    updated += 1
            except Exception as e:
                logger.warning(f"Failed to update stuck fixture {fix.id}: {e}")

        if updated > 0:
            s.commit()
            logger.info(f"Fixed {updated} stuck fixtures")

    return updated


def settle_placed_bets(days: int | None = None) -> tuple[int, float, list[dict]]:
    """Settle pending PlacedBets based on completed fixtures.

    Args:
        days: If provided, only settle bets for fixtures from the last N days.

    Returns (count, total_pnl, bet_details).
    bet_details is a list of dicts with: home, away, league, market, outcome, odds, result, won, pnl, stake
    """
    from src.storage.models import Team, League

    with get_session() as s:
        query = select(PlacedBet).where(PlacedBet.settled == False)
        
        if days is not None:
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = query.where(PlacedBet.placed_at >= cutoff)
        
        pending_bets = s.execute(query).scalars().all()

        total_pnl = 0.0
        settled = 0
        bet_details = []

        for bet in pending_bets:
            fixture = s.execute(select(Fixture).where(Fixture.id == bet.fixture_id)).scalar_one_or_none()

            if not fixture or fixture.goals_home is None or fixture.goals_away is None:
                continue
            
            if not can_settle_early(fixture, bet.market):
                continue

            result = get_market_result(fixture, bet.market)

            if result is None:
                continue

            bet.settled = True
            bet.result = result
            bet.pnl = ((bet.odds - 1) * bet.stake) if str(bet.outcome).lower() == str(result).lower() else (-bet.stake)
            bet.settled_at = datetime.utcnow()
            bet.won = (str(bet.outcome).lower() == str(result).lower())

            total_pnl += bet.pnl
            settled += 1

            home_team = s.execute(select(Team).where(Team.id == fixture.home_team_id)).scalar_one_or_none()
            away_team = s.execute(select(Team).where(Team.id == fixture.away_team_id)).scalar_one_or_none()
            league = s.execute(select(League).where(League.id == fixture.league_id)).scalar_one_or_none()

            bet_details.append({
                'home': home_team.name if home_team else str(fixture.home_team_id),
                'away': away_team.name if away_team else str(fixture.away_team_id),
                'league': league.name if league else '',
                'market': bet.market,
                'outcome': bet.outcome,
                'odds': bet.odds,
                'result': result,
                'won': bet.won,
                'pnl': bet.pnl,
                'stake': bet.stake,
            })

            logger.info(f"Bet {bet.id}: {bet.market} {bet.outcome} @ {bet.odds} → {result} | P/L: {bet.pnl:+.2f}")

        if settled > 0:
            s.commit()

            round_id = pending_bets[0].round_id if pending_bets and len(pending_bets) > 0 else None
            if round_id:
                from src.storage.models import BankrollRound
                try:
                    round_obj = s.execute(select(BankrollRound).where(BankrollRound.id == round_id)).scalar_one_or_none()
                    if round_obj:
                        round_obj.total_pnl = (round_obj.total_pnl or 0) + total_pnl
                        s.commit()
                except Exception as e:
                    logger.warning(f"Could not update round: {e}")

            logger.info(f"Settled {settled} bets, P/L: {total_pnl:+.2f}")

            if bet_details:
                send_settlement_alert(bet_details, total_pnl)
                
                from src.alerts.event_bus import event_bus, Events
                event_bus.emit(Events.BET_SETTLED, {
                    "settled_count": settled,
                    "total_pnl": total_pnl,
                    "wins": sum(1 for b in bet_details if b.get('won')),
                    "losses": sum(1 for b in bet_details if not b.get('won')),
                    "summary": f"Settled {settled} bets, P/L: {total_pnl:+.2f}"
                })

        logger.info(f"Done. Settled: {settled}, P/L: {total_pnl:+.2f}")

        return settled, total_pnl, bet_details


def send_settlement_alert(bet_details: list[dict], total_pnl: float):
    """Send Discord alert with nicely formatted settled bet results."""
    if not bet_details:
        return

    try:
        from src.betting.alerts import BettingAlerts

        wins = sum(1 for b in bet_details if b['won'])
        losses = len(bet_details) - wins
        total_stake = sum(b['stake'] for b in bet_details)

        msg = f"**SETTLED {len(bet_details)} BET(S)**\n"
        msg += f"{wins}W / {losses}L | P/L: {total_pnl:+.2f} | Stake: SEK {total_stake:.2f}\n"
        msg += "─────────────────────\n\n"

        for bet in bet_details:
            market_emoji = {
                'btts': '⚽',
                'ou25': '🥅',
                'ou15': '🥅',
                'h2h': '🏆',
            }.get(bet['market'], '📊')

            result_emoji = '💰' if bet['won'] else '💩'

            msg += f"{market_emoji} **{bet['home']}** vs **{bet['away']}**\n"
            msg += f"   └ {bet['market'].upper()} **{bet['outcome']}** @ {bet['odds']:.2f}\n"
            msg += f"   └ Result: **{bet['result']}** {result_emoji} | P/L: {bet['pnl']:+.2f}\n"
            msg += f"   └ {bet['league']}\n\n"

        msg += "─────────────────────\n"
        msg += f"Net P/L: {total_pnl:+.2f}"

        alerts = BettingAlerts(channels=["discord"], min_ev=5.0, min_odds=1.5, min_kelly=0.03)
        alerts.send_message(msg)
        logger.info(f"Sent settlement alert")
    except Exception as e:
        logger.warning(f"Failed to send settlement alert: {e}")


def settle_all(days: int | None = None) -> dict:
    """Settle pending bets and predictions. Does NOT fetch fixtures - caller should fetch first.

    Args:
        days: If provided, only settle predictions/bets for fixtures from the last N days.
              None means settle all unsettled predictions.

    Settles:
    - PlacedBets: actual bets placed by auto_bet (affects bankroll tracking)
    - PredictionRecords: model predictions for tracking accuracy
    - ValueBets: potential bet opportunities from daily_run (for analysis)
    """
    init_db()

    bets_settled, bets_pnl, bet_details = settle_placed_bets(days=days)
    preds_settled = settle_predictions(days=days)
    value_bets_settled = settle_value_bets(days=days)

    return {
        'bets_settled': bets_settled,
        'predictions_settled': preds_settled,
        'value_bets_settled': value_bets_settled,
        'bets_pnl': bets_pnl,
        'bet_details': bet_details,
    }


def settle_predictions(days: int | None = None) -> int:
    """Settle unsettled PredictionRecords for completed fixtures.

    Args:
        days: If provided, only settle predictions for fixtures from the last N days.

    Returns count of settled predictions.
    """
    from src.storage.models import PredictionRecord, Fixture
    from sqlalchemy import and_

    settled = 0
    with get_session() as s:
        query = (
            select(PredictionRecord, Fixture)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.settled == False)
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
            .where(Fixture.status.in_(['FT', 'FTm', 'AET', 'PEN']))  # Only settle finished games
        )

        if days is not None:
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = query.where(Fixture.date >= cutoff)

        unsettled = s.execute(query).all()

        for pred, fixture in unsettled:
            # Check if we can determine the result now
            if not can_settle_early(fixture, pred.market):
                continue
            
            actual = get_market_result(fixture, pred.market)
            if actual:
                pred.actual_outcome = actual
                pred.won = (str(pred.predicted_outcome).lower() == str(actual).lower())
                pred.settled = True
                pred.settled_at = datetime.utcnow()
                settled += 1

        if settled > 0:
            s.commit()

    logger.info(f"Settled {settled} predictions (days={days})")
    return settled


def settle_value_bets(days: int | None = None) -> int:
    """Settle unsettled ValueBets for completed fixtures.

    ValueBets are potential bet opportunities found by daily_run.
    They are for tracking/analysis only, not bankroll.

    Args:
        days: If provided, only settle bets from the last N days.

    Returns count of settled value bets.
    Note: ValueBet table was archived, this function now returns 0.
    """
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    result = settle_all()
    print(f"Done. Updated: {result['fixtures_updated']}, Settled: {result['bets_settled']}, P/L: {result['total_pnl']:+.2f}")