"""
src/models/model_registry.py

Single authority for model version lifecycle:
  - register_retrain()     — new vXX, cYY resets to 00
  - register_recalibration() — same vXX, cYY increments
  - activate()             — promote any historical version back to active
  - compare()              — metric diff between two version IDs
  - get_active()           — fetch the current active ModelVersion row
  - load_artifacts()       — load (model, calibrator) for any label or active

Pkl layout
----------
  data/model_{market}.pkl                      ← active (prediction pipeline reads this)
  data/models/model_{market}_{label}.pkl       ← versioned archive

Activating an old version copies its archive back to the active path.
"""
from __future__ import annotations

import logging
import os
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

_ACTIVE_DIR = Path("data")
_ARCHIVE_DIR = Path("data/models")


def _active_path(market: str) -> Path:
    return _ACTIVE_DIR / f"model_{market}.pkl"


def _archive_path(market: str, label: str) -> Path:
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    return _ARCHIVE_DIR / f"model_{market}_{label}.pkl"


def _make_label(model_number: int, calibration_number: int) -> str:
    return f"v{model_number:02d}_c{calibration_number:02d}"


class ModelRegistry:
    """Manages model version lifecycle for all markets."""

    MARKETS = ("h2h", "btts", "ou25", "ou15")

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_active(self, market: str) -> dict | None:
        """Return active version data for a market as a plain dict, or None."""
        from src.storage.db import get_session
        from src.storage.models import ModelVersion

        try:
            with get_session() as s:
                v = s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.market == market)
                    .where(ModelVersion.is_active == True)
                ).scalar_one_or_none()
                return self._to_dict(v) if v else None
        except Exception:
            logger.exception("get_active failed for %s", market)
            return None

    def get_active_ids(self) -> dict[str, int | None]:
        """Return {market: ModelVersion.id} for all active versions."""
        return {m: (v["id"] if (v := self.get_active(m)) else None) for m in self.MARKETS}

    def get_active_labels(self) -> dict[str, str]:
        """Return {market: version_label} for snapshot use."""
        result = {}
        for market in self.MARKETS:
            v = self.get_active(market)
            result[market] = v["version_label"] if v and v.get("version_label") else f"{market}_none"
        return result

    def list_versions(self, market: str, limit: int = 50) -> list[dict]:
        """Return all versions for a market ordered newest-first as plain dicts."""
        from src.storage.db import get_session
        from src.storage.models import ModelVersion

        try:
            with get_session() as s:
                rows = s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.market == market)
                    .order_by(ModelVersion.version_number.desc())
                    .limit(limit)
                ).scalars().all()
                return [self._to_dict(v) for v in rows]
        except Exception:
            logger.exception("list_versions failed for %s", market)
            return []

    def get_by_id(self, version_id: int) -> dict | None:
        """Return a version as a plain dict by primary key."""
        from src.storage.db import get_session
        from src.storage.models import ModelVersion

        try:
            with get_session() as s:
                v = s.get(ModelVersion, version_id)
                return self._to_dict(v) if v else None
        except Exception:
            logger.exception("get_by_id failed for version_id %d", version_id)
            return None

    @staticmethod
    def _to_dict(v) -> dict:
        return {
            "id": v.id,
            "market": v.market,
            "version_number": v.version_number,
            "model_number": v.model_number,
            "calibration_number": v.calibration_number,
            "version_label": v.version_label,
            "version_name": v.version_name,
            "brier_score": v.brier_score,
            "accuracy": v.accuracy,
            "ece": v.ece,
            "sample_size": v.sample_size,
            "calibration_sample_size": v.calibration_sample_size,
            "model_type": v.model_type,
            "features_used": v.features_used,
            "is_active": v.is_active,
            "trained_at": v.trained_at.isoformat() if v.trained_at else None,
        }

    def compare(self, v1_id: int, v2_id: int) -> dict:
        """Return metric comparison between two version IDs."""
        v1 = self.get_by_id(v1_id)
        v2 = self.get_by_id(v2_id)
        if not v1 or not v2:
            return {"error": "one or both version IDs not found"}
        return {
            "v1": {"id": v1["id"], "market": v1["market"], "label": v1["version_label"],
                   "brier": v1["brier_score"], "accuracy": v1["accuracy"], "ece": v1["ece"],
                   "sample_size": v1["sample_size"], "is_active": v1["is_active"]},
            "v2": {"id": v2["id"], "market": v2["market"], "label": v2["version_label"],
                   "brier": v2["brier_score"], "accuracy": v2["accuracy"], "ece": v2["ece"],
                   "sample_size": v2["sample_size"], "is_active": v2["is_active"]},
            "delta": {
                "brier": round((v2["brier_score"] or 0) - (v1["brier_score"] or 0), 6),
                "accuracy": round((v2["accuracy"] or 0) - (v1["accuracy"] or 0), 6),
                "ece": round((v2["ece"] or 0) - (v1["ece"] or 0), 6),
            },
            "better": "v2" if (v2["brier_score"] or 1) < (v1["brier_score"] or 1) else "v1",
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    def register_retrain(
        self,
        market: str,
        model,
        calibrator,
        metrics: dict,
        reason: str = "manual",
    ):
        """Register a newly trained model. Creates new vXX, resets cYY to 00.

        Returns the new ModelVersion row.
        """
        from src.storage.db import get_session
        from src.storage.models import ModelVersion, RetrainEvent

        with get_session() as s:
            # Get current max version_number and model_number
            existing = s.execute(
                select(ModelVersion)
                .where(ModelVersion.market == market)
                .order_by(ModelVersion.version_number.desc())
            ).scalars().first()

            next_version_number = (existing.version_number + 1) if existing else 1
            next_model_number = (existing.model_number + 1) if existing else 1
            label = _make_label(next_model_number, 0)

            # Deactivate current
            old_id = None
            old_brier = None
            if existing:
                active = s.execute(
                    select(ModelVersion)
                    .where(ModelVersion.market == market)
                    .where(ModelVersion.is_active == True)
                ).scalar_one_or_none()
                if active:
                    old_id = active.id
                    old_brier = active.brier_score
                    active.is_active = False

            new_ver = ModelVersion(
                market=market,
                version_number=next_version_number,
                version_name=label,
                model_number=next_model_number,
                calibration_number=0,
                version_label=label,
                brier_score=metrics.get("brier_score", 0),
                accuracy=metrics.get("accuracy", 0),
                ece=metrics.get("ece", 0),
                sample_size=metrics.get("sample_size", 0),
                calibration_sample_size=metrics.get("calibration_sample_size", 0),
                model_type=metrics.get("model_type", "lightgbm+isotonic"),
                features_used=metrics.get("features_used"),
                is_active=True,
                trained_at=datetime.utcnow(),
            )
            s.add(new_ver)
            s.flush()

            if old_id:
                s.add(RetrainEvent(
                    market=market,
                    old_version_id=old_id,
                    new_version_id=new_ver.id,
                    reason=reason,
                    reason_detail=f"Brier: {old_brier:.4f} -> {new_ver.brier_score:.4f}" if old_brier else reason,
                    brier_score_before=old_brier,
                    brier_score_after=new_ver.brier_score,
                    triggered_by_drift="drift" in reason,
                ))
            s.commit()
            new_id = new_ver.id  # read before session closes

        self._save_artifacts(market, label, model, calibrator)
        logger.info("Registered retrain %s %s (id=%d)", market, label, new_id)
        try:
            from src.notifications.discord_system_notifier import notify_model_change
            old_label = existing.version_label if existing else "—"
            notify_model_change(market, old_label, label, {
                "reason": reason,
                "brier_before": old_brier,
                "brier_after": metrics.get("brier_score"),
            })
        except Exception:
            pass
        return self.get_by_id(new_id)  # returns dict

    def register_recalibration(
        self,
        market: str,
        calibrator,
        metrics: dict,
        reason: str = "recalibration",
    ):
        """Register a recalibration of the current active model. Increments cYY.

        The base model pkl is unchanged; only the calibrator is updated.
        Returns the new ModelVersion row.
        """
        from src.storage.db import get_session
        from src.storage.models import ModelVersion, RetrainEvent

        # Read active version first, before opening the write session
        active_dict = self.get_active(market)
        if not active_dict:
            logger.warning("register_recalibration: no active version for %s", market)
            return None

        with get_session() as s:
            existing = s.execute(
                select(ModelVersion)
                .where(ModelVersion.market == market)
                .order_by(ModelVersion.version_number.desc())
            ).scalars().first()

            next_version_number = existing.version_number + 1
            next_calib = active_dict["calibration_number"] + 1
            label = _make_label(active_dict["model_number"], next_calib)
            old_id = active_dict["id"]
            old_brier = active_dict["brier_score"]

            # Deactivate old
            old_row = s.get(ModelVersion, old_id)
            if old_row:
                old_row.is_active = False

            new_ver = ModelVersion(
                market=market,
                version_number=next_version_number,
                version_name=label,
                model_number=active_dict["model_number"],
                calibration_number=next_calib,
                version_label=label,
                brier_score=metrics.get("brier_score", active_dict["brier_score"]),
                accuracy=metrics.get("accuracy", active_dict["accuracy"]),
                ece=metrics.get("ece", active_dict["ece"]),
                sample_size=active_dict["sample_size"],
                calibration_sample_size=metrics.get("calibration_sample_size", 0),
                model_type=active_dict["model_type"],
                features_used=active_dict["features_used"],
                is_active=True,
                trained_at=datetime.utcnow(),
            )
            s.add(new_ver)
            s.flush()

            trigger_ece = metrics.get("trigger_ece")
            post_ece = new_ver.ece or 0.0
            if trigger_ece is not None:
                detail = f"Trigger ECE: {trigger_ece:.4f} → post-recal ECE: {post_ece:.4f} (eval n={metrics.get('eval_sample_size', '?')})"
            else:
                prev_ece = active_dict.get("ece") or 0.0
                detail = f"ECE {prev_ece:.4f} → {post_ece:.4f}"
            s.add(RetrainEvent(
                market=market,
                old_version_id=old_id,
                new_version_id=new_ver.id,
                reason=reason,
                reason_detail=detail,
                brier_score_before=old_brier,
                brier_score_after=new_ver.brier_score,
                triggered_by_drift="drift" in reason,
            ))
            s.commit()
            new_id = new_ver.id

        # Load base model from previous archive, swap calibrator, save to both paths
        try:
            prev_label = _make_label(active_dict["model_number"], active_dict["calibration_number"])
            model, _ = self.load_artifacts(market, prev_label)
            if model is not None:
                self._save_artifacts(market, label, model, calibrator)
        except Exception:
            logger.exception("Failed to save recalibration artifacts for %s %s", market, label)

        logger.info("Registered recalibration %s %s (id=%d)", market, label, new_id)
        try:
            from src.notifications.discord_system_notifier import notify_model_change
            old_label_str = _make_label(active_dict["model_number"], active_dict["calibration_number"])
            notify_model_change(market, old_label_str, label, {
                "reason": reason,
                "brier_before": old_brier,
                "brier_after": metrics.get("brier_score"),
            })
        except Exception:
            pass
        return self.get_by_id(new_id)

    def activate(self, version_id: int) -> bool:
        """Promote a historical version to active. Copies its pkl to the active path."""
        from src.storage.db import get_session
        from src.storage.models import ModelVersion

        try:
            market = None
            label = None
            with get_session() as s:
                target = s.get(ModelVersion, version_id)
                if not target:
                    logger.warning("activate: version_id %d not found", version_id)
                    return False

                market = target.market
                label = target.version_label

                others = s.execute(
                    select(ModelVersion).where(ModelVersion.market == market)
                ).scalars().all()
                for v in others:
                    v.is_active = (v.id == version_id)
                s.commit()

            # Restore pkl outside the session
            archive = _archive_path(market, label)
            active = _active_path(market)
            if archive.exists():
                from src.security.safe_load import safe_model_load, safe_model_save
                payload = safe_model_load(str(archive))
                if payload is not None:
                    safe_model_save(payload, str(active))
                else:
                    # Archive has no sig yet (written before this fix); copy and re-sign
                    payload = safe_model_load(str(archive), verify_hmac=False)
                    if payload is not None:
                        safe_model_save(payload, str(active))
                logger.info("Activated %s %s — restored pkl from archive", market, label)
            else:
                logger.warning("Activated %s %s in DB but archive pkl not found at %s", market, label, archive)

            try:
                from src.notifications.discord_system_notifier import notify_model_change
                notify_model_change(market, "previous", label, {"reason": "manual activation"})
            except Exception:
                pass

            return True
        except Exception:
            logger.exception("activate failed for version_id %d", version_id)
            return False

    # ── Artifacts ─────────────────────────────────────────────────────────────

    def _save_artifacts(self, market: str, label: str, model, calibrator) -> None:
        """Save model+calibrator to both the active and versioned archive paths."""
        payload = {
            "model": model,
            "calibrator": calibrator,
            "market": market,
            "version_label": label,
            "saved_at": datetime.utcnow().isoformat(),
        }
        active = _active_path(market)
        archive = _archive_path(market, label)

        from src.security.safe_load import safe_model_save
        for path in (active, archive):
            path.parent.mkdir(parents=True, exist_ok=True)
            safe_model_save(payload, str(path))

        logger.info("Saved artifacts for %s %s → %s + %s", market, label, active, archive)

    def load_artifacts(self, market: str, label: str | None = None) -> tuple:
        """Load (model, calibrator) from disk.

        label=None → active path. label=str → versioned archive.
        Returns (None, None) on failure.
        """
        path = _active_path(market) if label is None else _archive_path(market, label)
        if not path.exists():
            logger.warning("load_artifacts: %s not found", path)
            return None, None
        try:
            from src.security import safe_model_load
            obj = safe_model_load(str(path))
            if obj is None:
                return None, None
            return obj.get("model"), obj.get("calibrator")
        except Exception:
            logger.exception("load_artifacts failed for %s", path)
            return None, None


_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
