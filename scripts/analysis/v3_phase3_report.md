
---
# Phase 3 — Dixon-Coles Goal Model (Approach B)

**Run date:** 2026-06-26 22:02

## Data coverage note

fdco historical odds (2019–2024) cover h2h + ou25 only; no btts or ou15. Therefore btts and ou15 cannot satisfy the ≥2 non-overlapping windows bar — this is a data-coverage artifact, not a model failure. The EV bar applies to **h2h and ou25** across windows 2022+2023+2025-26.

## W1: Per-league DC parameters

### Window 2022

- Leagues fitted: 8 / 8 (no-data: 0)
- ρ: mean=-0.0482, std=0.0458, range=[-0.1131, 0.0340]
- γ (home adv): mean=1.2059, std=0.0576
- Best ξ: 0.0 (inner-CV NLL grid: 0.0=2.8703, 0.5=2.8712, 1.0=2.8744, 1.5=2.8792, 2.0=2.8846, 3.0=2.8969, 4.0=2.9108)
- Thin-data: 14.3% val fixtures have ≥1 unseen team

### Window 2023

- Leagues fitted: 8 / 8 (no-data: 0)
- ρ: mean=-0.0416, std=0.0391, range=[-0.0920, 0.0274]
- γ (home adv): mean=1.2247, std=0.0583
- Best ξ: 0.5 (inner-CV NLL grid: 0.0=2.8262, 0.5=2.8253, 1.0=2.8290, 1.5=2.8351, 2.0=2.8421, 3.0=2.8561, 4.0=2.8706)
- Thin-data: 15.5% val fixtures have ≥1 unseen team

### Window 2025-26

- Leagues fitted: 279 / 285 (no-data: 6)
- ρ: mean=0.0047, std=0.1716, range=[-0.5000, 0.5000]
- γ (home adv): mean=1.2377, std=0.1680
- Best ξ: 0.5 (inner-CV NLL grid: 0.0=2.8756, 0.5=2.8730, 1.0=2.8783, 1.5=2.8852, 2.0=2.8912, 3.0=2.9054, 4.0=2.9193)
- Thin-data: 23.4% val fixtures have ≥1 unseen team

## W2: Poisson vs Dixon-Coles nested comparison

| Window | Market | DC AUC | Poi AUC | DC log-loss | Poi log-loss | DC Brier | Poi Brier |
|--------|--------|--------|---------|-------------|--------------|---------|----------|
| 2022 | h2h | 0.5903 | 0.5890 | 1.05372 | 1.05399 | 0.21153 | 0.21160 |
| 2022 | ou25 | 0.5407 | 0.5407 | 0.69704 | 0.69719 | 0.25158 | 0.25165 |
| 2022 | btts | 0.5171 | 0.5172 | 0.70034 | 0.70048 | 0.25344 | 0.25350 |
| 2022 | ou15 | 0.5432 | 0.5443 | 0.60296 | 0.60254 | 0.20566 | 0.20561 |
| 2023 | h2h | 0.6026 | 0.5987 | 1.04444 | 1.04539 | 0.20930 | 0.20952 |
| 2023 | ou25 | 0.5494 | 0.5493 | 0.69772 | 0.69764 | 0.25170 | 0.25167 |
| 2023 | btts | 0.5281 | 0.5282 | 0.70106 | 0.70177 | 0.25356 | 0.25389 |
| 2023 | ou15 | 0.5335 | 0.5348 | 0.58917 | 0.58923 | 0.19977 | 0.19986 |
| 2025-26 | h2h | 0.5897 | 0.5871 | 1.08688 | 1.08711 | 0.21452 | 0.21461 |
| 2025-26 | ou25 | 0.5950 | 0.5949 | 0.70322 | 0.70473 | 0.24854 | 0.24878 |
| 2025-26 | btts | 0.5520 | 0.5517 | 0.71155 | 0.71322 | 0.25341 | 0.25413 |
| 2025-26 | ou15 | 0.5881 | 0.5878 | 0.55887 | 0.56087 | 0.18422 | 0.18482 |

*Wave 1 baselines (V1a, 29-feat-N5 avg): h2h AUC=0.5844, ou25 AUC=0.5392*

## W3: Cross-market internal consistency

All four markets are derived from the same joint P(i,j) matrix, so invariants hold by construction: P(H)+P(D)+P(A)≈1, P(OU15)≥P(BTTS), P(OU25)≤P(OU15).

### Window 2022

- h2h normalisation RMSE: 0.00e+00 (should be ~0)
- P(OU15)<P(BTTS) violations: 0 / 3540
- P(OU25)>P(OU15) violations: 0 / 3540
- Independent Shin violations (P(OU15)<P(BTTS)): 0 / 0 (0.0%)

Sample predictions:

| fixture | P(H) | P(D) | P(A) | P(OU25) | P(OU15) | P(BTTS) | λ | μ |
|---------|------|------|------|---------|---------|---------|---|---|
| 710756 | 0.186 | 0.227 | 0.587 | 0.556 | 0.791 | 0.540 | 0.99 | 1.92 |
| 710765 | 0.189 | 0.224 | 0.587 | 0.570 | 0.800 | 0.552 | 1.02 | 1.95 |
| 715716 | 0.468 | 0.246 | 0.285 | 0.497 | 0.740 | 0.526 | 1.53 | 1.13 |
| 715718 | 0.407 | 0.252 | 0.341 | 0.492 | 0.737 | 0.532 | 1.39 | 1.25 |
| 715720 | 0.436 | 0.258 | 0.306 | 0.457 | 0.709 | 0.500 | 1.39 | 1.11 |

### Window 2023

- h2h normalisation RMSE: 0.00e+00 (should be ~0)
- P(OU15)<P(BTTS) violations: 0 / 5456
- P(OU25)>P(OU15) violations: 0 / 5456
- Independent Shin violations (P(OU15)<P(BTTS)): 0 / 0 (0.0%)

Sample predictions:

| fixture | P(H) | P(D) | P(A) | P(OU25) | P(OU15) | P(BTTS) | λ | μ |
|---------|------|------|------|---------|---------|---------|---|---|
| 881020 | 0.472 | 0.262 | 0.266 | 0.431 | 0.689 | 0.472 | 1.42 | 0.99 |
| 868123 | 0.611 | 0.213 | 0.176 | 0.597 | 0.818 | 0.563 | 2.07 | 1.02 |
| 875789 | 0.420 | 0.253 | 0.327 | 0.524 | 0.765 | 0.560 | 1.49 | 1.28 |
| 875791 | 0.244 | 0.273 | 0.483 | 0.405 | 0.670 | 0.449 | 0.91 | 1.40 |
| 875793 | 0.365 | 0.246 | 0.389 | 0.564 | 0.793 | 0.594 | 1.45 | 1.50 |

### Window 2025-26

- h2h normalisation RMSE: 0.00e+00 (should be ~0)
- P(OU15)<P(BTTS) violations: 0 / 3029
- P(OU25)>P(OU15) violations: 0 / 3029
- Independent Shin violations (P(OU15)<P(BTTS)): 0 / 2946 (0.0%)

Sample predictions:

| fixture | P(H) | P(D) | P(A) | P(OU25) | P(OU15) | P(BTTS) | λ | μ |
|---------|------|------|------|---------|---------|---------|---|---|
| 1387613 | 0.510 | 0.250 | 0.240 | 0.484 | 0.735 | 0.508 | 1.60 | 1.01 |
| 1387531 | 0.699 | 0.172 | 0.129 | 0.652 | 0.848 | 0.556 | 2.42 | 0.94 |
| 1469700 | 0.509 | 0.254 | 0.237 | 0.457 | 0.712 | 0.484 | 1.54 | 0.96 |
| 1378191 | 0.458 | 0.244 | 0.298 | 0.608 | 0.827 | 0.626 | 1.76 | 1.39 |
| 1494130 | 0.189 | 0.203 | 0.608 | 0.598 | 0.812 | 0.562 | 1.05 | 2.05 |

## W4: Walk-forward EV backtest

| Window | Market | n bets | ROI | CI lo | CI hi | CI>0 | ≥500 | Pass/Fail |
|--------|--------|--------|-----|-------|-------|------|------|-----------|
| 2022 | h2h | 3117 | -0.1063 | -0.1669 | -0.0489 | NO | YES | FAIL (CI includes zero: [-0.1669,-0.0489]) |
| 2022 | ou25 | 1744 | -0.0698 | -0.116 | -0.0245 | NO | YES | FAIL (CI includes zero: [-0.116,-0.0245]) |
| 2022 | btts | — | — | — | — | — | — | N/A (no odds this window) |
| 2022 | ou15 | — | — | — | — | — | — | N/A (no odds this window) |
| 2023 | h2h | 3824 | -0.0888 | -0.1424 | -0.0356 | NO | YES | FAIL (CI includes zero: [-0.1424,-0.0356]) |
| 2023 | ou25 | 2276 | -0.0882 | -0.1304 | -0.0485 | NO | YES | FAIL (CI includes zero: [-0.1304,-0.0485]) |
| 2023 | btts | — | — | — | — | — | — | N/A (no odds this window) |
| 2023 | ou15 | — | — | — | — | — | — | N/A (no odds this window) |
| 2025-26 | h2h | 1974 | -0.0765 | -0.1567 | 0.0091 | NO | YES | FAIL (CI includes zero: [-0.1567,0.0091]) |
| 2025-26 | ou25 | 826 | -0.0631 | -0.1403 | 0.0095 | NO | YES | FAIL (CI includes zero: [-0.1403,0.0095]) |
| 2025-26 | btts | 455 | -0.0596 | -0.1559 | 0.0431 | NO | NO | FAIL (<500 bets, n=455) |
| 2025-26 | ou15 | 807 | -0.096 | -0.218 | 0.0292 | NO | YES | FAIL (CI includes zero: [-0.218,0.0292]) |

## Phase 3 Verdict

- **h2h**: 0/3 windows pass → **FAIL** (bar: ≥2 windows, 95% CI>0, ≥500 bets)
- **ou25**: 0/3 windows pass → **FAIL** (bar: ≥2 windows, 95% CI>0, ≥500 bets)
- **btts/ou15**: N/A — single window coverage only (fdco 2019-2024 has no btts/ou15 odds)

*Pre-registered bar (locked before seeing results): 95% CI excludes zero, ≥500 bets/market/window, ≥2 non-overlapping windows.*
