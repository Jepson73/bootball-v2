#!/usr/bin/env python3
"""
scripts/odds_trajectory_scheduler.py

Active trajectory capture for ALL odds-carrying fixtures (Phase 25). Supersedes
the narrow 4-5-league forward-collection in config/forward_leagues.py — cost
difference between all-fixtures and Pinnacle-only scopes was trivial (Phase 24),
and Pinnacle-present can't be classified until near kickoff anyway (Phase 11b),
so there's no benefit to pre-filtering by league or by bookmaker.

Schedule shape per fixture:
  - ~1 touch/day from first-seen until 6h before kickoff (daily phase)
  - ~hourly in the final 6h before kickoff (hourly phase)

Near-kickoff samples are load-bearing, not optional: Pinnacle often posts only
close to kickoff (Phase 11b — ~20% of apparent Pinnacle-absences were just early-
fetch timing artifacts). A trajectory missing its near-kickoff touches misses the
sharp reference entirely, so near-kickoff touches are NEVER subject to the
self-imposed collection_daily_cap below — only the daily (far) phase is. Bounded
only by a hard global floor (never drives the account's overall remaining quota
below 500) regardless of how much the daily-phase budget has spent.

Budget: settings.collection_daily_cap (15,000 calls/day) governs daily-phase (far)
spend only, tracked in logs/trajectory_scheduler_state.json, reset at UTC midnight.

Per-fixture "due for a touch" tracking uses BOTH a real capture (odds_snapshots)
AND a bare attempt (logs/trajectory_last_attempt.json), because a fixture with
zero bookmaker coverage returns an empty payload every time — no snapshot row
ever gets written, so nothing ever ages its staleness clock, and it would
otherwise be retried (3 wasted calls) on every single 30-min cycle forever. This
was found live in Phase 25 bring-up: the daily-phase candidate pool never shrank
between runs and burned the entire collection_daily_cap on repeat-empty fixtures
by early afternoon, silently starving near-kickoff touches for ~90 minutes before
the near/far cap split above was added.

Run via cron every 30 min, 24/7 — deliberately NOT daytime-only like odds_poll.py's
8-23 CET window, since kickoffs in Tasmania/Asia/Oceania leagues land at UTC hours
a CET-daytime cron would miss entirely.

Usage:
    python scripts/odds_trajectory_scheduler.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from config.settings import settings
from src.ingestion.client import APIFootballClient, calls_used_today
from src.ingestion.odds_snapshot_capture import write_snapshots_from_response
from src.storage.db import get_session, init_db
from src.storage.models import Fixture, OddsSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("odds_trajectory_scheduler")

KICKOFF_WINDOW_DAYS = 7
HOURLY_PHASE_HOURS = 6.0
DAILY_PHASE_STALE_HOURS = 20.0   # just under 24h so cron jitter can't skip a whole day
HOURLY_PHASE_STALE_HOURS = 1.0
MARKETS = {"h2h": 1, "over_under": 5, "btts": 8}
GLOBAL_QUOTA_FLOOR = 500   # never drive the whole-account remaining quota below this

# Per-run cap on daily-phase (far) touches only. On a cold start — or after any gap —
# every candidate is simultaneously "due," and at ~1-2s/fixture (API throttle + latency)
# a run with no cap can take tens of minutes, overlapping the next 30-min cron tick.
# Near-kickoff touches are never capped (small population, always the priority).
MAX_FAR_TOUCHES_PER_RUN = 400

STATE_FILE = Path("logs/trajectory_scheduler_state.json")
ATTEMPT_FILE = Path("logs/trajectory_last_attempt.json")
QUOTA_LOG = Path("logs/quota_log.csv")
LOCK_FILE = Path("logs/trajectory_scheduler.lock")


def _utcnow() -> datetime:
    return datetime.utcnow()


@contextmanager
def _run_lock():
    """Non-blocking exclusive lock so an overlapping cron tick skips instead of racing.

    A cold-start backlog can make one run take longer than the 30-min cron cadence
    (mitigated by MAX_FAR_TOUCHES_PER_RUN, but a slow API day could still overrun it).
    Without this, two concurrent runs both read-modify-write the same state file and
    the second write silently clobbers the first — observed live during Phase 25
    bring-up: a manual bootstrap run's 4,499-call spend was overwritten by a cron tick
    that started while it was still running, leaving the visible daily total wrong.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _load_json(path: Path, default_factory) -> dict:
    today = _utcnow().strftime("%Y-%m-%d")
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return default_factory(today)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _load_state() -> dict:
    return _load_json(STATE_FILE, lambda today: {"date": today, "far_calls_used": 0, "near_calls_used": 0})


def _load_attempts() -> dict:
    """fixture_id (str) -> ISO timestamp of last touch attempt, regardless of outcome.
    Resets daily (paired with the daily-phase 20h stale window — nothing needs to survive
    a UTC-midnight boundary at this granularity, and it keeps the file from growing forever)."""
    return _load_json(ATTEMPT_FILE, lambda today: {"date": today, "attempts": {}})


def _log_quota(event: str) -> None:
    """Append to the shared quota_log.csv (same columns backfill_cron.py/daily_run.py use)
    so the scheduler's spend is visible in the one place quota is already tracked, not silent
    in its own file."""
    QUOTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not QUOTA_LOG.exists()
    used = calls_used_today()
    with open(QUOTA_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["timestamp_utc", "event", "calls_used", "calls_remaining", "daily_limit", "backfill_cap"])
        w.writerow([
            _utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            used,
            settings.api_calls_per_day - used,
            settings.api_calls_per_day,
            settings.backfill_daily_cap,
        ])


def _get_candidates(s, attempts: dict) -> tuple[list[int], list[int]]:
    """Return (near_kickoff_ids, daily_phase_ids), each sorted soonest-kickoff-first,
    restricted to fixtures actually due a touch given their phase's stale window.

    "Due" checks the newer of (last real capture, last bare attempt) — a fixture with
    zero bookmaker coverage never gets a capture, but its attempt timestamp still ages
    it out of immediate re-candidacy just like a successful one.
    """
    now = _utcnow()
    cutoff = now + timedelta(days=KICKOFF_WINDOW_DAYS)

    last_capture_subq = (
        select(OddsSnapshot.fixture_id, func.max(OddsSnapshot.captured_at).label("last_captured"))
        .group_by(OddsSnapshot.fixture_id)
        .subquery()
    )

    rows = s.execute(
        select(Fixture.id, Fixture.date, last_capture_subq.c.last_captured)
        .outerjoin(last_capture_subq, last_capture_subq.c.fixture_id == Fixture.id)
        .where(Fixture.status == "NS")
        .where(Fixture.date >= now)
        .where(Fixture.date <= cutoff)
    ).all()

    near: list[tuple[int, float]] = []
    far: list[tuple[int, float]] = []
    for fid, kickoff, last_captured in rows:
        if kickoff is None:
            continue
        hours_to_ko = (kickoff - now).total_seconds() / 3600.0
        if hours_to_ko <= HOURLY_PHASE_HOURS:
            stale_hours, bucket = HOURLY_PHASE_STALE_HOURS, near
        else:
            stale_hours, bucket = DAILY_PHASE_STALE_HOURS, far

        last_attempted = attempts.get(str(fid))
        last_attempted_dt = datetime.fromisoformat(last_attempted) if last_attempted else None

        candidates = [t for t in (last_captured, last_attempted_dt) if t is not None]
        last_touch = max(candidates) if candidates else None

        due = last_touch is None or (now - last_touch).total_seconds() / 3600.0 >= stale_hours
        if due:
            bucket.append((fid, hours_to_ko))

    near.sort(key=lambda t: t[1])
    far.sort(key=lambda t: t[1])
    return [f for f, _ in near], [f for f, _ in far]


def _touch_fixture(s, client: APIFootballClient, fixture_id: int, dry_run: bool) -> dict:
    """Fetch all 3 markets for one fixture and write snapshot rows.

    Returns {"calls": int, "written": int, "skipped_dedupe": int, "skipped_unparsed": int,
    "empty_payload": int, "errors": int} — every call is accounted for one of these ways,
    so "0 rows written" is never a dead end.
    """
    result = {"calls": 0, "written": 0, "skipped_dedupe": 0, "skipped_unparsed": 0,
              "empty_payload": 0, "errors": 0}
    now = _utcnow()

    for market_name, bet_id in MARKETS.items():
        try:
            raw = client.get_odds(fixture_id=fixture_id, bet_type=bet_id)
            result["calls"] += 1
        except Exception as e:
            logger.warning("API error fixture=%d market=%s: %s", fixture_id, market_name, e)
            result["calls"] += 1  # the attempt still cost a call (or a retry cycle) even on failure
            result["errors"] += 1
            continue

        if not raw:
            result["empty_payload"] += 1
            continue

        if dry_run:
            continue

        write_stats = write_snapshots_from_response(s, raw, fixture_id, now)
        result["written"] += write_stats["written"]
        result["skipped_dedupe"] += write_stats["skipped_dedupe"]
        result["skipped_unparsed"] += write_stats["skipped_unparsed"]

    return result


def run(dry_run: bool = False) -> dict:
    with _run_lock() as acquired:
        if not acquired:
            logger.warning("Another instance is already running — skipping this cycle")
            state = _load_state()
            return {
                "near_candidates": 0, "far_candidates": 0, "near_touched": 0, "far_touched": 0,
                "written": 0, "skipped_dedupe": 0, "skipped_unparsed": 0, "empty_payload": 0,
                "skipped_far_budget": 0, "errors": 0, "calls_this_run": 0,
                "far_calls_today": state["far_calls_used"],
                "far_budget_remaining": settings.collection_daily_cap - state["far_calls_used"],
                "locked_out": True,
            }
        return _run_locked(dry_run)


def _run_locked(dry_run: bool) -> dict:
    init_db()
    client = APIFootballClient()

    state = _load_state()
    attempts_doc = _load_attempts()
    attempts = attempts_doc["attempts"]
    far_remaining = settings.collection_daily_cap - state["far_calls_used"]

    calls_before = calls_used_today()
    stats = {
        "near_candidates": 0, "far_candidates": 0,
        "near_touched": 0, "far_touched": 0,
        "written": 0, "skipped_dedupe": 0, "skipped_unparsed": 0, "empty_payload": 0,
        "skipped_far_budget": 0, "errors": 0,
        "near_calls": 0, "far_calls": 0,
    }

    with get_session() as s:
        near_ids, far_ids = _get_candidates(s, attempts)
        stats["near_candidates"] = len(near_ids)
        stats["far_candidates"] = len(far_ids)

        # Near-kickoff: never subject to collection_daily_cap — only the hard global floor.
        for fid in near_ids:
            if settings.api_calls_per_day - calls_used_today() < GLOBAL_QUOTA_FLOOR:
                logger.warning("Global quota floor (%d) hit — stopping near-kickoff early", GLOBAL_QUOTA_FLOOR)
                break

            touch = _touch_fixture(s, client, fid, dry_run)
            stats["near_touched"] += 1
            stats["near_calls"] += touch["calls"]
            for k in ("written", "skipped_dedupe", "skipped_unparsed", "empty_payload", "errors"):
                stats[k] += touch[k]
            attempts[str(fid)] = _utcnow().isoformat()
            if not dry_run:
                s.commit()

        # Daily-phase: capped by collection_daily_cap (far_remaining) AND per-run cap.
        for fid in far_ids:
            if stats["far_touched"] >= MAX_FAR_TOUCHES_PER_RUN:
                stats["skipped_far_budget"] += 1
                continue
            if far_remaining - stats["far_calls"] < 3:
                stats["skipped_far_budget"] += 1
                continue
            if settings.api_calls_per_day - calls_used_today() < GLOBAL_QUOTA_FLOOR:
                logger.warning("Global quota floor (%d) hit — stopping daily-phase early", GLOBAL_QUOTA_FLOOR)
                stats["skipped_far_budget"] += 1
                continue

            touch = _touch_fixture(s, client, fid, dry_run)
            stats["far_touched"] += 1
            stats["far_calls"] += touch["calls"]
            for k in ("written", "skipped_dedupe", "skipped_unparsed", "empty_payload", "errors"):
                stats[k] += touch[k]
            attempts[str(fid)] = _utcnow().isoformat()
            if not dry_run:
                s.commit()

    run_spend = calls_used_today() - calls_before
    state["far_calls_used"] += stats["far_calls"]
    state["near_calls_used"] += stats["near_calls"]
    if not dry_run:
        _save_state(state)
        attempts_doc["attempts"] = attempts
        _save_json(ATTEMPT_FILE, attempts_doc)
        _log_quota("trajectory_scheduler_run")

    stats["calls_this_run"] = run_spend
    stats["far_calls_today"] = state["far_calls_used"]
    stats["far_budget_remaining"] = settings.collection_daily_cap - state["far_calls_used"]
    return stats


def _save_state(state: dict) -> None:
    _save_json(STATE_FILE, state)


def main():
    parser = argparse.ArgumentParser(description="Active odds trajectory capture — all odds-carrying fixtures")
    parser.add_argument("--dry-run", action="store_true", help="Discover + fetch but do not write or spend budget")
    args = parser.parse_args()

    stats = run(dry_run=args.dry_run)

    print("\n── Odds Trajectory Scheduler ───────────────────────────")
    print(f"  Date:                     {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Near-kickoff candidates:  {stats['near_candidates']} (touched {stats['near_touched']}, uncapped)")
    print(f"  Daily-phase candidates:   {stats['far_candidates']} (touched {stats['far_touched']})")
    print(f"  Written:                  {stats['written']}  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"  Skipped (dedupe):         {stats['skipped_dedupe']}")
    print(f"  Skipped (unparsed):       {stats['skipped_unparsed']}")
    print(f"  Empty payload:            {stats['empty_payload']}")
    print(f"  Skipped (far budget):     {stats['skipped_far_budget']}")
    print(f"  Errors:                   {stats['errors']}")
    print(f"  Calls this run:           {stats['calls_this_run']}")
    print(f"  Daily-phase spend today:  {stats['far_calls_today']} / {settings.collection_daily_cap} (near-kickoff not counted against this)")
    print("───────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
