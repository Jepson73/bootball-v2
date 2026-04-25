import logging
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from src.events.consumers.base import EventConsumer

logger = logging.getLogger(__name__)


class BettingDashboardConsumer(EventConsumer):
    """
    Consumer that maintains betting dashboard state projection.
    
    Listens to:
    - bets_generated
    - bets_settled
    - run_finished
    
    Responsibilities:
    - Maintain pending bets
    - Track ROI, wins/losses, stake totals
    - Update betting state file for dashboard
    """

    def __init__(self):
        self.state_file = "/opt/projects/bootball/data/betting_state.json"
        self._load_state()

    def handles(self, event_type: str) -> bool:
        return event_type in ["bets_generated", "bets_settled", "bet_settled", "run_finished"]

    def process(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == "bets_generated":
            self._handle_bets_generated(payload)
        elif event_type in ["bets_settled", "bet_settled"]:
            self._handle_bets_settled(payload)

    def _load_state(self) -> dict:
        """Load current state from file."""
        import os
        if os.path.exists(self.state_file):
            try:
                import json
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "pending_bets": [],
            "settled_bets": [],
            "total_pnl": 0,
            "wins": 0,
            "losses": 0,
            "last_updated": None
        }

    def _save_state(self, state: dict) -> None:
        """Save state to file."""
        import os
        import json
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _handle_bets_generated(self, payload: dict[str, Any]) -> None:
        """Handle bets_generated event - add to pending."""
        bets = payload.get("bets", [])
        if not bets:
            return

        state = self._load_state()
        
        for bet in bets:
            state["pending_bets"].append({
                "fixture_id": bet.get("fixture_id"),
                "market": bet.get("market"),
                "outcome": bet.get("outcome"),
                "odds": bet.get("odds"),
                "ev": bet.get("ev"),
                "stake": bet.get("stake"),
                "timestamp": bet.get("timestamp", datetime.now(ZoneInfo("UTC")).isoformat())
            })

        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        self._save_state(state)
        
        logger.info(f"BettingDashboardConsumer: added {len(bets)} pending bets")

    def _handle_bets_settled(self, payload: dict[str, Any]) -> None:
        """Handle bets_settled event - move from pending to settled."""
        settled_count = payload.get("settled_count", 0)
        pnl_total = payload.get("pnl_total", 0)
        wins = payload.get("wins", 0)
        losses = payload.get("losses", 0)

        state = self._load_state()
        
        # Move pending to settled (simplified - mark all pending as settled)
        settled_bets = state.pop("pending_bets", [])
        
        for bet in settled_bets:
            bet["settled"] = True
            bet["won"] = pnl_total > 0
            bet["pnl"] = pnl_total / len(settled_bets) if settled_bets else 0
        
        state["settled_bets"].extend(settled_bets)
        state["total_pnl"] = state.get("total_pnl", 0) + pnl_total
        state["wins"] = state.get("wins", 0) + wins
        state["losses"] = state.get("losses", 0) + losses
        state["last_updated"] = datetime.now(ZoneInfo("UTC")).isoformat()
        
        self._save_state(state)
        
        logger.info(f"BettingDashboardConsumer: settled {len(settled_bets)} bets, PnL: {pnl_total:.2f}")