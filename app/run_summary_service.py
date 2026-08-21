from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from backtest.result import AssessPayload, CurvePayload, RunResult

from .run_summary_support import (
    build_assess_summary_lines,
    build_curve_summary_lines,
    build_market_state_summary_line,
    build_result_meta_payload,
    build_universe_summary_line,
)
from .runtime import RuntimeContext


class RunSummaryService:
    """Render final console summaries and attach result metadata."""

    def print_final_summary(
        self,
        ctx: RuntimeContext,
        result: object | None,
        *,
        ensure_meta: Callable[[], dict] | None = None,
    ) -> None:
        if result is None:
            raise TypeError("print_final_summary 需要 RunResult。")
        if isinstance(result, RunResult):
            self.print_portfolio_run_summary(ctx, result, ensure_meta=ensure_meta)
            return
        raise TypeError("print_final_summary 需要 RunResult。")

    def print_portfolio_run_summary(
        self,
        ctx: RuntimeContext,
        result: RunResult | None,
        *,
        ensure_meta: Callable[[], dict] | None = None,
    ) -> None:
        if not isinstance(result, RunResult):
            raise TypeError("print_final_summary 需要显式 RunResult。")
        run_mode = result.request.mode

        ctx.warn_collector.emit_summary(ctx.raw_print)
        ctx.raw_print("\n" + "=" * 50)
        ctx.raw_print("全部任务完成")
        ctx.raw_print(f"回测周期: {ctx.cfg.backtest.start_time} ~ {ctx.cfg.backtest.end_time}")
        ctx.raw_print(build_universe_summary_line(ctx.cfg))
        ctx.raw_print(f"总耗时: {time.time() - ctx.start_ts:.2f} 秒")

        if run_mode == "assess":
            payload = result.payload
            self.print_assess_summary(
                ctx,
                payload.summary_df if isinstance(payload, AssessPayload) else None,
            )
            return

        if not isinstance(result.payload, CurvePayload) or result.payload.metrics is None:
            return

        market_state_line = build_market_state_summary_line(result, cfg=ctx.cfg)
        if market_state_line:
            ctx.raw_print(market_state_line)

        diagnostic_path = getattr(ctx, "diagnostic_path", None)
        diagnostic_report = getattr(ctx, "diagnostic_report", None) or {}
        for line in build_curve_summary_lines(
            result=result,
            diagnostic_path=diagnostic_path,
            diagnostic_report=diagnostic_report,
        ):
            ctx.raw_print(line)

        meta = ctx.meta or (ensure_meta() if ensure_meta is not None else {})
        result_payload = build_result_meta_payload(result)
        if result_payload is not None:
            meta["result"] = result_payload
            ctx.meta = meta

    def print_assess_summary(self, ctx: RuntimeContext, summary_df: pd.DataFrame | None) -> None:
        for line in build_assess_summary_lines(summary_df):
            ctx.raw_print(line)
