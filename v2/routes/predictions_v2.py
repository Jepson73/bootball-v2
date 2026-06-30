"""
v2/routes/predictions_v2.py — Per-fixture predictions view (Task 4).

Shows model probabilities for upcoming fixtures.
Track B (EV/CLV overlay) is shown only where Pinnacle odds exist — never blocks
the Track A prediction on absence of odds.
"""
from flask import Blueprint, request
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_predictions_for_upcoming

bp_predictions = Blueprint("predictions_v2", __name__)

_MARKET_LABELS = {"h2h": "H2H", "btts": "BTTS", "ou25": "O/U 2.5", "ou15": "O/U 1.5"}


def _prob_bar(p: float | None) -> str:
    if p is None:
        return '<span style="color:#8b949e">—</span>'
    pct = round(p * 100)
    colour = "#3fb950" if pct >= 60 else ("#d29922" if pct >= 40 else "#8b949e")
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
          <span class="empty-icon">🔮</span>
          <h2>No upcoming predictions</h2>
          <p>Model predictions appear here for NS fixtures with active PredictionRecords.</p>
          <p>The prediction engine runs every 20 minutes against covered leagues.</p>
        </div>
        """
        return page("Predictions", content, active="predictions")

    # Group by date
    from collections import defaultdict
    from datetime import datetime
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
            {date_str} — {len(day_fixtures)} fixtures
          </td>
        </tr>
        """
        for f in day_fixtures:
            time_str = f["date"].strftime("%H:%M") if f["date"] else "—"
            league = f["league_name"] or f"League {f['league_id']}"

            market_cells = {m: {"prob": None, "ev": None, "has_pinnacle": False}
                            for m in ["h2h", "ou25", "btts", "ou15"]}
            for m in f.get("markets", []):
                key = m["market"]
                if key in market_cells:
                    market_cells[key] = {
                        "prob": m["our_prob"],
                        "ev": m["ev"],
                        "has_pinnacle": m["has_pinnacle"],
                    }

            has_any_pinnacle = any(v["has_pinnacle"] for v in market_cells.values())
            pinnacle_badge = ('<span class="badge badge-green">Pinnacle</span>'
                              if has_any_pinnacle
                              else '<span class="badge badge-gray">No sharp odds</span>')

            rows_html += f"""
            <tr>
              <td style="font-family:monospace;font-size:11px;color:#8b949e">{time_str}</td>
              <td>{league}</td>
              <td>fix #{f['fixture_id']}</td>
              <td class="num">{_prob_bar(market_cells['h2h']['prob'])}</td>
              <td class="num">{_prob_bar(market_cells['ou25']['prob'])}</td>
              <td class="num">{_prob_bar(market_cells['btts']['prob'])}</td>
              <td>{pinnacle_badge} {_ev_badge(market_cells['h2h']['ev'], market_cells['h2h']['has_pinnacle'])}</td>
            </tr>
            """

    content = f"""
    <div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">
      <div>
        <h1>Predictions</h1>
        <p>{len(fixtures)} fixtures with model predictions in next 7 days</p>
      </div>
    </div>

    <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
      <span class="badge badge-blue">Track A</span>
      <span style="color:#8b949e;font-size:12px">Model probability — scored against outcomes regardless of odds</span>
      <span style="margin-left:8px" class="badge badge-gray">Track B</span>
      <span style="color:#8b949e;font-size:12px">EV shown only where Pinnacle odds exist (Pinnacle-gated)</span>
    </div>

    <div class="card" style="padding:0;overflow:hidden">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>League</th><th>Fixture</th>
            <th class="num">H2H</th><th class="num">O/U 2.5</th><th class="num">BTTS</th>
            <th>Track B</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <div class="card" style="background:#1a1a0a;border-color:#3a2a0a;margin-top:16px">
      <div class="card-title" style="color:#d29922">Track B note</div>
      <p>EV signals shown above are market-relative against Pinnacle odds only.
         These are analytical — not betting signals. The betting thesis is CLOSED (Phase 8).</p>
    </div>
    """

    return page("Predictions", content, active="predictions")
