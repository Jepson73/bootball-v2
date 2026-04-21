# Bootball Code Review & Script Analysis Report

## Executive Summary

The bootball project is a football prediction and betting automation system. It consists of **27 Python scripts** in `/scripts/`, supporting modules in `/src/`, and configuration in `/config/`. The system uses ML models (LightGBM) to predict match outcomes and identifies value bets using EV and Kelly criterion.

---

## 1. Script Inventory

### 1.1 Active/Production Scripts

| Script | Purpose | CLI Usage |
|--------|---------|-----------|
| `daily_run.py` | Main daily pipeline: fetch fixtures, generate predictions, find value bets, log to DB | `python scripts/daily_run.py [--dry-run] [--leagues 39,140] [--markets btts,ou25] [--catchup N]` |
| `auto_bet.py` | Automatic betting bot: place fictional bets, settle, track bankroll | `python scripts/auto_bet.py [--bet-only] [--settle-only] [--status] [--history] [--new-round]` |
| `backfill.py` | Historical data loader (legacy) | `python scripts/backfill.py --leagues 39 --seasons 2024 [--include-odds] [--dry-run]` |
| `backfill_all_europe.py` | Comprehensive European league backfill | `python scripts/backfill_all_europe.py --tier 1 [--seasons 2020 2024] [--dry-run]` |
| `backfill_odds.py` | Backfill odds for fixtures missing odds data | `python scripts/backfill_odds.py` |
| `odds_poll.py` | Selective odds polling for fixtures with pending bets | `python scripts/odds_poll.py [--dry-run] [--leagues 39] [--max-fixtures 50]` |
| `settle_fixtures.py` | Settlement: fetch completed fixtures, settle bets, trigger auto_bet | `python scripts/settle_fixtures.py [--dry-run] [--days 7] [--no-auto-bet]` |
| `settle_bets.py` | Settle value bets and predictions (standalone) | `python scripts/settle_bets.py [--dry-run] [--days 7] [--status] [--results] [--predictions]` |
| `live_monitor.py` | Monitor live matches, send in-play Discord alerts | `python scripts/live_monitor.py [--continuous] [--interval 60]` |
| `send_alerts.py` | Send top value bets to Discord | `python scripts/send_alerts.py [--top 10] [--min-ev 0.05] [--dry-run]` |
| `web_ui.py` | Flask web UI (predictions, betting, tracking, admin) | `python scripts/web_ui.py` (runs on port 5000) |
| `maintenance.py` | Daily maintenance checks (API, DB, config) | `python scripts/maintenance.py [--check-api] [--check-db] [--verbose]` |
| `backtest.py` | Historical backtesting across all markets | `python scripts/backtest.py [--market btts] [--ev 0.05] [--kelly 0.25]` |
| `check_model.py` | Model health check and retrain trigger | `python scripts/check_model.py [--force] [--roi-threshold -10]` |
| `setup_db.py` | Create all database tables | `python scripts/setup_db.py` |
| `fetch_player_data.py` | Fetch player injuries for a league | `python scripts/fetch_player_data.py --league 39 [--date YYYY-MM-DD]` |
| `live_stats_collector.py` | Collect live match stats during games | `python scripts/live_stats_collector.py [--continuous] [--interval 30]` |
| `extensive_logging.py` | Debugging trace for model inputs/outputs | `python scripts/extensive_logging.py [--fixture 1392155] [--market h2h] [--all-markets]` |

### 1.2 Training Scripts

| Script | Purpose |
|--------|---------|
| `train_multi_calibrated.py` | Train calibrated GradientBoosting model (all leagues) |
| `train_multi_league.py` | Train uncalibrated GradientBoosting model |
| `evaluate_model.py` | Evaluate Dixon-Coles model on Premier League |
| `evaluate_model_calibrated.py` | Evaluate calibrated Dixon-Coles |

---

## 2. Script Call Graph & Flowchart

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CRON / MANUAL                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
   ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
   │ daily_run.py │        │ settle_      │        │ maintenance  │
   │ (predictions │        │ fixtures.py   │        │ _py          │
   │  + value    │        │              │        │              │
   │  bets)       │        └──────┬───────┘        └──────────────┘
   └──────────────┘               │                            ▲
           │                      ▼                            │
           │            ┌──────────────────┐                  │
           │            │ auto_bet.py      │──────────────────┘
           │            │ (place + settle)  │ (check)
           │            └────────┬─────────┘
           │                     │
           │    ┌────────────────┼────────────────┐
           │    ▼                ▼                ▼
           │ ┌────────┐  ┌────────────┐  ┌──────────────────┐
           │ │send_   │  │ odds_poll  │  │ live_monitor.py │
           │ │alerts  │  │ .py        │  │ (in-play alerts)│
           │ └────────┘  └────────────┘  └──────────────────┘
           │
           ▼
  ┌─────────────────┐      ┌─────────────────┐
  │ backfill_all_   │      │ backfill.py     │
  │ europe.py       │      │ (legacy)        │
  │ (comprehensive  │      │                  │
  │  backfill)       │      └─────────────────┘
  └─────────────────┘
```

### Key Flow Relationships

| Caller | Callee | Via |
|--------|--------|-----|
| `settle_fixtures.py` | `src/settlement.py` | `settle_all()`, `fetch_and_update_fixtures()` |
| `settle_fixtures.py` | `auto_bet.py` | `subprocess.run` (triggered if open bets < 5) |
| `settle_fixtures.py` | `src.betting.round_manager` | `get_active_round_id()` |
| `daily_run.py` | `APIFootballClient` | `src.ingestion.client` |
| `daily_run.py` | `src.betting.ev`, `kelly`, `shin` | EV calculations |
| `daily_run.py` | `src.betting.alerts` | Discord alerts |
| `daily_run.py` | `src.models.calibrator` | Calibration |
| `auto_bet.py` | `src.betting.round_manager` | `get_active_round_id()` |
| `auto_bet.py` | `src.betting.alerts` | Discord alerts |
| `backfill.py` | `src.ingestion.backfill` | `Backfiller` class |
| `backfill_all_europe.py` | `APIFootballClient` | Direct usage |
| `odds_poll.py` | `src.models.calibrator` | `calibrate_prediction()` |
| `send_alerts.py` | `src.alerts.discord` | `discord_alerts.send_bet_alerts()` |
| `web_ui.py` | `src.cache.prediction_cache` | Prediction caching |
| `web_ui.py` | `src.models.calibrator` | `calibrate_prediction()` |
| `maintenance.py` | `config.backfill` | Backfill config validation |
| `maintenance.py` | `src.ingestion.client` | API connectivity check |
| `backtest.py` | `src.betting.predict` | **`MISSING MODULE`** |

---

## 3. Parameter Inputs & Outputs

### `daily_run.py`
- **Inputs**: `--leagues` (csv), `--markets` (csv), `--dry-run`, `--catchup` (days, default 1, max 7)
- **Outputs**: Logs value bets to DB (`PredictionRecord`), sends Discord alerts, creates `ValueBet` records
- **Model path**: `/opt/projects/bootball/data/model_{market}.pkl`

### `auto_bet.py`
- **Inputs**: `--bet-only`, `--settle-only`, `--status`, `--history`, `--new-round`, `--reset`, `--leagues`
- **Outputs**: `PlacedBet` records, `BankrollRound` updates, Discord alerts
- **Key constants**: `INITIAL_BANKROLL=1000`, `EV_THRESHOLD_BET=0.05`, `KELLY_FRACTION=0.25`, `MAX_BETS_PER_DAY=5`

### `backfill_all_europe.py`
- **Inputs**: `--tier` (1/2/3), `--seasons` (default BACKFILL_SEASONS), `--no-odds`, `--dry-run`
- **Outputs**: Fixtures, Teams, Players, FixtureStats, FixtureOdds, Standings to DB
- **API calls**: ~N fixtures + N/20 batch + N stats + N odds + 1 standings per league-season

### `odds_poll.py`
- **Inputs**: `--dry-run`, `--leagues` (csv), `--max-fixtures` (default 50)
- **Outputs**: Updates `FixtureOdds`, recalculates EV in `PredictionRecord`

### `settle_fixtures.py`
- **Inputs**: `--dry-run`, `--days` (default 1), `--no-auto-bet`
- **Outputs**: Updated fixture statuses/scores, settled `PlacedBet`, triggers `auto_bet --bet-only` if open bets < 5
- **Dependencies**: `src/settlement.py` (`fetch_and_update_fixtures`, `settle_all`)

### `backtest.py`
- **Inputs**: `--market`, `--markets`, `--league`, `--seasons`, `--ev`, `--kelly`, `--min-odds`, `--max-odds`
- **Outputs**: Console backtest report with ROI, win rate, Sharpe ratio
- **Bug**: Imports `src.betting.predict` which **does not exist** (see Issues section)

### `live_monitor.py`
- **Inputs**: `--continuous`, `--interval` (default 60s)
- **Outputs**: Discord alerts for in-play opportunities
- **Bug**: Imports `src.betting.predict` which **does not exist** (see Issues section)

### `send_alerts.py`
- **Inputs**: `--top` (default 5), `--min-ev` (default 0.05), `--dry-run`
- **Outputs**: Discord webhook messages
- **Note**: Uses `src.alerts.discord` correctly

### `maintenance.py`
- **Inputs**: `--check-api`, `--check-db`, `--check-config`, `--check-runs`, `--verbose`
- **Outputs**: Console health report
- **Checks**: API connectivity, DB integrity, backfill config, log file freshness

---

## 4. Issues & Malfunctions

### CRITICAL: Missing `src.betting.predict` Module

Two scripts import a non-existent module:

```python
# scripts/backtest.py:31
from src.betting.predict import predict_proba, MARKET_OUTCOMES

# scripts/live_monitor.py:31
from src.betting.predict import predict_proba
```

**File does not exist at `src/betting/predict.py`**. However, an old version exists at `old_shit_dont_use/predict.py` which contains the implementation.

**Impact**:
- `backtest.py` **will fail** on import
- `live_monitor.py` **will fail** on import

**Fix**: Either restore `src/betting/predict.py` from the old version, or redirect imports to `old_shit_dont_use/predict.py` (not recommended for production).

---

### CRITICAL: `old_web_ui.py` Imports Non-Existent `src.betting.value_bets`

```python
# scripts/old_web_ui.py:246
from src.betting.value_bets import find_all_market_value_bets
```

**File `src/betting/value_bets.py` does not exist.** This makes `old_web_ui.py` non-functional.

---

### HIGH: `src.alerts.__init__.py` Has Typo in `__all__`

```python
# src/alerts/__init__.py:6
__all__ = ["DiscordAlerts", "discord_alerts", "create_bet_alert", "BetAlert"]
#                                                          ^ typo: extra quote
```

Should be `"create_bet_alert"` not `"create_bet_alert"`.

---

### HIGH: `settlement.py` Line 102 References Undefined Variable

```python
# src/settlement.py:102
elif existing.status == "FTm" and status_short == "FT":
    existing.status = "FT"
```

`status_short` is **not defined** in this scope. The outer `if` block has it, but the `elif` is at the same indentation level and `status_short` would be out of scope. The `elif` block also references `status_short` from line 166 in `_fetch_stale_fixtures` but it's not passed/defined here.

---

### MEDIUM: `web_ui.py` Has Duplicate `_get_model_prediction` Logic

The web UI scripts (`web_ui.py`, `web_ui_3.py`, `web_ui_d.py`, `web_ui_backup.py`) all contain nearly identical `_get_model_prediction` functions (100+ lines each). This is copy-paste code.

---

### MEDIUM: `settlement.py` Line 303 Has Invalid Escape Sequence

```python
# src/settlement.py:303
msg += "─────────────────────n"  # Should be \n
```

There's a typo: `──n"` instead of `──\n"`. This won't cause a crash but the message formatting in Discord will be broken.

---

### MEDIUM: `old_web_ui.py` Uses `old_shit_dont_use/value_bets.py`

`old_web_ui.py` is a legacy simplified UI that:
1. Imports non-existent `src.betting.value_bets`
2. Uses a different betting flow (simpler version)
3. Has its own `BETTING_MARKETS = ['btts', 'ou25']` which is limited vs current `'h2h', 'btts', 'ou25', 'ou15'`

---

### LOW: `backtest.py` Uses Simulated Odds

The `get_odds_for_market_from_dict` function (line 152) generates **simulated odds** using random variance around league base rates, not real bookmaker odds. This means backtest results may not reflect real-world performance.

---

### LOW: Duplicate Model Loading Pattern

All scripts that load pickle models (`daily_run.py`, `auto_bet.py`, `web_ui.py`, etc.) have nearly identical model loading code:

```python
with open(model_path, 'rb') as f:
    obj = pickle.load(f)
if isinstance(obj, dict):
    model = obj['model']
    calibrator = obj.get('calibrator')
else:
    model = obj
    calibrator = None
```

This could be centralized into `src.betting.model_loader` or similar.

---

## 5. Unused / Dead Scripts

| Script | Status | Notes |
|--------|--------|-------|
| `old_web_ui.py` | **UNUSED** | Imports non-existent module, superseded by `web_ui.py` |
| `web_ui_3.py` | **UNUSED** | Variant of web_ui.py, not actively used |
| `web_ui_d.py` | **UNUSED** | Variant of web_ui.py, not actively used |
| `web_ui_backup.py` | **UNUSED** | Backup of web_ui.py, not actively used |
| `backfill.py` | **DEPRECATED** | Legacy backfill, use `backfill_all_europe.py` |
| `train_multi_calibrated.py` | **AD-HOC** | Training script, run manually |
| `train_multi_league.py` | **AD-HOC** | Training script, run manually |
| `evaluate_model.py` | **AD-HOC** | Evaluation script, run manually |
| `evaluate_model_calibrated.py` | **AD-HOC** | Evaluation script, run manually |
| `check_model.py` | **PARTIALLY USED** | `retrain_model()` is a stub (just logs) |

---

## 6. Cron Schedule (Recommended)

Based on the workflow.md and script analysis:

```bash
# crontab -e

# Daily pipeline (predictions + value bets) - early morning
0 6 * * * cd /opt/projects/bootball && .venv/bin/python scripts/daily_run.py >> logs/daily_run.log 2>&1

# Settlement (fetch results, settle bets) - after matches
30 22 * * * cd /opt/projects/bootball && .venv/bin/python scripts/settle_fixtures.py >> logs/settle.log 2>&1

# Odds polling (refresh odds for pending bets)
0 */4 * * * cd /opt/projects/bootball && .venv/bin/python scripts/odds_poll.py >> logs/odds_poll.log 2>&1

# Maintenance check (daily)
0 7 * * * cd /opt/projects/bootball && .venv/bin/python scripts/maintenance.py >> logs/maintenance.log 2>&1

# Backfill (weekly, low API usage time)
0 3 * * 0 cd /opt/projects/bootball && .venv/bin/python scripts/backfill_all_europe.py --tier 1 --seasons 2024 >> logs/backfill.log 2>&1
```

---

## 7. Summary Table

| Category | Count |
|----------|-------|
| Total scripts | 27 |
| Production scripts | 18 |
| Ad-hoc/training scripts | 5 |
| Unused/duplicate scripts | 8 |
| **Critical bugs** | 2 |
| **High severity** | 3 |
| **Medium severity** | 4 |
| **Low severity** | 4 |

---

## 8. Recommendations

1. **Fix `src.betting.predict`** - Create the module (restore from `old_shit_dont_use/predict.py`) or update imports in `backtest.py` and `live_monitor.py`

2. **Fix `src.betting.value_bets`** - Either create `src/betting/value_bets.py` or remove `old_web_ui.py`

3. **Fix typo in `src/alerts/__init__.py`** - Remove extra quote in `__all__`

4. **Fix `src/settlement.py` line 102** - Pass `status_short` to the elif block or restructure

5. **Consolidate web_ui variants** - Keep only `web_ui.py`, delete `web_ui_3.py`, `web_ui_d.py`, `web_ui_backup.py`

6. **Centralize model loading** - Extract duplicate pickle loading to `src.betting.model_loader`

7. **Implement `retrain_model()`** in `check_model.py` or remove the stub

8. **Clean up deprecated scripts** - Move `backfill.py` to `old_shit_dont_use/` and update documentation

9. **Fix `settlement.py` line 303** - Change `──n"` to `──\n"`
