"""
scripts/analysis/phase37_build_training_set.py

Phase 37 Part B.1/B.2 — availability-tier feature computation + training set
assembly for the 12 covered leagues (config/covered_leagues.py).

FEATURE DEFINITIONS (documented per Phase 37's requirement):

For a fixture in season S, league L, each team's absence features are
computed from PRIOR-SEASON (S-1) player_season_stats for that team+league --
not the in-season aggregate. This is a deliberate leakage-safety choice:
player_season_stats stores cumulative SEASON totals fetched long after the
season played out, so a same-season "share of this player's season goals"
figure would partly reflect matches played AFTER the fixture being scored --
real leakage. Prior-season totals are fully determined before the season
in question even starts, so they carry no such risk. Cost: a player new to
the team/league that season (promotion, transfer) has no prior-season row
and contributes 0 to the absence-weighted features below -- this is
CONSERVATIVE (understates true absence impact for new-signing injuries) but
never leaks. This shrinks the training set to fixtures where both teams
have season S-1 rows in the same league -- reported explicitly below, not
silently patched over.

Injuries are read from the injuries table, keyed by fixture_id. Each row
there is a "Missing Fixture" report (players AND suspensions both come
through this same API field) tied to that specific match -- a genuine
pre-match fact regardless of when we fetched it (see migration 032).

Features per team (home/away), all in [0, 1] or small integers:
  - absent_goal_share:    sum(prior-season goals of absent players) / team's total prior-season goals
                          NULL if team's total prior-season goals == 0 (division guard)
  - absent_minutes_share: sum(prior-season minutes of absent players) / team's total prior-season minutes
                          NULL if team's total prior-season minutes == 0
  - keeper_absent:        1 if any absent player's prior-season position == "Goalkeeper", else 0
  - n_regular_absences:   count of absent players whose prior-season minutes
                          share of team total minutes >= REGULAR_MINUTES_SHARE_THRESHOLD (0.4)

No new-coach flag: we did not collect historical coach-change data (lineups
backfill is deferred to Part C), so this optional feature is not built.

Model features used downstream (Part B.3): the DIFFERENCE (home - away) of
each of the four features above, since goal-market outcomes (btts/ou25) are
about the combined/relative goal-scoring capacity of both sides.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

from config.covered_leagues import COVERED_LEAGUE_IDS
from src.storage.db import get_session

REGULAR_MINUTES_SHARE_THRESHOLD = 0.4
OUT_CSV = Path(__file__).parent / "phase37_training_set.csv"


def _prior_season_team_stats(session, team_id: int, league_id: int, season: int) -> list[dict]:
    rows = session.execute(text("""
        SELECT player_id, position, goals, minutes
        FROM player_season_stats
        WHERE team_id=:tid AND league_id=:lid AND season=:sea
    """), {"tid": team_id, "lid": league_id, "sea": season}).fetchall()
    return [{"player_id": r[0], "position": r[1], "goals": r[2] or 0, "minutes": r[3] or 0} for r in rows]


def _team_features(session, team_id: int, league_id: int, season: int, fixture_id: int) -> dict | None:
    prior = _prior_season_team_stats(session, team_id, league_id, season - 1)
    if not prior:
        return None  # no prior-season data for this team -- fixture excluded, not silently zero-filled

    total_goals = sum(p["goals"] for p in prior)
    total_minutes = sum(p["minutes"] for p in prior)
    by_player = {p["player_id"]: p for p in prior}

    absent_rows = session.execute(text("""
        SELECT DISTINCT player_id FROM injuries WHERE fixture_id=:fid AND team_id=:tid
    """), {"fid": fixture_id, "tid": team_id}).fetchall()
    absent_ids = [r[0] for r in absent_rows if r[0] is not None]

    absent_goals = 0
    absent_minutes = 0
    keeper_absent = 0
    n_regular = 0
    n_unmatched = 0
    for pid in absent_ids:
        p = by_player.get(pid)
        if not p:
            n_unmatched += 1
            continue
        absent_goals += p["goals"]
        absent_minutes += p["minutes"]
        if p["position"] == "Goalkeeper":
            keeper_absent = 1
        if total_minutes > 0 and (p["minutes"] / total_minutes) >= REGULAR_MINUTES_SHARE_THRESHOLD:
            n_regular += 1

    return {
        "absent_goal_share": (absent_goals / total_goals) if total_goals > 0 else None,
        "absent_minutes_share": (absent_minutes / total_minutes) if total_minutes > 0 else None,
        "keeper_absent": keeper_absent,
        "n_regular_absences": n_regular,
        "n_absent_total": len(absent_ids),
        "n_absent_unmatched": n_unmatched,
    }


def main() -> None:
    ids = ",".join(map(str, COVERED_LEAGUE_IDS))
    with get_session() as s:
        fixtures = s.execute(text(f"""
            SELECT f.id, f.league_id, f.season, f.home_team_id, f.away_team_id, f.date
            FROM fixtures f
            WHERE f.status='FT' AND f.league_id IN ({ids})
              AND f.id IN (SELECT DISTINCT fixture_id FROM prediction_records WHERE settled=1)
        """)).fetchall()

        print(f"Candidate fixtures (covered leagues, settled): {len(fixtures)}")

        fixture_features: dict[int, dict] = {}
        excluded_no_prior = 0
        for fid, league_id, season, home_id, away_id, date in fixtures:
            home_feat = _team_features(s, home_id, league_id, season, fid)
            away_feat = _team_features(s, away_id, league_id, season, fid)
            if home_feat is None or away_feat is None:
                excluded_no_prior += 1
                continue
            fixture_features[fid] = {
                "league_id": league_id, "season": season, "date": date,
                "home": home_feat, "away": away_feat,
            }

        print(f"Fixtures excluded (missing prior-season data for a team): {excluded_no_prior}")
        print(f"Fixtures with complete availability features: {len(fixture_features)}")

        # Join to settled prediction_records for all 4 markets
        pr_rows = s.execute(text(f"""
            SELECT pr.fixture_id, pr.market, pr.our_prob, pr.won,
                   l.name || ' (' || l.country || ')', l.id
            FROM prediction_records pr
            JOIN fixtures f ON f.id = pr.fixture_id
            JOIN leagues l ON l.id = f.league_id
            WHERE pr.settled=1 AND pr.our_prob IS NOT NULL AND pr.won IS NOT NULL
              AND f.league_id IN ({ids})
        """)).fetchall()

    _BINARY_MARKETS = {"btts", "ou25", "ou15"}
    excluded_corrupted = 0
    out_rows = []
    for fixture_id, market, our_prob, won, league_name, league_id in pr_rows:
        feat = fixture_features.get(fixture_id)
        if feat is None:
            continue
        if market in _BINARY_MARKETS and our_prob < 0.5:
            # Same corrupted-row exclusion Track A applies (v2/db_v2.py) --
            # our_prob < 0.5 for a binary market's argmax-selected outcome is
            # mathematically impossible for a healthy write path.
            excluded_corrupted += 1
            continue
        h, a = feat["home"], feat["away"]

        def _diff(key):
            hv, av = h[key], a[key]
            if hv is None or av is None:
                return ""
            return hv - av

        out_rows.append({
            "fixture_id": fixture_id,
            "market": market,
            "our_prob": our_prob,
            "won": int(won),
            "date": feat["date"],
            "league_id": league_id,
            "league_name": league_name,
            "diff_absent_goal_share": _diff("absent_goal_share"),
            "diff_absent_minutes_share": _diff("absent_minutes_share"),
            "diff_keeper_absent": h["keeper_absent"] - a["keeper_absent"],
            "diff_n_regular_absences": h["n_regular_absences"] - a["n_regular_absences"],
            "home_n_absent_total": h["n_absent_total"],
            "away_n_absent_total": a["n_absent_total"],
        })

    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else [])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Excluded (corrupted our_prob < 0.5 on a binary market): {excluded_corrupted}")
    print(f"\nTraining rows written: {len(out_rows)} -> {OUT_CSV}")
    from collections import Counter
    by_market = Counter(r["market"] for r in out_rows)
    print("By market:", dict(by_market))
    by_league = Counter(r["league_name"] for r in out_rows)
    print("By league:", dict(sorted(by_league.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
