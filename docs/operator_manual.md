# Bootball — Operator Manual

Covers every dashboard, every output, what each means, and whether you need to do anything.

**Rule of thumb:** If Discord is quiet and the Betting Dashboard shows new pending bets before each match window, the system is healthy. Almost everything else is automatic.

---

## Table of Contents

1. [How the System Runs Day-to-Day](#1-how-the-system-runs-day-to-day)
2. [Discord Notifications — Full Reference](#2-discord-notifications--full-reference)
3. [Home Page](#3-home-page)
4. [Predictions Dashboard](#4-predictions-dashboard)
5. [Betting Dashboard](#5-betting-dashboard)
6. [Tracking Dashboard](#6-tracking-dashboard)
7. [Admin Panel](#7-admin-panel)
8. [Run Explorer](#8-run-explorer)
9. [Run Health Dashboard](#9-run-health-dashboard)
10. [System Control Panel (Runtime Modes)](#10-system-control-panel-runtime-modes)
11. [Governance Page](#11-governance-page)
12. [Architecture Evolution Page](#12-architecture-evolution-page)
13. [Fixture Focus Panel](#13-fixture-focus-panel)
14. [Calibration & Model Quality Numbers Explained](#14-calibration--model-quality-numbers-explained)
15. [When to Intervene — Decision Guide](#15-when-to-intervene--decision-guide)
16. [Seasonal Workflow](#16-seasonal-workflow)

---

## 1. How the System Runs Day-to-Day

The scheduler fires automatic jobs continuously. You do not need to trigger them manually under normal operation.

| Job | Frequency | What It Does |
|-----|-----------|-------------|
| Fetch fixtures | Every 6 hours | Pulls upcoming matches from the API, upserts into DB |
| Fetch results | Every hour | Updates finished match scores, settles bets, backfills any FT fixtures with missing goals |
| Fetch odds | Every hour | Refreshes bookmaker odds, recalculates EV on all predictions |
| Live status update | Continuous (cycle) | Polls matches in progress (1H, HT, 2H) and updates scores in real time |
| Prediction pipeline | Each coordinator cycle | Runs models against all upcoming fixtures, generates predictions |
| Bet selection | Each coordinator cycle | Applies portfolio engine + Kelly sizing + policy engine, selects bets |

**What "automatic" means for settlement:** When a match finishes, the system fetches the result, marks the fixture FT, computes the outcome (H/A/D), settles any placed bets against it, and logs the prediction as won or lost — all without any manual step. Discord will send a settlement notification.

**What requires a button press:**
- Placing bets (Betting Dashboard → "Place Bets (Auto)")
- Training a new model version (Admin → Train)
- Switching runtime mode (System Control)

---

## 2. Discord Notifications — Full Reference

Every message the bot sends, what it means, and what to do.

---

### ✅ Cycle Complete
**Sent:** Every 5th successful coordinator cycle  
**Means:** The pipeline ran end-to-end. Predictions were generated.  
**Shows:** Run ID, prediction count, bet count, duration  
**Action needed:** None. This is the normal heartbeat.

---

### 🎯 Bets Placed
**Sent:** When the betting engine executes bets  
**Means:** The system found positive-EV opportunities and placed stakes  
**Shows:** Each bet — fixture, market, pick, stake (SEK), odds  
**Action needed:** None. Monitor the Betting Dashboard if you want to review what was placed.

---

### 🏁 Bets Settled
**Sent:** After a match finishes and bets are resolved  
**Means:** Results came in, bets are settled, P&L recorded  
**Shows:** Won/loss breakdown, total P&L for the batch (green = profit, red = loss)  
**Action needed:** None. Individual results are visible on the Betting Dashboard.

---

### 🔮 Top Picks
**Sent:** Each cycle, when the top predictions change from the previous cycle  
**Means:** These are the highest-EV predictions the models see right now  
**Shows:** Up to 3 picks — match, market, outcome, odds, EV %, model probability vs bookmaker implied  
**Action needed:** None. Informational. If you want to manually evaluate, check the Predictions Dashboard.

---

### 🔄 Model Updated
**Sent:** After a new model version is activated  
**Means:** A retrained model outperformed the current one and was promoted  
**Shows:** Market, old version → new version, Brier score delta  
**Action needed:** None unless the Brier delta is large and unexpected (see §14). In that case check the Tracking page to see if accuracy is moving the right direction.

---

### 📐 League Calibration Updated
**Sent:** After a league-specific calibration fit runs  
**Means:** Enough settled predictions (100+) accumulated for a specific league + market pair, so a Platt scaling calibrator was fitted  
**Shows:** League name, market, Brier improvement, sample size  
**Action needed:** None. Better calibration → more accurate probability estimates for that league.

---

### ⚠️ POLICY ENGINE REPORT — THROTTLED
**Sent:** When the risk/policy engine applies a soft constraint  
**Means:** The portfolio had a mild risk signal (e.g. correlated markets, slight concentration). Stakes were scaled down, NOT blocked. Bets still placed.  
**Shows:** Risk score %, violated constraint name, scaling factor (e.g. "0.83x")  
**Action needed:** None under normal operation. Expect this regularly — it means the governance layer is active.

Common constraints that trigger this:
- `correlation_cluster_constraint` — betting btts + ou25 on the same fixture (both depend on total goals). Normal, expected.
- `exposure_concentration_constraint` — one market taking a large share of stake. Softly penalised.

---

### 🚨 RISK LIMIT BREACHED
**Sent:** Same event as THROTTLED, secondary alert format  
**Means:** Same as above. Duplicate notification for visibility. Not a crisis.  
**Action needed:** None unless the risk score is persistently 100% and bets are blocked (HARD violation). Check the Governance page if this happens daily.

---

### 📊 CALIBRATION REPORT
**Sent:** Each cycle  
**Means:** Current calibration quality across all markets  
**Shows:** Overall Error, Risk Bias, Portfolio Drift, per-market Brier + ECE  
**How to read it:**
- **Brier Score** — lower is better. 0.25 is roughly random for 3-outcome markets. Under 0.22 is good.
- **ECE (Expected Calibration Error)** — lower is better. 0.0 is perfect. Over 0.30 means the model's stated probabilities are significantly off from true frequencies.
- **0.000 / 0.000** for a market means not enough settled data yet to compute — normal early in the season.

**Action needed:** None. Review trends over weeks on the Tracking page.

---

### 🔁 System Adaptation Confirmed / CLOSED LOOP VALIDATION
**Sent:** After each closed-loop validation cycle  
**Means:** The system checked whether its recent adaptation moved performance in the right direction  
**Shows:** Run ID, Adaptation Score (0–1), Portfolio Drift, Adaptation Index  
**How to read it:**
- **Adaptation Score 0.20** early on is expected — there is not enough history to validate drift corrections confidently yet
- Score climbs toward 1.0 as the feedback loop accumulates settled data

**Action needed:** None. Monitor over months, not days.

---

### ❌ Cycle Failure / Pipeline Error
**Sent:** When the coordinator crashes  
**Means:** Something broke mid-cycle. The run was marked FAILED. The next cycle will start fresh.  
**Shows:** Error excerpt, suggested commands to diagnose  
**Action needed:** Check `journalctl -u bootball-runtime.service -n 50` for the full stack trace. Common causes: API rate limit hit, DB lock, network timeout. Most self-recover on the next cycle. If failures repeat more than 3 cycles in a row, investigate.

---

### 💔 Watchdog Alert
**Sent:** By the watchdog when it detects abnormal patterns  
**Means:** The watchdog observed something the cycle itself didn't catch — repeated crashes, heartbeat timeout, risk kill-switch triggered  
**Shows:** Alert type, severity, suggested commands  
**Action needed:** Read the alert type carefully. Heartbeat timeout = scheduler may have stalled. Kill-switch = a HARD policy constraint fired and blocked all execution. In either case, check the Run Health Dashboard.

---

## 3. Home Page

`/`

Nine navigation cards. No data, no controls. Pure navigation.

---

## 4. Predictions Dashboard

`/predictions`

**What it shows:** All model predictions for upcoming matches (next 7 days by default), sorted by EV.

### Filters

| Control | What It Does |
|---------|-------------|
| Market tabs (All / BTTS / O/U 2.5 / O/U 1.5 / 1X2) | Show only predictions for that market type |
| League dropdown | Narrow to a single league or country |
| Odds ≥ 1.6 checkbox (default: on) | Hides predictions with odds below 1.6. Turn off to see short-priced markets like OU15 |
| Sweet Spot checkbox | Shows only predictions with odds 1.8–2.2 AND positive EV — the statistically most reliable zone |
| Refresh button | Re-fetches predictions from the server |

### Reading a prediction card

```
Arsenal vs Fulham           16:30
BTTS — Yes
Odds: 2.00    EV: +4.2%    Prob: 54%
```

- **EV %** — edge over the bookmaker. Positive = model thinks this outcome is underpriced. Negative = overpriced by the bookmaker, avoid.
- **Prob** — model's estimated probability for this outcome occurring
- **Odds** — current bookmaker odds

**What "automated" means here:** Every cycle the system re-runs predictions and updates EV based on the latest odds. You do not need to refresh manually — the page is always showing the latest cycle's output.

**When to act:** Never required. Browse to form your own view or cross-check before placing. The Betting Dashboard handles selection and staking automatically.

### Live sidebar

Auto-refreshes every 30 seconds. Shows in-play matches with live scores. Click any match to open the Fixture Focus Panel.

---

## 5. Betting Dashboard

`/betting`

The operational heart. Shows your current round's bankroll state and all pending/settled bets.

### Metrics row

| Metric | What It Means |
|--------|--------------|
| Round # | Current betting round number. A new round is created when you click "New Round" |
| Balance (SEK) | Current bankroll. Starts at initial deposit, adjusted by settled P&L |
| ROI % | Return on investment for this round. Settled P&L ÷ initial bankroll |
| Pending | Number of bets placed but not yet settled (match not finished) |
| Record (W/L) | e.g. "6W / 3L" — wins and losses among settled bets this round |
| Pending Stake | Total SEK currently at risk in unsettled bets |

### Buttons

| Button | What It Does | When to Use |
|--------|-------------|-------------|
| **Place Bets (Auto)** | Runs the full bet selection pipeline: portfolio engine → Kelly sizing → policy engine → places bets | Before match windows — typically once in the morning and once before evening fixtures |
| **Settle Bets** | Fetches latest results and settles all pending bets whose matches have finished | After match windows close. The scheduler also does this automatically every hour. Only needed if you want immediate settlement. |
| **New Round** | Archives current round and starts a fresh one with a new round number | At the start of a new betting period (weekly, monthly, season start) |

### Pending Bets table

| Column | Meaning |
|--------|---------|
| Date | Match kick-off time (Europe/Stockholm) |
| Match | Home vs Away |
| Market | h2h (1X2), btts (Both Teams Score), ou25 (Over/Under 2.5), ou15 (Over/Under 1.5) |
| Ver | Model version that generated this prediction (e.g. `v02_c02`) |
| Pick | The outcome bet on (1=Home win, X=Draw, 2=Away win, Yes/No, Over/Under) |
| Stake (SEK) | Amount staked, Kelly-sized against current bankroll |
| Odds | Bookmaker odds at time of placement |
| EV | Expected value % at time of placement |
| Result | PENDING / WIN / LOSS |

**Tip:** Click any row to open the Fixture Focus Panel for live match data on that game.

### Round History table

Shows archived rounds with final P&L and ROI. Click a round to see the individual bets.

### What the system does automatically

- **Bet selection** — the coordinator cycle runs the portfolio engine and policy engine each cycle. Bets it selects are queued. Clicking "Place Bets" executes the queue.
- **Settlement** — the fetch_results job runs every hour. Any finished match automatically triggers settlement without any action from you.
- **Bankroll tracking** — each settled bet updates the balance automatically.

---

## 6. Tracking Dashboard

`/tracking`

Historical prediction performance. Use this to judge model quality over time.

### Filters

| Control | What It Does |
|---------|-------------|
| Market dropdown | Filter to a single market |
| Status dropdown | All / Settled / Pending |
| From date / To date | Date range for prediction creation |
| Per page | Records per page (20–100) |
| Date column header | Toggle ascending/descending sort |

### Stats box

| Metric | Meaning |
|--------|---------|
| Win Rate % | Percentage of settled predictions where the picked outcome occurred |
| Wins / Losses / Pending | Raw counts |
| Odds Coverage % | Percentage of predictions where the bookmaker offers odds for this outcome (data completeness) |

### Calibration chart

Shows predicted probability buckets (e.g. 50–60%, 60–70%) vs actual win rate in each bucket. A well-calibrated model should sit close to the diagonal line. If the bars are consistently above the line, the model is under-confident. If consistently below, it's over-confident. Early on (first few months) this chart is noisy — it needs hundreds of settled predictions per bucket to be meaningful.

### Prediction records table

| Column | Meaning |
|--------|---------|
| Date | Kick-off time |
| Match | Teams |
| Market | Market type |
| Ver | Model version that made this prediction |
| Pick | Predicted outcome |
| Prob % | Model's probability for this outcome |
| Odds | Bookmaker odds (if available) |
| EV % | Edge at time of prediction |
| Score | Final score (once settled) |
| Result | WIN / LOSS / PENDING |
| P&L | Profit/loss for this prediction (tracking only, not actual stake) |

**What the system does automatically:** All tracking records are created and settled without manual steps. Every prediction the pipeline generates is logged. Every time a result comes in, matching predictions are settled.

---

## 7. Admin Panel

`/admin`

For maintenance, model training, and system diagnostics. Not needed during normal daily operation.

### System Status card

Auto-loads on page open. Shows:
- API calls remaining today (quota from the football data provider)
- Database counts (fixtures, teams, standings, predictions)
- Prediction coverage

**When to check:** If predictions seem sparse or you suspect the data pipeline has a gap, check API calls remaining here. The daily quota resets at midnight. If it's near zero, fetch jobs will fail silently until reset.

### Buttons

| Button | What It Does | When to Use |
|--------|-------------|-------------|
| **Run Maintenance** | Scans the DB for orphaned fixtures, incorrect settlement states, stuck FT records. Reports findings. Does not auto-fix — it logs issues. | If you see unexpected data in Tracking or Run Health shows anomalies |
| **Run Daily Run** | Manually triggers the fixture + results fetch pipeline | Only needed if the scheduler failed and you want to force a catch-up |
| **Settle Bets** | Same as the button on the Betting Dashboard | Same — only if you want immediate settlement rather than waiting for the hourly job |

### Model Status table

| Column | Meaning |
|--------|---------|
| Market | h2h, btts, ou25, ou15 |
| Predictions | Total generated for this market |
| Settled | How many have a real-world result |
| Win % | Accuracy on settled predictions |
| Avg EV | Average edge at time of prediction |
| Brier | Probability calibration quality (lower = better) |
| ECE | Confidence calibration error (lower = better) |
| Signal | Whether the model is showing consistent edge |
| Trend | Recent direction (improving / stable / declining) |

### Training a model

1. Select a market from the dropdown (or "All" to train everything)
2. Click **Train**
3. The system fetches the most recent 5,000 finished fixtures, builds features from standings data, fits a GradientBoosting classifier, and saves the model with HMAC signing
4. If the new model beats the current one on Brier score, it is automatically activated
5. A Discord notification is sent on activation

**When to train:** The system eventually handles this automatically through the retrain scheduler job. You would manually train if you want to force a refresh after a large batch of new data arrives (e.g. after a major backfill completes).

### Model Iterations table

Shows every trained version for a market with performance metrics. Use the **Activate** button to manually roll back to or promote a specific version if the auto-activation picked something unexpected.

### League Calibrations

Shows which (league, market) pairs have enough settled predictions (100+) to have fitted a Platt scaling calibrator. Once a pair accumulates 100+ samples, the calibration runs automatically. The "Run Calibration Now" button forces an immediate fit for a specific combination.

---

## 8. Run Explorer

`/runs`

Every coordinator cycle creates an "experiment run" — a versioned snapshot of the pipeline's state. Use this to understand what changed between cycles.

### Recent Runs table

| Column | Meaning |
|--------|---------|
| Run ID | Unique identifier (first 8 chars shown) |
| Mode | DEV / LIVE / LIVE_EVAL badge |
| Start / End | Timestamps |
| Predictions | How many predictions were generated this cycle |
| Bets | How many bets were placed |
| Status | ACTIVE (in progress) / COMPLETE / FAILED |

Click **View** to open the Run Detail page for that cycle.

### Run Detail page (`/runs/<run_id>`)

**System Snapshot** — which exact model versions, calibrators, and feature pipeline version were active during this run.

**Market Breakdown table**

| Column | Meaning |
|--------|---------|
| Predictions | Count for this market in this run |
| Settled | How many have resolved |
| Win Rate | Win % for settled predictions |
| Avg EV | Average edge |
| Calibration Δ | How much the calibration layer shifted probabilities |
| Risk Δ | How much the risk layer penalised or boosted allocations |

**Layer Attribution table** — shows which layers of the inference stack contributed to EV:

| Layer | What It Does |
|-------|-------------|
| Calibration | Adjusts raw model probabilities using Platt scaling |
| League | Applies league-specific calibration if available |
| Latent | Latent feature adjustments |
| Drift | Accounts for regime changes over the season |
| Risk | Policy engine adjustments (throttling, concentration limits) |

A layer with near-zero EV contribution may be a candidate for removal via the Governance page.

**System Diagnostics** — automated recommendations the system generated for this run.

### Compare page (`/runs/compare`)

Select two run IDs side by side to see metric deltas. Useful after a model retrain to check if the new version improved things.

---

## 9. Run Health Dashboard

`/runs/health`

Real-time system observability. Auto-refreshes every 30 seconds. No controls — purely monitoring.

### System Health badge

| Status | Meaning |
|--------|---------|
| 🟢 HEALTHY | Pipeline running normally, no orphans, DB writes clean |
| 🟡 DEGRADED | Some metrics outside normal range — may be transient. Check the detail below. |
| 🔴 BROKEN | Serious issue detected. Investigate immediately. |

### Key metrics

| Metric | Normal Value | Action if Abnormal |
|--------|-------------|-------------------|
| Active Runs | 0–1 | More than 1 = a previous run didn't close. Will self-resolve on next cycle. |
| Orphan Predictions | 0 | Predictions with no run_id in modern epoch. If growing, a pipeline bug is leaking records. |
| Predictions with Run ID % | > 95% | Below 90% = data lineage is breaking down |
| Bets with Run ID % | > 95% | Same concern |

### Pipeline Health grid

Six components — each shows green (OK) or red (problem):
- **Scheduler** — APScheduler running and firing jobs
- **ExecutionEngine** — bet placement engine reachable
- **RunContext** — experiment context manager working
- **Predictions** — predictions being generated
- **Betting** — bets being placed (only meaningful if in DEV or LIVE mode)
- **DB Writes** — database commits succeeding

---

## 10. System Control Panel (Runtime Modes)

`/settings/system`

Controls what the system is allowed to do.

### Modes

| Mode | Badge Colour | Betting | Model Retraining | Predictions | When to Use |
|------|-------------|---------|-----------------|-------------|-------------|
| **DEV** | Grey | ✅ | ✅ | ✅ | Default. Use while collecting data and running the full pipeline. |
| **TRAINING** | Blue | ❌ | ✅ | ✅ | Force a retraining pass without placing any bets. Useful after a large data backfill. |
| **LIVE** | Green | ✅ | ❌ | ✅ | Production mode. Models frozen. Use when you're confident in model quality and want strict policy enforcement. |
| **LIVE_EVAL** | Red | ❌ | ❌ | ✅ | Evaluation snapshot. Models frozen AND no betting. Predictions still tracked and settled. Use to get a clean accuracy measurement without the feedback loop influencing the models. |

### Switching modes

Click the mode button. LIVE and LIVE_EVAL require confirmation. The page reloads after switching.

**Transition rules:**
- You can switch freely between DEV ↔ TRAINING ↔ LIVE
- LIVE_EVAL → anything other than DEV requires an override (not available in the UI — you'd need to do it via the API)

---

## 11. Governance Page

`/settings/governance`

Analyses whether each layer in the inference stack is earning its place.

### Layer Performance Summary table

| Column | Meaning |
|--------|---------|
| Avg EV Contribution | How much this layer adds to overall expected value |
| Stability | Consistency of its contribution across runs |
| Fragility | How sensitive its contribution is to input changes |
| Redundancy | Overlap with other layers (high = possibly removable) |

### Architecture Analysis

1. Select a run from the dropdown
2. Click **Analyze Architecture**
3. The system runs ablation — simulates each layer removed one at a time
4. Shows EV delta per layer and a recommendation (PROMOTE / RETAIN / DEMOTE / REMOVE)

**Action needed:** Only if a layer is recommended REMOVE and you want to act on it. Proceed to the Architecture Evolution page.

---

## 12. Architecture Evolution Page

`/settings/architecture`

Allows controlled modifications to the inference pipeline. Advanced — only relevant after months of data.

### Current Architecture card

Shows the active architecture's ID, which layers are enabled, and quality scores.

### Generating and applying a proposal

1. Select a run from the dropdown
2. Click **Generate Proposal** — the system proposes architecture changes based on governance analysis
3. Review: proposed layer changes, expected EV delta, rollback safety score
4. Click **Create Candidate** — runs a shadow simulation to validate the proposal
5. If validation passes (candidate shows in the table), click **Apply** to activate

**Never apply a candidate with a rollback safety score below 0.7** — this means the change is not cleanly reversible.

---

## 13. Fixture Focus Panel

Available from: Predictions, Betting, Tracking pages — click any row or card with a fixture.

### Tabs

| Tab | Shows |
|-----|-------|
| Overview | System's predictions for this fixture, live event timeline (goals, cards, subs) |
| Statistics | Live match stats — possession, shots on/off target, corners, fouls, shown as centre-out bars |
| Lineups | Starting XI and substitutes for both teams |
| H2H | Head-to-head history between these two clubs |

The panel auto-refreshes every 30 seconds while open.

---

## 14. Calibration & Model Quality Numbers Explained

### Brier Score

Measures how accurate probability estimates are. Computed as the mean squared error between predicted probability and actual outcome (0 or 1).

- **< 0.20** — excellent
- **0.20–0.25** — good
- **0.25–0.33** — roughly as good as a naive baseline (always predicting the base rate)
- **> 0.33** — the model is worse than just guessing the historical frequency

For a 2-outcome market (BTTS Yes/No) the random baseline is 0.25. For a 3-outcome market (1X2) it is 0.33.

### ECE (Expected Calibration Error)

Measures whether the model's stated confidence matches actual frequencies. If the model says 70% on 100 predictions, roughly 70 of them should win.

- **0.0** — perfect calibration
- **< 0.10** — well calibrated
- **0.10–0.25** — moderate miscalibration, Platt scaling will help
- **> 0.30** — significant miscalibration. Common in early operation before enough settled data exists.

### EV (Expected Value) %

`EV = (model_prob × odds) - 1`

A positive EV means the model believes the bookmaker is underpricing this outcome. This is the edge.

- **> 5%** — high conviction — these are the bets the system prioritises
- **1–5%** — moderate edge — included in portfolio
- **< 0%** — model agrees with or is worse than the bookmaker — not bet

### Kelly Fraction

Controls stake size. The system uses fractional Kelly (typically ¼ Kelly) to avoid ruin. Larger edge + higher probability = larger stake, but the fraction caps exposure.

---

## 15. When to Intervene — Decision Guide

### You never need to act on these

- Cycle Complete messages in Discord
- THROTTLED / Risk Limit messages — these are normal governance activity
- Calibration Reports — read them, don't react to single snapshots
- Bets settled with a loss — single results are noise; track over 100+ bets
- ECE above 0.30 in the first two months — insufficient data, will improve automatically

### Check but probably don't act

| Signal | Where to Look | What to Do |
|--------|--------------|-----------|
| No pending bets for 2+ match windows | Betting Dashboard | Check Run Health — is the scheduler running? Check Predictions page — are EV values positive? If no positive EV bets exist, the system correctly placed none. |
| API calls near zero | Admin → System Status | Nothing to do — quota resets at midnight. Data fetches will resume. |
| Brier score climbing over several weeks | Tracking Dashboard | May be data drift. Could trigger a manual retrain from Admin. |
| Run showing FAILED repeatedly | Run Explorer | Check Discord for the error message. If self-resolving (next run completes), no action needed. |

### Act immediately

| Signal | Where to Look | What to Do |
|--------|--------------|-----------|
| 💔 Watchdog: Kill Switch Triggered | Discord | A HARD policy violation blocked all execution. Go to `/settings/governance`, check which constraint fired. The system is stopped until you resolve the cause or loosen the constraint. |
| Run Health: BROKEN for more than 2 refresh cycles | `/runs/health` | Check Pipeline Health grid for which component is red. Usually DB writes or Scheduler. `journalctl -u bootball-runtime.service -n 50` for full logs. |
| Balance drops sharply (> 30% drawdown) | Betting Dashboard | Switch to TRAINING mode to disable betting. Investigate via Tracking whether there's a specific market/league with abnormally low win rates. |
| Predictions page shows 0 predictions for days that should have matches | Predictions Dashboard | API may have failed. Check Admin → System Status for API quota. Run Daily Run manually from Admin. |

---

## 16. Seasonal Workflow

### Now → next few months (DEV mode)

The system is collecting data and building the feedback loop. Every prediction logged and settled makes models and calibration better. Nothing specific to do — let it run.

- **Check weekly:** Tracking Dashboard for win rate trends, Calibration Report in Discord for ECE trends
- **After backfill completes:** Manually trigger a retrain from Admin to incorporate all historical data
- **You'll know it's working when:** ECE drops below 0.20 for most markets, and win rate on high-EV bets consistently exceeds implied probability

### When models look stable (LIVE mode)

Switch runtime mode to LIVE. Models are now frozen — no auto-retraining will change them mid-season. The betting engine picks the cream of the predictions. Monitor Betting Dashboard weekly for ROI.

### Fall (LIVE_EVAL mode)

Switch to LIVE_EVAL for a clean evaluation window. Betting stops, retraining stops. The system keeps predicting and logging outcomes. After 6–8 weeks you have a statistically clean accuracy snapshot of frozen model performance. Use Tracking Dashboard to review.

Then: if models performed well → switch back to LIVE. If models need improvement → switch to DEV or TRAINING for a retrain cycle.

---

*Last updated: May 2026*
