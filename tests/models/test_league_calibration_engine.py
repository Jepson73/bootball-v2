"""Tests for src/calibration/league_calibration_engine.py's h2h coherence helper.

Phase 33 Task 3: LeagueCalibrationEngine.apply() only calibrates the scalar
predicted-side probability. renormalize_h2h_vector() keeps the displayed
Home/Draw/Away split consistent with a calibrated predicted-side number
whenever calibrated h2h serving is turned on.
"""
import pytest

from src.calibration.league_calibration_engine import LeagueCalibrationEngine


def test_renormalize_pins_predicted_side():
    h, d, a = LeagueCalibrationEngine.renormalize_h2h_vector(0.5, 0.3, 0.2, "home", 0.65)
    assert h == pytest.approx(0.65)
    assert h + d + a == pytest.approx(1.0)


def test_renormalize_preserves_ratio_of_other_two():
    h, d, a = LeagueCalibrationEngine.renormalize_h2h_vector(0.5, 0.3, 0.2, "home", 0.65)
    assert d / a == pytest.approx(0.3 / 0.2)


def test_renormalize_downward_calibration():
    """Overconfidence correction (calibrated < raw) is the common direction."""
    h, d, a = LeagueCalibrationEngine.renormalize_h2h_vector(0.7, 0.2, 0.1, "home", 0.55)
    assert h == pytest.approx(0.55)
    assert h + d + a == pytest.approx(1.0)
    assert d > 0.2 and a > 0.1  # both grew to absorb the released mass


def test_renormalize_draw_predicted():
    h, d, a = LeagueCalibrationEngine.renormalize_h2h_vector(0.3, 0.4, 0.3, "draw", 0.5)
    assert d == pytest.approx(0.5)
    assert h + d + a == pytest.approx(1.0)
    assert h == pytest.approx(a)  # were equal raw, ratio preserved


def test_renormalize_degenerate_raw_predicted_side_near_certain():
    """No ratio to preserve among near-zero others -- split remainder evenly."""
    h, d, a = LeagueCalibrationEngine.renormalize_h2h_vector(0.999999, 0.0000005, 0.0000005, "home", 0.7)
    assert h == pytest.approx(0.7)
    assert h + d + a == pytest.approx(1.0, abs=1e-6)
    assert d == pytest.approx(a, abs=1e-9)


def test_renormalize_invalid_side_raises():
    with pytest.raises(ValueError):
        LeagueCalibrationEngine.renormalize_h2h_vector(0.5, 0.3, 0.2, "bogus", 0.6)
