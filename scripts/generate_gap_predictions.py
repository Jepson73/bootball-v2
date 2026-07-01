#!/usr/bin/env python3
"""
scripts/generate_gap_predictions.py — Phase 16b gap-fixture Elo predictions.

Generates h2h PredictionRecords for upcoming NS fixtures that have no h2h
prediction (teams lacking a Standing row bypass the normal model gate).

Approved hybrid per category:
  - Friendlies (both rated)    → elo_both
  - Friendlies (one unrated)   → flat_prior  H=0.43 D=0.27 A=0.30
  - USL L2 (both rated)        → elo_both
  - USL L2 (one unrated)       → elo_partial (1500 default for missing team)
  - FA Cup (both rated)        → elo_both
  - FA Cup (one unrated)       → elo_partial
  - Youth (U19/U20)            → ABSTAIN — no record written
  - All other clubs (any mix)  → elo_both / elo_partial

Run: python scripts/generate_gap_predictions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime

from sqlalchemy import text

sys.path.insert(0, ".")

from src.features.elo import EloEngine
from src.storage.db import get_session

FLAT_PRIOR = (0.43, 0.27, 0.30)  # H / D / A

YOUTH_KEYWORDS = ("U19", "U20", "U17", "Youth", "Juvenile")

FRIENDLY_LEAGUE_NAMES = {"Friendlies Clubs", "Friendlies", "Club Friendly"}


def _is_youth(league_name: str) -> bool:
    return any(kw in league_name for kw in YOUTH_KEYWORDS)


def _has_rating(session, team_id: int) -> bool:
    cnt = session.execute(
        text("SELECT COUNT(*) FROM elo_ratings WHERE team_id=:tid AND pool='club'"),
        {"tid": team_id},
    ).scalar()
    return cnt > 0


def _decide_approach(league_name: str, home_rated: bool, away_rated: bool):
    """
    Returns (approach, data_context) or (None, None) for abstain.

    approach: 'elo' | 'flat' | None
    data_context: 'elo_both' | 'elo_partial' | 'flat_prior' | None
    """
    is_friendly = league_name in FRIENDLY_LEAGUE_NAMES

    if home_rated and away_rated:
        return "elo", "elo_both"

    if is_friendly:
        return "flat", "flat_prior"

    # One team unrated — use Elo with 1500 default
    return "elo", "elo_partial"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = EloEngine()

    with get_session() as session:
        fixtures = session.execute(
            text("""
                SELECT f.id, f.date, f.home_team_id, f.away_team_id,
                       l.name AS league_name, ht.name AS home_name, at.name AS away_name
                FROM fixtures f
                JOIN leagues l ON l.id = f.league_id
                JOIN teams ht ON ht.id = f.home_team_id
                JOIN teams at ON at.id = f.away_team_id
                WHERE f.status = 'NS'
                  AND NOT EXISTS (
                      SELECT 1 FROM prediction_records pr
                      WHERE pr.fixture_id = f.id AND pr.market = 'h2h'
                  )
                ORDER BY f.date
            """)
        ).fetchall()

        counts = {
            "total": len(fixtures),
            "abstained": 0,
            "elo_both": 0,
            "elo_partial": 0,
            "flat_prior": 0,
            "skipped_error": 0,
        }

        rows_to_insert: list[dict] = []

        for fix in fixtures:
            fid, date, h_id, a_id, league_name, home_name, away_name = fix

            if _is_youth(league_name):
                counts["abstained"] += 1
                continue

            home_rated = _has_rating(session, h_id)
            away_rated = _has_rating(session, a_id)

            approach, data_context = _decide_approach(league_name, home_rated, away_rated)

            if approach is None:
                counts["abstained"] += 1
                continue

            if approach == "flat":
                ph, pd, pa = FLAT_PRIOR
            else:
                ph, pd, pa = engine.predict(h_id, a_id, pool="club")

            # Map to predicted_outcome
            probs = {"H": ph, "D": pd, "A": pa}
            predicted_outcome = max(probs, key=probs.__getitem__)
            our_prob = probs[predicted_outcome]

            rows_to_insert.append(
                {
                    "prediction_id": str(uuid.uuid4()),
                    "fixture_id": fid,
                    "market": "h2h",
                    "model_name": "elo_hybrid",
                    "feature_pipeline_version": "v2.0.0",
                    "predicted_outcome": predicted_outcome,
                    "raw_outcome": predicted_outcome,
                    "our_prob": our_prob,
                    "prob_home": ph,
                    "prob_draw": pd,
                    "prob_away": pa,
                    "data_context": data_context,
                    "sweet_spot": False,
                    "settled": False,
                    "created_at": datetime.utcnow(),
                }
            )
            counts[data_context] += 1

        print(f"\nGap predictions summary ({counts['total']} gap fixtures):")
        print(f"  Abstained (youth):  {counts['abstained']}")
        print(f"  elo_both:           {counts['elo_both']}")
        print(f"  elo_partial:        {counts['elo_partial']}")
        print(f"  flat_prior:         {counts['flat_prior']}")
        print(f"  Errors skipped:     {counts['skipped_error']}")
        print(f"  Records to write:   {len(rows_to_insert)}")

        if args.dry_run:
            print("\n[dry-run] No records written.")
            return

        if not rows_to_insert:
            print("Nothing to write.")
            return

        # INSERT OR IGNORE to be idempotent (uq_pred_record on fixture_id+market)
        session.execute(
            text("""
                INSERT OR IGNORE INTO prediction_records
                    (prediction_id, fixture_id, market, model_name,
                     feature_pipeline_version, predicted_outcome, raw_outcome,
                     our_prob, prob_home, prob_draw, prob_away,
                     data_context, sweet_spot, settled, created_at)
                VALUES
                    (:prediction_id, :fixture_id, :market, :model_name,
                     :feature_pipeline_version, :predicted_outcome, :raw_outcome,
                     :our_prob, :prob_home, :prob_draw, :prob_away,
                     :data_context, :sweet_spot, :settled, :created_at)
            """),
            rows_to_insert,
        )

        print(f"\nWrote {len(rows_to_insert)} prediction records.")


if __name__ == "__main__":
    main()
