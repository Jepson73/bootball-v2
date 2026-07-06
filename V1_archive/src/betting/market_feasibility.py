import logging
from typing import Optional
from sqlalchemy import text
from src.storage.db import get_session

logger = logging.getLogger(__name__)

MARKET_STATUS_LEARNABLE = "learnable"
MARKET_STATUS_UNDERFIT = "underfit"
MARKET_STATUS_NON_EXPRESSIBLE = "non-expressible"


def get_market_status(market: str) -> Optional[str]:
    """Get current status of a market from database.
    
    Returns: 'learnable', 'underfit', 'non-expressible', or None if not evaluated.
    """
    with get_session() as s:
        result = s.execute(
            text("SELECT status FROM market_feasibility WHERE market = :market"),
            {"market": market}
        ).fetchone()
        return result[0] if result else None


def is_market_bettable(market: str) -> bool:
    """Check if a market can be used for betting decisions.
    
    Only markets with status 'learnable' should generate actual bets.
    Underfit markets generate predictions but no bets.
    Non-expressible markets are flagged for model redesign.
    """
    status = get_market_status(market)
    if status is None:
        logger.warning(f"Market {market} not evaluated - treating as non-bettable")
        return False
    return status == MARKET_STATUS_LEARNABLE


def get_market_feasibility(market: str) -> Optional[dict]:
    """Get full feasibility metrics for a market."""
    with get_session() as s:
        result = s.execute(
            text("""
                SELECT market, feasibility_score, status, variance, entropy,
                       ev_roi_correlation, brier_raw, brier_cal, ece_raw, ece_cal,
                       prediction_count, settled_count, last_updated
                FROM market_feasibility WHERE market = :market
            """),
            {"market": market}
        ).fetchone()
        
        if not result:
            return None
        
        return {
            "market": result[0],
            "feasibility_score": result[1],
            "status": result[2],
            "variance": result[3],
            "entropy": result[4],
            "ev_roi_correlation": result[5],
            "brier_raw": result[6],
            "brier_cal": result[7],
            "ece_raw": result[8],
            "ece_cal": result[9],
            "prediction_count": result[10],
            "settled_count": result[11],
            "last_updated": result[12],
        }


def get_bettable_markets() -> list[str]:
    """Get list of markets currently eligible for betting."""
    with get_session() as s:
        results = s.execute(
            text("SELECT market FROM market_feasibility WHERE status = 'learnable'")
        ).fetchall()
        return [r[0] for r in results]


def get_all_market_statuses() -> dict[str, str]:
    """Get status for all markets."""
    with get_session() as s:
        results = s.execute(
            text("SELECT market, status FROM market_feasibility")
        ).fetchall()
        return {r[0]: r[1] for r in results}


def should_place_bet(market: str) -> bool:
    """Check if bets should be placed for a given market.
    
    Convenience function combining status check with logging.
    """
    status = get_market_status(market)
    
    if status is None:
        logger.warning(f"Market {market} feasibility unknown - skipping bet")
        return False
    
    if status == MARKET_STATUS_LEARNABLE:
        return True
    
    if status == MARKET_STATUS_UNDERFIT:
        logger.info(f"Market {market} is UNDERFIT - generating predictions but no bets")
        return False
    
    if status == MARKET_STATUS_NON_EXPRESSIBLE:
        logger.warning(f"Market {market} is NON-EXPRESSIBLE - model redesign needed, no bets")
        return False
    
    return False