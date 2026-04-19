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
from datetime import datetime, timedelta, timezone
from functools import wraps

sys.path.insert(0, '/opt/projects/bootball')

from flask import Flask, jsonify, request, make_response, render_template_string
from sqlalchemy import select, func

from config.settings import settings
from config.leagues import LEAGUES, TIER1_LEAGUE_IDS
from src.cache.prediction_cache import get_prediction_cache, cache_prediction, get_cached_prediction
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureOdds, Standing, PredictionRecord, PlacedBet,
    BankrollRound, Team, League, SettledBet, UserPreference
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

    let html = '';
    for (const p of data.slice(0, 50)) {
        const evClass = p.ev_positive ? 'ev-positive' : 'ev-negative';
        const cardClass = p.ev_positive ? 'ev-positive' : 'ev-negative';
        const confidence = Math.round((p.prob || 0.33) * 100);
        const market = p.market || 'h2h';
        const pick = p.pick || '?';
        const odds = p.odds || '-';
        const ev = p.ev || 0;

        html += '<div class="prediction-card ' + cardClass + '">';
        html += '<div class="match-time">' + (p.date || '').slice(0, 16) + ' | <span class="league-badge">' + (p.league_name || 'Unknown') + '</span></div>';
        html += '<div class="team-name">' + (p.home_name || 'Home') + ' vs ' + (p.away_name || 'Away') + '</div>';
        html += '<div style="margin: 8px 0;">';
        html += '<span class="badge badge-info">' + market.toUpperCase() + '</span> ';
        html += '<span>Pick: <strong>' + pick + '</strong></span> ';
        html += '<span>Odds: ' + odds + '</span> ';
        html += '<span>EV: <span class="' + evClass + '">' + (ev > 0 ? '+' : '') + ev.toFixed(1) + '%</span></span>';
        html += '</div>';
        html += '<div class="confidence-bar"><div class="confidence-fill" style="width: ' + confidence + '%"></div></div>';
        html += '<div style="font-size: 12px; color: #8b949e;">Confidence: ' + confidence + '%</div>';
        html += '</div>';
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

                if market == 'btts':
                    prob = pred_records.get('btts').our_prob if pred_records.get('btts') else None
                    odds = btts_row.odd_btts_yes if btts_row else None
                    pick = 'Yes' if prob and prob > 0.5 else 'No'
                elif market == 'ou25':
                    prob = pred_records.get('ou25').our_prob if pred_records.get('ou25') else None
                    odds = ou_row.odd_over if ou_row else None
                    pick = 'Over' if prob and prob > 0.5 else 'Under'
                elif market == 'ou15':
                    prob = pred_records.get('ou15').our_prob if pred_records.get('ou15') else None
                    odds = ou_row.odd_over15 if ou_row else None
                    pick = 'Over' if prob and prob > 0.5 else 'Under'
                elif market == 'h2h':
                    prob = pred_records.get('h2h').our_prob if pred_records.get('h2h') else None
                    odds = h2h_row.odd_home if h2h_row else None
                    pick = 'Home' if prob and prob > 0.5 else 'Away'

                if prob is None or odds is None or odds <= 0:
                    continue

                ev = compute_ev(prob, odds)

                # Store UTC date in cache, format on retrieval
                pred_result = {
                    'fixture_id': fix.id,
                    'date_utc': fix.date.isoformat() if fix.date else None,
                    'market': market,
                    'pick': pick,
                    'prob': prob,
                    'odds': odds,
                    'ev': ev,
                    'ev_positive': ev > 0,
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

        return jsonify(results)


# =============================================================================
# ROUTES: Betting
# =============================================================================

@app.route('/betting')
@require_auth
def betting_page():
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
        <div class="metric-value">''' + str(sum(1 for b in settled if b.won)) + '/' + str(len(settled)) + '''</div>
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
            <th>Match</th>
            <th>Market</th>
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
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'status'})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            document.getElementById('betsBody').innerHTML = (d.bets || []).map(b => 
                '<tr>' +
                '<td>' + b.home + ' vs ' + b.away + '</td>' +
                '<td>' + b.market + '</td>' +
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
    showMsg('Placing bets...', 'info');
    fetch('/betting/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'place-bets'})
    })
    .then(r => r.json())
    .then(d => {
        showMsg(d.placed + ' bets placed', d.ok ? 'success' : 'error');
        loadBets();
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
        showMsg(d.settled + ' bets settled', d.ok ? 'success' : 'error');
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
        <select id="daysFilter">
            <option value="1">1 Day</option>
            <option value="3" selected>3 Days</option>
            <option value="7">7 Days</option>
            <option value="all">All</option>
        </select>
    </div>
    <div class="col card">
        <div class="card-title">Stats</div>
        <div id="statsBox">Loading...</div>
    </div>
</div>

<table>
    <thead>
        <tr>
            <th>Date</th>
            <th>Match</th>
            <th>Market</th>
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

<script>
function loadTracking() {
    const status = document.getElementById('statusFilter').value;
    const days = document.getElementById('daysFilter').value;
    const settledParam = status === 'settled' ? '&settled=true' : (status === 'pending' ? '&settled=false' : '');
    const daysParam = days === 'all' ? '' : '&days=' + days;
    fetch('/api/predictions/recent?limit=100' + settledParam + daysParam, {credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            const tbody = document.getElementById('trackingBody');
            tbody.innerHTML = (d.results || []).map(r =>
                '<tr>' +
                '<td>' + ((r.date || '').slice(0, 10)) + '</td>' +
                '<td>' + r.home + ' vs ' + r.away + '</td>' +
                '<td>' + (r.market || '') + '</td>' +
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
                (total > 0 ? (wins/total*100).toFixed(0) : 0) + '% win rate)';
        });
}
loadTracking();
document.getElementById('statusFilter').addEventListener('change', loadTracking);
document.getElementById('daysFilter').addEventListener('change', loadTracking);
</script>
'''
    return page(content)


@app.route('/api/predictions/recent')
@require_auth
def api_predictions_recent():
    limit = int(request.args.get('limit', 100))
    settled_only = request.args.get('settled', 'false') == 'true'
    pending_only = request.args.get('settled', 'false') == 'false' and request.args.get('settled') is not None
    days = request.args.get('days', 'all')

    with get_session() as s:
        query = (
            select(PredictionRecord, Fixture.home_team_id, Fixture.away_team_id, Fixture.date, Fixture.goals_home, Fixture.goals_away)
            .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
        )

        if settled_only:
            query = query.where(PredictionRecord.settled == True)
        elif pending_only:
            query = query.where(PredictionRecord.settled == False)
        else:
            query = query.where(PredictionRecord.settled == True)

        if days != 'all':
            from datetime import datetime, timedelta
            days_int = int(days)
            now = datetime.utcnow()
            start = now - timedelta(days=days_int)
            query = query.where(Fixture.date >= start)

        query = query.order_by(Fixture.date.desc()).limit(limit)
        rows = s.execute(query).all()

        results = []
        for pred, home_id, away_id, fix_date, goals_home, goals_away in rows:
            home = TEAM_NAMES.get(home_id, str(home_id))
            away = TEAM_NAMES.get(away_id, str(away_id))
            actual = f"{goals_home}-{goals_away}" if goals_home is not None else None

            results.append({
                'fixture_id': pred.fixture_id,
                'home': home,
                'away': away,
                'date': fix_date.isoformat() if fix_date else None,
                'market': pred.market,
                'predicted': pred.predicted_outcome,
                'actual': actual,
                'prob': pred.our_prob,
                'odds': None,
                'ev': None,
                'settled': pred.settled,
                'won': pred.won,
                'pnl': None,
            })

        return jsonify({'results': results})


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
            <button class="btn btn-primary" onclick="runDailyRun()">Run Daily Run</button>
            <button class="btn btn-primary" onclick="placeBets()">Place Bets</button>
            <button class="btn btn-danger" onclick="settleBets()">Settle Bets</button>
            <button class="btn btn-primary" onclick="trainModels()">Train Models</button>
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

<script>
function runDailyRun() {
    fetch('/api/admin/daily_run', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg(d.output || 'Done', 'success'));
}

function placeBets() {
    fetch('/api/admin/place_bets', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg(d.message || 'Done', 'success'));
}

function settleBets() {
    fetch('/api/admin/settle', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg(d.message || 'Done', 'success'));
}

function trainModels() {
    showMsg('Training models...', 'info');
    fetch('/api/admin/train', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => showMsg('Models trained', 'success'));
}

function showMsg(text, type) {
    const el = document.getElementById('adminMsg');
    el.textContent = text;
    el.className = 'msg msg-' + type;
    el.style.display = 'block';
}

fetch('/api/admin/system_status', {credentials: 'include'})
    .then(r => r.json())
    .then(d => {
        document.getElementById('systemStatus').innerHTML =
            '<div>API Calls Today: <strong>' + (d.api_calls || 0) + '</strong></div>' +
            '<div>DB Fixtures: <strong>' + (d.fixture_count || 0) + '</strong></div>' +
            '<div>Last Daily Run: <strong>' + (d.last_daily_run || 'Never') + '</strong></div>';
    });
</script>
'''
    return page(content)


@app.route('/api/admin/daily_run', methods=['POST'])
@require_auth
def admin_daily_run():
    import subprocess
    result = subprocess.run(
        [sys.executable, 'scripts/daily_run.py'],
        capture_output=True, text=True, timeout=300
    )
    return jsonify({'ok': True, 'output': result.stdout[:2000]})


@app.route('/api/admin/place_bets', methods=['POST'])
@require_auth
def admin_place_bets():
    import subprocess
    result = subprocess.run(
        [sys.executable, 'scripts/auto_bet.py', '--bet-only'],
        capture_output=True, text=True, timeout=60
    )
    return jsonify({'ok': True, 'message': f"Bets placed: {result.stdout[:500]}"})


@app.route('/api/admin/settle', methods=['POST'])
@require_auth
def admin_settle():
    import subprocess
    result = subprocess.run(
        [sys.executable, 'scripts/settle_fixtures.py'],
        capture_output=True, text=True, timeout=60
    )
    return jsonify({'ok': True, 'message': f"Settled: {result.stdout[:500]}"})


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
                pending = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == False)).scalars().all()
                settled = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == True)).scalars().all()

                pending_stake = sum((b.stake or 0) for b in pending)
                settled_pnl = sum((b.pnl or 0) for b in settled)
                balance = initial + settled_pnl - pending_stake

                bets_list = []
                for b in settled + pending:
                    fix = s.execute(select(Fixture).where(Fixture.id == b.fixture_id)).scalar_one_or_none()
                    home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id)) if fix else '?'
                    away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id)) if fix else '?'
                    bets_list.append({
                        'home': home, 'away': away, 'market': b.market,
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
                from src.betting.value_bets import find_all_market_value_bets
                from src.betting.kelly import fractional_kelly

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

                placed = 0
                for fix, odds in fixes[:15]:
                    try:
                        candidates = find_all_market_value_bets(
                            fix.id, fix.home_team_id, fix.away_team_id,
                            odds, markets=['btts', 'ou25'], ev_threshold=0.05
                        )
                        if not candidates:
                            continue
                        cand = candidates[0]
                        if cand.decimal_odd < 1.5 or cand.decimal_odd > 10:
                            continue
                        kf = fractional_kelly(cand.our_prob, cand.decimal_odd, 0.25)
                        if kf < 0.02:
                            continue

                        existing = s.execute(
                            select(PlacedBet)
                            .where(PlacedBet.fixture_id == fix.id)
                            .where(PlacedBet.round_id == round_id)
                            .where(PlacedBet.settled == False)
                        ).first()
                        if existing:
                            continue

                        stake = round(min(50, max(1, kf * 1000)), 2)

                        bet = PlacedBet(
                            round_id=round_id, fixture_id=fix.id,
                            market=cand.market, outcome=cand.outcome,
                            stake=stake, odds=cand.decimal_odd,
                            our_prob=cand.our_prob, ev=cand.ev,
                            kelly_fraction=kf,
                        )
                        s.add(bet)
                        placed += 1
                    except Exception as e:
                        logger.warning("Bet error: %s", e)
                        continue

                s.commit()
                return jsonify({'ok': True, 'placed': placed})

            elif action == 'settle':
                settled_count = 0
                pending = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == False)).scalars().all()

                for bet in pending:
                    fix = s.execute(select(Fixture).where(Fixture.id == bet.fixture_id)).scalar_one_or_none()
                    if not fix or fix.status != 'FT' or fix.goals_home is None:
                        continue

                    total = fix.goals_home + fix.goals_away
                    if bet.market == 'btts':
                        result = 'Yes' if (fix.goals_home > 0 and fix.goals_away > 0) else 'No'
                    elif bet.market == 'ou25':
                        result = 'Over' if total > 2.5 else 'Under'
                    else:
                        continue

                    bet.settled = True
                    bet.result = result
                    bet.won = (bet.outcome == result)
                    bet.pnl = ((bet.odds - 1) * bet.stake) if bet.won else (-bet.stake)
                    bet.settled_at = datetime.utcnow()
                    settled_count += 1

                if settled_count > 0:
                    s.commit()

                return jsonify({'ok': True, 'settled': settled_count})

            elif action == 'new-round':
                r.is_active = False
                r.ended_at = datetime.utcnow()

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