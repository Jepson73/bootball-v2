"""
v2/db_v2.py — Read-only DB helpers for web_ui_v2.

Imports ONLY from shared infrastructure (src/storage, config).
Does NOT import from scripts/web_ui.py.
"""
from __future__ import annotations

import math
import csv
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, func, text, case as sa_case, or_
from sqlalchemy.orm import aliased

from src.storage.db import get_session
from src.storage.models import Fixture, OddsSnapshot, League

logger = logging.getLogger(__name__)

QUOTA_LOG = Path("logs/quota_log.csv")

_SOFT_BOOK_PRIORITY = {
    "Bet365": 1, "Unibet": 2, "William Hill": 3,
    "Betfair": 4, "Marathonbet": 5, "1xBet": 6,
}


def _fetch_soft_odds(fixture_ids: list[int]) -> dict[int, dict]:
    """
    Per-fixture consolidated soft-book odds from fixture_odds.
    One bookmaker per fixture (Bet365 preferred). Returns empty dict entry
    when no soft odds exist for a fixture.

    Keys: book, home, draw, away, over, under, over15, under15, btts_yes, btts_no
    """
    if not fixture_ids:
        return {}

    fid_str = ",".join(str(int(x)) for x in fixture_ids)

    with get_session() as s:
        rows = s.execute(text(f"""
            SELECT fixture_id, bookmaker,
                   MAX(odd_home)     AS odd_home,
                   MAX(odd_draw)     AS odd_draw,
                   MAX(odd_away)     AS odd_away,
                   MAX(odd_over)     AS odd_over,
                   MAX(odd_under)    AS odd_under,
                   MAX(odd_over15)   AS odd_over15,
                   MAX(odd_under15)  AS odd_under15,
                   MAX(odd_btts_yes) AS odd_btts_yes,
                   MAX(odd_btts_no)  AS odd_btts_no
            FROM fixture_odds
            WHERE fixture_id IN ({fid_str})
              AND bookmaker NOT IN ('Pinnacle', 'Pinnacle Sports')
            GROUP BY fixture_id, bookmaker
            ORDER BY fixture_id, bookmaker
        """)).fetchall()

    result: dict[int, dict] = {}
    for r in rows:
        fid = r.fixture_id
        if fid in result:
            existing_priority = _SOFT_BOOK_PRIORITY.get(result[fid]["book"], 99)
            new_priority = _SOFT_BOOK_PRIORITY.get(r.bookmaker, 99)
            if new_priority >= existing_priority:
                continue
        result[fid] = {
            "book": r.bookmaker,
            "home": r.odd_home,
            "draw": r.odd_draw,
            "away": r.odd_away,
            "over": r.odd_over,
            "under": r.odd_under,
            "over15": r.odd_over15,
            "under15": r.odd_under15,
            "btts_yes": r.odd_btts_yes,
            "btts_no": r.odd_btts_no,
        }
    return result


def _attach_soft_odds(fix_dict: dict[int, dict], markets_key: str = "markets") -> None:
    """
    Merge soft odds into market dicts in-place.
    fix_dict: {fixture_id: {"markets": [...]}} for predictions view
              {fixture_id: {"markets": {name: {...}}}} for explorer.
    markets_key is always "markets".
    """
    soft = _fetch_soft_odds(list(fix_dict.keys()))
    for fid, fix_data in fix_dict.items():
        s = soft.get(fid, {})
        mkt_container = fix_data[markets_key]
        items = mkt_container.values() if isinstance(mkt_container, dict) else mkt_container
        for mkt in items:
            mkt["soft_book"] = s.get("book")
            mkt["soft_home"] = s.get("home")
            mkt["soft_draw"] = s.get("draw")
            mkt["soft_away"] = s.get("away")
            mkt["soft_over"] = s.get("over")
            mkt["soft_under"] = s.get("under")
            mkt["soft_over15"] = s.get("over15")
            mkt["soft_under15"] = s.get("under15")
            mkt["soft_btts_yes"] = s.get("btts_yes")
            mkt["soft_btts_no"] = s.get("btts_no")


# ── Forward-collection ─────────────────────────────────────────────────────────

def get_snapshot_summary() -> dict:
    """Count odds_snapshots rows by league and bookmaker."""
    with get_session() as s:
        total = s.execute(select(func.count()).select_from(OddsSnapshot)).scalar() or 0
        by_league: list[dict] = []
        if total > 0:
            rows = s.execute(
                select(Fixture.league_id, League.name, func.count(OddsSnapshot.id))
                .join(Fixture, OddsSnapshot.fixture_id == Fixture.id)
                .join(League, Fixture.league_id == League.id)
                .group_by(Fixture.league_id, League.name)
                .order_by(func.count(OddsSnapshot.id).desc())
            ).all()
            by_league = [{"league_id": r[0], "league_name": r[1], "count": r[2]} for r in rows]

        earliest = latest = None
        if total > 0:
            earliest = s.execute(select(func.min(OddsSnapshot.captured_at))).scalar()
            latest = s.execute(select(func.max(OddsSnapshot.captured_at))).scalar()

    days_accumulated = 0
    if earliest and latest:
        days_accumulated = (latest - earliest).days + 1

    return {
        "total": total,
        "by_league": by_league,
        "earliest": earliest,
        "latest": latest,
        "days_accumulated": days_accumulated,
    }


def get_forward_fixtures() -> list[dict]:
    """Return upcoming NS fixtures in forward leagues with any snapshot counts."""
    from config.forward_leagues import FORWARD_LEAGUE_IDS
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_session() as s:
        rows = s.execute(
            select(
                Fixture.id,
                Fixture.date,
                Fixture.league_id,
                League.name.label("league_name"),
                func.count(OddsSnapshot.id).label("snapshot_count"),
            )
            .outerjoin(OddsSnapshot, OddsSnapshot.fixture_id == Fixture.id)
            .join(League, Fixture.league_id == League.id)
            .where(Fixture.league_id.in_(FORWARD_LEAGUE_IDS))
            .where(Fixture.status.in_(["NS", "1H", "HT", "2H"]))
            .where(Fixture.date >= now - timedelta(hours=2))
            .group_by(Fixture.id, Fixture.date, Fixture.league_id, League.name)
            .order_by(Fixture.date)
        ).all()
    return [
        {
            "id": r.id,
            "date": r.date,
            "league_id": r.league_id,
            "league_name": r.league_name,
            "snapshot_count": r.snapshot_count,
        }
        for r in rows
    ]


def get_snapshot_timeseries() -> list[dict]:
    """Return daily snapshot counts for the collection chart."""
    with get_session() as s:
        rows = s.execute(
            select(
                func.date(OddsSnapshot.captured_at).label("day"),
                func.count(OddsSnapshot.id),
            )
            .group_by(func.date(OddsSnapshot.captured_at))
            .order_by(func.date(OddsSnapshot.captured_at))
        ).all()
    return [{"day": str(r[0]), "count": r[1]} for r in rows]


# "Near-kickoff" matches odds_trajectory_scheduler.py's HOURLY_PHASE_HOURS (the
# scheduler switches to hourly polling inside this window). "Early" requires a
# snapshot from well outside that window (the once-daily "far phase"), so a
# qualifying pair actually spans an open-to-close trajectory rather than two
# snapshots taken minutes apart.
_NEAR_KICKOFF_HOURS = 2.0
_EARLY_MIN_HOURS = 12.0
_RECENT_RATE_WINDOW_DAYS = 5


def get_track_b_collection_status() -> dict:
    """Live read of odds_snapshots collection progress for the Track B panel.

    Replaces the old hardcoded "clock hasn't started / ~150 days" text. All
    numbers are computed from odds_snapshots + fixtures on every call — no
    stored plan, no hand-set thresholds beyond the phase-boundary constants
    above (which mirror the actual collection scheduler's own cadence).
    """
    with get_session() as s:
        total = s.execute(select(func.count()).select_from(OddsSnapshot)).scalar() or 0
        if total == 0:
            return {"started": False}

        earliest = s.execute(select(func.min(OddsSnapshot.captured_at))).scalar()
        latest = s.execute(select(func.max(OddsSnapshot.captured_at))).scalar()

        fixtures_with_2plus = s.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT fixture_id FROM odds_snapshots
                GROUP BY fixture_id HAVING COUNT(*) >= 2
            )
        """)).scalar() or 0

        pinnacle_earliest = s.execute(
            select(func.min(OddsSnapshot.captured_at)).where(OddsSnapshot.bookmaker_name == "Pinnacle")
        ).scalar()
        pinnacle_fixtures_2plus = s.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT fixture_id FROM odds_snapshots WHERE bookmaker_name = 'Pinnacle'
                GROUP BY fixture_id HAVING COUNT(*) >= 2
            )
        """)).scalar() or 0

        qualifying_sql = """
            SELECT {select_expr} FROM (
                SELECT f.id, f.date AS fixture_date
                FROM fixtures f
                JOIN odds_snapshots o ON o.fixture_id = f.id
                WHERE f.status IN ('FT', 'AET', 'PEN')
                GROUP BY f.id
                HAVING
                    SUM(CASE WHEN (julianday(f.date) - julianday(o.captured_at)) * 24 >= :early_h THEN 1 ELSE 0 END) > 0
                    AND SUM(CASE WHEN (julianday(f.date) - julianday(o.captured_at)) * 24 BETWEEN -2 AND :near_h THEN 1 ELSE 0 END) > 0
            ) q
        """
        params = {"early_h": _EARLY_MIN_HOURS, "near_h": _NEAR_KICKOFF_HOURS}
        qualifying_total = s.execute(
            text(qualifying_sql.format(select_expr="COUNT(*)")), params
        ).scalar() or 0

        daily_rows = s.execute(
            text(qualifying_sql.format(select_expr="substr(fixture_date, 1, 10) AS d, COUNT(*)") + " GROUP BY d ORDER BY d"),
            params,
        ).all()

    recent_days = daily_rows[-_RECENT_RATE_WINDOW_DAYS:] if daily_rows else []
    recent_rate = round(sum(r[1] for r in recent_days) / len(recent_days), 1) if recent_days else 0.0

    days_running = (latest.date() - earliest.date()).days + 1 if earliest and latest else 0

    return {
        "started": True,
        "collection_start": earliest.date().isoformat() if earliest else None,
        "days_running": days_running,
        "fixtures_with_2plus_snapshots": fixtures_with_2plus,
        "qualifying_pairs_total": qualifying_total,
        "recent_daily_qualifying_rate": recent_rate,
        "recent_rate_window_days": len(recent_days),
        "pinnacle_collection_start": pinnacle_earliest.date().isoformat() if pinnacle_earliest else None,
        "pinnacle_fixtures_with_2plus_snapshots": pinnacle_fixtures_2plus,
    }


# ── Quota log ─────────────────────────────────────────────────────────────────

def get_quota_log(n: int = 10) -> list[dict]:
    """Return last n rows from logs/quota_log.csv."""
    if not QUOTA_LOG.exists():
        return []
    try:
        with open(QUOTA_LOG, newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[-n:]
    except Exception as e:
        logger.warning("Could not read quota_log: %s", e)
        return []


def get_latest_quota() -> dict | None:
    rows = get_quota_log(2)
    if not rows:
        return None
    last = rows[-1]
    return {
        "timestamp": last.get("timestamp_utc", ""),
        "event": last.get("event", ""),
        "calls_used": int(last.get("calls_used", 0)),
        "calls_remaining": int(last.get("calls_remaining", 0)),
        "daily_limit": int(last.get("daily_limit", 75000)),
    }


# ── Serving calibration (Phase 33 Task 4) ───────────────────────────────────────

_MAINSTREAM_CONTEXTS = (None, "full")
_H2H_SIDE = {"1": "home", "X": "draw", "2": "away", "H": "home", "D": "draw", "A": "away"}


def _load_calibration_snapshot():
    from src.calibration.league_calibration_engine import LeagueCalibrationEngine
    return LeagueCalibrationEngine().load_active_calibrations()


def freeze_served_probs_for_fixture(fixture_id: int) -> int:
    """Persist served_prob (+ served_calibration_version) for every PredictionRecord of
    one fixture that doesn't have it yet -- write-once, never overwritten afterwards.

    Phase 33b acceptance found a settlement display discontinuity: served_prob was
    computed live on every request and never stored, so a settled row had no record
    of what was actually shown to a user pre-match -- get_explorer_data() fell back to
    raw our_prob for settled rows (see _mkt_cell()'s docstring in explorer_v2.py), so a
    fixture displayed at "61%" pre-match could show "94% X" once settled with no way to
    recover that 61% was ever served.

    Called from src/settlement.py::update_pending_fixture_scores() the moment a fixture
    is first observed live (NS -> 1H/2H/HT/...) -- so the frozen value is what was being
    served at kickoff, not at settlement (calibration can refit in between, especially
    now that a weekly fit_all floor exists -- Phase 33b). settle_predictions() also calls
    this as a fallback for any row that reaches settlement without ever being caught live
    (abandoned/awarded fixtures, resync gaps); that fallback freezes at settlement time
    instead of true kickoff and logs loudly, since it's a materially different guarantee.
    """
    from src.storage.models import PredictionRecord, Fixture

    with get_session() as s:
        fixture = s.get(Fixture, fixture_id)
        if fixture is None:
            return 0
        rows = s.execute(
            select(PredictionRecord)
            .where(PredictionRecord.fixture_id == fixture_id)
            .where(PredictionRecord.served_prob.is_(None))
        ).scalars().all()
        if not rows:
            return 0

        snap = _load_calibration_snapshot()
        updated = 0
        for pred in rows:
            if pred.our_prob is None:
                continue
            if pred.data_context in _MAINSTREAM_CONTEXTS:
                p_cal, version = snap.apply(pred.market, fixture.league_id, pred.our_prob)
                pred.served_prob = p_cal
                pred.served_calibration_version = version
            else:
                pred.served_prob = pred.our_prob
                pred.served_calibration_version = None
            updated += 1
        s.commit()
    return updated


def _serve_prob(snap, market: str, league_id: int | None, our_prob: float | None, data_context: str | None):
    """Return (served_prob, is_calibrated) for one prediction.

    Live-recomputes against TODAY's active calibration rather than trusting the
    stored calibrated_prob column -- that column reflects whatever calibration
    was active when the prediction was WRITTEN, which for anything older than
    the most recent fit_all() refit is stale (Phase 33 Task 2: the previous fit
    sat unrefreshed for 33 days). Only the mainstream pipeline's data_context
    ('full' or NULL) is calibrated -- Task 4's audit found applying it to the
    thin tiers (elo_both/elo_partial/flat_prior/national_elo) makes them WORSE,
    since the calibration was fit exclusively on full/NULL history.
    """
    if our_prob is None:
        return None, False
    if data_context not in _MAINSTREAM_CONTEXTS:
        return our_prob, False
    p_cal, _version = snap.apply(market, league_id, our_prob)
    return p_cal, True


# ── Track A ────────────────────────────────────────────────────────────────────

_BINARY_MARKETS = {"btts", "ou25", "ou15"}


def _log_corrupted_prob_trail(excluded: dict[str, int]) -> None:
    """Persist a correction-trail record for rows excluded by the invariant check
    below, so the exclusion is auditable without re-deriving it from scratch.

    Written once per distinct exclusion-count signature (cheap file existence
    check) rather than on every page load — get_track_a_stats() runs on every
    Track A request.
    """
    if not excluded:
        return
    logger.warning("TRACK_A_SCORING: excluded corrupted our_prob rows (invariant "
                    "our_prob>=0.5 for argmax-selected binary outcome violated): %s", excluded)
    trail_path = Path("data/lineage/track_a_scoring_corrections.json")
    sig = ",".join(f"{k}={v}" for k, v in sorted(excluded.items()))
    try:
        if trail_path.exists() and trail_path.read_text().find(sig) != -1:
            return  # already logged this exact signature
        trail_path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        entries = []
        if trail_path.exists():
            try:
                entries = _json.loads(trail_path.read_text())
            except Exception:
                entries = []
        entries.append({
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "signature": sig,
            "excluded_by_market": excluded,
            "reason": (
                "our_prob for the argmax-selected (predicted) outcome of a binary "
                "market can never legitimately be <0.5 -- best_outcome is chosen as "
                "max(model_probs.items()). Rows violating this were traced (Phase 32 "
                "Task 2) to a bad embedded per-outcome calibrator live 2026-05-09 to "
                "2026-05-12 for ou15 model_version_id=10, self-corrected by a "
                "subsequent model/calibrator swap (model_version 44, 2026-05-12 "
                "04:12:39). calibrated_prob coverage on the affected rows is too "
                "sparse (142/411) to use as a substitute, so affected rows are "
                "excluded from Track A scoring rather than assigned a fabricated "
                "probability. prediction_records is left untouched -- this is a "
                "scoring-time exclusion, not a data rewrite."
            ),
        })
        trail_path.write_text(_json.dumps(entries, indent=2))
    except Exception:
        logger.exception("TRACK_A_SCORING: failed to write correction-trail file")


def _score_bucket_row(buckets: dict, mkt: str, p: float, y: int, EPS: float) -> None:
    p = max(EPS, min(1 - EPS, p))
    bs = (p - y) ** 2
    ll = -(y * math.log(p) + (1 - y) * math.log(1 - p))
    b = buckets.setdefault(mkt, {"n": 0, "wins": 0, "bs_sum": 0.0, "ll_sum": 0.0})
    b["n"] += 1
    b["wins"] += y
    b["bs_sum"] += bs
    b["ll_sum"] += ll


def _calibration_bin_row(calibration: dict, mkt: str, p: float) -> dict:
    bin_idx = min(int(p * 10), 9)
    cb = calibration.setdefault(mkt, [{"sum_p": 0.0, "sum_y": 0, "n": 0} for _ in range(10)])
    return cb[bin_idx]


def _finalize_by_market(market_buckets: dict) -> dict:
    return {
        mkt: {
            "n": v["n"],
            "accuracy": round(v["wins"] / v["n"], 4) if v["n"] else 0,
            "brier": round(v["bs_sum"] / v["n"], 4) if v["n"] else 0,
            "logloss": round(v["ll_sum"] / v["n"], 4) if v["n"] else 0,
        }
        for mkt, v in sorted(market_buckets.items())
    }


def _finalize_calibration(calibration: dict) -> dict:
    return {
        mkt: [
            {
                "bin_label": f"{i*10}–{i*10+10}%",
                "mean_pred": round(cb[i]["sum_p"] / cb[i]["n"], 3) if cb[i]["n"] else None,
                "actual_rate": round(cb[i]["sum_y"] / cb[i]["n"], 3) if cb[i]["n"] else None,
                "n": cb[i]["n"],
            }
            for i in range(10)
        ]
        for mkt, cb in calibration.items()
    }


def get_track_a_stats() -> dict:
    """
    Compute Track A accuracy stats from settled PredictionRecords.
    Returns stats by market and top-10 leagues.
    Does NOT import from web_ui.py.

    Rows where our_prob < 0.5 for a binary market's predicted (argmax-selected)
    outcome are excluded from scoring -- see _log_corrupted_prob_trail(). That
    combination is mathematically impossible for a healthy write path (our_prob
    IS the max of the two-outcome dict), so its presence flags corrupted data,
    not a genuinely unconfident model.

    Phase 33 Task 4: also scores a parallel "calibrated" column -- our_prob run
    live through TODAY's active LeagueCalibrationEngine calibration (not the
    stored calibrated_prob column, which is frozen at whatever was active when
    each row was written). This is what keeps the calibrator's live value
    measurable forever without ever needing another "which ECE is this"
    investigation: it's always scored against the calibration that's active
    right now, on the exact same settled rows the raw column uses. our_prob
    scoring (`by_market`/`calibration`) remains the permanent, unchanged
    baseline record -- nothing here touches it.
    """
    try:
        from src.storage.models import PredictionRecord
    except ImportError:
        return {"error": "PredictionRecord model not available"}

    EPS = 1e-7

    with get_session() as s:
        rows = s.execute(
            select(
                PredictionRecord.market,
                PredictionRecord.our_prob,
                PredictionRecord.won,
                Fixture.league_id,
                League.name.label("league_name"),
            )
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .join(League, Fixture.league_id == League.id)
            .where(PredictionRecord.settled == True)
            .where(PredictionRecord.our_prob.isnot(None))
            .where(PredictionRecord.won.isnot(None))
        ).all()

    if not rows:
        return {"total": 0, "by_market": {}, "by_league": [], "calibration": {},
                "by_market_calibrated": {}, "calibration_calibrated": {}}

    cal_snapshot = _load_calibration_snapshot()

    # ── By market ─────────────────────────────────────────────────────────────
    market_buckets: dict[str, dict] = {}
    market_buckets_cal: dict[str, dict] = {}
    league_buckets: dict[tuple, dict] = {}
    calibration: dict[str, list] = {}
    calibration_cal: dict[str, list] = {}
    excluded_corrupted: dict[str, int] = {}

    for r in rows:
        mkt = r.market or "unknown"
        our_prob = float(r.our_prob or 0.5)

        if mkt in _BINARY_MARKETS and our_prob < 0.5:
            excluded_corrupted[mkt] = excluded_corrupted.get(mkt, 0) + 1
            continue

        p = max(EPS, min(1 - EPS, our_prob))
        y = 1 if r.won else 0

        _score_bucket_row(market_buckets, mkt, p, y, EPS)
        cb = _calibration_bin_row(calibration, mkt, p)
        cb["sum_p"] += p
        cb["sum_y"] += y
        cb["n"] += 1

        p_cal, _version = cal_snapshot.apply(mkt, r.league_id, our_prob)
        _score_bucket_row(market_buckets_cal, mkt, p_cal, y, EPS)
        cb_cal = _calibration_bin_row(calibration_cal, mkt, max(EPS, min(1 - EPS, p_cal)))
        cb_cal["sum_p"] += max(EPS, min(1 - EPS, p_cal))
        cb_cal["sum_y"] += y
        cb_cal["n"] += 1

        # league stats (raw only -- this table is about accuracy by league, not calibration)
        key = (r.league_id, r.league_name)
        lb = league_buckets.setdefault(key, {"n": 0, "wins": 0, "bs_sum": 0.0})
        lb["n"] += 1
        lb["wins"] += y
        lb["bs_sum"] += (p - y) ** 2

    by_league = sorted(
        [
            {
                "league_id": k[0],
                "league_name": k[1],
                "n": v["n"],
                "accuracy": round(v["wins"] / v["n"], 4) if v["n"] else 0,
                "brier": round(v["bs_sum"] / v["n"], 4) if v["n"] else 0,
            }
            for k, v in league_buckets.items()
        ],
        key=lambda x: -x["n"],
    )[:15]

    _log_corrupted_prob_trail(excluded_corrupted)

    return {
        "total": len(rows) - sum(excluded_corrupted.values()),
        "by_market": _finalize_by_market(market_buckets),
        "by_market_calibrated": _finalize_by_market(market_buckets_cal),
        "by_league": by_league,
        "calibration": _finalize_calibration(calibration),
        "calibration_calibrated": _finalize_calibration(calibration_cal),
        "excluded_corrupted": excluded_corrupted,
    }


# ── Predictions (Track A + B overlay) ─────────────────────────────────────────

def get_predictions_for_upcoming() -> list[dict]:
    """
    Upcoming predictions for NS fixtures — includes home/away team names and full
    h2h probability vector where stored.

    prob_home/prob_draw/prob_away are populated for all upcoming fixtures since
    the Phase 13c backfill (2026-07-01); older settled records may still lack
    them (re-running the model on past fixtures would leak current standings),
    in which case the view falls back to predicted_outcome + our_prob.

    Phase 33 Task 4: each market dict carries served_prob (served_prob_home/
    draw/away for h2h) alongside the raw our_prob/prob_home/draw/away -- see
    _serve_prob()'s docstring for what's calibrated and what isn't.
    """
    try:
        from src.storage.models import PredictionRecord, Team
    except ImportError:
        return []

    from sqlalchemy.orm import aliased
    HomeTeam = aliased(Team)
    AwayTeam = aliased(Team)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now + timedelta(days=7)

    with get_session() as s:
        # Subquery: count Pinnacle snapshots per fixture (avoids GROUP BY explosion)
        from sqlalchemy import literal_column
        pin_sub = (
            select(OddsSnapshot.fixture_id, func.count(OddsSnapshot.id).label("snap_count"))
            .where(OddsSnapshot.bookmaker_name == "Pinnacle")
            .group_by(OddsSnapshot.fixture_id)
            .subquery()
        )

        rows = s.execute(
            select(
                Fixture.id.label("fixture_id"),
                Fixture.date,
                Fixture.league_id,
                League.name.label("league_name"),
                HomeTeam.name.label("home_team_name"),
                AwayTeam.name.label("away_team_name"),
                PredictionRecord.market,
                PredictionRecord.our_prob,
                PredictionRecord.predicted_outcome,
                PredictionRecord.prob_home,
                PredictionRecord.prob_draw,
                PredictionRecord.prob_away,
                PredictionRecord.ev,
                PredictionRecord.odds_decimal,
                PredictionRecord.bookmaker,
                PredictionRecord.data_context,
                func.coalesce(pin_sub.c.snap_count, 0).label("has_pinnacle"),
            )
            .join(PredictionRecord, PredictionRecord.fixture_id == Fixture.id)
            .join(League, Fixture.league_id == League.id)
            .join(HomeTeam, Fixture.home_team_id == HomeTeam.id)
            .join(AwayTeam, Fixture.away_team_id == AwayTeam.id)
            .outerjoin(pin_sub, pin_sub.c.fixture_id == Fixture.id)
            .where(Fixture.status == "NS")
            .where(Fixture.date >= now)
            .where(Fixture.date <= cutoff)
            .where(PredictionRecord.settled == False)
            .order_by(Fixture.date, Fixture.league_id, Fixture.id, PredictionRecord.market)
            .limit(500)
        ).all()

    cal_snapshot = _load_calibration_snapshot()

    fixtures: dict[int, dict] = {}
    for r in rows:
        fid = r.fixture_id
        if fid not in fixtures:
            fixtures[fid] = {
                "fixture_id": fid,
                "date": r.date,
                "league_id": r.league_id,
                "league_name": r.league_name,
                "home_team": r.home_team_name or f"Team {fid}",
                "away_team": r.away_team_name or f"Team {fid}",
                "markets": [],
            }

        our_prob = float(r.our_prob) if r.our_prob is not None else None
        served_prob, is_calibrated = _serve_prob(
            cal_snapshot, r.market, r.league_id, our_prob, r.data_context
        )

        prob_home = float(r.prob_home) if r.prob_home is not None else None
        prob_draw = float(r.prob_draw) if r.prob_draw is not None else None
        prob_away = float(r.prob_away) if r.prob_away is not None else None
        served_home, served_draw, served_away = prob_home, prob_draw, prob_away
        if (
            r.market == "h2h" and is_calibrated
            and prob_home is not None and prob_draw is not None and prob_away is not None
        ):
            side = _H2H_SIDE.get(str(r.predicted_outcome).upper())
            if side is not None:
                from src.calibration.league_calibration_engine import LeagueCalibrationEngine
                served_home, served_draw, served_away = LeagueCalibrationEngine.renormalize_h2h_vector(
                    prob_home, prob_draw, prob_away, side, served_prob
                )

        fixtures[fid]["markets"].append({
            "market": r.market,
            "our_prob": round(our_prob, 3) if our_prob is not None else None,
            "served_prob": round(served_prob, 3) if served_prob is not None else None,
            "is_calibrated": is_calibrated,
            "predicted_outcome": r.predicted_outcome,
            "prob_home": round(prob_home, 3) if prob_home is not None else None,
            "prob_draw": round(prob_draw, 3) if prob_draw is not None else None,
            "prob_away": round(prob_away, 3) if prob_away is not None else None,
            "served_prob_home": round(served_home, 3) if served_home is not None else None,
            "served_prob_draw": round(served_draw, 3) if served_draw is not None else None,
            "served_prob_away": round(served_away, 3) if served_away is not None else None,
            "ev": round(float(r.ev), 4) if r.ev else None,
            "odds_decimal": round(float(r.odds_decimal), 2) if r.odds_decimal else None,
            "bookmaker": r.bookmaker,
            "is_pinnacle": r.bookmaker == "Pinnacle",
            "has_pinnacle": r.has_pinnacle > 0,
            "data_context": r.data_context,
        })

    _attach_soft_odds(fixtures)
    return list(fixtures.values())


# ── Prediction Explorer ────────────────────────────────────────────────────────

def get_league_country_map() -> dict[str, list[dict]]:
    """Return {country: [{id, name}]} for all leagues that have at least one prediction."""
    try:
        from src.storage.models import PredictionRecord, Team
    except ImportError:
        return {}
    with get_session() as s:
        rows = s.execute(
            select(League.id, League.name, League.country)
            .join(Fixture, Fixture.league_id == League.id)
            .join(PredictionRecord, PredictionRecord.fixture_id == Fixture.id)
            .where(League.country.isnot(None))
            .distinct()
            .order_by(League.country, League.name)
        ).all()
    result: dict[str, list[dict]] = {}
    for r in rows:
        cty = r.country
        if cty not in result:
            result[cty] = []
        result[cty].append({"id": r.id, "name": r.name})
    return result


def get_explorer_data(
    tab: str = "all",
    country: str | None = None,
    league_id: int | None = None,
    market: str | None = None,
    team: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    data_context: str | None = None,
    sort: str = "date",
    sort_dir: str = "desc",
    page: int = 0,
    page_size: int = 50,
) -> dict:
    """
    Server-side filtered, sorted, paginated prediction explorer.

    Uses minimal JOINs — only joins League and Team tables when those filters
    are actually active, so the unfiltered 'all' tab stays fast.

    Inner query: GROUP BY fixture_id, LIMIT/OFFSET on fixtures.
    Outer query: expands those fixture IDs to all market rows for display.
    Count query: COUNT(DISTINCT fixture_id) with same WHERE.

    Returns {fixtures: [...], total: int, page: int, page_size: int, query_ms: float}.
    """
    try:
        from src.storage.models import PredictionRecord, Team
    except ImportError:
        return {"fixtures": [], "total": 0, "page": page, "page_size": page_size, "query_ms": 0}

    HomeTeam = aliased(Team)
    AwayTeam = aliased(Team)

    t0 = time.time()

    # Parse date range
    date_from_dt: datetime | None = None
    date_to_dt: datetime | None = None
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            pass

    # Flags for which tables are needed in inner/count queries
    need_league = bool(country or league_id or sort == "league")
    need_teams = bool(team)
    need_fixture = bool(date_from_dt or date_to_dt or league_id or need_league)
    # Date sort always via Fixture.date
    if sort == "date":
        need_fixture = True

    # Sort column
    if sort in ("accuracy", "brier") and tab != "settled":
        sort = "date"  # accuracy/brier only meaningful for settled
        need_fixture = True

    if sort == "confidence":
        sort_col = (
            func.max(PredictionRecord.our_prob)
            if market
            else func.max(
                sa_case((PredictionRecord.market == "h2h", PredictionRecord.our_prob), else_=None)
            )
        )
    elif sort == "accuracy":
        sort_col = func.avg(
            sa_case((PredictionRecord.won == True, 1.0), else_=0.0)  # noqa: E712
        )
    elif sort == "brier":
        won_int = sa_case((PredictionRecord.won == True, 1.0), else_=0.0)  # noqa: E712
        diff = PredictionRecord.our_prob - won_int
        sort_col = func.avg(diff * diff)
    elif sort == "league":
        sort_col = League.name
    else:  # date
        sort_col = Fixture.date

    order_expr = sort_col.desc() if sort_dir == "desc" else sort_col.asc()

    # ── Count query — minimal joins ────────────────────────────────────────────
    # Start from PredictionRecord; only join what filters require.
    count_q = select(func.count(func.distinct(PredictionRecord.fixture_id))).select_from(
        PredictionRecord
    )
    count_conds = []
    if tab == "upcoming":
        count_conds.append(PredictionRecord.settled == False)  # noqa: E712
    elif tab == "settled":
        count_conds.append(PredictionRecord.settled == True)  # noqa: E712
    if market:
        count_conds.append(PredictionRecord.market == market)
    if data_context:
        count_conds.append(PredictionRecord.data_context == data_context)

    if need_fixture or need_league or need_teams:
        count_q = count_q.join(Fixture, Fixture.id == PredictionRecord.fixture_id)
    if need_league:
        count_q = count_q.join(League, Fixture.league_id == League.id)
    if need_teams:
        count_q = count_q.join(HomeTeam, Fixture.home_team_id == HomeTeam.id)
        count_q = count_q.join(AwayTeam, Fixture.away_team_id == AwayTeam.id)

    if date_from_dt:
        count_conds.append(Fixture.date >= date_from_dt)
    if date_to_dt:
        count_conds.append(Fixture.date <= date_to_dt)
    if league_id:
        count_conds.append(Fixture.league_id == league_id)
    if country:
        count_conds.append(League.country == country)
    if team:
        count_conds.append(
            or_(
                HomeTeam.name.ilike(f"%{team}%"),
                AwayTeam.name.ilike(f"%{team}%"),
            )
        )
    if count_conds:
        count_q = count_q.where(*count_conds)

    # ── Inner query — paged fixture IDs ───────────────────────────────────────
    # Always joins PredictionRecord → Fixture (needed for GROUP BY + sort).
    # Only adds League/Team when their filters are active.
    inner_base = (
        select(Fixture.id.label("fid"), sort_col.label("sort_key"))
        .select_from(PredictionRecord)
        .join(Fixture, Fixture.id == PredictionRecord.fixture_id)
    )
    if need_league:
        inner_base = inner_base.join(League, Fixture.league_id == League.id)
    if need_teams:
        inner_base = inner_base.join(HomeTeam, Fixture.home_team_id == HomeTeam.id)
        inner_base = inner_base.join(AwayTeam, Fixture.away_team_id == AwayTeam.id)

    # Reuse count conditions for inner (they reference the same column expressions)
    inner_q = (
        inner_base
        .where(*count_conds)
        .group_by(Fixture.id)
        .order_by(order_expr)
        .limit(page_size)
        .offset(page * page_size)
        .subquery("pf")
    )

    # ── Outer query — full market rows for paged fixtures ─────────────────────
    HomeTeam2 = aliased(Team)
    AwayTeam2 = aliased(Team)
    outer_conds = []
    if tab == "upcoming":
        outer_conds.append(PredictionRecord.settled == False)  # noqa: E712
    elif tab == "settled":
        outer_conds.append(PredictionRecord.settled == True)  # noqa: E712

    outer_q = (
        select(
            inner_q.c.sort_key,
            Fixture.id.label("fixture_id"),
            Fixture.date,
            Fixture.status.label("fixture_status"),
            Fixture.goals_home,
            Fixture.goals_away,
            Fixture.league_id,
            League.name.label("league_name"),
            League.country,
            HomeTeam2.name.label("home_team"),
            AwayTeam2.name.label("away_team"),
            PredictionRecord.market,
            PredictionRecord.our_prob,
            PredictionRecord.predicted_outcome,
            PredictionRecord.actual_outcome,
            PredictionRecord.won,
            PredictionRecord.settled,
            PredictionRecord.prob_home,
            PredictionRecord.prob_draw,
            PredictionRecord.prob_away,
            PredictionRecord.data_context,
            PredictionRecord.served_prob.label("frozen_served_prob"),
            PredictionRecord.served_calibration_version,
        )
        .select_from(inner_q)
        .join(Fixture, Fixture.id == inner_q.c.fid)
        .join(PredictionRecord, PredictionRecord.fixture_id == Fixture.id)
        .join(League, Fixture.league_id == League.id)
        .join(HomeTeam2, Fixture.home_team_id == HomeTeam2.id)
        .join(AwayTeam2, Fixture.away_team_id == AwayTeam2.id)
        .where(*outer_conds)
        .order_by(
            inner_q.c.sort_key.desc() if sort_dir == "desc" else inner_q.c.sort_key.asc(),
            Fixture.id,
            PredictionRecord.market,
        )
    )

    with get_session() as s:
        total = s.execute(count_q).scalar() or 0
        rows = s.execute(outer_q).all()

    t1 = time.time()

    # Phase 33b: settled rows now display the served_prob FROZEN at kickoff
    # (v2/db_v2.py::freeze_served_probs_for_fixture()) alongside raw our_prob,
    # rather than silently falling back to raw alone -- see _mkt_cell()'s
    # "served X% · raw Y%" rendering. Track A itself is untouched and keeps
    # scoring settled history on raw our_prob as the permanent baseline record;
    # this is purely a display concern (what did we actually SHOW someone).
    # Rows settled before this migration have no frozen value (era boundary --
    # settled history is never rewritten) and fall back to raw-only, exactly
    # as before. Unsettled rows still get the live-recalibrated treatment.
    cal_snapshot = _load_calibration_snapshot()

    # Assemble per-fixture dicts preserving sort order from inner query
    fixtures_map: dict[int, dict] = {}
    fixture_order: list[int] = []
    for r in rows:
        fid = r.fixture_id
        if fid not in fixtures_map:
            fixtures_map[fid] = {
                "fixture_id": fid,
                "date": r.date,
                "fixture_status": r.fixture_status,
                "goals_home": r.goals_home,
                "goals_away": r.goals_away,
                "league_name": r.league_name or "",
                "country": r.country or "",
                "home_team": r.home_team or "?",
                "away_team": r.away_team or "?",
                "markets": {},
            }
            fixture_order.append(fid)

        our_prob = float(r.our_prob) if r.our_prob is not None else None
        prob_home = float(r.prob_home) if r.prob_home is not None else None
        prob_draw = float(r.prob_draw) if r.prob_draw is not None else None
        prob_away = float(r.prob_away) if r.prob_away is not None else None

        if r.settled:
            frozen = float(r.frozen_served_prob) if r.frozen_served_prob is not None else None
            is_calibrated = frozen is not None and r.served_calibration_version is not None
            served_prob = frozen if frozen is not None else our_prob
            served_home, served_draw, served_away = prob_home, prob_draw, prob_away
            if (
                is_calibrated and r.market == "h2h"
                and prob_home is not None and prob_draw is not None and prob_away is not None
            ):
                side = _H2H_SIDE.get(str(r.predicted_outcome).upper())
                if side is not None:
                    from src.calibration.league_calibration_engine import LeagueCalibrationEngine
                    served_home, served_draw, served_away = LeagueCalibrationEngine.renormalize_h2h_vector(
                        prob_home, prob_draw, prob_away, side, served_prob
                    )
        else:
            served_prob, is_calibrated = _serve_prob(
                cal_snapshot, r.market, r.league_id, our_prob, r.data_context
            )
            served_home, served_draw, served_away = prob_home, prob_draw, prob_away
            if (
                r.market == "h2h" and is_calibrated
                and prob_home is not None and prob_draw is not None and prob_away is not None
            ):
                side = _H2H_SIDE.get(str(r.predicted_outcome).upper())
                if side is not None:
                    from src.calibration.league_calibration_engine import LeagueCalibrationEngine
                    served_home, served_draw, served_away = LeagueCalibrationEngine.renormalize_h2h_vector(
                        prob_home, prob_draw, prob_away, side, served_prob
                    )

        fixtures_map[fid]["markets"][r.market] = {
            "market": r.market,
            "our_prob": round(our_prob, 3) if our_prob is not None else None,
            "served_prob": round(served_prob, 3) if served_prob is not None else None,
            "is_calibrated": is_calibrated,
            "predicted_outcome": r.predicted_outcome,
            "actual_outcome": r.actual_outcome,
            "won": r.won,
            "settled": r.settled,
            "prob_home": round(prob_home, 3) if prob_home is not None else None,
            "prob_draw": round(prob_draw, 3) if prob_draw is not None else None,
            "prob_away": round(prob_away, 3) if prob_away is not None else None,
            "served_prob_home": round(served_home, 3) if served_home is not None else None,
            "served_prob_draw": round(served_draw, 3) if served_draw is not None else None,
            "served_prob_away": round(served_away, 3) if served_away is not None else None,
            "data_context": r.data_context,
        }

    _attach_soft_odds(fixtures_map)
    return {
        "fixtures": [fixtures_map[fid] for fid in fixture_order],
        "total": total,
        "page": page,
        "page_size": page_size,
        "query_ms": (t1 - t0) * 1000,
    }
