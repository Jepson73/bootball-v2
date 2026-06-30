"""
v2/routes/collection_v2.py — Forward-collection detail view (Task 5).

Shows odds snapshot time-series, bookmaker coverage, probe schedule.
Renders sensibly with 0 rows — honest empty state today, real data post-July 25.
"""
from flask import Blueprint
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_snapshot_summary, get_forward_fixtures, get_snapshot_timeseries, get_quota_log

bp_collection = Blueprint("collection_v2", __name__)

_FORWARD_LEAGUES = {
    648: ("Tasmania NPL", "🇦🇺", "Pinnacle probe July 3–4"),
    777: ("Norway 3.Div Gr.4", "🇳🇴", "Resumes July 25"),
    778: ("Norway 3.Div Gr.5", "🇳🇴", "Resumes July 25"),
    779: ("Norway 3.Div Gr.6", "🇳🇴", "Resumes July 25"),
}


@bp_collection.route("/collection")
@require_auth
def collection():
    snap = get_snapshot_summary()
    fixtures = get_forward_fixtures()
    timeseries = get_snapshot_timeseries()
    quota_log = get_quota_log(n=6)

    total = snap["total"]

    # ── Empty state ────────────────────────────────────────────────────────────
    if total == 0:
        league_rows = ""
        for lid, (name, flag, note) in _FORWARD_LEAGUES.items():
            count = sum(1 for f in fixtures if f["league_id"] == lid)
            status_badge = (
                f'<span class="badge badge-blue">{count} NS fixtures in DB</span>'
                if count > 0
                else '<span class="badge badge-gray">No fixtures yet (outside 7-day window)</span>'
            )
            league_rows += f"""
            <tr>
              <td>{flag} {name}</td>
              <td>{lid}</td>
              <td>{status_badge}</td>
              <td style="color:#8b949e;font-size:12px">{note}</td>
            </tr>
            """

        # Quota log
        quota_rows = ""
        for row in reversed(quota_log):
            quota_rows += f"""
            <tr>
              <td style="font-family:monospace;font-size:11px">{row.get('timestamp_utc','')}</td>
              <td>{row.get('event','')}</td>
              <td class="num">{int(row.get('calls_used',0)):,}</td>
              <td class="num">{int(row.get('calls_remaining',0)):,}</td>
            </tr>
            """

        content = f"""
        <div style="margin-bottom:24px">
          <h1>Forward Collection</h1>
          <p>Odds snapshots — open-to-close time-series for CLV measurement (Track B)</p>
        </div>

        <div class="status-block status-waiting" style="margin-bottom:24px">
          <h4>Clock Not Started — 0 Snapshots</h4>
          <p>The collection clock starts when the first Pinnacle odds row is written.
             Next opportunities: Tasmania probe July 3 at 06:00 UTC, and Norwegian
             3.Division resumption probe July 24 at 08:00 UTC. Until then this view
             shows the pipeline readiness state.</p>
        </div>

        <div class="card">
          <div class="card-title">Forward Leagues</div>
          <table>
            <thead><tr>
              <th>League</th><th>ID</th><th>DB State</th><th>Notes</th>
            </tr></thead>
            <tbody>{league_rows}</tbody>
          </table>
        </div>

        <div class="card">
          <div class="card-title">Why These Leagues?</div>
          <p style="margin-bottom:8px">These leagues were selected (Phase 10/11) for two reasons:</p>
          <ul style="font-size:13px;color:#8b949e;padding-left:20px;line-height:1.8">
            <li><strong style="color:#c9d1d9">Tasmania NPL</strong> — small, low-liquidity market.
                Pinnacle coverage unconfirmed (probing July 3–4).</li>
            <li><strong style="color:#c9d1d9">Norwegian 3.Division</strong> — summer break ends
                July 25. Confirmed 8 NS fixtures per group via direct API. Pinnacle coverage
                likely but unconfirmed until probe.</li>
          </ul>
          <p style="margin-top:8px">If Pinnacle is absent: <code>logs/soft_book_decision_needed.txt</code>
             is written and the user is flagged before any soft-book odds are recorded.</p>
        </div>

        <div class="card">
          <div class="card-title">API Quota Log (last 6 runs)</div>
          {"<table><thead><tr><th>Timestamp</th><th>Event</th><th class='num'>Used</th><th class='num'>Remaining</th></tr></thead><tbody>" + quota_rows + "</tbody></table>" if quota_rows else "<p>No quota log entries (logs/quota_log.csv)</p>"}
        </div>
        """
        return page("Collection", content, active="collection")

    # ── Live state (post-clock-start) ─────────────────────────────────────────
    by_league_rows = ""
    for entry in snap["by_league"]:
        by_league_rows += f"""
        <tr>
          <td>{entry['league_name']}</td>
          <td class="num">{entry['count']:,}</td>
        </tr>
        """

    ts_rows = ""
    if timeseries:
        max_count = max(r["count"] for r in timeseries) or 1
        for row in timeseries[-30:]:
            bar_w = int(row["count"] / max_count * 200)
            ts_rows += f"""
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
              <span style="font-family:monospace;font-size:11px;color:#8b949e;min-width:80px">{row['day']}</span>
              <div style="height:12px;width:{bar_w}px;background:#58a6ff55;border-radius:2px"></div>
              <span style="font-size:11px;color:#8b949e">{row['count']}</span>
            </div>
            """

    days_needed = 150
    days_acc = snap["days_accumulated"]
    pct = min(100, round(days_acc / days_needed * 100))

    content = f"""
    <div style="margin-bottom:24px">
      <h1>Forward Collection</h1>
      <p>{total:,} snapshots · {days_acc} days accumulated ·
         CLV-usable threshold: ~{days_needed} days ({pct}% complete)</p>
    </div>

    <div class="grid grid-3" style="margin-bottom:24px">
      <div class="stat">
        <span class="stat-value">{total:,}</span>
        <span class="stat-label">Total snapshots</span>
      </div>
      <div class="stat">
        <span class="stat-value">{days_acc}</span>
        <span class="stat-label">Days accumulated</span>
      </div>
      <div class="stat">
        <span class="stat-value">{pct}%</span>
        <span class="stat-label">Toward CLV threshold</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Snapshots by League</div>
      <table><thead><tr><th>League</th><th class="num">Snapshots</th></tr></thead>
      <tbody>{by_league_rows}</tbody></table>
    </div>

    {"<div class='card'><div class='card-title'>Daily Snapshot Count (last 30 days)</div>" + ts_rows + "</div>" if ts_rows else ""}
    """

    return page("Collection", content, active="collection")
