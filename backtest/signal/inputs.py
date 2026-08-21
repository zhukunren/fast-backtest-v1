"""Trade-calendar input for the panel-native signal pipeline."""
from __future__ import annotations

import pandas as pd

from core.exceptions import DataValidationError
from data import get_trade_days_cached


def get_trade_days(
    start_date: str,
    end_date: str,
    exchange: str = "",
    *,
    cfg=None,
    calendar_cache=None,
) -> list[pd.Timestamp]:
    """
    获取配置的 Tushare 交易日历（仅保留开市日）。
    """
    if cfg is None:
        raise DataValidationError("get_trade_days 需要显式传入 cfg。")
    exchange = exchange or cfg.tushare.exchange
    days = get_trade_days_cached(
        start_date,
        end_date,
        exchange=exchange,
        cfg=cfg,
        calendar_cache=calendar_cache,
    )
    if not days:
        raise DataValidationError(
            f"trade_cal 为空：exchange={exchange!r} start_date={start_date!r} end_date={end_date!r}"
        )
    return list(days)


__all__ = ["get_trade_days"]
