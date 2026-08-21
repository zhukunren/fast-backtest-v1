"""Vectorized presets for legacy, slot, and slot_broker engines."""
from __future__ import annotations

import pandas as pd

from core.exceptions import DataValidationError

from backtest.allocation.allocator import build_trade_segments
from backtest.decisions import SignalDecision
from backtest.ledger_rejections import merge_ledger_rejections
from backtest.netvalue.merge import merge_net_value_result_from_trading_dfs
from backtest.result import LedgerExecutionArtifacts, VectorizedExecutionArtifacts

from .contracts import EngineRequest, EngineResult, SegmentAssessmentSource

_VECTORIZED_ENGINE_IDS = frozenset({"legacy", "slot", "slot_broker"})


class VectorizedPreset:
    def __init__(self, engine_id: str) -> None:
        normalized = str(engine_id).strip().lower()
        if normalized not in _VECTORIZED_ENGINE_IDS:
            raise ValueError(f"unsupported vectorized engine: {engine_id!r}")
        self.engine_id = normalized

    def run(self, request: EngineRequest) -> EngineResult:
        if request.engine_spec.engine_id != self.engine_id:
            raise DataValidationError("engine request does not match composed preset")
        if not isinstance(request.decision, SignalDecision):
            raise DataValidationError(f"{self.engine_id} requires SignalDecision")
        strategy_spec = request.engine_spec.strategy_spec
        if strategy_spec is None:
            raise DataValidationError(f"{self.engine_id} requires strategy_spec")

        decision = request.decision
        allocation = build_trade_segments(
            selection=decision.selection,
            quote_store=decision.quote_store,
            trade_days=decision.trade_days,
            execution_policy=request.execution_policy,
            cfg=request.engine_spec.runtime_config,
            spec=strategy_spec,
        )
        diagnostics = dict(allocation.diagnostics or {})
        assessment_source = SegmentAssessmentSource(
            trading_frames=dict(allocation.trading_dfs),
            accounting_mode="cash" if self.engine_id in {"slot", "slot_broker"} else "close",
        )
        if request.run_spec.mode == "assess":
            execution = VectorizedExecutionArtifacts(
                equity_curve=None,
                trading_frames=dict(allocation.trading_dfs),
                processed_frames={},
                diagnostics=diagnostics,
            )
            return EngineResult(
                engine_id=self.engine_id,
                execution=execution,
                diagnostics=diagnostics,
                assessment_source=assessment_source,
            )

        merge_result = merge_net_value_result_from_trading_dfs(
            allocation.trading_dfs,
            accounting_mode=assessment_source.accounting_mode,
            collect_ledger_artifacts=self.engine_id == "slot_broker",
            quote_store=decision.quote_store,
            trade_days=decision.trade_days,
            execution_policy=request.execution_policy,
            cfg=request.engine_spec.runtime_config,
            spec=strategy_spec,
            risk_cfg=request.risk_policy,
            net_value_runtime=request.services.net_value_runtime,
        )
        if self.engine_id == "slot_broker":
            execution = LedgerExecutionArtifacts(
                equity_curve=merge_result.merged_df,
                orders_df=_frame_or_empty(merge_result.portfolio_orders_df),
                fills_df=_frame_or_empty(merge_result.portfolio_fills_df),
                risk_orders_df=_frame_or_empty(merge_result.risk_orders_df),
                risk_fills_df=_frame_or_empty(merge_result.risk_fills_df),
                rejections_df=merge_ledger_rejections(
                    merge_result.portfolio_orders_df,
                    merge_result.strategy_rejections_df,
                ),
                daily_state_df=merge_result.merged_df,
                trading_frames=dict(allocation.trading_dfs),
                processed_frames=dict(merge_result.processed_dfs),
                trade_segments_df=merge_result.trade_segments_df,
                trade_segments_summary_df=merge_result.trade_segments_summary_df,
                risk_control_summary_df=merge_result.risk_control_summary_df,
                risk_control_events_df=merge_result.risk_control_events_df,
                cost_summary_df=merge_result.cost_summary_df,
                diagnostics=diagnostics,
            )
        else:
            execution = VectorizedExecutionArtifacts(
                equity_curve=merge_result.merged_df,
                trading_frames=dict(allocation.trading_dfs),
                processed_frames=dict(merge_result.processed_dfs),
                trade_segments_df=merge_result.trade_segments_df,
                trade_segments_summary_df=merge_result.trade_segments_summary_df,
                risk_control_summary_df=merge_result.risk_control_summary_df,
                risk_control_events_df=merge_result.risk_control_events_df,
                cost_summary_df=merge_result.cost_summary_df,
                diagnostics=diagnostics,
            )
        return EngineResult(
            engine_id=self.engine_id,
            execution=execution,
            diagnostics=diagnostics,
            assessment_source=assessment_source,
        )


def _frame_or_empty(value: pd.DataFrame | None) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


__all__ = ["VectorizedPreset"]
