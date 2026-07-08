#!/usr/bin/env python3
"""
Bootball Web UI - Football Prediction & Betting Interface

Pages:
- /predictions: Upcoming matches with predictions (card-based)
- /betting: Bot automated betting with bankroll tracking (command center)
- /tracking: Track predictions vs actual results (timeline)
- /admin: System controls and configuration
- /debug: Technical details and logs

Color System:
- Background: #0d1117 (dark)
- Cards: #161b22 (slightly lighter)
- Borders: #30363d (subtle)
- Win: #3fb950 (green)
- Loss: #f85149 (red)
- EV+: #58a6ff (blue)
- Pending: #d29922 (amber)
"""
import os
import sys
from pathlib import Path
import json
import secrets
import logging
import pickle
import numpy as np
from datetime import datetime, timedelta, timezone
from functools import wraps

RUN_SYSTEM_ACTIVATION_TIMESTAMP = "2026-04-25 06:30:00"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── .env Safety Check ─────────────────────────────────────
_env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_path):
    _env_stat = os.stat(_env_path)
    if _env_stat.st_mode & 0o77:  # Check if group/other have any permissions
        import warnings
        warnings.warn(
            "SECURITY WARNING: .env file has permissions that allow group/other access. "
            "Run: chmod 600 .env"
        )

from flask import Flask, jsonify, request, make_response, render_template_string
from sqlalchemy import select, func, case

from config.settings import settings
from config.leagues import LEAGUES
from src.cache.prediction_cache import get_prediction_cache, cache_prediction, get_cached_prediction
from src.models.calibrator import get_calibration_cache, calibrate_prediction
from src.models.model_tracker import get_model_tracker, ModelTracker
from src.models.iteration_graph import generate_all_graphs
from src.prediction.lib.prediction import build_features_h2h
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureOdds, Standing, PredictionRecord, PlacedBet,
    BankrollRound, Team, League, UserPreference, WatchedFixture,
    ModelVersion, Bankroll
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

@app.errorhandler(Exception)
def handle_error(e):
    """Global error handler - ensures all errors return JSON."""
    logger.error(f"Global error handler caught: {e}")
    return jsonify({
        "success": False,
        "data": {},
        "error": str(e)
    }), 500


_APP_START_TIME = datetime.utcnow()


@app.route('/health')
def health():
    """Liveness + readiness probe. No auth required."""
    from sqlalchemy import text as sa_text
    from backend.runtime_mode import get_mode_name

    scheduler = getattr(app, 'scheduler', None)
    scheduler_running = scheduler is not None and getattr(scheduler, 'running', False)
    active_jobs = [j.id for j in scheduler.get_jobs()] if scheduler_running else []

    db_ok = False
    try:
        with get_session() as s:
            s.execute(sa_text("SELECT 1"))
            db_ok = True
    except Exception:
        logger.exception("Health check: DB unreachable")

    try:
        mode = get_mode_name()
    except Exception:
        mode = "unknown"

    uptime = int((datetime.utcnow() - _APP_START_TIME).total_seconds())
    status = "ok" if db_ok else "degraded"

    return jsonify({
        "status": status,
        "mode": mode,
        "scheduler_running": scheduler_running,
        "active_jobs": active_jobs,
        "db_reachable": db_ok,
        "uptime_seconds": uptime,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }), 200 if status == "ok" else 503


# In-memory caches
TEAM_NAMES = {}
TEAM_LOGOS = {}
LEAGUE_NAMES = {}
LEAGUE_FLAGS = {}
_cache_loaded_at: float = 0.0
_CACHE_TTL = 3600.0  # reload team/league data every hour


def load_caches(force: bool = False):
    """Load team/league names and logos into memory. Reloads after TTL expires."""
    import time
    global TEAM_NAMES, TEAM_LOGOS, LEAGUE_NAMES, LEAGUE_FLAGS, _cache_loaded_at
    if not force and TEAM_NAMES and (time.time() - _cache_loaded_at) < _CACHE_TTL:
        return
    try:
        with get_session() as s:
            teams = s.execute(select(Team)).scalars().all()
            TEAM_NAMES = {t.id: t.name for t in teams}
            TEAM_LOGOS = {t.id: t.logo_url for t in teams if t.logo_url}

            leagues = s.execute(select(League)).scalars().all()
            LEAGUE_NAMES = {l.id: l.name for l in leagues}
            LEAGUE_FLAGS = {l.id: l.flag for l in leagues if l.flag}

        _cache_loaded_at = time.time()
        logger.info("Loaded %d teams, %d leagues", len(TEAM_NAMES), len(LEAGUE_NAMES))
    except Exception as e:
        logger.warning("Cache load error: %s", e)


@app.before_request
def ensure_caches():
    """Ensure caches are loaded before handling requests."""
    load_caches()


def get_password():
    pw = os.environ.get('BOOTBALL_PASSWORD') or getattr(settings, 'bootball_password', None)
    if not pw:
        logger.critical("BOOTBALL_PASSWORD is not configured — all dashboard authentication will be denied")
    return pw


def require_auth(f):
    """Basic auth decorator."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.cookies.get('authenticated') == 'true':
            return f(*args, **kwargs)
        auth = request.authorization
        pw = get_password()
        if not auth or auth.username != 'bootball' or auth.password != pw:
            return make_response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="Bootball"'})
        resp = make_response(f(*args, **kwargs))
        resp.set_cookie('authenticated', 'true', max_age=3600)
        return resp
    return decorated


def compute_ev(our_prob, odds):
    if our_prob <= 0 or odds <= 0:
        return 0
    return (odds * our_prob) - (1 - our_prob)


def _extract_odds_for_outcome(market, outcome, h2h_row, btts_row, ou_row):
    """Extract best available odds for a market/outcome from pre-loaded FixtureOdds rows."""
    if market == "h2h" and h2h_row:
        if outcome == "1":
            return h2h_row.odd_home
        elif outcome == "X":
            return h2h_row.odd_draw
        elif outcome == "2":
            return h2h_row.odd_away
    elif market == "btts" and btts_row:
        if outcome in ("Yes", "BTTS_Yes"):
            return btts_row.odd_btts_yes
        elif outcome == "No":
            return btts_row.odd_btts_no
    elif market == "ou25" and ou_row:
        if outcome == "Over":
            return ou_row.odd_over
        elif outcome == "Under":
            return ou_row.odd_under
    elif market == "ou15" and ou_row:
        if outcome == "Over":
            return ou_row.odd_over15
        elif outcome == "Under":
            return ou_row.odd_under15
    return None


# =============================================================================
# HTML COMPONENTS
# =============================================================================

HTML_HEAD = '''<!DOCTYPE html>
<html>
<head>
<title>Bootball</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    line-height: 1.6;
}
.nav {
    display: flex;
    gap: 4px;
    padding: 8px 16px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 100;
}
.nav a {
    color: #8b949e;
    text-decoration: none;
    padding: 8px 16px;
    border-radius: 6px;
    font-size: 14px;
    transition: all 0.2s;
}
.nav a:hover { background: #21262d; color: #c9d1d9; }
.nav a.active { background: #238636; color: #fff; }
.sidebar {
    position: fixed;
    top: 49px;
    left: 0;
    bottom: 0;
    width: 260px;
    background: #161b22;
    border-right: 1px solid #30363d;
    padding: 16px 0;
    overflow-y: auto;
}
.sidebar a {
    display: block;
    color: #8b949e;
    text-decoration: none;
    padding: 10px 16px;
    font-size: 14px;
    transition: all 0.2s;
}
.sidebar a:hover { background: #21262d; color: #c9d1d9; }
.sidebar a.active { background: #30363d; color: #fff; border-left: 3px solid #58a6ff; }
.main {
    margin-left: 260px;
    margin-top: 49px;
    padding: 24px;
    min-height: calc(100vh - 49px);
}
.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 16px;
}
.card-title {
    font-size: 12px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}
.card-value {
    font-size: 28px;
    font-weight: 600;
}
.card-value.positive { color: #3fb950; }
.card-value.negative { color: #f85149; }
.card-value.neutral { color: #58a6ff; }
.card-value.pending { color: #d29922; }
.metric-row {
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
}
.metric-box {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px 24px;
    min-width: 120px;
}
.metric-label {
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
}
.metric-value {
    font-size: 22px;
    font-weight: 600;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
}
th, td {
    padding: 12px 16px;
    text-align: left;
    border-bottom: 1px solid #30363d;
}
th {
    background: #161b22;
    color: #8b949e;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
}
tr:hover { background: #21262d; }
.win { color: #3fb950; }
.loss { color: #f85149; }
.pending { color: #d29922; }
.ev-positive { color: #3fb950; }
.ev-negative { color: #f85149; }
.ev-neutral { color: #8b949e; }
.badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
}
.badge-success { background: #238636; color: #fff; }
.badge-warning { background: #d29922; color: #000; }
.badge-danger { background: #f85149; color: #fff; }
.badge-info { background: #58a6ff; color: #fff; }
.badge-sweet { background: linear-gradient(135deg, #f0b429 0%, #d17a07 100%); color: #000; text-shadow: 0 1px 0 rgba(255,255,255,0.3); }
.badge-live_eval { background: #f85149; color: #fff; }
.badge-live { background: #3fb950; color: #000; }
.badge-training { background: #1f6feb; color: #fff; }
.badge-dev { background: #8b949e; color: #000; }
.badge-market { background: #30363d; color: #c9d1d9; }
.stat-value { font-size: 24px; font-weight: 700; color: #c9d1d9; }
.btn {
    padding: 10px 20px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
}
.btn-success { background: #238636; color: #fff; }
.btn-danger { background: #f85149; color: #fff; }
.btn-primary { background: #58a6ff; color: #fff; }
.btn-secondary { background: #30363d; color: #c9d1d9; border: 1px solid #30363d; }
.btn-small { padding: 4px 10px; font-size: 12px; }
.btn:hover { opacity: 0.85; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.empty-state {
    text-align: center;
    padding: 48px 24px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    margin: 24px 0;
}
.empty-state h2 {
    color: #c9d1d9;
    margin-bottom: 8px;
}
.empty-state p {
    color: #8b949e;
    margin-bottom: 16px;
}
input, select {
    background: #0d1117;
    border: 1px solid #30363d;
    color: #c9d1d9;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 14px;
}
input:focus, select:focus {
    outline: none;
    border-color: #58a6ff;
}
.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.data-table th {
    background: #161b22;
    padding: 10px 12px;
    text-align: left;
    border-bottom: 2px solid #30363d;
    color: #8b949e;
    font-weight: 600;
}
.data-table td {
    padding: 8px 12px;
    border-bottom: 1px solid #21262d;
    color: #c9d1d9;
}
.data-table tr:hover td {
    background: #161b22;
}
.tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 16px;
    border-bottom: 1px solid #30363d;
    padding-bottom: 8px;
}
.tab {
    padding: 8px 16px;
    background: transparent;
    border: none;
    color: #8b949e;
    cursor: pointer;
    border-radius: 6px;
    font-size: 14px;
}
.tab.active { background: #30363d; color: #fff; }
.prediction-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}
.prediction-card.ev-positive { border-left: 4px solid #3fb950; }
.prediction-card.ev-negative { border-left: 4px solid #f85149; }
.match-time {
    font-size: 12px;
    color: #8b949e;
}
.team-name {
    font-size: 18px;
    font-weight: 600;
    margin: 4px 0;
}
.league-badge {
    display: inline-block;
    font-size: 10px;
    padding: 2px 8px;
    background: #30363d;
    border-radius: 10px;
    color: #8b949e;
}
.confidence-bar {
    height: 6px;
    background: #30363d;
    border-radius: 3px;
    margin: 8px 0;
    overflow: hidden;
}
.confidence-fill {
    height: 100%;
    background: #58a6ff;
    border-radius: 3px;
}
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.col { flex: 1; min-width: 300px; }
.col-2 { flex: 2; min-width: 400px; }
.msg { padding: 12px; border-radius: 6px; margin: 12px 0; }
.msg-success { background: #23863622; color: #3fb950; border: 1px solid #238636; }
.msg-error { background: #f8514922; color: #f85149; border: 1px solid #f85149; }
.msg-info { background: #58a6ff22; color: #58a6ff; border: 1px solid #58a6ff; }
.footer {
    margin-top: 40px;
    padding: 16px;
    text-align: center;
    color: #8b949e;
    font-size: 12px;
    border-top: 1px solid #30363d;
}
#fixtureFocusPanel {
    display: none;
    position: fixed;
    left: 260px;
    top: 49px;
    right: 0;
    bottom: 0;
    background: #0d1117;
    z-index: 150;
    overflow-y: auto;
    flex-direction: column;
}
#fixtureFocusPanel.open { display: flex; }
.ffp-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 20px 12px;
    border-bottom: 1px solid #30363d;
    background: #161b22;
    position: sticky;
    top: 0;
    z-index: 10;
}
.ffp-score {
    font-size: 28px;
    font-weight: 700;
    color: #58a6ff;
    min-width: 60px;
    text-align: center;
}
.ffp-team { font-size: 16px; font-weight: 600; color: #e6edf3; }
.ffp-team-logo { width: 28px; height: 28px; vertical-align: middle; }
.ffp-badge {
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 10px;
    background: #d29922;
    color: #0d1117;
    font-weight: 700;
}
.ffp-tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid #30363d;
    background: #161b22;
}
.ffp-tab {
    padding: 10px 20px;
    background: transparent;
    border: none;
    color: #8b949e;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
}
.ffp-tab:hover { color: #c9d1d9; }
.ffp-tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
.ffp-body { padding: 20px; flex: 1; }
.ffp-close {
    margin-left: auto;
    background: none;
    border: 1px solid #30363d;
    color: #8b949e;
    border-radius: 6px;
    padding: 4px 10px;
    cursor: pointer;
    font-size: 16px;
}
.ffp-close:hover { color: #f85149; border-color: #f85149; }
.stat-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.stat-bar-label { font-size: 12px; color: #8b949e; width: 140px; text-align: right; }
.stat-bar-label.right { text-align: left; }
.stat-bar-wrap { flex: 1; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; }
.stat-bar-fill-home { height: 100%; background: #58a6ff; border-radius: 4px; transition: width 0.4s; float: right; }
.stat-bar-fill-away { height: 100%; background: #f85149; border-radius: 4px; transition: width 0.4s; }
.event-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid #21262d;
    font-size: 13px;
}
.event-time { color: #8b949e; width: 36px; font-size: 12px; font-weight: 600; }
.event-icon { width: 20px; text-align: center; }
.event-detail { color: #c9d1d9; flex: 1; }
.event-team-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    background: #21262d;
    color: #8b949e;
}
.lineup-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.lineup-col h4 { font-size: 13px; color: #8b949e; margin: 0 0 10px; font-weight: 500; }
.lineup-player { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 5px 0; border-bottom: 1px solid #21262d; color: #c9d1d9; }
.lineup-player span { color: #8b949e; font-size: 11px; min-width: 18px; text-align: right; flex-shrink: 0; }
.lineup-player img { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; background: #21262d; flex-shrink: 0; }
.lineup-player img.no-photo { filter: opacity(0.3); }
.h2h-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid #21262d; font-size: 13px; }
[data-fixture-id] { cursor: pointer; }
tr[data-fixture-id]:hover td { background: #161b22; }
.prediction-card[data-fixture-id]:hover { filter: brightness(1.08); }
.h2h-result { font-weight: 700; min-width: 40px; text-align: center; border-radius: 4px; padding: 2px 6px; font-size: 11px; white-space: nowrap; }
.h2h-w { background: #23863640; color: #3fb950; }
.h2h-d { background: #d2992240; color: #d29922; }
.h2h-l { background: #f8514940; color: #f85149; }
.sidebar-game-item {
    cursor: pointer;
    transition: background 0.1s;
    border-radius: 6px;
    padding: 4px 6px;
    margin: -4px -6px 4px;
}
.sidebar-game-item:hover { background: #21262d; }
.sidebar-game-item.focused { background: #1f2937; border-left: 2px solid #58a6ff; padding-left: 4px; }
</style>
</head>
<body>
<div class="nav">
    <a href="/">Bootball</a>
    <a href="/predictions">Predictions</a>
    <a href="/betting">Betting</a>
    <a href="/tracking">Tracking</a>
    <a href="/admin">Admin</a>
    <a href="/processes">Processes</a>
    <a href="/debug">Debug</a>
</div>
<div class="sidebar">
    <div id="liveGamesSidebar" style="padding: 12px 8px;">
        <div id="liveGamesHeader" style="font-size: 11px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase;">Live Now</div>
        <div id="liveGamesList" style="color: #c9d1d9; font-size: 13px;">Loading...</div>
    </div>
</div>

<!-- Fixture Focus Panel -->
<div id="fixtureFocusPanel">
    <div class="ffp-header">
        <div id="ffpHomeLogoWrap"></div>
        <div class="ffp-team" id="ffpHome"></div>
        <div class="ffp-score" id="ffpScore">-</div>
        <div class="ffp-team" id="ffpAway"></div>
        <div id="ffpAwayLogoWrap"></div>
        <span class="ffp-badge" id="ffpBadge" style="margin-left:8px;"></span>
        <div style="margin-left:12px;font-size:12px;color:#8b949e;" id="ffpLeague"></div>
        <div style="margin-left:auto;display:flex;align-items:center;gap:10px;">
            <span style="font-size:11px;color:#484f58;" id="ffpRefreshLabel">Refreshing every 30s</span>
            <button class="ffp-close" onclick="closeFocus()">✕</button>
        </div>
    </div>
    <div class="ffp-tabs">
        <button class="ffp-tab active" onclick="ffpTab(this,'overview')">Overview</button>
        <button class="ffp-tab" onclick="ffpTab(this,'statistics')">Statistics</button>
        <button class="ffp-tab" onclick="ffpTab(this,'lineups')">Lineups</button>
        <button class="ffp-tab" onclick="ffpTab(this,'h2h')">H2H</button>
        <button class="ffp-tab" onclick="ffpTab(this,'table')">Table</button>
    </div>
    <div class="ffp-body">
        <div id="ffpOverview"></div>
        <div id="ffpStatistics" style="display:none;"></div>
        <div id="ffpLineups" style="display:none;"></div>
        <div id="ffpH2h" style="display:none;"></div>
        <div id="ffpTable" style="display:none;"></div>
    </div>
</div>

<script>
// ── Fixture Focus ────────────────────────────────────────────────────────────
var _focusId = null;
var _focusTimer = null;
var _focusData = {};

function focusFixture(id, home, away) {
    if (_focusId === id) { closeFocus(); return; }
    _focusId = id;
    document.querySelectorAll('.sidebar-game-item').forEach(function(el) {
        el.classList.toggle('focused', el.dataset.fid == id);
    });
    document.getElementById('ffpHome').textContent = home;
    document.getElementById('ffpAway').textContent = away;
    document.getElementById('ffpScore').textContent = '…';
    document.getElementById('ffpBadge').textContent = '';
    document.getElementById('ffpLeague').textContent = '';
    document.getElementById('ffpOverview').innerHTML = '<div style="padding:40px;text-align:center;color:#8b949e;">Loading…</div>';
    document.getElementById('ffpStatistics').innerHTML = '';
    document.getElementById('ffpLineups').innerHTML = '';
    document.getElementById('ffpH2h').innerHTML = '';
    document.getElementById('ffpTable').innerHTML = '';
    document.getElementById('fixtureFocusPanel').classList.add('open');
    ffpTab(document.querySelector('.ffp-tab'), 'overview');
    _loadFocus();
    clearInterval(_focusTimer);
    _focusTimer = setInterval(_loadFocus, 30000);
}

function closeFocus() {
    clearInterval(_focusTimer);
    _focusId = null;
    document.getElementById('fixtureFocusPanel').classList.remove('open');
    document.querySelectorAll('.sidebar-game-item,.fx-focusable').forEach(function(el) { el.classList.remove('focused'); });
}

// Global delegated handler — any element with data-fixture-id opens the focus panel
document.addEventListener('click', function(e) {
    var el = e.target.closest('[data-fixture-id]');
    if (!el) return;
    var fid = parseInt(el.dataset.fixtureId);
    var home = el.dataset.home || '';
    var away = el.dataset.away || '';
    if (fid) focusFixture(fid, home, away);
});

var _playerPhotoFallback = 'data:image/svg+xml,%3Csvg xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22 width%3D%2228%22 height%3D%2228%22%3E%3Ccircle cx%3D%2214%22 cy%3D%2214%22 r%3D%2214%22 fill%3D%22%2321262d%22%2F%3E%3Ccircle cx%3D%2214%22 cy%3D%2211%22 r%3D%225%22 fill%3D%22%2330363d%22%2F%3E%3Cellipse cx%3D%2214%22 cy%3D%2224%22 rx%3D%228%22 ry%3D%225%22 fill%3D%22%2330363d%22%2F%3E%3C%2Fsvg%3E';
function _playerImgError(img) { img.src = _playerPhotoFallback; img.classList.add('no-photo'); }

function ffpTab(btn, tab) {
    document.querySelectorAll('.ffp-tab').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    ['overview','statistics','lineups','h2h','table'].forEach(function(t) {
        document.getElementById('ffp' + t.charAt(0).toUpperCase() + t.slice(1)).style.display = t === tab ? '' : 'none';
    });
}

function _loadFocus() {
    if (!_focusId) return;
    fetch('/api/fixture/' + _focusId + '/focus', {credentials: 'include', cache: 'no-store'})
        .then(function(r) { return r.json(); })
        .then(function(d) { _renderFocus(d); })
        .catch(function(e) {
            document.getElementById('ffpOverview').innerHTML = '<div style="color:#f85149;padding:20px;">Failed to load: ' + e + '</div>';
        });
}

function _renderFocus(d) {
    _focusData = d;
    var homeTeam = d.home_team || '';
    var awayTeam = d.away_team || '';

    // ── Header ──────────────────────────────────────────────────────────────
    var hg = (d.score && d.score.home !== null) ? d.score.home : '–';
    var ag = (d.score && d.score.away !== null) ? d.score.away : '–';
    document.getElementById('ffpScore').textContent = hg + ' – ' + ag;
    document.getElementById('ffpBadge').textContent = d.match_state || '';
    document.getElementById('ffpLeague').textContent = d.league || '';
    if (d.home_logo) document.getElementById('ffpHomeLogoWrap').innerHTML = '<img src="' + d.home_logo + '" class="ffp-team-logo" style="margin-right:8px;">';
    if (d.away_logo) document.getElementById('ffpAwayLogoWrap').innerHTML = '<img src="' + d.away_logo + '" class="ffp-team-logo" style="margin-left:8px;">';

    // ── Overview: events timeline with home/away columns ─────────────────────
    var html = '';
    // Column headers
    html += '<div style="display:grid;grid-template-columns:1fr 52px 1fr;gap:0;margin-bottom:6px;">';
    html += '<div style="font-size:11px;font-weight:700;color:#58a6ff;text-transform:uppercase;padding:4px 8px 4px 0;">' + homeTeam + '</div>';
    html += '<div></div>';
    html += '<div style="font-size:11px;font-weight:700;color:#f85149;text-transform:uppercase;padding:4px 0 4px 8px;text-align:right;">' + awayTeam + '</div>';
    html += '</div>';

    var events = d.events || [];
    if (events.length === 0) {
        html += '<div style="color:#484f58;font-size:13px;padding:20px 0;">No events yet</div>';
    } else {
        events.forEach(function(ev) {
            var icon = _evIcon(ev.type, ev.detail);
            var isHome = ev.team_id != null ? (ev.team_id == d.home_team_id) : (ev.team === homeTeam);
            var player = ev.player || '';
            var assist = ev.assist ? '<span style="color:#8b949e;font-size:11px;"> +' + ev.assist + '</span>' : '';
            var time = (ev.time || '') + "'";
            html += '<div style="display:grid;grid-template-columns:1fr 52px 1fr;align-items:center;padding:5px 0;border-bottom:1px solid #21262d;">';
            if (isHome) {
                // Home event: player name left-aligned in left col, icon+time in centre
                html += '<div style="text-align:left;padding-left:0;font-size:13px;color:#c9d1d9;">' + player + assist + '</div>';
                html += '<div style="text-align:center;font-size:12px;color:#8b949e;white-space:nowrap;">' + icon + '<br><span style="font-size:11px;">' + time + '</span></div>';
                html += '<div></div>';
            } else {
                // Away event: icon+time in centre, player name right-aligned in right col
                html += '<div></div>';
                html += '<div style="text-align:center;font-size:12px;color:#8b949e;white-space:nowrap;">' + icon + '<br><span style="font-size:11px;">' + time + '</span></div>';
                html += '<div style="text-align:right;padding-right:0;font-size:13px;color:#c9d1d9;">' + player + assist + '</div>';
            }
            html += '</div>';
        });
    }

    // Our predictions
    if (d.our_predictions && d.our_predictions.length > 0) {
        html += '<div style="margin-top:28px;">';
        html += '<div style="font-size:11px;color:#8b949e;text-transform:uppercase;font-weight:600;margin-bottom:10px;">Our Predictions</div>';
        html += '<div style="display:flex;flex-wrap:wrap;gap:8px;">';
        d.our_predictions.forEach(function(p) {
            var evColor = (p.ev != null && p.ev > 0) ? '#3fb950' : '#8b949e';
            html += '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;min-width:110px;">';
            html += '<div style="font-size:11px;color:#8b949e;">' + p.market.toUpperCase() + '</div>';
            html += '<div style="font-weight:600;color:#e6edf3;">' + p.outcome + ' @ ' + (p.odds != null ? p.odds : '–') + '</div>';
            html += '<div style="font-size:12px;color:' + evColor + ';">EV ' + (p.ev != null ? (p.ev * 100).toFixed(1) + '%' : '–') + '</div>';
            html += '<div style="font-size:11px;color:#8b949e;">' + (p.prob != null ? (p.prob * 100).toFixed(0) + '% prob' : '') + '</div>';
            html += '</div>';
        });
        html += '</div></div>';
    }

    document.getElementById('ffpOverview').innerHTML = html;

    // ── Statistics: centre-out bars ──────────────────────────────────────────
    var shtml = '';
    // Team colour legend
    shtml += '<div style="display:flex;justify-content:space-between;margin-bottom:20px;">';
    shtml += '<div style="font-size:13px;font-weight:700;color:#58a6ff;">' + homeTeam + '</div>';
    shtml += '<div style="font-size:13px;font-weight:700;color:#f85149;">' + awayTeam + '</div>';
    shtml += '</div>';

    var stats = d.statistics || [];
    if (stats.length === 0) {
        shtml += '<div style="color:#484f58;font-size:13px;">No statistics available yet</div>';
    } else {
        // Stats where a lower value is better (fewer = winning)
        var lowerIsBetter = {'Fouls':1,'Yellow Cards':1,'Red Cards':1,'Offsides':1};
        stats.forEach(function(st) {
            var rawHome = st.home != null ? String(st.home) : '0';
            var rawAway = st.away != null ? String(st.away) : '0';
            var isPct = rawHome.endsWith('%') || rawAway.endsWith('%');
            var hv = parseFloat(rawHome) || 0;
            var av = parseFloat(rawAway) || 0;
            var hpct, apct;
            if (isPct) {
                hpct = Math.min(hv, 100);
                apct = Math.min(av, 100);
            } else {
                var total = hv + av;
                hpct = total > 0 ? (hv / total * 100) : 50;
                apct = total > 0 ? (av / total * 100) : 50;
            }
            // Determine winner for highlight
            var homeWins, awayWins;
            if (lowerIsBetter[st.label]) {
                homeWins = hv < av;
                awayWins = av < hv;
            } else {
                homeWins = hv > av;
                awayWins = av > hv;
            }
            var homeNumColor = homeWins ? '#58a6ff' : (awayWins ? '#484f58' : '#58a6ff');
            var awayNumColor = awayWins ? '#f85149' : (homeWins ? '#484f58' : '#f85149');
            var homeNumWeight = homeWins ? '800' : '500';
            var awayNumWeight = awayWins ? '800' : '500';

            shtml += '<div style="margin-bottom:14px;">';
            // Values + label row
            shtml += '<div style="display:grid;grid-template-columns:60px 1fr 60px;align-items:center;margin-bottom:5px;gap:8px;">';
            shtml += '<div style="font-size:14px;font-weight:' + homeNumWeight + ';color:' + homeNumColor + ';text-align:right;">' + rawHome + '</div>';
            shtml += '<div style="font-size:11px;color:#8b949e;text-align:center;">' + st.label + '</div>';
            shtml += '<div style="font-size:14px;font-weight:' + awayNumWeight + ';color:' + awayNumColor + ';">' + rawAway + '</div>';
            shtml += '</div>';
            // Dual bar: home fills from centre leftward, away fills from centre rightward
            shtml += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:2px;">';
            // Home bar (right-aligned, fills from centre leftward)
            shtml += '<div style="height:6px;background:#21262d;border-radius:3px 0 0 3px;overflow:hidden;display:flex;justify-content:flex-end;">';
            shtml += '<div style="width:' + hpct.toFixed(0) + '%;height:100%;background:#58a6ff;border-radius:3px 0 0 3px;"></div>';
            shtml += '</div>';
            // Away bar (left-aligned, fills from centre rightward)
            shtml += '<div style="height:6px;background:#21262d;border-radius:0 3px 3px 0;overflow:hidden;display:flex;">';
            shtml += '<div style="width:' + apct.toFixed(0) + '%;height:100%;background:#f85149;border-radius:0 3px 3px 0;"></div>';
            shtml += '</div>';
            shtml += '</div>';
            shtml += '</div>';
        });
    }
    document.getElementById('ffpStatistics').innerHTML = shtml;

    // ── Lineups ──────────────────────────────────────────────────────────────
    var lhtml = '';
    var lineups = d.lineups || [];
    if (lineups.length === 0) {
        lhtml = '<div style="color:#484f58;padding:20px 0;">Lineups not yet available</div>';
    } else {
        lhtml = '<div class="lineup-grid">';
        lineups.forEach(function(team) {
            var isHomeTeam = team.team === homeTeam;
            var teamColor = isHomeTeam ? '#58a6ff' : '#f85149';
            lhtml += '<div>';
            lhtml += '<h4 style="color:' + teamColor + ';margin-bottom:4px;">' + team.team + '</h4>';
            lhtml += '<div style="font-size:11px;color:#8b949e;margin-bottom:10px;">Formation: ' + (team.formation || '–') + '</div>';
            lhtml += '<div style="font-size:11px;color:#8b949e;margin-bottom:6px;text-transform:uppercase;">Starting XI</div>';
            function playerRow(p, dimmed) {
                var src = p.photo || _playerPhotoFallback;
                var imgHtml = '<img src="' + src + '" alt="" onerror="_playerImgError(this)" loading="lazy"' + (!p.photo ? ' class="no-photo"' : '') + '>';
                var style = dimmed ? ' style="color:#484f58;"' : '';
                return '<div class="lineup-player"' + style + '><span>' + (p.number || '') + '</span>' + imgHtml + (p.name || '') + '</div>';
            }
            (team.startXI || []).forEach(function(p) {
                lhtml += playerRow(p, false);
            });
            if (team.substitutes && team.substitutes.length > 0) {
                lhtml += '<div style="margin-top:14px;margin-bottom:6px;font-size:11px;color:#8b949e;text-transform:uppercase;">Substitutes</div>';
                team.substitutes.forEach(function(p) {
                    lhtml += playerRow(p, true);
                });
            }
            lhtml += '</div>';
        });
        lhtml += '</div>';
    }
    document.getElementById('ffpLineups').innerHTML = lhtml;

    // ── H2H ─────────────────────────────────────────────────────────────────
    var h2hData = d.h2h || [];
    var h2html = '';
    if (h2hData.length === 0) {
        h2html = '<div style="color:#484f58;padding:20px 0;">No H2H data available</div>';
    } else {
        h2html = '<div style="font-size:12px;color:#8b949e;margin-bottom:14px;">Last ' + h2hData.length + ' meetings — ';
        var hw = h2hData.filter(function(m){return m.result==='H';}).length;
        var dr = h2hData.filter(function(m){return m.result==='D';}).length;
        var aw = h2hData.filter(function(m){return m.result==='A';}).length;
        h2html += '<span style="color:#58a6ff;">' + homeTeam + ' ' + hw + 'W</span> · ';
        h2html += '<span style="color:#8b949e;">' + dr + 'D</span> · ';
        h2html += '<span style="color:#f85149;">' + awayTeam + ' ' + aw + 'W</span></div>';
        h2hData.forEach(function(m) {
            var res = m.result || '';
            var homeIsCurrentHome = m.home.toLowerCase() === homeTeam.toLowerCase();
            var hc, ac;
            if (res === 'H') {
                hc = homeIsCurrentHome ? '#3fb950' : '#f85149';
                ac = homeIsCurrentHome ? '#f85149' : '#3fb950';
            } else if (res === 'A') {
                hc = homeIsCurrentHome ? '#f85149' : '#3fb950';
                ac = homeIsCurrentHome ? '#3fb950' : '#f85149';
            } else {
                hc = ac = '#d29922';
            }
            h2html += '<div class="h2h-row">';
            h2html += '<span style="color:#8b949e;font-size:11px;min-width:75px;">' + (m.date || '').slice(0,10) + '</span>';
            h2html += '<span style="flex:1;"><span style="color:' + hc + ';font-weight:600;">' + m.home + '</span>';
            h2html += '<span style="color:#484f58;"> vs </span>';
            h2html += '<span style="color:' + ac + ';font-weight:600;">' + m.away + '</span></span>';
            h2html += '<span style="font-weight:700;color:#e6edf3;min-width:40px;text-align:center;">' + m.score + '</span>';
            h2html += '</div>';
        });
    }
    document.getElementById('ffpH2h').innerHTML = h2html;

    // ── Table (league standings) ─────────────────────────────────────────────
    var standings = d.standings || [];
    var score = d.score || {};
    var ms = d.match_state || '';
    var hasScore = score.home !== null && score.home !== undefined && score.away !== null && score.away !== undefined;
    // Live: has a real score and the state looks like in-progress (time, HT, ET, P, BT)
    var isLive = hasScore && (
        /\d+'/.test(ms) || /^[12]H/.test(ms) ||
        ['HT','ET','P','BT','PEN'].indexOf(ms) >= 0
    );

    // Compute simulated standings from the live score (in-memory only, never saved)
    var liveStandings = standings.map(function(r) { return Object.assign({}, r); });
    var posChange = {}; // team -> positive = moved up, negative = moved down

    if (isLive) {
        var hg = parseInt(score.home), ag = parseInt(score.away);
        var homeRow = null, awayRow = null;
        liveStandings.forEach(function(r) {
            if (r.team.toLowerCase() === homeTeam.toLowerCase()) homeRow = r;
            if (r.team.toLowerCase() === awayTeam.toLowerCase()) awayRow = r;
        });
        if (homeRow && awayRow) {
            homeRow.played = (homeRow.played || 0) + 1;
            awayRow.played = (awayRow.played || 0) + 1;
            homeRow.gf = (homeRow.gf || 0) + hg;  homeRow.ga = (homeRow.ga || 0) + ag;
            awayRow.gf = (awayRow.gf || 0) + ag;  awayRow.ga = (awayRow.ga || 0) + hg;
            homeRow.gd = homeRow.gf - homeRow.ga;
            awayRow.gd = awayRow.gf - awayRow.ga;
            if (hg > ag) {
                homeRow.won  = (homeRow.won  || 0) + 1; homeRow.points = (homeRow.points || 0) + 3;
                awayRow.lost = (awayRow.lost || 0) + 1;
            } else if (hg < ag) {
                awayRow.won  = (awayRow.won  || 0) + 1; awayRow.points = (awayRow.points || 0) + 3;
                homeRow.lost = (homeRow.lost || 0) + 1;
            } else {
                homeRow.drawn = (homeRow.drawn || 0) + 1; homeRow.points = (homeRow.points || 0) + 1;
                awayRow.drawn = (awayRow.drawn || 0) + 1; awayRow.points = (awayRow.points || 0) + 1;
            }
            liveStandings.sort(function(a, b) {
                if ((b.points||0) !== (a.points||0)) return (b.points||0) - (a.points||0);
                if ((b.gd||0)     !== (a.gd||0))     return (b.gd||0)     - (a.gd||0);
                return (b.gf||0) - (a.gf||0);
            });
            var origRank = {};
            standings.forEach(function(r) { origRank[r.team] = r.rank; });
            liveStandings.forEach(function(r, i) {
                posChange[r.team] = (origRank[r.team] || (i+1)) - (i + 1);
            });
        }
    }

    var displayRows = isLive ? liveStandings : standings;
    var thtml = '';
    if (displayRows.length === 0) {
        thtml = '<div style="color:#484f58;padding:20px 0;">No standings data available</div>';
    } else {
        if (isLive) {
            thtml += '<div style="font-size:11px;color:#8b949e;margin-bottom:6px;font-style:italic;">Simulated with current score</div>';
        }
        thtml += '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
        thtml += '<thead><tr style="color:#8b949e;border-bottom:1px solid #21262d;">';
        thtml += '<th style="text-align:right;padding:4px 6px 4px 0;width:24px;">#</th>';
        thtml += '<th style="text-align:left;padding:4px 6px;">Team</th>';
        if (isLive) thtml += '<th style="width:26px;"></th>';
        thtml += '<th style="text-align:center;padding:4px 4px;" title="Played">MP</th>';
        thtml += '<th style="text-align:center;padding:4px 4px;" title="Won">W</th>';
        thtml += '<th style="text-align:center;padding:4px 4px;" title="Drawn">D</th>';
        thtml += '<th style="text-align:center;padding:4px 4px;" title="Lost">L</th>';
        thtml += '<th style="text-align:center;padding:4px 4px;" title="Goal Difference">GD</th>';
        thtml += '<th style="text-align:center;padding:4px 0 4px 4px;font-weight:700;" title="Points">Pts</th>';
        thtml += '</tr></thead><tbody>';
        var homeTeamLower = homeTeam.toLowerCase();
        var awayTeamLower = awayTeam.toLowerCase();
        displayRows.forEach(function(row, idx) {
            var isHome = row.team.toLowerCase() === homeTeamLower;
            var isAway = row.team.toLowerCase() === awayTeamLower;
            var highlight = isHome ? 'background:#1a2030;' : isAway ? 'background:#1e1a2a;' : '';
            var teamColor = isHome ? '#58a6ff' : isAway ? '#f85149' : '#c9d1d9';
            var gd = (row.gd !== undefined && row.gd !== null) ? (row.gd > 0 ? '+' + row.gd : row.gd) : '';
            var displayRank = isLive ? (idx + 1) : row.rank;

            // Position change arrow (only when live, only when position actually changed)
            var arrow = '';
            var pc = posChange[row.team] || 0;
            if (isLive && pc > 0) arrow = '<span style="color:#3fb950;font-size:9px;margin-left:2px;vertical-align:middle;">▲</span>';
            else if (isLive && pc < 0) arrow = '<span style="color:#f85149;font-size:9px;margin-left:2px;vertical-align:middle;">▼</span>';

            // Live score badge: "team_goals-opp_goals" from each team's POV, coloured by outcome
            var badge = '';
            if (isLive && (isHome || isAway)) {
                var teamGoals = isHome ? score.home : score.away;
                var oppGoals  = isHome ? score.away : score.home;
                var scoreStr  = teamGoals + '-' + oppGoals;
                var bc  = teamGoals > oppGoals ? '#3fb950' : teamGoals < oppGoals ? '#f85149' : '#d29922';
                var bbg = teamGoals > oppGoals ? 'rgba(63,185,80,0.15)' : teamGoals < oppGoals ? 'rgba(248,81,73,0.15)' : 'rgba(210,153,34,0.15)';
                badge = '<span style="background:' + bbg + ';color:' + bc + ';border-radius:3px;padding:1px 5px;font-size:11px;font-weight:700;">' + scoreStr + '</span>';
            }

            thtml += '<tr style="border-bottom:1px solid #161b22;' + highlight + '">';
            thtml += '<td style="text-align:right;padding:5px 6px 5px 0;color:#484f58;white-space:nowrap;">' + displayRank + arrow + '</td>';
            thtml += '<td style="padding:5px 6px;color:' + teamColor + ';font-weight:' + (isHome || isAway ? '700' : '400') + ';">' + row.team + '</td>';
            if (isLive) thtml += '<td style="padding:5px 4px;text-align:center;">' + badge + '</td>';
            thtml += '<td style="text-align:center;padding:5px 4px;color:#8b949e;">' + (row.played || 0) + '</td>';
            thtml += '<td style="text-align:center;padding:5px 4px;color:#3fb950;">' + (row.won || 0) + '</td>';
            thtml += '<td style="text-align:center;padding:5px 4px;color:#8b949e;">' + (row.drawn || 0) + '</td>';
            thtml += '<td style="text-align:center;padding:5px 4px;color:#f85149;">' + (row.lost || 0) + '</td>';
            thtml += '<td style="text-align:center;padding:5px 4px;color:#8b949e;">' + gd + '</td>';
            thtml += '<td style="text-align:center;padding:5px 0 5px 4px;font-weight:700;color:#e6edf3;">' + (row.points || 0) + '</td>';
            thtml += '</tr>';
        });
        thtml += '</tbody></table>';
    }
    document.getElementById('ffpTable').innerHTML = thtml;
}

function _evIcon(type, detail) {
    if (!type) return '•';
    var t = type.toLowerCase();
    if (t === 'goal') return detail && detail.toLowerCase().includes('own') ? '⚽🔴' : '⚽';
    if (t === 'card') return detail && detail.toLowerCase().includes('yellow') ? '🟨' : '🟥';
    if (t === 'subst') return '🔄';
    if (t === 'var') return '📺';
    return '•';
}

// ── Live games sidebar ───────────────────────────────────────────────────────
function loadLiveGames() {
    const nocache = '_=' + Date.now();
    fetch('/api/live-games?' + nocache, {credentials: 'include', cache: 'no-store'})
        .then(r => {
            if (!r.ok) return [];
            return r.json();
        })
        .then(games => {
            const list = document.getElementById('liveGamesList');
            const header = document.getElementById('liveGamesHeader');
            if (!games || games.length === 0) {
                list.innerHTML = '<span style="color: #484f58;">No live games</span>';
                header.textContent = 'Coming Up';
                return;
            }

            const liveGames = games.filter(g => g.status && !['upcoming', 'finished'].includes(g.status));
            const upcomingGames = games.filter(g => g.status === 'upcoming');

            header.textContent = liveGames.length > 0 ? 'Live Now' : 'Coming Up';

            const displayGames = liveGames.length > 0 ? liveGames : upcomingGames;

            const gamesByLeague = {};
            for (const g of displayGames) {
                const league = g.league_display || g.league_name || 'Other';
                if (!gamesByLeague[league]) gamesByLeague[league] = [];
                gamesByLeague[league].push(g);
            }

            const sortedLeagues = Object.keys(gamesByLeague).sort((a, b) => a.localeCompare(b));

            for (const league in gamesByLeague) {
                gamesByLeague[league].sort((a, b) => {
                    const timeA = a.kickoff || '';
                    const timeB = b.kickoff || '';
                    return timeA.localeCompare(timeB);
                });
            }

            let html = '';
            for (const league of sortedLeagues) {
                html += '<div style="font-size:11px;font-weight:600;color:#8b949e;margin:12px 0 6px 0;text-transform:uppercase;">' + league + '</div>';
                for (const g of gamesByLeague[league]) {
                    const fid = g.id || '';
                    const safeHome = (g.home || '').replace(/"/g, '&quot;');
                    const safeAway = (g.away || '').replace(/"/g, '&quot;');
                    const clickable = fid ? 'class="sidebar-game-item' + (fid == _focusId ? ' focused' : '') + '" data-fid="' + fid + '" data-home="' + safeHome + '" data-away="' + safeAway + '" ' : '';
                    if (g.status === 'upcoming') {
                        const evBadge = g.ev_badge || '';
                        const homeLogo = g.home_logo ? '<img src="' + g.home_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-right:4px;">' : '';
                        const awayLogo = g.away_logo ? '<img src="' + g.away_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-left:4px;">' : '';
                        html += '<div ' + clickable + 'style="margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #21262d;">' +
                                '<div style="font-weight: 600; font-size: 12px;">' + homeLogo + g.home + ' vs ' + g.away + awayLogo + '</div>' +
                                '<div style="font-size: 11px; color: #8b949e; margin: 2px 0;">' + (g.kickoff || '') + '</div>' +
                                evBadge +
                                '</div>';
                    } else {
                        const matchState = g.match_state || g.status || '';
                        const score = (g.home_goals !== null && g.home_goals !== undefined && g.away_goals !== null && g.away_goals !== undefined)
                            ? g.home_goals + '-' + g.away_goals
                            : 'vs';
                        const homeLogo = g.home_logo ? '<img src="' + g.home_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-right:4px;">' : '';
                        const awayLogo = g.away_logo ? '<img src="' + g.away_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-left:4px;">' : '';
                        html += '<div ' + clickable + 'style="margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #21262d;">' +
                            '<div style="font-weight: 600;">' + homeLogo + g.home + ' <span style="color: #58a6ff;">' + score + '</span> ' + g.away + awayLogo + '</div>' +
                            '<div style="font-size: 11px; color: #d29922; margin: 2px 0;">' + matchState + '</div>' +
                            '</div>';
                    }
                }
            }

            list.innerHTML = html;
            // Attach click handlers via data attributes (avoids inline quote escaping)
            list.querySelectorAll('.sidebar-game-item').forEach(function(el) {
                el.addEventListener('click', function() {
                    var fid = parseInt(el.dataset.fid);
                    var home = el.dataset.home || '';
                    var away = el.dataset.away || '';
                    if (fid) focusFixture(fid, home, away);
                });
            });
        })
        .catch(() => {
            document.getElementById('liveGamesList').innerHTML = '<span style="color: #484f58;">Error loading</span>';
        });
}
setInterval(loadLiveGames, 30000);
loadLiveGames();
</script>
<div class="main">
''' + '\n{{ content }}\n' + '''
</div>
</body>
</html>'''


HTML_FOOT = '''<div class="footer">
<p>Bootball Prediction System | Server Time: ''' + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ''' UTC</p>
</div>
</body></html>'''


def page(content, title=''):
    """Wrap content in HTML page structure."""
    return HTML_HEAD.replace('{{ content }}', content)


# =============================================================================
# ROUTES: Home
# =============================================================================

@app.route('/')
def home():
    content = '''
<h1>Bootball</h1>
<p>Football prediction and betting automation system.</p>
<div class="row" style="margin-top: 24px;">
    <div class="col">
        <div class="card">
            <div class="card-title">Predictions</div>
            <p>View upcoming matches with model predictions. Bet on value opportunities.</p>
            <a href="/predictions" class="btn btn-primary" style="margin-top: 12px;">View Predictions</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Betting</div>
            <p>Automated betting bot places value bets. Monitor bankroll and pending bets.</p>
            <a href="/betting" class="btn btn-success" style="margin-top: 12px;">Open Betting</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Tracking</div>
            <p>Track prediction accuracy vs actual results. View historical performance.</p>
            <a href="/tracking" class="btn btn-primary" style="margin-top: 12px;">View Tracking</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Run Explorer</div>
            <p>System execution inspector. View experiment runs, layer attribution, and system diagnostics.</p>
            <a href="/runs" class="btn btn-secondary" style="margin-top: 12px;">View Runs</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Run Health</div>
            <p>Real-time observability of experiment runs, execution health, and pipeline completeness.</p>
            <a href="/runs/health" class="btn btn-primary" style="margin-top: 12px;">Health Dashboard</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">System Control</div>
            <p>Runtime mode configuration and system control panel.</p>
            <a href="/settings/system" class="btn btn-primary" style="margin-top: 12px;">Control Panel</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Governance</div>
            <p>Layer utility tracking and architecture self-optimization.</p>
            <a href="/settings/governance" class="btn btn-secondary" style="margin-top: 12px;">Governance</a>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Architecture</div>
            <p>Self-modifying inference architecture control.</p>
            <a href="/settings/architecture" class="btn btn-primary" style="margin-top: 12px;">Architecture</a>
        </div>
    </div>
</div>
'''
    return page(content)


# =============================================================================
# ROUTES: Run Explorer (Observability Dashboard)
# =============================================================================

@app.route('/runs')
def runs_page():
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    
    runs_html = ''
    for run in runs:
        mode_badge = run.get('mode', 'dev').upper()
        mode_class = 'live_eval' if mode_badge == 'LIVE_EVAL' else 'live' if mode_badge == 'LIVE' else 'training' if mode_badge == 'TRAINING' else 'dev'
        
        start_ts = run.get('start_timestamp', '')
        end_ts = run.get('end_timestamp', 'ongoing')
        
        status_badge = run.get('status', 'active')
        status_class = 'success' if status_badge == 'completed' else 'warning'
        
        runs_html += f'''
        <tr>
            <td><code style="font-size: 11px;">{run.get('run_id', '')[:12]}...</code></td>
            <td><span class="badge badge-{mode_class}">{mode_badge}</span></td>
            <td>{start_ts[:19] if start_ts else '-'}</td>
            <td>{end_ts[:19] if end_ts and end_ts != 'ongoing' else '<span class="badge badge-warning">ACTIVE</span>'}</td>
            <td><code style="font-size: 10px;">{run.get('config_hash', '')}</code></td>
            <td>{run.get('total_predictions', 0)}</td>
            <td>{run.get('total_bets', 0)}</td>
            <td><span class="badge badge-{status_class}">{status_badge}</span></td>
            <td><a href="/runs/{run.get('run_id', '')}" class="btn btn-small">View</a></td>
        </tr>
        '''
    
    content = f'''
    <h1>Run Explorer</h1>
    <p>System execution inspector. View experiment runs and layer attribution.</p>
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Compare Runs</div>
                <p>Select two runs to compare performance.</p>
                <a href="/runs/compare" class="btn btn-secondary" style="margin-top: 12px;">Compare Runs</a>
            </div>
        </div>
    </div>
    <h2>Recent Runs</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th>Run ID</th>
                <th>Mode</th>
                <th>Start</th>
                <th>End</th>
                <th>Config Hash</th>
                <th>Predictions</th>
                <th>Bets</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {runs_html if runs_html else '<tr><td colspan="8">No runs recorded yet.</td></tr>'}
        </tbody>
    </table>
    '''
    return page(content)


@app.route('/runs/<run_id>')
def run_detail_page(run_id):
    from backend.experiment_tracker import get_experiment_runs, compute_run_metrics
    from backend.experiment_tracker import compute_layer_attribution_aggregation, compute_attribution_by_market
    from src.betting.attribution_engine import get_layer_diagnostics
    
    runs = get_experiment_runs(limit=100)
    run = next((r for r in runs if r.get('run_id') == run_id), None)
    
    if not run:
        return page('<h1>Run Not Found</h1><p>The requested run does not exist.</p>')
    
    model_versions = run.get('model_versions', {})
    calib_versions = run.get('calibrator_versions', {})
    
    metrics = compute_run_metrics(run_id)
    layer_agg = compute_layer_attribution_aggregation(run_id)
    market_breakdown = compute_attribution_by_market(run_id)
    diagnostics = get_layer_diagnostics(run_id)
    
    mode_badge = run.get('mode', 'dev').upper()
    mode_class = 'live_eval' if mode_badge == 'LIVE_EVAL' else 'training' if mode_badge == 'TRAINING' else 'dev'
    
    system_snapshot = f'''
    <div class="row">
        <div class="col">
            <div class="card">
                <div class="card-title">Model Versions</div>
                <ul style="font-size: 12px; margin: 0;">
                    {''.join(f'<li>{k}: <code>{v}</code></li>' for k, v in model_versions.items())}
                </ul>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Calibrator Versions</div>
                <ul style="font-size: 12px; margin: 0;">
                    {''.join(f'<li>{k}: <code>{v}</code></li>' for k, v in calib_versions.items())}
                </ul>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">System Config</div>
                <ul style="font-size: 12px; margin: 0;">
                    <li>Feature Pipeline: {run.get('feature_pipeline_version', 'v1.0.0')}</li>
                    <li>Config Hash: <code>{run.get('config_hash', '')}</code></li>
                    <li>Status: {run.get('status', 'active')}</li>
                </ul>
            </div>
        </div>
    </div>
    '''
    
    performance = f'''
    <div class="row">
        <div class="col">
            <div class="card">
                <div class="card-title">Total Predictions</div>
                <div class="stat-value">{metrics.get('total_predictions', 0)}</div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Total Bets</div>
                <div class="stat-value">{metrics.get('total_bets', 0)}</div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Avg EV</div>
                <div class="stat-value" style="color: {'#3fb950' if metrics.get('avg_ev', 0) > 0 else '#f85149'};">
                    {metrics.get('avg_ev', 0):.3f}
                </div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Brier Score</div>
                <div class="stat-value">{metrics.get('brier_score', 0):.4f}</div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">ECE</div>
                <div class="stat-value">{metrics.get('ece', 0):.4f}</div>
            </div>
        </div>
    </div>
    '''
    
    market_rows = ''
    for market, data in market_breakdown.items():
        market_rows += f'''
        <tr>
            <td><span class="badge badge-market">{market.upper()}</span></td>
            <td>{data.get('total_predictions', 0)}</td>
            <td>{data.get('settled', 0)}</td>
            <td style="color: {'#3fb950' if data.get('win_rate', 0) > 0.5 else '#f85149'};">{data.get('win_rate', 0):.1%}</td>
            <td style="color: {'#3fb950' if data.get('avg_ev', 0) > 0 else '#f85149'};">{data.get('avg_ev', 0):.3f}</td>
            <td>{data.get('total_ev', 0):.3f}</td>
            <td>{data.get('layer_deltas', {}).get('calibration', 0):.4f}</td>
            <td>{data.get('layer_deltas', {}).get('risk', 0):.4f}</td>
        </tr>
        '''
    
    layer_impact = layer_agg.get('overall', {})
    layer_rows = ''
    for layer_name in ['calibration', 'league', 'latent', 'drift', 'risk']:
        layer_data = layer_agg.get(f'{layer_name}_layer', {})
        ev_contrib = layer_data.get('ev_contribution', 0)
        pct = layer_data.get('ev_contribution_pct', 0)
        layer_rows += f'''
        <tr>
            <td>{layer_name.title()}</td>
            <td>{layer_data.get('avg_delta', 0):.4f}</td>
            <td style="color: {'#3fb950' if ev_contrib > 0 else '#f85149'};">{ev_contrib:.4f}</td>
            <td>{pct:.1f}%</td>
            <td>{'-' if layer_name != 'risk' else f"{layer_data.get('bet_acceptance_rate', 0):.1%}"}</td>
        </tr>
        '''
    
    recommendations = diagnostics.get('recommendations', [])
    rec_html = ''.join(f'<li>{r}</li>' for r in recommendations) if recommendations else '<li>No recommendations available.</li>'
    
    content = f'''
    <h1>Run Detail: <code style="font-size: 14px;">{run_id[:16]}...</code> <span class="badge badge-{mode_class}">{mode_badge}</span></h1>
    <p>
        Started: {run.get('start_timestamp', '')[:19]}<br>
        Ended: {run.get('end_timestamp', 'ongoing')[:19] if run.get('end_timestamp') else 'In Progress'}
    </p>
    
    <h2>System Snapshot</h2>
    {system_snapshot}
    
    <h2>Performance Overview</h2>
    {performance}
    
    <h2>Market Breakdown</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th>Market</th>
                <th>Predictions</th>
                <th>Settled</th>
                <th>Win Rate</th>
                <th>Avg EV</th>
                <th>Total EV</th>
                <th>Calibration Δ</th>
                <th>Risk Δ</th>
            </tr>
        </thead>
        <tbody>
            {market_rows if market_rows else '<tr><td colspan="8">No market data available.</td></tr>'}
        </tbody>
    </table>
    
    <h2>Layer Attribution</h2>
    <p style="font-size: 12px; color: #8b949e;">Delta-based decomposition of EV contribution by system layer.</p>
    <table class="data-table">
        <thead>
            <tr>
                <th>Layer</th>
                <th>Avg Δ Prob</th>
                <th>EV Contribution</th>
                <th>EV %</th>
                <th>Bet Acceptance</th>
            </tr>
        </thead>
        <tbody>
            {layer_rows}
        </tbody>
    </table>
    
    <h2>System Diagnostics</h2>
    <ul style="font-size: 13px;">
        {rec_html}
    </ul>
    
    <div style="margin-top: 24px;">
        <a href="/runs" class="btn btn-secondary">← Back to Runs</a>
        <a href="/runs/compare?run1={run_id}" class="btn btn-secondary">Compare</a>
    </div>
    '''
    return page(content)


@app.route('/runs/compare')
def runs_compare_page():
    from backend.experiment_tracker import get_experiment_runs, compute_run_metrics
    from backend.experiment_tracker import compute_layer_attribution_aggregation, compute_attribution_by_market
    
    run1_id = request.args.get('run1', '')
    run2_id = request.args.get('run2', '')
    
    runs = get_experiment_runs(limit=20)
    
    if len(runs) < 2:
        empty_state = '''
        <div class="empty-state">
            <h2>Not Enough Runs to Compare</h2>
            <p>Run comparison requires at least 2 experiment runs.</p>
            <p style="color: #8b949e; font-size: 13px; margin-top: 8px;">
                Currently available: 0 runs. Start some experiment runs first.
            </p>
            <a href="/runs" class="btn btn-primary" style="margin-top: 16px;">Go to Runs Explorer</a>
        </div>
        '''
        return page(f'''
        <h1>Compare Runs</h1>
        <p>Select two runs to compare their performance.</p>
        {empty_state}
        ''')
    
    run_options = ''.join(f'''<option value="{r.get('run_id', '')}" {'selected' if r.get('run_id') == run1_id else ''}>
        {r.get('run_id', '')[:12]}... ({r.get('mode', 'dev')}) - {r.get('start_timestamp', '')[:16]}
    </option>''' for r in runs)
    
    comparison_html = ''
    if run1_id and run2_id and run1_id != run2_id and run1_id and run2_id:
        m1 = compute_run_metrics(run1_id)
        m2 = compute_run_metrics(run2_id)
        
        lb1 = compute_attribution_by_market(run1_id)
        lb2 = compute_attribution_by_market(run2_id)
        
        market_comparison_rows = ''.join(f'''<tr>
            <td>{m}</td>
            <td>{lb1.get(m, {}).get("avg_ev", 0):.4f}</td>
            <td>{lb2.get(m, {}).get("avg_ev", 0):.4f}</td>
            <td>{lb1.get(m, {}).get("win_rate", 0):.1%}</td>
            <td>{lb2.get(m, {}).get("win_rate", 0):.1%}</td>
        </tr>''' for m in ['h2h', 'btts', 'ou25', 'ou15'] if m in lb1 or m in lb2)
        
        comparison_html = f'''
        <h3>Performance Comparison</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Metric</th>
                    <th>Run 1</th>
                    <th>Run 2</th>
                    <th>Delta</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Total Predictions</td>
                    <td>{m1.get('total_predictions', 0)}</td>
                    <td>{m2.get('total_predictions', 0)}</td>
                    <td style="color: {'#3fb950' if m2.get('total_predictions', 0) > m1.get('total_predictions', 0) else '#f85149'};">
                        {m2.get('total_predictions', 0) - m1.get('total_predictions', 0):+d}
                    </td>
                </tr>
                <tr>
                    <td>Total Bets</td>
                    <td>{m1.get('total_bets', 0)}</td>
                    <td>{m2.get('total_bets', 0)}</td>
                    <td style="color: {'#3fb950' if m2.get('total_bets', 0) > m1.get('total_bets', 0) else '#f85149'};">
                        {m2.get('total_bets', 0) - m1.get('total_bets', 0):+d}
                    </td>
                </tr>
                <tr>
                    <td>Avg EV</td>
                    <td>{m1.get('avg_ev', 0):.4f}</td>
                    <td>{m2.get('avg_ev', 0):.4f}</td>
                    <td style="color: {'#3fb950' if m2.get('avg_ev', 0) > m1.get('avg_ev', 0) else '#f85149'};">
                        {m2.get('avg_ev', 0) - m1.get('avg_ev', 0):+.4f}
                    </td>
                </tr>
                <tr>
                    <td>Brier Score</td>
                    <td>{m1.get('brier_score', 0):.4f}</td>
                    <td>{m2.get('brier_score', 0):.4f}</td>
                    <td style="color: {'#3fb950' if m2.get('brier_score', 0) < m1.get('brier_score', 0) else '#f85149'};">
                        {m2.get('brier_score', 0) - m1.get('brier_score', 0):+.4f}
                    </td>
                </tr>
                <tr>
                    <td>ECE</td>
                    <td>{m1.get('ece', 0):.4f}</td>
                    <td>{m2.get('ece', 0):.4f}</td>
                    <td style="color: {'#3fb950' if m2.get('ece', 0) < m1.get('ece', 0) else '#f85149'};">
                        {m2.get('ece', 0) - m1.get('ece', 0):+.4f}
                    </td>
                </tr>
            </tbody>
        </table>
        
<h3>Market-Level Comparison</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Market</th>
                    <th>Run 1 EV</th>
                    <th>Run 2 EV</th>
                    <th>Run 1 Win Rate</th>
                    <th>Run 2 Win Rate</th>
                </tr>
            </thead>
            <tbody>
                {market_comparison_rows}
            </tbody>
        </table>
        '''
    
    content = f'''
    <h1>Compare Runs</h1>
    <p>Select two runs to compare their performance.</p>
    
    <form method="get" action="/runs/compare" style="margin-bottom: 24px;">
        <div class="row" style="gap: 16px; align-items: center;">
            <div>
                <label>Run 1:</label>
                <select name="run1" style="min-width: 300px;">
                    <option value="">Select Run 1...</option>
                    {run_options}
                </select>
            </div>
            <div>
                <label>Run 2:</label>
                <select name="run2" style="min-width: 300px;">
                    <option value="">Select Run 2...</option>
                    {run_options}
                </select>
            </div>
            <div>
                <button type="submit" class="btn btn-primary">Compare</button>
            </div>
        </div>
    </form>
    
    {comparison_html}
    
    <div style="margin-top: 24px;">
        <a href="/runs" class="btn btn-secondary">← Back to Runs</a>
    </div>
    '''
    return page(content)


@app.route('/runs/health')
def runs_health_page():
    """Run Health Dashboard - Real-time observability of experiment runs."""
    content = '''
    <h1>Run Health Dashboard</h1>
    <p>Real-time observability of experiment runs, execution health, and pipeline completeness.</p>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">System Health Status</div>
                <div id="health-status" style="margin-top: 12px;">
                    <span style="font-size: 48px;" id="health-icon">●</span>
                    <span id="health-text" style="font-size: 24px; margin-left: 12px;">Loading...</span>
                </div>
            </div>
        </div>
    </div>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Run Counts</div>
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 12px;">
                    <div><div class="metric-value" id="active-runs">0</div><div class="metric-label">Active Runs</div></div>
                    <div><div class="metric-value" id="completed-runs">0</div><div class="metric-label">Completed (24h)</div></div>
                    <div><div class="metric-value" id="failed-runs">0</div><div class="metric-label">Failed</div></div>
                    <div><div class="metric-value" id="orphan-preds">0</div><div class="metric-label">Orphan Predictions</div></div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Data Epochs</div>
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 12px;">
                    <div><div class="metric-value" id="legacy-preds" style="color: #8b949e;">0</div><div class="metric-label" style="font-size: 11px;">Pre-RunContext</div></div>
                    <div><div class="metric-value" id="modern-preds" style="color: #3fb950;">0</div><div class="metric-label" style="font-size: 11px;">Run-tracked</div></div>
                    <div><div class="metric-value" id="legacy-bets" style="color: #8b949e;">0</div><div class="metric-label" style="font-size: 11px;">Pre-RunContext Bets</div></div>
                    <div><div class="metric-value" id="modern-bets" style="color: #3fb950;">0</div><div class="metric-label" style="font-size: 11px;">Run-tracked Bets</div></div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Data Coverage (Modern Epoch)</div>
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px;">
                    <div><div class="metric-value" id="pred-coverage">0%</div><div class="metric-label">Predictions with Run ID</div></div>
                    <div><div class="metric-value" id="bet-coverage">0%</div><div class="metric-label">Bets with Run ID</div></div>
                </div>
            </div>
        </div>
    </div>
    
    <h2>Latest Runs</h2>
    <table class="data-table" id="latest-runs-table">
        <thead>
            <tr>
                <th>Run ID</th>
                <th>Mode</th>
                <th>Start</th>
                <th>End</th>
                <th>Predictions</th>
                <th>Bets</th>
                <th>Status</th>
                <th>Health</th>
            </tr>
        </thead>
        <tbody id="latest-runs-body">
            <tr><td colspan="8" style="text-align: center;">Loading...</td></tr>
        </tbody>
    </table>
    
    <h2>Orphan Detection</h2>
    <div id="orphan-warning" style="display: none; background: #f8514922; border: 1px solid #f85149; padding: 12px; border-radius: 8px; margin-bottom: 16px;">
        <strong>⚠️ Warning:</strong> <span id="orphan-count">0</span> orphan records detected (modern epoch)
    </div>
    <div class="row">
        <div class="col">
            <div class="card">
                <div class="card-title">Orphan Predictions (Modern)</div>
                <div id="orphan-preds-list" style="max-height: 200px; overflow-y: auto;">None</div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Orphan Bets (Modern)</div>
                <div id="orphan-bets-list" style="max-height: 200px; overflow-y: auto;">None</div>
            </div>
        </div>
    </div>
    
    <h2>Legacy Data (Pre-RunContext)</h2>
    <div style="background: #8b949e22; border: 1px solid #8b949e; padding: 12px; border-radius: 8px; margin-bottom: 16px;">
        <p style="margin: 0; color: #8b949e; font-size: 12px;">These records were created before the RunContext system was activated (before 2026-04-25 06:30 UTC). They are not tracked by the execution engine and are excluded from health metrics.</p>
    </div>
    <div class="row">
        <div class="col">
            <div class="card">
                <div class="card-title">Legacy Predictions</div>
                <div id="legacy-preds-list" style="max-height: 150px; overflow-y: auto;">None</div>
            </div>
        </div>
        <div class="col">
            <div class="card">
                <div class="card-title">Legacy Bets</div>
                <div id="legacy-bets-list" style="max-height: 150px; overflow-y: auto;">None</div>
            </div>
        </div>
    </div>
    
    <h2>Pipeline Health</h2>
    <div class="card">
        <div style="display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-top: 12px;">
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">📅</div>
                <div>Scheduler</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-scheduler">Active</div>
            </div>
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">⚙️</div>
                <div>ExecutionEngine</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-engine">Active</div>
            </div>
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">🔄</div>
                <div>RunContext</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-context">Active</div>
            </div>
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">🎯</div>
                <div>Predictions</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-predictions">Active</div>
            </div>
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">💰</div>
                <div>Betting</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-betting">Active</div>
            </div>
            <div style="text-align: center; padding: 12px; background: #161b22; border-radius: 8px;">
                <div style="font-size: 24px;">💾</div>
                <div>DB Writes</div>
                <div style="font-size: 12px; color: #8b949e;" id="pipe-db">Active</div>
            </div>
        </div>
    </div>
    
    <h2>Causal Decision Graph</h2>
    <div class="card">
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 12px;">
            <div><div class="metric-value" id="causal-nodes">0</div><div class="metric-label">Decision Nodes</div></div>
            <div><div class="metric-value" id="causal-edges">0</div><div class="metric-label">Causal Edges</div></div>
            <div><div class="metric-value" id="causal-runs">0</div><div class="metric-label">Runs Tracked</div></div>
            <div><div><a href="/runs/causal" class="btn btn-secondary" style="font-size: 12px;">View Graph</a></div></div>
        </div>
    </div>
    
    <div style="margin-top: 32px;">
        <a href="/runs" class="btn btn-secondary">← Back to Runs</a>
    </div>
    
    <script>
    function loadHealthDashboard() {
        fetch('/api/runs/health/summary')
        .then(r => r.json())
        .then(data => {
            const icon = document.getElementById('health-icon');
            const text = document.getElementById('health-text');
            
            const healthStatus = data.health_status || data.health_status;
            const warningLevel = data.warning_level;
            
            if (healthStatus === 'HEALTHY') {
                icon.style.color = '#3fb950';
                text.style.color = '#3fb950';
                text.textContent = 'HEALTHY';
            } else if (healthStatus === 'DEGRADED') {
                icon.style.color = '#d29922';
                text.style.color = '#d29922';
                text.textContent = 'PARTIAL DEGRADATION';
            } else {
                icon.style.color = '#f85149';
                text.style.color = '#f85149';
                text.textContent = 'BROKEN';
            }
            
            document.getElementById('active-runs').textContent = data.raw_metrics?.active_runs || 0;
            document.getElementById('completed-runs').textContent = data.raw_metrics?.completed_runs_24h || 0;
            document.getElementById('failed-runs').textContent = data.raw_metrics?.failed_runs || 0;
            document.getElementById('orphan-preds').textContent = data.modern_metrics?.predictions ? (data.modern_metrics.predictions - data.modern_metrics.predictions_with_run) : 0;
            
            document.getElementById('legacy-preds').textContent = data.legacy_metrics?.predictions || data.legacy_predictions || 0;
            document.getElementById('modern-preds').textContent = data.modern_metrics?.predictions || 0;
            document.getElementById('legacy-bets').textContent = data.legacy_metrics?.bets || data.legacy_bets || 0;
            document.getElementById('modern-bets').textContent = data.modern_metrics?.bets || 0;
            
            document.getElementById('pred-coverage').textContent = (data.modern_metrics?.prediction_coverage_pct || 0) + '%';
            document.getElementById('bet-coverage').textContent = (data.modern_metrics?.bet_coverage_pct || 0) + '%';
            
            const modernOrphans = (data.modern_metrics?.predictions || 0) > 0 
                ? (data.modern_metrics.predictions - data.modern_metrics.predictions_with_run) 
                : 0;
            const legacyOrphans = (data.legacy_metrics?.orphan_predictions || 0) + (data.legacy_metrics?.orphan_bets || 0);
            
            const warningEl = document.getElementById('orphan-warning');
            if (warningLevel === 'WARNING') {
                warningEl.style.display = 'block';
                warningEl.style.background = '#f8514922';
                warningEl.style.border = '1px solid #f85149';
                document.getElementById('orphan-count').textContent = modernOrphans;
                warningEl.innerHTML = '<strong>⚠️ System Issue:</strong> ' + (data.interpretation?.orphan_state || 'Modern orphan records detected');
            } else if (warningLevel === 'INFO' || legacyOrphans > 0) {
                warningEl.style.display = 'block';
                warningEl.style.background = '#8b949e22';
                warningEl.style.border = '1px solid #8b949e';
                document.getElementById('orphan-count').textContent = legacyOrphans;
                warningEl.innerHTML = '<strong>ℹ️ Legacy Data:</strong> ' + legacyOrphans + ' pre-RunContext records (excluded from health metrics, informational only)';
            } else {
                warningEl.style.display = 'none';
            }
        });
        
        fetch('/api/runs/health/latest')
        .then(r => r.json())
        .then(runs => {
            const tbody = document.getElementById('latest-runs-body');
            if (runs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center;">No runs found</td></tr>';
                return;
            }
            
            tbody.innerHTML = runs.map(r => {
                const statusColor = r.status === 'completed' ? '#3fb950' : r.status === 'failed' ? '#f85149' : '#8b949e';
                const healthColor = r.health_score > 0.7 ? '#3fb950' : r.health_score > 0.3 ? '#d29922' : '#f85149';
                return '<tr>' +
                    '<td>' + r.run_id.substring(0, 8) + '...</td>' +
                    '<td>' + r.mode + '</td>' +
                    '<td>' + (r.start_timestamp || '').substring(0, 16) + '</td>' +
                    '<td>' + (r.end_timestamp || '').substring(0, 16) + '</td>' +
                    '<td>' + r.pred_count + '</td>' +
                    '<td>' + r.bet_count + '</td>' +
                    '<td style="color:' + statusColor + '">' + r.status + '</td>' +
                    '<td style="color:' + healthColor + '">' + r.health_score + '</td>' +
                '</tr>';
            }).join('');
        });
        
        fetch('/api/runs/health/orphans')
        .then(r => r.json())
        .then(data => {
            const predList = document.getElementById('orphan-preds-list');
            const betList = document.getElementById('orphan-bets-list');
            const legacyPredList = document.getElementById('legacy-preds-list');
            const legacyBetList = document.getElementById('legacy-bets-list');
            
            if (data.orphan_predictions.length === 0) {
                predList.innerHTML = '<div style="color: #3fb950;">No orphan predictions ✓</div>';
            } else {
                predList.innerHTML = data.orphan_predictions.slice(0, 10).map(p => 
                    '<div style="padding: 4px; border-bottom: 1px solid #30363d;">ID:' + p.id + ' ' + p.market + ' ' + p.outcome + ' (' + p.score + ')</div>'
                ).join('');
            }
            
            if (data.orphan_bets.length === 0) {
                betList.innerHTML = '<div style="color: #3fb950;">No orphan bets ✓</div>';
            } else {
                betList.innerHTML = data.orphan_bets.slice(0, 10).map(b => 
                    '<div style="padding: 4px; border-bottom: 1px solid #30363d; color: #f85149;">ID:' + b.id + ' ' + b.market + ' ' + b.outcome + ' ($' + b.stake + ') (' + b.score + ')</div>'
                ).join('');
            }
            
            if (data.legacy_predictions && data.legacy_predictions.length > 0) {
                legacyPredList.innerHTML = data.legacy_predictions.slice(0, 10).map(p => 
                    '<div style="padding: 4px; border-bottom: 1px solid #30363d; color: #8b949e;">ID:' + p.id + ' ' + p.market + ' ' + p.outcome + ' (pre-run)</div>'
                ).join('');
            } else {
                legacyPredList.innerHTML = '<div style="color: #8b949e;">No legacy predictions</div>';
            }
            
            if (data.legacy_bets && data.legacy_bets.length > 0) {
                legacyBetList.innerHTML = data.legacy_bets.slice(0, 10).map(b => 
                    '<div style="padding: 4px; border-bottom: 1px solid #30363d; color: #8b949e;">ID:' + b.id + ' ' + b.market + ' ' + b.outcome + ' (pre-run)</div>'
                ).join('');
            } else {
                legacyBetList.innerHTML = '<div style="color: #8b949e;">No legacy bets</div>';
            }
        });
        
        fetch('/api/causal/stats')
        .then(r => r.json())
        .then(data => {
            document.getElementById('causal-nodes').textContent = data.total_nodes || 0;
            document.getElementById('causal-edges').textContent = data.total_edges || 0;
            document.getElementById('causal-runs').textContent = data.runs_tracked || 0;
        })
        .catch(() => {});
    }
    
    loadHealthDashboard();
    setInterval(loadHealthDashboard, 30000);
    </script>
    '''
    return page(content)


# =============================================================================
# API: Run Endpoints
# =============================================================================

@app.route('/api/runs')
def api_runs():
    from backend.experiment_tracker import get_experiment_runs
    runs = get_experiment_runs(limit=20)
    return jsonify({'runs': runs})


@app.route('/api/runs/<run_id>')
def api_run_detail(run_id):
    from backend.experiment_tracker import get_experiment_runs, compute_run_metrics
    from backend.experiment_tracker import compute_layer_attribution_aggregation, compute_attribution_by_market
    from src.betting.attribution_engine import get_layer_diagnostics
    
    runs = get_experiment_runs(limit=100)
    run = next((r for r in runs if r.get('run_id') == run_id), None)
    
    if not run:
        return jsonify({'error': 'Run not found'}), 404
    
    metrics = compute_run_metrics(run_id)
    layer_agg = compute_layer_attribution_aggregation(run_id)
    market_breakdown = compute_attribution_by_market(run_id)
    diagnostics = get_layer_diagnostics(run_id)
    
    return jsonify({
        'run': run,
        'metrics': metrics,
        'layer_aggregation': layer_agg,
        'market_breakdown': market_breakdown,
        'diagnostics': diagnostics
    })


@app.route('/api/runs/<run_id>/metrics')
def api_run_metrics(run_id):
    from backend.experiment_tracker import compute_run_metrics
    metrics = compute_run_metrics(run_id)
    return jsonify(metrics)


@app.route('/api/runs/health/summary')
def api_runs_health_summary():
    """Global system health summary using centralized observability semantics."""
    from backend.observability_semantics import get_observability_semantics
    from src.storage.db import get_session
    from sqlalchemy import text
    
    semantics = get_observability_semantics()
    
    with get_session() as s:
        legacy_preds = s.execute(text(
            "SELECT COUNT(*) FROM prediction_records WHERE created_at < :epoch"
        ), {"epoch": RUN_SYSTEM_ACTIVATION_TIMESTAMP}).scalar() or 0
        
        legacy_bets = s.execute(text(
            "SELECT COUNT(*) FROM placed_bets WHERE placed_at < :epoch"
        ), {"epoch": RUN_SYSTEM_ACTIVATION_TIMESTAMP}).scalar() or 0
    
    semantics['legacy_predictions'] = legacy_preds
    semantics['legacy_bets'] = legacy_bets
    
    return jsonify(semantics)


@app.route('/api/runs/health/latest')
def api_runs_health_latest():
    """Latest experiment runs with health metrics."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    with get_session() as s:
        runs = s.execute(text("""
            SELECT 
                er.run_id,
                er.mode,
                er.start_timestamp,
                er.end_timestamp,
                er.total_predictions,
                er.total_bets,
                er.status,
                (SELECT COUNT(*) FROM prediction_records WHERE run_id = er.run_id) as pred_count,
                (SELECT COUNT(*) FROM placed_bets WHERE run_id = er.run_id) as bet_count
            FROM experiment_runs er
            ORDER BY er.start_timestamp DESC
            LIMIT 20
        """)).fetchall()
        
        result = []
        for row in runs:
            pred_count = row[7] or 0
            bet_count = row[8] or 0
            
            if pred_count > 0:
                bet_coverage = bet_count / pred_count if pred_count > 0 else 0
                health_score = min(1.0, (pred_count * 0.01) + (bet_coverage * 0.3))
            else:
                health_score = 0.0
            
            result.append({
                "run_id": row[0],
                "mode": row[1],
                "start_timestamp": row[2],
                "end_timestamp": row[3],
                "total_predictions": row[4],
                "total_bets": row[5],
                "status": row[6],
                "pred_count": pred_count,
                "bet_count": bet_count,
                "bet_coverage": round(bet_coverage, 2) if pred_count > 0 else 0,
                "health_score": round(health_score, 2)
            })
        
        return jsonify(result)


@app.route('/api/runs/health/orphans')
def api_runs_health_orphans():
    """Detect orphan predictions and bets (modern epoch only)."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    epoch = RUN_SYSTEM_ACTIVATION_TIMESTAMP
    
    with get_session() as s:
        orphan_preds = s.execute(text("""
            SELECT pr.id, pr.fixture_id, pr.market, pr.predicted_outcome, pr.created_at,
                   f.home_team_id, f.away_team_id, f.goals_home, f.goals_away
            FROM prediction_records pr
            JOIN fixtures f ON pr.fixture_id = f.id
            WHERE pr.run_id IS NULL AND pr.created_at >= :epoch
            ORDER BY pr.created_at DESC
            LIMIT 50
        """), {"epoch": epoch}).fetchall()
        
        orphan_bets = s.execute(text("""
            SELECT pb.id, pb.fixture_id, pb.market, pb.outcome, pb.stake, pb.odds, pb.placed_at,
                   f.home_team_id, f.away_team_id, f.goals_home, f.goals_away
            FROM placed_bets pb
            JOIN fixtures f ON pb.fixture_id = f.id
            WHERE pb.run_id IS NULL AND pb.placed_at >= :epoch
            ORDER BY pb.placed_at DESC
            LIMIT 50
        """), {"epoch": epoch}).fetchall()
        
        legacy_preds = s.execute(text("""
            SELECT pr.id, pr.fixture_id, pr.market, pr.predicted_outcome, pr.created_at,
                   f.home_team_id, f.away_team_id
            FROM prediction_records pr
            JOIN fixtures f ON pr.fixture_id = f.id
            WHERE pr.created_at < :epoch
            ORDER BY pr.created_at DESC
            LIMIT 10
        """), {"epoch": epoch}).fetchall()
        
        legacy_bets = s.execute(text("""
            SELECT pb.id, pb.fixture_id, pb.market, pb.outcome, pb.stake, pb.placed_at,
                   f.home_team_id, f.away_team_id
            FROM placed_bets pb
            JOIN fixtures f ON pb.fixture_id = f.id
            WHERE pb.placed_at < :epoch
            ORDER BY pb.placed_at DESC
            LIMIT 10
        """), {"epoch": epoch}).fetchall()
        
        return jsonify({
            "orphan_predictions": [
                {"id": r[0], "fixture_id": r[1], "market": r[2], "outcome": r[3], 
                 "created_at": r[4], "home_id": r[5], "away_id": r[6], "score": f"{r[7]}-{r[8]}"}
                for r in orphan_preds
            ],
            "orphan_bets": [
                {"id": r[0], "fixture_id": r[1], "market": r[2], "outcome": r[3],
                 "stake": r[4], "odds": r[5], "placed_at": r[6], "home_id": r[7], "away_id": r[8], "score": f"{r[9]}-{r[10]}"}
                for r in orphan_bets
            ],
            "legacy_predictions": [
                {"id": r[0], "fixture_id": r[1], "market": r[2], "outcome": r[3], 
                 "created_at": r[4], "home_id": r[5], "away_id": r[6]}
                for r in legacy_preds
            ],
            "legacy_bets": [
                {"id": r[0], "fixture_id": r[1], "market": r[2], "outcome": r[3],
                 "stake": r[4], "placed_at": r[5], "home_id": r[6], "away_id": r[7]}
                for r in legacy_bets
            ],
            "counts": {
                "orphan_predictions": len(orphan_preds),
                "orphan_bets": len(orphan_bets)
            }
        })


@app.route('/api/runs/health/run/<run_id>')
def api_runs_health_run_detail(run_id):
    """Get detailed execution trace for a specific run."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    with get_session() as s:
        run = s.execute(text(
            "SELECT * FROM experiment_runs WHERE run_id = :id"
        ), {"id": run_id}).fetchone()
        
        if not run:
            return jsonify({"error": "Run not found"}), 404
        
        predictions = s.execute(text("""
            SELECT market, COUNT(*) as count, 
                   AVG(our_prob) as avg_prob, AVG(ev) as avg_ev,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins
            FROM prediction_records 
            WHERE run_id = :id AND settled = 1
            GROUP BY market
        """), {"id": run_id}).fetchall()
        
        bets = s.execute(text("""
            SELECT market, COUNT(*) as count, 
                   SUM(stake) as total_stake, SUM(pnl) as total_pnl,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins
            FROM placed_bets 
            WHERE run_id = :id AND settled = 1
            GROUP BY market
        """), {"id": run_id}).fetchall()
        
        return jsonify({
            "run": {
                "run_id": run[1],
                "mode": run[2],
                "start_timestamp": run[3],
                "end_timestamp": run[4],
                "model_versions": run[5],
                "calibrator_versions": run[6],
                "status": run[13]
            },
            "predictions_by_market": [
                {"market": r[0], "count": r[1], "avg_prob": round(r[2], 3) if r[2] else 0,
                 "avg_ev": round(r[3], 3) if r[3] else 0, "wins": r[4], "win_rate": round(r[4]/r[1], 2) if r[1] > 0 else 0}
                for r in predictions
            ],
            "bets_by_market": [
                {"market": r[0], "count": r[1], "total_stake": round(r[2], 2) if r[2] else 0,
                 "total_pnl": round(r[3], 2) if r[3] else 0, "wins": r[4], "win_rate": round(r[4]/r[1], 2) if r[1] > 0 else 0}
                for r in bets
            ]
        })


# =============================================================================
# API: System Control (Runtime Mode)
# =============================================================================

@app.route('/api/system/mode', methods=['GET'])
def api_system_mode():
    from backend.runtime_mode import RuntimeModeManager, get_mode_name, get_runtime_mode
    
    mgr = RuntimeModeManager()
    mode = mgr.get_mode_name()
    
    mode_info = {
        "dev": {"color": "gray", "description": "Full flexibility"},
        "training": {"color": "blue", "description": "Model updates allowed, no betting"},
        "live_eval": {"color": "red", "description": "Frozen system for evaluation"}
    }
    
    info = mode_info.get(mode, {"color": "gray", "description": "Unknown"})
    
    return jsonify({
        "mode": mode,
        "description": info.get("description"),
        "color": info.get("color"),
        "is_live_eval": mode == "live_eval",
        "is_training": mode == "training",
        "is_dev": mode == "dev"
    })


@app.route('/api/system/mode', methods=['POST'])
def api_system_mode_set():
    from backend.runtime_mode import RuntimeModeManager, get_runtime_mode, is_live_eval_mode
    import os
    
    data = request.get_json() or {}
    new_mode = data.get('mode', '').lower()
    
    if new_mode not in ['dev', 'training', 'live', 'live_eval']:
        return jsonify({"error": "Invalid mode. Must be: dev, training, live, or live_eval"}), 400
    
    mgr = RuntimeModeManager()
    current_mode = mgr.get_mode_name()
    
    if current_mode == 'live_eval' and new_mode != 'dev':
        override = data.get('override', False)
        if not override:
            return jsonify({
                "error": "Cannot switch from LIVE_EVAL without override flag",
                "warning": "LIVE_EVAL mode is for evaluation only"
            }), 403
    
    old_mode = current_mode
    os.environ['RUNTIME_MODE'] = new_mode
    
    import importlib
    import backend.runtime_mode
    importlib.reload(backend.runtime_mode)
    
    mgr = RuntimeModeManager()
    final_mode = mgr.get_mode_name()
    
    from src.storage.db import get_session
    try:
        with get_session() as s:
            s.execute(text("""
                INSERT INTO system_mode_changes (old_mode, new_mode, reason, changed_at)
                VALUES (:old, :new, :reason, :ts)
            """), {"old": old_mode, "new": final_mode, "reason": data.get('reason', ''), "ts": datetime.now().isoformat()})
            s.commit()
    except:
        pass
    
    return jsonify({
        "old_mode": old_mode,
        "new_mode": final_mode,
        "status": "updated"
    })


@app.route('/api/governance/summary')
def api_governance_summary():
    """Get layer governance summary across recent runs."""
    from backend.system_governance_engine import get_governance_engine
    
    engine = get_governance_engine()
    summary = engine.get_layer_governance_summary()
    
    return jsonify(summary)


@app.route('/api/governance/evaluate/<run_id>')
def api_governance_evaluate(run_id):
    """Evaluate architecture recommendation for a run."""
    from backend.system_governance_engine import get_governance_engine
    
    engine = get_governance_engine()
    result = engine.evaluate_architecture_recommendation(run_id)
    
    return jsonify(result)


@app.route('/api/governance/ablation/<run_id>')
def api_governance_ablation(run_id):
    """Run full layer ablation analysis for a run."""
    from backend.system_governance_engine import get_governance_engine
    
    engine = get_governance_engine()
    results = engine.run_full_ablation_analysis(run_id)
    
    return jsonify({
        layer: {
            'baseline_ev': r.baseline_ev,
            'ablated_ev': r.ablated_ev,
            'ev_delta': r.ev_delta,
            'calibration_delta': r.calibration_delta,
            'risk_delta': r.risk_delta,
            'prediction_count': r.prediction_count,
            'recommendation': r.recommendation
        }
        for layer, r in results.items()
    })


@app.route('/api/governance/promotion-demotion/<run_id>')
def api_governance_promotion_demotion(run_id):
    """Evaluate promotion/demotion recommendations for layers."""
    from backend.system_governance_engine import get_governance_engine
    
    engine = get_governance_engine()
    recommendations = engine.evaluate_promotion_demotion(run_id)
    
    return jsonify(recommendations)


@app.route('/api/architecture/current')
def api_architecture_current():
    """Get current active architecture."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    arch = engine.get_active_architecture()
    
    if not arch:
        return jsonify({'error': 'No active architecture'}), 404
    
    return jsonify(arch)


@app.route('/api/architecture/candidates')
def api_architecture_candidates():
    """Get candidate architectures."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    candidates = engine.get_candidates()
    
    return jsonify(candidates)


@app.route('/api/architecture/history')
def api_architecture_history():
    """Get architecture evolution history."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    history = engine.get_architecture_history()
    
    return jsonify(history)


@app.route('/api/architecture/propose/<run_id>')
def api_architecture_propose(run_id):
    """Propose new architecture based on run analysis."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    proposal = engine.propose_architecture_update(run_id)
    
    return jsonify({
        'current_architecture': proposal.current_architecture,
        'proposed_architecture': proposal.proposed_architecture,
        'changes': proposal.changes,
        'expected_ev_delta': proposal.expected_ev_delta,
        'expected_risk_delta': proposal.expected_risk_delta,
        'rollback_safety_score': proposal.rollback_safety_score
    })


@app.route('/api/architecture/simulate/<architecture_id>')
def api_architecture_simulate(architecture_id):
    """Run shadow simulation on architecture."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    result = engine.run_shadow_simulation(architecture_id, [])
    
    return jsonify({
        'architecture_id': result.architecture_id,
        'baseline_ev': result.baseline_ev,
        'simulated_ev': result.simulated_ev,
        'ev_delta': result.ev_delta,
        'calibration_delta': result.calibration_delta,
        'validation_score': result.validation_score,
        'is_safe': result.is_safe
    })


@app.route('/api/architecture/apply/<architecture_id>', methods=['POST'])
def api_architecture_apply(architecture_id):
    """Apply architecture if validation passes."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    data = request.get_json() or {}
    reason = data.get('reason', 'Manual apply')
    
    result = engine.apply_architecture(architecture_id, reason)
    
    if not result.get('success'):
        return jsonify(result), 400
    
    return jsonify(result)


@app.route('/api/architecture/rollback', methods=['POST'])
def api_architecture_rollback():
    """Rollback to previous safe architecture."""
    from backend.architecture_evolution_engine import get_evolution_engine
    
    engine = get_evolution_engine()
    data = request.get_json() or {}
    target = data.get('target_architecture_id')
    
    result = engine.rollback_architecture(target)
    
    if not result.get('success'):
        return jsonify(result), 400
    
    return jsonify(result)


@app.route('/api/architecture/transitions')
def api_architecture_transitions():
    """Get architecture transition history."""
    from backend.architecture_evolution_engine import get_evolution_engine

    engine = get_evolution_engine()
    history = engine.get_transition_history()

    return jsonify(history)


@app.route('/api/architecture/candidates', methods=['POST'])
def api_architecture_candidates_create():
    """Persist a proposal as a candidate architecture DB record."""
    from backend.architecture_evolution_engine import get_evolution_engine

    data = request.get_json() or {}
    engine = get_evolution_engine()

    current = engine.get_active_architecture()
    if not current:
        return jsonify({'error': 'No active architecture found'}), 400

    changes = data.get('changes', {})
    remove_layers = changes.get('remove_layers', [])
    reweight_layers = changes.get('reweight_layers', {})

    new_layers = [l for l in current['active_layers'] if l not in remove_layers]
    new_weights = dict(current['layer_weights'])
    for layer, factor in reweight_layers.items():
        if layer in new_weights:
            new_weights[layer] = round(new_weights[layer] * factor, 4)

    ev_score = float(data.get('expected_ev_delta', 0.0))
    risk_score = float(data.get('expected_risk_delta', 0.0))
    rollback_safety = float(data.get('rollback_safety_score', 0.85))

    architecture_id = engine.create_candidate_architecture(
        parent_id=current['architecture_id'],
        active_layers=new_layers,
        layer_weights=new_weights,
        feature_set={},
        governance_score=rollback_safety,
        ev_score=ev_score,
        risk_score=risk_score,
        description=f"Proposed from {current['architecture_id']}: remove={remove_layers} reweight={list(reweight_layers.keys())}",
    )

    return jsonify({'architecture_id': architecture_id, 'success': True})


# =============================================================================
# UI: System Control Panel
# =============================================================================

@app.route('/settings/system')
def system_control_page():
    from backend.runtime_mode import get_mode_name
    
    mode = get_mode_name()
    mode_class = 'live_eval' if mode == 'live_eval' else 'live' if mode == 'live' else 'training' if mode == 'training' else 'dev'

    mode_description = {
        'dev': 'Full flexibility - data collection, training, betting all allowed',
        'training': 'Model retraining allowed, betting disabled',
        'live': 'Production - betting on, models frozen, strict policies enforced',
        'live_eval': 'Evaluation snapshot - predictions tracked, no retraining, no betting',
    }
    
    content = f'''
    <h1>System Control Panel</h1>
    <p>Runtime configuration and mode management.</p>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Current Mode</div>
                <div style="display: flex; align-items: center; gap: 12px; margin-top: 12px;">
                    <span class="badge badge-{mode_class}" style="font-size: 16px; padding: 8px 16px;">{mode.upper()}</span>
                    <span style="color: #8b949e;">{mode_description.get(mode, '')}</span>
                </div>
            </div>
        </div>
    </div>
    
    <h2>Switch Runtime Mode</h2>
    <div class="card" style="max-width: 600px;">
        <p style="color: #8b949e; margin-bottom: 16px;">
            Select a new runtime mode. Note that some transitions require confirmation.
        </p>
        
        <div style="display: flex; gap: 12px; flex-wrap: wrap;">
            <button class="btn btn-{'primary' if mode != 'dev' else 'secondary'}" onclick="switchMode('dev', this)">DEV</button>
            <button class="btn btn-{'primary' if mode != 'training' else 'secondary'}" onclick="switchMode('training', this)">TRAINING</button>
            <button class="btn btn-{'primary' if mode != 'live' else 'secondary'}" onclick="switchMode('live', this)">LIVE</button>
            <button class="btn btn-{'primary' if mode != 'live_eval' else 'secondary'}" onclick="switchMode('live_eval', this)">LIVE_EVAL</button>
        </div>
        
        <div id="mode-switch-result" style="margin-top: 16px;"></div>
    </div>
    
    <h2>Mode Information</h2>
    <table class="data-table" style="max-width: 600px;">
        <thead>
            <tr>
                <th>Mode</th>
                <th>Description</th>
                <th>Behavior</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><span class="badge badge-dev">DEV</span></td>
                <td>Development</td>
                <td>Full flexibility — data collection, retraining, betting all on</td>
            </tr>
            <tr>
                <td><span class="badge badge-training">TRAINING</span></td>
                <td>Training</td>
                <td>Model retraining on, betting off</td>
            </tr>
            <tr>
                <td><span class="badge badge-live">LIVE</span></td>
                <td>Production</td>
                <td>Betting on, models frozen, strict policies — pick the best predictions</td>
            </tr>
            <tr>
                <td><span class="badge badge-live_eval">LIVE_EVAL</span></td>
                <td>Evaluation Snapshot</td>
                <td>Predictions tracked &amp; settled, no retraining, no betting — clean accuracy measurement</td>
            </tr>
        </tbody>
    </table>
    
    <script>
    function switchMode(mode, btn) {{
        if (mode === 'live' && !confirm('Switching to LIVE mode: betting is on, models are frozen, strict policies enforced. Continue?')) {{
            return;
        }}
        if (mode === 'live_eval' && !confirm('WARNING: Switching to LIVE_EVAL will disable betting, model retraining, and all mutations. Continue?')) {{
            return;
        }}
        
        fetch('/api/system/mode', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{mode: mode}})
        }})
        .then(r => r.json())
        .then(data => {{
            const result = document.getElementById('mode-switch-result');
            if (data.error) {{
                result.innerHTML = '<div style="color: #f85149; padding: 8px; background: #f8514922; border-radius: 4px;">' + data.error + '</div>';
            }} else {{
                result.innerHTML = '<div style="color: #3fb950; padding: 8px; background: #3fb95022; border-radius: 4px;">Mode switched from ' + data.old_mode + ' to ' + data.new_mode + '</div>';
                setTimeout(() => location.reload(), 1500);
            }}
        }})
        .catch(err => {{
            document.getElementById('mode-switch-result').innerHTML = '<div style="color: #f85149;">Error: ' + err + '</div>';
        }});
    }}
    </script>
    
    <div style="margin-top: 32px;">
        <a href="/runs" class="btn btn-secondary">← Back to Runs</a>
    </div>
    '''
    return page(content)


@app.route('/settings/governance')
def governance_page():
    """System Governance & Architecture Optimization UI."""
    from backend.experiment_tracker import get_experiment_runs
    from backend.system_governance_engine import get_governance_engine, LAYER_DISPLAY_NAMES
    
    runs = get_experiment_runs(limit=10)
    engine = get_governance_engine()
    summary = engine.get_layer_governance_summary()
    
    run_options = ''.join(f'''<option value="{r.get('run_id', '')}">{r.get('run_id', '')[:12]}... - {r.get('start_timestamp', '')[:16]}</option>'''
        for r in runs)
    
    layers_html = ''
    for layer, data in summary.get('layers', {}).items():
        display_name = LAYER_DISPLAY_NAMES.get(layer, layer)
        layers_html += f'''
        <tr>
            <td>{display_name}</td>
            <td style="color: {'#3fb950' if data.get('avg_ev_contribution', 0) > 0 else '#f85149'}">{data.get('avg_ev_contribution', 0):.4f}</td>
            <td>{data.get('avg_stability', 0):.2f}</td>
            <td style="color: {'#f85149' if data.get('avg_fragility', 0) > 0.5 else '#8b949e'}">{data.get('avg_fragility', 0):.2f}</td>
            <td style="color: {'#f85149' if data.get('avg_redundancy', 0) > 0.7 else '#8b949e'}">{data.get('avg_redundancy', 0):.2f}</td>
            <td>{data.get('sample_count', 0)}</td>
        </tr>
        '''
    
    if not layers_html:
        layers_html = '<tr><td colspan="6" style="text-align: center; color: #8b949e;">No governance data yet. Run experiments first.</td></tr>'
    
    content = f'''
    <h1>System Governance</h1>
    <p>Layer utility tracking, promotion/demotion decisions, and structural optimization.</p>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            <div class="card">
                <div class="card-title">Layer Performance Summary</div>
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Layer</th>
                            <th>Avg EV Contribution</th>
                            <th>Stability</th>
                            <th>Fragility</th>
                            <th>Redundancy</th>
                            <th>Samples</th>
                        </tr>
                    </thead>
                    <tbody>
                        {layers_html}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <h2>Architecture Analysis</h2>
    <div class="card" style="max-width: 500px;">
        <p style="color: #8b949e; margin-bottom: 16px;">Select a run to analyze architecture recommendations.</p>
        
        <select id="governance-run-select" style="margin-bottom: 12px;">
            <option value="">Select a run...</option>
            {run_options}
        </select>
        
        <button class="btn btn-primary" onclick="runGovernanceAnalysis()">Analyze Architecture</button>
        
        <div id="governance-results" style="margin-top: 16px;"></div>
    </div>
    
    <h3>Ablation Analysis</h3>
    <div class="card" style="max-width: 700px;">
        <p style="color: #8b949e; margin-bottom: 16px;">
            Simulate system performance without each layer to determine essential vs redundant layers.
        </p>
        <button class="btn btn-secondary" onclick="runAblationAnalysis()">Run Full Ablation</button>
        <div id="ablation-results" style="margin-top: 16px;"></div>
    </div>
    
    <div style="margin-top: 32px;">
        <a href="/runs" class="btn btn-secondary">← Back to Runs</a>
    </div>
    
    <script>
    function runGovernanceAnalysis() {{
        const runId = document.getElementById('governance-run-select').value;
        if (!runId) {{
            document.getElementById('governance-results').innerHTML = '<div style="color: #f85149;">Please select a run first</div>';
            return;
        }}
        
        fetch('/api/governance/evaluate/' + runId)
        .then(r => r.json())
        .then(data => {{
            let html = '<table class="data-table" style="margin-top: 12px;">';
            html += '<thead><tr><th>Layer</th><th>EV Delta</th><th>Recommendation</th></tr></thead><tbody>';
            
            for (const [layer, result] of Object.entries(data.ablation_results || {{}})) {{
                const color = result.ev_delta > 0 ? '#3fb950' : result.ev_delta < 0 ? '#f85149' : '#8b949e';
                html += '<tr><td>' + layer + '</td><td style="color:' + color + '">' + result.ev_delta.toFixed(4) + '</td><td>' + result.recommendation + '</td></tr>';
            }}
            
            html += '</tbody></table>';
            html += '<div style="margin-top: 12px;"><strong>Layers to remove:</strong> ' + (data.layers_to_remove || []).join(', ') + '</div>';
            html += '<div><strong>Expected EV change:</strong> ' + data.expected_ev_change + '</div>';
            
            document.getElementById('governance-results').innerHTML = html;
        }})
        .catch(err => {{
            document.getElementById('governance-results').innerHTML = '<div style="color: #f85149;">Error: ' + err + '</div>';
        }});
    }}
    
    function runAblationAnalysis() {{
        const runId = document.getElementById('governance-run-select').value;
        if (!runId) {{
            document.getElementById('ablation-results').innerHTML = '<div style="color: #f85149;">Please select a run first</div>';
            return;
        }}
        
        fetch('/api/governance/ablation/' + runId)
        .then(r => r.json())
        .then(data => {{
            let html = '<table class="data-table" style="margin-top: 12px;">';
            html += '<thead><tr><th>Layer</th><th>Baseline EV</th><th>Ablated EV</th><th>Delta</th><th>Recommendation</th></tr></thead><tbody>';
            
            for (const [layer, result] of Object.entries(data)) {{
                const color = result.ev_delta > 0 ? '#3fb950' : result.ev_delta < 0 ? '#f85149' : '#8b949e';
                html += '<tr><td>' + layer + '</td><td>' + result.baseline_ev.toFixed(4) + '</td><td>' + result.ablated_ev.toFixed(4) + '</td><td style="color:' + color + '">' + result.ev_delta.toFixed(4) + '</td><td>' + result.recommendation + '</td></tr>';
            }}
            
            html += '</tbody></table>';
            document.getElementById('ablation-results').innerHTML = html;
        }})
        .catch(err => {{
            document.getElementById('ablation-results').innerHTML = '<div style="color: #f85149;">Error: ' + err + '</div>';
        }});
    }}
    </script>
    '''
    return page(content)


@app.route('/settings/architecture')
def architecture_page():
    """Architecture Evolution & Control UI."""
    from backend.architecture_evolution_engine import get_evolution_engine
    from backend.experiment_tracker import get_experiment_runs
    
    engine = get_evolution_engine()
    current = engine.get_active_architecture()
    history = engine.get_architecture_history()
    candidates = engine.get_candidates()
    transitions = engine.get_transition_history()
    runs = get_experiment_runs(limit=10)
    
    current_html = ''
    if current:
        layers_str = ', '.join(current.get('active_layers', []))
        current_html = f'''
        <div class="card">
            <div class="card-title">Current Architecture</div>
            <div style="margin-top: 12px;">
                <strong>ID:</strong> {current.get('architecture_id')}<br>
                <strong>Layers:</strong> {layers_str}<br>
                <strong>Governance Score:</strong> {current.get('governance_score', 0):.2f}<br>
                <strong>EV Score:</strong> {current.get('ev_score', 0):.4f}<br>
                <strong>Risk Score:</strong> {current.get('risk_score', 0):.4f}<br>
                <strong>Validation Score:</strong> {current.get('validation_score', 0):.2f}
            </div>
        </div>
        '''
    
    history_html = ''
    for arch in history[:10]:
        status = 'ACTIVE' if arch.get('is_active') else ('CANDIDATE' if arch.get('is_candidate') else 'ARCHIVED')
        status_class = 'badge-success' if arch.get('is_active') else ('badge-warning' if arch.get('is_candidate') else 'badge-secondary')
        history_html += f'''
        <tr>
            <td>{arch.get('architecture_id')}</td>
            <td>{', '.join(arch.get('active_layers', []))}</td>
            <td><span class="badge {status_class}">{status}</span></td>
            <td>{arch.get('validation_score', 0):.2f}</td>
            <td>{str(arch.get('created_at', ''))[:16]}</td>
        </tr>
        '''
    
    if not history_html:
        history_html = '<tr><td colspan="5" style="text-align: center; color: #8b949e;">No architecture history</td></tr>'
    
    candidates_html = ''
    for c in candidates:
        val = c.get('validation_score', 0)
        val_color = '#3fb950' if val > 0.6 else '#f85149'
        arch_id = c.get('architecture_id', '')
        candidates_html += f'''
        <tr>
            <td>{arch_id}</td>
            <td>{', '.join(c.get('active_layers', []))}</td>
            <td style="color: {val_color}">{val:.2f}</td>
            <td>{c.get('ev_score', 0):.4f}</td>
            <td><button class="btn btn-secondary" style="padding:4px 10px;font-size:12px;" onclick="applyCandidate('{arch_id}')">Apply</button></td>
        </tr>
        '''
    
    if not candidates_html:
        candidates_html = '<tr><td colspan="4" style="text-align: center; color: #8b949e;">No candidates</td></tr>'
    
    run_options = ''.join(f'''<option value="{r.get('run_id', '')}">{r.get('run_id', '')[:12]}... - {r.get('start_timestamp', '')[:16]}</option>'''
        for r in runs)
    
    content = f'''
    <h1>Architecture Evolution</h1>
    <p>Controlled self-modifying system for inference architecture.</p>
    
    <div class="row" style="margin-bottom: 24px;">
        <div class="col">
            {current_html}
        </div>
    </div>
    
    <h2>Generate Proposal</h2>
    <div class="card" style="max-width: 500px;">
        <p style="color: #8b949e; margin-bottom: 16px;">
            Generate architecture proposal based on run governance analysis.
        </p>
        
        <select id="proposal-run-select" style="margin-bottom: 12px;">
            <option value="">Select a run...</option>
            {run_options}
        </select>
        
        <button class="btn btn-primary" onclick="generateProposal()">Generate Proposal</button>
        
        <div id="proposal-results" style="margin-top: 16px;"></div>
    </div>
    
    <h2>Architecture History</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th>Architecture</th>
                <th>Layers</th>
                <th>Status</th>
                <th>Validation</th>
                <th>Created</th>
            </tr>
        </thead>
        <tbody>
            {history_html}
        </tbody>
    </table>
    
    <h2>Candidate Architectures</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th>Architecture</th>
                <th>Layers</th>
                <th>Validation</th>
                <th>EV Score</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {candidates_html}
        </tbody>
    </table>
    
    <h2>Transition History</h2>
    <table class="data-table">
        <thead>
            <tr>
                <th>From</th>
                <th>To</th>
                <th>EV Delta</th>
                <th>Type</th>
                <th>Timestamp</th>
            </tr>
        </thead>
        <tbody>
            {''.join(f"<tr><td>{t.get('from_architecture', '-')}</td><td>{t.get('to_architecture', '-')}</td><td>{t.get('ev_delta', 0):.4f}</td><td>{t.get('transition_type', '-')}</td><td>{str(t.get('timestamp', ''))[:16]}</td></tr>" for t in transitions[:10]) or '<tr><td colspan="5" style="text-align: center; color: #8b949e;">No transitions</td></tr>'}
        </tbody>
    </table>
    
    <div style="margin-top: 32px;">
        <a href="/settings/governance" class="btn btn-secondary">← Back to Governance</a>
    </div>
    
    <script>
    function generateProposal() {{
        const runId = document.getElementById('proposal-run-select').value;
        if (!runId) {{
            document.getElementById('proposal-results').innerHTML = '<div style="color: #f85149;">Please select a run first</div>';
            return;
        }}
        
        fetch('/api/architecture/propose/' + runId)
        .then(r => r.json())
        .then(data => {{
            let html = '<div style="background: #161b22; padding: 16px; border-radius: 8px; margin-top: 12px;">';
            html += '<h3>Proposal: ' + data.proposed_architecture + '</h3>';
            html += '<p><strong>Current:</strong> ' + data.current_architecture + '</p>';
            html += '<p><strong>Expected EV Delta:</strong> <span style="color:' + (data.expected_ev_delta > 0 ? '#3fb950' : '#f85149') + '">' + data.expected_ev_delta + '</span></p>';
            html += '<p><strong>Expected Risk Delta:</strong> ' + data.expected_risk_delta + '</p>';
            html += '<p><strong>Changes:</strong> ' + JSON.stringify(data.changes) + '</p>';
            
            html += '<button class="btn btn-primary" style="margin-top: 12px;" onclick="createCandidate()">Create Candidate</button>';
            html += '</div>';
            
            window.currentProposal = data;
            document.getElementById('proposal-results').innerHTML = html;
        }})
        .catch(err => {{
            document.getElementById('proposal-results').innerHTML = '<div style="color: #f85149;">Error: ' + err + '</div>';
        }});
    }}
    
    function createCandidate() {{
        const data = window.currentProposal;
        if (!data) return;

        const btn = event.target;
        btn.disabled = true;
        btn.textContent = 'Creating...';

        fetch('/api/architecture/candidates', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(data)
        }})
        .then(r => r.json())
        .then(created => {{
            if (!created.architecture_id) {{
                throw new Error(created.error || 'Failed to create candidate');
            }}
            window.pendingCandidateId = created.architecture_id;
            btn.textContent = 'Running simulation...';
            return fetch('/api/architecture/simulate/' + created.architecture_id);
        }})
        .then(r => r.json())
        .then(sim => {{
            const safeColor = sim.is_safe ? '#3fb950' : '#f85149';
            const safeText = sim.is_safe ? 'SAFE' : 'RISKY';
            let html = document.getElementById('proposal-results').innerHTML;
            html += '<div style="margin-top:12px; padding:12px; background:#0d1117; border-radius:6px;">';
            html += '<strong>Candidate:</strong> ' + window.pendingCandidateId + '<br>';
            html += '<strong>Validation:</strong> <span style="color:' + safeColor + '">' + safeText + ' (' + (sim.validation_score || 0).toFixed(2) + ')</span><br>';
            html += '<strong>EV Delta:</strong> ' + (sim.ev_delta || 0).toFixed(4) + '<br>';
            if (sim.is_safe) {{
                html += '<button class="btn btn-primary" style="margin-top:8px;" onclick="applyCandidate(\'' + window.pendingCandidateId + '\')">Apply Architecture</button>';
            }}
            html += '</div>';
            document.getElementById('proposal-results').innerHTML = html;
            btn.textContent = 'Create Candidate';
            btn.disabled = false;
            location.reload();
        }})
        .catch(err => {{
            document.getElementById('proposal-results').innerHTML += '<div style="color: #f85149; margin-top:8px;">Error: ' + err + '</div>';
            btn.textContent = 'Create Candidate';
            btn.disabled = false;
        }});
    }}

    function applyCandidate(architectureId) {{
        if (!confirm('Apply architecture ' + architectureId + '? This will become the active architecture.')) return;
        fetch('/api/architecture/apply/' + architectureId, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{reason: 'Manual apply from UI'}})
        }})
        .then(r => r.json())
        .then(result => {{
            if (result.success) {{
                alert('Architecture ' + architectureId + ' applied successfully.');
                location.reload();
            }} else {{
                alert('Apply failed: ' + (result.error || 'Unknown error'));
            }}
        }})
        .catch(err => alert('Error: ' + err));
    }}
    </script>
    '''
    return page(content)


# =============================================================================
# API: Layer Evolution & System Intelligence
# =============================================================================

@app.route('/api/layers/evolution')
def api_layers_evolution():
    from src.betting.layer_evolution import compute_layer_evolution_metrics, compute_all_layer_interactions
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return jsonify({'error': 'No runs available', 'runs': []})
    
    layer_metrics = compute_layer_evolution_metrics(run_ids)
    interactions = compute_all_layer_interactions(run_ids)
    
    return jsonify({
        'runs': run_ids,
        'run_count': len(run_ids),
        'layer_metrics': layer_metrics,
        'interactions': interactions
    })


@app.route('/api/layers/summary')
def api_layers_summary():
    from src.betting.layer_evolution import get_layer_summary_ranked
    
    summary = get_layer_summary_ranked()
    return jsonify(summary)


@app.route('/api/layers/timeseries/<layer_name>')
def api_layer_timeseries(layer_name):
    from src.betting.layer_evolution import get_layer_timeseries
    
    limit = request.args.get('limit', 20, type=int)
    timeseries = get_layer_timeseries(layer_name, limit)
    
    return jsonify({
        'layer': layer_name,
        'data': timeseries
    })


@app.route('/api/layers/insights')
def api_layers_insights():
    from src.betting.layer_evolution import generate_system_insights
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return jsonify({'error': 'No runs available', 'insights': []})
    
    insights = generate_system_insights(run_ids)
    
    return jsonify({
        'runs': run_ids,
        'insights': insights
    })


# =============================================================================
# API: Counterfactual Analysis & Architecture Optimization
# =============================================================================

@app.route('/api/counterfactual/ablation/<run_id>')
def api_counterfactual_ablation(run_id):
    from src.betting.layer_ablation_engine import LayerAblationEngine
    
    engine = LayerAblationEngine()
    baseline = engine.get_baseline_metrics(run_id)
    results = engine.run_ablation_study(run_id)
    
    return jsonify({
        'run_id': run_id,
        'baseline': baseline,
        'ablations': [r.to_dict() for r in results]
    })


@app.route('/api/counterfactual/importance/<run_id>')
def api_layer_importance(run_id):
    from src.betting.layer_ablation_engine import compute_layer_importance
    
    importance = compute_layer_importance(run_id)
    return jsonify({
        'run_id': run_id,
        'importance': importance
    })


@app.route('/api/counterfactual/suggestions')
def api_counterfactual_suggestions():
    from src.betting.layer_ablation_engine import suggest_simplified_architecture
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return jsonify({'error': 'No runs available'})
    
    suggestions = suggest_simplified_architecture(run_ids)
    return jsonify(suggestions)


@app.route('/api/counterfactual/pareto')
def api_pareto_optimal():
    from src.betting.layer_ablation_engine import find_pareto_optimal_architectures
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return jsonify({'error': 'No runs available'})
    
    pareto = find_pareto_optimal_architectures(run_ids)
    return jsonify({
        'run_ids': run_ids,
        'pareto_optimal': pareto
    })


@app.route('/api/counterfactual/insights')
def api_counterfactual_insights():
    from src.betting.layer_ablation_engine import generate_counterfactual_insights
    from backend.experiment_tracker import get_experiment_runs
    
    runs = get_experiment_runs(limit=20)
    run_ids = [r.get('run_id', '') for r in runs if r.get('run_id')]
    
    if not run_ids:
        return jsonify({'error': 'No runs available', 'insights': []})
    
    insights = generate_counterfactual_insights(run_ids)
    
    return jsonify({
        'run_ids': run_ids,
        'insights': insights
    })


# =============================================================================
# ROUTES: Predictions
# =============================================================================

@app.route('/predictions')
@require_auth
def predictions_page():
    content = '''
<h1>Predictions</h1>
<div class="tabs">
    <button class="tab active" data-market="all">All Markets</button>
    <button class="tab" data-market="btts">BTTS</button>
    <button class="tab" data-market="ou25">O/U 2.5</button>
    <button class="tab" data-market="ou15">O/U 1.5</button>
    <button class="tab" data-market="h2h">1X2</button>
</div>
<div class="row" style="margin-bottom: 16px;">
    <select id="leagueFilter" style="min-width: 200px;">
        <option value="">All Leagues</option>
    </select>
    <label style="display: flex; align-items: center; gap: 6px; color: #c9d1d9;">
        <input type="checkbox" id="minOddsCheck" checked style="width: 16px; height: 16px; accent-color: #3fb950;">
        Odds ≥ <span id="minOddsValue">1.6</span>
    </label>
    <label style="display: flex; align-items: center; gap: 6px; color: #c9d1d9;">
        <input type="checkbox" id="sweetOnlyCheck" style="width: 16px; height: 16px; accent-color: #d29922;">
        🌟 Sweet Spot
    </label>
    <button class="btn btn-primary" onclick="loadPredictions()">Refresh</button>
</div>
<div id="predictionsList">
    <p style="color: #8b949e;">Loading predictions...</p>
</div>
<script>
let currentMarket = 'all';
let predictionsData = [];

function loadLeagues() {
    console.log('loadLeagues called');
    return fetch('/api/leagues', {credentials: 'include'})
        .then(r => {
            console.log('leagues response:', r.status);
            if (!r.ok) throw new Error('leagues failed: ' + r.status);
            return r.json();
        })
        .then(d => {
            console.log('leagues data:', d);
            const select = document.getElementById('leagueFilter');
            select.innerHTML = '<option value="">All Leagues</option>';
            for (const country in d) {
                const group = document.createElement('optgroup');
                group.label = country;
                d[country].forEach(l => {
                    const opt = document.createElement('option');
                    opt.value = l.id;
                    opt.textContent = l.name;
                    group.appendChild(opt);
                });
                select.appendChild(group);
            }
        });
}

function loadPredictions() {
    const league = document.getElementById('leagueFilter').value;
    const container = document.getElementById('predictionsList');
    container.innerHTML = '<p style="color: #8b949e;">Loading predictions...</p>';
    const marketParam = currentMarket !== 'all' ? '&market=' + currentMarket : '';
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const url = '/api/predictions?days=7' + (league ? '&league=' + league : '') + marketParam + '&tz=' + encodeURIComponent(tz);
    console.log('Fetching:', url);
    return fetch(url, {credentials: 'include'})
        .then(r => {
            console.log('Response status:', r.status);
            if (!r.ok) throw new Error('predictions failed: ' + r.status);
            return r.json();
        })
        .then(d => {
            console.log('Got data:', d.length, 'results');
            predictionsData = d;
            renderPredictions(d);
        })
        .catch(e => {
            console.error('loadPredictions error:', e);
            container.innerHTML = '<p style="color: #f00;">Error: ' + e.message + '</p>';
        });
}

function renderPredictions(data) {
    const container = document.getElementById('predictionsList');
    if (!data || data.length === 0) {
        container.innerHTML = '<p style="color: #8b949e;">No predictions available</p>';
        return;
    }

    // Get filter settings
    const minOddsChecked = document.getElementById('minOddsCheck').checked;
    const minOdds = minOddsChecked ? 1.6 : 0;  // Default 1.6, or 0 if unchecked
    const sweetOnly = document.getElementById('sweetOnlyCheck').checked;

    let filtered = data.filter(p => {
        const o = p.odds || 0;
        if (o < minOdds) return false;
        if (sweetOnly) {
            // Sweet spot: odds 1.8-2.2 with positive EV
            if (o < 1.8 || o > 2.2 || !p.ev_positive) return false;
        }
        return true;
    });

    let html = '';
    for (const p of filtered.slice(0, 50)) {
        const evClass = p.ev_positive ? 'ev-positive' : 'ev-negative';
        const cardClass = p.ev_positive ? 'ev-positive' : 'ev-negative';
        const confidence = Math.round((p.prob || 0.33) * 100);
        const market = p.market || 'h2h';
        const pick = p.pick || '?';
        const odds = p.odds || '-';
        const ev = p.ev || 0;

        // Sweet spot badge: odds 1.8-2.2 with positive EV
        const isSweet = odds >= 1.8 && odds <= 2.2 && ev > 0;

        const leagueFlag = p.league_flag ? '<img src="' + p.league_flag + '" style="width:16px;height:12px;vertical-align:middle;margin-right:4px;">' : '';
        const homeLogo = p.home_logo ? '<img src="' + p.home_logo + '" style="width:24px;height:24px;vertical-align:middle;margin-right:4px;">' : '';
        const awayLogo = p.away_logo ? '<img src="' + p.away_logo + '" style="width:24px;height:24px;vertical-align:middle;margin-left:4px;">' : '';
        const safeHome = (p.home_name || 'Home').replace(/"/g, '&quot;');
        const safeAway = (p.away_name || 'Away').replace(/"/g, '&quot;');
        const fidAttr = p.fixture_id ? ' data-fixture-id="' + p.fixture_id + '" data-home="' + safeHome + '" data-away="' + safeAway + '"' : '';
        html += '<div class="prediction-card ' + cardClass + '"' + fidAttr + '>';
        html += '<div class="match-time">' + (p.date || '').slice(0, 16) + ' | <span class="league-badge">' + leagueFlag + (p.league_name || 'Unknown') + '</span></div>';
        html += '<div class="team-name">' + homeLogo + (p.home_name || 'Home') + ' vs ' + (p.away_name || 'Away') + awayLogo + '</div>';
        html += '<div style="margin: 8px 0;">';
        html += '<span class="badge badge-info">' + market.toUpperCase() + '</span> ';
        if (isSweet) html += '<span class="badge badge-sweet">🌟 SWEET</span> ';
        html += '<span>Pick: <strong>' + pick + '</strong></span> ';
        html += '<span>Odds: ' + odds + '</span> ';
        html += '<span>EV: <span class="' + evClass + '">' + (ev > 0 ? '+' : '') + ev.toFixed(1) + '%</span></span>';
        html += '</div>';
        html += '<div class="confidence-bar"><div class="confidence-fill" style="width: ' + confidence + '%"></div></div>';
        html += '<div style="font-size: 12px; color: #8b949e;">Confidence: ' + confidence + '%</div>';
        html += '</div>';
    }
    if (filtered.length === 0) {
        html = '<p style="color: #8b949e;">No predictions match your filters</p>';
    }
    container.innerHTML = html;
}

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', function() {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        this.classList.add('active');
        currentMarket = this.dataset.market;
        loadPredictions();
    });
});

document.getElementById('leagueFilter').addEventListener('change', loadPredictions);
document.getElementById('minOddsCheck').addEventListener('change', function() {
    // Re-render with current data
    renderPredictions(predictionsData);
});
document.getElementById('sweetOnlyCheck').addEventListener('change', function() {
    // Re-render with current data
    renderPredictions(predictionsData);
});

// Load on page load
loadLeagues();
loadPredictions();
</script>
'''
    return page(content)


@app.route('/api/leagues')
@require_auth
def api_leagues():
    with get_session() as s:
        leagues = s.execute(select(League).order_by(League.country, League.name)).scalars().all()
        grouped = {}
        for l in leagues:
            if l.country not in grouped:
                grouped[l.country] = []
            grouped[l.country].append({'id': l.id, 'name': l.name})
        return jsonify(grouped)


_live_api_cache: dict = {"data": {}, "fetched_at": 0.0}
_LIVE_CACHE_TTL = 60  # seconds between real API fetches

@app.route('/api/live-games')
@require_auth
def api_live_games():
    """Get live and upcoming games for sidebar."""
    now = datetime.utcnow()
    today_str = now.strftime('%Y-%m-%d')

    # Rate-limit: fetch from the API at most once per minute regardless of
    # how many browser tabs poll this endpoint.
    import time as _time
    live_fresh = {}
    now_mono = _time.monotonic()
    if now_mono - _live_api_cache["fetched_at"] >= _LIVE_CACHE_TTL:
        fetched = {}
        try:
            from src.ingestion.client import APIFootballClient
            client = APIFootballClient()
            for status in ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'INT']:
                raw = client.get_fixtures(date=today_str, status=status, force_refresh=True)
                for r in raw:
                    fix = r.get('fixture', {})
                    fid = fix.get('id')
                    if fid:
                        status_info = fix.get('status', {})
                        league = r.get('league', {})
                        fetched[fid] = {
                            'status': status_info.get('short', status) if isinstance(status_info, dict) else status,
                            'elapsed': status_info.get('elapsed'),
                            'goals': r.get('goals', {}),
                            'home_team': r.get('teams', {}).get('home', {}).get('name', ''),
                            'away_team': r.get('teams', {}).get('away', {}).get('name', ''),
                            'home_logo': r.get('teams', {}).get('home', {}).get('logo', ''),
                            'away_logo': r.get('teams', {}).get('away', {}).get('logo', ''),
                            'league_name': league.get('name', ''),
                            'league_country': league.get('country', ''),
                        }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Could not fetch live API data: {e}")
        _live_api_cache["data"] = fetched
        _live_api_cache["fetched_at"] = now_mono

    live_fresh = _live_api_cache["data"]
    
    cutoff_24h = now + timedelta(hours=24)
    
    try:
        from src.storage.db import get_session
        from sqlalchemy import text
        
        league_flags = {
            'England': '🇬🇧', 'Sweden': '🇸🇪', 'Germany': '🇩🇪', 'Spain': '🇪🇸',
            'Italy': '🇮🇹', 'France': '🇫🇷', 'Netherlands': '🇳🇱', 'Belgium': '🇧🇪',
            'Portugal': '🇵🇹', 'Turkey': '🇹🇷', 'Poland': '🇵🇱', 'Austria': '🇦🇹',
            'Switzerland': '🇨🇭', 'Denmark': '🇩🇰', 'Norway': '🇳🇴', 'Finland': '🇫🇮',
            'Czech Republic': '🇨🇿', 'Greece': '🇬🇷', 'Romania': '🇷🇴', 'Hungary': '🇭🇺',
            'Scotland': '🏴󠁧󠁢󠁳󠁣󠁴󠁿', 'Wales': '🏴󠁧󠁢󠁷󠁬󠁳󠁿', 'Ireland': '🇮🇪',
            'Russia': '🇷🇺', 'Ukraine': '🇺🇦', 'Croatia': '🇭🇷', 'Serbia': '🇷🇸',
            'Slovenia': '🇸🇮', 'Slovakia': '🇸🇰', 'Argentina': '🇦🇷', 'Brazil': '🇧🇷',
            'Japan': '🇯🇵', 'Australia': '🇦🇺', 'South Korea': '🇰🇷', 'USA': '🇺🇸',
            'International': '🌍', 'Bosnia': '🇧🇦', 'Albania': '🇦🇱', 'Montenegro': '🇲🇪',
            'North Macedonia': '🇲🇰', 'Bulgaria': '🇧🇬', 'Israel': '🇮🇱', 'Cyprus': '🇨🇾',
            'Luxembourg': '🇱🇺', 'Malta': '🇲🇹', 'Iceland': '🇮🇸', 'Latvia': '🇱🇻',
            'Lithuania': '🇱🇹', 'Estonia': '🇪🇪', 'Armenia': '🇦🇲', 'Georgia': '🇬🇪',
            'Azerbaijan': '🇦🇿', 'Kazakhstan': '🇰🇿', 'Belarus': '🇧🇾', 'Moldova': '🇲🇩'
        }
        
        with get_session() as s:
            league_info = {}
            all_fixture_ids = list(live_fresh.keys())
            if all_fixture_ids:
                from sqlalchemy import text as _text, bindparam as _bp
                league_rows = s.execute(
                    _text("""
                        SELECT f.id, l.name, l.country
                        FROM fixtures f
                        LEFT JOIN leagues l ON f.league_id = l.id
                        WHERE f.id IN :ids
                    """).bindparams(_bp('ids', expanding=True)),
                    {"ids": all_fixture_ids}
                ).fetchall()
                for row in league_rows:
                    country = row[2] or ''
                    flag = league_flags.get(country, '🌍')
                    league_info[row[0]] = {
                        'league_name': row[1] or '',
                        'league_country': country,
                        'league_flag': flag,
                    }
            
            # Use space-separated format to match how SQLite stores datetimes
            # (isoformat() uses 'T' which sorts before ' ' in string comparison)
            now_sql = now.strftime('%Y-%m-%d %H:%M:%S')
            yesterday_sql = (now - timedelta(hours=4)).strftime('%Y-%m-%d %H:%M:%S')
            cutoff_sql = cutoff_24h.strftime('%Y-%m-%d %H:%M:%S')

            live_db_rows = s.execute(text("""
                SELECT DISTINCT
                    f.id as fixture_id,
                    home.name as home_team,
                    away.name as away_team,
                    home.id as home_team_id,
                    away.id as away_team_id,
                    f.status,
                    f.date as kickoff_time,
                    f.goals_home,
                    f.goals_away,
                    f.elapsed,
                    l.name as league_name,
                    l.country as league_country,
                    home.logo_url as home_logo,
                    away.logo_url as away_logo
                FROM fixtures f
                JOIN teams home ON f.home_team_id = home.id
                JOIN teams away ON f.away_team_id = away.id
                LEFT JOIN leagues l ON f.league_id = l.id
                WHERE (
                    f.status IN ('1H','2H','HT','ET','BT','P','INT','LIVE')
                    OR (f.status = 'NS' AND f.date >= :kickoff_window AND f.date < :now_sql)
                )
                AND f.date >= :yesterday AND f.date < :cutoff_24h
                ORDER BY l.name ASC, f.date ASC
            """), {
                'yesterday': yesterday_sql,
                'cutoff_24h': cutoff_sql,
                'now_sql': now_sql,
                'kickoff_window': (now - timedelta(minutes=115)).strftime('%Y-%m-%d %H:%M:%S'),
            }).fetchall()

            upcoming_rows = s.execute(text("""
                SELECT DISTINCT
                    f.id as fixture_id,
                    home.name as home_team,
                    away.name as away_team,
                    home.id as home_team_id,
                    away.id as away_team_id,
                    f.status,
                    f.date as kickoff_time,
                    f.goals_home,
                    f.goals_away,
                    f.elapsed,
                    l.name as league_name,
                    l.country as league_country
                FROM fixtures f
                JOIN teams home ON f.home_team_id = home.id
                JOIN teams away ON f.away_team_id = away.id
                LEFT JOIN leagues l ON f.league_id = l.id
                WHERE f.status = 'NS' AND f.date >= :now AND f.date < :cutoff_24h
                ORDER BY l.name ASC, f.date ASC
            """), {
                'now': now_sql,
                'cutoff_24h': cutoff_sql,
            }).fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Query error: {e}")
        live_db_rows = []
        upcoming_rows = []

    from backend.services.match_state_renderer import render_match_state

    results = []

    # Build live results: API data takes priority; DB fills in when API quota is gone
    seen_live_ids = set()
    for fid, data in live_fresh.items():
        status = data.get('status')
        elapsed = data.get('elapsed')
        if status == 'FT':
            continue
        seen_live_ids.add(fid)
        league_name = data.get('league_name', '')
        league_country = data.get('league_country', '')
        league_flag = league_flags.get(league_country, '🌍')
        league_display = league_flag + ' ' + league_name
        match_state = render_match_state(status, elapsed)
        results.append({
            'id': fid,
            'status': status,
            'home': data.get('home_team', ''),
            'away': data.get('away_team', ''),
            'home_logo': data.get('home_logo', ''),
            'away_logo': data.get('away_logo', ''),
            'home_goals': data.get('goals', {}).get('home'),
            'away_goals': data.get('goals', {}).get('away'),
            'elapsed': elapsed,
            'kickoff': '',
            'match_state': match_state,
            'league_name': league_name,
            'league_flag': league_flag,
            'league_display': league_display,
            'goals_home': data.get('goals', {}).get('home'),
            'goals_away': data.get('goals', {}).get('away'),
        })

    # DB fallback: show live matches from DB for any fixture the API didn't return
    for row in live_db_rows:
        fid = row[0]
        if fid in seen_live_ids:
            continue  # API already provided fresher data
        status = row[5] or ''
        elapsed = row[9]
        kickoff_str = str(row[6]) if row[6] else ''
        if elapsed:
            match_state = render_match_state(status, elapsed)
        elif status in ('1H', '2H', 'HT', 'ET', 'BT', 'P', 'INT', 'LIVE'):
            match_state = render_match_state(status, elapsed)
        elif status == 'NS' and kickoff_str:
            # Estimate period from minutes since kickoff
            try:
                kickoff_dt = datetime.strptime(kickoff_str[:19], '%Y-%m-%d %H:%M:%S')
                mins = int((now - kickoff_dt).total_seconds() / 60)
                if mins <= 0:
                    match_state = 'Kick-off'
                elif mins <= 45:
                    match_state = f'1H {mins}\''
                elif mins <= 60:
                    match_state = 'HT'
                elif mins <= 105:
                    match_state = f'2H {mins - 15}\''
                else:
                    match_state = 'FT?'
            except Exception:
                match_state = 'In Progress?'
        else:
            match_state = 'In Progress?'
        country = row[11] or ''
        league_name = row[10] or ''
        league_flag = league_flags.get(country, '🌍')
        results.append({
            'id': fid,
            'status': status,
            'home': row[1] or '',
            'away': row[2] or '',
            'home_logo': row[12] or None,
            'away_logo': row[13] or None,
            'home_goals': row[7],
            'away_goals': row[8],
            'elapsed': elapsed,
            'kickoff': '',
            'match_state': match_state,
            'league_name': league_name,
            'league_flag': league_flag,
            'league_display': league_flag + ' ' + league_name,
            'goals_home': row[7],
            'goals_away': row[8],
        })

    for row in upcoming_rows:
        results.append({
            'status': 'upcoming',
            'home': row[1],
            'away': row[2],
            'home_logo': None,
            'away_logo': None,
            'home_goals': None,
            'away_goals': None,
            'elapsed': None,
            'kickoff': str(row[6])[11:16] if row[6] else '',
            'match_state': '',
            'league_name': row[10] or '',
            'league_flag': league_flags.get(row[11] or '', ''),
            'league_display': league_flags.get(row[11] or '', '') + ' ' + (row[10] or ''),
            'best_market': None,
            'best_odds': None,
            'date': str(row[6]) if row[6] else '',
        })
    
    response = jsonify(results)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/fixture/<int:fixture_id>/focus')
@require_auth
def api_fixture_focus(fixture_id: int):
    """
    Return all available live data for a single fixture:
    score, events, statistics, lineups, H2H, and our predictions.
    Called every 30 s by the fixture focus panel.
    """
    import logging as _log
    log = _log.getLogger(__name__)

    from src.ingestion.client import APIFootballClient
    from src.storage.db import get_session
    from sqlalchemy import text as sa_text

    client = APIFootballClient()
    out = {
        "fixture_id": fixture_id,
        "home_team": "", "away_team": "",
        "home_logo": "", "away_logo": "",
        "home_team_id": None,
        "league": "",
        "score": {"home": None, "away": None},
        "match_state": "",
        "events": [],
        "statistics": [],
        "lineups": [],
        "h2h": [],
        "our_predictions": [],
    }

    # ── 1. Basic fixture info from DB ────────────────────────────────────────
    home_team_id = away_team_id = league_id = None
    try:
        with get_session() as s:
            row = s.execute(sa_text("""
                SELECT f.goals_home, f.goals_away, f.status, f.elapsed,
                       ht.name, at.name,
                       f.home_team_id, f.away_team_id, f.league_id,
                       l.name, l.country, f.date,
                       ht.logo_url, at.logo_url
                FROM fixtures f
                JOIN teams ht ON f.home_team_id = ht.id
                JOIN teams at ON f.away_team_id = at.id
                LEFT JOIN leagues l ON f.league_id = l.id
                WHERE f.id = :fid
            """), {"fid": fixture_id}).fetchone()
            if row:
                out["score"] = {"home": row[0], "away": row[1]}
                _status = row[2] or ""
                _elapsed = row[3]
                if _elapsed:
                    _match_state = str(_elapsed) + "'"
                elif _status == "NS" and row[11]:
                    # Past-kickoff NS: estimate period from minutes since kickoff
                    from datetime import datetime as _dt
                    try:
                        _kickoff = _dt.strptime(str(row[11])[:19], "%Y-%m-%d %H:%M:%S")
                        _mins = int((_dt.utcnow() - _kickoff).total_seconds() / 60)
                        if _mins <= 0:
                            _match_state = "Kick-off"
                        elif _mins <= 45:
                            _match_state = f"1H {_mins}'"
                        elif _mins <= 60:
                            _match_state = "HT"
                        elif _mins <= 105:
                            _match_state = f"2H {_mins - 15}'"
                        else:
                            _match_state = "FT?"
                    except Exception:
                        _match_state = "In Progress?"
                else:
                    _match_state = _status
                out["match_state"] = _match_state
                out["home_team"] = row[4] or ""
                out["away_team"] = row[5] or ""
                home_team_id = row[6]
                away_team_id = row[7]
                league_id = row[8]
                country = row[10] or ""
                out["league"] = (row[9] or "") + (" · " + country if country else "")
                out["home_team_id"] = home_team_id
                # Logos from DB (fallback when API is unavailable)
                if row[12]:
                    out["home_logo"] = row[12]
                if row[13]:
                    out["away_logo"] = row[13]
    except Exception as e:
        log.warning(f"[FOCUS] DB lookup failed: {e}")

    # ── 2. Live score + state from API (force-fresh for live games) ──────────
    # Note: free plan does not support the plural 'ids' param, but 'id' (singular) works.
    try:
        fixture_data = client.get(
            "fixtures", {"id": fixture_id}, force_refresh=True
        )
        # If the API returned the fixture with a live status, update the DB so
        # future sidebar polls also reflect it without another API call.
        if fixture_data:
            _fx = fixture_data[0]
            _new_status = _fx.get("fixture", {}).get("status", {}).get("short", "")
            _goals = _fx.get("goals", {})
            if _new_status:
                try:
                    from src.storage.db import get_session as _gs
                    from sqlalchemy import select as _sel
                    with _gs() as _s:
                        _fix = _s.execute(_sel(Fixture).where(Fixture.id == fixture_id)).scalar_one_or_none()
                        if _fix and _fix.status != _new_status:
                            _fix.status = _new_status
                            if _goals.get("home") is not None:
                                _fix.goals_home = _goals["home"]
                                _fix.goals_away = _goals["away"]
                            _s.commit()
                except Exception:
                    pass
        if fixture_data:
            fx = fixture_data[0]
            goals = fx.get("goals", {})
            status_obj = fx.get("fixture", {}).get("status", {})
            elapsed = status_obj.get("elapsed")
            short = status_obj.get("short", "")
            if goals.get("home") is not None:
                out["score"] = {"home": goals["home"], "away": goals["away"]}
            if elapsed:
                out["match_state"] = str(elapsed) + "'"
            elif short:
                out["match_state"] = short
            if not out["home_logo"]:
                out["home_logo"] = fx.get("teams", {}).get("home", {}).get("logo", "")
            if not out["away_logo"]:
                out["away_logo"] = fx.get("teams", {}).get("away", {}).get("logo", "")
            if not out["home_team_id"]:
                out["home_team_id"] = fx.get("teams", {}).get("home", {}).get("id")
    except Exception as e:
        log.warning(f"[FOCUS] Live fixture fetch failed: {e}")

    # ── 3. Events (goals, cards, subs) ──────────────────────────────────────
    try:
        raw_events = client.get(
            "fixtures/events", {"fixture": fixture_id}, force_refresh=True
        )
        for ev in raw_events:
            team_name = ev.get("team", {}).get("name", "")
            player = ev.get("player", {}).get("name", "")
            assist = ev.get("assist", {}).get("name", "")
            time_obj = ev.get("time", {})
            elapsed = time_obj.get("elapsed", "")
            extra = time_obj.get("extra")
            time_str = str(elapsed) + ("+" + str(extra) if extra else "")
            out["events"].append({
                "time": time_str,
                "team": team_name,
                "team_id": ev.get("team", {}).get("id"),
                "type": ev.get("type", ""),
                "detail": ev.get("detail", ""),
                "player": player,
                "assist": assist,
            })
    except Exception as e:
        log.warning(f"[FOCUS] Events fetch failed: {e}")

    # ── 4. Statistics (shots, possession, corners, etc.) ────────────────────
    try:
        raw_stats = client.get(
            "fixtures/statistics", {"fixture": fixture_id}, force_refresh=True
        )
        # raw_stats is [{"team": {...}, "statistics": [{type, value}, ...]}, ...]
        stat_map = {}  # label -> {home, away}
        for team_block in raw_stats:
            team_name = team_block.get("team", {}).get("name", "")
            is_home = team_name == out["home_team"]
            for st in team_block.get("statistics", []):
                label = st.get("type", "")
                val = st.get("value")
                val_str = str(val) if val is not None else "0"
                if label not in stat_map:
                    stat_map[label] = {"label": label, "home": "0", "away": "0"}
                if is_home:
                    stat_map[label]["home"] = val_str
                else:
                    stat_map[label]["away"] = val_str
        # Order: important stats first
        priority = [
            "Ball Possession", "Total Shots", "Shots on Goal",
            "Shots off Goal", "Blocked Shots", "Corner Kicks",
            "Fouls", "Yellow Cards", "Red Cards",
            "Goalkeeper Saves", "Total passes", "Passes accurate", "Offsides",
        ]
        ordered = []
        for p in priority:
            if p in stat_map:
                ordered.append(stat_map.pop(p))
        ordered.extend(stat_map.values())
        out["statistics"] = ordered
    except Exception as e:
        log.warning(f"[FOCUS] Statistics fetch failed: {e}")

    # ── 5. Lineups ───────────────────────────────────────────────────────────
    try:
        raw_lineups = client.get("fixtures/lineups", {"fixture": fixture_id}, force_refresh=True)
        for team_block in raw_lineups:
            team_name = team_block.get("team", {}).get("name", "")
            formation = team_block.get("formation", "")
            def _player_entry(p):
                pl = p.get("player", {})
                pid = pl.get("id")
                return {
                    "number": pl.get("number", ""),
                    "name": pl.get("name", ""),
                    "photo": f"https://media.api-sports.io/football/players/{pid}.png" if pid else "",
                    "pos": pl.get("pos", ""),
                }
            start_xi = [_player_entry(p) for p in team_block.get("startXI", [])]
            subs = [_player_entry(p) for p in team_block.get("substitutes", [])]
            out["lineups"].append({
                "team": team_name,
                "formation": formation,
                "startXI": start_xi,
                "substitutes": subs,
            })
    except Exception as e:
        log.warning(f"[FOCUS] Lineups fetch failed: {e}")

    # ── 6. Head-to-Head ──────────────────────────────────────────────────────
    if home_team_id and away_team_id:
        try:
            raw_h2h = client.get(
                "fixtures/headtohead",
                {"h2h": f"{home_team_id}-{away_team_id}"},
            )
            # Exclude the current fixture (not yet history) and sort newest-first
            past_h2h = sorted(
                [m for m in raw_h2h if m.get("fixture", {}).get("id") != fixture_id],
                key=lambda m: m.get("fixture", {}).get("date", ""),
                reverse=True,
            )
            for m in past_h2h[:10]:
                fx_obj = m.get("fixture", {})
                teams = m.get("teams", {})
                goals = m.get("goals", {})
                date = fx_obj.get("date", "")[:10]
                home_name = teams.get("home", {}).get("name", "")
                away_name = teams.get("away", {}).get("name", "")
                hg = goals.get("home")
                ag = goals.get("away")
                score_str = (str(hg) + "–" + str(ag)) if hg is not None else "?"
                # Result from home side of THIS fixture
                if hg is not None and ag is not None:
                    if home_name == out["home_team"]:
                        result = "H" if hg > ag else ("A" if hg < ag else "D")
                    else:
                        result = "H" if ag > hg else ("A" if ag < hg else "D")
                else:
                    result = "?"
                out["h2h"].append({
                    "date": date,
                    "home": home_name,
                    "away": away_name,
                    "score": score_str,
                    "result": result,
                })
        except Exception as e:
            log.warning(f"[FOCUS] H2H fetch failed: {e}")

    # ── 7. Our predictions for this fixture ──────────────────────────────────
    try:
        with get_session() as s:
            pred_rows = s.execute(sa_text("""
                SELECT market, predicted_outcome, our_prob, calibrated_prob, odds_decimal, ev
                FROM prediction_records
                WHERE fixture_id = :fid AND is_legacy = 0
                ORDER BY market
            """), {"fid": fixture_id}).fetchall()

            # Fetch live odds from fixture_odds for enrichment when prediction has no odds
            odds_map = {}  # (market, outcome) -> best odds value
            needs_enrichment = any(r[4] is None for r in pred_rows)
            if needs_enrichment and pred_rows:
                odds_rows = s.execute(sa_text("""
                    SELECT odd_home, odd_draw, odd_away, odd_btts_yes, odd_btts_no,
                           odd_over, odd_under, odd_over15, odd_under15
                    FROM fixture_odds
                    WHERE fixture_id = :fid
                """), {"fid": fixture_id}).fetchall()
                # Pick the best (highest) available odds per slot
                def _best(col_idx):
                    vals = [r[col_idx] for r in odds_rows if r[col_idx] is not None]
                    return max(vals) if vals else None
                odds_map = {
                    ("h2h", "1"):    _best(0),
                    ("h2h", "X"):    _best(1),
                    ("h2h", "2"):    _best(2),
                    ("btts", "Yes"): _best(3),
                    ("btts", "No"):  _best(4),
                    ("ou25", "Over"):  _best(5),
                    ("ou25", "Under"): _best(6),
                    ("ou15", "Over"):  _best(7),
                    ("ou15", "Under"): _best(8),
                }

            # Deduplicate: keep one row per (market, outcome) — prefer row with odds
            seen = {}
            for r in pred_rows:
                key = (r[0], r[1])
                existing = seen.get(key)
                if existing is None or (existing[4] is None and r[4] is not None):
                    seen[key] = r
            deduped = list(seen.values())

            for r in deduped:
                market = r[0] or ""
                outcome = r[1] or ""
                our_prob = float(r[2]) if r[2] is not None else None
                cal_prob = float(r[3]) if r[3] is not None else our_prob
                odds = float(r[4]) if r[4] is not None else None
                ev = float(r[5]) if r[5] is not None else None

                # Enrich with live fixture_odds when stored odds are missing
                if odds is None:
                    live_odds = odds_map.get((market, outcome))
                    if live_odds and live_odds >= 1.0:
                        odds = float(live_odds)
                        prob_for_ev = cal_prob if cal_prob is not None else our_prob
                        if prob_for_ev is not None:
                            ev = round(prob_for_ev * odds - 1, 4)

                out["our_predictions"].append({
                    "market": market,
                    "outcome": outcome,
                    "prob": our_prob,
                    "odds": odds,
                    "ev": ev,
                })
    except Exception as e:
        log.warning(f"[FOCUS] Predictions fetch failed: {e}")

    # ── 7. League standings ──────────────────────────────────────────────────
    out["standings"] = []
    if league_id:
        try:
            with get_session() as s:
                rows = s.execute(sa_text("""
                    SELECT s.rank, s.team_name, s.points, s.played, s.won, s.drawn, s.lost,
                           s.goals_for, s.goals_against, s.goal_diff
                    FROM standings s
                    WHERE s.league_id = :lid
                      AND s.season = (
                          SELECT MAX(s2.season) FROM standings s2 WHERE s2.league_id = :lid
                      )
                    ORDER BY s.rank ASC
                """), {"lid": league_id}).fetchall()
                for r in rows:
                    out["standings"].append({
                        "rank": r[0],
                        "team": r[1] or "",
                        "points": r[2],
                        "played": r[3],
                        "won": r[4],
                        "drawn": r[5],
                        "lost": r[6],
                        "gf": r[7],
                        "ga": r[8],
                        "gd": r[9],
                    })
        except Exception as e:
            log.warning(f"[FOCUS] Standings fetch failed: {e}")

    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp


_model_cache: dict = {}

def _get_model_prediction(market: str, home_team_id: int, away_team_id: int, league_id: int, error_counts: dict | None = None) -> float | None:
    """Get prediction from trained LightGBM model.

    Returns probability or None if model not available.
    Handles both old format (model only) and new format (dict with model+calibrator).
    If error_counts dict is provided, errors are counted instead of logged.
    """
    global _model_cache
    import pickle
    import os
    import numpy as np

    # Check in-memory model cache first
    if market in _model_cache:
        model, calibrator = _model_cache[market]
    else:
        model_path = f'/opt/projects/bootball/data/model_{market}.pkl'
        if not os.path.exists(model_path):
            return None

        try:
            with open(model_path, 'rb') as f:
                obj = pickle.load(f)

            # Handle both old format (model directly) and new format (dict)
            if isinstance(obj, dict):
                model = obj['model']
                calibrator = obj.get('calibrator')
            else:
                model = obj
                calibrator = None

            # Cache the loaded model
            _model_cache[market] = (model, calibrator)
        except Exception as e:
            if error_counts is not None:
                error_counts[market] = error_counts.get(market, 0) + 1
            else:
                logger.warning(f"Failed to load model for {market}: {e}")
            return None

        # Get team stats - extract values before session closes
        with get_session() as s:
            home_standing = s.execute(
                select(Standing).where(Standing.team_id == home_team_id).where(Standing.season >= 2024)
            ).first()
            away_standing = s.execute(
                select(Standing).where(Standing.team_id == away_team_id).where(Standing.season >= 2024)
            ).first()

            if not home_standing or not away_standing:
                return None

            hs = home_standing[0]
            as_ = away_standing[0]

            # Extract all needed values while session is open
            features = [
                float(hs.rank or 15),
                float(as_.rank or 15),
                float((hs.goals_for or 1) - (hs.goals_against or 1)),
                float((as_.goals_for or 1) - (as_.goals_against or 1)),
                float(hs.goals_for or 1),
                float(as_.goals_for or 1),
                float(hs.goals_against or 1),
                float(as_.goals_against or 1),
                float(abs((hs.rank or 15) - (as_.rank or 15))),
            ]

        features = np.array([features])

        # Get raw probability (suppress sklearn feature name warning)
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='X does not have valid feature names')
            raw_probs = model.predict_proba(features)[0]

        # For binary, use positive class
        if len(raw_probs) == 2:
            raw_prob = float(raw_probs[1])
            calibrated = raw_prob
        else:
            # For 3-class H2H, return all probabilities (not max) for correct EV calculation
            raw_prob = float(np.max(raw_probs))
            calibrated = raw_prob

        # Apply isotonic calibration if available
        if calibrator:
            try:
                calibrated = calibrator.predict([raw_prob])[0]
                calibrated = max(0.01, min(0.99, calibrated))
            except Exception:
                pass

        return calibrated


@app.route('/api/predictions')
@require_auth
def api_predictions():
    days_str = request.args.get('days', '7')
    league_filter = request.args.get('league', '')
    market_filter = request.args.get('market', 'all')
    tz_name = request.args.get('tz', 'Europe/Stockholm')

    if days_str == 'all':
        now = datetime.utcnow()
        end = now + timedelta(days=365)
    else:
        days = int(days_str)
        now = datetime.utcnow()
        end = now + timedelta(days=days)

    markets = ['btts', 'ou25', 'ou15', 'h2h'] if market_filter == 'all' else [market_filter]
    cache = get_prediction_cache()

    with get_session() as s:
        # Only show fixtures where bookmaker odds exist — this is the page users act on
        from sqlalchemy import exists as _exists
        _has_odds = _exists().where(FixtureOdds.fixture_id == Fixture.id)
        query = (select(Fixture)
                 .where(Fixture.date >= now)
                 .where(Fixture.date <= end)
                 .where(Fixture.status == 'NS')
                 .where(_has_odds))
        if league_filter:
            query = query.where(Fixture.league_id == int(league_filter))
        fixtures = s.execute(query.order_by(Fixture.date)).scalars().all()

        results = []
        cache_hits = 0
        cache_misses = 0
        model_errors = {}

        for fix in fixtures:
            league_name = LEAGUE_NAMES.get(fix.league_id, '')
            home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id))
            away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id))
            home_logo = TEAM_LOGOS.get(fix.home_team_id)
            away_logo = TEAM_LOGOS.get(fix.away_team_id)
            league_flag = LEAGUE_FLAGS.get(fix.league_id)

            preds = s.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            pred_records = {p.market: p for p in preds}

            all_odds = s.execute(select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)).scalars().all()
            # Use MAX across bookmakers per column — avoids last-row-wins ambiguity
            from types import SimpleNamespace as _NS
            def _best_odds(rows, *cols):
                ns = {c: None for c in cols}
                for c in cols:
                    vals = [getattr(r, c) for r in rows if getattr(r, c) is not None]
                    if vals:
                        ns[c] = max(vals)
                return _NS(**ns)
            _h2h = [r for r in all_odds if r.bet_type == 'h2h']
            _btts = [r for r in all_odds if r.bet_type == 'btts']
            _ou = [r for r in all_odds if r.bet_type == 'over_under']
            h2h_row = _best_odds(_h2h, 'odd_home', 'odd_draw', 'odd_away') if _h2h else None
            btts_row = _best_odds(_btts, 'odd_btts_yes', 'odd_btts_no') if _btts else None
            ou_row = _best_odds(_ou, 'odd_over', 'odd_under', 'odd_over15', 'odd_under15') if _ou else None

            for market in markets:
                # Check in-memory cache first
                cached_pred = cache.get(fix.id, market)
                if cached_pred is not None:
                    # Format date in user's timezone
                    if cached_pred.get('date_utc'):
                        try:
                            from zoneinfo import ZoneInfo
                            dt_utc = datetime.fromisoformat(cached_pred['date_utc'])
                            if dt_utc.tzinfo is None:
                                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                            local_dt = dt_utc.astimezone(ZoneInfo(tz_name))
                            cached_pred['date'] = local_dt.strftime('%Y-%m-%d %H:%M')
                        except Exception:
                            pass
                    cached_pred['home_name'] = home
                    cached_pred['away_name'] = away
                    cached_pred['league_name'] = league_name
                    cached_pred['home_logo'] = home_logo
                    cached_pred['away_logo'] = away_logo
                    cached_pred['league_flag'] = league_flag
                    results.append(cached_pred)
                    cache_hits += 1
                    continue

                # Fallback: check PredictionRecord from DB (already loaded as pred_records).
                # Also show preliminary predictions (odds_decimal=None) so every NS fixture
                # appears in the table even before odds are available.
                db_pred = pred_records.get(market)
                if db_pred:
                    odds = db_pred.odds_decimal
                    ev = db_pred.ev
                    if odds is None or odds < 1.0:
                        # Enrich from already-loaded fixture_odds rows
                        live_odds = _extract_odds_for_outcome(
                            market, db_pred.predicted_outcome, h2h_row, btts_row, ou_row
                        )
                        if live_odds and live_odds >= 1.0:
                            odds = live_odds
                            prob_for_ev = db_pred.calibrated_prob or db_pred.our_prob
                            if prob_for_ev:
                                ev = round(prob_for_ev * odds - 1, 4)
                    has_odds = odds is not None and odds >= 1.0
                    cached_pred = {
                        'fixture_id': fix.id,
                        'date_utc': fix.date.isoformat() if fix.date else None,
                        'market': market,
                        'pick': db_pred.predicted_outcome,
                        'prob': db_pred.our_prob,
                        'calibrated_prob': round(db_pred.calibrated_prob or db_pred.our_prob, 3),
                        'odds': odds,
                        'ev': ev if has_odds else None,
                        'ev_positive': bool(ev and ev > 0),
                        'preliminary': not has_odds,
                    }
                    cache_prediction(fix.id, market, cached_pred)
                    cache_hits += 1
                    continue

                cache_misses += 1

                # Try trained model first
                model_prob = _get_model_prediction(market, fix.home_team_id, fix.away_team_id, fix.league_id, model_errors)

                if model_prob is None:
                    continue  # No fallback to stale PredictionRecord

                prob = model_prob

                if market == 'btts':
                    yes_odds = btts_row.odd_btts_yes if btts_row else None
                    no_odds = btts_row.odd_btts_no if btts_row else None
                    # Calculate EV for both outcomes and pick the better one
                    if yes_odds and no_odds and yes_odds > 0 and no_odds > 0:
                        ev_yes = (prob * yes_odds) - 1
                        ev_no = ((1 - prob) * no_odds) - 1
                        if ev_yes >= ev_no and ev_yes > 0:
                            odds, pick, ev = yes_odds, 'Yes', ev_yes
                        elif ev_no > 0:
                            odds, pick, ev = no_odds, 'No', ev_no
                        else:
                            continue
                    elif yes_odds and yes_odds > 0:
                        odds, pick, ev = yes_odds, 'Yes', (prob * yes_odds) - 1
                    else:
                        continue
                elif market == 'ou25':
                    over_odds = ou_row.odd_over if ou_row else None
                    under_odds = ou_row.odd_under if ou_row else None
                    if over_odds and under_odds and over_odds > 0 and under_odds > 0:
                        ev_over = (prob * over_odds) - 1
                        ev_under = ((1 - prob) * under_odds) - 1
                        if ev_over >= ev_under and ev_over > 0:
                            odds, pick, ev = over_odds, 'Over', ev_over
                        elif ev_under > 0:
                            odds, pick, ev = under_odds, 'Under', ev_under
                        else:
                            continue
                    elif over_odds and over_odds > 0:
                        odds, pick, ev = over_odds, 'Over', (prob * over_odds) - 1
                    else:
                        continue
                elif market == 'ou15':
                    over_odds = ou_row.odd_over15 if ou_row else None
                    under_odds = ou_row.odd_under15 if ou_row else None
                    if over_odds and under_odds and over_odds > 0 and under_odds > 0:
                        ev_over = (prob * over_odds) - 1
                        ev_under = ((1 - prob) * under_odds) - 1
                        if ev_over >= ev_under and ev_over > 0:
                            odds, pick, ev = over_odds, 'Over', ev_over
                        elif ev_under > 0:
                            odds, pick, ev = under_odds, 'Under', ev_under
                        else:
                            continue
                    elif over_odds and over_odds > 0:
                        odds, pick, ev = over_odds, 'Over', (prob * over_odds) - 1
                    else:
                        continue
                elif market == 'h2h':
                    home_odds = h2h_row.odd_home if h2h_row else None
                    draw_odds = h2h_row.odd_draw if h2h_row else None
                    away_odds = h2h_row.odd_away if h2h_row else None
                    # H2H is 3-way, pick highest EV using class-specific probabilities
                    if home_odds and draw_odds and away_odds:
                        # Get all 3 probabilities for correct EV calculation
                        model_path = f'/opt/projects/bootball/data/model_{market}.pkl'
                        if os.path.exists(model_path):
                            try:
                                with open(model_path, 'rb') as f:
                                    obj = pickle.load(f)
                                h2h_model = obj['model'] if isinstance(obj, dict) else obj

                                with get_session() as s:
                                    home_standing = s.execute(
                                        select(Standing).where(Standing.team_id == fix.home_team_id).where(Standing.season >= 2024)
                                    ).first()
                                    away_standing = s.execute(
                                        select(Standing).where(Standing.team_id == fix.away_team_id).where(Standing.season >= 2024)
                                    ).first()

                                    if home_standing and away_standing:
                                        hs = home_standing[0]
                                        as_ = away_standing[0]
                                        features = build_features_h2h(hs, as_)

                                import warnings
                                with warnings.catch_warnings():
                                    warnings.filterwarnings('ignore', message='X does not have valid feature names')
                                    h2h_raw_probs = h2h_model.predict_proba(features)[0]

                                prob_home = h2h_raw_probs[0]
                                prob_draw = h2h_raw_probs[1]
                                prob_away = h2h_raw_probs[2]

                                ev_home = (prob_home * home_odds) - 1
                                ev_draw = (prob_draw * draw_odds) - 1
                                ev_away = (prob_away * away_odds) - 1
                                best_ev = max(ev_home, ev_draw, ev_away)
                                if best_ev == ev_home and ev_home > 0:
                                    odds, pick, ev = home_odds, 'Home', ev_home
                                elif best_ev == ev_draw and ev_draw > 0:
                                    odds, pick, ev = draw_odds, 'Draw', ev_draw
                                elif ev_away > 0:
                                    odds, pick, ev = away_odds, 'Away', ev_away
                                else:
                                    continue
                            except Exception as e:
                                logger.warning(f"H2H prediction error: {e}")
                                continue
                        else:
                            continue
                    elif home_odds and home_odds > 0:
                        odds, pick, ev = home_odds, 'Home', (prob * home_odds) - 1
                    else:
                        continue
                else:
                    continue

                if ev <= 0:
                    continue

                # Apply calibration if available
                calibration = calibrate_prediction(market, prob)
                calibrated_prob = calibration.calibrated_prob
                confidence_low = calibration.confidence_low
                confidence_high = calibration.confidence_high
                sample_size = calibration.sample_size

                # Calculate confidence strength
                if sample_size < 1000:
                    confidence_level = "LOW"
                elif sample_size < 5000:
                    confidence_level = "MEDIUM"
                else:
                    confidence_level = "HIGH"

                # Store UTC date in cache, format on retrieval
                pred_result = {
                    'fixture_id': fix.id,
                    'date_utc': fix.date.isoformat() if fix.date else None,
                    'market': market,
                    'pick': pick,
                    'prob': prob,
                    'calibrated_prob': round(calibrated_prob, 3),
                    'odds': odds,
                    'ev': ev,
                    'ev_positive': bool(ev > 0),
                    'confidence': {
                        'low': round(confidence_low, 3),
                        'high': round(confidence_high, 3),
                        'level': confidence_level,
                        'sample_size': sample_size,
                    },
                }

                cache_prediction(fix.id, market, pred_result)

                # Format date for response
                if fix.date:
                    try:
                        from zoneinfo import ZoneInfo
                        if fix.date.tzinfo is None:
                            fix_dt = fix.date.replace(tzinfo=timezone.utc)
                        else:
                            fix_dt = fix.date
                        pred_result['date'] = fix_dt.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pred_result['date'] = fix.date.strftime('%Y-%m-%d %H:%M')
                else:
                    pred_result['date'] = None

                pred_result['home_name'] = home
                pred_result['away_name'] = away
                pred_result['league_name'] = league_name
                pred_result['home_logo'] = home_logo
                pred_result['away_logo'] = away_logo
                pred_result['league_flag'] = league_flag
                results.append(pred_result)

        if model_errors:
            total_errors = sum(model_errors.values())
            error_summary = ', '.join(f"{m}: {c}" for m, c in sorted(model_errors.items()))
            logger.warning(f"Model prediction errors ({total_errors}/{len(fixtures[:100]) * len(markets)}): {error_summary}")

        return jsonify(results)


# =============================================================================
# ROUTES: Betting
# =============================================================================

@app.route('/betting')
@require_auth
def betting_page():
    from src.state.betting_state import build_betting_state
    
    state = build_betting_state()
    
    round_number = state.active_round_number or 1
    initial = 1000  # Used for comparison in UI
    
    content = '''
<h1>Betting Dashboard</h1>

<div class="metric-row">
    <div class="metric-box">
        <div class="metric-label">Round</div>
        <div class="metric-value">#''' + str(round_number) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Balance</div>
        <div class="metric-value ''' + ('positive' if state.balance >= initial else 'negative') + '''">SEK ''' + str(int(state.balance)) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">ROI</div>
        <div class="metric-value ''' + ('positive' if state.roi >= 0 else 'negative') + '''">''' + ('+' if state.roi >= 0 else '') + str(int(state.roi)) + '''%</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Pending</div>
        <div class="metric-value pending">''' + str(state.pending_count) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Record (W/L)</div>
        <div class="metric-value">''' + str(state.wins) + 'W / ' + str(state.losses) + 'L' + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Pending Stake</div>
        <div class="metric-value">SEK ''' + str(int(state.pending_stake)) + '''</div>
    </div>
</div>

<div class="row" style="margin-top: 16px;">
    <div class="col">
        <button class="btn btn-success" onclick="placeBets()" id="btnPlace">Place Bets (Auto)</button>
        <button class="btn btn-danger" onclick="settleBets()" id="btnSettle">Settle Bets</button>
    </div>
</div>

<div id="msg" class="msg" style="display:none;"></div>

<h2 style="margin-top: 24px;">Pending Bets</h2>
<table>
    <thead>
        <tr>
            <th>Date</th>
            <th>Match</th>
            <th>Market</th>
            <th>Ver</th>
            <th>Pick</th>
            <th>Stake</th>
            <th>Odds</th>
            <th>EV</th>
            <th>Result</th>
        </tr>
    </thead>
    <tbody id="betsBody">
    </tbody>
</table>

<h2 style="margin-top: 24px;">Round History</h2>
<table>
    <thead>
        <tr>
            <th>Round</th>
            <th>Started</th>
            <th>Initial</th>
            <th>Ended</th>
            <th>P&L</th>
            <th>ROI</th>
            <th>Status</th>
        </tr>
    </thead>
    <tbody id="historyBody">
    </tbody>
</table>

<div id="roundDetailPanel" style="display:none; margin-top:16px; background:#1a1a1a; border:1px solid #333; border-radius:6px; padding:16px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <h3 id="roundDetailTitle" style="margin:0; font-size:1.1em;"></h3>
        <button onclick="document.getElementById('roundDetailPanel').style.display='none'" style="background:#333; border:none; color:#ccc; padding:4px 12px; cursor:pointer; border-radius:4px;">Close</button>
    </div>
    <div id="roundDetailSummary" style="display:flex; gap:20px; flex-wrap:wrap; margin-bottom:14px; font-size:0.9em; color:#aaa;"></div>
    <table>
        <thead>
            <tr>
                <th>Date</th><th>Match</th><th>Market</th><th>Ver</th><th>Pick</th>
                <th>Stake</th><th>Odds</th><th>EV</th><th>Result</th><th>P&L</th>
            </tr>
        </thead>
        <tbody id="roundDetailBody"></tbody>
    </table>
</div>

<script>
console.log("Loading betting data...");
function loadBets() {
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'status'})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        console.log("Got data:", d);
        
        // Update metrics if round data exists
        if (d.round) {
            var r = d.round;
            var els = document.querySelectorAll('.metric-row .metric-box');
            if (els.length >= 6) {
                // Round, Balance, ROI, Pending, Wins/Losses, Pending Stake
                var initial = r.balance + r.pending_stake - r.settled_pnl;
                var roi = r.settled_pnl / initial * 100;
                els[1].querySelector('.metric-value').textContent = 'SEK ' + Math.round(r.balance);
                els[1].querySelector('.metric-value').className = 'metric-value ' + (r.balance >= initial ? 'positive' : 'negative');
                els[2].querySelector('.metric-value').textContent = (roi >= 0 ? '+' : '') + Math.round(roi) + '%';
                els[2].querySelector('.metric-value').className = 'metric-value ' + (roi >= 0 ? 'positive' : 'negative');
                els[3].querySelector('.metric-value').textContent = r.pending;
                els[4].querySelector('.metric-value').textContent = r.wins + 'W / ' + (r.settled - r.wins) + 'L';
                els[5].querySelector('.metric-value').textContent = 'SEK ' + Math.round(r.pending_stake);
            }
        }
        
        // Build bets table
        var html = "";
        for (var i = 0; i < d.bets.length; i++) {
            var b = d.bets[i];
            var evPct = (b.ev * 100).toFixed(1) + '%';
            var result = b.settled ? (b.won ? 'WIN' : 'LOSS') : 'PENDING';
            var fidAttr = b.fixture_id ? ' data-fixture-id="' + b.fixture_id + '" data-home="' + (b.home || '').replace(/"/g,'&quot;') + '" data-away="' + (b.away || '').replace(/"/g,'&quot;') + '"' : '';
            html = html + "<tr" + fidAttr + ">" +
                "<td>" + b.date + "</td>" +
                "<td>" + b.home + " vs " + b.away + "</td>" +
                "<td>" + b.market + "</td>" +
                "<td>" + (b.model_version || '-') + "</td>" +
                "<td>" + b.outcome + "</td>" +
                "<td>SEK " + parseFloat(b.stake).toFixed(2) + "</td>" +
                "<td>" + b.odds + "</td>" +
                "<td>" + evPct + "</td>" +
                "<td>" + result + "</td>" +
                "</tr>";
        }
        document.getElementById('betsBody').innerHTML = html;
        
        // Load history
        loadHistory();
    });
}

function loadHistory() {
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'history'})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        console.log("History:", d);
        var html = "";
        for (var i = 0; i < d.history.length; i++) {
            var h = d.history[i];
            var pnl = (h.total_pnl !== null && h.total_pnl !== undefined) ? h.total_pnl.toFixed(2) : '—';
            var roi = (h.roi_pct !== null && h.roi_pct !== undefined) ? h.roi_pct.toFixed(1) : '—';
            var pnlColor = (h.total_pnl > 0) ? 'color:#4caf50' : (h.total_pnl < 0 ? 'color:#f44336' : '');
            html = html + '<tr style="cursor:pointer;" onclick="showRoundDetail(' + h.id + ', ' + h.round_number + ')" data-round-id="' + h.id + '">' +
                "<td>#" + h.round_number + "</td>" +
                "<td>" + (h.started_at || '—') + "</td>" +
                "<td>SEK " + h.initial_bankroll + "</td>" +
                "<td>" + (h.ended_at || '—') + "</td>" +
                '<td style="' + pnlColor + '">SEK ' + pnl + "</td>" +
                "<td>" + roi + "%</td>" +
                "<td>" + h.status + "</td>" +
                "</tr>";
        }
        document.getElementById('historyBody').innerHTML = html;
    });
}

loadBets();

function showRoundDetail(roundId, roundNumber) {
    var panel = document.getElementById('roundDetailPanel');
    var title = document.getElementById('roundDetailTitle');
    var body = document.getElementById('roundDetailBody');
    var summary = document.getElementById('roundDetailSummary');

    title.textContent = 'Round #' + roundNumber + ' — loading…';
    panel.style.display = 'block';
    panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    body.innerHTML = '<tr><td colspan="10" style="color:#888; padding:12px;">Loading…</td></tr>';
    summary.innerHTML = '';

    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'round_detail', round_id: roundId})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (!d.ok) { body.innerHTML = '<tr><td colspan="10">Error: ' + (d.error || 'unknown') + '</td></tr>'; return; }

        var r = d.round || {};
        var stats = d.stats || {};
        title.textContent = 'Round #' + roundNumber + (r.status ? '  (' + r.status + ')' : '');

        var pnlVal = (r.total_pnl !== null && r.total_pnl !== undefined) ? r.total_pnl.toFixed(2) : '—';
        var roiVal = (r.roi_pct !== null && r.roi_pct !== undefined) ? r.roi_pct.toFixed(1) + '%' : '—';
        var wins = stats.wins || 0;
        var losses = stats.losses || 0;
        var pending = stats.pending || 0;
        var totalBets = (r.total_bets !== null && r.total_bets !== undefined) ? r.total_bets : (wins + losses + pending);
        var totalStaked = (r.total_staked !== null && r.total_staked !== undefined) ? 'SEK ' + parseFloat(r.total_staked).toFixed(2) : '—';

        summary.innerHTML =
            '<span><b>' + wins + 'W / ' + losses + 'L' + (pending > 0 ? ' / ' + pending + 'P' : '') + '</b></span>' +
            '<span>Bets: ' + totalBets + '</span>' +
            '<span>Staked: ' + totalStaked + '</span>' +
            '<span style="' + (r.total_pnl > 0 ? 'color:#4caf50' : r.total_pnl < 0 ? 'color:#f44336' : '') + '">P&L: SEK ' + pnlVal + '</span>' +
            '<span>ROI: ' + roiVal + '</span>' +
            '<span>Started: ' + (r.started_at || '—') + '</span>' +
            (r.ended_at ? '<span>Ended: ' + r.ended_at + '</span>' : '');

        var html = '';
        if (!d.bets || d.bets.length === 0) {
            html = '<tr><td colspan="10" style="color:#888; padding:12px;">No bets recorded for this round.</td></tr>';
        } else {
            for (var i = 0; i < d.bets.length; i++) {
                var b = d.bets[i];
                var result = b.settled ? (b.won ? '<span style="color:#4caf50">WIN</span>' : '<span style="color:#f44336">LOSS</span>') : '<span style="color:#888">PENDING</span>';
                var evStr = (b.ev !== null && b.ev !== undefined) ? (b.ev * 100).toFixed(1) + '%' : '—';
                var pnlStr = (b.pnl !== null && b.pnl !== undefined) ? (b.pnl >= 0 ? '<span style="color:#4caf50">+' : '<span style="color:#f44336">') + parseFloat(b.pnl).toFixed(2) + '</span>' : '—';
                html += '<tr>' +
                    '<td>' + b.date + '</td>' +
                    '<td>' + (b.home || '?') + ' vs ' + (b.away || '?') + '</td>' +
                    '<td>' + (b.market || '—') + '</td>' +
                    '<td style="font-size:0.8em;">' + (b.model_version || '—') + '</td>' +
                    '<td>' + (b.outcome || '—') + '</td>' +
                    '<td>SEK ' + parseFloat(b.stake || 0).toFixed(2) + '</td>' +
                    '<td>' + (b.odds || '—') + '</td>' +
                    '<td>' + evStr + '</td>' +
                    '<td>' + result + '</td>' +
                    '<td>' + pnlStr + '</td>' +
                    '</tr>';
            }
        }
        body.innerHTML = html;
    })
    .catch(function(e) {
        body.innerHTML = '<tr><td colspan="10">Request failed: ' + e + '</td></tr>';
    });
}

function placeBets() {
    var btn = document.getElementById('btnPlace');
    var msg = document.getElementById('msg');
    btn.disabled = true;
    btn.textContent = 'Placing…';
    msg.style.display = 'none';
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'include',
        body: JSON.stringify({action: 'place-bets'})
    })
    .then(r => r.json())
    .then(d => {
        btn.disabled = false;
        btn.textContent = 'Place Bets (Auto)';
        msg.style.display = 'block';
        if (d.ok) {
            msg.className = 'msg msg-success';
            msg.textContent = d.message || 'Done';
            if (d.placed > 0) setTimeout(() => location.reload(), 1200);
        } else {
            msg.className = 'msg msg-error';
            msg.textContent = d.error || 'Error placing bets';
        }
    })
    .catch(e => {
        btn.disabled = false;
        btn.textContent = 'Place Bets (Auto)';
        msg.style.display = 'block';
        msg.className = 'msg msg-error';
        msg.textContent = 'Request failed: ' + e;
    });
}

function settleBets() {
    if (!confirm('Settle all pending bets?')) return;
    fetch('/api/settle_bets', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                alert('Settled: ' + d.settled_count + ' bets, P/L: ' + d.total_pnl.toFixed(2));
                location.reload();
            } else {
                alert(d.error || 'Error');
            }
        })
        .catch(e => alert('Error: ' + e));
}
</script>
'''
    return page(content)


@app.route('/betting/run/<run_id>')
@require_auth
def betting_run_page(run_id):
    """Detail view for a specific experiment run."""
    from sqlalchemy import text
    tz_name = request.args.get('tz', 'Europe/Stockholm')

    with get_session() as s:
        rd = s.execute(text("""
            SELECT run_id, mode, start_timestamp, end_timestamp, bankroll_snapshot, status
            FROM experiment_runs WHERE run_id = :run_id
        """), {'run_id': run_id}).fetchone()
        
        if not rd:
            return page('<h1>Run not found</h1><p><a href="/betting">Back to Betting</a></p>')
        
        run_id_val = rd[0]
        mode = rd[1]
        start_ts = rd[2]
        end_ts = rd[3]
        bankroll = rd[4] or 1000.0
        
        bets = s.execute(text("""
            SELECT * FROM placed_bets 
            WHERE run_id = :run_id
            ORDER BY placed_at DESC
        """), {'run_id': run_id_val}).fetchall()
        
        def format_date(dt, tz):
            if dt is None:
                return '-'
            try:
                from zoneinfo import ZoneInfo
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt.replace('Z', '').split('+')[0].split('.')[0])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(ZoneInfo(tz)).strftime('%Y-%m-%d %H:%M')
            except Exception:
                return str(dt)[:16]
        
        bets_list = []
        for b in bets:
            fix_id = b[2]  # fixture_id column
            fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
            home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id)) if fix else '?'
            away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id)) if fix else '?'
            
            model_ver = b[18]  # calibration_version_id (most specific label)
            if not model_ver:
                model_ver_id = b[16]  # model_version_id column
                if model_ver_id:
                    mv = s.execute(select(ModelVersion).where(ModelVersion.id == model_ver_id)).scalar_one_or_none()
                    if mv:
                        model_ver = mv.version_label or f"v{mv.version_number:02d}_c00"
            
            score = None
            fix_status = None
            if fix:
                fix_status = fix.status
                if fix.goals_home is not None and fix.goals_away is not None:
                    score = f"{fix.goals_home}-{fix.goals_away}"

            bets_list.append({
                'id': b[0],
                'home': home,
                'away': away,
                'date': format_date(b[14], tz_name),  # placed_at column
                'market': b[3],  # market column
                'model_version': model_ver,
                'outcome': b[4],  # outcome column
                'stake': b[5],  # stake column
                'odds': b[6],  # odds column
                'ev': b[8],  # ev column
                'pnl': b[13],  # pnl column
                'settled': b[10],  # settled column
                'won': b[12],  # won column
                'result': b[11],  # actual_result column
                'score': score,
                'fix_status': fix_status,
            })
    
    status_val = rd[5] if len(rd) > 5 else 'unknown'
    start_str = start_ts[:16].replace('T', ' ') if start_ts else '-'
    end_str = end_ts[:16].replace('T', ' ') if end_ts else 'Active'
    bankroll_val = float(bankroll) if bankroll else 1000.0
    
    content = '''
<h1>Run ''' + run_id[:8] + '''... Details</h1>

<div class="metric-row">
    <div class="metric-box">
        <div class="metric-label">Status</div>
        <div class="metric-value">''' + ('Active' if status_val == 'active' else 'Closed') + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Started</div>
        <div class="metric-value">''' + start_str + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Ended</div>
        <div class="metric-value">''' + end_str + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Initial</div>
        <div class="metric-value">SEK ''' + str(bankroll_val) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Mode</div>
        <div class="metric-value">''' + mode + '''</div>
    </div>
</div>
    <div class="metric-box">
        <div class="metric-label">Bets</div>
        <div class="metric-value">''' + str(len(bets_list)) + '''</div>
    </div>
</div>

<p><a href="/betting" class="btn btn-secondary">&larr; Back to Betting</a></p>

<h2 style="margin-top: 24px;">Bets (''' + str(len(bets_list)) + ''')</h2>
<table>
    <thead>
        <tr>
            <th>Date</th>
            <th>Match</th>
            <th>Market</th>
            <th>Ver</th>
            <th>Pick</th>
            <th>Stake</th>
            <th>Odds</th>
            <th>EV</th>
            <th>Score</th>
            <th>P&L</th>
            <th>Result</th>
        </tr>
    </thead>
    <tbody id="betsBody">
'''

    for b in bets_list:
        result_class = 'pending'
        result_text = 'PENDING'
        if b['settled']:
            result_class = 'win' if b['won'] else 'loss'
            result_text = 'WIN' if b['won'] else 'LOSS'

        score_display = b.get('score') or '-'
        fix_status = b.get('fix_status') or ''
        live_statuses = {'1H', '2H', 'HT', 'ET', 'BT', 'P', 'INT', 'LIVE'}
        if fix_status in live_statuses and b.get('score'):
            score_display = f'<span style="color:#f90;font-weight:bold">{b["score"]} <small>{fix_status}</small></span>'
        elif b.get('score') and fix_status in ('FT', 'AET', 'PEN'):
            score_display = f'{b["score"]} <small style="color:#888">{fix_status}</small>'

        content += '''
        <tr>
            <td>''' + str(b['date']) + '''</td>
            <td>''' + str(b['home']) + ' vs ' + str(b['away']) + '''</td>
            <td>''' + str(b['market']) + '''</td>
            <td>''' + (str(b['model_version']) if b['model_version'] else '-') + '''</td>
            <td>''' + str(b['outcome']) + '''</td>
            <td>SEK ''' + f"{float(b['stake']):.2f}" + '''</td>
            <td>''' + str(b['odds']) + '''</td>
            <td>''' + str(round((b['ev'] or 0) * 100, 1)) + '''%</td>
            <td>''' + score_display + '''</td>
            <td class="''' + ('positive' if b['pnl'] else 'negative') + '''">''' + str(b['pnl'] or 0) + '''</td>
            <td class="''' + result_class + '''">''' + result_text + '''</td>
        </tr>
'''
    
    content += '''
    </tbody>
</table>

<p><a href="/betting" class="btn btn-secondary">&larr; Back to Betting</a></p>
'''
    return page(content)


# =============================================================================
# ROUTES: Tracking
# =============================================================================

@app.route('/tracking')
@require_auth
def tracking_page():
    content = '''
<h1>Prediction Tracking</h1>

<div class="row" style="margin-bottom: 24px;">
    <div class="col card">
        <div class="card-title">Filter</div>
        <select id="marketFilter">
            <option value="">All Markets</option>
            <option value="btts">BTTS</option>
            <option value="ou25">O/U 2.5</option>
            <option value="ou15">O/U 1.5</option>
            <option value="h2h">1X2</option>
        </select>
        <select id="statusFilter">
            <option value="">All</option>
            <option value="settled">Settled</option>
            <option value="pending">Pending</option>
        </select>
        <div style="margin-top: 8px;">
            <label>From: <input type="date" id="fromDate"></label>
            <label>To: <input type="date" id="toDate"></label>
        </div>
        <div style="margin-top: 8px;">
            <label>Per page:
                <select id="pageSize">
                    <option value="20" selected>20</option>
                    <option value="40">40</option>
                    <option value="60">60</option>
                    <option value="80">80</option>
                    <option value="100">100</option>
                </select>
            </label>
        </div>
    </div>
    <div class="col card">
        <div class="card-title">Stats</div>
        <div id="statsBox">Loading...</div>
    </div>
</div>

<div id="calibrationBox" style="display:none;"></div>
<div id="oddsBox" style="display:none;"></div>

<div id="pageNavTop" style="display:none; margin: 12px 0;">
    <button onclick="changePage(-1)" id="prevBtn">← Prev</button>
    <span id="pageNumbers"></span>
    <button onclick="changePage(1)" id="nextBtn">Next →</button>
    <span id="pageInfo" style="margin-left: 12px;"></span>
</div>

<table>
    <thead>
        <tr>
            <th><button onclick="toggleDateSort()" id="dateSortBtn" style="background:none;border:none;color:inherit;cursor:pointer;padding:0;font-weight:bold;">Date ▲</button></th>
            <th>Match</th>
            <th>Market</th>
            <th>Ver</th>
            <th>Pick</th>
            <th>Prob</th>
            <th>Odds</th>
            <th>Bookmaker</th>
            <th>EV</th>
            <th>Score</th>
            <th>Result</th>
            <th>P&L</th>
        </tr>
    </thead>
    <tbody id="trackingBody">
    </tbody>
</table>

<div id="pageNavBottom" style="display:none; margin: 12px 0;">
    <button onclick="changePage(-1)" id="prevBtn2">← Prev</button>
    <span id="pageNumbers2"></span>
    <button onclick="changePage(1)" id="nextBtn2">Next →</button>
    <span id="pageInfo2" style="margin-left: 12px;"></span>
</div>

<script>
let currentPage = 1;
let pageSize = 20;
let dateSortDesc = false;

function toggleDateSort() {
    dateSortDesc = !dateSortDesc;
    document.getElementById('dateSortBtn').textContent = 'Date ' + (dateSortDesc ? '▼' : '▲');
    currentPage = 1;
    loadTracking();
}

function formatLocalDateTime(isoString) {
    if (!isoString) return '-';
    try {
        const date = new Date(isoString);
        return date.toLocaleString(undefined, {
            timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        });
    } catch {
        return isoString.slice(0, 16);
    }
}
let totalResults = 0;

function loadTracking() {
    const market = document.getElementById('marketFilter').value;
    const status = document.getElementById('statusFilter').value;
    const fromDate = document.getElementById('fromDate').value;
    const toDate = document.getElementById('toDate').value;
    pageSize = parseInt(document.getElementById('pageSize').value);

    let url = '/api/predictions/recent?page=' + currentPage + '&page_size=' + pageSize;
    if (market) url += '&market=' + market;
    if (status === 'settled') url += '&settled=true';
    else if (status === 'pending') url += '&settled=false';
    if (fromDate) url += '&from_date=' + fromDate;
    if (toDate) url += '&to_date=' + toDate;
    url += '&sort_desc=' + dateSortDesc;

    fetch(url, {credentials: 'include'})
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(d => {
            totalResults = d.total || 0;
            const tbody = document.getElementById('trackingBody');
            tbody.innerHTML = (d.results || []).map(r => {
                const homeLogo = r.home_logo ? '<img src="' + r.home_logo + '" style="width:20px;height:20px;vertical-align:middle;margin-right:4px;">' : '';
                const awayLogo = r.away_logo ? '<img src="' + r.away_logo + '" style="width:20px;height:20px;vertical-align:middle;margin-left:4px;">' : '';
                const fidAttr = r.fixture_id ? ' data-fixture-id="' + r.fixture_id + '" data-home="' + (r.home || '').replace(/"/g,'&quot;') + '" data-away="' + (r.away || '').replace(/"/g,'&quot;') + '"' : '';
                return '<tr' + fidAttr + '>' +
                '<td>' + formatLocalDateTime(r.date) + '</td>' +
                '<td>' + homeLogo + r.home + ' vs ' + r.away + awayLogo + '</td>' +
                '<td>' + (r.market || '') + '</td>' +
                '<td>' + (r.model_version || '-') + '</td>' +
                '<td>' + (r.predicted || '-') + '</td>' +
                '<td>' + (r.prob ? (r.prob * 100).toFixed(0) + '%' : '-') + '</td>' +
                '<td>' + (r.odds ? r.odds : (r.preliminary ? '<span style="color:#8b949e;font-size:0.8em;">No odds</span>' : '-')) + '</td>' +
                '<td>' + (r.bookmaker || '-') + '</td>' +
                '<td>' + (r.ev ? (r.ev * 100).toFixed(1) + '%' : (r.preliminary ? '<span style="color:#8b949e;font-size:0.8em;">—</span>' : '-')) + '</td>' +
                '<td>' + (r.actual || '-') + '</td>' +
                '<td class="' + (r.settled ? (r.won ? 'win' : 'loss') : 'pending') + '">' +
                    (r.preliminary ? '<span style="color:#8b949e;font-size:0.8em;">PRELIMINARY</span>' : r.settled ? (r.won ? 'WIN' : 'LOSS') : 'PENDING') +
                '</td>' +
                '<td>' + (r.pnl != null && typeof r.pnl === 'number' ? (r.pnl >= 0 ? '+' : '') + r.pnl.toFixed(2) : '-') + '</td>' +
                '</tr>';
            }).join('');

            // Generation stats table (all-time, not date-filtered)
            const st = d.stats || {};
            const gens = st.generation || {};
            const markets = ['h2h', 'btts', 'ou25', 'ou15'];
            const genOrder = ['legacy', 'base', 'vcl'];
            const genColor = { legacy: '#8b949e', base: '#58a6ff', vcl: '#3fb950' };

            let genHtml = '<table style="width:100%;font-size:0.85em;border-collapse:collapse;">';
            genHtml += '<thead><tr style="border-bottom:1px solid #30363d;">' +
                '<th style="text-align:left;padding:4px 8px;">Generation</th>' +
                '<th style="padding:4px 8px;">Settled</th>' +
                '<th style="padding:4px 8px;">Wins</th>' +
                '<th style="padding:4px 8px;">Win%</th>' +
                '<th style="padding:4px 8px;">Pending</th>' +
                markets.map(m => '<th style="padding:4px 8px;">' + m.toUpperCase() + '</th>').join('') +
                '</tr></thead><tbody>';

            for (const g of genOrder) {
                const gd = gens[g];
                if (!gd) continue;
                const pct = gd.win_pct || 0;
                const pctColor = pct >= 50 ? '#3fb950' : pct >= 40 ? '#d29922' : '#f85149';
                genHtml += '<tr style="border-bottom:1px solid #21262d;">' +
                    '<td style="padding:4px 8px;color:' + genColor[g] + ';font-weight:bold;">' + gd.label + '</td>' +
                    '<td style="padding:4px 8px;text-align:center;">' + (gd.settled || 0) + '</td>' +
                    '<td style="padding:4px 8px;text-align:center;">' + (gd.wins || 0) + '</td>' +
                    '<td style="padding:4px 8px;text-align:center;color:' + pctColor + ';font-weight:bold;">' + pct + '%</td>' +
                    '<td style="padding:4px 8px;text-align:center;color:#8b949e;">' + (gd.pending || 0) + '</td>' +
                    markets.map(m => {
                        const ms = (gd.markets || {})[m] || {};
                        const mp = ms.win_pct || 0;
                        const mc = mp >= 50 ? '#3fb950' : mp >= 40 ? '#d29922' : '#f85149';
                        return ms.settled
                            ? '<td style="padding:4px 8px;text-align:center;">' +
                              ms.wins + '/' + ms.settled +
                              ' <span style="color:' + mc + ';">(' + mp + '%)</span></td>'
                            : '<td style="padding:4px 8px;text-align:center;color:#484f58;">—</td>';
                    }).join('') +
                    '</tr>';
            }
            genHtml += '</tbody></table>';
            document.getElementById('statsBox').innerHTML = genHtml;
            document.getElementById('calibrationBox').innerHTML = '';
            document.getElementById('oddsBox').innerHTML = '';

            updatePagination();
        })
        .catch(err => {
            console.error('loadTracking error:', err);
            document.getElementById('trackingBody').innerHTML = '<tr><td colspan="12">Error: ' + err.message + '</td></tr>';
        });
}

function updatePagination() {
    const totalPages = Math.ceil(totalResults / pageSize);
    const showNav = totalPages > 1;

    ['pageNavTop', 'pageNavBottom', 'prevBtn', 'nextBtn', 'prevBtn2', 'nextBtn2'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = showNav ? 'inline' : 'none';
    });

    if (!showNav) return;

    // Page numbers
    let pagesHtml = '';
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPage - 1 && i <= currentPage + 1)) {
            pagesHtml += '<button onclick="goToPage(' + i + ')" class="' + (i === currentPage ? 'btn btn-primary' : '') + '" style="margin:0 2px;">' + i + '</button>';
        } else if (i === currentPage - 2 || i === currentPage + 2) {
            pagesHtml += '<span style="margin:0 4px;">...</span>';
        }
    }
    document.getElementById('pageNumbers').innerHTML = pagesHtml;
    document.getElementById('pageNumbers2').innerHTML = pagesHtml;

    // Info
    const start = (currentPage - 1) * pageSize + 1;
    const end = Math.min(currentPage * pageSize, totalResults);
    document.getElementById('pageInfo').textContent = start + '-' + end + ' of ' + totalResults;
    document.getElementById('pageInfo2').textContent = start + '-' + end + ' of ' + totalResults;

    // Buttons
    document.getElementById('prevBtn').disabled = currentPage === 1;
    document.getElementById('nextBtn').disabled = currentPage === totalPages;
    document.getElementById('prevBtn2').disabled = currentPage === 1;
    document.getElementById('nextBtn2').disabled = currentPage === totalPages;
}

function changePage(delta) {
    const totalPages = Math.ceil(totalResults / pageSize);
    currentPage = Math.max(1, Math.min(totalPages, currentPage + delta));
    loadTracking();
}

function goToPage(page) {
    currentPage = page;
    loadTracking();
}

// Default: from today, no end date (user sets to_date manually)
const today = new Date().toISOString().split('T')[0];
document.getElementById('fromDate').value = today;
document.getElementById('toDate').value = '';

// Now load with the date preset
loadTracking();

document.getElementById('statusFilter').addEventListener('change', () => { currentPage = 1; loadTracking(); });
document.getElementById('marketFilter').addEventListener('change', () => { currentPage = 1; loadTracking(); });
document.getElementById('fromDate').addEventListener('change', () => { currentPage = 1; loadTracking(); });
document.getElementById('toDate').addEventListener('change', () => { currentPage = 1; loadTracking(); });
document.getElementById('pageSize').addEventListener('change', () => { currentPage = 1; loadTracking(); });
</script>
'''
    return page(content)


@app.route('/api/predictions/recent')
@require_auth
def api_predictions_recent():
    from datetime import datetime as dt
    
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    settled_only = request.args.get('settled', 'false') == 'true'
    pending_only = request.args.get('settled', 'false') == 'false' and request.args.get('settled') is not None
    market = request.args.get('market', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    sort_desc = request.args.get('sort_desc', 'true') == 'true'

    with get_session() as s:
        query = (
            select(PredictionRecord, Fixture.home_team_id, Fixture.away_team_id, Fixture.date, Fixture.goals_home, Fixture.goals_away, Fixture.status)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.is_legacy == False)
        )

        if settled_only:
            query = query.where(PredictionRecord.settled == True)
        elif pending_only:
            query = query.where(PredictionRecord.settled == False)
        # else: no filter - show all (settled and pending)

        if market:
            query = query.where(PredictionRecord.market == market)

        _from_dt = dt.strptime(from_date, '%Y-%m-%d') if from_date else None
        _to_dt = dt.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S') if to_date else None

        if _from_dt:
            query = query.where(Fixture.date >= _from_dt)
        if _to_dt:
            query = query.where(Fixture.date <= _to_dt)

        # Get total count before pagination
        count_query = (
            select(PredictionRecord.id)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .where(PredictionRecord.is_legacy == False)
        )
        if settled_only:
            count_query = count_query.where(PredictionRecord.settled == True)
        elif pending_only:
            count_query = count_query.where(PredictionRecord.settled == False)
        if market:
            count_query = count_query.where(PredictionRecord.market == market)
        if _from_dt:
            count_query = count_query.where(Fixture.date >= _from_dt)
        if _to_dt:
            count_query = count_query.where(Fixture.date <= _to_dt)

        total = s.execute(select(func.count()).select_from(count_query.subquery())).scalar() or 0

        # All-time stats by system generation — NOT date-filtered
        # legacy    = is_legacy=1 (pre-pipeline records)
        # base      = is_legacy=0, no calibration_version_id (base model + global cal only)
        # vcl       = is_legacy=0, calibration_version_id set (full VxxCyyLzzzz)
        from sqlalchemy import text as _text
        gen_rows = s.execute(_text("""
            SELECT
                CASE
                    WHEN p.is_legacy = 1 THEN 'legacy'
                    WHEN p.calibration_version_id IS NOT NULL THEN 'vcl'
                    ELSE 'base'
                END AS gen,
                p.market,
                SUM(CASE WHEN p.settled=1 THEN 1 ELSE 0 END) AS settled,
                SUM(CASE WHEN p.settled=1 AND p.won=1 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN p.settled=0 THEN 1 ELSE 0 END) AS pending
            FROM prediction_records p
            GROUP BY gen, p.market
        """)).fetchall()

        _GEN_ORDER = ['legacy', 'base', 'vcl']
        _GEN_LABEL = {'legacy': 'Legacy', 'base': 'Base model', 'vcl': 'Full VCL'}
        generation_stats = {g: {'label': _GEN_LABEL[g], 'markets': {}, 'settled': 0, 'wins': 0, 'pending': 0} for g in _GEN_ORDER}

        for gen, market, settled, wins, pending in gen_rows:
            if gen not in generation_stats:
                continue
            s_v, w_v, p_v = (settled or 0), (wins or 0), (pending or 0)
            generation_stats[gen]['markets'][market] = {
                'settled': s_v, 'wins': w_v, 'pending': p_v,
                'win_pct': round(w_v / s_v * 100) if s_v else 0,
            }
            generation_stats[gen]['settled'] += s_v
            generation_stats[gen]['wins'] += w_v
            generation_stats[gen]['pending'] += p_v

        for g in generation_stats:
            sv = generation_stats[g]['settled']
            wv = generation_stats[g]['wins']
            generation_stats[g]['win_pct'] = round(wv / sv * 100) if sv else 0

        stats = {'generation': generation_stats}

        order_col = Fixture.date.desc() if sort_desc else Fixture.date.asc()
        query = query.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
        rows = s.execute(query).all()

        # Batch-fetch fixture_odds for any predictions that have null odds_decimal
        fixture_ids_needing_odds = {
            pred.fixture_id for pred, *_ in rows if pred.odds_decimal is None
        }
        enriched_odds_map = {}  # (fixture_id, market, outcome) -> odds float
        if fixture_ids_needing_odds:
            fo_rows = s.execute(
                select(FixtureOdds).where(FixtureOdds.fixture_id.in_(fixture_ids_needing_odds))
            ).scalars().all()
            # Group by fixture_id and build per-fixture odds dicts using MAX per column
            from collections import defaultdict
            fo_by_fixture = defaultdict(list)
            for fo in fo_rows:
                fo_by_fixture[fo.fixture_id].append(fo)
            _col_map = [
                ("h2h", "1", "odd_home"), ("h2h", "X", "odd_draw"), ("h2h", "2", "odd_away"),
                ("btts", "Yes", "odd_btts_yes"), ("btts", "No", "odd_btts_no"),
                ("ou25", "Over", "odd_over"), ("ou25", "Under", "odd_under"),
                ("ou15", "Over", "odd_over15"), ("ou15", "Under", "odd_under15"),
            ]
            for fid, fo_list in fo_by_fixture.items():
                for mkt, out, col in _col_map:
                    vals = [getattr(r, col) for r in fo_list if getattr(r, col) is not None]
                    if vals:
                        enriched_odds_map[(fid, mkt, out)] = max(vals)

        # Batch-load ModelVersions once to avoid N+1 per row
        all_model_versions = s.execute(select(ModelVersion)).scalars().all()
        model_version_labels = {
            mv.id: (mv.version_label or f"v{mv.version_number:02d}_c00")
            for mv in all_model_versions
        }

        results = []
        for pred, home_id, away_id, fix_date, goals_home, goals_away, fix_status in rows:
            home = TEAM_NAMES.get(home_id, str(home_id))
            away = TEAM_NAMES.get(away_id, str(away_id))
            home_logo = TEAM_LOGOS.get(home_id)
            away_logo = TEAM_LOGOS.get(away_id)
            if goals_home is not None and goals_away is not None:
                score_str = f"{goals_home}-{goals_away}"
                if fix_status and fix_status not in ("FT", "AET", "PEN"):
                    score_str += f" ({fix_status})"
                actual = score_str
            else:
                actual = None

            model_ver = pred.calibration_version_id  # most specific label
            if not model_ver and pred.model_version_id:
                model_ver = model_version_labels.get(pred.model_version_id)

            # P&L must only derive from stored odds_decimal — never enriched odds
            pnl = None
            if pred.settled and pred.odds_decimal:
                pnl = (pred.odds_decimal - 1) if pred.won else -1

            # Enrich display odds/ev from fixture_odds when prediction was saved as preliminary
            display_odds = pred.odds_decimal
            display_ev = pred.ev
            if display_odds is None and pred.predicted_outcome:
                enriched = enriched_odds_map.get((pred.fixture_id, pred.market, pred.predicted_outcome))
                if enriched and enriched >= 1.0:
                    display_odds = enriched
                    prob_for_ev = pred.calibrated_prob or pred.our_prob
                    if prob_for_ev:
                        display_ev = round(prob_for_ev * enriched - 1, 4)

            results.append({
                'fixture_id': pred.fixture_id,
                'home': home,
                'away': away,
                'home_logo': home_logo,
                'away_logo': away_logo,
                'date': fix_date.isoformat() + 'Z' if fix_date else None,
                'market': pred.market,
                'model_version': model_ver,
                'predicted': pred.predicted_outcome,
                'actual': actual,
                'prob': pred.our_prob,
                'odds': display_odds,
                'bookmaker': pred.bookmaker,
                'ev': display_ev,
                'settled': pred.settled,
                'won': pred.won,
                'pnl': pnl,
                'preliminary': pred.odds_decimal is None and not pred.settled,
            })

        return jsonify({'results': results, 'total': total, 'stats': stats})


# =============================================================================
# ROUTES: Admin
# =============================================================================

@app.route('/admin')
@require_auth
def admin_page():
    content = '''
<h1>Admin Panel</h1>

<div class="row">
    <div class="col">
        <div class="card">
            <div class="card-title">System Actions</div>
            <button class="btn btn-primary" onclick="runMaintenance()">Run Maintenance</button>
            <button class="btn btn-primary" onclick="runDailyRun()">Run Daily Run</button>
            <button class="btn btn-danger" onclick="settleBets()">Settle Bets</button>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">System Status</div>
            <div id="systemStatus">Loading...</div>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Closing Line Value (CLV)</div>
            <p style="color:#8b949e;font-size:12px;margin:0 0 10px;">Same-day signal: did the market move toward our price after we bet (positive = beat the close = real edge) or away (negative = likely model error)? Captured near kickoff for open bets.</p>
            <div id="clvSummary">Loading...</div>
        </div>
    </div>
</div>

<div id="adminMsg" class="msg" style="display:none;"></div>

<h2 style="margin-top: 24px;">Model Training & Stats</h2>
<div class="row">
    <div class="col">
        <div class="card">
            <div class="card-title">Active Model — VxxCyyL0000</div>
            <p style="color:#8b949e;font-size:12px;margin:0 0 10px;">Predictions made with the current fully-calibrated pipeline. Populates as matches settle.</p>
            <div id="modelStatsActive">Loading...</div>
        </div>
    </div>
</div>

<div class="row" style="margin-top:16px;">
    <div class="col">
        <div class="card">
            <div class="card-title">All-Time Summary — Vxx</div>
            <p style="color:#8b949e;font-size:12px;margin:0 0 10px;">All settled predictions for the current active base model (across all calibration versions).</p>
            <div id="modelStatsTotal">Loading...</div>
        </div>
    </div>
</div>

<h3>Train Market</h3>
<div class="card" style="margin-bottom: 16px;">
    <div style="display: flex; gap: 8px; align-items: center;">
        <select id="trainMarketSelect">
            <option value="all">All Markets</option>
            <option value="btts">BTTS</option>
            <option value="ou25">Over/Under 2.5</option>
            <option value="ou15">Over/Under 1.5</option>
            <option value="h2h">Head to Head</option>
        </select>
        <button class="btn btn-primary" onclick="trainSelectedMarket()">Train</button>
    </div>
    <div style="margin-top: 12px;">
        <span id="trainingIndicator" style="display: none; color: #58a6ff; font-weight: bold;"></span>
    </div>
</div>

<style>
.spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid rgba(88, 166, 255, 0.3);
    border-top-color: #58a6ff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
@keyframes spin {
    to { transform: rotate(360deg); }
}
</style>

<h3>Model Iterations</h3>
<select id="marketSelect" onchange="loadIterations()">
    <option value="btts">BTTS</option>
    <option value="ou25">Over/Under 2.5</option>
    <option value="ou15">Over/Under 1.5</option>
    <option value="h2h">Head to Head</option>
</select>

<div id="iterationsContainer" style="margin-top: 16px;">
    <div id="iterationsList">Select a market to view iterations</div>
</div>

<h3 style="margin-top: 24px;">Retrain Events</h3>
<div id="retrainEvents">Loading...</div>

<h3 style="margin-top: 32px; display:flex; align-items:center; gap:12px;">
    League Calibrations (VxxCyyLzzzz)
    <button onclick="runLeagueCal()" class="btn btn-primary" style="font-size:13px;padding:4px 12px;">Run Calibration Now</button>
</h3>
<p style="color:#8b949e;font-size:13px;margin-bottom:12px;">
    Active Platt-scaling calibrations per market. Global (L0000) is the fallback used when no league-specific calibration exists.
    L0000 delta is vs raw model output. League-specific deltas are vs L0000 applied to the same test set — positive means the league cal genuinely adds value beyond global.
</p>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:10px;">
    <label style="color:#8b949e;font-size:13px;">Show leagues:</label>
    <select id="leagueCalLimit" onchange="renderLeagueCalTable()" style="font-size:13px;padding:2px 8px;">
        <option value="10" selected>± 10</option>
        <option value="25">± 25</option>
        <option value="50">± 50</option>
        <option value="100">± 100</option>
        <option value="0">All</option>
    </select>
    <span id="leagueCalCount" style="color:#484f58;font-size:12px;"></span>
</div>
<div id="leagueCalSummary" style="margin-bottom:20px;">Loading...</div>

<details style="margin-bottom:16px;">
<summary style="cursor:pointer;color:#8b949e;font-size:13px;">Inspect a specific league ▸</summary>
<div style="margin-top:12px;display:flex;gap:12px;flex-wrap:wrap;">
    <select id="leagueCalSelect" onchange="loadLeagueCals()" style="min-width:220px;">
        <option value="">— Select a league —</option>
        <option value="all">All Leagues</option>
    </select>
    <select id="leagueCalMarket" onchange="loadLeagueCals()" style="min-width:140px;">
        <option value="btts">BTTS</option>
        <option value="ou25">Over/Under 2.5</option>
        <option value="ou15">Over/Under 1.5</option>
        <option value="h2h">Head to Head</option>
    </select>
</div>
<div id="leagueCalContainer" style="display:none;">
    <div id="leagueCalTable"></div>
</div>
</details>

<script>
function runMaintenance() {
    showMsg('Running maintenance checks...', 'info');
    fetch('/api/admin/maintenance', {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            let html = '<div style="margin-bottom: 12px;"><strong>System Health</strong></div>';
            var q = d.api_quota || {};
            var qRemaining = q.remaining !== undefined ? q.remaining : d.api_calls;
            var qLimit = q.limit || 75000;
            var qUsed = q.used || (qLimit - qRemaining);
            var qPct = Math.round(qRemaining / qLimit * 100);
            var qColor = qPct > 50 ? '#3fb950' : qPct > 20 ? '#d29922' : '#f85149';
            var qSource = q.source === 'api' ? '' : ' <span style="color:#484f58;font-size:10px;">(local est.)</span>';
            var qPlan = q.plan ? ' · ' + q.plan : '';
            html += '<div>API Quota' + qPlan + ': <strong style="color:' + qColor + '">' + qRemaining.toLocaleString() + '</strong> / ' + qLimit.toLocaleString() + ' remaining (' + qUsed.toLocaleString() + ' used)' + qSource + '</div>';
            html += '<div>DB Fixtures: <strong>' + d.fixture_count + '</strong></div>';
            html += '<div>DB Teams: <strong>' + d.team_count + '</strong></div>';
            html += '<div>Standings: <strong>' + d.standing_count + '</strong></div>';
            html += '<hr style="margin: 8px 0;">';
            html += '<div>Predictions: <strong>' + d.predictions.settled + '</strong> settled, <strong>' + d.predictions.unsettled + '</strong> unsettled</div>';
            html += '<div>Pending Bets: <strong>' + d.pending_bets + '</strong></div>';
            html += '<hr style="margin: 8px 0;">';
            html += '<div>Orphaned Fixtures: <strong style="color:' + (d.orphaned_fixtures > 0 ? '#f85149' : '#3fb950') + '">' + d.orphaned_fixtures + '</strong></div>';
            html += '<div>FT Null Goals: <strong style="color:' + (d.ft_null_goals > 0 ? '#f85149' : '#3fb950') + '">' + d.ft_null_goals + '</strong></div>';
            document.getElementById('systemStatus').innerHTML = html;
            showMsg('Maintenance check complete', 'success');
        })
        .catch(e => showMsg('Error: ' + e, 'error'));
}

function runDailyRun() {
    fetch('/api/admin/daily_run', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg(d.output || d.error || 'Done', d.error ? 'error' : 'success'));
}

function settleBets() {
    showMsg('Settling bets...', 'info');
    fetch('/api/settle_bets', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                showMsg(d.message || 'Settled: ' + d.settled_count + ' bets', 'success');
                setTimeout(() => location.reload(), 1500);
            } else {
                showMsg(d.error || 'Error', 'error');
            }
        })
        .catch(e => showMsg('Error: ' + e, 'error'));
}

function setTrainingIndicator(active, text) {
    const indicator = document.getElementById('trainingIndicator');
    if (indicator) {
        indicator.innerHTML = active
            ? '<span class="spinner"></span> ' + text
            : '';
        indicator.style.display = active ? 'inline-flex' : 'none';
    }
    // Also disable/enable all train buttons
    document.querySelectorAll('button[onclick^="train"]').forEach(btn => {
        btn.disabled = active;
    });
}

function trainSelectedMarket() {
    const market = document.getElementById('trainMarketSelect').value;
    const isAll = market === 'all';
    setTrainingIndicator(true, isAll ? 'Training all markets...' : 'Training ' + market + '...');
    showMsg(isAll ? 'Training all markets...' : 'Training ' + market + '...', 'info');

    fetch('/api/admin/train', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(isAll ? {} : {market: market}),
        credentials: 'include'
    })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                var lines = isAll ? [d.message] : ['Trained ' + market];
                for (var mkt in d.results) {
                    var result = d.results[mkt];
                    if (result.error) {
                        lines.push(mkt + ': ERROR - ' + result.error);
                    } else {
                        lines.push(mkt + ' v' + result.version + ': Brier=' + result.brier_score + ' (raw=' + result.brier_raw + ') ECE=' + result.ece + ' Acc=' + (result.accuracy * 100).toFixed(1) + '% [' + result.sample_size + ' samples]');
                    }
                }
                showMsg(lines.join('\\n'), 'success');
            } else {
                showMsg('Training failed: ' + (d.error || 'Unknown error'), 'error');
            }
            setTrainingIndicator(false);
            loadModelStats();
        })
        .catch(e => {
            showMsg('Training error: ' + e, 'error');
            setTrainingIndicator(false);
        });
}

function renderStatsTable(d, targetId) {
    const markets = ['h2h', 'btts', 'ou25', 'ou15'];
    const total = d['_total'];
    const allEmpty = markets.every(m => !d[m] || d[m].total_predictions === 0);
    if (allEmpty) {
        document.getElementById(targetId).innerHTML = '<p style="color:#8b949e;font-size:13px;">No predictions yet — populates as matches settle.</p>';
        return;
    }
    let html = '<table style="width:100%;">';
    html += '<thead><tr><th>Market</th><th>Version</th><th>Predictions</th><th>Settled</th><th>Win%</th><th>Avg EV</th><th>Brier</th><th>ECE</th><th>Signal</th><th>Trend</th></tr></thead>';
    html += '<tbody>';
    for (const market of markets) {
        const st = d[market];
        if (!st) continue;
        const trendClass = st.trend === 'improving' ? 'win' : (st.trend === 'degrading' ? 'loss' : '');
        html += '<tr class="' + trendClass + '">';
        html += '<td><strong>' + market.toUpperCase() + '</strong></td>';
        html += '<td><code style="font-size:11px;color:#8b949e;">' + (st.active_version || '—') + '</code></td>';
        html += '<td>' + st.total_predictions + '</td>';
        html += '<td>' + st.settled_predictions + '</td>';
        html += '<td class="' + (st.win_rate_pct >= 50 ? 'win' : 'loss') + '">' + st.win_rate_pct + '%</td>';
        html += '<td class="' + (st.average_ev_pct > 0 ? 'win' : 'loss') + '">' + st.average_ev_pct + '%</td>';
        html += '<td>' + (st.brier_score ? st.brier_score.toFixed(4) : 'N/A') + '</td>';
        html += '<td>' + (st.ece ? st.ece.toFixed(4) : 'N/A') + '</td>';
        html += '<td>' + st.signal + '</td>';
        html += '<td>' + getTrendBadge(st.trend) + '</td>';
        html += '</tr>';
    }
    if (total) {
        html += '<tr style="border-top:2px solid #30363d;font-weight:600;">';
        html += '<td>TOTAL</td><td></td>';
        html += '<td>' + total.total_predictions + '</td>';
        html += '<td>' + total.settled_predictions + '</td>';
        html += '<td class="' + (total.win_rate_pct >= 50 ? 'win' : 'loss') + '">' + total.win_rate_pct + '%</td>';
        html += '<td class="' + (total.average_ev_pct > 0 ? 'win' : 'loss') + '">' + total.average_ev_pct + '%</td>';
        html += '<td>' + (total.brier_score ? total.brier_score.toFixed(4) : 'N/A') + '</td>';
        html += '<td>' + (total.ece ? total.ece.toFixed(4) : 'N/A') + '</td>';
        html += '<td>' + total.signal + '</td>';
        html += '<td>' + getTrendBadge(total.trend) + '</td>';
        html += '</tr>';
    }
    html += '</tbody></table>';
    document.getElementById(targetId).innerHTML = html;
}

function loadModelStats() {
    fetch('/api/models/stats?mode=active', {credentials: 'include'})
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(d => renderStatsTable(d, 'modelStatsActive'))
        .catch(() => { document.getElementById('modelStatsActive').innerHTML = '<div style="color:#f85149;">Error loading stats</div>'; });

    fetch('/api/models/stats?mode=total', {credentials: 'include'})
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(d => renderStatsTable(d, 'modelStatsTotal'))
        .catch(() => { document.getElementById('modelStatsTotal').innerHTML = '<div style="color:#f85149;">Error loading stats</div>'; });
}

let _leagueCalRows = [];

function loadLeagueCalSummary() {
    fetch('/api/league_calibrations/active', {credentials: 'include'})
        .then(r => r.json())
        .then(rows => {
            if (!rows.length) {
                document.getElementById('leagueCalSummary').innerHTML = '<p style="color:#8b949e;">No active league calibrations yet.</p>';
                return;
            }
            _leagueCalRows = rows;
            renderLeagueCalTable();
        })
        .catch(() => {
            document.getElementById('leagueCalSummary').innerHTML = '<p style="color:#f85149;">Error loading calibrations</p>';
        });
}

function renderLeagueCalTable() {
    if (!_leagueCalRows.length) return;
    const n = parseInt(document.getElementById('leagueCalLimit').value, 10);
    const markets = ['h2h', 'btts', 'ou25', 'ou15'];

    // Build per-market L0000 improvement lookup for relative comparison.
    // Until the next fit_all() run, DB stores brier_improvement vs raw for all rows.
    // We compare league rows against their market's L0000 to determine green/red.
    const globalImp = {};
    _leagueCalRows.filter(r => r.is_global).forEach(r => { globalImp[r.market] = r.brier_improvement || 0; });

    // Annotate each league row with beats_global flag, then sort per market:
    // green rows (beat L0000) descending above L0000, red rows ascending below.
    const globals = _leagueCalRows.filter(r => r.is_global);
    const leagues = _leagueCalRows.filter(r => !r.is_global).map(r => ({
        ...r,
        beats_global: (r.brier_improvement || 0) > (globalImp[r.market] || 0)
    }));

    // Per market: green rows sorted best first, then red rows sorted least-bad first
    const sortedLeagues = [];
    for (const m of markets) {
        const ml = leagues.filter(r => r.market === m);
        const green = ml.filter(r => r.beats_global).sort((a, b) => (b.brier_improvement || 0) - (a.brier_improvement || 0));
        const red   = ml.filter(r => !r.beats_global).sort((a, b) => (b.brier_improvement || 0) - (a.brier_improvement || 0));
        sortedLeagues.push(...green, ...red);
    }

    let visible;
    if (n === 0) {
        // All: interleave global rows between their market's leagues
        visible = [];
        for (const m of markets) {
            const g = globals.find(r => r.market === m);
            const green = sortedLeagues.filter(r => r.market === m && r.beats_global);
            const red   = sortedLeagues.filter(r => r.market === m && !r.beats_global);
            visible.push(...green);
            if (g) visible.push(g);
            visible.push(...red);
        }
    } else {
        visible = [];
        for (const m of markets) {
            const g = globals.find(r => r.market === m);
            const green = sortedLeagues.filter(r => r.market === m && r.beats_global);
            const red   = sortedLeagues.filter(r => r.market === m && !r.beats_global);
            visible.push(...green.slice(0, n));
            if (g) visible.push(g);
            visible.push(...red.slice(0, n));
        }
    }

    const leagueTotal = leagues.length;
    const leagueShown = visible.filter(r => !r.is_global).length;
    document.getElementById('leagueCalCount').textContent =
        n === 0 ? `${leagueTotal} leagues` : `showing ${leagueShown} of ${leagueTotal} leagues`;

    let html = '<table style="width:100%;font-size:13px;">';
    html += '<thead><tr><th>Market</th><th>League</th><th>Version</th><th>Brier</th>';
    html += '<th title="L0000 (yellow) is the reference. Green = beats L0000, red = worse than L0000. L0000 delta is vs raw uncalibrated output.">Δ vs Baseline ⓘ</th>';
    html += '<th>Samples</th></tr></thead>';
    html += '<tbody>';
    for (const r of visible) {
        // L0000: yellow reference. League rows: green/red vs their market's L0000.
        const beatsGlobal = r.is_global ? null : r.beats_global;
        const impColor = r.is_global ? '#d29922' : (beatsGlobal ? '#3fb950' : '#f85149');
        const impSign  = (r.brier_improvement || 0) > 0 ? '+' : '';
        const rowStyle = r.is_global ? 'background:rgba(88,166,255,0.06);border-top:1px solid #30363d;border-bottom:1px solid #30363d;' : '';
        const baselineLabel = r.is_global ? 'Baseline (Δ vs raw)' : (beatsGlobal ? 'Beats L0000' : 'Worse than L0000');
        html += '<tr style="' + rowStyle + '">';
        const flagImg = (!r.is_global && r.flag) ? '<img src="' + r.flag + '" style="width:16px;height:11px;object-fit:cover;margin-right:5px;vertical-align:middle;border-radius:1px;" onerror="this.remove()">' : '';
        const leagueLabel = r.is_global ? '<em style="color:#58a6ff;">Global (L0000)</em>' : (flagImg + r.league_name);
        html += '<td><strong>' + r.market.toUpperCase() + '</strong></td>';
        html += '<td>' + leagueLabel + '</td>';
        html += '<td><code style="font-size:11px;">' + r.version_label + '</code></td>';
        html += '<td>' + (r.brier_score !== null ? r.brier_score.toFixed(4) : '-') + '</td>';
        html += '<td style="color:' + impColor + ';" title="' + baselineLabel + '">' + (r.brier_improvement !== null ? impSign + r.brier_improvement.toFixed(4) : '-') + '</td>';
        html += '<td>' + (r.sample_size || 0).toLocaleString() + '</td>';
        html += '</tr>';
    }
    html += '</tbody></table>';
    document.getElementById('leagueCalSummary').innerHTML = html;
}

function showMsg(text, type) {
    const el = document.getElementById('adminMsg');
    el.textContent = text;
    el.className = 'msg msg-' + type;
    el.style.display = 'block';
}

function getDriftBadge(driftScore, isDrifted) {
    if (!isDrifted) return '<span style="color: #3fb950;">Stable</span>';
    if (driftScore > 0) return '<span style="color: #f85149;">Worse ▲</span>';
    return '<span style="color: #58a6ff;">Better ▼</span>';
}

function getTrendBadge(trend) {
    const colors = {
        'stable': '#d29922',
        'improving': '#3fb950',
        'degrading': '#f85149',
        'insufficient_data': '#8b949e'
    };
    return '<span style="color: ' + (colors[trend] || '#8b949e') + ';">' + trend + '</span>';
}

fetch('/api/admin/system_status', {credentials: 'include'})
    .then(r => {
        if (!r.ok) throw new Error('Auth required');
        return r.json();
    })
    .then(d => {
        var q = d.api_quota || {};
        var qRemaining = q.remaining !== undefined ? q.remaining : (d.api_calls || 0);
        var qLimit = q.limit || 75000;
        var qUsed = q.used || (qLimit - qRemaining);
        var qPct = Math.round(qRemaining / qLimit * 100);
        var qColor = qPct > 50 ? '#3fb950' : qPct > 20 ? '#d29922' : '#f85149';
        var qSource = q.source === 'api' ? '' : ' <span style="color:#484f58;font-size:10px;">(local)</span>';
        var qPlan = q.plan ? ' (' + q.plan + ')' : '';
        var botBanner = d.bot_enabled
            ? '<div style="color:#3fb950;">Betting: <strong>ENABLED</strong> — bets are being placed</div>'
            : '<div style="background:#3d2a0a;border:1px solid #d29922;border-radius:6px;padding:6px 10px;margin-bottom:8px;color:#d29922;">⏸ Betting <strong>PAUSED</strong> (bot_enabled=False) — predictions still generate for analysis/CLV, but no new bets are placed</div>';
        document.getElementById('systemStatus').innerHTML =
            botBanner +
            '<div>API Quota' + qPlan + ': <strong style="color:' + qColor + '">' + qRemaining.toLocaleString() + ' / ' + qLimit.toLocaleString() + '</strong> remaining' + qSource + '</div>' +
            '<div style="color:#8b949e;font-size:11px;">Used today: ' + qUsed.toLocaleString() + '</div>' +
            '<div>DB Fixtures: <strong>' + (d.fixture_count || 0) + '</strong></div>' +
            '<div>Last Daily Run: <strong>' + (d.last_daily_run || 'Never') + '</strong></div>';
    })
    .catch(() => {
        document.getElementById('systemStatus').innerHTML = '<div style="color:#f85149;">Please login</div>';
    });

fetch('/api/clv/summary', {credentials: 'include'})
    .then(r => r.json())
    .then(d => {
        const el = document.getElementById('clvSummary');
        const o = d.overall || {};
        if (!o.n) {
            el.innerHTML = '<div style="color:#8b949e;">No closing lines captured yet — populates as open bets approach kickoff.</div>';
            return;
        }
        const avgColor = (o.avg_clv_pct || 0) > 0 ? '#3fb950' : '#f85149';
        let html = '<div>Overall: <strong style="color:' + avgColor + '">' + (o.avg_clv_pct > 0 ? '+' : '') + o.avg_clv_pct + '%</strong> avg CLV over <strong>' + o.n + '</strong> bet(s), <strong>' + o.pct_positive + '%</strong> beat the close</div>';
        html += '<table style="margin-top:8px;font-size:12px;"><thead><tr><th>Market</th><th>N</th><th>Avg CLV</th><th>% Beat Close</th></tr></thead><tbody>';
        (d.by_market || []).forEach(m => {
            const c = (m.avg_clv_pct || 0) > 0 ? '#3fb950' : '#f85149';
            html += '<tr><td>' + m.market + '</td><td>' + m.n + '</td><td style="color:' + c + '">' + (m.avg_clv_pct > 0 ? '+' : '') + m.avg_clv_pct + '%</td><td>' + m.pct_positive + '%</td></tr>';
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    })
    .catch(() => {
        document.getElementById('clvSummary').innerHTML = '<div style="color:#8b949e;">—</div>';
    });

// Load model stats on page load
loadModelStats();
loadLeagueCalSummary();

// Load iterations for selected market
function loadIterations() {
    const market = document.getElementById('marketSelect').value;
    fetch('/api/models/iterations/' + market, {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.length === 0) {
                document.getElementById('iterationsList').innerHTML = 'No iterations yet';
                return;
            }
            let html = '<table style="width: 100%;">';
            html += '<thead><tr><th>Label</th><th>Brier</th><th>Accuracy</th><th>ECE</th><th>Samples</th><th>Status</th><th>Trained</th><th>Actions</th></tr></thead>';
            html += '<tbody>';
            for (const iter of d) {
                const label = iter.version_label || ('v' + String(iter.version_number).padStart(2,'0') + '_c00');
                const activeStyle = iter.is_active ? 'background:#3fb950;color:#000;padding:2px 6px;border-radius:3px;font-size:11px;' : 'color:#8b949e;font-size:11px;';
                const activeText = iter.is_active ? 'Active' : 'Inactive';
                const activateBtn = iter.is_active ? '' : '<button style="font-size:11px;padding:2px 6px;" onclick="activateVersion(' + iter.id + ')">Activate</button>';
                html += '<tr>';
                html += '<td><code style="font-size:13px;">' + label + '</code></td>';
                html += '<td>' + (iter.brier_score ? iter.brier_score.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (iter.accuracy ? (iter.accuracy * 100).toFixed(1) + '%' : 'N/A') + '</td>';
                html += '<td>' + (iter.ece ? (iter.ece * 100).toFixed(2) + '%' : 'N/A') + '</td>';
                html += '<td>' + (iter.sample_size || 0).toLocaleString() + '</td>';
                html += '<td><span style="' + activeStyle + '">' + activeText + '</span></td>';
                html += '<td>' + (iter.trained_at ? new Date(iter.trained_at).toLocaleDateString() : 'N/A') + '</td>';
                html += '<td>' + activateBtn + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('iterationsList').innerHTML = html;
        })
        .catch(e => {
            document.getElementById('iterationsList').innerHTML = '<div style="color: #f85149;">Error loading iterations</div>';
        });

    // Load retrain events
    fetch('/api/models/retrain-events/' + market, {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.length === 0) {
                document.getElementById('retrainEvents').innerHTML = 'No retrain events';
                return;
            }
            let html = '<table style="width: 100%;">';
            html += '<thead><tr><th>Date</th><th>Reason</th><th>Brier Before</th><th>Brier After</th><th>Detail</th><th>Drift Trigger</th></tr></thead>';
            html += '<tbody>';
            for (const event of d) {
                const driftBadge = event.triggered_by_drift ? '<span style="color: #f85149;">Yes</span>' : '<span style="color: #8b949e;">No</span>';
                html += '<tr>';
                html += '<td>' + (event.created_at ? new Date(event.created_at).toLocaleDateString() : 'N/A') + '</td>';
                html += '<td>' + event.reason + '</td>';
                html += '<td>' + (event.brier_score_before ? event.brier_score_before.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (event.brier_score_after ? event.brier_score_after.toFixed(4) : 'N/A') + '</td>';
                html += '<td style="color: #8b949e; font-size: 0.85em;">' + (event.reason_detail || '') + '</td>';
                html += '<td>' + driftBadge + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('retrainEvents').innerHTML = html;
        })
        .catch(e => {
            document.getElementById('retrainEvents').innerHTML = '<div style="color: #f85149;">Error loading retrain events</div>';
        });
}

// Initial load
loadIterations();

function activateVersion(versionId) {
    if (!confirm('Activate version ' + versionId + '? This will replace the active model pkl.')) return;
    fetch('/api/models/activate/' + versionId, {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                alert('Activated: ' + d.activated);
                loadIterations();
            } else {
                alert('Error: ' + (d.error || 'unknown'));
            }
        })
        .catch(e => alert('Request failed: ' + e));
}

// ── League calibration UI ────────────────────────────────────────────────────

// Populate league dropdown using the same /api/leagues endpoint as Predictions page
fetch('/api/leagues', {credentials: 'include'})
    .then(r => r.json())
    .then(grouped => {
        const sel = document.getElementById('leagueCalSelect');
        for (const country in grouped) {
            const group = document.createElement('optgroup');
            group.label = country;
            grouped[country].forEach(lg => {
                const opt = document.createElement('option');
                opt.value = lg.id;
                opt.textContent = lg.name;
                group.appendChild(opt);
            });
            sel.appendChild(group);
        }
    })
    .catch(() => {});

function loadLeagueCals() {
    const leagueId = document.getElementById('leagueCalSelect').value;
    const market   = document.getElementById('leagueCalMarket').value;
    const container = document.getElementById('leagueCalContainer');
    if (!leagueId) { container.style.display = 'none'; return; }
    if (leagueId === 'all') { runLeagueCal(); return; }
    container.style.display = 'block';
    document.getElementById('leagueCalTable').innerHTML = 'Loading...';
    fetch('/api/league_calibrations/' + leagueId + '/' + market, {credentials: 'include'})
        .then(r => r.json())
        .then(rows => {
            if (!rows.length) {
                document.getElementById('leagueCalTable').innerHTML =
                    '<p style="color:#8b949e;">No calibration data yet for this league/market (min 100 settled samples required).</p>';
                return;
            }
            let html = '<table style="width:100%;">';
            html += '<thead><tr><th>Version</th><th>Slope</th><th>Intercept</th><th>Brier (League)</th><th>Brier (Global)</th><th>Improvement</th><th>Samples</th><th>Status</th><th>Fitted</th></tr></thead>';
            html += '<tbody>';
            for (const r of rows) {
                const impColor = r.brier_improvement > 0 ? '#3fb950' : (r.brier_improvement < 0 ? '#f85149' : '#8b949e');
                const impSign  = r.brier_improvement > 0 ? '+' : '';
                const statusBadge = r.is_active
                    ? '<span style="background:#3fb950;color:#000;padding:2px 6px;border-radius:3px;font-size:11px;">Active</span>'
                    : '<span style="color:#8b949e;font-size:11px;">Inactive</span>';
                html += '<tr>';
                html += '<td><code>' + r.version_label + '</code></td>';
                html += '<td>' + (r.slope !== null ? r.slope.toFixed(4) : '-') + '</td>';
                html += '<td>' + (r.intercept !== null ? r.intercept.toFixed(4) : '-') + '</td>';
                html += '<td>' + (r.brier_score !== null ? r.brier_score.toFixed(4) : '-') + '</td>';
                html += '<td>' + (r.brier_score_global !== null ? r.brier_score_global.toFixed(4) : '-') + '</td>';
                html += '<td style="color:' + impColor + ';">' + (r.brier_improvement !== null ? impSign + r.brier_improvement.toFixed(4) : '-') + '</td>';
                html += '<td>' + (r.sample_size || 0).toLocaleString() + '</td>';
                html += '<td>' + statusBadge + '</td>';
                html += '<td>' + (r.created_at ? new Date(r.created_at).toLocaleDateString() : '-') + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('leagueCalTable').innerHTML = html;
        })
        .catch(() => { document.getElementById('leagueCalTable').innerHTML = '<p style="color:#f85149;">Error loading calibration data.</p>'; });
}

function runLeagueCal() {
    if (!confirm('Run league calibration now? This will fit calibrations for all leagues with enough data.')) return;
    showMsg('Running league calibration...', 'info');
    fetch('/api/league_calibrations/run', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showMsg('Fitted ' + d.count + ' league calibrations.', 'success');
                loadLeagueCals();
            } else {
                showMsg('Error: ' + (d.error || 'unknown'), 'error');
            }
        })
        .catch(e => showMsg('Request failed: ' + e, 'error'));
}
</script>

<style>
.degrading { background: rgba(248, 81, 73, 0.1); }
.improving { background: rgba(63, 185, 80, 0.1); }
</style>

<h2 style="margin-top: 32px;">League Performance Ranking</h2>
<p style="color:#8b949e;font-size:13px;margin-bottom:12px;">
    Leagues ranked by prediction accuracy (win rate) across all settled predictions.
    Version shows league-specific calibration where active, otherwise the global model.
    Min 10 settled predictions per market to appear.
</p>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:12px;flex-wrap:wrap;">
    <div>
        <label style="color:#8b949e;font-size:13px;">Show: </label>
        <select id="rankLimit" onchange="loadLeagueRanking()" style="min-width:100px;">
            <option value="10">Top 10</option>
            <option value="25">Top 25</option>
            <option value="all">All</option>
        </select>
    </div>
    <div>
        <label style="color:#8b949e;font-size:13px;">Market: </label>
        <select id="rankMarket" onchange="loadLeagueRanking()" style="min-width:120px;">
            <option value="all">All markets</option>
            <option value="btts">BTTS</option>
            <option value="ou25">Over/Under 2.5</option>
            <option value="ou15">Over/Under 1.5</option>
            <option value="h2h">Head to Head</option>
        </select>
    </div>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none;">
        <input type="checkbox" id="rankOddsFilter" checked onchange="loadLeagueRanking()"
               style="width:16px;height:16px;accent-color:#58a6ff;cursor:pointer;">
        <span style="color:#c9d1d9;font-size:13px;">Min odds 1.6</span>
    </label>
    <button onclick="loadLeagueRanking()" style="padding:4px 12px;font-size:13px;">Refresh</button>
</div>
<div id="leagueRankingContainer">
    <div id="leagueRankingTable" style="color:#8b949e;">Loading...</div>
</div>

<script>
function loadLeagueRanking() {
    const limit      = document.getElementById('rankLimit').value;
    const market     = document.getElementById('rankMarket').value;
    const oddsFilter = document.getElementById('rankOddsFilter').checked;
    document.getElementById('leagueRankingTable').innerHTML = 'Loading...';

    const url = '/api/leagues/performance' + (oddsFilter ? '?min_odds=1.6' : '');
    fetch(url, {credentials: 'include'})
        .then(r => r.json())
        .then(all => {
            let rows = market === 'all' ? all : all.filter(r => r.market === market);
            const total = rows.length;
            if (limit !== 'all') rows = rows.slice(0, parseInt(limit));

            if (!rows.length) {
                document.getElementById('leagueRankingTable').innerHTML =
                    '<p style="color:#8b949e;">No data yet — predictions are still being settled.</p>';
                return;
            }

            const MARKET_LABELS = {btts: 'BTTS', ou25: 'O/U 2.5', ou15: 'O/U 1.5', h2h: 'H2H'};
            const FLAG_MAP = ''' + __import__('json').dumps({
                'England':'🏴󠁧󠁢󠁥󠁮󠁧󠁿','France':'🇫🇷','Germany':'🇩🇪','Spain':'🇪🇸','Italy':'🇮🇹',
                'Netherlands':'🇳🇱','Portugal':'🇵🇹','Belgium':'🇧🇪','Switzerland':'🇨🇭',
                'Turkey':'🇹🇷','Austria':'🇦🇹','Sweden':'🇸🇪','Denmark':'🇩🇰','Norway':'🇳🇴',
                'Poland':'🇵🇱','Hungary':'🇭🇺','Romania':'🇷🇴','Croatia':'🇭🇷','Serbia':'🇷🇸',
                'Ukraine':'🇺🇦','Scotland':'🏴󠁧󠁢󠁳󠁣󠁴󠁿','Greece':'🇬🇷','Slovakia':'🇸🇰',
                'Australia':'🇦🇺','Japan':'🇯🇵','USA':'🇺🇸','South Korea':'🇰🇷','Brazil':'🇧🇷',
            }) + ''';

            let html = '<table style="width:100%;border-collapse:collapse;">';
            html += '<thead><tr style="border-bottom:1px solid #30363d;">';
            html += '<th style="text-align:right;padding:6px 8px;color:#8b949e;font-size:12px;">#</th>';
            html += '<th style="text-align:left;padding:6px 8px;color:#8b949e;font-size:12px;">League</th>';
            html += '<th style="text-align:left;padding:6px 8px;color:#8b949e;font-size:12px;">Market</th>';
            html += '<th style="text-align:left;padding:6px 8px;color:#8b949e;font-size:12px;">Version</th>';
            html += '<th style="text-align:right;padding:6px 8px;color:#8b949e;font-size:12px;">Samples</th>';
            html += '<th style="text-align:right;padding:6px 8px;color:#8b949e;font-size:12px;">Win %</th>';
            html += '<th style="text-align:right;padding:6px 8px;color:#8b949e;font-size:12px;">Brier</th>';
            html += '<th style="text-align:right;padding:6px 8px;color:#8b949e;font-size:12px;">Δ Global</th>';
            html += '</tr></thead><tbody>';

            rows.forEach((r, i) => {
                const flag = FLAG_MAP[r.country] || '🌍';
                const mLabel = MARKET_LABELS[r.market] || r.market;
                const verStyle = r.has_league_cal
                    ? 'color:#58a6ff;font-size:11px;'
                    : 'color:#8b949e;font-size:11px;';

                const winColor = r.win_rate >= 60 ? '#3fb950' : r.win_rate >= 50 ? '#d29922' : '#f85149';

                let brierCell = r.brier_score !== null ? r.brier_score.toFixed(4) : '—';
                let deltaCell = '—';
                let deltaColor = '#8b949e';
                if (r.brier_improvement !== null) {
                    const sign = r.brier_improvement > 0 ? '+' : '';
                    deltaCell = sign + r.brier_improvement.toFixed(4);
                    deltaColor = r.brier_improvement > 0 ? '#3fb950' : '#f85149';
                }

                const rowBg = i % 2 === 0 ? '' : 'background:rgba(255,255,255,0.02);';
                html += `<tr style="${rowBg}">`;
                html += `<td style="text-align:right;padding:5px 8px;color:#8b949e;font-size:12px;">${i + 1}</td>`;
                html += `<td style="padding:5px 8px;font-size:13px;">${flag} ${r.league}</td>`;
                html += `<td style="padding:5px 8px;font-size:12px;color:#8b949e;">${mLabel}</td>`;
                html += `<td style="padding:5px 8px;"><code style="${verStyle}">${r.version}</code></td>`;
                html += `<td style="text-align:right;padding:5px 8px;font-size:13px;">${r.total.toLocaleString()}</td>`;
                html += `<td style="text-align:right;padding:5px 8px;font-weight:600;color:${winColor};">${r.win_rate}%</td>`;
                html += `<td style="text-align:right;padding:5px 8px;font-size:12px;color:#8b949e;">${brierCell}</td>`;
                html += `<td style="text-align:right;padding:5px 8px;font-size:12px;color:${deltaColor};">${deltaCell}</td>`;
                html += '</tr>';
            });

            html += '</tbody></table>';
            if (limit !== 'all') {
                html += `<p style="color:#8b949e;font-size:12px;margin-top:8px;">Showing ${rows.length} of ${total} entries.</p>`;
            }
            document.getElementById('leagueRankingTable').innerHTML = html;
        })
        .catch(() => {
            document.getElementById('leagueRankingTable').innerHTML =
                '<p style="color:#f85149;">Error loading ranking data.</p>';
        });
}
loadLeagueRanking();
</script>
'''
    return page(content)


@app.route('/api/admin/daily_run', methods=['POST'])
@require_auth
def admin_daily_run():
    import subprocess
    import os
    lock_file = '/tmp/bootball_daily_run.lock'

    if os.path.exists(lock_file):
        return jsonify({'ok': False, 'error': 'Daily run is already in progress'}), 409

    try:
        with open(lock_file, 'w') as f:
            f.write(str(datetime.utcnow()))

        result = subprocess.run(
            [sys.executable, 'scripts/daily_run.py'],
            capture_output=True, text=True, timeout=300
        )
        return jsonify({'ok': True, 'output': result.stdout[:2000]})
    finally:
        if os.path.exists(lock_file):
            os.remove(lock_file)


@app.route('/api/settle_bets', methods=['POST'])
@require_auth
def api_settle_bets():
    """Unified bet settlement endpoint.
    
    Fetches fixtures from API first, then settles all bets/predictions.
    Used by both Admin and Betting Dashboard.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from src.settlement import settle_all, fetch_and_update_fixtures, update_pending_fixture_scores

        # Fetch fixtures from API first
        fixtures_updated = fetch_and_update_fixtures(days=7)
        logger.info(f"Updated {fixtures_updated} fixtures")

        # Update live game statuses (efficient 7-call global fetch, not per-league)
        live_updated = update_pending_fixture_scores()
        logger.info(f"Updated {live_updated} live fixtures")
        
        # Settle all bets and predictions
        result = settle_all()

        from src.betting.round_manager import update_closed_round_stats
        from src.storage.db import get_session as _get_session
        with _get_session() as _s:
            update_closed_round_stats(_s)

        logger.info(f"Settled: {result['bets_settled']} bets, {result['predictions_settled']} predictions, P/L: {result['bets_pnl']:+.2f}")
        
        return jsonify({
            'ok': True,
            'settled_count': result['bets_settled'],
            'wins': sum(1 for b in result.get('bet_details', []) if b.get('won')),
            'losses': sum(1 for b in result.get('bet_details', []) if not b.get('won')),
            'total_pnl': result['bets_pnl'],
            'predictions_settled': result['predictions_settled'],
            'message': f"Settled: {result['bets_settled']} bets, {result['predictions_settled']} predictions, P/L: {result['bets_pnl']:+.2f}"
        })
    except Exception as e:
        logger.error(f"Settlement error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/admin/maintenance', methods=['GET', 'POST'])
@require_auth
def api_admin_maintenance():
    """Run maintenance checks and return status."""
    from src.ingestion.client import get_api_status
    from src.storage.models import Fixture, Team, Standing, PredictionRecord, PlacedBet
    from sqlalchemy import select, func

    results = {}

    with get_session() as s:
        _quota = get_api_status()
        results['api_calls'] = _quota['remaining']
        results['api_quota'] = _quota

        results['fixture_count'] = s.execute(select(func.count()).select_from(Fixture)).scalar() or 0
        results['team_count'] = s.execute(select(func.count()).select_from(Team)).scalar() or 0
        results['standing_count'] = s.execute(select(func.count()).select_from(Standing)).scalar() or 0
        results['pred_count'] = s.execute(select(func.count()).select_from(PredictionRecord)).scalar() or 0

        settled_preds = s.execute(
            select(func.count()).select_from(PredictionRecord).where(PredictionRecord.settled == True)
        ).scalar() or 0
        unsettled_preds = s.execute(
            select(func.count()).select_from(PredictionRecord).where(PredictionRecord.settled == False)
        ).scalar() or 0
        results['predictions'] = {'settled': settled_preds, 'unsettled': unsettled_preds}

        pending_bets = s.execute(
            select(func.count()).select_from(PlacedBet).where(PlacedBet.settled == False)
        ).scalar() or 0
        results['pending_bets'] = pending_bets

        # Exclude competitions that structurally have no league standings
        _no_standings = [
            '%cup%', '%trophy%', '%pokal%', '%beker%', '%copa%', '%coupe%',
            '%coppa%', '%taç%', '%friendl%', '%nations%', '%champions league%',
            '%europa league%', '%concacaf%', '%conmebol%', '%libertador%',
            '%sudamerican%', '%saff%', '%eaff%', '%asean%', '%youth league%',
        ]
        from sqlalchemy import and_, text as _text
        exclude_filter = and_(*[~League.name.ilike(p) for p in _no_standings])
        league_standings_subq = select(Standing.league_id).distinct()
        # Leagues maintenance has already examined (any outcome — fixed, no
        # data available, structurally no standings) are "handled": they
        # shouldn't keep counting as orphaned just because no rows landed.
        checked_subq = select(_text("league_id")).select_from(_text("league_standings_check"))
        orphaned = s.execute(
            select(func.count(func.distinct(Fixture.league_id)))
            .join(League, Fixture.league_id == League.id)
            .where(Fixture.league_id.notin_(league_standings_subq))
            .where(Fixture.league_id.notin_(checked_subq))
            .where(exclude_filter)
        ).scalar() or 0
        results['orphaned_fixtures'] = orphaned

        ft_null_goals = s.execute(
            select(func.count()).select_from(Fixture)
            .where(Fixture.status == 'FT')
            .where(Fixture.goals_home == None)
        ).scalar() or 0
        results['ft_null_goals'] = ft_null_goals

    return jsonify(results)


@app.route('/api/admin/train', methods=['POST'])
@require_auth
def api_admin_train():
    """Train calibrated models for all markets.

    Following research: Isotonic regression calibration for +34.69% ROI.
    """
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss

    data = request.get_json() or {}
    market = data.get('market')  # Optional: train specific market
    reason = data.get('reason', 'manual')

    results = {}
    markets_to_train = [market] if market else ['btts', 'ou25', 'ou15', 'h2h']

    for mkt in markets_to_train:
        try:
            result = _train_market_with_calibration(mkt, reason)
            results[mkt] = result
        except Exception as e:
            logger.error(f"Training failed for {mkt}: {e}")
            results[mkt] = {'error': str(e)}

    return jsonify({
        'ok': True,
        'results': results,
        'message': f"Trained {len([r for r in results.values() if 'error' not in r])} markets successfully"
    })


def _train_market_with_calibration(market: str, reason: str = 'manual') -> dict:
    """Train a market model with optional isotonic calibration.

    Uses LightGBM (primary) with sklearn GradientBoosting as fallback.
    Binary markets use isotonic calibration; multi-class (h2h) uses argmax.
    """
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss

    from src.models.calibrator import MarketCalibrator
    from src.models.model_tracker import get_model_tracker

    logger.info(f"Training {market} with LightGBM calibration...")

    try:
        import lightgbm as lgb
        use_lightgbm = True
        model_name = 'LightGBM'
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        use_lightgbm = False
        model_name = 'GradientBoosting'

    # Get market config
    market_configs = {
        'h2h': {'target_fn': lambda gh, ga, outcome: 0 if outcome == 'H' else (1 if outcome == 'D' else 2)},
        'btts': {'target_fn': lambda gh, ga, outcome: 1 if (gh > 0 and ga > 0) else 0},
        'ou25': {'target_fn': lambda gh, ga, outcome: 1 if (gh + ga > 2.5) else 0},
        'ou15': {'target_fn': lambda gh, ga, outcome: 1 if (gh + ga > 1.5) else 0},
    }

    if market not in market_configs:
        return {'error': f'Unknown market: {market}'}

    config = market_configs[market]

    # Fetch training data and build features in one session
    with get_session() as s:
        fixtures = s.execute(
            select(Fixture)
            .where(Fixture.status == 'FT')
            .where(Fixture.outcome.isnot(None))
            .where(Fixture.goals_home.isnot(None))
            .where(Fixture.goals_away.isnot(None))
            .order_by(Fixture.date.desc())
            .limit(5000)
        ).scalars().all()

        if len(fixtures) < 100:
            return {'error': f'Insufficient data: {len(fixtures)} fixtures'}

        # Extract fixture data and build features while session is active
        X_list, y_list = [], []
        for fix in fixtures:
            home_stats = s.execute(
                select(Standing).where(Standing.team_id == fix.home_team_id).where(Standing.season >= 2024)
            ).first()
            away_stats = s.execute(
                select(Standing).where(Standing.team_id == fix.away_team_id).where(Standing.season >= 2024)
            ).first()

            if not home_stats or not away_stats:
                continue

            hs = home_stats[0]
            as_ = away_stats[0]

            features = [
                float(hs.rank or 15),
                float(as_.rank or 15),
                float((hs.goals_for or 1) - (hs.goals_against or 1)),
                float((as_.goals_for or 1) - (as_.goals_against or 1)),
                float(hs.goals_for or 1),
                float(as_.goals_for or 1),
                float(hs.goals_against or 1),
                float(as_.goals_against or 1),
                float(abs((hs.rank or 15) - (as_.rank or 15))),
            ]

            target = config['target_fn'](fix.goals_home, fix.goals_away, fix.outcome)

            X_list.append(features)
            y_list.append(target)

    if len(X_list) < 100:
        return {'error': f'Insufficient valid samples: {len(X_list)}'}

    X = np.array(X_list)
    y = np.array(y_list)

    all_classes = set(y)
    num_classes = len(all_classes)

    # Train/test split
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # Validate both splits have all classes
    if len(set(y_train)) < num_classes:
        return {'error': f'Train split missing classes: got {set(y_train)}, expected {all_classes}'}
    if len(set(y_test)) < num_classes:
        return {'error': f'Test split missing classes: got {set(y_test)}, expected {all_classes}'}

    # Train base model
    if use_lightgbm:
        model = lgb.LGBMClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
            verbose=-1,
            force_col_wise=True,
            objective='multiclass' if num_classes > 2 else 'binary',
            num_class=num_classes if num_classes > 2 else None
        )
    else:
        model = GradientBoostingClassifier(
            n_estimators=200, max_depth=4,
            learning_rate=0.1, random_state=42
        )
    model.fit(X_train, y_train)

    # Get raw probabilities for test set
    if len(set(y_test)) == 1:
        return {'error': 'Test set has single class'}

    raw_probs = model.predict_proba(X_test)
    num_classes = len(set(y))

    # For binary classification, use positive class probability
    if num_classes == 2:
        if raw_probs.ndim == 1:
            raw_prob_values = raw_probs
        else:
            raw_prob_values = raw_probs[:, 1]
        y_binary = y_test.astype(float)
        use_calibration = True
    else:
        # Multi-class: use mean probability across all classes as overall confidence
        # (isotonic calibration doesn't work directly on multi-class)
        raw_prob_values = np.mean(raw_probs, axis=1)
        y_binary = (np.argmax(raw_probs, axis=1) == y_test).astype(float)
        use_calibration = False

    # Apply isotonic calibration for binary only
    calibrator = None
    if use_calibration:
        calibrator = IsotonicRegression(out_of_bounds='clip')
        try:
            calibrator.fit(raw_prob_values, y_binary)
        except Exception as e:
            logger.warning(f"Calibration fit failed: {e}")
            calibrator = None

    # Calculate metrics
    if calibrator:
        calibrated_probs = calibrator.predict(raw_prob_values)
        calibrated_probs = np.clip(calibrated_probs, 0.01, 0.99)
    else:
        calibrated_probs = raw_prob_values

    if num_classes == 2:
        brier_raw = float(brier_score_loss(y_binary, raw_prob_values))
        brier_calibrated = float(brier_score_loss(y_binary, calibrated_probs))
        ece = calc_ece(calibrated_probs, y_binary)
    else:
        # Multi-class: per-class OvR Brier averaged; ECE on top-pick confidence vs win rate
        brier_raw = float(np.mean([brier_score_loss(y_test == i, raw_probs[:, i]) for i in range(num_classes)]))
        brier_calibrated = brier_raw
        # ECE: treat as binary — "did the model's argmax pick win?"
        top_probs = np.max(raw_probs, axis=1)
        top_wins = (np.argmax(raw_probs, axis=1) == y_test).astype(float)
        ece = calc_ece(top_probs, top_wins)

    # Calculate accuracy
    if num_classes == 2:
        y_pred = (calibrated_probs > 0.5).astype(float)
        accuracy = float(np.mean(y_pred == y_binary))
    else:
        y_pred = np.argmax(raw_probs, axis=1)
        accuracy = float(np.mean(y_pred == y_test))

    metrics = {
        "brier_score": brier_calibrated,
        "accuracy": accuracy,
        "ece": ece,
        "sample_size": len(X_train),
        "calibration_sample_size": len(X_test),
        "model_type": f"{model_name.lower()}+isotonic",
        "features_used": "rank,goal_diff,goals_for,goals_against,rank_diff",
    }

    # Register through ModelRegistry — this handles DB write, versioned pkl, and active swap.
    from src.models.model_registry import get_model_registry
    registry = get_model_registry()
    new_ver = registry.register_retrain(market, model, calibrator, metrics, reason=reason)
    label = new_ver["version_label"] if new_ver else "unknown"

    logger.info("Trained %s %s: Brier=%.4f ECE=%.4f Acc=%.3f", market, label, brier_calibrated, ece, accuracy)

    return {
        'version_label': label,
        'version': new_ver["version_number"] if new_ver else 0,
        'brier_score': round(brier_calibrated, 4),
        'brier_raw': round(brier_raw, 4),
        'ece': round(ece, 4),
        'accuracy': round(accuracy, 4),
        'sample_size': len(X_train),
        'test_size': len(X_test),
        'improvement': round(brier_raw - brier_calibrated, 4),
    }


@app.route('/api/admin/system_status', methods=['GET'])
@require_auth
def api_admin_system_status():
    from src.ingestion.client import get_api_status
    from config.settings import settings as _settings
    with get_session() as s:
        fixture_count = s.execute(select(func.count()).select_from(Fixture)).scalar() or 0

    quota = get_api_status()
    return jsonify({
        'api_calls': quota['remaining'],
        'api_quota': quota,
        'fixture_count': fixture_count,
        'last_daily_run': 'Check logs',
        'bot_enabled': bool(_settings.bot_enabled),
    })


# =============================================================================
# ROUTES: User Preferences
# =============================================================================

@app.route('/api/preferences', methods=['GET'])
@require_auth
def api_get_preferences():
    """Get user preferences. Uses default (NULL user_id) if no specific user."""
    user_id = request.args.get('user_id')  # Future: from session

    with get_session() as s:
        pref = s.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        ).scalar_one_or_none()

        if not pref:
            # Return defaults
            return jsonify({
                'timezone': 'Europe/Stockholm',
                'preferred_markets': 'btts,ou25,ou15,h2h',
                'preferred_leagues': None,
                'alerts_enabled': True,
                'alerts_min_ev': 0.05,
                'alerts_top_n': 5,
                'default_days': 7,
            })

        return jsonify({
            'timezone': pref.timezone,
            'preferred_markets': pref.preferred_markets,
            'preferred_leagues': pref.preferred_leagues,
            'alerts_enabled': pref.alerts_enabled,
            'alerts_min_ev': pref.alerts_min_ev,
            'alerts_top_n': pref.alerts_top_n,
            'default_days': pref.default_days,
        })


@app.route('/api/preferences', methods=['PUT', 'POST'])
@require_auth
def api_update_preferences():
    """Update user preferences."""
    user_id = request.args.get('user_id')  # Future: from session
    data = request.get_json()

    with get_session() as s:
        pref = s.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        ).scalar_one_or_none()

        if not pref:
            pref = UserPreference(user_id=user_id)
            s.add(pref)

        if 'timezone' in data:
            pref.timezone = data['timezone']
        if 'preferred_markets' in data:
            pref.preferred_markets = data['preferred_markets']
        if 'preferred_leagues' in data:
            pref.preferred_leagues = data['preferred_leagues']
        if 'alerts_enabled' in data:
            pref.alerts_enabled = data['alerts_enabled']
        if 'alerts_min_ev' in data:
            pref.alerts_min_ev = float(data['alerts_min_ev'])
        if 'alerts_top_n' in data:
            pref.alerts_top_n = int(data['alerts_top_n'])
        if 'default_days' in data:
            pref.default_days = int(data['default_days'])

        s.commit()

        return jsonify({'ok': True, 'message': 'Preferences updated'})


def _get_odds_for_market(odds_row, market):
    """Extract odds value for a given market from FixtureOdds row."""
    if not odds_row:
        return None
    if market == 'btts':
        return odds_row.odd_btts_yes
    elif market == 'ou25':
        return odds_row.odd_over
    elif market == 'ou15':
        return odds_row.odd_over15
    elif market == 'h2h':
        return odds_row.odd_home
    return None


# =============================================================================
# ROUTES: Watched Fixtures (Following)
# =============================================================================

@app.route('/api/watch', methods=['POST'])
@require_auth
def api_watch_fixture():
    """Add a fixture to watch list.

    Body: {"fixture_id": int, "market": str, "selection_type": "watch"|"auto"|"manual"}
    """
    data = request.get_json()
    fixture_id = data.get('fixture_id')
    market = data.get('market', 'btts')
    selection_type = data.get('selection_type', 'watch')

    if not fixture_id:
        return jsonify({'error': 'fixture_id required'}), 400

    with get_session() as s:
        existing = s.execute(
            select(WatchedFixture).where(
                WatchedFixture.fixture_id == fixture_id,
                WatchedFixture.market == market,
                WatchedFixture.user_id == None
            )
        ).scalar_one_or_none()

        if existing:
            existing.selection_type = selection_type
            existing.status = 'pending'
            s.commit()
            return jsonify({'ok': True, 'message': 'Updated watched fixture'})

        watched = WatchedFixture(
            fixture_id=fixture_id,
            market=market,
            selection_type=selection_type,
            status='pending'
        )
        s.add(watched)
        s.commit()

        return jsonify({'ok': True, 'message': 'Added to watch list'})


@app.route('/api/watch/<int:fixture_id>', methods=['DELETE'])
@require_auth
def api_unwatch_fixture(fixture_id):
    """Remove a fixture from watch list."""
    market = request.args.get('market', 'btts')

    with get_session() as s:
        result = s.execute(
            select(WatchedFixture).where(
                WatchedFixture.fixture_id == fixture_id,
                WatchedFixture.market == market,
                WatchedFixture.user_id == None
            )
        )
        watched = result.scalar_one_or_none()

        if not watched:
            return jsonify({'error': 'Not found'}), 404

        s.delete(watched)
        s.commit()

        return jsonify({'ok': True, 'message': 'Removed from watch list'})


@app.route('/api/watching', methods=['GET'])
@require_auth
def api_get_watched():
    """Get all watched fixtures with predictions."""
    days = int(request.args.get('days', 7))
    market_filter = request.args.get('market')

    with get_session() as s:
        query = (
            select(Fixture, WatchedFixture)
            .join(WatchedFixture, WatchedFixture.fixture_id == Fixture.id)
            .where(Fixture.date >= datetime.utcnow() - timedelta(days=1))
            .where(Fixture.date <= datetime.utcnow() + timedelta(days=days))
            .where(WatchedFixture.status.in_(['pending', 'live']))
        )

        if market_filter:
            query = query.where(WatchedFixture.market == market_filter)

        results = s.execute(query.order_by(Fixture.date)).all()

        watched_list = []
        for fix, watched in results:
            pred = s.execute(
                select(PredictionRecord).where(
                    PredictionRecord.fixture_id == fix.id,
                    PredictionRecord.market == watched.market
                )
            ).scalar_one_or_none()

            odds_row = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fix.id,
                    FixtureOdds.bet_type == watched.market
                )
            ).scalar_one_or_none()

            item = {
                'fixture_id': fix.id,
                'home_name': fix.home_team.name if fix.home_team else 'TBD',
                'away_name': fix.away_team.name if fix.away_team else 'TBD',
                'league': fix.league.name if fix.league else 'Unknown',
                'date': fix.date.isoformat() if fix.date else None,
                'market': watched.market,
                'selection_type': watched.selection_type,
                'status': watched.status,
                'prediction': {
                    'pick': pred.predicted_outcome if pred else None,
                    'prob': pred.our_prob if pred else None,
                    'calibrated_prob': pred.our_prob if pred else None,
                    'odds': _get_odds_for_market(odds_row, watched.market) if odds_row else None,
                } if pred else None,
            }
            watched_list.append(item)

        return jsonify(watched_list)


# =============================================================================
# ROUTES: Model Stats & Graphs
# =============================================================================

@app.route('/api/models/stats', methods=['GET'])
@require_auth
def api_model_stats():
    """Get model performance stats.

    ?mode=active  — filter to current VxxCyyL0000 (calibration_version_id)
    ?mode=total   — filter to current Vxx model (model_version_id), all-time

    Returns 4 market rows + a _total aggregate row.
    L-level calibration stats are in /api/league_calibrations/active.
    """
    from flask import request as flask_request
    from sqlalchemy import select, func
    from src.storage.db import get_session
    from src.storage.models import PredictionRecord, Fixture, PlacedBet, ModelVersion, LeagueCalibration

    mode = flask_request.args.get('mode', 'total')
    markets = ['h2h', 'btts', 'ou25', 'ou15']
    stats = {}

    with get_session() as s:
        # Resolve active model version IDs (Vxx)
        active_versions = {
            mv.market: mv
            for mv in s.execute(
                select(ModelVersion).where(ModelVersion.is_active == True)
            ).scalars().all()
        }

        # Resolve active L0000 version labels (VxxCyyL0000)
        l0_labels = {
            row.market: row.version_label
            for row in s.execute(
                select(LeagueCalibration)
                .where(LeagueCalibration.league_id.is_(None))
                .where(LeagueCalibration.is_active == True)
            ).scalars().all()
        }

        for market in markets:
            active_mv = active_versions.get(market)
            l0_label  = l0_labels.get(market)

            q = (
                select(PredictionRecord, Fixture.date)
                .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
                .where(PredictionRecord.market == market)
                .order_by(Fixture.date)
            )
            if mode == 'active':
                # VxxCyyL0000Www: match any _wWW iteration of the current L0000 base label.
                # Strip _wNN suffix if present so LIKE matches all iterations.
                if l0_label:
                    base = l0_label.split('_w')[0] if '_w' in l0_label else l0_label
                    q = q.where(PredictionRecord.calibration_version_id.like(f"{base}%"))
                else:
                    q = q.where(PredictionRecord.id == -1)  # no L0000 yet → empty
            else:
                # total: filter by model_version_id (current Vxx, all-time)
                if active_mv:
                    q = q.where(PredictionRecord.model_version_id == active_mv.id)

            pred_rows = s.execute(q).all()

            preds = [p for p, _ in pred_rows]
            total = len(preds)
            settled = [p for p in preds if p.settled]
            unsettled = [p for p in preds if not p.settled]
            settled_count = len(settled)
            
            # Compute metrics from settled predictions
            wins = sum(1 for p in settled if p.won)
            win_rate = (wins / settled_count * 100) if settled_count > 0 else 0
            
            # Get settled predictions with odds for EV analysis
            settled_with_odds = [p for p in settled if p.odds_decimal and p.ev is not None]
            
            # Average EV (from predictions with odds)
            ev_values = [p.ev for p in settled_with_odds]
            avg_ev = (sum(ev_values) / len(ev_values)) if ev_values else 0
            
            # Realized ROI calculation
            realized_pnl = sum((p.odds_decimal - 1) if p.won else -1 for p in settled_with_odds)
            realized_roi = (realized_pnl / len(settled_with_odds) * 100) if settled_with_odds else 0
            
            # EV-ROI gap (positive = EV overestimates)
            ev_roi_gap = (avg_ev * 100) - realized_roi
            
            # EV Stability: compute rolling EV variance
            ev_stability = "stable"
            ev_variance = 0
            if len(settled_with_odds) >= 20:
                # Split into 4 quarters and compute EV for each
                quarter_size = len(settled_with_odds) // 4
                quarter_evs = []
                for i in range(4):
                    start = i * quarter_size
                    end = start + quarter_size if i < 3 else len(settled_with_odds)
                    q_evs = [p.ev for p in settled_with_odds[start:end] if p.ev]
                    if q_evs:
                        quarter_evs.append(sum(q_evs) / len(q_evs))
                
                if quarter_evs and len(quarter_evs) > 1:
                    import numpy as np
                    ev_variance = np.std(quarter_evs) * 100  # as percentage
                    if ev_variance > 15:
                        ev_stability = "unstable"
                    elif ev_variance > 8:
                        ev_stability = "variable"
            
            # Compute calibration: our_prob vs actual win rate
            probs = [p.our_prob for p in settled if p.our_prob is not None]
            avg_prob = (sum(probs) / len(probs)) if probs else 0
            calibration_error = abs(avg_prob - (wins / settled_count)) if settled_count > 0 else 0
            
            # Brier score
            brier_sum = sum((p.our_prob - (1 if p.won else 0)) ** 2 for p in settled if p.our_prob is not None and p.won is not None)
            brier_score = brier_sum / settled_count if settled_count > 0 else None
            
            # ECE (Expected Calibration Error)
            buckets = {}
            for p in settled:
                if p.our_prob is not None and p.won is not None:
                    bucket = int(p.our_prob * 10) / 10
                    if bucket not in buckets:
                        buckets[bucket] = {'total': 0, 'wins': 0}
                    buckets[bucket]['total'] += 1
                    if p.won:
                        buckets[bucket]['wins'] += 1
            
            ece = 0
            for bucket, data in buckets.items():
                predicted = bucket + 0.05
                actual = data['wins'] / data['total'] if data['total'] > 0 else 0
                ece += (data['total'] / settled_count) * abs(predicted - actual) if settled_count > 0 else 0
            
            # Sample size signal
            if settled_count < 30:
                signal = "low sample"
            elif settled_count < 100:
                signal = "emerging signal"
            else:
                signal = "statistically meaningful"
            
            # Determine trend based on recent performance vs expected
            trend = "stable"
            if settled_count >= 15:
                recent_15 = settled[-15:]
                recent_win_rate = sum(1 for p in recent_15 if p.won) / 15 * 100
                expected_win_rate = avg_prob * 100
                if recent_win_rate > expected_win_rate + 10:
                    trend = "improving"
                elif recent_win_rate < expected_win_rate - 10:
                    trend = "degrading"
            
            # Issue detection
            issues = []
            if ev_roi_gap > 15:
                issues.append("EV_inflated")
            elif ev_roi_gap < -15:
                issues.append("EV_deflated")
            if calibration_error > 0.1:
                issues.append("calibration_poor")
            if ev_stability == "unstable":
                issues.append("EV_unstable")
            
            # Check placed bets for ROI
            bets = s.execute(
                select(func.count(PlacedBet.id))
                .where(PlacedBet.market == market)
                .where(PlacedBet.settled == True)
            ).scalar() or 0
            
            roi = None
            if bets > 0:
                won_bets = s.execute(
                    select(func.count(PlacedBet.id))
                    .where(PlacedBet.market == market)
                    .where(PlacedBet.settled == True)
                    .where(PlacedBet.won == True)
                ).scalar() or 0
                roi = (won_bets / bets * 100) if bets > 0 else 0
            
            active_ver = l0_label if mode == 'active' else (active_mv.version_label if active_mv else None)

            stats[market] = {
                "market": market,
                "active_version": active_ver,
                "total_predictions": total,
                "settled_predictions": settled_count,
                "unsettled_predictions": len(unsettled),
                "wins": wins,
                "win_rate_pct": round(win_rate, 1),
                "average_prob_pct": round(avg_prob * 100, 1),
                "average_ev_pct": round(avg_ev * 100, 2),
                "realized_roi_pct": round(realized_roi, 2),
                "ev_roi_gap_pct": round(ev_roi_gap, 1),
                "ev_variance_pct": round(ev_variance, 1),
                "ev_stability": ev_stability,
                "brier_score": round(brier_score, 4) if brier_score else None,
                "ece": round(ece, 4) if ece else None,
                "calibration_error_pct": round(calibration_error * 100, 1),
                "signal": signal,
                "trend": trend,
                "issues": issues,
                "placed_bets": bets,
                "roi_pct": round(roi, 1) if roi is not None else None,
            }

    # Compute _total row (aggregate across all markets)
    all_m = list(stats.values())
    t_total = sum(m["total_predictions"] for m in all_m)
    t_settled = sum(m["settled_predictions"] for m in all_m)
    t_wins = sum(m["wins"] for m in all_m)
    t_win_rate = round(t_wins / t_settled * 100, 1) if t_settled > 0 else 0
    # Weighted averages by settled count
    def _wavg(key):
        num = sum(m[key] * m["settled_predictions"] for m in all_m if m[key] is not None)
        den = sum(m["settled_predictions"] for m in all_m if m[key] is not None)
        return round(num / den, 4) if den > 0 else None
    t_ev = round(sum(m["average_ev_pct"] * m["settled_predictions"] for m in all_m if m["average_ev_pct"] is not None)
                 / max(1, sum(m["settled_predictions"] for m in all_m if m["average_ev_pct"] is not None)), 2)
    t_brier = _wavg("brier_score")
    t_ece   = _wavg("ece")
    t_signal = "statistically meaningful" if t_settled >= 100 else ("emerging signal" if t_settled >= 30 else "low sample")
    degrading_count = sum(1 for m in all_m if m["trend"] == "degrading")
    t_trend = "degrading" if degrading_count >= 3 else ("improving" if degrading_count == 0 else "mixed")

    stats["_total"] = {
        "market": "_total",
        "active_version": None,
        "total_predictions": t_total,
        "settled_predictions": t_settled,
        "wins": t_wins,
        "win_rate_pct": t_win_rate,
        "average_ev_pct": t_ev,
        "brier_score": t_brier,
        "ece": t_ece,
        "signal": t_signal,
        "trend": t_trend,
    }

    return jsonify(stats)


@app.route('/api/models/iterations/<market>', methods=['GET'])
@require_auth
def api_model_iterations(market):
    """Get iteration history for a specific market — returns vXX_cYY labels."""
    from src.models.model_registry import get_model_registry
    versions = get_model_registry().list_versions(market, limit=50)
    return jsonify([
        {
            "id": v["id"],
            "version_number": v["version_number"],
            "version_label": v["version_label"] or f"v{v['version_number']:02d}_c00",
            "version_name": v["version_name"],
            "brier_score": round(v["brier_score"], 4) if v["brier_score"] else None,
            "accuracy": round(v["accuracy"], 4) if v["accuracy"] else None,
            "ece": round(v["ece"], 4) if v["ece"] else None,
            "sample_size": v["sample_size"],
            "is_active": v["is_active"],
            "trained_at": v["trained_at"],
        }
        for v in versions
    ])


@app.route('/api/models/graphs/<market>', methods=['GET'])
@require_auth
def api_model_graphs(market):
    """Get graph data for model lifecycle visualization.

    Returns chart-ready data for: brier_score, accuracy, calibration,
    drift, retrain_impact, sample_size.
    """
    tracker = get_model_tracker(market)
    graphs = generate_all_graphs(tracker, market)

    return jsonify(graphs)


@app.route('/api/models/retrain-events/<market>', methods=['GET'])
@require_auth
def api_retrain_events(market):
    """Get retrain events for a market."""
    tracker = get_model_tracker(market)
    lifecycle = tracker.get_lifecycle_graph()

    return jsonify(lifecycle.retrain_events)


@app.route('/api/models/versions/<market>', methods=['GET'])
@require_auth
def api_model_versions(market):
    """List all versions for a market with vXX_cYY labels."""
    from src.models.model_registry import get_model_registry
    registry = get_model_registry()
    versions = registry.list_versions(market, limit=50)
    return jsonify([
        {
            "id": v.id,
            "market": v.market,
            "version_label": v.version_label or f"v{v.version_number:02d}_c00",
            "model_number": v.model_number,
            "calibration_number": v.calibration_number,
            "version_number": v.version_number,
            "brier_score": round(v.brier_score, 4) if v.brier_score else None,
            "accuracy": round(v.accuracy, 4) if v.accuracy else None,
            "ece": round(v.ece, 4) if v.ece else None,
            "sample_size": v.sample_size,
            "model_type": v.model_type,
            "is_active": v.is_active,
            "trained_at": v.trained_at.isoformat() if v.trained_at else None,
        }
        for v in versions
    ])


@app.route('/api/models/activate/<int:version_id>', methods=['POST'])
@require_auth
def api_model_activate(version_id):
    """Activate a specific model version (deactivates all others for that market)."""
    from src.models.model_registry import get_model_registry
    registry = get_model_registry()
    ok = registry.activate(version_id)
    if ok:
        ver = registry.get_by_id(version_id)
        return jsonify({"success": True, "activated": ver["version_label"] if ver else version_id})
    return jsonify({"success": False, "error": "version not found or activation failed"}), 404


@app.route('/api/models/compare', methods=['GET'])
@require_auth
def api_model_compare():
    """Compare two model versions by metric delta.

    Query params: v1=<id>&v2=<id>
    """
    from src.models.model_registry import get_model_registry
    try:
        v1 = int(request.args.get("v1", 0))
        v2 = int(request.args.get("v2", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "v1 and v2 must be integer version IDs"}), 400

    if not v1 or not v2:
        return jsonify({"error": "provide ?v1=<id>&v2=<id>"}), 400

    registry = get_model_registry()
    return jsonify(registry.compare(v1, v2))


@app.route('/api/models/recalibrate/<market>', methods=['POST'])
@require_auth
def api_model_recalibrate(market):
    """Recalibrate the active model for a market (increments cYY, keeps vXX).

    Refits the isotonic calibrator on recent settled prediction_records.
    """
    from src.models.model_registry import get_model_registry
    from src.calibration.calibrator_fitting import fit_calibrator_for_market

    calibrator, cal_metrics = fit_calibrator_for_market(market)
    if calibrator is None:
        return jsonify({"error": "insufficient settled prediction data for recalibration"}), 400

    registry = get_model_registry()
    new_ver = registry.register_recalibration(market, calibrator, metrics=cal_metrics, reason="manual")
    if new_ver:
        return jsonify({"success": True, "version_label": new_ver["version_label"]})
    return jsonify({"error": "recalibration failed"}), 500


# =============================================================================
# API: League Calibrations
# =============================================================================

@app.route('/api/league_calibrations/active', methods=['GET'])
@require_auth
def api_league_calibrations_active():
    """Return all active VxxCyyLzzzz calibrations for the summary table.

    L0000 (league_id=NULL) is included as the global fallback row.
    Sorted by market then league name (L0000 first per market).
    """
    from sqlalchemy import select
    from src.storage.db import get_session
    from src.storage.models import LeagueCalibration, League

    with get_session() as s:
        rows = s.execute(
            select(LeagueCalibration, League.name, League.country, League.flag)
            .outerjoin(League, LeagueCalibration.league_id == League.id)
            .where(LeagueCalibration.is_active == True)
            .order_by(LeagueCalibration.market, LeagueCalibration.league_id.is_(None).desc(), League.name)
        ).all()

        result = []
        for cal, league_name, country, flag in rows:
            result.append({
                "league_id": cal.league_id,
                "league_name": league_name or ("Global (L0000)" if cal.league_id is None else f"League {cal.league_id}"),
                "country": country or "",
                "flag": flag or "",
                "market": cal.market,
                "version_label": cal.version_label,
                "slope": round(cal.slope, 4) if cal.slope is not None else None,
                "intercept": round(cal.intercept, 4) if cal.intercept is not None else None,
                "brier_score": round(cal.brier_score, 4) if cal.brier_score is not None else None,
                "brier_score_global": round(cal.brier_score_global, 4) if cal.brier_score_global is not None else None,
                "brier_improvement": round(cal.brier_improvement, 4) if cal.brier_improvement is not None else None,
                "sample_size": cal.sample_size,
                "is_global": cal.league_id is None,
            })

    return jsonify(result)


@app.route('/api/league_calibrations/<int:league_id>/<market>', methods=['GET'])
@require_auth
def api_league_calibrations(league_id, market):
    """List all LeagueCalibration rows for a (league_id, market) pair."""
    from src.storage.models import LeagueCalibration
    with get_session() as s:
        rows = s.execute(
            select(LeagueCalibration)
            .where(LeagueCalibration.league_id == league_id)
            .where(LeagueCalibration.market == market)
            .order_by(LeagueCalibration.created_at.desc())
        ).scalars().all()
        return jsonify([
            {
                "id": r.id,
                "market": r.market,
                "league_id": r.league_id,
                "version_label": r.version_label,
                "slope": r.slope,
                "intercept": r.intercept,
                "brier_score": r.brier_score,
                "brier_score_global": r.brier_score_global,
                "brier_improvement": r.brier_improvement,
                "sample_size": r.sample_size,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ])


@app.route('/api/league_calibrations/run', methods=['POST'])
@require_auth
def api_league_calibrations_run():
    """Trigger league calibration fitting for all qualifying leagues."""
    from src.calibration.league_calibration_engine import LeagueCalibrationEngine
    try:
        engine = LeagueCalibrationEngine()
        results = engine.fit_all()
        return jsonify({"success": True, "count": len(results)})
    except Exception as exc:
        logger.exception("League calibration run failed")
        return jsonify({"success": False, "error": str(exc)}), 500


# =============================================================================
# API: League Performance Ranking
# =============================================================================

@app.route('/api/leagues/performance', methods=['GET'])
@require_auth
def api_league_performance():
    """Per-(league, market) prediction performance, sorted by win rate descending.

    Returns rows with settled prediction stats, active version label (league-specific
    if available, else global), and Brier improvement where league calibration exists.
    Only leagues with >= 10 settled predictions for the market are included.
    """
    min_odds = request.args.get('min_odds', type=float)

    from src.storage.models import LeagueCalibration, ModelVersion
    with get_session() as s:
        # Aggregate prediction outcomes per (league_id, market)
        q = (
            select(
                Fixture.league_id,
                League.name.label("league_name"),
                League.country.label("country"),
                PredictionRecord.market,
                func.count(PredictionRecord.id).label("total"),
                func.sum(case((PredictionRecord.won == True, 1), else_=0)).label("won"),
            )
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
            .join(League, Fixture.league_id == League.id)
            .where(PredictionRecord.settled == True)
            .where(PredictionRecord.won.isnot(None))
        )
        if min_odds is not None:
            q = q.where(PredictionRecord.odds_decimal >= min_odds)
        pred_rows = s.execute(
            q.group_by(Fixture.league_id, League.name, League.country, PredictionRecord.market)
            .having(func.count(PredictionRecord.id) >= 10)
        ).all()

        # Active league calibrations keyed by (market, league_id)
        cal_rows = s.execute(
            select(LeagueCalibration)
            .where(LeagueCalibration.is_active == True)
        ).scalars().all()
        cal_map = {(c.market, c.league_id): c for c in cal_rows}

        # Active global versions keyed by market
        global_vers = s.execute(
            select(ModelVersion).where(ModelVersion.is_active == True)
        ).scalars().all()
        global_map = {mv.market: mv.version_label or f"v{mv.version_number:02d}_c00" for mv in global_vers}

        # Build rows inside session so ORM cal attributes are still accessible
        rows = []
        for r in pred_rows:
            win_rate = r.won / r.total if r.total else 0
            cal = cal_map.get((r.market, r.league_id))
            version = (cal.version_label if cal else None) or global_map.get(r.market, "—")
            rows.append({
                "league_id": r.league_id,
                "league": r.league_name,
                "country": r.country or "",
                "market": r.market,
                "version": version,
                "has_league_cal": cal is not None,
                "total": r.total,
                "won": r.won,
                "win_rate": round(win_rate * 100, 1),
                "brier_score": round(cal.brier_score, 4) if cal and cal.brier_score else None,
                "brier_improvement": round(cal.brier_improvement, 4) if cal and cal.brier_improvement is not None else None,
            })

    rows.sort(key=lambda x: x["win_rate"], reverse=True)
    return jsonify(rows)


@app.route('/api/clv/summary', methods=['GET'])
@require_auth
def api_clv_summary():
    """Closing Line Value summary — overall and by market.

    CLV (positive = beat the closing line = the market moved toward your
    price after you bet — a same-day signal that an "edge" was real, vs.
    waiting weeks for matches to settle). Captured near-kickoff by
    scripts/odds_poll.py::capture_closing_lines for open placed bets.
    Only includes bets where a closing line was actually captured
    (clv_pct IS NOT NULL) — most bets won't have one yet until they
    pass through their near-kickoff window.
    """
    with get_session() as s:
        rows = s.execute(
            select(
                PlacedBet.market,
                func.count(PlacedBet.id).label("n"),
                func.avg(PlacedBet.clv_pct).label("avg_clv"),
                func.sum(case((PlacedBet.clv_pct > 0, 1), else_=0)).label("positive"),
            )
            .where(PlacedBet.clv_pct.isnot(None))
            .group_by(PlacedBet.market)
        ).all()

    by_market = [
        {
            "market": r.market,
            "n": r.n,
            "avg_clv_pct": round(r.avg_clv * 100, 2) if r.avg_clv is not None else None,
            "pct_positive": round(r.positive / r.n * 100, 1) if r.n else None,
        }
        for r in rows
    ]
    total_n = sum(r["n"] for r in by_market)
    total_positive = sum(round(r["pct_positive"] / 100 * r["n"]) for r in by_market if r["pct_positive"] is not None)
    overall_avg = (
        sum(r["avg_clv_pct"] * r["n"] for r in by_market if r["avg_clv_pct"] is not None) / total_n
        if total_n else None
    )

    return jsonify({
        "overall": {
            "n": total_n,
            "avg_clv_pct": round(overall_avg, 2) if overall_avg is not None else None,
            "pct_positive": round(total_positive / total_n * 100, 1) if total_n else None,
        },
        "by_market": by_market,
    })


# =============================================================================
# API: Unified Observability (Backwards Compatible Wrappers)
# =============================================================================

@app.route('/api/unified/predictions')
@require_auth
def api_unified_predictions():
    """Unified predictions endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    predictions = truth.get("predictions", {})
    
    # Semantic validation
    computed = {"total_count": predictions.get("total_count", 0)}
    validation = validate_dashboard_snapshot("predictions", predictions, computed)
    
    # Auto-healing if divergences detected
    healing_data = predictions
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("predictions", validation.divergences, predictions)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/betting')
@require_auth
def api_unified_betting():
    """Unified betting endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    execution = truth.get("execution", {})
    data_health = truth.get("data_health", {})
    
    data = {
        "bankroll": execution.get("bankroll", 0),
        "total_staked": execution.get("total_staked", 0),
        "total_won": execution.get("total_won", 0),
        "pnl": execution.get("pnl", 0),
        "bets": data_health.get("bets", {})
    }
    
    # Semantic validation
    computed = {"bankroll": data["bankroll"], "total_staked": data["total_staked"], "pnl": data["pnl"]}
    validation = validate_dashboard_snapshot("betting", data, computed)
    
    # Auto-healing
    healing_data = data
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("betting", validation.divergences, data)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/tracking')
@require_auth
def api_unified_tracking():
    """Unified tracking/CLVE endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    clve = truth.get("clve", {})
    
    data = {
        "pds_threshold": clve.get("pds_threshold"),
        "ai_threshold": clve.get("ai_threshold"),
        "cds_threshold": clve.get("cds_threshold"),
        "system_health": clve.get("system_health", {})
    }
    
    # Semantic validation
    computed = {"pds": data["pds_threshold"], "ai": data["ai_threshold"], "cds": data["cds_threshold"]}
    validation = validate_dashboard_snapshot("tracking", data, computed)
    
    # Auto-healing
    healing_data = data
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("tracking", validation.divergences, data)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/runs')
@require_auth
def api_unified_runs():
    """Unified runs/lineage endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    lineage = truth.get("lineage", {})
    
    # Semantic validation
    computed = {"recent_runs": len(lineage.get("recent_runs", []))}
    validation = validate_dashboard_snapshot("runs", lineage, computed)
    
    # Auto-healing
    healing_data = lineage
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("runs", validation.divergences, lineage)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/health')
@require_auth
def api_unified_health():
    """Unified scheduler health endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    scheduler = truth.get("scheduler_state", {})
    
    # Semantic validation
    computed = {"job_count": scheduler.get("job_count", 0)}
    validation = validate_dashboard_snapshot("health", scheduler, computed)
    
    # Auto-healing
    healing_data = scheduler
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("health", validation.divergences, scheduler)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/governance')
@require_auth
def api_unified_governance():
    """Unified governance endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    temporal = truth.get("temporal_governance", {})
    clve = truth.get("clve", {})
    
    data = {
        "temporal": temporal,
        "clve": clve
    }
    
    # Semantic validation
    computed = {"psi": temporal.get("recent_states", [{}])[0].get("psi", 0) if temporal.get("recent_states") else 0}
    validation = validate_dashboard_snapshot("governance", data, computed)
    
    # Auto-healing
    healing_data = data
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("governance", validation.divergences, data)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


@app.route('/api/unified/architecture')
@require_auth
def api_unified_architecture():
    """Unified architecture/pipeline endpoint using system truth."""
    from src.api.system_truth_snapshot import get_truth_response
    from src.governance.ui_semantic_contract_engine import validate_dashboard_snapshot
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    truth = response.data
    pipeline = truth.get("pipeline", {})
    system_status = truth.get("system_status", {})
    
    data = {
        "pipeline": pipeline,
        "system_status": system_status
    }
    
    # Semantic validation
    computed = {"status": system_status.get("status", "unknown")}
    validation = validate_dashboard_snapshot("architecture", data, computed)
    
    # Auto-healing
    healing_data = data
    healing_applied = False
    if validation.divergences and validation.status.value != "VALID":
        from src.governance.ui_semantic_auto_healing_engine import heal_dashboard
        healing_data = heal_dashboard("architecture", validation.divergences, data)
        healing_applied = True
    
    return jsonify({
        "success": True,
        "data": healing_data,
        "meta": {
            "source": "system_truth",
            "timestamp": truth.get("timestamp")
        },
        "semantic": {
            "status": validation.status.value,
            "contract_version": validation.contract_version,
            "warnings": validation.warnings,
            "divergences": validation.divergences,
            "healing_applied": healing_applied
        }
    })


# =============================================================================
# ROUTES: System Truth (Unified Observability)
# =============================================================================

@app.route('/system/truth')
@require_auth
def system_truth():
    """Unified System Truth Layer - single source of observability."""
    from src.api.system_truth_snapshot import get_truth_response
    
    response = get_truth_response()
    return jsonify(response.to_dict())


@app.route('/system/debug/truth_validation')
@require_auth
def truth_validation():
    """Validate truth schema completeness."""
    from src.api.system_truth_snapshot import get_truth_response, validate_truth_schema
    
    response = get_truth_response()
    if not response.success:
        return jsonify({"success": False, "error": response.error})
    
    validation = validate_truth_schema(response.data)
    return jsonify({
        "success": True,
        "data": validation
    })


@app.route('/system/debug/predictions_live')
@require_auth
def debug_predictions_live():
    """Debug endpoint showing live prediction status."""
    from src.storage.db import get_session
    from src.storage.models import PredictionRecord
    from src.infra.lineage_tracker import get_lineage_tracker
    from sqlalchemy import select, func
    from datetime import datetime, timedelta
    
    with get_session() as s:
        # Generated count (last 1 hour)
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        generated = s.execute(
            select(func.count(PredictionRecord.id))
            .where(
                PredictionRecord.is_legacy == 0,
                PredictionRecord.timestamp > one_hour_ago
            )
        ).scalar() or 0
        
        # Saved count (non-legacy total)
        saved = s.execute(
            select(func.count(PredictionRecord.id))
            .where(PredictionRecord.is_legacy == 0)
        ).scalar() or 0
        
        # Linked to current run
        lineage_tracker = get_lineage_tracker()
        linked_to_run = len(lineage_tracker._current_lineage.prediction_ids) if lineage_tracker._current_lineage else 0
        
        # Visible in truth
        visible = generated  # Same as generated for recent timeframe
    
    return jsonify({
        "success": True,
        "data": {
            "generated": generated,
            "saved": saved,
            "linked_to_run": linked_to_run,
            "visible_in_truth": visible
        }
    })


@app.route('/system/debug/ui_view')
@require_auth
def system_truth_ui_debug():
    """UI debug panel showing raw system truth JSON."""
    from src.api.system_truth_snapshot import get_truth_response, validate_truth_schema
    from src.governance.ui_semantic_contract_engine import get_semantic_status
    
    response = get_truth_response()
    if not response.success:
        content = f'''
        <h1>System Truth Debug</h1>
        <div class="error">Error: {response.error}</div>
        '''
        return page(content)
    
    validation = validate_truth_schema(response.data)
    semantic_status = get_semantic_status()
    
    content = f'''
    <h1>System Truth Debug Panel</h1>
    
    <div class="card">
        <div class="card-title">Schema Validation</div>
        <pre style="color: {"#3fb950" if validation["valid"] else "#f85149"};">
Valid: {validation["valid"]}
Errors: {len(validation["errors"])}
Warnings: {len(validation["warnings"])}
        </pre>
    </div>
    
    <div class="card">
        <div class="card-title">Semantic Consistency Status</div>
        <pre style="color: {"#3fb950" if semantic_status.get("status") == "SEMANTICALLY_CONSISTENT" else "#f85149"};">
Status: {semantic_status.get("status", "UNKNOWN")}
Dashboards Validated: {semantic_status.get("dashboards_validated", 0)}
Divergence Summary: {semantic_status.get("divergence_summary", {})}
        </pre>
        <pre>{json.dumps(validation["errors"], indent=2) if validation["errors"] else "None"}</pre>
    </div>
    
    <div class="card">
        <div class="card-title">System Status</div>
        <pre>{json.dumps(response.data.get("system_status", {}), indent=2)}</pre>
    </div>
    
    <div class="card">
        <div class="card-title">Predictions</div>
        <pre>{json.dumps(response.data.get("predictions", {}), indent=2)}</pre>
    </div>
    
    <div class="card">
        <div class="card-title">Scheduler State</div>
        <pre>{json.dumps(response.data.get("scheduler_state", {}), indent=2)}</pre>
    </div>
    
    <div class="card">
        <div class="card-title">Temporal Governance</div>
        <pre>{json.dumps(response.data.get("temporal_governance", {}), indent=2)}</pre>
    </div>
    
    <div class="card">
        <div class="card-title">Data Health</div>
        <pre>{json.dumps(response.data.get("data_health", {}), indent=2)}</pre>
    </div>
    
    <script>
        // Auto-refresh every 30 seconds
        setTimeout(() => window.location.reload(), 30000);
    </script>
    '''
    return page(content)


# =============================================================================
# ROUTES: Process Monitor
# =============================================================================

@app.route('/api/system/processes')
@require_auth
def api_system_processes():
    import sqlite3
    import subprocess
    from datetime import datetime as _dt

    SCHEDULER_DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'scheduler.db')
    SCHEDULER_DB = os.path.normpath(SCHEDULER_DB)

    SCHEDULE_LABELS = {
        'fetch_fixtures':      'every 6 h',
        'fetch_results':       'every 1 h',
        'fetch_odds':          'every 1 h',
        'cleanup_matches':     'every 5 min',
        'betting_pipeline':    'every 20 min',
        'maintenance':         'every cycle (~20 min)',
        'backfill_all':        'manual',
        'backfill_cron':       'daily 06:00 UTC',
    }

    JOB_DESCRIPTIONS = {
        'fetch_fixtures':      'Pull upcoming fixtures from API-Football',
        'fetch_results':       'Update finished scores, auto-settle bets',
        'fetch_odds':          'Poll fresh odds, recalculate EV',
        'cleanup_matches':     'Archive stale live matches',
        'betting_pipeline':    'Prediction + portfolio execution cycle (20 min)',
        'maintenance':         'Backfill missing FT scores & orphaned-league standings (runs at end of each cycle)',
        'backfill_all':        'Historical data backfill (manually started)',
        'backfill_cron':       'Daily automated backfill of all 1225 leagues',
    }

    now_ts = _dt.utcnow()

    # System cron jobs not tracked by APScheduler — compute next_run from fixed schedule
    SYSTEM_CRON_NEXT = {}
    try:
        from datetime import timedelta as _td
        _today_0600 = now_ts.replace(hour=6, minute=0, second=0, microsecond=0)
        _next_0600 = _today_0600 if _today_0600 > now_ts else _today_0600 + _td(days=1)
        SYSTEM_CRON_NEXT['backfill_cron'] = {
            'next_run': _next_0600.strftime('%Y-%m-%d %H:%M'),
            'overdue': False,
        }
    except Exception:
        pass

    # ── APScheduler jobs (next_run_time) ─────────────────────────────────────
    scheduler_jobs = {}
    try:
        conn = sqlite3.connect(SCHEDULER_DB)
        rows = conn.execute('SELECT id, next_run_time FROM apscheduler_jobs').fetchall()
        conn.close()
        for job_id, nrt in rows:
            if nrt:
                nrt_dt = _dt.utcfromtimestamp(nrt)
                overdue = nrt_dt < now_ts
                scheduler_jobs[job_id] = {
                    'next_run': nrt_dt.strftime('%Y-%m-%d %H:%M'),
                    'overdue': overdue,
                }
            else:
                scheduler_jobs[job_id] = {'next_run': None, 'overdue': False}
    except Exception as e:
        scheduler_jobs['_error'] = str(e)

    # ── execution_logs: last run per job ────────────────────────────────────
    exec_history = {}
    try:
        with get_session() as s:
            from sqlalchemy import text as _text
            rows = s.execute(_text('''
                SELECT job_name, start_time, end_time, status, error_message, result_summary
                FROM execution_logs
                ORDER BY start_time DESC
            ''')).fetchall()
        # For each job: pick the most recent completed record, and note if
        # there is currently a running record with no end_time.
        seen = {}
        running_now = set()
        for job_name, start, end, status, error, summary in rows:
            if status == 'running' and not end:
                running_now.add(job_name)
            if job_name not in seen and status != 'running':
                seen[job_name] = {
                    'last_start': start,
                    'last_end': end,
                    'last_status': status,
                    'last_error': error,
                    'last_summary': summary,
                }
        # For jobs that only ever appear as running (never completed), add them
        for job_name in running_now:
            if job_name not in seen:
                seen[job_name] = {
                    'last_start': None, 'last_end': None,
                    'last_status': 'running', 'last_error': None, 'last_summary': None,
                }
        exec_history = seen
        exec_running_now = running_now
    except Exception as e:
        exec_history['_error'] = str(e)
        exec_running_now = set()

    # ── ingestion_log: last run per base job name ────────────────────────────
    ingestion_history = {}
    try:
        with get_session() as s:
            rows = s.execute(_text('''
                SELECT job_name, run_at, success, fixtures_updated, error_message
                FROM ingestion_log
                WHERE job_name NOT LIKE 'heal_%'
                AND job_name != 'test'
                ORDER BY run_at DESC
            ''')).fetchall()
        seen_ing = {}
        for job_name, run_at, success, updated, error in rows:
            if job_name not in seen_ing:
                seen_ing[job_name] = {
                    'last_run': run_at,
                    'success': bool(success),
                    'fixtures_updated': updated,
                    'error': error,
                }
        ingestion_history = seen_ing
    except Exception as e:
        ingestion_history['_error'] = str(e)

    # ── Is execution_runtime process alive? ──────────────────────────────────
    runtime_pid = None
    runtime_alive = False
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'execution_runtime.py'],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().splitlines()
        if pids:
            runtime_pid = int(pids[0])
            runtime_alive = True
    except Exception:
        pass

    # ── Detect manually-run background processes ─────────────────────────────
    backfill_running = False
    try:
        bp = subprocess.run(['pgrep', '-f', 'backfill_all.py'], capture_output=True, text=True)
        backfill_running = bool(bp.stdout.strip())
    except Exception:
        pass

    backfill_cron_running = False
    try:
        bc = subprocess.run(['pgrep', '-f', 'backfill_cron.py'], capture_output=True, text=True)
        backfill_cron_running = bool(bc.stdout.strip())
    except Exception:
        pass

    # ── Merge into unified job list ──────────────────────────────────────────
    def _parse_dt(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(s.replace(' ', 'T'))
        except ValueError:
            return None

    all_job_ids = set(SCHEDULE_LABELS.keys()) | set(exec_history.keys()) | set(ingestion_history.keys())
    jobs = []
    for job_id in sorted(all_job_ids):
        sched = scheduler_jobs.get(job_id) or SYSTEM_CRON_NEXT.get(job_id, {})
        ex = exec_history.get(job_id, {})
        ing = ingestion_history.get(job_id, {})

        _ing_updated = ing.get('fixtures_updated', 0) if ing else 0
        _ing_summary = (f"{_ing_updated} updated" if _ing_updated else ing.get('error')) if ing else None
        _ing_status = ('success' if ing.get('success') else 'failed') if ing else None

        # A job can be logged through either execution_logs (ExecutionEngine
        # dispatch) or ingestion_log (direct runtime/scheduler writes) — and a
        # job's code path can change over time, leaving stale rows behind in
        # whichever table it no longer writes to. Trust whichever source has
        # the more recent completed run rather than always favoring one table,
        # so an old "failed"/"blocked" row can't permanently shadow a fresh
        # success (or vice versa).
        ex_ts, ing_ts = _parse_dt(ex.get('last_start')), _parse_dt(ing.get('last_run'))
        if ex_ts and (not ing_ts or ex_ts >= ing_ts):
            last_run = ex.get('last_start')
            last_status = ex.get('last_status')
            last_error = ex.get('last_error')
            last_summary = ex.get('last_summary') or _ing_summary
        else:
            last_run = ing.get('last_run')
            last_status = _ing_status
            last_error = ing.get('error')
            last_summary = _ing_summary
        is_running = job_id in exec_running_now
        if job_id == 'backfill_all':
            is_running = backfill_running
        elif job_id == 'backfill_cron':
            is_running = backfill_cron_running

        jobs.append({
            'id': job_id,
            'description': JOB_DESCRIPTIONS.get(job_id, ''),
            'schedule': SCHEDULE_LABELS.get(job_id, ''),
            'running': is_running,
            'next_run': sched.get('next_run'),
            'next_overdue': sched.get('overdue', False),
            'last_run': last_run,
            'last_status': last_status,
            'last_error': last_error,
            'last_summary': last_summary,
        })

    from config.settings import settings as _settings
    return jsonify({
        'jobs': jobs,
        'runtime': {'pid': runtime_pid, 'alive': runtime_alive},
        'bot_enabled': bool(_settings.bot_enabled),
        'as_of': now_ts.strftime('%Y-%m-%d %H:%M:%S'),
    })


@app.route('/processes')
@require_auth
def processes_page():
    content = '''
<h1>Process Monitor</h1>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px;">
    <span id="runtimeBadge" style="padding:4px 12px;border-radius:4px;font-size:0.85em;">...</span>
    <span id="botEnabledBadge" style="padding:4px 12px;border-radius:4px;font-size:0.85em;">...</span>
    <span style="color:#8b949e;font-size:0.85em;">As of: <span id="asOf">—</span></span>
    <button class="btn btn-secondary" onclick="loadProcesses()" style="margin-left:auto;">Refresh</button>
</div>

<table id="processTable">
    <thead>
        <tr>
            <th>Job</th>
            <th>Description</th>
            <th>Schedule</th>
            <th>Status</th>
            <th>Last Run</th>
            <th>Last Result</th>
            <th>Next Scheduled</th>
            <th>Details</th>
        </tr>
    </thead>
    <tbody id="processBody">
        <tr><td colspan="8" style="text-align:center;color:#8b949e;">Loading...</td></tr>
    </tbody>
</table>

<script>
function loadProcesses() {
    fetch('/api/system/processes', {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            document.getElementById('asOf').textContent = d.as_of + ' UTC';

            const rt = d.runtime || {};
            const badge = document.getElementById('runtimeBadge');
            if (rt.alive) {
                badge.textContent = 'ExecutionRuntime running (PID ' + rt.pid + ')';
                badge.style.background = '#1a4731';
                badge.style.color = '#3fb950';
            } else {
                badge.textContent = 'ExecutionRuntime NOT running';
                badge.style.background = '#4a1515';
                badge.style.color = '#f85149';
            }

            const botBadge = document.getElementById('botEnabledBadge');
            if (d.bot_enabled) {
                botBadge.textContent = 'Betting ENABLED — placing real (simulated) bets';
                botBadge.style.background = '#1a4731';
                botBadge.style.color = '#3fb950';
            } else {
                botBadge.textContent = '⏸ Betting PAUSED — analysis-only (predictions still generate, no bets placed)';
                botBadge.style.background = '#3d2a0a';
                botBadge.style.color = '#d29922';
            }

            const tbody = document.getElementById('processBody');
            tbody.innerHTML = (d.jobs || []).map(j => {
                const statusCell = j.running
                    ? '<span style="color:#f0883e;">⟳ RUNNING</span>'
                    : j.last_status === 'success'
                        ? '<span style="color:#3fb950;">✓ OK</span>'
                        : j.last_status === 'failed'
                            ? '<span style="color:#f85149;">✗ FAILED</span>'
                            : '<span style="color:#8b949e;">—</span>';

                const nextCell = j.next_run
                    ? (j.next_overdue
                        ? '<span style="color:#f85149;" title="Overdue">' + j.next_run + ' ⚠</span>'
                        : '<span style="color:#8b949e;">' + j.next_run + '</span>')
                    : '<span style="color:#8b949e;">—</span>';

                const lastRunCell = j.last_run
                    ? '<span style="color:#8b949e;">' + j.last_run.toString().slice(0,16) + '</span>'
                    : '<span style="color:#8b949e;">never</span>';

                const summaryCell = j.last_summary
                    ? '<span style="color:#8b949e;font-size:0.85em;">' + j.last_summary + '</span>'
                    : '<span style="color:#8b949e;">—</span>';

                const detailCell = j.last_error
                    ? '<span style="color:#f85149;font-size:0.8em;" title="' + j.last_error.replace(/"/g, "&quot;") + '">' + j.last_error.slice(0, 60) + (j.last_error.length > 60 ? '…' : '') + '</span>'
                    : '<span style="color:#8b949e;">—</span>';

                return '<tr>' +
                    '<td style="font-family:monospace;white-space:nowrap;">' + j.id + '</td>' +
                    '<td style="color:#8b949e;font-size:0.85em;">' + (j.description || '') + '</td>' +
                    '<td style="color:#8b949e;font-size:0.85em;white-space:nowrap;">' + (j.schedule || '') + '</td>' +
                    '<td>' + statusCell + '</td>' +
                    '<td style="white-space:nowrap;">' + lastRunCell + '</td>' +
                    '<td>' + summaryCell + '</td>' +
                    '<td style="white-space:nowrap;">' + nextCell + '</td>' +
                    '<td style="max-width:300px;">' + detailCell + '</td>' +
                    '</tr>';
            }).join('');
        })
        .catch(err => {
            document.getElementById('processBody').innerHTML =
                '<tr><td colspan="8" style="color:#f85149;">Error: ' + err.message + '</td></tr>';
        });
}

loadProcesses();
setTimeout(() => window.location.reload(), 30000);
</script>
'''
    return page(content)


# =============================================================================
# ROUTES: Debug
# =============================================================================

@app.route('/debug')
@require_auth
def debug_page():
    content = '''
<h1>Debug Information</h1>

<div class="card">
    <div class="card-title">Server Status</div>
    <pre id="serverInfo">Loading...</pre>
</div>

<div class="card">
    <div class="card-title">Recent Predictions (Last 10)</div>
    <pre id="recentPreds">Loading...</pre>
</div>

<div class="card">
    <div class="card-title">Database Stats</div>
    <pre id="dbStats">Loading...</pre>
</div>

<script>
fetch('/api/admin/system_status', {credentials: 'include'})
    .then(r => r.json())
    .then(d => {
        document.getElementById('serverInfo').textContent = JSON.stringify(d, null, 2);
    });

fetch('/api/predictions?days=1', {credentials: 'include'})
    .then(r => r.json())
    .then(d => {
        document.getElementById('recentPreds').textContent = JSON.stringify(d.slice(0, 10), null, 2);
    });
</script>
'''
    return page(content)


# =============================================================================
# ROUTES: Betting Action (Core API)
# =============================================================================

@app.route('/betting/action', methods=['POST'])
@require_auth
def betting_action():
    from sqlalchemy import select, text
    from src.storage.models import BankrollRound, PlacedBet
    data = request.get_json() or {}
    action = data.get('action', 'status')

    try:
        with get_session() as s:
            # Get active round from bankroll_rounds
            active_round = s.execute(
                select(BankrollRound)
                .where(BankrollRound.is_active == True)
                .order_by(BankrollRound.round_number.desc())
                .limit(1)
            ).scalar_one_or_none()

            if not active_round:
                active_round = BankrollRound(round_number=1, initial_bankroll=1000.0, is_active=True)
                s.add(active_round)
                s.commit()

            round_id = active_round.id
            round_number = active_round.round_number
            initial = active_round.initial_bankroll

            if action == 'status':
                from src.state.betting_state import build_betting_state
                
                state = build_betting_state()
                
                pending = [b for b in state.bets if not b.get("settled")]
                settled = [b for b in state.bets if b.get("settled")]
                settled_pnl = sum(b.get("pnl", 0) or 0 for b in settled)
                
                from datetime import timezone as _utc_tz
                from zoneinfo import ZoneInfo
                _sthlm = ZoneInfo('Europe/Stockholm')

                def _fmt_bet_date(fd):
                    if fd is None:
                        return '-'
                    try:
                        if isinstance(fd, str):
                            fd = datetime.fromisoformat(fd.replace('Z', '').split('+')[0].split('.')[0])
                        if fd.tzinfo is None:
                            fd = fd.replace(tzinfo=_utc_tz.utc)
                        return fd.astimezone(_sthlm).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        return str(fd)[:16]

                bets_list = []
                for b in state.bets:
                    bets_list.append({
                        'fixture_id': b.get('fixture_id'),
                        'home': b.get('home_team', '?'),
                        'away': b.get('away_team', '?'),
                        'date': _fmt_bet_date(b.get('fixture_date')),
                        'market': b.get('market'),
                        'model_version': b.get('model_version') or '-',
                        'outcome': b.get('outcome'),
                        'stake': b.get('stake'),
                        'odds': b.get('odds'),
                        'ev': b.get('ev'),
                        'settled': b.get('settled'),
                        'won': b.get('won')
                    })

                return jsonify({
                    'ok': True,
                    'round': {
                        'balance': state.balance,
                        'round_number': state.active_round_number or 1,
                        'pending': state.pending_count,
                        'settled': len(settled),
                        'wins': state.wins,
                        'pending_stake': state.pending_stake,
                        'settled_pnl': settled_pnl,
                    },
                    'bets': bets_list
                })

            elif action == 'history':
                from src.state.betting_state import build_betting_state

                state = build_betting_state()

                return jsonify({'ok': True, 'history': state.rounds})

            elif action == 'round_detail':
                req_round_id = data.get('round_id')
                if not req_round_id:
                    return jsonify({'ok': False, 'error': 'round_id required'})

                from src.state.betting_state import build_betting_state
                from datetime import timezone as _utc_tz2
                from zoneinfo import ZoneInfo as _ZoneInfo

                _sthlm2 = _ZoneInfo('Europe/Stockholm')

                def _fmt_rd(fd):
                    if fd is None:
                        return '-'
                    try:
                        if isinstance(fd, str):
                            fd = datetime.fromisoformat(fd.replace('Z', '').split('+')[0].split('.')[0])
                        if fd.tzinfo is None:
                            fd = fd.replace(tzinfo=_utc_tz2.utc)
                        return fd.astimezone(_sthlm2).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        return str(fd)[:16]

                rd_state = build_betting_state(active_round_id=int(req_round_id))

                rd_bets = []
                for b in rd_state.bets:
                    rd_bets.append({
                        'fixture_id': b.get('fixture_id'),
                        'home': b.get('home_team', '?'),
                        'away': b.get('away_team', '?'),
                        'date': _fmt_rd(b.get('fixture_date')),
                        'market': b.get('market'),
                        'model_version': b.get('model_version') or '-',
                        'outcome': b.get('outcome'),
                        'stake': b.get('stake'),
                        'odds': b.get('odds'),
                        'ev': b.get('ev'),
                        'settled': b.get('settled'),
                        'won': b.get('won'),
                        'pnl': b.get('pnl'),
                    })

                rd_round = next((r for r in rd_state.rounds if r['id'] == int(req_round_id)), None)

                return jsonify({
                    'ok': True,
                    'round': rd_round,
                    'bets': rd_bets,
                    'stats': {
                        'wins': rd_state.wins,
                        'losses': rd_state.losses,
                        'pending': rd_state.pending_count,
                    }
                })

            elif action == 'place-bets':
                from src.betting.kelly import fractional_kelly, kelly_stake
                from src.prediction.lib.ev import expected_value
                from src.models.trainer import get_cache_path
                from src.betting.round_manager import close_round_if_full
                import pickle
                import numpy as np
                import warnings

                r = active_round
                new_r = close_round_if_full(s)
                if new_r:
                    r = new_r
                    round_id = r.id
                    initial = r.initial_bankroll

                MIN_ODDS = 1.5
                MAX_ODDS = 10.0
                EV_THRESHOLD = 0.05
                KELLY_FRACTION = 0.25
                MAX_STAKE = 50.0
                MIN_STAKE = 1.0
                BET_MARKETS = ["h2h", "btts", "ou25", "ou15"]

                now = datetime.now()
                today = now.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow = today + timedelta(days=1)

                fixes = s.execute(
                    select(Fixture, FixtureOdds)
                    .join(FixtureOdds, FixtureOdds.fixture_id == Fixture.id)
                    .where(Fixture.status == 'NS')
                    .where(Fixture.date >= today)
                    .where(Fixture.date < tomorrow)
                ).all()

                open_positions = {
                    (b.fixture_id, b.market, b.outcome)
                    for b in s.execute(
                        select(PlacedBet).where(PlacedBet.settled == False)
                    ).scalars().all()
                }

                all_candidates = []

                for fix, odds in fixes:
                    for market in BET_MARKETS:
                        model_path = get_cache_path(market)
                        if not os.path.exists(model_path):
                            continue

                        try:
                            with open(model_path, 'rb') as f:
                                obj = pickle.load(f)
                            model = obj['model'] if isinstance(obj, dict) else obj

                            home_st = s.execute(
                                select(Standing).where(Standing.team_id == fix.home_team_id).where(Standing.season >= 2024)
                            ).first()
                            away_st = s.execute(
                                select(Standing).where(Standing.team_id == fix.away_team_id).where(Standing.season >= 2024)
                            ).first()

                            if not home_st or not away_st:
                                continue

                            hs = home_st[0]
                            as_ = away_st[0]
                            features = np.array([[
                                float(hs.rank or 15),
                                float(as_.rank or 15),
                                float((hs.goals_for or 1) - (hs.goals_against or 1)),
                                float((as_.goals_for or 1) - (as_.goals_against or 1)),
                                float(hs.goals_for or 1),
                                float(as_.goals_for or 1),
                                float(hs.goals_against or 1),
                                float(as_.goals_against or 1),
                                float(abs((hs.rank or 15) - (as_.rank or 15))),
                            ]])

                            with warnings.catch_warnings():
                                warnings.filterwarnings('ignore')
                                raw_probs = model.predict_proba(features)[0]

                            if market == 'h2h':
                                if len(raw_probs) == 3:
                                    model_probs = {"1": float(raw_probs[0]), "X": float(raw_probs[1]), "2": float(raw_probs[2])}
                                else:
                                    continue
                            elif len(raw_probs) == 2:
                                model_probs = {"Yes": float(raw_probs[1]), "No": float(1 - raw_probs[1])}
                            else:
                                continue

                            odds_map = {
                                "h2h": {"1": odds.odd_home, "X": odds.odd_draw, "2": odds.odd_away},
                                "btts": {"Yes": odds.odd_btts_yes, "No": odds.odd_btts_no},
                                "ou25": {"Over": odds.odd_over, "Under": odds.odd_under},
                                "ou15": {"Over": odds.odd_over15, "Under": odds.odd_under15},
                            }

                            market_odds = odds_map.get(market, {})
                            if not market_odds:
                                continue

                            for outcome, odd in market_odds.items():
                                if not odd or odd <= 0:
                                    continue
                                if (fix.id, market, outcome) in open_positions:
                                    continue
                                our_prob = model_probs.get(outcome, 0.0)
                                if our_prob <= 0:
                                    continue
                                ev = expected_value(our_prob, odd)
                                if ev < EV_THRESHOLD:
                                    continue
                                kf = fractional_kelly(our_prob, odd, KELLY_FRACTION)
                                if kf < 0.01:
                                    continue

                                all_candidates.append({
                                    'fixture': fix,
                                    'odds_row': odds,
                                    'market': market,
                                    'outcome': outcome,
                                    'decimal_odd': odd,
                                    'our_prob': our_prob,
                                    'ev': ev,
                                    'kelly_fraction': kf,
                                })
                        except Exception as e:
                            continue

                if not all_candidates:
                    return jsonify({'ok': True, 'placed': 0, 'message': 'No value bets found'})

                all_candidates.sort(key=lambda x: x['our_prob'] * x['ev'], reverse=True)
                cand = all_candidates[0]
                fix = cand['fixture']

                if cand['decimal_odd'] < MIN_ODDS or cand['decimal_odd'] > MAX_ODDS:
                    return jsonify({'ok': True, 'placed': 0, 'message': 'Best bet odds outside range'})

                stake = kelly_stake(
                    initial, cand['our_prob'], cand['decimal_odd'],
                    KELLY_FRACTION, 0.2
                )
                stake = round(max(MIN_STAKE, min(stake, MAX_STAKE)), 2)

                active_version = s.execute(
                    select(ModelVersion).where(
                        ModelVersion.market == cand['market'],
                        ModelVersion.is_active == True
                    )
                ).scalar_one_or_none()
                model_version_id = active_version.id if active_version else None

                bet = PlacedBet(
                    round_id=r.id, fixture_id=fix.id,
                    market=cand['market'], model_version_id=model_version_id,
                    outcome=cand['outcome'],
                    stake=stake, odds=cand['decimal_odd'],
                    our_prob=cand['our_prob'], ev=cand['ev'],
                    kelly_fraction=cand['kelly_fraction'],
                )
                s.add(bet)
                s.commit()

                close_round_if_full(s)

                home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id))
                away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id))
                league_name = LEAGUE_NAMES.get(fix.league_id, '')

                win_prob_pct = round(cand['our_prob'] * 100, 1)
                return jsonify({
                    'ok': True, 'placed': 1, 'message': f"Bet placed: {home} vs {away} - {cand['market']} {cand['outcome']} @ {cand['decimal_odd']} (win prob {win_prob_pct}%)",
                    'bet': {
                        'home': home, 'away': away,
                        'market': cand['market'],
                        'model_version': active_version.version_name if active_version else None,
                        'outcome': cand['outcome'],
                        'stake': stake, 'odds': cand['decimal_odd'],
                        'ev': cand['ev'], 'our_prob': cand['our_prob'], 'settled': False
                    }
                })

            elif action == 'settle':
                return jsonify({'ok': False, 'error': 'Use /api/settle_bets endpoint'}), 400

            else:
                return jsonify({'ok': False, 'error': 'Unknown action'}), 400

    except Exception as e:
        logger.error("Betting action error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


# =============================================================================
# ROUTES: Causal Graph API
# =============================================================================

@app.route('/api/causal/node/<node_id>')
@require_auth
def api_causal_node(node_id):
    """Get a single decision node."""
    from backend.causal_graph import get_node
    node = get_node(node_id)
    if not node:
        return jsonify({'error': 'Node not found'}), 404
    return jsonify(node.to_dict())


@app.route('/api/causal/trace/<node_id>')
@require_auth
def api_causal_trace(node_id):
    """Get causal trace from a node (ancestors and descendants)."""
    from backend.causal_graph import get_node_trace
    return jsonify(get_node_trace(node_id))


@app.route('/api/causal/subgraph/<run_id>')
@require_auth
def api_causal_subgraph(run_id):
    """Get entire causal subgraph for a run."""
    from backend.causal_graph import get_subgraph_for_run
    return jsonify(get_subgraph_for_run(run_id))


@app.route('/api/causal/bet_explanation/<int:bet_id>')
@require_auth
def api_causal_bet_explanation(bet_id):
    """Get full causal explanation for a bet."""
    from backend.causal_graph import explain_bet
    result = explain_bet(bet_id)
    if 'error' in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route('/api/causal/stats')
@require_auth
def api_causal_stats():
    """Get causal graph statistics."""
    from backend.causal_graph import get_causal_stats
    return jsonify(get_causal_stats())


# =============================================================================
# ROUTES: Model Evaluation API (Offline Analytics)
# =============================================================================

@app.route('/api/model-evaluation')
@require_auth
def api_model_evaluation():
    """
    Get model performance evaluation from events.
    
    Query params:
        days: Time period (default 30)
        run_id: Specific run to evaluate
        
    Returns:
        Performance metrics from event history
    """
    from src.analytics.model_evaluator import ModelEvaluator
    
    days = request.args.get('days', default=30, type=int)
    run_id = request.args.get('run_id')
    
    evaluator = ModelEvaluator()
    
    if run_id:
        result = evaluator.evaluate_by_run(run_id)
    else:
        result = evaluator.evaluate_by_date_range(days=days)
    
    return jsonify({
        'ok': True,
        'evaluation': result
    })


@app.route('/api/model-evaluation/markets')
@require_auth
def api_market_analysis():
    """
    Get market profitability analysis.
    
    Query params:
        days: Time period (default 30)
        
    Returns:
        Market-by-market performance
    """
    from src.analytics.market_analysis import MarketAnalyzer
    
    days = request.args.get('days', default=30, type=int)
    
    analyzer = MarketAnalyzer()
    result = analyzer.analyze_markets()
    
    return jsonify({
        'ok': True,
        'markets': result
    })


@app.route('/api/model-evaluation/markets/rank')
@require_auth
def api_market_ranking():
    """Get markets ranked by profitability."""
    from src.analytics.market_analysis import MarketAnalyzer
    
    analyzer = MarketAnalyzer()
    ranking = analyzer.rank_markets()
    
    return jsonify({
        'ok': True,
        'ranking': ranking
    })


@app.route('/api/model-evaluation/compare')
@require_auth
def api_model_comparison():
    """
    Compare two model versions.
    
    Query params:
        model_a: First model version
        model_b: Second model version
        
    Returns:
        Comparison metrics
    """
    from src.analytics.model_comparator import ModelComparator
    
    model_a = request.args.get('model_a', 'version_a')
    model_b = request.args.get('model_b', 'version_b')
    
    comparator = ModelComparator()
    result = comparator.compare_models(model_a, model_b)
    
    return jsonify({
        'ok': True,
        'comparison': result
    })


@app.route('/api/model-evaluation/optimal')
@require_auth
def api_optimal_model():
    """Find the best performing model version."""
    from src.analytics.model_comparator import ModelComparator
    
    comparator = ModelComparator()
    result = comparator.find_optimal_model()
    
    return jsonify({
        'ok': True,
        'result': result
    })


# =============================================================================
# REALTIME LAYER
# =============================================================================

try:
    from src.realtime.event_stream import get_event_stream
    from src.realtime.ws_server import setup_realtime
    
    # Initialize event stream
    event_stream = get_event_stream()
    
    # Wire event bus to push events to stream
    from src.events.event_bus import event_bus as bootball_event_bus
    
    def push_to_stream(event):
        event_stream.push_event(event)
    
    bootball_event_bus._subscribers["*"].append(push_to_stream)
    
    # Setup realtime routes
    socketio = setup_realtime(app, event_stream)
    
    REALTIME_ENABLED = True
    logger.info("Realtime layer enabled")
except Exception as e:
    REALTIME_ENABLED = False
    logger.warning(f"Realtime layer disabled: {e}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    init_db()
    load_caches()
    logger.info("Starting Bootball web UI...")
    
    if REALTIME_ENABLED and socketio:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    else:
        app.run(host='0.0.0.0', port=5000, debug=False)