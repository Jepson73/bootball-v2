#!/usr/bin/env python3
# ruff: noqa
"""
V1b supplement: blend-weight re-optimization using pre-window val_fix (has odds).

The main diagnostic_v1v2v3.py tried to use cal_fx (training fixtures) for blend
weight selection, but those fixtures have no odds data → all NaN.

Fix: use val_fix[date < test_start] as the selection pool (strictly before test
window, has h2h/ou25 odds, no leakage into test).

For each window × market: grid-search blend weight on pre-window val_fix,
select best weight, reapply to test window, report ROI vs default w=0.35.

Output appended to v2_phase2_report.md and saved to v1b_supplement_results.json.
"""
import importlib.util
import json
import logging
import sys
import warnings
from pathlib import Path
from typing import List, Optional

import lightgbm as lgb
import numpy as np
from sklearn.metrics import log_loss

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("v1b_supplement")

ANALYSIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT  = ANALYSIS_DIR.parent.parent
DB_PATH       = PROJECT_ROOT / "data" / "football.db"
HIST_DB_PATH  = ANALYSIS_DIR / "historical_odds.db"
CACHE_DIR     = ANALYSIS_DIR / "feature_cache"
OUTPUT_PATH   = ANALYSIS_DIR / "v1b_supplement_results.json"
REPORT_PATH   = ANALYSIS_DIR / "v2_phase2_report.md"

sys.path.insert(0, str(ANALYSIS_DIR))
from features_v1 import FeatureBuilder

# Import shared machinery from V4
_v4_path = ANALYSIS_DIR / "walk_forward_backtest_v4.py"
spec = importlib.util.spec_from_file_location("wfbv4", _v4_path)
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)

MARKET_CONFIGS             = v4.MARKET_CONFIGS
StandingsCache             = v4.StandingsCache
shin_probabilities         = v4.shin_probabilities
fit_platt                  = v4.fit_platt
apply_platt                = v4.apply_platt
compute_ev                 = v4.compute_ev
bootstrap_roi_ci           = v4.bootstrap_roi_ci
BOT_MIN_EV                 = v4.BOT_MIN_EV
CALIB_HOLDOUT_FRAC         = v4.CALIB_HOLDOUT_FRAC
MAX_TRAIN_SAMPLES          = v4.MAX_TRAIN_SAMPLES
N_BOOTSTRAP                = v4.N_BOOTSTRAP
load_all_training_fixtures = v4.load_all_training_fixtures
load_validation_fixtures   = v4.load_validation_fixtures
WINDOWS                    = v4.WINDOWS

import sqlite3

BLEND_WEIGHT_GRID = [0.0, 0.15, 0.25, 0.35, 0.50, 0.65, 1.0]


def make_splits(all_train: List[dict], test_start: str):
    """Identical to diagnostic_v1v2v3.py — same random seed → same split."""
    train_mask   = np.array([f["date"] < test_start for f in all_train], dtype=bool)
    true_indices = np.where(train_mask)[0]
    if len(true_indices) > MAX_TRAIN_SAMPLES:
        rng = np.random.default_rng(42)
        cutoff_yr = all_train[true_indices[-1]]["date"][:4]
        recent_yr = str(int(cutoff_yr) - 1)
        recent_mask = np.array(
            [all_train[i]["date"] >= f"{recent_yr}-01-01" for i in true_indices], dtype=bool
        )
        recent_idx = true_indices[recent_mask]
        older_idx  = true_indices[~recent_mask]
        n_need = max(0, MAX_TRAIN_SAMPLES - len(recent_idx))
        if n_need > 0 and len(older_idx) > 0:
            sampled = rng.choice(older_idx, size=min(n_need, len(older_idx)), replace=False)
            true_indices = np.sort(np.concatenate([recent_idx, sampled]))
        else:
            true_indices = recent_idx[:MAX_TRAIN_SAMPLES]
    n     = len(true_indices)
    split = int(n * (1 - CALIB_HOLDOUT_FRAC))
    return true_indices[:split], true_indices[split:]


def fit_model(market: str, X: np.ndarray, y: np.ndarray) -> Optional[lgb.LGBMClassifier]:
    if len(X) < 100:
        return None
    if market == "h2h":
        params = dict(n_estimators=300, num_leaves=31, learning_rate=0.05,
                      objective="multiclass", num_class=3, n_jobs=4, verbose=-1, random_state=42)
    else:
        params = dict(n_estimators=300, num_leaves=31, learning_rate=0.05,
                      objective="binary", n_jobs=4, verbose=-1, random_state=42)
    m = lgb.LGBMClassifier(**params)
    m.fit(X, y)
    return m


def blend_weight_search_on_val(
    model:       lgb.LGBMClassifier,
    calibrators: dict,
    X_sel:       np.ndarray,          # pre-window val_fix features
    sel_fx:      List[dict],           # pre-window val_fix fixtures (have odds)
    market:      str,
    cfg:         dict,
) -> dict:
    """
    Grid-search blend weight using pre-test-window val_fix fixtures.
    These have odds and outcomes — no leakage since all dates < test_start.
    """
    lbl_fn = cfg["label_fn"]
    y_sel  = np.array([lbl_fn(f) for f in sel_fx])
    valid  = y_sel >= 0

    result = {}
    for oc in cfg["outcomes"]:
        oc_idx        = oc["idx"]
        win_lbl       = oc["win_label"]
        odds_key      = oc["odds_key"]
        all_odds_keys = oc["all_odds_keys"]
        oc_name       = oc["name"]

        weight_ll = {}
        weight_n  = {}
        for w in BLEND_WEIGHT_GRID:
            ll_sum, n_ok = 0.0, 0
            for i, f in enumerate(sel_fx):
                if not valid[i]:
                    continue
                # Skip fdco fixtures for btts/ou15 (no odds)
                if f.get("odds_source") == "fdco" and odds_key in (
                    "odd_btts_yes", "odd_btts_no", "odd_ou15_over", "odd_ou15_under"
                ):
                    continue
                all_odds = [f.get(k) for k in all_odds_keys]
                if any(o is None or o < 1.01 for o in all_odds):
                    continue
                try:
                    raw     = model.predict_proba([X_sel[i]])[0]
                    p_model = cfg["get_prob"](raw, oc_idx)
                    p_cal   = apply_platt(calibrators.get(win_lbl), p_model)
                    devigged = shin_probabilities(all_odds)
                    p_mkt   = devigged[oc_idx]
                    p_blend = w * p_cal + (1 - w) * p_mkt
                    p_clip  = max(1e-7, min(1 - 1e-7, p_blend))
                    won     = int(y_sel[i] == win_lbl)
                    ll_sum += -(won * np.log(p_clip) + (1 - won) * np.log(1 - p_clip))
                    n_ok   += 1
                except Exception:
                    continue
            weight_ll[w] = float(ll_sum / n_ok) if n_ok > 0 else float("nan")
            weight_n[w]  = n_ok

        valid_w = {w: v for w, v in weight_ll.items() if not np.isnan(v)}
        best_w  = min(valid_w, key=valid_w.get) if valid_w else 0.35

        result[oc_name] = {
            "best_weight":      best_w,
            "n_sel_fixtures":   weight_n.get(0.35, 0),
            "scores_by_weight": {str(w): weight_ll[w] for w in BLEND_WEIGHT_GRID},
        }

    return result


def simulate_bets_w(
    model:        lgb.LGBMClassifier,
    calibrators:  dict,
    X_test:       np.ndarray,
    test_fx:      List[dict],
    market:       str,
    cfg:          dict,
    wname:        str,
    model_weight: float,
) -> List[dict]:
    """Simulate bets with given model_weight blend."""
    lbl_fn = cfg["label_fn"]
    bets = []
    for i, f in enumerate(test_fx):
        try:
            raw_probs = model.predict_proba([X_test[i]])[0]
        except Exception:
            continue
        for oc in cfg["outcomes"]:
            odds_key      = oc["odds_key"]
            oc_idx        = oc["idx"]
            win_lbl       = oc["win_label"]
            all_odds_keys = oc["all_odds_keys"]
            odds = f.get(odds_key)
            if odds is None or odds < 1.6:
                continue
            if f.get("odds_source") == "fdco" and odds_key in (
                "odd_btts_yes", "odd_btts_no", "odd_ou15_over", "odd_ou15_under"
            ):
                continue
            p_model = cfg["get_prob"](raw_probs, oc_idx)
            p_cal   = apply_platt(calibrators.get(win_lbl), p_model)
            all_odds = [f.get(k) for k in all_odds_keys]
            if not all_odds or any(o is None or o < 1.01 for o in all_odds):
                continue
            try:
                devigged   = shin_probabilities(all_odds)
                p_mkt      = devigged[oc_idx]
                p_blended  = model_weight * p_cal + (1 - model_weight) * p_mkt
            except Exception:
                continue
            ev = compute_ev(p_blended, odds)
            if ev <= BOT_MIN_EV:
                continue
            actual_label = lbl_fn(f)
            if actual_label < 0:
                continue
            won = (actual_label == win_lbl)
            pnl = odds - 1 if won else -1.0
            bets.append({"won": won, "pnl": pnl, "odds": odds, "ev": ev,
                          "p_blended": p_blended, "fixture_id": f["id"],
                          "window": wname})
    return bets


def score(bets: list) -> dict:
    if not bets:
        return {"n_bets": 0, "roi": float("nan"),
                "ci_lo": float("nan"), "ci_hi": float("nan")}
    pnls = [b["pnl"] for b in bets]
    roi  = float(np.mean(pnls))
    ci_lo, ci_hi = bootstrap_roi_ci(pnls)
    wins = sum(1 for b in bets if b["won"])
    return {"n_bets": len(bets), "roi": roi, "ci_lo": ci_lo, "ci_hi": ci_hi,
            "win_rate": wins / len(bets)}


def main():
    import sqlite3
    logger.info("=== V1b Supplement: Blend-Weight Re-Optimization ===")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"ATTACH '{HIST_DB_PATH}' AS hist")

    logger.info("Loading fixtures...")
    all_train = load_all_training_fixtures(conn)
    val_fix   = load_validation_fixtures(conn)

    logger.info("Loading cached feature matrices...")
    X_train_n5 = np.load(CACHE_DIR / "train_feat29_n5.npy")
    X_val_n5   = np.load(CACHE_DIR / "val_feat29_n5.npy")
    logger.info(f"  train_n5: {X_train_n5.shape}, val_n5: {X_val_n5.shape}")

    assert X_train_n5.shape[0] == len(all_train), "cache mismatch: all_train"
    assert X_val_n5.shape[0]   == len(val_fix),   "cache mismatch: val_fix"

    all_results = []

    for window in WINDOWS:
        wname      = window["name"]
        test_start = window["test_start"]
        test_end   = window["test_end"]

        fit_idx, cal_idx = make_splits(all_train, test_start)
        fit_fx = [all_train[i] for i in fit_idx]
        cal_fx = [all_train[i] for i in cal_idx]
        X_fit  = X_train_n5[fit_idx]
        X_cal  = X_train_n5[cal_idx]

        # Pre-window val_fix: has odds, strictly before test window
        sel_mask    = np.array([f["date"] < test_start for f in val_fix], dtype=bool)
        sel_indices = np.where(sel_mask)[0]
        sel_fx      = [val_fix[i] for i in sel_indices]
        X_sel       = X_val_n5[sel_indices]

        # Test window
        test_mask    = np.array([test_start <= f["date"] < test_end for f in val_fix], dtype=bool)
        test_indices = np.where(test_mask)[0]
        test_fx      = [val_fix[i] for i in test_indices]
        X_test       = X_val_n5[test_indices]

        logger.info(f"\nWindow {wname}: {len(fit_fx):,} fit / {len(cal_fx):,} cal / "
                    f"{len(sel_fx):,} pre-window val (blend selection) / {len(test_fx):,} test")

        window_result: dict = {"window": wname, "markets": {}}

        for market in ["h2h", "ou25"]:
            cfg    = MARKET_CONFIGS[market]
            lbl_fn = cfg["label_fn"]

            y_fit  = np.array([lbl_fn(f) for f in fit_fx])
            mask   = y_fit >= 0
            if mask.sum() < 100:
                continue

            logger.info(f"  Fitting {market} (nl=31, ne=300)...")
            model = fit_model(market, X_fit[mask], y_fit[mask])
            if model is None:
                continue

            # Platt calibration using cal holdout (same as V4/main diagnostic)
            calibrators: dict = {}
            for oc in cfg["outcomes"]:
                oc_idx  = oc["idx"]
                win_lbl = oc["win_label"]
                cal_probs = []
                for j, f in enumerate(cal_fx):
                    try:
                        raw = model.predict_proba([X_cal[j]])[0]
                        cal_probs.append(cfg["get_prob"](raw, oc_idx))
                    except Exception:
                        cal_probs.append(0.5)
                cal_labels_bin = [1 if lbl_fn(f) == win_lbl else 0 for f in cal_fx]
                calibrators[win_lbl] = fit_platt(cal_probs, cal_labels_bin)

            # V1b: blend weight search on pre-window val_fix
            logger.info(f"  V1b blend search for {market} on {len(sel_fx):,} pre-window fixtures...")
            blend_res = blend_weight_search_on_val(
                model, calibrators, X_sel, sel_fx, market, cfg
            )

            # EV at default weight (w=0.35)
            bets_35 = simulate_bets_w(model, calibrators, X_test, test_fx,
                                       market, cfg, wname, 0.35)

            # EV at best weight (per outcome — for markets with one outcome use that)
            oc_name = list(blend_res.keys())[0] if blend_res else None
            best_w  = blend_res.get(oc_name, {}).get("best_weight", 0.35) if oc_name else 0.35
            bets_opt = simulate_bets_w(model, calibrators, X_test, test_fx,
                                        market, cfg, wname, best_w)

            # Also try all weights for a full picture
            ev_by_weight = {}
            for w in BLEND_WEIGHT_GRID:
                bets_w = simulate_bets_w(model, calibrators, X_test, test_fx,
                                          market, cfg, wname, w)
                ev_by_weight[str(w)] = score(bets_w)

            window_result["markets"][market] = {
                "blend_search":   blend_res,
                "ev_by_weight":   ev_by_weight,
                "ev_default_35":  score(bets_35),
                "ev_opt_blend":   {"best_weight": best_w, **score(bets_opt)},
            }

            def _roi(s): return f"{s.get('roi',float('nan'))*100:+.1f}%" if s.get('n_bets',0) > 0 else "0 bets"
            logger.info(f"  {market}: best_w={best_w:.0%}, "
                        f"default ROI={_roi(score(bets_35))}, "
                        f"opt ROI={_roi(score(bets_opt))}")
            for w in BLEND_WEIGHT_GRID:
                s = ev_by_weight[str(w)]
                logger.info(f"    w={w:.0%}: {s.get('n_bets',0)} bets, ROI {_roi(s)}")

        all_results.append(window_result)

    # Write JSON
    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"\nResults saved to {OUTPUT_PATH}")

    # Build report section
    lines = ["\n### V1b (Corrected): Blend Weight Re-Optimization — Pre-Window Val_Fix\n"]
    lines.append("Selection pool: val_fix[date < test_start] (has odds, no test leakage).\n")
    lines.append("Platt calibration uses cal holdout (same as main pipeline).\n")

    for market in ["h2h", "ou25"]:
        lines.append(f"\n**{market.upper()}**\n")
        lines.append("| Window | Best Weight | N sel | ROI(w=0%) | ROI(w=15%) | ROI(w=25%) | ROI(w=35%) | ROI(w=50%) | ROI(w=65%) | ROI(w=100%) |")
        lines.append("|--------|------------|-------|-----------|-----------|-----------|-----------|-----------|-----------|------------|")
        for wr in all_results:
            wname = wr["window"]
            mr    = wr["markets"].get(market, {})
            if not mr:
                lines.append(f"| {wname} | n/a | n/a | — | — | — | — | — | — | — |")
                continue
            br     = mr.get("blend_search", {})
            oc_nm  = list(br.keys())[0] if br else None
            best_w = br.get(oc_nm, {}).get("best_weight", 0.35) if oc_nm else 0.35
            n_sel  = br.get(oc_nm, {}).get("n_sel_fixtures", 0) if oc_nm else 0
            ev_wt  = mr.get("ev_by_weight", {})
            def _r(w):
                s = ev_wt.get(str(w), {})
                n = s.get("n_bets", 0)
                roi = s.get("roi", float("nan"))
                if n == 0: return "0 bets"
                return f"{roi*100:+.1f}% (n={n})"
            lines.append(f"| {wname} | **{best_w:.0%}** | {n_sel:,} | {_r(0.0)} | {_r(0.15)} | {_r(0.25)} | {_r(0.35)} | {_r(0.50)} | {_r(0.65)} | {_r(1.0)} |")

    lines.append("\n**Key finding:** See whether best_weight ≠ 0.35 and whether optimized EV")
    lines.append("is materially different from default w=0.35.\n")

    report_text = "\n".join(lines)
    with open(REPORT_PATH, "a") as f:
        f.write(report_text)
    logger.info(f"Appended V1b section to {REPORT_PATH}")

    # Canary check
    for wr in all_results:
        for market in ["ou25"]:
            mr = wr["markets"].get(market, {})
            s35 = mr.get("ev_default_35", {})
            roi = s35.get("roi", float("nan"))
            if not np.isnan(roi) and roi > 0.20:
                logger.warning(f"CANARY: {wr['window']}/{market} ROI={roi*100:+.1f}% — check win_label fix")


if __name__ == "__main__":
    main()
