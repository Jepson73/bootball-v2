# ADOPTION.md — Phase 31 Adoption Register

Part B of the Phase 31 V1/V2 separation. For every module classified ENTANGLED in Part A's
inventory, this records the adopt-or-strip decision against the four adoption criteria:

1. Zero references to betting-era machinery in live paths (PlacedBet-as-feedback, Kelly, policy
   engine, bankroll, adaptation scoring) — read-only historical queries only where documented.
2. Zero imports from / dependencies on anything classified V1-only.
3. A named V2 owner process (Part C) that invokes it.
4. Recorded here.

**Status as of this writing: criterion 3 cannot yet pass for anything** — no V2 owner process
exists yet (that's Part C, not yet built/approved). Every row below shows PASS/FAIL per
criterion 1–2 (assessable now) and marks 3 as PENDING-PART-C. Nothing is "adopted" in the
complete sense until Part C gives it an owner and Part D's cutover is verified; this document
records the module-level decision (adopt vs strip vs strip-then-adopt) so that work is ready to
execute once Part C/D are approved.

## Adopted (module-level) — will move to V2 ownership as-is or after a stated strip

| Module | Why adopted | What must be stripped first | Criterion 1 | Criterion 2 | Criterion 3 |
|---|---|---|---|---|---|
| `src/prediction/unified_prediction_service.py`, `market_normalizer.py` | The shared production `PredictionRecord` writer — this is the single call V2 needs preserved | Nothing in the file itself; its only V1 dependency is being *called from* `src.agents.coordinator` — that call site moves to a new V2 scheduler job in Part C, not stripped from this file | PASS | PASS (no import of V1-only code) | PENDING-PART-C |
| `src/betting/prediction.py`, `market_taxonomy.py`, `league_normalizer.py`, `temporal_adapter.py`, `ev.py`, `shin.py` | Feature engineering + EV math + Shin de-vig feeding `unified_prediction_service.py`'s blend step | Nothing functionally; **recommend relocating out of `src/betting/`** (e.g. to `src/prediction/lib/` or similar) as part of adoption — living under a directory named "betting" after V1 is archived is exactly the kind of label-not-architecture drift this phase exists to fix | PASS | PASS | PENDING-PART-C |
| `src/calibration/state_calibration_engine.py` | Live-drift ECE monitor (Phase 28-retargeted to read `PredictionRecord`, not `PlacedBet`) | **Strip the dead `add_portfolio_state()` method (line 219) and its `from src.portfolio.state.portfolio_state import PortfolioState` import (line 26)** — zero callers found anywhere, confirmed by direct grep; it's a live import of V1-only code with no live call, which fails criterion 2 as written today | FAIL (until stripped) | FAIL (until stripped) | PENDING-PART-C |
| `src/calibration/league_calibration_engine.py` | Per-league calibration applied to every prediction | **Rewire `notify_calibration_change()` (line 275) from `src.notifications.discord_system_notifier` to `src.notifications.v2_discord_notifier`** — functionally silent today (`discord_v1_enabled=False`), but it's a live import dependency on a file this phase archives | PASS (silenced) | FAIL (imports V1-only `discord_system_notifier.py`) | PENDING-PART-C |
| `src/calibration/market_blend.py` | Shin-based market blend, the fix from Phase 30 | Nothing | PASS | PASS | PENDING-PART-C |
| `src/models/model_registry.py` | Model version registry, used by prediction generation and calibration consumer | **Rewire all three `notify_model_change()` call sites (lines 243, 349, 402) from `discord_system_notifier` to `v2_discord_notifier`** — same shape as league_calibration_engine.py's coupling | PASS (silenced) | FAIL (imports V1-only `discord_system_notifier.py`) | PENDING-PART-C |
| `src/models/calibrator.py` | `calibrate_prediction()`, called at every prediction | Nothing | PASS | PASS | PENDING-PART-C |
| `src/security/safe_load.py` | HMAC-signed model artifact loading | Nothing | PASS | PASS | PENDING-PART-C |
| `src/storage/db.py`, `models.py` | DB engine/session factory + ORM. DB itself is kept by decree | See table-level strip list below — the **file** is adopted whole, the **schema inside it** is not | PASS (file) | PASS (file) | PENDING-PART-C |
| `src/ingestion/client.py` | API-Football HTTP client + quota tracking, used everywhere data is fetched | Nothing | PASS | PASS | PENDING-PART-C |
| `src/ingestion/odds_snapshot_capture.py` | Writes `OddsSnapshot` rows; only ever called from `odds_poll.py`/`odds_trajectory_scheduler.py` (both cron, non-betting) | Reclassify V2-NATIVE, not entangled — not touched by the V1 coordinator at all | PASS | PASS | PENDING-PART-C |
| `src/alerts/event_bus.py`, `src/alerts/__init__.py` (event_bus/Events exports only) | The shared pub/sub bus almost everything in the codebase — V1 and V2 alike — publishes/subscribes through | **Recommend relocating out of `src/alerts/`** once `discord.py` and `handlers.py` (both dead, see Part A) are removed from that directory — same label-drift concern as `src/betting/` | PASS | PASS | PENDING-PART-C |
| `src/governance/runtime_lock.py` | Generic `fcntl` single-instance process lock — not betting-specific, any V2 runtime needs the same guard | Nothing functionally; recommend relocating out of `src/governance/` (a name that otherwise means "policy/CLVE/meta-policy" throughout this codebase) | PASS | PASS | PENDING-PART-C |
| `src/governance/system_versioning.py`, `lineage_tracker.py` | Generic run-lineage/versioning, wraps the whole runtime cycle including prediction generation | Same relocation recommendation as `runtime_lock.py` | PASS | PASS | PENDING-PART-C |

## The pr.ev verdict (carried over from the Phase 30 brief, now resolved)

**What probability does stored `pr.ev` use on the current coordinator path — blended or raw?**
As of this writing: **blended, and verified working**, but it took three rounds of fixes across
this session to get there, all producing the identical "raw-formula EV" symptom independently:

1. `UnifiedPredictionService._get_market_odds_set()` read ORM attributes *after* its
   `with get_session()` block had already committed+closed (SQLAlchemy `expire_on_commit=True`
   default) — raised `DetachedInstanceError` on literally every call, 100% failure rate. The
   caller's blanket `except Exception` swallowed it and fell back to unblended EV with zero log
   trace. **100% of the 11,511 odds-bearing `prediction_records` rows in the DB carry this
   unblended signature** — the market-blend feature had never once executed successfully in
   production since it was introduced. Fixed in commit `ce87acd`.
2. An independent second write path, `scripts/odds_poll.py::recalculate_prediction_ev()`
   (invoked by `backend/scheduler.py`'s `job_fetch_odds`, in-process inside
   `bootball-runtime.service`), computed `ev` straight off `calibrated_prob` with **no blend
   attempted at all**, re-corrupting already-fixed rows every time odds changed. Fixed in
   commit `b343c6c`.
3. A third, subtler gap: `unified_prediction_service.py`'s post-fix fallback check only tested
   whether `market_odds` came back truthy — it never checked whether `blend_with_market()`
   itself then internally rejected the blend (outcome label mismatch, odds < 1.01, Shin
   failure) and returned `p_market=None` anyway, which wrote unblended EV with zero log trace,
   same shape as bug #1 one level deeper. `odds_poll.py`'s equivalent already checked this
   correctly. Fixed in commit `6135f65`, deployed and verified live.

**Quantified and disposed of:**
- 11,511 historical odds-bearing rows carry the unblended signature. **Settled history was
  deliberately not rewritten** — era boundary documented in `docs/codebase_reference.md`'s
  Phase 30 section: anything reading `pr.ev` on a row from before these fixes must assume the
  unblended signature.
- Of 63 unsettled, odds-bearing rows caught unblended during Phase 31's own re-audit, 27 were
  still pre-kickoff (NS, future) and got a clean regenerate+save (104 predictions, 0 fallbacks);
  the other 36 were already live/finished-but-not-yet-settled and were left untouched — they'll
  settle with whatever `ev` was last computed before their own kickoff, per the
  recompute-forward-only-when-pre-kickoff rule the user set in Phase 30.
- Both fixed write paths now log loudly (not silently) on any future fallback, with per-cycle
  counters — see `docs/codebase_reference.md`'s Phase 30 section for the full narrative.

**One known, separate, lower-severity bug found but intentionally not fixed (flagged for a
future pass, not part of this adoption):** `odds_poll.py::recalculate_prediction_ev()`'s
single-bookmaker `odds_row = ...first()` lookup can land on a bookmaker row with every odds
column `NULL` instead of aggregating across bookmakers — causing it to skip updating a handful
of predictions that do have valid odds from a different bookmaker. Out of scope for the
blend-EV fix; does not affect the blended-vs-raw question above.

## `storage/models.py` — table-level adopt/strip decisions

**Strip (archive with V1 — no live V2 use, confirmed by both classification agents):**
`Bankroll`, `BankrollRound`, `PlacedBet` (betting ledger — zero new rows since 2026-06-07),
`LayerGovernanceMetrics`, `LayerAblationResults`, `PredictionAttribution` (V1 governance/
attribution layer), `ArchitectureVersions`, `ArchitectureTransitions` (zero usages found
anywhere), `ModelDrift`, `ModelCalibration` (zero usages found anywhere — superseded by
`CalibrationDriftState`), `WatchedFixture`, `UserPreference` (single-file usage, needs manual
verification of which file before stripping).

**Adopt (V2 needs these):** `League`, `Team`, `Player`, `PlayerSeasonStats`, `PlayerFetchLog`,
`Injury`, `Fixture`, `FixtureStats`, `FixtureEvent`, `Standing`, `FixtureOdds`, `OddsSnapshot`,
`PredictionRecord`, `ModelVersion`, `RetrainEvent`, `LeagueCalibration`, `CalibrationDriftState`.

**Conditional:** `EloRating`, `EloRebuildLog` — depend on the `src/features/elo.py` verdict
(see Part A: actively-edited code with no scheduled/cron caller, only reachable from three
manual scripts — flagged DEAD/UNCLEAR rather than guessed either way; human call needed on
whether Elo predictions are an active feature or an abandoned side-branch before deciding these
tables' fate).

## Not entangled — reclassified during this pass

- `src/ingestion/odds_snapshot_capture.py`: initially flagged ENTANGLED by one pass, but on
  closer inspection its only callers (`scripts/odds_poll.py`, `scripts/odds_trajectory_scheduler.py`)
  are both cron/data-collection jobs never touched by the V1 coordinator. **V2-NATIVE.**

## Explicitly out of scope for this document

Modules classified DEAD/UNCLEAR in Part A (the `src/decision_engine/` package, `src/handlers/*`,
`src/monitoring/*`, the orphaned `src/models/{btts,dixon_coles,ensemble,h2h,halftime,injuries,
late_goals,overunder,poisson}.py` + their `src/features/{form,xg_features,strength}.py` feeders,
`src/alerts/discord.py`, `src/alerts/handlers.py`, `src/betting/confidence_weighting.py`,
`markets.py`, `risk_decisions.py`, `unified_latent.py`/`latent_shock.py`, `stress_testing.py`,
`portfolio_optimizer.py`, and others listed in Part A) are neither adopted nor archived by this
register — they have zero confirmed live importers under either V1 or V2 and are candidates for
outright deletion regardless of the V1/V2 split. That's a separate cleanup decision, not an
adoption decision, and needs its own human sign-off given "not obviously imported" is not the
same as "safe to delete" (dynamic imports, `importlib`, or string-based dispatch were not
exhaustively ruled out).
