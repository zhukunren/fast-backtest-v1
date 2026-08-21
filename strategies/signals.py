"""Canonical strategy-facing signal and price-frame guardrails."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_NONE,
    DIRECTION_SELL,
    SIGNAL_COL_BUY,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
    SIGNAL_COL_SELL_RAW,
)
from core.dataframe_utils import (
    normalize_columns,
    parse_trade_date,
    validate_no_duplicate_dates,
)

from .validation import validate_sort_spec_columns


_NUMERIC_COLS = ("open", "high", "low", "close", "volume")


def _coerce_signal_value(
    value: object,
    *,
    column_name: str,
    error_cls: type[Exception],
) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)) and value in (0, 1):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        if np.isnan(value):
            return False
        if np.isfinite(value) and value in (0.0, 1.0):
            return bool(value)
    raise error_cls(
        f"{column_name} must contain only boolean-compatible values "
        "(bool, 0/1, or null)"
    )


def coerce_signal_bool_array(
    series: pd.Series,
    *,
    column_name: str,
    expected_index: pd.Index | None = None,
    copy: bool = False,
    error_cls: type[Exception] = ValueError,
) -> np.ndarray:
    """Convert a signal Series without treating arbitrary values as true."""
    if not isinstance(series, pd.Series):
        raise error_cls(f"{column_name} must be a pandas Series")
    if expected_index is not None and not series.index.equals(expected_index):
        raise error_cls(
            f"{column_name} index must exactly match the strategy DataFrame index"
        )
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.fillna(False).to_numpy(dtype=bool, copy=copy)

    values = [
        _coerce_signal_value(
            value,
            column_name=column_name,
            error_cls=error_cls,
        )
        for value in series.to_numpy(copy=False)
    ]
    array = np.asarray(values, dtype=bool)
    return array.copy() if copy else array


def preprocess_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize and validate one single-stock daily price frame."""
    out = df
    cols = out.columns
    cols_str = cols.astype(str)
    cols_lower = cols_str.str.lower()
    if (
        not cols_lower.equals(cols_str)
        or "ts_code" in cols
        or ("vol" in cols and "volume" not in cols)
    ):
        out = normalize_columns(out.copy())

    if COL_TRADE_DATE in out.columns and pd.api.types.is_datetime64_any_dtype(
        out[COL_TRADE_DATE]
    ):
        trade_dates = out[COL_TRADE_DATE]
        if trade_dates.isna().any():
            bad = out.loc[trade_dates.isna()].head(5).to_dict(orient="records")
            raise ValueError(f"{COL_TRADE_DATE} has invalid dates: {bad}")
    else:
        if out is df:
            out = out.copy()
        out = parse_trade_date(out)
        trade_dates = out[COL_TRADE_DATE]

    if not trade_dates.is_monotonic_increasing:
        out = out.sort_values(by=COL_TRADE_DATE, kind="mergesort").reset_index(
            drop=True
        )
    elif not isinstance(out.index, pd.RangeIndex):
        out = out.reset_index(drop=True)

    validate_no_duplicate_dates(out)

    missing = [
        column
        for column in (*_NUMERIC_COLS, COL_STOCK_CODE)
        if column not in out.columns
    ]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    for column in _NUMERIC_COLS:
        series = out[column]
        if not pd.api.types.is_numeric_dtype(series):
            if out is df:
                out = out.copy()
            out[column] = pd.to_numeric(series, errors="raise")
            series = out[column]
        if series.isna().any():
            bad = (
                out.loc[series.isna(), [COL_TRADE_DATE, COL_STOCK_CODE]]
                .head(5)
                .to_dict(orient="records")
            )
            raise ValueError(f"{column} contains NaN: {bad}")

    code_series = out[COL_STOCK_CODE]
    if code_series.isna().any():
        bad = out.loc[
            code_series.isna(),
            [COL_TRADE_DATE, COL_STOCK_CODE],
        ].head(5)
        raise ValueError(
            f"stock_code contains empty values: {bad.to_dict(orient='records')}"
        )
    empty_codes = code_series.astype(str).str.strip().eq("")
    if empty_codes.any():
        bad = out.loc[
            empty_codes,
            [COL_TRADE_DATE, COL_STOCK_CODE],
        ].head(5)
        raise ValueError(
            f"stock_code contains empty values: {bad.to_dict(orient='records')}"
        )

    return out


def finalize_signals(
    df: pd.DataFrame,
    buy_cond: pd.Series,
    sell_cond: pd.Series,
) -> pd.DataFrame:
    """Write raw strategy intent; tradability and execution remain engine-owned."""
    buy_raw = coerce_signal_bool_array(
        buy_cond,
        column_name="buy_cond",
        expected_index=df.index,
    )
    sell_raw = coerce_signal_bool_array(
        sell_cond,
        column_name="sell_cond",
        expected_index=df.index,
    )

    df[SIGNAL_COL_BUY_RAW] = buy_raw
    df[SIGNAL_COL_SELL_RAW] = sell_raw
    df[SIGNAL_COL_BUY] = buy_raw
    df[SIGNAL_COL_SELL] = sell_raw
    direction = np.full(len(df), DIRECTION_NONE, dtype=object)
    direction[buy_raw] = DIRECTION_BUY
    direction[sell_raw] = DIRECTION_SELL
    df[SIGNAL_COL_DIRECTION] = direction
    return df


__all__ = [
    "coerce_signal_bool_array",
    "finalize_signals",
    "preprocess_ohlcv",
    "validate_sort_spec_columns",
]
