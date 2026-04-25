import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


MATCH_STATES = {
    'UPCOMING': ['NS', 'NJS', 'TBD'],
    'LIVE': ['1H', '2H', 'HT', 'ET', 'LIVE', 'BT'],
    'HALFTIME': ['HT'],
    'FINISHED': ['FT', 'FTm', 'AET', 'PEN', 'AWOD'],
    'ARCHIVED': ['ARCHIVED', 'CANC', 'POSP', 'ABAN', 'AWOA']
}


@dataclass
class MatchState:
    """Normalized match state."""
    fixture_id: int
    raw_status: str
    state: str
    display_status: str
    elapsed: Optional[int]
    last_update: datetime
    is_stale: bool


class MatchStateNormalizer:
    """
    Normalizes raw API match states into internal states.
    
    Responsibilities:
    - Convert raw API status to internal state
    - Detect stale "90'" matches and convert to FT
    - Apply timeout-based FT detection
    - Mark matches as ARCHIVED after 30 minutes post-FT
    """
    
    def __init__(
        self,
        stale_threshold_minutes: int = 10,
        archive_after_minutes: int = 30
    ):
        self.stale_threshold_minutes = stale_threshold_minutes
        self.archive_after_minutes = archive_after_minutes
    
    def normalize_match(self, fixture: Any, api_data: Dict = None) -> MatchState:
        """Normalize a single match."""
        now = datetime.utcnow()
        
        raw_status = fixture.status
        elapsed = getattr(fixture, 'elapsed', None)
        
        state = self._get_state(raw_status)
        
        display_status = self._get_display_status(raw_status, elapsed)
        
        last_update = getattr(fixture, 'updated_at', None) or now
        
        is_stale = self._is_stale(state, elapsed, last_update, api_data)
        
        return MatchState(
            fixture_id=fixture.id,
            raw_status=raw_status,
            state=state,
            display_status=display_status,
            elapsed=elapsed,
            last_update=last_update,
            is_stale=is_stale
        )
    
    def _get_state(self, raw_status: str) -> str:
        """Map raw status to internal state."""
        for state, statuses in MATCH_STATES.items():
            if raw_status in statuses:
                return state
        return 'UPCOMING'
    
    def _get_display_status(self, raw_status: str, elapsed: Optional[int]) -> str:
        """Get human-readable display status."""
        if elapsed is not None and raw_status in ['1H', '2H']:
            if raw_status == 'HT':
                return 'HT'
            return f"{elapsed}'"
        
        status_map = {
            'FT': 'FT',
            'FTm': 'FT',
            'AET': 'AET',
            'PEN': 'PEN',
            'HT': 'HT',
            'NS': '',
            '1H': '1H',
            '2H': '2H',
            'ET': 'ET',
            'BT': 'BT'
        }
        return status_map.get(raw_status, raw_status)
    
    def _is_stale(
        self,
        state: str,
        elapsed: Optional[int],
        last_update: datetime,
        api_data: Dict = None
    ) -> bool:
        """Check if a match is stuck in stale state."""
        now = datetime.utcnow()
        time_since_update = (now - last_update).total_seconds() / 60
        
        if state == 'LIVE':
            if elapsed is not None and elapsed >= 90:
                if time_since_update > self.stale_threshold_minutes:
                    return True
            
            if time_since_update > self.stale_threshold_minutes * 2:
                return True
        
        return False
    
    def normalize_matches(self, fixtures: List[Any], api_data: Dict = None) -> List[MatchState]:
        """Normalize a list of matches."""
        return [self.normalize_match(f, api_data) for f in fixtures]
    
    def should_transition_to_finished(self, match_state: MatchState) -> bool:
        """Determine if a LIVE match should transition to FINISHED."""
        if match_state.state != 'LIVE':
            return False
        
        if match_state.is_stale:
            return True
        
        return False
    
    def should_archive(self, match_state: MatchState) -> bool:
        """Determine if a FINISHED match should be ARCHIVED."""
        if match_state.state != 'FINISHED':
            return False
        
        now = datetime.utcnow()
        time_since_finished = (now - match_state.last_update).total_seconds() / 60
        
        return time_since_finished > self.archive_after_minutes


def normalize_match_states(fixtures: List[Any], api_data: Dict = None) -> Dict[str, List[MatchState]]:
    """
    Main entry point: normalize matches and categorize by state.
    
    Returns dict with keys: live, upcoming, finished, archived
    """
    normalizer = MatchStateNormalizer()
    
    normalized = normalizer.normalize_matches(fixtures, api_data)
    
    result = {
        'live': [],
        'upcoming': [],
        'finished': [],
        'archived': []
    }
    
    for match in normalized:
        if match.state == 'LIVE':
            result['live'].append(match)
        elif match.state == 'UPCOMING':
            result['upcoming'].append(match)
        elif match.state == 'FINISHED':
            result['finished'].append(match)
        else:
            result['archived'].append(match)
    
    return result


def get_matches_needing_state_update(session) -> List[int]:
    """Get fixture IDs that need state normalization."""
    from sqlalchemy import text
    
    result = session.execute(text("""
        SELECT id FROM fixtures 
        WHERE status IN ('1H', '2H', 'HT', 'ET', 'BT', 'LIVE')
        AND (
            updated_at < datetime('now', '-10 minutes')
            OR elapsed >= 90
        )
    """)).fetchall()
    
    return [r[0] for r in result]


def cleanup_finished_matches() -> Dict[str, Any]:
    """
    Cleanup job: transition stale LIVE matches to FINISHED, 
    archive old FINISHED matches.
    """
    from src.storage.db import get_session
    from sqlalchemy import text
    from datetime import datetime, timedelta
    
    logger.info("Starting match state cleanup...")
    
    with get_session() as session:
        stale_matches = session.execute(text("""
            SELECT id, status, elapsed, updated_at 
            FROM fixtures 
            WHERE status IN ('1H', '2H', 'HT', 'ET', 'BT', 'LIVE')
            AND (
                updated_at < datetime('now', '-10 minutes')
                OR elapsed >= 90
            )
        """)).fetchall()
        
        transitioned = 0
        for row in stale_matches:
            session.execute(text("""
                UPDATE fixtures SET status = 'FTm' WHERE id = :id
            """), {"id": row[0]})
            transitioned += 1
        
        archived = 0
        old_finished = session.execute(text("""
            SELECT id FROM fixtures 
            WHERE status = 'FTm'
            AND updated_at < datetime('now', '-30 minutes')
        """)).fetchall()
        
        for row in old_finished:
            session.execute(text("""
                UPDATE fixtures SET status = 'ARCHIVED' WHERE id = :id
            """), {"id": row[0]})
            archived += 1
        
        session.commit()
        
        logger.info(f"Match cleanup complete: {transitioned} transitioned to FT, {archived} archived")
        
        return {
            "transitioned_to_ft": transitioned,
            "archived": archived
        }