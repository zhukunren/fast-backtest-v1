"""Broker portfolio-engine preset."""
from __future__ import annotations

from pathlib import Path

from core.exceptions import DataValidationError

from backtest.broker.cost_summary import build_broker_cost_summary
from backtest.broker.runner import run_broker_portfolio
from backtest.decisions import SignalDecision
from backtest.evaluation.assess import (
    build_actual_trade_segments_from_broker,
    build_actual_trade_segments_summary_from_broker,
)
from backtest.ledger_rejections import merge_ledger_rejections
from backtest.result import LedgerExecutionArtifacts
from backtest.slot_broker import build_slot_broker_risk_orders

from .contracts import EngineRequest, EngineResult, LedgerAssessmentSource


class BrokerPreset:
    engine_id = "broker"

    def run(self, request: EngineRequest) -> EngineResult:
        if request.engine_spec.engine_id != self.engine_id:
            raise DataValidationError("engine request does not match broker preset")
        if not isinstance(request.decision, SignalDecision):
            raise DataValidationError("broker requires SignalDecision")
        strategy_spec = request.engine_spec.strategy_spec
        if strategy_spec is None:
            raise DataValidationError("broker requires strategy_spec")
        decision = request.decision
        output_root = request.services.output_root
        broker_result = run_broker_portfolio(
            decision.selection,
            decision.quote_store,
            decision.trade_days,
            logger=request.services.logger,
            run_id=request.services.run_id,
            log_dir=(Path(output_root) / "logs") if output_root is not None else None,
            cfg=request.engine_spec.runtime_config,
            spec=strategy_spec,
            execution_policy=request.execution_policy,
            risk_cfg=request.risk_policy,
        )
        trade_segments_df = build_actual_trade_segments_from_broker(
            fills_df=broker_result.fills_df,
            risk_fills_df=broker_result.risk_fills_df,
            logger=request.services.logger,
        )
        trade_segments_summary_df = build_actual_trade_segments_summary_from_broker(
            prefix="实际交易段",
            fills_df=broker_result.fills_df,
            risk_fills_df=broker_result.risk_fills_df,
            logger=request.services.logger,
        )
        risk_orders_df = build_slot_broker_risk_orders(broker_result.risk_fills_df)
        runtime_config = request.engine_spec.runtime_config
        cost_summary_df = build_broker_cost_summary(
            merged_df=broker_result.merged_df,
            fills_df=broker_result.fills_df,
            risk_fills_df=broker_result.risk_fills_df,
            initial_capital=float(runtime_config.portfolio.initial_capital),
            cost_cfg=runtime_config.transaction_cost,
        )
        execution = LedgerExecutionArtifacts(
            equity_curve=broker_result.merged_df,
            orders_df=broker_result.orders_df,
            fills_df=broker_result.fills_df,
            risk_orders_df=risk_orders_df,
            risk_fills_df=broker_result.risk_fills_df,
            rejections_df=merge_ledger_rejections(
                broker_result.orders_df,
                broker_result.strategy_rejections_df,
            ),
            daily_state_df=broker_result.merged_df,
            daily_position_snapshots=broker_result.daily_position_snapshots,
            trade_segments_df=trade_segments_df,
            trade_segments_summary_df=trade_segments_summary_df,
            cost_summary_df=cost_summary_df,
        )
        return EngineResult(
            engine_id=self.engine_id,
            execution=execution,
            diagnostics={},
            assessment_source=LedgerAssessmentSource(
                equity_curve=broker_result.merged_df,
                fills_df=broker_result.fills_df,
                risk_fills_df=broker_result.risk_fills_df,
            ),
        )


__all__ = ["BrokerPreset"]
