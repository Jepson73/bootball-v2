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

RUN_SYSTEM_ACTIVATION_TIMESTAMP = "2026-04-25 06:30:00"

sys.path.insert(0, '/opt/projects/bootball')

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
    BankrollRound, Team, League, UserPreference, WatchedFixture,
    ModelVersion, Bankroll
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
    <div id="liveGamesSidebar" style="padding: 12px 8px;">
        <div id="liveGamesHeader" style="font-size: 11px; color: #8b949e; margin-bottom: 8px; text-transform: uppercase;">Live Now</div>
        <div id="liveGamesList" style="color: #c9d1d9; font-size: 13px;">Loading...</div>
    </div>
</div>
<script>
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
            
            const sortedLeagues = Object.keys(gamesByLeague).sort((a, b) => {
                const tierA = gamesByLeague[a][0]?.league_tier || 99;
                const tierB = gamesByLeague[b][0]?.league_tier || 99;
                if (tierA !== tierB) return tierA - tierB;
                return a.localeCompare(b);
            });
            
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
                    if (g.status === 'upcoming') {
                        const evBadge = g.ev_badge || '';
                        const homeLogo = g.home_logo ? '<img src="' + g.home_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-right:4px;">' : '';
                        const awayLogo = g.away_logo ? '<img src="' + g.away_logo + '" style="width:16px;height:16px;vertical-align:middle;margin-left:4px;">' : '';
                        html += '<div style="margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #21262d;">' +
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
                        html += '<div style="margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #21262d;">' +
                            '<div style="font-weight: 600;">' + homeLogo + g.home + ' <span style="color: #58a6ff;">' + score + '</span> ' + g.away + awayLogo + '</div>' +
                            '<div style="font-size: 11px; color: #d29922; margin: 2px 0;">' + matchState + '</div>' +
                            '</div>';
                    }
                }
            }
            
            list.innerHTML = html;
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
        mode_class = 'live_eval' if mode_badge == 'LIVE_EVAL' else 'training' if mode_badge == 'TRAINING' else 'dev'
        
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
    
    if new_mode not in ['dev', 'training', 'live_eval']:
        return jsonify({"error": "Invalid mode. Must be: dev, training, or live_eval"}), 400
    
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


# =============================================================================
# UI: System Control Panel
# =============================================================================

@app.route('/settings/system')
def system_control_page():
    from backend.runtime_mode import get_mode_name
    
    mode = get_mode_name()
    mode_class = 'live_eval' if mode == 'live_eval' else 'training' if mode == 'training' else 'dev'
    
    mode_description = {
        'dev': 'Full flexibility - all operations allowed',
        'training': 'Model updates allowed, betting disabled',
        'live_eval': 'Frozen evaluation mode - no mutations allowed'
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
                <td>Full flexibility - all operations allowed</td>
            </tr>
            <tr>
                <td><span class="badge badge-training">TRAINING</span></td>
                <td>Training</td>
                <td>Model updates allowed, betting disabled</td>
            </tr>
            <tr>
                <td><span class="badge badge-live_eval">LIVE_EVAL</span></td>
                <td>Live Evaluation</td>
                <td>Frozen system - read-only, no mutations</td>
            </tr>
        </tbody>
    </table>
    
    <script>
    function switchMode(mode, btn) {{
        if (mode === 'live_eval' && !confirm('WARNING: Switching to LIVE_EVAL will disable model retraining and restrict mutating operations. Continue?')) {{
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
        candidates_html += f'''
        <tr>
            <td>{c.get('architecture_id')}</td>
            <td>{', '.join(c.get('active_layers', []))}</td>
            <td style="color: {'#3fb950' if c.get('validation_score', 0) > 0.6 else '#f85149'}">{c.get('validation_score', 0):.2f}</td>
            <td>{c.get('ev_score', 0):.4f}</td>
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
        
        fetch('/api/architecture/simulate/' + data.proposed_architecture)
        .then(r => r.json())
        .then(sim => {{
            if (sim.is_safe) {{
                alert('Simulation passed! Safe to apply.');
            }} else {{
                alert('Warning: Simulation shows risk. Validation score: ' + sim.validation_score);
            }}
        }})
        .catch(err => {{
            document.getElementById('proposal-results').innerHTML = '<div style="color: #f85149;">Error: ' + err + '</div>';
        }});
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
        html += '<div class="prediction-card ' + cardClass + '">';
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


@app.route('/api/live-games')
@require_auth
def api_live_games():
    """Get live and upcoming games for sidebar."""
    now = datetime.utcnow()
    today_str = now.strftime('%Y-%m-%d')
    
    live_fresh = {}
    try:
        from src.ingestion.client import APIFootballClient
        client = APIFootballClient()
        for status in ['1H', '2H', 'HT']:
            raw = client.get_fixtures(date=today_str, status=status, force_refresh=True)
            for r in raw:
                fix = r.get('fixture', {})
                fid = fix.get('id')
                if fid:
                    status_info = fix.get('status', {})
                    league = r.get('league', {})
                    live_fresh[fid] = {
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
                # Use SQLAlchemy parameter binding for IN clause (safe from SQL injection)
                league_rows = s.execute(
                    text("""
                        SELECT f.id, l.name, l.country, COALESCE(l.tier, 99)
                        FROM fixtures f
                        LEFT JOIN leagues l ON f.league_id = l.id
                        WHERE f.id IN :fixture_ids
                    """),
                    {"fixture_ids": tuple(all_fixture_ids)}
                ).fetchall()
                for row in league_rows:
                    country = row[2] or ''
                    flag = league_flags.get(country, '🌍')
                    league_info[row[0]] = {
                        'league_name': row[1] or '',
                        'league_country': country,
                        'league_flag': flag,
                        'league_tier': row[3] if row[3] else 99
                    }
            
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
                    l.country as league_country,
                    COALESCE(l.tier, 99) as league_tier
                FROM fixtures f
                JOIN teams home ON f.home_team_id = home.id
                JOIN teams away ON f.away_team_id = away.id
                LEFT JOIN leagues l ON f.league_id = l.id
                WHERE f.status = 'NS' AND f.date >= :now AND f.date < :cutoff_24h
                ORDER BY l.tier ASC, l.name ASC, f.date ASC
            """), {
                'now': now.isoformat(),
                'cutoff_24h': cutoff_24h.isoformat()
            }).fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Query error: {e}")
        upcoming_rows = []
    
    results = []
    
    for fid, data in live_fresh.items():
        status = data.get('status')
        elapsed = data.get('elapsed')
        
        if status == 'FT':
            continue
        
        from backend.services.match_state_renderer import render_match_state
        match_state = render_match_state(status, elapsed)
        
        league_name = data.get('league_name', '')
        league_country = data.get('league_country', '')
        league_flag = league_flags.get(league_country, '🌍')
        league_display = league_flag + ' ' + league_name
        
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
            'league_tier': 99,
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
            'league_tier': row[12] if len(row) > 12 else 99,
            'best_market': None,
            'best_odds': None,
            'date': str(row[6]) if row[6] else '',
        })
    
    response = jsonify(results)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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

            # Get team logos and league flag
            home_logo = away_logo = league_flag = None
            home_team = s.execute(select(Team).where(Team.id == fix.home_team_id)).scalar_one_or_none()
            if home_team:
                home_logo = home_team.logo_url
            away_team = s.execute(select(Team).where(Team.id == fix.away_team_id)).scalar_one_or_none()
            if away_team:
                away_logo = away_team.logo_url
            league_rec = s.execute(select(League).where(League.id == fix.league_id)).scalar_one_or_none()
            if league_rec:
                league_flag = league_rec.flag

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

                # Fallback: check PredictionRecord from DB (already loaded as pred_records)
                db_pred = pred_records.get(market)
                if db_pred and db_pred.odds_decimal and db_pred.ev and db_pred.ev > 0:
                    cached_pred = {
                        'fixture_id': fix.id,
                        'date_utc': fix.date.isoformat() if fix.date else None,
                        'market': market,
                        'pick': db_pred.predicted_outcome,
                        'prob': db_pred.our_prob,
                        'calibrated_prob': round(db_pred.calibrated_prob or db_pred.our_prob, 3),
                        'odds': db_pred.odds_decimal,
                        'ev': db_pred.ev,
                        'ev_positive': bool(db_pred.ev > 0),
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
        <div class="metric-label">Wins/Losses</div>
        <div class="metric-value">''' + str(state.wins) + '/' + str(state.losses) + '''</div>
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
        <button class="btn btn-primary" onclick="newRound()">New Round</button>
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
                els[4].querySelector('.metric-value').textContent = r.wins + '/' + r.settled;
                els[5].querySelector('.metric-value').textContent = 'SEK ' + Math.round(r.pending_stake);
            }
        }
        
        // Build bets table
        var html = "";
        for (var i = 0; i < d.bets.length; i++) {
            var b = d.bets[i];
            var evPct = (b.ev * 100).toFixed(1) + '%';
            var result = b.settled ? (b.won ? 'WIN' : 'LOSS') : 'PENDING';
            html = html + "<tr>" +
                "<td>" + b.date + "</td>" +
                "<td>" + b.home + " vs " + b.away + "</td>" +
                "<td>" + b.market + "</td>" +
                "<td>" + (b.model_version || '-') + "</td>" +
                "<td>" + b.outcome + "</td>" +
                "<td>SEK " + b.stake + "</td>" +
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
            html = html + "<tr>" +
                "<td>#" + h.round_number + "</td>" +
                "<td>" + h.started_at + "</td>" +
                "<td>SEK " + h.initial_bankroll + "</td>" +
                "<td>" + h.ended_at + "</td>" +
                "<td>SEK " + h.total_pnl.toFixed(2) + "</td>" +
                "<td>" + h.roi_pct.toFixed(1) + "%</td>" +
                "<td>" + h.status + "</td>" +
                "</tr>";
        }
        document.getElementById('historyBody').innerHTML = html;
    });
}

loadBets();
</script>
'''
    return page(content)


@app.route('/betting/run/<run_id>')
@require_auth
def betting_run_page(run_id):
    """Detail view for a specific experiment run."""
    from sqlalchemy import text
    tz_name = request.args.get('tz', 'UTC')
    
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
                # Handle string timestamps
                if isinstance(dt, str):
                    return dt[:16] if len(dt) >= 16 else dt
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local = dt.astimezone(ZoneInfo(tz))
                return local.strftime('%Y-%m-%d %H:%M')
            except Exception:
                return str(dt)[:16]
        
        bets_list = []
        for b in bets:
            fix_id = b[2]  # fixture_id column
            fix = s.execute(select(Fixture).where(Fixture.id == fix_id)).scalar_one_or_none()
            home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id)) if fix else '?'
            away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id)) if fix else '?'
            
            model_ver = None
            model_ver_id = b[16]  # model_version_id column
            if model_ver_id:
                mv = s.execute(select(ModelVersion).where(ModelVersion.id == model_ver_id)).scalar_one_or_none()
                if mv:
                    model_ver = mv.version_name or f"v{mv.version_number}"
            
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
                'result': b[11]  # actual_result column
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
        
        content += '''
        <tr>
            <td>''' + str(b['date']) + '''</td>
            <td>''' + str(b['home']) + ' vs ' + str(b['away']) + '''</td>
            <td>''' + str(b['market']) + '''</td>
            <td>''' + (str(b['model_version']) if b['model_version'] else '-') + '''</td>
            <td>''' + str(b['outcome']) + '''</td>
            <td>SEK ''' + str(b['stake']) + '''</td>
            <td>''' + str(b['odds']) + '''</td>
            <td>''' + str(round((b['ev'] or 0) * 100, 1)) + '''%</td>
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

<div class="row" style="margin-bottom: 24px;">
    <div class="col card">
        <div class="card-title">Calibration (Expected vs Actual Wins)</div>
        <div id="calibrationBox">Loading...</div>
    </div>
    <div class="col card">
        <div class="card-title">Odds Summary</div>
        <div id="oddsBox">Loading...</div>
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
            <th><button onclick="toggleDateSort()" id="dateSortBtn" style="background:none;border:none;color:inherit;cursor:pointer;padding:0;font-weight:bold;">Date ▼</button></th>
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
let dateSortDesc = true;

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
                return '<tr>' +
                '<td>' + formatLocalDateTime(r.date) + '</td>' +
                '<td>' + homeLogo + r.home + ' vs ' + r.away + awayLogo + '</td>' +
                '<td>' + (r.market || '') + '</td>' +
                '<td>' + (r.model_version || '-') + '</td>' +
                '<td>' + (r.predicted || '-') + '</td>' +
                '<td>' + (r.prob ? (r.prob * 100).toFixed(0) + '%' : '-') + '</td>' +
                '<td>' + (r.odds || '-') + '</td>' +
                '<td>' + (r.bookmaker || '-') + '</td>' +
                '<td>' + (r.ev ? (r.ev * 100).toFixed(1) + '%' : '-') + '</td>' +
                '<td>' + (r.actual || '-') + '</td>' +
                '<td class="' + (r.settled ? (r.won ? 'win' : 'loss') : 'pending') + '">' +
                    (r.settled ? (r.won ? 'WIN' : 'LOSS') : 'PENDING') +
                '</td>' +
                '<td>' + (r.pnl !== null && r.pnl !== undefined ? (r.pnl >= 0 ? '+' : '') + r.pnl.toFixed(2) : '-') + '</td>' +
                '</tr>';
            }).join('');

            // Stats - use server-side calculated stats
            const st = d.stats || {};
            const oc = st.odds_coverage || {};
            const marketRows = Object.entries(st.by_market || {}).map(([m, s]) =>
                '<span style="margin-right:12px;">' + m.toUpperCase() + ': ' + s.wins + '/' + s.total + ' (' + s.win_pct + '%)</span>'
            ).join('');
            document.getElementById('statsBox').innerHTML =
                '<div style="margin-bottom:8px;">' +
                '<strong>' + (st.total_wins || 0) + '/' + (st.total_settled || 0) + '</strong> wins (' + (st.win_pct || 0) + '%) | ' +
                'Pending: <strong>' + (st.total_unsettled || 0) + '</strong> | ' +
                'Total: ' + ((st.total_settled || 0) + (st.total_unsettled || 0)) +
                '</div>' +
                '<div style="font-size:0.85em;color:#8b949e;">' + marketRows + '</div>' +
                '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #21262d;font-size:0.85em;">' +
                '<strong style="color:#d29922;">Odds Coverage:</strong> ' +
                oc.fixtures_with_odds + '/' + oc.fixtures_with_predictions + ' (' + oc.odds_pct + '%) with odds | ' +
                'NS: ' + oc.ns_with_odds + '/' + oc.ns_fixtures + ' (' + oc.ns_odds_pct + '%) upcoming' +
                '</div>' +
                '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #21262d;font-size:0.85em;">' +
                '<strong style="color:#f0883e;">High EV (≥10%):</strong> ' +
                (st.high_ev_history?.wins || 0) + '/' + (st.high_ev_history?.total || 0) + ' wins (' + (st.high_ev_history?.win_pct || 0) + '%) | ' +
                'P&L: <strong style="color:' + ((st.high_ev_history?.pnl || 0) >= 0 ? '#3fb950' : '#f85149') + ';">' + (st.high_ev_history?.pnl || 0).toFixed(2) + '</strong> | ' +
                'Upcoming: <strong>' + (st.high_ev_history?.upcoming || 0) + '</strong>' +
                '</div>';

            // High EV picks (EV >= 10%)
            const highEv = (d.results || []).filter(r => r.ev && r.ev >= 0.05);
            const highEvByFixture = {};
            for (const r of highEv) {
                const key = r.fixture_id;
                if (!highEvByFixture[key] || r.ev > highEvByFixture[key].ev) {
                    highEvByFixture[key] = r;
                }
            }
            const topHighEv = Object.values(highEvByFixture).sort((a, b) => b.ev - a.ev).slice(0, 10);
            let highEvHtml = '<table style="width:100%;font-size:0.85em;"><tr><th>Match</th><th>Pick</th><th>EV</th><th>Result</th><th>P&L</th></tr>';
            for (const r of topHighEv) {
                highEvHtml += '<tr>' +
                    '<td>' + r.home + ' vs ' + r.away + '</td>' +
                    '<td>' + r.predicted + ' @ ' + r.odds + '</td>' +
                    '<td style="color:#3fb950;font-weight:bold;">' + (r.ev * 100).toFixed(0) + '%</td>' +
                    '<td class="' + (r.settled ? (r.won ? 'win' : 'loss') : 'pending') + '">' + (r.actual || '-') + '</td>' +
                    '<td>' + (r.pnl !== undefined ? r.pnl.toFixed(2) : '-') + '</td>' +
                    '</tr>';
            }
            highEvHtml += '</table>';
            if (topHighEv.length === 0) {
                highEvHtml = '<div style="color:#8b949e;font-size:0.85em;">No high-EV picks found</div>';
            }
            // Removed highEvBox - using sidebar for upcoming picks

// Calibration table
            const cal = st.calibration || {};
            let calHtml = '<table style="width:100%;font-size:0.85em;"><tr><th>Bucket</th><th>Wins</th><th>Total</th><th>%</th><th>Expected</th></tr>';
            const markets = ['h2h', 'btts', 'ou25', 'ou15'];
            for (const m of markets) {
                const buckets = cal[m] || [];
                if (buckets.length === 0) continue;
                calHtml += '<tr><td colspan="5" style="background:#21262d;padding:4px;">' + m.toUpperCase() + '</td></tr>';
                for (const b of buckets) {
                    const diff = b.wins - b.expected_wins;
                    const diffStr = diff >= 0 ? '+' + diff.toFixed(1) : diff.toFixed(1);
                    calHtml += '<tr><td>' + b.bucket + '</td><td>' + b.wins + '</td><td>' + b.total + '</td><td>' + b.win_pct + '%</td><td style="color:' + (diff >= 0 ? '#3fb950' : '#f85149') + ';">' + diffStr + '</td></tr>';
                }
            }
            calHtml += '</table>';
            document.getElementById('calibrationBox').innerHTML = calHtml;

            // Odds summary
            const odds = st.odds_summary || {};
            let oddsHtml = '<table style="width:100%;font-size:0.85em;"><tr><th>Market</th><th>Avg</th><th>Highest Won</th><th>Lowest Lost</th></tr>';
            for (const m of markets) {
                const o = odds[m] || {};
                oddsHtml += '<tr><td>' + m.toUpperCase() + '</td><td>' + (o.avg || '-') + '</td><td>' + (o.highest_won || '-') + '</td><td>' + (o.lowest_lost || '-') + '</td></tr>';
            }
            oddsHtml += '</table>';
            document.getElementById('oddsBox').innerHTML = oddsHtml;

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
    sort_desc = request.args.get('sort_desc', 'true') == 'true'

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

        # Server-side stats across ALL predictions (not filtered)
        all_settled = s.execute(
            select(PredictionRecord).where(PredictionRecord.settled == True)
        ).scalars().all()
        all_wins = sum(1 for p in all_settled if p.won)
        total_settled = len(all_settled)
        total_unsettled = s.execute(
            select(func.count(PredictionRecord.id)).where(PredictionRecord.settled == False)
        ).scalar() or 0

        # Stats by market
        market_stats = {}
        for m in ['h2h', 'btts', 'ou25', 'ou15']:
            market_preds = [p for p in all_settled if p.market == m]
            market_wins = sum(1 for p in market_preds if p.won)
            market_stats[m] = {
                'wins': market_wins,
                'total': len(market_preds),
                'win_pct': round(market_wins / len(market_preds) * 100) if market_preds else 0
            }

        # Calibration buckets: win% by probability bucket
        calibration_buckets = {}
        bucket_edges = [0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
        bucket_labels = ['40-50%', '50-60%', '60-70%', '70-80%', '80%+']
        
        for m in ['h2h', 'btts', 'ou25', 'ou15']:
            market_preds = [p for p in all_settled if p.market == m and p.calibrated_prob is not None]
            bucket_data = []
            for i in range(len(bucket_edges) - 1):
                low, high = bucket_edges[i], bucket_edges[i + 1]
                bucket_preds = [p for p in market_preds if low <= p.calibrated_prob < high]
                wins = sum(1 for p in bucket_preds if p.won)
                total = len(bucket_preds)
                expected_wins = total * ((low + high) / 2)  # mid-point as expected
                bucket_data.append({
                    'bucket': bucket_labels[i],
                    'total': total,
                    'wins': wins,
                    'win_pct': round(wins / total * 100) if total else 0,
                    'expected_wins': round(expected_wins, 1)
                })
            calibration_buckets[m] = bucket_data
        
        # Odds summary per market
        odds_summary = {}
        for m in ['h2h', 'btts', 'ou25', 'ou15']:
            market_preds = [p for p in all_settled if p.market == m and p.odds_decimal is not None]
            if not market_preds:
                odds_summary[m] = {'avg': 0, 'highest_won': 0, 'lowest_lost': 0, 'count': 0}
                continue
            
            avg_odds = sum(p.odds_decimal for p in market_preds) / len(market_preds)
            won_preds = [p.odds_decimal for p in market_preds if p.won]
            lost_preds = [p.odds_decimal for p in market_preds if not p.won]
            highest_won = max(won_preds) if won_preds else 0
            lowest_lost = min(lost_preds) if lost_preds else 0
            
            odds_summary[m] = {
                'avg': round(avg_odds, 2),
                'highest_won': round(highest_won, 2) if highest_won else 0,
                'lowest_lost': round(lowest_lost, 2) if lowest_lost else 0,
                'count': len(market_preds)
            }
        
        # Odds coverage stats
        from datetime import datetime as dt
        from sqlalchemy import func as sql_func
        
        total_fixtures_with_preds = s.execute(
            select(sql_func.count(sql_func.distinct(PredictionRecord.fixture_id)))
        ).scalar() or 0
        
        # Fixtures that have both predictions and at least one odds entry
        fixtures_with_odds = s.execute(
            select(sql_func.count(sql_func.distinct(FixtureOdds.fixture_id)))
        ).scalar() or 0
        
        total_ns_fixtures = s.execute(
            select(sql_func.count(Fixture.id))
            .where(Fixture.status == 'NS')
            .where(Fixture.date >= dt.utcnow())
        ).scalar() or 0
        
        # NS fixtures that have at least one odds entry
        ns_with_odds = s.execute(
            select(sql_func.count(sql_func.distinct(FixtureOdds.fixture_id)))
            .join(Fixture, Fixture.id == FixtureOdds.fixture_id)
            .where(Fixture.status == 'NS')
            .where(Fixture.date >= dt.utcnow())
        ).scalar() or 0
        
        # Odds coverage - only count fixtures with BOTH predictions AND odds
        fixtures_with_both = s.execute(
            select(sql_func.count(sql_func.distinct(FixtureOdds.fixture_id)))
            .join(PredictionRecord, FixtureOdds.fixture_id == PredictionRecord.fixture_id)
        ).scalar() or 0
        
        odds_coverage = {
            'fixtures_with_predictions': total_fixtures_with_preds,
            'fixtures_with_odds': fixtures_with_both,
            'odds_pct': round(fixtures_with_both / total_fixtures_with_preds * 100) if total_fixtures_with_preds else 0,
            'ns_fixtures': total_ns_fixtures,
            'ns_with_odds': ns_with_odds,
            'ns_odds_pct': round(ns_with_odds / total_ns_fixtures * 100) if total_ns_fixtures else 0,
        }
        
        stats = {
            'total_settled': total_settled,
            'total_wins': all_wins,
            'win_pct': round(all_wins / total_settled * 100) if total_settled else 0,
            'total_unsettled': total_unsettled,
            'by_market': market_stats,
            'calibration': calibration_buckets,
            'odds_summary': odds_summary,
            'odds_coverage': odds_coverage,
        }
        
        # High EV historical stats (settled predictions with EV >= 10%)
        high_ev_settled = [p for p in all_settled if p.ev and p.ev >= 0.10]
        high_ev_wins = sum(1 for p in high_ev_settled if p.won)
        high_ev_total = len(high_ev_settled)
        high_ev_wins_pct = round(high_ev_wins / high_ev_total * 100) if high_ev_total else 0
        high_ev_pnl = sum((p.odds_decimal - 1) if p.won else -1 for p in high_ev_settled if p.odds_decimal)
        
        # High EV upcoming (unsettled) count
        all_unsettled = s.execute(
            select(PredictionRecord).where(PredictionRecord.settled == False)
        ).scalars().all()
        high_ev_upcoming = len([p for p in all_unsettled if p.ev and p.ev >= 0.10])
        
        stats['high_ev_history'] = {
            'wins': high_ev_wins,
            'total': high_ev_total,
            'win_pct': high_ev_wins_pct,
            'pnl': round(high_ev_pnl, 2),
            'upcoming': high_ev_upcoming,
        }

        order_col = Fixture.date.desc() if sort_desc else Fixture.date.asc()
        query = query.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
        rows = s.execute(query).all()

        results = []
        for pred, home_id, away_id, fix_date, goals_home, goals_away in rows:
            home = TEAM_NAMES.get(home_id, str(home_id))
            away = TEAM_NAMES.get(away_id, str(away_id))
            actual = f"{goals_home}-{goals_away}" if goals_home is not None else None

            # Get logos
            home_logo = away_logo = None
            home_team = s.execute(select(Team).where(Team.id == home_id)).scalar_one_or_none()
            if home_team:
                home_logo = home_team.logo_url
            away_team = s.execute(select(Team).where(Team.id == away_id)).scalar_one_or_none()
            if away_team:
                away_logo = away_team.logo_url

            model_ver = None
            if pred.model_version_id:
                mv = s.execute(select(ModelVersion).where(ModelVersion.id == pred.model_version_id)).scalar_one_or_none()
                if mv:
                    model_ver = mv.version_name or f"v{mv.version_number}"

            # Calculate P&L - only show when odds_decimal is not null
            pnl = None
            if pred.settled and pred.odds_decimal:
                pnl = (pred.odds_decimal - 1) if pred.won else -1

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
                'odds': pred.odds_decimal,
                'bookmaker': pred.bookmaker,
                'ev': pred.ev,
                'settled': pred.settled,
                'won': pred.won,
                'pnl': pnl,
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
    fetch('/api/settle_bets', {method: 'POST', credentials: 'include'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                showMsg(d.message || 'Settled', 'success');
                loadBets();
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

function loadModelStats() {
    fetch('/api/models/stats', {credentials: 'include'})
        .then(r => {
            if (!r.ok) throw new Error('Auth required');
            return r.json();
        })
        .then(d => {
            let html = '<table style="width: 100%;">';
            html += '<thead><tr><th>Market</th><th>Predictions</th><th>Settled</th><th>Win%</th><th>Avg EV</th><th>Brier</th><th>ECE</th><th>Signal</th><th>Trend</th></tr></thead>';
            html += '<tbody>';
            for (const [market, stats] of Object.entries(d)) {
                const trendClass = stats.trend === 'improving' ? 'win' : (stats.trend === 'degrading' ? 'loss' : '');
                const evClass = stats.average_ev_pct > 0 ? 'win' : 'loss';
                html += '<tr class="' + trendClass + '">';
                html += '<td><strong>' + market.toUpperCase() + '</strong></td>';
                html += '<td>' + stats.total_predictions + '</td>';
                html += '<td>' + stats.settled_predictions + '</td>';
                html += '<td class="' + (stats.win_rate_pct >= 50 ? 'win' : 'loss') + '">' + stats.win_rate_pct + '%</td>';
                html += '<td class="' + evClass + '">' + stats.average_ev_pct + '%</td>';
                html += '<td>' + (stats.brier_score ? stats.brier_score.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + (stats.ece ? stats.ece.toFixed(4) : 'N/A') + '</td>';
                html += '<td>' + stats.signal + '</td>';
                html += '<td>' + getTrendBadge(stats.trend) + '</td>';
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
    .then(r => {
        if (!r.ok) throw new Error('Auth required');
        return r.json();
    })
    .then(d => {
        document.getElementById('systemStatus').innerHTML =
            '<div>API Calls Today: <strong>' + (d.api_calls || 0) + '</strong></div>' +
            '<div>DB Fixtures: <strong>' + (d.fixture_count || 0) + '</strong></div>' +
            '<div>Last Daily Run: <strong>' + (d.last_daily_run || 'Never') + '</strong></div>';
    })
    .catch(() => {
        document.getElementById('systemStatus').innerHTML = '<div style="color:#f85149;">Please login</div>';
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
        from src.settlement import settle_all, fetch_and_update_fixtures, update_live_fixture_statuses
        
        # Fetch fixtures from API first
        fixtures_updated = fetch_and_update_fixtures(days=7)
        logger.info(f"Updated {fixtures_updated} fixtures")
        
        # Update live game statuses
        live_updated = update_live_fixture_statuses()
        logger.info(f"Updated {live_updated} live fixtures")
        
        # Settle all bets and predictions
        result = settle_all()
        
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

        non_cup_leagues = select(League.id).where(~League.name.ilike('%cup%'))
        orphaned = s.execute(
            select(Fixture)
            .where(Fixture.league_id.notin_(select(Standing.league_id).distinct()))
            .where(Fixture.league_id.in_(non_cup_leagues))
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
    """Get model performance stats computed from actual prediction_records data.
    
    Returns computed metrics for each market including:
    - EV vs ROI alignment
    - EV stability (variance over rolling windows)
    - Calibration quality
    - Trend stability indicators
    """
    from sqlalchemy import select, func
    from src.storage.db import get_session
    from src.storage.models import PredictionRecord, Fixture, PlacedBet
    
    markets = ['h2h', 'btts', 'ou25', 'ou15']
    stats = {}
    
    with get_session() as s:
        for market in markets:
            # Get predictions for this market (need fixture date for time ordering)
            pred_rows = s.execute(
                select(PredictionRecord, Fixture.date)
                .join(Fixture, PredictionRecord.fixture_id == Fixture.id)
                .where(PredictionRecord.market == market)
                .order_by(Fixture.date)
            ).all()
            
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
            
            stats[market] = {
                "market": market,
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
                
                bets_list = []
                for b in state.bets:
                    bets_list.append({
                        'home': b.get('home_team', '?'),
                        'away': b.get('away_team', '?'),
                        'date': str(b.get('fixture_date', ''))[:16] if b.get('fixture_date') else '-',
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

                existing = s.execute(
                    select(PlacedBet).where(
                        PlacedBet.round_id == r.id,
                        PlacedBet.fixture_id == fix.id,
                        PlacedBet.market == cand['market'],
                        PlacedBet.outcome == cand['outcome']
                    )
                ).scalar_one_or_none()
                if existing:
                    return jsonify({'ok': True, 'placed': 0, 'message': 'Bet already exists for this selection'})

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
                return jsonify({'ok': False, 'error': 'Use /api/settle_bets endpoint'}), 400

            elif action == 'new-round':
                from sqlalchemy import func

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
    from src.alerts.event_bus import event_bus as bootball_event_bus
    
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