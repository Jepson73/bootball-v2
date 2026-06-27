# Bootball V2 — Research Record

**Status: Research archive. Not a deployable betting system.**

This repository documents a seven-phase systematic investigation into whether public football data can generate profitable edge on h2h (1X2), BTTS, and over/under markets against retail bookmakers.

**Start here:** [`scripts/analysis/AUDIT_V2.md`](scripts/analysis/AUDIT_V2.md) — the consolidated verdict across all phases.

---

## What This Repository Contains

Two things that are kept together because they inform each other:

**1. The production system** (`src/`, `backend/`, `scripts/`, `config/`)  
A working autonomous betting pipeline: data ingestion from API-Football, LightGBM + Dixon-Coles prediction models, Markowitz portfolio allocation, fractional Kelly sizing, settlement, and per-league Platt calibration. The system was running live during the research period (Apr–Jun 2026, 448 bets placed).

**2. The research investigation** (`scripts/analysis/`)  
Seven phases of walk-forward backtesting using 20,658 historical fixtures (2019–2026) across eight European leagues. Each phase tested a specific model lever against a pre-registered profitability bar. See [AUDIT_V2.md](scripts/analysis/AUDIT_V2.md) for the full verdict.

---

## What Was Found

The short version (see [AUDIT_V2.md](scripts/analysis/AUDIT_V2.md) for the full decomposition):

- The prediction engine generates **real directional signal** — the Dixon-Coles + xG model achieves statistically significant positive Closing-Line Value on h2h markets (+0.5% without xG, ~+2% with xG). This is evidence the model identifies genuine mispricing direction.
- **No phase produced profitable realized ROI** against the pre-registered bar (95% CI > 0 in ≥2 windows). The fundamental gap is ~10pp: market margin (~5.5% at B365) plus a selection penalty (4–16pp) far exceed the current CLV signal.
- Levers exhausted (Wave 1 rolling features, Dixon-Coles, weather, league regime, odds ceilings) are documented in the audit's Confirmed Dead table. Levers remaining (strength-adjusted xG, exchange access, market-structure timing) are in the Genuinely Untested table.
- The research record's purpose is to make the stopping/continuing decision in Phase 8 evidence-based rather than open-ended.

---

## Phase Summary

| Phase | Lever | Key outcome |
|-------|-------|-------------|
| 1a | Baseline (9-feat LightGBM, raw probabilities) | −3.5% ROI; EV filter passes ~100% of bets (formula bug) |
| 1b–1d | Calibration + formula fix + Shin market blend | −1.4% ROI [−8.1%,+4.9%]; first honest baseline |
| 2 | +20 rolling form / H2H / league features | −8.5% to −9.4% h2h ROI; significantly worse than baseline |
| 3 | Dixon-Coles bivariate Poisson goal model | −7.7% to −10.6% h2h ROI; CLV slightly better than LightGBM |
| 4/4b | Odds ceiling + overround + bias hunt | No segment or filter passes bar; FLB trap confirmed |
| 5T1 | CLV analysis | **h2h CLV +0.55% (CI > 0, both windows)** — first genuinely positive signal |
| 5T2 | Weather + referee features | Referee significant but ±1.5pp effect; no EV improvement |
| 6 | CLV decomposition + edge gap quantification | Gap = margin 5.5pp + selection penalty 4–6pp ≈ 10pp vs B365 |
| 7 | xG from Understat (EPL/Serie A/La Liga) | CLV doubles to ~+2%; EV ROI unstable across windows; bar not met |

---

## Data and Licensing

**What is included in this repo:**
- All Python analysis scripts (`scripts/analysis/*.py`)
- All phase reports (`scripts/analysis/v2_phase*_report.md` through `v7_xg_report.md`)
- The consolidated audit (`scripts/analysis/AUDIT_V2.md`)
- Result JSON files documenting numerical findings
- The production system source code

**What is excluded (gitignored, regenerable):**
- All SQLite databases (`*.db`) including the production `data/football.db` and analysis `historical_odds.db`
- Analysis cache directories (`feature_cache/`, `weather_cache/`, `dc_cache/`, `understat_cache/`, `fdco_cache/`) — see [AUDIT_V2.md §7](scripts/analysis/AUDIT_V2.md) for regeneration instructions
- API credentials (`.env`) — see `.env.example` for the required variables
- Trained ML models (`*.pkl`)

**Third-party data notices:**
- **football-data.co.uk** odds (used in Phases 1d–7): Free for personal use; not redistributed in this repo. Run `scripts/analysis/fdco_backfill.py` to regenerate.
- **Understat xG** (Phase 7): Scraped via `understatapi` at 0.8 req/s. Redistribution status unconfirmed; not included. Run `scripts/analysis/phase7_xg_analysis.py` to regenerate.
- **API-Football** fixture data: Requires a paid API-Football key. The production database is not redistributed.

---

## Running the Analysis

To reproduce any phase, regenerate the relevant caches first:

```bash
# Install dependencies
pip install -r requirements.txt

# Set DB path
export DATABASE_PATH=./data/football.db   # requires local copy of production DB

# Phase 1d — fdco odds backfill (downloads from football-data.co.uk, free)
python scripts/analysis/fdco_backfill.py

# Phase 2 — Wave 1 walk-forward backtest (generates feature_cache/)
python scripts/analysis/walk_forward_backtest_v4.py

# Phase 3 — Dixon-Coles (generates dc_cache/)
python scripts/analysis/dixon_coles_backtest.py

# Phase 5 T2 — Weather + referee (generates weather_cache/, ~639 API calls, free)
python scripts/analysis/phase5_wave2.py

# Phase 6 — Combined
python scripts/analysis/phase6_combined.py

# Phase 7 — xG from Understat (generates understat_cache/, ~110 scrape calls at 0.8/s)
python scripts/analysis/phase7_xg_analysis.py
```

Results are written to the corresponding `*_results.json` files and phase report `.md` files in `scripts/analysis/`.

---

## Running the Production System

The production pipeline is included as context for the research, not as a recommended deployment. If you want to run it:

```bash
cp .env.example .env
# Set: API_FOOTBALL_KEY, BOOTBALL_PASSWORD, DISCORD_WEBHOOK_URL

python scripts/migrate.py     # apply all DB migrations
python scripts/web_ui.py      # Flask app + scheduler at http://localhost:5000
```

The betting bot is **disabled by default** (`BOT_ENABLED=false` in `.env.example`). Do not enable it without reading the audit first — the research found no statistically significant edge at current model maturity.

---

## Further Reading

- [`scripts/analysis/AUDIT_V2.md`](scripts/analysis/AUDIT_V2.md) — **consolidated research verdict** (start here)
- [`scripts/analysis/v2_phase1_report.md`](scripts/analysis/v2_phase1_report.md) through [`v7_xg_report.md`](scripts/analysis/v7_xg_report.md) — detailed phase reports
- [`docs/codebase_reference.md`](docs/codebase_reference.md) — production system architecture reference
