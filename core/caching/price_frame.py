"""Shared low-level helpers for price-frame cache implementations."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError
from core.schema import PRICE_NORMALIZED_ATTR


def normalize_price_cache_code(value: object) -> str:
    return str(value or "").strip().upper()


def normalized_price_cache_codes(values: Iterable[object]) -> tuple[str, ...]:
    codes = {code for value in values for code in [normalize_price_cache_code(value)] if code}
    return tuple(sorted(codes, key=str.casefold))


def is_price_frame_normalized(df: pd.DataFrame) -> bool:
    return bool(df.attrs.get(PRICE_NORMALIZED_ATTR, False))


def mark_price_frame_normalized(df: pd.DataFrame) -> pd.DataFrame:
    df.attrs[PRICE_NORMALIZED_ATTR] = True
    return df


def _parse_yyyymmdd_parts(value: object) -> tuple[int, int, int] | None:
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return int(text[:4]), int(text[4:6]), int(text[6:8])


def parse_price_cache_trade_day(value: object) -> pd.Timestamp:
    parts = _parse_yyyymmdd_parts(value)
    if parts is not None:
        year, month, day = parts
        return pd.Timestamp(year=year, month=month, day=day)
    return pd.to_datetime(value, format="%Y%m%d", errors="raise").normalize()


def parse_price_cache_trade_day_np(value: object) -> np.datetime64:
    parts = _parse_yyyymmdd_parts(value)
    if parts is not None:
        year, month, day = parts
        return np.datetime64(f"{year:04d}-{month:02d}-{day:02d}", "ns")
    return parse_price_cache_trade_day(value).to_datetime64()


def coerce_price_cache_trade_dates(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.normalize()
    return pd.to_datetime(series, errors="raise").dt.normalize()


def _can_use_ordered_datetime_slice(trade_dates: pd.Series) -> bool:
    return bool(
        pd.api.types.is_datetime64_any_dtype(trade_dates)
        and trade_dates.is_monotonic_increasing
    )


def _slice_ordered_datetime_frame(
    df: pd.DataFrame,
    trade_dates: pd.Series,
    *,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    values = trade_dates.to_numpy(dtype="datetime64[ns]", copy=False)
    start_ts = parse_price_cache_trade_day_np(start_date)
    end_ts = parse_price_cache_trade_day_np(end_date)
    left = int(values.searchsorted(start_ts, side="left"))
    right = int(values.searchsorted(end_ts, side="right"))
    return df.iloc[left:right].reset_index(drop=True)


def slice_price_frame_by_date(
    df: pd.DataFrame,
    *,
    stock_code: str,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    if COL_TRADE_DATE not in df.columns:
        raise DataValidationError(f"{stock_code}: price_cache 缺少 trade_date 列。")
    trade_dates_raw = df[COL_TRADE_DATE]
    if _can_use_ordered_datetime_slice(trade_dates_raw):
        return _slice_ordered_datetime_frame(
            df,
            trade_dates_raw,
            start_date=start_date,
            end_date=end_date,
        )
    start_ts = parse_price_cache_trade_day(start_date)
    end_ts = parse_price_cache_trade_day(end_date)
    trade_dates = coerce_price_cache_trade_dates(trade_dates_raw)
    mask = (trade_dates >= start_ts) & (trade_dates <= end_ts)
    return df.loc[mask].reset_index(drop=True)


__all__ = [
    "coerce_price_cache_trade_dates",
    "is_price_frame_normalized",
    "mark_price_frame_normalized",
    "normalize_price_cache_code",
    "normalized_price_cache_codes",
    "parse_price_cache_trade_day",
    "parse_price_cache_trade_day_np",
    "slice_price_frame_by_date",
]
