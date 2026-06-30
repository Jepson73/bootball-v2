"""
v2/routes/track_a_v2.py — Track A: prediction accuracy view (Task 3).

Per-market Brier score, log-loss, accuracy, calibration, and top-league breakdown.
Works entirely from settled PredictionRecords — no odds required.
"""
from flask import Blueprint
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_track_a_stats

bp_track_a = Blueprint("track_a_v2", __name__)

_MARKET_LABELS = {"h2h": "Match Result (H2H)", "btts": "Both Teams Score", "ou25": "Over/Under 2.5", "ou15": "Over/Under 1.5"}
_BRIER_BASELINE = {"h2h": 0.222, "btts": 0.25, "ou25": 0.25, "ou15": 0.25}


def _brier_colour(brier: float, market: str) -> str:
    baseline = _BRIER_BASELINE.get(market, 0.25)
    if brier < baseline - 0.01:
        return "green"
    if brier < baseline + 0.01:
        return "amber"
    return "red"


@bp_track_a.route("/track-a")
@require_auth
def track_a():
    stats = get_track_a_stats()

    if stats.get("total", 0) == 0:
        content = """
        <h1>Track A — Prediction Accuracy</h1>
        <div class="empty-state">
          <span class="empty-icon">📊</span>
          <h2>No settled predictions yet</h2>
          <p>Track A accuracy is computed from settled PredictionRecords.</p>
          <p>Scores will appear once fixtures with predictions reach FT status.</p>
        </div>
        """
        return page("Track A · Accuracy", content, active="track_a")

    # ── Top stats ────────────────────────────────────────────────────────────
    stat_cards = ""
    for mkt, v in stats["by_market"].items():
        colour = _brier_colour(v["brier"], mkt)
        stat_cards += f"""
        <div class="card">
          <div class="card-title">{_MARKET_LABELS.get(mkt, mkt)}</div>
          <div class="grid grid-3">
            <div class="stat">
              <span class="stat-value">{v['accuracy']*100:.1f}%</span>
              <span class="stat-label">Accuracy</span>
            </div>
            <div class="stat">
              <span class="stat-value"><span class="badge badge-{colour}">{v['brier']:.4f}</span></span>
              <span class="stat-label">Brier score ↓</span>
            </div>
            <div class="stat">
              <span class="stat-value">{v['logloss']:.4f}</span>
              <span class="stat-label">Log-loss ↓</span>
            </div>
          </div>
          <p style="font-size:11px;color:#8b949e;margin-top:8px">n={v['n']:,} &nbsp;|&nbsp;
             Baseline Brier (random): {_BRIER_BASELINE.get(mkt, 0.25):.3f}</p>
        </div>
        """

    # ── Calibration ──────────────────────────────────────────────────────────
    cal_html = ""
    for mkt, bins in stats.get("calibration", {}).items():
        rows = ""
        for b in bins:
            if b["n"] == 0:
                continue
            mp = b["mean_pred"] or 0
            ar = b["actual_rate"] or 0
            w = 200
            pred_w = int(mp * w)
            act_w = int(ar * w)
            diff_colour = "green" if abs(mp - ar) < 0.05 else ("amber" if abs(mp - ar) < 0.12 else "red")
            rows += f"""
            <div style="margin-bottom:8px">
              <div style="display:flex;align-items:center;gap:8px;font-size:11px;margin-bottom:2px">
                <span style="min-width:52px;color:#8b949e">{b['bin_label']}</span>
                <span style="color:#58a6ff">pred {mp*100:.0f}%</span>
                <span class="badge badge-{diff_colour}">actual {ar*100:.0f}%</span>
                <span style="color:#8b949e">n={b['n']}</span>
              </div>
              <div style="position:relative;height:6px;background:#21262d;border-radius:3px;width:{w}px">
                <div style="height:6px;width:{pred_w}px;background:#58a6ff55;border-radius:3px;position:absolute"></div>
                <div style="height:6px;width:{act_w}px;background:#3fb95077;border-radius:3px;position:absolute;top:0"></div>
              </div>
            </div>
            """
        if rows:
            cal_html += f"""
            <div class="card">
              <div class="card-title">Calibration — {_MARKET_LABELS.get(mkt, mkt)}</div>
              <p style="font-size:11px;color:#8b949e;margin-bottom:12px">Blue bar = mean predicted probability · Green bar = actual win rate per bin</p>
              {rows}
            </div>
            """

    # ── Top leagues ──────────────────────────────────────────────────────────
    league_rows = ""
    for league in stats["by_league"]:
        colour = "green" if league["brier"] < 0.235 else ("amber" if league["brier"] < 0.25 else "red")
        league_rows += f"""
        <tr>
          <td>{league['league_name']}</td>
          <td class="num">{league['n']:,}</td>
          <td class="num">{league['accuracy']*100:.1f}%</td>
          <td class="num"><span class="badge badge-{colour}">{league['brier']:.4f}</span></td>
        </tr>
        """

    league_table = f"""
    <div class="card">
      <div class="card-title">Top Leagues by Sample Size</div>
      <table>
        <thead><tr>
          <th>League</th><th class="num">Predictions</th>
          <th class="num">Accuracy</th><th class="num">Brier ↓</th>
        </tr></thead>
        <tbody>{league_rows}</tbody>
      </table>
    </div>
    """ if league_rows else ""

    content = f"""
    <div style="margin-bottom:24px">
      <h1>Track A — Prediction Accuracy</h1>
      <p>{stats['total']:,} settled predictions · scored against actual outcomes · no odds required</p>
    </div>

    <div style="margin-bottom:8px">
      <span class="badge badge-blue">Track A</span>
      <span style="color:#8b949e;font-size:12px;margin-left:8px">All leagues — accuracy is the primary metric regardless of odds availability</span>
    </div>

    {stat_cards}
    {cal_html}
    {league_table}

    <div class="card" style="background:#0d2340;border-color:#1f6feb">
      <div class="card-title" style="color:#58a6ff">Track B — Pending Pinnacle Data</div>
      <p>CLV / EV overlay requires Pinnacle odds snapshots. Collection clock hasn't started.
         Once ~150 days of snapshots are available, Track B will overlay EV signals here
         for Pinnacle-covered fixtures only.</p>
    </div>
    """

    return page("Track A · Accuracy", content, active="track_a")
