#!/usr/bin/env python3
"""
Task N — football-data.co.uk historical odds backfill.

Downloads free CSVs for 9 leagues (2019–2024), fuzzy-matches each row to
a Bootball fixture_id, and inserts matched H/D/A and OU 2.5 odds into
fixture_odds with bookmaker='fdco'.

Leagues:
  E0=39 (EPL), E1=40 (Championship), E2=41 (League One), E3=42 (League Two),
  SP1=140 (La Liga), SP2=141 (Segunda), I1=135 (Serie A Italy),
  I2=136 (Serie B Italy), D3=80 (3.Liga Germany)

Seasons: 2019–2024 (fd season codes 1920–2324, API-Football season = start year)

Output: scripts/analysis/fdco_backfill_report.json  +  inserts into football.db
"""

import csv
import io
import json
import logging
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("fdco_backfill")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "football.db"
REPORT_PATH = Path(__file__).resolve().parent / "fdco_backfill_report.json"
CACHE_DIR = Path(__file__).resolve().parent / "fdco_cache"
CACHE_DIR.mkdir(exist_ok=True)

BOOKMAKER = "fdco"
RATE_SLEEP = 1.0   # seconds between downloads (polite)

# fd.co.uk league code → Bootball league_id
LEAGUE_MAP = {
    "E0":  39,   # Premier League
    "E1":  40,   # Championship
    "E2":  41,   # League One
    "E3":  42,   # League Two
    "SP1": 140,  # La Liga
    "SP2": 141,  # Segunda División
    "I1":  135,  # Serie A (Italy)
    "I2":  136,  # Serie B (Italy)
    "D3":  80,   # 3. Liga (Germany)
}

# fd.co.uk season code → API-Football season year
FD_SEASONS = ["1920", "2021", "2122", "2223", "2324"]
FD_TO_API_SEASON = {
    "1920": 2019, "2021": 2020, "2122": 2021, "2223": 2022, "2324": 2023,
}

FD_BASE = "https://www.football-data.co.uk/mmz4281"


# ── Team name normalisation ───────────────────────────────────────────────────

# fd.co.uk short names → canonical expanded forms (populated further by fuzzy match)
KNOWN_ALIASES = {
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "brighton": "brighton & hove albion",
    "sheffield utd": "sheffield united",
    "sheffield weds": "sheffield wednesday",
    "sheffield wed": "sheffield wednesday",
    "west brom": "west bromwich albion",
    "qpr": "queens park rangers",
    "swansea": "swansea city",
    "cardiff": "cardiff city",
    "hull": "hull city",
    "stoke": "stoke city",
    "burnley": "burnley",
    "blackburn": "blackburn rovers",
    "birmingham": "birmingham city",
    "leicester": "leicester city",
    "norwich": "norwich city",
    "ipswich": "ipswich town",
    "luton": "luton town",
    "oxford": "oxford united",
    "plymouth": "plymouth argyle",
    "rotherham": "rotherham united",
    "peterborough": "peterborough united",
    "bolton": "bolton wanderers",
    "bristol city": "bristol city",
    "bristol rov": "bristol rovers",
    "crewe": "crewe alexandra",
    "port vale": "port vale",
    "colchester": "colchester united",
    "wigan": "wigan athletic",
    "scunthorpe": "scunthorpe united",
    "shrewsbury": "shrewsbury town",
    "gillingham": "gillingham",
    "tranmere": "tranmere rovers",
    "exeter": "exeter city",
    "cheltenham": "cheltenham town",
    "harrogate": "harrogate town",
    "newport co": "newport county",
    "salford": "salford city",
    "stevenage": "stevenage",
    "crawley": "crawley town",
    "crawley town": "crawley town",
    "grimsby": "grimsby town",
    "accrington": "accrington stanley",
    "fleetwood": "fleetwood town",
    "wycombe": "wycombe wanderers",
    "cambridge": "cambridge united",
    # German aliases
    "dortmund": "borussia dortmund",
    "m'gladbach": "borussia monchengladbach",
    "m'gladbach": "borussia monchengladbach",
    "e frankfurt": "eintracht frankfurt",
    "ein frankfurt": "eintracht frankfurt",
    "b leverkusen": "bayer leverkusen",
    "bayer leverkusen": "bayer leverkusen",
    "rb leipzig": "rb leipzig",
    "b munich": "bayern munich",
    "hertha": "hertha berlin",
    "hamburg": "hamburger sv",
    # Italian aliases
    "ac milan": "ac milan",
    "inter": "inter milan",
    "inter milan": "inter milan",
    "internazionale": "inter milan",
    "juventus": "juventus",
    "roma": "as roma",
    "lazio": "ss lazio",
    "napoli": "ssc napoli",
    "atlanta": "atalanta",
    "atalanta": "atalanta",
    "fiorentina": "acf fiorentina",
    "bologna": "bologna fc",
    "torino": "torino fc",
    "verona": "hellas verona",
    "udinese": "udinese calcio",
    "sampdoria": "uc sampdoria",
    "genoa": "genoa cfc",
    "sassuolo": "us sassuolo",
    "spezia": "spezia calcio",
    "salernitana": "us salernitana",
    "venezia": "venezia fc",
    "empoli": "empoli fc",
    "frosinone": "frosinone calcio",
    "cagliari": "cagliari calcio",
    # Spanish aliases
    "barcelona": "fc barcelona",
    "real madrid": "real madrid cf",
    "atletico madrid": "atletico de madrid",
    "atletico": "atletico de madrid",
    "sevilla": "sevilla fc",
    "betis": "real betis",
    "valencia": "valencia cf",
    "villarreal": "villarreal cf",
    "real sociedad": "real sociedad",
    "getafe": "getafe cf",
    "osasuna": "ca osasuna",
    "celta": "rc celta",
    "athletic club": "athletic club",
    "bilbao": "athletic club",
    "espanyol": "rcd espanyol",
    "mallorca": "rcd mallorca",
    "cadiz": "cadiz cf",
    "elche": "elche cf",
    "levante": "levante ud",
    "rayo": "rayo vallecano",
    "valladolid": "real valladolid",
    "almeria": "ud almeria",
    "granada": "granada cf",
    "eibar": "sd eibar",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation for fuzzy matching."""
    if not name:
        return ""
    n = name.lower().strip()
    n = n.replace(".", "").replace(",", "").replace("-", " ").replace("_", " ")
    n = " ".join(n.split())
    return KNOWN_ALIASES.get(n, n)


def fuzzy_match(query: str, choices: list, threshold: float = 0.75) -> Optional[str]:
    """Return best match from choices if similarity >= threshold, else None."""
    best_score = 0.0
    best_match = None
    qn = normalize_name(query)
    for c in choices:
        cn = normalize_name(c)
        score = SequenceMatcher(None, qn, cn).ratio()
        if score > best_score:
            best_score = score
            best_match = c
    if best_score >= threshold:
        return best_match
    return None


# ── CSV download ──────────────────────────────────────────────────────────────

def download_csv(fd_league: str, fd_season: str) -> Optional[list]:
    """Download and parse a fd.co.uk CSV. Returns list of row dicts or None."""
    cache_path = CACHE_DIR / f"{fd_league}_{fd_season}.csv"
    if cache_path.exists():
        raw = cache_path.read_bytes()
    else:
        url = f"{FD_BASE}/{fd_season}/{fd_league}.csv"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            cache_path.write_bytes(raw)
            logger.info(f"  Downloaded {url} ({len(raw):,} bytes)")
            time.sleep(RATE_SLEEP)
        except Exception as e:
            logger.warning(f"  Failed to download {url}: {e}")
            return None

    try:
        text = raw.decode("cp1252", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = [row for row in reader if row.get("HomeTeam", "").strip()]
        return rows
    except Exception as e:
        logger.warning(f"  Failed to parse {fd_league}_{fd_season}: {e}")
        return None


# ── Date parsing ──────────────────────────────────────────────────────────────

def parse_fd_date(date_str: str) -> Optional[str]:
    """Convert DD/MM/YYYY or DD/MM/YY to YYYY-MM-DD."""
    s = date_str.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── Odds extraction ───────────────────────────────────────────────────────────

def safe_float(row: dict, *keys) -> Optional[float]:
    """Try multiple column name variants, return first valid float."""
    for k in keys:
        v = row.get(k, "").strip()
        if v:
            try:
                f = float(v)
                return f if f >= 1.0 else None
            except ValueError:
                continue
    return None


def extract_odds(row: dict) -> dict:
    """Extract Bet365 (or best available) odds from a CSV row."""
    result = {}

    # H/D/A odds — prefer Bet365, then Pinnacle (PS), then market avg (Avg)
    h = safe_float(row, "B365H", "PSH", "AvgH", "MaxH")
    d = safe_float(row, "B365D", "PSD", "AvgD", "MaxD")
    a = safe_float(row, "B365A", "PSA", "AvgA", "MaxA")
    if h and d and a:
        result["h2h"] = (h, d, a)

    # OU 2.5 — various column name formats used by fd.co.uk over the years
    ov = safe_float(row, "B365>2.5", "B365Ov", "PSO>2.5", "Avg>2.5", "Max>2.5")
    un = safe_float(row, "B365<2.5", "B365Un", "PSO<2.5", "Avg<2.5", "Max<2.5")
    if ov and un:
        result["ou25"] = (ov, un)

    return result


# ── Main backfill logic ───────────────────────────────────────────────────────

def build_fixture_lookup(conn, league_id: int, season: int) -> dict:
    """
    Build lookup: (date_str, norm_home, norm_away) → fixture_id
    Also returns list of team names for fuzzy matching.
    """
    rows = conn.execute("""
        SELECT f.id, f.date, t_h.name AS home_name, t_a.name AS away_name
        FROM fixtures f
        JOIN teams t_h ON t_h.id = f.home_team_id
        JOIN teams t_a ON t_a.id = f.away_team_id
        WHERE f.league_id = ? AND f.season = ? AND f.status = 'FT'
    """, (league_id, season)).fetchall()

    lookup_exact = {}
    lookup_by_date = {}  # date → list of (norm_home, norm_away, fixture_id)

    for fid, date_str, home_name, away_name in rows:
        d = date_str[:10]  # YYYY-MM-DD
        nh = normalize_name(home_name)
        na = normalize_name(away_name)
        lookup_exact[(d, nh, na)] = fid

        if d not in lookup_by_date:
            lookup_by_date[d] = []
        lookup_by_date[d].append((home_name, away_name, nh, na, fid))

    all_team_names = list(set(
        name for (fid, date_str, home_name, away_name) in rows
        for name in [home_name, away_name]
    ))

    return lookup_exact, lookup_by_date, all_team_names


def match_fixture(date_str: str, home_fd: str, away_fd: str,
                  lookup_exact: dict, lookup_by_date: dict) -> tuple:
    """
    Returns (fixture_id, match_type) where match_type is 'exact', 'fuzzy', or 'none'.
    """
    nh = normalize_name(home_fd)
    na = normalize_name(away_fd)

    # Exact match
    fid = lookup_exact.get((date_str, nh, na))
    if fid is not None:
        return fid, "exact"

    # Fuzzy match by date
    candidates = lookup_by_date.get(date_str, [])
    if not candidates:
        return None, "none"

    best_score = 0.0
    best_fid = None

    for (raw_home, raw_away, cn_home, cn_away, fid) in candidates:
        score_h = SequenceMatcher(None, nh, cn_home).ratio()
        score_a = SequenceMatcher(None, na, cn_away).ratio()
        combined = (score_h + score_a) / 2
        if combined > best_score:
            best_score = combined
            best_fid = fid

    if best_score >= 0.72:
        return best_fid, "fuzzy"

    return None, "none"


def odds_already_exist(conn, fixture_id: int) -> bool:
    """Check if fdco odds already exist for this fixture."""
    return conn.execute(
        "SELECT 1 FROM fixture_odds WHERE fixture_id=? AND bookmaker=? LIMIT 1",
        (fixture_id, BOOKMAKER)
    ).fetchone() is not None


def insert_odds(conn, fixture_id: int, odds_dict: dict, fetched_at: str):
    """Insert matched odds rows into fixture_odds."""
    if "h2h" in odds_dict:
        h, d, a = odds_dict["h2h"]
        conn.execute("""
            INSERT OR IGNORE INTO fixture_odds
            (fixture_id, bookmaker, bet_type, odd_home, odd_draw, odd_away, fetched_at)
            VALUES (?, ?, 'h2h', ?, ?, ?, ?)
        """, (fixture_id, BOOKMAKER, h, d, a, fetched_at))

    if "ou25" in odds_dict:
        ov, un = odds_dict["ou25"]
        conn.execute("""
            INSERT OR IGNORE INTO fixture_odds
            (fixture_id, bookmaker, bet_type, odd_over, odd_under, fetched_at)
            VALUES (?, ?, 'over_under', ?, ?, ?)
        """, (fixture_id, BOOKMAKER, ov, un, fetched_at))


def run():
    conn = sqlite3.connect(DB_PATH)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    totals = {
        "downloaded": 0, "rows_parsed": 0, "matched_exact": 0,
        "matched_fuzzy": 0, "unmatched": 0, "inserted_h2h": 0,
        "inserted_ou25": 0, "already_existed": 0,
    }
    per_league_season = []
    ambiguous_log = []

    for fd_league, league_id in LEAGUE_MAP.items():
        for fd_season in FD_SEASONS:
            api_season = FD_TO_API_SEASON[fd_season]
            logger.info(f"Processing {fd_league} {fd_season} → league_id={league_id}, season={api_season}")

            rows = download_csv(fd_league, fd_season)
            if rows is None:
                per_league_season.append({
                    "fd_league": fd_league, "fd_season": fd_season,
                    "league_id": league_id, "api_season": api_season,
                    "status": "download_failed", "rows": 0,
                })
                continue

            totals["downloaded"] += 1
            totals["rows_parsed"] += len(rows)

            lookup_exact, lookup_by_date, all_team_names = build_fixture_lookup(
                conn, league_id, api_season)

            stats = {
                "fd_league": fd_league, "fd_season": fd_season,
                "league_id": league_id, "api_season": api_season,
                "status": "ok", "rows": len(rows),
                "matched_exact": 0, "matched_fuzzy": 0, "unmatched": 0,
                "inserted_h2h": 0, "inserted_ou25": 0, "already_existed": 0,
            }

            for row in rows:
                home_fd = row.get("HomeTeam", "").strip()
                away_fd = row.get("AwayTeam", "").strip()
                date_raw = row.get("Date", "").strip()

                if not home_fd or not away_fd or not date_raw:
                    continue

                date_str = parse_fd_date(date_raw)
                if not date_str:
                    continue

                fid, match_type = match_fixture(
                    date_str, home_fd, away_fd, lookup_exact, lookup_by_date)

                if match_type == "none":
                    stats["unmatched"] += 1
                    totals["unmatched"] += 1
                    ambiguous_log.append({
                        "fd_league": fd_league, "fd_season": fd_season,
                        "date": date_str, "home": home_fd, "away": away_fd,
                        "reason": "no_match",
                    })
                    continue

                if match_type == "exact":
                    stats["matched_exact"] += 1
                    totals["matched_exact"] += 1
                else:
                    stats["matched_fuzzy"] += 1
                    totals["matched_fuzzy"] += 1

                if odds_already_exist(conn, fid):
                    stats["already_existed"] += 1
                    totals["already_existed"] += 1
                    continue

                odds = extract_odds(row)
                if not odds:
                    continue

                fetched_at = f"{date_str} 00:00:00"
                insert_odds(conn, fid, odds, fetched_at)

                if "h2h" in odds:
                    stats["inserted_h2h"] += 1
                    totals["inserted_h2h"] += 1
                if "ou25" in odds:
                    stats["inserted_ou25"] += 1
                    totals["inserted_ou25"] += 1

            conn.commit()
            per_league_season.append(stats)
            match_total = stats["matched_exact"] + stats["matched_fuzzy"]
            match_rate = match_total / stats["rows"] if stats["rows"] else 0
            logger.info(f"  {fd_league} {fd_season}: {stats['rows']} rows, "
                        f"{match_total} matched ({match_rate:.1%}), "
                        f"h2h_inserted={stats['inserted_h2h']}, "
                        f"ou25_inserted={stats['inserted_ou25']}")

    conn.close()

    # Post-backfill odds coverage
    conn2 = sqlite3.connect(DB_PATH)
    coverage = conn2.execute("""
        SELECT f.season,
               COUNT(DISTINCT CASE WHEN fo.odd_home IS NOT NULL THEN f.id END) has_h2h,
               COUNT(DISTINCT CASE WHEN fo.odd_over IS NOT NULL THEN f.id END) has_ou25,
               COUNT(DISTINCT f.id) total_ft
        FROM fixtures f
        LEFT JOIN fixture_odds fo ON fo.fixture_id = f.id
        WHERE f.season BETWEEN 2019 AND 2024
          AND f.status = 'FT'
          AND f.league_id IN (39,40,41,42,135,136,140,141,80)
        GROUP BY f.season
        ORDER BY f.season
    """).fetchall()
    conn2.close()

    coverage_rows = [
        {"season": r[0], "fixtures_with_h2h": r[1], "fixtures_with_ou25": r[2], "total_ft": r[3]}
        for r in coverage
    ]

    report = {
        "generated": datetime.utcnow().isoformat(),
        "totals": totals,
        "coverage_post_backfill": coverage_rows,
        "per_league_season": per_league_season,
        "ambiguous_sample": ambiguous_log[:50],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    logger.info(f"Report written to {REPORT_PATH}")

    # Print summary
    print("\n" + "=" * 64)
    print("FOOTBALL-DATA.CO.UK BACKFILL SUMMARY")
    print("=" * 64)
    total_matched = totals["matched_exact"] + totals["matched_fuzzy"]
    total_rows = totals["rows_parsed"]
    print(f"Rows parsed:      {total_rows:,}")
    print(f"Matched (exact):  {totals['matched_exact']:,}")
    print(f"Matched (fuzzy):  {totals['matched_fuzzy']:,}")
    print(f"Unmatched:        {totals['unmatched']:,}")
    print(f"Match rate:       {total_matched/total_rows:.1%}" if total_rows else "N/A")
    print(f"H2H inserted:     {totals['inserted_h2h']:,}")
    print(f"OU25 inserted:    {totals['inserted_ou25']:,}")
    print(f"Already existed:  {totals['already_existed']:,}")
    print()
    print("Post-backfill odds coverage (target leagues, seasons 2019-2024):")
    print(f"  {'Season':>8} {'FT_Fixtures':>13} {'Has_H2H':>9} {'Has_OU25':>10}")
    for r in coverage_rows:
        print(f"  {r['season']:>8} {r['total_ft']:>13,} "
              f"{r['fixtures_with_h2h']:>9,} {r['fixtures_with_ou25']:>10,}")


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    run()
