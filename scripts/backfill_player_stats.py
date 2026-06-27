#!/usr/bin/env python3
"""
scripts/backfill_player_stats.py

Fetch per-season player statistics for all teams across seasons 2021–2025.

The API endpoint is GET /players?team=<id>&season=<year>.  One call per
(team_id, season) pair.  Completed pairs are recorded in player_fetch_log so
the script resumes cleanly across multiple days.

Usage (manual / ad-hoc):
    python scripts/backfill_player_stats.py                  # all seasons, all teams
    python scripts/backfill_player_stats.py --seasons 2025   # single season
    python scripts/backfill_player_stats.py --limit 500      # stop after N API calls

The daily cron (backfill_cron.py) calls _backfill_player_stats() directly as
Phase 3 after stats backfill.  Estimated total: ~67 000 calls across 5 seasons.
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text

from src.ingestion.client import get_api_status
from src.storage.db import get_session, init_db
from src.storage.models import PlayerFetchLog, PlayerSeasonStats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TARGET_SEASONS = [2021, 2022, 2023, 2024, 2025]
DEFAULT_STOP_AT = 25_000


def _remaining() -> int:
    return get_api_status()["remaining"]


def _done_pairs() -> set[tuple[int, int]]:
    with get_session() as s:
        rows = s.execute(text("SELECT team_id, season FROM player_fetch_log")).fetchall()
    return {(r[0], r[1]) for r in rows}


def _teams_for_season(season: int) -> list[int]:
    with get_session() as s:
        rows = s.execute(
            text("""
                SELECT DISTINCT team_id FROM (
                    SELECT home_team_id AS team_id FROM fixtures WHERE season = :s
                    UNION
                    SELECT away_team_id AS team_id FROM fixtures WHERE season = :s
                )
            """),
            {"s": season},
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def _parse_stat(d: dict, *keys, default=0):
    for k in keys:
        d = d.get(k) or {}
        if not isinstance(d, dict):
            return d if d is not None else default
    return default


def _store_player_stats(player_id: int, team_id: int, season: int, player_info: dict, stats_list: list) -> int:
    """Insert/update rows in player_season_stats.  Returns number of rows upserted."""
    count = 0
    with get_session() as s:
        for stat in stats_list:
            league = stat.get("league") or {}
            league_id = league.get("id")
            if not league_id:
                continue

            games = stat.get("games") or {}
            goals = stat.get("goals") or {}
            shots = stat.get("shots") or {}
            passes = stat.get("passes") or {}
            tackles = stat.get("tackles") or {}
            duels = stat.get("duels") or {}
            dribbles = stat.get("dribbles") or {}
            cards = stat.get("cards") or {}
            fouls = stat.get("fouls") or {}
            penalty = stat.get("penalty") or {}

            try:
                rating_raw = games.get("rating")
                rating = float(rating_raw) if rating_raw else None
            except (ValueError, TypeError):
                rating = None

            try:
                acc_raw = passes.get("accuracy")
                pass_acc = float(acc_raw) if acc_raw else None
            except (ValueError, TypeError):
                pass_acc = None

            existing = s.execute(
                text("""
                    SELECT id FROM player_season_stats
                    WHERE player_id=:pid AND team_id=:tid AND season=:sea AND league_id=:lid
                """),
                {"pid": player_id, "tid": team_id, "sea": season, "lid": league_id},
            ).fetchone()

            row = PlayerSeasonStats(
                player_id=player_id,
                team_id=team_id,
                season=season,
                league_id=league_id,
                player_name=player_info.get("name"),
                position=games.get("position"),
                photo_url=player_info.get("photo"),
                appearances=games.get("appearences") or 0,
                lineups=games.get("lineups") or 0,
                minutes=games.get("minutes") or 0,
                rating=rating,
                goals=goals.get("total") or 0,
                assists=goals.get("assists") or 0,
                goals_conceded=goals.get("conceded") or 0,
                saves=goals.get("saves") or 0,
                shots_total=shots.get("total") or 0,
                shots_on=shots.get("on") or 0,
                passes_total=passes.get("total") or 0,
                passes_key=passes.get("key") or 0,
                pass_accuracy=pass_acc,
                tackles_total=tackles.get("total") or 0,
                duels_total=duels.get("total") or 0,
                duels_won=duels.get("won") or 0,
                dribbles_attempts=dribbles.get("attempts") or 0,
                dribbles_success=dribbles.get("success") or 0,
                yellow_cards=cards.get("yellow") or 0,
                red_cards=cards.get("red") or 0,
                fouls_drawn=fouls.get("drawn") or 0,
                fouls_committed=fouls.get("committed") or 0,
                pens_scored=penalty.get("scored") or 0,
                pens_missed=penalty.get("missed") or 0,
                pens_saved=penalty.get("saved") or 0,
                fetched_at=datetime.utcnow(),
            )

            if existing:
                s.execute(
                    text("""
                        UPDATE player_season_stats SET
                            appearances=:appearances, lineups=:lineups, minutes=:minutes,
                            rating=:rating, goals=:goals, assists=:assists,
                            goals_conceded=:goals_conceded, saves=:saves,
                            shots_total=:shots_total, shots_on=:shots_on,
                            passes_total=:passes_total, passes_key=:passes_key,
                            pass_accuracy=:pass_accuracy,
                            tackles_total=:tackles_total, duels_total=:duels_total,
                            duels_won=:duels_won, dribbles_attempts=:dribbles_attempts,
                            dribbles_success=:dribbles_success,
                            yellow_cards=:yellow_cards, red_cards=:red_cards,
                            fouls_drawn=:fouls_drawn, fouls_committed=:fouls_committed,
                            pens_scored=:pens_scored, pens_missed=:pens_missed,
                            pens_saved=:pens_saved, fetched_at=:fetched_at
                        WHERE player_id=:player_id AND team_id=:team_id
                          AND season=:season AND league_id=:league_id
                    """),
                    {
                        "player_id": player_id, "team_id": team_id,
                        "season": season, "league_id": league_id,
                        "appearances": row.appearances, "lineups": row.lineups,
                        "minutes": row.minutes, "rating": row.rating,
                        "goals": row.goals, "assists": row.assists,
                        "goals_conceded": row.goals_conceded, "saves": row.saves,
                        "shots_total": row.shots_total, "shots_on": row.shots_on,
                        "passes_total": row.passes_total, "passes_key": row.passes_key,
                        "pass_accuracy": row.pass_accuracy,
                        "tackles_total": row.tackles_total,
                        "duels_total": row.duels_total, "duels_won": row.duels_won,
                        "dribbles_attempts": row.dribbles_attempts,
                        "dribbles_success": row.dribbles_success,
                        "yellow_cards": row.yellow_cards, "red_cards": row.red_cards,
                        "fouls_drawn": row.fouls_drawn,
                        "fouls_committed": row.fouls_committed,
                        "pens_scored": row.pens_scored, "pens_missed": row.pens_missed,
                        "pens_saved": row.pens_saved,
                        "fetched_at": row.fetched_at,
                    },
                )
            else:
                s.add(row)
                count += 1

        s.commit()
    return count


def run_backfill(
    seasons: list[int] | None = None,
    stop_at: int = DEFAULT_STOP_AT,
    call_limit: int | None = None,
) -> int:
    """
    Fetch player season stats for all teams in the given seasons.

    Returns total player stat rows inserted.
    """
    from src.ingestion.client import APIFootballClient
    client = APIFootballClient()

    if seasons is None:
        seasons = TARGET_SEASONS

    done = _done_pairs()
    total_inserted = 0
    calls_made = 0

    for season in seasons:
        if _remaining() <= stop_at:
            logger.info(f"Quota threshold reached before season {season} — stopping")
            break

        teams = _teams_for_season(season)
        missing = [t for t in teams if (t, season) not in done]

        if not missing:
            logger.info(f"Season {season}: all {len(teams)} teams already fetched")
            continue

        logger.info(f"Season {season}: {len(missing)}/{len(teams)} teams to fetch")

        for team_id in missing:
            if _remaining() <= stop_at:
                logger.info("Quota threshold reached mid-season — stopping")
                return total_inserted
            if call_limit is not None and calls_made >= call_limit:
                logger.info(f"Call limit {call_limit} reached — stopping")
                return total_inserted

            try:
                raw = client.get_players(team_id=team_id, season=season)
                calls_made += 1
            except Exception as exc:
                logger.warning(f"get_players({team_id}, {season}) failed: {exc}")
                continue

            inserted = 0
            if raw:
                for entry in raw:
                    player_info = entry.get("player") or {}
                    player_id = player_info.get("id")
                    stats_list = entry.get("statistics") or []
                    if player_id and stats_list:
                        inserted += _store_player_stats(
                            player_id, team_id, season, player_info, stats_list
                        )

            with get_session() as s:
                s.execute(
                    text("""
                        INSERT OR REPLACE INTO player_fetch_log (team_id, season, fetched_at, row_count)
                        VALUES (:tid, :sea, :ts, :rc)
                    """),
                    {
                        "tid": team_id, "sea": season,
                        "ts": datetime.utcnow(), "rc": inserted,
                    },
                )
                s.commit()

            total_inserted += inserted

        logger.info(
            f"Season {season} done. Inserted so far: {total_inserted}. "
            f"Remaining quota: {_remaining()}"
        )

    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Backfill per-season player stats")
    parser.add_argument("--seasons", nargs="+", type=int, default=None)
    parser.add_argument("--stop-at", type=int, default=DEFAULT_STOP_AT,
                        help="Stop when API calls remaining drops to this level")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum API calls to make this run")
    args = parser.parse_args()

    init_db()
    remaining_start = _remaining()
    logger.info(f"Player stats backfill starting. API calls remaining: {remaining_start}")

    inserted = run_backfill(
        seasons=args.seasons,
        stop_at=args.stop_at,
        call_limit=args.limit,
    )

    logger.info(
        f"Done. Player stat rows inserted: {inserted}. "
        f"API calls remaining: {_remaining()}"
    )


if __name__ == "__main__":
    main()
