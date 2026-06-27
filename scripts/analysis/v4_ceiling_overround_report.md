# Phase 4 — Odds Ceiling + Overround Segmentation

## Task 2: Odds Ceiling Re-Evaluation (DC Model)

*(Wave 1 LightGBM individual predictions are not cached; results below are DC only. DC has higher AUC than Wave 1 in every window, so it is the more favourable test of whether an odds ceiling rescues model performance.)*

*Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows passing.*

### H2H

| Ceiling | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|---------|--------|--------|-----|--------|-------|-------|-------|
| No ceiling | 2022 | 3,117 | -10.6% | [-16.5%,-4.6%] | YES | NO | FAIL |
| No ceiling | 2023 | 3,824 | -8.9% | [-14.3%,-3.5%] | YES | NO | FAIL |
| No ceiling | 2025-26 | 1,974 | -7.6% | [-16.1%,+0.8%] | YES | NO | FAIL |
| No ceiling | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.0 | 2022 | 205 | -17.6% | [-29.4%,-5.5%] | NO | NO | FAIL |
| ≤2.0 | 2023 | 201 | -3.1% | [-15.3%,+9.4%] | NO | NO | FAIL |
| ≤2.0 | 2025-26 | 72 | -5.1% | [-25.1%,+15.0%] | NO | NO | FAIL |
| ≤2.0 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.5 | 2022 | 572 | -6.8% | [-15.1%,+2.1%] | YES | NO | FAIL |
| ≤2.5 | 2023 | 570 | -5.7% | [-14.3%,+2.9%] | YES | NO | FAIL |
| ≤2.5 | 2025-26 | 259 | +6.6% | [-6.3%,+19.8%] | NO | NO | FAIL |
| ≤2.5 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤3.0 | 2022 | 1,006 | -7.5% | [-15.0%,-0.1%] | YES | NO | FAIL |
| ≤3.0 | 2023 | 1,086 | -11.3% | [-18.2%,-4.2%] | YES | NO | FAIL |
| ≤3.0 | 2025-26 | 482 | +0.4% | [-10.1%,+10.9%] | NO | NO | FAIL |
| ≤3.0 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |

### OU25

| Ceiling | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|---------|--------|--------|-----|--------|-------|-------|-------|
| No ceiling | 2022 | 1,744 | -7.0% | [-11.7%,-2.4%] | YES | NO | FAIL |
| No ceiling | 2023 | 2,392 | -9.3% | [-13.6%,-5.0%] | YES | NO | FAIL |
| No ceiling | 2025-26 | 1,109 | -8.1% | [-15.2%,-1.3%] | YES | NO | FAIL |
| No ceiling | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.0 | 2022 | 1,064 | -5.4% | [-11.0%,+0.1%] | YES | NO | FAIL |
| ≤2.0 | 2023 | 894 | -3.0% | [-8.8%,+3.1%] | YES | NO | FAIL |
| ≤2.0 | 2025-26 | 195 | +6.6% | [-6.2%,+19.1%] | NO | NO | FAIL |
| ≤2.0 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.5 | 2022 | 1,686 | -7.0% | [-11.5%,-2.4%] | YES | NO | FAIL |
| ≤2.5 | 2023 | 2,083 | -8.2% | [-12.6%,-3.9%] | YES | NO | FAIL |
| ≤2.5 | 2025-26 | 678 | -2.3% | [-10.3%,+5.8%] | YES | NO | FAIL |
| ≤2.5 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |
| ≤3.0 | 2022 | 1,727 | -6.9% | [-11.4%,-2.3%] | YES | NO | FAIL |
| ≤3.0 | 2023 | 2,306 | -9.6% | [-13.8%,-5.3%] | YES | NO | FAIL |
| ≤3.0 | 2025-26 | 945 | -5.1% | [-12.5%,+2.1%] | YES | NO | FAIL |
| ≤3.0 | — | — | — | — | — | — | BAR NOT MET |
| | | | | | | | |

## Task 3: Bookmaker Margin Segmentation

Overround = sum of implied probabilities across all outcomes in a market. Higher overround = wider bookmaker margin. Quartile cuts computed across the full validation pool.

**Discovery set:** fdco windows (2022 + 2023). **Held-out replication:** production window (2025-26).

*Multiple-comparisons note: bucket cutoffs are computed on the full pool, results reported on the fdco windows as discovery. Any bucket passing the bar is tested on the production held-out window. Given the many slices across all phases, replication in the held-out window is required before treating any result as real.*

### H2H

Overround quartile cuts: Q1/Q2 split=1.0481, Q2/Q3=1.0530, Q3/Q4=1.0580

| Bucket | OR range | DC n (full) | DC ROI | DC CI | Naive n | Naive ROI | Naive CI |
|--------|----------|-------------|--------|-------|---------|-----------|----------|
| Q1 (tightest) | [0.000,1.048) | 2,374 | -5.3% | [-13.0%,+2.4%] | 8,985 | -4.8% | [-8.0%,-1.4%] |
| Q2 | [1.048,1.053) | 2,269 | -7.9% | [-14.5%,-1.0%] | 8,925 | -6.0% | [-9.0%,-2.9%] |
| Q3 | [1.053,1.058) | 2,205 | -8.3% | [-15.2%,-0.9%] | 9,051 | -6.3% | [-9.4%,-3.3%] |
| Q4 (loosest) | [1.058,9.900) | 2,067 | -16.2% | [-23.4%,-8.8%] | 8,991 | -8.7% | [-11.8%,-5.7%] |

**Discovery (fdco 2022+2023 only) vs Held-out (2025-26):**

| Bucket | DC fdco ROI | DC fdco CI | Naive fdco ROI | DC prod ROI | DC prod CI | Naive prod ROI |
|--------|-------------|-----------|----------------|-------------|-----------|----------------|
| Q1 (tightest) | -3.5% [FAIL] | [-14.2%,+7.4%] | -5.1% | -6.8% | [-17.4%,+4.1%] | -4.5% |
| Q2 | -7.2% [FAIL] | [-14.1%,-0.3%] | -6.0% | -19.6% | [-46.8%,+9.2%] | -6.6% |
| Q3 | -9.3% [FAIL] | [-16.3%,-2.0%] | -6.1% | +9.4% | [-23.8%,+45.5%] | -9.5% |
| Q4 (loosest) | -17.2% [FAIL] | [-25.4%,-8.8%] | -8.4% | -11.8% | [-27.3%,+4.4%] | -9.4% |

### OU25

Overround quartile cuts: Q1/Q2 split=1.0391, Q2/Q3=1.0556, Q3/Q4=1.0617

| Bucket | OR range | DC n (full) | DC ROI | DC CI | Naive n | Naive ROI | Naive CI |
|--------|----------|-------------|--------|-------|---------|-----------|----------|
| Q1 (tightest) | [0.000,1.039) | 1,384 | -5.0% | [-10.7%,+0.7%] | 5,700 | -3.2% | [-5.8%,-0.6%] |
| Q2 | [1.039,1.056) | 1,341 | -6.7% | [-12.6%,-0.7%] | 6,098 | -5.0% | [-7.5%,-2.6%] |
| Q3 | [1.056,1.062) | 1,279 | -13.5% | [-19.2%,-7.8%] | 5,952 | -6.4% | [-8.8%,-3.8%] |
| Q4 (loosest) | [1.062,9.900) | 1,241 | -8.4% | [-14.1%,-2.8%] | 6,212 | -6.9% | [-9.3%,-4.5%] |

**Discovery (fdco 2022+2023 only) vs Held-out (2025-26):**

| Bucket | DC fdco ROI | DC fdco CI | Naive fdco ROI | DC prod ROI | DC prod CI | Naive prod ROI |
|--------|-------------|-----------|----------------|-------------|-----------|----------------|
| Q1 (tightest) | -2.3% [FAIL] | [-9.2%,+4.8%] | -3.2% | -8.5% | [-17.7%,+0.8%] | -3.2% |
| Q2 | -5.1% [FAIL] | [-11.6%,+1.6%] | -4.5% | -12.9% | [-27.1%,+1.1%] | -6.5% |
| Q3 | -13.9% [FAIL] | [-19.8%,-8.3%] | -6.3% | -7.7% | [-31.3%,+17.1%] | -7.5% |
| Q4 (loosest) | -9.7% [FAIL] | [-15.7%,-3.9%] | -7.0% | +1.9% | [-17.5%,+21.8%] | -6.5% |

