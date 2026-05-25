#!/usr/bin/env python3
# DEAD CODE — not called from live pipeline as of 2026-05-25
# Kept for reference: player stats and injury ingestion; intended to be automated (see backfill_cron for pattern)
"""
scripts/fetch_player_data.py

Fetch player statistics and injuries for target leagues.
Run daily before predictions.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Player, Injury


def fetch_injuries(league_id: int, date: str = None):
    """Fetch current injuries for league."""
    client = APIFootballClient()
    
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    
    injuries = client.get_injuries(league_id=league_id, date=date)
    
    with get_session() as session:
        # Clear old for this league/date
        session.query(Injury).filter(Injury.fixture_id != None).delete()
        
        for inj in injuries or []:
            player = inj.get("player", {})
            team = inj.get("team", {})
            fixture = inj.get("fixture", {})
            
            player_id = player.get("id") if player else None
            team_id = team.get("id") if team else None
            fixture_id = fixture.get("id") if fixture else None
            
            if not player_id:
                continue
            
            inj_obj = Injury(
                player_id=player_id,
                player_name=player.get("name", "Unknown"),
                team_id=team_id,
                fixture_id=fixture_id,
                type=player.get("type", "Unknown"),
                status=team.get("reason", "injured") if team else "injured",
                start_date=datetime.now(),
            )
            session.add(inj_obj)
        
        session.commit()
    
    print(f"Stored {len(injuries or [])} injuries for league {league_id}")


# Position impact weights (from research)
POSITION_IMPACT = {
    "Goalkeeper": -0.15,  # Key save presence
    "Defender": -0.10,   # Set piece, organize
    "Midfielder": -0.20, # Creators, goals
    "Attacker": -0.40,   # Main goal threat
    "Forward": -0.40,
}
DEFAULT_IMPACT = -0.10


def get_injury_impact(team_id: int) -> float:
    """Calculate goal impact from injuries."""
    with get_session() as session:
        injuries = session.query(Injury).filter(
            Injury.team_id == team_id,
            Injury.status.in_(["injured", "doubt"])
        ).all()
        
        impact = 0.0
        for inj in injuries:
            # Default modest impact
            impact -= DEFAULT_IMPACT
        
        return max(impact, -0.5)  # Cap at -0.5


def fetch_player_positions(team_id: int, season: int = 2025):
    """Fetch player positions for a team."""
    client = APIFootballClient()
    players = client.get_players(team_id=team_id, season=season)
    
    player_positions = {}
    for p in players or []:
        player_id = p.get("player", {}).get("id")
        stats = p.get("statistics", [{}])[0] if p.get("statistics") else {}
        games = stats.get("games", {})
        
        position = games.get("position", "Unknown")
        player_positions[player_id] = position
    
    return player_positions


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", type=int, required=True, help="League ID")
    parser.add_argument("--date", type=str, help="Date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    fetch_injuries(args.league, args.date)