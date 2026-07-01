"""
src/settlement.py - Shared settlement logic for fixtures and bets.

Used by:
- scripts/settle_fixtures.py (standalone script)
- scripts/web_ui.py (betting dashboard settle)
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import select

from src.ingestion.client import APIFootballClient
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, PlacedBet, Team, League

logger = logging.getLogger(__name__)


def get_market_result(fixture: Fixture, market: str) -> str | None:
    """Determine actual result from fixture goals.

    For irreversible early-settle outcomes (btts Yes, ou Over) returns the
    result as soon as it is mathematically certain.  For all other outcomes
    (h2h, Under, btts No) returns None unless the fixture is fully finished,
    so no code path can accidentally settle these mid-game.
    """
    if fixture.goals_home is None or fixture.goals_away is None:
        return None

    is_final = fixture.status in ["FT", "AET", "PEN"]
    total = fixture.goals_home + fixture.goals_away

    if market == "h2h":
        # h2h result can flip (goal overturned by VAR, etc.) — wait for FT
        if not is_final:
            return None
        if fixture.goals_home > fixture.goals_away:
            return "1"
        elif fixture.goals_home < fixture.goals_away:
            return "2"
        return "X"

    if market == "btts":
        if fixture.goals_home > 0 and fixture.goals_away > 0:
            return "Yes"   # irreversible — both teams have scored
        if is_final:
            return "No"    # game over without both scoring
        return None        # still live, can't rule out btts Yes yet

    if market == "ou25":
        if total > 2.5:
            return "Over"  # irreversible
        if is_final:
            return "Under"
        return None        # still live, more goals possible

    if market == "ou15":
        if total > 1.5:
            return "Over"  # irreversible
        if is_final:
            return "Under"
        return None        # still live, more goals possible

    return None


def can_settle_early(fixture: Fixture, market: str, outcome: str | None = None) -> bool:
    """Check if a bet outcome is irreversibly determined before FT.

    Only outcomes that cannot revert to a loss after VAR are eligible:
    - Over outcomes (ou15/ou25): once the threshold is crossed it stays crossed
    - btts Yes: once both teams have scored it cannot un-score
    All Under, btts No, and h2h outcomes must wait for FT.
    """
    if fixture.status in ["FT", "AET", "PEN"]:
        return True

    if fixture.goals_home is None or fixture.goals_away is None:
        return False

    total = fixture.goals_home + fixture.goals_away

    if market == "ou15":
        return outcome == "Over" and total > 1.5
    elif market == "ou25":
        return outcome == "Over" and total > 2.5
    elif market == "btts":
        return outcome == "Yes" and fixture.goals_home > 0 and fixture.goals_away > 0

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
                    from config.settings import settings as _s
                    season = _s.get_season(lid)
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
                            score = r.get('score', {})
                            new_status = r.get('fixture', {}).get('status', {}).get('short', '')
                            if not new_status:
                                continue
                            changed = False
                            if new_status != fix.status:
                                fix.status = new_status
                                changed = True
                            new_home = goals.get('home')
                            new_away = goals.get('away')
                            if new_home is not None and new_home != fix.goals_home:
                                fix.goals_home = new_home
                                changed = True
                            if new_away is not None and new_away != fix.goals_away:
                                fix.goals_away = new_away
                                changed = True
                            # Persist halftime scores when available
                            ht = score.get('halftime', {})
                            ht_home = ht.get('home')
                            ht_away = ht.get('away')
                            if ht_home is not None and ht_home != fix.ht_goals_home:
                                fix.ht_goals_home = ht_home
                                changed = True
                            if ht_away is not None and ht_away != fix.ht_goals_away:
                                fix.ht_goals_away = ht_away
                                changed = True
                            if changed:
                                updated += 1
                except Exception:
                    pass
        if updated > 0:
            s.commit()
    return updated


def update_pending_fixture_scores() -> int:
    """Fetch live scores for all currently live matches globally (3 API calls).

    Uses date+status queries (1H, 2H, HT) instead of per-fixture or per-league
    lookups — those either require the paid 'ids' plan feature or consume 6000+
    API calls per run.  This approach costs exactly 3 calls and returns every
    live match on the planet.
    """
    client = APIFootballClient()
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")

    all_live: list[dict] = []
    for status in ["1H", "2H", "HT", "ET", "BT", "P", "INT"]:
        try:
            raw = client.get_fixtures(date=today, status=status, force_refresh=True)
            if raw:
                all_live.extend(raw)
        except Exception as e:
            logger.warning("update_pending_fixture_scores: %s fetch error: %s", status, e)

    if not all_live:
        return 0

    updated = 0
    with get_session() as s:
        for fix_data in all_live:
            fix_info = fix_data.get("fixture", {})
            fix_id = fix_info.get("id")
            if not fix_id:
                continue
            fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
            if not fix:
                continue
            goals = fix_data.get("goals", {})
            new_status = fix_info.get("status", {}).get("short", "")
            elapsed = fix_info.get("status", {}).get("elapsed")
            changed = False
            if new_status and new_status != fix.status:
                fix.status = new_status
                changed = True
            if elapsed is not None and getattr(fix, "elapsed", None) != elapsed:
                try:
                    fix.elapsed = elapsed
                    changed = True
                except Exception:
                    pass
            for attr, val in [("goals_home", goals.get("home")), ("goals_away", goals.get("away"))]:
                if val is not None and getattr(fix, attr) != val:
                    setattr(fix, attr, val)
                    changed = True
            if changed:
                try:
                    fix.fetched_at = datetime.utcnow()
                except Exception:
                    pass
                updated += 1

        if updated > 0:
            s.commit()

    logger.info("update_pending_fixture_scores: updated %d fixtures (checked %d live globally)", updated, len(all_live))
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

    from config.settings import settings as _s
    from sqlalchemy import func as _func

    # Derive league IDs dynamically from fixtures that matter:
    # - leagues with unsettled bets (must fetch their FT results)
    # - leagues with recent NS/in-play fixtures (need score updates)
    with get_session() as s:
        bet_league_ids = s.execute(
            select(Fixture.league_id)
            .join(PlacedBet, PlacedBet.fixture_id == Fixture.id)
            .where(PlacedBet.settled == False)
            .distinct()
        ).scalars().all()

        recent_cutoff = datetime.utcnow() - timedelta(days=days + 1)
        fixture_league_ids = s.execute(
            select(Fixture.league_id)
            .where(Fixture.date >= recent_cutoff)
            .distinct()
        ).scalars().all()

    active_league_ids = list(set(list(bet_league_ids) + list(fixture_league_ids)))
    logger.info(f"Fetching FT results for {len(active_league_ids)} active leagues")

    completed = []
    for lid in active_league_ids:
        season = _s.get_season(lid)
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
                # Update if not already FT/AET/PEN
                if existing.status not in ["FT", "AET", "PEN"]:
                    existing.status = "FT"
                    needs_update = True
                if needs_update or existing.goals_home != home_goals:
                    existing.goals_home = home_goals
                    existing.goals_away = away_goals
                    if home_goals > away_goals:
                        existing.outcome = "H"
                    elif away_goals > home_goals:
                        existing.outcome = "A"
                    else:
                        existing.outcome = "D"
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

    # Fix stuck fixtures: PEN/AET already final, 1H/2H stale
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
                Fixture.status.notin_(["FT", "AET", "PEN"]),
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
    - AET, PEN: Already have final scores, treat as settled
    - 1H, 2H with kickoff > 3 hours ago: Force-fetch from API

    Returns count of fixtures fixed.
    """
    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(hours=3)
    updated = 0

    with get_session() as s:
        # Fix AET, PEN fixtures - they already have scores
        final_statuses = ["AET", "PEN"]
        non_ft = s.execute(
            select(Fixture).where(Fixture.status.in_(final_statuses))
        ).scalars().all()

        for fix in non_ft:
            if fix.goals_home is not None:
                # Already has scores, mark as effectively settled
                logger.info(f"Fixture {fix.id} already finished: {fix.goals_home}-{fix.goals_away} ({fix.status})")
                updated += 1

        # Force-fetch 1H/2H/P fixtures > 3 hours old
        # 'P' = Penalty In Progress — should have reached PEN within minutes; stale means missed update
        stale_with_scores = s.execute(
            select(Fixture).where(
                Fixture.date < cutoff,
                Fixture.status.in_(["1H", "2H", "HT", "P"]),
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


def backfill_missing_scores(days: int = 14) -> int:
    """Fetch and store scores for FT fixtures that are missing goals_home/goals_away.

    Called before settlement so those fixtures can be settled in the same pass.
    Returns the number of fixtures updated.
    """
    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(days=days)
    updated = 0

    with get_session() as s:
        missing = s.execute(
            select(Fixture)
            .where(Fixture.status.in_(["FT", "AET", "PEN"]))
            .where(Fixture.goals_home.is_(None))
            .where(Fixture.date >= cutoff)
        ).scalars().all()

        if not missing:
            return 0

        logger.info(f"[SETTLEMENT] Backfilling scores for {len(missing)} finished fixtures")
        ids = [f.id for f in missing]
        id_map = {f.id: f for f in missing}

        # Fetch in chunks of 20 (API limit)
        for i in range(0, len(ids), 20):
            chunk = ids[i:i+20]
            try:
                raw = client.get("fixtures", {"ids": "-".join(str(x) for x in chunk)}, force_refresh=True)
            except Exception as e:
                logger.warning(f"[SETTLEMENT] Score fetch failed for chunk: {e}")
                continue

            for entry in raw:
                fid = entry.get("fixture", {}).get("id")
                goals = entry.get("goals", {})
                gh = goals.get("home")
                ga = goals.get("away")
                if fid in id_map and gh is not None and ga is not None:
                    fix = id_map[fid]
                    fix.goals_home = gh
                    fix.goals_away = ga
                    if gh > ga:
                        fix.outcome = "H"
                    elif ga > gh:
                        fix.outcome = "A"
                    else:
                        fix.outcome = "D"
                    updated += 1

        s.commit()

    logger.info(f"[SETTLEMENT] Backfilled scores for {updated} fixtures")
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
        confirmations_updated = 0
        bet_details = []

        for bet in pending_bets:
            fixture = s.execute(select(Fixture).where(Fixture.id == bet.fixture_id)).scalar_one_or_none()

            if not fixture or fixture.goals_home is None or fixture.goals_away is None:
                continue

            is_final = fixture.status in ["FT", "AET", "PEN"]
            result = get_market_result(fixture, bet.market)

            if result is None:
                continue

            if is_final:
                # Fixture is finished — settle immediately, no VAR window needed
                bet.settled = True
                bet.actual_result = result
                bet.pnl = ((bet.odds - 1) * bet.stake) if str(bet.outcome).lower() == str(result).lower() else (-bet.stake)
                bet.settled_at = datetime.utcnow()
                bet.won = (str(bet.outcome).lower() == str(result).lower())
                bet.settle_confirmations = 0
                bet.settle_pending_result = None
            elif can_settle_early(fixture, bet.market, bet.outcome):
                # Outcome is irreversible mid-game — require 3 consecutive confirmations
                # to guard against data latency or VAR reversals
                if bet.settle_pending_result != result:
                    # Result changed (or first observation) — reset counter
                    bet.settle_pending_result = result
                    bet.settle_confirmations = 1
                    logger.debug("Bet %d: early settle candidate %s, confirmation 1/3", bet.id, result)
                else:
                    bet.settle_confirmations = (bet.settle_confirmations or 0) + 1
                    logger.debug("Bet %d: early settle confirmation %d/3", bet.id, bet.settle_confirmations)

                confirmations_updated += 1
                if bet.settle_confirmations >= 3:
                    bet.settled = True
                    bet.actual_result = result
                    bet.pnl = ((bet.odds - 1) * bet.stake) if str(bet.outcome).lower() == str(result).lower() else (-bet.stake)
                    bet.settled_at = datetime.utcnow()
                    bet.won = (str(bet.outcome).lower() == str(result).lower())
                    bet.settle_confirmations = 0
                    bet.settle_pending_result = None
                    logger.info("Bet %d: settled early after 3 confirmations — %s", bet.id, result)
                else:
                    continue
            else:
                # Outcome cannot be settled early — reset any stale confirmation state
                if bet.settle_confirmations or bet.settle_pending_result:
                    bet.settle_confirmations = 0
                    bet.settle_pending_result = None
                    confirmations_updated += 1
                continue

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

        if settled > 0 or confirmations_updated > 0:
            s.commit()

        if settled > 0:
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


def fix_incorrect_settlements() -> int:
    """Re-evaluate and correct bets that were settled prematurely with wrong results.

    The old can_settle_early() contained a tautology (total > X or total < X)
    that caused ou15/ou25 bets to settle during live play before enough goals were
    scored.  This function corrects those bets against the final score for any
    fixture that has since reached FT/AET/PEN.

    Also backfills actual_result=NULL for bets settled before the bet.result ->
    bet.actual_result column-name fix.

    Safe to run multiple times — only writes when the stored value differs.
    """
    corrected = 0
    with get_session() as s:
        bets = s.execute(
            select(PlacedBet).where(
                PlacedBet.settled == True,
                PlacedBet.market.in_(["ou15", "ou25", "btts"])
            )
        ).scalars().all()

        for bet in bets:
            fixture = s.execute(
                select(Fixture).where(Fixture.id == bet.fixture_id)
            ).scalar_one_or_none()

            if not fixture or fixture.goals_home is None or fixture.goals_away is None:
                continue

            is_final = fixture.status in ["FT", "AET", "PEN"]
            if not is_final and not can_settle_early(fixture, bet.market, bet.outcome):
                continue

            correct_result = get_market_result(fixture, bet.market)
            if correct_result is None:
                continue

            if bet.actual_result != correct_result:
                bet.actual_result = correct_result
                bet.won = str(bet.outcome).lower() == str(correct_result).lower()
                bet.pnl = ((bet.odds - 1) * bet.stake) if bet.won else (-bet.stake)
                bet.settled_at = datetime.utcnow()
                corrected += 1

        if corrected > 0:
            s.commit()

    # --- PredictionRecord corrections ---
    pred_corrected = 0
    with get_session() as s:
        from src.storage.models import PredictionRecord
        preds = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.settled == True,
                PredictionRecord.market.in_(["ou15", "ou25", "btts"])
            )
        ).scalars().all()

        for pred in preds:
            fixture = s.execute(
                select(Fixture).where(Fixture.id == pred.fixture_id)
            ).scalar_one_or_none()

            if not fixture or fixture.goals_home is None or fixture.goals_away is None:
                continue

            is_final = fixture.status in ["FT", "AET", "PEN"]
            if not is_final and not can_settle_early(fixture, pred.market, pred.predicted_outcome):
                continue

            correct_result = get_market_result(fixture, pred.market)
            if correct_result is None:
                continue

            if pred.actual_outcome != correct_result:
                pred.actual_outcome = correct_result
                pred.won = str(pred.predicted_outcome).lower() == str(correct_result).lower()
                pred.settled_at = datetime.utcnow()
                pred_corrected += 1

        if pred_corrected > 0:
            s.commit()

    if pred_corrected > 0:
        logger.info(f"[SETTLE] Corrected {pred_corrected} mis-settled predictions")

    return corrected + pred_corrected


def recheck_early_loss_settlements(hours: int = 2) -> int:
    """Force-re-fetch fixture scores for Over bets recently settled as LOSS.

    The API sometimes briefly reports a game as FT with the wrong (low) score
    while the match is still in progress.  When that happens:
      1. update_pending_fixture_scores() accepts the bogus FT score.
      2. settle_placed_bets() marks the Over bet as LOSS.
      3. update_pending_fixture_scores() then skips the fixture (it's "FT").
      4. The wrong result persists until the hourly job_fetch_results corrects it.

    This function short-circuits step 3 by force-fetching fixture data for any
    Over bet settled as LOSS in the last `hours` hours, so fix_incorrect_settlements()
    can correct the record at the next live_settle tick (every 2 min).

    Returns the number of fixtures whose score changed.
    """
    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    with get_session() as s:
        fixture_ids = s.execute(
            select(PlacedBet.fixture_id)
            .where(PlacedBet.settled == True)
            .where(PlacedBet.won == False)
            .where(PlacedBet.market.in_(["ou15", "ou25"]))
            .where(PlacedBet.outcome == "Over")
            .where(PlacedBet.settled_at >= cutoff)
            .distinct()
        ).scalars().all()

    if not fixture_ids:
        return 0

    logger.debug("recheck_early_loss_settlements: re-fetching %d fixture(s)", len(fixture_ids))

    updated = 0
    with get_session() as s:
        for fix_id in fixture_ids:
            try:
                raw = client.get_fixtures(fixture_id=fix_id, force_refresh=True)
                if not raw:
                    continue
                fix_data = raw[0]
                fix_info = fix_data.get("fixture", {})
                new_status = fix_info.get("status", {}).get("short", "")
                goals = fix_data.get("goals", {})
                new_home = goals.get("home")
                new_away = goals.get("away")

                fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
                if not fix:
                    continue

                changed = False
                if new_status and new_status != fix.status:
                    fix.status = new_status
                    changed = True
                if new_home is not None and new_home != fix.goals_home:
                    fix.goals_home = new_home
                    changed = True
                if new_away is not None and new_away != fix.goals_away:
                    fix.goals_away = new_away
                    changed = True
                if changed:
                    logger.info(
                        "recheck_early_loss_settlements: fixture %d updated to %s-%s (%s)",
                        fix_id, new_home, new_away, new_status,
                    )
                    updated += 1
            except Exception as e:
                logger.warning("recheck_early_loss_settlements: fixture %d error: %s", fix_id, e)

        if updated > 0:
            s.commit()

    return updated


def backfill_prediction_odds() -> int:
    """Backfill odds_decimal and bookmaker for PredictionRecord rows where they are NULL.

    Looks up FixtureOdds for the same fixture+market and populates the fields.
    Safe to run multiple times — only writes when values are missing.
    """
    from src.storage.models import PredictionRecord, FixtureOdds

    filled = 0
    with get_session() as s:
        rows = s.execute(
            select(PredictionRecord).where(
                PredictionRecord.odds_decimal.is_(None),
            )
        ).scalars().all()

        for pred in rows:
            fx_odds = s.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id == pred.fixture_id)
            ).scalars().first()

            if not fx_odds:
                continue

            market = pred.market
            outcome = str(pred.predicted_outcome or "").lower()
            odds_val = None

            if market == "h2h":
                if outcome in ("1", "home", "h"):
                    odds_val = fx_odds.odd_home
                elif outcome in ("x", "draw", "d"):
                    odds_val = fx_odds.odd_draw
                elif outcome in ("2", "away", "a"):
                    odds_val = fx_odds.odd_away
            elif market == "ou25":
                if outcome in ("over", "o25"):
                    odds_val = fx_odds.odd_over
                else:
                    odds_val = fx_odds.odd_under
            elif market == "ou15":
                if outcome in ("over", "o15"):
                    odds_val = fx_odds.odd_over15
                else:
                    odds_val = fx_odds.odd_under15
            elif market == "btts":
                if outcome in ("yes",):
                    odds_val = fx_odds.odd_btts_yes
                else:
                    odds_val = fx_odds.odd_btts_no

            if odds_val and odds_val >= 1.0:
                import json as _json
                pred.odds_decimal = odds_val
                pred.bookmaker = fx_odds.bookmaker
                snap = {"odds": odds_val, "bookmaker": fx_odds.bookmaker}
                pred.odds_snapshot = _json.dumps(snap)
                filled += 1

        if filled > 0:
            s.commit()

    logger.info(f"[SETTLE] Backfilled odds for {filled} prediction rows")
    return filled


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

    backfill_missing_scores(days=14)
    backfill_prediction_odds()
    corrections = fix_incorrect_settlements()
    if corrections > 0:
        logger.info(f"[SETTLE] Corrected {corrections} previously mis-settled bets/predictions")

    bets_settled, bets_pnl, bet_details = settle_placed_bets(days=days)
    preds_settled = settle_predictions(days=days)
    value_bets_settled = settle_value_bets(days=days)

    return {
        'bets_settled': bets_settled,
        'corrections': corrections,
        'predictions_settled': preds_settled,
        'value_bets_settled': value_bets_settled,
        'bets_pnl': bets_pnl,
        'bet_details': bet_details,
    }


# h2h predicted_outcome is stored in two notations across the pipeline: the
# ensemble/backfill path writes "1"/"X"/"2" (API-Football convention, matches
# get_market_result()'s return value), while the Elo hybrid path (Phase 16b+)
# writes "H"/"D"/"A". Comparing raw strings silently mis-scores every H/D/A
# prediction as a loss. Normalize both sides to "1"/"X"/"2" before comparing.
_H2H_NOTATION = {"H": "1", "D": "X", "A": "2", "1": "1", "X": "X", "2": "2"}


def _outcomes_match(market: str, predicted_outcome: str, actual: str) -> bool:
    if market == "h2h":
        p = _H2H_NOTATION.get(str(predicted_outcome).upper(), predicted_outcome)
        a = _H2H_NOTATION.get(str(actual).upper(), actual)
        return p == a
    return str(predicted_outcome).lower() == str(actual).lower()


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
            .where(Fixture.status.in_(["FT", "AET", "PEN"]))
        )

        if days is not None:
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = query.where(Fixture.date >= cutoff)

        unsettled = s.execute(query).all()

        for pred, fixture in unsettled:
            actual = get_market_result(fixture, pred.market)
            if actual:
                pred.actual_outcome = actual
                pred.won = _outcomes_match(pred.market, pred.predicted_outcome, actual)
                pred.settled = True
                pred.settled_at = datetime.utcnow()
                settled += 1

        if settled > 0:
            s.commit()

    logger.info(f"Settled {settled} predictions (days={days})")
    return settled


# Fixture statuses that mean "match did not produce a real playing result" —
# unplayed markets should be excluded from Track A, not scored as losses.
VOID_STATUSES = ("PST", "CANC", "ABD", "WO", "SUSP")

# Consecutive empty-response resyncs before a stale NS fixture is marked DEAD.
# Phase 22 investigation found the API-Football provider re-issues fixture IDs
# for playoff/knockout-bracket and provisional lower-tier schedules once the
# real pairing is finalized — the old ID returns an empty response forever.
# This is a recurring provider behavior (not one-off historical residue), so
# untraceable IDs need an ongoing rule, not just a one-time cleanup. 3 misses
# (~3 daily resync cycles) filters out a single transient API hiccup while
# still bounding the leak within days rather than letting it accumulate.
DEAD_THRESHOLD = 3
_STALE_FAILURE_FILE = Path("data/raw/.stale_fetch_failures.json")


def _load_stale_failures() -> dict[str, int]:
    if _STALE_FAILURE_FILE.exists():
        try:
            return json.loads(_STALE_FAILURE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_stale_failures(counts: dict[str, int]) -> None:
    _STALE_FAILURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STALE_FAILURE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(counts))
    tmp.replace(_STALE_FAILURE_FILE)


def mark_fixtures_dead(fixture_ids: list[int]) -> int:
    """Mark fixtures as permanently untraceable (API returns empty response).

    Sets Fixture.status='DEAD' — excludes them from resync_stale_fixtures()'s
    query (status='NS' only) so they can no longer occupy a slot at the head
    of the oldest-first resync queue. Voids their unsettled predictions with
    actual_outcome='untraced' (distinct from PST/CANC/AWD — there is no
    real-world event here, just data the provider no longer serves). Kept
    to 10 chars — PredictionRecord.actual_outcome is String(10); SQLite
    doesn't enforce the length but Postgres/MySQL would truncate or error.

    Returns count of predictions voided.
    """
    from src.storage.models import Fixture, PredictionRecord

    if not fixture_ids:
        return 0

    voided = 0
    with get_session() as s:
        for fid in fixture_ids:
            fix = s.get(Fixture, fid)
            if fix:
                fix.status = "DEAD"

        preds = s.execute(
            select(PredictionRecord)
            .where(PredictionRecord.fixture_id.in_(fixture_ids))
            .where(PredictionRecord.settled == False)
        ).scalars().all()
        for pred in preds:
            pred.actual_outcome = "untraced"
            pred.won = None
            pred.settled = True
            pred.settled_at = datetime.utcnow()
            voided += 1

        s.commit()

    logger.info("Marked %d fixtures DEAD, voided %d predictions", len(fixture_ids), voided)
    return voided


def void_unplayable_predictions(fixture_ids: list[int] | None = None) -> int:
    """Void unsettled predictions for PST/CANC/ABD/WO/SUSP fixtures.

    These fixtures never produced a result, so scoring them win/loss would
    corrupt Track A. Marks settled=True, won=None, actual_outcome=<status> —
    get_track_a_stats() filters on won.isnot(None), so voided rows are
    excluded from the accuracy denominator while still leaving the pipeline
    (no more unsettled/limbo residue).

    Args:
        fixture_ids: restrict to these fixtures (used by resync_stale_fixtures
            to void only the fixtures it just discovered); None = all matching
            fixtures in the DB.

    Returns count of predictions voided.
    """
    from src.storage.models import PredictionRecord, Fixture

    voided = 0
    with get_session() as s:
        query = (
            select(PredictionRecord, Fixture.status)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.settled == False)
            .where(Fixture.status.in_(VOID_STATUSES))
        )
        if fixture_ids:
            query = query.where(PredictionRecord.fixture_id.in_(fixture_ids))

        for pred, status in s.execute(query).all():
            pred.actual_outcome = status
            pred.won = None
            pred.settled = True
            pred.settled_at = datetime.utcnow()
            voided += 1

        if voided > 0:
            s.commit()

    logger.info(f"Voided {voided} predictions for PST/CANC/ABD/WO/SUSP fixtures")
    return voided


def settle_awarded_predictions(fixture_ids: list[int] | None = None) -> dict:
    """Settle h2h predictions for AWD (awarded/walk-over) fixtures against the
    API's declared winner; void goal-based markets (no goals were played).

    AWD fixtures have a real winner (one team forfeits) but no genuine
    scoreline, so:
      - h2h: scored as a normal hit/miss against the awarded winner.
      - ou25/ou15/btts: voided (won=None) — there is no real goal outcome.
      - If the API itself has no winner flag on either team (rare data gap),
        h2h is voided too rather than guessed.

    Does NOT touch Fixture.status (stays 'AWD', never promoted toward FT) so
    the generic settle_predictions() goal-based path can never fire on these
    predictions using the placeholder/forfeit scoreline.

    Args:
        fixture_ids: restrict to these fixtures; None = all unsettled AWD
            fixtures in the DB.

    Returns {"h2h_settled": n, "h2h_voided_no_winner": n, "goal_markets_voided": n,
             "api_calls": n}.
    """
    from src.storage.models import PredictionRecord, Fixture

    with get_session() as s:
        query = (
            select(PredictionRecord.fixture_id)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.settled == False)
            .where(Fixture.status == "AWD")
        )
        if fixture_ids:
            query = query.where(PredictionRecord.fixture_id.in_(fixture_ids))
        target_ids = sorted(set(s.execute(query).scalars().all()))

    if not target_ids:
        return {"h2h_settled": 0, "h2h_voided_no_winner": 0, "goal_markets_voided": 0, "api_calls": 0}

    client = APIFootballClient()
    raw_fixtures = client.get_fixtures_batch(target_ids)
    api_calls = -(-len(target_ids) // 20)

    winners: dict[int, str | None] = {}  # fixture_id -> "1" | "2" | None (undetermined)
    for raw in raw_fixtures:
        fid = raw.get("fixture", {}).get("id")
        if not fid:
            continue
        home_w = raw.get("teams", {}).get("home", {}).get("winner")
        away_w = raw.get("teams", {}).get("away", {}).get("winner")
        if home_w is True:
            winners[fid] = "1"
        elif away_w is True:
            winners[fid] = "2"
        else:
            winners[fid] = None  # API has no winner flag — can't determine

    h2h_settled = 0
    h2h_voided = 0
    goal_voided = 0
    with get_session() as s:
        preds = s.execute(
            select(PredictionRecord).where(PredictionRecord.fixture_id.in_(target_ids))
            .where(PredictionRecord.settled == False)
        ).scalars().all()

        for pred in preds:
            if pred.market == "h2h":
                winner = winners.get(pred.fixture_id)
                if winner is None:
                    pred.actual_outcome = "AWD"
                    pred.won = None
                    h2h_voided += 1
                else:
                    pred.actual_outcome = winner
                    pred.won = _outcomes_match("h2h", pred.predicted_outcome, winner)
                    h2h_settled += 1
            else:
                pred.actual_outcome = "AWD"
                pred.won = None
                goal_voided += 1
            pred.settled = True
            pred.settled_at = datetime.utcnow()

        s.commit()

    logger.info(
        "AWD settlement: %d h2h settled, %d h2h voided (no winner flag), "
        "%d goal-market predictions voided, %d API calls",
        h2h_settled, h2h_voided, goal_voided, api_calls,
    )
    return {
        "h2h_settled": h2h_settled,
        "h2h_voided_no_winner": h2h_voided,
        "goal_markets_voided": goal_voided,
        "api_calls": api_calls,
    }


def resync_stale_fixtures(limit: int = 100) -> dict:
    """Re-fetch fixtures stuck in status='NS' with a date that has already
    passed — the one impossible state _save_upcoming() can never self-correct
    (it only updates fixtures the API still reports as NS within the rolling
    7-day window; once a fixture goes live or is rescheduled outside that
    window, its stored date is frozen forever without this targeted re-fetch).

    Scoped ONLY to status='NS' AND date < now — never touches fixtures with a
    legitimate future NS date, live matches, or fixtures already in a
    terminal/void state. Capped at `limit` fixtures per call (batch-fetched at
    20/call) so this can run on every pipeline cycle without meaningful
    quota impact once the one-time backlog is cleared.

    After updating date/status/goals, if a fixture landed on:
      - FT/AET/PEN: leaves settlement to the caller (settle_predictions()).
      - PST/CANC/ABD/WO/SUSP: immediately voids its predictions.
      - AWD: immediately settles via settle_awarded_predictions() (1 extra
        API call batch, only for fixtures that actually resolved to AWD).

    Fixtures that come back with an empty API response (fixture ID no longer
    exists — see mark_fixtures_dead docstring) are tracked across calls; after
    DEAD_THRESHOLD consecutive empty responses they're auto-marked DEAD and
    excluded from future resync queries, so a permanently-untraceable ID can't
    sit at the head of the oldest-first queue forever.

    Returns counts: fixtures_checked, api_calls, updated, unchanged,
    resolved_ft, resolved_void, resolved_awd, still_ns_unresolved, marked_dead.
    """
    from src.storage.models import Fixture

    now = datetime.utcnow()
    with get_session() as s:
        stale_ids = s.execute(
            select(Fixture.id)
            .where(Fixture.status == "NS")
            .where(Fixture.date < now)
            .order_by(Fixture.date.asc())
            .limit(limit)
        ).scalars().all()

    if not stale_ids:
        return {
            "fixtures_checked": 0, "api_calls": 0, "updated": 0, "unchanged": 0,
            "resolved_ft": 0, "resolved_void": 0, "resolved_awd": 0,
            "still_ns_unresolved": 0, "marked_dead": 0,
        }

    client = APIFootballClient()
    raw_fixtures = client.get_fixtures_batch(list(stale_ids))
    api_calls = -(-len(stale_ids) // 20)

    found_ids = {raw.get("fixture", {}).get("id") for raw in raw_fixtures if raw.get("fixture", {}).get("id")}
    missing_ids = [fid for fid in stale_ids if fid not in found_ids]

    # Track consecutive empty-response misses per fixture; auto-mark DEAD at
    # DEAD_THRESHOLD so a permanently-untraceable ID stops occupying a slot
    # in the oldest-first resync queue after a few confirmed-empty cycles.
    #
    # Guard: if the ENTIRE batch came back empty, that's a call-level failure
    # signal (quota exhaustion, network blip, provider incident) — not N
    # individual 404s. Counting it as N misses would risk marking legitimate
    # fixtures dead during a transient outage. Only count misses when at
    # least some IDs in the batch resolved, i.e. the API round-trip worked.
    failures = _load_stale_failures()
    newly_dead: list[int] = []
    if raw_fixtures:
        for fid in missing_ids:
            key = str(fid)
            failures[key] = failures.get(key, 0) + 1
            if failures[key] >= DEAD_THRESHOLD:
                newly_dead.append(fid)
                del failures[key]
        for fid in found_ids:
            failures.pop(str(fid), None)  # reset on any successful response
        _save_stale_failures(failures)

    dead_voided = 0
    if newly_dead:
        dead_voided = mark_fixtures_dead(newly_dead)

    updated = 0
    unchanged = 0
    resolved_ft: list[int] = []
    resolved_void: list[int] = []
    resolved_awd: list[int] = []
    still_ns_unresolved = 0

    with get_session() as s:
        for raw in raw_fixtures:
            f = raw.get("fixture", {})
            fid = f.get("id")
            if not fid:
                continue
            fix = s.get(Fixture, fid)
            if not fix:
                continue

            new_status = f.get("status", {}).get("short")
            date_str = f.get("date")
            new_date = (
                datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if date_str else None
            )
            goals = raw.get("goals", {})
            new_gh, new_ga = goals.get("home"), goals.get("away")
            ht = raw.get("score", {}).get("halftime", {})
            new_hth, new_hta = ht.get("home"), ht.get("away")

            changed = False
            if new_status and new_status != fix.status:
                fix.status = new_status
                changed = True
            if new_date and new_date != fix.date:
                fix.date = new_date
                changed = True
            if new_gh is not None and new_gh != fix.goals_home:
                fix.goals_home = new_gh
                changed = True
            if new_ga is not None and new_ga != fix.goals_away:
                fix.goals_away = new_ga
                changed = True
            if new_hth is not None and new_hth != fix.ht_goals_home:
                fix.ht_goals_home = new_hth
                changed = True
            if new_hta is not None and new_hta != fix.ht_goals_away:
                fix.ht_goals_away = new_hta
                changed = True
            if new_gh is not None and new_ga is not None:
                outcome = "H" if new_gh > new_ga else ("A" if new_gh < new_ga else "D")
                if outcome != fix.outcome:
                    fix.outcome = outcome
                    changed = True

            if changed:
                updated += 1
            else:
                unchanged += 1

            if new_status in ("FT", "AET", "PEN"):
                resolved_ft.append(fid)
            elif new_status == "AWD":
                resolved_awd.append(fid)
            elif new_status in VOID_STATUSES:
                resolved_void.append(fid)
            elif new_status == "NS":
                still_ns_unresolved += 1  # API still reports NS with a past date — either genuinely
                # unresolvable (fixture ID no longer exists on the API) or the match
                # kicked off in just the last few hours and hasn't been caught by the
                # live-status poller yet. Left unchanged; will be retried next run.

        s.commit()

    if resolved_void:
        void_unplayable_predictions(fixture_ids=resolved_void)
    awd_result = {"api_calls": 0}
    if resolved_awd:
        awd_result = settle_awarded_predictions(fixture_ids=resolved_awd)

    logger.info(
        "resync_stale_fixtures: checked %d, updated %d, unchanged %d, "
        "resolved_ft=%d resolved_void=%d resolved_awd=%d still_ns_unresolved=%d "
        "marked_dead=%d, %d+%d API calls",
        len(stale_ids), updated, unchanged, len(resolved_ft), len(resolved_void),
        len(resolved_awd), still_ns_unresolved, len(newly_dead), api_calls, awd_result["api_calls"],
    )
    return {
        "fixtures_checked": len(stale_ids),
        "api_calls": api_calls + awd_result["api_calls"],
        "updated": updated,
        "unchanged": unchanged,
        "resolved_ft": len(resolved_ft),
        "resolved_void": len(resolved_void),
        "resolved_awd": len(resolved_awd),
        "still_ns_unresolved": still_ns_unresolved,
        "marked_dead": len(newly_dead),
    }


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