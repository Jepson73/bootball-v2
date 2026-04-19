# football-predictor

Football match prediction using Dixon-Coles, Elo, xG, and ML ensemble models.
Identifies value bets with Kelly-criterion sizing against bookmaker odds.

---

## Stack

| Layer | Library |
|---|---|
| API | API-Football v3 (75k calls/day) |
| Storage | SQLAlchemy + SQLite (dev) / Postgres (prod) |
| Statistical models | scipy, statsmodels |
| ML models | XGBoost, LightGBM, scikit-learn |
| Config | pydantic-settings + .env |

---

## Setup

```bash
# On your Proxmox container
cd ~/football-predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env and set API_FOOTBALL_KEY=your_key
```

---

## Project Structure

```
football-predictor/
├── config/
│   ├── leagues.py          # All 94 leagues with priority tiers
│   └── settings.py         # Typed settings from .env
│
├── src/
│   ├── ingestion/
│   │   ├── client.py       # Rate-limited, cache-first API client
│   │   ├── backfill.py     # Resumable historical data loader
│   │   └── scheduler.py    # Daily operational pipeline
│   │
│   ├── storage/
│   │   ├── db.py           # SQLAlchemy engine + session helpers
│   │   └── models.py       # ORM: Fixture, Stats, Odds, Elo, Predictions, ValueBets
│   │
│   ├── features/           # (Phase 2)
│   │   ├── elo.py          # Rolling Elo ratings
│   │   ├── form.py         # Recent form, momentum, fatigue
│   │   ├── strength.py     # Dixon-Coles attack/defense strengths
│   │   └── xg_features.py  # xG rolling averages
│   │
│   ├── models/             # (Phase 3)
│   │   ├── poisson.py      # Base Poisson regression
│   │   ├── dixon_coles.py  # Dixon-Coles with time decay
│   │   ├── ml_ensemble.py  # XGBoost + LightGBM stacking
│   │   └── ensemble.py     # Weighted model blend
│   │
│   ├── betting/            # (Phase 4)
│   │   ├── ev.py           # Expected Value calculator
│   │   ├── kelly.py        # Kelly Criterion bet sizing
│   │   ├── shin.py         # Shin method: remove bookmaker margin
│   │   └── value_bets.py   # Flag bets where our P > implied P
│   │
│   └── evaluation/         # (Phase 5)
│       ├── calibration.py  # Brier score, log loss
│       ├── backtesting.py  # Historical ROI simulation
│       └── sharpe.py       # Risk-adjusted return metrics
│
├── scripts/
│   └── backfill.py         # CLI: python scripts/backfill.py --leagues 39 --seasons 2024
│
├── tests/
│   └── test_client.py
│
└── notebooks/
    ├── 01_eda.ipynb
    ├── 02_dixon_coles_dev.ipynb
    └── 03_backtesting.ipynb
```

---

## Build Phases

### Phase 1 — Data (NOW)
```bash
# Initialise DB
python -c "from src.storage.db import init_db; init_db()"

# Backfill one league to validate pipeline
python scripts/backfill.py --leagues 39 --seasons 2024

# Full Tier 1 backfill (all 6 leagues, 5 seasons ~630 API calls)
python scripts/backfill.py --seasons 2020 2021 2022 2023 2024
```

### Phase 2 — Feature Engineering
Build `src/features/`:
- `elo.py` — rolling Elo per team, updated after each match
- `strength.py` — Dixon-Coles attack/defense parameters via MLE
- `form.py` — last 5/10 match form, home/away splits
- `xg_features.py` — xG for/against rolling averages (from fixture stats shots on goal proxy)

### Phase 3 — Models
- Start with Dixon-Coles (most validated for ROI)
- Add Elo as standalone predictor baseline
- Add ML ensemble trained on feature set from Phase 2

### Phase 4 — Betting Engine
- Shin method to remove bookmaker margin from raw odds
- EV = (our_prob × bookmaker_odd) - 1
- Flag bets where EV > 5% threshold
- Kelly fraction for stake sizing (use 0.25× Kelly to be conservative)

### Phase 5 — Evaluation
- Brier score + log loss per model
- ROI backtest on historical value bets
- Sharpe ratio of returns
- Calibration plots (are our probabilities actually correct?)

---

## API Call Budget

| Task | Calls |
|---|---|
| Daily operations (fixtures, odds, injuries) | ~1,000 |
| Full Tier 1 backfill (6 leagues × 5 seasons) | ~630 |
| Full 15-league backfill × 5 seasons | ~1,575 |
| **Daily headroom for development/experiments** | **~73,000** |

---

## Key Scientific References

| Paper | What it gives us |
|---|---|
| Dixon & Coles (1997) | Bivariate Poisson model + low-score correction |
| Hvattum & Arntzen (2010) | Elo with margin-of-victory + home advantage |
| Kelly (1956) | Optimal bet sizing |
| Shin (1993) | Remove bookmaker margin from odds |
| Rue & Salvesen (2000) | Dynamic team strength estimation |
| arxiv 2410.21484 (2024) | Systematic ML review — current state of the art |

---

## Daily Cron (once pipeline is live)

```bash
# crontab -e
0 6  * * * cd ~/football-predictor && .venv/bin/python -m scripts.daily_run
```
