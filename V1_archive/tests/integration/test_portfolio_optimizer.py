"""
tests/integration/test_portfolio_optimizer.py

Tests for MarkowitzOptimizer.optimize() — pure logic, no DB required.

Run: pytest tests/integration/test_portfolio_optimizer.py -v
"""
import sys
sys.path.insert(0, ".")

import pytest
import numpy as np

from src.betting.portfolio.markowitz_optimizer import (
    MarkowitzOptimizer,
    MarkowitzConfig,
    BetCandidate,
    OptimizationResult,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _candidate(fixture_id=1, market="h2h", outcome="H",
               odds=2.10, prob=0.55, ev=0.155, kelly=0.10,
               bet_id=None) -> dict:
    return {
        "id": bet_id or f"{fixture_id}_{market}_{outcome}",
        "fixture_id": fixture_id,
        "market": market,
        "outcome": outcome,
        "odds": odds,
        "prob": prob,
        "ev": ev,
        "kelly_fraction": kelly,
        "correlation_key": f"fixture_{fixture_id}",
    }


def _optimizer(max_per_bet=0.05, max_exposure=0.25, risk_aversion=1.0) -> MarkowitzOptimizer:
    cfg = MarkowitzConfig(
        risk_aversion=risk_aversion,
        max_total_exposure=max_exposure,
        max_bet_pct=max_per_bet,
        use_correlation_engine=False,
    )
    return MarkowitzOptimizer(config=cfg)


# ── Empty / degenerate inputs ──────────────────────────────────────────────────

class TestEmptyInputs:
    def test_empty_candidates_returns_empty_result(self):
        opt = _optimizer()
        result = opt.optimize([], bankroll=10_000)
        assert isinstance(result, OptimizationResult)
        assert result.bets == []

    def test_zero_bankroll_returns_empty_result(self):
        opt = _optimizer()
        cands = [_candidate()]
        result = opt.optimize(cands, bankroll=0)
        assert result.bets == []

    def test_negative_ev_only_no_allocation(self):
        opt = _optimizer()
        cands = [
            _candidate(prob=0.30, odds=2.0, ev=-0.40, kelly=0.0),
            _candidate(fixture_id=2, prob=0.35, odds=2.0, ev=-0.30, kelly=0.0),
        ]
        result = opt.optimize(cands, bankroll=10_000)
        total_stake = sum(b.stake for b in result.bets)
        assert total_stake == pytest.approx(0.0, abs=0.01)


# ── Single bet ─────────────────────────────────────────────────────────────────

class TestSingleBet:
    def test_positive_ev_gets_allocation(self):
        opt = _optimizer(max_per_bet=0.10)
        cands = [_candidate(prob=0.60, odds=2.0, ev=0.20, kelly=0.20)]
        result = opt.optimize(cands, bankroll=10_000)
        assert len(result.bets) == 1
        assert result.bets[0].stake > 0

    def test_stake_respects_max_bet_pct(self):
        bankroll = 10_000
        max_pct = 0.05
        opt = _optimizer(max_per_bet=max_pct)
        cands = [_candidate(prob=0.99, odds=10.0, ev=8.9, kelly=1.0)]
        result = opt.optimize(cands, bankroll=bankroll)
        if result.bets:
            assert result.bets[0].stake <= bankroll * max_pct + 0.01

    def test_stake_is_non_negative(self):
        opt = _optimizer()
        cands = [_candidate()]
        result = opt.optimize(cands, bankroll=5_000)
        for bet in result.bets:
            assert bet.stake >= 0.0


# ── Multiple bets ──────────────────────────────────────────────────────────────

class TestMultipleBets:
    def test_total_exposure_respected(self):
        bankroll = 10_000
        max_exp = 0.20
        opt = _optimizer(max_exposure=max_exp)
        cands = [
            _candidate(fixture_id=i, prob=0.60, odds=2.0, ev=0.20, kelly=0.20)
            for i in range(10)
        ]
        result = opt.optimize(cands, bankroll=bankroll)
        total = sum(b.stake for b in result.bets)
        assert total <= bankroll * max_exp + 0.50  # small float tolerance

    def test_higher_ev_gets_more_allocation(self):
        opt = _optimizer(max_per_bet=0.10)
        cands = [
            _candidate(fixture_id=1, prob=0.70, odds=2.0, ev=0.40, kelly=0.40,
                       bet_id="high"),
            _candidate(fixture_id=2, prob=0.52, odds=2.0, ev=0.04, kelly=0.04,
                       bet_id="low"),
        ]
        result = opt.optimize(cands, bankroll=10_000)
        by_id = {b.bet_id: b for b in result.bets}
        if "high" in by_id and "low" in by_id:
            assert by_id["high"].stake >= by_id["low"].stake

    def test_result_bets_have_required_fields(self):
        opt = _optimizer()
        cands = [_candidate(fixture_id=i) for i in range(3)]
        result = opt.optimize(cands, bankroll=10_000)
        for bet in result.bets:
            assert bet.bet_id
            assert bet.fixture_id is not None
            assert isinstance(bet.stake, (int, float))
            assert isinstance(bet.weight, (int, float))

    def test_dict_and_dataclass_candidates_produce_same_output(self):
        opt = _optimizer()
        bankroll = 10_000
        as_dict = [_candidate()]
        as_obj = [BetCandidate(
            id="1_h2h_H", fixture_id=1, market="h2h", outcome="H",
            odds=2.10, prob=0.55, ev=0.155, kelly_fraction=0.10,
        )]
        r1 = opt.optimize(as_dict, bankroll=bankroll)
        r2 = opt.optimize(as_obj, bankroll=bankroll)
        assert len(r1.bets) == len(r2.bets)
        if r1.bets and r2.bets:
            assert abs(r1.bets[0].stake - r2.bets[0].stake) < 0.01


# ── Risk aversion ──────────────────────────────────────────────────────────────

class TestRiskAversion:
    def test_higher_risk_aversion_produces_smaller_stakes(self):
        cands = [
            _candidate(fixture_id=i, prob=0.60, odds=2.0, ev=0.20, kelly=0.20)
            for i in range(5)
        ]
        bankroll = 10_000

        low_risk = _optimizer(risk_aversion=0.5)
        high_risk = _optimizer(risk_aversion=5.0)

        r_low = low_risk.optimize(cands, bankroll=bankroll)
        r_high = high_risk.optimize(cands, bankroll=bankroll)

        total_low = sum(b.stake for b in r_low.bets)
        total_high = sum(b.stake for b in r_high.bets)
        assert total_low >= total_high
