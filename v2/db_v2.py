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
            "bookmaker": r.bookmaker,
            "is_pinnacle": r.bookmaker == "Pinnacle",
            "has_pinnacle": r.has_pinnacle > 0,
            "data_context": r.data_context,
        })

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

    # Assemble per-fixture dicts preserving sort order from inner query
    fixtures_map: dict[int, dict] = {}
    fixture_order: list[int] = []
    for r in rows:
        fid = r.fixture_id
        if fid not in fixtures_map:
            fixtures_map[fid] = {
                "fixture_id": fid,
                "date": r.date,
                "league_name": r.league_name or "",
                "country": r.country or "",
                "home_team": r.home_team or "?",
                "away_team": r.away_team or "?",
                "markets": {},
            }
            fixture_order.append(fid)
        fixtures_map[fid]["markets"][r.market] = {
            "market": r.market,
            "our_prob": round(float(r.our_prob), 3) if r.our_prob is not None else None,
            "predicted_outcome": r.predicted_outcome,
            "actual_outcome": r.actual_outcome,
            "won": r.won,
            "settled": r.settled,
            "prob_home": round(float(r.prob_home), 3) if r.prob_home is not None else None,
            "prob_draw": round(float(r.prob_draw), 3) if r.prob_draw is not None else None,
            "prob_away": round(float(r.prob_away), 3) if r.prob_away is not None else None,
            "data_context": r.data_context,
        }

    return {
        "fixtures": [fixtures_map[fid] for fid in fixture_order],
        "total": total,
        "page": page,
        "page_size": page_size,
        "query_ms": (t1 - t0) * 1000,
    }
