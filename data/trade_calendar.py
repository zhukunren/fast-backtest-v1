"""
Trade calendar helpers.
"""
from __future__ import annotations

import datetime as dt
import re
from bisect import bisect_left, bisect_right
from typing import Callable

import pandas as pd

from core.exceptions import ConfigurationError, DataSourceError, DataValidationError
from data.tushare_client import (
    create_tushare_client,
    redact_tushare_error,
    require_tushare_token,
    resolve_required_tushare_token,
)


ConfigLoader = Callable[[], object]


class TradeCalendarCache:
    """Own trade-day cache and clamp-warning state for one runtime scope."""

    def __init__(self) -> None:
        self._trade_days: dict[tuple[str, str, str, str], tuple[pd.Timestamp, ...]] = {}
        self.offset_clamp_warned = False

    def get_trade_days(
        self,
        cal_start: str,
        cal_end: str,
        exchange: str,
        token: str,
    ) -> tuple[pd.Timestamp, ...]:
        key = (cal_start, cal_end, exchange, token)
        cached = self._trade_days.get(key)
        if cached is None:
            cached = _load_trade_days(cal_start, cal_end, exchange, token)
            self._trade_days[key] = cached
        return cached

    def mark_offset_clamp_warned(self) -> bool:
        if self.offset_clamp_warned:
            return False
        self.offset_clamp_warned = True
        return True

    def clear(self) -> None:
        self._trade_days.clear()
        self.offset_clamp_warned = False

    def size(self) -> int:
        return len(self._trade_days)


_TRADE_CALENDAR_CACHE_STATE = TradeCalendarCache()


def create_trade_calendar_cache() -> TradeCalendarCache:
    """Create an isolated trade-calendar cache for tests or explicit embedding."""
    return TradeCalendarCache()


def _resolve_cfg(*, cfg, config_loader: ConfigLoader | None = None):
    if cfg is not None:
        return cfg
    if config_loader is not None:
        return config_loader()
    raise ConfigurationError("data.trade_calendar 需要显式传入 cfg。")


def _should_log_offset_clamp(enabled: bool | None) -> bool:
    if enabled is None:
        return True
    return bool(enabled)


def _format_offset_clamp_warning(
    *,
    stock_code: str | None,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
) -> str:
    code_label = f" stock_code={stock_code}" if stock_code else ""
    return (
        "[WARN] offset_days 超出交易日历范围，已使用可用边界："
        f"{code_label} "
        f"start={start_day.strftime('%Y%m%d')} "
        f"end={end_day.strftime('%Y%m%d')}"
    )


def _parse_date_yyyymmdd_or_iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(value).normalize()

    s = str(value).strip()
    if s == "":
        return None
    if re.fullmatch(r"\d{8}", s):
        ts_val = pd.to_datetime(s, format="%Y%m%d", errors="raise")
    else:
        ts_val = pd.to_datetime(s, errors="raise")
    if pd.isna(ts_val):
        raise DataValidationError(f"无法解析日期: {value!r}")
    return pd.Timestamp(ts_val).normalize()


def _resolve_tushare_runtime(
    *,
    cfg=None,
    exchange: str = "",
    config_loader: ConfigLoader | None = None,
) -> tuple[str, str]:
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    return (exchange or cfg.tushare.exchange, resolve_required_tushare_token(cfg=cfg))


def get_trade_calendar(
    start_date: str,
    end_date: str,
    exchange: str = "",
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
    token: str | None = None,
) -> pd.DataFrame:
    """Fetch open trading days from the configured Tushare source."""

    if token is None:
        exchange_val, cfg_token = _resolve_tushare_runtime(
            cfg=cfg,
            exchange=exchange,
            config_loader=config_loader,
        )
        token = cfg_token
    else:
        exchange_val = exchange
    token = require_tushare_token(token)
    pro = create_tushare_client(token)

    try:
        df = pro.trade_cal(
            exchange=exchange_val,
            start_date=start_date,
            end_date=end_date,
            is_open="1",
        )
    except Exception as e:
        message = redact_tushare_error(e, token=token)
        raise DataSourceError(f"Tushare trade_cal 获取失败: err={message}") from None

    df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d")
    df = df.sort_values("cal_date", kind="mergesort").reset_index(drop=True)
    return df


def _load_trade_days(
    cal_start: str,
    cal_end: str,
    exchange: str,
    token: str,
) -> tuple[pd.Timestamp, ...]:
    cal = get_trade_calendar(cal_start, cal_end, exchange=exchange or "", cfg=None, token=token or "")
    if cal is None or cal.empty:
        raise DataSourceError(
            f"trade_cal 为空: exchange={exchange!r} start_date={cal_start!r} end_date={cal_end!r}"
        )
    days = pd.to_datetime(cal["cal_date"], errors="raise").dt.normalize()
    if days.isna().any():
        bad = cal.loc[days.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"trade_cal cal_date 存在无法解析的日期（示例：{bad}）")
    days = days.dropna().drop_duplicates().sort_values(kind="mergesort")
    if days.empty:
        raise DataSourceError(
            f"trade_cal 解析后为空: exchange={exchange!r} start_date={cal_start!r} end_date={cal_end!r}"
        )
    return tuple(days.tolist())


def _cached_trade_days(
    cal_start: str,
    cal_end: str,
    exchange: str,
    token: str,
    *,
    cache: TradeCalendarCache | None = None,
) -> tuple[pd.Timestamp, ...]:
    cache_state = _TRADE_CALENDAR_CACHE_STATE if cache is None else cache
    return cache_state.get_trade_days(cal_start, cal_end, exchange, token)


def clear_trade_calendar_cache(*, cache: TradeCalendarCache | None = None) -> None:
    cache_state = _TRADE_CALENDAR_CACHE_STATE if cache is None else cache
    cache_state.clear()


def get_trade_calendar_cache_size(*, cache: TradeCalendarCache | None = None) -> int:
    cache_state = _TRADE_CALENDAR_CACHE_STATE if cache is None else cache
    return cache_state.size()


def compute_trade_date_offset(
    start_time: str,
    end_time: str,
    offset_days: int,
    *,
    exchange: str = "",
    cal_start: str | None = None,
    cal_end: str | None = None,
    allow_clamp: bool = False,
    stock_code: str | None = None,
    offset_clamp_log: bool | None = None,
    cfg,
    config_loader: ConfigLoader | None = None,
    calendar_cache: TradeCalendarCache | None = None,
) -> tuple[str, str]:
    """
    按交易日计算回测区间偏移（预热/缓冲）。
    """
    start_ts = _parse_date_yyyymmdd_or_iso(start_time)
    end_ts = _parse_date_yyyymmdd_or_iso(end_time)
    if start_ts is None or end_ts is None:
        raise DataValidationError("start_time/end_time 不能为空")
    if start_ts > end_ts:
        raise DataValidationError(f"start_time 不能晚于 end_time: {start_time!r} > {end_time!r}")

    offset_days = int(offset_days or 0)
    if offset_days <= 0:
        return start_ts.strftime("%Y%m%d"), end_ts.strftime("%Y%m%d")

    buffer_days = max(30, offset_days * 2 + 30)
    auto_start = start_ts - pd.Timedelta(days=buffer_days)
    auto_end = end_ts + pd.Timedelta(days=buffer_days)

    if cfg is not None:
        cal_start = cal_start or getattr(cfg.tushare, "trade_cal_start", None)
        cal_end = cal_end or getattr(cfg.tushare, "trade_cal_end", None)

    if cal_start:
        cal_start_ts = _parse_date_yyyymmdd_or_iso(cal_start)
        if cal_start_ts is None:
            cal_start_ts = auto_start
        else:
            cal_start_ts = min(cal_start_ts, auto_start)
    else:
        cal_start_ts = auto_start

    if cal_end:
        cal_end_ts = _parse_date_yyyymmdd_or_iso(cal_end)
        if cal_end_ts is None:
            cal_end_ts = auto_end
        else:
            cal_end_ts = max(cal_end_ts, auto_end)
    else:
        cal_end_ts = auto_end

    cal_start_str = cal_start_ts.strftime("%Y%m%d")
    cal_end_str = cal_end_ts.strftime("%Y%m%d")
    exchange_val, token = _resolve_tushare_runtime(
        cfg=cfg,
        exchange=exchange,
        config_loader=config_loader,
    )
    calendar_cache_state = _TRADE_CALENDAR_CACHE_STATE if calendar_cache is None else calendar_cache
    trade_days = _cached_trade_days(
        cal_start_str,
        cal_end_str,
        exchange_val or "",
        token or "",
        cache=calendar_cache_state,
    )

    start_idx = bisect_left(trade_days, start_ts)
    if start_idx >= len(trade_days):
        raise DataValidationError(
            f"start_time 超出交易日历范围: start_time={start_time!r} cal_end={cal_end_str!r}"
        )
    end_idx = bisect_right(trade_days, end_ts) - 1
    if end_idx < 0:
        raise DataValidationError(
            f"end_time 早于交易日历范围: end_time={end_time!r} cal_start={cal_start_str!r}"
        )

    start_offset_idx = start_idx - offset_days
    end_offset_idx = end_idx + offset_days
    if start_offset_idx < 0 or end_offset_idx >= len(trade_days):
        if not allow_clamp:
            raise DataValidationError(
                "offset_days 超出交易日历范围，请扩大 tushare.trade_cal_start/end 或减少 offset_days。"
            )
        start_offset_idx = max(0, start_offset_idx)
        end_offset_idx = min(len(trade_days) - 1, end_offset_idx)
        if _should_log_offset_clamp(offset_clamp_log):
            if calendar_cache_state.mark_offset_clamp_warned():
                print(
                    _format_offset_clamp_warning(
                        stock_code=stock_code,
                        start_day=trade_days[start_offset_idx],
                        end_day=trade_days[end_offset_idx],
                    )
                )

    return (
        trade_days[start_offset_idx].strftime("%Y%m%d"),
        trade_days[end_offset_idx].strftime("%Y%m%d"),
    )


def get_trade_days_cached(
    start_date: str,
    end_date: str,
    *,
    exchange: str = "",
    cfg,
    config_loader: ConfigLoader | None = None,
    calendar_cache: TradeCalendarCache | None = None,
) -> list[pd.Timestamp]:
    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    if start_ts is None or end_ts is None:
        raise DataValidationError("start_date/end_date 不能为空")
    if start_ts > end_ts:
        raise DataValidationError(f"start_date 不能晚于 end_date: {start_date!r} > {end_date!r}")

    cal_start_str = start_ts.strftime("%Y%m%d")
    cal_end_str = end_ts.strftime("%Y%m%d")
    exchange_val, token = _resolve_tushare_runtime(
        cfg=cfg,
        exchange=exchange,
        config_loader=config_loader,
    )
    trade_days = _cached_trade_days(
        cal_start_str,
        cal_end_str,
        exchange_val or "",
        token or "",
        cache=calendar_cache,
    )
    return [d for d in trade_days if start_ts <= d <= end_ts]


__all__ = [
    "_format_offset_clamp_warning",
    "TradeCalendarCache",
    "compute_trade_date_offset",
    "clear_trade_calendar_cache",
    "create_trade_calendar_cache",
    "get_trade_calendar_cache_size",
    "get_trade_calendar",
    "get_trade_days_cached",
    "_parse_date_yyyymmdd_or_iso",
]
