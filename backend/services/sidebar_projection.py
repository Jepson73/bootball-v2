from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from src.storage.db import get_session
from sqlalchemy import text


SE_TIMEZONE = ZoneInfo('Europe/Stockholm')

FT_WINDOW_MINUTES = 30
LOOKAHEAD_HOURS = 24


@dataclass
class ProjectedFixture:
    """Final sidebar DTO after all transformations."""
    fixture_id: int
    home_team: str
    away_team: str
    home_team_id: int
    away_team_id: int
    home_logo: Optional[str]
    away_logo: Optional[str]
    kickoff_time: str
    kickoff_date: str
    status: str
    match_state: str
    goals_home: Optional[int]
    goals_away: Optional[int]
    league_name: str
    league_flag: str
    league_country: str
    best_market: Optional[str]
    best_odds: Optional[float]
    best_ev: Optional[float]
    section: str  # 'live', 'upcoming', 'finished'


LEAGUE_FLAGS = {
    'England': '🇬🇧', 'Sweden': '🇸🇪', 'Germany': '🇩🇪', 'Spain': '🇪🇸',
    'Italy': '🇮🇹', 'France': '🇫🇷', 'Netherlands': '🇳🇱', 'Belgium': '🇧🇪',
    'Portugal': '🇵🇹', 'Turkey': '🇹🇷', 'Poland': '🇵🇱', 'Austria': '🇦🇹',
    'Switzerland': '🇨🇭', 'Denmark': '🇩🇰', 'Norway': '🇳🇴', 'Finland': '🇫🇮',
    'Czech Republic': '🇨🇿', 'Greece': '🇬🇷', 'Romania': '🇷🇴', 'Hungary': '🇭🇺'
}

MARKET_RANKING = {
    'h2h': 1,
    'ou25': 2,
    'ou15': 3,
    'btts': 4,
}


def resolve_lifecycle_state(status: str, kickoff: datetime, now: datetime) -> Tuple[str, str]:
    """
    Resolve fixture lifecycle state and section.
    
    Returns: (match_state, section)
    - match_state: formatted state like "1H 46'" or "FT"
    - section: 'live', 'upcoming', or 'finished'
    """
    if status in ('1H', '2H', 'HT', 'LIVE'):
        return format_live_state(status), 'live'
    
    if status == 'FT':
        if now - kickoff <= timedelta(minutes=FT_WINDOW_MINUTES):
            return 'FT', 'finished'
        else:
            return 'FT', 'excluded'
    
    if status == 'NS':
        if kickoff > now and kickoff <= now + timedelta(hours=LOOKAHEAD_HOURS):
            return f"KO {format_time(kickoff)}", 'upcoming'
        else:
            return 'NS', 'excluded'
    
    return status, 'excluded'


def format_live_state(status: str, elapsed: Optional[int] = None) -> str:
    """Format live match state."""
    if status == 'HT':
        return 'HT'
    if status == 'FT':
        return 'FT'
    if status in ('1H', '2H', 'LIVE', 'ET'):
        if elapsed is not None:
            return f"{status} {elapsed}'"
        return f"{status} live"
    return status


def format_time(dt: datetime) -> str:
    """Format datetime to HH:MM in SE timezone."""
    if not dt:
        return '-'
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SE_TIMEZONE)
        local = dt.astimezone(SE_TIMEZONE)
        return local.strftime('%H:%M')
    except Exception:
        if isinstance(dt, str):
            if len(dt) >= 16:
                return dt[11:16]
            return dt[:5]
        return str(dt)


def format_date(dt: datetime) -> str:
    """Format datetime to 'Mon dd' in SE timezone."""
    if not dt:
        return ''
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SE_TIMEZONE)
        local = dt.astimezone(SE_TIMEZONE)
        return local.strftime('%a %d')
    except Exception:
        return ''


def get_flag_from_country(country: str) -> str:
    """Get flag emoji from country name."""
    if not country:
        return ''
    return LEAGUE_FLAGS.get(country, '')


def select_best_market(markets: List[Dict]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Select best market: highest rank, then highest odds."""
    if not markets:
        return None, None, None
    
    candidates = []
    for m in markets:
        bt = m.get('bet_type', '')
        rank = MARKET_RANKING.get(bt, 99)
        
        if bt == 'h2h' and m.get('odd_home'):
            candidates.append(('h2h', m.get('odd_home', 0), rank))
        elif bt in ('over_under', 'ou25'):
            if m.get('odd_over'):
                candidates.append(('ou25', m.get('odd_over', 0), rank))
            if m.get('odd_under'):
                candidates.append(('ou25', m.get('odd_under', 0), rank))
        elif bt == 'btts':
            if m.get('odd_btts_yes'):
                candidates.append(('btts', m.get('odd_btts_yes', 0), rank))
    
    if not candidates:
        return None, None, None
    
    candidates.sort(key=lambda x: (x[2], -x[1]))
    return candidates[0][0], candidates[0][1], None


def project_sidebar_fixtures() -> Dict[str, List[ProjectedFixture]]:
    """
    Main projection service - transforms raw fixtures into sidebar-ready DTOs.
    
    Strictly enforces:
    - Lifecycle states (LIVE / UPCOMING / FINISHED)
    - Time windows (24h upcoming, 30min FT window)
    - Deduplication (one row per fixture)
    - Best market selection
    """
    now = datetime.utcnow()
    cutoff_upcoming = now + timedelta(hours=LOOKAHEAD_HOURS)
    cutoff_finished = now - timedelta(minutes=FT_WINDOW_MINUTES)
    
    with get_session() as s:
        fixtures = s.execute(text("""
            SELECT 
                f.id,
                home.name as home_team,
                home.id as home_team_id,
                home.logo_url as home_logo,
                away.name as away_team,
                away.id as away_team_id,
                away.logo_url as away_logo,
                f.status,
                f.date as kickoff,
                f.goals_home,
                f.goals_away,
                f.elapsed,
                l.name as league_name,
                l.country as league_country
            FROM fixtures f
            JOIN teams home ON f.home_team_id = home.id
            JOIN teams away ON f.away_team_id = away.id
            LEFT JOIN leagues l ON f.league_id = l.id
            WHERE 
                f.status IN ('1H', '2H', 'HT', 'LIVE', 'FT', 'NS')
            ORDER BY f.date ASC
        """)).fetchall()
        
        fixture_ids = [r[0] for r in fixtures]
        
        odds_map: Dict[int, List[Dict]] = {fid: [] for fid in fixture_ids}
        if fixture_ids:
            odds = s.execute(text("""
                SELECT fixture_id, bet_type, odd_home, odd_draw, odd_away, 
                       odd_over, odd_under, odd_btts_yes, odd_btts_no
                FROM fixture_odds
                WHERE fixture_id IN :fids
            """), {'fids': tuple(fixture_ids)}).fetchall()
            
            for o in odds:
                fid = o[0]
                if fid in odds_map:
                    odds_map[fid].append({
                        'bet_type': o[1],
                        'odd_home': o[2],
                        'odd_draw': o[3],
                        'odd_away': o[4],
                        'odd_over': o[5],
                        'odd_under': o[6],
                        'odd_btts_yes': o[7],
                        'odd_btts_no': o[8]
                    })
    
    live_results = []
    upcoming_results = []
    finished_results = []
    
    for row in fixtures:
        fixture_id = row[0]
        home_team = row[1]
        home_team_id = row[2]
        home_logo = row[3]
        away_team = row[4]
        away_team_id = row[5]
        away_logo = row[6]
        status = row[7]
        kickoff = row[8]
        goals_home = row[9]
        goals_away = row[10]
        elapsed = row[11]
        league_name = row[12] or ''
        league_country = row[13] or ''
        
        kickoff_dt = kickoff
        match_state, section = resolve_lifecycle_state(status, kickoff_dt, now)
        
        if section == 'excluded':
            continue
        
        best_market, best_odds, best_ev = select_best_market(odds_map.get(fixture_id, []))
        
        kickoff_time = format_time(kickoff_dt)
        kickoff_date = format_date(kickoff_dt)
        league_flag = get_flag_from_country(league_country)
        
        pf = ProjectedFixture(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_logo=home_logo,
            away_logo=away_logo,
            kickoff_time=kickoff_time,
            kickoff_date=kickoff_date,
            status=status,
            match_state=match_state,
            goals_home=goals_home,
            goals_away=goals_away,
            league_name=league_name,
            league_flag=league_flag,
            league_country=league_country,
            best_market=best_market,
            best_odds=best_odds,
            best_ev=best_ev,
            section=section
        )
        
        if section == 'live':
            live_results.append(pf)
        elif section == 'upcoming':
            upcoming_results.append(pf)
        elif section == 'finished':
            finished_results.append(pf)
    
    return {
        'live': live_results,
        'upcoming': upcoming_results,
        'finished': finished_results
    }


def get_sidebar_summary() -> Dict[str, int]:
    """Get sidebar counts."""
    data = project_sidebar_fixtures()
    return {
        'live': len(data['live']),
        'upcoming': len(data['upcoming']),
        'finished': len(data['finished']),
        'total': len(data['live']) + len(data['upcoming']) + len(data['finished'])
    }