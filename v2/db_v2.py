"""
v2/db_v2.py — Read-only DB helpers for web_ui_v2.

Imports ONLY from shared infrastructure (src/storage, config).
Does NOT import from scripts/web_ui.py.
"""
from __future__ import annotations

import math
import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, func, text

from src.storage.db import get_session
from src.storage.models import Fixture, OddsSnapshot, League

logger = logging.getLogger(__name__)

QUOTA_LOG = Path("logs/quota_log.csv")


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


# ── Track A ────────────────────────────────────────────────────────────────────

def get_track_a_stats() -> dict:
    """
    Compute Track A accuracy stats from settled PredictionRecords.
    Returns stats by market and top-10 leagues.
    Does NOT import from web_ui.py.
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
        return {"total": 0, "by_market": {}, "by_league": [], "calibration": {}}

    # ── By market ─────────────────────────────────────────────────────────────
    market_buckets: dict[str, dict] = {}
    league_buckets: dict[tuple, dict] = {}
    calibration: dict[str, list] = {}

    for r in rows:
        p = float(r.our_prob or 0.5)
        p = max(EPS, min(1 - EPS, p))
        y = 1 if r.won else 0
        bs = (p - y) ** 2
        ll = -(y * math.log(p) + (1 - y) * math.log(1 - p))
        mkt = r.market or "unknown"

        # market stats
        mb = market_buckets.setdefault(mkt, {"n": 0, "wins": 0, "bs_sum": 0.0, "ll_sum": 0.0})
        mb["n"] += 1
        mb["wins"] += y
        mb["bs_sum"] += bs
        mb["ll_sum"] += ll

        # league stats
        key = (r.league_id, r.league_name)
        lb = league_buckets.setdefault(key, {"n": 0, "wins": 0, "bs_sum": 0.0})
        lb["n"] += 1
        lb["wins"] += y
        lb["bs_sum"] += bs

        # calibration bins per market
        bin_idx = min(int(p * 10), 9)
        cb = calibration.setdefault(mkt, [{"sum_p": 0.0, "sum_y": 0, "n": 0} for _ in range(10)])
        cb[bin_idx]["sum_p"] += p
        cb[bin_idx]["sum_y"] += y
        cb[bin_idx]["n"] += 1

    by_market = {
        mkt: {
            "n": v["n"],
            "accuracy": round(v["wins"] / v["n"], 4) if v["n"] else 0,
            "brier": round(v["bs_sum"] / v["n"], 4) if v["n"] else 0,
            "logloss": round(v["ll_sum"] / v["n"], 4) if v["n"] else 0,
        }
        for mkt, v in sorted(market_buckets.items())
    }

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

    calibration_out = {
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

    return {
        "total": len(rows),
        "by_market": by_market,
        "by_league": by_league,
        "calibration": calibration_out,
    }


# ── Predictions (Track A + B overlay) ─────────────────────────────────────────

def get_predictions_for_upcoming() -> list[dict]:
    """
    Upcoming predictions for NS fixtures — includes home/away team names and full
    h2h probability vector where stored.

    Data-gap note: prob_home/prob_draw/prob_away are NULL for all current records
    (columns added in Phase 11b schema but never populated by the prediction engine).
    The view falls back to predicted_outcome + our_prob for H2H labelling.
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
        fixtures[fid]["markets"].append({
            "market": r.market,
            "our_prob": round(float(r.our_prob), 3) if r.our_prob else None,
            "predicted_outcome": r.predicted_outcome,
            "prob_home": round(float(r.prob_home), 3) if r.prob_home else None,
            "prob_draw": round(float(r.prob_draw), 3) if r.prob_draw else None,
            "prob_away": round(float(r.prob_away), 3) if r.prob_away else None,
            "ev": round(float(r.ev), 4) if r.ev else None,
            "odds_decimal": round(float(r.odds_decimal), 2) if r.odds_decimal else None,
            "has_pinnacle": r.has_pinnacle > 0,
        })

    return list(fixtures.values())
