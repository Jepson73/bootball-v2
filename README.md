# Bootball

Autonomous football betting intelligence platform. Generates calibrated match predictions across four markets, allocates capital using a portfolio engine, tracks accuracy, and improves through a closed-loop feedback cycle.

---

## What It Does

1. **Ingests** fixtures, results, odds, and standings from the API-Football (api-sports.io) data feed across 1,225+ leagues
2. **Predicts** outcomes for h2h (1X2), btts, ou25, and ou15 markets using per-market GradientBoosting classifiers calibrated with Platt scaling
3. **Allocates** capital across predictions using Markowitz portfolio optimisation + fractional Kelly sizing
4. **Enforces** risk policy constraints (correlation limits, exposure concentration) before any bet is placed
5. **Settles** results automatically when matches finish, updates model accuracy metrics
6. **Improves** over time through league-specific calibration that activates once enough settled data exists per (market, league) pair

Everything runs continuously via a scheduler. The web UI at port 5000 provides full visibility and control.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — minimum required: API_FOOTBALL_KEY, BOOTBALL_PASSWORD, DISCORD_WEBHOOK_URL

# Run database migrations
python scripts/migrate.py

# Start
python scripts/web_ui.py
# → http://localhost:5000
```

---

## Architecture in One Diagram

```
api-sports.io
     │
     ▼
┌────────────────────────────────────────────────────────────────┐
│  APScheduler (embedded in web_ui.py)                           │
│                                                                  │
│  fetch_fixtures (6h) ─── fetch_odds (2h) ─── fetch_results (1h)│
│         │                      │                     │          │
│         └──────────────────────┴─────────────────────┘          │
│                                │                                 │
│                                ▼                                 │
│                    AgentCoordinator cycle                        │
│                                │                                 │
│         ┌──────────────────────┼──────────────────────┐         │
│         ▼                      ▼                      ▼         │
│  PredictionService      PortfolioEngine        PolicyEngine     │
│  (4 markets × N        (Markowitz + Kelly)    (constraints)     │
│   fixtures)                    │                      │         │
│         │                      └──────────────────────┘         │
│         │                                │                       │
│         ▼                                ▼                       │
│   prediction_records              placed_bets                   │
│                                          │                       │
│                               settlement (auto)                  │
│                                          │                       │
│                               league calibration                 │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
Flask web UI — Predictions / Betting / Tracking / Admin / Runs
```

---

## Configuration

All settings are read from `.env` via `config/settings.py` (pydantic-settings). No hardcoded values.

| Variable | Required | Description |
|----------|----------|-------------|
| `API_FOOTBALL_KEY` | Yes | api-sports.io key (x-apisports-key header) |
| `BOOTBALL_PASSWORD` | Yes | Web UI login password |
| `DISCORD_WEBHOOK_URL` | Recommended | Discord channel webhook for notifications |
| `RUNTIME_MODE` | No | `dev` (default) / `training` / `live` / `live_eval` |
| `DATABASE_URL` | No | Default: `sqlite:///data/football.db` |
| `BOT_MIN_EV` | No | Minimum EV threshold for bet selection (default: 0.05) |
| `BOT_MAX_STAKE` | No | Max single bet stake in SEK (default: 50.0) |
| `FETCH_FIXTURES_INTERVAL_HOURS` | No | Default: 6 |
| `FETCH_RESULTS_INTERVAL_HOURS` | No | Default: 1 |
| `FETCH_ODDS_INTERVAL_HOURS` | No | Default: 2 |
| `TIMEZONE` | No | Display timezone (default: `Europe/Stockholm`) |

---

## Runtime Modes

Controlled via `/settings/system` in the UI or `RUNTIME_MODE` env var.

| Mode | Betting | Retraining | Predictions | Use When |
|------|---------|-----------|-------------|----------|
| `dev` | ✅ | ✅ | ✅ | Default — full pipeline, data collection phase |
| `training` | ❌ | ✅ | ✅ | Force retrain cycle without live betting |
| `live` | ✅ | ❌ | ✅ | Production — models frozen, strict policies |
| `live_eval` | ❌ | ❌ | ✅ | Evaluation snapshot — measure frozen model accuracy |

---

## Markets

| Market | Picks | Model Features |
|--------|-------|----------------|
| `h2h` | 1 (home win), X (draw), 2 (away win) | Rank, goal difference, attack vs defence matchup |
| `btts` | Yes, No | Attack correlation, defensive weakness ratios |
| `ou25` | Over, Under | Expected total goals, variance proxy, league baseline |
| `ou15` | Over, Under | Same as ou25 with 1.5 threshold |

Features are computed from `standings` table (rank, goals_for, goals_against) and normalised against per-league baselines where available.

---

## Backfilling Historical Data

Historical match data is needed to train the models. A cron job runs nightly at 4am to continue filling in past seasons:

```bash
# Manual backfill (will stop at 15,000 API calls remaining)
python scripts/backfill_cron.py

# Or backfill a specific league/season directly
python scripts/backfill_all.py --seasons 2023 2022 --stop-at-remaining 15000
```

The 4am cron covers all 1,225 configured leagues for seasons 2025–2020, newest first.

--- 

## Web UI Pages

| URL | Purpose |
|-----|---------|
| `/` | Home — navigation |
| `/predictions` | Live predictions with EV, market/league filters |
| `/betting` | Bankroll, pending bets, round history |
| `/tracking` | Prediction accuracy, calibration, win rate |
| `/admin` | Model training, system status, manual settlement |
| `/runs` | Experiment run explorer |
| `/runs/health` | Pipeline health dashboard (auto-refreshes) |
| `/settings/system` | Runtime mode control |
| `/settings/governance` | Layer attribution and ablation analysis |
| `/settings/architecture` | Architecture evolution proposals |

See `docs/operator_manual.md` for a full walkthrough of every control.

---

## Model Security

Trained models are saved with HMAC-SHA256 signatures using `src/security/safe_load.py`. Loading a model that was saved without the signature (e.g. plain `pickle.dump`) will fail. Always use `safe_model_save` / `safe_model_load`.

---

## Key Files

| Path | Purpose |
|------|---------|
| `scripts/web_ui.py` | Flask app + embedded scheduler — the single entry point |
| `src/agents/coordinator.py` | Multi-agent pipeline: predictions → portfolio → execution |
| `src/betting/prediction.py` | Feature building and model inference |
| `src/models/trainer.py` | Model training (GradientBoostingClassifier) |
| `src/betting/portfolio/portfolio_engine.py` | Markowitz + Kelly capital allocation |
| `src/governance/policy_engine.py` | Risk constraints (correlation, concentration) |
| `src/settlement.py` | Result fetching, bet settlement, score backfill |
| `src/betting/league_normalizer.py` | Per-league baseline statistics |
| `backend/runtime_mode.py` | Mode enforcement singleton |
| `backend/scheduler.py` | APScheduler job definitions |
| `config/settings.py` | All configuration via pydantic-settings |
| `config/leagues.py` | ALL_LEAGUE_IDS (1,225 leagues) |
| `scripts/backfill_cron.py` | Nightly historical data backfill |

---

## Further Reading

- `docs/operator_manual.md` — full UI walkthrough, Discord notification guide, when-to-intervene guide
- `docs/technical.md` — architecture deep-dive, schema reference, component internals
- `docs/user_manual.md` — original architecture decision record
