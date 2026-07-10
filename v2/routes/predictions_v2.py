"""
v2/routes/predictions_v2.py — Per-fixture predictions view (Task 4).

Shows model probabilities for upcoming fixtures with explicit outcome labels.
Track B (EV/CLV overlay) is shown only where Pinnacle odds exist.

H2H vector: prob_home/prob_draw/prob_away populated for all upcoming fixtures
after the Phase 13c backfill (2026-07-01). Settled historical records remain
NULL (re-running the model on past fixtures would leak current standings).
Fall back to predicted_outcome + our_prob scalar for NULL-vector records.
"""
from flask import Blueprint
from v2.auth_v2 import require_auth
from v2.templates_v2 import page
from v2.db_v2 import get_predictions_for_upcoming

bp_predictions = Blueprint("predictions_v2", __name__)

# Map h2h predicted_outcome codes → display labels
_H2H_LABELS = {"1": "Home", "X": "Draw", "2": "Away", "H": "Home", "D": "Draw", "A": "Away"}


def _price_tag(val: float | None) -> str:
    """Format a decimal odds value as a subtle inline span, or empty string."""
    if val is None:
        return ""
    return f'<span style="color:#6e7681;font-size:10px"> ({val:.2f})</span>'


def _book_tag(book: str | None, has_price: bool) -> str:
    """Compact bookmaker label shown once per cell when soft prices are present."""
    if not book or not has_price:
        return ""
    return (
        f'<br><span style="color:#484f58;font-size:9px" title="indicative — not sharp-verified">'
        f'{book}</span>'
    )


def _format_market(market: dict) -> str:
    """
    Return a labelled probability string for one market cell.

    H2H full vector (stacked):
      H 56% (1.78)
      D 24% (3.60)
      A 20% (4.20)
          Bet365
    H2H scalar:      Home 54% (1.78) *
    ou25/ou15/btts:  Over 55% (1.90)  [Bet365]

    Soft prices are per-outcome from fixture_odds (not prediction_records.odds_decimal).
    Track B (EV) is Pinnacle-only and unchanged — soft prices never feed EV logic.

    Phase 33 Task 4: shows served_prob (live-recalibrated for full/NULL tiers,
    raw for the thin tiers per _CTX_BADGE below) rather than raw our_prob --
    see v2/db_v2.py::_serve_prob(). market["our_prob"] is still present in the
    dict for anyone reading it directly, just not what's rendered here.
    """
    m = market["market"]
    p = market.get("served_prob")
    outcome = market.get("predicted_outcome") or ""

    if p is None:
        return '<span style="color:#8b949e">—</span>'

    pct = round(p * 100)
    book = market.get("soft_book")

    if m == "h2h":
        ph = market.get("served_prob_home")
        pd_ = market.get("served_prob_draw")
        pa = market.get("served_prob_away")
        if ph is not None and pd_ is not None and pa is not None:
            def _col(v):
                c = "#3fb950" if v >= 0.5 else ("#d29922" if v >= 0.35 else "#8b949e")
                return f'<span style="color:{c};font-weight:600">{round(v*100)}%</span>'
            sh = _price_tag(market.get("soft_home"))
            sd = _price_tag(market.get("soft_draw"))
            sa = _price_tag(market.get("soft_away"))
            has_price = any([market.get("soft_home"), market.get("soft_draw"), market.get("soft_away")])
            return (
                f'H {_col(ph)}{sh}<br>'
                f'D {_col(pd_)}{sd}<br>'
                f'A {_col(pa)}{sa}'
                + _book_tag(book, has_price)
            )
        else:
            label = _H2H_LABELS.get(str(outcome), outcome or "?")
            colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
            if outcome in ("1", "H"):
                price = market.get("soft_home")
            elif outcome in ("X", "D"):
                price = market.get("soft_draw")
            else:
                price = market.get("soft_away")
            return (
                f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'
                + _price_tag(price)
                + f'<span style="color:#8b949e;font-size:10px" title="Full 3-way vector not stored"> *</span>'
                + _book_tag(book, bool(price))
            )

    elif m in ("ou25", "ou15"):
        label = str(outcome).capitalize() if outcome else "?"
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        is_over = outcome.lower() == "over"
        if m == "ou25":
            price = market.get("soft_over") if is_over else market.get("soft_under")
        else:
            price = market.get("soft_over15") if is_over else market.get("soft_under15")
        return (
            f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'
            + _price_tag(price)
            + _book_tag(book, bool(price))
        )

    elif m == "btts":
        label = str(outcome).capitalize() if outcome else "?"
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        price = market.get("soft_btts_yes") if outcome.lower() == "yes" else market.get("soft_btts_no")
        return (
            f'<span style="color:{colour};font-weight:600">{label} {pct}%</span>'
            + _price_tag(price)
            + _book_tag(book, bool(price))
        )

    else:
        colour = "#3fb950" if pct >= 55 else ("#d29922" if pct >= 45 else "#8b949e")
        return f'<span style="color:{colour};font-weight:600">{pct}%</span>'


# Phase 33 Task 3: elo_both/elo_partial/flat_prior/national_elo are written by
# scripts/generate_gap_predictions.py and generate_wc_predictions.py, neither of
# which ever calls LeagueCalibrationEngine.apply() -- calibrated_prob is NULL for
# 100% of these rows. That's the right call (settled volume per market/tier tops
# out at 61 rows, well under fitting a calibration honestly), but it was silent.
# Badge titles say so explicitly now so "uncalibrated" is never just an absence.
_UNCALIBRATED_NOTE = " — raw model probability, not calibrated (too few settled samples to fit one honestly)"
_CTX_BADGE = {
    "full":         '<span class="badge badge-blue" title="Normal standings-based prediction">Full</span>',
    "elo_both":     f'<span class="badge badge-gray" title="Club Elo — both teams rated{_UNCALIBRATED_NOTE}">Elo (raw)</span>',
    "elo_partial":  f'<span class="badge" style="background:#5a3e00;color:#d29922;border:1px solid #6a4e00" title="Club Elo — one team unrated (1500 default){_UNCALIBRATED_NOTE}">Elo~ (raw)</span>',
    "flat_prior":   f'<span class="badge badge-gray" title="Flat prior H43/D27/A30 — no meaningful rating data{_UNCALIBRATED_NOTE}">Prior (raw)</span>',
    "national_elo": f'<span class="badge badge-blue" title="National-team Elo{_UNCALIBRATED_NOTE}">Nat (raw)</span>',
}


def _ctx_badge(data_context: str | None) -> str:
    if data_context is None:
        return ""
    return _CTX_BADGE.get(data_context, "")


def _ev_badge(ev: float | None, is_pinnacle: bool) -> str:
    """
    Track B cell — Pinnacle EV only.
    Soft-book odds belong in their respective market cells (_soft_odds_tag), not here.
    EV is never computed or displayed against soft-book odds.
    """
    if not is_pinnacle:
        return '<span class="badge badge-gray">No sharp odds</span>'
    if ev is None:
        return '<span class="badge badge-gray">No EV</span>'
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

            # data_context badge — use the h2h record's context (primary market)
            h2h_ctx = mkt_map.get("h2h", {}).get("data_context")
            ctx_tag = _ctx_badge(h2h_ctx)

            # Sharp indicator: Pinnacle from forward-collection snapshots (future) OR
            # from prediction_records.bookmaker (current pipeline).
            h2h_ev = mkt_map.get("h2h", {})
            is_pinnacle = h2h_ev.get("is_pinnacle", False) or h2h_ev.get("has_pinnacle", False)
            pinnacle_badge = (
                '<span class="badge badge-green">Pinnacle</span>'
                if is_pinnacle
                else '<span class="badge badge-gray">No sharp odds</span>'
            )

            h2h_cell = _format_market(mkt_map["h2h"]) if "h2h" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'
            ou25_cell = _format_market(mkt_map["ou25"]) if "ou25" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'
            btts_cell = _format_market(mkt_map["btts"]) if "btts" in mkt_map else '<span style="color:#8b949e">&mdash;</span>'

            track_b = _ev_badge(h2h_ev.get("ev"), is_pinnacle)

            rows_html += f"""
            <tr>
              <td style="font-family:monospace;font-size:11px;color:#8b949e;white-space:nowrap">{time_str}</td>
              <td>
                <span style="color:#e6edf3;font-weight:500">{home}</span>
                <span style="color:#8b949e;margin:0 4px">vs</span>
                <span style="color:#c9d1d9">{away}</span>
                <br><span style="color:#8b949e;font-size:10px">{league}</span>
                {"<br>" + ctx_tag if ctx_tag else ""}
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
