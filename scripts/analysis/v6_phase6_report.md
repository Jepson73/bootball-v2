# Phase 6 — Combined Analysis Report

> **Data scope:** fdco 2022 + 2023 windows only (closing-line data available). Both windows must pass the pre-registered bar (95% CI > 0, ≥500 bets/window). DC_BLEND_W: 2022=1.0 (pure DC), 2023=0.65 (blended).

## Task A — h2h CLV Breakdown

*Metric: (1) CLV% = (open − close)/close; (2) Settling-at-close ROI = close-price payoff on actual outcome. Both per subset, per window. Key question: does any subset show positive closing-line ROI?*

### Window 2022 (n_selected=3,117, n_with_close=3,103)

**Overall h2h:** CLV=+0.675% [+0.300%,+1.084%], Close ROI=-11.409% [-17.365%,-5.673%], n=3,103

**By selection direction:**

| Direction | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|-----------|---|------|-----------|-------|-----------|-----------|--------|
| away | 1,480 | +0.566% | [-0.041%,+1.197%] | NO | -9.546% | [-18.948%,+0.370%] | NO |
| draw | 546 | +0.677% | [+0.062%,+1.309%] | YES | -15.516% | [-29.660%,-1.687%] | NO |
| home | 1,077 | +0.825% | [+0.141%,+1.454%] | YES | -11.888% | [-19.801%,-3.518%] | NO |

**By opening odds bucket:**

| Bucket | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|--------|---|------|-----------|-------|-----------|-----------|--------|
| < 1.50 | 20 | -0.080% | [-2.421%,+2.398%] | NO | +7.800% | [-18.400%,+29.950%] | NO |
| 1.50-2.00 | 154 | +0.972% | [-0.099%,+2.058%] | NO | -16.331% | [-30.273%,-1.974%] | NO |
| 2.00-3.00 | 745 | +0.056% | [-0.511%,+0.631%] | NO | -6.872% | [-15.371%,+2.043%] | NO |
| 3.00-5.00 | 1,519 | +0.094% | [-0.406%,+0.610%] | NO | -16.055% | [-23.821%,-8.291%] | NO |
| > 5.00 | 665 | +2.649% | [+1.457%,+3.878%] | YES | -5.319% | [-23.327%,+12.631%] | NO |

**By league:**

| League | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|--------|---|------|-----------|-------|-----------|-----------|--------|
| E0 ⚠️<500 | 345 | +1.675% | [+0.419%,+2.942%] | YES | -6.707% | [-27.104%,+15.402%] | NO |
| E1 ⚠️<500 | 480 | -0.000% | [-0.844%,+0.825%] | NO | +4.979% | [-10.338%,+20.248%] | NO |
| E2 | 509 | +0.579% | [-0.365%,+1.512%] | NO | -18.727% | [-33.211%,-3.571%] | NO |
| E3 ⚠️<500 | 492 | -0.828% | [-1.791%,+0.112%] | NO | -7.413% | [-21.159%,+6.307%] | NO |
| I1 ⚠️<500 | 304 | +1.077% | [-0.195%,+2.427%] | NO | -19.451% | [-38.090%,+0.762%] | NO |
| I2 ⚠️<500 | 307 | +0.256% | [-0.950%,+1.481%] | NO | -26.489% | [-42.766%,-9.161%] | NO |
| SP1 ⚠️<500 | 256 | +0.630% | [-0.625%,+1.921%] | NO | -17.129% | [-35.821%,+3.462%] | NO |
| SP2 ⚠️<500 | 410 | +2.591% | [+1.432%,+3.748%] | YES | -9.439% | [-24.618%,+6.518%] | NO |

**By opening overround quartile:**

| Quartile | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|----------|---|------|-----------|-------|-----------|-----------|--------|
| Q1 (lowest) | 763 | +1.308% | [+0.534%,+2.138%] | YES | -6.356% | [-18.742%,+6.118%] | NO |
| Q2 | 744 | +1.058% | [+0.276%,+1.847%] | YES | -7.176% | [-18.870%,+4.824%] | NO |
| Q3 | 743 | +0.316% | [-0.470%,+1.123%] | NO | -6.602% | [-18.757%,+5.954%] | NO |
| Q4 (highest) | 853 | +0.087% | [-0.685%,+0.860%] | NO | -23.809% | [-34.556%,-12.588%] | NO |

### Window 2023 (n_selected=3,824, n_with_close=3,808)

**Overall h2h:** CLV=+0.427% [+0.109%,+0.749%], Close ROI=-9.119% [-14.441%,-3.608%], n=3,808

**By selection direction:**

| Direction | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|-----------|---|------|-----------|-------|-----------|-----------|--------|
| away | 1,905 | +0.392% | [-0.116%,+0.899%] | NO | -10.427% | [-18.402%,-2.029%] | NO |
| draw | 671 | +0.328% | [-0.218%,+0.867%] | NO | -8.203% | [-22.243%,+5.718%] | NO |
| home | 1,232 | +0.535% | [+0.018%,+1.092%] | YES | -7.594% | [-15.650%,+0.389%] | NO |

**By opening odds bucket:**

| Bucket | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|--------|---|------|-----------|-------|-----------|-----------|--------|
| < 1.50 | 12 | -0.161% | [-2.048%,+1.772%] | NO | -7.167% | [-43.750%,+28.583%] | NO |
| 1.50-2.00 | 166 | +0.125% | [-0.789%,+1.059%] | NO | +0.000% | [-13.501%,+13.428%] | NO |
| 2.00-3.00 | 805 | -0.843% | [-1.399%,-0.288%] | NO | -14.632% | [-22.850%,-6.044%] | NO |
| 3.00-5.00 | 1,825 | +0.170% | [-0.254%,+0.590%] | NO | -8.518% | [-15.977%,-1.097%] | NO |
| > 5.00 | 1,000 | +1.975% | [+1.141%,+2.830%] | YES | -7.314% | [-20.698%,+7.009%] | NO |

**By league:**

| League | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|--------|---|------|-----------|-------|-----------|-----------|--------|
| E0 ⚠️<500 | 495 | +0.592% | [-0.414%,+1.622%] | NO | -18.606% | [-33.520%,-2.530%] | NO |
| E1 | 597 | -0.040% | [-0.824%,+0.749%] | NO | -4.235% | [-18.288%,+10.307%] | NO |
| E2 | 546 | +0.728% | [-0.100%,+1.575%] | NO | -0.225% | [-14.407%,+14.606%] | NO |
| E3 | 691 | +0.311% | [-0.367%,+1.001%] | NO | -5.223% | [-17.699%,+7.650%] | NO |
| I1 ⚠️<500 | 331 | +1.124% | [+0.050%,+2.198%] | YES | -15.619% | [-32.807%,+2.287%] | NO |
| I2 ⚠️<500 | 400 | +0.454% | [-0.579%,+1.480%] | NO | -17.005% | [-32.708%,-0.587%] | NO |
| SP1 ⚠️<500 | 286 | +0.249% | [-0.844%,+1.371%] | NO | -15.811% | [-33.280%,+2.985%] | NO |
| SP2 ⚠️<500 | 462 | +0.257% | [-0.739%,+1.222%] | NO | -5.974% | [-20.364%,+8.976%] | NO |

**By opening overround quartile:**

| Quartile | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|----------|---|------|-----------|-------|-----------|-----------|--------|
| Q1 (lowest) | 1,060 | +0.550% | [-0.015%,+1.144%] | NO | -6.124% | [-16.594%,+4.039%] | NO |
| Q2 | 950 | +0.290% | [-0.312%,+0.898%] | NO | -11.919% | [-22.064%,-1.338%] | NO |
| Q3 | 921 | +0.121% | [-0.572%,+0.824%] | NO | -9.783% | [-20.640%,+1.280%] | NO |
| Q4 (highest) | 877 | +0.747% | [+0.022%,+1.490%] | YES | -9.008% | [-20.573%,+2.863%] | NO |

### Task A Verdict

*Per the bar: a subset is 'persistent edge' only if closing-line ROI CI > 0 in BOTH windows with ≥500 bets.*

**No subset clears the bar.** The h2h CLV signal is real but diffuse — no identifiable pocket where the market failed to fully correct DC's edge by the close.

**Direction finding (brief A.1 direct answer):** The CLV is concentrated in **home** selections — the only direction with CI > 0 in both windows (+0.825% in 2022, +0.535% in 2023). Away CLV is positive in both windows but CI includes zero in both (+0.566% [−0.041%, +1.197%] in 2022; +0.392% [−0.116%, +0.899%] in 2023). This contradicts the hypothesis that DC's edge comes disproportionately from away selections drifting out. Away odds do drift out on average (mean +0.021 from Phase 5), but DC's positive CLV is driven by home picks, not away picks.

## Task B — ou25 Directional Diagnosis

*Three variants: baseline (DC selection), flip (bet opposite direction), under-only. Metric: CLV% and settling-at-close ROI per window.*

| Window | Variant | N | CLV% | CLV 95%CI | CI>0? | Close ROI | ROI 95%CI | ROI>0? |
|--------|---------|---|------|-----------|-------|-----------|-----------|--------|
| 2022 | Baseline (over) | 865 | -0.596% | [-1.017%,-0.173%] | NO | -9.431% | [-16.421%,-2.449%] | NO |
| 2022 | Baseline (under) | 870 | +0.335% | [-0.092%,+0.756%] | NO | -4.241% | [-10.705%,+2.376%] | NO |
| 2022 | Baseline (total) | 1,735 | -0.129% | [-0.423%,+0.170%] | NO | -6.829% | [-11.429%,-2.131%] | NO |
| 2022 | Flip total | 1,735 | +0.118% | [-0.170%,+0.408%] | NO | -2.669% | [-7.273%,+1.880%] | NO |
| 2022 | Under-only | 870 | +0.335% | [-0.092%,+0.756%] | NO | -4.241% | [-10.705%,+2.376%] | NO |
| 2023 | Baseline (over) | 779 | -1.752% | [-2.202%,-1.289%] | NO | -8.598% | [-16.568%,-0.450%] | NO |
| 2023 | Baseline (under) | 1,598 | -0.119% | [-0.419%,+0.180%] | NO | -8.783% | [-13.725%,-3.736%] | NO |
| 2023 | Baseline (total) | 2,377 | -0.654% | [-0.908%,-0.397%] | NO | -8.723% | [-13.090%,-4.349%] | NO |
| 2023 | Flip total | 2,377 | +0.471% | [+0.251%,+0.681%] | YES | -2.873% | [-6.425%,+0.623%] | NO |
| 2023 | Under-only | 1,598 | -0.119% | [-0.419%,+0.180%] | NO | -8.783% | [-13.725%,-3.736%] | NO |

### Task B Verdict

- Baseline over-bet share: 49.9% (2022), 32.8% (2023) — over-bias not dominant.
- Flip variant (trade-with-drift) ROI>0 in both windows: NO.
- Under-only ROI>0 in both windows: NO.

**Diagnosis: the ou25 model has no directional edge, but the over-picks do leak CLV.**

The flip (trade-with-drift) cuts the bleed from −6.8%/−8.7% to −2.7%/−2.9% by capturing the systematic over-drift — most of the recoverable CLV leak is recouped this way. But flip CLV clears zero only in 2023 (+0.471%), not 2022 (+0.118%), and closing ROI stays negative in both variants and both windows. The DC OU25 component produces no out-of-sample signal in either direction; the flip improvement is mechanical drift-capture, not genuine edge. The right framing is not "inverted signal" — it is "no signal, plus a structural CLV drag from selecting over picks into a market that already knows to drift them out."

## Task C — League Regime as Direct Model Input

*Features (trailing, leakage-safe): hw_rate, hhi, avg_goals. Applied as GLM multipliers on DC's λ/μ. Walk-forward 2022 + 2023.*

### C1 — Cross-League Regime Distribution (as of 2022-01-01)

| League | BTTS | O2.5 | HW rate | Avg goals | HHI | N hist |
|--------|------|------|---------|-----------|-----|--------|
| E0 | 0.482 | 0.507 | 0.411 | 2.713 | 0.0561 | 735 |
| E1 | 0.470 | 0.435 | 0.411 | 2.397 | 0.0365 | 1,085 |
| E2 | 0.514 | 0.494 | 0.429 | 2.644 | 0.0380 | 968 |
| E3 | 0.510 | 0.460 | 0.416 | 2.447 | 0.0362 | 963 |
| I1 | 0.604 | 0.599 | 0.415 | 3.078 | 0.0594 | 780 |
| I2 | 0.514 | 0.434 | 0.389 | 2.468 | 0.0414 | 763 |
| SP1 | 0.508 | 0.446 | 0.427 | 2.472 | 0.0547 | 762 |
| SP2 | 0.464 | 0.366 | 0.428 | 2.151 | 0.0393 | 939 |

BTTS range: 0.464–0.604 (1.3x). AvgGoals: 2.151–3.078 (1.4x). HHI: 0.0362–0.0594.

**Heterogeneity note:** The brief expected larger spread (Phase 1b cited "3x goal range, 8x BTTS range") — but those figures were from the full multi-continent production dataset, likely 100+ leagues. The fdco-8 are all premier-tier Western European leagues (England, Italy, Spain) and are structurally similar. A 1.3x BTTS range and 1.4x goal range across these 8 leagues is correct and expected; an 8x BTTS range is also mathematically implausible since BTTS rates are bounded ~0.4–0.65. The heterogeneity present here is real but modest — which matters for C3 interpretation below.

### C2 — Raw Model Quality (vs Phase 3 DC Baseline)

| Window | Market | Model | N | AUC | Log-loss | Brier |
|--------|--------|-------|---|-----|----------|-------|
| 2022 | H2H | DC (base) | 3,540 | 0.59028 | 1.05372 | 0.63459 |
| 2022 | H2H | DC+LeagueRegime | 3,540 | 0.58925 | 1.05529 | 0.63551 |
| 2022 | OU25 | DC (base) | 3,540 | 0.54074 | 0.69704 | 0.25158 |
| 2022 | OU25 | DC+LeagueRegime | 3,540 | 0.54409 | 0.70604 | 0.25527 |
| 2023 | H2H | DC (base) | 5,456 | 0.60259 | 1.04444 | 0.6279 |
| 2023 | H2H | DC+LeagueRegime | 5,456 | 0.60307 | 1.04491 | 0.62823 |
| 2023 | OU25 | DC (base) | 5,456 | 0.54936 | 0.69772 | 0.2517 |
| 2023 | OU25 | DC+LeagueRegime | 5,456 | 0.5565 | 0.70093 | 0.25286 |

### C3 — EV Backtest (pre-registered bar)

| Window | Market | Model | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|--------|--------|-------|--------|-----|--------|-------|-------|-------|
| 2022 | H2H | DC (base) | 3,117 | -10.6% | [-16.5%,-4.6%] | YES | NO | FAIL |
| 2022 | H2H | DC+LR | 3,236 | -9.7% | [-15.3%,-3.7%] | YES | NO | FAIL |
| 2022 | OU25 | DC (base) | 1,744 | -7.0% | [-11.7%,-2.4%] | YES | NO | FAIL |
| 2022 | OU25 | DC+LR | 2,104 | -6.7% | [-10.7%,-2.6%] | YES | NO | FAIL |
| 2023 | H2H | DC (base) | 3,824 | -8.9% | [-14.3%,-3.5%] | YES | NO | FAIL |
| 2023 | H2H | DC+LR | 3,938 | -9.5% | [-14.8%,-4.3%] | YES | NO | FAIL |
| 2023 | OU25 | DC (base) | 2,392 | -9.3% | [-13.6%,-5.0%] | YES | NO | FAIL |
| 2023 | OU25 | DC+LR | 2,470 | -9.5% | [-13.5%,-5.3%] | YES | NO | FAIL |

### Task C Verdict

- H2H EV bar: 0/2 windows pass. OU25 EV bar: 0/2 windows pass.
- H2H AUC: flat or marginal regression in both windows (2022: 0.59028→0.58925, 2023: 0.60259→0.60307 — effectively unchanged).
- OU25 AUC rises in **both** windows (2022: 0.54074→0.54409; 2023: 0.54936→0.55650), but log-loss and Brier **worsen** in both — ranking slightly improved, calibration degraded, no net usable gain.
- GLM feature selection: only 3 of the brief's 5 features entered the GLM (btts_rate and o25_rate are near-collinear with avg_goals, so the optimizer absorbs their contribution into avg_goals). avg_goals was highly significant (p<10⁻⁷); HHI marginally significant (p≈0.01 in both windows) — the only in-sample signal with a plausible structural story (competitive imbalance predicts stronger-team goal rates). Yet HHI's in-sample signal produced no out-of-sample lift.

**Verdict:** League-regime features do not improve DC. The per-league fit already encodes structural goal-level and competition-balance differences; the GLM correction layer is fitting noise. The modest heterogeneity in this 8-league fdco subset (C1 above) further reduces the odds of meaningful improvement here compared to a wider league set.

## Task D — Edge Gap Quantification

### D1 — Measured Margins

| Source | Type | Overround |
|--------|------|-----------|
| B365 | Opening h2h | 5.520% |
| B365 | Closing h2h (n=17,918) | 5.572% |
| Pinnacle | Closing h2h (n=17,905) | 3.056% |

### D2 — True Gap Analysis (anchored on realized ROI)

**The correct anchor for "gap to breakeven" is the realized open-odds ROI, not the CLV-minus-margin shortfall.** These two measures answer different questions:

- `CLV − margin` gives the EV *assuming* DC's selections are as well-calibrated as the closing line (i.e., selection penalty = 0)
- Realized ROI measures actual PnL on selected bets settled at opening odds — the true profitability gap

The difference between them is the **selection penalty**: DC's picks underperform the closing-line proxy. The pure-DC 2022 window shows a larger penalty than the market-blended 2023 window, which is expected — Shin blending imports market calibration and reduces overconfidence bias.

**Decomposition (realized EV = CLV − closing_margin − selection_penalty):**

| Window | Realized ROI | CLV | B365 closing margin | Selection penalty |
|--------|-------------|-----|---------------------|-------------------|
| 2022 | −10.6% | +0.675% | 5.572% | 5.70pp |
| 2023 | −8.9% | +0.427% | 5.572% | 3.76pp |

Check (2022): +0.675% − 5.572% − 5.70% = −10.60% ✓  
Check (2023): +0.427% − 5.572% − 3.76% = −8.91% ✓

**Measured margins:**

| Source | Type | Overround |
|--------|------|-----------|
| B365 | Opening h2h | 5.520% |
| B365 | Closing h2h | 5.572% |
| Pinnacle | Closing h2h | 3.056% |

**Gap to breakeven (anchored correctly on realized ROI):**

| Reference | Realized ROI | Gap to break-even | Decomposed as |
|-----------|-------------|-------------------|---------------|
| vs B365 open | −10.6% / −8.9% | ~10pp | margin (~5.5pp) + selection penalty (~4–6pp) |
| vs Pinnacle close | −10.6% / −8.9% | ~8pp | Pinnacle margin (~3pp) + selection penalty (~5–6pp) |

The previous framing ("5.02% CLV shortfall vs B365") understated the gap by ~2× because it omitted the selection penalty. The true deficit is ~10pp against B365 and ~8pp against Pinnacle, decomposed into two separate components that must both be closed.

### D3 — Honest Closability Assessment

*This is a judgment call, not a test result, informed by Phases 1–6.*

To reach profitability against B365 requires closing an ~10pp realized-ROI gap — composed of two independent problems:

1. **The margin problem (~5.5pp vs B365):** Requires consistently generating CLV > 5.5%. Current blended CLV is 0.55% — 10× below this level.
2. **The selection penalty (~4–6pp):** DC's picks underperform even the devigged closing-line probability. Pure-DC (2022) shows a ~5.7pp penalty; blending with Shin (2023) reduces it to ~3.8pp. This penalty is not fixed by better feature engineering — it reflects that DC's probability estimates, while directionally correct (positive CLV), are miscalibrated enough that the selected outcomes underperform relative to the market's closing probability.

**Evidence from Phases 1–6 on remaining levers:**

- *Form features (Phase 2):* No improvement over 9-feature baseline.
- *Weather (Phase 5):* Not significant (p=0.32–0.64 for cold-night mechanism).
- *Referee (Phase 5):* Significant predictor (p<10⁻²⁸) but only ±1.5pp ROI effect — helpful but not gap-closing.
- *League regime (Phase 6):* Did not improve prediction quality vs per-league DC base.
- *Market-movement priors (Phase 5–6):* h2h CLV positive at +0.55%, but this is ~10× below the combined margin+penalty gap vs B365.

**Judgment:** The gap is not closable with public-data-only models on these markets. Two separate problems — the margin and the selection penalty — both need substantial improvement, and all tested levers address neither convincingly. The DCM already captures most of the identifiable structure; the selection penalty is a calibration shortfall the model can't correct through feature additions alone. To reach break-even at Pinnacle prices (~3% margin) would still require closing ~5pp of selection penalty — which requires either (a) dramatically better probability calibration or (b) selection criteria that filter only the most precisely calibrated subsets.

The more productive structural path is earlier-odds access (pre-opening vs. opening, where the market has processed less information), niche markets where opening efficiency is lower, or in-play where the model can respond to live state that the market reprices continuously.

