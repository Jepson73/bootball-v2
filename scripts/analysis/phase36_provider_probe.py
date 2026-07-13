"""
scripts/analysis/phase36_provider_probe.py

Phase 36 Task 2 — bounded provider coverage probe.
Samples recent FT fixtures stratified across our league habitat and probes
fixtures/lineups + injuries for each. Cache-aware client: re-runs cost 0 calls.

Projected cost (stated before running): 36 fixtures x 2 endpoints = 72 calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ingestion.client import APIFootballClient, calls_used_today

FIXTURES = [
    # (fixture_id, league_name, country, tier_label)
    (1379339, "Premier League", "England", "top-flight"),
    (1379340, "Premier League", "England", "top-flight"),
    (1379341, "Premier League", "England", "top-flight"),
    (1379342, "Premier League", "England", "top-flight"),
    (1391198, "La Liga", "Spain", "top-flight"),
    (1391189, "La Liga", "Spain", "top-flight"),
    (1391190, "La Liga", "Spain", "top-flight"),
    (1391191, "La Liga", "Spain", "top-flight"),
    (1490324, "MLS", "USA", "mls-usl"),
    (1490323, "MLS", "USA", "mls-usl"),
    (1490322, "MLS", "USA", "mls-usl"),
    (1490321, "MLS", "USA", "mls-usl"),
    (1525364, "USL League Two", "USA", "mls-usl"),
    (1525334, "USL League Two", "USA", "mls-usl"),
    (1525354, "USL League Two", "USA", "mls-usl"),
    (1525327, "USL League Two", "USA", "mls-usl"),
    (1567818, "Serie D", "Brazil", "serie-d-class"),
    (1567819, "Serie D", "Brazil", "serie-d-class"),
    (1567843, "Serie D", "Brazil", "serie-d-class"),
    (1567808, "Serie D", "Brazil", "serie-d-class"),
    (1544739, "Serie D - Girone A", "Italy", "serie-d-class"),
    (1544215, "Serie D - Girone A", "Italy", "serie-d-class"),
    (1544216, "Serie D - Girone A", "Italy", "serie-d-class"),
    (1544217, "Serie D - Girone A", "Italy", "serie-d-class"),
    (1541123, "Premier League", "Ethiopia", "long-tail"),
    (1541124, "Premier League", "Ethiopia", "long-tail"),
    (1541121, "Premier League", "Ethiopia", "long-tail"),
    (1541120, "Premier League", "Ethiopia", "long-tail"),
    (1477266, "Ligi kuu Bara", "Tanzania", "long-tail"),
    (1477267, "Ligi kuu Bara", "Tanzania", "long-tail"),
    (1477268, "Ligi kuu Bara", "Tanzania", "long-tail"),
    (1477269, "Ligi kuu Bara", "Tanzania", "long-tail"),
    (1554071, "Azadegan League", "Iran", "long-tail"),
    (1554072, "Azadegan League", "Iran", "long-tail"),
    (1554073, "Azadegan League", "Iran", "long-tail"),
    (1554074, "Azadegan League", "Iran", "long-tail"),
    (1529021, "GFA League", "Gambia", "long-tail"),
    (1529022, "GFA League", "Gambia", "long-tail"),
    (1529023, "GFA League", "Gambia", "long-tail"),
    (1529024, "GFA League", "Gambia", "long-tail"),
]


def main() -> None:
    client = APIFootballClient()
    before = calls_used_today()
    results = []

    for fid, league, country, tier in FIXTURES:
        lineups = client.get("fixtures/lineups", {"fixture": fid})
        injuries = client.get("injuries", {"fixture": fid})

        lineup_ok = len(lineups) == 2
        home_xi = len(lineups[0].get("startXI", [])) if lineup_ok else 0
        away_xi = len(lineups[1].get("startXI", [])) if lineup_ok else 0
        has_coach = lineup_ok and bool(lineups[0].get("coach", {}).get("id"))
        has_formation = lineup_ok and bool(lineups[0].get("formation"))

        results.append({
            "fixture_id": fid,
            "league": league,
            "country": country,
            "tier": tier,
            "lineups_present": lineup_ok,
            "home_xi_count": home_xi,
            "away_xi_count": away_xi,
            "has_coach": has_coach,
            "has_formation": has_formation,
            "injuries_count": len(injuries),
        })

    after = calls_used_today()

    out = {
        "calls_used": after - before,
        "results": results,
    }
    out_path = Path(__file__).parent / "phase36_probe_results.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Calls used this run: {after - before}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
