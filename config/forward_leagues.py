"""
config/forward_leagues.py

Leagues selected for forward odds collection (Phase 11 Task 1).

Selection criteria (Phase 10 Tasks 2–3):
  - Passes Pinnacle sharp gate (confirmed coverage on 2026-06-28)
  - No fdco historical odds backfill available
  - High avg goals and/or BTTS rate (Phase 10 Task 3 DB query)

These leagues are monitored for upcoming fixtures. Odds are captured
at multiple timestamps (open → close) to build a time-series in odds_snapshots.
"""

from dataclasses import dataclass, field

CURRENT_SEASON = 2026


@dataclass(frozen=True)
class ForwardLeague:
    league_id: int
    name: str
    country: str
    avg_goals: float    # trailing avg from Phase 10 Task 3
    btts_pct: float     # trailing BTTS rate


FORWARD_LEAGUES: list[ForwardLeague] = [
    # Norwegian 3. Division groups — all three confirmed Pinnacle coverage 2026-06-28
    ForwardLeague(777, "3. Division - Girone 4", "Norway", avg_goals=4.23, btts_pct=0.66),
    ForwardLeague(778, "3. Division - Girone 5", "Norway", avg_goals=4.23, btts_pct=0.66),
    ForwardLeague(779, "3. Division - Girone 6", "Norway", avg_goals=4.23, btts_pct=0.66),
    # Tasmania NPL — confirmed Pinnacle coverage, highest Pinnacle-passing avg-goal league
    ForwardLeague(648, "Tasmania NPL",            "Australia", avg_goals=4.39, btts_pct=0.60),
]

FORWARD_LEAGUE_IDS: list[int] = [fl.league_id for fl in FORWARD_LEAGUES]

# Bookmakers to capture — Pinnacle (id=4) is the sharp reference; Bet365 (id=8) is the soft anchor
CAPTURE_BOOKMAKERS: dict[int, str] = {
    4: "Pinnacle",
    8: "Bet365",
}

# Markets to capture per fixture (API-Football bet IDs)
CAPTURE_MARKETS: dict[str, int] = {
    "h2h":        1,   # Match Winner (1X2)
    "over_under": 5,   # Goals Over/Under (includes 2.5 line)
    "btts":       8,   # Both Teams Score
}

# Minimum hours between captures for the same fixture/bookmaker/market.
# Running the script more often than this is a no-op for any fixture already captured.
CAPTURE_STALE_HOURS: float = 2.0
