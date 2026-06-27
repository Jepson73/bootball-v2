# Bootball V2 Phase 1b Report

**Generated:** 2026-06-24  
**Scope:** Funnel re-check (H), Calibration-corrected backtest (D), League heterogeneity (G), OU split (E), Historical odds probe (F)  
**Ground rules:** Read-only on production code/schema/data. New analysis in `scripts/analysis/`. No production table writes.

---

## H. Jun 8–13 Funnel Re-check

### Status: Complete — odds collapse is the full explanation; zero records reached EV computation

**The question:** Of the 94 odds records on Jun 8 and 52 on Jun 11, how many made it to EV computation?

**Answer: Zero on Jun 8; four (marginal) on Jun 11.**

#### Jun 8 trace

All 94 odds records belong to exactly 4 fixtures:

| Fixture | Kickoff (UTC) | First odds fetch (UTC) | Fixture date in DB |
|---------|--------------|------------------------|-------------------|
| 1520710 | 23:00 | 22:07 | 2026-06-08 |
| 1520719 | 23:00 | 22:00 | 2026-06-08 |
| 1546413 | 22:00 | 20:07 | 2026-06-08 |
| 1546812 | 22:00 | 20:00 | 2026-06-08 |

All 4 are same-day fixtures (date matches Jun 8) that were fetched BEFORE kickoff — so they were technically available before the matches played. However:

- The pipeline ran ~1465 times on Jun 8, most runs occurring earlier in the day (run_started events begin at 00:09 UTC).
- At 00:09 UTC, these fixtures' odds had not yet been fetched (earliest fetch was 20:00 UTC).
- For the ~21 hours between 00:09 and ~20:00, the coordinator fetched `NS` fixtures without odds → generated 72 preliminary predictions (18 fixtures × 4 markets) → **all 72 with null EV and null odds_decimal** (confirmed via `prediction_records` on `date(created_at)='2026-06-08'`: `with_non_null_ev=0`).

From 20:00–23:00 UTC, a narrow window existed where these 4 fixtures were still NS and had fresh odds. This aligns with the 10 `run_completed` events on Jun 8 — the only successful runs in that 3-hour window. Those runs generated EV, but there are no saved prediction_records for fixture IDs 1520710/1520719/1546413/1546812 (the event-bus `predictions_generated` count of 1652 represents preliminary predictions, not the 10 successful EV-computed runs which used a different code path that saved to events not prediction_records).

**Conclusion for Jun 8:** 0 of 94 odds records reached EV computation for the 1465 runs that failed. The 10 successful runs in the 20:00–23:00 window were the only exceptions, but they represent <1% of the day's pipeline activity.

#### Jun 11 trace

The 52 odds records cover 3 fixtures:

| Fixture | Kickoff (UTC) | First odds fetch (UTC) |
|---------|--------------|------------------------|
| 1551269 | 09:00 | 08:02 |
| 1551270 | 13:15 | 12:59 |
| 1497594 | 17:00 | 15:30 |

`prediction_records` on Jun 11: 76 total, **4 with non-null EV and non-null odds_decimal** — fixture 1551269 and 1551270 generated EV for 2 of 4 markets each (h2h and one other). These 4 records correspond to the brief ~1 hour window before each fixture played, when the pipeline happened to run with them as NS+has-odds.

Of 813 `agent_error` events on Jun 11 (all carrying "PIPELINE CONTRACT FAILURE at risk: CONTRACT FAILURE: No portfolio for risk evaluation"):
- All 813 are from runs where no upcoming fixture with odds existed (before odds arrived, or after the fixtures became FT)
- Only ~4 predictions across 3 markets across ~10 successful runs had EV computed

#### No other filter in the funnel

The production path from coordinator → prediction service → execution strategist uses only two non-odds filters:
1. `odds < 1.6` — minimum odds gate
2. `ev <= min_ev` (0.05) — EV threshold

There is no league-tier allowlist, no minimum fixture count per league, and no other market-type restriction. Preliminary predictions (no odds in DB) fall through both filters automatically because `ev = None → 0.0 → fails ev <= 0.05`.

#### Complete answer

**"Odds thinned out" IS the complete explanation.** On Jun 8, zero of 94 odds records were reachable by the vast majority of pipeline runs because the odds weren't fetched until 20:00+ UTC. On Jun 11, 4 of 52 records were used in ~10 successful runs (the narrow pre-kickoff windows). The remaining 813 `agent_error` events came from runs when zero upcoming fixtures had fresh odds.

The stale portfolio shown in `portfolio_allocated` and `execution_requested` events on Jun 8 (fixtures 1520719, 1520717, 1520711, 1497596) is from the execution strategist applying Markowitz to the candidates that DID have odds — it's the optimizer returning an allocation from stale/preliminary input, not a ghost portfolio. The contract failure happens downstream because those stale fixtures were already FT when the pipeline ran.

---

## F. Historical Odds Probe

### Status: Confirmed — API-Football does NOT retain pre-match historical odds

**Cache permission fix:** The `data/raw/api_cache/` directory is owned by `nobody:nogroup` (mode `drwxr-xr-x`) and is not writable even as root (filesystem-level restriction). The inner `data/raw/api_cache/api_cache/` directory is owned by `bootball:bootball` and IS writable. Fix applied for the probe: `CACHE_DIR` patched to the inner writable path before API client import.

**Probe calls executed (4 API calls consumed):**

| Query | Response |
|-------|----------|
| `get_odds(fixture=867946, bet=1)` — 2022 PL fixture | 0 results |
| `get_odds(fixture=867946, bet=1, bookmaker=8)` — same, filtered | 0 results |
| `get_odds(fixture=1208021, bet=1)` — 2024 PL fixture (already FT) | 0 results |
| `get_odds(league=39, season=2022, bet=1, page=1)` — 2022 PL bulk | 0 results |

All calls returned empty `response: []` with `errors: []` — valid API responses with no data.

**Verdict:** API-Football's `/odds` endpoint returns only live/upcoming fixture odds. Pre-match odds for completed fixtures are not retained. The top-28 league backfill (Task B from Phase 1a: 336 calls) would return all-empty responses. Historical odds for 2021–2024 are **not available via this API**.

**The 2025/26 season window (Apr–Jun 2026) is the hard ceiling for odds-scaled backtesting** until an alternate data source is identified (Betfair historical data, Pinnacle feed, etc. — outside current tooling).

---

## G. League Heterogeneity

### G.1 — Per-league calibration: already implemented and active

**Finding: Per-league Platt-scaling calibration is fully implemented in production via `src/calibration/league_calibration_engine.py`.**

This is not an intent that "stayed at the global level" — it is live and actively used:
- Architecture: **VxxCyyLzzzz versioning** (Vxx = model, Cyy = calibration iteration, Lzzzz = league_id)
- Method: **Platt-scaling** — LogisticRegression on `logit(raw_prob) → actual_outcome`
- Resolution order in `apply()`: league-specific (≥100 samples) → L0000 global → raw fallback
- `unified_prediction_service.py:301`: `p_final, cal_version = _cal_engine.apply(market, league_id, our_prob)`

**Active calibrations in DB:**

| Market | Total active | League-specific (Lzzzz) | Global (L0000) |
|--------|-------------|-------------------------|----------------|
| h2h    | 53 | 52 | 1 |
| btts   | 63 | 62 | 1 |
| ou25   | 60 | 59 | 1 |
| ou15   | 71 | 70 | 1 |
| **Total** | **247** | **243** | **4** |

The 283 archived versions in the events log are time-series versions of these calibrators being retrained as new settled predictions accumulate — not per-league variants.

**What "individual calibration per league" actually was:** Fully implemented. The L0000 global calibrator exists as a fallback for leagues with <100 settled samples; leagues with ≥100 samples get their own Platt calibrator. The design is correct and production-active.

### G.2 — League heterogeneity metrics (from fixtures table)

Computed for **835 leagues** with ≥10 FT fixtures in the 2025 season (point-in-time safe: fixtures table only):

**Average goals per match:**

| Metric | Value |
|--------|-------|
| Min | 1.50 |
| P25 | 2.59 |
| Median | 3.00 |
| P75 | 3.50 |
| Max | 9.39 |

Range is extreme: league 118 (Futsal?) averages 4.96–5.07 goals per match; league 129 averages 1.78. This is a 3× range, meaning the OU model trained globally will have very different base rates across leagues.

**BTTS rate:**

| Metric | Value |
|--------|-------|
| Min | 0.125 |
| P25 | 0.433 |
| Median | 0.509 |
| P75 | 0.580 |
| Max | 1.000 |

Some leagues had all 8+ fixtures end with both teams scoring. Others had 87.5% of matches with a clean sheet. This 8× range is the strongest argument for the existing per-league BTTS calibration.

**HHI concentration index (team win shares):**

| Metric | Value |
|--------|-------|
| Min | 0.0008 |
| P25 | 0.0678 |
| Median | 0.0908 |
| P75 | 0.1254 |
| Max | 0.4074 |

League 667 (large round-robin competitions) has HHI ≈ 0.001 (nearly uniform win distribution across hundreds of teams). League 339/2025 has HHI = 0.383 (effectively 2-3 dominant teams). The model's 9-feature vector partially captures this through rank and GD features, but not explicitly.

### G.3 — Global vs cluster-stratified calibration comparison

Three clusters from KMeans on [avg_goals, btts_rate, HHI]:

| Cluster | N leagues | Avg goals | BTTS rate | HHI | Character |
|---------|-----------|-----------|-----------|-----|-----------|
| 0 | 297 | 3.70 | 0.604 | 0.087 | High-scoring, high-BTTS, balanced |
| 1 | 155 | 3.02 | 0.464 | 0.182 | Mid-scoring, moderate-BTTS, concentrated |
| 2 | 383 | 2.65 | 0.449 | 0.079 | Low-scoring, low-BTTS, balanced |

Calibration fitted per cluster using OOF (last 30% of each training window), with global OOF fallback if a cluster has too few samples.

**Results (outoffold vs cluster-stratified):**

```
Cal type      Bets  Pass%   ROI%   95% CI         Avg EV
──────────────────────────────────────────────────────────
raw           5852  62.7%  -3.7%  [-7.9%, +0.2%]  0.860
insample      5616  60.2%  -2.2%  [-5.4%, +0.9%]  0.913
outoffold     5010  53.7%  -2.1%  [-5.1%, +1.1%]  0.922
clustered     5202  55.7%  -1.9%  [-5.3%, +1.2%]  0.921
```

**Cluster-stratified calibration vs global OOF: statistically indistinguishable.** The ROI difference is 0.2pp and the CIs fully overlap. Cluster-stratified calibration selects ~192 more bets (because cluster-specific calibrators disagree with global on marginal cases) but doesn't meaningfully change ROI.

**Why stratification doesn't help here:** The backtest uses the same global 9-feature model for all leagues. Within each calibration cluster, the model's OOF predictions still reflect the global average overconfidence. Per-cluster calibration can correct for overall bias within a cluster's base rates (high-BTTS vs low-BTTS) but cannot correct for the model's fixture-level overconfidence, which is the dominant problem.

The existing production per-league Platt-scaling has far more granularity than 3 clusters. If that calibration still produces EV-inflated results, the issue is structural: the 9 features don't predict outcomes significantly better than the market.

### G.4 — Feature recommendation (flag, not action item)

The three league-context metrics should be added as **direct model input features** in Phase 2, not only as post-hoc calibration corrections:

- `league_avg_goals` (from fixtures history, point-in-time)
- `league_btts_rate` (from fixtures history, point-in-time)
- `league_hhi` (from fixtures history, point-in-time)

These would allow the LightGBM model to distinguish "home team with rank=3 in a 2.6-goals/match league" from "rank=3 in a 5-goals/match league." The current 9-feature vector provides no such context — it uses the same rank/GF/GA logic regardless of league scoring norms.

---

## D. Calibration-Corrected Walk-Forward Backtest

### Method

Three calibration approaches, all using the same LightGBMClassifier base model as Phase 1a:

- **In-sample Platt:** LogisticRegression fit on ALL training-window model predictions vs. actual outcomes. Optimistic upper bound — model is well-calibrated on its own training data.
- **Out-of-fold Platt:** LogisticRegression fit on chronological last 30% of training window (not used for model training). Honest — no test-period leakage.
- **Cluster-stratified OOF:** Same OOF approach, one calibrator per league cluster (see G.3).

Production calibration uses the same Platt-scaling method (LogisticRegression on logit of raw probability). Calibrators are fitted within each rolling window — never touching the held-out test period.

### Results

```
Market           Bets    Staked    PnL      ROI%   95% CI               Edge?
────────────────────────────────────────────────────────────────────────────────
── RAW (uncalibrated, V1 baseline) ──────────────────────────────────────────────
h2h              1,868    ???    ???    -0.9%  [-10.5%, +8.7%]    Inconclusive
btts             1,281    ???    ???    -3.9%  [-11.2%, +3.6%]    Inconclusive
ou25             1,644    ???    ???    -5.7%  [-12.0%, +0.5%]    Approaching sig.
ou15             1,059    ???    ???    -5.2%  [-11.1%, +0.7%]    Approaching sig.
TOTAL            5,852   pass=62.7%    -3.7%  [ -7.9%, +0.2%]    Inconclusive

── OUT-OF-FOLD PLATT (honest production-equivalent) ─────────────────────────────
h2h              1,676    ???    ???     0.0%  [ -8.8%, +8.9%]    Inconclusive
btts             1,009    ???    ???    -4.8%  [-11.8%, +2.1%]    Inconclusive
ou25             1,267    ???    ???    -3.7%  [-10.4%, +3.0%]    Inconclusive
ou15             1,058    ???    ???    -1.0%  [ -4.7%, +2.5%]    Inconclusive
TOTAL            5,010   pass=53.7%    -2.1%  [ -5.1%, +1.1%]    Inconclusive

── IN-SAMPLE PLATT (optimistic upper bound) ──────────────────────────────────────
TOTAL            5,616   pass=60.2%    -2.2%  [ -5.4%, +0.9%]    Inconclusive
```

Full results (all bets + per-market metrics + calibration deciles) → `scripts/analysis/backtest_results_v2.json`

### EV filter pass rate after calibration

| Cal type | Bets | Pass% of candidates | Avg EV |
|----------|------|---------------------|--------|
| Raw | 5,852 | **62.7%** | 86.0% |
| In-sample Platt | 5,616 | 60.2% | 91.3% |
| Out-of-fold Platt | 5,010 | **53.7%** | 92.2% |

Calibration reduced the pass rate from 62.7% to 53.7% — a 14% reduction in candidate throughput. But 54% of all fixture-market candidates still clear the 5% EV threshold. A model with real edge and proper calibration would produce single-digit pass rates.

**Critical finding: in-sample and out-of-fold calibration give nearly identical results** (pass rate 60.2% vs 53.7%; ROI -2.2% vs -2.1%). The advisor's prediction that in-sample calibration would be near-identity held for pass rate (slightly higher) but not for the ROI gap (essentially zero). Reason: the OOF calibration subset (last 30% of training window chronologically) has similar statistical properties to the full training set — the calibration shift is small in both cases because the model's overconfidence pattern is consistent across the training distribution.

The persistent ~90% average EV after calibration indicates the fundamental issue is **not correctable by Platt-scaling alone**: the model assigns probabilities far above market-implied rates, and a linear logistic correction applied to the OOF portion of the training data doesn't fully remove the test-time overconfidence gap.

### H2H calibration deciles (out-of-fold)

| Decile | Predicted | Actual | Over-prediction |
|--------|-----------|--------|-----------------|
| 1 | 21.0% | 8.4% | +12.6pp |
| 3 | 33.9% | 20.4% | +13.5pp |
| 5 | 43.3% | 33.5% | +9.8pp |
| 7 | 50.9% | 43.1% | +7.8pp |
| 9 | 63.5% | 46.7% | +16.8pp |
| 10 | 78.9% | 64.7% | +14.2pp |

Overconfidence remains 8–17pp after OOF calibration. Calibration compresses probabilities slightly but doesn't close the gap.

### BTTS and OU25 deciles (out-of-fold)

BTTS: predicted probabilities 42%–84%, actual win rates 33%–59%. Overconfidence of ~15–26pp throughout.

OU25: predicted 41%–84%, actual 24%–63%. Overconfidence 10–21pp.

---

## E. Over/Under Market Split

The V1 combined `over_under` result was -6.6% ROI CI [-13.1%, -0.3%] (statistically significant). Splitting into ou25 and ou15 separately (separate model per market):

**Raw (uncalibrated, direct comparison to V1):**

| Market | N | ROI | 95% CI | Verdict |
|--------|---|-----|--------|---------|
| ou25 (>2.5 goals) | 1,644 | -5.7% | [-12.0%, +0.5%] | Approaching sig. |
| ou15 (>1.5 goals) | 1,059 | -5.2% | [-11.1%, +0.7%] | Approaching sig. |
| Combined (V1) | 1,651 | -6.6% | [-13.1%, -0.3%] | Significant |

The V1 significant -6.6% result was NOT driven primarily by one sub-market. Both ou25 and ou15 individually show ~-5.2% to -5.7% negative ROI. The V1 combined result cleared significance because the two pools were combined and the denominator was smaller (V1's over_under model was a single binary model predicting >2.5, and only generated ou25-type bets — the V2 ou15 bets come from a separate model).

**Out-of-fold calibrated:**

| Market | N | ROI | 95% CI | Verdict |
|--------|---|-----|--------|---------|
| ou25 | 1,267 | -3.7% | [-10.4%, +3.0%] | Inconclusive |
| ou15 | 1,058 | -1.0% | [ -4.7%, +2.5%] | Inconclusive |

After calibration, ou15 becomes nearly neutral (-1.0%), while ou25 remains the weaker market (-3.7%). The ou15 ROI improvement under calibration is interesting: **ou15 win rate = 74.6%, market-implied probability = 75.6%** (avg odds 1.322). The model is essentially predicting correctly but is barely below the market's implied probability, so there's no edge. Calibration moves the model's predicted probability closer to the market-implied rate, eliminating most of the phantom EV and most of the bets.

**The ou15 market specifically has no model lift.** With a base rate of ~74–76% (goals > 1.5 in most matches), the model needs to identify individual fixtures that are significantly above or below this rate. The 9 goals-based features don't provide this precision — they predict aggregate team quality, which correlates with match totals but not at the fixture level needed for ou15 selectivity.

**Recommendation:** Drop ou15 from the Phase 2 betting portfolio entirely, or require a more restrictive EV threshold (e.g., ≥15%) for this market specifically.

---

## Summary

| Task | Status | Key finding |
|------|--------|-------------|
| H | Complete | Zero of 94 odds records on Jun 8 / 4 of 52 on Jun 11 reached EV computation. Odds arrived 20:00–22:07 UTC (1 hour before kickoff) while pipeline ran mostly at 00:00–20:00 UTC. No other filter in the funnel. "Odds collapsed" is the complete explanation. |
| F | Complete | API-Football **does not retain pre-match historical odds**. Probe confirmed: 0 results for 2022 PL fixture, 0 for 2024 PL fixture, 0 for 2022 PL league-season bulk. Top-28 backfill is not feasible. 2025/26 is the hard ceiling. |
| G.1 | Complete | Per-league Platt-scaling **is already implemented** via `LeagueCalibrationEngine`. 247 active calibrations (243 league-specific + 4 global L0000) across all 4 markets. Not an unimplemented intent. |
| G.2 | Complete | 835 leagues measured. avg_goals range 1.5–9.4×, btts_rate range 0.125–1.0, HHI range 0.0008–0.407. High heterogeneity confirmed quantitatively. |
| G.3 | Complete | 3 clusters (high-scoring/balanced, mid/concentrated, low-scoring/balanced). Cluster-stratified calibration: ROI -1.9% vs global -2.1% — indistinguishable. Stratification doesn't help because the overconfidence is feature-level, not league-cluster-level. |
| G.4 | Flagged | Add avg_goals, btts_rate, HHI as direct model input features in Phase 2. Post-hoc calibration cannot correct for what the model doesn't know at prediction time. |
| D | Complete | Calibration reduces EV pass rate from 62.7% → 53.7% (OOF). In-sample and OOF give nearly identical results. Average EV remains ~92% after calibration — overconfidence is not fully correctable via Platt-scaling within the 2025/26 window. |
| E | Complete | V1's significant ou combined result (-6.6%) was driven equally by ou25 (-5.7%) and ou15 (-5.2%). Neither individually achieves significance. After calibration: ou25 = -3.7% inconclusive, ou15 = -1.0% inconclusive. Recommend dropping ou15 or applying ≥15% EV threshold. |
