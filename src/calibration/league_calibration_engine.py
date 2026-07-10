"""
src/calibration/league_calibration_engine.py

Per-league Platt-scaling calibration.

Three-tier version system: VxxCyyLzzzz
  - Vxx  : base model (retrained rarely)
  - Cyy  : global calibration (real-time drift correction, league_id=0)
  - Lzzzz: per-league calibration (seasonal, specific league_id)

apply() resolution order:
  1. Active league-specific calibration for the fixture's league_id
  2. Active global calibration (L0000, league_id=0)
  3. Raw p_raw fallback

Version label format:  v{model_number:02d}_c{calibration_number:02d}_l{league_id:04d}

Usage:
    engine = LeagueCalibrationEngine()
    results = engine.fit_all()           # fit every qualifying league + L0000 global
    p_cal = engine.apply("h2h", 39, 0.63)  # calibrate for Premier League
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import NamedTuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select, func

from src.storage.db import get_session
from src.storage.models import Fixture, LeagueCalibration, ModelVersion, PredictionRecord

logger = logging.getLogger(__name__)

# Minimum settled samples per (market, league) to attempt fitting
MIN_SAMPLES = 25
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

    def fit_all(self, market: str | None = None) -> list[FitResult]:
        """Fit calibration for every (market, league_id) with enough data.

        Pass `market` to scope the refit to a single market (used by the
        drift-triggered path — Phase 33 made this the sole calibrator that
        serves, so an unscoped fit_all() on every single-market drift event
        would needlessly refit the other 3 markets' ~150 league calibrations
        each time and spam notify_calibration_change() for all of them).
        """
        results: list[FitResult] = []
        with get_session() as s:
            # Pull all settled predictions with their league context.
            # our_prob < 0.5 for a binary market's predicted outcome is the
            # Phase 32 corruption signature (mathematically impossible for a
            # healthy write path — our_prob is the max() of the two-outcome
            # dict) — excluded here too so a refit never re-trains on the
            # same known-bad rows get_track_a_stats() already excludes from
            # scoring.
            query = (
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
                .where(
                    (PredictionRecord.market.notin_(["btts", "ou25", "ou15"]))
                    | (PredictionRecord.our_prob >= 0.5)
                )
                .order_by(PredictionRecord.created_at)
            )
            if market is not None:
                query = query.where(PredictionRecord.market == market)
            rows = s.execute(query).all()

        # Group chronologically per (market, league_id)
        from collections import defaultdict
        groups: dict[tuple[str, int], list] = defaultdict(list)
        for mkt, league_id, prob, won, ts in rows:
            groups[(mkt, league_id)].append((ts, float(prob), int(won)))

        # Get active ModelVersion per market for version labels
        version_map = self._active_versions()

        # Fit L0000 global calibration first (all leagues combined, league_id=None).
        # L0000 delta is vs raw — measures whether global calibration helps at all.
        global_groups: dict[str, list] = {}
        for (mkt, _league_id), samples in groups.items():
            global_groups.setdefault(mkt, []).extend(samples)
        global_results: dict[str, FitResult] = {}
        for mkt, samples in global_groups.items():
            if len(samples) >= MIN_SAMPLES:
                result = self._fit_one(mkt, None, samples, version_map)
                if result:
                    self._save(result)
                    results.append(result)
                    global_results[mkt] = result
                    logger.info(
                        "Global cal (L0000) %s  bs=%.4f (Δ%+.4f vs raw)  activated=%s",
                        result.version_label, result.brier_score,
                        result.brier_improvement, result.activated
                    )

        # League-specific calibrations: delta is vs L0000 (not raw) so "positive" means
        # the league cal genuinely beats the global fallback, not just uncalibrated output.
        refit_league_keys: set[tuple[str, int]] = set()
        for (mkt, league_id), samples in groups.items():
            if len(samples) < MIN_SAMPLES:
                continue
            refit_league_keys.add((mkt, league_id))
            global_cal = global_results.get(mkt)
            result = self._fit_one(mkt, league_id, samples, version_map, global_cal=global_cal)
            if result:
                self._save(result)
                results.append(result)
                logger.info(
                    "League cal %s league=%d  bs=%.4f (Δ%+.4f vs L0000)  activated=%s",
                    result.version_label, league_id,
                    result.brier_score, result.brier_improvement, result.activated
                )

        # A league whose sample count DROPS below MIN_SAMPLES between refits (e.g.
        # losing rows to a corruption exclusion, as ou15 league 211 did going from
        # 25 -> 24 the first time this ran with the Phase 32 filter) is skipped above
        # -- but its previously-active calibration must not be left silently serving
        # forever just because nothing newer ever supersedes it. Deactivate any
        # currently-active league calibration in this fit's scope that isn't among
        # today's refit leagues, so apply() falls back to L0000/raw as designed.
        self._deactivate_orphaned_leagues(market, refit_league_keys)

        return results

    def _deactivate_orphaned_leagues(self, market: str | None, refit_league_keys: set[tuple[str, int]]) -> None:
        with get_session() as s:
            q = select(LeagueCalibration).where(
                LeagueCalibration.is_active == True,
                LeagueCalibration.league_id.isnot(None),
            )
            if market is not None:
                q = q.where(LeagueCalibration.market == market)
            active_league_rows = s.execute(q).scalars().all()
            for row in active_league_rows:
                if (row.market, row.league_id) not in refit_league_keys:
                    logger.warning(
                        "League cal %s (market=%s league=%d) fell below MIN_SAMPLES=%d "
                        "on refit -- deactivating so apply() falls back to L0000/raw "
                        "instead of leaving it silently active.",
                        row.version_label, row.market, row.league_id, MIN_SAMPLES,
                    )
                    row.is_active = False
            if active_league_rows:
                s.commit()

    def _fit_one(
        self,
        market: str,
        league_id: int | None,
        samples: list[tuple],
        version_map: dict[str, tuple[int, int]],
        global_cal: "FitResult | None" = None,
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

        if global_cal is not None:
            # League-specific: baseline is L0000 applied to this league's test set.
            # Positive improvement means this league cal genuinely beats the global fallback.
            p_test_baseline = np.array([
                _sigmoid(global_cal.slope * _logit(p) + global_cal.intercept)
                for _, p, _ in test
            ])
            bs_baseline = _brier(p_test_baseline, y_test)
        else:
            # L0000 itself: baseline is raw (uncalibrated) model output.
            bs_baseline = _brier(p_test_raw, y_test)

        improvement = bs_baseline - bs_league  # positive = beats baseline

        model_num, cal_num = version_map.get(market, (1, 0))
        league_suffix = "l0000" if league_id is None else f"l{league_id:04d}"
        base_label = f"v{model_num:02d}_c{cal_num:02d}_{league_suffix}"
        ww = self._next_iteration(market, league_id, base_label)
        version_label = f"{base_label}_w{ww:02d}"

        activated = improvement > 0

        return FitResult(
            market=market,
            league_id=league_id,
            version_label=version_label,
            slope=slope,
            intercept=intercept,
            brier_score=bs_league,
            brier_score_global=bs_baseline,  # raw for L0000; L0000-applied for league rows
            brier_improvement=improvement,
            sample_size=n,
            activated=activated,
        )

    def _next_iteration(self, market: str, league_id: int | None, base_label: str) -> int:
        """Return the next _ww iteration number for a (market, league_id, VxxCyy) base label.

        Counts existing rows whose version_label starts with '{base_label}_w'.
        Old-format rows (pre-ww, no _w suffix) are not counted.
        """
        with get_session() as s:
            q = (
                select(func.count())
                .select_from(LeagueCalibration)
                .where(LeagueCalibration.market == market)
                .where(LeagueCalibration.version_label.like(f"{base_label}_w%"))
            )
            if league_id is None:
                q = q.where(LeagueCalibration.league_id.is_(None))
            else:
                q = q.where(LeagueCalibration.league_id == league_id)
            count = s.execute(q).scalar() or 0
        return count + 1

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

    def get_calibration(self, market: str, league_id: int | None) -> tuple[float, float, str] | None:
        """Return (slope, intercept, version_label) for active calibration, or None.

        Pass league_id=None to query the global (L0000) calibration.
        """
        with get_session() as s:
            q = (
                select(LeagueCalibration)
                .where(LeagueCalibration.market == market)
                .where(LeagueCalibration.is_active == True)
            )
            if league_id is None:
                q = q.where(LeagueCalibration.league_id.is_(None))
            else:
                q = q.where(LeagueCalibration.league_id == league_id)
            row = s.execute(q).scalar_one_or_none()
            if row is None:
                return None
            return (float(row.slope), float(row.intercept), row.version_label)

    # Minimum sample size before trusting a league-specific calibration.
    # Below this threshold, fall back to L0000 (global) calibration.
    # L0000 always has thousands of samples and is safe to use unconditionally.
    _MIN_LEAGUE_SAMPLES = 100

    def apply(self, market: str, league_id: int | None, p_raw: float) -> tuple[float, str | None]:
        """Apply calibration: league-specific → L0000 global → raw fallback.

        Resolution order:
          1. Active calibration for this league_id (Lzzzz) — only if sample_size >= _MIN_LEAGUE_SAMPLES
          2. Active global calibration (L0000, league_id=None)
          3. Raw p_raw

        Returns (calibrated_prob, version_label_or_None).
        """
        if league_id is not None:
            with get_session() as s:
                q = (
                    select(LeagueCalibration)
                    .where(LeagueCalibration.market == market)
                    .where(LeagueCalibration.is_active == True)
                    .where(LeagueCalibration.league_id == league_id)
                )
                row = s.execute(q).scalar_one_or_none()
                if row is not None and row.sample_size >= self._MIN_LEAGUE_SAMPLES:
                    p_cal = _sigmoid(float(row.slope) * _logit(p_raw) + float(row.intercept))
                    return p_cal, row.version_label
                # Insufficient samples or no league cal — fall through to L0000

        cal = self.get_calibration(market, None)  # L0000 global
        if cal is None:
            return p_raw, None
        slope, intercept, version_label = cal
        p_cal = _sigmoid(slope * _logit(p_raw) + intercept)
        return p_cal, version_label

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _active_versions(self) -> dict[str, tuple[int, int]]:
        """Return {market: (model_number, calibration_number)} for active versions."""
        with get_session() as s:
            rows = s.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)
            ).scalars().all()
            return {mv.market: (mv.model_number or 1, mv.calibration_number or 0) for mv in rows}
