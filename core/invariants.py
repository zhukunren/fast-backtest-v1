from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pandas.api.extensions import ExtensionArray

from core.constants import FLOAT_TOLERANCE, VALID_DIRECTIONS, COL_TRADE_DATE, SIGNAL_COL_DIRECTION
from core.exceptions import DataValidationError


_PRICE_KEY_COLUMNS = ("trade_date", "stock_code")
_PRICE_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")
PANEL_SORTED_ATTR = "_sorted_stock_code_trade_date"


@dataclass(frozen=True, slots=True)
class PriceFrameInvariants:
    """Compact immutable snapshot of engine-owned price columns."""

    row_count: int
    columns: tuple[str, ...]
    dtypes: tuple[object, ...]
    values: tuple[ExtensionArray, ...]


def capture_price_frame_invariants(
    df: pd.DataFrame,
    *,
    include_price_values: bool = True,
    error_cls: type[Exception] = DataValidationError,
) -> PriceFrameInvariants:
    """Capture engine-owned row keys and OHLCV before strategy code runs."""
    required = set(_PRICE_KEY_COLUMNS)
    missing = sorted(required - set(getattr(df, "columns", ())), key=str.casefold)
    if missing:
        raise error_cls(f"price frame missing invariant columns: {missing}")
    columns = list(_PRICE_KEY_COLUMNS)
    if include_price_values:
        columns.extend(col for col in _PRICE_VALUE_COLUMNS if col in df.columns)
    return PriceFrameInvariants(
        row_count=len(df),
        columns=tuple(columns),
        dtypes=tuple(df[col].dtype for col in columns),
        values=tuple(df[col].array.copy() for col in columns),
    )


def assert_price_frame_preserved(
    expected: PriceFrameInvariants,
    actual: pd.DataFrame,
    *,
    source: str = "strategy_hook",
    allow_price_projection: bool = False,
    error_cls: type[Exception] = DataValidationError,
) -> None:
    """Require strategy hooks to preserve canonical rows and returned source OHLCV."""
    if not isinstance(actual, pd.DataFrame):
        raise error_cls(f"{source} must return a pandas DataFrame")
    if len(actual) != expected.row_count:
        raise error_cls(
            f"{source} must preserve input row count "
            f"({expected.row_count} expected, {len(actual)} returned)"
        )

    required = [
        col
        for col in expected.columns
        if not allow_price_projection or col in _PRICE_KEY_COLUMNS
    ]
    missing = [col for col in required if col not in actual.columns]
    if missing:
        raise error_cls(f"{source} must preserve input columns: {missing}")

    compared_columns = [col for col in expected.columns if col in actual.columns]
    expected_by_column = dict(
        zip(expected.columns, zip(expected.dtypes, expected.values, strict=True), strict=True)
    )
    preserved = {
        col: actual[col].dtype == expected_by_column[col][0]
        and actual[col].array.equals(expected_by_column[col][1])
        for col in compared_columns
    }
    if not all(preserved[col] for col in _PRICE_KEY_COLUMNS):
        raise error_cls(f"{source} must preserve input trade_date/stock_code rows and order")

    changed = [
        col
        for col in expected.columns
        if col not in _PRICE_KEY_COLUMNS and col in actual.columns and not preserved[col]
    ]
    if changed:
        raise error_cls(f"{source} must preserve input OHLCV values: changed {changed}")


def assert_sorted_unique(
    df: pd.DataFrame,
    by: str | list[str],
    *,
    name: str = "index",
) -> None:
    """断言按给定键升序且唯一。"""
    if isinstance(by, str):
        series = df[by]
        if not series.is_monotonic_increasing:
            raise DataValidationError(f"{name} 未升序排序")
        if series.duplicated().any():
            dup = series[series.duplicated()].unique()[:5]
            raise DataValidationError(f"{name} 存在重复: {dup}")
    else:
        df_sorted = df.sort_values(by=by, kind="mergesort")
        if not df_sorted.index.equals(df.index):
            raise DataValidationError(f"{name} 未按 {by} 升序排序")
        if df.duplicated(subset=by).any():
            dup = df.loc[df.duplicated(subset=by), by].head(5)
            raise DataValidationError(f"{name} 在 {by} 上存在重复: {dup.to_dict(orient='records')}")


def ensure_trade_date_normalized(
    df: pd.DataFrame,
    col: str = COL_TRADE_DATE,
    *,
    require_unique: bool = False,
) -> pd.DataFrame:
    """规范化 trade_date 列为日期且无 NaT。可选校验唯一性。"""
    if col not in df.columns:
        return df
    series = df[col]
    if pd.api.types.is_datetime64_any_dtype(series):
        # Fast path: already datetime64 — skip copy and conversion.
        # Trade dates in this pipeline are usually day-level after initial
        # parsing. Avoid constructing a full normalized Series unless an
        # intraday timestamp is actually present.
        if bool(getattr(series, "hasnans", False)):
            bad = df.loc[series.isna(), [col]].head(5).to_dict(orient="records")
            raise DataValidationError(f"{col} 存在 NaT：{bad}")
        if len(series) > 0:
            tz = getattr(series.dt, "tz", None)
            if tz is None:
                values = series.to_numpy(dtype="datetime64[ns]", copy=False).astype("int64", copy=False)
                has_intraday_time = bool((values % 86_400_000_000_000 != 0).any())
            else:
                normalized = series.dt.normalize()
                has_intraday_time = not bool(series.eq(normalized).all())
            if has_intraday_time:
                # Any intraday timestamp must be normalized before date joins.
                out = df.copy()
                out[col] = series.dt.normalize()
                if require_unique and out[col].duplicated().any():
                    dup = out.loc[out[col].duplicated(), col].unique()[:5]
                    raise DataValidationError(f"{col} 存在重复：{dup}")
                return out
        if require_unique and series.duplicated().any():
            dup = df.loc[series.duplicated(), col].unique()[:5]
            raise DataValidationError(f"{col} 存在重复：{dup}")
        return df
    # Slow path: non-datetime dtype — full conversion needed
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="raise").dt.normalize()
    if bool(getattr(out[col], "hasnans", False)):
        bad = out.loc[out[col].isna(), [col]].head(5).to_dict(orient="records")
        raise DataValidationError(f"{col} 存在 NaT：{bad}")
    if require_unique and out[col].duplicated().any():
        dup = out.loc[out[col].duplicated(), col].unique()[:5]
        raise DataValidationError(f"{col} 存在重复：{dup}")
    return out


def assert_sorted_unique_index(df: pd.DataFrame, name: str = "trade_date") -> None:
    """断言索引已按升序排序且无重复。"""
    if not df.index.is_monotonic_increasing:
        raise DataValidationError(f"{name} 索引未排序：{df.index[:3]} ... {df.index[-3:]}")
    if df.index.has_duplicates:
        dup = df.index[df.index.duplicated()].unique()[:5]
        raise DataValidationError(f"{name} 索引存在重复：{dup}")


def assert_positive_float(val, *, name: str, allow_zero: bool = False) -> float:
    """检查浮点为有限正数，允许容差。"""
    f = float(val)
    if not np.isfinite(f):
        raise DataValidationError(f"{name} 非有限数")
    if allow_zero:
        if f < -FLOAT_TOLERANCE:
            raise DataValidationError(f"{name} 不能为负")
    else:
        if f <= FLOAT_TOLERANCE:
            raise DataValidationError(f"{name} 必须为正")
    return f


def assert_direction_values(df: pd.DataFrame, col: str = SIGNAL_COL_DIRECTION) -> None:
    """断言 direction 列仅包含允许取值。"""
    if col not in df.columns:
        raise DataValidationError(f"{col} 列缺失，无法校验方向。")
    values = set(df[col].dropna().astype(str).str.lower().unique().tolist())
    invalid = values - VALID_DIRECTIONS
    if invalid:
        raise DataValidationError(f"{col} 存在非法取值: {sorted(invalid)}，允许 {sorted(VALID_DIRECTIONS)}")
