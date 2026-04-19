"""
config/backfill.py

Backfill configuration for automated and manual use.

- Seasons are auto-computed: current year + 5 seasons back
- Leagues are manual config (what you want to backfill/keep)
- Per-league season overrides if needed (optional)
"""
from datetime import datetime


def get_backfill_seasons() -> list[int]:
    """Get seasons for backfill: current year + 5 seasons back."""
    current_year = datetime.now().year
    return list(range(current_year - 5, current_year + 1))


# Leagues to backfill - ALL leagues from config/leagues.py for complete training data
# This includes Tier 1 (elite), Tier 2 (secondary), and Tier 3 (cups)
# ALL leagues should be included for backfill and training per user request
from config.leagues import ALL_LEAGUE_IDS

BACKFILL_LEAGUES = ALL_LEAGUE_IDS

# Seasons - auto-computed, DO NOT EDIT
BACKFILL_SEASONS = get_backfill_seasons()

# Per-league season overrides (optional)
# Example: only backfill recent seasons for certain leagues
# Uncomment and edit as needed
# BACKFILL_LEAGUE_SEASONS = {
#     39: [2023, 2024, 2025],  # Premier League: only last 3 seasons
#     140: [2022, 2023, 2024, 2025],  # La Liga: last 4
# }

# Default: use BACKFILL_SEASONS for all leagues
BACKFILL_LEAGUE_SEASONS = {}


def get_league_seasons(league_id: int) -> list[int]:
    """Get seasons for a specific league."""
    return BACKFILL_LEAGUE_SEASONS.get(league_id, BACKFILL_SEASONS)


def get_backfill_leagues() -> list[int]:
    """Get leagues to backfill."""
    return list(BACKFILL_LEAGUES)


def add_league(league_id: int) -> None:
    """Add a league to backfill config."""
    if league_id not in BACKFILL_LEAGUES:
        BACKFILL_LEAGUES.append(league_id)
        BACKFILL_LEAGUES.sort()


def remove_league(league_id: int) -> None:
    """Remove a league from backfill config."""
    if league_id in BACKFILL_LEAGUES:
        BACKFILL_LEAGUES.remove(league_id)


def add_season(season: int) -> None:
    """Add a season to backfill config."""
    if season not in BACKFILL_SEASONS:
        BACKFILL_SEASONS.append(season)
        BACKFILL_SEASONS.sort()


def remove_season(season: int) -> None:
    """Remove a season from backfill config."""
    if season in BACKFILL_SEASONS:
        BACKFILL_SEASONS.remove(season)


if __name__ == '__main__':
    print("Backfill Configuration")
    print(f"Leagues: {get_backfill_leagues()}")
    print(f"Seasons: {get_backfill_seasons()}")
    print(f"Total: {len(get_backfill_leagues())} leagues × {len(get_backfill_seasons())} seasons")
