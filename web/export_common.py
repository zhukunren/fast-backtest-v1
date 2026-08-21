"""Shared schema and value helpers for web artifact exporters."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import pandas as pd

from backtest.result import (
    AssessPayload,
    CurvePayload,
    LedgerExecutionArtifacts,
    RunResult,
    VectorizedExecutionArtifacts,
)

EXECUTION_COLUMNS = [
    "日期", "组合", "股票代码", "方向", "股数", "成交价", "手续费", "印花税", "滑点",
    "手续费+印花税", "资金变动", "来源", "原仓位", "目标仓位", "风控原因", "净值风控",
    "回撤风控", "备注",
]

ORDER_COLUMNS = [
    "日期", "组合", "订单号", "股票代码", "方向", "委托股数", "成交股数", "价格",
    "状态", "原因", "来源", "价格类型", "备注",
]


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
        return 0.0 if pd.isna(number) or not math.isfinite(number) else number
    except Exception:
        return 0.0


def _execution_from_result(result: RunResult):
    payload = result.payload
    if isinstance(payload, (CurvePayload, AssessPayload)):
        return payload.execution
    return None


def _resolved_trades_df(result: RunResult) -> pd.DataFrame | None:
    execution = _execution_from_result(result)
    if not isinstance(execution, (VectorizedExecutionArtifacts, LedgerExecutionArtifacts)):
        return None
    df = execution.trade_segments_df
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df
    return execution.trade_segments_summary_df


def _sorted_return_frames(result: RunResult) -> list[tuple[str, pd.DataFrame]]:
    execution = _execution_from_result(result)
    if not isinstance(execution, (VectorizedExecutionArtifacts, LedgerExecutionArtifacts)):
        return []
    frames = execution.processed_frames
    if not isinstance(frames, Mapping):
        return []
    return [
        (str(name), frame)
        for name, frame in sorted(frames.items(), key=lambda item: str(item[0]).casefold())
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]


def _slot_label(name: str) -> str:
    return str(name).replace("df_", "").replace("_with_returns", "").replace("_", "-")


def _format_trade_date(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    parsed = pd.to_datetime(value, errors="coerce")
    return str(value or "") if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _clean_float(value: Any, *, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if pd.isna(number) or not math.isfinite(number):
        return None
    return round(number, digits) if isinstance(digits, int) else number


def _clean_int(value: Any) -> int | None:
    try:
        number = float(value)
    except Exception:
        return None
    if pd.isna(number) or not math.isfinite(number):
        return None
    rounded = round(number)
    return int(rounded) if abs(number - rounded) <= 1e-9 else int(number)


def _normalize_holding_code_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"none", "null", "nan"}:
        return ""
    return text


def _normalize_initial_asset_hint(value: Any) -> float | None:
    amount = _clean_float(value)
    return None if amount is None or float(amount) <= 0.0 else float(amount)


def _resolve_slot_initial_asset_hint(initial_asset_hint: Any, slot_count: int) -> float | None:
    total_asset = _normalize_initial_asset_hint(initial_asset_hint)
    if total_asset is None or int(slot_count) <= 0:
        return None
    return float(total_asset) / float(slot_count)


__all__ = ["EXECUTION_COLUMNS", "ORDER_COLUMNS"]
