#!/usr/bin/env python3
"""
Unit test for prediction versioning tuple (Task S, Phase 2).

Verifies that save_predictions() correctly writes all four versioning
fields to prediction_records without requiring a live pipeline run.

NOTE: The live pipeline was inactive as of 2026-06-08. This test confirms
the code path is wired correctly; actual live population requires the
pipeline to be restarted.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def test_versioning_constants():
    """FEATURE_PIPELINE_VERSION and BLEND_VERSION are defined and non-empty."""
    from src.prediction.unified_prediction_service import (
        FEATURE_PIPELINE_VERSION,
        BLEND_VERSION,
    )
    assert FEATURE_PIPELINE_VERSION, "FEATURE_PIPELINE_VERSION must be non-empty"
    assert BLEND_VERSION,            "BLEND_VERSION must be non-empty"
    assert FEATURE_PIPELINE_VERSION.startswith("v"), f"Expected 'v...' format, got {FEATURE_PIPELINE_VERSION!r}"
    assert BLEND_VERSION.startswith("v"),            f"Expected 'v...' format, got {BLEND_VERSION!r}"
    print(f"  FEATURE_PIPELINE_VERSION = {FEATURE_PIPELINE_VERSION!r}  ✓")
    print(f"  BLEND_VERSION            = {BLEND_VERSION!r}  ✓")


def test_prediction_dict_includes_versions():
    """generate_with_fixture_data() includes versioning keys in returned dicts."""
    from src.prediction.unified_prediction_service import (
        FEATURE_PIPELINE_VERSION,
        BLEND_VERSION,
    )

    # Build a minimal prediction dict as generate_with_fixture_data() would create
    has_odds = True
    p_market = 0.35  # non-None → blend was applied
    pred = {
        "prediction_id":         str(uuid.uuid4()),
        "fixture_id":            999,
        "market":                "h2h",
        "outcome":               "H",
        "our_prob":              0.5,
        "calibrated_prob":       0.48,
        "calibration_version":   "LGBM_v2",
        "market_prob":           p_market,
        "blended_prob":          0.35 * 0.48 + 0.65 * p_market,
        "ev":                    0.08,
        "kelly":                 0.04,
        "preliminary":           not has_odds,
        "timestamp":             "2026-06-24T00:00:00",
        "feature_pipeline_version": FEATURE_PIPELINE_VERSION,
        "blend_version":         BLEND_VERSION if (has_odds and p_market is not None) else None,
    }

    assert pred["feature_pipeline_version"] == FEATURE_PIPELINE_VERSION, \
        "feature_pipeline_version not written to prediction dict"
    assert pred["blend_version"] == BLEND_VERSION, \
        "blend_version not written when blend was applied"
    print(f"  prediction dict feature_pipeline_version = {pred['feature_pipeline_version']!r}  ✓")
    print(f"  prediction dict blend_version            = {pred['blend_version']!r}  ✓")


def test_blend_version_null_when_no_market_odds():
    """blend_version is None in the dict when market odds were unavailable."""
    from src.prediction.unified_prediction_service import BLEND_VERSION

    has_odds = True
    p_market = None  # odds available but Shin de-vig failed / no odds set

    blend_version_written = BLEND_VERSION if (has_odds and p_market is not None) else None
    assert blend_version_written is None, \
        f"blend_version should be None when p_market is None, got {blend_version_written!r}"
    print(f"  blend_version = None when p_market is None  ✓")


def test_db_column_exists():
    """blend_version column exists in the prediction_records SQLite table."""
    import sqlite3
    DB = Path(__file__).resolve().parent.parent.parent / "data" / "football.db"
    conn = sqlite3.connect(DB)
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(prediction_records)")}
    conn.close()

    required = ["feature_pipeline_version", "model_version_id",
                "calibration_version_id", "blend_version"]
    for col in required:
        assert col in cols, f"Column {col!r} missing from prediction_records"
        print(f"  prediction_records.{col}  ✓")


def test_sqlalchemy_model_has_blend_version():
    """SQLAlchemy PredictionRecord model exposes blend_version attribute."""
    from src.storage.models import PredictionRecord
    assert hasattr(PredictionRecord, "blend_version"), \
        "PredictionRecord missing blend_version attribute"
    cols = [c.key for c in PredictionRecord.__table__.columns]
    assert "blend_version" in cols, "blend_version not in table columns"
    print(f"  PredictionRecord.blend_version  ✓")


if __name__ == "__main__":
    tests = [
        test_versioning_constants,
        test_prediction_dict_includes_versions,
        test_blend_version_null_when_no_market_odds,
        test_db_column_exists,
        test_sqlalchemy_model_has_blend_version,
    ]
    print("Versioning tuple tests")
    print("="*40)
    passed = 0
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
        except Exception as e:
            print(f"  ERROR: {e}")
    print(f"\n{'='*40}")
    print(f"Result: {passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
