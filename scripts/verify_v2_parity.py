#!/usr/bin/env python3
"""
Phase 31 Part C — parity verification, step 1 (dry-run comparison).

Runs the new src.prediction.prediction_cycle.generate_predictions(save=False) against
today's NS fixtures and compares it, fixture-by-fixture and market-by-market, to the
most recent PredictionRecord row AgentCoordinator's own live cycle already wrote for
those same fixture/market pairs. This never writes anything — pure read + in-memory
generate — so it's safe to run against the live DB at any time.

Usage: python3 scripts/verify_v2_parity.py
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from sqlalchemy import select
from src.storage.db import get_session
from src.storage.models import PredictionRecord
from src.prediction.prediction_cycle import generate_predictions

COMPARE_FIELDS = ["outcome", "our_prob", "calibrated_prob", "market_prob", "blended_prob", "ev", "odds"]
FIELD_TO_COLUMN = {
    "outcome": "predicted_outcome",
    "our_prob": "our_prob",
    "calibrated_prob": "calibrated_prob",
    "market_prob": "market_prob",
    "blended_prob": "blended_prob",
    "ev": "ev",
    "odds": "odds_decimal",
}


def main():
    result = generate_predictions(save=False)
    predictions = result["predictions"]
    print(f"Dry-run generated {len(predictions)} predictions across {result['fixtures']} fixtures.\n")

    if not predictions:
        print("Nothing to compare.")
        return

    matches = 0
    mismatches = 0
    no_stored_row = 0

    with get_session() as s:
        for pred in predictions:
            fixture_id = pred["fixture_id"]
            market = pred["market"]

            stored = s.execute(
                select(PredictionRecord)
                .where(PredictionRecord.fixture_id == fixture_id)
                .where(PredictionRecord.market == market)
                .order_by(PredictionRecord.id.desc())
            ).scalars().first()

            if not stored:
                no_stored_row += 1
                continue

            diffs = []
            for field in COMPARE_FIELDS:
                new_val = pred.get(field)
                old_val = getattr(stored, FIELD_TO_COLUMN[field])
                if isinstance(new_val, float) and isinstance(old_val, float):
                    if abs(new_val - old_val) > 1e-6:
                        diffs.append(f"{field}: stored={old_val:.6f} new={new_val:.6f}")
                elif new_val != old_val:
                    diffs.append(f"{field}: stored={old_val!r} new={new_val!r}")

            if diffs:
                mismatches += 1
                print(f"MISMATCH fixture={fixture_id} market={market}: " + "; ".join(diffs))
            else:
                matches += 1

    print(
        f"\nSummary: {matches} matched, {mismatches} mismatched, "
        f"{no_stored_row} had no existing stored row (new fixture/market, not a mismatch) "
        f"out of {len(predictions)} generated."
    )


if __name__ == "__main__":
    main()
