"""
tests/test_betting.py
Run: pytest tests/test_betting.py -v
"""
import pytest
from src.betting.ev import expected_value, implied_probability
from src.betting.kelly import kelly_fraction, fractional_kelly, stake
from src.betting.shin import shin_probabilities, overround
from src.evaluation.calibration import brier_score, log_loss


# ── EV tests ──────────────────────────────────────────────────────────────────

def test_ev_positive_edge():
    # Our prob 0.55, odd 2.10 → EV = 0.55*2.10 - 1 = 0.155
    ev = expected_value(0.55, 2.10)
    assert abs(ev - 0.155) < 0.001

def test_ev_no_edge():
    # Our prob matches implied: 1/2.0 = 0.50
    ev = expected_value(0.50, 2.00)
    assert ev == 0.0

def test_ev_negative():
    ev = expected_value(0.40, 2.00)
    assert ev < 0.0


# ── Kelly tests ───────────────────────────────────────────────────────────────

def test_kelly_positive():
    # Classic example: p=0.6, even odds (2.0)
    # f* = (1*0.6 - 0.4) / 1 = 0.20
    f = kelly_fraction(0.6, 2.0)
    assert abs(f - 0.20) < 0.001

def test_kelly_no_edge_returns_zero():
    f = kelly_fraction(0.5, 2.0)
    assert f == 0.0

def test_kelly_negative_edge_returns_zero():
    f = kelly_fraction(0.3, 2.0)
    assert f == 0.0

def test_fractional_kelly():
    full = kelly_fraction(0.6, 2.0)
    quarter = fractional_kelly(0.6, 2.0, fraction=0.25)
    assert abs(quarter - full * 0.25) < 0.0001

def test_stake_caps_at_max():
    # Even with huge edge, stake should cap at max_stake_pct
    s = stake(bankroll=10000, our_prob=0.99, decimal_odd=10.0,
               fraction=1.0, max_stake_pct=0.05)
    assert s <= 500.01   # 5% of 10000


# ── Shin tests ────────────────────────────────────────────────────────────────

def test_shin_probs_sum_to_one():
    probs = shin_probabilities([2.10, 3.40, 3.80])
    assert abs(sum(probs) - 1.0) < 1e-6

def test_shin_removes_overround():
    raw_odds = [2.10, 3.40, 3.80]
    raw_margin = overround(raw_odds)
    assert raw_margin > 0.0   # bookmaker margin exists

    shin_probs = shin_probabilities(raw_odds)
    # Shin probs sum to 1; raw implied probs would sum to > 1
    raw_sum = sum(1/o for o in raw_odds)
    assert raw_sum > 1.0
    assert abs(sum(shin_probs) - 1.0) < 1e-6

def test_shin_fair_odds_unchanged():
    # Perfectly fair odds (no margin) should return same as 1/odd
    odds = [2.0, 4.0, 4.0]   # sum of implied = 0.5 + 0.25 + 0.25 = 1.0
    probs = shin_probabilities(odds)
    assert abs(probs[0] - 0.5) < 0.01
    assert abs(probs[1] - 0.25) < 0.01


# ── Calibration tests ─────────────────────────────────────────────────────────

def test_brier_perfect():
    preds = [{"H": 1.0, "D": 0.0, "A": 0.0}]
    actuals = ["H"]
    assert brier_score(preds, actuals) == 0.0

def test_brier_random():
    preds = [{"H": 1/3, "D": 1/3, "A": 1/3}] * 300
    actuals = ["H"] * 100 + ["D"] * 100 + ["A"] * 100
    score = brier_score(preds, actuals)
    # Random uniform over 3 outcomes ≈ 0.667
    assert 0.60 < score < 0.74

def test_log_loss_perfect():
    preds = [{"H": 1.0, "D": 0.0, "A": 0.0}]
    actuals = ["H"]
    loss = log_loss(preds, actuals)
    assert loss < 1e-5
