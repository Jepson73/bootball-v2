-- Migration: Add prediction_id for immutable prediction tracking

-- Add prediction_id column
ALTER TABLE prediction_records ADD COLUMN prediction_id VARCHAR(36);

-- Create index for prediction_id lookups
CREATE INDEX IF NOT EXISTS idx_pred_record_prediction_id ON prediction_records(prediction_id);

-- Note: The unique constraint on (fixture_id, market) should remain for now
-- to ensure one active prediction per fixture+market at any time.
-- This migration enables tracking of individual prediction versions.
