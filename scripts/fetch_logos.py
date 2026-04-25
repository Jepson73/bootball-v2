#!/usr/bin/env python3
"""
Fetch team country and flags from API-Football and populate DB.

Usage:
    python scripts/fetch_logos.py
"""
import sys
sys.path.insert(0, '/opt/projects/bootball')

from src.ingestion.client import APIFootballClient
from src.storage.db import get_session
from src.storage.models import Team, League
from sqlalchemy import select

FLAG_BASE = "https://media.api-sports.io/flags"

COUNTRY_CODE_CACHE = {}

def get_country_code(client: APIFootballClient, country_name: str) -> str | None:
    """Get country code from API-Football."""
    if country_name in COUNTRY_CODE_CACHE:
        return COUNTRY_CODE_CACHE[country_name]
    
    try:
        resp = client.get('countries', {'name': country_name})
        if resp and len(resp) > 0:
            code = resp[0].get('code')
            COUNTRY_CODE_CACHE[country_name] = code
            return code
    except:
        pass
    COUNTRY_CODE_CACHE[country_name] = None
    return None

def main():
    client = APIFootballClient()

    with get_session() as s:
        # Fetch league flags (from country.flag)
        leagues = s.execute(select(League)).scalars().all()
        print(f"Fetching flags for {len(leagues)} leagues...")

        updated = 0
        for league in leagues:
            try:
                resp = client.get('leagues', {'id': league.id})
                if resp and len(resp) > 0:
                    country_data = resp[0].get('country', {})
                    flag = country_data.get('flag')
                    if flag and not league.flag:
                        league.flag = flag
                        updated += 1
            except Exception as e:
                print(f"Error fetching league {league.id}: {e}")
        s.commit()
        print(f"Updated {updated} league flags")

        # Fetch ALL teams
        teams = s.execute(select(Team)).scalars().all()
        print(f"Processing {len(teams)} teams...")

        updated = 0
        country_updated = 0
        for team in teams:
            try:
                resp = client.get('teams', {'id': team.id})
                if resp and len(resp) > 0:
                    team_data = resp[0].get('team', {})
                    country = team_data.get('country')
                    team_logo = team_data.get('logo')
                    
                    # Update country if missing
                    if country and not team.country:
                        team.country = country
                        country_updated += 1
                    
                    # Update logo if missing
                    if team_logo and not team.logo_url:
                        team.logo_url = team_logo
                    
                    # Set flag to country flag
                    if country:
                        code = get_country_code(client, country)
                        if code:
                            team.flag = f"{FLAG_BASE}/{code.lower()}.svg"
                            updated += 1
            except Exception as e:
                print(f"Error fetching team {team.id}: {e}")
        
        s.commit()
        print(f"Updated {updated} team country flags, {country_updated} team countries")

if __name__ == "__main__":
    main()