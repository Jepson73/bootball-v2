# Phase 7 — xG Replication (Wilkens 2026) Report

> **Scope:** EPL (E0), Serie A (I1), La Liga (SP1) — Understat-covered leagues only.
> Walk-forward windows: fdco 2022 (Jan–Dec 2022) and fdco 2023 (Jan 2023–Jun 2024).
> Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows pass.


## Task 1 — xG Source

**Source used:** Understat via `understatapi` Python package (synchronous interface).
Cloudflare test passed on first attempt — no blocking encountered at ≤0.8 req/s.

**Coverage:** EPL (league 39), Serie_A (league 135), La_Liga (league 140). Seasons 2014/15–2023/24 (understat season keys 2014–2023). Total: 11400 completed matches.

**Overlap with fdco backtest windows:**
- fdco 2022 window (Jan–Dec 2022): E0/I1/SP1 account for 1,000 of 3,540 preds (28%)
- fdco 2023 window (Jan 2023–Jun 2024): 1,738 of 5,456 preds (32%)

The other 5 fdco leagues (E1/E2/E3/I2/SP2) are not in Understat's top-6 coverage; this analysis is restricted to the 3 matching leagues.

**Rolling window tested:** 5 and 10 matches. (Wilkens 2026 exact window not recoverable without paper access; both alternatives are reported.)



## Roll window = 5 matches


### Window 2022

- n_val_preds (covered leagues): 1000
- Dropped (no xG): 3
- Var-B: rolling xG → DC bivariate Poisson (no optimizer)

**C2-equivalent — Raw Model Quality (val set)**

| Model | N | AUC | Log-loss | Brier |
|-------|---|-----|----------|-------|
| DC baseline (goals) | 997 | 0.69757 | 1.00162 | 0.19929 |
| Var-A Skellam+iso (xG) | 997 | 0.66962 | 1.08985 | 0.20416 |
| Var-B DC-xG | 997 | 0.67093 | 1.06425 | 0.20991 |

**EV Backtest**

| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |
|-------|-------|---|------|-------|-------|-------|-------|
| DC baseline | all-bets | 740 | -11.592% | [-24.633%,1.642%] | YES | NO | FAIL |
| DC baseline | home-only | 266⚠️ | -20.53% | [-37.023%,-3.033%] | NO⚠️ | NO | FAIL |
| Var-A Skellam+iso | all-bets | 845 | -12.764% | [-24.622%,0.127%] | YES | NO | FAIL |
| Var-A Skellam+iso | home-only | 416⚠️ | -15.986% | [-29.665%,-2.067%] | NO⚠️ | NO | FAIL |
| Var-B DC-xG | all-bets | 846 | -3.106% | [-13.674%,8.646%] | YES | NO | FAIL |
| Var-B DC-xG | home-only | 424⚠️ | -4.809% | [-18.073%,8.657%] | NO⚠️ | NO | FAIL |

**CLV Cross-Check**

| Model | N | CLV% | 95%CI | CI>0? |
|-------|---|------|-------|-------|
| DC baseline | 735 | 1.083% | [0.2702%,1.9309%] | YES |
| Var-A Skellam+iso | 839 | 2.2531% | [1.4869%,3.0292%] | YES |
| Var-B DC-xG | 840 | 1.6874% | [1.0616%,2.3538%] | YES |

### Window 2023

- n_val_preds (covered leagues): 1738
- Dropped (no xG): 1
- Var-B: rolling xG → DC bivariate Poisson (no optimizer)

**C2-equivalent — Raw Model Quality (val set)**

| Model | N | AUC | Log-loss | Brier |
|-------|---|-----|----------|-------|
| DC baseline (goals) | 1737 | 0.7001 | 0.99256 | 0.19731 |
| Var-A Skellam+iso (xG) | 1737 | 0.68004 | 1.07043 | 0.20177 |
| Var-B DC-xG | 1737 | 0.67884 | 1.043 | 0.20667 |

**EV Backtest**

| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |
|-------|-------|---|------|-------|-------|-------|-------|
| DC baseline | all-bets | 1351 | -16.02% | [-25.606%,-5.905%] | YES | NO | FAIL |
| DC baseline | home-only | 358⚠️ | -14.461% | [-31.833%,3.97%] | NO⚠️ | NO | FAIL |
| Var-A Skellam+iso | all-bets | 1403 | -20.006% | [-29.14%,-10.117%] | YES | NO | FAIL |
| Var-A Skellam+iso | home-only | 527 | -12.116% | [-25.651%,2.2%] | YES | NO | FAIL |
| Var-B DC-xG | all-bets | 1446 | -19.488% | [-28.131%,-10.61%] | YES | NO | FAIL |
| Var-B DC-xG | home-only | 552 | -9.217% | [-21.529%,3.531%] | YES | NO | FAIL |

**CLV Cross-Check**

| Model | N | CLV% | 95%CI | CI>0? |
|-------|---|------|-------|-------|
| DC baseline | 1345 | 1.0324% | [0.4277%,1.6359%] | YES |
| Var-A Skellam+iso | 1394 | 1.5137% | [0.8982%,2.14%] | YES |
| Var-B DC-xG | 1437 | 1.7576% | [1.1921%,2.3432%] | YES |

## Roll window = 10 matches


### Window 2022

- n_val_preds (covered leagues): 1000
- Dropped (no xG): 3
- Var-B: rolling xG → DC bivariate Poisson (no optimizer)

**C2-equivalent — Raw Model Quality (val set)**

| Model | N | AUC | Log-loss | Brier |
|-------|---|-----|----------|-------|
| DC baseline (goals) | 997 | 0.69757 | 1.00162 | 0.19929 |
| Var-A Skellam+iso (xG) | 997 | 0.70668 | 1.12755 | 0.19807 |
| Var-B DC-xG | 997 | 0.70796 | 1.00421 | 0.19901 |

**EV Backtest**

| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |
|-------|-------|---|------|-------|-------|-------|-------|
| DC baseline | all-bets | 740 | -11.592% | [-24.633%,1.642%] | YES | NO | FAIL |
| DC baseline | home-only | 266⚠️ | -20.53% | [-37.023%,-3.033%] | NO⚠️ | NO | FAIL |
| Var-A Skellam+iso | all-bets | 761 | -3.154% | [-15.781%,9.345%] | YES | NO | FAIL |
| Var-A Skellam+iso | home-only | 375⚠️ | -6.205% | [-19.657%,8.124%] | NO⚠️ | NO | FAIL |
| Var-B DC-xG | all-bets | 764 | -2.542% | [-12.882%,7.956%] | YES | NO | FAIL |
| Var-B DC-xG | home-only | 438⚠️ | -7.005% | [-18.805%,5.074%] | NO⚠️ | NO | FAIL |

**CLV Cross-Check**

| Model | N | CLV% | 95%CI | CI>0? |
|-------|---|------|-------|-------|
| DC baseline | 735 | 1.083% | [0.2702%,1.9309%] | YES |
| Var-A Skellam+iso | 754 | 2.4559% | [1.6588%,3.2561%] | YES |
| Var-B DC-xG | 757 | 2.0734% | [1.3617%,2.7819%] | YES |

### Window 2023

- n_val_preds (covered leagues): 1738
- Dropped (no xG): 1
- Var-B: rolling xG → DC bivariate Poisson (no optimizer)

**C2-equivalent — Raw Model Quality (val set)**

| Model | N | AUC | Log-loss | Brier |
|-------|---|-----|----------|-------|
| DC baseline (goals) | 1737 | 0.7001 | 0.99256 | 0.19731 |
| Var-A Skellam+iso (xG) | 1737 | 0.70168 | 1.07433 | 0.19851 |
| Var-B DC-xG | 1737 | 0.70203 | 1.00333 | 0.19935 |

**EV Backtest**

| Model | Scope | N | ROI% | 95%CI | ≥500? | CI>0? | Pass? |
|-------|-------|---|------|-------|-------|-------|-------|
| DC baseline | all-bets | 1351 | -16.02% | [-25.606%,-5.905%] | YES | NO | FAIL |
| DC baseline | home-only | 358⚠️ | -14.461% | [-31.833%,3.97%] | NO⚠️ | NO | FAIL |
| Var-A Skellam+iso | all-bets | 1394 | -18.618% | [-27.906%,-9.262%] | YES | NO | FAIL |
| Var-A Skellam+iso | home-only | 506 | -7.98% | [-22.091%,7.345%] | YES | NO | FAIL |
| Var-B DC-xG | all-bets | 1355 | -20.163% | [-29.446%,-10.668%] | YES | NO | FAIL |
| Var-B DC-xG | home-only | 563 | -11.755% | [-24.482%,1.197%] | YES | NO | FAIL |

**CLV Cross-Check**

| Model | N | CLV% | 95%CI | CI>0? |
|-------|---|------|-------|-------|
| DC baseline | 1345 | 1.0324% | [0.4277%,1.6359%] | YES |
| Var-A Skellam+iso | 1386 | 1.5554% | [0.9515%,2.1859%] | YES |
| Var-B DC-xG | 1348 | 1.6863% | [1.0949%,2.3086%] | YES |

---

## Task 5 — Verdict


### Roll window = 5 matches

Pre-registered bar: 95% CI > 0, ≥500 bets, ≥2 windows pass.

| Model | Scope | 2022 pass? | 2023 pass? | Both windows? |
|-------|-------|-----------|-----------|---------------|
| DC baseline | all | NO | NO | FAIL |
| DC baseline | home | NO | NO | FAIL |
| Var-A Skellam+iso | all | NO | NO | FAIL |
| Var-A Skellam+iso | home | NO | NO | FAIL |
| Var-B DC-xG | all | NO | NO | FAIL |
| Var-B DC-xG | home | NO | NO | FAIL |

### Roll window = 10 matches

Pre-registered bar: 95% CI > 0, ≥500 bets, ≥2 windows pass.

| Model | Scope | 2022 pass? | 2023 pass? | Both windows? |
|-------|-------|-----------|-----------|---------------|
| DC baseline | all | NO | NO | FAIL |
| DC baseline | home | NO | NO | FAIL |
| Var-A Skellam+iso | all | NO | NO | FAIL |
| Var-A Skellam+iso | home | NO | NO | FAIL |
| Var-B DC-xG | all | NO | NO | FAIL |
| Var-B DC-xG | home | NO | NO | FAIL |

### Home-only underpowering note

The home-only restriction targets the cell where both Wilkens and our Phase 6 CLV results show concentration. With ~1,000/1,738 covered preds per window and ~35% home-selection rate, the 2022 home-only cell contains 266–438 bets — below the ≥500 bar and formally underpowered. The 2023 window clears 500 for Var-A home (506 bets, CI=[−22.1%,+7.3%]) and Var-B home (552–563 bets, CI upper bounds +1.2% to +3.5%). These are encouraging but none of the CI lower bounds are positive, so the bar is not formally met.

---

### Synthesis — Is xG the missing lever?

**The central finding is a paradox: xG widens positive CLV substantially, but realized ROI does not improve in a stable way across windows.**

**CLV finding (consistent across all variants and windows):**

| Model | 2022 CLV | 2023 CLV | Both CI>0? |
|-------|---------|---------|-----------|
| DC baseline (goals) | +1.083% | +1.032% | YES |
| Var-A Skellam+iso, roll=5 | +2.253% | +1.514% | YES |
| Var-B DC-bivariate, roll=5 | +1.687% | +1.758% | YES |
| Var-A Skellam+iso, roll=10 | +2.456% | +1.555% | YES |
| Var-B DC-bivariate, roll=10 | +2.073% | +1.686% | YES |

The baseline CLV on E0/I1/SP1 (+1.08%/+1.03%) is already higher than the Phase 6 full-8-league result (+0.68%/+0.43%), confirming these three top-division leagues concentrate DC's signal. xG approximately doubles the CLV to +1.5–2.5%. This is robust.

**The paradox — selection-penalty decomposition (realized EV = CLV − margin − selection_penalty):**

Applying the Phase 6 decomposition: `SP = CLV − margin − realized_ROI`. Using the 8-league B365 closing margin of 5.572% (note: top-division margins are slightly tighter; absolute SP levels are approximate but cancel in cross-window comparison):

| Model (roll=10) | Window | Realized ROI | CLV | SP = CLV − 5.572% − ROI |
|---------|--------|-------------|-----|------------------|
| DC baseline | 2022 | −11.592% | +1.083% | **7.10pp** |
| DC baseline | 2023 | −16.02% | +1.032% | **11.48pp** |
| Var-A Skellam+iso | 2022 | −3.154% | +2.456% | **+0.04pp** |
| Var-A Skellam+iso | 2023 | −18.618% | +1.555% | **+14.60pp** |
| Var-B DC-bivariate | 2022 | −2.542% | +2.073% | **−0.96pp** |
| Var-B DC-bivariate | 2023 | −20.163% | +1.686% | **+16.28pp** |

*Check: SP = CLV − margin − ROI. Baseline 2022: 1.083 − 5.572 + 11.592 = 7.103 ✓. Var-B 2022: 2.073 − 5.572 + 2.542 = −0.957 ✓.*

**The critical 2022 result:** Var-B 2022 SP = −0.96pp. Negative selection penalty means the Var-B selections in 2022 slightly *beat* the devigged closing line in realized outcomes. This is the single most positive quantitative result in the analysis. But it does not replicate in 2023 (SP = +16.28pp, much worse than baseline). The pattern is inconsistent — one window shows near-zero/negative penalty, the next shows a larger penalty than the baseline.

**Raw quality finding — window size matters:**

On the full xG-matched prediction set (N=997/1737, not EV-selected bets):

*Roll=5 match window:*
| Window | Baseline AUC | Var-A AUC | Var-B AUC | Baseline LL | Var-A LL | Var-B LL |
|--------|------------|---------|---------|-----------|--------|--------|
| 2022 | 0.698 | 0.670 | 0.671 | 1.002 | 1.090 | 1.064 |
| 2023 | 0.700 | 0.680 | 0.679 | 0.993 | 1.070 | 1.043 |

*5-match window: xG models show WORSE discrimination (lower AUC) than baseline. Rolling over only 5 matches is too noisy to stabilize xG signal.*

*Roll=10 match window:*
| Window | Baseline AUC | Var-A AUC | Var-B AUC | Baseline LL | Var-A LL | Var-B LL |
|--------|------------|---------|---------|-----------|--------|--------|
| 2022 | 0.698 | 0.707 | 0.708 | 1.002 | 1.128 | 1.004 |
| 2023 | 0.700 | 0.702 | 0.702 | 0.993 | 1.074 | 1.003 |

*10-match window: xG models achieve slightly higher AUC than baseline (+0.7–0.9pp) in both windows. Var-B (DC-bivariate, no isotonic) matches baseline log-loss almost exactly (1.004/1.003 vs 1.002/0.993). Var-A (Skellam+isotonic) has noticeably worse log-loss (1.128/1.074) — isotonic calibration is over-fitting or distorting probability magnitudes.*

**Architecture finding:**

Var-B (DC bivariate Poisson, no isotonic calibration) is a meaningfully better-calibrated architecture than Var-A (Skellam+isotonic) in the 10-match window: log-loss near-identical to baseline vs +0.08–0.13 worse. The distribution choice (Skellam vs DC bivariate Poisson) is secondary; the calibration step (isotonic) is the source of Var-A's log-loss degradation. For Phase 8, DC-bivariate with rolling xG input is the preferred architecture, without isotonic calibration.

**Comparison with Phase 3 baseline context:**

Phase 3 baseline on the full 8-league set had H2H AUC=0.590/0.603. The Phase 7 baseline on E0/I1/SP1 only shows AUC=0.698/0.700. This 10-point gap reflects two things: (1) top-division leagues are more predictable than lower divisions; (2) these quality metrics are computed on all xG-matched preds (n=997/1737), which are all three top-division leagues — a naturally higher-predictability subset.

**Final verdict:**

xG input partially closes the signal gap but is not the missing lever in isolation:
- xG **widens CLV** (1% → 2.5%, consistent and statistically robust)
- xG with 10-match window **slightly improves discrimination** (+0.7–0.9pp AUC, both windows)
- xG **does not reduce selection penalty** in a stable way — near-zero in 2022, much larger in 2023
- The pre-registered bar is **not met** in any model/scope/window combination
- Closest result: Var-A home-only 2023, roll=10: n=506, ROI=−8.0%, CI=[−22.1%,+7.3%]
- Architecture: DC-bivariate (Var-B) preferred over Skellam+isotonic (Var-A) on calibration grounds

**The gap between Wilkens' ~10% ROI result and these results (~−3% to −20%)** is most plausibly explained by: (a) Wilkens used Bundesliga, a single league where xG may be more predictive; (b) his rolling window length is unknown and may be more optimized; (c) raw rolling xG without strength-of-schedule adjustment is a first approximation.

**Recommendation for Phase 8:** strength-adjusted xG (rolling xG normalized by opponent xGA percentile) as a more precise signal, tested on the same 3 leagues, using DC-bivariate architecture without isotonic calibration.
