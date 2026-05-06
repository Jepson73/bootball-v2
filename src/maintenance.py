"""
src/maintenance.py - Routine data-integrity maintenance.

Runs each execution cycle (via ExecutionRuntime._run_maintenance) to keep
the DB clean without manual intervention.

Functions:
  fix_ft_null_goals()       — fetch scores for FT fixtures missing goals
  fix_orphaned_fixtures()   — fetch standings for leagues without any
  run_maintenance()         — run both and return a summary dict
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from src.storage.db import get_session
from src.storage.models import Fixture, Standing, League

logger = logging.getLogger(__name__)


# ── 1. Fix FT fixtures with missing scores ────────────────────────────────────

def fix_ft_null_goals(days: int = 30) -> list[dict]:
    """Fetch and persist scores for FT fixtures that have goals_home = NULL.

    Returns a list of dicts describing each fixture that was fixed.
    """
    from src.ingestion.client import APIFootballClient

    client = APIFootballClient()
    cutoff = datetime.utcnow() - timedelta(days=days)
    fixed = []

    with get_session() as s:
        missing = s.execute(
            select(Fixture)
            .where(Fixture.status.in_(["FT", "AET", "PEN"]))
            .where(Fixture.goals_home.is_(None))
            .where(Fixture.date >= cutoff)
        ).scalars().all()

        if not missing:
            return []

        logger.info(f"[MAINTENANCE] {len(missing)} FT fixtures missing scores — fetching")
        id_map = {f.id: f for f in missing}

        ids = list(id_map.keys())
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            try:
                raw = client.get(
                    "fixtures",
                    {"ids": "-".join(str(x) for x in chunk)},
                    force_refresh=True,
                )
            except Exception as e:
                logger.warning(f"[MAINTENANCE] Score fetch failed for chunk {chunk[:3]}…: {e}")
                continue

            for entry in raw:
                fid = entry.get("fixture", {}).get("id")
                goals = entry.get("goals", {})
                gh = goals.get("home")
                ga = goals.get("away")
                teams = entry.get("teams", {})
                home_name = teams.get("home", {}).get("name", "?")
                away_name = teams.get("away", {}).get("name", "?")

                if fid in id_map and gh is not None and ga is not None:
                    fix = id_map[fid]
                    old_status = fix.status
                    fix.goals_home = gh
                    fix.goals_away = ga
                    fix.outcome = "H" if gh > ga else ("A" if ga > gh else "D")
                    record = {
                        "fixture_id": fid,
                        "home": home_name,
                        "away": away_name,
                        "score": f"{gh}-{ga}",
                        "outcome": fix.outcome,
                        "status": old_status,
                        "date": str(fix.date)[:10],
                    }
                    fixed.append(record)
                    logger.info(
                        f"[MAINTENANCE] Score fixed: {home_name} {gh}-{ga} {away_name} "
                        f"(fixture {fid}, {old_status}, {record['date']})"
                    )

        s.commit()

    if fixed:
        logger.info(f"[MAINTENANCE] Fixed scores for {len(fixed)} fixtures")
    return fixed


# ── 2. Fix orphaned fixtures (leagues with no standings) ──────────────────────

_NO_STANDINGS_KEYWORDS = [
    "cup", "trophy", "pokal", "beker", "copa", "coupe", "coppa", "taça",
    "friendl", "nations", "champions league", "europa league",
    "concacaf", "conmebol", "libertador", "sudamerican",
    "caf ", "afc ", "ofc ", "saff", "eaff", "asean", "youth league",
]


def fix_orphaned_fixtures(season: int = 2025) -> list[dict]:
    """Fetch standings for leagues that have fixtures but no standing rows.

    'Orphaned' = non-cup league in fixtures table but absent from standings.
    Returns a list of dicts describing each league that was fixed (or skipped).
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from src.ingestion.client import APIFootballClient

    client = APIFootballClient()
    report = []

    with get_session() as s:
        # Leagues in fixtures but not in standings (exclude cups by name)
        leagues_with_standings = select(Standing.league_id).distinct()
        leagues_in_fixtures = select(Fixture.league_id).distinct()
        cup_leagues = select(League.id).where(League.name.ilike("%cup%"))

        orphan_league_ids = s.execute(
            leagues_in_fixtures
            .where(Fixture.league_id.notin_(leagues_with_standings))
            .where(Fixture.league_id.notin_(cup_leagues))
        ).scalars().all()

        if not orphan_league_ids:
            return []

        logger.info(
            f"[MAINTENANCE] {len(orphan_league_ids)} leagues have fixtures but no standings — fetching"
        )

        for league_id in orphan_league_ids:
            league_name = s.execute(
                select(League.name).where(League.id == league_id)
            ).scalar() or str(league_id)

            # Skip competitions that structurally have no league standings
            lname_lower = league_name.lower()
            if any(kw in lname_lower for kw in _NO_STANDINGS_KEYWORDS):
                report.append({"league_id": league_id, "league": league_name, "status": "no_standings_expected", "rows": 0})
                continue

            # Try current season, then fall back two seasons
            raw = None
            for s_year in (season, season - 1, season - 2):
                try:
                    raw = client.get_standings(league_id, s_year)
                    if raw:
                        break
                except Exception as e:
                    logger.warning(f"[MAINTENANCE] Standings fetch failed league {league_id} ({league_name}) season {s_year}: {e}")

            if not raw:
                logger.info(f"[MAINTENANCE] No standings for league {league_id} ({league_name}) — skipped")
                report.append({"league_id": league_id, "league": league_name, "status": "no_data", "rows": 0})
                continue

            rows = []
            for entry in raw:
                team = entry.get("team", {})
                all_ = entry.get("all", {})
                goals = all_.get("goals", {})
                rows.append({
                    "league_id": league_id,
                    "season": season,
                    "team_id": team.get("id"),
                    "team_name": team.get("name", ""),
                    "rank": entry.get("rank"),
                    "points": entry.get("points"),
                    "played": all_.get("played"),
                    "won": all_.get("win"),
                    "drawn": all_.get("draw"),
                    "lost": all_.get("lose"),
                    "goals_for": goals.get("for"),
                    "goals_against": goals.get("against"),
                    "goal_diff": entry.get("goalsDiff"),
                    "fetched_at": datetime.utcnow(),
                })

            if rows:
                stmt = sqlite_insert(Standing).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["league_id", "season", "team_id"],
                    set_={k: getattr(stmt.excluded, k) for k in
                          ["team_name", "rank", "points", "played", "won",
                           "drawn", "lost", "goals_for", "goals_against",
                           "goal_diff", "fetched_at"]},
                )
                s.execute(stmt)
                s.commit()
                logger.info(
                    f"[MAINTENANCE] Standings fixed: league {league_id} ({league_name}), {len(rows)} teams"
                )
                report.append({
                    "league_id": league_id,
                    "league": league_name,
                    "status": "fixed",
                    "rows": len(rows),
                })
            else:
                report.append({"league_id": league_id, "league": league_name, "status": "empty", "rows": 0})

    return report


# ── 3. Combined maintenance run ───────────────────────────────────────────────

def run_maintenance(days: int = 30, season: int = 2025) -> dict:
    """Run all maintenance tasks. Called each execution cycle.

    Returns a summary dict with counts and detail lists.
    """
    summary = {
        "ft_null_goals_fixed": 0,
        "ft_null_goals_detail": [],
        "orphaned_leagues_fixed": 0,
        "orphaned_leagues_skipped": 0,
        "orphaned_detail": [],
        "ran_at": datetime.utcnow().isoformat(),
    }

    # Task 1: scores
    try:
        fixed_scores = fix_ft_null_goals(days=days)
        summary["ft_null_goals_fixed"] = len(fixed_scores)
        summary["ft_null_goals_detail"] = fixed_scores
    except Exception as e:
        logger.warning(f"[MAINTENANCE] fix_ft_null_goals failed: {e}")

    # Task 2: orphaned leagues
    try:
        orphan_report = fix_orphaned_fixtures(season=season)
        summary["orphaned_detail"] = orphan_report
        summary["orphaned_leagues_fixed"] = sum(1 for r in orphan_report if r["status"] == "fixed")
        summary["orphaned_leagues_skipped"] = sum(1 for r in orphan_report if r["status"] != "fixed")
    except Exception as e:
        logger.warning(f"[MAINTENANCE] fix_orphaned_fixtures failed: {e}")

    if summary["ft_null_goals_fixed"] or summary["orphaned_leagues_fixed"]:
        logger.info(
            f"[MAINTENANCE] Done — scores fixed: {summary['ft_null_goals_fixed']}, "
            f"leagues fixed: {summary['orphaned_leagues_fixed']}, "
            f"leagues skipped: {summary['orphaned_leagues_skipped']}"
        )
    else:
        logger.info("[MAINTENANCE] Done — nothing to fix")

    return summary
