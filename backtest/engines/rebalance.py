"""Fixed target-weight rebalance engine preset."""
from __future__ import annotations

from core.exceptions import DataValidationError

from backtest.decisions import TargetWeightDecision
from backtest.result import RebalanceExecutionArtifacts
from backtest.static_allocation import (
    StaticAllocationConfig,
    StaticAllocationCostConfig,
    run_static_allocation_backtest,
)

from .contracts import EngineRequest, EngineResult


class FixedRebalancePreset:
    engine_id = "fixed_rebalance"

    def run(self, request: EngineRequest) -> EngineResult:
        if request.engine_spec.engine_id != self.engine_id:
            raise DataValidationError("engine request does not match fixed_rebalance preset")
        if request.run_spec.mode != "curve":
            raise DataValidationError("fixed_rebalance only supports curve mode")
        if not isinstance(request.decision, TargetWeightDecision):
            raise DataValidationError("fixed_rebalance requires TargetWeightDecision")

        decision = request.decision
        runtime = request.engine_spec.runtime_config
        tc = runtime.transaction_cost
        result = run_static_allocation_backtest(
            decision.quote_store,
            StaticAllocationConfig(
                target_weights=decision.target_weights,
                trade_days=decision.trade_days,
                target_weight_schedule=decision.artifacts.weight_schedule,
                initial_capital=float(runtime.portfolio.initial_capital),
                lot_size=int(runtime.portfolio.lot_size),
                cash_buffer_pct=float(runtime.portfolio.cash_buffer_pct),
                rebalance_frequency=decision.rebalance_frequency,
                rebalance_dates=decision.rebalance_dates,
                buy_price_col=decision.buy_price_col,
                sell_price_col=decision.sell_price_col,
                mark_price_col=decision.mark_price_col,
                start_date=request.run_spec.start_time,
                end_date=request.run_spec.end_time,
                cost=StaticAllocationCostConfig(
                    enable=tc.enable,
                    commission_rate=tc.commission_rate,
                    min_commission=tc.min_commission,
                    stamp_duty_rate=tc.stamp_duty_rate,
                    slippage_bps=tc.slippage_bps,
                    include_buy_commission=tc.include_buy_commission,
                    include_sell_commission=tc.include_sell_commission,
                ),
                trade_constraints=getattr(runtime, "trade_constraints", None),
                normalize_weights=decision.normalize_weights,
            ),
        )
        diagnostics: dict[str, object] = {
            "target_count": len(result.target_weights),
            "target_weights": dict(result.target_weights),
            "symbol_type": decision.symbol_type,
            "rebalance_frequency": decision.rebalance_frequency,
        }
        execution = RebalanceExecutionArtifacts(
            equity_curve=result.merged_df,
            orders_df=result.orders_df,
            fills_df=result.fills_df,
            daily_state_df=result.merged_df,
            positions_df=result.positions_df,
            diagnostics=diagnostics,
        )
        return EngineResult(
            engine_id=self.engine_id,
            execution=execution,
            diagnostics=diagnostics,
        )


__all__ = ["FixedRebalancePreset"]
