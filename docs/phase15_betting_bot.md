# Phase 15: Betting Bot - Prediction Feedback Loop

## Overview

Automated system that simulates fictional bets to test predictions against real results,
creating a feedback loop for model improvement.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DAILY PIPELINE                            │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch Fixtures  →  2. Predict  →  3. Find Value  →  4. Log │
│                                                                  │
│  python daily_run.py                                            │
│     ↓                                                           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Predict.prob │ →  │ Value_bets   │ →  │ ValueBets table │  │
│  │ (all markets)│    │ (Shin + Kelly)│    │ (unsettled)     │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     SETTLEMENT JOB                               │
├─────────────────────────────────────────────────────────────────┤
│  1. Check Finished  →  2. Settle  →  3. Update Bankroll         │
│                                                                  │
│  python settle_bets.py                                          │
│     ↓                                                           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Fixture (FT) │ →  │ Calculate P/L│ →  │ SettledBets     │  │
│  │              │    │ Won/Lost     │    │ Bankroll table  │  │
│  └──────────────┘    └──────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     FEEDBACK LOOP                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │ Settled Bets │ →  │ ROI Analysis │ →  │ Model Retrain   │   │
│  │ P/L by market│    │ by league    │    │ Trigger if ROI  │   │
│  └──────────────┘    └──────────────┘    │ drops           │   │
│        ↓                   ↓                   ↓             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              PERFORMANCE DASHBOARD                       │   │
│  │  • Total P/L    • Win Rate    • ROI %    • By Market   │   │
│  │  • By League    • By Model    • Drawdown               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files

### Core Modules

| File | Purpose |
|------|---------|
| `src/betting/predict.py` | Unified prediction interface for all markets |
| `src/betting/value_bets.py` | Multi-market value bet detection |
| `src/betting/__init__.py` | Module exports |

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/daily_run.py` | Daily pipeline (predict → find value → log) |
| `scripts/settle_bets.py` | Settlement job (settle bets → update bankroll) |

### Database Tables

| Table | Purpose |
|-------|---------|
| `ValueBet` | Unsettled bet records (from daily_run) |
| `SettledBet` | Historical settled bets (for analysis) |
| `Bankroll` | Daily bankroll snapshots |

---

## Usage

### Daily Pipeline

```bash
# Full run (all leagues, all markets)
python scripts/daily_run.py

# Preview only
python scripts/daily_run.py --dry-run

# Specific leagues
python scripts/daily_run.py --leagues 39,140

# Specific markets
python scripts/daily_run.py --markets btts,ou25

# Combine
python scripts/daily_run.py --leagues 39,78 --markets btts --dry-run
```

### Settlement

```bash
# Settle completed bets (last 7 days)
python scripts/settle_bets.py

# Preview without changes
python scripts/settle_bets.py --dry-run

# Check longer history
python scripts/settle_bets.py --days 30

# Show results
python scripts/settle_bets.py --results
python scripts/settle_bets.py --results --results-days 60

# Show bankroll
python scripts/settle_bets.py --status
```

### Cron Setup

```bash
# crontab -e

# Daily pipeline at 6 AM
0 6 * * * cd ~/bootball && .venv/bin/python scripts/daily_run.py

# Settlement check every hour (for quick settling)
0 * * * * cd ~/bootball && .venv/bin/python scripts/settle_bets.py --days 1

# Weekly results report
0 8 * * 1 cd ~/bootball && .venv/bin/python scripts/settle_bets.py --results
```

---

## Supported Markets

| Market | ID | Outcomes |
|--------|-----|----------|
| Match Winner (1X2) | h2h | 1, X, 2 |
| Both Teams To Score | btts | Yes, No |
| Over/Under 2.5 | ou25 | Over, Under |
| Over/Under 1.5 | ou15 | Over, Under |

---

## Metrics Tracked

### Per Bet
- Market, Outcome, Our Probability
- Bookmaker Odds, Implied Probability (Shin-adjusted)
- Expected Value (EV)
- Kelly Fraction, Recommended Stake
- Actual Result, Won/Lost, P/L

### Aggregated
- Total P/L
- Win Rate by Market
- ROI % (Profit / Staked)
- By League
- By Market
- Drawdown

---

## Next Steps

1. **Backtesting Script** - Run historical simulation
2. **Model Retrain Trigger** - Auto-retrain if ROI drops below threshold
3. **Telegram/Slack Alerts** - Notify on high-value bets
4. **Web UI Integration** - Show bankroll in dashboard

---

## Phase 15a (COMPLETED) - Refactor Foundation

- [x] Unified prediction interface
- [x] Multi-market value bet detection
- [x] Bankroll tracking tables
- [x] Settlement job
- [x] Refactored daily_run.py

## Phase 15b (COMPLETED) - Betting Bot Core

- [x] Backtesting script (`scripts/backtest.py`)
- [x] Model retrain trigger (`scripts/check_model.py`)
- [x] Alert system (`src/betting/alerts.py`)
- [x] Web UI bankroll display (`/api/bankroll`)

### Phase 15b Scripts

| Script | Purpose |
|--------|---------|
| `scripts/backtest.py` | Historical ROI simulation |
| `scripts/check_model.py` | Model health & retrain trigger |
| `src/betting/alerts.py` | Telegram/Slack notifications |

### Phase 15b Usage

```bash
# Backtesting
python scripts/backtest.py --market btts --ev 0.05
python scripts/backtest.py --league 78 --kelly 0.25

# Model health check
python scripts/check_model.py --roi-threshold -10
```

### Alerts Setup (.env)

**Discord (recommended):**
1. Create Discord server/channel
2. Edit Channel → Integrations → Webhooks → New Webhook
3. Copy webhook URL
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your/webhook
```

**Telegram:**
```
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
```

**Slack:**
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
```

To use Discord alerts, import and configure:
```python
from src.betting.alerts import BettingAlerts
alerts = BettingAlerts(channels=["discord"])
```

## Phase 15c (TODO) - Advanced Features

- [ ] Kelly criterion optimization
- [ ] Portfolio balancing across markets
- [ ] Live odds tracking for in-play

---

## REMINDER: Phase 11 Review (May 16th, 2026)

Review Phase 11: Multi-user, chat, flagging system

Add to crontab:
```bash
# May 16th reminder
0 9 16 5 * cd ~/bootball && python3 -c "
from src.betting.alerts import BettingAlerts
alerts = BettingAlerts(channels=['discord'])
alerts.send_message('REMINDER: Review Phase 11 - Multi-user system')
"
