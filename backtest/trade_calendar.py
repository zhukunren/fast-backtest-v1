"""
Trade-calendar normalization and clipping helpers.
"""
from __future__ import annotations

import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError


def normalize_trade_days(
    days: list[pd.Timestamp] | pd.DataFrame | None,
    *,
    name: str = "trade_days",
) -> list[pd.Timestamp]:
    return normalize_trade_days_index(days, name=name).to_list()


def normalize_trade_days_index(
    days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame | None,
    *,
    name: str = "trade_days",
) -> pd.DatetimeIndex:
    if days is None:
        raise DataValidationError(f"{name} 不能为空。")
    if isinstance(days, pd.DatetimeIndex):
        idx = days.normalize()
        idx = pd.DatetimeIndex(sorted(idx.unique()))
        if idx.empty:
            raise DataValidationError(f"{name} 为空。")
        return idx
    if isinstance(days, pd.DataFrame):
        if COL_TRADE_DATE in days.columns:
            days = days[COL_TRADE_DATE].to_list()
        elif "cal_date" in days.columns:
            days = days["cal_date"].to_list()
        else:
            raise DataValidationError(f"{name} DataFrame 缺少 trade_date/cal_date 列。")
    idx = pd.DatetimeIndex(pd.to_datetime([d for d in days if pd.notna(d)], errors="raise")).normalize()
    idx = pd.DatetimeIndex(sorted(idx.unique()))
    if idx.empty:
        raise DataValidationError(f"{name} 为空。")
    return idx


def extract_frame_date_range(
    df: pd.DataFrame | None,
    *,
    date_col: str = COL_TRADE_DATE,
    name: str = "signal_df",
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    if df is None or df.empty:
        return None
    if date_col not in df.columns:
        raise DataValidationError(f"{name} 缺少 {date_col} 列。")
    idx = pd.DatetimeIndex(pd.to_datetime(df[date_col], errors="raise")).normalize()
    idx = pd.DatetimeIndex(sorted(idx.unique()))
    if idx.empty:
        raise DataValidationError(f"{name}.{date_col} 为空。")
    return idx[0], idx[-1]


def clip_trade_days_to_range(
    days: list[pd.Timestamp],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    name: str = "trade_days",
) -> list[pd.Timestamp]:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    clipped = [d for d in days if start <= pd.Timestamp(d).normalize() <= end]
    if not clipped:
        raise DataValidationError(
            f"{name} 按日期范围裁剪后为空：range={start.strftime('%Y-%m-%d')}~{end.strftime('%Y-%m-%d')}"
        )
    return clipped


__all__ = [
    "clip_trade_days_to_range",
    "extract_frame_date_range",
    "normalize_trade_days",
    "normalize_trade_days_index",
]
