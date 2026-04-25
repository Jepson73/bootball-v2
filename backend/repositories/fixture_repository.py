from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from src.storage.db import get_session
from sqlalchemy import text


@dataclass
class SidebarFixture:
    """Normalized fixture DTO for sidebar display."""
    id: int
    home_team: str
    away_team: str
    kickoff_time: str
    status: str
    goals_home: Optional[int]
    goals_away: Optional[int]
    elapsed: Optional[int] = None
    league_name: Optional[str] = None


def get_sidebar_fixtures() -> Dict[str, List[SidebarFixture]]:
    """
    Get fixtures for sidebar (live and upcoming).
    
    Returns normalized DTOs with proper team name JOINs.
    """
    with get_session() as s:
        live_fixtures = s.execute(text("""
            SELECT 
                f.id,
                home.name as home_team,
                away.name as away_team,
                f.date as kickoff_time,
                f.status,
                f.goals_home,
                f.goals_away,
                f.elapsed,
                l.name as league_name
            FROM fixtures f
            JOIN teams home ON f.home_team_id = home.id
            JOIN teams away ON f.away_team_id = away.id
            LEFT JOIN leagues l ON f.league_id = l.id
            WHERE f.status IN ('1H', '2H', 'HT', 'LIVE', 'ET', 'BT')
            ORDER BY f.date ASC
            LIMIT 20
        """)).fetchall()
        
        now = datetime.utcnow()
        
        upcoming_fixtures = s.execute(text("""
            SELECT 
                f.id,
                home.name as home_team,
                away.name as away_team,
                f.date as kickoff_time,
                f.status,
                f.goals_home,
                f.goals_away,
                f.elapsed,
                l.name as league_name
            FROM fixtures f
            JOIN teams home ON f.home_team_id = home.id
            JOIN teams away ON f.away_team_id = away.id
            LEFT JOIN leagues l ON f.league_id = l.id
            WHERE f.status = 'NS'
            AND f.date >= :now
            AND f.date < datetime(:now, '+3 days')
            ORDER BY f.date ASC
            LIMIT 30
        """), {'now': now.isoformat()}).fetchall()
    
    live = []
    for row in live_fixtures:
        kickoff = row[3]
        if hasattr(kickoff, 'strftime'):
            kickoff_str = kickoff.strftime('%H:%M')
        else:
            kickoff_str = str(kickoff)[:5] if kickoff else '-'
        
        live.append(SidebarFixture(
            id=row[0],
            home_team=row[1],
            away_team=row[2],
            kickoff_time=kickoff_str,
            status=row[4],
            goals_home=row[5],
            goals_away=row[6],
            elapsed=row[7],
            league_name=row[8]
        ))
    
    upcoming = []
    for row in upcoming_fixtures:
        kickoff = row[3]
        if hasattr(kickoff, 'strftime'):
            kickoff_str = kickoff.strftime('%H:%M')
        else:
            kickoff_str = str(kickoff)[:5] if kickoff else '-'
        
        upcoming.append(SidebarFixture(
            id=row[0],
            home_team=row[1],
            away_team=row[2],
            kickoff_time=kickoff_str,
            status=row[4],
            goals_home=row[5],
            goals_away=row[6],
            elapsed=row[7],
            league_name=row[8]
        ))
    
    return {
        'live': live,
        'upcoming': upcoming
    }


def get_fixtures_for_date_range(start_date: datetime, end_date: datetime) -> List[SidebarFixture]:
    """Get fixtures within a date range."""
    with get_session() as s:
        fixtures = s.execute(text("""
            SELECT 
                f.id,
                home.name as home_team,
                away.name as away_team,
                f.date as kickoff_time,
                f.status,
                f.goals_home,
                f.goals_away,
                f.elapsed,
                l.name as league_name
            FROM fixtures f
            JOIN teams home ON f.home_team_id = home.id
            JOIN teams away ON f.away_team_id = away.id
            LEFT JOIN leagues l ON f.league_id = l.id
            WHERE f.date >= :start AND f.date < :end
            ORDER BY f.date ASC
            LIMIT 100
        """), {
            'start': start_date.isoformat(),
            'end': end_date.isoformat()
        }).fetchall()
    
    result = []
    for row in fixtures:
        kickoff = row[3]
        if hasattr(kickoff, 'strftime'):
            kickoff_str = kickoff.strftime('%H:%M')
        else:
            kickoff_str = str(kickoff)[:5] if kickoff else '-'
        
        result.append(SidebarFixture(
            id=row[0],
            home_team=row[1],
            away_team=row[2],
            kickoff_time=kickoff_str,
            status=row[4],
            goals_home=row[5],
            goals_away=row[6],
            elapsed=row[7],
            league_name=row[8]
        ))
    
    return result


def get_live_fixture_count() -> int:
    """Get count of currently live fixtures."""
    with get_session() as s:
        return s.execute(text("""
            SELECT COUNT(*) FROM fixtures 
            WHERE status IN ('1H', '2H', 'HT', 'LIVE', 'ET', 'BT')
        """)).scalar() or 0


def get_upcoming_fixture_count() -> int:
    """Get count of upcoming fixtures (next 7 days)."""
    with get_session() as s:
        now = datetime.utcnow()
        return s.execute(text("""
            SELECT COUNT(*) FROM fixtures 
            WHERE status = 'NS'
            AND date >= :now
            AND date < datetime(:now, '+7 days')
        """), {'now': now.isoformat()}).scalar() or 0