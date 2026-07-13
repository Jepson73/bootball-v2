"""
config/covered_leagues.py

Phase 37 Part A.1 — the empirically-confirmed "covered" league set for the
player-availability feature lever (Phase 36/37).

Phase 36's first probe (9 leagues, 4 tiers) found lineups+injuries coverage at
API-Football is INVERTED relative to our prediction habitat: present for
prestige leagues (England PL, Spain La Liga, MLS), a clean provider zero for
USL League Two / Serie D (Brazil + Italy) / Ethiopia PL / Azadegan Iran /
Gambia GFA -- exactly the long-tail leagues carrying most of our settled
volume and most of Track A's btts/ou25 resolution problem.

Phase 37 Part A.1 widened that probe to the top-60 habitat leagues by settled-
prediction volume (51 had an FT fixture to test) plus 6 major European leagues
not otherwise in that top 60 (Ligue 1, Bundesliga, Eredivisie, Süper Lig,
Primeira Liga, Scotland Championship) -- 192 live calls total across both
probes (78 + 102 + 12), trivial against the daily budget.

Finding that reshapes the picture: LINEUPS and INJURIES do NOT share a
footprint. Confirmed lineups are available in a much broader set of mid-tier
leagues (Brazil Serie B, Spain Segunda División, Poland's top three tiers,
Czech 3rd/4th tier, Ukraine PL, Belgium Jupiler Pro League, Morocco Botola
Pro, Japan J1, Norway/Sweden 2nd tiers, Netherlands Tweede Divisie, Romania
Liga I, Portugal Primeira Liga, Scotland Championship, Tanzania Ligi kuu Bara
-- roughly 30 leagues tested positive). INJURIES data is much narrower: only
12 of the ~63 leagues probed came back with both signals present.

Part B (availability tier) needs injuries/sidelined data specifically, so
COVERED_LEAGUES below is the lineups-AND-injuries intersection, not the wider
lineups-only set. Do not widen this list based on lineup presence alone --
re-probe injuries explicitly (see scripts/analysis/phase37_covered_league_probe.py
and phase37_big5_probe_results.json) before adding anything.

This list gates Part B's training set (config, not tribal knowledge) and
(pending a Part B pass) Part C's near-kickoff lineup collection scope.
"""

from dataclasses import dataclass

CURRENT_SEASON = 2026


@dataclass(frozen=True)
class CoveredLeague:
    league_id: int
    name: str
    country: str
    settled_predictions: int  # as of Phase 37 probe (2026-07-12), for context only


COVERED_LEAGUES: list[CoveredLeague] = [
    CoveredLeague(253, "MLS", "USA", 281),
    CoveredLeague(140, "La Liga", "Spain", 274),
    CoveredLeague(113, "Allsvenskan", "Sweden", 205),
    CoveredLeague(39, "Premier League", "England", 203),
    CoveredLeague(135, "Serie A", "Italy", 193),
    CoveredLeague(71, "Serie A", "Brazil", 190),
    CoveredLeague(235, "Premier League", "Russia", 169),
    CoveredLeague(103, "Eliteserien", "Norway", 169),
    CoveredLeague(88, "Eredivisie", "Netherlands", 161),
    CoveredLeague(61, "Ligue 1", "France", 161),
    CoveredLeague(78, "Bundesliga", "Germany", 146),
    CoveredLeague(203, "Süper Lig", "Turkey", 144),
]

COVERED_LEAGUE_IDS: list[int] = [cl.league_id for cl in COVERED_LEAGUES]

# Total settled predictions across the covered set at probe time -- the upper
# bound on Part B's training universe before requiring the exact
# (team, season, league) player_season_stats join Phase 36 found only ~24%
# complete even within backfilled leagues. Real usable n will be smaller.
COVERED_SETTLED_TOTAL: int = sum(cl.settled_predictions for cl in COVERED_LEAGUES)

# Leagues confirmed to have CONFIRMED LINEUPS but NOT injuries data (Phase 37
# probe). Not part of COVERED_LEAGUES -- Part B needs injuries. Recorded here
# so Part C doesn't have to re-derive this if lineup-only scope is ever
# revisited (e.g. if injuries coverage is later found to widen, or if a
# confirmed-XI-only feature set is explored without an availability tier).
LINEUP_ONLY_LEAGUE_IDS: list[int] = [
    72,   # Serie B, Brazil
    141,  # Segunda División, Spain
    114,  # Superettan, Sweden
    104,  # 1. Division, Norway
    98,   # J1 League, Japan
    200,  # Botola Pro, Morocco
    349,  # 3. liga - MSFL, Czech Republic
    144,  # Jupiler Pro League, Belgium
    333,  # Premier League, Ukraine
    107,  # I Liga, Poland
    685,  # 3. liga - CFL B, Czech Republic
    109,  # II Liga - East, Poland
    348,  # 3. liga - CFL A, Czech Republic
    106,  # Ekstraklasa, Poland
    492,  # Tweede Divisie, Netherlands
    283,  # Liga I, Romania
    94,   # Primeira Liga, Portugal
    180,  # Championship, Scotland
    567,  # Ligi kuu Bara, Tanzania -- coach field inconsistent, see Phase 36 report
]
