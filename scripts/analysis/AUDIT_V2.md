# Bootball V2 — Consolidation Audit

**Date:** 2026-06-27  
**Scope:** Phases 1–7. This document replaces seven separate phase reports as the single authoritative verdict on the research arc.

---

## 1. Verdict Trail

One row per phase. The **pre-registered bar** throughout: 95% CI excludes zero (positive direction), ≥500 bets per market per window, holds across ≥2 non-overlapping walk-forward windows.

The walk-forward windows used in Phases 2–7:
- **2022**: Jan–Dec 2022, fdco leagues (E0, E1, E2, E3, I1, I2, SP1, SP2)
- **2023**: Jan 2023–Jun 2024, same fdco leagues
- **2025-26**: Apr 2026–Jun 2026, production live odds (all markets)

CLV metric (Phase 5 onward): `(open_odds − close_odds) / close_odds`. Bar for CLV: 95% CI > 0, ≥500 bets, ≥2 windows.

| Phase | Label | Lever tested | Key result | Bar verdict |
|-------|-------|-------------|------------|-------------|
| 1a | Uncalibrated baseline | 9-feature LightGBM (rankings + goals), raw probabilities, 2025-26 window only | h2h −0.6% CI[−10.2%,+9.2%]; ou combined −6.6%* CI[−13.1%,−0.3%]; total −3.5% CI[−8.1%,+1.4%]; avg EV 85% (inflated by wrong formula) | **FAIL** — CI includes 0; *ou statistically negative |
| 1b | Calibration (Platt OOF) | Out-of-fold Platt scaling applied to baseline LightGBM (formula still wrong in this run) | Total −2.1% CI[−5.1%,+1.1%]; cluster-stratified −1.9% (indistinguishable); API-Football confirmed: no historical pre-match odds retained | **FAIL** — CI includes 0 |
| 1c | Formula + mechanism audit | EV formula corrected (`p × d − 1`); three-layer calibration pipeline documented; EV filter selectivity measured | Post-fix: 83–90% of all predictions still pass 5% EV filter; avg EV 27–64%; filter is non-selective even after formula fix | Diagnostic (no bar test) |
| 1d | Production formula | Correct EV formula + Shin market blend (35% model / 65% market); fdco historical odds add 17,629 fixtures | OOF blend: −1.4% CI[−8.1%,+4.9%], 1,716 bets; pass rate 18.4%; first honest baseline using production formula | **FAIL** — CI includes 0 |
| 2 | Wave 1 features | 20 new features: rolling form (shots, possession, corners, pass accuracy), league context (avg goals, BTTS rate, HHI), H2H history; 29-feat LightGBM | h2h −8.5% to −9.4% CI[all fully negative]; ou25 −8.0% to −12.8% CI[all fully negative]; 0/8 market×N combinations pass; worse than Phase 1d | **FAIL** — CIs worse than baseline, fully negative |
| 3 | Dixon-Coles goal model | Bivariate Poisson with home advantage (γ ≈ 1.22), ρ low-score correction, per-league fit, 3 windows | h2h −7.7% to −10.6% CI[all negative]; ou25 −6.3% to −9.3% CI[all negative]; DC AUC slightly better than Wave 1 but gap small | **FAIL** — 0/3 windows pass on either market |
| 4 | Odds ceiling + overround | DC model filtered by odds ceiling (≤2.0/2.5/3.0) and bookmaker overround quartile | No ceiling or margin-quartile segment passes bar in 2+ windows; Q1 (tightest overround) not reliably better | **FAIL** — 0 configurations pass |
| 4b | Bias hunt | Favorite-longshot bias analysis; naive contrarian (bet all odds ≤threshold); league-tier segmentation | FLB confirmed: naive ROI <1.5× odds = −1.8%, 7×+ = −23.2%; Bootball concentrated in 4–7× range; no naive or contrarian strategy passes bar | **FAIL** — 0 strategies pass |
| 5 T1 | CLV signal | Closing-line value: do DC selections beat the B365 market by the close? (fdco 2022 + 2023 only) | h2h CLV +0.68% CI[+0.30%,+1.08%] (2022), +0.43% CI[+0.11%,+0.75%] (2023) — **CLV bar met in both windows**; ou25 CLV −0.13%/−0.65% — bar not met | **CLV BAR MET (h2h)**; closing ROI still −11%/−9% — EV bar **FAIL** |
| 5 T2 | Weather + referee | GLM correction on DC λ/μ using wind, precipitation, temp deviation, referee tendency | Referee significant (p<10⁻²⁸); ±1.5pp ROI effect. Weather not significant. No EV improvement in either window. | **FAIL** |
| 6 | CLV decomp + league regime | h2h CLV by direction/odds/league/overround; OU25 directional diagnosis; league-regime GLM; gap quantification | No CLV subset shows positive closing ROI. Gap = margin 5.5pp + selection penalty 4–6pp ≈ 10pp vs B365. League regime: 0 AUC improvement. | **FAIL** — no subset passes |
| 7 | xG (Understat) | Rolling xG as DC input (EPL, Serie A, La Liga; 10-season Understat data); Var-A Skellam+isotonic vs Var-B DC-bivariate | CLV doubles: +1.1% → +1.7–2.5% (CI>0 both windows, all xG variants). EV ROI: best 2022 Var-B −2.5% CI[−12.9%,+8.0%], 2023 −20.2% CI[−29.4%,−10.7%] — unstable. Selection penalty near-zero 2022, +16pp 2023. | **FAIL** — EV bar not met in any model/scope/window combination |

\* Phase 1a ou result used a wrong EV formula (`p×(d+1)−1` instead of `p×d−1`) and is not comparable to later phases; retained for completeness.

---

## 2. Decomposition: Why It Fails

The binding arithmetic is captured in Phase 6's decomposition. For any market:

```
Realized ROI = CLV − Market margin − Selection penalty
```

**Measured values (h2h, fdco windows, DC model):**

| Window | CLV | B365 margin | Selection penalty | Realized ROI |
|--------|-----|-------------|-------------------|--------------|
| 2022 (DC goals) | +0.675% | 5.572% | 5.70pp | −10.6% |
| 2023 (DC goals) | +0.427% | 5.572% | 3.76pp | −8.9% |
| 2022 (DC + xG, Var-B, roll=10) | +2.073% | 5.572% | −0.96pp | −2.5% |
| 2023 (DC + xG, Var-B, roll=10) | +1.686% | 5.572% | +16.28pp | −20.2% |

**Three independent components:**

**1. Market margin (~5.5pp vs B365, ~3pp vs Pinnacle) — structural.**  
This is the cost of betting into B365. To break even at B365, CLV must exceed 5.5% *before* accounting for the selection penalty. Current best CLV (xG, Phase 7) is ~2%. This alone is a 3.5pp deficit even with perfect calibration.

**2. CLV edge (now ~+2% with xG, +0.55% without) — real but too small.**  
The h2h CLV signal is positive, statistically significant, and reproducible across windows. It demonstrates that the DC model genuinely identifies directional mispricing. Phase 7's xG addition doubles this to ~2%. This is the component that has been improving across phases.

**3. Selection penalty (4–16pp, highly unstable) — the binding constraint.**  
This is the gap between what the model's selections earn in realized outcomes and what the de-vigged closing probability says they should earn. The selection penalty reflects that the bets the model selects are the bets where the model is most overconfident at the moment of selection — the model's calibration is worst exactly where it picks most aggressively. Phase 7's paradox (2022 penalty ≈ 0, 2023 penalty = 16pp) shows this penalty is not a stable property of the architecture: it varies dramatically across market regimes. No tested refinement has reliably reduced it.

**Gap to profitability:**
- Against B365 closing: ~10pp (Phase 1d through Phase 7 realized ROI range)
- Against Pinnacle closing: ~8pp (lower margin but still large gap)
- Even ignoring margin (pure exchange at 0% margin): selection penalty alone is 4–16pp

---

## 3. Marginal Movement Tracking

How much did each successive refinement move the key metrics? This is the evidence for the diminishing-returns question.

| Phase | Lever | Δ ROI vs previous | Δ CLV | Δ AUC (h2h) | Δ Selection penalty |
|-------|-------|-------------------|----|-------------|---------------------|
| 1d baseline | Production formula (Platt + Shin blend) | — (reference: −1.4%) | — | 0.562 | — |
| Phase 2 | +20 Wave 1 features | **−7pp** (worsened to −8.5%) | not measured | +0.018 | not measured |
| Phase 3 | Dixon-Coles model | ~+1pp vs Wave 1 (−7.7%) | not measured | +0.011 vs Wave 1 | not measured |
| Phase 4/4b | Ceiling + bias filters | 0pp (no improvement found) | — | — | — |
| Phase 5 T1 | CLV measurement | (new metric) | **+0.55%** baseline | — | ~4–6pp (first quantification) |
| Phase 5 T2 | Weather + referee | ~+1pp (small) | +0.1% (ref) | negligible | negligible |
| Phase 6 | League regime GLM | 0pp | 0 | ~0 | stable 4–6pp |
| Phase 7 | xG (rolling 10-match) | unstable: +9pp (2022) / −11pp (2023) | **+1pp** (to ~2%) | +0.007–0.009 | 0pp 2022 / +12pp 2023 |

**Pattern:**
- AUC gains are accumulating (0.56 → 0.59 → 0.60 → 0.70 restricted to top leagues), but each AUC increment has required a new data source or model type and produced diminishing profitability gains.
- CLV has improved in two steps: baseline (~0.5%) → xG (~2%). The CLV improvements are real. The remaining gap to B365 break-even is ~3.5pp of additional CLV.
- ROI movements are not systematically improving and are highly window-sensitive. Wave 1 features worsened ROI significantly; xG improved 2022 but collapsed 2023. No refinement has produced consistent improvement across all windows.
- The selection penalty is the most volatile component and remains uncontrolled.

**Verdict on diminishing returns:** The CLV signal is improving but not at the rate needed to close the margin gap. ROI improvements are inconsistent across windows. The Phase 7 xG result (near-zero selection penalty in 2022, +16pp in 2023) suggests the 2022 result may be a lucky window, not a durable property.

---

## 4. Confirmed Dead vs. Genuinely Untested

### Confirmed Dead (do not re-test)

| Lever | Phase tested | Why ruled out |
|-------|-------------|---------------|
| Odds ceiling filters (≤2.0/2.5/3.0) | Phase 4 | No ceiling passes bar at DC AUC 0.59–0.60; multiple comparisons controlled with held-out window |
| Bookmaker overround segmentation (Q1/Q2/Q3/Q4) | Phase 4 | Q1 (tightest margin) shows marginal improvement but doesn't replicate to held-out 2025-26 window |
| FLB contrarian (bet all heavy favorites) | Phase 4b | Naive favorite strategy ROI −1.4% [−3.5%,+0.7%] — doesn't pass bar even without model |
| Wave 1 rolling form features (shots, possession, corners, pass accuracy, yellow cards) | Phase 2 | CIs fully negative at both N=5 and N=10; HP tuning confirmed not a confound; cold-start not a confound |
| H2H history features | Phase 2 | Included in Wave 1; adds 5 features; zero improvement vs Wave 1 baseline |
| League context features (avg goals, BTTS rate, HHI) as LightGBM inputs | Phase 2 | Included in Wave 1; tested directly as GLM correction in Phase 6; per-league DC already encodes this |
| League-regime GLM layer on DC | Phase 6 | AUC flat or regressed; log-loss worsened; fdco-8 leagues have modest heterogeneity (1.4× goal range, not 3×) |
| Weather features (wind, precipitation, temp deviation) | Phase 5 T2 | Not statistically significant at p<0.05 in GLM; no EV improvement |
| ou15 market | Phases 1–3 | Shin de-vigging eliminates virtually all bets in blend mode (97 bets at production pass rate); structurally unviable unless odds access improves dramatically |
| Calibration via cluster-stratification (vs per-league Platt) | Phase 1b | Statistically indistinguishable from global OOF (−1.9% vs −2.1%) |
| LightGBM hyperparameter tuning (wider trees) | Phase 2 diagnostic | Default (nl=31, ne=300) optimal at 40K training cap; wider trees overfit |
| Blend weight re-optimization (grid search) | Phase 2 diagnostic | Optimal weight unstable across windows (100%/65%/25% for h2h); no consistent improvement |
| Historical odds via API-Football backfill | Phase 1b | API does not retain pre-match historical odds (confirmed live probe: 0 results for 2022 PL fixture) |

### Genuinely Untested

| Lever | Status | Why it remains open |
|-------|--------|---------------------|
| Strength-adjusted xG (rolling xG / opponent xGA percentile) | Proposed Phase 8 | Phase 7 used raw rolling xG; strength adjustment is the most direct fix for the window-to-window instability in selection penalty |
| Sharp/exchange access (Betfair, Pinnacle) | Untested | At Pinnacle's ~3% margin, current CLV of +0.55% is much closer to break-even than B365's 5.5%; xG's +2% CLV would be nearly sufficient. No exchange integration exists. |
| Early-market timing (opening odds access) | Untested as strategy | fdco data has only closing prices; opening-to-closing movement was measured but betting *at open* vs *at close* was not tested. Need API with opening odds series. |
| Promoted-team / early-season markets | Untested | Structural information gap: newly promoted clubs lack standings history; model may have larger edge here. Requires targeted feature engineering or niche-league analysis. |
| Asian handicap markets | Untested | Different payout structure; potentially tighter effective margins than 1X2. No odds data available in current schema for this market. |
| In-play / live models | Untested | Model uses pre-match features only; live score + possession state + shot count = substantially different information set. Out of scope for current architecture. |
| Referee-segmented analysis at high confidence | Partially tested | Phase 5 T2 showed referee is a significant GLM predictor. Phase 6 didn't break down CLV by referee tier. Referee-concentration analysis (bet only when DC + referee signal align strongly) is untested. |

---

## 5. Stopping Rule for Phase 8 (Strength-Adjusted xG)

Phase 8 will test rolling xG normalized by opponent xGA percentile (strength-adjusted xG) as the DC model input, using the same DC-bivariate architecture (no isotonic calibration — Phase 7 finding), on EPL/Serie A/La Liga, same two fdco windows.

The test is specified **before running it**:

**Continue prediction-side work** if ALL of:
1. h2h CLV > 3.0% in both fdco 2022 and 2023 windows (i.e., +1pp above Phase 7's ~2% baseline)
2. Selection penalty is stable and < 6pp in both windows (i.e., both windows consistent; no single-window collapse like Phase 7's 2023)
3. ROI 95% CI upper bound > −4% in at least one window (approaching the threshold where exchange/market-structure add-on could bridge remaining gap)

If all three criteria are met, the remaining path is: (a) expand to Betfair/Pinnacle odds access, (b) test strength-adjusted xG on full fdco-8 leagues, (c) run EV backtest against exchange prices.

**Pivot to market-structure work** if EITHER:
1. CLV improvement from strength-adjusted xG is < +0.5pp above Phase 7 baseline (i.e., CLV < 2.5%) — signals that xG variants have exhausted the publicly-derivable signal
2. Selection penalty collapses in 2023 window again (> 8pp) even if 2022 looks good — signals that 2022's near-zero penalty was a lucky draw, not a property of the architecture

Market-structure pivot means: shift research focus to (a) characterizing line-movement patterns on DC-identified disagreements, (b) testing a timing-based strategy (bet only on DC selections that move in the predicted direction from open to close), and (c) live CLV accumulation from the production pipeline to build a real dataset.

**Stop prediction-side work entirely** if:
- Phase 8 meets both pivot criteria above AND the post-pivot market-structure investigation finds that the CLV signal is too diffuse to identify reliable timing entry points (i.e., no identifiable structural pattern in when/how DC-detected mispricings get corrected)

---

## 6. Honest Bottom-Line Assessment

### Is profitable public-data betting on these markets plausibly reachable?

**Against B365 (the current target): No, not at current skill level.**

The evidence from seven phases is consistent: the DC + xG model generates real directional signal (positive CLV), but ~2% CLV against a 5.5% margin with a 4–16pp selection penalty does not produce profits. The selection penalty is the unpredictable element — it reflects model overconfidence at the bets most aggressively selected, and no phase has shown a reliable way to reduce it.

The gap to B365 profitability is approximately 10pp of realized ROI improvement. The seven phases have collectively moved the needle by less than 2pp (from the Phase 1d −1.4% baseline to the best Phase 7 window at −2.5% for a 3-league subset). The rate of improvement is far below what's needed.

**Against Pinnacle or Betfair (untested): Possible but unconfirmed.**

At Pinnacle's 3% margin, the arithmetic changes significantly. The current CLV of +0.55% (Phase 6, DC goals) requires closing only a 2.45pp selection-penalty gap instead of 9.5pp. Phase 7's xG-boosted CLV of ~2% at Pinnacle would require only a 1pp selection-penalty improvement for break-even. This is in the range of achievable. Whether it is actually achievable depends on (a) whether the selection penalty is systematically reduceable through better calibration, and (b) whether Pinnacle's sharper market would eliminate the CLV signal before it can be exploited.

### What is the salvageable value of the prediction engine?

**1. As a CLV signal for exchange/sharp-book access.**  
The DC model's positive, statistically significant h2h CLV (+0.55% on goals, ~+2% with xG) is genuine evidence of directional accuracy. At a sharper market, this is the closest thing to a viable core. The research record defines exactly what the signal is and its current size — which is the prerequisite for any exchange-based strategy proposal.

**2. As an analytics tool.**  
The DC model with xG produces probability estimates better than the standings-only baseline (h2h AUC 0.70 vs 0.56 for the top-3-league subset). These estimates have value for pre-match tactical analysis, match-quality assessment, or commercial prediction services where the bar is accuracy, not profitability.

**3. As a foundation for a market-structure system.**  
The CLV evidence proves the model can identify direction. A market-structure system that bets when DC detects large opening-line disagreements (before the market corrects) would use the model differently — as a signal for when and where to act, not as an EV calculator against retail odds. The Phase 5–7 CLV data is the baseline to characterize what such a system would look like.

**4. As a research artifact.**  
The systematic elimination of levers in Phases 1–7 defines clearly what has been ruled out. Any continuation of this research (by this team or anyone reviewing this work) has a documented starting point. The seven phase reports + this audit replace the common failure mode in sports betting research: re-running the same tests because the negative results weren't recorded.

---

## 7. Data and Regeneration Reference

All analysis artifacts in `scripts/analysis/` fall into three categories:

**Commit (include in repo):**
- All Python scripts (`*.py`)
- All phase reports (`v2_phase1_report.md` through `v7_xg_report.md`)
- This audit (`AUDIT_V2.md`)
- Small result JSONs (phase-level summaries: `dc_results.json`, `phase4*.json`, `phase5*.json`, `phase6_results.json`, `phase7_results.json`, `v1b_supplement_results.json`, `fdco_backfill_report.json`)
- Large result JSONs (`backtest_results.json`, `backtest_results_v2.json`, `backtest_results_v3.json`, `backtest_results_v4.json`, `diagnostic_results.json`) — kept for completeness as bet-level logs

**Exclude (gitignored — regenerable):**
- `feature_cache/` (429MB) → regenerated by `walk_forward_backtest_v4.py`
- `weather_cache/` (635MB) → regenerated by `phase5_wave2.py` (Open-Meteo free API, no key required)
- `dc_cache/` (13MB) → regenerated by `dixon_coles_backtest.py`
- `understat_cache/` (3.7MB) → regenerated by `phase7_xg_analysis.py` (Understat, rate-limited to 0.8 req/s)
- `fdco_cache/` (7.9MB) → regenerated by `fdco_backfill.py` (downloads CSVs from football-data.co.uk)
- `historical_odds.db` → covered by `*.db` gitignore pattern; regenerated by `fdco_backfill.py`
- `football.db` (analysis stub) → covered by `*.db` pattern

**ToS note:** The fdco odds data originates from [football-data.co.uk](https://www.football-data.co.uk/) (free for personal use, no redistribution without permission). Understat xG data is sourced from [understat.com](https://understat.com/) (scraped via `understatapi`, redistribution status unconfirmed). Neither dataset is included in this repository.

---

*This document was written on 2026-06-27 and covers all research through Phase 7 (xG). The next planned activity is Phase 8 (strength-adjusted xG), governed by the stopping rule in Section 5.*
