-- Migration 025: versioning tuple for prediction provenance.
--
-- Context: Phase 2 investigation (2026-06) found that a prediction
-- made during development can't be traced to the exact blend formula,
-- feature set, or model that produced it (Phase 1d, Task M: file edited
-- after process was started, calibrated_prob=None for 9 June-7 bets).
--
-- Adds blend_version so every prediction_records row carries the full
-- 4-tuple (feature_pipeline_version, model_version_id,
-- calibration_version_id, blend_version) identifying the exact code that
-- produced it. feature_pipeline_version already existed (default v1.0.0)
-- but was never written explicitly from code — it will now be set per
-- prediction rather than falling through to the column default.
--
-- blend_version values:
--   'v1.0'  — blend_with_market(MODEL_WEIGHT=0.35, Shin de-vigging)
--             first deployed 2026-06-07 via src/calibration/market_blend.py
--   NULL    — no blend applied (preliminary prediction or pre-blend-era)

ALTER TABLE prediction_records ADD COLUMN blend_version VARCHAR(20);
