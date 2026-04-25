# AGENTS.md — Bootball: Football Prediction Platform

## Agent Operating Mode

You are a cautious senior backend engineer on a solo project.
The betting bot uses **simulated/fake money only** — this is an ML challenge and entertainment,
not a real financial system. There are no real stakes, bookmaker accounts, or live money flows.

You must:
- Prioritize correctness and data integrity over speed
- Avoid irreversible actions (especially DB mutations)
- Ask before making impactful or destructive changes
- Prefer read-only inspection over mutation when uncertain

---

## Project Overview

**Bootball** — a full-stack football prediction and simulated betting intelligence platform.

Core loop:
1. Ingest match data, odds, lineups, standings from api-football
2. Engineer features and train calibrated ML ensemble models
3. Find positive expected value (EV) bets vs bookmaker implied probabilities
4. Simulate bet placement using Kelly criterion sizing (fake money)
5. Track performance, ROI, calibration drift, and model health in a Flask frontend

Solo project on an LXC container, accessible via WireGuard VPN tunnel.

**CURRENT STATE**: Single Flask app in `scripts/web_ui.py` (~3000 lines) with ALL routes directly on app. No Blueprints, APScheduler IMPLEMENTED, no React frontend. Flask + scheduler run via `backend/app.py`.

---

## Tech Stack

| Layer        | Technology                                       |
|--------------|--------------------------------------------------|
| Backend      | Python 3, Flask, NO Blueprints (all in web_ui.py)|
| Database     | SQLite3 (WAL mode)                               |
| ML           | XGBoost, LightGBM, scikit-learn                |
| Scheduler    | APScheduler (SQLAlchemyJobStore → SQLite)    |
| Frontend     | Flask server-side templates (NOT React)          |
| External API | api-football (RapidAPI) — only data source needed|
| Infra        | LXC container, WireGuard tunnel                  |

---

## Project Structure

```
/
├── backend/
│   ├── config.py               # Config class, loads .env
│   ├── db/
│   │   └── connection.py      # get_session(), init_db(), sets WAL + FK pragmas (src/storage/)
│   └── migrations/            # Numbered SQL scripts: 001_*.sql, 002_*.sql ... (NOT IMPLEMENTED)
├── src/
│   ├── storage/
│   │   ├── db.py              # get_session(), init_db() — DB connection
│   │   └── models.py           # SQLAlchemy ORM models
│   ├── ingestion/
│   │   └── client.py           # API-Football client
│   ├── betting/
│   │   ├── prediction.py      # get_model_prediction()
│   │   ├── ev.py              # expected_value()
│   │   ├── kelly.py           # fractional_kelly()
│   │   └── alerts.py          # BettingAlerts
│   ├── models/
│   │   ├── calibrator.py     # calibrate_prediction()
│   │   └── model_tracker.py   # ModelTracker
│   └── features/              # Feature engineering (NOT FULLY IMPLEMENTED)
├── scripts/                    # MAIN ENTRY POINTS
│   ├── web_ui.py              # Flask app (~3000 lines) — ALL routes here, NO Blueprints
│   ├── daily_run.py           # Fetch fixtures + run predictions
│   ├── auto_bet.py             # Betting bot (fake money)
│   ├── odds_poll.py           # Poll odds every 30min
│   ├── settle_fixtures.py    # Settle predictions after match
│   ├── live_monitor.py        # Monitor live matches
│   └── setup_db.py            # Create tables
├── data/
│   ├── football.db           # PRIMARY DATABASE — EXISTS
│   └── models/               # Trained model .pkl files
│       ├── model_h2h.pkl
│       ├── model_btts.pkl
│       └── model_ou25.pkl
├── frontend/                   # NOT IMPLEMENTED - server-side Flask templates only
├── docs/research/              # Research documents — read-only reference
├── logs/
├── tests/
├── schema.sql                  # Canonical schema — source of truth
├── .env                        # NEVER commit
├── .env.example
├── opencode.json
├── AGENTS.md
├── requirements.txt
├── pytest.ini
├── Makefile
└── nginx.conf
```

---

## Database

- **Engine:** SQLite3, WAL mode + `PRAGMA foreign_keys=ON` set on every connection
- **Primary DB:** `data/football.db` — already contains ~50 leagues, 5 seasons of data
- **Scheduler DB:** `data/scheduler.db` — APScheduler job store, separate file
- **Schema source of truth:** `backend/db/schema.sql`
- **Access:** always via `get_db()` from `backend/db/connection.py`

### Rules
- Never ALTER, DROP, TRUNCATE, or mass DELETE directly
- All schema changes need a numbered migration in `/migrations/` AND an update to `schema.sql`

### Table Reference

**Reference data**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `leagues` | id, name, country, tier, flag | api-football league id as PK |
| `teams` | id, name, code, country, logo_url, flag | api-football team id as PK |

**Fixtures & match data**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `fixtures` | id, league_id, season, home_team_id, away_team_id, date, status, goals_home/away, ht_goals, outcome | outcome = H/D/A |
| `fixture_stats` | fixture_id (unique), shots, possession, xG, passes, cards, corners | post-match only |
| `fixture_events` | fixture_id, minute, team_id, player_name, event_type, detail | raw feed |
| `match_events` | fixture_id, type, minute, team, is_home | deduplicated live events |
| `live_match_stats` | fixture_id, minute, goals, shots, possession, cards, xg, momentum_10min, period | unique on (fixture_id, minute) |

**Standings & ratings**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `standings` | league_id, season, team_id, rank, points, played, won, drawn, lost, goals_for/against | unique on (league_id, season, team_id) |
| `elo_ratings` | team_id, as_of_date, rating, games_played | unique on (team_id, as_of_date) |

**Players & injuries**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `players` | id, team_id, name, position, goals, assists, yellow_cards, red_cards, minutes_played | season stats |
| `injuries` | player_id, player_name, fixture_id, team_id, type, status, start_date, end_date | |

**Odds**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `fixture_odds` | fixture_id, bookmaker, bet_type, odd_home/draw/away/over/under/btts_yes/btts_no/over15/under15 | structured |
| `bookmaker_odds` | fixture_id, bookmaker, bet_type, odds_json | raw JSON blob from api-football |

**ML — Models & versioning**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `model_versions` | market, version_number, brier_score, accuracy, ece, features_used, is_active, model_type | unique on (market, version_number) |
| `retrain_events` | market, old/new_version_id, reason, brier_before/after, triggered_by_drift, drift_score | audit log |
| `model_drift` | market, period_start/end, accuracy_pct, drift_score, retrain_recommended | rolling drift detection |
| `model_calibration` | market, period_start/end, brier_score, ece, reliability_diagram (JSON), retrain_recommended | calibration history |

**Predictions**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `predictions_archive` | fixture_id, model_name, prob_home/draw/away, predicted goals | unique on (fixture_id, model_name) |
| `prediction_records` | fixture_id, market, model_name, model_version_id, predicted_outcome, our_prob, calibrated_prob, implied_prob, ev, edge, odds_decimal, bookmaker, sweet_spot, settled, won | unique on (fixture_id, market) — primary table |

**Bankroll & bets (fake money)**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `bankroll_rounds` | round_number, initial_bankroll, ending_balance, total_bets/wins/staked/pnl, roi_pct, is_active | unique on round_number |
| `bankroll` | date, balance, total_staked/won/lost, bet_count, win_count, round_id | daily snapshot |
| `placed_bets` | round_id, fixture_id, market, outcome, stake, odds, our_prob, ev, kelly_fraction, settled, won, pnl, model_version_id | unique on (fixture_id, market, outcome, round_id) |
| `value_bets_archive` | fixture_id, model_name, market, outcome, our_prob, bookmaker_odd, implied_prob, ev, kelly_fraction, recommended_stake, won, pnl, settled | historical EV bet log |

**User / UI state**
| Table | Key columns | Notes |
|-------|-------------|-------|
| `user_preferences` | user_id, timezone (default: Europe/Stockholm), preferred_markets/leagues, alerts_enabled, alerts_min_ev, alerts_top_n | unique on user_id |
| `watched_fixtures` | user_id, fixture_id, market, selection_type, status, notes | unique on (user_id, fixture_id, market) |

---

## Background Scheduling (APScheduler)

**CURRENT STATE**: IMPLEMENTED. APScheduler runs jobs automatically on Flask startup.

APScheduler replaces cron. SQLAlchemyJobStore persists jobs in `data/scheduler.db`.
All jobs use `coalesce=True` and `max_instances=1`.

**Files:**
- `backend/scheduler.py` - Job definitions (import scripts and call their main())
- `backend/app.py` - Flask app that starts scheduler on `create_app()`
- `data/scheduler.db` - SQLite job store (auto-created on first run)

**To run:** `python backend/app.py`

| Job ID | Schedule | Description |
|--------|----------|-------------|
| `fetch_fixtures` | Every 6h | Pull upcoming fixtures, upsert changes |
| `fetch_results` | Every 1h | Update finished match scores and outcomes |
| `fetch_odds` | Every 2h | Pull latest odds into fixture_odds + bookmaker_odds |
| `run_predictions` | Daily 03:00 | Run ML inference, write to prediction_records |
| `retrain_models` | Weekly Mon 04:00 | Full retrain, write model_versions + retrain_events |
| `run_betting_bot` | Every 30min | Evaluate value bets, write to placed_bets (fake money) |

---

## ⚡ ML Strategy — Read This Before Touching Any ML Code

### The Core Insight: Calibration > Accuracy

**This is the most important principle in the entire project.**

Research finding (Walsh & Joshi, 2024):
- Models optimized for **calibration**: +34.69% ROI
- Models optimized for **accuracy**: -35.17% ROI

We do NOT optimize for classification accuracy. We optimize for calibrated probability estimates.
A model that says 60% and wins 60% of the time is worth more than one that says 80% and wins 55%.

**Never change the loss function or evaluation metric to accuracy-based without explicit discussion.**

### The EV Formula

```
EV = (our_calibrated_prob × decimal_odds) - 1
```

A bet is only placed when EV > `BOT_MIN_EV` (default 0.05 = +5% edge).

### Kelly Criterion Sizing

```
kelly_fraction = (b × p - q) / b

Where:
  p = our_calibrated_prob
  q = 1 - p
  b = decimal_odds - 1

Actual stake = kelly_fraction × 0.25 × current_bankroll  # fractional Kelly (25%)
```

Never use full Kelly. Always fractional (25%) to survive variance.

### Proxy xG Formula

We derive xG from api-football shot data (no Understat needed):

```python
proxy_xg = shots * 0.12 + shots_on_target * 0.25
# Correlates ~0.85-0.90 with true xG. Sufficient for modeling.
```

This is stored in `fixture_stats.home_xg` / `fixture_stats.away_xg`.

### Model Architecture

| Model | Market | Purpose |
|-------|--------|---------|
| XGBoost | h2h (home/draw/away) | Match outcome probabilities |
| LightGBM | over/under 2.5, btts | Goals-based market probabilities |
| LogisticRegression | All markets | Isotonic calibration layer on top |

The calibration layer is critical — raw XGBoost/LightGBM outputs are not used directly for EV.
Always run calibration after training. Store calibrated_prob in `prediction_records`.

### Feature Engineering (features.py)

Key features, in rough priority order:
1. **ELO ratings** — from `elo_ratings` table, as_of_date before kickoff
2. **Form (last 5)** — derived from recent `fixtures` outcomes
3. **Home/away stats** — goals for/against split by venue from `standings`
4. **H2H** — via api-football `/fixtures/head2head` endpoint, stored in fixtures
5. **Proxy xG** — from `fixture_stats` (shots * 0.12 + sog * 0.25)
6. **Odds movement** — diff between earliest and latest `fixture_odds` fetch
7. **Injuries** — key player availability from `injuries` table
8. **Lineup** — when available from api-football lineups endpoint
9. **Goalkeeper availability** — GK absence is a strong predictor (Peters & Pacheco, 2022)
10. **League tier** — from `leagues.tier` as strength proxy

### Edge Strategies Implemented (or planned)

1. **Calibration edge** — isotonic regression on model output, track ECE in `model_calibration`
2. **Decorrelation** — compare our_prob vs implied_prob; edge = when they diverge significantly
3. **Distribution forecasting** — use proxy xG distributions, not just outcome prediction
4. **Lineup edge** — flag matches where confirmed lineups differ from expected (esp. GK changes)
5. **Odds movement** — poll `fixture_odds` over time; movement toward us = confidence signal

### sweet_spot Flag

`prediction_records.sweet_spot = 1` when:
- EV > threshold AND
- odds_decimal > 3.5 (long odds, where calibration edge is most profitable per research)
- Research shows long-odds value beats short-favorite betting even at similar EV

### Model Lifecycle

1. Train via `backend/ml/train.py`
2. Calibrate via `backend/ml/calibrate.py`
3. Evaluate via `backend/ml/evaluate.py` — compare Brier score and ECE vs current active model
4. Only save if metrics improve
5. Write new row to `model_versions` (is_active=True, old version is_active=False)
6. Write row to `retrain_events`
7. Update `model_calibration` with new calibration curve

**Never overwrite a model file without metric comparison. Never skip the calibration step.**

### Drift Detection

`model_drift` tracks rolling accuracy vs expected wins per market.
When `drift_score` exceeds threshold or `retrain_recommended = 1`, the scheduler triggers retrain.
The Tracking page visualizes this. The Admin page allows manual retrain trigger.

---

## Data Source Philosophy

**api-football is the only required data source.** Do not add external scrapers or APIs
without explicit discussion. Reasons:

- Shots + shots_on_target from api-football = sufficient proxy xG (r ≈ 0.87)
- 3 seasons of historical data is sufficient (research shows diminishing returns beyond 3-5)
- api-football provides lineups, standings, H2H, injuries, odds — everything needed
- Adding Understat, FBref, etc. adds maintenance burden without proven model improvement

Exception: Reddit/Pushshift sentiment could be added if social sentiment edge is pursued.

### api-football Quota Protection

The api-football key has a monthly call limit. This is a real constraint.
- Never add ad-hoc API calls outside scheduled ingestion jobs
- Never call api-football endpoints in test code against the live key
- Use a sandbox/mock in tests
- The `ingestion_log` table tracks all API calls and record counts

---

## Frontend Pages

| Page | Route | Key Data |
|------|-------|----------|
| Predictions | `/predictions` | prediction_records with EV, calibrated_prob, sweet_spot flags |
| Betting | `/betting` | placed_bets, bankroll_rounds, bot status |
| Tracking | `/tracking` | ROI curve, calibration chart (reliability diagram), drift alerts |
| Admin | `/admin` | Scheduler job control, model_versions table, manual retrain |
| Debug | `/debug` | Raw DB queries, ingestion_log, APScheduler job status, error log tail |

The **Tracking page** calibration chart uses `model_calibration.reliability_diagram` (JSON blob)
to render a reliability diagram — predicted probability buckets vs actual win rates.

---

## API & Secrets

All secrets in `.env`, never hardcoded. See `.env.example` for required vars.

Key vars: `API_FOOTBALL_KEY`, `SECRET_KEY`, `BOT_ENABLED` (default: false),
`BOT_MAX_STAKE`, `BOT_MIN_EV`, `DATABASE_PATH` (→ data/football.db), `SCHEDULER_DB_PATH`

---

## 🚨 Critical Rules

### 🔴 Never Without Explicit Confirmation
- Do not trigger `run_betting_bot` or any bet placement as a side effect
- Do not call api-football endpoints outside scheduled ingestion jobs (quota risk)
- Do not overwrite model files without metric comparison and calibration step
- Do not change the ML optimization target from calibration to accuracy

### 🛑 Database Safety
- Never DROP, TRUNCATE, or mass DELETE any table
- Never write raw SQL against `data/football.db` in ad-hoc scripts
- Schema changes require: (1) migration in `/migrations/`, (2) update to `schema.sql`
- Always use `get_db()` — never open a raw sqlite3 connection

### 🔐 Security
- Never log or print env var values
- Never hardcode any key, token, or path
- All config via `config.py` which reads from `.env`

### ⚙️ Architecture
- Flask Blueprints only — no routes registered directly on `app` (NOT CURRENTLY IMPLEMENTED - all routes in web_ui.py)
- APScheduler only for scheduling — no threads, no cron, no subprocess (IMPLEMENTED in backend/scheduler.py)
- Background jobs defined exclusively in `backend/scheduler.py` (calls scripts/*.py main() functions)

### 🤖 ML Safety
- Calibration is not optional — it is the core of the edge strategy
- Always write to `model_versions` and `retrain_events` on retrain
- Always write to `model_calibration` after calibration step
- sweet_spot flag logic lives in `predict.py` — keep it consistent with `value_bets_archive`

### 🧪 When Uncertain
- Use Debug API endpoints to inspect state rather than querying DB directly
- Prefer read-only actions
- Ask before any write that affects more than one table
