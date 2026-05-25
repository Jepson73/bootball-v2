# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: manual standings refresh for all active leagues (maintenance.py handles this automatically now)
"""
scripts/backfill_standings.py

Refresh league standings for all leagues that have fixtures in the past 10 weeks.
Force-fetches from the API (bypasses cache) so stale/missing standings are corrected.

Usage:
    python3 scripts/backfill_standings.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config.settings import settings
from src.ingestion.client import APIFootballClient, get_api_status
from src.storage.db import get_session, init_db
from src.storage.models import Standing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _upsert_standings(rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_session() as s:
        stmt = sqlite_insert(Standing).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["league_id", "season", "team_id"],
            set_={
                "team_name":      stmt.excluded.team_name,
                "rank":           stmt.excluded.rank,
                "points":         stmt.excluded.points,
                "played":         stmt.excluded.played,
                "won":            stmt.excluded.won,
                "drawn":          stmt.excluded.drawn,
                "lost":           stmt.excluded.lost,
                "goals_for":      stmt.excluded.goals_for,
                "goals_against":  stmt.excluded.goals_against,
                "goal_diff":      stmt.excluded.goal_diff,
                "fetched_at":     stmt.excluded.fetched_at,
            },
        )
        s.execute(stmt)
    return len(rows)


def backfill_standings(weeks: int = 10, limit: int | None = None, dry_run: bool = False) -> dict:
    init_db()
    client = APIFootballClient()

    cutoff = (datetime.utcnow() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    with get_session() as s:
        rows = s.execute(
            text("SELECT DISTINCT league_id FROM fixtures WHERE date >= :c ORDER BY league_id"),
            {"c": cutoff},
        ).fetchall()

    league_ids = [r[0] for r in rows]
    if limit:
        league_ids = league_ids[:limit]

    log.info("Leagues to process: %d  (fixtures since %s)", len(league_ids), cutoff)

    quota = get_api_status()
    log.info("API quota: %d used / %d limit / %d remaining (source: %s)",
             quota["used"], quota["limit"], quota["remaining"], quota["source"])

    if quota["remaining"] < len(league_ids) + 10:
        log.warning("Low API budget — only %d calls left, need %d. Run later or use --limit.",
                    quota["remaining"], len(league_ids))

    ok = skip = err = rows_written = 0
    t0 = time.monotonic()

    for i, lid in enumerate(league_ids, 1):
        season = settings.get_season(lid)
        try:
            # force_refresh=True bypasses cache so stale standings get replaced
            raw_entries = client.get("standings", {"league": lid, "season": season}, force_refresh=True)
        except Exception as e:
            log.warning("[%d/%d] League %d season %d — fetch error: %s", i, len(league_ids), lid, season, e)
            err += 1
            continue

        # API returns the response list; for standings that's a list of league objects
        # each containing nested standings arrays — extract flat list of team entries
        team_rows = []
        for item in raw_entries:
            if isinstance(item, dict):
                if "rank" in item and "team" in item:
                    team_rows.append(item)
                elif "league" in item:
                    for group in item["league"].get("standings", []):
                        if isinstance(group, list):
                            team_rows.extend(group)

        if not team_rows:
            log.debug("[%d/%d] League %d season %d — no standings returned", i, len(league_ids), lid, season)
            skip += 1
            continue

        rows_to_insert = []
        for entry in team_rows:
            team = entry.get("team", {})
            team_id = team.get("id")
            if not team_id:
                continue  # skip entries missing team id (can't upsert without PK)
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

        if not dry_run:
            try:
                rows_written += _upsert_standings(rows_to_insert)
            except Exception as e:
                log.warning("[%d/%d] League %d season %d — DB write error: %s", i, len(league_ids), lid, season, e)
                err += 1
                continue
        ok += 1

        if i % 50 == 0 or i == len(league_ids):
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(league_ids) - i) / rate if rate > 0 else 0
            log.info(
                "Progress: %d/%d  ok=%d skip=%d err=%d rows=%d  %.1f leagues/min  ETA %.0fs",
                i, len(league_ids), ok, skip, err, rows_written, rate * 60, eta,
            )

    log.info("Done. ok=%d skip=%d err=%d rows_written=%d", ok, skip, err, rows_written)
    return {"ok": ok, "skip": skip, "err": err, "rows_written": rows_written}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=10, help="How many weeks back to look for active leagues")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of leagues processed")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    args = parser.parse_args()

    result = backfill_standings(weeks=args.weeks, limit=args.limit, dry_run=args.dry_run)
    sys.exit(0 if result["err"] == 0 else 1)
