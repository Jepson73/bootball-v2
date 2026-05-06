-- Migration 018: add model_number, calibration_number, version_label to model_versions
-- model_number = vXX (increments on retrain, constant across recalibrations)
-- calibration_number = cYY (resets to 0 on retrain, increments on recalibrate)
-- version_label = v{model_number:02d}_c{calibration_number:02d}

ALTER TABLE model_versions ADD COLUMN model_number INTEGER;
ALTER TABLE model_versions ADD COLUMN calibration_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_versions ADD COLUMN version_label TEXT;

-- Back-fill: every historical row was a retrain, so model_number = version_number, cYY = 00
UPDATE model_versions
SET model_number       = version_number,
    calibration_number = 0,
    version_label      = printf('v%02d_c00', version_number);
