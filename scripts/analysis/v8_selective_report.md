# Phase 8 — Selective Prediction / Calibrated Abstention

> **Scope:** Var-B DC+xG roll=10 (Phase 7 primary model).
> **Leagues:** EPL (39), Serie A (135), La Liga (140).
> **Windows:** 2022 (Jan–Dec 2022), 2023 (Jan 2023–Jun 2024).
> **Calibration:** 2022 window ← pre-2022 fdco training bets; 2023 window ← 2022 validation bets (forward-in-time only).
> **Abstention signal:** p_selected (DC+xG model probability of bet direction).

## Reconciliation with Phase 7

Regenerated per-bet records must match Phase 7 published numbers:

| Window | Expected n | Got n | Expected ROI | Got ROI | Match? |
|--------|------------|-------|-------------|---------|--------|
| 2022 | 764 | 764 | -2.542% | -2.542% | ✓ |
| 2023 | 1355 | 1355 | -20.163% | -20.163% | ✓ |

## Task 1 — Reliability Signal Inventory

### Window 2022 (764 bets)

**Signal 1: p_selected (model confidence) — ROI by decile**

Monotonicity: none (6/9 steps +)

| Decile | p_selected range | n | ROI% | [95% CI] | CLV% | SP% |
|--------|-----------------|---|------|----------|------|-----|
| D01 | [5.94%, 24.04%] | 77 | 3.247% | [-52.597%,66.558%] | 6.981% | -1.410% |
| D02 | [24.04%, 31.70%] | 76 | 5.342% | [-35.224%,48.237%] | 3.546% | -6.841% |
| D03 | [31.70%, 37.59%] | 76 | -2.171% | [-36.712%,34.873%] | 1.765% | -1.192% |
| D04 | [37.59%, 44.55%] | 77 | -1.377% | [-32.863%,30.806%] | 1.276% | -2.384% |
| D05 | [44.55%, 50.73%] | 76 | -23.276% | [-49.949%,4.711%] | 3.140% | 21.311% |
| D06 | [50.73%, 56.16%] | 76 | -9.763% | [-36.318%,18.803%] | 1.321% | 6.070% |
| D07 | [56.16%, 64.08%] | 77 | -8.675% | [-32.028%,15.377%] | 0.791% | 4.385% |
| D08 | [64.08%, 71.56%] | 76 | 2.132% | [-19.462%,23.831%] | 0.436% | -6.804% |
| D09 | [71.56%, 81.70%] | 76 | 12.250% | [-7.408%,32.408%] | 1.050% | -16.416% |
| D10 | [81.70%, 98.17%] | 77 | -3.130% | [-17.611%,10.559%] | 0.458% | -1.604% |

**Signal 2: |p_model − p_market| disagreement**

Monotonicity: none (5/9 steps +)

**Signal 3: xG data depth (min n_xg_home, n_xg_away)**

Monotonicity: None (?/1 steps +)

**Signal 4: Per-league ROI**

| League | n | ROI% | CI lo | CI hi |
|--------|---|------|-------|-------|
| 39 | 266 | 3.977% | -15.361% | 24.678% |
| 135 | 259 | -5.822% | -22.290% | 10.850% |
| 140 | 239 | -6.243% | -22.360% | 10.352% |

**Summary:** best monotone signal = `p_selected` (6 of 9 decile steps show +ROI direction)

### Window 2023 (1355 bets)

**Signal 1: p_selected (model confidence) — ROI by decile**

Monotonicity: none (6/9 steps +)

| Decile | p_selected range | n | ROI% | [95% CI] | CLV% | SP% |
|--------|-----------------|---|------|----------|------|-----|
| D01 | [1.03%, 8.17%] | 136 | -27.574% | [-66.186%,17.289%] | 3.825% | 26.254% |
| D02 | [8.17%, 13.18%] | 135 | -52.778% | [-80.005%,-20.000%] | 2.398% | 50.084% |
| D03 | [13.18%, 18.20%] | 136 | -10.478% | [-46.140%,27.578%] | 2.169% | 7.668% |
| D04 | [18.20%, 24.28%] | 135 | 0.696% | [-34.615%,37.121%] | -0.404% | -6.212% |
| D05 | [24.28%, 28.72%] | 135 | -36.881% | [-60.869%,-9.022%] | 2.413% | 34.102% |
| D06 | [28.72%, 33.97%] | 136 | -36.838% | [-60.111%,-10.625%] | 1.028% | 32.784% |
| D07 | [33.97%, 40.70%] | 135 | -17.333% | [-41.594%,8.561%] | 1.608% | 13.826% |
| D08 | [40.70%, 49.25%] | 136 | -15.338% | [-37.648%,7.993%] | 0.257% | 10.514% |
| D09 | [49.25%, 59.69%] | 135 | 8.163% | [-14.363%,31.113%] | 1.518% | -11.736% |
| D10 | [59.69%, 90.30%] | 136 | -13.250% | [-31.015%,4.568%] | 2.039% | 10.052% |

**Signal 2: |p_model − p_market| disagreement**

Monotonicity: none (4/9 steps +)

**Signal 3: xG data depth (min n_xg_home, n_xg_away)**

Monotonicity: None (?/1 steps +)

**Signal 4: Per-league ROI**

| League | n | ROI% | CI lo | CI hi |
|--------|---|------|-------|-------|
| 39 | 492 | -12.600% | -28.592% | 4.521% |
| 135 | 451 | -21.459% | -37.186% | -4.614% |
| 140 | 412 | -27.777% | -42.352% | -12.238% |

**Summary:** best monotone signal = `p_selected` (6 of 9 decile steps show +ROI direction)

## Task 2 — Selective Prediction Layer

Calibration: p_selected threshold from prior-in-time training bets.

### Window 2022 (calibration n=2230)

| Abstention | τ | n bets | ROI% | [95% CI] | CLV B365% | CLV Pin% | SP% |
|-----------|---|--------|------|----------|-----------|----------|-----|
| 0% | — | 764 | -2.542% | [-12.882%,7.956%] | 2.073% | -2.016% | -0.492% |
| 25% | 0.308 | 619 | -4.359% | [-13.565%,4.442%] | 1.400% | -1.962% | 0.651% |
| 50% | 0.450 | 447 | -5.233% | [-14.515%,4.229%] | 1.251% | -1.722% | 1.366% |
| 75% | 0.609 | 257 | 1.973% | [-8.549%,12.484%] | 0.658% | -1.684% | -6.485% |

### Window 2023 (calibration n=764)

| Abstention | τ | n bets | ROI% | [95% CI] | CLV B365% | CLV Pin% | SP% |
|-----------|---|--------|------|----------|-----------|----------|-----|
| 0% | — | 1355 | -20.163% | [-29.446%,-10.668%] | 1.686% | -3.767% | 16.737% |
| 25% | 0.346 | 527 | -9.148% | [-20.535%,2.088%] | 1.369% | -2.176% | 5.380% |
| 50% | 0.507 | 249 | -2.827% | [-18.020%,12.338%] | 1.973% | -1.115% | -0.366% |
| 75% | 0.674 | 69 | -14.725% | [-37.624%,9.393%] | 2.426% | -0.501% | 11.865% |

## Task 3 — Pinnacle CLV Cross-Check

Realized ROI is book-independent (bet at B365 open). Two CLV measures test whether the B365-priced edge survives against the sharp-market final price.

| Window | Abstention | n | CLV vs B365 close | CI | CLV vs Pinnacle close | CI |
|--------|-----------|---|-------------------|-----|----------------------|----|
| 2022 | 0% | 764 | 2.073% | [1.362%,2.782%] | -2.016% | [-2.650%,-1.354%] |
| 2022 | 25% | 619 | 1.400% | [0.768%,2.013%] | -1.962% | [-2.580%,-1.361%] |
| 2022 | 50% | 447 | 1.251% | [0.623%,1.913%] | -1.722% | [-2.343%,-1.085%] |
| 2022 | 75% | 257 | 0.658% | [-0.037%,1.347%] | -1.684% | [-2.372%,-1.007%] |
| 2023 | 0% | 1355 | 1.686% | [1.095%,2.309%] | -3.767% | [-4.400%,-3.169%] |
| 2023 | 25% | 527 | 1.369% | [0.681%,2.052%] | -2.176% | [-2.855%,-1.500%] |
| 2023 | 50% | 249 | 1.973% | [1.034%,2.937%] | -1.115% | [-2.043%,-0.131%] |
| 2023 | 75% | 69 | 2.426% | [0.811%,4.070%] | -0.501% | [-2.104%,1.136%] |

## Task 4 — Stopping Rule

**Decision: STOP_ENTIRELY**

Abstention does NOT monotonically improve ROI in both windows. The selection penalty is diffuse — not concentrated in low-confidence bets. Prediction-side improvements cannot close the gap.

Per-window criteria:

| Criterion | 2022 | 2023 |
|-----------|------|------|
| Abstention improves ROI | YES | YES |
| Pinnacle CLV CI > 0 | NO | NO |
| ≥500 bets in selective set | NO | YES |
| Selection penalty < 4pp | NO | NO |
| ROI monotone in abstention | NO | YES |
