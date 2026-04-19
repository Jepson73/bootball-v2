# Lessons Learned

Running log of mistakes and patterns to avoid.
Updated after every correction. **Review this at the start of every session.**

---

## Session Start Checklist
- [ ] Review relevant lessons below
- [ ] Check API call budget: `python scripts/db_status.py`
- [ ] Verify DB connection works
- [ ] Review `tasks/todo.md` for active phase

---

## SQLAlchemy / Database

### L001 — Extract data before closing session
SQLAlchemy objects can't be used outside their session context.
Always extract .id, strings, primitives before the `with get_session()` block closes.

### L002 — session.merge() uses PK only, not unique constraints
merge() checks the primary key. For tables with unique constraints on non-PK columns
(e.g. league_id + season + team_id on standings), query first then update or insert.
  WRONG: session.merge(Standing(...))
  RIGHT: query by unique cols → update existing or add new

### L003 — SQLite needs check_same_thread=False
Add connect_args={"check_same_thread": False} for SQLite engines.
Apply conditionally: only when "sqlite" in database_url.

---

## API-Football

### L004 — Cache before every API call
Check local cache before any HTTP request. Never re-fetch already stored data.
75k/day is generous until you backfill 94 leagues x 5 seasons x 6 endpoints.

### L005 — Batch fixture fetching (20 IDs per call)
Use ?ids=id1-id2-... (max 20). 380 PL fixtures = 19 batched calls vs 380 individual.
Always use get_fixtures_batch(). Never loop get_fixtures(fixture_id=x).

### L006 — Odds have a 7-day history limit — capture live or lose forever
Pre-match odds available 1-14 days before match, stored max 7 days.
Live odds disappear permanently after match ends.
There is NO way to backfill historical odds. Must capture in daily pipeline.

### L007 — Daily call counter must be date-aware
Reset counter at midnight, keyed by today's date string.
Not date-keyed = counter blocks calls after first full day.

---

## Python / Dependencies

### L008 — Check imports before building on new code
Verify packages installed (numpy, scipy, sklearn, statsmodels) before writing code.
Use pip install -e ".[dev]" at session start.

### L009 — Use math.factorial, not np.math
NumPy removed the math submodule in newer versions. Use stdlib math directly.

### L010 — pydantic-settings needs explicit env_file config
  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
Without model_config, .env is not loaded automatically.

---

## Modelling

### L011 — 380 matches is not enough
One season of one league gives Brier ~0.87.
Need 50k+ matches across multiple leagues/seasons.
1871 fixtures (7 leagues) → Brier 0.230 — target met.

### L012 — Calibrate with isotonic regression
Isotonic regression outperformed Platt scaling on this dataset.
Brier improvement: 0.307 → 0.230. Apply as post-processing on all model outputs.

### L013 — Dixon-Coles xi=0.01 is optimal time decay
Tested range of xi values. 0.01 balances recency vs sample size.

### L014 — Use time-based train/test split for Dixon-Coles
Random split leaks future data into training for time-series models.
Train on seasons 1-4, test on season 5.

---

## Betting

### L015 — Always use Shin method, never raw implied probs
Raw 1/odd overstates underdog probability due to bookmaker overround structure.
shin_probabilities(odds) is the entry point for all odds-to-prob conversion.

### L016 — Quarter-Kelly default, hard cap 5% bankroll
Kelly assumes perfect prob estimates. Ours have error → overbetting risk.
fractional_kelly(fraction=0.25) + max_stake_pct=0.05 always.

### L017 — BTTS sweet spot: odds 1.85-2.20, edge 10%+
Backtesting confirmed: +37.84 units. Do not chase outside this range.

### L018 — High-scoring leagues for Over 2.5
Bundesliga (62%), Eredivisie (62%), MLS (63%), Swiss Super League (67%).
La Liga / Serie A trend Under 2.5 — use different market for those.

---

## Project Hygiene

### L019 — .env must never be committed
API keys in git = security incident. .gitignore must include .env.
Only .env.example with placeholder values is tracked.

### L020 — data/ directories are gitignored
Raw JSON cache, SQLite DB, processed data never committed. Reproducible from API.

### L021 — Fix bugs incrementally, don't stack changes before testing
Each logical change should be followed by a test run before building on top.

---

## Web UI / Flask

### L022 — Never overwrite app with app.wsgi_app
`app = app.wsgi_app` at module level replaces the Flask app object with its raw WSGI callable.
gunicorn will fail. For gunicorn use `gunicorn --preload "scripts.web_ui:app"` — no reassignment needed.

### L023 — API bet_type must be a string, not an integer
`client.get_odds(fixture_id=x, bet_type=1)` silently returns empty — the API expects "h2h", "btts", "over_under".
Integer bet type IDs are for the `/odds/bets` discovery endpoint only.

### L024 — Odds on every page load burns API budget
50 fixtures × 3 markets = 150 API calls per page view.
Fix: DB-first lookup (`_get_odds_from_db`), only call API if not stored.
Once saved to FixtureOdds table, served from DB forever.

### L025 — Load model once at startup, not lazily per request
`if MODEL is None: load_model()` inside a route is thread-unsafe in multi-worker Flask/gunicorn.
Call `load_model()` once in `main()` before `app.run()`.
For gunicorn: use `--preload` flag so model loads before workers fork.

### L026 — league_id as a numeric ML feature is meaningless
league_id=39 is not "greater than" league_id=78 — it's a nominal category.
Never pass raw IDs as numeric features. Either one-hot encode or exclude.
The feature that matters is team strength within the league, not the league's ID.

---

## Betting Bot / Refactoring

### L027 — Unified model interface prevents duplication
Different models had different return formats (tuples, dataclasses).
Solution: `src/betting/predict.py` provides single `predict_proba(market, home_id, away_id)` entry point.

### L028 — Shin method must be called with list of odds, not single odd
`shin_probabilities([2.0, 3.5, 4.0])` expects all odds at once.
Don't call it per-outcome — it needs the full overround calculation.

### L029 — Test settlement logic with mock data before DB integration
`get_market_result()` was tested with MockFixture objects before connecting to real DB.
This caught edge cases (H2H draws, BTTS clean sheets).

### L030 — Use dataclasses for structured returns
`ValueBetCandidate` provides consistent structure across all markets.
Avoids dict key typos and makes IDE autocompletion work.

### L031 — Cron jobs need date awareness
Settlement job should look back X days, not all time.
Use `--days` parameter to control scope.

### L032 — Dry-run mode for all destructive operations
`daily_run.py --dry-run` and `settle_bets.py --dry-run` let you preview without changes.
Always implement this for betting operations — mistakes cost real money.

