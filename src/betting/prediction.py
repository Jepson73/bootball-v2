import logging
import os
import pickle
import sys
import warnings

import numpy as np
from sqlalchemy import select

sys.path.insert(0, '/opt/projects/bootball')

from src.storage.db import get_session
from src.storage.models import Standing

logger = logging.getLogger(__name__)

MODEL_PATH = '/opt/projects/bootball/data/model_{market}.pkl'

MARKET_OUTCOMES = {
    "h2h": ["1", "X", "2"],
    "btts": ["Yes", "No"],
    "ou25": ["Over", "Under"],
    "ou15": ["Over", "Under"],
}


def get_model_prediction(market: str, home_team_id: int, away_team_id: int) -> dict[str, float] | None:
    """Get prediction from trained LightGBM model.

    Returns dict of outcome -> probability, or None if model unavailable.
    """
    model_path = MODEL_PATH.format(market=market)
    if not os.path.exists(model_path):
        logger.warning(f"Model not found: {model_path}")
        return None

    try:
        with open(model_path, 'rb') as f:
            obj = pickle.load(f)

        if isinstance(obj, dict):
            model = obj['model']
            calibrator = obj.get('calibrator')
        else:
            model = obj
            calibrator = None

        with get_session() as s:
            home_standing = s.execute(
                select(Standing).where(Standing.team_id == home_team_id).where(Standing.season >= 2024)
            ).first()
            away_standing = s.execute(
                select(Standing).where(Standing.team_id == away_team_id).where(Standing.season >= 2024)
            ).first()

            if not home_standing or not away_standing:
                return None

            hs = home_standing[0]
            as_ = away_standing[0]

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

        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='X does not have valid feature names')
            raw_probs = model.predict_proba(features)[0]

        outcomes = MARKET_OUTCOMES.get(market, [])
        if len(outcomes) == 2:
            probs = {outcomes[0]: float(raw_probs[1]), outcomes[1]: float(1 - raw_probs[1])}
        elif len(raw_probs) == 3:
            probs = {outcomes[i]: float(raw_probs[i]) for i in range(3)}
        else:
            return None

        if calibrator:
            try:
                for k in probs:
                    probs[k] = max(0.01, min(0.99, calibrator.predict([probs[k]])[0]))
            except Exception:
                pass

        return probs

    except Exception as e:
        logger.warning(f"Model prediction error for {market}: {e}")
        return None