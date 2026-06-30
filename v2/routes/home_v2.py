"""
v2/routes/home_v2.py — Status / home view (Task 2).

Shows honest project state:
- Betting thesis: CLOSED
- Forward-collection: live status
- Track A: prediction engine summary
"""
from flask import Blueprint
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_snapshot_summary, get_latest_quota, get_track_a_stats

bp_home = Blueprint("home_v2", __name__)

_PROBE_SCHEDULE = [
    ("2026-07-03 06:00 UTC", "Tasmania NPL (league 648)", "~25h before July 4 kickoffs", "blue"),
    ("2026-07-04 06:00 UTC", "Tasmania NPL (league 648)", "~22h before July 5 kickoff", "blue"),
    ("2026-07-18", "Norwegian 3.Div (777/778/779)", "fixtures enter 7-day window — daily_run.py fetches", "amber"),
    ("2026-07-24 08:00 UTC", "Norwegian 3.Div (777/778/779)", "~30h before July 25 resumption", "blue"),
]


@bp_home.route("/")
@require_auth
def home():
    snap = get_snapshot_summary()
    quota = get_latest_quota()
    track_a = get_track_a_stats()

    # ── Betting thesis block ───────────────────────────────────────────────────
    betting_block = """
    <div class="status-block status-closed">
      <h4>Betting Thesis — CLOSED</h4>
      <p>Phase 8 (STOP_ENTIRELY verdict): no independent edge found for the target
         leagues after multi-phase research. The coordinator pipeline runs with
         <code>bot_enabled=False</code> — prediction cycles continue for Track A
         calibration but no capital is deployed.</p>
    </div>
    """

    # ── Forward-collection block ──────────────────────────────────────────────
    total_snaps = snap["total"]
    days_acc = snap["days_accumulated"]
    days_needed = 150  # ~5 months to CLV-usable
    pct = min(100, round(days_acc / days_needed * 100))

    if total_snaps == 0:
        snap_status_html = """
        <div class="status-block status-waiting">
          <h4>Forward Collection — Awaiting Clock Start</h4>
          <p>0 odds snapshots captured. Clock starts when the first Pinnacle odds are
             written — next opportunities: Tasmania July 3–4 probe and Norway July 25
             resumption. No action needed; probes are scheduled via cron.</p>
        </div>
        """
    else:
        snap_status_html = f"""
        <div class="status-block status-active">
          <h4>Forward Collection — LIVE</h4>
          <p>{total_snaps:,} snapshots captured. {days_acc} of ~{days_needed} days
             accumulated ({pct}% toward CLV-usable threshold).</p>
        </div>
        """

    # Probe schedule table
    probe_rows = "".join(
        f'<div class="probe-row">'
        f'<span class="probe-date">{d}</span>'
        f'<span class="badge badge-{colour}">{league}</span>'
        f'<span style="color:#8b949e;font-size:12px;margin-left:6px">{note}</span>'
        f'</div>'
        for d, league, note, colour in _PROBE_SCHEDULE
    )

    # Snapshot progress bar
    progress_html = f"""
    <div style="margin:12px 0">
      <div style="display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-bottom:4px">
        <span>Days accumulated</span><span>{days_acc} / {days_needed}</span>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div>
    </div>
    """ if total_snaps > 0 else ""

    # Quota
    if quota:
        used_pct = round(quota["calls_used"] / quota["daily_limit"] * 100)
        quota_html = f"""
        <div class="stat" style="text-align:left;padding:10px 14px">
          <span style="font-size:11px;color:#8b949e">Last quota check: {quota['timestamp']}</span><br>
          <span style="font-size:13px;color:#e6edf3;font-weight:600">{quota['calls_remaining']:,}</span>
          <span style="color:#8b949e;font-size:11px"> remaining / {quota['daily_limit']:,} daily
          ({used_pct}% used)</span>
        </div>
        """
    else:
        quota_html = '<p style="font-size:12px;color:#8b949e">No quota log entries yet (logs/quota_log.csv)</p>'

    # ── Track A summary ───────────────────────────────────────────────────────
    if track_a.get("total", 0) > 0:
        market_badges = " ".join(
            f'<span class="badge badge-blue">{m} · {v["accuracy"]*100:.1f}%</span>'
            for m, v in track_a["by_market"].items()
        )
        ta_html = f"""
        <div class="status-block status-active">
          <h4>Track A — Prediction Engine Live</h4>
          <p>{track_a['total']:,} settled predictions across {len(track_a['by_league'])}+ leagues.<br>
          {market_badges}</p>
        </div>
        """
    else:
        ta_html = """
        <div class="status-block status-waiting">
          <h4>Track A — Awaiting Settled Predictions</h4>
          <p>No settled predictions yet.</p>
        </div>
        """

    content = f"""
    <div style="margin-bottom:24px">
      <h1>Project Status</h1>
      <p>Bootball V2 — two-track evaluation system. Honest state as of today.</p>
    </div>

    <div class="grid grid-3" style="margin-bottom:24px">
      <div class="stat">
        <span class="stat-value">{'CLOSED' if True else 'OPEN'}</span>
        <span class="stat-label">Betting thesis</span>
      </div>
      <div class="stat">
        <span class="stat-value">{total_snaps:,}</span>
        <span class="stat-label">Odds snapshots captured</span>
      </div>
      <div class="stat">
        <span class="stat-value">{track_a.get('total', 0):,}</span>
        <span class="stat-label">Settled predictions (Track A)</span>
      </div>
    </div>

    {betting_block}
    {ta_html}

    <div class="card">
      <div class="card-title">Forward Collection</div>
      {snap_status_html}
      {progress_html}
      <h3 style="margin-top:16px">Probe Schedule</h3>
      {probe_rows}
    </div>

    <div class="card">
      <div class="card-title">API Quota</div>
      {quota_html}
    </div>
    """

    return page("Status", content, active="home")
