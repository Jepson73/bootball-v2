import logging
import numpy as np
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)

MIN_CONFIDENCE_THRESHOLD = 0.30
BASE_EV_THRESHOLD = 0.05
MAX_KELLY_FRACTION = 0.25
MAX_EXPOSURE_PER_LEAGUE = 0.30
MAX_EXPOSURE_PER_MARKET = 0.40
NOISE_BAND_EV = 0.02


@dataclass
class BetDecision:
    """Result of bet eligibility decision."""
    is_valid: bool
    ev: float
    confidence_score: float
    risk_score: float
    required_threshold: float
    final_stake: float
    kelly_fraction: float
    rejection_reason: Optional[str]
    league_exposure: float
    market_exposure: float


def compute_dynamic_threshold(
    model_confidence: float,
    regime_volatility: bool,
    drift_score: float,
    league_stability: float
) -> float:
    """Compute required EV threshold based on uncertainty factors."""
    
    base = BASE_EV_THRESHOLD
    
    conf_penalty = (1 - model_confidence) * 0.05
    
    regime_penalty = 0.03 if regime_volatility else 0.0
    
    drift_penalty = drift_score * 0.10 if drift_score > 0.15 else 0.0
    
    stability_bonus = league_stability * 0.02 if league_stability > 0.7 else 0.0
    
    threshold = base + conf_penalty + regime_penalty + drift_penalty - stability_bonus
    
    return max(0.02, min(0.20, threshold))


def compute_risk_score(
    model_confidence: float,
    drift_score: float,
    regime_volatile: bool,
    league_stability: float
) -> float:
    """Compute overall risk score (0-1)."""
    
    risk = 0.0
    
    risk += (1 - model_confidence) * 0.3
    
    risk += drift_score * 0.3 if drift_score > 0.15 else drift_score * 0.1
    
    risk += 0.2 if regime_volatile else 0.0
    
    risk += (1 - league_stability) * 0.2
    
    return min(1.0, max(0.0, risk))


def compute_adjusted_kelly(
    base_kelly: float,
    confidence: float,
    drift_score: float,
    regime_volatile: bool,
    risk_score: float
) -> float:
    """Adjust Kelly fraction based on uncertainty factors."""
    
    adjusted = base_kelly
    
    adjusted *= (0.5 + confidence * 0.5)
    
    if drift_score > 0.2:
        adjusted *= 0.5
    elif drift_score > 0.15:
        adjusted *= 0.7
    
    if regime_volatile:
        adjusted *= 0.7
    
    adjusted *= (1 - risk_score * 0.3)
    
    return max(0.01, min(MAX_KELLY_FRACTION, adjusted))


def get_portfolio_exposure(league_id: int, market: str) -> Tuple[float, float]:
    """Get current exposure for a league and market combination."""
    
    with get_session() as s:
        league_exposure = 0.0
        market_exposure = 0.0
        
        today = datetime.utcnow().date()
        
        league_exp = s.execute(text("""
            SELECT SUM(stake) / :bankroll as exposure
            FROM placed_bets pb
            JOIN fixtures f ON pb.fixture_id = f.id
            WHERE f.league_id = :league_id
            AND pb.settled = 0
            AND DATE(pb.placed_at) = :today
        """), {"league_id": league_id, "bankroll": 1000.0, "today": today.isoformat()}).scalar()
        
        if league_exp:
            league_exposure = float(league_exp)
        
        market_exp = s.execute(text("""
            SELECT SUM(stake) / :bankroll as exposure
            FROM placed_bets pb
            WHERE pb.market = :market
            AND pb.settled = 0
            AND DATE(pb.placed_at) = :today
        """), {"market": market, "bankroll": 1000.0, "today": today.isoformat()}).scalar()
        
        if market_exp:
            market_exposure = float(market_exp)
        
        return league_exposure, market_exposure


def evaluate_bet_eligibility(
    fixture_id: int,
    league_id: int,
    market: str,
    ev: float,
    odds: float,
    our_prob: float,
    model_confidence: float,
    drift_score: float,
    regime_volatile: bool,
    league_stability: float,
    current_balance: float = 1000.0,
    base_stake: float = 10.0
) -> BetDecision:
    """Evaluate if a bet should be placed with full risk assessment."""
    
    rejection_reason = None
    
    if model_confidence < MIN_CONFIDENCE_THRESHOLD:
        rejection_reason = f"low_model_confidence ({model_confidence:.2f})"
        return BetDecision(
            is_valid=False, ev=ev, confidence_score=model_confidence,
            risk_score=1.0, required_threshold=0.0, final_stake=0.0,
            kelly_fraction=0.0, rejection_reason=rejection_reason,
            league_exposure=0.0, market_exposure=0.0
        )
    
    if ev < NOISE_BAND_EV and ev > 0:
        rejection_reason = "within_noise_band"
        return BetDecision(
            is_valid=False, ev=ev, confidence_score=model_confidence,
            risk_score=1.0, required_threshold=0.0, final_stake=0.0,
            kelly_fraction=0.0, rejection_reason=rejection_reason,
            league_exposure=0.0, market_exposure=0.0
        )
    
    risk_score = compute_risk_score(
        model_confidence, drift_score, regime_volatile, league_stability
    )
    
    required_threshold = compute_dynamic_threshold(
        model_confidence, regime_volatile, drift_score, league_stability
    )
    
    if ev < required_threshold:
        rejection_reason = f"ev_below_threshold ({ev:.3f} < {required_threshold:.3f})"
        return BetDecision(
            is_valid=False, ev=ev, confidence_score=model_confidence,
            risk_score=risk_score, required_threshold=required_threshold,
            final_stake=0.0, kelly_fraction=0.0, rejection_reason=rejection_reason,
            league_exposure=0.0, market_exposure=0.0
        )
    
    league_exp, market_exp = get_portfolio_exposure(league_id, market)
    
    if league_exp >= MAX_EXPOSURE_PER_LEAGUE:
        rejection_reason = f"league_exposure_limit ({league_exp:.2%})"
        return BetDecision(
            is_valid=False, ev=ev, confidence_score=model_confidence,
            risk_score=risk_score, required_threshold=required_threshold,
            final_stake=0.0, kelly_fraction=0.0, rejection_reason=rejection_reason,
            league_exposure=league_exp, market_exposure=market_exp
        )
    
    if market_exp >= MAX_EXPOSURE_PER_MARKET:
        rejection_reason = f"market_exposure_limit ({market_exp:.2%})"
        return BetDecision(
            is_valid=False, ev=ev, confidence_score=model_confidence,
            risk_score=risk_score, required_threshold=required_threshold,
            final_stake=0.0, kelly_fraction=0.0, rejection_reason=rejection_reason,
            league_exposure=league_exp, market_exposure=market_exp
        )
    
    b = odds - 1
    p = our_prob
    q = 1 - p
    raw_kelly = (b * p - q) / b if b > 0 else 0
    
    kelly_fraction = compute_adjusted_kelly(
        raw_kelly, model_confidence, drift_score, regime_volatile, risk_score
    )
    
    stake = kelly_fraction * current_balance
    
    stake = max(1.0, min(stake, base_stake * 2))
    
    return BetDecision(
        is_valid=True, ev=ev, confidence_score=model_confidence,
        risk_score=risk_score, required_threshold=required_threshold,
        final_stake=stake, kelly_fraction=kelly_fraction,
        rejection_reason=None, league_exposure=league_exp,
        market_exposure=market_exp
    )


def create_bet_decision_log_table():
    """Create bet_decision_log table."""
    
    with get_session() as s:
        result = s.execute(text("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='bet_decision_log'
        """)).fetchone()
        
        if not result:
            s.execute(text("""
                CREATE TABLE bet_decision_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    market TEXT NOT NULL,
                    ev REAL,
                    confidence_score REAL,
                    risk_score REAL,
                    required_threshold REAL,
                    decision TEXT NOT NULL,
                    rejection_reason TEXT,
                    final_stake REAL,
                    kelly_fraction REAL,
                    league_exposure REAL,
                    market_exposure REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            s.execute(text("""
                CREATE INDEX idx_bet_decision_fixture_market 
                ON bet_decision_log(fixture_id, market)
            """))
            s.execute(text("""
                CREATE INDEX idx_bet_decision_decision 
                ON bet_decision_log(decision)
            """))
            s.execute(text("""
                CREATE INDEX idx_bet_decision_timestamp 
                ON bet_decision_log(timestamp)
            """))
            s.commit()
            logger.info("Created bet_decision_log table")


def log_bet_decision(
    fixture_id: int,
    market: str,
    decision: BetDecision
) -> None:
    """Log a bet decision to the audit table."""
    
    try:
        with get_session() as s:
            s.execute(text("""
                INSERT INTO bet_decision_log
                (fixture_id, market, ev, confidence_score, risk_score, 
                 required_threshold, decision, rejection_reason, final_stake,
                 kelly_fraction, league_exposure, market_exposure, timestamp)
                VALUES (:fixture_id, :market, :ev, :conf, :risk, :threshold,
                        :decision, :reason, :stake, :kelly, :league_exp, 
                        :market_exp, :timestamp)
            """), {
                "fixture_id": fixture_id,
                "market": market,
                "ev": decision.ev,
                "conf": decision.confidence_score,
                "risk": decision.risk_score,
                "threshold": decision.required_threshold,
                "decision": "bet" if decision.is_valid else "reject",
                "reason": decision.rejection_reason,
                "stake": decision.final_stake,
                "kelly": decision.kelly_fraction,
                "league_exp": decision.league_exposure,
                "market_exp": decision.market_exposure,
                "timestamp": datetime.utcnow().isoformat(),
            })
            s.commit()
    except Exception as e:
        logger.debug(f"Could not log bet decision: {e}")