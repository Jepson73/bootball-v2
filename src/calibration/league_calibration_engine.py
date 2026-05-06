"""
src/calibration/league_calibration_engine.py

Per-league Platt-scaling calibration.

For each (market, league_id) pair with enough settled history, fits a
logistic regression on (logit(p_raw), y) to produce a league-specific
calibration layer on top of the global model.

Version label format:  v{model_number:02d}_c{calibration_number:02d}_l{league_id:04d}

Usage:
    engine = LeagueCalibrationEngine()
    results = engine.fit_all()           # fit every qualifying league
    p_cal = engine.apply("h2h", 39, 0.63)  # calibrate for Premier League
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import NamedTuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select

from src.storage.db import get_session
from src.storage.models import Fixture, LeagueCalibration, ModelVersion, PredictionRecord

logger = logging.getLogger(__name__)

# Minimum settled samples per (market, league) to attempt fitting
MIN_SAMPLES = 100
# Fraction of samples reserved for hold-out evaluation (chronological tail)
HOLDOUT_FRACTION = 0.20
_EPSILON = 1e-7


class FitResult(NamedTuple):
    market: str
    league_id: int
    version_label: str
    slope: float
    intercept: float
    brier_score: float          # league-cal hold-out
    brier_score_global: float   # no-cal (raw prob) hold-out as global proxy
    brier_improvement: float    # positive = league cal wins
    sample_size: int
    activated: bool             # True if improvement > 0


def _logit(p: float) -> float:
    p = max(_EPSILON, min(1 - _EPSILON, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _brier(probs: np.ndarray, actuals: np.ndarray) -> float:
    return float(np.mean((probs - actuals) ** 2))


class LeagueCalibrationEngine:
    """Fits and applies per-league Platt-scaling calibration."""

    def fit_all(self) -> list[FitResult]:
        """Fit calibration for every (market, league_id) with enough data."""
        results: list[FitResult] = []
        with get_session() as s:
            # Pull all settled predictions with their league context
            rows = s.execute(
                select(
                    PredictionRecord.market,
                    Fixture.league_id,
                    PredictionRecord.our_prob,
                    PredictionRecord.won,
                    PredictionRecord.created_at,
                )
                .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
                .where(PredictionRecord.settled == True)
                .where(PredictionRecord.won.isnot(None))
                .where(PredictionRecord.our_prob.isnot(None))
                .order_by(PredictionRecord.created_at)
            ).all()

        # Group chronologically per (market, league_id)
        from collections import defaultdict
        groups: dict[tuple[str, int], list] = defaultdict(list)
        for market, league_id, prob, won, ts in rows:
            groups[(market, league_id)].append((ts, float(prob), int(won)))

        # Get active ModelVersion per market for version labels
        version_map = self._active_versions()

        for (market, league_id), samples in groups.items():
            if len(samples) < MIN_SAMPLES:
                continue
            result = self._fit_one(market, league_id, samples, version_map)
            if result:
                self._save(result)
                results.append(result)
                logger.info(
                    "League cal %s league=%d  bs=%.4f (Δ%+.4f)  activated=%s",
                    result.version_label, league_id,
                    result.brier_score, result.brier_improvement, result.activated
                )

        return results

    def _fit_one(
        self,
        market: str,
        league_id: int,
        samples: list[tuple],
        version_map: dict[str, ModelVersion | None],
    ) -> FitResult | None:
        samples.sort(key=lambda x: x[0])  # chronological
        n = len(samples)
        split = max(1, int(n * (1 - HOLDOUT_FRACTION)))

        train = samples[:split]
        test = samples[split:]
        if not test:
            test = train[-max(1, n // 5):]

        X_train = np.array([[_logit(p)] for _, p, _ in train])
        y_train = np.array([y for _, _, y in train])
        X_test = np.array([[_logit(p)] for _, p, _ in test])
        y_test = np.array([y for _, _, y in test])
        p_test_raw = np.array([p for _, p, _ in test])

        try:
            lr = LogisticRegression(solver="lbfgs", max_iter=1000)
            lr.fit(X_train, y_train)
            slope = float(lr.coef_[0][0])
            intercept = float(lr.intercept_[0])
        except Exception as exc:
            logger.warning("League cal fit failed market=%s league=%d: %s", market, league_id, exc)
            return None

        p_test_cal = np.array([_sigmoid(slope * _logit(p) + intercept) for _, p, _ in test])
        bs_league = _brier(p_test_cal, y_test)
        bs_global = _brier(p_test_raw, y_test)
        improvement = bs_global - bs_league  # positive = better

        mv = version_map.get(market)
        model_num = mv.model_number if mv else 1
        cal_num = mv.calibration_number if mv else 0
        version_label = f"v{model_num:02d}_c{cal_num:02d}_l{league_id:04d}"

        activated = improvement > 0

        return FitResult(
            market=market,
            league_id=league_id,
            version_label=version_label,
            slope=slope,
            intercept=intercept,
            brier_score=bs_league,
            brier_score_global=bs_global,
            brier_improvement=improvement,
            sample_size=n,
            activated=activated,
        )

    def _save(self, r: FitResult) -> None:
        with get_session() as s:
            # Deactivate old entries for this (market, league_id)
            old = s.execute(
                select(LeagueCalibration)
                .where(LeagueCalibration.market == r.market)
                .where(LeagueCalibration.league_id == r.league_id)
                .where(LeagueCalibration.is_active == True)
            ).scalars().all()
            for o in old:
                o.is_active = False

            existing = s.execute(
                select(LeagueCalibration)
                .where(LeagueCalibration.market == r.market)
                .where(LeagueCalibration.league_id == r.league_id)
                .where(LeagueCalibration.version_label == r.version_label)
            ).scalar_one_or_none()

            if existing:
                existing.slope = r.slope
                existing.intercept = r.intercept
                existing.brier_score = r.brier_score
                existing.brier_score_global = r.brier_score_global
                existing.brier_improvement = r.brier_improvement
                existing.sample_size = r.sample_size
                existing.is_active = r.activated
            else:
                s.add(LeagueCalibration(
                    market=r.market,
                    league_id=r.league_id,
                    version_label=r.version_label,
                    slope=r.slope,
                    intercept=r.intercept,
                    brier_score=r.brier_score,
                    brier_score_global=r.brier_score_global,
                    brier_improvement=r.brier_improvement,
                    sample_size=r.sample_size,
                    is_active=r.activated,
                ))
            s.commit()

        # Discord notification — look up league name for a friendly message
        try:
            from src.notifications.discord_system_notifier import notify_calibration_change
            from src.storage.models import League
            with get_session() as s:
                lg = s.execute(
                    select(League).where(League.id == r.league_id)
                ).scalar_one_or_none()
                league_name = lg.name if lg else f"league {r.league_id}"
            notify_calibration_change(
                market=r.market,
                league_id=r.league_id,
                league_name=league_name,
                version_label=r.version_label,
                brier_improvement=r.brier_improvement,
                sample_size=r.sample_size,
            )
        except Exception:
            pass

    # ── Lookup / apply ────────────────────────────────────────────────────────

    def get_calibration(self, market: str, league_id: int) -> LeagueCalibration | None:
        """Return the active LeagueCalibration row, or None if not available."""
        with get_session() as s:
            return s.execute(
                select(LeagueCalibration)
                .where(LeagueCalibration.market == market)
                .where(LeagueCalibration.league_id == league_id)
                .where(LeagueCalibration.is_active == True)
            ).scalar_one_or_none()

    def apply(self, market: str, league_id: int, p_raw: float) -> tuple[float, str | None]:
        """Apply league calibration if available; fall back to p_raw.

        Returns (calibrated_prob, version_label_or_None).
        """
        cal = self.get_calibration(market, league_id)
        if cal is None:
            return p_raw, None
        p_cal = _sigmoid(cal.slope * _logit(p_raw) + cal.intercept)
        return p_cal, cal.version_label

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _active_versions(self) -> dict[str, ModelVersion | None]:
        with get_session() as s:
            rows = s.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)
            ).scalars().all()
            return {mv.market: mv for mv in rows}
