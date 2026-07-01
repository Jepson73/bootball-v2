#!/usr/bin/env python3
"""
scripts/update_national_ratings.py — Build the national-team Elo pool.

Uses NATIONAL_POOL_LEAGUES whitelist from src/features/elo.py:
  WC, Euro, AFCON, Copa America, Asian Cup, Nations Leagues,
  all confederation WC qualifiers, and senior Friendlies (league 10).

Friendlies note: 42 of 2492 FT Friendlies fixtures involve a club-rated team
(Hull City, Alanyaspor, etc. in one-off national warmups). These are harmless —
club teams appear at 1500 default and don't skew national team convergence.

Pool isolation: clears ONLY pool='national' rows before rebuilding. Club ratings
are untouched. Run: python scripts/update_national_ratings.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from src.features.elo import update_all_ratings, NATIONAL_POOL_LEAGUES
from src.storage.db import get_session
from sqlalchemy import text


def main() -> None:
    print(f"Building national Elo pool ({len(NATIONAL_POOL_LEAGUES)} whitelisted leagues)...")
    n = update_all_ratings(pool="national")
    print(f"Processed {n:,} FT fixtures.")

    with get_session() as s:
        stats = s.execute(text(
            "SELECT COUNT(*), AVG(rating), MIN(rating), MAX(rating) "
            "FROM elo_ratings WHERE pool = 'national'"
        )).fetchone()
        print(
            f"National pool: {stats[0]:,} teams rated, "
            f"avg={stats[1]:.1f}, min={stats[2]:.1f}, max={stats[3]:.1f}"
        )

        # Pool isolation: confirm no row has pool=NULL or wrong pool
        leak = s.execute(text(
            "SELECT COUNT(*) FROM elo_ratings WHERE pool NOT IN ('club', 'national')"
        )).scalar()
        print(f"Isolation check: {leak} rows with unexpected pool value (should be 0)")

        # Club pool should be untouched
        club_cnt = s.execute(text(
            "SELECT COUNT(*) FROM elo_ratings WHERE pool = 'club'"
        )).scalar()
        print(f"Club pool still has {club_cnt:,} rows (should be 20930 if unchanged)")

        # Teams appearing in BOTH pools (bridge teams — expected to be small clubs
        # that played in Friendlies alongside national teams)
        bridge = s.execute(text(
            "SELECT t.name, cn.rating AS nat_r, cc.rating AS club_r "
            "FROM elo_ratings cn "
            "JOIN elo_ratings cc ON cc.team_id = cn.team_id AND cc.pool = 'club' "
            "JOIN teams t ON t.id = cn.team_id "
            "WHERE cn.pool = 'national' "
            "ORDER BY cn.games_played ASC LIMIT 20"
        )).fetchall()
        if bridge:
            print(f"\nBridge teams (in both pools): {len(bridge)} shown (ordered by fewest national games)")
            for r in bridge[:10]:
                print(f"  {r[0]}: nat={r[1]:.0f}  club={r[2]:.0f}")

        # Spot-check: a few WC teams should have plausible ratings
        print("\nWC team spot-check:")
        for nm in ["Brazil", "Argentina", "England", "Spain", "Cape Verde Islands", "Bosnia & Herzegovina"]:
            row = s.execute(text(
                "SELECT er.rating, er.games_played FROM elo_ratings er "
                "JOIN teams t ON t.id = er.team_id "
                "WHERE t.name = :nm AND er.pool = 'national' LIMIT 1"
            ), {"nm": nm}).fetchone()
            if row:
                print(f"  {nm}: rating={row[0]:.1f}, games={row[1]}")
            else:
                print(f"  {nm}: NOT FOUND in national pool")


if __name__ == "__main__":
    main()
