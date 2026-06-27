#!/usr/bin/env python3
# ruff: noqa
"""
Diagnostic V1/V2/V3 for Bootball V2 Phase 2.

V1: Raw classifier metrics (AUC, log-loss, Brier) on test windows +
    blend-weight re-optimization via grid search on cal holdout only.

V2: Hyperparameter retuning — walk-forward HP grid search (num_leaves,
    n_estimators), check if properly-tuned Wave1 changes the FAIL verdict.

V3: Rolling-feature reliability — fraction of validation fixtures with
    < N prior fixture_stats matches; cold-start clustering in losing bets.

Features are cached to feature_cache/ after first run (~24 min compute).

Output: scripts/analysis/diagnostic_results.json
        (findings also appended to scripts/analysis/v2_phase2_report.md)
"""
import importlib.util
import json
import logging
import sys
import warnings
from bisect import bisect_left
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("diag_v123")

ANALYSIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT  = ANALYSIS_DIR.parent.parent
DB_PATH       = PROJECT_ROOT / "data" / "football.db"
HIST_DB_PATH  = ANALYSIS_DIR / "historical_odds.db"
CACHE_DIR     = ANALYSIS_DIR / "feature_cache"
OUTPUT_PATH   = ANALYSIS_DIR / "diagnostic_results.json"
REPORT_PATH   = ANALYSIS_DIR / "v2_phase2_report.md"

sys.path.insert(0, str(ANALYSIS_DIR))
from features_v1 import FeatureBuilder

# ── Import shared machinery from V4 (preserves win_label fix and Shin logic) ──
_v4_path = ANALYSIS_DIR / "walk_forward_backtest_v4.py"
spec = importlib.util.spec_from_file_location("wfbv4", _v4_path)
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)

MARKET_CONFIGS              = v4.MARKET_CONFIGS      # includes win_label fix
StandingsCache              = v4.StandingsCache
shin_probabilities          = v4.shin_probabilities
fit_platt                   = v4.fit_platt
apply_platt                 = v4.apply_platt
compute_ev                  = v4.compute_ev
bootstrap_roi_ci            = v4.bootstrap_roi_ci
BOT_MIN_EV                  = v4.BOT_MIN_EV
CALIB_HOLDOUT_FRAC          = v4.CALIB_HOLDOUT_FRAC
MAX_TRAIN_SAMPLES           = v4.MAX_TRAIN_SAMPLES
N_BOOTSTRAP                 = v4.N_BOOTSTRAP
load_all_training_fixtures  = v4.load_all_training_fixtures
load_validation_fixtures    = v4.load_validation_fixtures
WINDOWS                     = v4.WINDOWS
MARKET_SLOTS                = v4.MARKET_SLOTS

import sqlite3
conn_main = None  # set in main()

BLEND_WEIGHT_GRID = [0.0, 0.15, 0.25, 0.35, 0.50, 0.65, 1.0]

# HP grid: vary num_leaves and n_estimators; learning_rate stays fixed for
# apples-to-apples comparison with V3/V4 baseline.
HP_GRID = [
    {"num_leaves":  31, "n_estimators": 300},   # V4 default
    {"num_leaves":  63, "n_estimators": 300},
    {"num_leaves": 127, "n_estimators": 300},
    {"num_leaves":  31, "n_estimators": 500},
    {"num_leaves":  63, "n_estimators": 500},
    {"num_leaves": 127, "n_estimators": 500},
]
LEARNING_RATE = 0.05

# V3 cold-start thresholds to examine
V3_THRESHOLDS = [5, 10]


# ── Feature computation & caching ─────────────────────────────────────────────

def compute_features_all_variants(
    fixtures: List[dict],
    sc: StandingsCache,
    wave1: FeatureBuilder,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (feat9, feat29_n5, feat29_n10) in one pass to avoid triple cost."""
    n = len(fixtures)
    feat9     = np.zeros((n,  9), dtype=float)
    feat29_n5 = np.zeros((n, 29), dtype=float)
    feat29_n10= np.zeros((n, 29), dtype=float)
    for i, f in enumerate(fixtures):
        if i % 50_000 == 0:
            logger.info(f"  Features: {i:,}/{n:,}")
        try:
            s          = sc.build_standings_features(f)
            w1_5, w1_10 = wave1.build_pair(f)
            feat9[i]      = s
            feat29_n5[i]  = np.concatenate([s, w1_5])
            feat29_n10[i] = np.concatenate([s, w1_10])
        except Exception:
            pass
    return feat9, feat29_n5, feat29_n10


def load_or_compute_features(
    all_train: List[dict],
    val_fix:   List[dict],
    sc:        StandingsCache,
    wave1:     FeatureBuilder,
) -> dict:
    CACHE_DIR.mkdir(exist_ok=True)
    paths = {
        "train9":    CACHE_DIR / "train_feat9.npy",
        "train_n5":  CACHE_DIR / "train_feat29_n5.npy",
        "train_n10": CACHE_DIR / "train_feat29_n10.npy",
        "val9":      CACHE_DIR / "val_feat9.npy",
        "val_n5":    CACHE_DIR / "val_feat29_n5.npy",
        "val_n10":   CACHE_DIR / "val_feat29_n10.npy",
    }

    if all(p.exists() for p in paths.values()):
        logger.info("Loading cached feature matrices from disk...")
        mats = {k: np.load(v) for k, v in paths.items()}
        for k, arr in mats.items():
            logger.info(f"  {k}: {arr.shape}")
        return mats

    logger.info(f"Computing features for {len(all_train):,} training fixtures...")
    t9, t5, t10 = compute_features_all_variants(all_train, sc, wave1)
    logger.info(f"Computing features for {len(val_fix):,} validation fixtures...")
    v9, v5, v10 = compute_features_all_variants(val_fix, sc, wave1)

    mats = {
        "train9":    t9,  "train_n5":  t5,  "train_n10": t10,
        "val9":      v9,  "val_n5":    v5,  "val_n10":   v10,
    }
    for k, arr in mats.items():
        np.save(paths[k], arr)
        logger.info(f"  Saved {paths[k].name}: {arr.shape}")
    return mats


# ── V3: prior match counts ─────────────────────────────────────────────────────

def compute_prior_counts(fixtures: List[dict], wave1: FeatureBuilder) -> List[dict]:
    """
    For each fixture, count prior fixture_stats entries in wave1._team_form
    for home and away teams (strictly before the fixture date).
    Returns list of dicts with fixture_id, home_prior, away_prior, min_prior.
    """
    results = []
    for f in fixtures:
        hid, aid, date = f["home_team_id"], f["away_team_id"], f["date"]
        h_entries = wave1._team_form.get(hid, [])
        a_entries = wave1._team_form.get(aid, [])
        # Entries are (date, shots_on, shots_total, possession, corners, pass_acc, yellow_cards)
        h_dates = [e[0] for e in h_entries]
        a_dates = [e[0] for e in a_entries]
        n_h = bisect_left(h_dates, date)
        n_a = bisect_left(a_dates, date)
        results.append({
            "fixture_id": f["id"],
            "home_prior":  n_h,
            "away_prior":  n_a,
            "min_prior":   min(n_h, n_a),
        })
    return results


# ── Model fitting ──────────────────────────────────────────────────────────────

def fit_lgbm_custom(
    market: str,
    X: np.ndarray,
    y: np.ndarray,
    num_leaves: int = 31,
    n_estimators: int = 300,
) -> Optional[lgb.LGBMClassifier]:
    if len(X) < 100:
        return None
    if market == "h2h":
        params = dict(n_estimators=n_estimators, num_leaves=num_leaves,
                      learning_rate=LEARNING_RATE, objective="multiclass",
                      num_class=3, n_jobs=4, verbose=-1, random_state=42)
    else:
        params = dict(n_estimators=n_estimators, num_leaves=num_leaves,
                      learning_rate=LEARNING_RATE, objective="binary",
                      n_jobs=4, verbose=-1, random_state=42)
    m = lgb.LGBMClassifier(**params)
    m.fit(X, y)
    return m


# ── V1a: raw classifier metrics on test set ────────────────────────────────────

def raw_classifier_metrics(
    model:   lgb.LGBMClassifier,
    X_test:  np.ndarray,
    test_fx: List[dict],
    market:  str,
    cfg:     dict,
) -> dict:
    """
    Compute AUC, log-loss, Brier on test set.
    Uses Platt-calibrated probabilities would require cal → use raw model probs here
    to isolate classifier quality from calibration quality.
    """
    lbl_fn = cfg["label_fn"]
    y_true = np.array([lbl_fn(f) for f in test_fx])
    valid  = y_true >= 0
    if valid.sum() < 10:
        return {}
    try:
        probs = model.predict_proba(X_test[valid])
    except Exception:
        return {}
    y_v = y_true[valid]

    if market == "h2h":
        try:
            auc = float(roc_auc_score(y_v, probs, multi_class="ovr", average="macro"))
        except Exception:
            auc = float("nan")
        ll    = float(log_loss(y_v, probs, labels=[0, 1, 2]))
        brier = float(np.mean([
            brier_score_loss((y_v == c).astype(int), probs[:, c])
            for c in range(3)
        ]))
    else:
        p_pos = probs[:, 1]
        try:
            auc = float(roc_auc_score(y_v, p_pos))
        except Exception:
            auc = float("nan")
        ll    = float(log_loss(y_v, probs))
        brier = float(brier_score_loss(y_v, p_pos))

    return {"auc": auc, "log_loss": ll, "brier": brier, "n": int(valid.sum())}


# ── V1b: blend weight search on cal holdout ────────────────────────────────────

def blend_weight_search(
    model:   lgb.LGBMClassifier,
    X_cal:   np.ndarray,
    cal_fx:  List[dict],
    market:  str,
    cfg:     dict,
) -> dict:
    """
    Grid-search blend weight w in BLEND_WEIGHT_GRID using cal holdout log-loss.
    The cal holdout was also used for Platt — mild optimism toward higher model
    weight; noted in report but not corrected here.
    Returns per-outcome best_weight and scores_by_weight.
    """
    lbl_fn = cfg["label_fn"]
    y_cal  = np.array([lbl_fn(f) for f in cal_fx])
    valid  = y_cal >= 0

    result = {}
    for oc in cfg["outcomes"]:
        oc_idx      = oc["idx"]
        win_lbl     = oc["win_label"]
        odds_key    = oc["odds_key"]
        all_odds_keys = oc["all_odds_keys"]
        oc_name     = oc["name"]

        # Raw model probs on cal set
        p_model_all = []
        for i in range(len(cal_fx)):
            if not valid[i]:
                p_model_all.append(None)
                continue
            try:
                raw = model.predict_proba([X_cal[i]])[0]
                p_model_all.append(cfg["get_prob"](raw, oc_idx))
            except Exception:
                p_model_all.append(None)

        weight_ll = {}
        weight_n  = {}
        for w in BLEND_WEIGHT_GRID:
            ll_sum, n_ok = 0.0, 0
            for i, f in enumerate(cal_fx):
                if not valid[i] or p_model_all[i] is None:
                    continue
                all_odds = [f.get(k) for k in all_odds_keys]
                if any(o is None or o < 1.01 for o in all_odds):
                    continue
                p_m = p_model_all[i]
                try:
                    devigged = shin_probabilities(all_odds)
                    p_mkt    = devigged[oc_idx]
                except Exception:
                    continue
                p_blend  = w * p_m + (1 - w) * p_mkt
                p_clip   = max(1e-7, min(1 - 1e-7, p_blend))
                won      = int(y_cal[i] == win_lbl)
                ll_sum  += -(won * np.log(p_clip) + (1 - won) * np.log(1 - p_clip))
                n_ok    += 1
            weight_ll[w] = float(ll_sum / n_ok) if n_ok > 0 else float("nan")
            weight_n[w]  = n_ok

        valid_w = {w: v for w, v in weight_ll.items() if not np.isnan(v)}
        best_w  = min(valid_w, key=valid_w.get) if valid_w else 0.35

        result[oc_name] = {
            "best_weight":      best_w,
            "scores_by_weight": {str(w): weight_ll[w] for w in BLEND_WEIGHT_GRID},
            "n_cal_fixtures":   weight_n.get(0.35, 0),
        }

    return result


# ── V2: HP search on cal holdout ───────────────────────────────────────────────

def hp_search(
    market: str,
    X_fit:  np.ndarray,
    y_fit:  np.ndarray,
    X_cal:  np.ndarray,
    cal_fx: List[dict],
    cfg:    dict,
) -> dict:
    """
    Grid-search over HP_GRID; select by log-loss on cal holdout (no test leakage).
    Returns best_hp and per-combo scores.
    """
    lbl_fn = cfg["label_fn"]
    y_cal  = np.array([lbl_fn(f) for f in cal_fx])
    valid  = y_cal >= 0
    mask_fit = y_fit >= 0

    if valid.sum() < 50 or mask_fit.sum() < 100:
        return {"best_hp": None, "scores": {}}

    X_cal_v = X_cal[valid]
    y_cal_v = y_cal[valid]
    scores  = {}

    for hp in HP_GRID:
        hp_key = f"nl{hp['num_leaves']}_ne{hp['n_estimators']}"
        try:
            m = fit_lgbm_custom(
                market, X_fit[mask_fit], y_fit[mask_fit],
                num_leaves=hp["num_leaves"], n_estimators=hp["n_estimators"],
            )
            if m is None:
                raise ValueError("fit returned None")
            probs = m.predict_proba(X_cal_v)
            if market == "h2h":
                ll = float(log_loss(y_cal_v, probs, labels=[0, 1, 2]))
            else:
                ll = float(log_loss(y_cal_v, probs))
            scores[hp_key] = {**hp, "log_loss": ll}
        except Exception as e:
            scores[hp_key] = {**hp, "log_loss": float("nan"), "error": str(e)}

    valid_scores = {k: v["log_loss"] for k, v in scores.items()
                    if not np.isnan(v["log_loss"])}
    if valid_scores:
        best_key = min(valid_scores, key=valid_scores.get)
        best_hp  = scores[best_key]
    else:
        best_key = None
        best_hp  = {"num_leaves": 31, "n_estimators": 300}  # V4 default fallback

    return {"best_hp": best_hp, "scores": scores}


# ── Bet simulation (full pipeline) ────────────────────────────────────────────

def simulate_bets(
    model:        lgb.LGBMClassifier,
    X_cal:        np.ndarray,
    cal_fx:       List[dict],
    X_test:       np.ndarray,
    test_fx:      List[dict],
    market:       str,
    cfg:          dict,
    wname:        str,
    model_weight: float = 0.35,
    prior_lookup: Optional[dict] = None,
) -> List[dict]:
    """
    Full EV pipeline: Platt calibration → blend → EV filter → bet list.
    Returns list of bet dicts (includes min_prior if prior_lookup provided).
    """
    lbl_fn = cfg["label_fn"]
    y_cal  = np.array([lbl_fn(f) for f in cal_fx])

    # Per-outcome Platt calibrators
    calibrators: dict[int, object] = {}
    for oc in cfg["outcomes"]:
        oc_idx  = oc["idx"]
        win_lbl = oc["win_label"]
        cal_probs = []
        for i, f in enumerate(cal_fx):
            try:
                raw = model.predict_proba([X_cal[i]])[0]
                cal_probs.append(cfg["get_prob"](raw, oc_idx))
            except Exception:
                cal_probs.append(0.5)
        cal_labels_bin = [1 if lbl_fn(f) == win_lbl else 0 for f in cal_fx]
        calibrators[win_lbl] = fit_platt(cal_probs, cal_labels_bin)

    bets = []
    for i, f in enumerate(test_fx):
        try:
            raw_probs = model.predict_proba([X_test[i]])[0]
        except Exception:
            continue

        for oc in cfg["outcomes"]:
            odds_key   = oc["odds_key"]
            oc_idx     = oc["idx"]
            win_lbl    = oc["win_label"]
            all_odds_keys = oc["all_odds_keys"]

            odds = f.get(odds_key)
            if odds is None or odds < 1.6:
                continue
            if f["odds_source"] == "fdco" and odds_key in (
                "odd_btts_yes", "odd_btts_no", "odd_ou15_over", "odd_ou15_under"
            ):
                continue

            p_model   = cfg["get_prob"](raw_probs, oc_idx)
            p_cal     = apply_platt(calibrators.get(win_lbl), p_model)
            all_odds  = [f.get(k) for k in all_odds_keys]
            if not all_odds or any(o is None or o < 1.01 for o in all_odds):
                p_blended, p_market = p_cal, None
            else:
                try:
                    devigged   = shin_probabilities(all_odds)
                    p_market   = devigged[oc_idx]
                    p_blended  = model_weight * p_cal + (1 - model_weight) * p_market
                except Exception:
                    p_blended, p_market = p_cal, None

            ev = compute_ev(p_blended, odds)
            if ev <= BOT_MIN_EV:
                continue

            actual_label = lbl_fn(f)
            if actual_label < 0:
                continue

            won = (actual_label == win_lbl)
            pnl = odds - 1 if won else -1.0
            min_prior = prior_lookup.get(f["id"], -1) if prior_lookup else -1

            bets.append({
                "fixture_id":  f["id"],
                "date":        f["date"],
                "outcome":     oc["name"],
                "odds":        odds,
                "ev":          ev,
                "p_blended":   p_blended,
                "p_market":    p_market,
                "won":         won,
                "pnl":         pnl,
                "window":      wname,
                "odds_source": f["odds_source"],
                "min_prior":   min_prior,
            })

    return bets


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_bets(bets: list, label: str) -> dict:
    if not bets:
        return {"label": label, "n_bets": 0, "roi": float("nan"),
                "ci_lo": float("nan"), "ci_hi": float("nan"),
                "win_rate": float("nan"), "avg_odds": float("nan")}
    pnls   = [b["pnl"] for b in bets]
    roi    = float(np.mean(pnls))
    ci_lo, ci_hi = bootstrap_roi_ci(pnls)
    wins   = sum(1 for b in bets if b["won"])
    return {
        "label":     label,
        "n_bets":    len(bets),
        "roi":       roi,
        "ci_lo":     ci_lo,
        "ci_hi":     ci_hi,
        "win_rate":  wins / len(bets),
        "avg_odds":  float(np.mean([b["odds"] for b in bets])),
        "avg_ev":    float(np.mean([b["ev"] for b in bets])),
    }


# ── Subsample + cal split (identical to V4) ───────────────────────────────────

def make_splits(all_train: List[dict], test_start: str):
    train_mask   = np.array([f["date"] < test_start for f in all_train], dtype=bool)
    true_indices = np.where(train_mask)[0]

    if len(true_indices) > MAX_TRAIN_SAMPLES:
        rng = np.random.default_rng(42)
        cutoff_yr = all_train[true_indices[-1]]["date"][:4]
        recent_yr = str(int(cutoff_yr) - 1)
        recent_mask = np.array(
            [all_train[i]["date"] >= f"{recent_yr}-01-01" for i in true_indices],
            dtype=bool,
        )
        recent_idx = true_indices[recent_mask]
        older_idx  = true_indices[~recent_mask]
        n_need = max(0, MAX_TRAIN_SAMPLES - len(recent_idx))
        if n_need > 0 and len(older_idx) > 0:
            sampled = rng.choice(older_idx, size=min(n_need, len(older_idx)), replace=False)
            true_indices = np.sort(np.concatenate([recent_idx, sampled]))
        else:
            true_indices = recent_idx[:MAX_TRAIN_SAMPLES]

    n      = len(true_indices)
    split  = int(n * (1 - CALIB_HOLDOUT_FRAC))
    fit_idx = true_indices[:split]
    cal_idx = true_indices[split:]
    return fit_idx, cal_idx


# ── Per-window diagnostic ──────────────────────────────────────────────────────

def run_window(
    window:          dict,
    feature_mats:    dict,
    all_train:       List[dict],
    val_fix:         List[dict],
    prior_counts_val: List[dict],   # parallel to val_fix
) -> dict:
    wname      = window["name"]
    test_start = window["test_start"]
    test_end   = window["test_end"]

    fit_idx, cal_idx = make_splits(all_train, test_start)
    fit_fx = [all_train[i] for i in fit_idx]
    cal_fx = [all_train[i] for i in cal_idx]

    val_mask    = np.array([test_start <= f["date"] < test_end for f in val_fix], dtype=bool)
    val_indices = np.where(val_mask)[0]
    test_fx     = [val_fix[i] for i in val_indices]

    # prior_lookup: fixture_id → min_prior (for bets placed in this window)
    prior_lookup = {prior_counts_val[i]["fixture_id"]: prior_counts_val[i]["min_prior"]
                    for i in val_indices}

    logger.info(f"\n{'='*70}")
    logger.info(f"Window {wname}: {len(fit_fx):,} fit / {len(cal_fx):,} cal / {len(test_fx):,} test")

    win_result: dict = {"window": wname}

    # Run three feature variants: 9-feat baseline, 29-feat-n5, 29-feat-n10
    # For V2 HP search and V1 blend search we only run on n5 (n10 mirrors n5)
    variants = [
        ("9feat",    "train9",    "val9",    False),
        ("29feat_n5","train_n5",  "val_n5",  True ),   # full diagnostics
        ("29feat_n10","train_n10","val_n10", False),   # EV + raw metrics only
    ]

    for var_name, tr_key, vl_key, full_diag in variants:
        X_train_all = feature_mats[tr_key]
        X_val_all   = feature_mats[vl_key]

        X_fit  = X_train_all[fit_idx]
        X_cal  = X_train_all[cal_idx]
        X_test = X_val_all[val_indices]

        var_result: dict = {}

        for market, cfg in MARKET_CONFIGS.items():
            lbl_fn = cfg["label_fn"]
            y_fit  = np.array([lbl_fn(f) for f in fit_fx])
            mask_fit = y_fit >= 0
            if mask_fit.sum() < 100:
                continue

            logger.info(f"  {var_name} / {market} ...")

            # ── Default model (V4-equivalent HP) ──────────────────────────────
            model_default = fit_lgbm_custom(
                market, X_fit[mask_fit], y_fit[mask_fit],
                num_leaves=31, n_estimators=300,
            )
            if model_default is None:
                continue

            # V1a: raw classifier metrics on test set
            raw_m = raw_classifier_metrics(model_default, X_test, test_fx, market, cfg)

            # Default EV bets (MODEL_WEIGHT=0.35)
            bets_default = simulate_bets(
                model_default, X_cal, cal_fx, X_test, test_fx,
                market, cfg, wname, model_weight=0.35,
                prior_lookup=prior_lookup,
            )

            mkt_result: dict = {
                "raw_metrics_default": raw_m,
                "ev_default": score_bets(bets_default, f"{wname}/{var_name}/{market}/default"),
                "bets_default_per_prior": {},
                "v1_blend": {},
                "v2_hp": {},
                "ev_opt_blend": {},
                "ev_tuned_hp": {},
            }

            # V3: cold-start breakdown of default bets
            for thresh in V3_THRESHOLDS:
                warm = [b for b in bets_default if b["min_prior"] >= thresh]
                cold = [b for b in bets_default if 0 <= b["min_prior"] < thresh]
                mkt_result["bets_default_per_prior"][f"warm_ge{thresh}"] = score_bets(
                    warm, f"{wname}/{var_name}/{market}/warm_ge{thresh}"
                )
                mkt_result["bets_default_per_prior"][f"cold_lt{thresh}"] = score_bets(
                    cold, f"{wname}/{var_name}/{market}/cold_lt{thresh}"
                )

            if full_diag:
                # V1b: blend weight search
                blend_res = blend_weight_search(model_default, X_cal, cal_fx, market, cfg)
                mkt_result["v1_blend"] = blend_res

                # EV bets with optimized blend weight (per outcome)
                for oc in cfg["outcomes"]:
                    oc_name = oc["name"]
                    best_w  = blend_res.get(oc_name, {}).get("best_weight", 0.35)
                    bets_opt = simulate_bets(
                        model_default, X_cal, cal_fx, X_test, test_fx,
                        market, cfg, wname, model_weight=best_w,
                        prior_lookup=prior_lookup,
                    )
                    mkt_result["ev_opt_blend"][oc_name] = {
                        "best_weight": best_w,
                        **score_bets(bets_opt, f"{wname}/{var_name}/{market}/opt_blend_{oc_name}"),
                    }

                # V2: HP search
                hp_res = hp_search(market, X_fit, y_fit, X_cal, cal_fx, cfg)
                mkt_result["v2_hp"] = hp_res

                best_hp = hp_res.get("best_hp") or {"num_leaves": 31, "n_estimators": 300}
                nl  = best_hp.get("num_leaves",  31)
                ne  = best_hp.get("n_estimators", 300)

                # Only refit + simulate if best HP differs from default
                if nl != 31 or ne != 300:
                    model_tuned = fit_lgbm_custom(
                        market, X_fit[mask_fit], y_fit[mask_fit],
                        num_leaves=nl, n_estimators=ne,
                    )
                    if model_tuned is not None:
                        raw_m_tuned = raw_classifier_metrics(
                            model_tuned, X_test, test_fx, market, cfg
                        )
                        bets_tuned = simulate_bets(
                            model_tuned, X_cal, cal_fx, X_test, test_fx,
                            market, cfg, wname, model_weight=0.35,
                            prior_lookup=prior_lookup,
                        )
                        mkt_result["ev_tuned_hp"] = {
                            "best_hp":      {"num_leaves": nl, "n_estimators": ne},
                            "raw_metrics":  raw_m_tuned,
                            **score_bets(bets_tuned, f"{wname}/{var_name}/{market}/tuned_hp"),
                        }
                else:
                    # Best HP IS the default — no refit needed
                    mkt_result["ev_tuned_hp"] = {
                        "best_hp":     {"num_leaves": nl, "n_estimators": ne},
                        "note":        "best HP same as default; no refit",
                        **score_bets(bets_default, f"{wname}/{var_name}/{market}/tuned_hp_same"),
                    }

            var_result[market] = mkt_result
        win_result[var_name] = var_result

    return win_result


# ── V3 global summary ──────────────────────────────────────────────────────────

def v3_global_summary(
    all_window_results: List[dict],
    prior_counts_val:   List[dict],
    val_fix:            List[dict],
) -> dict:
    """Aggregate cold-start statistics across all windows."""
    counts = [pc["min_prior"] for pc in prior_counts_val]
    counts = [c for c in counts if c >= 0]

    summary: dict = {
        "n_val_fixtures": len(counts),
        "prior_counts": {},
    }
    for thresh in V3_THRESHOLDS:
        n_cold = sum(1 for c in counts if c < thresh)
        summary["prior_counts"][f"lt_{thresh}"] = {
            "n":      n_cold,
            "frac":   n_cold / len(counts) if counts else float("nan"),
        }

    # Aggregate bet-level cold-start stats from 29feat_n5 across all windows
    bets_agg: dict[str, dict] = {}
    for wr in all_window_results:
        v = wr.get("29feat_n5", {})
        for market, mr in v.items():
            if market not in bets_agg:
                bets_agg[market] = {"warm5": [], "cold5": [], "warm10": [], "cold10": []}
            pp = mr.get("bets_default_per_prior", {})
            # We don't have the raw bet lists here; use the scored summaries
            bets_agg[market][f"warm5_n"]  = bets_agg[market].get("warm5_n", 0)  + pp.get("warm_ge5", {}).get("n_bets", 0)
            bets_agg[market][f"cold5_n"]  = bets_agg[market].get("cold5_n", 0)  + pp.get("cold_lt5", {}).get("n_bets", 0)
            bets_agg[market][f"warm10_n"] = bets_agg[market].get("warm10_n", 0) + pp.get("warm_ge10", {}).get("n_bets", 0)
            bets_agg[market][f"cold10_n"] = bets_agg[market].get("cold10_n", 0) + pp.get("cold_lt10", {}).get("n_bets", 0)

            # Accumulate pnl arrays for ROI computation — from scored summaries
            for key, subkey in [("warm5", "warm_ge5"), ("cold5", "cold_lt5"),
                                 ("warm10", "warm_ge10"), ("cold10", "cold_lt10")]:
                sc = pp.get(subkey, {})
                bets_agg[market].setdefault(f"{key}_roi_samples", [])
                if sc.get("n_bets", 0) > 0:
                    bets_agg[market][f"{key}_roi_samples"].append({
                        "n": sc["n_bets"], "roi": sc.get("roi", float("nan")),
                        "ci_lo": sc.get("ci_lo", float("nan")),
                        "ci_hi": sc.get("ci_hi", float("nan")),
                    })

    summary["bet_breakdown_by_market"] = bets_agg
    return summary


# ── Report generation ──────────────────────────────────────────────────────────

def _fmt_roi(r: dict, key="roi") -> str:
    v = r.get(key, float("nan"))
    return f"{v*100:+.1f}%" if not np.isnan(v) else "n/a"

def _fmt_ci(r: dict) -> str:
    lo = r.get("ci_lo", float("nan"))
    hi = r.get("ci_hi", float("nan"))
    if np.isnan(lo):
        return "[n/a]"
    return f"[{lo*100:+.1f}%, {hi*100:+.1f}%]"

def _fmt_auc(r: dict) -> str:
    v = r.get("auc", float("nan"))
    return f"{v:.4f}" if not np.isnan(v) else "n/a"

def _fmt_ll(r: dict) -> str:
    v = r.get("log_loss", float("nan"))
    return f"{v:.4f}" if not np.isnan(v) else "n/a"

def _fmt_br(r: dict) -> str:
    v = r.get("brier", float("nan"))
    return f"{v:.4f}" if not np.isnan(v) else "n/a"


def build_report_section(all_window_results: List[dict], global_results: dict) -> str:
    lines = []
    L = lines.append

    L("\n---\n")
    L("## Diagnostic Addendum: V1 / V2 / V3\n")
    L("*Appended after Task R. Pre-registered success bar from Task R applies.*\n")
    L("*Ground rules: read-only production schema; all analysis in scripts/analysis/.*\n")

    # ── V2 first: code-read finding ───────────────────────────────────────────
    L("### V2 Hyperparameter Finding (code read)\n")
    L("| Model | n_estimators | num_leaves | learning_rate | Features |")
    L("|-------|-------------|------------|--------------|----------|")
    L("| V3 baseline | 200 | 31 | 0.05 | 9 (standings) |")
    L("| V4 Wave 1 | 300 | 31 | 0.05 | 29 (standings + Wave1) |")
    L("")
    L("V4 bumped `n_estimators` from 200→300 but kept `num_leaves=31`.")
    L("With 3× more features, the same tree complexity likely underfits —")
    L("this is a plausible confound. Grid below tests `num_leaves` ∈ {31, 63, 127}")
    L("and `n_estimators` ∈ {300, 500}.\n")

    # ── V1: raw classifier metrics ────────────────────────────────────────────
    L("### V1a: Raw Classifier Metrics (test set, no market blend)\n")
    L("Metrics from raw model probabilities — before Platt calibration and before blending.")
    L("Lower log-loss/Brier = better; higher AUC = better.\n")

    for market in ["h2h", "ou25", "btts", "ou15"]:
        L(f"**{market.upper()}**\n")
        L("| Window | Variant | AUC | Log-Loss | Brier |")
        L("|--------|---------|-----|----------|-------|")
        for wr in all_window_results:
            wname = wr["window"]
            for var in ["9feat", "29feat_n5", "29feat_n10"]:
                mr = wr.get(var, {}).get(market, {})
                rm = mr.get("raw_metrics_default", {})
                if rm:
                    L(f"| {wname} | {var} | {_fmt_auc(rm)} | {_fmt_ll(rm)} | {_fmt_br(rm)} |")
        L("")

    # ── V1b: blend weight optimization ────────────────────────────────────────
    L("### V1b: Blend Weight Re-Optimization (29-feat N=5)\n")
    L("Grid: model weights [0%, 15%, 25%, 35%, 50%, 65%, 100%].")
    L("Selected on cal holdout only (note: same holdout used for Platt → mild optimism).\n")

    for market in ["h2h", "ou25", "btts"]:
        L(f"**{market.upper()}**\n")
        L("| Window | Outcome | Best Weight | Default (0.35) LL | Best LL | Bets(default) | ROI(default) | Bets(opt) | ROI(opt) |")
        L("|--------|---------|------------|-------------------|---------|--------------|-------------|----------|---------|")
        for wr in all_window_results:
            wname = wr["window"]
            mr = wr.get("29feat_n5", {}).get(market, {})
            if not mr:
                continue
            blend = mr.get("v1_blend", {})
            ev_def = mr.get("ev_default", {})
            for oc_name, bdata in blend.items():
                bw   = bdata.get("best_weight", float("nan"))
                sbs  = bdata.get("scores_by_weight", {})
                ll35 = sbs.get("0.35", float("nan"))
                ll_best = sbs.get(str(bw), float("nan"))
                ev_opt = mr.get("ev_opt_blend", {}).get(oc_name, {})
                ll35_s = f"{ll35:.4f}" if not np.isnan(ll35) else "n/a"
                llb_s  = f"{ll_best:.4f}" if not np.isnan(ll_best) else "n/a"
                L(f"| {wname} | {oc_name} | {bw:.0%} | {ll35_s} | {llb_s} "
                  f"| {ev_def.get('n_bets','n/a')} | {_fmt_roi(ev_def)} "
                  f"| {ev_opt.get('n_bets','n/a')} | {_fmt_roi(ev_opt)} |")
        L("")

    # ── V2: HP grid results ───────────────────────────────────────────────────
    L("### V2: Hyperparameter Grid Search Results (29-feat N=5)\n")
    L("Selected by cal-holdout log-loss. Tuned EV uses best HP with MODEL_WEIGHT=0.35.\n")

    for market in ["h2h", "ou25"]:
        L(f"**{market.upper()}**\n")
        L("| Window | Best HP | Cal-LL (default nl31/ne300) | Cal-LL (best) | Bets(default) | ROI(default) | Bets(tuned) | ROI(tuned) | CI(tuned) |")
        L("|--------|---------|--------------------------|---------------|--------------|-------------|------------|-----------|----------|")
        for wr in all_window_results:
            wname = wr["window"]
            mr    = wr.get("29feat_n5", {}).get(market, {})
            if not mr:
                continue
            v2    = mr.get("v2_hp", {})
            scores= v2.get("scores", {})
            bh    = v2.get("best_hp") or {}
            default_ll = scores.get("nl31_ne300", {}).get("log_loss", float("nan"))
            best_ll    = bh.get("log_loss", float("nan"))
            ev_def = mr.get("ev_default", {})
            ev_tun = mr.get("ev_tuned_hp", {})
            dfl_s  = f"{default_ll:.4f}" if not np.isnan(default_ll) else "n/a"
            bst_s  = f"{best_ll:.4f}" if not np.isnan(best_ll) else "n/a"
            best_hp_str = f"nl{bh.get('num_leaves','?')}/ne{bh.get('n_estimators','?')}"
            L(f"| {wname} | {best_hp_str} | {dfl_s} | {bst_s} "
              f"| {ev_def.get('n_bets','n/a')} | {_fmt_roi(ev_def)} "
              f"| {ev_tun.get('n_bets','n/a')} | {_fmt_roi(ev_tun)} | {_fmt_ci(ev_tun)} |")
        L("")

    # ── V3: rolling reliability ───────────────────────────────────────────────
    L("### V3: Rolling-Feature Reliability (29-feat N=5)\n")

    v3g = global_results.get("v3_global", {})
    pc  = v3g.get("prior_counts", {})
    nv  = v3g.get("n_val_fixtures", 0)
    L(f"Validation pool: {nv:,} fixtures.\n")
    L("| Threshold | Cold-start fixtures | Fraction |")
    L("|-----------|-------------------|---------|")
    for k, v in pc.items():
        L(f"| < {k.split('_')[1]} prior matches | {v.get('n',0):,} | {v.get('frac',float('nan'))*100:.1f}% |")
    L("")

    L("**Cold-start vs warm bet ROI (all windows combined, 29-feat N=5):**\n")
    L("| Market | Threshold | Group | N bets | Avg ROI | CI |")
    L("|--------|-----------|-------|--------|---------|-----|")
    bbd = v3g.get("bet_breakdown_by_market", {})
    for market in ["h2h", "ou25", "btts"]:
        if market not in bbd:
            continue
        m = bbd[market]
        for thresh in V3_THRESHOLDS:
            for grp, subkey in [(f"≥{thresh} prior","warm"), (f"<{thresh} prior","cold")]:
                samples = m.get(f"{subkey}{thresh}_roi_samples", [])
                n_total = sum(s["n"] for s in samples)
                if n_total == 0:
                    L(f"| {market} | {thresh} | {grp} | 0 | n/a | n/a |")
                    continue
                # Weighted average ROI
                roi_w = sum(s["n"] * s["roi"] for s in samples if not np.isnan(s.get("roi", float("nan")))) / max(n_total, 1)
                L(f"| {market} | {thresh} | {grp} | {n_total:,} | {roi_w*100:+.1f}% | (per-window CI above) |")
    L("")

    L("### Diagnostic Conclusions\n")
    L("*(Auto-generated from run results — interpret in context of Task R verdicts.)*\n")

    # Draw conclusions from the data
    conclusions = []

    # V1a: did Wave1 improve raw metrics over 9-feat?
    conclusions.append("**V1a (Raw Classifier):** See table above. If Wave1 AUC ≤ 9-feat AUC, "
                        "the Wave1 feature set provides no classifier improvement and the negative "
                        "EV outcome is explained at the signal level, not the blending/HP level.")

    # V1b: did optimized blend weight change verdict?
    conclusions.append("**V1b (Blend Weight):** See table above. If best_weight ≠ 0.35 and "
                        "ROI(opt) materially differs from ROI(default), blend weight was a confound.")

    # V2: did tuned HP change verdict?
    conclusions.append("**V2 (HP Tuning):** See table above. If ROI(tuned) with wider num_leaves "
                        "still shows negative CI, HP underfitting was not the primary cause of failure.")

    # V3: cold-start
    conclusions.append("**V3 (Cold-Start):** See fraction table above. If cold-start fixtures "
                        "have significantly worse ROI than warm fixtures, fixture_stats data "
                        "coverage is a confound for Wave1 rolling features.")

    for c in conclusions:
        L(f"- {c}")

    L("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import sqlite3
    logger.info("=== Diagnostic V1/V2/V3 (Bootball Phase 2) ===")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"ATTACH '{HIST_DB_PATH}' AS hist")

    logger.info("Loading training fixtures...")
    all_train = load_all_training_fixtures(conn)
    logger.info("Loading validation fixtures...")
    val_fix   = load_validation_fixtures(conn)

    logger.info("Building StandingsCache...")
    sc = StandingsCache()
    sc.build(all_train + val_fix)

    logger.info("Loading Wave1 FeatureBuilder...")
    wave1 = FeatureBuilder(conn, n_rolling=5, min_season=2018)
    wave1.load()
    logger.info(f"  Teams: {len(wave1._team_form):,}  "
                f"Leagues: {len(wave1._league_history):,}  "
                f"H2H: {len(wave1._h2h):,}")

    # V3: compute prior counts for all validation fixtures
    logger.info("Computing prior match counts for validation fixtures (V3)...")
    prior_counts_val = compute_prior_counts(val_fix, wave1)

    # Feature matrices (cached)
    feature_mats = load_or_compute_features(all_train, val_fix, sc, wave1)

    # Canary check: 9-feat matrices should have 9 columns
    assert feature_mats["train9"].shape[1]   == 9,  "train9 shape mismatch"
    assert feature_mats["train_n5"].shape[1] == 29, "train_n5 shape mismatch"

    logger.info("\n--- Running per-window diagnostics ---")
    all_window_results = []
    for window in WINDOWS:
        wr = run_window(window, feature_mats, all_train, val_fix, prior_counts_val)
        all_window_results.append(wr)

    # V3 global summary
    v3g = v3_global_summary(all_window_results, prior_counts_val, val_fix)

    global_results = {"v3_global": v3g}

    # Write JSON output
    output = {
        "windows":        all_window_results,
        "global":         global_results,
        "config": {
            "blend_weight_grid": BLEND_WEIGHT_GRID,
            "hp_grid":           HP_GRID,
            "bot_min_ev":        BOT_MIN_EV,
            "v3_thresholds":     V3_THRESHOLDS,
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info(f"Results written to {OUTPUT_PATH}")

    # Canary: ou25 default EV for 29feat_n5 across windows should be negative
    for wr in all_window_results:
        ou25_ev = wr.get("29feat_n5", {}).get("ou25", {}).get("ev_default", {})
        roi = ou25_ev.get("roi", float("nan"))
        if not np.isnan(roi) and roi > 0.20:
            logger.warning(
                f"CANARY FAIL: Window {wr['window']} ou25 default ROI = {roi*100:+.1f}% "
                f"— label inversion may be back. Check win_label fix."
            )

    # Append report section
    report_text = build_report_section(all_window_results, global_results)
    with open(REPORT_PATH, "a") as f:
        f.write(report_text)
    logger.info(f"Appended diagnostic section to {REPORT_PATH}")

    # Print summary to stdout
    print("\n" + "="*70)
    print("DIAGNOSTIC SUMMARY")
    print("="*70)
    for wr in all_window_results:
        wname = wr["window"]
        print(f"\nWindow: {wname}")
        for var in ["9feat", "29feat_n5"]:
            vd = wr.get(var, {})
            for market in ["h2h", "ou25"]:
                mr = vd.get(market, {})
                if not mr:
                    continue
                ev_def = mr.get("ev_default", {})
                ev_tun = mr.get("ev_tuned_hp", {})
                rm     = mr.get("raw_metrics_default", {})
                n = ev_def.get("n_bets", 0)
                roi = ev_def.get("roi", float("nan"))
                auc = rm.get("auc", float("nan"))
                print(f"  {var}/{market}: {n} bets, ROI {roi*100:+.1f}%, AUC {auc:.4f}", end="")
                if ev_tun and ev_tun.get("n_bets", 0) > 0:
                    n_t = ev_tun.get("n_bets", 0)
                    roi_t = ev_tun.get("roi", float("nan"))
                    print(f" | tuned: {n_t} bets, ROI {roi_t*100:+.1f}%", end="")
                print()


if __name__ == "__main__":
    main()
