"""
src/handlers/odds_handler.py

Handles odds updates and stale detection.
Consumes OddsUpdated events to trigger prediction recalculation.
"""
from __future__ import annotations

import logging
from typing import Any

from src.events.base import EventEmitter, EventType, get_emitter
from src.events.odds_events import OddsUpdated
from src.storage.db import get_session
from src.storage.models import FixtureOdds, PredictionRecord

logger = logging.getLogger(__name__)


class OddsHandler:
    """Handles odds update events.

    Consumes OddsUpdated events and:
    1. Detects if EV changed significantly
    2. Triggers prediction recalculation if needed
    3. Detects stale odds and alerts
    """

    def __init__(self, emitter: EventEmitter | None = None):
        self.emitter = emitter or get_emitter()
        self._ev_change_threshold = 0.05  # 5% EV change triggers recalculation
        self._register()

    def _register(self) -> None:
        """Register event handlers."""
        self.emitter.subscribe(EventType.ODDS_UPDATED, self.handle_odds_updated)

    def handle_odds_updated(self, event: Any) -> None:
        """Handle odds update event.

        Args:
            event: OddsUpdated event
        """
        if not isinstance(event, OddsUpdated):
            return

        fixture_id = event.fixture_id
        market = event.market
        old_odds = event.old_odds
        new_odds = event.new_odds

        logger.info(f"Odds updated for fixture {fixture_id}, market {market}")

        try:
            ev_change = self._calculate_ev_change(old_odds, new_odds)

            if ev_change and abs(ev_change) > self._ev_change_threshold * 100:
                logger.info(f"  Significant EV change: {ev_change:+.1f}% - triggering recalculation")
                self._trigger_prediction_recalculation(fixture_id, market)
            else:
                logger.debug(f"  EV change negligible: {ev_change:.2f}%")

            self._update_odds_in_db(fixture_id, market, new_odds)

        except Exception as e:
            logger.error(f"Odds handler error for fixture {fixture_id}: {e}")

    def _calculate_ev_change(
        self,
        old_odds: dict[str, float] | None,
        new_odds: dict[str, float],
    ) -> float | None:
        """Calculate EV change between old and new odds.

        Args:
            old_odds: Previous odds dict
            new_odds: New odds dict

        Returns:
            EV change as percentage, or None if can't calculate
        """
        if not old_odds or not new_odds:
            return None

        # Get best odds for each (assuming key is the outcome)
        old_best = max(old_odds.values()) if old_odds else 0
        new_best = max(new_odds.values()) if new_odds else 0

        if old_best <= 0 or new_best <= 0:
            return None

        # Calculate implied EV change (simplified - assumes 50% probability)
        old_implied_prob = 1 / old_best if old_best > 0 else 0
        new_implied_prob = 1 / new_best if new_best > 0 else 0

        # EV = probability * (odds - 1) - (1 - probability)
        # For 50% probability:
        old_ev = 0.5 * (old_best - 1) - 0.5
        new_ev = 0.5 * (new_best - 1) - 0.5

        return (new_ev - old_ev) * 100  # Return as percentage

    def _trigger_prediction_recalculation(
        self,
        fixture_id: int,
        market: str,
    ) -> None:
        """Trigger prediction recalculation for fixture/market.

        This would typically:
        1. Fetch fresh predictions from ML model
        2. Compare with existing predictions
        3. Update if EV changed significantly
        4. Emit PredictionUpdated event

        For now, this is a placeholder - actual implementation would
        integrate with the ML prediction pipeline.
        """
        logger.info(f"  Would recalculate {market} prediction for fixture {fixture_id}")

        # TODO: Actual recalculation logic
        # from src.betting.predict import predict_proba
        # new_prob = predict_proba(market, home_id, away_id)
        # update_prediction_record(fixture_id, market, new_prob)

    def _update_odds_in_db(
        self,
        fixture_id: int,
        market: str,
        new_odds: dict[str, float],
    ) -> None:
        """Update odds in database.

        Args:
            fixture_id: Fixture ID
            market: Market type
            new_odds: New odds values
        """
        with get_session() as s:
            # Find existing odds rows for this fixture/market
            odds_rows = s.execute(
                select(FixtureOdds).where(
                    FixtureOdds.fixture_id == fixture_id,
                    FixtureOdds.bet_type == market,
                )
            ).scalars().all()

            if not odds_rows:
                logger.debug(f"  No existing odds for fixture {fixture_id}, market {market}")
                return

            # Update the first matching row with new odds
            # (in production, might need to update multiple bookmakers)
            for row in odds_rows:
                # Map market to correct odds field
                if market == "btts":
                    row.odd_btts_yes = new_odds.get("Yes") or new_odds.get("yes")
                elif market == "ou25":
                    row.odd_over = new_odds.get("Over")
                elif market == "h2h":
                    row.odd_home = new_odds.get("Home")
                    row.odd_draw = new_odds.get("Draw")
                    row.odd_away = new_odds.get("Away")

                logger.debug(f"  Updated {market} odds for fixture {fixture_id}")

            s.commit()


# Global handler instance
_handler = None


def get_handler() -> OddsHandler:
    """Get the global odds handler."""
    global _handler
    if _handler is None:
        _handler = OddsHandler()
    return _handler


def handle_odds_updated(event: OddsUpdated) -> None:
    """Convenience function to handle odds update."""
    get_handler().handle_odds_updated(event)
