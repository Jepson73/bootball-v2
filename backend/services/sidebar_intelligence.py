from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from src.storage.db import get_session
from sqlalchemy import text

from backend.services.match_state_renderer import (
    render_match_state,
    format_kickoff_time,
    format_kickoff_date,
    get_team_logos,
    get_league_info
)


@dataclass
class SidebarItem:
    """Unified sidebar fixture DTO."""
    fixture_id: int
    home_team: str
    away_team: str
    status: str
    kickoff_time: str
    goals_home: Optional[int]
    goals_away: Optional[int]
    best_market: Optional[str]
    best_odds: Optional[float]
    best_ev: Optional[float]
    is_live: bool = False
    is_finished: bool = False
    is_upcoming: bool = False
    match_state: str = ''
    home_logo: Optional[str] = None
    away_logo: Optional[str] = None
    league_name: str = ''
    league_flag: str = ''
    league_country: str = ''
    kickoff_date: str = ''
    elapsed: Optional[int] = None


MARKET_RANKING = {
    'h2h': 1,
    'ou25': 2,
    'ou15': 3,
    'btts': 4,
    'over_under': 5,
}


def build_sidebar_fixtures() -> Dict[str, List[SidebarItem]]:
    """
    Build unified sidebar fixtures with strict classification:
    
    - LIVE: status IN ('1H', '2H', 'HT', 'LIVE') OR (elapsed IS NOT NULL AND status != 'FT')
    - UPCOMING: status = 'NS' AND kickoff within next 24h
    - FINISHED: NOT USED (removed - no reliable finished timestamp)
    
    Rules:
    - Each fixture appears in exactly ONE category
    - No FT in Live Now
    - No mixing of statuses
    """
    now = datetime.utcnow()
    cutoff_24h = now + timedelta(hours=24)
    
    with get_session() as s:
        raw_fixtures = s.execute(text("""
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
                l.flag as league_flag,
                COALESCE(l.tier, 99) as league_tier
            FROM fixtures f
            JOIN teams home ON f.home_team_id = home.id
            JOIN teams away ON f.away_team_id = away.id
            LEFT JOIN leagues l ON f.league_id = l.id
            WHERE 
                (f.status IN ('1H', '2H', 'HT', 'LIVE'))
                OR (f.status = 'NS' AND f.date >= :now AND f.date < :cutoff_24h)
            ORDER BY league_tier ASC, l.name ASC, f.date ASC
        """), {
            'now': now.isoformat(),
            'cutoff_24h': cutoff_24h.isoformat()
        }).fetchall()
    
    fixture_map: Dict[int, Dict] = {}
    
    for row in raw_fixtures:
        fix_id = row[0]
        
        if fix_id not in fixture_map:
            fixture_map[fix_id] = {
                'fixture_id': fix_id,
                'home_team': row[1],
                'away_team': row[2],
                'home_team_id': row[3],
                'away_team_id': row[4],
                'status': row[5],
                'kickoff_time': row[6],
                'goals_home': row[7],
                'goals_away': row[8],
                'elapsed': row[9],
                'league_name': row[10] or '',
                'league_country': row[11] or '',
                'league_flag': row[12] or '',
                'league_tier': row[13] if len(row) > 13 else 99,
                'markets': []
            }
    
    odds_rows = s.execute(text("""
        SELECT 
            fixture_id,
            bet_type,
            odd_home,
            odd_draw,
            odd_away,
            odd_over,
            odd_under,
            odd_btts_yes,
            odd_btts_no
        FROM fixture_odds
    """)).fetchall()
    
    for row in odds_rows:
        fix_id = row[0]
        if fix_id in fixture_map:
            bet_type = row[1]
            if bet_type:
                odds_data = {
                    'bet_type': bet_type,
                    'odd_home': row[2],
                    'odd_draw': row[3],
                    'odd_away': row[4],
                    'odd_over': row[5],
                    'odd_under': row[6],
                    'odd_btts_yes': row[7],
                    'odd_btts_no': row[8]
                }
                fixture_map[fix_id]['markets'].append(odds_data)
    
    live_items = []
    upcoming_items = []
    finished_items = []
    
    league_flags = {
        'England': '🇬🇧', 'Sweden': '🇸🇪', 'Germany': '🇩🇪', 'Spain': '🇪🇸',
        'Italy': '🇮🇹', 'France': '🇫🇷', 'Netherlands': '🇳🇱', 'Belgium': '🇧🇪',
        'Portugal': '🇵🇹', 'Turkey': '🇹🇷', 'Poland': '🇵🇱', 'Austria': '🇦🇹',
        'Switzerland': '🇨🇭', 'Denmark': '🇩🇰', 'Norway': '🇳🇴', 'Finland': '🇫🇮',
        'Czech Republic': '🇨🇿', 'Greece': '🇬🇷', 'Romania': '🇷🇴', 'Hungary': '🇭🇺'
    }
    
    for fix_id, data in fixture_map.items():
        kickoff = data['kickoff_time']
        if kickoff:
            if hasattr(kickoff, 'strftime'):
                kickoff_str = kickoff.strftime('%H:%M')
            elif isinstance(kickoff, str) and len(kickoff) >= 16:
                kickoff_str = kickoff[11:16]
                if kickoff_str.startswith('20'):
                    kickoff_str = kickoff[-5:] if len(kickoff) >= 5 else kickoff_str
            else:
                kickoff_str = str(kickoff)
        else:
            kickoff_str = '-'
        
        status = data['status']
        elapsed = data.get('elapsed')
        
        is_live = status in ('1H', '2H', 'HT', 'LIVE') or (elapsed is not None and status != 'FT')
        is_finished = False
        is_upcoming = status == 'NS'
        
        match_state = render_match_state(status, elapsed)
        
        kickoff_dt = data['kickoff_time']
        kickoff_time_formatted = format_kickoff_time(kickoff_dt) if kickoff_dt else kickoff_str
        kickoff_date_formatted = format_kickoff_date(kickoff_dt) if kickoff_dt else ''
        
        home_logo, away_logo = get_team_logos(data['home_team_id'], data['away_team_id'])
        
        league_flag = data.get('league_flag', '')
        if not league_flag or league_flag.startswith('http'):
            league_country = data.get('league_country', '')
            league_flag = league_flags.get(league_country, '')
        
        best_market, best_odds, best_ev = select_best_market(data['markets'])
        
        item = SidebarItem(
            fixture_id=fix_id,
            home_team=data['home_team'],
            away_team=data['away_team'],
            status=status,
            kickoff_time=kickoff_time_formatted,
            kickoff_date=kickoff_date_formatted,
            goals_home=data['goals_home'],
            goals_away=data['goals_away'],
            best_market=best_market,
            best_odds=best_odds,
            best_ev=best_ev,
            is_live=is_live,
            is_finished=is_finished,
            is_upcoming=is_upcoming,
            match_state=match_state,
            home_logo=home_logo,
            away_logo=away_logo,
            league_name=data.get('league_name', ''),
            league_country=data.get('league_country', ''),
            league_flag=league_flag,
            elapsed=elapsed
        )
        
        if is_live:
            live_items.append(item)
        elif is_finished:
            finished_items.append(item)
        elif is_upcoming:
            upcoming_items.append(item)
    
    return {
        'live': live_items,
        'upcoming': upcoming_items,
        'finished': finished_items
    }


def select_best_market(markets: List[Dict]) -> tuple:
    """Select best market based on EV and odds ranking."""
    if not markets:
        return None, None, None
    
    candidates = []
    
    for m in markets:
        bet_type = m.get('bet_type', '')
        
        if bet_type == 'h2h' and m.get('odd_home'):
            candidates.append(('h2h', 'home', m.get('odd_home', 0)))
        elif bet_type == 'over_under' or bet_type == 'ou25':
            if m.get('odd_over'):
                candidates.append(('ou25', 'over', m.get('odd_over', 0)))
            if m.get('odd_under'):
                candidates.append(('ou25', 'under', m.get('odd_under', 0)))
        elif bet_type == 'btts':
            if m.get('odd_btts_yes'):
                candidates.append(('btts', 'yes', m.get('odd_btts_yes', 0)))
    
    if not candidates:
        return None, None, None
    
    candidates.sort(key=lambda x: (-MARKET_RANKING.get(x[0], 99), -x[2]))
    
    best = candidates[0]
    return best[0], best[2], None


def get_sidebar_summary() -> Dict[str, Any]:
    """Get sidebar summary counts."""
    data = build_sidebar_fixtures()
    return {
        'live_count': len(data['live']),
        'upcoming_count': len(data['upcoming']),
        'total': len(data['live']) + len(data['upcoming'])
    }