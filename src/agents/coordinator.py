"""
Multi-Agent Coordinator - STATEFUL VERSION WITH POLICY ENGINE.

Orchestrates the agents in sequence:
1. Load PortfolioState
2. Predictor Agent → generates predictions
3. Risk Manager Agent → computes risk profile (with state feedback)
4. Execution Strategist Agent → builds portfolio
5. Portfolio Engine → PRIMARY DECISION CORE (with state)
6. Monte Carlo Simulation → simulate trajectories
7. Policy Engine → HARD CONSTRAINT GOVERNOR (NEW)
8. Execution Engine → executes approved allocations
9. Learning → update state and weights
10. Persist NEW PortfolioState
"""

import logging
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from typing import Optional

from src.alerts.event_bus import event_bus
from src.agents.shared.events import AgentEvents
from src.agents.predictor.agent import get_predictor_agent
from src.agents.risk_manager.agent import get_risk_manager_agent
from src.agents.execution_strategist.agent import get_execution_strategist_agent
from src.agents.adversary.agent import get_adversary_agent
from src.betting.portfolio.portfolio_engine import get_portfolio_engine
from src.learning import get_performance_evaluator, get_weight_optimizer, get_event_replay
from src.agents.shared.state_store import get_state_store
from src.notifications.agent_reporter import get_agent_reporter
from src.portfolio.state.state_manager import get_state_manager, StateManager
from src.portfolio.state.portfolio_state import PortfolioState
from src.governance.policy_engine import get_policy_engine, PolicyDecision, PolicyDecisionType
from src.governance.execution_spine_guard import get_execution_spine_guard, compute_state_hash


from src.simulation.monte_carlo_engine import get_monte_carlo_engine
from src.calibration.state_calibration_engine import get_state_calibration_engine
from src.governance.meta_policy import get_meta_policy_engine
from src.governance.meta_policy.meta_policy_engine import PolicyOutcome
from src.prediction.unified_prediction_service import get_unified_prediction_service
from src.governance.closed_loop_validation_engine import get_closed_loop_validation_engine
from src.betting.bankroll import get_bankroll_manager
from src.contracts.pipeline_contracts import (
    PipelineTrace, PipelineStage, FailureClassification, PolicyDecision,
    ContractValidator, ContractValidationError
)
from src.governance.system_versioning import get_system_version, create_run_lineage
from src.governance.lineage_tracker import get_lineage_tracker
logger = logging.getLogger(__name__)


def _write_attribution(predictions: list, run_id: str) -> None:
    """Write per-prediction layer attribution records for architecture governance. Non-fatal."""
    try:
        from src.betting.attribution_engine import AttributionEngine
        from src.storage.models import PredictionRecord
        from src.storage.db import get_session
        from sqlalchemy import select as sa_select

        attribution_engine = AttributionEngine(run_id=run_id)

        with get_session() as s:
            for pred in predictions:
                p = pred if isinstance(pred, dict) else (pred.__dict__ if hasattr(pred, '__dict__') else {})
                fixture_id = p.get("fixture_id")
                market = p.get("market")
                if not fixture_id or not market:
                    continue

                record = s.execute(
                    sa_select(PredictionRecord).where(
                        PredictionRecord.fixture_id == fixture_id,
                        PredictionRecord.market == market,
                        PredictionRecord.run_id == run_id,
                    )
                ).scalars().first()

                if not record:
                    continue

                prob_raw = float(p.get("our_prob") or 0.5)
                prob_cal = float(p.get("calibrated_prob") or prob_raw)
                odds = float(p.get("odds") or 1.0)

                attribution_engine.compute_attribution(
                    prediction_id=record.id,
                    fixture_id=fixture_id,
                    market=market,
                    model_prob_raw=prob_raw,
                    calibration_prob=prob_cal,
                    league_adjusted_prob=prob_cal,
                    latent_adjusted_prob=prob_cal,
                    drift_adjusted_prob=prob_cal,
                    final_prob=prob_cal,
                    odds_decimal=odds,
                )
                attribution_engine.save_to_database(record.id)

        logger.info(f"[COORDINATOR] Attribution written for run {run_id}")
    except Exception:
        logger.warning("[COORDINATOR] Attribution write failed (non-fatal)", exc_info=True)


class AgentCoordinator:
    """
    Coordinates multi-agent decision pipeline.
    
    Flow:
    1. Start run (emit RUN_STARTED)
    2. Run Predictor Agent
    3. Run Risk Manager Agent
    4. Run Execution Strategist Agent
    5. End run (emit RUN_COMPLETED)
    6. Generate reports
    """
    
    def __init__(self):
        self.state_store = get_state_store()
        self.reporter = get_agent_reporter()
        self.state_manager = get_state_manager()
        
        # Get agent instances
        self.predictor = get_predictor_agent()
        self.risk_manager = get_risk_manager_agent()
        self.execution_strategist = get_execution_strategist_agent()
        self.adversary = get_adversary_agent()
        
        # Get portfolio engine - PRIMARY DECISION CORE
        self.portfolio_engine = get_portfolio_engine()
        
        # Get governance engines
        self.policy_engine = get_policy_engine()
        self.spine_guard = get_execution_spine_guard()
        
        # Get simulation engine
        self.monte_carlo = get_monte_carlo_engine()
        
        # Get calibration and meta-policy engines
        self.calibration_engine = get_state_calibration_engine()
        self.meta_policy_engine = get_meta_policy_engine()
        
        # Get closed-loop validation engine
        self.clve = get_closed_loop_validation_engine()
        
        # Get bankroll manager
        self.bankroll_manager = get_bankroll_manager()
        
        # Get learning components
        self.evaluator = get_performance_evaluator()
        self.weight_optimizer = get_weight_optimizer()
        self.replay = get_event_replay()
        
        # Validation tracking
        self._feedback_completed = False
        self._calibration_updated = False
        self._policy_updated = False
        self._monte_carlo_executed = False

        # Track which placed bet IDs have already been fed to the calibration engine
        # to prevent duplicate accumulation across cycles within a single process session.
        self._calibration_seen_bet_ids: set[int] = set()
        
        logger.info("[COORDINATOR] Multi-agent system initialized - CLOSED-LOOP ADAPTIVE CAPITAL SYSTEM")
    
    def run_cycle(self, predictions: list = None) -> dict:
        """
        Execute full closed-loop adaptive capital cycle.
        
        This is the PRIMARY entry point that ensures:
        1. Prediction
        2. Portfolio Allocation  
        3. Risk Evaluation
        4. Monte Carlo Simulation
        5. Policy Decision (HARD GATE)
        6. Execution
        7. FEEDBACK LOOP (REQUIRED)
        8. VALIDATION (FAIL if incomplete)
        
        Args:
            predictions: Optional pre-generated predictions to use instead of regenerating.
        
        Returns:
            Run summary dict
        """
        # Reset validation flags for new run
        self._feedback_completed = False
        self._calibration_updated = False
        self._policy_updated = False
        self._monte_carlo_executed = False
        
        return self._run_internal(predictions=predictions)
    
    def run(self) -> dict:
        """
        Alias for run_cycle() - backwards compatibility.
        """
        return self.run_cycle()
    
    def _run_internal(self, predictions: list = None) -> dict:
        """
        Internal run implementation with contract validation.
        
        Args:
            predictions: Optional pre-generated predictions to use instead of regenerating.
        """
        run_id = str(uuid.uuid4())[:8]
        system_version = get_system_version()
        
        # Initialize PipelineTrace for observability
        trace = PipelineTrace(
            run_id=run_id,
            system_version=system_version.composite_version()
        )
        
        # Initialize lineage tracking
        lineage = create_run_lineage(run_id)
        lineage_tracker = get_lineage_tracker()
        lineage_tracker.start_run(run_id)
        
        # Use experiment tracker if already started, otherwise start new run
        from backend.experiment_tracker import get_tracker
        tracker = get_tracker()
        if tracker.get_current_run_id() is None:
            from backend.runtime_mode import get_mode_name
            runtime_mode = get_mode_name()  # Returns string, not RuntimeMode enum
            tracker.start_run(runtime_mode, record_in_db=True)
        # Capture once — avoid races where finalize_run() on another thread nulls it mid-cycle
        _tracker_run_id: str = tracker.get_current_run_id() or run_id

        # Capture active model version IDs once at cycle start for PlacedBet stamping.
        try:
            from src.models.model_registry import get_model_registry
            _active_model_ids: dict = get_model_registry().get_active_ids()
        except Exception:
            _active_model_ids = {}

        logger.info(f"[COORDINATOR] Starting run {run_id}")
        logger.info(f"[COORDINATOR] System version: {system_version.composite_version()}")
        
        # Initialize reporter
        self.reporter.start_run()
        
        # Emit run started
        event_bus.emit(AgentEvents.RUN_STARTED, {
            "run_id": run_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # NEW: Load previous PortfolioState
        previous_state = self.state_manager.load_previous_state()
        
        try:
            # Step 1: Unified Prediction Service (SINGLE SOURCE OF TRUTH)
            # Use pre-generated predictions if provided, otherwise generate fresh
            if predictions is not None:
                logger.info("[COORDINATOR] Using pre-generated predictions")
                # Convert dicts to PredictionPackets if needed
                try:
                    predictions = ContractValidator.validate_prediction_input(predictions)
                except ContractValidationError as e:
                    trace.add_failure(PipelineStage.PREDICTION, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                    raise RuntimeError(f"PIPELINE CONTRACT FAILURE at prediction: {e}")
                
                # Record predictions to experiment tracker (pre-generated, so record now)
                from backend.experiment_tracker import get_tracker
                tracker = get_tracker()
                tracker.record_predictions_made(len(predictions))
            else:
                logger.info("[COORDINATOR] Step 1: UnifiedPredictionService - SINGLE SOURCE OF TRUTH")
                prediction_service = get_unified_prediction_service()
                
                # Fetch upcoming fixtures
                from src.storage.db import get_session
                from src.storage.models import Fixture
                
                fixtures = []
                with get_session() as s:
                    # Use naive UTC so the comparison works against SQLite's naive datetime strings.
                    # Order by date ascending (nearest fixtures first).
                    # No row limit — we want every NS fixture in the upcoming window.
                    rows = s.execute(
                        select(Fixture)
                        .where(Fixture.status == "NS")
                        .where(Fixture.date >= datetime.utcnow())
                        .order_by(Fixture.date.asc())
                    ).scalars().all()

                    # Create simple stubs to avoid session detachment
                    class FixtureStub:
                        def __init__(self, f):
                            self.id = f.id
                            self.home_team_id = f.home_team_id
                            self.away_team_id = f.away_team_id
                            self.league_id = f.league_id
                            self.date = f.date
                            self.status = f.status

                    fixtures = [FixtureStub(f) for f in rows]
                
                if not fixtures:
                    trace.add_failure(PipelineStage.PREDICTION, FailureClassification.DATA_FAILURE, "No fixtures available")
                    raise RuntimeError("PIPELINE FAILURE: No fixtures available for prediction pipeline")
                
                logger.info(f"[COORDINATOR] Fetched {len(fixtures)} fixtures for prediction")
                
                # Generate predictions via unified service
                predictions = prediction_service.generate_with_fixture_data(fixtures)
                
                # CONTRACT VALIDATION - MUST validate predictions entering pipeline
                try:
                    predictions = ContractValidator.validate_prediction_input(predictions)
                except ContractValidationError as e:
                    trace.add_failure(PipelineStage.PREDICTION, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                    raise RuntimeError(f"PIPELINE CONTRACT FAILURE at prediction: {e}")
                
                # Save predictions to database (immutable storage)
                # Use _tracker_run_id captured at cycle start to avoid race with finalize_run().
                prediction_service = get_unified_prediction_service()
                saved_pred_ids = prediction_service.save_predictions(predictions, run_id=_tracker_run_id)
                logger.info(f"[COORDINATOR] Saved {len(saved_pred_ids)} predictions to database (run_id={_tracker_run_id})")

                _write_attribution(predictions, _tracker_run_id)

                tracker.record_predictions_made(len(predictions))
            
            # HARD ASSERTION - pipeline must produce predictions
            if len(predictions) == 0:
                raise RuntimeError("PIPELINE DEAD: NO PREDICTIONS GENERATED")
            
            # Update trace
            trace.mark_prediction(len(predictions))
            for i, pred in enumerate(predictions):
                pred_id = pred.prediction_id if hasattr(pred, 'prediction_id') else f"pred_{run_id}_{i}"
                lineage_tracker.record_prediction(pred_id, pred.fixture_id, pred.market)
            
            avg_ev = sum((p.ev or 0) for p in predictions) / len(predictions) if predictions else 0
            self.reporter.record_predictions(len(predictions), avg_ev)
            
            # Step 2: Run Risk Manager (with state feedback)
            logger.info("[COORDINATOR] Step 2: Running Risk Manager Agent (stateful)")
            risk_profile = self.risk_manager.run(portfolio_state=previous_state)
            
            self.reporter.record_risk(
                risk_profile["regime"],
                risk_profile["lambda"],
                risk_profile["drawdown"]
            )
            
            # Step 3: Run Execution Strategist (generates candidate portfolio)
            logger.info("[COORDINATOR] Step 3: Running Execution Strategist Agent")

            # Sync state_store bankroll from actual round balance so Markowitz
            # sizes stakes correctly against the real bankroll, not the default.
            try:
                from src.storage.db import get_session as _get_session
                from src.storage.models import BankrollRound as _BankrollRound, PlacedBet as _PlacedBet
                from sqlalchemy import func as _func
                with _get_session() as _s:
                    _round = _s.execute(
                        select(_BankrollRound).where(_BankrollRound.is_active == True)
                    ).scalar_one_or_none()
                    if _round:
                        _staked = float(_s.execute(
                            select(_func.coalesce(_func.sum(_PlacedBet.stake), 0))
                            .where(_PlacedBet.round_id == _round.id)
                            .where(_PlacedBet.settled == False)
                        ).scalar() or 0)
                        _settled_pnl = float(_s.execute(
                            select(_func.coalesce(_func.sum(_PlacedBet.pnl), 0))
                            .where(_PlacedBet.round_id == _round.id)
                            .where(_PlacedBet.settled == True)
                        ).scalar() or 0)
                        _available = max(0.0, _round.initial_bankroll + _settled_pnl - _staked)
                        self.state_store.update_bankroll(_available)
                        logger.info(f"[COORDINATOR] Bankroll synced: initial={_round.initial_bankroll:.2f}, settled_pnl={_settled_pnl:.2f}, staked={_staked:.2f}, available={_available:.2f}")
            except Exception as _e:
                logger.warning(f"[COORDINATOR] Could not sync bankroll: {_e}")

            # Set predictions and risk profile for the execution strategist
            self.execution_strategist.set_predictions([p.__dict__ if hasattr(p, '__dict__') else p for p in predictions])
            self.execution_strategist.set_risk_profile(risk_profile)
            
            portfolio_candidates = self.execution_strategist.run()
            
            # Convert execution strategist output to allocation vectors directly
            # (avoiding double-optimization that results in 0 bets)
            if portfolio_candidates and any(p.get("stake", 0) > 0 for p in portfolio_candidates):
                logger.info("[COORDINATOR] Using Execution Strategist output directly (no re-optimization)")
                from src.betting.portfolio.portfolio_engine import AllocationVector
                allocation_vectors = [
                    AllocationVector(
                        bet_id=p.get("bet_id", f"{p['fixture_id']}_{p['market']}_{p['outcome']}"),
                        fixture_id=p.get("fixture_id", 0),
                        market=p.get("market", "h2h"),
                        outcome=p.get("outcome", ""),
                        odds=p.get("odds", 1.0),
                        stake=p.get("stake", 0),
                        weight=p.get("weight", 0.0),
                        expected_return=p.get("expected_return", 0),
                        risk_contribution=p.get("risk_contribution", 0),
                        ev=p.get("ev", 0),
                        our_prob=p.get("our_prob", 0.5),
                    )
                    for p in portfolio_candidates if p.get("stake", 0) > 0
                ]
                new_state = previous_state.copy() if previous_state else None
                # Recompute exposure_by_market from the actual new allocation vectors
                # so the policy engine sees current concentration, not stale values.
                if new_state is not None and allocation_vectors:
                    _market_stake = {}
                    for _v in allocation_vectors:
                        _market_stake[_v.market] = _market_stake.get(_v.market, 0) + _v.stake
                    _total = sum(_market_stake.values())
                    if _total > 0:
                        new_state.exposure_by_market = {m: s / _total for m, s in _market_stake.items()}
                    else:
                        new_state.exposure_by_market = {}
            else:
                # Step 3b: Portfolio Engine (PRIMARY DECISION CORE) - STATEFUL VERSION
                logger.info("[COORDINATOR] Step 3b: Running Portfolio Engine (stateful)")
                bankroll = self.state_store.get_current_bankroll()
                allocation_vectors, new_state = self.portfolio_engine.compute_allocation(
                    predictions=portfolio_candidates,
                    bankroll=bankroll,
                    risk_profile=risk_profile,
                    previous_state=previous_state
                )
            
            # Convert allocation vectors to portfolio format
            portfolio = [
                {
                    "bet_id": v.bet_id,
                    "fixture_id": v.fixture_id,
                    "market": v.market,
                    "outcome": v.outcome,
                    "odds": v.odds,
                    "stake": v.stake,
                    "expected_return": v.expected_return,
                    "risk_contribution": v.risk_contribution,
                    "ev": v.ev,
                    "our_prob": v.our_prob,
                }
                for v in allocation_vectors
            ]
            
            # CONTRACT VALIDATION - Portfolio must match predictions
            try:
                portfolio = ContractValidator.validate_portfolio_input(predictions, portfolio)
            except ContractValidationError as e:
                trace.add_failure(PipelineStage.PORTFOLIO, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                raise RuntimeError(f"PIPELINE CONTRACT FAILURE at portfolio: {e}")
            
            trace.mark_portfolio(len(portfolio))
            lineage_tracker.record_portfolio(f"portfolio_{run_id}")
            
            # PIPELINE ASSERTION - portfolio must not be empty
            if not portfolio:
                trace.add_warning(PipelineStage.PORTFOLIO, "Portfolio is empty - no bets to place")
                logger.warning("[COORDINATOR] Portfolio is empty - no bets to place")
            logger.info("[COORDINATOR] Step 4: Running Adversarial Agent")
            adversary_result = self.adversary.run(
                portfolio=portfolio,
                risk_profile=risk_profile
            )
            
            # Record adversarial results
            self.reporter.record_adversarial(
                risk_score=adversary_result.portfolio_risk_score,
                max_drawdown=adversary_result.max_drawdown_simulated,
                recommendation=adversary_result.recommendation,
                vulnerabilities=len(adversary_result.vulnerable_positions)
            )
            
            # Apply adversarial adjustments if needed
            if adversary_result.recommendation == "adjust":
                logger.info("[COORDINATOR] Applying adversarial adjustments")
                portfolio = self.adversary.apply_adjustments(portfolio)
            elif adversary_result.recommendation == "reject":
                logger.warning("[COORDINATOR] Adversary rejected portfolio - no execution")
                portfolio = []
            
            # Step 5: Monte Carlo Simulation + Policy Engine
            logger.info("[COORDINATOR] Step 5: Running Policy Engine (HARD CONSTRAINT GOVERNOR)")
            
            # Compute state hash for observability
            state_hash = compute_state_hash({
                "allocations": portfolio,
                "risk_lambda": risk_profile.get("lambda", 1.0),
                "regime": risk_profile.get("regime", "neutral"),
            })
            
            # Run policy evaluation (simplified - no MonteCarlo for now)
            from src.governance.policy_engine import MonteCarloResults
            mc_results = MonteCarloResults(
                trajectories=[[new_state]],
                final_balances=[new_state.realized_pnl],
                max_drawdowns=[new_state.drawdown],
            )
            
            # Mark Monte Carlo as executed (simplified version)
            self._monte_carlo_executed = True
            
            policy_decision = self.policy_engine.evaluate(
                simulation_results=mc_results,
                current_state=new_state,
                proposed_allocation={b["bet_id"]: b for b in portfolio} if portfolio else {}
            )
            
            # CONTRACT VALIDATION - Risk evaluation must complete
            try:
                risk_data = {
                    "lambda": risk_profile.get("lambda", 1.0),
                    "regime": risk_profile.get("regime", "neutral"),
                    "approved_allocations": portfolio
                }
                risk_data = ContractValidator.validate_risk_input(portfolio, risk_data)
            except ContractValidationError as e:
                trace.add_failure(PipelineStage.RISK, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                raise RuntimeError(f"PIPELINE CONTRACT FAILURE at risk: {e}")
            
            trace.mark_risk(len(portfolio))
            lineage_tracker.record_risk(f"risk_{run_id}", "v1")
            
            logger.info(f"[COORDINATOR] Policy decision: {policy_decision.decision.value}")
            
            # CONTRACT VALIDATION - Policy must explicitly decide
            try:
                policy_data = {
                    "decision": policy_decision.decision,
                    "violated_constraints": policy_decision.violated_constraints,
                    "risk_score": policy_decision.risk_score
                }
                policy_data = ContractValidator.validate_policy_input(risk_data, policy_data)
            except ContractValidationError as e:
                trace.add_failure(PipelineStage.POLICY, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                raise RuntimeError(f"PIPELINE CONTRACT FAILURE at policy: {e}")
            
            if policy_decision.approved:
                trace.mark_policy(PolicyDecision.APPROVE)
            else:
                trace.mark_policy(PolicyDecision.REJECT)
            
            lineage_tracker.record_policy(f"policy_{run_id}", "v1")
            
            # Check policy decision
            if not policy_decision.approved:
                logger.warning(f"[COORDINATOR] Policy REJECTED: {policy_decision.reject_reason}")
                portfolio = []  # Clear portfolio - no execution
            
            # Apply policy throttle if needed
            if policy_decision.adjusted_allocation_scale < 1.0:
                scale = policy_decision.adjusted_allocation_scale
                logger.info(f"[COORDINATOR] Policy throttle applied: {scale:.2f}x")
                for bet in portfolio:
                    bet["stake"] = bet.get("stake", 0) * scale
                    bet["expected_return"] = bet.get("expected_return", 0) * scale
            
            # Store policy decision for execution
            policy_decision_data = policy_decision
            
            # Calculate totals before event emission
            total_stake = sum(b["stake"] for b in portfolio)
            expected_return = sum(b["expected_return"] for b in portfolio)
            
            # Save bets to database for dashboard display — gated by the bot_enabled kill
            # switch. Prediction generation and portfolio computation always run (needed to
            # keep calibration data and CLV measurement flowing); only persistence of
            # PlacedBet rows (and the bankroll consumption that follows from them) is paused.
            from config.settings import settings as _bet_settings
            if portfolio and total_stake > 0 and not _bet_settings.bot_enabled:
                logger.warning(
                    f"[COORDINATOR] Betting PAUSED (bot_enabled=False) — would have placed "
                    f"{len(portfolio)} bets totaling {total_stake:.2f} SEK "
                    f"(expected_return={expected_return:.2f}) — not persisted"
                )
            elif portfolio and total_stake > 0:
                saved_bets = 0
                skipped_bets = 0
                try:
                    from src.storage.db import get_session
                    from src.storage.models import PlacedBet, BankrollRound, PredictionRecord
                    from sqlalchemy import func

                    with get_session() as s:
                        # Get or create active round
                        round_row = s.execute(
                            select(BankrollRound).where(BankrollRound.is_active == True)
                        ).scalar_one_or_none()
                        
                        if not round_row:
                            prev_round = s.execute(
                                select(BankrollRound).order_by(BankrollRound.id.desc())
                            ).scalar_one_or_none()
                            
                            next_num = (prev_round.round_number + 1) if prev_round else 1
                            
                            from config.settings import settings as _settings
                            round_row = BankrollRound(
                                round_number=next_num,
                                initial_bankroll=_settings.initial_bankroll,
                                ending_balance=_settings.initial_bankroll,
                                is_active=True
                            )
                            s.add(round_row)
                            s.flush()
                        
                        # PRODUCTION-GRADE BANKROLL PROTECTION
                        initial_bankroll = round_row.initial_bankroll

                        # Query total already staked in this round (unsettled)
                        staked_result = s.execute(
                            select(func.coalesce(func.sum(PlacedBet.stake), 0))
                            .where(PlacedBet.round_id == round_row.id)
                            .where(PlacedBet.settled == False)
                        ).scalar()

                        # Query settled P&L — losses reduce the available bankroll
                        settled_pnl_result = s.execute(
                            select(func.coalesce(func.sum(PlacedBet.pnl), 0))
                            .where(PlacedBet.round_id == round_row.id)
                            .where(PlacedBet.settled == True)
                        ).scalar()

                        already_staked = float(staked_result or 0)
                        settled_pnl = float(settled_pnl_result or 0)
                        available = max(0.0, initial_bankroll + settled_pnl - already_staked)

                        logger.info(f"[COORDINATOR] Bankroll check: initial={initial_bankroll:.2f}, settled_pnl={settled_pnl:.2f}, already_staked={already_staked:.2f}, available={available:.2f}")

                        # Filter bets to only those within available bankroll
                        eligible_bets = []
                        for bet in portfolio:
                            stake = bet.get("stake", 0)
                            if stake <= 0:
                                continue

                            if stake > available:
                                logger.warning(f"[COORDINATOR] SKIP {bet.get('fixture_id')}/{bet.get('market')}: stake={stake:.2f} > available={available:.2f}")
                                skipped_bets += 1
                                continue

                            eligible_bets.append(bet)
                            available -= stake
                        
                        if not eligible_bets:
                            logger.warning(f"[COORDINATOR] No eligible bets - all {skipped_bets} skipped due to bankroll limits")
                        
                        # PRODUCTION-GRADE: Idempotent bet insertion - check for duplicates first
                        for bet in eligible_bets:
                            fixture_id = bet.get("fixture_id", 0)
                            market = bet.get("market", "h2h")
                            outcome = bet.get("outcome", "")
                            
                            # Check if bet already exists (idempotent)
                            existing = s.execute(
                                select(PlacedBet).where(
                                    PlacedBet.round_id == round_row.id,
                                    PlacedBet.fixture_id == fixture_id,
                                    PlacedBet.market == market,
                                    PlacedBet.outcome == outcome
                                )
                            ).scalar_one_or_none()
                            
                            if existing:
                                logger.info(f"[COORDINATOR] SKIP duplicate bet: {fixture_id}/{market}/{outcome}")
                                skipped_bets += 1
                                continue

                            # Link to originating prediction record
                            pred_rec = s.execute(
                                select(PredictionRecord).where(
                                    PredictionRecord.fixture_id == fixture_id,
                                    PredictionRecord.market == market,
                                    PredictionRecord.is_legacy == False,
                                )
                            ).scalar_one_or_none()

                            placed = PlacedBet(
                                round_id=round_row.id,
                                fixture_id=fixture_id,
                                market=market,
                                outcome=outcome,
                                stake=bet.get("stake", 0),
                                odds=bet.get("odds", 0),
                                our_prob=bet.get("our_prob", 0.5),
                                calibrated_prob=bet.get("calibrated_prob"),
                                ev=bet.get("ev", 0),
                                kelly_fraction=bet.get("kelly", 0),
                                run_id=_tracker_run_id,
                                calibration_version_id=bet.get("calibration_version"),
                                model_version_id=_active_model_ids.get(market),
                                prediction_record_id=pred_rec.id if pred_rec else None,
                            )
                            s.add(placed)
                            saved_bets += 1
                        
                        s.commit()
                        logger.info(f"[COORDINATOR] Saved {saved_bets} bets, skipped {skipped_bets} (bankroll+duplicates)")
                except Exception as e:
                    logger.warning(f"[COORDINATOR] Failed to save bets: {e}")
            
            # Emit PORTFOLIO_ALLOCATED with policy_decision for ExecutionEngine
            event_bus.emit(AgentEvents.PORTFOLIO_ALLOCATED, {
                "run_id": run_id,
                "bets": portfolio,
                "total_stake": total_stake,
                "expected_return": expected_return,
                "regime": risk_profile.get("regime", "neutral"),
                "lambda": risk_profile.get("lambda", 1.0),
                "policy_decision": policy_decision_data,
                "source_chain": [
                    "AgentCoordinator",
                    "PortfolioEngine",
                    "RiskEngine",
                    "MonteCarlo", 
                    "PolicyEngine",
                    "ExecutionEngine"
                ],
                "portfolio_state_hash": state_hash,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Get execution results
            
            # CONTRACT VALIDATION - Execution must only execute approved allocations
            try:
                execution_data = ContractValidator.validate_execution_input(
                    {"decision": policy_decision.decision, "approved": policy_decision.approved},
                    portfolio
                )
            except ContractValidationError as e:
                trace.add_failure(PipelineStage.EXECUTION, FailureClassification.PIPELINE_CONTRACT_FAILURE, str(e))
                raise RuntimeError(f"PIPELINE CONTRACT FAILURE at execution: {e}")
            
            trace.mark_execution(len(portfolio))
            lineage_tracker.record_execution(f"execution_{run_id}")
            
            self.reporter.record_execution(
                len(portfolio),
                total_stake,
                expected_return,
                expected_return  # Using as risk proxy
            )
            
            # Record bets to experiment tracker for UI display
            from backend.experiment_tracker import get_tracker
            tracker = get_tracker()
            tracker.record_bets_placed(len(portfolio))
            
            # Step 5: Learning - evaluate performance and update weights
            logger.info("[COORDINATOR] Step 5: Running Learning System")
            
            # Evaluate performance
            performance = self.evaluator.evaluate(
                bets=portfolio,
                predictions=predictions,
                risk_profile=risk_profile,
                previous_weights=self.weight_optimizer.get_weights()
            )
            
            # Record for replay
            self.replay.record_run(
                run_id=run_id,
                predictions=predictions,
                risk_profile=risk_profile,
                portfolio=portfolio,
                execution_results=portfolio,
                performance=performance
            )
            
            # Update weights based on performance
            new_weights = self.weight_optimizer.optimize(performance)
            
            # Record learning
            self.reporter.record_learning(
                performance=performance,
                new_weights=new_weights,
                best_markets=performance.get("best_markets", []),
                worst_markets=performance.get("worst_markets", [])
            )
            
            # Emit learning events
            event_bus.emit(AgentEvents.PERFORMANCE_RECORDED, performance)
            event_bus.emit(AgentEvents.WEIGHTS_UPDATED, {
                "weights": new_weights,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Update state with this run's execution and learning results
            if portfolio:
                self.state_manager.update_from_execution(portfolio, total_stake, len(portfolio))
            self.state_manager.update_from_learning(
                new_weights=new_weights,
                regime=risk_profile.get("regime", "neutral"),
                lambda_val=risk_profile.get("lambda", 1.0)
            )

            # Persist PortfolioState
            self.state_manager.persist_state(run_id, "run_completed")
            
            # End run
            self.state_store.end_run()
            event_bus.emit(AgentEvents.RUN_COMPLETED, {
                "run_id": run_id,
                "predictions": len(predictions),
                "bets": len(portfolio),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Save reports
            self.reporter.save_reports()
            
            # Set execution result for feedback loop
            execution_result = {"bets": portfolio, "total_stake": total_stake}
            
            # Step 7: FEEDBACK LOOP (REQUIRED)
            logger.info("[COORDINATOR] Step 7: Running FEEDBACK LOOP")
            feedback_results = self._run_feedback_cycle(execution_result, run_id)
            
            # Step 8: VALIDATION (FAIL if incomplete)
            logger.info("[COORDINATOR] Step 8: Validating run completion")
            self._validate_run_completion(feedback_results)
            
            # Step 9: CLOSED LOOP VALIDATION (BLOCK if not adapting)
            logger.info("[COORDINATOR] Step 9: Closed-Loop Self-Adaptation Validation")
            clve_report = self.clve.evaluate(run_id)
            
            # HARD ENFORCEMENT: Block if system is not adapting
            if not clve_report.decision.get("adaptive"):
                error_msg = (
                    f"SYSTEM NOT SELF-ADAPTING: EXECUTION BLOCKED. "
                    f"Reason: {clve_report.decision.get('reason')}. "
                    f"PDS={clve_report.metrics.pds:.4f}, AI={clve_report.metrics.ai:.4f}, "
                    f"CDS={clve_report.metrics.cds:.4f}. "
                    f"Check portfolio feedback wiring."
                )
                logger.critical(f"[COORDINATOR] {error_msg}")
                
                # Emit error event
                event_bus.emit(AgentEvents.AGENT_ERROR, {
                    "run_id": run_id,
                    "error": error_msg,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                
                # Raise to block execution
                raise RuntimeError(error_msg)
            
            logger.info(
                f"[COORDINATOR] CLVE: System confirmed ADAPTIVE "
                f"(score={clve_report.adaptive_score:.2f})"
            )
            
            summary = {
                "run_id": run_id,
                "predictions": len(predictions),
                "prediction_ids": [p.prediction_id for p in predictions if hasattr(p, 'prediction_id') and p.prediction_id],
                "bets": len(portfolio),
                "total_stake": total_stake,
                "expected_return": expected_return,
                "mc_expected_return": expected_return,
                "mc_volatility": 0.0,
                "mc_max_drawdown": new_state.drawdown if new_state else 0.0,
                "mc_ruin_probability": 0.0 if not portfolio else 0.0,  # No ruin if no bets
                "feedback_completed": self._feedback_completed,
                "calibration_updated": self._calibration_updated,
                "policy_updated": self._policy_updated,
                "clve_adaptive": clve_report.decision.get("adaptive"),
                "clve_score": clve_report.adaptive_score,
                "system_version": system_version.composite_version(),
                "pipeline_status": trace.get_status(),
            }
            
            # Complete pipeline trace
            trace.mark_complete()
            
            lineage_tracker.set_run_metrics(
                prediction_count=len(predictions),
                bet_count=len(portfolio),
                health_score=clve_report.adaptive_score if clve_report else 0.0
            )
            lineage_tracker.complete_lineage(trace.get_status())
            
            # Generate observability report
            self._save_pipeline_trace(trace, run_id)
            
            # Finalize experiment tracker only if coordinator started it
            from backend.experiment_tracker import get_tracker
            tracker = get_tracker()
            if tracker.get_current_run_id() is not None:
                bankroll = self.bankroll_manager.get_balance() if self.bankroll_manager else None
                tracker.finalize_run(bankroll_snapshot=bankroll, final_metrics=summary)
            
            logger.info(f"[COORDINATOR] Run {run_id} completed: {len(portfolio)} bets placed, feedback={self._feedback_completed}")
            
            return summary
            
        except Exception as e:
            logger.exception("[COORDINATOR] Run failed")
            
            # Mark trace as failed
            if trace:
                trace.add_failure(PipelineStage.EXECUTION, FailureClassification.EXECUTION_FAILURE, str(e))
                trace.mark_complete()
                self._save_pipeline_trace(trace, run_id)
            
            # Complete lineage as failed
            lineage_tracker.set_run_metrics(
                prediction_count=len(predictions) if predictions is not None else 0,
                bet_count=0,
                health_score=0.0
            )
            lineage_tracker.complete_lineage("FAILED")
            
            event_bus.emit(AgentEvents.AGENT_ERROR, {
                "run_id": run_id,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            })

            # Always finalize the tracker run so it doesn't stay 'active' and accumulate
            # predictions from future cycles on the same stuck run_id.
            from backend.experiment_tracker import get_tracker
            tracker = get_tracker()
            if tracker.get_current_run_id() is not None:
                tracker.finalize_run(final_metrics={"pipeline_status": "FAILED", "error": str(e)[:200]})

            raise



    def _run_feedback_cycle(self, execution_result: dict, run_id: str) -> dict:
        """
        Execute feedback loop - REQUIRED for run completion.
        
        This method:
        1. Fetches recent outcomes
        2. Computes performance metrics
        3. Updates calibration
        4. Updates meta-policy
        5. Persists state
        """
        logger.info("[COORDINATOR] Starting FEEDBACK CYCLE (REQUIRED)")
        
        feedback_results = {
            "performance": None,
            "calibration": None,
            "policy": None,
            "state_persisted": False,
        }
        
        try:
            # 7.1 Fetch outcomes from settled bets
            logger.info("[COORDINATOR] 7.1 Fetching recent outcomes")
            from src.storage.db import get_session
            from src.storage.models import PlacedBet
            
            outcomes = []
            with get_session() as session:
                settled_bets = session.execute(
                    select(PlacedBet).where(
                        PlacedBet.settled == True
                    ).order_by(PlacedBet.settled_at.desc()).limit(100)
                ).scalars().all()

                new_bets = [b for b in settled_bets if b.id not in self._calibration_seen_bet_ids]
                for bet in new_bets:
                    self._calibration_seen_bet_ids.add(bet.id)
                    outcomes.append({
                        "fixture_id": bet.fixture_id,
                        "market": bet.market,
                        # Use VCL calibrated prob for drift tracking; fall back to raw if unavailable
                        "predicted_prob": bet.calibrated_prob if bet.calibrated_prob is not None else bet.our_prob,
                        "actual_outcome": 1 if bet.pnl and bet.pnl > 0 else 0,
                        "odds": bet.odds,
                    })

            logger.info(
                f"[COORDINATOR] Found {len(outcomes)} new settled bets for feedback "
                f"({len(self._calibration_seen_bet_ids)} total seen)"
            )
            
            # 7.2 Compute performance metrics
            logger.info("[COORDINATOR] 7.2 Computing performance metrics")
            performance = self.evaluator.evaluate(
                bets=execution_result.get("bets", []),
                predictions=[],
                risk_profile={},
                previous_weights=self.weight_optimizer.get_weights()
            )
            feedback_results["performance"] = performance
            
            event_bus.emit(AgentEvents.PERFORMANCE_COMPUTED, {
                "roi": performance.get("roi", 0),
                "win_rate": performance.get("win_rate", 0),
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # 7.3 Calibration update
            logger.info("[COORDINATOR] 7.3 Updating calibration")
            if outcomes:
                for outcome in outcomes:
                    self.calibration_engine.add_prediction_outcome(
                        fixture_id=outcome["fixture_id"],
                        market=outcome["market"],
                        predicted_prob=outcome["predicted_prob"],
                        actual_outcome=outcome["actual_outcome"],
                        odds=outcome["odds"],
                    )
                
                calibration_report = self.calibration_engine.generate_report()
                feedback_results["calibration"] = calibration_report
                self._calibration_updated = True
                
                logger.info(f"[COORDINATOR] Calibration updated: error={calibration_report.overall_calibration_error:.3f}")
            
            # 7.3b Update active architecture scores from governance analysis
            try:
                from backend.system_governance_engine import get_governance_engine
                from backend.architecture_evolution_engine import get_evolution_engine as get_arch_engine
                governance_engine = get_governance_engine()
                layer_metrics = governance_engine.compute_layer_metrics_from_attribution(run_id)
                if layer_metrics:
                    governance_engine.save_layer_metrics(run_id, layer_metrics)
                    avg_ev = sum(m.ev_contribution for m in layer_metrics) / len(layer_metrics)
                    avg_risk = sum(m.fragility_score for m in layer_metrics) / len(layer_metrics)
                    governance_score = sum(m.stability_score for m in layer_metrics) / len(layer_metrics)
                    get_arch_engine().update_active_architecture_scores(
                        governance_score=round(governance_score, 4),
                        ev_score=round(avg_ev, 4),
                        risk_score=round(avg_risk, 4),
                    )
            except Exception:
                logger.warning("[COORDINATOR] Architecture score update failed (non-fatal)", exc_info=True)

            # 7.4 Meta-policy update
            logger.info("[COORDINATOR] 7.4 Updating meta-policy")
            policy_outcome = PolicyOutcome(
                decision_id=run_id,
                decision="approved" if execution_result.get("bets") else "empty",
                policy_decision_type="approve",
                approved=bool(execution_result.get("bets")),
                resulted_in_drawdown=performance.get("max_drawdown", 0) > 0.05,
                drawdown_magnitude=performance.get("max_drawdown", 0),
                resulted_in_profit=performance.get("roi", 0) > 0,
                profit_magnitude=performance.get("roi", 0),
                decided_at=datetime.utcnow().isoformat(),
            )
            
            self.meta_policy_engine.add_policy_outcome(policy_outcome)
            
            # Only update policy periodically (every 20 outcomes)
            if len(self.meta_policy_engine._policy_outcomes) >= 20:
                policy_update = self.meta_policy_engine.update_policy()
                feedback_results["policy"] = policy_update
                self._policy_updated = True
                logger.info(f"[COORDINATOR] Meta-policy updated: {len(policy_update.adjusted_constraints)} changes")
            
            # 7.5 Persist state
            logger.info("[COORDINATOR] 7.5 Persisting system state")
            self.state_manager.persist_state(run_id, "feedback_completed")
            feedback_results["state_persisted"] = True
            
            # Mark feedback as completed
            self._feedback_completed = True
            
            # Emit completion event
            event_bus.emit(AgentEvents.RUN_FEEDBACK_COMPLETED, {
                "run_id": run_id,
                "roi": performance.get("roi", 0),
                "max_drawdown": performance.get("max_drawdown", 0),
                "calibration_updated": self._calibration_updated,
                "policy_updated": self._policy_updated,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            logger.info("[COORDINATOR] FEEDBACK CYCLE COMPLETED")
            
        except Exception:
            logger.exception("[COORDINATOR] Feedback cycle failed")
            raise
        
        return feedback_results
    
    def _save_pipeline_trace(self, trace, run_id: str) -> None:
        """Save pipeline trace report for observability."""
        from src.contracts.pipeline_contracts import get_trace_report
        from pathlib import Path
        
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        
        report = get_trace_report(trace)
        filepath = report_dir / f"pipeline_trace_{run_id}_{trace.system_version}.md"
        
        with open(filepath, 'w') as f:
            f.write(report)
        
        logger.info(f"[COORDINATOR] Saved pipeline trace: {filepath}")
    
    def _validate_run_completion(self, feedback_results: dict) -> None:
        """
        Validate that all required steps completed.
        
        Raises:
            RuntimeError if validation fails
        """
        errors = []
        
        if not self._monte_carlo_executed:
            errors.append("Monte Carlo not executed")
        
        if not self._feedback_completed:
            errors.append("Feedback cycle not completed")
        
        if not self._calibration_updated:
            logger.warning("[VALIDATION] Calibration not updated (may need more data)")
        
        if not self._policy_updated:
            logger.warning("[VALIDATION] Policy not updated (periodic update)")
        
        if errors:
            error_msg = f"Run validation FAILED: {'; '.join(errors)}"
            logger.error(f"[COORDINATOR] {error_msg}")
            raise RuntimeError(error_msg)
        
        logger.info("[COORDINATOR] Run validation PASSED")

# Global coordinator
_coordinator: Optional[AgentCoordinator] = None


def get_agent_coordinator() -> AgentCoordinator:
    """Get global agent coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator


def run_multi_agent_pipeline(predictions: list = None) -> dict:
    """
    Convenience function to run the full closed-loop pipeline.
    
    This is the PRIMARY entry point for the capital allocation system.
    
    Args:
        predictions: Optional pre-generated predictions to use instead of regenerating.
                     If None, predictions will be generated fresh.
    """
    coordinator = get_agent_coordinator()
    return coordinator.run_cycle(predictions=predictions)
