"""
v2/routes/predictions_v2.py — Per-fixture predictions view (Task 4).

Shows model probabilities for upcoming fixtures with explicit outcome labels.
Track B (EV/CLV overlay) is shown only where Pinnacle odds exist.

H2H data gap: prob_home/prob_draw/prob_away are NULL for all current records
(columns added in Phase 11b but never populated by the prediction engine).
We fall back to labelling predicted_outcome + our_prob scalar ("Home 54%").
"""
from flask import Blueprint
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_predictions_for_upcoming

bp_predictions = Blueprint("predictions_v2", __name__)

# Map h2h predicted_outcome codes → display labels
_H2H_LABELS = {"1": "Home", "X": "Draw", "2": "Away", "H": "Home", "D": "Draw", "A": "Away"}


def _format_market(market: dict) -> str:
    """
    Return a labelled probability string for one market row.
    Every probability states the outcome it refers to.

    H2H with full vector: "H 56% / D 24% / A 20%"
    H2H scalar fallback:  "Home 54%" (+ note that vector is missing)
    ou25/ou15/btts:       "Over 52%" / "Yes 86%" — from predicted_outcome directly
    """
    m = market["market"]
    p = market["our_prob"]
    outcome = market.get("predicted_outcome") or ""

    if p is None:
        return '<span style="color:#8b949e">—</span>'

    pct = round(p * 100)

    if m == "h2h":
        ph = market.get("prob_home")
        pd_ = market.get("prob_draw")
        pa = market.get("prob_away")
        if ph is not None and pd_ is not None and pa is not None:
            # Full 3-way vector available
            def _col(v):
                c = "#3fb950" if v >= 0.5 else ("#d29922" if v >= 0.35 else "#8b949e")
                return f'<span style="color:{c};font-weight:600">{round(v*100)}%</span>'
            return (
                f'H {_col(ph)} &nbsp;'
                f'D {_col(pd_)} &nbsp;'
                f'A {_col(pa)}'
            )
        else:
            # Scalar fallback — label the predicted side
            label = _H2H_LABELS.get(str(outcome), outcome or "?")
            colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
            return (
                f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'
                f'<span style="color:#8b949e;font-size:10px" title="Full 3-way vector not stored — showing predicted side only"> *</span>'
            )

    elif m in ("ou25", "ou15"):
        # predicted_outcome is "Over" or "Under"
        label = str(outcome).capitalize() if outcome else "?"
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        return f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'

    elif m == "btts":
        # predicted_outcome is "Yes" or "No"
        label = str(outcome).capitalize() if outcome else "?"
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        return f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'

    else:
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        return f'<span style="color:{colour};font-weight:600">{pct}%</span>'


def _ev_badge(ev: float | None, has_pinnacle: bool) -> str:
    if not has_pinnacle:
        return '<span class="badge badge-gray">No Pinnacle</span>'
    if ev is None:
        return '<span class="badge badge-gray">No odds</span>'
    if ev > 0.02:
        return f'<span class="badge badge-green">EV +{ev*100:.1f}%</span>'
    if ev > -0.02:
        return f'<span class="badge badge-amber">EV {ev*100:.1f}%</span>'
    return f'<span class="badge badge-red">EV {ev*100:.1f}%</span>'


@bp_predictions.route("/predictions")
@require_auth
def predictions():
    fixtures = get_predictions_for_upcoming()

    if not fixtures:
        content = """
        <h1>Predictions</h1>
        <div class="empty-state">
          <span class="empty-icon">&#128302;</span>
          <h2>No upcoming predictions</h2>
          <p>Model predictions appear here for NS fixtures with active PredictionRecords.</p>
          <p>The prediction engine runs every 20 minutes against covered leagues.</p>
        </div>
        """
        return page("Predictions", content, active="predictions")

    # Count H2H records missing the full vector (data-gap reporting)
    h2h_total = sum(1 for f in fixtures for m in f["markets"] if m["market"] == "h2h")
    h2h_missing_vec = sum(
        1 for f in fixtures for m in f["markets"]
        if m["market"] == "h2h" and m.get("prob_home") is None
    )

    # Group by date
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for f in fixtures:
        d = f["date"].strftime("%Y-%m-%d") if f["date"] else "Unknown"
        by_date[d].append(f)

    rows_html = ""
    for date_str in sorted(by_date.keys()):
        day_fixtures = by_date[date_str]
        rows_html += f"""
        <tr style="background:#0d1117">
          <td colspan="7" style="padding:8px 10px;color:#8b949e;font-size:11px;
              font-weight:700;text-transform:uppercase;letter-spacing:.05em">
            {date_str} &mdash; {len(day_fixtures)} fixtures
          </td>
        </tr>
        """
        for f in day_fixtures:
            time_str = f["date"].strftime("%H:%M") if f["date"] else "&mdash;"
            home = f.get("home_team") or "?"
            away = f.get("away_team") or "?"
            league = f["league_name"] or f"League {f['league_id']}"

            mkt_map: dict[str, dict] = {}
            for m in f.get("markets", []):
                mkt_map[m["market"]] = m

            has_any_pinnacle = any(v.get("has_pinnacle") for v in mkt_map.values())
            pinnacle_badge = (
                '<span class="badge badge-green">Pinnacle</span>'
                if has_any_pinnacle
                else '<span class="badge badge-gray">No sharp odds</span>'
            )

            h2h_cell = _format_market(mkt_map["h2h"]) if "h2h" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'
            ou25_cell = _format_market(mkt_map["ou25"]) if "ou25" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'
            btts_cell = _format_market(mkt_map["btts"]) if "btts" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'

            h2h_ev = mkt_map.get("h2h", {})
            track_b = _ev_badge(h2h_ev.get("ev"), h2h_ev.get("has_pinnacle", False))

            rows_html += f"""
            <tr>
              <td style="font-family:monospace;font-size:11px;color:#8b949e;white-space:nowrap">{time_str}</td>
              <td>
                <span style="color:#e6edf3;font-weight:500">{home}</span>
                <span style="color:#8b949e;margin:0 4px">vs</span>
                <span style="color:#c9d1d9">{away}</span>
                <br><span style="color:#8b949e;font-size:10px">{league}</span>
              </td>
              <td>{h2h_cell}</td>
              <td>{ou25_cell}</td>
              <td>{btts_cell}</td>
              <td>{track_b} {pinnacle_badge}</td>
            </tr>
            """

    # Data-gap notice for H2H vector
    if h2h_missing_vec > 0:
        vec_notice = f"""
        <div class="card" style="background:#1a1a0a;border-color:#3a2a0a;margin-bottom:16px">
          <div class="card-title" style="color:#d29922">H2H vector gap ({h2h_missing_vec}/{h2h_total} predictions)</div>
          <p>The full three-way probability vector (prob_home / prob_draw / prob_away) was added
             to the schema in Phase 11b but has not been written by the prediction engine for any
             existing records. H2H cells marked <span style="color:#8b949e">*</span> show the
             <em>predicted side only</em> (e.g. "Home 54%") derived from
             <code>predicted_outcome + our_prob</code>. This is a data-pipeline gap, not a
             display-layer gap. To fix: the prediction engine must persist the full softmax
             vector when saving PredictionRecords.</p>
        </div>
        """
    else:
        vec_notice = ""

    content = f"""
    <div style="margin-bottom:16px">
      <h1>Predictions</h1>
      <p>{len(fixtures)} fixtures &middot; next 7 days &middot; home advantage labelled on left</p>
    </div>

    <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="badge badge-blue">Track A</span>
      <span style="color:#8b949e;font-size:12px">Outcome-labelled probabilities scored against results regardless of odds</span>
      <span style="margin-left:8px" class="badge badge-gray">Track B</span>
      <span style="color:#8b949e;font-size:12px">EV overlay (Pinnacle-gated)</span>
    </div>

    {vec_notice}

    <div class="card" style="padding:0;overflow:hidden">
      <table>
        <thead>
          <tr>
            <th>Time UTC</th>
            <th>Match</th>
            <th>H2H</th>
            <th>O/U 2.5</th>
            <th>BTTS</th>
            <th>Track B</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <div class="card" style="background:#1a1a0a;border-color:#3a2a0a;margin-top:16px">
      <div class="card-title" style="color:#d29922">Track B note</div>
      <p>EV signals are market-relative against Pinnacle closing odds only.
         Analytical only &mdash; the betting thesis is CLOSED (Phase 8).</p>
    </div>
    """

    return page("Predictions", content, active="predictions")
