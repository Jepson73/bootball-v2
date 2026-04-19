"""
src/handlers/settlement_handler.py

Handles settlement of predictions and bets.
Consumes FixtureCompleted events to settle predictions.
"""
from __future__ import annotations

import logging
from typing import Any

from src.events.base import EventEmitter, EventType, get_emitter
from src.events.fixture_events import FixtureCompleted
from src.events.prediction_events import PredictionSettled, emit_prediction_settled
from src.storage.db import get_session
from src.storage.models import PredictionRecord, PlacedBet

logger = logging.getLogger(__name__)


class SettlementHandler:
    """Handles settlement of predictions when fixtures complete.

    Consumes FixtureCompleted events and:
    1. Settles all predictions for the fixture
    2. Calculates P&L for placed bets
    3. Emits PredictionSettled events
    """

    def __init__(self, emitter: EventEmitter | None = None):
        self.emitter = emitter or get_emitter()
        self._register()

    def _register(self) -> None:
        """Register event handlers."""
        self.emitter.subscribe(EventType.FIXTURE_COMPLETED, self.handle_fixture_completed)

    def _unregister(self) -> None:
        """Unregister event handlers."""
        self.emitter.unsubscribe(EventType.FIXTURE_COMPLETED, self.handle_fixture_completed)

    def handle_fixture_completed(self, event: Any) -> None:
        """Handle fixture completion.

        Args:
            event: FixtureCompleted event
        """
        if not isinstance(event, FixtureCompleted):
            return

        fixture_id = event.fixture_id
        home_goals = event.home_goals
        away_goals = event.away_goals
        outcome = event.outcome

        logger.info(f"Settling fixture {fixture_id}: {home_goals}-{away_goals} ({outcome})")

        try:
            self._settle_predictions(fixture_id, home_goals, away_goals, outcome)
            self._settle_bets(fixture_id, home_goals, away_goals, outcome)
        except Exception as e:
            logger.error(f"Settlement error for fixture {fixture_id}: {e}")

    def _settle_predictions(
        self,
        fixture_id: int,
        home_goals: int,
        away_goals: int,
        outcome: str,
    ) -> int:
        """Settle all predictions for a fixture.

        Returns:
            Number of predictions settled
        """
        settled_count = 0

        with get_session() as s:
            predictions = s.execute(
                select(PredictionRecord).where(
                    PredictionRecord.fixture_id == fixture_id,
                    PredictionRecord.settled == False,
                )
            ).scalars().all()

            for pred in predictions:
                won = self._determine_winner(pred, home_goals, away_goals)

                pred.settled = True
                pred.won = won
                pred.actual_outcome = f"{home_goals}-{away_goals}"

                # Calculate P&L if bet was placed
                pnl = None
                if won:
                    # For now, assume even odds - would need to join with placed bets
                    # to get actual odds
                    pnl = 0.0  # Placeholder

                # Emit settlement event
                emit_prediction_settled(
                    fixture_id=fixture_id,
                    market=pred.market,
                    predicted_outcome=pred.predicted_outcome,
                    actual_outcome=pred.actual_outcome,
                    won=won,
                    pnl=pnl,
                )

                settled_count += 1
                logger.info(f"  Settled {pred.market}: {pred.predicted_outcome} -> {'WIN' if won else 'LOSS'}")

            s.commit()

        return settled_count

    def _determine_winner(
        self,
        prediction: PredictionRecord,
        home_goals: int,
        away_goals: int,
    ) -> bool:
        """Determine if prediction was correct.

        Args:
            prediction: PredictionRecord
            home_goals: Home team goals
            away_goals: Away team goals

        Returns:
            True if prediction was correct
        """
        market = prediction.market
        predicted = prediction.predicted_outcome

        if market == "h2h":
            # H = home win, D = draw, A = away win
            if predicted == "H":
                return home_goals > away_goals
            elif predicted == "D":
                return home_goals == away_goals
            elif predicted == "A":
                return home_goals < away_goals

        elif market == "btts":
            both_scored = home_goals > 0 and away_goals > 0
            return (predicted == "Y" and both_scored) or (predicted == "N" and not both_scored)

        elif market in ("ou25", "ou15"):
            total_goals = home_goals + away_goals
            if market == "ou25":
                return (predicted == "Over" and total_goals > 2.5) or (predicted == "Under" and total_goals < 2.5)
            elif market == "ou15":
                return (predicted == "Over" and total_goals > 1.5) or (predicted == "Under" and total_goals < 1.5)

        # Default: didn't win
        return False

    def _settle_bets(
        self,
        fixture_id: int,
        home_goals: int,
        away_goals: int,
        outcome: str,
    ) -> int:
        """Settle all placed bets for a fixture.

        Returns:
            Number of bets settled
        """
        settled_count = 0

        with get_session() as s:
            bets = s.execute(
                select(PlacedBet).where(
                    PlacedBet.fixture_id == fixture_id,
                    PlacedBet.settled == False,
                )
            ).scalars().all()

            for bet in bets:
                # Determine win/loss
                if outcome == "H":
                    won = bet.selection == "H"
                elif outcome == "A":
                    won = bet.selection == "A"
                else:  # Draw
                    won = bet.selection == "D"

                # Calculate P&L
                if won:
                    pnl = bet.stake * (bet.odds - 1)  # Profit = stake * (odds - 1)
                else:
                    pnl = -bet.stake  # Loss = entire stake

                bet.settled = True
                bet.won = won
                bet.pnl = pnl

                from datetime import datetime, timezone
                bet.settled_at = datetime.now(timezone.utc)

                settled_count += 1
                logger.info(f"  Bet settled: {bet.selection} @ {bet.odds} -> {'WIN' if won else 'LOSS'} (PnL: {pnl:.2f})")

            s.commit()

        return settled_count


# Global handler instance
_handler = None


def get_handler() -> SettlementHandler:
    """Get the global settlement handler."""
    global _handler
    if _handler is None:
        _handler = SettlementHandler()
    return _handler


def handle_fixture_completed(event: FixtureCompleted) -> None:
    """Convenience function to handle fixture completion."""
    get_handler().handle_fixture_completed(event)
