# Phase 11 Integration Status — 2026-06-28

> **Task 4:** Document the current build, what's live now, and what requires 5–7 months of forward collection before it's actionable.

---

## What was built (Phase 11 Task 1–3)

### Task 1 — Forward-collection pipeline (LIVE)

| Component | Status | Notes |
| --- | --- | --- |
| `OddsSnapshot` model | LIVE (migration 026 applied) | Time-series table; no UniqueConstraint — one row per capture |
| `config/forward_leagues.py` | LIVE | Leagues 777/778/779 (Norwegian 3. Div), 648 (Tasmania NPL) |
| `scripts/capture_forward_odds.py` | LIVE | Run every 4h via cron; captures Pinnacle + Bet365 across h2h/ou25/btts |
| Fixtures | PENDING | Forward leagues currently show no NS fixtures in DB — will populate when daily_run.py runs post-quota-reset |

**Cron recommendation:**
```
0 */4 * * * cd /opt/projects/bootball && python scripts/capture_forward_odds.py >> logs/capture_forward.log 2>&1
```
Run `daily_run.py` first (once/day) to populate upcoming fixtures; then `capture_forward_odds.py` every 4 hours.

**Quota load:** ~10 calls/day across 4 leagues × 3 markets. Negligible (<0.01% of 75k Ultra quota).

---

### Task 2 — Prediction engine cleanup (LIVE)

| Component | Status | Notes |
| --- | --- | --- |
| `_fetch_upcoming_fixtures()` join fix | LIVE | Removed `.join(FixtureOdds)` that silently dropped fixtures with no odds |
| `generate()` delegates to `generate_with_fixture_data()` | LIVE | Dead-code path removed; one codepath for all fixtures |
| `evaluate_track_a(market, records)` static method | LIVE | Log-loss, Brier, AUC from settled PredictionRecord objects |

**Model divergence (explicit):**

Production prediction engine uses `LGBMClassifier` on standings features (`feature_pipeline_version="v1.0.0"`):
- 9 features: rank, goals_for, goals_against, normalized goal difference, rank gap
- Source: `src/betting/prediction.py` + `data/model_{h2h,btts,ou25,ou15}.pkl`
- Estimated 1X2 AUC: ~0.56–0.58 (standings-only baseline)

Phase 10 research DC+xG model:
- Rolling xG time-series, Dixon-Coles bivariate Poisson
- 1X2 AUC: **0.70–0.71** (Phase 10 walk-forward, 3-league validation)
- NOT in production — lives in `scripts/analysis/phase10_two_track.py`

**Implication:** Track-A scores from `evaluate_track_a()` will reflect the weaker standings model, not the research model. Porting DC+xG to production requires a separate decision (Understat xG data pipeline, rolling-feature infrastructure).

---

### Task 3 — Value layer (Pinnacle gate enforced)

| Component | Status | Notes |
| --- | --- | --- |
| EV computation | CORRECT (unchanged) | `generate_with_fixture_data()` reads stored prediction object; no re-derivation |
| CLV computation | FIXED | `capture_closing_lines()` now filters `FixtureOdds.bookmaker == 'Pinnacle'` |
| Pinnacle gate | ENFORCED | CLV returns None (no capture) if Pinnacle odds are absent |

**One-source-of-truth check:**
- `generate_with_fixture_data()` produces prediction dicts with `our_prob`, `calibrated_prob`, `blended_prob`
- EV = `blended_prob * odds - 1` (uses stored prediction's blended_prob, not re-derived)
- CLV = `(bet_odds - pinnacle_closing) / pinnacle_closing` (reads `PlacedBet.odds`, compares to Pinnacle FixtureOdds)
- Value layer does NOT call the model again for CLV — reads from stored records ✓

---

## Honest status: now vs. 5–7 months

### What is valid today

| Capability | Status |
| --- | --- |
| Track-A evaluation of production model (LGBM standings) | Valid now — `evaluate_track_a()` works on any settled PredictionRecord batch |
| Forward collection pipeline (logging open→close trajectory) | Valid from next NS fixture — run cron to collect |
| Pinnacle gate on CLV | Enforced — no CLV without Pinnacle closing line |
| Inspectable predictions (our_prob, calibrated_prob, blended_prob, ev, kelly, preliminary) | Valid now — `generate_with_fixture_data()` output |

### What is NOT valid today

| Claim | Why not valid yet |
| --- | --- |
| CLV on forward-collection leagues | No collected bets yet — collection starts now |
| ROI on forward leagues | Requires ~32 weeks (600 bets) for statistical confidence |
| CLV cross-check (Phase 8 gate) on Norwegian 3. Div / Tasmania NPL | Requires ~21 weeks (384 bets) for CLV estimate ±1% at 95% CI |
| Track-A on DC+xG production model | DC+xG not in production; would require porting Understat xG pipeline |
| Improvement over Phase 8 baseline | Phase 8 found −2.02%/−3.77% CLV vs Pinnacle; nothing in Phase 11 changes the model signal |

### What changes the picture

1. **Port DC+xG to production** (separate decision): would lift 1X2 AUC from ~0.56 to ~0.71. This is the most impactful model change but requires Understat xG data in the production pipeline.

2. **Forward collection maturing** (~21 weeks): produces CLV-usable sample (384 bets) on Norwegian 3. Div and Tasmania NPL. At that point Phase 8-style CLV gate can be re-run on these leagues natively.

3. **Per-market model retraining** on forward-collection data: as OddsSnapshot accumulates, can retrain BTTS and O/U 2.5 models using actual opening/closing line features (not just standings). This is 5–7 months out.

---

## Architecture summary (single-sentence version)

**Now:** One prediction engine (LGBM-standings), one codepath (all fixtures, odds-agnostic intake), value layer reads predictions not re-derives them, Pinnacle gate on CLV, forward collection logging from today.

**In 5–7 months:** CLV-usable sample on Norwegian 3. Div and Tasmania NPL ready for Phase 8-style cross-check; at that point the collection bet-vs-close analysis either confirms or refutes that the standings model has edge on high-goal long-tail leagues.
