#!/usr/bin/env python3
"""
scripts/generate_wc_predictions.py — National Elo predictions for WC NS fixtures.

The 11 World Cup NS fixtures already have h2h prediction records from the ensemble
model, but those records show clustered non-differentiating outputs (the ensemble
has no standing-based signal for national teams). This script replaces them with
national Elo predictions (data_context='national_elo').

Prerequisites:
  - National Elo pool must be built: python scripts/update_national_ratings.py
  - WC fixtures must be NS status in the DB

Uses UPDATE (not INSERT) since the unique constraint on (fixture_id, market)
means the existing records block an INSERT. The old model_name and feature_pipeline
are overwritten; prediction_id is preserved.

Run: python scripts/generate_wc_predictions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from sqlalchemy import text

from src.features.elo import EloEngine
from src.storage.db import get_session


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = EloEngine()

    with get_session() as s:
        wc_fixtures = s.execute(text("""
            SELECT f.id, f.home_team_id, f.away_team_id,
                   ht.name AS home_name, at.name AS away_name
            FROM fixtures f
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE f.status = 'NS' AND f.league_id = 1
            ORDER BY f.date
        """)).fetchall()

        if not wc_fixtures:
            print("No NS World Cup fixtures found.")
            return

        updates: list[dict] = []
        missing_rating: list[str] = []

        for fix in wc_fixtures:
            fid, h_id, a_id, home_name, away_name = fix

            # Check both teams have national ratings (not just the 1500 default)
            h_games = s.execute(text(
                "SELECT games_played FROM elo_ratings WHERE team_id=:tid AND pool='national' LIMIT 1"
            ), {"tid": h_id}).scalar()
            a_games = s.execute(text(
                "SELECT games_played FROM elo_ratings WHERE team_id=:tid AND pool='national' LIMIT 1"
            ), {"tid": a_id}).scalar()

            if not h_games:
                missing_rating.append(f"  {home_name} (home) has no national Elo rating")
            if not a_games:
                missing_rating.append(f"  {away_name} (away) has no national Elo rating")

            ph, pd, pa = engine.predict(h_id, a_id, pool="national")
            probs = {"H": ph, "D": pd, "A": pa}
            predicted_outcome = max(probs, key=probs.__getitem__)
            our_prob = probs[predicted_outcome]

            updates.append({
                "fixture_id": fid,
                "ph": ph, "pd": pd, "pa": pa,
                "predicted_outcome": predicted_outcome,
                "raw_outcome": predicted_outcome,
                "our_prob": our_prob,
                "home_name": home_name,
                "away_name": away_name,
            })

        print(f"\n{'DRY RUN — ' if args.dry_run else ''}World Cup national Elo predictions:")
        print(f"  {'Home':25s}  {'Away':25s}  H%    D%    A%   Pred")
        print("  " + "-" * 75)
        for u in updates:
            pred_label = {"H": "Home", "D": "Draw", "A": "Away"}[u["predicted_outcome"]]
            print(
                f"  {u['home_name']:25s}  {u['away_name']:25s}"
                f"  {u['ph']:.0%}  {u['pd']:.0%}  {u['pa']:.0%}  {pred_label}"
            )

        if missing_rating:
            print("\nWARNING — teams defaulting to 1500 (no national rating history):")
            for m in missing_rating:
                print(m)

        if args.dry_run:
            print("\n[dry-run] No records written.")
            return

        # UPDATE existing h2h records for WC fixtures
        for u in updates:
            s.execute(text("""
                UPDATE prediction_records SET
                    model_name              = 'elo_hybrid',
                    feature_pipeline_version = 'v2.0.0',
                    predicted_outcome        = :predicted_outcome,
                    raw_outcome              = :raw_outcome,
                    our_prob                 = :our_prob,
                    prob_home                = :ph,
                    prob_draw                = :pd,
                    prob_away                = :pa,
                    data_context             = 'national_elo'
                WHERE fixture_id = :fixture_id AND market = 'h2h'
            """), u)

        print(f"\nUpdated {len(updates)} WC prediction records to national_elo.")

        # Youth NS fixtures — confirm abstained
        youth_ns = s.execute(text(
            "SELECT COUNT(*) FROM fixtures f "
            "JOIN prediction_records pr ON pr.fixture_id = f.id "
            "WHERE f.status = 'NS' AND f.league_id IN (493, 918)"
        )).scalar()
        print(f"Youth abstain check: {youth_ns} h2h records for U19 NS fixtures (expected 0)")


if __name__ == "__main__":
    main()
