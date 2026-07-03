#!/usr/bin/env python3
"""
scripts/daily_run.py - DATA BASELINE ONLY (REFACTORED)

This script is now ONLY responsible for:
1. Backfilling finished fixtures from API
2. Fetching upcoming fixtures (7 days ahead)
3. Validating DB consistency
4. Forcing settlement baseline

MUST NOT:
- call prediction engine
- call portfolio engine  
- call execution engine

The continuous prediction/betting is handled by run_continuous_cycle()
"""

import argparse
import logging
import sys
from pathlib import Path
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

import csv

from config.leagues import ALL_LEAGUE_IDS, LEAGUES
from config.settings import settings
from src.ingestion.client import APIFootballClient, calls_used_today, get_api_status
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, PredictionRecord, PlacedBet, Team, League

QUOTA_LOG = Path(__file__).resolve().parent.parent / "logs" / "quota_log.csv"


def _log_quota(event: str, used: int) -> None:
    """Append one row to logs/quota_log.csv (created on first use)."""
    QUOTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not QUOTA_LOG.exists()
    with QUOTA_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp_utc", "event", "calls_used", "calls_remaining", "daily_limit", "backfill_cap"])
        w.writerow([
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            used,
            settings.api_calls_per_day - used,
            settings.api_calls_per_day,
            settings.backfill_daily_cap,
        ])

from src.alerts.event_bus import event_bus as EventBus, Events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_with_context(run_id: str, mode: str = "daily_baseline", dry_run: bool = False):
    """Execute pipeline with run context for tracking."""
    from backend.experiment_tracker import ExperimentTracker
    
    start_time = time.time()
    errors = []
    
    try:
        EventBus.emit(
            Events.RUN_STARTED,
            {
                "run_id": run_id,
                "mode": mode,
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        pipeline = DailyBaselinePipeline(dry_run=dry_run, context={"run_id": run_id, "mode": mode})
        pipeline.run()
        
        duration = time.time() - start_time
        
        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": mode,
                "errors": errors,
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        return True, len(errors), duration
        
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Pipeline failed: {e}")
        
        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": mode,
                "errors": [str(e)],
                "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
            },
        )
        
        return False, len(errors) + 1, duration


class DailyBaselinePipeline:
    """
    DATA BASELINE ONLY - NO predictions, NO betting.
    
    Responsibilities:
    1. Backfill ALL finished fixtures (FT) from API
    2. Fetch ALL upcoming fixtures (7 days ahead)
    3. Validate DB consistency
    4. Force settlement baseline
    5. Emit events for data readiness
    """
    
    def __init__(self, dry_run=False, league_ids=None, catchup_days=7, context=None):
        self.client = APIFootballClient()
        self.dry_run = dry_run
        self.league_ids = league_ids or ALL_LEAGUE_IDS
        self.catchup_days = catchup_days
        self.context = context or {}
        
        self.errors = []
        self.fixtures_backfilled = 0
        self.upcoming_count = 0
        self.settled_count = 0
        
        # Track last successful run for incremental backfill
        self._last_run_timestamp = None
    
    @staticmethod
    def _league_season(league_id: int, now: datetime) -> int:
        from config.settings import settings
        return settings.get_season(league_id)

    def run(self):
        """Execute data baseline pipeline."""
        logger.info("=" * 60)
        logger.info("DAILY BASELINE PIPELINE - DATA ONLY")
        logger.info("=" * 60)

        now = datetime.now(ZoneInfo("UTC"))
        run_id = self.context.get("run_id")

        # Log quota at pipeline start
        used_start = calls_used_today()
        _log_quota("run_start", used_start)
        logger.info(
            "[QUOTA] Start: %d used / %d cap / %d limit",
            used_start, settings.backfill_daily_cap, settings.api_calls_per_day,
        )

        # Emit run started
        EventBus.emit(
            Events.RUN_STARTED,
            {
                "run_id": run_id,
                "mode": "daily_baseline",
                "timestamp": now.isoformat(),
            },
        )
        
        # STEP 1: Backfill completed fixtures
        logger.info("[BASELINE] Step 1: Backfilling completed fixtures...")
        self._fetch_completed(now)

        # STEP 2: Fetch upcoming fixtures
        logger.info("[BASELINE] Step 2: Fetching upcoming fixtures...")
        fixtures = self._fetch_upcoming(now)
        self.upcoming_count = len(fixtures)

        # STEP 3: Resync stale NS fixtures (Phase 21) — fixtures stuck at
        # status='NS' with a date that has already passed, which _save_upcoming()
        # (step 2) can never self-correct since it only touches fixtures the API
        # still reports as NS. Bounded to 100/run (~5 API calls) so steady-state
        # cost is negligible; the one-time backlog was cleared separately.
        logger.info("[BASELINE] Step 3: Resyncing stale NS fixtures...")
        try:
            from src.settlement import resync_stale_fixtures
            resync_result = resync_stale_fixtures(limit=100)
            logger.info(f"[BASELINE] Resync: {resync_result}")
        except Exception:
            logger.exception("[BASELINE] resync_stale_fixtures failed")

        # STEP 4: Validate DB consistency
        logger.info("[BASELINE] Step 4: Validating DB consistency...")
        validation = self._validate_consistency(fixtures)

        # STEP 5: Force settlement baseline
        logger.info("[BASELINE] Step 5: Forcing settlement baseline...")
        self._force_settlement_baseline()

        # STEP 6: Refresh standings for active leagues
        logger.info("[BASELINE] Step 6: Refreshing league standings...")
        self._fetch_standings(now)

        # STEP 7: Emit baseline ready events
        logger.info("[BASELINE] Step 7: Emitting baseline ready events...")
        
        EventBus.emit("BASELINE_READY", {
            "run_id": run_id,
            "fixtures_backfilled": self.fixtures_backfilled,
            "upcoming_count": self.upcoming_count,
            "settled_count": self.settled_count,
            "validation": validation,
            "timestamp": now.isoformat(),
        })
        
        EventBus.emit("DATA_BACKFILL_COMPLETED", {
            "run_id": run_id,
            "backfilled": self.fixtures_backfilled,
            "upcoming": self.upcoming_count,
            "settled": self.settled_count,
            "errors": self.errors,
            "timestamp": now.isoformat(),
        })
        
        # Emit run finished
        EventBus.emit(
            Events.RUN_FINISHED,
            {
                "run_id": run_id,
                "mode": "daily_baseline",
                "backfilled": self.fixtures_backfilled,
                "upcoming": self.upcoming_count,
                "settled": self.settled_count,
                "validation_errors": validation.get("errors", []),
                "timestamp": now.isoformat(),
            },
        )
        
        logger.info(f"[BASELINE] Complete: {self.fixtures_backfilled} backfilled, {self.upcoming_count} upcoming, {self.settled_count} settled")

        # Log quota at pipeline end
        used_end = calls_used_today()
        _log_quota("run_end", used_end)
        logger.info("[QUOTA] End: %d used this run, %d total today", used_end - used_start, used_end)

        if self.errors:
            logger.warning(f"[BASELINE] Errors: {self.errors}")
    
    def _fetch_completed(self, now):
        """Fetch and backfill completed fixtures."""
        days_back = max(1, self.catchup_days)

        for league_id in self.league_ids:
            # Enforce backfill daily cap — forward-collection and real-time calls have
            # first claim on the full 75k quota; backfill stops when the soft cap is hit.
            if calls_used_today() >= settings.backfill_daily_cap:
                _log_quota("backfill_paused", calls_used_today())
                logger.info(
                    "[QUOTA] Backfill paused: %d calls used >= cap %d",
                    calls_used_today(), settings.backfill_daily_cap,
                )
                break

            season = self._league_season(league_id, now)
            try:
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=season,
                    from_date=(now - timedelta(days=days_back)).strftime("%Y-%m-%d"),
                    to_date=now.strftime("%Y-%m-%d"),
                    status="FT",
                    force_refresh=True,
                )
                if raw:
                    count = self._save_completed(raw, season)
                    self.fixtures_backfilled += count
                    logger.info(f"[BASELINE] League {league_id}: {count} FT fixtures")
            except Exception as e:
                error_msg = f"League {league_id}: {e}"
                self.errors.append(error_msg)
                logger.warning(f"[BASELINE] {error_msg}")
    
    def _save_completed(self, raw, season):
        """Save completed fixtures to DB."""
        count = 0
        for match in raw:
            fixture = match["fixture"]
            fid = fixture["id"]
            
            with get_session() as s:
                existing = s.execute(
                    select(Fixture).where(Fixture.id == fid)
                ).scalar_one_or_none()
                
                if not existing:
                    continue
                
                goals = fixture.get("goals", {})
                old_status, old_gh, old_ga = existing.status, existing.goals_home, existing.goals_away
                existing.goals_home = goals.get("home")
                existing.goals_away = goals.get("away")
                # Only flip to FT if not already in a terminal state — this
                # query is filtered to status="FT" at the API level, but that
                # filter itself can momentarily include a still-live fixture
                # (see Phase 27: Dundee/Thor internal-freeze cases), so this
                # write must not clobber a genuinely later live status. The
                # fixture still gets picked up correctly once truly final,
                # since this loop re-runs every cycle.
                if existing.status not in ("FT", "AET", "PEN"):
                    existing.status = "FT"
                logger.info(
                    "[BASELINE] _save_completed: fixture %d %s %s-%s -> %s %s-%s",
                    fid, old_status, old_gh, old_ga, existing.status, existing.goals_home, existing.goals_away,
                )

                if goals.get("home") is not None and goals.get("away") is not None:
                    if goals["home"] > goals["away"]:
                        existing.outcome = "H"
                    elif goals["home"] < goals["away"]:
                        existing.outcome = "A"
                    else:
                        existing.outcome = "D"
                
                s.commit()
                count += 1
        
        return count
    
    def _fetch_upcoming(self, now):
        """Fetch upcoming fixtures and upsert them into the DB."""
        all_raw = []

        for league_id in self.league_ids:
            season = self._league_season(league_id, now)
            try:
                raw = self.client.get_fixtures(
                    league_id=league_id,
                    season=season,
                    from_date=now.strftime("%Y-%m-%d"),
                    to_date=(now + timedelta(days=7)).strftime("%Y-%m-%d"),
                    status="NS",
                )
                if raw:
                    all_raw.extend(raw)
            except Exception as e:
                error_msg = f"Fetch upcoming league {league_id}: {e}"
                self.errors.append(error_msg)
                logger.warning(f"[BASELINE] {error_msg}")

        saved = self._save_upcoming(all_raw)
        logger.info(f"[BASELINE] Fetched {len(all_raw)} upcoming fixtures, upserted {saved}")
        return all_raw

    def _save_upcoming(self, raw_fixtures: list) -> int:
        """Upsert upcoming NS fixtures into the Fixture table."""
        from config.leagues import LEAGUES as _LEAGUES
        saved = 0
        with get_session() as s:
            _added_leagues: set = set()
            _added_teams: set = set()
            for match in raw_fixtures:
                fix_info = match.get("fixture", {})
                fid = fix_info.get("id")
                if not fid:
                    continue

                teams = match.get("teams", {})
                home_id = teams.get("home", {}).get("id")
                away_id = teams.get("away", {}).get("id")
                league_info = match.get("league", {})
                league_id = league_info.get("id")
                season = league_info.get("season")

                if not all([home_id, away_id, league_id, season]):
                    continue

                # Ensure League row exists
                if league_id not in _added_leagues and not s.get(League, league_id):
                    meta = _LEAGUES.get(league_id, {})
                    country_raw = league_info.get("country", "")
                    country_str = country_raw if isinstance(country_raw, str) else (country_raw or {}).get("name", "")
                    s.add(League(
                        id=league_id,
                        name=meta.get("name", league_info.get("name", str(league_id))),
                        country=meta.get("country", country_str),
                        tier=1,
                    ))
                    _added_leagues.add(league_id)

                # Ensure Team rows exist
                for tid, tdata in [(home_id, teams["home"]), (away_id, teams["away"])]:
                    if tid not in _added_teams and not s.get(Team, tid):
                        s.add(Team(
                            id=tid,
                            name=tdata.get("name", str(tid)),
                            country="",
                        ))
                        _added_teams.add(tid)

                date_str = fix_info.get("date")
                fix_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None) if date_str else None

                existing = s.get(Fixture, fid)
                if existing:
                    # Only update if still not started
                    if existing.status in ("NS", None):
                        existing.date = fix_date
                        existing.status = "NS"
                else:
                    s.add(Fixture(
                        id=fid,
                        league_id=league_id,
                        season=season,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        date=fix_date,
                        status="NS",
                    ))
                    saved += 1

            s.commit()
        return saved
    
    def _validate_consistency(self, fixtures):
        """Validate DB consistency."""
        validation = {"errors": [], "warnings": []}
        
        with get_session() as s:
            # Check for fixtures missing odds
            no_odds = s.execute(
                select(func.count(Fixture.id))
                .join(FixtureOdds, Fixture.id == FixtureOdds.fixture_id, isouter=True)
                .where(FixtureOdds.id == None)
                .where(Fixture.status == "NS")
            ).scalar() or 0
            
            if no_odds > 0:
                validation["warnings"].append(f"{no_odds} upcoming fixtures missing odds")
            
            # Check for stuck fixtures (not FT but should be)
            stuck = s.execute(
                select(func.count(Fixture.id))
                .where(Fixture.date < datetime.now(ZoneInfo("UTC")))
                .where(Fixture.status == "NS")
            ).scalar() or 0
            
            if stuck > 10:
                validation["warnings"].append(f"{stuck} old fixtures still in NS status")
            
            # Check for unsettled bets
            unsettled = s.execute(
                select(func.count(PlacedBet.id))
                .where(PlacedBet.settled == False)
            ).scalar() or 0
            
            if unsettled > 0:
                validation["warnings"].append(f"{unsettled} bets still unsettled")
        
        return validation
    
    def _fetch_standings(self, now: datetime) -> int:
        """Refresh standings for leagues with upcoming fixtures whose data is stale (>12h).

        Scoped to leagues with fixtures in the next 7 days to keep API cost low
        (~30-60 calls per run in steady state). Uses force_refresh=True so the
        live API value replaces any cached response.
        """
        from sqlalchemy import text as sa_text
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from src.storage.models import Standing

        with get_session() as s:
            rows = s.execute(sa_text("""
                SELECT DISTINCT f.league_id FROM fixtures f
                WHERE f.date >= datetime('now')
                  AND f.date <= datetime('now', '+7 days')
                  AND f.status = 'NS'
                  AND NOT EXISTS (
                      SELECT 1 FROM standings s2
                      WHERE s2.league_id = f.league_id
                        AND s2.fetched_at >= datetime('now', '-12 hours')
                  )
                ORDER BY f.league_id
            """)).fetchall()

        league_ids = [r[0] for r in rows]
        if not league_ids:
            logger.info("[BASELINE] Standings up to date for all active leagues")
            return 0

        logger.info("[BASELINE] Refreshing standings for %d leagues", len(league_ids))
        updated = err = 0

        for lid in league_ids:
            season = self._league_season(lid, now)
            try:
                raw_entries = self.client.get("standings", {"league": lid, "season": season}, force_refresh=True)
            except Exception as e:
                logger.warning("[BASELINE] Standings fetch failed league=%d: %s", lid, e)
                err += 1
                continue

            team_rows = []
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                if "rank" in item and "team" in item:
                    team_rows.append(item)
                elif "league" in item:
                    for group in item["league"].get("standings", []):
                        if isinstance(group, list):
                            team_rows.extend(group)

            if not team_rows:
                continue

            rows_to_insert = []
            for entry in team_rows:
                team = entry.get("team", {})
                team_id = team.get("id")
                if not team_id:
                    continue
                all_ = entry.get("all", {})
                goals = all_.get("goals", {})
                rows_to_insert.append({
                    "league_id":     lid,
                    "season":        season,
                    "team_id":       team_id,
                    "team_name":     team.get("name", ""),
                    "rank":          entry.get("rank"),
                    "points":        entry.get("points"),
                    "played":        all_.get("played"),
                    "won":           all_.get("win"),
                    "drawn":         all_.get("draw"),
                    "lost":          all_.get("lose"),
                    "goals_for":     goals.get("for"),
                    "goals_against": goals.get("against"),
                    "goal_diff":     entry.get("goalsDiff"),
                    "fetched_at":    datetime.utcnow(),
                })

            if not rows_to_insert:
                continue

            try:
                with get_session() as s:
                    stmt = sqlite_insert(Standing).values(rows_to_insert)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["league_id", "season", "team_id"],
                        set_={
                            "team_name":     stmt.excluded.team_name,
                            "rank":          stmt.excluded.rank,
                            "points":        stmt.excluded.points,
                            "played":        stmt.excluded.played,
                            "won":           stmt.excluded.won,
                            "drawn":         stmt.excluded.drawn,
                            "lost":          stmt.excluded.lost,
                            "goals_for":     stmt.excluded.goals_for,
                            "goals_against": stmt.excluded.goals_against,
                            "goal_diff":     stmt.excluded.goal_diff,
                            "fetched_at":    stmt.excluded.fetched_at,
                        },
                    )
                    s.execute(stmt)
                updated += 1
            except Exception as e:
                logger.warning("[BASELINE] Standings DB write failed league=%d: %s", lid, e)
                err += 1

        logger.info("[BASELINE] Standings refreshed: %d ok, %d err", updated, err)
        return updated

    def _force_settlement_baseline(self):
        """Settle bets and predictions for any finished fixtures."""
        from src.settlement import settle_placed_bets, settle_predictions, verify_ft_fixtures

        try:
            verify_ft_fixtures()
        except Exception:
            logger.exception("[BASELINE] verify_ft_fixtures failed")

        try:
            bets_settled, pnl, _ = settle_placed_bets()
        except Exception:
            logger.exception("[BASELINE] settle_placed_bets failed")
            bets_settled, pnl = 0, 0.0

        try:
            preds_settled = settle_predictions()
        except Exception:
            logger.exception("[BASELINE] settle_predictions failed")
            preds_settled = 0

        self.settled_count = bets_settled + preds_settled

        if self.settled_count > 0:
            logger.info(
                "[BASELINE] Settled %d bets (P/L: %+.2f) and %d predictions",
                bets_settled, pnl, preds_settled,
            )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Daily Baseline Pipeline - DATA ONLY")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--catchup-days", type=int, default=7, help="Days to backfill")
    args = parser.parse_args()
    
    from backend.experiment_tracker import get_tracker
    from backend.runtime_mode import get_mode_name
    from backend.run_context import create_run_context
    
    mode = get_mode_name()
    tracker = get_tracker()
    
    run_id = None
    context = None
    
    if mode in ["training", "dev"]:
        run_id = tracker.start_run(runtime_mode=mode)
        context = create_run_context(run_id, mode)
        print(f"Started experiment run: {run_id}")
    
    try:
        success, errors, duration = run_with_context(
            run_id or "daily_baseline",
            mode="daily_baseline",
            dry_run=args.dry_run
        )
        print(f"Pipeline {'succeeded' if success else 'failed'}: {errors} errors in {duration:.1f}s")
    except Exception as e:
        print(f"Pipeline failed: {e}")
        if run_id:
            try:
                tracker.finalize_run(run_id, status="failed")
            except:
                pass
        raise


if __name__ == "__main__":
    main()
