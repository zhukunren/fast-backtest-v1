from __future__ import annotations

from typing import Any

from backtest.reporting.diagnostics import build_backtest_single_run_report
from backtest.decisions import SignalDecisionArtifacts
from backtest.result import CurvePayload, RunResult

from ..runtime import RuntimeContext


def build_single_run_diagnostic_report(
    *,
    ctx: RuntimeContext,
    result: RunResult | None,
) -> dict[str, Any] | None:
    if not isinstance(result, RunResult) or not isinstance(result.payload, CurvePayload):
        return None
    payload = result.payload
    merged_df = payload.equity_curve
    if merged_df is None:
        return None
    return build_backtest_single_run_report(
        merged_df=merged_df,
        execution=payload.execution,
        decision_artifacts=(
            payload.decision_artifacts
            if isinstance(payload.decision_artifacts, SignalDecisionArtifacts)
            else None
        ),
        start_time=ctx.cfg.backtest.start_time,
        end_time=ctx.cfg.backtest.end_time,
    )


class SingleRunDiagnosticsService:
    """只读取本次回测显式结果，生成单次回测诊断。"""

    def build_report(
        self,
        *,
        ctx: RuntimeContext,
        result: RunResult | None,
    ) -> dict[str, Any] | None:
        return build_single_run_diagnostic_report(ctx=ctx, result=result)


__all__ = [
    "SingleRunDiagnosticsService",
    "build_single_run_diagnostic_report",
]
