"""
v2/routes/explorer_v2.py — Prediction Explorer (Phase 14).

Browsable, filterable, paginated view of ALL predictions.
Per-fixture rows with 4 market columns.  Track A accuracy (won/lost) shown on
settled rows; Track B (EV/CLV) is not exposed here — this is a Track-A surface.
"""
from __future__ import annotations

import json

from flask import Blueprint, request

from v2.auth_v2 import require_auth
from v2.db_v2 import get_explorer_data, get_league_country_map
from v2.templates_v2 import page

bp_explorer = Blueprint("explorer_v2", __name__)

_H2H = {"1": "Home", "X": "Draw", "2": "Away"}

_LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "INT"}
_VOID_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO", "SUSP"}

def _status_badge(status: str | None) -> str:
    """Inline badge for any fixture that isn't NS or FT — empty string otherwise."""
    if not status or status in ("NS", "FT", "AET", "PEN"):
        return ""
    if status in _LIVE_STATUSES:
        label = {"HT": "HT", "INT": "INT"}.get(status, "LIVE")
        return (
            f'<span style="background:#1a3a1a;color:#3fb950;border:1px solid #2a6a2a;'
            f'border-radius:3px;font-size:9px;padding:1px 4px;margin-left:4px;'
            f'font-weight:700">{label}</span>'
        )
    # Void / problematic statuses
    _LABELS = {
        "PST": "POSTPONED", "CANC": "CANCELLED", "ABD": "ABANDONED",
        "AWD": "AWARDED", "WO": "WALKOVER", "SUSP": "SUSPENDED",
    }
    label = _LABELS.get(status, status)
    return (
        f'<span style="background:#1a1a2a;color:#8b949e;border:1px solid #30363d;'
        f'border-radius:3px;font-size:9px;padding:1px 4px;margin-left:4px;'
        f'font-weight:700" title="Predictions will not settle automatically">{label}</span>'
    )


def _ex_price(val: float | None) -> str:
    if val is None:
        return ""
    return f'<span style="color:#6e7681;font-size:9px"> ({val:.2f})</span>'


def _mkt_cell(m: dict | None) -> str:
    """Render one market <td> for the explorer table."""
    if not m:
        return '<td style="color:#8b949e;text-align:center;padding:6px 10px">—</td>'

    p = m.get("our_prob")
    if p is None:
        return '<td style="color:#8b949e;text-align:center;padding:6px 10px">—</td>'

    pct = round(p * 100)
    mkt = m.get("market", "")
    outcome = str(m.get("predicted_outcome") or "")
    settled = m.get("settled", False)
    won = m.get("won")
    book = m.get("soft_book")

    if mkt == "h2h":
        label = _H2H.get(outcome, outcome or "?")
        ph = m.get("prob_home")
        pd_ = m.get("prob_draw")
        pa = m.get("prob_away")
        if ph is not None and pd_ is not None and pa is not None:
            star = ""
            sh = _ex_price(m.get("soft_home"))
            sd = _ex_price(m.get("soft_draw"))
            sa = _ex_price(m.get("soft_away"))
            has_price = any([m.get("soft_home"), m.get("soft_draw"), m.get("soft_away")])
            bk = (
                f'<br><span style="color:#484f58;font-size:8px" title="indicative">{book}</span>'
                if book and has_price else ""
            )
            dist = (
                f'<br><small style="color:#636e7b;font-size:0.78em;line-height:1.6">'
                f'H&nbsp;{round(ph*100)}%{sh}<br>'
                f'D&nbsp;{round(pd_*100)}%{sd}<br>'
                f'A&nbsp;{round(pa*100)}%{sa}'
                f'{bk}</small>'
            )
        else:
            star = " *"
            if outcome in ("1", "H"):
                price = m.get("soft_home")
            elif outcome in ("X", "D"):
                price = m.get("soft_draw")
            else:
                price = m.get("soft_away")
            dist = _ex_price(price)
            if book and price:
                dist += f'<span style="color:#484f58;font-size:8px" title="indicative"> {book}</span>'
    else:
        label = outcome.capitalize() if outcome else "?"
        star = ""
        # Per-outcome price for binary markets
        if mkt in ("ou25", "ou15"):
            is_over = outcome.lower() == "over"
            if mkt == "ou25":
                price = m.get("soft_over") if is_over else m.get("soft_under")
            else:
                price = m.get("soft_over15") if is_over else m.get("soft_under15")
        elif mkt == "btts":
            price = m.get("soft_btts_yes") if outcome.lower() == "yes" else m.get("soft_btts_no")
        else:
            price = None
        dist = _ex_price(price)
        if book and price:
            dist += f'<span style="color:#484f58;font-size:8px" title="indicative"> {book}</span>'

    color = "#3fb950" if pct >= 60 else ("#d29922" if pct >= 50 else "#8b949e")
    pred = f'<span style="color:{color};font-weight:600">{label}&nbsp;{pct}%{star}</span>{dist}'

    if settled and won is not None:
        if won:
            result = '<span style="color:#3fb950;margin-left:4px" title="Correct">&#10003;</span>'
        else:
            actual = str(m.get("actual_outcome") or "")
            if mkt == "h2h":
                act_label = _H2H.get(actual, actual)
            else:
                act_label = actual.capitalize() if actual else "?"
            result = (
                f'<span style="color:#f85149;margin-left:4px"'
                f' title="Wrong — actual: {act_label}">&#10007;</span>'
            )
    else:
        result = ""

    return f'<td style="white-space:nowrap;padding:6px 10px">{pred}{result}</td>'


def _build_pagination(page_num: int, total_pages: int, url_fn) -> str:
    if total_pages <= 1:
        return ""

    parts = []

    if page_num > 0:
        parts.append(f'<a href="{url_fn(page_num - 1)}" class="pg-btn">&#8592; Prev</a>')
    else:
        parts.append('<span class="pg-btn" style="opacity:.4">&#8592; Prev</span>')

    # Page number window (show at most 7 page buttons)
    start = max(0, page_num - 3)
    end = min(total_pages, start + 7)
    start = max(0, end - 7)

    if start > 0:
        parts.append(f'<a href="{url_fn(0)}" class="pg-btn">1</a>')
        if start > 1:
            parts.append('<span class="pg-btn" style="opacity:.4">…</span>')

    for p in range(start, end):
        if p == page_num:
            parts.append(f'<span class="pg-btn pg-active">{p + 1}</span>')
        else:
            parts.append(f'<a href="{url_fn(p)}" class="pg-btn">{p + 1}</a>')

    if end < total_pages:
        if end < total_pages - 1:
            parts.append('<span class="pg-btn" style="opacity:.4">…</span>')
        parts.append(f'<a href="{url_fn(total_pages - 1)}" class="pg-btn">{total_pages}</a>')

    if page_num < total_pages - 1:
        parts.append(f'<a href="{url_fn(page_num + 1)}" class="pg-btn">Next &#8594;</a>')
    else:
        parts.append('<span class="pg-btn" style="opacity:.4">Next &#8594;</span>')

    return (
        '<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;'
        'margin-top:12px;padding-top:12px;border-top:1px solid #21262d">'
        + "".join(parts)
        + "</div>"
    )


@bp_explorer.route("/explorer")
@require_auth
def explorer():
    # ── Parse query params ────────────────────────────────────────────────────
    tab = request.args.get("tab", "all")
    if tab not in ("all", "upcoming", "settled"):
        tab = "all"

    country = request.args.get("country") or None
    league_id_raw = request.args.get("league_id") or None
    league_id = int(league_id_raw) if league_id_raw and league_id_raw.isdigit() else None
    market = request.args.get("market") or None
    if market not in (None, "h2h", "ou25", "ou15", "btts"):
        market = None
    team = request.args.get("team") or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    data_context = request.args.get("data_context") or None
    if data_context not in (None, "full", "elo_both", "elo_partial", "flat_prior", "national_elo"):
        data_context = None

    sort = request.args.get("sort", "date")
    if sort not in ("date", "league", "confidence", "accuracy", "brier"):
        sort = "date"

    default_dir = "asc" if tab == "upcoming" and sort == "date" else "desc"
    sort_dir = request.args.get("sort_dir") or default_dir
    if sort_dir not in ("asc", "desc"):
        sort_dir = default_dir

    try:
        page_num = max(0, int(request.args.get("page", 0)))
    except (ValueError, TypeError):
        page_num = 0
    page_size = 50

    # ── URL helpers ───────────────────────────────────────────────────────────
    def build_url(**overrides):
        args = dict(request.args)
        args.update(overrides)
        args.pop("page", None)  # always reset page on param change
        return "/explorer?" + "&".join(f"{k}={v}" for k, v in args.items() if v)

    def page_url(p: int) -> str:
        args = dict(request.args)
        args["page"] = str(p)
        return "/explorer?" + "&".join(f"{k}={v}" for k, v in args.items() if v)

    # ── Data ──────────────────────────────────────────────────────────────────
    league_map = get_league_country_map()
    result = get_explorer_data(
        tab=tab,
        country=country,
        league_id=league_id,
        market=market,
        team=team,
        date_from=date_from,
        date_to=date_to,
        data_context=data_context,
        sort=sort,
        sort_dir=sort_dir,
        page=page_num,
        page_size=page_size,
    )

    fixtures = result["fixtures"]
    total = result["total"]
    query_ms = result["query_ms"]
    total_pages = max(1, (total + page_size - 1) // page_size)

    # ── Tab bar ───────────────────────────────────────────────────────────────
    def tc(t):
        return "explorer-tab active" if t == tab else "explorer-tab"

    tab_bar = f"""
    <div class="explorer-tabs">
      <a href="{build_url(tab='all')}" class="{tc('all')}">All</a>
      <a href="{build_url(tab='upcoming')}" class="{tc('upcoming')}">Upcoming</a>
      <a href="{build_url(tab='settled')}" class="{tc('settled')}">Settled</a>
    </div>"""

    # ── Filter form ───────────────────────────────────────────────────────────
    countries = sorted(league_map.keys())

    def opt(val, label, selected_val):
        sel = " selected" if str(val) == str(selected_val or "") else ""
        return f'<option value="{val}"{sel}>{label}</option>'

    country_opts = opt("", "All Countries", country) + "".join(
        opt(c, c, country) for c in countries
    )

    league_opts = opt("", "All Leagues", league_id)
    if country and country in league_map:
        for lg in league_map[country]:
            league_opts += opt(lg["id"], lg["name"], league_id)

    market_opts = (
        opt("", "All Markets", market)
        + opt("h2h", "H2H (Match Result)", market)
        + opt("ou25", "O/U 2.5 Goals", market)
        + opt("ou15", "O/U 1.5 Goals", market)
        + opt("btts", "Both Teams Score", market)
    )

    ctx_opts = (
        opt("", "All Tiers", data_context)
        + opt("full", "Full (standings)", data_context)
        + opt("elo_both", "Elo: both rated", data_context)
        + opt("elo_partial", "Elo: partial (1 unrated)", data_context)
        + opt("flat_prior", "Flat prior", data_context)
    )

    sort_choices = [
        ("date", "Date"),
        ("league", "League"),
        ("confidence", "Confidence"),
        ("accuracy", "Track-A Accuracy" + (" (settled only)" if tab != "settled" else "")),
        ("brier", "Brier Score" + (" (settled only)" if tab != "settled" else "")),
    ]
    sort_opts = "".join(opt(v, l, sort) for v, l in sort_choices)
    dir_opts = opt("desc", "↓ Desc", sort_dir) + opt("asc", "↑ Asc", sort_dir)

    form_html = f"""
    <form method="get" action="/explorer" id="explorer-form">
      <input type="hidden" name="tab" value="{tab}">
      <input type="hidden" name="page" value="0" id="form-page">
      <div class="explorer-filters">
        <select name="country" id="cty-sel" class="flt-ctrl">
          {country_opts}
        </select>
        <select name="league_id" id="lg-sel" class="flt-ctrl">
          {league_opts}
        </select>
        <select name="market" class="flt-ctrl">{market_opts}</select>
        <select name="data_context" class="flt-ctrl" title="Prediction tier">{ctx_opts}</select>
        <input name="team" type="text" id="team-inp" class="flt-ctrl"
               placeholder="Team name..." value="{team or ''}">
        <input name="date_from" type="date" value="{date_from or ''}" class="flt-ctrl"
               title="From date">
        <input name="date_to" type="date" value="{date_to or ''}" class="flt-ctrl"
               title="To date">
        <select name="sort" class="flt-ctrl">{sort_opts}</select>
        <select name="sort_dir" class="flt-ctrl">{dir_opts}</select>
        <button type="submit" class="flt-btn">Apply</button>
        <a href="/explorer?tab={tab}" class="flt-clear">Clear</a>
      </div>
    </form>"""

    # ── Summary bar ───────────────────────────────────────────────────────────
    start_n = page_num * page_size + 1 if total > 0 else 0
    end_n = min((page_num + 1) * page_size, total)
    summary = (
        f'<div class="explorer-summary">'
        f"Showing {start_n}–{end_n} of {total:,} fixtures &middot; {query_ms:.0f}ms"
        f'<span style="float:right">Page {page_num + 1} of {total_pages}</span>'
        f"</div>"
    )

    # ── Track A / Track B legend ──────────────────────────────────────────────
    legend = (
        '<div style="margin-bottom:10px;display:flex;gap:10px;align-items:center;'
        'flex-wrap:wrap;font-size:12px">'
        '<span class="badge badge-blue">Track A</span>'
        '<span style="color:#8b949e">Prediction accuracy — outcome labelled, '
        "scored against results regardless of odds</span>"
        "</div>"
    )

    # ── Table ─────────────────────────────────────────────────────────────────
    if not fixtures:
        table_html = """
        <div class="empty-state">
          <span class="empty-icon">&#128269;</span>
          <h2>No predictions match these filters</h2>
          <p>Try widening the date range, removing a team filter, or selecting a different tab.</p>
        </div>"""
    else:
        rows_html = ""
        for fix in fixtures:
            d = fix["date"]
            date_str = d.strftime("%Y-%m-%d") if d else ""
            time_str = d.strftime("%H:%M") if d else "—"
            home = fix["home_team"]
            away = fix["away_team"]
            league_lbl = fix["league_name"]
            country_lbl = fix["country"]
            mkts = fix["markets"]
            status_badge = _status_badge(fix.get("fixture_status"))

            rows_html += (
                "<tr>"
                f'<td style="font-family:monospace;font-size:11px;color:#8b949e;'
                f'white-space:nowrap;padding:6px 10px">{date_str}<br>{time_str}</td>'
                "<td style=\"padding:6px 10px\">"
                f'<span style="color:#e6edf3;font-weight:500">{home}</span>'
                '<span style="color:#8b949e;margin:0 4px">vs</span>'
                f'<span style="color:#c9d1d9">{away}</span>'
                f'{status_badge}'
                f'<br><span style="color:#8b949e;font-size:10px">{league_lbl}'
                f'{"&nbsp;·&nbsp;" + country_lbl if country_lbl else ""}</span>'
                "</td>"
                + _mkt_cell(mkts.get("h2h"))
                + _mkt_cell(mkts.get("ou25"))
                + _mkt_cell(mkts.get("ou15"))
                + _mkt_cell(mkts.get("btts"))
                + "</tr>\n"
            )

        table_html = (
            '<div class="card" style="padding:0;overflow-x:auto">'
            "<table>"
            "<thead><tr>"
            "<th>Date / Time</th>"
            "<th>Match</th>"
            "<th>H2H</th>"
            "<th>O/U 2.5</th>"
            "<th>O/U 1.5</th>"
            "<th>BTTS</th>"
            "</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table></div>"
        )

    # ── Pagination ────────────────────────────────────────────────────────────
    pagination = _build_pagination(page_num, total_pages, page_url)

    # ── JS: country→league dependency + team debounce ─────────────────────────
    league_map_js = json.dumps(
        {c: lgs for c, lgs in league_map.items()}, ensure_ascii=False
    )
    saved_league = str(league_id) if league_id else ""
    js = f"""<script>
const LEAGUE_MAP = {league_map_js};
const ctySel = document.getElementById('cty-sel');
const lgSel  = document.getElementById('lg-sel');
const savedLeague = "{saved_league}";

function populateLeagues(cty, restore) {{
  lgSel.innerHTML = '<option value="">All Leagues</option>';
  if (cty && LEAGUE_MAP[cty]) {{
    LEAGUE_MAP[cty].forEach(function(lg) {{
      const o = document.createElement('option');
      o.value = lg.id; o.textContent = lg.name;
      if (restore && String(lg.id) === restore) o.selected = true;
      lgSel.appendChild(o);
    }});
  }}
}}

ctySel.addEventListener('change', function() {{
  populateLeagues(this.value, null);
  document.getElementById('form-page').value = '0';
  document.getElementById('explorer-form').submit();
}});

if (ctySel.value) populateLeagues(ctySel.value, savedLeague);

// Auto-submit any select or date change (except country, already handled above)
document.querySelectorAll('#explorer-form select:not(#cty-sel), #explorer-form input[type=date]')
  .forEach(function(el) {{
    el.addEventListener('change', function() {{
      document.getElementById('form-page').value = '0';
      document.getElementById('explorer-form').submit();
    }});
  }});

// Team typeahead: debounce 450 ms then submit
const teamInp = document.getElementById('team-inp');
let tmr;
teamInp.addEventListener('input', function() {{
  clearTimeout(tmr);
  tmr = setTimeout(function() {{
    document.getElementById('form-page').value = '0';
    document.getElementById('explorer-form').submit();
  }}, 450);
}});
</script>"""

    content = f"""
    <div style="margin-bottom:12px">
      <h1>Prediction Explorer</h1>
      <p>Browse all predictions &middot; filters applied server-side &middot; 50 fixtures per page</p>
    </div>

    {tab_bar}
    {form_html}
    {summary}
    {legend}
    {table_html}
    {pagination}
    {js}"""

    return page("Explorer", content, active="explorer")
