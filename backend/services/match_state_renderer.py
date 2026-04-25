from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo


SE_TIMEZONE = ZoneInfo('Europe/Stockholm')


def render_match_state(
    raw_status: str,
    elapsed: Optional[int] = None,
    extra_time: Optional[int] = None
) -> str:
    """
    Render human-readable match state.
    
    Rules:
    - 1H: "1H 34'" or "1H 45+2'" (max 45)
    - HT: "HT" (when 1H ends at 45+)
    - 2H: "2H 67'" or "2H 90+3'" (max 90)
    - FT: "FT"
    - Other: return as-is
    """
    raw = raw_status.upper() if raw_status else ''
    
    if raw == 'HT':
        return 'HT'
    
    if raw == 'FT' or raw == 'FTM':
        return 'FT'
    
    if raw in ('1H', 'LIVE'):
        if elapsed is not None:
            if elapsed >= 45:
                return 'HT'
            base = raw.replace('LIVE', '1H')
            if extra_time and extra_time > 0:
                return f"{base} {elapsed}+{extra_time}'"
            return f"{base} {elapsed}'"
        base = raw.replace('LIVE', '1H')
        return base
    
    if raw == '2H':
        if elapsed is not None:
            if elapsed >= 90:
                return 'FT'
            if extra_time and extra_time > 0:
                return f"2H {elapsed}+{extra_time}'"
            return f"2H {elapsed}'"
        return '2H'
    
    if raw == 'ET':
        return 'ET'
    
    if raw == 'BT':
        return 'BT'
    
    if raw == 'AET':
        return 'AET'
    
    if raw == 'PEN':
        return 'PEN'
    
    return raw


def format_kickoff_time(dt: datetime, tz: str = 'Europe/Stockholm') -> str:
    """
    Format kickoff time in clean HH:MM format.
    
    Args:
        dt: datetime object
        tz: timezone string (default: Europe/Stockholm)
    """
    if not dt:
        return '-'
    
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SE_TIMEZONE)
        
        local_dt = dt.astimezone(ZoneInfo(tz))
        return local_dt.strftime('%H:%M')
    except Exception:
        if isinstance(dt, str):
            if len(dt) >= 16:
                return dt[11:16]
            return dt[:5]
        return str(dt)


def format_kickoff_date(dt: datetime, tz: str = 'Europe/Stockholm') -> str:
    """Format kickoff date as 'Mon 25'."""
    if not dt:
        return ''
    
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SE_TIMEZONE)
        
        local_dt = dt.astimezone(ZoneInfo(tz))
        return local_dt.strftime('%a %d')
    except Exception:
        return ''


def get_team_logos(home_team_id: int, away_team_id: int) -> Tuple[Optional[str], Optional[str]]:
    """Get team logos from database."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    with get_session() as s:
        home_logo = s.execute(text(
            "SELECT logo_url FROM teams WHERE id = :tid"
        ), {'tid': home_team_id}).scalar()
        
        away_logo = s.execute(text(
            "SELECT logo_url FROM teams WHERE id = :tid"
        ), {'tid': away_team_id}).scalar()
    
    return home_logo, away_logo


def get_league_info(fixture_id: int) -> dict:
    """Get league info with flag for a fixture."""
    from src.storage.db import get_session
    from sqlalchemy import text
    
    league_flags = {
        'England': '🇬🇧', 'Sweden': '🇸🇪', 'Germany': '🇩🇪', 'Spain': '🇪🇸',
        'Italy': '🇮🇹', 'France': '🇫🇷', 'Netherlands': '🇳🇱', 'Belgium': '🇧🇪',
        'Portugal': '🇵🇹', 'Turkey': '🇹🇷', 'Poland': '🇵🇱', 'Austria': '🇦🇹',
        'Switzerland': '🇨🇭', 'Denmark': '🇩🇰', 'Norway': '🇳🇴', 'Finland': '🇫🇮',
        'Czech Republic': '🇨🇿', 'Greece': '🇬🇷', 'Romania': '🇷🇴', 'Hungary': '🇭🇺'
    }
    
    with get_session() as s:
        row = s.execute(text("""
            SELECT l.name, l.country, l.flag
            FROM fixtures f
            JOIN leagues l ON f.league_id = l.id
            WHERE f.id = :fid
        """), {'fid': fixture_id}).fetchone()
        
        if row:
            flag = league_flags.get(row[1], '') or (row[2] if row[2] else '')
            return {
                'name': row[0],
                'country': row[1],
                'flag': flag
            }
    
    return {'name': '', 'country': '', 'flag': ''}


class MatchStateRenderer:
    """Main renderer for sidebar match states."""
    
    def __init__(self, timezone: str = 'Europe/Stockholm'):
        self.timezone = timezone
    
    def render(
        self,
        raw_status: str,
        elapsed: Optional[int],
        extra_time: Optional[int],
        kickoff_dt: datetime,
        home_team_id: int,
        away_team_id: int,
        fixture_id: int
    ) -> dict:
        """Render complete fixture display data."""
        league_info = get_league_info(fixture_id)
        home_logo, away_logo = get_team_logos(home_team_id, away_team_id)
        
        return {
            'match_state': render_match_state(raw_status, elapsed, extra_time),
            'kickoff_time': format_kickoff_time(kickoff_dt, self.timezone),
            'kickoff_date': format_kickoff_date(kickoff_dt, self.timezone),
            'league_name': league_info['name'],
            'league_flag': league_info['flag'],
            'home_logo': home_logo,
            'away_logo': away_logo
        }