"""
v2/templates_v2.py — Shared HTML base template and CSS for web_ui_v2.

No V1 imports. All styling is self-contained.
"""
from __future__ import annotations

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,monospace;font-size:14px;line-height:1.5}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
/* Nav */
nav{background:#161b22;border-bottom:1px solid #30363d;padding:0 24px;display:flex;align-items:center;gap:0;height:48px}
.nav-brand{color:#58a6ff;font-weight:700;font-size:15px;margin-right:24px;white-space:nowrap}
.nav-brand span{color:#f85149;font-size:11px;vertical-align:super;margin-left:3px}
.nav-link{color:#8b949e;padding:0 14px;height:48px;display:flex;align-items:center;border-bottom:2px solid transparent;font-size:13px;transition:color .15s}
.nav-link:hover{color:#e6edf3;text-decoration:none}
.nav-link.active{color:#e6edf3;border-bottom-color:#58a6ff}
.nav-spacer{flex:1}
.nav-tag{font-size:11px;color:#8b949e;background:#21262d;border:1px solid #30363d;padding:2px 8px;border-radius:4px}
/* Layout */
main{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:20px;font-weight:600;margin-bottom:4px}
h2{font-size:16px;font-weight:600;margin-bottom:12px;color:#c9d1d9}
h3{font-size:13px;font-weight:600;margin-bottom:8px;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}
p{color:#8b949e;font-size:13px}
/* Cards */
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:16px;margin-bottom:16px}
.card-title{font-size:13px;font-weight:600;color:#c9d1d9;margin-bottom:12px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:1fr 1fr}
.grid-3{grid-template-columns:1fr 1fr 1fr}
.grid-4{grid-template-columns:repeat(4,1fr)}
@media(max-width:800px){.grid-2,.grid-3,.grid-4{grid-template-columns:1fr}}
/* Stat chips */
.stat{padding:12px 16px;background:#0d1117;border:1px solid #21262d;border-radius:6px;text-align:center}
.stat-value{font-size:22px;font-weight:700;color:#e6edf3;display:block}
.stat-label{font-size:11px;color:#8b949e;margin-top:2px;display:block}
/* Badges */
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:12px;vertical-align:middle}
.badge-green{background:#1a3a1a;color:#3fb950;border:1px solid #2ea043}
.badge-red{background:#3a1a1a;color:#f85149;border:1px solid #da3633}
.badge-amber{background:#3a2a0a;color:#d29922;border:1px solid #bb8009}
.badge-blue{background:#0d2340;color:#58a6ff;border:1px solid #1f6feb}
.badge-gray{background:#21262d;color:#8b949e;border:1px solid #30363d}
/* Status panels */
.status-block{padding:12px 16px;border-radius:6px;border-left:3px solid;margin-bottom:12px}
.status-closed{background:#1a0a0a;border-color:#f85149}
.status-active{background:#0a1a0a;border-color:#3fb950}
.status-waiting{background:#0a0d1a;border-color:#58a6ff}
.status-block h4{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.status-closed h4{color:#f85149}
.status-active h4{color:#3fb950}
.status-waiting h4{color:#58a6ff}
.status-block p{font-size:12px;color:#8b949e;margin:0}
/* Tables */
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 10px;color:#8b949e;border-bottom:1px solid #21262d;font-weight:600;white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #161b22;color:#c9d1d9}
tr:hover td{background:#161b22}
.num{text-align:right;font-variant-numeric:tabular-nums}
/* Progress bar */
.progress-track{background:#21262d;border-radius:4px;height:6px;margin:4px 0}
.progress-fill{height:6px;border-radius:4px;background:#58a6ff}
/* Empty state */
.empty-state{text-align:center;padding:48px 24px;color:#8b949e}
.empty-state h2{font-size:15px;color:#c9d1d9;margin-bottom:8px}
.empty-state p{font-size:13px;margin-bottom:4px}
.empty-icon{font-size:32px;margin-bottom:16px;display:block}
/* Probe schedule */
.probe-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #21262d}
.probe-row:last-child{border-bottom:none}
.probe-date{font-family:monospace;font-size:12px;color:#58a6ff;min-width:140px}
/* Calibration */
.cal-bar{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:11px}
.cal-label{min-width:50px;color:#8b949e}
.cal-predicted{height:8px;background:#58a6ff44;border-radius:2px}
.cal-actual{height:8px;background:#3fb95066;border-radius:2px;margin-top:2px}
.cal-pct{min-width:36px;text-align:right;color:#8b949e}
/* Explorer */
.explorer-tabs{display:flex;gap:0;margin-bottom:14px;border-bottom:1px solid #30363d}
.explorer-tab{padding:8px 16px;color:#8b949e;font-size:13px;border-bottom:2px solid transparent;transition:color .15s}
.explorer-tab:hover{color:#e6edf3;text-decoration:none}
.explorer-tab.active{color:#e6edf3;border-bottom-color:#58a6ff}
.explorer-filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px}
.flt-ctrl{background:#161b22;border:1px solid #30363d;border-radius:4px;color:#c9d1d9;
          font-size:12px;padding:4px 8px;height:28px}
.flt-ctrl:focus{outline:none;border-color:#58a6ff}
.flt-btn{background:#1f6feb;border:none;border-radius:4px;color:#fff;cursor:pointer;
         font-size:12px;font-weight:600;padding:4px 12px;height:28px}
.flt-btn:hover{background:#388bfd}
.flt-clear{color:#8b949e;font-size:12px;padding:4px 6px}
.flt-clear:hover{color:#c9d1d9}
.explorer-summary{font-size:12px;color:#8b949e;margin-bottom:8px;padding:4px 0}
.pg-btn{display:inline-block;padding:3px 10px;font-size:12px;border-radius:4px;
        background:#161b22;border:1px solid #30363d;color:#8b949e}
.pg-btn:hover{color:#e6edf3;text-decoration:none;border-color:#58a6ff}
.pg-active{background:#1f6feb!important;border-color:#1f6feb!important;color:#fff!important}
"""

_NAV = """
<nav>
  <span class="nav-brand">Bootball<span>V2</span></span>
  <a href="/" class="nav-link {h}">Status</a>
  <a href="/track-a" class="nav-link {ta}">Track A · Accuracy</a>
  <a href="/predictions" class="nav-link {pr}">Predictions</a>
  <a href="/explorer" class="nav-link {ex}">Explorer</a>
  <a href="/collection" class="nav-link {co}">Collection</a>
  <span class="nav-spacer"></span>
  <span class="nav-tag">bot_enabled=False</span>
</nav>
"""


def page(title: str, content: str, active: str = "") -> str:
    """Render a full HTML page with nav and shared CSS."""
    from flask import render_template_string
    nav = _NAV.format(
        h="active" if active == "home" else "",
        ta="active" if active == "track_a" else "",
        pr="active" if active == "predictions" else "",
        ex="active" if active == "explorer" else "",
        co="active" if active == "collection" else "",
    )
    return render_template_string(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Bootball V2</title>
<style>{_CSS}</style>
</head>
<body>
{nav}
<main>{content}</main>
</body>
</html>""")
