from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.reporting.diagnostics import (
    attach_multi_start_to_diagnostic_report,
    build_backtest_multi_start_summary,
    build_backtest_single_run_report,
)
from backtest.decisions import SignalDecisionArtifacts
from backtest.result import CurvePayload, RunResult
from config import AppConfig

from ..runtime import RuntimeContext
from ..session_metadata import read_session_meta_reason

SessionRunner = Callable[..., Any]


def build_multi_start_rerun_cfg(cfg: AppConfig, *, root_dir: Path) -> AppConfig:
    if not is_dataclass(cfg):
        return cfg

    updates: dict[str, Any] = {}
    if is_dataclass(getattr(cfg, "run", None)):
        updates["run"] = replace(
            cfg.run,
            clear_outputs=False,
            plot_net_value=False,
            compute_metrics=False,
            build_topn_trade_segments_report=False,
            profile_timing=False,
            quiet=True,
        )
    if is_dataclass(getattr(cfg, "paths", None)):
        updates["paths"] = replace(
            cfg.paths,
            data_root=root_dir,
            signal_data=root_dir / "signal_data",
            stock_data=root_dir / "stock_data",
            trade_data=root_dir / "trade_data",
            return_date=root_dir / "return_date",
            return_date_with_returns=root_dir / "return_date_with_returns",
            net_value_unique_date=root_dir / "net_value_unique_date",
        )
    if is_dataclass(getattr(cfg, "diagnostics", None)):
        updates["diagnostics"] = replace(
            cfg.diagnostics,
            enabled=False,
            multi_start_enabled=False,
        )
    if not updates:
        return cfg
    return replace(cfg, **updates)


def resolve_multi_start_report_kwargs(
    diagnostics_cfg,
    *,
    multi_start_enabled: bool,
) -> dict[str, Any]:
    return {
        "multi_start_enabled": bool(multi_start_enabled),
        "multi_start_step_days": int(getattr(diagnostics_cfg, "multi_start_step_days", 20)),
        "multi_start_window_days": int(getattr(diagnostics_cfg, "multi_start_window_days", 250)),
        "multi_start_max_starts": int(getattr(diagnostics_cfg, "multi_start_max_starts", 8)),
        "robust_lambda": float(getattr(diagnostics_cfg, "robust_lambda", 0.5)),
        "robust_gamma": float(getattr(diagnostics_cfg, "robust_gamma", 1.0)),
    }


def _window_session_overrides(cfg: AppConfig, *, start_time: str, end_time: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "backtest.start_time": start_time,
        "backtest.end_time": end_time,
    }
    if is_dataclass(getattr(cfg, "diagnostics", None)):
        overrides["diagnostics.enabled"] = False
        overrides["diagnostics.multi_start_enabled"] = False
    return overrides


def run_multi_start_window_metrics(
    *,
    cfg: AppConfig,
    start_time: str,
    end_time: str,
    session_runner: SessionRunner,
) -> dict[str, float]:
    outcome = session_runner(
        cfg=cfg,
        overrides=_window_session_overrides(cfg, start_time=start_time, end_time=end_time),
    )
    result = getattr(outcome, "result", None)
    if result is None:
        ctx = getattr(outcome, "ctx", None)
        reason = read_session_meta_reason(getattr(ctx, "meta", None), default="backtest_session_failed")
        raise RuntimeError(f"多起始点窗口回测失败: {reason}")
    if not isinstance(result, RunResult) or not isinstance(result.payload, CurvePayload):
        raise RuntimeError(f"多起始点窗口回测结果类型无效: {type(result).__name__}")
    payload = result.payload
    merged_df = payload.equity_curve
    if merged_df is None:
        raise RuntimeError("多起始点窗口回测结果为空")
    metrics = build_backtest_single_run_report(
        merged_df=merged_df,
        execution=payload.execution,
        decision_artifacts=(
            payload.decision_artifacts
            if isinstance(payload.decision_artifacts, SignalDecisionArtifacts)
            else None
        ),
        start_time=start_time,
        end_time=end_time,
    ).get("metrics", {})
    return metrics if isinstance(metrics, dict) else {}


def maybe_attach_multi_start_summary(
    report: dict[str, Any],
    *,
    merged_df: pd.DataFrame,
    ctx: RuntimeContext,
    window_metrics_runner: Callable[..., dict[str, float]],
) -> dict[str, Any]:
    diagnostics_cfg = getattr(ctx.cfg, "diagnostics", None)
    if not bool(getattr(diagnostics_cfg, "multi_start_enabled", False)):
        return report

    with tempfile.TemporaryDirectory(prefix="backtest_diag_", ignore_cleanup_errors=True) as tmp_dir:
        diag_cfg = build_multi_start_rerun_cfg(ctx.cfg, root_dir=Path(tmp_dir))

        def _runner(window_start: str, window_end: str) -> dict[str, float]:
            return window_metrics_runner(
                cfg=diag_cfg,
                start_time=window_start,
                end_time=window_end,
            )

        multi_start_summary = build_backtest_multi_start_summary(
            merged_df=merged_df,
            multi_start_runner=_runner,
            **resolve_multi_start_report_kwargs(
                diagnostics_cfg,
                multi_start_enabled=True,
            ),
        )
    return attach_multi_start_to_diagnostic_report(
        report,
        multi_start_summary=multi_start_summary,
    )


class MultiStartRerunDiagnosticsService:
    """只负责多起始点窗口复跑与稳健性摘要。"""

    def __init__(self, *, session_runner: SessionRunner | None = None) -> None:
        self._session_runner = session_runner

    @staticmethod
    def build_rerun_cfg(cfg: AppConfig, *, root_dir: Path) -> AppConfig:
        return build_multi_start_rerun_cfg(cfg, root_dir=root_dir)

    def run_window_metrics(
        self,
        *,
        cfg: AppConfig,
        start_time: str,
        end_time: str,
    ) -> dict[str, float]:
        if self._session_runner is None:
            raise RuntimeError("multi-start diagnostics requires an injected session runner")
        return run_multi_start_window_metrics(
            cfg=cfg,
            start_time=start_time,
            end_time=end_time,
            session_runner=self._session_runner,
        )

    def maybe_attach(
        self,
        report: dict[str, Any],
        *,
        merged_df: pd.DataFrame,
        ctx: RuntimeContext,
    ) -> dict[str, Any]:
        return maybe_attach_multi_start_summary(
            report,
            merged_df=merged_df,
            ctx=ctx,
            window_metrics_runner=self.run_window_metrics,
        )


__all__ = [
    "MultiStartRerunDiagnosticsService",
    "build_multi_start_rerun_cfg",
    "maybe_attach_multi_start_summary",
    "resolve_multi_start_report_kwargs",
    "run_multi_start_window_metrics",
]
