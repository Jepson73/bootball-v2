"""
Calibration Consumer - handles calibration events.

On CALIBRATION_DRIFT_DETECTED with a market key: triggers actual recalibration
via ModelRegistry and sends a Discord result notification.

On CALIBRATION_DRIFT_DETECTED without a market key (legacy overall alert):
sends a notification only.
"""

import os
import logging
import threading
from typing import Any

from src.events.consumers.base import EventConsumer
from src.alerts.event_bus import Events

logger = logging.getLogger(__name__)


class CalibrationConsumer(EventConsumer):
    """
    Consumer that handles calibration events.

    Listens to:
    - CALIBRATION_DRIFT_DETECTED  (per-market → recalibrate; overall → notify)
    - MODEL_BIAS_ADJUSTED
    - RISK_MODEL_CORRECTED
    - PORTFOLIO_REWEIGHTING_SUGGESTED
    - CALIBRATION_REPORT_READY
    """

    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)

        self.event_types = [
            Events.CALIBRATION_DRIFT_DETECTED,
            Events.MODEL_BIAS_ADJUSTED,
            Events.RISK_MODEL_CORRECTED,
            Events.PORTFOLIO_REWEIGHTING_SUGGESTED,
            Events.CALIBRATION_REPORT_READY,
        ]

    def handles(self, event_type: str) -> bool:
        return event_type in self.event_types

    def process(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            logger.debug("Calibration consumer disabled (no webhook URL)")
            return

        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == Events.CALIBRATION_DRIFT_DETECTED:
            self._handle_calibration_drift(payload)
        elif event_type == Events.MODEL_BIAS_ADJUSTED:
            self._handle_model_bias(payload)
        elif event_type == Events.RISK_MODEL_CORRECTED:
            self._handle_risk_correction(payload)
        elif event_type == Events.PORTFOLIO_REWEIGHTING_SUGGESTED:
            self._handle_portfolio_reweighting(payload)
        elif event_type == Events.CALIBRATION_REPORT_READY:
            self._handle_calibration_report(payload)

    # ── CALIBRATION_DRIFT_DETECTED ───────────────────────────────────────────

    def _handle_calibration_drift(self, payload: dict[str, Any]) -> None:
        market = payload.get("market")

        if market:
            # Per-market event: fire actual recalibration in background
            threading.Thread(
                target=self._run_recalibration,
                args=(market, payload),
                daemon=True,
            ).start()
        else:
            # Overall drift notification (legacy / no action)
            self._send_drift_notification(payload)

    def _run_recalibration(self, market: str, payload: dict[str, Any]) -> None:
        """Fit a new calibrator for the market, register it, and notify Discord."""
        try:
            from backend.execution_engine import _fit_calibrator_for_market
            from src.models.model_registry import get_model_registry

            calibrator, cal_metrics = _fit_calibrator_for_market(market)
            if calibrator is None:
                self._send_webhook({
                    "title": f"⚠️ RECALIBRATION SKIPPED: {market.upper()}",
                    "description": "Insufficient settled data (< 100 rows). Will retry after next cooldown period.",
                    "color": 15105570,
                    "fields": [
                        {"name": "Market", "value": market.upper(), "inline": True},
                        {"name": "Trigger", "value": payload.get("reason", "ece_drift"), "inline": True},
                    ],
                    "timestamp": payload.get("timestamp", ""),
                })
                return

            trigger_ece = payload.get("ece", 0)
            post_ece = (cal_metrics or {}).get("ece", 0)
            cal_metrics["trigger_ece"] = trigger_ece

            registry = get_model_registry()
            new_ver = registry.register_recalibration(
                market, calibrator, metrics=cal_metrics, reason="auto_drift"
            )
            label = new_ver["version_label"] if new_ver else "unknown"

            self._send_webhook({
                "title": f"✅ RECALIBRATION COMPLETE: {market.upper()}",
                "description": "Automatic recalibration triggered by ECE drift",
                "color": 3066993,
                "fields": [
                    {"name": "Market", "value": market.upper(), "inline": True},
                    {"name": "New Version", "value": f"`{label}`", "inline": True},
                    {"name": "Trigger ECE", "value": f"{trigger_ece:.4f}", "inline": True},
                    {"name": "Post-recal ECE", "value": f"{post_ece:.4f}", "inline": True},
                    {"name": "Reason", "value": payload.get("reason", "drift"), "inline": False},
                ],
                "timestamp": payload.get("timestamp", ""),
            })
            logger.info("[CALIBRATION] Auto-recalibration complete: %s → %s", market, label)

        except Exception as e:
            logger.exception("[CALIBRATION] Auto-recalibration failed for %s", market)
            self._send_webhook({
                "title": f"❌ RECALIBRATION FAILED: {market.upper()}",
                "description": str(e),
                "color": 15158332,
                "timestamp": payload.get("timestamp", ""),
            })

    def _send_drift_notification(self, payload: dict[str, Any]) -> None:
        """Overall drift notification — no automated action taken."""
        self._send_webhook({
            "title": "📊 CALIBRATION DRIFT DETECTED",
            "description": "Overall model calibration drift detected",
            "color": 15105570,
            "fields": [
                {"name": "Calibration Error", "value": f"{payload.get('calibration_error', 0):.3f}", "inline": True},
                {"name": "Risk Bias", "value": f"{payload.get('risk_bias', 0):.3f}", "inline": True},
            ],
            "timestamp": payload.get("timestamp", ""),
        })

    # ── Other events ─────────────────────────────────────────────────────────

    def _handle_model_bias(self, payload: dict[str, Any]) -> None:
        self._send_webhook({
            "title": "🔧 MODEL BIAS ADJUSTED",
            "description": "Model bias corrected based on calibration feedback",
            "color": 3066993,
            "fields": [{"name": "Timestamp", "value": payload.get("timestamp", ""), "inline": False}],
            "timestamp": payload.get("timestamp", ""),
        })

    def _handle_risk_correction(self, payload: dict[str, Any]) -> None:
        adjustment = payload.get("adjustment") or {}
        self._send_webhook({
            "title": "⚖️ RISK MODEL CORRECTED",
            "description": "Risk model adjusted based on calibration feedback",
            "color": 3066993,
            "fields": [
                {"name": "Risk Bias", "value": f"{payload.get('risk_bias', 0):.3f}", "inline": True},
                {"name": "Action", "value": adjustment.get("action", "N/A"), "inline": True},
            ],
            "timestamp": payload.get("timestamp", ""),
        })

    def _handle_portfolio_reweighting(self, payload: dict[str, Any]) -> None:
        adjustment = payload.get("adjustment") or {}
        self._send_webhook({
            "title": "⚖️ PORTFOLIO REWEIGHTING SUGGESTED",
            "description": "Portfolio drift detected",
            "color": 15105570,
            "fields": [
                {"name": "Portfolio Drift", "value": f"{payload.get('drift', 0):.3f}", "inline": True},
                {"name": "Action", "value": adjustment.get("action", "N/A"), "inline": True},
            ],
            "timestamp": payload.get("timestamp", ""),
        })

    def _handle_calibration_report(self, payload: dict[str, Any]) -> None:
        markets = payload.get("markets", {})
        market_fields = [
            {
                "name": m.upper(),
                "value": f"Brier: {v.get('brier', 0):.3f}  ECE: {v.get('ece', 0):.3f}",
                "inline": True,
            }
            for m, v in markets.items()
        ]
        self._send_webhook({
            "title": "📊 CALIBRATION REPORT",
            "description": "System calibration status",
            "color": 3066993,
            "fields": [
                {"name": "Overall Error", "value": f"{payload.get('overall_error', 0):.3f}", "inline": True},
                {"name": "Risk Bias", "value": f"{payload.get('risk_bias', 0):.3f}", "inline": True},
                {"name": "Portfolio Drift", "value": f"{payload.get('portfolio_drift', 0):.3f}", "inline": True},
                {"name": "Correlation Error", "value": f"{payload.get('correlation_error', 0):.3f}", "inline": True},
            ] + market_fields,
            "timestamp": payload.get("timestamp", ""),
        })

    # ── Webhook ───────────────────────────────────────────────────────────────

    def _send_webhook(self, message: dict) -> None:
        import requests

        if not self.webhook_url:
            logger.warning("[CALIBRATION] No Discord webhook URL configured")
            return
        try:
            response = requests.post(
                self.webhook_url,
                json={"embeds": [message]},
                timeout=10,
            )
            response.raise_for_status()
            logger.info("[CALIBRATION] Discord message sent successfully")
        except Exception as e:
            logger.error("[CALIBRATION] Failed to send Discord message: %s", e)
