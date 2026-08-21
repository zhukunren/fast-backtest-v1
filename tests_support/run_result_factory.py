"""Concise builders for schema-v2 run-result tests."""
from __future__ import annotations

import pandas as pd

from backtest.decisions import SignalDecisionArtifacts
from backtest.result import (
    RUN_RESULT_SCHEMA_VERSION,
    AssessPayload,
    CurveMetrics,
    CurvePayload,
    LedgerExecutionArtifacts,
    RunDiagnostics,
    RunIdentity,
    RunProvenance,
    RunRequestSummary,
    RunResult,
    SinglePayload,
    VectorizedExecutionArtifacts,
    compute_curve_metrics,
)


def signal_artifacts(**kwargs) -> SignalDecisionArtifacts:
    return SignalDecisionArtifacts(**kwargs)


def vectorized_execution(
    *,
    equity_curve: pd.DataFrame | None = None,
    trading_frames: dict[str, pd.DataFrame] | None = None,
    processed_frames: dict[str, pd.DataFrame] | None = None,
    **kwargs,
) -> VectorizedExecutionArtifacts:
    return VectorizedExecutionArtifacts(
        equity_curve=equity_curve,
        trading_frames=trading_frames or {},
        processed_frames=processed_frames or {},
        **kwargs,
    )


def ledger_execution(
    *,
    equity_curve: pd.DataFrame | None = None,
    orders_df: pd.DataFrame | None = None,
    fills_df: pd.DataFrame | None = None,
    risk_orders_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
    rejections_df: pd.DataFrame | None = None,
    daily_state_df: pd.DataFrame | None = None,
    **kwargs,
) -> LedgerExecutionArtifacts:
    return LedgerExecutionArtifacts(
        equity_curve=equity_curve,
        orders_df=orders_df if isinstance(orders_df, pd.DataFrame) else pd.DataFrame(),
        fills_df=fills_df if isinstance(fills_df, pd.DataFrame) else pd.DataFrame(),
        risk_orders_df=(
            risk_orders_df if isinstance(risk_orders_df, pd.DataFrame) else pd.DataFrame()
        ),
        risk_fills_df=(
            risk_fills_df if isinstance(risk_fills_df, pd.DataFrame) else pd.DataFrame()
        ),
        rejections_df=(
            rejections_df if isinstance(rejections_df, pd.DataFrame) else pd.DataFrame()
        ),
        daily_state_df=daily_state_df,
        **kwargs,
    )


def curve_result(
    *,
    equity_curve: pd.DataFrame | None = None,
    engine_id: str = "legacy",
    decision_artifacts: SignalDecisionArtifacts | None = None,
    execution=None,
    metrics: CurveMetrics | None = None,
    signal_stock_count: int = 0,
    run_id: str = "test-run",
    duration_sec: float | None = None,
    timings_sec: dict[str, float] | None = None,
) -> RunResult:
    resolved_metrics = metrics if metrics is not None else compute_curve_metrics(equity_curve)
    return RunResult(
        schema_version=RUN_RESULT_SCHEMA_VERSION,
        identity=RunIdentity(run_id=run_id),
        request=RunRequestSummary(
            mode="curve",
            engine_id=engine_id,
            start_time="20240101",
            end_time="20240131",
            signal_stock_count=signal_stock_count,
        ),
        payload=CurvePayload(
            engine_id=engine_id,
            decision_artifacts=decision_artifacts,
            equity_curve=equity_curve,
            metrics=resolved_metrics,
            execution=execution,
        ),
        diagnostics=RunDiagnostics(values={}),
        timings_sec=timings_sec or {},
        provenance=RunProvenance(),
        duration_sec=duration_sec,
    )


def assess_result(
    *,
    summary_df: pd.DataFrame | None = None,
    engine_id: str = "legacy",
    decision_artifacts: SignalDecisionArtifacts | None = None,
    execution=None,
    signal_stock_count: int = 0,
    run_id: str = "test-run",
) -> RunResult:
    signals = decision_artifacts if decision_artifacts is not None else SignalDecisionArtifacts()
    return RunResult(
        schema_version=RUN_RESULT_SCHEMA_VERSION,
        identity=RunIdentity(run_id=run_id),
        request=RunRequestSummary(
            mode="assess",
            engine_id=engine_id,
            start_time="20240101",
            end_time="20240131",
            signal_stock_count=signal_stock_count,
        ),
        payload=AssessPayload(
            engine_id=engine_id,
            summary_df=summary_df,
            decision_artifacts=signals,
            execution=execution,
        ),
        diagnostics=RunDiagnostics(values={}),
        timings_sec={},
        provenance=RunProvenance(),
    )


def single_result(*, frame: pd.DataFrame | None = None, run_id: str = "test-run") -> RunResult:
    return RunResult(
        schema_version=RUN_RESULT_SCHEMA_VERSION,
        identity=RunIdentity(run_id=run_id),
        request=RunRequestSummary(
            mode="single",
            engine_id=None,
            start_time="20240101",
            end_time="20240131",
            signal_stock_count=1 if isinstance(frame, pd.DataFrame) and not frame.empty else 0,
        ),
        payload=SinglePayload(frame=frame),
        diagnostics=RunDiagnostics(values={}),
        timings_sec={},
        provenance=RunProvenance(),
    )


__all__ = [
    "assess_result",
    "curve_result",
    "ledger_execution",
    "signal_artifacts",
    "single_result",
    "vectorized_execution",
]
