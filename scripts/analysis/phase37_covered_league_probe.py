"""
scripts/analysis/phase37_covered_league_probe.py

Phase 37 Part A.1 — confirmation pass extending Phase 36's 9-league probe to the
top-60 habitat leagues by settled-prediction volume (53 targeted, 51 had an FT
fixture to probe). Determines which leagues actually carry lineups + injuries
data at the provider, to be committed as config/covered_leagues.py.

Projected cost (stated before running): 51 leagues x 2 calls = 102 calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ingestion.client import APIFootballClient, calls_used_today

INPUT_PSV = Path(__file__).parent / "phase37_covered_probe_targets.psv"


def load_targets() -> list[tuple[int, str, str, int, int]]:
    targets = []
    for line in INPUT_PSV.read_text().splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        lid, name, country, n, fid = parts
        targets.append((int(lid), name, country, int(n), int(fid)))
    return targets


def main() -> None:
    client = APIFootballClient()
    before = calls_used_today()
    results = []

    for lid, name, country, n, fid in load_targets():
        lineups = client.get("fixtures/lineups", {"fixture": fid})
        injuries = client.get("injuries", {"fixture": fid})

        lineup_ok = len(lineups) == 2
        home_xi = len(lineups[0].get("startXI", [])) if lineup_ok else 0
        away_xi = len(lineups[1].get("startXI", [])) if lineup_ok else 0

        results.append({
            "league_id": lid,
            "league": name,
            "country": country,
            "settled_n": n,
            "fixture_id": fid,
            "lineups_present": lineup_ok,
            "home_xi_count": home_xi,
            "away_xi_count": away_xi,
            "injuries_count": len(injuries),
        })

    after = calls_used_today()

    out = {"calls_used": after - before, "results": results}
    out_path = Path(__file__).parent / "phase37_covered_probe_results.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Calls used this run: {after - before}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
