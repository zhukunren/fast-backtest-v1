"""
Shared web-side backtest execution helpers.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.backtest_session_service import BacktestSessionOutcome, run_backtest_session_with_overrides
from app.session_metadata import read_session_meta_reason, read_session_meta_status
from backtest.result import CurvePayload, RunResult

from .exporter import export_run
from .log_service import log_event
from .runtime_jit import install_shared_jit_dispatchers


@dataclass(frozen=True, slots=True)
class WebExecutionResult:
    status: str
    error: str | None
    meta: dict[str, Any]
    outcome: BacktestSessionOutcome | None
    run_result: RunResult | None
    merged_df: pd.DataFrame | None


def execute_web_backtest(
    *,
    base_cfg,
    overrides: Mapping[str, Any],
    stock_code_list: tuple[str, ...] | None = None,
) -> WebExecutionResult:
    try:
        install_shared_jit_dispatchers()
        outcome = run_backtest_session_with_overrides(
            base_cfg,
            overrides,
            raw_print=lambda *args, **kwargs: None,
            print_summary=False,
            stock_code_list=stock_code_list,
        )
        run_result = outcome.result
        meta = dict(outcome.ctx.meta or {})
        if run_result is None:
            reason = read_session_meta_reason(meta)
            status = "config_failed" if reason.startswith("config_validation") else "failed"
            return WebExecutionResult(
                status=status,
                error=reason,
                meta=meta,
                outcome=outcome,
                run_result=None,
                merged_df=None,
            )

        if not isinstance(run_result, RunResult):
            return WebExecutionResult(
                status="failed",
                error=f"unsupported run result type: {type(run_result).__name__}",
                meta=meta,
                outcome=outcome,
                run_result=None,
                merged_df=None,
            )

        try:
            export_run(
                outcome.ctx.run_id,
                run_result=run_result,
                cfg=getattr(outcome.ctx, "cfg", None),
            )
        except Exception as exc:
            log_event(
                "WARNING",
                "backtest_execution",
                "Web backtest export failed",
                run_id=getattr(outcome.ctx, "run_id", ""),
                error=str(exc),
                exception_type=type(exc).__name__,
            )
            pass

        payload = run_result.payload
        merged_df = payload.equity_curve if isinstance(payload, CurvePayload) else None
        if merged_df is not None:
            status = "completed"
            error = None
        else:
            meta_status = read_session_meta_status(meta).strip().lower()
            if meta_status == "failed":
                status = "failed"
                error = read_session_meta_reason(meta)
            else:
                status = "no_signal"
                error = None

        return WebExecutionResult(
            status=status,
            error=error,
            meta=meta,
            outcome=outcome,
            run_result=run_result,
            merged_df=merged_df,
        )
    except Exception as exc:
        log_event(
            "ERROR",
            "backtest_execution",
            "Web backtest execution failed",
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return WebExecutionResult(
            status="failed",
            error=str(exc),
            meta={},
            outcome=None,
            run_result=None,
            merged_df=None,
        )
__all__ = ["WebExecutionResult", "execute_web_backtest"]
