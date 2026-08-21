from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backtest.result import CurvePayload, RunResult

from .diagnostics.multi_start import MultiStartRerunDiagnosticsService, SessionRunner
from .diagnostics.reporting import diagnostic_output_path, write_diagnostic_report
from .diagnostics.single_run import SingleRunDiagnosticsService
from .diagnostics.snapshot import build_diagnostic_snapshot
from .runtime import RuntimeContext


class BacktestDiagnosticsService:
    """回测结束后的诊断总服务，编排单次诊断与多起始点复跑诊断。"""

    def __init__(self, *, session_runner: SessionRunner | None = None) -> None:
        self.single_run = SingleRunDiagnosticsService()
        self.multi_start = MultiStartRerunDiagnosticsService(session_runner=session_runner)

    @staticmethod
    def diagnostic_output_path(ctx: RuntimeContext) -> Path:
        return diagnostic_output_path(ctx)

    @staticmethod
    def build_diagnostic_snapshot(report: dict[str, Any] | None) -> dict[str, Any]:
        return build_diagnostic_snapshot(report)

    def collect_and_write(
        self,
        ctx: RuntimeContext,
        result: RunResult | None,
        *,
        ensure_meta: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        diagnostics_cfg = getattr(ctx.cfg, "diagnostics", None)
        if not bool(getattr(diagnostics_cfg, "enabled", True)):
            return

        try:
            report = self.single_run.build_report(ctx=ctx, result=result)
            if not isinstance(report, dict):
                return
            payload = result.payload if isinstance(result, RunResult) else None
            merged_df = payload.equity_curve if isinstance(payload, CurvePayload) else None
            if merged_df is None:
                return
            report = self.multi_start.maybe_attach(
                report,
                merged_df=merged_df,
                ctx=ctx,
            )
            write_diagnostic_report(
                ctx=ctx,
                report=report,
                ensure_meta=ensure_meta,
            )
        except Exception as exc:
            ctx.logger.warning("diagnostics_build_failed run_id=%s error=%s", ctx.run_id, exc)


__all__ = [
    "BacktestDiagnosticsService",
    "MultiStartRerunDiagnosticsService",
    "SingleRunDiagnosticsService",
]
