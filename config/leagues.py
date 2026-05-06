"""
config/leagues.py

All leagues covered for prediction and betting.

Season logic: year = season START year
- 2025/26 season = 2025 (starts Aug 2025, ends May 2026)
- 2026 season = 2026 (starts March 2026, e.g., Allsvenskan)

Per-league model quality (btts_weight, over25_weight, under25_weight) reflects
historical backtest ROI.  All leagues are equally eligible for betting — ordering
and priority are determined at runtime by the league calibration engine
(brier_improvement in LeagueCalibration), not by hardcoded tier labels.

WEIGHT GUIDE (based on backtest ROI):
- 1.3+ = Excellent (consistently profitable)
- 1.1-1.2 = Good (usually profitable)
- 0.9-1.0 = Average (keep in training)
- < 0.9 = Poor (still predict but lower confidence)
"""

LEAGUES = {
    61: {"name": "Ligue 1", "country": "France",         "btts": 44, "over25": 44, "under25": 56,
         "btts_weight": 1.5, "over25_weight": 1.5, "under25_weight": 0.9,
         "recommended_market": "btts,ou25",
         "note": "Best performer: +39% BTTS, +29% O/U"},
    78: {"name": "Bundesliga", "country": "Germany",         "btts": 58, "over25": 62, "under25": 38,
         "btts_weight": 1.3, "over25_weight": 1.4, "under25_weight": 0.8,
         "note": "Strong: +11% BTTS, +20% O/U"},
    88: {"name": "Eredivisie", "country": "Netherlands",         "btts": 57, "over25": 62, "under25": 38,
         "btts_weight": 1.2, "over25_weight": 1.3, "under25_weight": 0.8},
    207: {"name": "Swiss Super League", "country": "Switzerland",         "btts": 55, "over25": 67, "under25": 33,
         "btts_weight": 1.2, "over25_weight": 1.3, "under25_weight": 0.7},

    253: {"name": "MLS", "country": "USA",          "btts": 50, "over25": 63, "under25": 37,
          "btts_weight": 0.9, "over25_weight": 1.5, "under25_weight": 0.8,
          "late_goal_factor": 1.3,
          "note": "O/U excellent: +31%, BTTS poor: +3%"},
    332: {"name": "Super Liga", "country": "Slovakia",          "btts": 50, "over25": 52, "under25": 48,
          "btts_weight": 0.8, "over25_weight": 0.9, "under25_weight": 1.0},
    188: {"name": "A-League", "country": "Australia",          "btts": 55, "over25": 56, "under25": 44,
          "btts_weight": 1.1, "over25_weight": 1.1, "under25_weight": 1.0,
          "late_goal_factor": 1.5},
    98: {"name": "J1 League", "country": "Japan",         "btts": 52, "over25": 52, "under25": 48,
         "btts_weight": 1.0, "over25_weight": 1.0, "under25_weight": 1.0},
    213: {"name": "Third NL - Istok", "country": "Croatia",         "btts": 45, "over25": 46, "under25": 54,
         "btts_weight": 0.7, "over25_weight": 0.7, "under25_weight": 1.1},
    909: {"name": "MLS Next Pro", "country": "USA",          "btts": 50, "over25": 55, "under25": 45,
          "btts_weight": 0.8, "over25_weight": 0.9, "under25_weight": 1.0},

    # Scandinavia - good performers
    113: {"name": "Allsvenskan", "country": "Sweden",         "btts": 52, "over25": 55, "under25": 45,
         "btts_weight": 1.3, "over25_weight": 1.2, "under25_weight": 0.9,
         "note": "Strong: +18% BTTS, +8% O/U"},

    # Top 5 European - mixed results
    140: {"name": "La Liga", "country": "Spain",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 1.2, "over25_weight": 0.95, "under25_weight": 1.0,
         "note": "BTTS good: +13%, O/U poor: +1%"},
    135: {"name": "Serie A", "country": "Italy",         "btts": 48, "over25": 45, "under25": 55,
         "btts_weight": 1.1, "over25_weight": 1.1, "under25_weight": 1.0,
         "note": "Decent: +9% BTTS, +7% O/U"},
    39: {"name": "Premier League", "country": "England",         "btts": 52, "over25": 53, "under25": 47,
         "btts_weight": 0.85, "over25_weight": 0.8, "under25_weight": 1.1,
         "note": "Poor performer: +0.5% BTTS, -4% O/U"},

    # England - poor performers (keep in training)
    40: {"name": "Championship", "country": "England",         "btts": 50, "over25": 50, "under25": 50,
         "btts_weight": 0.8, "over25_weight": 0.7, "under25_weight": 1.1,
         "note": "Avoid: -2% BTTS, -10% O/U"},
    41: {"name": "League One", "country": "England",         "btts": 48, "over25": 48, "under25": 52,
         "btts_weight": 0.85, "over25_weight": 0.7, "under25_weight": 1.1,
         "note": "Avoid: -6% O/U"},
    42: {"name": "League Two", "country": "England",         "btts": 45, "over25": 45, "under25": 55,
         "btts_weight": 0.85, "over25_weight": 0.8, "under25_weight": 1.1},

    # Spain
    141: {"name": "Segunda División", "country": "Spain",         "btts": 47, "over25": 48, "under25": 52,
         "btts_weight": 0.9, "over25_weight": 0.85, "under25_weight": 1.0},

    # Italy - Low scoring
    136: {"name": "Serie B", "country": "Italy",         "btts": 44, "over25": 42, "under25": 58,
         "btts_weight": 0.85, "over25_weight": 0.8, "under25_weight": 1.2},

    # Germany
    79: {"name": "2. Bundesliga", "country": "Germany",         "btts": 55, "over25": 58, "under25": 42,
         "btts_weight": 1.1, "over25_weight": 1.15, "under25_weight": 0.85},

    # France
    62: {"name": "Ligue 2", "country": "France",         "btts": 42, "over25": 42, "under25": 58,
         "btts_weight": 0.9, "over25_weight": 0.85, "under25_weight": 1.1},

    # Netherlands - High scoring
    89: {"name": "Eerste Divisie", "country": "Netherlands",         "btts": 58, "over25": 64, "under25": 36,
         "btts_weight": 1.2, "over25_weight": 1.3, "under25_weight": 0.75},

    # Portugal
    94: {"name": "Primeira Liga", "country": "Portugal",         "btts": 46, "over25": 50, "under25": 50,
         "btts_weight": 0.9, "over25_weight": 0.95, "under25_weight": 1.0},
    95: {"name": "Segunda Liga", "country": "Portugal",         "btts": 44, "over25": 46, "under25": 54,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},

    # Turkey
    203: {"name": "Süper Lig", "country": "Turkey",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.9, "over25_weight": 0.95, "under25_weight": 1.0},
    204: {"name": "1. Lig", "country": "Turkey",         "btts": 46, "over25": 48, "under25": 52,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},

    # Belgium
    144: {"name": "Jupiler Pro League", "country": "Belgium",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.95, "over25_weight": 0.95, "under25_weight": 1.0},
    145: {"name": "Challenger Pro League", "country": "Belgium",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},

    # Switzerland
    208: {"name": "Challenge League", "country": "Switzerland",         "btts": 52, "over25": 58, "under25": 42,
         "btts_weight": 1.0, "over25_weight": 1.1, "under25_weight": 0.85},

    # Austria
    218: {"name": "Bundesliga", "country": "Austria",         "btts": 52, "over25": 55, "under25": 45,
         "btts_weight": 1.0, "over25_weight": 1.05, "under25_weight": 0.95},
    219: {"name": "2. Liga", "country": "Austria",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.9, "over25_weight": 0.95, "under25_weight": 1.0},

    # Scandinavia
    119: {"name": "Superliga", "country": "Denmark",         "btts": 52, "over25": 59, "under25": 41,
         "btts_weight": 1.0, "over25_weight": 1.1, "under25_weight": 0.9},
    120: {"name": "1. Division", "country": "Denmark",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.9, "over25_weight": 0.95, "under25_weight": 1.0},
    114: {"name": "Superettan", "country": "Sweden",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0,
         "note": "Avoid: +0% BTTS, -6% O/U"},
    103: {"name": "Eliteserien", "country": "Norway",         "btts": 54, "over25": 58, "under25": 42,
         "btts_weight": 1.05, "over25_weight": 1.1, "under25_weight": 0.9},
    104: {"name": "1. Division", "country": "Norway",         "btts": 52, "over25": 55, "under25": 45,
         "btts_weight": 0.95, "over25_weight": 1.0, "under25_weight": 1.0},

    # Scotland
    179: {"name": "Premiership", "country": "Scotland",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.9, "over25_weight": 0.9, "under25_weight": 1.0},
    180: {"name": "Championship", "country": "Scotland",         "btts": 46, "over25": 48, "under25": 52,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.1},
    183: {"name": "League One", "country": "Scotland",         "btts": 44, "over25": 45, "under25": 55,
         "btts_weight": 0.85, "over25_weight": 0.8, "under25_weight": 1.1},

    # Poland
    106: {"name": "Ekstraklasa", "country": "Poland",         "btts": 46, "over25": 48, "under25": 52,
         "btts_weight": 0.9, "over25_weight": 0.9, "under25_weight": 1.0},
    107: {"name": "I Liga", "country": "Poland",         "btts": 44, "over25": 46, "under25": 54,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.1},

    # Central/Eastern Europe
    271: {"name": "NB I", "country": "Hungary",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.95, "over25_weight": 0.95, "under25_weight": 1.0},
    272: {"name": "NB II", "country": "Hungary",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.85, "over25_weight": 0.9, "under25_weight": 1.0},
    210: {"name": "HNL", "country": "Croatia",         "btts": 44, "over25": 45, "under25": 55,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.1},
    283: {"name": "Liga I", "country": "Romania",         "btts": 42, "over25": 44, "under25": 56,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.1},

    # Russia / Ukraine
    235: {"name": "Premier League", "country": "Russia",         "btts": 46, "over25": 48, "under25": 52,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},
    333: {"name": "Premier League", "country": "Ukraine",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.85, "over25_weight": 0.9, "under25_weight": 1.0},

    # Greece
    197: {"name": "Super League 1", "country": "Greece",         "btts": 42, "over25": 44, "under25": 56,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.1},

    # Asia
    292: {"name": "K League 1", "country": "South Korea",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.95, "over25_weight": 0.95, "under25_weight": 1.0},

    45: {"name": "FA Cup", "country": "England",         "btts": 50, "over25": 52, "under25": 48,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},
    46: {"name": "EFL Trophy", "country": "England",         "btts": 48, "over25": 50, "under25": 50,
         "btts_weight": 0.85, "over25_weight": 0.85, "under25_weight": 1.0},
}

# Leagues we actively track, fetch, and predict on.
# Ordering within this set is determined by per-league model performance
# (brier_improvement in LeagueCalibration), not by manual tier labels.
PRIORITY_LEAGUE_IDS = [45, 46, 78, 88, 207, 253, 188, 98, 39, 140, 135, 61, 40, 41, 42, 141, 136, 79, 62, 89, 94, 95, 203, 204, 144, 145, 208, 218, 219, 119, 120, 113, 114, 103, 104, 179, 180, 183, 106, 107, 271, 272, 210, 283, 235, 333, 197, 292, 332]

# Merge all API-known leagues as generic entries — curated LEAGUES above take priority.
# New leagues from pre_league.py become available automatically on next process start.
try:
    from config.pre_league import API_LEAGUES as _API_LEAGUES

    _generic: dict = {
        lid: {
            "name": info["name"],
            "country": info["country"],
            "btts": 50,
            "over25": 50,
            "under25": 50,
            "btts_weight": 0.8,
            "over25_weight": 0.8,
            "under25_weight": 0.8,
        }
        for lid, info in _API_LEAGUES.items()
        if lid not in LEAGUES
    }
    LEAGUES = {**_generic, **LEAGUES}   # curated entries override generic ones
except ImportError:
    pass  # pre_league.py missing — fall back to curated list only

# All leagues combined
ALL_LEAGUE_IDS = list(LEAGUES.keys())

# Seasons to backfill (year = season START year)
# 2025 = 2025/26 season (Aug 2025 - May 2026)
# 2026 = 2026 season (March - December 2026, e.g., Scandinavian leagues)
BACKFILL_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
