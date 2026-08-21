"""Discriminated, schema-v2 result contracts for V1 backtests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias

import pandas as pd

from backtest.decisions import SignalDecisionArtifacts, TargetWeightDecisionArtifacts

RUN_RESULT_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class CurveMetrics:
    final_net_value: float
    max_drawdown: float
    actual_segment_trade_count: int = 0
    actual_segment_avg_trade_return: float = 0.0
    actual_segment_win_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class VectorizedExecutionArtifacts:
    equity_curve: pd.DataFrame | None
    trading_frames: Mapping[str, pd.DataFrame]
    processed_frames: Mapping[str, pd.DataFrame]
    trade_segments_df: pd.DataFrame | None = None
    trade_segments_summary_df: pd.DataFrame | None = None
    risk_control_summary_df: pd.DataFrame | None = None
    risk_control_events_df: pd.DataFrame | None = None
    cost_summary_df: pd.DataFrame | None = None
    diagnostics: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LedgerExecutionArtifacts:
    equity_curve: pd.DataFrame | None
    orders_df: pd.DataFrame
    fills_df: pd.DataFrame
    risk_orders_df: pd.DataFrame
    risk_fills_df: pd.DataFrame
    rejections_df: pd.DataFrame
    daily_state_df: pd.DataFrame | None
    daily_position_snapshots: tuple[Mapping[str, object], ...] = ()
    trading_frames: Mapping[str, pd.DataFrame] | None = None
    processed_frames: Mapping[str, pd.DataFrame] | None = None
    trade_segments_df: pd.DataFrame | None = None
    trade_segments_summary_df: pd.DataFrame | None = None
    risk_control_summary_df: pd.DataFrame | None = None
    risk_control_events_df: pd.DataFrame | None = None
    cost_summary_df: pd.DataFrame | None = None
    diagnostics: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RebalanceExecutionArtifacts:
    equity_curve: pd.DataFrame
    orders_df: pd.DataFrame
    fills_df: pd.DataFrame
    daily_state_df: pd.DataFrame
    positions_df: pd.DataFrame
    diagnostics: Mapping[str, object] | None = None


ExecutionArtifacts: TypeAlias = (
    VectorizedExecutionArtifacts | LedgerExecutionArtifacts | RebalanceExecutionArtifacts
)
DecisionArtifacts: TypeAlias = SignalDecisionArtifacts | TargetWeightDecisionArtifacts | None


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: str
    strategy_name: str | None = None
    strategy_source_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RunRequestSummary:
    mode: str
    engine_id: str | None
    start_time: str
    end_time: str
    signal_stock_count: int = 0


@dataclass(frozen=True, slots=True)
class RunDiagnostics:
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RunProvenance:
    config_hash: str | None = None
    data_hash: str | None = None
    code_hash: str | None = None


@dataclass(frozen=True, slots=True)
class CurvePayload:
    engine_id: str
    decision_artifacts: DecisionArtifacts
    equity_curve: pd.DataFrame | None
    metrics: CurveMetrics | None
    execution: ExecutionArtifacts | None


@dataclass(frozen=True, slots=True)
class AssessPayload:
    engine_id: str
    summary_df: pd.DataFrame | None
    decision_artifacts: SignalDecisionArtifacts
    execution: ExecutionArtifacts | None


@dataclass(frozen=True, slots=True)
class SinglePayload:
    frame: pd.DataFrame | None


RunPayload: TypeAlias = CurvePayload | AssessPayload | SinglePayload


@dataclass(frozen=True, slots=True)
class RunResult:
    schema_version: int
    identity: RunIdentity
    request: RunRequestSummary
    payload: RunPayload
    diagnostics: RunDiagnostics
    timings_sec: Mapping[str, float]
    provenance: RunProvenance
    duration_sec: float | None = None


def _sum_metric(df: pd.DataFrame | None, name: str) -> float:
    if df is None or df.empty or name not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[name], errors="coerce").fillna(0.0).sum())


def compute_max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    numeric = pd.to_numeric(series, errors="raise")
    # Net value is measured from initial capital 1.0, which remains the
    # starting peak when first-day fees push the first recorded point lower.
    roll_max = numeric.cummax().clip(lower=1.0)
    drawdown = (numeric - roll_max) / roll_max
    return float(abs(drawdown.min()))


def compute_curve_metrics(
    equity_curve: pd.DataFrame | None,
    *,
    trade_segments_summary_df: pd.DataFrame | None = None,
) -> CurveMetrics | None:
    if equity_curve is None or equity_curve.empty or "sum_net_value" not in equity_curve.columns:
        return None
    series = pd.to_numeric(equity_curve["sum_net_value"], errors="raise")
    return CurveMetrics(
        final_net_value=float(series.iloc[-1]),
        max_drawdown=compute_max_drawdown(series),
        actual_segment_trade_count=int(_sum_metric(trade_segments_summary_df, "实际交易段交易笔数")),
        actual_segment_avg_trade_return=_sum_metric(
            trade_segments_summary_df,
            "实际交易段平均每笔收益率",
        ),
        actual_segment_win_rate=_sum_metric(trade_segments_summary_df, "实际交易段胜率"),
    )


__all__ = [
    "AssessPayload",
    "CurveMetrics",
    "CurvePayload",
    "DecisionArtifacts",
    "ExecutionArtifacts",
    "LedgerExecutionArtifacts",
    "RUN_RESULT_SCHEMA_VERSION",
    "RebalanceExecutionArtifacts",
    "RunDiagnostics",
    "RunIdentity",
    "RunPayload",
    "RunProvenance",
    "RunRequestSummary",
    "RunResult",
    "SinglePayload",
    "VectorizedExecutionArtifacts",
    "compute_curve_metrics",
    "compute_max_drawdown",
]
