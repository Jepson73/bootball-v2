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
    """Determine actual result from fixture goals."""
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
        return "Yes" if (fixture.goals_home > 0 and fixture.goals_away > 0) else "No"
    elif market == "ou25":
        return "Over" if total > 2.5 else "Under"
    elif market == "ou15":
        return "Over" if total > 1.5 else "Under"

    return None


def fetch_and_update_fixtures(days: int = 7) -> int:
    """Fetch completed fixtures from API and update DB. Returns count of updated fixtures."""
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
                if existing.status != "FT":
                    existing.status = "FT"
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    updated += 1
                    logger.info(f"Fixture {fix_id} completed: {home_goals}-{away_goals}")
                elif existing.goals_home != home_goals:
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    logger.info(f"Fixture {fix_id} score corrected: {home_goals}-{away_goals}")

        if updated > 0:
            s.commit()
            logger.info(f"Updated {updated} fixtures")

    return updated


def settle_placed_bets() -> tuple[int, float, list[dict]]:
    """Settle pending PlacedBets based on completed fixtures.

    Returns (count, total_pnl, bet_details).
    bet_details is a list of dicts with: home, away, league, market, outcome, odds, result, won, pnl, stake
    """
    from src.storage.models import Team, League

    with get_session() as s:
        pending_bets = s.execute(
            select(PlacedBet).where(PlacedBet.settled == False)
        ).scalars().all()

        total_pnl = 0.0
        settled = 0
        bet_details = []

        for bet in pending_bets:
            fixture = s.execute(select(Fixture).where(Fixture.id == bet.fixture_id)).scalar_one_or_none()

            if not fixture or fixture.status != "FT" or fixture.goals_home is None:
                continue

            result = get_market_result(fixture, bet.market)

            if result is None:
                continue

            bet.settled = True
            bet.result = result
            bet.pnl = ((bet.odds - 1) * bet.stake) if bet.outcome == result else (-bet.stake)
            bet.settled_at = datetime.utcnow()
            bet.won = (bet.outcome == result)

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

            round_id = pending_bets[0].round_id if pending_bets else None
            if round_id:
                round_obj = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id)).scalar_one_or_none()
                if round_obj:
                    from src.storage.models import BankrollRound
                    round_obj = s.execute(select(BankrollRound).where(BankrollRound.id == round_id)).scalar_one_or_none()
                    if round_obj:
                        round_obj.total_pnl = (round_obj.total_pnl or 0) + total_pnl
                        s.commit()

            logger.info(f"Settled {settled} bets, P/L: {total_pnl:+.2f}")

            if bet_details:
                send_settlement_alert(bet_details, total_pnl)

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
        msg += f"{wins}W / {losses}L | P/L: {total_pnl:+.2f} | Stake: £{total_stake:.2f}\n"
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


def settle_all() -> dict:
    """Settle pending bets. Does NOT fetch fixtures - caller should fetch first."""
    init_db()

    settled, total_pnl, bet_details = settle_placed_bets()

    return {
        'bets_settled': settled,
        'total_pnl': total_pnl,
        'bet_details': bet_details,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    result = settle_all()
    print(f"Done. Updated: {result['fixtures_updated']}, Settled: {result['bets_settled']}, P/L: {result['total_pnl']:+.2f}")