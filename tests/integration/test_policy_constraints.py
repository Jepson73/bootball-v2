"""
tests/integration/test_policy_constraints.py

Tests for PolicyEngine and individual policy constraints.
Pure logic — no DB or external services required.

Run: pytest tests/integration/test_policy_constraints.py -v
"""
import sys
sys.path.insert(0, ".")

import pytest
from datetime import datetime

from src.portfolio.state.portfolio_state import PortfolioState
from src.governance.policy_engine import (
    PolicyEngine,
    PolicyDecisionType,
    MonteCarloResults,
    DrawdownConstraint,
    RuinProbabilityConstraint,
    VolatilityConstraint,
    ExposureConcentrationConstraint,
    Severity,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _state(drawdown=0.0, volatility=0.0, realized_pnl=0.0,
           exposure_by_market=None) -> PortfolioState:
    return PortfolioState(
        timestamp=datetime.utcnow().isoformat(),
        drawdown=drawdown,
        volatility=volatility,
        realized_pnl=realized_pnl,
        exposure_by_market=exposure_by_market or {},
    )


def _mc(drawdowns=None, final_balances=None, ruin_count=0,
        volatility=0.05, mean_return=0.02) -> MonteCarloResults:
    return MonteCarloResults(
        trajectories=[],
        final_balances=final_balances or [10_000.0] * 100,
        max_drawdowns=drawdowns or [0.05] * 100,
        ruin_count=ruin_count,
        mean_return=mean_return,
        volatility=volatility,
        percentile_5=9_000.0,
        percentile_95=11_000.0,
    )


# ── DrawdownConstraint ─────────────────────────────────────────────────────────

class TestDrawdownConstraint:
    def test_passes_when_drawdown_within_limit(self):
        c = DrawdownConstraint(max_drawdown=0.15)
        trajectory = [_state(drawdown=0.05), _state(drawdown=0.10)]
        passed, risk = c.evaluate(trajectory)
        assert passed is True
        assert risk == 0.0

    def test_fails_when_drawdown_exceeds_limit(self):
        c = DrawdownConstraint(max_drawdown=0.15)
        trajectory = [_state(drawdown=0.05), _state(drawdown=0.20)]
        passed, risk = c.evaluate(trajectory)
        assert passed is False
        assert risk > 0.0

    def test_empty_trajectory_passes(self):
        c = DrawdownConstraint(max_drawdown=0.15)
        passed, risk = c.evaluate([])
        assert passed is True
        assert risk == 0.0

    def test_exactly_at_limit_passes(self):
        c = DrawdownConstraint(max_drawdown=0.15)
        trajectory = [_state(drawdown=0.15)]
        passed, _ = c.evaluate(trajectory)
        assert passed is True

    def test_risk_score_increases_with_severity(self):
        c = DrawdownConstraint(max_drawdown=0.15)
        _, r1 = c.evaluate([_state(drawdown=0.16)])
        _, r2 = c.evaluate([_state(drawdown=0.30)])
        assert r2 > r1


# ── RuinProbabilityConstraint ──────────────────────────────────────────────────

class TestRuinProbabilityConstraint:
    def test_passes_when_ruin_prob_acceptable(self):
        c = RuinProbabilityConstraint(max_ruin_prob=0.02)
        # 1 ruin out of 100 = 1%
        trajectory = [_state(realized_pnl=100.0)]
        mc = _mc(ruin_count=1, final_balances=[10_000.0] * 99 + [50.0])
        passed, _ = c.evaluate(trajectory)
        assert passed is True

    def test_no_bets_placed_always_passes(self):
        c = RuinProbabilityConstraint(max_ruin_prob=0.02)
        trajectory = [_state(realized_pnl=0.0)]
        passed, risk = c.evaluate(trajectory)
        assert passed is True
        assert risk == 0.0

    def test_empty_trajectory_passes(self):
        c = RuinProbabilityConstraint()
        passed, _ = c.evaluate([])
        assert passed is True

    def test_severity_is_kill_switch(self):
        c = RuinProbabilityConstraint()
        assert c.severity == Severity.KILL_SWITCH


# ── VolatilityConstraint ───────────────────────────────────────────────────────

def _volatile_trajectory(n=25, roi_std=0.20):
    """Build a trajectory whose ROI std equals roi_std (approx)."""
    rng = list(range(n))
    # alternate positive/negative roi to create variance
    states = []
    for i in rng:
        roi = roi_std * (1 if i % 2 == 0 else -1)
        s = PortfolioState(
            timestamp=datetime.utcnow().isoformat(),
            roi=roi,
        )
        states.append(s)
    return states


class TestVolatilityConstraint:
    def test_passes_for_stable_trajectory(self):
        # All same ROI → std = 0 → passes
        c = VolatilityConstraint(max_volatility=0.15)
        trajectory = [_state() for _ in range(25)]  # roi=0.0 for all
        passed, _ = c.evaluate(trajectory)
        assert passed is True

    def test_fails_for_highly_volatile_trajectory(self):
        c = VolatilityConstraint(max_volatility=0.05)
        trajectory = _volatile_trajectory(n=25, roi_std=0.30)
        passed, _ = c.evaluate(trajectory)
        assert passed is False

    def test_single_state_always_passes(self):
        # Cannot compute std from fewer than 2 states
        c = VolatilityConstraint(max_volatility=0.0)
        passed, _ = c.evaluate([_state()])
        assert passed is True

    def test_empty_trajectory_passes(self):
        c = VolatilityConstraint()
        passed, _ = c.evaluate([])
        assert passed is True


# ── ExposureConcentrationConstraint ───────────────────────────────────────────

class TestExposureConcentrationConstraint:
    # This constraint reads current_state, not trajectory
    def test_passes_when_exposure_balanced(self):
        c = ExposureConcentrationConstraint(max_market_exposure=0.35)
        current = _state(exposure_by_market={"h2h": 0.20, "btts": 0.15})
        passed, _ = c.evaluate([], current_state=current)
        assert passed is True

    def test_fails_when_single_market_overexposed(self):
        c = ExposureConcentrationConstraint(max_market_exposure=0.35)
        current = _state(exposure_by_market={"h2h": 0.50})
        passed, _ = c.evaluate([], current_state=current)
        assert passed is False

    def test_no_current_state_passes(self):
        # No current_state → constraint cannot evaluate → conservatively passes
        c = ExposureConcentrationConstraint(max_market_exposure=0.35)
        passed, _ = c.evaluate([])
        assert passed is True

    def test_empty_exposure_passes(self):
        c = ExposureConcentrationConstraint(max_market_exposure=0.35)
        current = _state(exposure_by_market={})
        passed, _ = c.evaluate([], current_state=current)
        assert passed is True


# ── PolicyEngine (full evaluation) ─────────────────────────────────────────────

class TestPolicyEngine:
    def _engine(self) -> PolicyEngine:
        return PolicyEngine()

    def test_approve_for_healthy_portfolio(self):
        engine = self._engine()
        trajectory = [_state(drawdown=0.05, volatility=0.08)]
        mc = _mc(drawdowns=[0.05] * 100, ruin_count=0, volatility=0.08)
        current = _state(drawdown=0.05, volatility=0.08, realized_pnl=500.0)
        allocation = {"h2h": 0.15, "btts": 0.10}

        decision = engine.evaluate(mc, current_state=current,
                                   proposed_allocation=allocation)
        assert decision.decision in (
            PolicyDecisionType.APPROVE,
            PolicyDecisionType.THROTTLE,
        )

    def test_reject_for_extreme_drawdown(self):
        engine = self._engine()
        mc = _mc(drawdowns=[0.50] * 100, ruin_count=5, volatility=0.30)
        current = _state(drawdown=0.50, volatility=0.30, realized_pnl=1.0)

        decision = engine.evaluate(mc, current_state=current)
        assert decision.decision in (
            PolicyDecisionType.REJECT,
            PolicyDecisionType.THROTTLE,
        )

    def test_decision_has_risk_score(self):
        engine = self._engine()
        mc = _mc()
        decision = engine.evaluate(mc)
        assert isinstance(decision.risk_score, float)
        assert 0.0 <= decision.risk_score <= 1.0

    def test_decision_has_violated_constraints_list(self):
        engine = self._engine()
        mc = _mc()
        decision = engine.evaluate(mc)
        assert hasattr(decision, "violated_constraints")
        assert isinstance(decision.violated_constraints, list)

    def test_add_remove_constraint(self):
        engine = self._engine()
        initial_count = len(engine.constraints)

        extra = DrawdownConstraint(max_drawdown=0.05)
        extra.name = "test_extra_constraint"
        engine.add_constraint(extra)
        assert len(engine.constraints) == initial_count + 1

        engine.remove_constraint("test_extra_constraint")
        assert len(engine.constraints) == initial_count

    def test_empty_constraints_always_approve(self):
        engine = self._engine()
        engine.constraints = []
        mc = _mc()
        decision = engine.evaluate(mc)
        assert decision.decision == PolicyDecisionType.APPROVE
