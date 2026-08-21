from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import pandas as pd

from core.constants import (
    COL_TRADE_DATE,
    COL_STOCK_CODE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    DIRECTION_NONE,
    VALID_DIRECTIONS,
)
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized


# ---------------------------------------------------------------------------
# DataFrame-level normalizers
# ---------------------------------------------------------------------------

def normalize_and_sort_df(
    df: pd.DataFrame,
    *,
    date_col: str = COL_TRADE_DATE,
    sort_cols: Sequence[str] | None = None,
    sort_asc: Sequence[bool] | None = None,
) -> pd.DataFrame:
    """统一规范化 + 稳定排序。"""
    out = ensure_trade_date_normalized(df.copy(), col=date_col)
    if sort_cols is None:
        sort_cols = [date_col]
    if sort_asc is None:
        sort_asc = [True] * len(sort_cols)
    out = out.sort_values(list(sort_cols), ascending=list(sort_asc), kind="mergesort")
    return out.reset_index(drop=True)


def dedup_by_trade_date(
    df: pd.DataFrame,
    *,
    date_col: str = COL_TRADE_DATE,
    keep: str = "last",
    sort_cols: Sequence[str] | None = None,
    sort_asc: Sequence[bool] | None = None,
    presorted: bool = False,
    assume_normalized: bool = False,
) -> pd.DataFrame:
    """按 trade_date 去重（默认保留最后一条）。"""
    if presorted:
        out = df if assume_normalized else ensure_trade_date_normalized(df, col=date_col)
    else:
        out = normalize_and_sort_df(df, date_col=date_col, sort_cols=sort_cols, sort_asc=sort_asc)
    out = out.drop_duplicates(subset=[date_col], keep=keep)
    return out.reset_index(drop=True)


def dedup_by_date_code(
    df: pd.DataFrame,
    *,
    date_col: str = COL_TRADE_DATE,
    code_col: str = COL_STOCK_CODE,
    keep: str = "first",
    sort_cols: Sequence[str] | None = None,
    sort_asc: Sequence[bool] | None = None,
    presorted: bool = False,
    assume_normalized: bool = False,
) -> pd.DataFrame:
    """按 trade_date + stock_code 去重（默认保留第一条）。"""
    if sort_cols is None:
        sort_cols = [date_col, code_col]
        sort_asc = [True, True]
    if presorted:
        out = df if assume_normalized else ensure_trade_date_normalized(df, col=date_col)
    else:
        out = normalize_and_sort_df(df, date_col=date_col, sort_cols=sort_cols, sort_asc=sort_asc)
    out = out.drop_duplicates(subset=[date_col, code_col], keep=keep)
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Value-level normalizers & predicates  (originally core/validators.py)
# ---------------------------------------------------------------------------

def normalize_direction(value: Any, *, strict: bool = False) -> str:
    """
    统一 direction 取值：
    - None/NaN/NaT => ""
    - 其他 => lower/strip 后仅允许 buy/sell/""
    """
    if value is None:
        return DIRECTION_NONE

    try:
        if pd.isna(value):
            return DIRECTION_NONE
    except Exception:
        pass

    try:
        if isinstance(value, float) and math.isnan(value):
            return DIRECTION_NONE
    except Exception:
        pass

    s = str(value).strip().lower()
    if s in VALID_DIRECTIONS:
        return s
    if strict:
        raise DataValidationError(f"direction invalid: {value!r}")
    return DIRECTION_NONE


def normalize_direction_series(series: pd.Series) -> pd.Series:
    """批量标准化 direction 列为小写字符串（不做严格校验）。"""
    if (
        series.dtype == object
        and not series.hasnans
        and set(series.unique()).issubset(VALID_DIRECTIONS)
    ):
        return series
    return series.fillna("").astype(str).str.strip().str.lower()


def is_buy_signal(value: Any) -> bool:
    return normalize_direction(value) == DIRECTION_BUY


def is_sell_signal(value: Any) -> bool:
    return normalize_direction(value) == DIRECTION_SELL


def is_empty_value(value: Any) -> bool:
    """统一空值判定：None / NaN / 空字符串。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    try:
        if isinstance(value, float) and math.isnan(value):
            return True
    except Exception:
        pass
    return False


def has_nans(df: pd.DataFrame, cols: Iterable[str] | None = None) -> bool:
    """统一 NaN 检测（可选指定列）。"""
    if df is None or df.empty:
        return False
    if cols:
        return df.loc[:, list(cols)].isna().any().any()
    return df.isna().any().any()
