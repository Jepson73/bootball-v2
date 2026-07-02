"""
src/ingestion/odds_snapshot_capture.py

Shared odds_snapshots writer for both the active trajectory scheduler
(scripts/odds_trajectory_scheduler.py) and the passive piggyback layer inside
scripts/odds_poll.py. One parsing/dedupe implementation so the two capture paths
can't drift into different bet_name handling or different dedupe windows.

Unlike config/forward_leagues.py's CAPTURE_BOOKMAKERS (Pinnacle + Bet365 only,
built for the narrow 4-league CLV experiment), this module writes EVERY bookmaker
in the response — soft-book trajectories have standalone value (the user's own
soft-book betting, which-book-moves-first timing) even where they can't produce
Pinnacle-referenced CLV.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from src.storage.models import OddsSnapshot

logger = logging.getLogger(__name__)

# Default dedupe window for the passive layer and for a scheduler run's own
# intra-run safety check. Deliberately shorter than the scheduler's own phase
# thresholds (~1h hourly-phase, ~20h daily-phase — see odds_trajectory_scheduler.py)
# so it never suppresses a legitimate new scheduled touch, only genuine same-cycle
# overlap between active and passive.
DEFAULT_DEDUPE_MINUTES = 45


def _parse_h2h(bet_values: list[dict]) -> dict:
    out: dict = {}
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Home":
            out["odd_home"] = f
        elif hand == "Draw":
            out["odd_draw"] = f
        elif hand == "Away":
            out["odd_away"] = f
    return out


def _parse_ou25(bet_values: list[dict]) -> dict:
    out: dict = {}
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Over 2.5":
            out["odd_over"] = f
        elif hand == "Under 2.5":
            out["odd_under"] = f
    return out


def _parse_btts(bet_values: list[dict]) -> dict:
    out: dict = {}
    for v in bet_values:
        hand = str(v.get("value", ""))
        odd_val = v.get("odd")
        if not odd_val:
            continue
        try:
            f = float(odd_val)
        except (ValueError, TypeError):
            continue
        if hand == "Yes":
            out["odd_btts_yes"] = f
        elif hand == "No":
            out["odd_btts_no"] = f
    return out


# bet_name strings as returned by API-Football → (market_type, parser).
# Verified against live payloads in Phase 11b (v11b_findings.md) and Phase 22
# (explorer_v2.py h2h scalar-fallback gap) — these are the confirmed variants seen
# in production, not guesses from docs.
_BET_NAME_HANDLERS = {
    "Match Winner": ("h2h", _parse_h2h),
    "1x2": ("h2h", _parse_h2h),
    "Goals Over/Under": ("ou25", _parse_ou25),
    "Over/Under": ("ou25", _parse_ou25),
    "Both Teams Score": ("btts", _parse_btts),
}


def already_captured_within(s, fixture_id: int, market_type: str, bookmaker_name: str, minutes: float) -> bool:
    """True if a snapshot for this (fixture, market, bookmaker) exists within `minutes`."""
    threshold = datetime.utcnow() - timedelta(minutes=minutes)
    count = s.execute(
        select(func.count())
        .select_from(OddsSnapshot)
        .where(OddsSnapshot.fixture_id == fixture_id)
        .where(OddsSnapshot.market_type == market_type)
        .where(OddsSnapshot.bookmaker_name == bookmaker_name)
        .where(OddsSnapshot.captured_at >= threshold)
    ).scalar()
    return (count or 0) > 0


def write_snapshots_from_response(
    s,
    raw_odds: list[dict],
    fixture_id: int,
    captured_at: datetime,
    dedupe_minutes: float = DEFAULT_DEDUPE_MINUTES,
) -> dict:
    """Parse one get_odds() response (already fetched, no API cost here) and insert
    OddsSnapshot rows for every bookmaker present, deduped per (fixture, market, bookmaker).

    Returns {"written": n, "skipped_dedupe": n, "skipped_unparsed": n} — a bare "0 rows
    written" tells you nothing about *why* (empty payload upstream vs every candidate row
    deduped vs unrecognised bet_name); this project has been burned by exactly that kind
    of unobservable-failure gap before (Phase 22/23). Caller is responsible for s.commit().
    """
    stats = {"written": 0, "skipped_dedupe": 0, "skipped_unparsed": 0}
    for fixture_block in raw_odds:
        for bm in fixture_block.get("bookmakers", []):
            bm_name = bm.get("name", "Unknown")

            for bet in bm.get("bets", []):
                bet_name = bet.get("name", "")
                handler = _BET_NAME_HANDLERS.get(bet_name)
                if handler is None:
                    logger.debug(
                        "Unrecognised bet_name=%r for fixture=%d bm=%r — skipping",
                        bet_name, fixture_id, bm_name,
                    )
                    stats["skipped_unparsed"] += 1
                    continue

                market_type, parser = handler
                bet_values = bet.get("values", [])
                if not bet_values:
                    stats["skipped_unparsed"] += 1
                    continue

                parsed = parser(bet_values)
                if not parsed:
                    stats["skipped_unparsed"] += 1
                    continue

                if already_captured_within(s, fixture_id, market_type, bm_name, dedupe_minutes):
                    stats["skipped_dedupe"] += 1
                    continue

                s.add(OddsSnapshot(
                    fixture_id=fixture_id,
                    bookmaker_id=None,
                    bookmaker_name=bm_name,
                    market_type=market_type,
                    captured_at=captured_at,
                    **parsed,
                ))
                stats["written"] += 1

    return stats
