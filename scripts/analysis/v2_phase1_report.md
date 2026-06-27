# Bootball V2 Phase 1 Report

**Generated:** 2026-06-24  
**Scope:** Bug triage (Task A), Historical odds feasibility (Task B), Walk-forward backtest (Task C)  
**Ground rules:** Tasks A and B are read-only investigations. Task C writes only to `scripts/analysis/`. No production code, schema, or data changes.

---

## Task A — Jun 8–13 Zero-Bet Bug

### Status: Root cause confirmed

### What happened

Between approximately 2026-06-08 and 2026-06-13, the betting pipeline ran 700–850 times per day and placed **zero bets**. The `data/events.jsonl` shows 4,788 `agent_error` events in that window, every one carrying the message:

```
PIPELINE CONTRACT FAILURE at risk: CONTRACT FAILURE: No portfolio for risk evaluation
```

### Root cause

The error originates in `src/contracts/pipeline_contracts.py` at commit `4d66842` (the version running during this period). The relevant check (confirmed via `git show`):

```python
@staticmethod
def validate_risk_input(portfolio: list, risk: dict) -> dict:
    if not portfolio:                          # ← falsy check, not "is None"
        raise ContractValidationError("CONTRACT FAILURE: No portfolio for risk evaluation")
```

The check is `if not portfolio:` — which raises on an **empty list** `[]`, not just `None`. This means any run that produced zero qualifying bets would be classified as a pipeline failure and emit an `agent_error` event rather than exiting cleanly.

The current code has been updated to `if portfolio is None:` — this bug is already fixed in the current codebase.

### Why the portfolio was empty

Two overlapping failure modes both produced empty portfolios:

**Failure mode 1 — Odds coverage collapse (primary):**

| Date     | Odds records fetched |
|----------|----------------------|
| 2026-06-07 | 613 |
| 2026-06-08 | 94 ← sharp drop |
| 2026-06-09 | 302 |
| 2026-06-10 | 225 |
| 2026-06-11 | 52 ← near-zero |
| 2026-06-12 | 937 |
| 2026-06-13 | 2,250 |

With only 52–94 odds records on Jun 8/11, very few upcoming fixtures had fresh odds. Without odds, `unified_prediction_service.py` sets EV=NULL and Kelly=0 for those fixtures. The execution strategist produced candidates with zero stakes; `validate_portfolio_input` passed (predictions existed), but the final portfolio was empty.

**Failure mode 2 — Stale state amplification (secondary):**

The `state_manager.load_previous_state()` call at `coordinator.py:253` loaded a `PortfolioState` frozen from the last successful run (before Jun 8). This state's `allocation_weights` were skewed toward fixtures 1520719, 1520717, 1520711, 1497596 — all played by Jun 8–10. The learning-weight boost these stale allocations received likely reduced scores for fresh candidates, making it even harder to clear the execution strategist's stake threshold.

**Combined effect:** Near-zero odds coverage → execution strategist produces zero staked candidates → final portfolio `[]` → `if not portfolio:` → contract failure.

### Code path (confirmed)

```
coordinator.py:386  portfolio_candidates = execution_strategist.run()         # returns empty or zero-stake list
coordinator.py:425  allocation_vectors, new_state = portfolio_engine.compute_allocation(...)  # returns []
coordinator.py:433  portfolio = [... for v in allocation_vectors]              # []
coordinator.py:474  portfolio = adversary.apply_adjustments(portfolio)         # still []
coordinator.py:519  ContractValidator.validate_risk_input(portfolio, risk_data) # [] is falsy → raises
coordinator.py:522  raise RuntimeError("PIPELINE CONTRACT FAILURE at risk: ...")
```

### What should have happened

An empty portfolio is a valid outcome of "nothing qualifies today." The pipeline should log that and exit with status `run_completed` (zero bets placed). The contract's falsy check treated it as a fatal error instead.

**Fix already in place in current code** (`if portfolio is None:` instead of `if not portfolio:`).

### Open question

The `PORTFOLIO_STATE_LOADED` events (5,323 during Jun 8–13) reference stale allocations. Settlement ran through Jun 11, but the stale state was never cleared post-settlement. A hardened recovery path would: (a) detect stale allocations for FT fixtures at load time and zero their weights, and (b) trigger state re-initialization after settlement settles all pending bets.

---

## Task B — Historical Odds Retroactive-Fetch Feasibility

### Status: Feasible for top leagues; infeasible at full scale

### API capability: Confirmed

`src/ingestion/client.py` exposes:

```python
def get_odds(self, fixture_id=None, league_id=None, season=None, bet_type=1)
```

Three bet-type codes: `{"h2h": 1, "btts": 8, "over_under": 5}`.

Querying with `(league_id, season, bet_type)` returns odds for all fixtures in that league-season in a single call (with pagination). This is confirmed — the same endpoint is used by `scripts/odds_poll.py` for live and backfill fetches.

Whether API-Football retains pre-match odds for historical seasons is unconfirmed — it would require a live test against a 2022 fixture. (A permission error on the cache directory blocked the test in this session; the test itself is one line and uses no quota beyond a single API call.)

### Gap inventory

All FT fixtures without odds in the database, by season:

| Season | FT fixtures without odds |
|--------|--------------------------|
| 2019 | 51,331 |
| 2020 | 22,385 |
| 2021 | 138,210 |
| 2022 | 154,137 |
| 2023 | 161,407 |
| 2024 | 167,269 |
| **2021–2024 total** | **621,023** |

Current API quota: **Ultra plan, 75,000 calls/day** (30,821 remaining as of last check).

### Cost estimate

**Option A — Full historical backfill (2021–2024, all 1,225 leagues):**
- Call pattern: `get_odds(league_id, season, bet_type)` — one call returns all odds for that league-season
- Unique (league, season, bet_type) combinations: 1,225 leagues × 4 seasons × 3 bet_types = **14,700 calls**
- At 75,000 calls/day: **< 1 day** to fetch all league-season combinations
- Caveat: API-Football may not have pre-match odds for most historical or lower-tier fixtures, making most of these calls return empty. Only major leagues reliably retain historical odds.

**Option B — Targeted top-league backfill (top ~28 leagues, 2021–2024):**
- 28 leagues × 4 seasons × 3 bet_types = **336 calls** (< 1% of daily quota)
- FT fixtures in scope: **28,336 fixtures** (from DB query)
- This is the practical path — historically meaningful odds are concentrated in top leagues.

**Option C — Per-fixture queries (if league-season bulk is unavailable):**
- 621,023 fixtures × 3 bet_types = **1.86M calls**
- At 75,000 calls/day: **~25 days** of full-quota burn
- This burns the backfill budget currently consumed by fixture/standings ingestion — not viable without a dedicated API key.

### Feasibility verdict

| Path | Calls | Days | Notes |
|------|-------|------|-------|
| All leagues, 2021–2024, league-season bulk | ~14,700 | <1 | Risk: mostly empty returns for lower leagues |
| Top-28 leagues, 2021–2024, league-season bulk | 336 | <1 hour | Recommended first probe |
| All leagues, per-fixture | 1.86M | 25 | Not viable |

**Recommendation:** Run a single probe on one top-league 2022 fixture (`get_odds(fixture_id=867946, bet_type='h2h')`) to confirm whether API-Football retains pre-match odds. If confirmed: the top-28 bulk backfill is 336 calls and should be done immediately — it costs nothing in quota terms and would expand the backtest pool significantly. If not confirmed: historical odds are unavailable and Option A/B are moot.

---

## Task C — Walk-Forward Backtest

### Status: Completed. Key finding: model is uncalibrated; EV threshold provides zero selectivity.

### Methodology

**Scope:** 2025-season FT fixtures only (the only season with odds coverage in the DB).
- Training pool: 103,219 FT fixtures (Aug 2025 – Apr 2026)
- Test candidates: 2,334 FT fixtures with stored odds (Apr 15 – Jun 16, 2026)

**Features:** Exact 9-element production vector, computed point-in-time from the `fixtures` table:

```
[h_rank, a_rank, h_gf-h_ga, a_gf-a_ga, h_gf, a_gf, h_ga, a_ga, |h_rank-a_rank|]
```

Standings are **not** read from the `standings` table (which holds final/live values and would leak future results). Instead, each team's GF, GA, and points are derived from all FT fixtures in that league+season with `date < fixture_date`. Rank is computed as 1 + count(teams with more points at that date). Production cold-start defaults apply (rank=15, GF=1.0, GA=1.0).

**Model:** LightGBMClassifier (n_estimators=200, num_leaves=31, lr=0.05). No calibration applied — testing raw V1 probabilities exactly as the production pipeline would use them without an IsotonicRegression calibrator.

**EV and sizing:** Identical to production (`unified_prediction_service.py` lines 137–142):
```python
ev = (our_prob * odds) - (1 - our_prob)
kelly = max(0, (b * our_prob - q) / b) * 0.25
```
EV threshold: 5%. Min bet: £10. Starting bankroll: £1,000 per window.

**Walk-forward windows:**
- Window 1: train < Apr 15 (92,107 fixtures), test = Apr 15–Apr 30 (496 fixtures)
- Window 2: train < May 1 (94,005 fixtures), test = May 1–May 31 (1,694 fixtures)
- Window 3: train < Jun 1 (102,486 fixtures), test = Jun 1–Jun 16 (144 fixtures)

### Results

```
Market          Bets    Staked       PnL      ROI  95% CI (bootstrap)   Edge?
─────────────────────────────────────────────────────────────────────────────────
h2h             1,879   77,584   -  453    -0.6%  [-10.2%,  +9.2%]     Inconclusive
btts            1,324   43,044   -1,509    -3.5%  [-10.6%,  +3.9%]     Inconclusive
over_under      1,651   70,500   -4,659    -6.6%  [-13.1%,  -0.3%]     ✗ Negative
─────────────────────────────────────────────────────────────────────────────────
TOTAL           4,854  191,128   -6,621    -3.5%  [ -8.1%,  +1.4%]     Inconclusive
```

Full results (including per-bet log) → `scripts/analysis/backtest_results.json`

### Critical finding: the EV filter is broken

**All 4,854 qualifying bets had EV > 20%.** Mean EV was 85%; maximum was 2,156%.

This is not a feature of real edge — it is a symptom of **model overconfidence**. The raw LightGBM probabilities are systematically too high across every probability range:

**H2H calibration deciles (predicted vs. actual win rate):**

| Decile | Mean Predicted | Actual Win Rate | Over-prediction |
|--------|---------------|-----------------|-----------------|
| 1 (lowest prob) | 21.9% | 10.2% | +11.7% |
| 2 | 29.2% | 15.0% | +14.3% |
| 5 | 39.6% | 32.1% | +7.5% |
| 9 | 56.0% | 38.0% | +18.0% |
| 10 (highest prob) | 68.9% | 58.3% | +10.6% |

The model uniformly over-predicts by 8–18 percentage points. A predicted probability of 50% corresponds to an actual win rate of ~35–40%. Applied to the EV formula, this adds 10–18% of phantom EV to every candidate — enough to push nearly every fixture's best outcome above the 5% threshold. The EV filter is not selecting edge; it is selecting noise.

**BTTS and OU show the same pattern**, with all deciles showing positive over-prediction (mean Δ ≈ −10%).

### Why the production V1 calibrator matters

The deployed `.pkl` model files contain an `IsotonicRegression` calibrator alongside the `LGBMClassifier`. The backtest deliberately ran without calibration to test the raw model — and this is the result. Calibration would map predicted 50% → actual 35% and suppress most of the phantom EVs. The fact that production V1 uses calibrators means its EV signals are more grounded — but calibration was applied on the same training data the model was fit on (not on held-out data), so it may be partially absorbed.

### Interpretation

- **No statistically significant edge is demonstrated** for h2h or btts (CI spans zero in both cases).
- **Over/under shows statistically significant negative ROI** (CI entirely below zero: [-13.1%, -0.3%]). The model picks the wrong side more often than chance on OU bets, likely because the 9 features (goals-based standings) have genuine signal for home/away winner but weaker signal for total goals.
- **The system as currently structured bets too broadly** — 4,854 bets across 2,334 fixtures (~2.1 bets/fixture) means the EV filter is providing virtually no selectivity. With an honest calibrated probability, EV-positive bets would be rare.

### Limitations

1. **Two-month test window** (Apr 15 – Jun 16, 2026). Wide CIs are expected at this sample size; the h2h inconclusive result could easily be positive or negative with more data.
2. **No calibration in the backtest** — intentional (testing raw probabilities), but this makes the result a worst-case view of the uncalibrated system.
3. **Standings from one season only** — the 2025/26 season has enough history for reasonable features by April, but early-season fixtures (Aug–Sep 2025) in the training set have very sparse point-in-time features.
4. **Rank approximation** — rank is computed from the pool of teams that have played in the same (league, season), not from the official league table. This matches what the production trainer does (it has the same limitation since it also reads from the standings table which may lag).

### Recommended next steps

1. **Calibrate before computing EV.** Apply the existing `IsotonicRegression` calibrators on held-out data (not training data). This will shrink most EVs toward zero and make the 5% filter meaningful.
2. **Audit the OU market specifically.** The significant negative ROI suggests the model predicts "over" too aggressively. Inspect which features drive OU predictions and whether additional features (scoring rate, home/away-specific GF/GA) would improve it.
3. **Historical odds backfill (see Task B).** The current backtest covers only 62 calendar days. A top-28 league backfill costs 336 API calls and extends the odds window significantly.
4. **Add calibration to the backtest harness.** The script at `scripts/analysis/walk_forward_backtest.py` can be extended to apply `CalibratedClassifierCV(cv='prefit')` on a held-out calibration set; this would give a fairer estimate of what the production pipeline (with calibration) would achieve.

---

## Summary

| Task | Status | Key finding |
|------|--------|-------------|
| A | Complete | `if not portfolio:` in old contracts treated empty portfolios as failures; odds volume crashed Jun 8 triggering the cascade. Fix is already in current code. |
| B | Mostly complete | API supports historical bulk fetch by league-season; top-28 league backfill = 336 calls (< 1 day). One live probe needed to confirm API-Football retains pre-match odds for 2021–2024. |
| C | Complete | No significant edge demonstrated at 2 months of test data. Model overconfidence inflates EV by 10–18pp; EV filter selects ~100% of fixtures. Over/under shows statistically significant negative ROI. Calibration is prerequisite to honest EV computation. |
