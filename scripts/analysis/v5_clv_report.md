# Phase 5 — Closing-Line / CLV Analysis

> **Scope note:** Closing-line data exists only for the fdco windows (2022 + 2023). The 2025-26 production pool was assembled from API-Football snapshots with no historical close series. Because ≥2 non-overlapping windows are required, **both fdco windows must pass** — there is no third window as slack.

## 1.1 Closing-Line Extraction

- CSV files processed: 40 (all cached at `scripts/analysis/fdco_cache/`)
- Fixtures matched with closing h2h odds: 8,952
- Fixtures matched with closing ou25 odds: 8,952
- Bookmaker priority (open and close): B365 → PS → Avg → Max
- Stored in `fixture_odds_closing` table (separate from `fixture_odds` to avoid contaminating `MAX()` aggregation in V4 loader)

## 1.2 DC Re-Validated Against Closing Line

Comparison: opening-line Shin probabilities (Phase 3 original) vs closing-line Shin probabilities. DC model probabilities unchanged; only the market reference changes.

*Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows.*

### H2H

| Window | Reference | N bets | ROI | 95% CI | CI>0? |
|--------|-----------|--------|-----|--------|-------|
| 2022 | open | 3,117 | -10.6% | [-16.5%,-4.6%] | NO |
| 2022 | close | 3,173 | -8.3% | [-14.2%,-2.5%] | NO |
| 2023 | open | 3,824 | -8.9% | [-14.3%,-3.5%] | NO |
| 2023 | close | 3,967 | -11.3% | [-16.5%,-5.9%] | NO |

### OU25

| Window | Reference | N bets | ROI | 95% CI | CI>0? |
|--------|-----------|--------|-----|--------|-------|
| 2022 | open | 1,744 | -7.0% | [-11.7%,-2.4%] | NO |
| 2022 | close | 1,865 | -6.3% | [-11.0%,-1.8%] | NO |
| 2023 | open | 2,392 | -9.3% | [-13.6%,-5.0%] | NO |
| 2023 | close | 2,597 | -8.8% | [-13.0%,-4.7%] | NO |

## 1.3 Direct CLV Test

Selection criterion: **opening-line EV filter** (same as Phase 3 DC backtest — as a real bettor would act before the close). CLV% = (opening_price − closing_price) / closing_price, matching `odds_poll.py:383`. Positive = we got better price than the market settled on.

*Pre-registered bar: 95% CI > 0 (positive), ≥500 bets/window, ≥2 windows.*

### H2H

| Window | N selected | N with close | Mean CLV% | 95% CI | ≥500? | CI>0? | Pass? |
|--------|------------|--------------|-----------|--------|-------|-------|-------|
| 2022 | 3,117 | 3,103 | +0.68% | [+0.30%,+1.08%] | YES | YES | PASS |
| 2023 | 3,824 | 3,808 | +0.43% | [+0.11%,+0.75%] | YES | YES | PASS |

*H2H CLV verdict: **BAR MET** (2/2 windows pass)*

### OU25

| Window | N selected | N with close | Mean CLV% | 95% CI | ≥500? | CI>0? | Pass? |
|--------|------------|--------------|-----------|--------|-------|-------|-------|
| 2022 | 1,744 | 1,735 | -0.13% | [-0.42%,+0.17%] | YES | NO | FAIL |
| 2023 | 2,392 | 2,377 | -0.65% | [-0.91%,-0.40%] | YES | NO | FAIL |

*OU25 CLV verdict: BAR NOT MET (0/2 windows pass)*

## 1.4 Market Movement Context

Mean odds movement from open to close (positive = odds lengthened, negative = shortened). All fdco 2022+2023 fixtures combined.

### H2H Movement

| Outcome | N | Mean move | Mean |move| | % shortened | % lengthened |
|---------|---|-----------|-------------|-------------|---------------|
| home | 8,952 | +0.006 | 0.174 | 42.8% | 41.3% |
| draw | 8,952 | -0.002 | 0.137 | 38.2% | 30.7% |
| away | 8,952 | +0.021 | 0.341 | 40.0% | 44.2% |

### OU25 Movement

| Outcome | N | Mean move | Mean |move| | % shortened | % lengthened |
|---------|---|-----------|-------------|-------------|---------------|
| over | 8,952 | +0.029 | 0.097 | 35.4% | 46.5% |
| under | 8,952 | -0.004 | 0.083 | 46.7% | 35.6% |

