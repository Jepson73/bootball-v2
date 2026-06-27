"""
Phase 3 W1–W4: Dixon-Coles walk-forward validation.

W1: Per-league DC model — parameters, ξ CV, thin-data coverage
W2: Nested Poisson vs Dixon-Coles comparison (ρ=0 vs fitted ρ)
W3: All four markets from one joint P(i,j); cross-market consistency
W4: Walk-forward validation (same 3 windows as V4) — raw metrics vs Wave 1
    baseline (h2h AUC 0.58, ou25 AUC 0.54), then EV backtest with per-window
    blend-weight CV; explicit pass/fail against pre-registered bar.

Pre-registered success bar:
  - 95% CI excludes zero (positive ROI)
  - ≥500 bets per market per window
  - ≥2 non-overlapping windows passing
  (btts and ou15 cannot pass — fdco has no btts/ou15 odds → 1-window ceiling)

Run: python3 scripts/analysis/dixon_coles_backtest.py
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import (norm, poisson as scipy_poisson)
from sklearn.metrics import (log_loss, roc_auc_score, brier_score_loss)

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
DB   = ROOT / 'data' / 'football.db'
HIST = Path(__file__).parent / 'historical_odds.db'
CACHE_DIR = Path(__file__).parent / 'dc_cache'
REPORT    = Path(__file__).parent / 'v3_phase3_report.md'

sys.path.insert(0, str(Path(__file__).parent))
from dixon_coles_model import (
    DixonColesLeague, DixonColesLeagueSet,
    joint_prob_matrix, derive_markets,
)

# ── Load V4 harness for data loading and utilities ────────────────────────────
_v4_path = Path(__file__).parent / 'walk_forward_backtest_v4.py'
spec = importlib.util.spec_from_file_location('wfbv4', _v4_path)
_v4  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_v4)
shin_probabilities  = _v4.shin_probabilities
bootstrap_roi_ci    = _v4.bootstrap_roi_ci

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BOT_MIN_EV  = 0.05
BLEND_GRID  = [0.0, 0.15, 0.25, 0.35, 0.50, 0.65, 1.0]
XI_GRID     = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]   # annual decay rates
N_BOOTSTRAP = 2000

# fdco leagues — used for ξ tuning (most consistent multi-season history)
FDCO_LEAGUES = [39, 40, 41, 42, 135, 136, 140, 141]

WINDOWS = [
    {'name': '2022',    'test_start': '2022-01-01', 'test_end': '2023-01-01'},
    {'name': '2023',    'test_start': '2023-01-01', 'test_end': '2025-01-01'},
    {'name': '2025-26', 'test_start': '2025-01-01', 'test_end': '2027-01-01'},
]

# Wave 1 baselines from Phase 2 (V1a)
WAVE1_AUC = {'h2h': 0.5844, 'ou25': 0.5392}

# ── Data loading ─────────────────────────────────────────────────────────────

def load_goals_fixtures(conn: sqlite3.Connection) -> List[dict]:
    """Load all completed fixtures with goals data (for DC fitting)."""
    rows = conn.execute("""
        SELECT id, league_id, home_team_id, away_team_id, date, goals_home, goals_away
        FROM fixtures
        WHERE status = 'FT' AND goals_home IS NOT NULL AND goals_away IS NOT NULL
          AND season >= 2019
        ORDER BY date ASC
    """).fetchall()
    cols = ['id', 'league_id', 'home_team_id', 'away_team_id', 'date', 'goals_home', 'goals_away']
    logger.info(f"Loaded {len(rows):,} goal fixtures for DC fitting")
    return [dict(zip(cols, r)) for r in rows]


def load_validation_fixtures(conn: sqlite3.Connection) -> List[dict]:
    """Reuse V4 loader — returns fixtures with odds for EV simulation."""
    return _v4.load_validation_fixtures(conn)


# ── ξ inner-CV tuning ─────────────────────────────────────────────────────────

def tune_xi(all_goals: List[dict], test_start: str,
            holdout_months: int = 6) -> Tuple[float, Dict]:
    """
    Pick best ξ via inner holdout.
    Holds out the last <holdout_months> months of the training window.
    Uses fdco leagues only for consistent comparison across windows.
    Returns (best_xi, {xi: mean_nll}).
    """
    from datetime import timedelta
    cutoff_dt  = datetime.fromisoformat(test_start[:10])
    # inner holdout = last 6 months of training
    holdout_dt = datetime(cutoff_dt.year, cutoff_dt.month, cutoff_dt.day)
    # walk back ~182 days
    inner_cut = datetime(
        holdout_dt.year - (1 if holdout_dt.month <= 6 else 0),
        ((holdout_dt.month - 6 - 1) % 12) + 1,
        1
    )
    inner_cut_str  = inner_cut.strftime('%Y-%m-%d')

    fdco_goals = [f for f in all_goals if f['league_id'] in FDCO_LEAGUES]
    inner_train = [f for f in fdco_goals if f['date'] <  inner_cut_str]
    inner_val   = [f for f in fdco_goals if inner_cut_str <= f['date'] < test_start]

    if len(inner_val) < 200:
        logger.warning(f"Inner val only {len(inner_val)} fixtures; returning ξ=0")
        return 0.0, {}

    scores: Dict[float, float] = {}
    for xi in XI_GRID:
        ls = DixonColesLeagueSet(xi=xi, use_rho=True)
        ls.fit(inner_train, inner_cut_str, league_ids=FDCO_LEAGUES, n_jobs=1)
        nll = ls.avg_nll_on(inner_val, inner_cut_str)
        scores[xi] = nll
        logger.info(f"  ξ={xi:.2f} → inner-val mean NLL = {nll:.5f}")

    best_xi = min(scores, key=lambda x: scores[x] if not np.isnan(scores[x]) else 1e9)
    logger.info(f"Best ξ = {best_xi} (NLL {scores[best_xi]:.5f})")
    return best_xi, scores


# ── Predictions for a val window ─────────────────────────────────────────────

def predict_window(ls_dc: DixonColesLeagueSet,
                   ls_poi: DixonColesLeagueSet,
                   val_fixtures: List[dict]) -> List[dict]:
    """
    Generate per-fixture predictions for both DC and Poisson models.
    Each result dict has: fixture fields + dc/poi market probs.
    """
    results = []
    for f in val_fixtures:
        pred_dc  = ls_dc.predict(f)
        pred_poi = ls_poi.predict(f)
        results.append({
            'id':           f['id'],
            'league_id':    f['league_id'],
            'date':         f['date'],
            'home_team_id': f['home_team_id'],
            'away_team_id': f['away_team_id'],
            'goals_home':   f['goals_home'],
            'goals_away':   f['goals_away'],
            'outcome':      f['outcome'],
            'odd_home':     f.get('odd_home'),
            'odd_draw':     f.get('odd_draw'),
            'odd_away':     f.get('odd_away'),
            'odd_ou25_over':f.get('odd_ou25_over'),
            'odd_ou25_under':f.get('odd_ou25_under'),
            'odd_btts_yes': f.get('odd_btts_yes'),
            'odd_btts_no':  f.get('odd_btts_no'),
            'odd_ou15_over':f.get('odd_ou15_over'),
            'odd_ou15_under':f.get('odd_ou15_under'),
            'odds_source':  f.get('odds_source', ''),
            'dc':  pred_dc,
            'poi': pred_poi,
        })
    return results


# ── Raw metrics (W4.2) ────────────────────────────────────────────────────────

def _h2h_outcome(f: dict) -> Optional[int]:
    """Returns 0 (H), 1 (D), 2 (A) from goals. None if missing."""
    gh, ga = f.get('goals_home'), f.get('goals_away')
    if gh is None or ga is None:
        return None
    if gh > ga:  return 0
    if gh == ga: return 1
    return 2


def _ou25_outcome(f: dict) -> Optional[int]:
    gh, ga = f.get('goals_home'), f.get('goals_away')
    if gh is None or ga is None:
        return None
    return 1 if (gh + ga) > 2 else 0


def _btts_outcome(f: dict) -> Optional[int]:
    gh, ga = f.get('goals_home'), f.get('goals_away')
    if gh is None or ga is None:
        return None
    return 1 if (gh > 0 and ga > 0) else 0


def _ou15_outcome(f: dict) -> Optional[int]:
    gh, ga = f.get('goals_home'), f.get('goals_away')
    if gh is None or ga is None:
        return None
    return 1 if (gh + ga) > 1 else 0


def raw_metrics(preds: List[dict], model_key: str = 'dc') -> Dict:
    """AUC, log-loss, Brier for each available market."""
    out = {}

    # h2h (multiclass)
    h2h_probs, h2h_labels = [], []
    for p in preds:
        lbl = _h2h_outcome(p)
        ph  = p[model_key].get('p_h2h')
        if lbl is not None and ph is not None:
            h2h_probs.append(ph)
            h2h_labels.append(lbl)

    if len(h2h_labels) >= 50:
        try:
            auc_h2h = roc_auc_score(h2h_labels, h2h_probs, multi_class='ovr', average='macro')
            ll_h2h  = log_loss(h2h_labels, h2h_probs)
            # brier (mean of per-class brier)
            n = len(h2h_labels)
            yp = np.array(h2h_probs)
            brier = float(np.mean([
                np.mean((yp[:, c] - (np.array(h2h_labels) == c).astype(float)) ** 2)
                for c in range(3)
            ]))
            out['h2h'] = {'n': n, 'auc': round(auc_h2h, 4),
                          'log_loss': round(ll_h2h, 5), 'brier': round(brier, 5)}
        except Exception as e:
            out['h2h'] = {'error': str(e)}

    # Binary markets
    for market, prob_key, outcome_fn, has_odds_key in [
        ('ou25', 'p_ou25_over', _ou25_outcome, 'odd_ou25_over'),
        ('btts', 'p_btts_yes',  _btts_outcome, 'odd_btts_yes'),
        ('ou15', 'p_ou15_over', _ou15_outcome, 'odd_ou15_over'),
    ]:
        probs, labels = [], []
        for p in preds:
            lbl = outcome_fn(p)
            pv  = p[model_key].get(prob_key)
            if lbl is not None and pv is not None:
                probs.append(pv)
                labels.append(lbl)
        if len(labels) >= 50:
            try:
                auc = roc_auc_score(labels, probs)
                ll  = log_loss(labels, probs)
                br  = brier_score_loss(labels, probs)
                out[market] = {'n': len(labels), 'auc': round(auc, 4),
                               'log_loss': round(ll, 5), 'brier': round(br, 5)}
            except Exception as e:
                out[market] = {'error': str(e)}

    return out


# ── Blend weight CV (per V1b pattern) ─────────────────────────────────────────

def blend_weight_cv(all_val: List[dict], test_start: str, model_key: str = 'dc') -> float:
    """
    Select blend weight w using pre-window val fixtures (has odds by construction).
    Mirrors V1b's validated approach — no test leakage.
    """
    pre_val = [p for p in all_val if p['date'] < test_start]
    if len(pre_val) < 50:
        logger.warning(f"Only {len(pre_val)} pre-window val fixtures; using w=0.35")
        return 0.35

    best_w, best_ll = 0.35, float('inf')
    for w in BLEND_GRID:
        lls = []
        for p in pre_val:
            # h2h
            odds = [p.get('odd_home'), p.get('odd_draw'), p.get('odd_away')]
            if all(o is not None and o > 1.01 for o in odds):
                lbl = _h2h_outcome(p)
                ph  = p[model_key].get('p_h2h')
                if lbl is not None and ph is not None:
                    shin = shin_probabilities(odds)
                    pbl  = [w * ph[c] + (1 - w) * shin[c] for c in range(3)]
                    pbl  = [max(1e-9, min(1 - 1e-9, x)) for x in pbl]
                    s    = sum(pbl); pbl = [x / s for x in pbl]
                    lls.append(-np.log(pbl[lbl]))
            # ou25
            o_ov, o_un = p.get('odd_ou25_over'), p.get('odd_ou25_under')
            if o_ov is not None and o_un is not None and o_ov > 1.01 and o_un > 1.01:
                lbl = _ou25_outcome(p)
                pov = p[model_key].get('p_ou25_over')
                if lbl is not None and pov is not None:
                    shin = shin_probabilities([o_ov, o_un])
                    pbl  = w * pov + (1 - w) * shin[0]
                    pbl  = max(1e-9, min(1 - 1e-9, pbl))
                    lls.append(-np.log(pbl if lbl == 1 else 1 - pbl))
        if lls:
            ll = np.mean(lls)
            if ll < best_ll:
                best_ll, best_w = ll, w

    logger.info(f"Blend CV → best w={best_w} (log-loss {best_ll:.5f} over {len(pre_val)} pre-val)")
    return best_w


# ── EV simulation ─────────────────────────────────────────────────────────────

def simulate_bets(preds: List[dict], model_w: float,
                  model_key: str = 'dc') -> Dict[str, List[dict]]:
    """Run EV filter and collect per-market bets."""
    bets: Dict[str, List[dict]] = {m: [] for m in ['h2h', 'ou25', 'btts', 'ou15']}

    for p in preds:
        m_preds = p[model_key]

        # h2h
        odds = [p.get('odd_home'), p.get('odd_draw'), p.get('odd_away')]
        ph   = m_preds.get('p_h2h')
        if all(o is not None and o > 1.01 for o in odds) and ph is not None:
            shin = shin_probabilities(odds)
            for c, (name, o) in enumerate(zip(['H', 'D', 'A'], odds)):
                pbl = model_w * ph[c] + (1 - model_w) * shin[c]
                ev  = pbl * o - 1.0
                if ev > BOT_MIN_EV:
                    gh, ga = p.get('goals_home'), p.get('goals_away')
                    won = (gh is not None and ga is not None and _h2h_outcome(p) == c)
                    pnl = (o - 1.0) if won else -1.0
                    bets['h2h'].append({'name': name, 'odds': o, 'ev': ev,
                                        'p_model': ph[c], 'p_shin': shin[c],
                                        'p_blend': pbl, 'won': won, 'pnl': pnl,
                                        'date': p['date'], 'league_id': p['league_id']})

        # ou25
        o_ov, o_un = p.get('odd_ou25_over'), p.get('odd_ou25_under')
        pov = m_preds.get('p_ou25_over')
        if o_ov and o_un and o_ov > 1.01 and o_un > 1.01 and pov is not None:
            shin = shin_probabilities([o_ov, o_un])
            for name, pm, o, lbl_val in [('over', pov, o_ov, 1),
                                          ('under', 1 - pov, o_un, 0)]:
                pbl = model_w * pm + (1 - model_w) * shin[0 if name == 'over' else 1]
                ev  = pbl * o - 1.0
                if ev > BOT_MIN_EV:
                    won = (_ou25_outcome(p) == lbl_val)
                    pnl = (o - 1.0) if won else -1.0
                    bets['ou25'].append({'name': name, 'odds': o, 'ev': ev,
                                         'p_model': pm, 'p_blend': pbl,
                                         'won': won, 'pnl': pnl,
                                         'date': p['date'], 'league_id': p['league_id']})

        # btts
        o_by, o_bn = p.get('odd_btts_yes'), p.get('odd_btts_no')
        pby = m_preds.get('p_btts_yes')
        if o_by and o_bn and o_by > 1.01 and o_bn > 1.01 and pby is not None:
            shin = shin_probabilities([o_by, o_bn])
            for name, pm, o, lbl_val in [('yes', pby, o_by, 1),
                                          ('no',  1 - pby, o_bn, 0)]:
                pbl = model_w * pm + (1 - model_w) * shin[0 if name == 'yes' else 1]
                ev  = pbl * o - 1.0
                if ev > BOT_MIN_EV:
                    won = (_btts_outcome(p) == lbl_val)
                    pnl = (o - 1.0) if won else -1.0
                    bets['btts'].append({'name': name, 'odds': o, 'ev': ev,
                                          'p_model': pm, 'p_blend': pbl,
                                          'won': won, 'pnl': pnl,
                                          'date': p['date'], 'league_id': p['league_id']})

        # ou15
        o_15ov, o_15un = p.get('odd_ou15_over'), p.get('odd_ou15_under')
        p15 = m_preds.get('p_ou15_over')
        if o_15ov and o_15un and o_15ov > 1.01 and o_15un > 1.01 and p15 is not None:
            shin = shin_probabilities([o_15ov, o_15un])
            for name, pm, o, lbl_val in [('over', p15, o_15ov, 1),
                                           ('under', 1 - p15, o_15un, 0)]:
                pbl = model_w * pm + (1 - model_w) * shin[0 if name == 'over' else 1]
                ev  = pbl * o - 1.0
                if ev > BOT_MIN_EV:
                    won = (_ou15_outcome(p) == lbl_val)
                    pnl = (o - 1.0) if won else -1.0
                    bets['ou15'].append({'name': name, 'odds': o, 'ev': ev,
                                          'p_model': pm, 'p_blend': pbl,
                                          'won': won, 'pnl': pnl,
                                          'date': p['date'], 'league_id': p['league_id']})

    return bets


def ev_table(bets: Dict[str, List[dict]]) -> Dict[str, dict]:
    out = {}
    for market, bet_list in bets.items():
        if not bet_list:
            out[market] = {'n': 0}
            continue
        pnls = [b['pnl'] for b in bet_list]
        roi  = float(np.mean(pnls))
        ci   = bootstrap_roi_ci(pnls, N_BOOTSTRAP)
        out[market] = {
            'n': len(bet_list),
            'roi': round(roi, 4),
            'ci_lo': round(ci[0], 4),
            'ci_hi': round(ci[1], 4),
            'ci_excl_zero': (ci[0] > 0),
            'pass_500':     (len(bet_list) >= 500),
        }
    return out


# ── W3: cross-market consistency ─────────────────────────────────────────────

def consistency_check(preds: List[dict], model_key: str = 'dc', n_sample: int = 5) -> dict:
    """
    Verify that probabilities derived from the same joint matrix are internally consistent.
    Invariants by construction:
      P(H) + P(D) + P(A) ≈ 1
      P(OU15) ≥ P(BTTS)      (BTTS implies ≥ 2 goals → OU15)
      P(OU25) ≤ P(OU15)      (OU25 implies OU15)
    """
    violations_btts = violations_ou = 0
    total = 0
    sum_sq = 0.0
    examples = []

    for p in preds:
        m  = p[model_key]
        ph = m.get('p_h2h')
        if ph is None:
            continue
        s = sum(ph)
        sum_sq += (s - 1.0) ** 2

        pou25 = m.get('p_ou25_over', 0.0)
        pou15 = m.get('p_ou15_over', 0.0)
        pbtts = m.get('p_btts_yes', 0.0)

        if pou15 < pbtts - 1e-8:
            violations_btts += 1
        if pou25 > pou15 + 1e-8:
            violations_ou += 1
        total += 1

        if len(examples) < n_sample:
            examples.append({
                'id': p['id'],
                'p_h2h': [round(x, 4) for x in ph],
                'p_ou25': round(pou25, 4),
                'p_ou15': round(pou15, 4),
                'p_btts': round(pbtts, 4),
                'lam': round(m.get('lam', 0), 3),
                'mu':  round(m.get('mu', 0), 3),
            })

    return {
        'n': total,
        'h2h_norm_rmse': round(float(np.sqrt(sum_sq / max(total, 1))), 8),
        'violations_ou15_lt_btts': violations_btts,
        'violations_ou25_gt_ou15': violations_ou,
        'sample': examples,
    }


# ── Independent-classifiers incoherence demo (W3 contrast) ───────────────────

def check_independent_coherence(preds: List[dict]) -> dict:
    """
    Show fraction of fixtures where the independent Shin probabilities for
    ou15_over < btts_yes — a violation impossible with DC's joint matrix.
    """
    violations = total = 0
    for p in preds:
        o_by  = p.get('odd_btts_yes')
        o_bn  = p.get('odd_btts_no')
        o_15o = p.get('odd_ou15_over')
        o_15u = p.get('odd_ou15_under')
        if all(o is not None and o > 1.01 for o in [o_by, o_bn, o_15o, o_15u]):
            p_btts = shin_probabilities([o_by, o_bn])[0]
            p_ou15 = shin_probabilities([o_15o, o_15u])[0]
            if p_ou15 < p_btts - 0.01:
                violations += 1
            total += 1
    return {
        'n': total,
        'violations': violations,
        'pct_violations': round(100.0 * violations / max(total, 1), 2),
    }


# ── W1: thin-data coverage ────────────────────────────────────────────────────

def thin_data_report(ls: DixonColesLeagueSet, val_fixtures: List[dict]) -> dict:
    """Count val fixtures with at least one unseen team in DC model."""
    n_total  = len(val_fixtures)
    n_unknown_h = n_unknown_a = n_any_unknown = 0
    for f in val_fixtures:
        pred = ls.predict(f)
        if pred.get('h_unknown'):
            n_unknown_h += 1
        if pred.get('a_unknown'):
            n_unknown_a += 1
        if pred.get('h_unknown') or pred.get('a_unknown'):
            n_any_unknown += 1
    return {
        'n_total':        n_total,
        'n_any_unknown':  n_any_unknown,
        'pct_any_unknown': round(100.0 * n_any_unknown / max(n_total, 1), 1),
        'n_home_unknown': n_unknown_h,
        'n_away_unknown': n_unknown_a,
    }


# ── Main orchestration ────────────────────────────────────────────────────────

def main():
    CACHE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute(f"ATTACH '{HIST}' AS hist")

    logger.info("Loading goals fixtures for DC fitting...")
    all_goals = load_goals_fixtures(conn)

    logger.info("Loading validation fixtures (with odds)...")
    all_val = load_validation_fixtures(conn)

    # Pre-generate DC predictions for all val fixtures — we'll use them across windows
    # Actually predictions depend on the test window (we must fit per window), so we'll
    # compute per window.

    window_results = []

    for window in WINDOWS:
        wname      = window['name']
        test_start = window['test_start']
        test_end   = window['test_end']
        logger.info(f"\n{'='*60}")
        logger.info(f"WINDOW: {wname}  [{test_start} → {test_end}]")
        logger.info(f"{'='*60}")

        train_goals = [f for f in all_goals if f['date'] < test_start]
        win_val     = [f for f in all_val  if test_start <= f['date'] < test_end]

        if not win_val:
            logger.warning(f"No val fixtures for window {wname}, skipping")
            continue

        logger.info(f"Training goals: {len(train_goals):,}  Val fixtures: {len(win_val):,}")

        # Leagues to fit
        val_leagues = list(set(f['league_id'] for f in win_val))
        is_fdco_window = (wname in ('2022', '2023'))
        fit_leagues = FDCO_LEAGUES if is_fdco_window else val_leagues
        logger.info(f"Fit {len(fit_leagues)} leagues  (fdco_only={is_fdco_window})")

        # ── ξ tuning ─────────────────────────────────────────────────────────
        xi_cache = CACHE_DIR / f'xi_cv_{wname}.json'
        if xi_cache.exists():
            xi_data = json.loads(xi_cache.read_text())
            best_xi  = xi_data['best_xi']
            xi_scores = xi_data['scores']
            logger.info(f"ξ cache hit: best_xi={best_xi}")
        else:
            logger.info("Tuning ξ via inner holdout...")
            best_xi, xi_scores = tune_xi(all_goals, test_start)
            xi_cache.write_text(json.dumps({'best_xi': best_xi, 'scores': xi_scores}))

        # ── Fit final models ─────────────────────────────────────────────────
        for variant, use_rho in [('dc', True), ('poi', False)]:
            cache_path = CACHE_DIR / f'{variant}_{wname}.json'
            key = f'ls_{variant}'
            if cache_path.exists():
                logger.info(f"  Loading cached {variant} model for {wname}")
                ls = DixonColesLeagueSet.load(str(cache_path))
            else:
                logger.info(f"  Fitting {variant} ({len(fit_leagues)} leagues, ξ={best_xi})...")
                ls = DixonColesLeagueSet(xi=best_xi, use_rho=use_rho)
                # Production window: parallelise over 291 leagues; fdco: fast enough sequential
                n_jobs = 8 if not is_fdco_window else 1
                ls.fit(train_goals, test_start, league_ids=fit_leagues, n_jobs=n_jobs)
                ls.save(str(cache_path))

            if variant == 'dc':
                ls_dc  = ls
            else:
                ls_poi = ls

        logger.info("  DC summary:  " + str(ls_dc.summary()))
        logger.info("  Poi summary: " + str(ls_poi.summary()))

        # ── Generate predictions ─────────────────────────────────────────────
        preds_cache = CACHE_DIR / f'preds_{wname}.json'
        if preds_cache.exists():
            logger.info("  Loading cached predictions...")
            preds = json.loads(preds_cache.read_text())
        else:
            logger.info("  Generating predictions...")
            preds = predict_window(ls_dc, ls_poi, win_val)
            preds_cache.write_text(json.dumps(preds))
            logger.info(f"  Saved {len(preds)} predictions")

        # ── W1: thin-data report ─────────────────────────────────────────────
        thin = thin_data_report(ls_dc, win_val)
        logger.info(f"W1 thin-data: {thin['pct_any_unknown']:.1f}% val fixtures with ≥1 unknown team")

        # ── W2: raw metrics — DC vs Poisson ─────────────────────────────────
        metrics_dc  = raw_metrics(preds, 'dc')
        metrics_poi = raw_metrics(preds, 'poi')
        logger.info(f"W2 raw metrics (DC):  {metrics_dc}")
        logger.info(f"W2 raw metrics (Poi): {metrics_poi}")

        # ── W3: consistency (only if btts/ou15 odds available — window 3) ───
        consistency = consistency_check(preds, 'dc')
        indep_coh   = check_independent_coherence(preds)
        logger.info(f"W3 consistency: {consistency}")
        logger.info(f"W3 independent Shin violations: {indep_coh}")

        # ── W4: blend weight CV ──────────────────────────────────────────────
        logger.info("W4: blend weight CV on pre-window val...")
        best_w = blend_weight_cv(
            predict_window(ls_dc, ls_poi, [f for f in all_val if f['date'] < test_start]),
            test_start, 'dc'
        )

        # ── W4: EV simulation ────────────────────────────────────────────────
        logger.info(f"W4: EV simulation with w={best_w}...")
        bets      = simulate_bets(preds, best_w, 'dc')
        ev_result = ev_table(bets)
        logger.info(f"W4 EV table: {ev_result}")

        # ── Pass/fail ────────────────────────────────────────────────────────
        pf: Dict[str, str] = {}
        btts_ou15_note = "N/A — fdco has no btts/ou15 odds (data coverage constraint)"
        for market in ['h2h', 'ou25', 'btts', 'ou15']:
            r = ev_result.get(market, {})
            if market in ('btts', 'ou15') and is_fdco_window:
                pf[market] = 'N/A (no odds this window)'
                continue
            if r.get('n', 0) == 0:
                pf[market] = 'FAIL (no bets)'
            elif not r.get('pass_500', False):
                pf[market] = f"FAIL (<500 bets, n={r['n']})"
            elif not r.get('ci_excl_zero', False):
                pf[market] = f"FAIL (CI includes zero: [{r['ci_lo']},{r['ci_hi']}])"
            else:
                pf[market] = f"PASS (n={r['n']}, ROI={r['roi']:.3f}, CI=[{r['ci_lo']},{r['ci_hi']}])"

        logger.info(f"Pass/fail: {pf}")

        window_results.append({
            'window':      wname,
            'test_start':  test_start,
            'test_end':    test_end,
            'n_val':       len(win_val),
            'n_fit_leagues': len(fit_leagues),
            'best_xi':     best_xi,
            'xi_scores':   xi_scores,
            'best_w':      best_w,
            'thin':        thin,
            'dc_summary':  ls_dc.summary(),
            'metrics_dc':  metrics_dc,
            'metrics_poi': metrics_poi,
            'consistency': consistency,
            'indep_coherence': indep_coh,
            'ev_table':    ev_result,
            'pass_fail':   pf,
        })

    # ── Save full results ────────────────────────────────────────────────────
    results_path = Path(__file__).parent / 'dc_results.json'
    results_path.write_text(json.dumps(window_results, indent=2))
    logger.info(f"Full results saved to {results_path}")

    # ── Write report ─────────────────────────────────────────────────────────
    _write_report(window_results)
    logger.info(f"Report written to {REPORT}")


# ── Report generation ─────────────────────────────────────────────────────────

def _write_report(results: List[dict]) -> None:
    lines = []
    lines.append("\n---\n")
    lines.append("# Phase 3 — Dixon-Coles Goal Model (Approach B)\n\n")
    lines.append(f"**Run date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")

    lines.append("## Data coverage note\n\n")
    lines.append("fdco historical odds (2019–2024) cover h2h + ou25 only; no btts or ou15. "
                 "Therefore btts and ou15 cannot satisfy the ≥2 non-overlapping windows bar — "
                 "this is a data-coverage artifact, not a model failure. "
                 "The EV bar applies to **h2h and ou25** across windows 2022+2023+2025-26.\n\n")

    lines.append("## W1: Per-league DC parameters\n\n")
    for wr in results:
        dc_s = wr['dc_summary']
        thin = wr['thin']
        lines.append(f"### Window {wr['window']}\n\n")
        lines.append(f"- Leagues fitted: {dc_s['n_fitted']} / {dc_s['n_leagues']} "
                     f"(no-data: {dc_s['n_no_data']})\n")
        lines.append(f"- ρ: mean={dc_s['rho_mean']:.4f}, "
                     f"std={dc_s['rho_std']:.4f}, "
                     f"range=[{dc_s['rho_min']:.4f}, {dc_s['rho_max']:.4f}]\n")
        lines.append(f"- γ (home adv): mean={dc_s['gamma_mean']:.4f}, "
                     f"std={dc_s['gamma_std']:.4f}\n")
        lines.append(f"- Best ξ: {wr['best_xi']} (inner-CV NLL grid: "
                     + ', '.join(f"{k}={v:.4f}" for k, v in sorted(wr['xi_scores'].items(), key=lambda x: float(x[0])))
                     + ")\n")
        lines.append(f"- Thin-data: {thin['pct_any_unknown']:.1f}% val fixtures have ≥1 unseen team\n\n")

    lines.append("## W2: Poisson vs Dixon-Coles nested comparison\n\n")
    lines.append("| Window | Market | DC AUC | Poi AUC | DC log-loss | Poi log-loss | "
                 "DC Brier | Poi Brier |\n")
    lines.append("|--------|--------|--------|---------|-------------|--------------|"
                 "---------|----------|\n")
    for wr in results:
        for market in ['h2h', 'ou25', 'btts', 'ou15']:
            dc  = wr['metrics_dc'].get(market, {})
            poi = wr['metrics_poi'].get(market, {})
            if not dc.get('n', 0):
                continue
            lines.append(
                f"| {wr['window']} | {market} "
                f"| {dc.get('auc','?'):.4f} | {poi.get('auc','?'):.4f} "
                f"| {dc.get('log_loss','?'):.5f} | {poi.get('log_loss','?'):.5f} "
                f"| {dc.get('brier','?'):.5f} | {poi.get('brier','?'):.5f} |\n"
            )
    lines.append("\n")

    # Wave 1 baselines
    lines.append(f"*Wave 1 baselines (V1a, 29-feat-N5 avg): "
                 f"h2h AUC={WAVE1_AUC['h2h']}, ou25 AUC={WAVE1_AUC['ou25']}*\n\n")

    lines.append("## W3: Cross-market internal consistency\n\n")
    lines.append("All four markets are derived from the same joint P(i,j) matrix, so "
                 "invariants hold by construction: P(H)+P(D)+P(A)≈1, P(OU15)≥P(BTTS), P(OU25)≤P(OU15).\n\n")
    for wr in results:
        c   = wr['consistency']
        ich = wr['indep_coherence']
        lines.append(f"### Window {wr['window']}\n\n")
        lines.append(f"- h2h normalisation RMSE: {c['h2h_norm_rmse']:.2e} (should be ~0)\n")
        lines.append(f"- P(OU15)<P(BTTS) violations: {c['violations_ou15_lt_btts']} / {c['n']}\n")
        lines.append(f"- P(OU25)>P(OU15) violations: {c['violations_ou25_gt_ou15']} / {c['n']}\n")
        lines.append(f"- Independent Shin violations (P(OU15)<P(BTTS)): "
                     f"{ich['violations']} / {ich['n']} ({ich['pct_violations']:.1f}%)\n")
        if c.get('sample'):
            lines.append("\nSample predictions:\n\n")
            lines.append("| fixture | P(H) | P(D) | P(A) | P(OU25) | P(OU15) | P(BTTS) | λ | μ |\n")
            lines.append("|---------|------|------|------|---------|---------|---------|---|---|\n")
            for ex in c['sample']:
                ph = ex['p_h2h']
                lines.append(f"| {ex['id']} | {ph[0]:.3f} | {ph[1]:.3f} | {ph[2]:.3f} "
                              f"| {ex['p_ou25']:.3f} | {ex['p_ou15']:.3f} | {ex['p_btts']:.3f} "
                              f"| {ex['lam']:.2f} | {ex['mu']:.2f} |\n")
        lines.append("\n")

    lines.append("## W4: Walk-forward EV backtest\n\n")
    lines.append("| Window | Market | n bets | ROI | CI lo | CI hi | CI>0 | ≥500 | Pass/Fail |\n")
    lines.append("|--------|--------|--------|-----|-------|-------|------|------|-----------|\n")
    for wr in results:
        for market in ['h2h', 'ou25', 'btts', 'ou15']:
            r  = wr['ev_table'].get(market, {})
            pf = wr['pass_fail'].get(market, '?')
            if r.get('n', 0) == 0 and 'N/A' in pf:
                lines.append(f"| {wr['window']} | {market} | — | — | — | — | — | — | {pf} |\n")
                continue
            lines.append(
                f"| {wr['window']} | {market} | {r.get('n',0)} "
                f"| {r.get('roi','?')} | {r.get('ci_lo','?')} | {r.get('ci_hi','?')} "
                f"| {'YES' if r.get('ci_excl_zero') else 'NO'} "
                f"| {'YES' if r.get('pass_500') else 'NO'} "
                f"| {pf} |\n"
            )
    lines.append("\n")

    # Overall verdict
    lines.append("## Phase 3 Verdict\n\n")
    # Count windows passing per market
    market_passes: Dict[str, int] = {}
    for wr in results:
        for market in ['h2h', 'ou25']:
            pf_str = wr['pass_fail'].get(market, '')
            if pf_str.startswith('PASS'):
                market_passes[market] = market_passes.get(market, 0) + 1

    for market in ['h2h', 'ou25']:
        n_pass = market_passes.get(market, 0)
        verdict = "**PASS**" if n_pass >= 2 else "**FAIL**"
        lines.append(f"- **{market}**: {n_pass}/3 windows pass → {verdict} "
                     f"(bar: ≥2 windows, 95% CI>0, ≥500 bets)\n")
    lines.append("- **btts/ou15**: N/A — single window coverage only (fdco 2019-2024 has no btts/ou15 odds)\n\n")

    lines.append("*Pre-registered bar (locked before seeing results): "
                 "95% CI excludes zero, ≥500 bets/market/window, ≥2 non-overlapping windows.*\n")

    with open(REPORT, 'a') as f:
        f.writelines(lines)


if __name__ == '__main__':
    main()
