import logging
import os
import time
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_SCHEDULER_DB = _BASE_DIR / "data" / "scheduler.db"
_SCHEDULER_DB.parent.mkdir(parents=True, exist_ok=True)

jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{_SCHEDULER_DB}')
}

# ── Circuit Breaker ────────────────────────────────────────────────────────────
# Tracks consecutive failures per job. After CIRCUIT_OPEN_AFTER failures the job
# is skipped for an exponentially increasing cooldown before resetting.

_CIRCUIT_OPEN_AFTER = 3          # failures before opening
_CIRCUIT_MAX_COOLDOWN = 7200     # cap cooldown at 2 hours (seconds)

_circuit: dict[str, dict] = {}  # {job_id: {failures, open_until}}


def _circuit_ok(job_id: str) -> bool:
    """Return True if the job is allowed to run (circuit closed or cooled down)."""
    state = _circuit.get(job_id)
    if not state:
        return True
    if state["failures"] < _CIRCUIT_OPEN_AFTER:
        return True
    now = time.monotonic()
    if now >= state["open_until"]:
        logger.info("CIRCUIT: %s cooldown expired — resetting", job_id)
        _circuit.pop(job_id, None)
        return True
    remaining = int(state["open_until"] - now)
    logger.warning("CIRCUIT OPEN: skipping %s (%ds cooldown remaining)", job_id, remaining)
    return False


def _circuit_success(job_id: str) -> None:
    """Record a successful job run — resets the circuit."""
    _circuit.pop(job_id, None)


def _circuit_failure(job_id: str) -> None:
    """Record a job failure — may open the circuit."""
    state = _circuit.setdefault(job_id, {"failures": 0, "open_until": 0.0})
    state["failures"] += 1
    failures = state["failures"]
    if failures >= _CIRCUIT_OPEN_AFTER:
        # Exponential backoff: 60s * 2^(failures - OPEN_AFTER), capped
        cooldown = min(60 * (2 ** (failures - _CIRCUIT_OPEN_AFTER)), _CIRCUIT_MAX_COOLDOWN)
        state["open_until"] = time.monotonic() + cooldown
        logger.error(
            "CIRCUIT OPENED: %s failed %d times in a row — pausing for %ds",
            job_id, failures, cooldown,
        )



def job_fetch_fixtures():
    """Pull upcoming fixtures, upsert changes, then seed odds for newly ingested fixtures."""
    if not _circuit_ok("fetch_fixtures"):
        return
    logger.info("JOB: fetch_fixtures starting")
    from src.storage.db import get_session
    from sqlalchemy import text
    success = False
    error_msg = None
    try:
        import uuid
        from scripts.daily_run import DailyBaselinePipeline
        pipeline = DailyBaselinePipeline(context={"run_id": str(uuid.uuid4())[:8]})
        pipeline.run()
        _circuit_success("fetch_fixtures")
        success = True
        logger.info("JOB: fetch_fixtures completed")

        # Immediately seed odds for fixtures that have none, so fetch_odds only refreshes
        try:
            from src.ingestion.client import APIFootballClient, calls_remaining_today
            from scripts.odds_poll import find_fixtures_needing_odds, poll_and_update_odds

            remaining = calls_remaining_today()
            if remaining >= 50:
                client = APIFootballClient()
                with get_session() as s:
                    # Only bootstrap bucket: fixtures with no odds and no predictions
                    all_ids = find_fixtures_needing_odds(s)
                    # Identify fixtures with no existing odds at all
                    from src.storage.models import FixtureOdds
                    from sqlalchemy import select as _select
                    has_odds_ids = {
                        r[0] for r in s.execute(
                            _select(FixtureOdds.fixture_id).where(
                                FixtureOdds.fixture_id.in_(all_ids)
                            ).distinct()
                        ).all()
                    }
                    no_odds_ids = [fid for fid in all_ids if fid not in has_odds_ids]
                    max_seed = min(300, remaining // 3)
                    seed_ids = no_odds_ids[:max_seed]
                    if seed_ids:
                        logger.info(f"JOB: fetch_fixtures — seeding odds for {len(seed_ids)} new fixtures")
                        seeded = poll_and_update_odds(s, client, seed_ids)
                        logger.info(f"JOB: fetch_fixtures — seeded {seeded} odds rows")
            else:
                logger.info("JOB: fetch_fixtures — skipping odds seed (low quota)")
        except Exception as seed_err:
            logger.warning(f"JOB: fetch_fixtures — odds seed failed (non-fatal): {seed_err}")

    except Exception as e:
        error_msg = str(e)
        _circuit_failure("fetch_fixtures")
        logger.exception("JOB: fetch_fixtures failed")
    try:
        with get_session() as s:
            s.execute(
                text("INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message) VALUES (:job, :success, :updated, :error)"),
                {"job": "fetch_fixtures", "success": success, "updated": 0, "error": error_msg}
            )
            s.commit()
    except Exception:
        logger.exception("JOB: fetch_fixtures — failed to write ingestion_log")


def job_live_settle():
    """Fetch live scores for all live fixtures globally, then settle any pending bets.

    Runs every 2 minutes, costs ~7 API calls (one per live-status code) regardless
    of bet activity — Track A predictions need live status/score/date corrections
    even with zero pending bets. Previously this whole fetch was gated behind
    "any unsettled placed_bets", which made it a permanent no-op since betting
    closed (Phase 8): pending_count has been 0 ever since, so live fixtures went
    unpolled and forward-dated-but-live corrections (see
    src.settlement.update_pending_fixture_scores) never ran. Settlement itself
    stays gated below since there's nothing to settle with zero pending bets.
    Bets requiring early settlement need 3 consecutive confirmations before
    committing, to absorb VAR delays.
    """
    from src.storage.db import get_session
    from sqlalchemy import text

    logger.debug("JOB: live_settle — fetching live scores")

    try:
        from src.settlement import update_pending_fixture_scores
        update_pending_fixture_scores()
    except Exception:
        logger.exception("JOB: live_settle live-score fetch failed (non-fatal)")

    with get_session() as s:
        pending_count = s.execute(
            text("SELECT COUNT(*) FROM placed_bets WHERE settled = 0")
        ).scalar()

    if not pending_count:
        return  # nothing to settle

    try:
        from src.settlement import settle_placed_bets
        settled, pnl, _ = settle_placed_bets()
        if settled:
            logger.info("JOB: live_settle settled %d bets, P/L: %+.2f", settled, pnl)
    except Exception:
        logger.exception("JOB: live_settle settlement failed (non-fatal)")


def job_fetch_results():
    """Update finished match scores and outcomes."""
    if not _circuit_ok("fetch_results"):
        return
    logger.info("JOB: fetch_results starting")
    from src.storage.db import get_session
    from sqlalchemy import text

    success = False
    error_msg = None

    try:
        import uuid
        from scripts.daily_run import DailyBaselinePipeline
        pipeline = DailyBaselinePipeline(context={"run_id": str(uuid.uuid4())[:8]})
        pipeline.run()
        success = True
        _circuit_success("fetch_results")
        logger.info("JOB: fetch_results completed")
    except Exception as e:
        error_msg = str(e)
        _circuit_failure("fetch_results")
        logger.exception("JOB: fetch_results failed")

    # After fetching results, settle any pending bets whose fixtures are now FT
    try:
        from src.settlement import fetch_and_update_fixtures, settle_all, backfill_missing_scores, verify_ft_fixtures
        fetch_and_update_fixtures(days=7)
        backfill_missing_scores(days=14)
        verify_ft_fixtures()
        result = settle_all()
        if result['bets_settled'] > 0 or result['predictions_settled'] > 0:
            logger.info(
                f"JOB: fetch_results auto-settled "
                f"{result['bets_settled']} bets, {result['predictions_settled']} predictions, "
                f"P/L: {result['bets_pnl']:+.2f}"
            )
    except Exception:
        logger.exception("JOB: fetch_results — auto-settlement failed (non-fatal)")

    try:
        with get_session() as s:
            s.execute(
                text("""INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message)
                       VALUES (:job, :success, :updated, :error)"""),
                {"job": "fetch_results", "success": success, "updated": 0, "error": error_msg}
            )
            s.commit()
    except Exception:
        logger.exception("JOB: fetch_results — failed to write ingestion_log")


def job_fetch_odds():
    """Poll fresh odds for upcoming fixtures and recalculate EV on predictions."""
    if not _circuit_ok("fetch_odds"):
        return
    logger.info("JOB: fetch_odds starting")
    from src.storage.db import get_session
    from sqlalchemy import text

    success = False
    error_msg = None
    odds_updated = 0

    try:
        from src.ingestion.client import APIFootballClient, calls_remaining_today
        from scripts.odds_poll import find_fixtures_needing_odds, poll_and_update_odds, recalculate_prediction_ev

        remaining = calls_remaining_today()
        if remaining < 50:
            logger.warning(f"JOB: fetch_odds — low API calls ({remaining}), skipping")
            return

        client = APIFootballClient()

        with get_session() as s:
            fixture_ids = find_fixtures_needing_odds(s)

        if not fixture_ids:
            logger.info("JOB: fetch_odds — no fixtures need odds polling")
            _circuit_success("fetch_odds")
            return

        max_fixtures = remaining // 3  # no arbitrary cap — quota is the only governor
        fixture_ids = fixture_ids[:max_fixtures]
        logger.info(f"JOB: fetch_odds — polling {len(fixture_ids)} fixtures (quota remaining: {remaining})")

        with get_session() as s:
            odds_updated = poll_and_update_odds(s, client, fixture_ids)
            recalculate_prediction_ev(s, fixture_ids)

        success = True
        _circuit_success("fetch_odds")
        logger.info(f"JOB: fetch_odds completed — {odds_updated} odds rows updated")
    except Exception as e:
        error_msg = str(e)
        _circuit_failure("fetch_odds")
        logger.exception("JOB: fetch_odds failed")

    try:
        with get_session() as s:
            s.execute(
                text("""INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message)
                       VALUES (:job, :success, :updated, :error)"""),
                {"job": "fetch_odds", "success": success, "updated": odds_updated, "error": error_msg}
            )
            s.commit()
    except Exception:
        logger.exception("JOB: fetch_odds — failed to write ingestion_log")


def job_cleanup_matches():
    """Cleanup stale live matches and archive old finished matches."""
    if not _circuit_ok("cleanup_matches"):
        return
    logger.info("JOB: cleanup_matches starting")
    from src.storage.db import get_session
    from sqlalchemy import text
    success = False
    error_msg = None
    try:
        from backend.match_state_normalizer import cleanup_finished_matches
        result = cleanup_finished_matches()
        _circuit_success("cleanup_matches")
        success = True
        logger.info(
            "JOB: cleanup_matches completed — %d transitioned, %d archived",
            result.get("transitioned_to_ft", 0), result.get("archived", 0),
        )
    except Exception as e:
        error_msg = str(e)
        _circuit_failure("cleanup_matches")
        logger.exception("JOB: cleanup_matches failed")
    try:
        with get_session() as s:
            s.execute(
                text("INSERT INTO ingestion_log (job_name, success, fixtures_updated, error_message) VALUES (:job, :success, :updated, :error)"),
                {"job": "cleanup_matches", "success": success, "updated": 0, "error": error_msg}
            )
            s.commit()
    except Exception:
        logger.exception("JOB: cleanup_matches — failed to write ingestion_log")


def job_daily_sanity_check():
    """Run the daily sanity check to detect season mismatches, stale model sigs, and coverage gaps."""
    if not _circuit_ok("daily_sanity_check"):
        return
    logger.info("JOB: daily_sanity_check starting")
    try:
        from scripts.daily_sanity_check import main as run_sanity_check
        run_sanity_check()
        _circuit_success("daily_sanity_check")
        logger.info("JOB: daily_sanity_check completed")
    except Exception:
        _circuit_failure("daily_sanity_check")
        logger.exception("JOB: daily_sanity_check failed")


def job_v2_collection_heartbeat():
    """Phase 30: once-daily V2 Discord digest — snapshots captured, trajectories
    accumulated, scheduler spend vs cap, quota headroom. Silence here is meant
    to read as "broken", so this has to actually run once/day rather than rely
    on some other job's side effect."""
    if not _circuit_ok("v2_collection_heartbeat"):
        return
    logger.info("JOB: v2_collection_heartbeat starting")
    try:
        from src.notifications.v2_discord_notifier import notify_collection_heartbeat
        notify_collection_heartbeat()
        _circuit_success("v2_collection_heartbeat")
        logger.info("JOB: v2_collection_heartbeat completed")
    except Exception:
        _circuit_failure("v2_collection_heartbeat")
        logger.exception("JOB: v2_collection_heartbeat failed")


def get_scheduler() -> BackgroundScheduler:
    """Create and configure the scheduler.
    
    NOTE: This scheduler is now AUXILIARY ONLY.
    
    Core execution (run_continuous_cycle) is handled by ExecutionRuntime.
    This scheduler only handles non-critical data operations:
    - fetch_fixtures: Fixture ingestion
    - fetch_results: Result updates
    - fetch_odds: Odds updates
    - cleanup_matches: Match cleanup
    
    Jobs REMOVED from APScheduler (now handled by ExecutionRuntime):
    - run_continuous_cycle (core pipeline)
    - run_betting_bot (execution)
    - run_predictions (predictions)
    - retrain_models (training)
    - auto_heal_runs (governance)
    """
    try:
        from backend.runtime_mode import is_live_eval_mode, get_mode_name
        mode = get_mode_name()
    except Exception:
        mode = "dev"
    
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300
    )
    
    logger.info(f"Configuring scheduler for RUNTIME_MODE: {mode}")
    logger.info("NOTE: Scheduler is now AUXILIARY ONLY (data operations)")

    try:
        if is_live_eval_mode():
            logger.info("⚠️  LIVE_EVAL MODE: Scheduler will run ONLY ingestion jobs")
    except Exception:
        pass

    # daily_sanity_check / v2_collection_heartbeat use 'cron' (fixed wall-clock time),
    # not 'interval'. An interval trigger's next_run_time is computed from the moment
    # add_job() runs, so replace_existing=True on every service restart keeps pushing
    # it another {hours} out — on a host that reboots daily in under 24h, a 24h interval
    # job can never survive to its own fire time (found 2026-07-09, see
    # docs/deployment_state.md's "Host reboots daily" section). A cron trigger's next
    # fire time derives from the wall clock instead, so it's reboot-immune by
    # construction regardless of when the process happens to (re)start. Times are
    # deliberately far from the ~04:00-04:20 UTC reboot window, and misfire_grace_time
    # is generous so a restart that straddles the slot still fires on recovery instead
    # of silently skipping the day.
    auxiliary_jobs = [
        ("fetch_fixtures", job_fetch_fixtures, 'interval', {'hours': 6}, None),
        ("fetch_results", job_fetch_results, 'interval', {'hours': 1}, None),
        ("fetch_odds", job_fetch_odds, 'interval', {'hours': 1}, None),
        ("cleanup_matches", job_cleanup_matches, 'interval', {'minutes': 5}, None),
        ("live_settle", job_live_settle, 'interval', {'minutes': 2}, None),
        ("daily_sanity_check", job_daily_sanity_check, 'cron', {'hour': 8, 'minute': 0}, 7200),
        ("v2_collection_heartbeat", job_v2_collection_heartbeat, 'cron', {'hour': 12, 'minute': 0}, 7200),
    ]

    for job_id, job_func, trigger_type, trigger_args, misfire_grace_time in auxiliary_jobs:
        job_kwargs = dict(trigger_args)
        if misfire_grace_time is not None:
            job_kwargs['misfire_grace_time'] = misfire_grace_time
        scheduler.add_job(
            job_func,
            trigger_type,
            id=job_id,
            name=f"{job_id} ({mode})",
            replace_existing=True,
            **job_kwargs
        )
        logger.info(f"   → Added auxiliary job: {job_id}")

    logger.info("=" * 50)
    logger.info("SCHEDULER CONFIGURATION COMPLETE")
    logger.info("Core execution moved to ExecutionRuntime")
    logger.info("=" * 50)

    return scheduler


def start_scheduler():
    """Start the scheduler."""
    scheduler = get_scheduler()
    scheduler.start()
    logger.info(f"Scheduler started with jobs: {[j.id for j in scheduler.get_jobs()]}")
    return scheduler


def stop_scheduler(scheduler):
    """Stop the scheduler gracefully."""
    scheduler.shutdown(wait=True)
    logger.info("Scheduler stopped")