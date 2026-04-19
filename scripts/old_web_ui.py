#!/usr/bin/env python3
"""
Bootball Web UI - betting and predictions interface
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
from src.storage.db import get_session, init_db
from src.storage.models import (
    Fixture, FixtureOdds, Standing, PredictionRecord, PlacedBet, 
    BankrollRound, Team, League, SettledBet
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

TEAM_NAMES = {}
LEAGUE_NAMES = {}

def load_caches():
    global TEAM_NAMES, LEAGUE_NAMES
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

def get_password():
    return os.environ.get('BOOTBALL_PASSWORD') or getattr(settings, 'bootball_password', 'changeme')

def require_auth(f):
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

HTML_HEAD = '''<!DOCTYPE html>
<html>
<head><title>Bootball</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.5}
.nav{display:flex;gap:8px;padding:12px;background:#161b22;border-bottom:1px solid #30363d}
.nav a{color:#58a6ff;text-decoration:none;padding:8px 16px;border-radius:6px}
.nav a:hover{background:#21262d}
.container{max-width:1200px;margin:0 auto;padding:20px}
.metric{display:inline-block;background:#161b22;padding:16px 24px;border-radius:8px;margin:4px}
.metric-label{font-size:12px;color:#8b949e}
.metric-value{font-size:24px;font-weight:600}
table{border-collapse:collapse;width:100%;margin:16px 0}
th,td{padding:12px;text-align:left;border-bottom:1px solid #30363d}
th{background:#161b22;color:#8b949e;font-weight:600}
.win{color:#3fb950}
.loss{color:#f85149}
.pending{color:#8b949e}
</style></head>
<body>
<div class="nav">
<a href="/">Home</a>
<a href="/predictions">Predictions</a>
<a href="/betting">Betting</a>
<a href="/admin">Admin</a>
</div>
<div class="container">'''

HTML_FOOT = '</div></body></html>'

@app.route('/')
def home():
    return HTML_HEAD + '<h1>Bootball</h1><p><a href="/predictions">Predictions</a> | <a href="/betting">Betting</a> | <a href="/admin">Admin</a></p>' + HTML_FOOT

@app.route('/predictions')
@require_auth
def predictions_page():
    content = '''<h1>Predictions</h1>
<p>Loading predictions...</p>
<table><thead><tr><th>Match</th><th>League</th><th>Date</th><th>Market</th><th>Pick</th><th>Prob</th><th>Odds</th><th>EV</th></tr></thead>
<tbody id="predBody"></tbody></table>
<script>
fetch('/api/predictions',{credentials:'include'}).then(r=>r.json()).then(d=>{
var html='';
for(var i=0;i<Math.min(d.length,50);i++){
var p=d[i];
var bttsPick=p.btts_prob_yes>0.5?'Yes':'No';
html+='<tr><td>'+p.home_name+' vs '+p.away_name+'</td><td>'+(p.league_name||'')+'</td><td>'+(p.date||'').slice(0,16)+'</td><td>btts</td><td>'+bttsPick+'</td><td>'+(p.btts_prob_yes*100).toFixed(0)+'%</td><td>'+(p.btts_odds?.Yes||'-')+'</td><td>'+(p.btts_ev?.Y||0).toFixed(1)+'%</td></tr>';
}
document.getElementById('predBody').innerHTML=html;
});
</script>'''
    return HTML_HEAD + content + HTML_FOOT

@app.route('/betting')
@require_auth
def betting_page():
    with get_session() as s:
        r = s.execute(select(BankrollRound).where(BankrollRound.is_active == True).order_by(BankrollRound.round_number.desc()).limit(1)).scalar_one_or_none()
        if not r:
            r = BankrollRound(round_number=1, initial_bankroll=1000.0, is_active=True)
            s.add(r)
            s.commit()
        
        stats = {'balance': 1000.0, 'round_number': 1, 'pending': 0, 'settled': 0, 'wins': 0}
        if r:
            round_id = r.id
            initial = r.initial_bankroll
            pending = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == False)).scalars().all()
            settled = s.execute(select(PlacedBet).where(PlacedBet.round_id == round_id).where(PlacedBet.settled == True)).scalars().all()
            
            pending_stake = sum((b.stake or 0) for b in pending)
            settled_pnl = sum((b.pnl or 0) for b in settled)
            balance = initial + settled_pnl - pending_stake
            
            stats = {
                'balance': balance,
                'round_number': r.round_number,
                'pending': len(pending),
                'settled': len(settled),
                'wins': sum(1 for b in settled if b.won)
            }
    
    # Build content using string concatenation
    content = '<h1>Betting</h1>'
    content += '<div><span class="metric-label">Balance</span><br><span class="metric-value" id="balance">$'+str(int(stats['balance']))+'</span></div>'
    content += '<div><span class="metric-label">Round</span><br><span class="metric-value">#'+str(stats['round_number'])+'</span></div>'
    content += '<div><span class="metric-label">Pending</span><br><span class="metric-value">'+str(stats['pending'])+'</span></div>'
    content += '<div><span class="metric-label">Wins</span><br><span class="metric-value">'+str(stats['wins'])+'/'+str(stats['settled'])+'</span></div>'
    content += '<br>'
    content += '''<button onclick="placeBets()" style="background:#238636;color:#fff;padding:10px 20px;border:none;border-radius:6px;cursor:pointer">Place Bets</button>
<button onclick="settleBets()" style="background:#f85149;color:#fff;padding:10px 20px;border:none;border-radius:6px;cursor:pointer">Settle Bets</button>
<div id="msg" style="margin:12px 0;color:#58a6ff"></div>
<h2>Pending Bets</h2>
<table><thead><tr><th>Match</th><th>Market</th><th>Pick</th><th>Stake</th><th>Odds</th><th>EV</th><th>Result</th></tr></thead>
<tbody id="betsBody"></tbody></table>
<script>
function loadBets(){
fetch('/betting/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'status'})})
.then(function(r){return r.json();})
.then(function(d){if(d.ok){
document.getElementById('balance').innerText='$'+d.round.balance.toFixed(0);
renderBets(d.bets||[]);}});}
function renderBets(bets){
var html='';
for(var i=0;i<bets.length;i++){
var b=bets[i];
var resultClass=b.settled?(b.won?'win':'loss'):'pending';
var resultText=b.settled?(b.won?'WIN':'LOSS'):'PENDING';
html+='<tr><td>'+b.home+' vs '+b.away+'</td><td>'+b.market+'</td><td>'+b.outcome+'</td><td>$'+b.stake+'</td><td>'+b.odds+'</td><td>'+(b.ev*100).toFixed(1)+'%</td><td class="'+resultClass+'">'+resultText+'</td></tr>';
}
document.getElementById('betsBody').innerHTML=html;
}
function placeBets(){
document.getElementById('msg').innerText='Placing bets...';
fetch('/betting/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'place-bets'})})
.then(function(r){return r.json();})
.then(function(d){document.getElementById('msg').innerText=d.placed+' bets placed';loadBets();});
}
function settleBets(){
document.getElementById('msg').innerText='Settling...';
fetch('/betting/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'settle'})})
.then(function(r){return r.json();})
.then(function(d){document.getElementById('msg').innerText=d.settled+' bets settled';loadBets();});
}
loadBets();
</script>'''
    
    return HTML_HEAD + content + HTML_FOOT

@app.route('/betting/action', methods=['POST'])
@require_auth
def betting_action():
    data = request.get_json() or {}
    action = data.get('action', 'status')
    
    try:
        with get_session() as s:
            r = s.execute(select(BankrollRound).where(BankrollRound.is_active == True).order_by(BankrollRound.round_number.desc()).limit(1)).scalar_one_or_none()
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
                
                return jsonify({'ok': True, 'round': {
                    'balance': balance, 'round_number': r.round_number,
                    'pending': len(pending), 'settled': len(settled),
                    'wins': sum(1 for b in settled if b.won)
                }, 'bets': bets_list})
            
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
                        print("Bet error: %s", e)
                        continue
                
                s.commit()
                return jsonify({'ok': True, 'placed': placed})
            
            elif action == 'settle':
                settled = 0
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
                    
                    if bet.outcome == result:
                        bet.won = True
                        bet.pnl = (bet.odds - 1) * bet.stake
                    else:
                        bet.won = False
                        bet.pnl = -bet.stake
                    
                    bet.settled_at = datetime.utcnow()
                    settled += 1
                
                if settled > 0:
                    s.commit()
                
                return jsonify({'ok': True, 'settled': settled})
        
        return jsonify({'ok': False, 'error': 'Server error'}), 500
    except Exception as e:
        logger.error("Betting action error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/predictions')
@require_auth
def api_predictions():
    now = datetime.utcnow()
    end = now + timedelta(days=7)
    
    with get_session() as s:
        fixtures = s.execute(
            select(Fixture)
            .where(Fixture.date >= now)
            .where(Fixture.date <= end)
            .where(Fixture.status == 'NS')
            .order_by(Fixture.date)
        ).scalars().all()
        
        results = []
        for fix in fixtures[:100]:
            league_name = LEAGUE_NAMES.get(fix.league_id, '')
            home = TEAM_NAMES.get(fix.home_team_id, str(fix.home_team_id))
            away = TEAM_NAMES.get(fix.away_team_id, str(fix.away_team_id))
            
            preds = s.execute(
                select(PredictionRecord).where(PredictionRecord.fixture_id == fix.id)
            ).scalars().all()
            
            cached = {p.market: p for p in preds}
            
            btts_yes = cached.get('btts').our_prob if cached.get('btts') else 0.5
            ou_over = cached.get('ou25').our_prob if cached.get('ou25') else 0.5
            
            odds = s.execute(select(FixtureOdds).where(FixtureOdds.fixture_id == fix.id)).scalars().first()
            
            btts_odds = {'Yes': odds.btts_yes, 'No': odds.btts_no} if odds else {}
            ou_odds = {'Over': odds.over_25, 'Under': odds.under_25} if odds else {}
            
            results.append({
                'fixture_id': fix.id,
                'home_name': home, 'away_name': away,
                'league_name': league_name,
                'date': fix.date.isoformat() if fix.date else None,
                'btts_prob_yes': btts_yes,
                'btts_odds': btts_odds,
                'btts_ev': {'Y': compute_ev(btts_yes, btts_odds.get('Yes', 0)), 'N': compute_ev(1-btts_yes, btts_odds.get('No', 0))},
                'ou_prob_over': ou_over,
                'ou_odds': ou_odds,
            })
        
        return jsonify(results)

@app.route('/admin')
@require_auth
def admin_page():
    content = '<h1>Admin</h1><p>System admin</p>'
    return HTML_HEAD + content + HTML_FOOT

if __name__ == '__main__':
    init_db()
    load_caches()
    logger.info("Starting Bootball web UI...")
    app.run(host='0.0.0.0', port=5000, debug=False)