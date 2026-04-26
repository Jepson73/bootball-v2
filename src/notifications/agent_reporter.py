"""
Agent Reporter - generates Discord reports for multi-agent runs.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.agents.shared.state_store import get_state_store

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("/opt/projects/bootball/reports")


class AgentReporter:
    """
    Generates reports for multi-agent runs.
    
    Outputs:
    - Discord notification (if configured)
    - reports/report.md (run-specific)
    - reports/latest_report.md (always overwritten)
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self._run_data: dict = {}
    
    def start_run(self) -> None:
        """Initialize run data."""
        self._run_data = {
            "started_at": datetime.utcnow().isoformat(),
            "predictions": {},
            "risk": {},
            "execution": {},
        }
    
    def record_predictions(self, count: int, avg_ev: float) -> None:
        """Record predictions data."""
        self._run_data["predictions"] = {
            "count": count,
            "avg_ev": avg_ev,
        }
    
    def record_risk(self, regime: str, lambda_val: float, drawdown: float) -> None:
        """Record risk data."""
        self._run_data["risk"] = {
            "regime": regime.upper(),
            "lambda": lambda_val,
            "drawdown": drawdown,
        }
    
    def record_execution(self, bets: int, stake: float, expected_return: float, risk: float) -> None:
        """Record execution data."""
        self._run_data["execution"] = {
            "bets_placed": bets,
            "total_stake": stake,
            "expected_return": expected_return,
            "risk": risk,
        }
    
    def record_adversarial(
        self,
        risk_score: float,
        max_drawdown: float,
        recommendation: str,
        vulnerabilities: int
    ) -> None:
        """Record adversarial analysis results."""
        self._run_data["adversary"] = {
            "risk_score": risk_score,
            "max_drawdown": max_drawdown,
            "recommendation": recommendation.upper(),
            "vulnerabilities": vulnerabilities,
        }
    
    def generate_report(self) -> str:
        """Generate markdown report."""
        preds = self._run_data.get("predictions", {})
        risk = self._run_data.get("risk", {})
        exec_data = self._run_data.get("execution", {})
        adv = self._run_data.get("adversary", {})
        
        report = f"""# Multi-Agent Run Report

## Run Info
- **Started**: {self._run_data.get('started_at', 'N/A')}
- **Completed**: {datetime.utcnow().isoformat()}

## Predictor Output
- **Signals Generated**: {preds.get('count', 0)}
- **Average EV**: {preds.get('avg_ev', 0):.1%}

## Risk Profile
- **Regime**: {risk.get('regime', 'N/A')}
- **Lambda (λ)**: {risk.get('lambda', 0):.2f}
- **Drawdown**: {risk.get('drawdown', 0):.2%}

## Portfolio Construction
- **Bets Placed**: {exec_data.get('bets_placed', 0)}
- **Total Stake**: {exec_data.get('total_stake', 0):.2f} SEK
- **Expected Return**: {exec_data.get('expected_return', 0):.2%}
- **Risk**: {exec_data.get('risk', 0):.2%}

## Adversarial Stress Test
- **Risk Score**: {adv.get('risk_score', 0):.2f}
- **Worst-case Drawdown**: {adv.get('max_drawdown', 0):.2%}
- **Decision**: {adv.get('recommendation', 'N/A')}
- **Vulnerabilities**: {adv.get('vulnerabilities', 0)}

## Execution Summary
- **Bankroll**: {self.state_store.get_current_bankroll():.2f} SEK
- **Bets This Run**: {self.state_store.get_bets_placed()}

## Events Trace
- PREDICTIONS_READY → RISK_PROFILE_UPDATED → PORTFOLIO_ALLOCATED → PORTFOLIO_STRESSED → EXECUTION_REQUESTED
"""
        
        return report
    
    def save_reports(self) -> None:
        """Save reports to files."""
        REPORTS_DIR.mkdir(exist_ok=True)
        
        report = self.generate_report()
        
        # Save latest
        (REPORTS_DIR / "latest_report.md").write_text(report)
        
        # Save with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        (REPORTS_DIR / f"report_{timestamp}.md").write_text(report)
        
        logger.info(f"[REPORTER] Reports saved")
    
    def get_discord_message(self) -> str:
        """Get Discord-formatted message."""
        preds = self._run_data.get("predictions", {})
        risk = self._run_data.get("risk", {})
        exec_data = self._run_data.get("execution", {})
        adv = self._run_data.get("adversary", {})
        
        # Determine emoji based on recommendation
        rec = adv.get("recommendation", "ACCEPT")
        emoji = "✅" if rec == "ACCEPT" else "⚠️" if rec == "ADJUST" else "🛑"
        
        return f"""🤖 **MULTI-AGENT RUN REPORT**

**Predictor:**
- signals: {preds.get('count', 0)}
- avg EV: {preds.get('avg_ev', 0):.1%}

**Risk Manager:**
- regime: {risk.get('regime', 'N/A')}
- λ: {risk.get('lambda', 0):.2f}
- drawdown: {risk.get('drawdown', 0):.1%}

**Adversarial:**
- risk score: {adv.get('risk_score', 0):.2f}
- worst-case DD: {adv.get('max_drawdown', 0):.1%}
- decision: {rec} {emoji}

**Execution:**
- bets placed: {exec_data.get('bets_placed', 0)}
- exposure: {exec_data.get('risk', 0):.1%}
- expected return: {exec_data.get('expected_return', 0):.1%}

**Bankroll:** {self.state_store.get_current_bankroll():.0f} SEK"""


# Global instance
_reporter: Optional[AgentReporter] = None


def get_agent_reporter() -> AgentReporter:
    """Get global agent reporter."""
    global _reporter
    if _reporter is None:
        _reporter = AgentReporter()
    return _reporter
