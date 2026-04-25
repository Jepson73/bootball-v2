#!/usr/bin/env python3
"""
scripts/retrain_models_new.py

Retrain all market models using market-type specific feature pipelines.
Each market gets a dedicated model aligned with its model family.
"""
import sys
sys.path.insert(0, '/opt/projects/bootball')

import logging
import pickle
import numpy as np
from datetime import datetime
from pathlib import Path
from sqlalchemy import text

from src.storage.db import get_session, init_db
from src.storage.models import Fixture, FixtureOdds, Standing, PredictionRecord, ModelVersion
from src.betting.prediction import build_features_h2h, build_features_btts, build_features_ou
from src.models.calibrator import MarketCalibrator, get_calibration_cache

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = Path('/opt/projects/bootball/data')
MODEL_DIR = DATA_DIR

MIN_TRAINING_SAMPLES = 50
MIN_FEATURE_VARIANCE = 10.0


def compute_brier(probs, outcomes):
    return np.mean((np.array(probs) - np.array(outcomes)) ** 2)


def compute_ece(probs, outcomes, n_bins=10):
    buckets = {}
    for p, o in zip(probs, outcomes):
        bucket = int(p * n_bins) / n_bins
        if bucket not in buckets:
            buckets[bucket] = {'total': 0, 'wins': 0}
        buckets[bucket]['total'] += 1
        buckets[bucket]['wins'] += o
    
    total = sum(b['total'] for b in buckets.values())
    ece = 0
    for bucket, data in buckets.items():
        predicted = bucket + (0.5 / n_bins)
        actual = data['wins'] / data['total'] if data['total'] > 0 else 0
        ece += (data['total'] / total) * abs(predicted - actual)
    return ece


def build_training_data_h2h(session):
    """Build H2H training data using categorical features."""
    logger.info("Building H2H training data...")
    
    results = session.execute(text("""
        SELECT 
            f.id, f.home_team_id, f.away_team_id,
            f.goals_home, f.goals_away, f.outcome
        FROM fixtures f
        WHERE f.status = 'FT'
        AND f.goals_home IS NOT NULL
        AND f.goals_away IS NOT NULL
        AND f.outcome IS NOT NULL
        ORDER BY f.date DESC
        LIMIT 500
    """)).fetchall()
    
    X, y = [], []
    
    for r in results:
        fixture_id, home_id, away_id, goals_home, goals_away, outcome = r
        
        home_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": home_id}).fetchone()
        
        away_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": away_id}).fetchone()
        
        if not home_standing or not away_standing:
            continue
        
        class MockStanding:
            def __init__(self, row):
                self.rank = row[0]
                self.goals_for = row[1]
                self.goals_against = row[2]
        
        features = build_features_h2h(MockStanding(home_standing), MockStanding(away_standing))
        
        if outcome == 'H':
            label = 0
        elif outcome == 'D':
            label = 1
        else:
            label = 2
        
        X.append(features[0])
        y.append(label)
    
    X = np.array(X)
    y = np.array(y)
    
    logger.info(f"  H2H: {len(X)} samples, {X.shape[1]} features")
    logger.info(f"  Feature variance: {np.var(X, axis=0).mean():.2f}")
    logger.info(f"  Labels: H={sum(y==0)}, D={sum(y==1)}, A={sum(y==2)}")
    
    return X, y


def build_training_data_btts(session):
    """Build BTTS training data using joint event features."""
    logger.info("Building BTTS training data...")
    
    results = session.execute(text("""
        SELECT 
            f.id, f.home_team_id, f.away_team_id,
            f.goals_home, f.goals_away
        FROM fixtures f
        WHERE f.status = 'FT'
        AND f.goals_home IS NOT NULL
        AND f.goals_away IS NOT NULL
        ORDER BY f.date DESC
        LIMIT 500
    """)).fetchall()
    
    X, y = [], []
    
    for r in results:
        fixture_id, home_id, away_id, goals_home, goals_away = r
        
        home_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": home_id}).fetchone()
        
        away_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": away_id}).fetchone()
        
        if not home_standing or not away_standing:
            continue
        
        class MockStanding:
            def __init__(self, row):
                self.rank = row[0]
                self.goals_for = row[1]
                self.goals_against = row[2]
        
        features = build_features_btts(MockStanding(home_standing), MockStanding(away_standing))
        
        btts_yes = 1 if goals_home > 0 and goals_away > 0 else 0
        
        X.append(features[0])
        y.append(btts_yes)
    
    X = np.array(X)
    y = np.array(y)
    
    var = np.var(X, axis=0).mean()
    logger.info(f"  BTTS: {len(X)} samples, {X.shape[1]} features")
    logger.info(f"  Feature variance: {var:.2f}")
    logger.info(f"  Labels: Yes={sum(y==1)}, No={sum(y==0)}")
    
    if var < MIN_FEATURE_VARIANCE:
        logger.warning(f"  BTTS feature variance too low: {var:.2f} < {MIN_FEATURE_VARIANCE}")
    
    return X, y


def build_training_data_ou(session, threshold: float = 2.5):
    """Build OU training data using goal distribution features."""
    logger.info(f"Building OU{threshold} training data...")
    
    results = session.execute(text("""
        SELECT 
            f.id, f.home_team_id, f.away_team_id,
            f.goals_home, f.goals_away
        FROM fixtures f
        WHERE f.status = 'FT'
        AND f.goals_home IS NOT NULL
        AND f.goals_away IS NOT NULL
        ORDER BY f.date DESC
        LIMIT 500
    """)).fetchall()
    
    X, y = [], []
    
    for r in results:
        fixture_id, home_id, away_id, goals_home, goals_away = r
        
        home_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": home_id}).fetchone()
        
        away_standing = session.execute(text("""
            SELECT rank, goals_for, goals_against 
            FROM standings 
            WHERE team_id = :tid AND season >= 2024 
            LIMIT 1
        """), {"tid": away_id}).fetchone()
        
        if not home_standing or not away_standing:
            continue
        
        class MockStanding:
            def __init__(self, row):
                self.rank = row[0]
                self.goals_for = row[1]
                self.goals_against = row[2]
        
        features = build_features_ou(MockStanding(home_standing), MockStanding(away_standing), threshold)
        
        total_goals = goals_home + goals_away
        over = 1 if total_goals > threshold else 0
        
        X.append(features[0])
        y.append(over)
    
    X = np.array(X)
    y = np.array(y)
    
    var = np.var(X, axis=0).mean()
    logger.info(f"  OU{threshold}: {len(X)} samples, {X.shape[1]} features")
    logger.info(f"  Feature variance: {var:.2f}")
    logger.info(f"  Labels: Over={sum(y==1)}, Under={sum(y==0)}")
    
    return X, y


def train_model(X, y, model_family: str):
    """Train a model based on model family."""
    from lightgbm import LGBMClassifier
    
    if model_family == "classification":
        model = LGBMClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            num_leaves=31,
            random_state=42,
            verbose=-1,
        )
        model.fit(X, y)
        return model
    
    elif model_family == "binary_coupling" or model_family == "poisson_count":
        model = LGBMClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            num_leaves=20,
            random_state=42,
            verbose=-1,
        )
        model.fit(X, y)
        return model
    
    else:
        raise ValueError(f"Unknown model family: {model_family}")


def train_and_save_model(market: str, X, y, model_family: str):
    """Train model with calibration and save to disk."""
    logger.info(f"Training {market} model ({model_family})...")
    
    if len(X) < MIN_TRAINING_SAMPLES:
        logger.error(f"  Not enough samples: {len(X)} < {MIN_TRAINING_SAMPLES}")
        return None
    
    model = train_model(X, y, model_family)
    
    if model_family in ("binary_coupling", "poisson_count"):
        y_binary = y
        raw_probs = model.predict_proba(X)[:, 1]
    else:
        y_binary = y
        raw_probs = model.predict_proba(X)
        if raw_probs.shape[1] == 3:
            raw_probs = raw_probs[:, 2]
        else:
            raw_probs = raw_probs[:, 1]
    
    calibrator = MarketCalibrator(market)
    calibrator.fit(raw_probs, y_binary)
    
    if not calibrator.isotonic:
        logger.warning(f"  {market}: Calibration failed, using raw model")
        calibrator = None
    
    output_path = MODEL_DIR / f"model_{market}.pkl"
    
    with open(output_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'calibrator': calibrator,
            'market': market,
            'version': 1,
            'trained_at': datetime.utcnow().isoformat(),
            'model_family': model_family,
            'features_used': list(range(X.shape[1])),
            'n_samples': len(X),
        }, f)
    
    logger.info(f"  Saved to {output_path}")
    
    if calibrator:
        logger.info(f"  Calibrator: Brier={calibrator.brier_score:.4f}, ECE={calibrator.ece:.4f}")
    
    return model, calibrator


def update_model_version(market: str, brier_score: float, accuracy: float, ece: float):
    """Update model_versions table."""
    with get_session() as s:
        existing = s.execute(
            text("SELECT MAX(version_number) FROM model_versions WHERE market = :market"),
            {"market": market}
        ).scalar()
        
        new_version = (existing or 0) + 1
        
        s.execute(text("UPDATE model_versions SET is_active = 0 WHERE market = :market"),
                 {"market": market})
        
        s.execute(text("""
            INSERT INTO model_versions 
            (market, version_number, brier_score, accuracy, sample_size, ece, calibration_sample_size,
             model_type, features_used, is_active, trained_at, created_at)
            VALUES (:market, :version, :brier, :acc, :n_samples, :ece, :n_samples, :family, :features, 1, :trained, :trained)
        """), {
            "market": market,
            "version": new_version,
            "brier": brier_score,
            "acc": accuracy,
            "ece": ece,
            "features": "new_market_type_features",
            "family": "lightgbm",
            "trained": datetime.utcnow().isoformat(),
            "n_samples": 500
        })
        s.commit()
    
    logger.info(f"  Updated model_versions for {market}")


def main():
    logger.info("=" * 60)
    logger.info("RETRAINING MODELS WITH MARKET-TYPE FEATURES")
    logger.info("=" * 60)
    
    init_db()
    
    with get_session() as session:
        results = {}
        
        logger.info("\n--- Training H2H ---")
        X_h2h, y_h2h = build_training_data_h2h(session)
        if len(X_h2h) >= MIN_TRAINING_SAMPLES:
            model, calib = train_and_save_model("h2h", X_h2h, y_h2h, "classification")
            
            if calib:
                probs = model.predict_proba(X_h2h)
                probs = probs[:, 2] if probs.shape[1] == 3 else probs[:, 1]
                preds = np.array([calib.isotonic.predict([p])[0] for p in probs])
                brier = compute_brier(preds, y_h2h)
                ece = compute_ece(preds, y_h2h)
                acc = np.mean((preds > 0.5) == y_h2h)
            else:
                probs = model.predict_proba(X_h2h)[:, 2] if model.predict_proba(X_h2h).shape[1] == 3 else model.predict_proba(X_h2h)[:, 1]
                brier = compute_brier(probs, y_h2h)
                ece = compute_ece(probs, y_h2h)
                acc = np.mean((probs > 0.5) == y_h2h)
            
            update_model_version("h2h", brier, acc, ece)
            results["h2h"] = {"brier": brier, "ece": ece, "accuracy": acc}
        
        logger.info("\n--- Training BTTS ---")
        X_btts, y_btts = build_training_data_btts(session)
        if len(X_btts) >= MIN_TRAINING_SAMPLES:
            model, calib = train_and_save_model("btts", X_btts, y_btts, "binary_coupling")
            
            if calib:
                probs = model.predict_proba(X_btts)[:, 1]
                preds = np.array([calib.isotonic.predict([p])[0] for p in probs])
                brier = compute_brier(preds, y_btts)
                ece = compute_ece(preds, y_btts)
                acc = np.mean((preds > 0.5) == y_btts)
            else:
                probs = model.predict_proba(X_btts)[:, 1]
                brier = compute_brier(probs, y_btts)
                ece = compute_ece(probs, y_btts)
                acc = np.mean((probs > 0.5) == y_btts)
            
            update_model_version("btts", brier, acc, ece)
            results["btts"] = {"brier": brier, "ece": ece, "accuracy": acc}
        
        logger.info("\n--- Training OU25 ---")
        X_ou25, y_ou25 = build_training_data_ou(session, 2.5)
        if len(X_ou25) >= MIN_TRAINING_SAMPLES:
            model, calib = train_and_save_model("ou25", X_ou25, y_ou25, "poisson_count")
            
            if calib:
                probs = model.predict_proba(X_ou25)[:, 1]
                preds = np.array([calib.isotonic.predict([p])[0] for p in probs])
                brier = compute_brier(preds, y_ou25)
                ece = compute_ece(preds, y_ou25)
                acc = np.mean((preds > 0.5) == y_ou25)
            else:
                probs = model.predict_proba(X_ou25)[:, 1]
                brier = compute_brier(probs, y_ou25)
                ece = compute_ece(probs, y_ou25)
                acc = np.mean((probs > 0.5) == y_ou25)
            
            update_model_version("ou25", brier, acc, ece)
            results["ou25"] = {"brier": brier, "ece": ece, "accuracy": acc}
        
        logger.info("\n--- Training OU15 ---")
        X_ou15, y_ou15 = build_training_data_ou(session, 1.5)
        if len(X_ou15) >= MIN_TRAINING_SAMPLES:
            model, calib = train_and_save_model("ou15", X_ou15, y_ou15, "poisson_count")
            
            if calib:
                probs = model.predict_proba(X_ou15)[:, 1]
                preds = np.array([calib.isotonic.predict([p])[0] for p in probs])
                brier = compute_brier(preds, y_ou15)
                ece = compute_ece(preds, y_ou15)
                acc = np.mean((preds > 0.5) == y_ou15)
            else:
                probs = model.predict_proba(X_ou15)[:, 1]
                brier = compute_brier(probs, y_ou15)
                ece = compute_ece(probs, y_ou15)
                acc = np.mean((probs > 0.5) == y_ou15)
            
            update_model_version("ou15", brier, acc, ece)
            results["ou15"] = {"brier": brier, "ece": ece, "accuracy": acc}
    
    logger.info("\n" + "=" * 60)
    logger.info("TRAINING COMPLETE")
    logger.info("=" * 60)
    
    for market, metrics in results.items():
        logger.info(f"  {market}: Brier={metrics['brier']:.4f}, ECE={metrics['ece']:.4f}, Acc={metrics['accuracy']:.2%}")
    
    logger.info("\nModels saved to data/model_*.pkl")
    logger.info("Run backfill to update predictions with new models")


if __name__ == "__main__":
    main()