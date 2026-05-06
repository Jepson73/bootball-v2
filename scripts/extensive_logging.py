#!/usr/bin/env python3
"""
scripts/extensive_logging.py

Extensive logging script to trace model inputs, outputs, and calculations.
Run this to debug EV and Kelly discrepancies between daily_run, auto_bet, and web_ui.

Usage:
    python scripts/extensive_logging.py                    # Default fixture (Deportivo vs Mirandes)
    python scripts/extensive_logging.py --fixture 1392155   # Specific fixture ID
    python scripts/extensive_logging.py --market h2h       # Just h2h market
    python scripts/extensive_logging.py --all-markets      # All markets
"""
import argparse
import sys
from pathlib import Path
import os
import pickle
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
import numpy as np

from src.storage.db import get_session
from src.storage.models import Fixture, Standing, Team, League, FixtureOdds, ValueBet
from src.betting.ev import expected_value
from src.betting.kelly import kelly_fraction, fractional_kelly, stake as kelly_stake
from src.betting.shin import shin_probabilities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

MODEL_PATH = '/opt/projects/bootball/data/model_{market}.pkl'

MARKET_OUTCOMES = {
    "h2h": ["1", "X", "2"],
    "btts": ["Yes", "No"],
    "ou25": ["Over", "Under"],
    "ou15": ["Over", "Under"],
}

ODDS_FIELD_MAP = {
    "h2h": {"1": "odd_home", "X": "odd_draw", "2": "odd_away"},
    "btts": {"Yes": "odd_btts_yes", "No": "odd_btts_no"},
    "ou25": {"Over": "odd_over", "Under": "odd_under"},
    "ou15": {"Over": "odd_over15", "Under": "odd_under15"},
}


def get_model_prediction_trace(market: str, home_team_id: int, away_team_id: int, verbose: bool = True) -> dict:
    """Get prediction from model with extensive logging."""
    result = {
        'market': market,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'model_path': MODEL_PATH.format(market=market),
        'model_exists': False,
        'calibrator': None,
        'features': None,
        'raw_probs': None,
        'processed_probs': None,
        'error': None,
    }

    model_path = result['model_path']
    if not os.path.exists(model_path):
        logger.warning(f"Model not found: {model_path}")
        result['error'] = "Model not found"
        return result

    result['model_exists'] = True

    try:
        with open(model_path, 'rb') as f:
            obj = pickle.load(f)

        result['model_type'] = type(obj.get('model') if isinstance(obj, dict) else obj).__name__
        result['model_version'] = obj.get('version') if isinstance(obj, dict) else None
        result['features_used'] = obj.get('features_used') if isinstance(obj, dict) else None

        if isinstance(obj, dict):
            model = obj['model']
            result['calibrator'] = obj.get('calibrator')
            result['calibrator_type'] = type(obj.get('calibrator')).__name__ if obj.get('calibrator') else None
        else:
            model = obj
            result['calibrator'] = None

        if verbose:
            logger.info(f"  Model: {result['model_type']}, version={result['model_version']}")
            logger.info(f"  Features used: {result['features_used']}")
            logger.info(f"  Calibrator: {result['calibrator_type']}")

        with get_session() as s:
            home_standing = s.execute(
                select(Standing).where(Standing.team_id == home_team_id).where(Standing.season >= 2024)
            ).first()
            away_standing = s.execute(
                select(Standing).where(Standing.team_id == away_team_id).where(Standing.season >= 2024)
            ).first()

            if not home_standing or not away_standing:
                result['error'] = "Standing not found"
                return result

            hs = home_standing[0]
            as_ = away_standing[0]

            result['home_standing'] = {
                'team_id': hs.team_id,
                'rank': hs.rank,
                'goals_for': hs.goals_for,
                'goals_against': hs.goals_against,
                'goal_diff': (hs.goals_for or 1) - (hs.goals_against or 1),
            }
            result['away_standing'] = {
                'team_id': as_.team_id,
                'rank': as_.rank,
                'goals_for': as_.goals_for,
                'goals_against': as_.goals_against,
                'goal_diff': (as_.goals_for or 1) - (as_.goals_against or 1),
            }

            features = np.array([[
                float(hs.rank or 15),
                float(as_.rank or 15),
                float((hs.goals_for or 1) - (hs.goals_against or 1)),
                float((as_.goals_for or 1) - (as_.goals_against or 1)),
                float(hs.goals_for or 1),
                float(as_.goals_for or 1),
                float(hs.goals_against or 1),
                float(as_.goals_against or 1),
                float(abs((hs.rank or 15) - (as_.rank or 15))),
            ]])
            result['features'] = features[0].tolist()

            if verbose:
                logger.info(f"  Features: {features[0].tolist()}")

        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='X does not have valid feature names')
            raw_probs = model.predict_proba(features)[0]

        result['raw_probs'] = raw_probs.tolist()
        result['raw_probs_sum'] = sum(raw_probs)

        if verbose:
            logger.info(f"  Raw probs: {raw_probs}")
            logger.info(f"  Raw probs sum: {sum(raw_probs):.6f}")

        outcomes = MARKET_OUTCOMES.get(market, [])
        if len(outcomes) == 2:
            probs = {outcomes[0]: float(raw_probs[1]), outcomes[1]: float(1 - raw_probs[1])}
        elif len(raw_probs) == 3:
            probs = {outcomes[i]: float(raw_probs[i]) for i in range(3)}
        else:
            result['error'] = f"Unexpected raw_probs length: {len(raw_probs)}"
            return result

        if result['calibrator']:
            try:
                for k in probs:
                    probs[k] = max(0.01, min(0.99, result['calibrator'].predict([probs[k]])[0]))
                if verbose:
                    logger.info(f"  Calibrated probs: {probs}")
            except Exception as e:
                if verbose:
                    logger.warning(f"  Calibration failed: {e}")

        result['processed_probs'] = probs

        if verbose:
            outcome_str = ", ".join(f"{k}={v:.3f} ({v*100:.1f}%)" for k, v in probs.items())
            logger.info(f"  Processed probs: {outcome_str}")

    except Exception as e:
        logger.error(f"Model prediction error: {e}")
        result['error'] = str(e)

    return result


def calculate_ev_trace(model_probs: dict, odds_row, market: str, verbose: bool = True) -> list:
    """Calculate EV with extensive logging."""
    market_odds = {}
    field_map = ODDS_FIELD_MAP.get(market, {})
    for outcome, field in field_map.items():
        value = getattr(odds_row, field, None)
        if value:
            market_odds[outcome] = value

    if len(market_odds) < 2:
        return []

    outcomes = list(market_odds.keys())
    odds_values = [market_odds[o] for o in outcomes]

    try:
        shin_probs = shin_probabilities(odds_values)
        shin_note = "Shin probabilities"
    except Exception:
        shin_probs = [1 / o for o in odds_values]
        shin_note = "Naive implied (1/odd)"

    if verbose:
        logger.info(f"  Odds: {market_odds}")
        logger.info(f"  {shin_note}: {shin_probs}")

    candidates = []
    for i, outcome in enumerate(outcomes):
        model_prob = model_probs.get(outcome, 0.0)
        decimal_odd = market_odds[outcome]

        if decimal_odd <= 0:
            continue

        ev = expected_value(model_prob, decimal_odd)
        kf = fractional_kelly(model_prob, decimal_odd, 0.25)
        implied_raw = 1.0 / decimal_odd
        shin_implied = shin_probs[i] if i < len(shin_probs) else implied_raw

        candidate = {
            'outcome': outcome,
            'our_prob': model_prob,
            'decimal_odd': decimal_odd,
            'implied_prob_raw': implied_raw,
            'implied_prob_shin': shin_implied,
            'ev': ev,
            'ev_percent': ev * 100,
            'kelly_fraction': kf,
            'kelly_percent': kf * 100,
            'stake_kelly_1000': kf * 1000,
            'edge_vs_shin': (model_prob - shin_implied) * 100,
        }

        candidates.append(candidate)

        if verbose:
            logger.info(f"  {outcome}:")
            logger.info(f"    Our prob: {model_prob:.4f} ({model_prob*100:.2f}%)")
            logger.info(f"    Odds: {decimal_odd}")
            logger.info(f"    Implied (raw): {implied_raw:.4f} ({implied_raw*100:.2f}%)")
            logger.info(f"    Implied (Shin): {shin_implied:.4f} ({shin_implied*100:.2f}%)")
            logger.info(f"    EV: {ev:.4f} ({ev*100:.2f}%)")
            logger.info(f"    Kelly fraction: {kf:.4f} ({kf*100:.2f}%)")
            logger.info(f"    Stake (Kelly*1000): SEK {kf * 1000:.2f}")
            logger.info(f"    Edge vs Shin: {(model_prob - shin_implied)*100:.2f}%")

    return candidates


def web_ui_ev_calculation_trace(raw_probs: list, market: str, odds_row, verbose: bool = True) -> dict:
    """Trace web_ui style EV calculation for comparison."""
    result = {}

    if market == 'h2h':
        home_odds = getattr(odds_row, 'odd_home', None)
        draw_odds = getattr(odds_row, 'odd_draw', None)
        away_odds = getattr(odds_row, 'odd_away', None)

        if verbose:
            logger.info(f"  Web_ui H2H calculation (USING MAX PROB):")
            logger.info(f"    Home odds: {home_odds}, Draw odds: {draw_odds}, Away odds: {away_odds}")

        prob = float(np.max(raw_probs))
        if verbose:
            logger.info(f"    Using max(raw_probs) = {prob:.4f} ({prob*100:.2f}%)")

        ev_home = (prob * home_odds) - 1 if home_odds else None
        ev_draw = (0.33 * draw_odds) - 1 if draw_odds else None
        ev_away = ((1 - prob) * away_odds) - 1 if away_odds else None

        result['method'] = "web_ui (max prob)"
        result['prob_used'] = prob
        result['ev_home'] = ev_home
        result['ev_draw'] = ev_draw
        result['ev_away'] = ev_away
        result['best_ev'] = max(filter(None, [ev_home, ev_draw, ev_away]))
        result['best_ev_percent'] = result['best_ev'] * 100

        if verbose:
            logger.info(f"    EV Home (wrong formula): {ev_home:.4f} ({ev_home*100:.2f}%)" if ev_home else "    EV Home: N/A")
            logger.info(f"    EV Draw (simplified): {ev_draw:.4f} ({ev_draw*100:.2f}%)" if ev_draw else "    EV Draw: N/A")
            logger.info(f"    EV Away (wrong formula): {ev_away:.4f} ({ev_away*100:.2f}%)" if ev_away else "    EV Away: N/A")
            logger.info(f"    Best EV: {result['best_ev']:.4f} ({result['best_ev_percent']:.2f}%)")

        # Correct calculation
        if verbose:
            logger.info(f"  Correct H2H calculation (class-specific probs):")
            logger.info(f"    Raw probs: Home={raw_probs[0]:.4f}, Draw={raw_probs[1]:.4f}, Away={raw_probs[2]:.4f}")

        ev_home_correct = (raw_probs[0] * home_odds) - 1 if home_odds else None
        ev_draw_correct = (raw_probs[1] * draw_odds) - 1 if draw_odds else None
        ev_away_correct = (raw_probs[2] * away_odds) - 1 if away_odds else None

        result['ev_home_correct'] = ev_home_correct
        result['ev_draw_correct'] = ev_draw_correct
        result['ev_away_correct'] = ev_away_correct
        result['best_ev_correct'] = max(filter(None, [ev_home_correct, ev_draw_correct, ev_away_correct]))
        result['best_ev_correct_percent'] = result['best_ev_correct'] * 100

        if verbose:
            logger.info(f"    EV Home (correct): {ev_home_correct:.4f} ({ev_home_correct*100:.2f}%)" if ev_home_correct else "    EV Home: N/A")
            logger.info(f"    EV Draw (correct): {ev_draw_correct:.4f} ({ev_draw_correct*100:.2f}%)" if ev_draw_correct else "    EV Draw: N/A")
            logger.info(f"    EV Away (correct): {ev_away_correct:.4f} ({ev_away_correct*100:.2f}%)" if ev_away_correct else "    EV Away: N/A")
            logger.info(f"    Best EV (correct): {result['best_ev_correct']:.4f} ({result['best_ev_correct_percent']:.2f}%)")

    return result


def main():
    parser = argparse.ArgumentParser(description="Extensive logging for model predictions")
    parser.add_argument("--fixture", type=int, default=1392155, help="Fixture ID (default: 1392155 Deportivo La Coruna vs Mirandes)")
    parser.add_argument("--market", type=str, default="h2h", choices=["h2h", "btts", "ou25", "ou15"], help="Market to trace")
    parser.add_argument("--all-markets", action="store_true", help="Trace all markets")
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("EXTENSIVE MODEL LOGGING")
    logger.info("=" * 70)

    with get_session() as s:
        fixture = s.execute(select(Fixture).where(Fixture.id == args.fixture)).scalar_one_or_none()
        if not fixture:
            logger.error(f"Fixture {args.fixture} not found")
            return

        home_team = s.execute(select(Team).where(Team.id == fixture.home_team_id)).scalar_one_or_none()
        away_team = s.execute(select(Team).where(Team.id == fixture.away_team_id)).scalar_one_or_none()
        league = s.execute(select(League).where(League.id == fixture.league_id)).scalar_one_or_none()

        odds_row = s.execute(select(FixtureOdds).where(FixtureOdds.fixture_id == fixture.id)).scalars().first()

        odds_bookmaker = odds_row.bookmaker if odds_row else None
        odds_odd_home = odds_row.odd_home if odds_row else None
        odds_odd_draw = odds_row.odd_draw if odds_row else None
        odds_odd_away = odds_row.odd_away if odds_row else None
        odds_odd_btts_yes = odds_row.odd_btts_yes if odds_row else None
        odds_odd_btts_no = odds_row.odd_btts_no if odds_row else None
        odds_odd_over = odds_row.odd_over if odds_row else None
        odds_odd_under = odds_row.odd_under if odds_row else None
        odds_odd_over15 = odds_row.odd_over15 if odds_row else None
        odds_odd_under15 = odds_row.odd_under15 if odds_row else None

    logger.info(f"Fixture: {home_name} vs {away_name}")
    logger.info(f"League: {league_name} (ID: {league_id})")
    logger.info(f"Date: {fixture_date}")
    logger.info(f"Status: {fixture_status}")
    logger.info(f"Fixture ID: {fixture_id}")
    logger.info(f"Home team ID: {home_team_id}, Away team ID: {away_team_id}")

    if odds_bookmaker:
        logger.info(f"Odds row found: bookmaker={odds_bookmaker}")
    else:
        logger.warning("No odds found for this fixture")

    markets = [args.market] if not args.all_markets else ["h2h", "btts", "ou25", "ou15"]

    for market in markets:
        logger.info("")
        logger.info("-" * 50)
        logger.info(f"MARKET: {market.upper()}")
        logger.info("-" * 50)

        logger.info(f"\n[1] Getting model prediction...")
        pred_result = get_model_prediction_trace(market, home_team_id, away_team_id)

        if pred_result['error']:
            logger.error(f"  Error: {pred_result['error']}")
            continue

        if not odds_row:
            logger.warning("  No odds available, skipping EV calculation")
            continue

        logger.info(f"\n[2] Calculating EV...")
        ev_candidates = calculate_ev_trace(pred_result['processed_probs'], odds_row, market)

        if ev_candidates:
            logger.info(f"\n[3] Top EV candidates:")
            for c in sorted(ev_candidates, key=lambda x: x['ev'], reverse=True):
                logger.info(f"    {c['outcome']}: EV={c['ev_percent']:.2f}%, Kelly={c['kelly_percent']:.2f}%, Stake=SEK {c['stake_kelly_1000']:.2f}")

        logger.info(f"\n[4] Web_ui style calculation (for comparison)...")
        if market == 'h2h' and pred_result['raw_probs']:
            web_ui_result = web_ui_ev_calculation_trace(pred_result['raw_probs'], market, odds_row)

            logger.info(f"\n[5] DISCREPANCY SUMMARY:")
            logger.info(f"    Web_ui best EV: {web_ui_result.get('best_ev_percent', 0):.2f}%")
            logger.info(f"    Correct best EV: {web_ui_result.get('best_ev_correct_percent', 0):.2f}%")
            diff = web_ui_result.get('best_ev_correct_percent', 0) - web_ui_result.get('best_ev_percent', 0)
            logger.info(f"    Difference: {diff:.2f}%")

    logger.info("")
    logger.info("=" * 70)
    logger.info("LOGGING COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
