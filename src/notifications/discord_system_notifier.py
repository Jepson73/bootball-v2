"""
Discord system notifications for Bootball execution events.

Sends formatted embeds to Discord for:
- Cycle success summary (every N cycles)
- Cycle failures / pipeline errors with remediation steps
- Watchdog alerts with actionable commands
- Bet placement summaries
- Settled bet results

Configured via DISCORD_WEBHOOK_URL in .env
"""

import logging
import os
from datetime import datetime
from threading import Thread

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

from config.settings import settings as _settings
_WEB_URL = _settings.web_base_url

# Only send a success summary every N cycles to avoid spam
SUCCESS_NOTIFY_EVERY = 5

_cycle_count = 0

# Remediation steps for each alert type
_REMEDIATION = {
    "no_predictions": (
        "**What to check:**\n"
        "```bash\n"
        "# Check runtime logs for errors\n"
        "journalctl -u bootball-runtime.service -n 50\n\n"
        "# Restart the runtime\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ),
    "no_bets": (
        "**This may be normal** (low EV day, policy throttling, or risk limits).\n"
        f"Check if predictions have positive EV at {_WEB_URL}\n"
        "```bash\n"
        "# Check runtime logs for policy rejections\n"
        "journalctl -u bootball-runtime.service -n 50 | grep -E 'POLICY|REJECT|EV'\n"
        "```"
    ),
    "repeated_crashes": (
        "**The execution cycle is crashing in a loop.**\n"
        "```bash\n"
        "# See full error\n"
        "journalctl -u bootball-runtime.service -n 80 | grep -A5 ERROR\n\n"
        "# Restart\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ),
    "heartbeat_timeout": (
        "**Runtime is frozen or dead.**\n"
        "```bash\n"
        "# Check if process is still running\n"
        "systemctl status bootball-runtime.service\n\n"
        "# Force restart\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ),
    "max_restarts_reached": (
        "**Watchdog gave up restarting — manual intervention required.**\n"
        "```bash\n"
        "# See what is failing\n"
        "journalctl -u bootball-runtime.service -n 100\n\n"
        "# Restart after fixing the issue\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ),
    "risk_limit_breached": (
        "**Risk limits triggered — stakes throttled or bets blocked.**\n"
        f"Check the Risk page at {_WEB_URL}\n"
        "```bash\n"
        "journalctl -u bootball-runtime.service -n 30 | grep -E 'POLICY|RISK'\n"
        "```"
    ),
}

_PIPELINE_REMEDIATION = {
    "POLICY_REJECTED": (
        "**Policy engine rejected the portfolio — no bets placed this cycle.**\n"
        "This may be normal (drawdown protection, exposure limits).\n"
        "```bash\n"
        "journalctl -u bootball-runtime.service -n 30 | grep -E 'POLICY|REJECT'\n"
        "```"
    ),
    "KILL_SWITCH": (
        "**Kill switch triggered — all betting halted.**\n"
        "```bash\n"
        "# Check what triggered it\n"
        "journalctl -u bootball-runtime.service -n 50 | grep -E 'KILL|SWITCH'\n\n"
        "# After investigating, restart to reset\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ),
}


def _post(payload: dict) -> bool:
    if not WEBHOOK_URL:
        return False
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"[DISCORD] Send failed: {e}")
        return False


def _post_async(payload: dict):
    Thread(target=_post, args=(payload,), daemon=True).start()


def _embed(title: str, description: str, color: int, fields: list = None) -> dict:
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": f"Bootball AI — {_WEB_URL}"},
    }
    if fields:
        embed["fields"] = fields
    return {"embeds": [embed]}


GREEN = 0x3fb950
RED = 0xf85149
YELLOW = 0xd29922
BLUE = 0x58a6ff


def notify_cycle_success(predictions: int, bets: int, duration: float, run_id: str):
    global _cycle_count
    _cycle_count += 1
    if _cycle_count % SUCCESS_NOTIFY_EVERY != 1:
        return
    payload = _embed(
        title="✅ Cycle Complete",
        description=f"Run `{run_id}` completed successfully.",
        color=GREEN,
        fields=[
            {"name": "Predictions", "value": str(predictions), "inline": True},
            {"name": "Bets placed", "value": str(bets), "inline": True},
            {"name": "Duration", "value": f"{duration:.1f}s", "inline": True},
        ],
    )
    _post_async(payload)


def notify_cycle_failure(run_id: str, error: str):
    description = (
        f"Run `{run_id}` failed.\n"
        f"```{error[:300]}```\n"
        "**What to do:**\n"
        "```bash\n"
        "# See full traceback\n"
        "journalctl -u bootball-runtime.service -n 60\n\n"
        "# Restart the runtime\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    )
    payload = _embed(title="❌ Cycle Failed", description=description, color=RED)
    _post_async(payload)


def notify_watchdog_alert(alert_type: str, detail: dict):
    titles = {
        "no_predictions": "⚠️ No Predictions",
        "no_bets": "⚠️ No Bets Placed",
        "repeated_crashes": "🔥 Repeated Crashes",
        "heartbeat_timeout": "💀 Runtime Stalled",
        "max_restarts_reached": "🚨 Max Restarts Reached",
        "risk_limit_breached": "🛑 Risk Limit Breached",
    }
    title = titles.get(alert_type, f"⚠️ {alert_type}")
    remediation = _REMEDIATION.get(alert_type, "")

    stat_lines = "\n".join(
        f"**{k}:** {v}" for k, v in detail.items()
        if k not in ("event_type", "timestamp") and v is not None
    )
    description = f"{stat_lines}\n\n{remediation}" if stat_lines else remediation

    payload = _embed(title=title, description=description[:3900], color=YELLOW)
    _post_async(payload)


def notify_bets_placed(bets: list):
    if not bets:
        return
    lines = []
    for b in bets[:10]:
        fixture = b.get("fixture", b.get("fixture_id", "?"))
        market = b.get("market", "?")
        outcome = b.get("outcome", "?")
        stake = b.get("stake", 0)
        odds = b.get("odds", 0)
        lines.append(f"• {fixture} | {market} {outcome} @ {odds} — SEK {stake:.2f}")
    if len(bets) > 10:
        lines.append(f"  ...and {len(bets) - 10} more")
    payload = _embed(
        title=f"🎯 {len(bets)} Bet(s) Placed",
        description="\n".join(lines),
        color=BLUE,
    )
    _post_async(payload)


def notify_settlement(won: int, lost: int, pnl: float):
    color = GREEN if pnl >= 0 else RED
    sign = "+" if pnl >= 0 else ""
    payload = _embed(
        title="🏁 Bets Settled",
        description=f"P/L: **{sign}SEK {pnl:.2f}**",
        color=color,
        fields=[
            {"name": "Won", "value": str(won), "inline": True},
            {"name": "Lost", "value": str(lost), "inline": True},
        ],
    )
    _post_async(payload)


_last_picks_hash: str = ""


def notify_top_picks():
    """Send the 3 best positive-EV picks only if they differ from the last send."""
    global _last_picks_hash
    try:
        from src.storage.db import get_session
        from sqlalchemy import text as sa_text
        import hashlib
        with get_session() as s:
            rows = s.execute(sa_text("""
                SELECT pr.market, pr.predicted_outcome, pr.ev, pr.odds_decimal,
                       pr.our_prob, pr.blended_prob,
                       ht.name, at.name, f.date
                FROM prediction_records pr
                JOIN fixtures f ON pr.fixture_id = f.id
                JOIN teams ht ON f.home_team_id = ht.id
                JOIN teams at ON f.away_team_id = at.id
                WHERE pr.ev > 0
                  AND pr.odds_decimal BETWEEN 1.6 AND 15.0
                  AND pr.settled = 0
                  AND f.date >= datetime('now')
                  AND pr.our_prob < (1.0 / pr.odds_decimal) * 2.5
                ORDER BY date(f.date) ASC, pr.ev DESC
                LIMIT 3
            """)).fetchall()
        if not rows:
            return

        # Deduplicate: skip if picks are identical to last send
        picks_key = "|".join(f"{r[6]}{r[7]}{r[0]}{r[1]}{r[3]}" for r in rows)
        picks_hash = hashlib.md5(picks_key.encode()).hexdigest()
        if picks_hash == _last_picks_hash:
            logger.info("[DISCORD] Top picks unchanged, skipping notification")
            return
        _last_picks_hash = picks_hash

        lines = []
        for r in rows:
            market, outcome, ev, odds, our_prob, blended_prob, home, away, date = r
            # Use blended_prob for display (matches the EV stored in pr.ev)
            model_pct = f"{blended_prob*100:.0f}%" if blended_prob else (f"{our_prob*100:.0f}%" if our_prob else "—")
            implied_pct = f"{(1/odds)*100:.0f}%" if odds else "—"
            # Use stored ev — it was computed as blended_prob * odds - 1 by the engine
            ev_str = f"+{ev*100:.1f}%" if ev else "—"
            odds_str = f"{odds:.2f}" if odds else "—"
            date_str = str(date)[:10] if date else ""
            fixture_str = f"{home} vs {away}"
            warning = " ⚠️ model high" if (ev and ev > 0.30) else ""
            lines.append(
                f"**{fixture_str}** · {date_str}\n"
                f"  {market.upper()} · **{outcome}** @ {odds_str} · EV {ev_str}{warning}\n"
                f"  Model {model_pct} vs market {implied_pct}"
            )
        payload = _embed(
            title="🔮 Top 3 Picks This Run",
            description="\n\n".join(lines),
            color=BLUE,
        )
        _post_async(payload)
    except Exception as e:
        logger.warning(f"[DISCORD] notify_top_picks failed: {e}")


def notify_model_change(market: str, old_label: str, new_label: str, metrics: dict):
    """Notify when a global model version is activated (retrain or recalibration)."""
    brier_before = metrics.get("brier_before")
    brier_after  = metrics.get("brier_after")
    reason       = metrics.get("reason", "manual")

    brier_str = ""
    if brier_before is not None and brier_after is not None:
        delta = brier_after - brier_before
        sign  = "+" if delta >= 0 else ""
        brier_str = f"\nBrier: `{brier_before:.4f}` → `{brier_after:.4f}` ({sign}{delta:.4f})"

    description = (
        f"Market: **{market.upper()}**\n"
        f"Version: `{old_label}` → `{new_label}`\n"
        f"Reason: {reason}"
        f"{brier_str}"
    )
    payload = _embed(title="🔄 Model Updated", description=description, color=BLUE)
    _post_async(payload)


def notify_calibration_change(
    market: str,
    league_id: int,
    league_name: str,
    version_label: str,
    brier_improvement: float | None,
    sample_size: int,
):
    """Notify when a league-specific calibration is fitted and activated."""
    if brier_improvement is not None:
        sign  = "+" if brier_improvement >= 0 else ""
        imp_str = f"{sign}{brier_improvement:.4f}"
        quality = "better than global" if brier_improvement > 0 else "no improvement over global (not activated)"
    else:
        imp_str = "—"
        quality = "no comparison data"

    description = (
        f"Market: **{market.upper()}**  |  League: **{league_name}** (id={league_id})\n"
        f"Version: `{version_label}`\n"
        f"Brier improvement: `{imp_str}`  — {quality}\n"
        f"Fitted on {sample_size:,} settled samples"
    )
    color = GREEN if (brier_improvement is not None and brier_improvement > 0) else YELLOW
    payload = _embed(title="📐 League Calibration Updated", description=description, color=color)
    _post_async(payload)


def notify_pipeline_error(stage: str, error: str):
    remediation = _PIPELINE_REMEDIATION.get(stage, (
        "```bash\n"
        "journalctl -u bootball-runtime.service -n 50\n"
        "systemctl restart bootball-runtime.service\n"
        "```"
    ))
    description = f"```{error[:300]}```\n{remediation}"
    payload = _embed(
        title=f"🔴 Pipeline Error — {stage}",
        description=description[:3900],
        color=RED,
    )
    _post_async(payload)


def send_test_message():
    payload = _embed(
        title="🤖 Bootball Connected",
        description=(
            "Discord notifications are active.\n\n"
            "**Useful commands:**\n"
            "```bash\n"
            "# Check service status\n"
            "systemctl status bootball-runtime.service bootball-web.service\n\n"
            "# View live logs\n"
            "journalctl -u bootball-runtime.service -f\n\n"
            "# Restart runtime\n"
            "systemctl restart bootball-runtime.service\n"
            "```"
        ),
        color=GREEN,
    )
    return _post(payload)


def notify_sanity_check(new_issues: list[dict], resolved_issues: list[dict]):
    """Send Discord notification for new and resolved sanity check issues."""
    if not new_issues and not resolved_issues:
        return

    lines = []
    if new_issues:
        lines.append(f"**{len(new_issues)} new issue(s) detected:**")
        for issue in new_issues[:10]:
            lines.append(f"• `{issue['check_type']}` — {issue['detail'][:120]}")
        if len(new_issues) > 10:
            lines.append(f"  ...and {len(new_issues) - 10} more")

    if resolved_issues:
        if lines:
            lines.append("")
        lines.append(f"**{len(resolved_issues)} issue(s) resolved:**")
        for issue in resolved_issues[:5]:
            lines.append(f"✓ `{issue['check_type']}` — {issue['detail'][:120]}")

    color = RED if new_issues else GREEN
    title = "🔍 Sanity Check Alert" if new_issues else "✅ Sanity Check — Issues Resolved"
    payload = _embed(title=title, description="\n".join(lines)[:3900], color=color)
    _post_async(payload)


def wire_to_event_bus():
    """Subscribe to the event bus and forward relevant events to Discord."""
    try:
        from src.alerts.event_bus import event_bus, Events

        def on_run_completed(event: dict):
            notify_cycle_success(
                predictions=event.get("count", event.get("predictions", 0)),
                bets=event.get("bets", 0),
                duration=event.get("duration", 0),
                run_id=event.get("run_id", "?"),
            )
            notify_top_picks()

        def on_alert(event: dict):
            alert_type = event.get("alert_type", "unknown")
            notify_watchdog_alert(alert_type, {k: v for k, v in event.items() if k != "event_type"})

        def on_policy_rejected(event: dict):
            notify_pipeline_error("POLICY_REJECTED", str(event))

        def on_kill_switch(event: dict):
            notify_pipeline_error("KILL_SWITCH", str(event))

        def on_risk_breach(event: dict):
            notify_watchdog_alert("risk_limit_breached", event)

        def on_bets_settled(event: dict):
            notify_settlement(
                won=event.get("won", 0),
                lost=event.get("lost", 0),
                pnl=event.get("total_pnl", 0),
            )

        def on_execution_summary(event: dict):
            bets = event.get("bets", [])
            if bets:
                notify_bets_placed(bets)

        event_bus.subscribe(Events.RUN_COMPLETED, on_run_completed)
        event_bus.subscribe(Events.ALERT_TRIGGERED, on_alert)
        event_bus.subscribe(Events.POLICY_REJECTED, on_policy_rejected)
        event_bus.subscribe(Events.KILL_SWITCH_TRIGGERED, on_kill_switch)
        event_bus.subscribe(Events.RISK_LIMIT_BREACHED, on_risk_breach)
        event_bus.subscribe(Events.BETS_SETTLED, on_bets_settled)
        event_bus.subscribe(Events.EXECUTION_SUMMARY, on_execution_summary)

        logger.info("[DISCORD] Wired to event bus")
    except Exception as e:
        logger.warning(f"[DISCORD] Could not wire to event bus: {e}")
