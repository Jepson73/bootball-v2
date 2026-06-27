# Phase 4b — Bookmaker Bias Hunt

**Validation pool:** 301 league-window pairs, 71,834 total candidate bets across 3 walk-forward windows.

## Z1: Favorite-Longshot Bias

### Z1a: All markets — naive flat-stake ROI by odds bucket

*(All outcomes with odds available, no filter, unit stake — this tests whether the bias exists in this dataset.)*

| Odds range | N bets | Naive ROI | 95% CI | Share of all candidates |
|------------|--------|-----------|--------|-------------------------|
| < 1.5 | 5,385 | -1.8% | [-3.3%, -0.3%] | 7.5% |
| 1.5–2.5 | 35,409 | -4.2% | [-5.2%, -3.2%] | 49.3% |
| 2.5–4.0 | 21,697 | -6.6% | [-8.6%, -4.7%] | 30.2% |
| 4.0–7.0 | 7,831 | -13.6% | [-17.6%, -9.2%] | 10.9% |
| 7.0+ | 1,512 | -23.2% | [-36.2%, -9.6%] | 2.1% |

### Z1b: h2h only — naive ROI by bucket

| Odds range | N bets | Naive ROI | 95% CI |
|------------|--------|-----------|--------|
| < 1.5 | 1,164 | +0.3% | [-3.0%, +3.6%] |
| 1.5–2.5 | 9,113 | -2.3% | [-4.4%, -0.2%] |
| 2.5–4.0 | 17,796 | -5.8% | [-7.9%, -3.7%] |
| 4.0–7.0 | 6,515 | -12.1% | [-16.6%, -7.5%] |
| 7.0+ | 1,364 | -21.2% | [-35.1%, -7.1%] |

### Z1c: Bootball (DC EV-filtered) bet concentration by odds bucket

*(Confirms where the EV filter actually placed bets — tests the longshot-trap hypothesis.)*

| Odds range | N bets selected | Share of Bootball bets | Naive ROI at these odds |
|------------|----------------|------------------------|-------------------------|
| < 1.5 | 73 | 0.4% | -1.8% |
| 1.5–2.5 | 6,008 | 36.4% | -4.2% |
| 2.5–4.0 | 5,451 | 33.0% | -6.6% |
| 4.0–7.0 | 4,027 | 24.4% | -13.6% |
| 7.0+ | 968 | 5.9% | -23.2% |

*Production placed bets (448 settled): h2h avg odds 6.43 (72 bets, win rate 20.8%), ou25 avg odds 2.75 (151 bets, win rate 38.4%), btts avg odds 2.28 (163 bets, win rate 45.4%), ou15 avg odds 3.26 (62 bets, win rate 40.3%).*

## Z2: League Liquidity Segmentation

**Tier definitions:**
- Tier 1 (top): EPL (39), La Liga (140), Serie A (135)
- Tier 2 (mid fdco): Championship (40), League One (41), League Two (42), Serie B (136), Segunda División (141)
- Tier 3 (long tail): all other leagues (fdco 2022/23 has no long-tail fixtures; these appear only in the 2025-26 production window)

### Z2a: All-market naive ROI by league tier

| Tier | N candidates | Naive ROI | 95% CI | N Bootball bets | Bootball ROI | Bootball CI |
|------|-------------|-----------|--------|-----------------|--------------|-------------|
| Top fdco (EPL, La Liga, Serie A) | 15,104 | -6.5% | [-8.6%, -4.3%] | 3574 | -13.5% | [-18.4%, -8.6%] |
| Mid fdco (Championship, L1/L2, Serie B, Segunda) | 32,574 | -5.9% | [-7.2%, -4.5%] | 8039 | -7.8% | [-11.0%, -4.6%] |
| Long tail (all other leagues) | 24,156 | -6.4% | [-8.0%, -4.8%] | 4914 | -7.9% | [-12.4%, -3.3%] |

### Z2b: By market

| Market | Tier | N | Naive ROI | 95% CI |
|--------|------|---|-----------|--------|
| h2h | Tier 1 (top) | 8,688 | -7.5% | [-10.6%, -4.3%] |
| h2h | Tier 2 (mid) | 19,206 | -6.0% | [-8.0%, -3.9%] |
| h2h | Tier 3 (long tail) | 8,058 | -6.5% | [-10.0%, -3.1%] |
| ou25 | Tier 1 (top) | 5,790 | -5.2% | [-7.7%, -2.7%] |
| ou25 | Tier 2 (mid) | 12,798 | -5.5% | [-7.2%, -3.8%] |
| ou25 | Tier 3 (long tail) | 5,374 | -5.3% | [-7.9%, -2.6%] |

## Z3: Naive Contrarian Walk-Forward Backtest

Strategy: flat-stake bet on **all outcomes where decimal odds ≤ threshold**, no model, no calibration, same three walk-forward windows.

*Pre-registered bar applies: 95% CI excludes zero (positive), ≥500 bets per window, ≥2 non-overlapping windows.*

### Z3 — H2H

| Threshold | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|-----------|--------|--------|-----|--------|-------|-------|-------|
| ≤1.5 | 2022 | 360 | -0.5% | [-6.6%, +5.6%] | NO | NO | FAIL |
| ≤1.5 | 2023 | 585 | -0.5% | [-5.3%, +4.4%] | YES | NO | FAIL |
| ≤1.5 | 2025-26 | 399 | +1.7% | [-4.0%, +7.3%] | NO | NO | FAIL |
| ≤1.5 | **ALL** | 1,344 | +0.1% | [-3.1%, +3.3%] | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.0 | 2022 | 1,664 | -0.7% | [-4.8%, +3.4%] | YES | NO | FAIL |
| ≤2.0 | 2023 | 2,704 | -3.4% | [-6.5%, -0.3%] | YES | NO | FAIL |
| ≤2.0 | 2025-26 | 1,463 | +1.3% | [-2.7%, +5.4%] | YES | NO | FAIL |
| ≤2.0 | **ALL** | 5,831 | -1.4% | [-3.5%, +0.7%] | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.5 | 2022 | 3,167 | -2.8% | [-6.2%, +0.6%] | YES | NO | FAIL |
| ≤2.5 | 2023 | 4,854 | -3.2% | [-5.9%, -0.5%] | YES | NO | FAIL |
| ≤2.5 | 2025-26 | 2,671 | +2.0% | [-1.7%, +5.5%] | YES | NO | FAIL |
| ≤2.5 | **ALL** | 10,692 | -1.8% | [-3.6%, -0.0%] | — | — | BAR NOT MET |
| | | | | | | | |

### Z3 — OU25

| Threshold | Window | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|-----------|--------|--------|-----|--------|-------|-------|-------|
| ≤1.5 | 2022 | 241 | -3.7% | [-12.4%, +4.9%] | NO | NO | FAIL |
| ≤1.5 | 2023 | 767 | -0.5% | [-5.2%, +4.1%] | YES | NO | FAIL |
| ≤1.5 | 2025-26 | 647 | -0.1% | [-5.0%, +4.8%] | YES | NO | FAIL |
| ≤1.5 | **ALL** | 1,655 | -0.8% | [-4.1%, +2.3%] | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.0 | 2022 | 4,415 | -3.9% | [-6.4%, -1.3%] | YES | NO | FAIL |
| ≤2.0 | 2023 | 6,601 | -2.1% | [-4.2%, -0.0%] | YES | NO | FAIL |
| ≤2.0 | 2025-26 | 3,434 | -2.5% | [-5.2%, +0.2%] | YES | NO | FAIL |
| ≤2.0 | **ALL** | 14,450 | -2.8% | [-4.1%, -1.4%] | — | — | BAR NOT MET |
| | | | | | | | |
| ≤2.5 | 2022 | 6,890 | -5.2% | [-7.4%, -2.9%] | YES | NO | FAIL |
| ≤2.5 | 2023 | 10,329 | -5.2% | [-7.0%, -3.4%] | YES | NO | FAIL |
| ≤2.5 | 2025-26 | 5,199 | -3.3% | [-5.9%, -0.8%] | YES | NO | FAIL |
| ≤2.5 | **ALL** | 22,418 | -4.8% | [-6.0%, -3.5%] | — | — | BAR NOT MET |
| | | | | | | | |

## Z4: Line-Timing Data Limitation

Closing-line-value (CLV) and line-movement analysis cannot currently be tested. The project has a single odds snapshot per fixture — either at ingestion time or whenever the odds poller last captured them — not a time series of how each line moved from opening to kickoff.

The CLV infrastructure added in migration 024 (`placed_bets.closing_odds`, `placed_bets.clv_pct`, captured by `odds_poll.py::capture_closing_lines`) will collect near-kickoff snapshots going forward, but the historical backtest window (2019–2026) has no multi-snapshot odds history. Testing timing-based edge will require forward accumulation of live CLV data — a minimum of one full season of active betting before any meaningful signal can be assessed.

This limitation is flagged; no timing analysis has been attempted.
