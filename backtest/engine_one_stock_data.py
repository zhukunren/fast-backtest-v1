"""
Data/window/output helpers for the single-stock backtest pipeline.
"""
from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

import pandas as pd

from core.constants import COL_TRADE_DATE, DEFAULT_TRADE_CALENDAR_MISSING_WARN_RATIO
from core.exceptions import DataValidationError, PriceDataError
from core.logging import log_info
from core.price_data_errors import is_expected_missing_price_data_error
from core.caching.price_frame import slice_price_frame_by_date
from data import compute_trade_date_offset, get_trade_days_cached, read_ohlcv_from_mysql


def _is_expected_missing_data_error(exc: Exception) -> bool:
    return is_expected_missing_price_data_error(exc)


def _is_probable_index_code(stock_code: str) -> bool:
    code = str(stock_code or "").strip().upper()
    if "." not in code:
        return False
    raw, market = code.split(".", 1)
    if len(raw) != 6 or not raw.isdigit():
        return False
    if market == "SH" and raw.startswith("000"):
        return True
    if market == "SZ" and raw.startswith("399"):
        return True
    return False


def _candidate_symbol_types(stock_code: str) -> list[str]:
    symbol_types = ["stock"]
    if _is_probable_index_code(stock_code):
        symbol_types.append("index")
    return symbol_types


def _check_trade_calendar(
    df: pd.DataFrame,
    *,
    stock_code: str,
    start_eval: pd.Timestamp | None,
    end_eval: pd.Timestamp | None,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    cfg,
    calendar_cache=None,
) -> None:
    if not bool(cfg.run.check_trade_calendar):
        return
    if bool(cfg.run.quiet):
        return
    try:
        if COL_TRADE_DATE not in df.columns:
            raise DataValidationError(f"缺少 {COL_TRADE_DATE} 列")
        df_days = _coerce_runtime_trade_dates(df[COL_TRADE_DATE])
        if df_days.isna().any():
            raise DataValidationError(f"{COL_TRADE_DATE} 存在 NaT")

        check_start = start_eval if start_eval is not None else start_ts
        check_end = end_eval if end_eval is not None else end_ts
        trade_days = get_trade_days_cached(
            check_start.strftime("%Y%m%d"),
            check_end.strftime("%Y%m%d"),
            exchange=cfg.tushare.exchange or "",
            cfg=cfg,
            calendar_cache=calendar_cache,
        )
        if trade_days:
            actual_days = set(df_days.tolist())
            missing = [d for d in trade_days if d not in actual_days]
            missing_ratio = len(missing) / len(trade_days)
            warn_ratio = _resolve_trade_calendar_warn_ratio(cfg)
            if missing_ratio >= warn_ratio:
                sample = [d.strftime("%Y%m%d") for d in missing[:5]]
                log_info(
                    f"[CALENDAR_GAP] stock={stock_code} expected={len(trade_days)} "
                    f"missing={len(missing)} ratio={missing_ratio:.4f} sample={sample}"
                )
    except Exception as exc:
        # This check establishes the runtime trading-day axis.  Continuing
        # without it can silently validate a price series against the wrong
        # calendar, so retain the diagnostic but fail the run.
        log_info(f"[CALENDAR_GAP] stock={stock_code} check_failed={exc}")
        raise


def _resolve_trade_calendar_warn_ratio(cfg) -> float:
    """
    Keep the historical default warn threshold unless an explicit non-empty
    override is provided by runtime config parsing.
    """
    raw_ratio = getattr(cfg.run, "trade_calendar_missing_warn_ratio", None)
    return float(raw_ratio or DEFAULT_TRADE_CALENDAR_MISSING_WARN_RATIO)


def _collect_warmup_shortfall(
    df: pd.DataFrame,
    *,
    stock_code: str,
    start_eval: pd.Timestamp | None,
    offset_days: int,
) -> dict | None:
    if offset_days <= 0 or COL_TRADE_DATE not in df.columns or start_eval is None:
        return None
    td_series = _coerce_runtime_trade_dates(df[COL_TRADE_DATE])
    if td_series.isna().any():
        bad = df.loc[td_series.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"{stock_code}: {COL_TRADE_DATE} 存在 NaT（示例：{bad}）")
    pre_count = int((td_series < start_eval).sum())
    if pre_count >= offset_days:
        return None
    actual_start = td_series.min()
    actual_end = td_series.max()
    actual_start_str = actual_start.strftime("%Y%m%d") if pd.notna(actual_start) else ""
    actual_end_str = actual_end.strftime("%Y%m%d") if pd.notna(actual_end) else ""
    return {
        "stock_code": stock_code,
        "pre_count": int(pre_count),
        "offset_days": int(offset_days),
        "actual_start": actual_start_str,
        "actual_end": actual_end_str,
    }


def _parse_trade_day_yyyymmdd(value: str) -> pd.Timestamp:
    return pd.to_datetime(value, format="%Y%m%d", errors="raise").normalize()


def _coerce_runtime_trade_dates(td: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(td):
        return td
    return pd.to_datetime(td, errors="raise").dt.normalize()


def _resolve_trade_window(
    *,
    cfg,
    stock_code: str,
    start_time: str,
    end_time: str,
    offset_days: int,
    offset_clamp_log: bool | None = None,
    calendar_cache=None,
) -> tuple[str, str, pd.Timestamp | None, pd.Timestamp | None]:
    start_eval = None
    end_eval = None
    if offset_days > 0:
        start_eval = _parse_trade_day_yyyymmdd(start_time)
        end_eval = _parse_trade_day_yyyymmdd(end_time)

    start_time_read = start_time
    end_time_read = end_time
    if offset_days > 0:
        start_time_read, end_time_read = compute_trade_date_offset(
            start_time,
            end_time,
            offset_days,
            cfg=cfg,
            exchange=cfg.tushare.exchange,
            cal_start=cfg.tushare.trade_cal_start,
            cal_end=cfg.tushare.trade_cal_end,
            allow_clamp=True,
            stock_code=stock_code,
            offset_clamp_log=offset_clamp_log,
            calendar_cache=calendar_cache,
        )

    return start_time_read, end_time_read, start_eval, end_eval


def _slice_mapping_price_cache_frame(
    cached_df: pd.DataFrame,
    *,
    stock_code: str,
    start_time_read: str,
    end_time_read: str,
) -> pd.DataFrame:
    return slice_price_frame_by_date(
        cached_df,
        stock_code=stock_code,
        start_date=start_time_read,
        end_date=end_time_read,
    )


def _read_cached_price_frame(
    *,
    price_cache: Any | None,
    stock_code: str,
    start_time_read: str,
    end_time_read: str,
) -> pd.DataFrame | None:
    if price_cache is None:
        return None

    cache_stock_code = str(stock_code).strip()
    has_slice_df = hasattr(price_cache, "slice_df")
    if has_slice_df:
        cached_df = price_cache.slice_df(cache_stock_code, start_time_read, end_time_read)
    elif isinstance(price_cache, Mapping):
        cached_df = price_cache.get(cache_stock_code)
    else:
        return None

    if cached_df is None:
        return None
    if COL_TRADE_DATE not in cached_df.columns:
        raise DataValidationError(f"{stock_code}: price_cache 缺少 trade_date 列。")
    if has_slice_df:
        return cached_df.reset_index(drop=True)
    return _slice_mapping_price_cache_frame(
        cached_df,
        stock_code=stock_code,
        start_time_read=start_time_read,
        end_time_read=end_time_read,
    )


def _read_price_df(
    *,
    stock_code: str,
    start_time_read: str,
    end_time_read: str,
    cfg=None,
    price_cache: Any | None = None,
) -> tuple[pd.DataFrame, float]:
    t_read_start = time.perf_counter()
    df = _read_cached_price_frame(
        price_cache=price_cache,
        stock_code=stock_code,
        start_time_read=start_time_read,
        end_time_read=end_time_read,
    )
    if df is not None:
        if df.empty:
            raise PriceDataError(
                f"price_cache 查询结果为空: symbol_code={stock_code!r} "
                f"start_date={start_time_read!r} end_date={end_time_read!r}"
            )
    else:
        last_error: Exception | None = None
        for symbol_type in _candidate_symbol_types(stock_code):
            try:
                df = read_ohlcv_from_mysql(
                    stock_code,
                    symbol_type,
                    start_date=start_time_read,
                    end_date=end_time_read,
                    cfg=cfg,
                )
                break
            except Exception as exc:
                last_error = exc
                if symbol_type != "stock" or not _is_expected_missing_data_error(exc):
                    raise
        else:
            if last_error is not None:
                raise last_error
            raise PriceDataError(f"读取行情失败: symbol_code={stock_code!r}")
    t_read_sec = time.perf_counter() - t_read_start
    return df, t_read_sec


__all__ = [
    "_candidate_symbol_types",
    "_check_trade_calendar",
    "_collect_warmup_shortfall",
    "_coerce_runtime_trade_dates",
    "_is_expected_missing_data_error",
    "_parse_trade_day_yyyymmdd",
    "_read_cached_price_frame",
    "_read_price_df",
    "_resolve_trade_calendar_warn_ratio",
    "_resolve_trade_window",
    "_slice_mapping_price_cache_frame",
]
