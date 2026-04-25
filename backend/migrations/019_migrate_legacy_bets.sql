-- Migration: Migrate legacy betting data into run-based architecture
-- Run this script to attach all legacy bets to a synthetic run

-- STEP 1: Create legacy run
-- Generate UUID: legacy-run-00000000-0000-0000-0000-000000000001
INSERT INTO experiment_runs (
    run_id,
    mode,
    start_timestamp,
    end_timestamp,
    config_hash,
    status,
    total_predictions,
    total_bets,
    feature_pipeline_version
)
SELECT 
    'legacy-run-00000000-0000-0000-0000-000000000001',
    'legacy',
    MIN(placed_at),
    MAX(placed_at),
    'LEGACY01',
    'completed',
    0,
    COUNT(*),
    'v1.0.0'
FROM placed_bets
WHERE run_id IS NULL;

-- STEP 2: Attach legacy bets to the new run
UPDATE placed_bets
SET run_id = 'legacy-run-00000000-0000-0000-0000-000000000001'
WHERE run_id IS NULL;

-- STEP 3: Validate
-- Should return 0
SELECT 'Orphan bets remaining:' AS check_name, COUNT(*) AS count
FROM placed_bets WHERE run_id IS NULL;

-- Should return 1
SELECT 'Legacy runs created:' AS check_name, COUNT(*) AS count
FROM experiment_runs WHERE run_id LIKE 'legacy-run-%';

-- Total bets should still be 21
SELECT 'Total bets in system:' AS check_name, COUNT(*) AS count FROM placed_bets;

-- Verify legacy run details
SELECT run_id, mode, status, start_timestamp, end_timestamp, total_bets
FROM experiment_runs WHERE run_id LIKE 'legacy-run-%';