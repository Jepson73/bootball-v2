# Phase 5 Task 2 — Wave 2: Weather & Referee

> **Scope note:** 2025-26 production fixtures have `venue=NULL` in `football.db` — weather features cannot be computed for that window. Walk-forward validation uses the fdco 2022 and 2023 windows only (same constraint as Task 1 CLV; ≥2 windows required, both must pass).

## 2.1 Venue Geocoding

- Distinct venue names in fdco 8 leagues (2019-2024): **334**
- Successfully geocoded: **334** (100%)
- Unique coordinate buckets (2dp): 239
- Source: Nominatim OpenStreetMap (1.1 req/sec rate limit)
- Fallback chain: venue name → team name + 'stadium' → team name

## 2.2 Weather Backfill

- API: Open-Meteo Historical Archive (ERA5 reanalysis, free, no key)
- Date range: 2019-08-01 – 2024-06-30
- Variables: temperature_2m (°C), precipitation (mm/h), wind_speed_10m (m/s)
- API calls made: 239 (one per unique lat/lon bucket)
- Fixtures with weather matched: 17,907 / 18,060 (99%)

## 2.3 Feature Design

All features are leakage-safe (determined by kickoff date/location, known before the match).

| Feature | Description | Applied to |
|---------|-------------|------------|
| `wind_speed` | Wind speed at kickoff (m/s) | Both λ_home and μ_away (reduces total goals) |
| `precipitation` | Precipitation at kickoff (mm/h) | Both λ_home and μ_away |
| `temp_dev_away` | Match temperature minus away team's average home temperature | μ_away only |
| `ref_ratio` | Referee's historical (total goals / match) ÷ league average | Both λ_home and μ_away |

Feature families per the brief:
1. **Absolute conditions** (wind, precipitation at kickoff)
2. **Deviation from baseline** (`temp_dev_away` = cold-weather-away-team handicap)
3. **Interaction with style**: style features from Wave 1 are in `feature_cache/` but not included here — the GLM tests if base weather conditions add value beyond DC's team-strength model, before layering style interactions.

## 2.4 Referee — Data Availability

Referee name is in `fixtures.referee` — already ingested, zero additional API cost.

Fill rates for fdco leagues:
- English EPL/Championship/L1/L2 (2019-2024): **100%**
- Italian Serie A/B (2019-2024): **100%**
- Spanish La Liga/Segunda (2019-2024): **100%**
- 2025 season (partial): 79-89% depending on league

Referee features computed using matches strictly prior to each fixture's date (no lookahead). Minimum 5 prior matches to include a referee; fixtures with unknown referee assigned the league average.

## 2.5 Walk-Forward Validation

### Covariate Model: Two-Stage Approach

- Stage 1: Base DC per-league (Phase 3 cached predictions, no retraining)
- Stage 2: Global Poisson-GLM correction fitted on training fixtures.
  `log(λ_adj) = log(λ_base) + const + β_wind×wind + β_precip×precip + β_ref×(ref_ratio−1)`
  `log(μ_adj) = log(μ_base) + const + β_wind×wind + β_precip×precip + β_ref×(ref_ratio−1) + β_temp×temp_dev_away`

### GLM Coefficients

**2022 window training fit:**

- n_training_fixtures: 8745
- Home (λ): wind=-0.0064, precip=0.0515, ref_ratio=0.8165 (p=0.077/0.000)
- Away (μ): wind=0.0012, precip=-0.0204, temp_dev=0.0015, ref_ratio=1.1591

**2023 window training fit:**

- n_training_fixtures: 12341
- Home (λ): wind=-0.0065, precip=0.0440, ref_ratio=0.8240 (p=0.034/0.000)
- Away (μ): wind=0.0023, precip=-0.0092, temp_dev=0.0006, ref_ratio=1.1914

### Raw Model Quality vs Phase 3 DC Baseline

*(Base DC probs from Phase 3 cache; Wave 2 adjusted probs using GLM correction.)*

| Window | Market | Model | AUC | Log-loss |
|--------|--------|-------|-----|----------|
| 2022 | H2H | DC (Phase 3) | 0.5903 | 1.05372 |
| 2022 | OU25 | DC (Phase 3) | 0.5407 | 0.69704 |
| 2023 | H2H | DC (Phase 3) | 0.6026 | 1.04444 |
| 2023 | OU25 | DC (Phase 3) | 0.5494 | 0.69772 |

### EV Walk-Forward Backtest

*Pre-registered bar: 95% CI > 0, ≥500 bets/window, ≥2 windows pass.*

| Window | Market | Model | N bets | ROI | 95% CI | ≥500? | CI>0? | Pass? |
|--------|--------|-------|--------|-----|--------|-------|-------|-------|
| 2022 | H2H | DC base | 3,117 | -10.6% | [-16.5%,-4.6%] | YES | NO | FAIL |
| 2022 | H2H | Wave 2 | 3,178 | -9.0% | [-15.1%,-3.1%] | YES | NO | FAIL |
| 2023 | H2H | DC base | 3,824 | -8.9% | [-14.3%,-3.5%] | YES | NO | FAIL |
| 2023 | H2H | Wave 2 | 3,927 | -9.4% | [-14.7%,-4.2%] | YES | NO | FAIL |

*H2H DC base: 0/2 windows pass; Wave 2: 0/2 windows pass.*

| 2022 | OU25 | DC base | 1,744 | -7.0% | [-11.7%,-2.4%] | YES | NO | FAIL |
| 2022 | OU25 | Wave 2 | 2,222 | -6.6% | [-10.4%,-2.8%] | YES | NO | FAIL |
| 2023 | OU25 | DC base | 2,392 | -9.3% | [-13.6%,-5.0%] | YES | NO | FAIL |
| 2023 | OU25 | Wave 2 | 2,754 | -9.1% | [-12.9%,-5.3%] | YES | NO | FAIL |

*OU25 DC base: 0/2 windows pass; Wave 2: 0/2 windows pass.*

## Phase 5 Task 2 Verdict

- H2H Wave 2: BAR NOT MET (0/2 windows pass)
- OU25 Wave 2: BAR NOT MET (0/2 windows pass)
