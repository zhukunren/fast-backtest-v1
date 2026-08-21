"""
Raw signal filtering before portfolio assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Type

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_SELL_RAW,
)
from core.exceptions import DataValidationError
from core.timing_flags import coerce_timing_flag_series
from strategies.signals import coerce_signal_bool_array


@dataclass(frozen=True, slots=True)
class RawSignalEvents:
    buy: np.ndarray
    sell: np.ndarray
    inside_window: np.ndarray | None


def _terminal_buy_mask(
    frame: pd.DataFrame,
    *,
    end_eval: pd.Timestamp | None,
) -> np.ndarray:
    if frame.empty:
        return np.zeros(0, dtype=bool)
    dates = frame[COL_TRADE_DATE]
    terminal = np.zeros(len(frame), dtype=bool)
    if end_eval is not None:
        terminal |= dates.eq(end_eval).to_numpy(dtype=bool, copy=False)
    if COL_STOCK_CODE in frame.columns:
        codes = frame[COL_STOCK_CODE].to_numpy(copy=False)
        if len(codes) > 0 and bool((codes == codes[0]).all()):
            terminal[-1] = True
        else:
            max_dates = frame.groupby(COL_STOCK_CODE, sort=False)[COL_TRADE_DATE].transform("max")
            terminal |= dates.eq(max_dates).to_numpy(dtype=bool, copy=False)
    else:
        terminal[-1] = True
    return terminal


def _allow_terminal_buy(execution_policy, *, error_cls: Type[Exception]) -> bool:
    if execution_policy is None:
        raise error_cls("execution_policy 不能为空。")
    buy_timing = str(getattr(execution_policy, "buy_timing", "")).strip().lower() or "next_open"
    if buy_timing not in {"next_open", "signal_close"}:
        raise error_cls(f"不支持的 buy_timing={buy_timing!r}")
    return buy_timing == "signal_close"


def filter_raw_signal_events(
    frame: pd.DataFrame,
    *,
    spec,
    execution_policy,
    start_eval: pd.Timestamp | None,
    end_eval: pd.Timestamp | None,
    error_cls: Type[Exception] = DataValidationError,
) -> RawSignalEvents:
    """Filter raw signals without pairing buys and sells into a pre-TopN position."""
    allow_terminal_buy = _allow_terminal_buy(execution_policy, error_cls=error_cls)
    buy = coerce_signal_bool_array(
        frame[SIGNAL_COL_BUY_RAW],
        column_name=SIGNAL_COL_BUY_RAW,
        expected_index=frame.index,
        copy=True,
        error_cls=error_cls,
    )
    sell = coerce_signal_bool_array(
        frame[SIGNAL_COL_SELL_RAW],
        column_name=SIGNAL_COL_SELL_RAW,
        expected_index=frame.index,
        copy=True,
        error_cls=error_cls,
    )

    if getattr(spec, "use_index_timing", False) and "time_ok" in frame.columns:
        time_ok = coerce_timing_flag_series(
            frame["time_ok"],
            column_name="time_ok",
            error_cls=error_cls,
        ).to_numpy(dtype=bool, copy=False)
        if len(time_ok) != len(buy):
            raise error_cls(f"time_ok 长度与信号不一致（{len(time_ok)} vs {len(buy)}）。")
        buy &= time_ok

    inside = None
    if start_eval is not None and end_eval is not None:
        inside = (
            (frame[COL_TRADE_DATE] >= start_eval)
            & (frame[COL_TRADE_DATE] <= end_eval)
        ).to_numpy(dtype=bool, copy=False)
        buy &= inside
        sell &= inside

    if not allow_terminal_buy:
        buy &= ~_terminal_buy_mask(frame, end_eval=end_eval)

    return RawSignalEvents(buy=buy, sell=sell, inside_window=inside)


__all__ = ["RawSignalEvents", "filter_raw_signal_events"]
