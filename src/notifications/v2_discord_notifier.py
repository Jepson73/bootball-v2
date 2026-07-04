"""
V2 Discord notifier — Phase 30 (Separation Principle).

The honest voice: reports on the prediction/collection system that actually
runs today, nothing else. No predictions, no picks, no EV, no Kelly — those
live in the web UI with full context (Track A/B separation, data_context
tiers, indicative-odds labeling); a Discord line strips all of that and
recreates the exact V1 trap-message format this phase retires.

Event set (signal-only, no cycle spam):
    1. Genuine drift alarms      — notify_drift_alarm()
    2. Settlement-integrity      — notify_settlement_integrity()
    3. Collection heartbeat      — notify_collection_heartbeat() (daily, once)
    4. Deploy confirmations      — notify_deploy_complete()

Sent with a distinct identity (username override) on the SAME webhook relay
V1 used (confirmed generic in Phase 15) so provenance is never ambiguous —
every embed is visibly from "Bootball V2", never mistaken for the retired
V1 voice.

Rate-limiting + replay-safety: a small JSON state file
(data/state/v2_notifier_state.json) tracks last-sent timestamps per event
kind and the last-notified deploy commit / heartbeat date. Since this state
is read from disk (not memory), a process restart cannot replay an event
that already fired, and repeated identical conditions (e.g. drift staying
above threshold every cycle) collapse to one ping per cooldown window
instead of spamming — full detail always still lands in the logs.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

_STATE_FILE = Path("data/state/v2_notifier_state.json")

_USERNAME = "Bootball V2"

GREEN = 0x3fb950
RED = 0xf85149
YELLOW = 0xd29922
BLUE = 0x58a6ff

# Minimum seconds between two Discord pings of the same kind. A condition
# that keeps re-triggering inside the window is still logged in full by the
# caller (settlement.py / state_calibration_engine.py) — only the Discord
# ping is throttled.
_COOLDOWN_SECONDS = {
    "drift_alarm": 6 * 3600,
    "verify_guard_correction": 15 * 60,
    "forward_dated_live_catch": 15 * 60,
    "dead_mark_spike": 15 * 60,
    "collection_heartbeat": 20 * 3600,  # once/day with slop for scheduler jitter
    "deploy_complete": 0,  # deploy events are already 1-per-deploy; dedup by commit, not time
}


def _load_state() -> dict[str, Any]:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(_STATE_FILE)


def _cooldown_ok(kind: str, key: str) -> bool:
    """True if enough time has passed since the last send for (kind, key)."""
    state = _load_state()
    last_sent = state.get("last_sent", {}).get(f"{kind}:{key}", 0)
    return (time.time() - last_sent) >= _COOLDOWN_SECONDS.get(kind, 0)


def _mark_sent(kind: str, key: str) -> None:
    state = _load_state()
    state.setdefault("last_sent", {})[f"{kind}:{key}"] = time.time()
    _save_state(state)


def _embed(title: str, description: str, color: int, fields: list | None = None) -> dict:
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Bootball V2 — {settings.web_base_url}"},
    }
    if fields:
        embed["fields"] = fields
    return embed


def _post(embed: dict) -> bool:
    webhook_url = settings.discord_webhook_url
    if not webhook_url:
        logger.debug("[V2-DISCORD] No webhook configured, skipping")
        return False
    try:
        r = requests.post(
            webhook_url,
            json={"username": _USERNAME, "embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 204):
            return True
        logger.warning("[V2-DISCORD] send failed: %s %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.warning("[V2-DISCORD] send exception: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────
# 1. Genuine drift alarms
# ─────────────────────────────────────────────────────────────────────────

def notify_drift_alarm(market: str, live_drift_ece: float, threshold: float, reason: str) -> None:
    """live_drift_ece crossed threshold on a full window (Phase 28: the
    metric now reads live PredictionRecord settlements with persistent
    dedup, so — unlike the pre-Phase-28 ghost alarm — this is worth
    believing. Fired alongside (not instead of) whatever auto-recalibration
    CalibrationConsumer triggers; this is the human-facing echo of it.
    """
    if not _cooldown_ok("drift_alarm", market):
        logger.info("[V2-DISCORD] drift_alarm(%s) suppressed — within cooldown", market)
        return

    embed = _embed(
        title=f"📈 Drift Alarm — {market.upper()}",
        description=f"`live_drift_ece` crossed threshold on a full settlement window.",
        color=YELLOW,
        fields=[
            {"name": "live_drift_ece", "value": f"{live_drift_ece:.4f}", "inline": True},
            {"name": "Threshold", "value": f"{threshold:.4f}", "inline": True},
            {"name": "Trigger", "value": reason, "inline": False},
        ],
    )
    if _post(embed):
        _mark_sent("drift_alarm", market)


def wire_drift_alarm() -> None:
    """Subscribe to CALIBRATION_DRIFT_DETECTED (state_calibration_engine.py).
    Runs unconditionally — independent of the V1 discord_v1_enabled flag.
    """
    from src.alerts.event_bus import event_bus, Events

    def _on_drift(event: dict) -> None:
        market = event.get("market") or "overall"
        live_drift_ece = event.get("live_drift_ece", 0.0)
        reason = event.get("reason", "live_drift_ece_threshold_exceeded")
        try:
            from src.calibration.state_calibration_engine import StateCalibrationEngine
            threshold = StateCalibrationEngine().live_drift_ece_threshold
        except Exception:
            threshold = 0.10
        notify_drift_alarm(market, live_drift_ece, threshold, reason)

    event_bus.subscribe(Events.CALIBRATION_DRIFT_DETECTED, _on_drift)


# ─────────────────────────────────────────────────────────────────────────
# 2. Settlement-integrity events
# ─────────────────────────────────────────────────────────────────────────

def notify_settlement_integrity(kind: str, payload: dict) -> None:
    """verify-guard corrections, future-dated-live catches, DEAD-mark spikes."""
    dedup_key = kind  # one cooldown bucket per kind (not per fixture)
    if not _cooldown_ok(kind, dedup_key):
        logger.info("[V2-DISCORD] settlement_integrity(%s) suppressed — within cooldown", kind)
        return

    if kind == "verify_guard_correction":
        corrections = payload.get("corrections", [])
        lines = [f"• fixture {c['fixture_id']}: `{c['from']}` → `{c['to']}`" for c in corrections[:10]]
        if len(corrections) > 10:
            lines.append(f"  ...and {len(corrections) - 10} more")
        embed = _embed(
            title="🛡️ Settlement Verify-Guard Correction",
            description=f"{payload.get('count', len(corrections))} FT fixture(s) had a stale/glitched snapshot corrected before settlement.\n\n" + "\n".join(lines),
            color=YELLOW,
        )
    elif kind == "forward_dated_live_catch":
        catches = payload.get("catches", [])
        lines = [
            f"• fixture {c['fixture_id']}: stored `{c['stored_date']}` → live now `{c['live_date']}` ({c['status']})"
            for c in catches[:10]
        ]
        if len(catches) > 10:
            lines.append(f"  ...and {len(catches) - 10} more")
        embed = _embed(
            title="⏱️ Forward-Dated-Live Fixture(s) Corrected",
            description=f"{payload.get('count', len(catches))} fixture(s) were confirmed live while stored materially in the future.\n\n" + "\n".join(lines),
            color=YELLOW,
        )
    elif kind == "dead_mark_spike":
        embed = _embed(
            title="💀 DEAD-Mark Spike",
            description=(
                f"Marked **{payload.get('count')}** fixture(s) permanently untraceable this run — "
                f"well above the recent rolling baseline (**{payload.get('rolling_baseline')}**/run)."
            ),
            color=RED,
        )
    else:
        logger.warning("[V2-DISCORD] unknown settlement_integrity kind: %s", kind)
        return

    if _post(embed):
        _mark_sent(kind, dedup_key)


def wire_settlement_integrity() -> None:
    """Subscribe to SETTLEMENT_INTEGRITY_EVENT (src/settlement.py). Runs
    unconditionally — independent of the V1 discord_v1_enabled flag.
    """
    from src.alerts.event_bus import event_bus, Events

    def _on_event(event: dict) -> None:
        kind = event.get("kind")
        if kind:
            notify_settlement_integrity(kind, event)

    event_bus.subscribe(Events.SETTLEMENT_INTEGRITY_EVENT, _on_event)


# ─────────────────────────────────────────────────────────────────────────
# 3. Collection heartbeat (daily digest)
# ─────────────────────────────────────────────────────────────────────────

def notify_collection_heartbeat() -> None:
    """One daily digest: snapshots captured, trajectories accumulated,
    scheduler spend vs cap, quota headroom. Silence here means "broken" —
    this is the thing that prevents that failure mode from going unnoticed.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not _cooldown_ok("collection_heartbeat", "daily"):
        logger.info("[V2-DISCORD] collection_heartbeat suppressed — already sent recently")
        return

    from src.storage.db import get_session
    from sqlalchemy import text as sa_text

    with get_session() as s:
        has_snapshots = _table_exists(s, "odds_snapshots")
        snapshots_today = s.execute(sa_text(
            "SELECT COUNT(*) FROM odds_snapshots WHERE date(captured_at) = date('now')"
        )).scalar() if has_snapshots else None

        trajectories_total = s.execute(sa_text(
            "SELECT COUNT(DISTINCT fixture_id) FROM odds_snapshots"
        )).scalar() if has_snapshots else None

    quota_line = _read_quota_summary()

    fields = []
    if snapshots_today is not None:
        fields.append({"name": "Snapshots today", "value": str(snapshots_today), "inline": True})
    if trajectories_total is not None:
        fields.append({"name": "Trajectories accumulated", "value": f"{trajectories_total} / ~500", "inline": True})
    if quota_line:
        fields.append({"name": "Quota", "value": quota_line, "inline": False})

    embed = _embed(
        title="📡 Collection Heartbeat",
        description=f"Daily digest — {today}",
        color=BLUE,
        fields=fields or [{"name": "Status", "value": "No collection tables found", "inline": False}],
    )
    if _post(embed):
        _mark_sent("collection_heartbeat", "daily")


def _table_exists(session, name: str) -> bool:
    from sqlalchemy import text as sa_text
    row = session.execute(
        sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}
    ).fetchone()
    return row is not None


def _read_quota_summary() -> str | None:
    """Read the most recent row from logs/quota_log.csv (timestamp_utc,event,
    calls_used,calls_remaining,daily_limit,backfill_cap — written throughout
    the day by scripts/daily_run.py and odds_trajectory_scheduler.py)."""
    quota_log = Path("logs/quota_log.csv")
    if not quota_log.exists():
        return None
    try:
        lines = quota_log.read_text().strip().splitlines()
        if len(lines) < 2:
            return None
        header = lines[0].split(",")
        last = lines[-1].split(",")
        row = dict(zip(header, last))
        used = row.get("calls_used", "?")
        remaining = row.get("calls_remaining", "?")
        limit = row.get("daily_limit", str(settings.api_calls_per_day))
        return (
            f"{used}/{limit} calls used, {remaining} remaining "
            f"(backfill cap {settings.backfill_daily_cap:,}, collection cap {settings.collection_daily_cap:,})"
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# 4. Deploy confirmations
# ─────────────────────────────────────────────────────────────────────────

def notify_deploy_complete(commit_hash: str, services: dict[str, bool]) -> None:
    """deploy.sh completion — commit hash + services confirmed current.
    Turns the committed-but-not-running class of bug into a push notification
    instead of a silent gap discovered later.
    """
    state = _load_state()
    if state.get("last_deploy_commit") == commit_hash:
        logger.info("[V2-DISCORD] deploy_complete(%s) suppressed — already notified this commit", commit_hash)
        return

    all_up = all(services.values())
    lines = [f"{'✅' if up else '❌'} {name}" for name, up in services.items()]
    embed = _embed(
        title="🚀 Deploy Complete" if all_up else "⚠️ Deploy Complete — Service Mismatch",
        description=f"Commit `{commit_hash}`\n\n" + "\n".join(lines),
        color=GREEN if all_up else RED,
    )
    if _post(embed):
        state["last_deploy_commit"] = commit_hash
        _save_state(state)


def wire_v2_notifier() -> None:
    """Wire all always-on V2 subscriptions. Call once at process startup,
    independent of settings.discord_v1_enabled (V2 has its own voice)."""
    wire_drift_alarm()
    wire_settlement_integrity()
    logger.info("[V2-DISCORD] wired (drift alarms + settlement integrity)")
