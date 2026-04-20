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
import secrets
import logging
import pickle
import numpy as np
from datetime import datetime, timedelta, timezone
from functools import wraps

sys.path.insert(0, '/opt/projects/bootball')

from flask import Flask, jsonify, request, make_response, render_template_string
from sqlalchemy import select, func

from config.settings import settings
from config.leagues import LEAGUES, TIER1_LEAGUE_IDS
from src.cache.prediction_cache import get_prediction_cache, cache_prediction, get_cached_prediction
from src.models.calibrator import get_calibration_cache, calibrate_prediction
from src.models.model_tracker import get_model_tracker, ModelTracker
from src.models.iteration_graph import generate_all_graphs
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureOdds, Standing, PredictionRecord, PlacedBet,
    BankrollRound, Team, League, SettledBet, UserPreference, WatchedFixture,
    ModelVersion
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# In-memory caches
TEAM_NAMES = {}
LEAGUE_NAMES = {}


def load_caches():
    """Load team and league names into memory."""
    global TEAM_NAMES, LEAGUE_NAMES
    if TEAM_NAMES:
        return
    try:
        with get_session() as s:
            teams = s.execute(select(Team)).scalars().all()
            for t in teams:
                TEAM_NAMES[t.id] = t.name

            leagues = s.execute(select(League)).scalars().all()
            for l in leagues:
                LEAGUE_NAMES[l.id] = l.name
            logger.info("Loaded %d teams, %d leagues", len(TEAM_NAMES), len(LEAGUE_NAMES))
    except Exception as e:
        logger.warning("Cache load error: %s", e)


@app.before_request
def ensure_caches():
    """Ensure caches are loaded before handling requests."""
    load_caches()


def get_password():
    return os.environ.get('BOOTBALL_PASSWORD') or getattr(settings, 'bootball_password', 'changeme')


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
    width: 200px;
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
    margin-left: 200px;
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
.btn:hover { opacity: 0.85; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
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
</style>
</head>
<body>
<div class="nav">
    <a href="/">Bootball</a>
    <a href="/predictions">Predictions</a>
    <a href="/betting">Betting</a>
    <a href="/tracking">Tracking</a>
    <a href="/admin">Admin</a>
    <a href="/debug">Debug</a>
</div>
<div class="sidebar">
    <a href="/predictions" id="navPredictions">Predictions</a>
    <a href="/betting" id="navBetting">Betting</a>
    <a href="/tracking" id="navTracking">Tracking</a>
    <a href="/admin" id="navAdmin">Admin</a>
    <a href="/debug" id="navDebug">Debug</a>
</div>
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
</div>
'''
    return page(content)


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
    <select id="daysFilter">
        <option value="1">1 Day</option>
        <option value="3" selected>3 Days</option>
        <option value="7">7 Days</option>
        <option value="all">All</option>
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
    <button class="btn btn-sm" onclick="debugLoad()">Debug Load</button>
    <span id="debugStatus" style="margin-left: 10px; color: #8b949e;"></span>
</div>
<div id="predictionsList">
    <p style="color: #8b949e;">Loading predictions...</p>
</div>
<script>
let currentMarket = 'all';
let predictionsData = [];

function debugLoad() {
    const status = document.getElementById('debugStatus');
    status.textContent = 'Starting...';
    status.style.color = '#fff';

    // First test leagues API directly
    status.textContent = 'Testing leagues API...';
    fetch('/api/leagues', {credentials: 'include'})
        .then(r => {
            status.textContent = 'Leagues status: ' + r.status;
            if (!r.ok) throw new Error('leagues failed: ' + r.status);
            return r.json();
        })
        .then(d => {
            status.textContent = 'Leagues OK: ' + Object.keys(d).length + ' countries';
            status.style.color = '#0f0';
            // Now test predictions
            const days = document.getElementById('daysFilter').value;
            const league = document.getElementById('leagueFilter').value;
            return fetch('/api/predictions?days=' + days + (league ? '&league=' + league : ''), {credentials: 'include'});
        })
        .then(r => {
            status.textContent += ', Predictions status: ' + r.status;
            if (!r.ok) throw new Error('predictions failed: ' + r.status);
            return r.json();
        })
        .then(d => {
            status.textContent += ', Predictions: ' + d.length + ' results';
            status.style.color = '#0f0';
            predictionsData = d;
            renderPredictions(d);
        })
        .catch(e => {
            status.textContent = 'Error: ' + e.message;
            status.style.color = '#f00';
            console.error('Debug error:', e);
        });
}

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
    const days = document.getElementById('daysFilter').value;
    const league = document.getElementById('leagueFilter').value;
    const container = document.getElementById('predictionsList');
    container.innerHTML = '<p style="color: #8b949e;">Loading predictions...</p>';
    const marketParam = currentMarket !== 'all' ? '&market=' + currentMarket : '';
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const url = '/api/predictions?days=' + days + (league ? '&league=' + league : '') + marketParam + '&tz=' + encodeURIComponent(tz);
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

        html += '<div class="prediction-card ' + cardClass + '">';
        html += '<div class="match-time">' + (p.date || '').slice(0, 16) + ' | <span class="league-badge">' + (p.league_name || 'Unknown') + '</span></div>';
        html += '<div class="team-name">' + (p.home_name || 'Home') + ' vs ' + (p.away_name || 'Away') + '</div>';
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

document.getElementById('daysFilter').addEventListener('change', loadPredictions);
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


def _get_model_prediction(market: str, home_team_id: int, away_team_id: int, league_id: int, error_counts: dict | None = None) -> float | None:
    """Get prediction from trained LightGBM model.

    Returns probability or None if model not available.
    Handles both old format (model only) and new format (dict with model+calibrator).
    If error_counts dict is provided, errors are counted instead of logged.
    """
    import pickle
    import os
    import numpy as np

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

    except Exception as e:
        if error_counts is not None:
            error_counts[market] = error_counts.get(market, 0) + 1
        else:
            logger.warning(f"Model prediction error for {market}: {e}")
        return None


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
        query = select(Fixture).where(Fixture.date >= now).where(Fixture.date <= end).where(Fixture.status == 'NS')
        if league_filter:
            query = query.where(Fixture.league_id == int(league_filter))
        fixtures = s.execute(query.order_by(Fixture.date)).scalars().all()

        results = []
        cache_hits = 0
        cache_misses = 0
        model_errors = {}

        for fix in fixtures[:100]:
            league_name = LEAGUE_NAMES.get(fix.league_id, '')
            home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id))
            away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id))

            preds = s.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            pred_records = {p.market: p for p in preds}

            all_odds = s.execute(select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)).scalars().all()
            odds_by_type = {row.bet_type: row for row in all_odds}
            btts_row = odds_by_type.get('btts')
            ou_row = odds_by_type.get('over_under')
            h2h_row = odds_by_type.get('h2h')

            for market in markets:
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
                    results.append(cached_pred)
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
    tz_name = request.args.get('tz', 'UTC')
    with get_session() as s:
        r = s.execute(
            select(BankrollRound)
            .where(BankrollRound.is_active == True)
            .order_by(BankrollRound.round_number.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not r:
            r = BankrollRound(round_number=1, initial_bankroll=1000.0, is_active=True)
            s.add(r)
            s.commit()

        round_id = r.id
        initial = r.initial_bankroll
        round_number = r.round_number
        pending = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == False)).scalars().all()
        settled = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == True)).scalars().all()

        pending_stake = sum((b.stake or 0) for b in pending)
        settled_pnl = sum((b.pnl or 0) for b in settled)
        settled_wins = sum(1 for b in settled if b.won)
        settled_count = len(settled)
        balance = initial + settled_pnl - pending_stake
        roi = (settled_pnl / initial * 100) if initial > 0 else 0

    content = '''
<h1>Betting Dashboard</h1>

<div class="metric-row">
    <div class="metric-box">
        <div class="metric-label">Balance</div>
        <div class="metric-value ''' + ('positive' if balance >= initial else 'negative') + '''">$''' + str(int(balance)) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Round</div>
        <div class="metric-value">#''' + str(round_number) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">ROI</div>
        <div class="metric-value ''' + ('positive' if roi >= 0 else 'negative') + '''">''' + ('+' if roi >= 0 else '') + str(int(roi)) + '''%</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Pending</div>
        <div class="metric-value pending">''' + str(len(pending)) + '''</div>
    </div>
    <div class="metric-box">
        <div class="metric-label">Wins/Losses</div>
        <div class="metric-value">''' + str(settled_wins) + '/' + str(settled_count) + '''</div>
    </div>
</div>

<div class="row">
    <div class="col">
        <div class="card">
            <div class="card-title">Actions</div>
            <button class="btn btn-success" onclick="placeBets()" id="btnPlace">Place Bets (Auto)</button>
            <button class="btn btn-danger" onclick="settleBets()" id="btnSettle">Settle Bets</button>
            <button class="btn btn-primary" onclick="newRound()">New Round</button>
        </div>
    </div>
    <div class="col">
        <div class="card">
            <div class="card-title">Pending Bets</div>
            <div id="pendingCount">''' + str(len(pending)) + ''' pending</div>
            <div id="pendingStake">Stake: $''' + str(int(pending_stake)) + '''</div>
        </div>
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

<script>
function loadBets() {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'status', tz: tz})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            document.getElementById('betsBody').innerHTML = (d.bets || []).map(b =>
                '<tr>' +
                '<td>' + b.date + '</td>' +
                '<td>' + b.home + ' vs ' + b.away + '</td>' +
                '<td>' + b.market + '</td>' +
                '<td>' + (b.model_version || '-') + '</td>' +
                '<td>' + b.outcome + '</td>' +
                '<td>$' + b.stake + '</td>' +
                '<td>' + b.odds + '</td>' +
                '<td>' + ((b.ev || 0) * 100).toFixed(1) + '%</td>' +
                '<td class="' + (b.settled ? (b.won ? 'win' : 'loss') : 'pending') + '">' +
                    (b.settled ? (b.won ? 'WIN' : 'LOSS') : 'PENDING') +
                '</td></tr>'
            ).join('');
        }
    });
}

function placeBets() {
    showMsg('Placing bet...', 'info');
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'place-bets'})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            showMsg(d.message || '1 bet placed', 'success');
            loadBets();
        } else {
            showMsg(d.message || 'Failed', 'error');
        }
    });
}

function settleBets() {
    showMsg('Settling bets...', 'info');
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'settle'})
    })
    .then(r => r.json())
    .then(d => {
        showMsg(d.message || (d.ok ? 'Done' : 'Error'), d.ok ? 'success' : 'error');
        loadBets();
    });
}

function newRound() {
    if (!confirm('Start new round? Current pending bets will be kept.')) return;
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'new-round'})
    })
    .then(r => r.json())
    .then(d => {
        showMsg('New round started', 'success');
        location.reload();
    });
}

function showMsg(text, type) {
    const el = document.getElementById('msg');
    el.textContent = text;
    el.className = 'msg msg-' + type;
    el.style.display = 'block';
    setTimeout(() => el.style.display = 'none', 5000);
}

loadBets();
</script>
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

<div id="pageNavTop" style="display:none; margin: 12px 0;">
    <button onclick="changePage(-1)" id="prevBtn">← Prev</button>
    <span id="pageNumbers"></span>
    <button onclick="changePage(1)" id="nextBtn">Next →</button>
    <span id="pageInfo" style="margin-left: 12px;"></span>
</div>

<table>
    <thead>
        <tr>
            <th>Date</th>
            <th>Match</th>
            <th>Market</th>
            <th>Ver</th>
            <th>Pick</th>
            <th>Prob</th>
            <th>Odds</th>
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

    fetch(url, {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            totalResults = d.total || 0;
            const tbody = document.getElementById('trackingBody');
            tbody.innerHTML = (d.results || []).map(r =>
                '<tr>' +
                '<td>' + formatLocalDateTime(r.date) + '</td>' +
                '<td>' + r.home + ' vs ' + r.away + '</td>' +
                '<td>' + (r.market || '') + '</td>' +
                '<td>' + (r.model_version || '-') + '</td>' +
                '<td>' + (r.predicted || '-') + '</td>' +
                '<td>' + (r.prob ? (r.prob * 100).toFixed(0) + '%' : '-') + '</td>' +
                '<td>' + (r.odds || '-') + '</td>' +
                '<td>' + (r.ev ? (r.ev * 100).toFixed(1) + '%' : '-') + '</td>' +
                '<td>' + (r.actual || '-') + '</td>' +
                '<td class="' + (r.settled ? (r.won ? 'win' : 'loss') : 'pending') + '">' +
                    (r.settled ? (r.won ? 'WIN' : 'LOSS') : 'PENDING') +
                '</td>' +
                '<td>' + (r.pnl !== null ? (r.pnl >= 0 ? '+' : '') + r.pnl.toFixed(2) : '-') + '</td>' +
                '</tr>'
            ).join('');

            // Stats
            const settled = (d.results || []).filter(r => r.settled);
            const wins = settled.filter(r => r.won).length;
            const total = settled.length;
            document.getElementById('statsBox').innerHTML =
                '<strong>' + wins + '/' + total + '</strong> wins (' +
                (total > 0 ? (wins/total*100).toFixed(0) : 0) + '% win rate) | Total: ' + d.total;

            updatePagination();
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
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    settled_only = request.args.get('settled', 'false') == 'true'
    pending_only = request.args.get('settled', 'false') == 'false' and request.args.get('settled') is not None
    market = request.args.get('market', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')

    with get_session() as s:
        query = (
            select(PredictionRecord, Fixture.home_team_id, Fixture.away_team_id, Fixture.date, Fixture.goals_home, Fixture.goals_away)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
        )

        if settled_only:
            query = query.where(PredictionRecord.settled == True)
        elif pending_only:
            query = query.where(PredictionRecord.settled == False)
        # else: no filter - show all (settled and pending)

        if market:
            query = query.where(PredictionRecord.market == market)

        if from_date:
            from datetime import datetime
            query = query.where(Fixture.date >= datetime.strptime(from_date, '%Y-%m-%d'))
        if to_date:
            from datetime import datetime
            query = query.where(Fixture.date <= datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))

        # Get total count before pagination
        count_query = select(PredictionRecord.id).join(Fixture, PredictionRecord.fixture_id == Fixture.id)
        if settled_only:
            count_query = count_query.where(PredictionRecord.settled == True)
        elif pending_only:
            count_query = count_query.where(PredictionRecord.settled == False)
        if market:
            count_query = count_query.where(PredictionRecord.market == market)
        if from_date:
            count_query = count_query.where(Fixture.date >= datetime.strptime(from_date, '%Y-%m-%d'))
        if to_date:
            count_query = count_query.where(Fixture.date <= datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))

        total = len(s.execute(count_query).scalars().all())

        query = query.order_by(Fixture.date.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = s.execute(query).all()

        results = []
        for pred, home_id, away_id, fix_date, goals_home, goals_away in rows:
            home = TEAM_NAMES.get(home_id, str(home_id))
            away = TEAM_NAMES.get(away_id, str(away_id))
            actual = f"{goals_home}-{goals_away}" if goals_home is not None else None

            model_ver = None
            if pred.model_version_id:
                mv = s.execute(select(ModelVersion).where(ModelVersion.id == pred.model_version_id)).scalar_one_or_none()
                if mv:
                    model_ver = mv.version_name or f"v{mv.version_number}"

            results.append({
                'fixture_id': pred.fixture_id,
                'home': home,
                'away': away,
                'date': fix_date.isoformat() + 'Z' if fix_date else None,
                'market': pred.market,
                'model_version': model_ver,
                'predicted': pred.predicted_outcome,
                'actual': actual,
                'prob': pred.our_prob,
                'odds': None,
                'ev': None,
                'settled': pred.settled,
                'won': pred.won,
                'pnl': None,
            })

        return jsonify({'results': results, 'total': total})


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
</div>

<div id="adminMsg" class="msg" style="display:none;"></div>

<h2 style="margin-top: 24px;">Model Training & Stats</h2>
<div class="row">
    <div class="col">
        <div class="card">
            <div class="card-title">Model Status</div>
            <div id="modelStats">Loading...</div>
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

<script>
function runMaintenance() {
    showMsg('Running maintenance checks...', 'info');
    fetch('/api/admin/maintenance', {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            let html = '<div style="margin-bottom: 12px;"><strong>System Health</strong></div>';
            html += '<div>API Calls Remaining: <strong>' + d.api_calls + '</strong></div>';
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
    fetch('/api/admin/settle', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg(d.message || 'Done', 'success'));
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

function loadModelStats() {
    fetch('/api/models/stats', {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            let html = '<table style="width: 100%;">';
            html += '<thead><tr><th>Market</th><th>Iterations</th><th>Retrains</th><th>Brier</th><th>Baseline</th><th>Drift</th><th>Trend</th></tr></thead>';
            html += '<tbody>';
            for (const [market, stats] of Object.entries(d)) {
                const driftClass = stats.is_drifted ? (stats.drift_score > 0 ? 'degrading' : 'improving') : '';
                html += '<tr class="' + driftClass + '">';
                html += '<td><strong>' + market + '</strong></td>';
                html += '<td>' + (stats.total_iterations || 0) + '</td>';
                html += '<td>' + (stats.total_retrains || 0) + '</td>';
                html += '<td>' + (stats.current_brier ? stats.current_brier.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (stats.baseline_brier ? stats.baseline_brier.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + getDriftBadge(stats.drift_score, stats.is_drifted) + '</td>';
                html += '<td>' + getTrendBadge(stats.overall_trend) + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            document.getElementById('modelStats').innerHTML = html;
        })
        .catch(e => {
            document.getElementById('modelStats').innerHTML = '<div style="color: #f85149;">Error loading stats</div>';
        });
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
    .then(r => r.json())
    .then(d => {
        document.getElementById('systemStatus').innerHTML =
            '<div>API Calls Today: <strong>' + (d.api_calls || 0) + '</strong></div>' +
            '<div>DB Fixtures: <strong>' + (d.fixture_count || 0) + '</strong></div>' +
            '<div>Last Daily Run: <strong>' + (d.last_daily_run || 'Never') + '</strong></div>';
    });

// Load model stats on page load
loadModelStats();

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
            html += '<thead><tr><th>Version</th><th>Name</th><th>Brier</th><th>Accuracy</th><th>ECE</th><th>Samples</th><th>Active</th><th>Trained</th></tr></thead>';
            html += '<tbody>';
            for (const iter of d) {
                const activeBadge = iter.is_active ? '<span style="background: #3fb950; color: #000; padding: 2px 6px; border-radius: 3px;">Active</span>' : '';
                html += '<tr>';
                html += '<td>v' + iter.version_number + '</td>';
                html += '<td>' + (iter.version_name || '-') + '</td>';
                html += '<td>' + (iter.brier_score ? iter.brier_score.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (iter.accuracy ? (iter.accuracy * 100).toFixed(1) + '%' : 'N/A') + '</td>';
                html += '<td>' + (iter.ece ? (iter.ece * 100).toFixed(2) + '%' : 'N/A') + '</td>';
                html += '<td>' + iter.sample_size.toLocaleString() + '</td>';
                html += '<td>' + activeBadge + '</td>';
                html += '<td>' + (iter.trained_at ? new Date(iter.trained_at).toLocaleDateString() : 'N/A') + '</td>';
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
            html += '<thead><tr><th>Date</th><th>Reason</th><th>Before</th><th>After</th><th>Drift Trigger</th></tr></thead>';
            html += '<tbody>';
            for (const event of d) {
                const driftBadge = event.triggered_by_drift ? '<span style="color: #f85149;">Yes</span>' : '<span style="color: #8b949e;">No</span>';
                html += '<tr>';
                html += '<td>' + (event.created_at ? new Date(event.created_at).toLocaleDateString() : 'N/A') + '</td>';
                html += '<td>' + event.reason + '</td>';
                html += '<td>' + (event.brier_score_before ? event.brier_score_before.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (event.brier_score_after ? event.brier_score_after.toFixed(4) : 'N/A') + '</td>';
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
</script>

<style>
.degrading { background: rgba(248, 81, 73, 0.1); }
.improving { background: rgba(63, 185, 80, 0.1); }
</style>

<h2 style="margin-top: 24px;">Configuration</h2>
<table>
    <thead>
        <tr>
            <th>Setting</th>
            <th>Value</th>
            <th>Actions</th>
        </tr>
    </thead>
    <tbody id="configBody">
    </tbody>
</table>
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


@app.route('/api/admin/settle', methods=['POST'])
@require_auth
def admin_settle():
    import subprocess
    result = subprocess.run(
        [sys.executable, 'scripts/settle_fixtures.py'],
        capture_output=True, text=True, timeout=60
    )
    return jsonify({'ok': True, 'message': f"Settled: {result.stdout[:500]}"})


@app.route('/api/admin/maintenance', methods=['GET', 'POST'])
@require_auth
def api_admin_maintenance():
    """Run maintenance checks and return status."""
    from src.ingestion.client import calls_remaining_today
    from src.storage.models import Fixture, Team, Standing, PredictionRecord, PlacedBet
    from sqlalchemy import select, func

    results = {}

    with get_session() as s:
        results['api_calls'] = calls_remaining_today()

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

        orphaned = s.execute(
            select(Fixture)
            .where(Fixture.league_id.notin_(select(Standing.league_id).distinct()))
            .limit(5)
        ).scalars().all()
        results['orphaned_fixtures'] = len(orphaned)

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
        # Multi-class: Brier score for each class averaged, no calibration ECE
        brier_raw = float(brier_score_loss(y_test, raw_probs, multi_class='raw') if hasattr(brier_score_loss, 'multi_class') else np.mean([brier_score_loss(y_test == i, raw_probs[:, i]) for i in range(num_classes)]))
        brier_calibrated = brier_raw  # No calibration for multi-class yet
        ece = 0.0  # ECE not implemented for multi-class

    # Get next version number
    tracker = get_model_tracker(market)
    existing_iterations = tracker.get_iterations(limit=1)
    version_number = (existing_iterations[0].version_number + 1) if existing_iterations else 1

    # Calculate accuracy
    if num_classes == 2:
        y_pred = (calibrated_probs > 0.5).astype(float)
        accuracy = float(np.mean(y_pred == y_binary))
    else:
        # Multi-class: use raw_probs for predictions
        y_pred = np.argmax(raw_probs, axis=1)
        accuracy = float(np.mean(y_pred == y_test))

    # Record to ModelVersion
    from src.storage.models import ModelVersion, RetrainEvent
    with get_session() as s:
        old_version = s.execute(
            select(ModelVersion)
            .where(ModelVersion.market == market)
            .where(ModelVersion.is_active == True)
        ).scalar_one_or_none()

        old_version_id = old_version.id if old_version else None
        old_brier = old_version.brier_score if old_version else None

        if old_version:
            old_version.is_active = False

        new_version = ModelVersion(
            market=market,
            version_number=version_number,
            version_name=f"v{version_number}",
            brier_score=brier_calibrated,
            accuracy=accuracy,
            ece=ece,
            sample_size=len(X_train),
            calibration_sample_size=len(X_test),
            model_type=f'{model_name.lower()}+isotonic',
            is_active=True,
        )
        s.add(new_version)
        s.flush()

        # Record retrain event if this is not the first version
        if old_version:
            retrain_event = RetrainEvent(
                market=market,
                old_version_id=old_version_id,
                new_version_id=new_version.id,
                reason=reason,
                reason_detail=f"Brier: {old_brier:.4f} -> {brier_calibrated:.4f}",
                brier_score_before=old_brier,
                brier_score_after=brier_calibrated,
                triggered_by_drift=False,
            )
            s.add(retrain_event)

        s.commit()

    # Save model and calibrator to disk for predictions to use
    import pickle
    import os
    model_dir = '/opt/projects/bootball/data'
    os.makedirs(model_dir, exist_ok=True)

    model_data = {
        'model': model,
        'calibrator': calibrator,
        'market': market,
        'version': version_number,
        'features_used': ['rank', 'goal_diff', 'goals_for', 'goals_against', 'rank_diff'],
    }

    model_path = os.path.join(model_dir, f'model_{market}.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump(model_data, f)
    logger.info(f"Saved {market} model to {model_path}")

    logger.info(f"Trained {market} v{version_number}: Brier={brier_calibrated:.4f}, ECE={ece:.4f}, Accuracy={accuracy:.3f}")

    return {
        'version': version_number,
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
    from src.ingestion.client import calls_remaining_today
    with get_session() as s:
        fixture_count = s.execute(select(func.count()).select_from(Fixture)).scalar() or 0

    return jsonify({
        'api_calls': calls_remaining_today(),
        'fixture_count': fixture_count,
        'last_daily_run': 'Check logs',
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
    """Get model performance stats for all markets.

    Returns summary stats for each market.
    """
    from config.markets import get_all_markets, get_active_markets

    stats = {}
    for market_config in get_active_markets():
        tracker = get_model_tracker(market_config.market_id)
        market_id = market_config.market_id
        lifecycle = tracker.get_lifecycle_graph()
        stats[market_id] = {
            "market": market_id,
            "total_iterations": len(lifecycle.iterations),
            "total_retrains": len(lifecycle.retrain_events),
            "current_brier": round(lifecycle.current_brier, 4) if lifecycle.current_brier else None,
            "baseline_brier": round(lifecycle.baseline_brier, 4) if lifecycle.baseline_brier else None,
            "drift_score": round(lifecycle.drift_score, 4) if lifecycle.drift_score else 0,
            "overall_trend": lifecycle.overall_trend,
            "is_drifted": abs(lifecycle.drift_score) > 0.05 if lifecycle.drift_score else False,
        }

    return jsonify(stats)


@app.route('/api/models/iterations/<market>', methods=['GET'])
@require_auth
def api_model_iterations(market):
    """Get iteration history for a specific market.

    Returns list of model versions with metrics.
    """
    tracker = get_model_tracker(market)
    iterations = tracker.get_iterations(limit=50)

    return jsonify([
        {
            "version_number": i.version_number,
            "version_name": i.version_name,
            "brier_score": round(i.brier_score, 4),
            "accuracy": round(i.accuracy, 4) if i.accuracy else None,
            "ece": round(i.ece, 4) if i.ece else None,
            "sample_size": i.sample_size,
            "is_active": i.is_active,
            "trained_at": i.trained_at.isoformat() if i.trained_at else None,
        }
        for i in iterations
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
    data = request.get_json() or {}
    action = data.get('action', 'status')

    try:
        with get_session() as s:
            r = s.execute(
                select(BankrollRound)
                .where(BankrollRound.is_active == True)
                .order_by(BankrollRound.round_number.desc())
                .limit(1)
            ).scalar_one_or_none()

            if not r:
                r = BankrollRound(round_number=1, initial_bankroll=1000.0, is_active=True)
                s.add(r)
                s.commit()

            round_id = r.id
            initial = r.initial_bankroll

            if action == 'status':
                tz_name = data.get('tz', 'UTC')
                pending = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == False)).scalars().all()
                settled = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == True)).scalars().all()

                pending_stake = sum((b.stake or 0) for b in pending)
                settled_pnl = sum((b.pnl or 0) for b in settled)
                balance = initial + settled_pnl - pending_stake

                def format_date(dt, tz):
                    if dt is None:
                        return '-'
                    try:
                        from zoneinfo import ZoneInfo
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        local = dt.astimezone(ZoneInfo(tz))
                        return local.strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        return dt.strftime('%Y-%m-%d %H:%M')

                bets_list = []
                for b in settled + pending:
                    fix = s.execute(select(Fixture).where(Fixture.id == b.fixture_id)).scalar_one_or_none()
                    home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id)) if fix else '?'
                    away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id)) if fix else '?'
                    fix_date = format_date(fix.date, tz_name) if fix else '-'

                    model_ver = None
                    if b.model_version_id:
                        mv = s.execute(select(ModelVersion).where(ModelVersion.id == b.model_version_id)).scalar_one_or_none()
                        if mv:
                            model_ver = mv.version_name or f"v{mv.version_number}"

                    bets_list.append({
                        'home': home, 'away': away, 'date': fix_date, 'market': b.market,
                        'model_version': model_ver,
                        'outcome': b.outcome, 'stake': b.stake, 'odds': b.odds,
                        'ev': b.ev, 'settled': b.settled, 'won': b.won
                    })

                return jsonify({
                    'ok': True,
                    'round': {
                        'balance': balance,
                        'round_number': r.round_number,
                        'pending': len(pending),
                        'settled': len(settled),
                        'wins': sum(1 for b in settled if b.won),
                        'pending_stake': pending_stake,
                    },
                    'bets': bets_list
                })

            elif action == 'place-bets':
                from src.betting.kelly import fractional_kelly, kelly_stake
                from src.betting.ev import expected_value
                from src.models.trainer import get_cache_path
                import pickle
                import numpy as np
                import warnings

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

                all_candidates.sort(key=lambda x: x['ev'], reverse=True)
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

                home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id))
                away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id))
                league_name = LEAGUE_NAMES.get(fix.league_id, '')

                return jsonify({
                    'ok': True, 'placed': 1, 'message': f"Bet placed: {home} vs {away} - {cand['market']} {cand['outcome']} @ {cand['decimal_odd']}",
                    'bet': {
                        'home': home, 'away': away,
                        'market': cand['market'],
                        'model_version': active_version.version_name if active_version else None,
                        'outcome': cand['outcome'],
                        'stake': stake, 'odds': cand['decimal_odd'],
                        'ev': cand['ev'], 'settled': False
                    }
                })

            elif action == 'settle':
                from src.settlement import settle_all
                result = settle_all(days=7)
                return jsonify({
                    'ok': True,
                    'message': f"Updated: {result['fixtures_updated']}, Settled: {result['bets_settled']}, P/L: {result['total_pnl']:+.2f}"
                })

            elif action == 'new-round':
                from sqlalchemy import func
                from src.storage.models import PlacedBet

                settled = s.execute(
                    select(PlacedBet).where(PlacedBet.round_id == r.id).where(PlacedBet.settled == True)
                ).scalars().all()

                r.is_active = False
                r.ended_at = datetime.utcnow()
                r.ending_balance = initial + sum(b.pnl or 0 for b in settled)
                r.total_bets = len(settled)
                r.total_wins = sum(1 for b in settled if b.won)
                r.total_staked = sum(b.stake for b in settled)
                r.total_pnl = sum(b.pnl or 0 for b in settled)
                r.roi_pct = (r.total_pnl / r.total_staked * 100) if r.total_staked > 0 else 0
                r.reason = 'manual_reset'

                new_round = BankrollRound(
                    round_number=r.round_number + 1,
                    started_at=datetime.utcnow(),
                    initial_bankroll=initial,
                    is_active=True,
                )
                s.add(new_round)
                s.commit()

                return jsonify({'ok': True, 'round_number': new_round.round_number})

        return jsonify({'ok': False, 'error': 'Server error'}), 500

    except Exception as e:
        logger.error("Betting action error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    init_db()
    load_caches()
    logger.info("Starting Bootball web UI...")
    app.run(host='0.0.0.0', port=5000, debug=False)