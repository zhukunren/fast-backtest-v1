"""
Unified execution semantics helpers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.constants import (
    DIRECTION_BUY,
    DIRECTION_NONE,
    DIRECTION_SELL,
    SIGNAL_COL_BUY,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
)
from core.exceptions import DataValidationError
from core.normalizers import normalize_direction_series
from core.returns_calculator import defer_entry_day_signal_close_exits
from backtest.trade_state_machine import TradeStateRuntime, apply_trade_state_machine


@dataclass(frozen=True, slots=True)
class ResolvedExecutionSemantics:
    buy_timing: str
    sell_timing: str
    shift_t1_sell: bool
    allow_intraday: bool
    allow_terminal_buy: bool


@dataclass(frozen=True, slots=True)
class ResolvedTradeWindow:
    signal_pos: int
    sell_pos: int | None
    entry_idx: int
    exit_idx: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    entry_price_type: str
    exit_price_type: str
    exit_is_forced: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedTradeFlags:
    buy_trade: np.ndarray
    sell_trade: np.ndarray
    entry_flags: np.ndarray
    exit_flags: np.ndarray


def resolve_execution_semantics(execution_policy) -> ResolvedExecutionSemantics:
    if execution_policy is None:
        raise DataValidationError("execution_policy 不能为空。")

    buy_timing = str(getattr(execution_policy, "buy_timing", "")).strip().lower() or "next_open"
    sell_timing = str(getattr(execution_policy, "sell_timing", "")).strip().lower() or "signal_close"
    if buy_timing not in {"next_open", "signal_close"}:
        raise DataValidationError(f"不支持的 buy_timing={buy_timing!r}")
    if sell_timing not in {"signal_close", "next_open"}:
        raise DataValidationError(f"不支持的 sell_timing={sell_timing!r}")

    shift_t1_sell = bool(getattr(execution_policy, "shift_t1_sell", False))
    allow_intraday = bool(getattr(execution_policy, "allow_intraday", False))
    if sell_timing == "next_open":
        shift_t1_sell = False
        allow_intraday = False
    allow_terminal_buy = buy_timing == "signal_close"
    return ResolvedExecutionSemantics(
        buy_timing=buy_timing,
        sell_timing=sell_timing,
        shift_t1_sell=shift_t1_sell,
        allow_intraday=allow_intraday,
        allow_terminal_buy=allow_terminal_buy,
    )


def normalize_direction_array(direction_series: pd.Series) -> np.ndarray:
    raw_direction_arr = direction_series.to_numpy(copy=False)
    non_null_mask = pd.notna(raw_direction_arr)
    if bool(np.isin(raw_direction_arr[non_null_mask], (DIRECTION_BUY, DIRECTION_SELL, DIRECTION_NONE)).all()):
        if bool(non_null_mask.all()):
            return np.asarray(raw_direction_arr, dtype=object)
        return np.where(
            non_null_mask,
            np.asarray(raw_direction_arr, dtype=object),
            DIRECTION_NONE,
        )
    return normalize_direction_series(direction_series).to_numpy()


def resolve_trade_flags(
    *,
    execution_policy,
    direction_series: pd.Series | None = None,
    buy_trade: np.ndarray | None = None,
    sell_trade: np.ndarray | None = None,
    stock_code: str | None,
    aggregate_t1_stats: bool,
    cfg=None,
    runtime: TradeStateRuntime | None = None,
) -> ResolvedTradeFlags:
    if buy_trade is not None or sell_trade is not None:
        if buy_trade is None or sell_trade is None:
            raise DataValidationError("buy_trade/sell_trade 必须同时提供。")
        buy_exec = np.asarray(buy_trade, dtype=bool)
        sell_exec = np.asarray(sell_trade, dtype=bool)
        if buy_exec.shape != sell_exec.shape:
            raise DataValidationError(
                f"buy_trade/sell_trade shape mismatch: {buy_exec.shape} vs {sell_exec.shape}"
            )
        if direction_series is not None and len(direction_series) != len(buy_exec):
            raise DataValidationError("direction_series 与 buy_trade/sell_trade 长度不一致。")
    else:
        if direction_series is None:
            raise DataValidationError("direction_series 与 buy_trade/sell_trade 不能同时缺失。")
        buy_exec, sell_exec = build_trade_flags_from_direction(
            direction_series,
            stock_code=stock_code,
            execution_policy=execution_policy,
            aggregate_t1_stats=aggregate_t1_stats,
            cfg=cfg,
            runtime=runtime,
        )

    entry_flags, exit_flags = build_position_transition_flags(
        buy_trade=np.asarray(buy_exec, dtype=bool),
        sell_trade=np.asarray(sell_exec, dtype=bool),
        execution_policy=execution_policy,
    )
    return ResolvedTradeFlags(
        buy_trade=np.asarray(buy_exec, dtype=bool),
        sell_trade=np.asarray(sell_exec, dtype=bool),
        entry_flags=np.asarray(entry_flags, dtype=bool),
        exit_flags=np.asarray(exit_flags, dtype=bool),
    )


def resolve_trade_flags_from_frames(
    frame: pd.DataFrame,
    *,
    execution_policy,
    signal_df: pd.DataFrame | None = None,
    buy_trade: np.ndarray | None = None,
    sell_trade: np.ndarray | None = None,
    stock_code: str | None,
    aggregate_t1_stats: bool,
    cfg=None,
    runtime: TradeStateRuntime | None = None,
) -> ResolvedTradeFlags:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise DataValidationError("frame 不能为空。")

    n = len(frame)
    direction_series: pd.Series | None = None
    buy_exec = buy_trade
    sell_exec = sell_trade

    if buy_exec is not None or sell_exec is not None:
        if buy_exec is None or sell_exec is None:
            raise DataValidationError("buy_trade/sell_trade 必须同时提供。")
        if len(buy_exec) != n or len(sell_exec) != n:
            raise DataValidationError("buy_trade/sell_trade 长度与 frame 不一致。")
    else:
        if isinstance(signal_df, pd.DataFrame):
            if SIGNAL_COL_BUY in signal_df.columns and SIGNAL_COL_SELL in signal_df.columns:
                buy_exec = signal_df[SIGNAL_COL_BUY].reindex(frame.index).fillna(False).to_numpy(dtype=bool)
                sell_exec = signal_df[SIGNAL_COL_SELL].reindex(frame.index).fillna(False).to_numpy(dtype=bool)
            if SIGNAL_COL_DIRECTION in signal_df.columns:
                direction_series = signal_df[SIGNAL_COL_DIRECTION].reindex(frame.index)

        if buy_exec is None or sell_exec is None:
            if SIGNAL_COL_BUY in frame.columns and SIGNAL_COL_SELL in frame.columns:
                buy_exec = frame[SIGNAL_COL_BUY].fillna(False).to_numpy(dtype=bool)
                sell_exec = frame[SIGNAL_COL_SELL].fillna(False).to_numpy(dtype=bool)

        if direction_series is None and SIGNAL_COL_DIRECTION in frame.columns:
            direction_series = frame[SIGNAL_COL_DIRECTION]

    return resolve_trade_flags(
        execution_policy=execution_policy,
        direction_series=direction_series,
        buy_trade=buy_exec,
        sell_trade=sell_exec,
        stock_code=stock_code,
        aggregate_t1_stats=aggregate_t1_stats,
        cfg=cfg,
        runtime=runtime,
    )


def build_trade_flags_from_direction(
    direction_series: pd.Series,
    *,
    execution_policy,
    stock_code: str | None,
    aggregate_t1_stats: bool,
    cfg=None,
    runtime: TradeStateRuntime | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    semantics = resolve_execution_semantics(execution_policy)
    directions = normalize_direction_array(direction_series)
    buy_raw = directions == DIRECTION_BUY
    sell_raw = directions == DIRECTION_SELL
    buy_trade, sell_trade = apply_trade_state_machine(
        buy_raw,
        sell_raw,
        trade_dates=None,
        stock_code=stock_code,
        print_on_shift=False,
        shift_t1_sell=semantics.shift_t1_sell,
        allow_terminal_buy=semantics.allow_terminal_buy,
        aggregate_t1_stats=aggregate_t1_stats,
        cfg=cfg,
        runtime=runtime,
    )
    return np.asarray(buy_trade, dtype=bool), np.asarray(sell_trade, dtype=bool)


def resolve_entry_execution(
    *,
    signal_pos: int,
    last_idx: int,
    dates: pd.Series,
    opens: np.ndarray,
    closes: np.ndarray,
    execution_policy,
    stock_code: str,
) -> tuple[int, pd.Timestamp, float, str]:
    semantics = resolve_execution_semantics(execution_policy)
    if semantics.buy_timing == "signal_close":
        entry_idx = signal_pos
        return entry_idx, pd.Timestamp(dates.iloc[entry_idx]).normalize(), float(closes[entry_idx]), "close"

    entry_idx = signal_pos + 1
    if entry_idx > last_idx:
        raise DataValidationError(f"{stock_code}: 买入信号位于样本最后一天，无法 next_open 成交。")
    return entry_idx, pd.Timestamp(dates.iloc[entry_idx]).normalize(), float(opens[entry_idx]), "open"


def resolve_exit_execution(
    *,
    sell_pos: int | None,
    entry_idx: int,
    last_idx: int,
    dates: pd.Series,
    opens: np.ndarray,
    closes: np.ndarray,
    execution_policy,
) -> tuple[int, pd.Timestamp, float, str]:
    semantics = resolve_execution_semantics(execution_policy)
    if sell_pos is None:
        exit_idx = last_idx
        return exit_idx, pd.Timestamp(dates.iloc[exit_idx]).normalize(), float(closes[exit_idx]), "close"

    if semantics.sell_timing == "next_open":
        if sell_pos < last_idx:
            exit_idx = sell_pos + 1
            return exit_idx, pd.Timestamp(dates.iloc[exit_idx]).normalize(), float(opens[exit_idx]), "open"
        exit_idx = sell_pos
        return exit_idx, pd.Timestamp(dates.iloc[exit_idx]).normalize(), float(closes[exit_idx]), "close"

    exit_idx = sell_pos
    if exit_idx == entry_idx and (semantics.shift_t1_sell or (not semantics.allow_intraday)):
        next_idx = entry_idx + 1
        if next_idx <= last_idx:
            exit_idx = next_idx
    return exit_idx, pd.Timestamp(dates.iloc[exit_idx]).normalize(), float(closes[exit_idx]), "close"


def build_position_transition_flags(
    *,
    buy_trade: np.ndarray,
    sell_trade: np.ndarray,
    execution_policy,
) -> tuple[np.ndarray, np.ndarray]:
    semantics = resolve_execution_semantics(execution_policy)
    buy_exec = np.asarray(buy_trade, dtype=bool)
    sell_exec = np.asarray(sell_trade, dtype=bool)
    if buy_exec.shape != sell_exec.shape:
        raise DataValidationError(f"buy_trade/sell_trade shape mismatch: {buy_exec.shape} vs {sell_exec.shape}")

    entry = np.zeros(len(buy_exec), dtype=bool)
    exit_ = np.zeros(len(sell_exec), dtype=bool)
    if semantics.buy_timing == "signal_close":
        entry[:] = buy_exec
    elif len(entry) > 1:
        entry[1:] = buy_exec[:-1]

    if semantics.sell_timing == "next_open":
        if len(exit_) > 1:
            exit_[1:] = sell_exec[:-1]
    else:
        exit_[:] = sell_exec

    if semantics.sell_timing == "signal_close" and (semantics.shift_t1_sell or (not semantics.allow_intraday)):
        exit_ = defer_entry_day_signal_close_exits(
            entry,
            exit_,
            allow_intraday=semantics.allow_intraday,
            shift_t1_sell=semantics.shift_t1_sell,
        )
    return entry, exit_


def iterate_trade_windows_from_flags(
    *,
    buy_trade: np.ndarray,
    sell_trade: np.ndarray,
    dates,
    opens: np.ndarray,
    closes: np.ndarray,
    execution_policy,
    stock_code: str,
) -> list[ResolvedTradeWindow]:
    buy_exec = np.asarray(buy_trade, dtype=bool)
    sell_exec = np.asarray(sell_trade, dtype=bool)
    if buy_exec.shape != sell_exec.shape:
        raise DataValidationError(f"buy_trade/sell_trade shape mismatch: {buy_exec.shape} vs {sell_exec.shape}")
    if len(opens) != len(buy_exec) or len(closes) != len(buy_exec):
        raise DataValidationError("prices length does not match buy/sell trade flags")

    date_index = pd.DatetimeIndex(pd.to_datetime(dates, errors="raise")).normalize()
    if len(date_index) != len(buy_exec):
        raise DataValidationError("dates length does not match buy/sell trade flags")

    last_idx = len(buy_exec) - 1
    windows: list[ResolvedTradeWindow] = []
    semantics = resolve_execution_semantics(execution_policy)
    signal_pos: int | None = None
    entry_idx: int | None = None
    entry_date: pd.Timestamp | None = None
    entry_price = np.nan
    entry_price_type = "open"
    holding = False
    date_series = pd.Series(date_index, copy=False)

    for pos in range(len(buy_exec)):
        if (not holding) and buy_exec[pos]:
            signal_pos = pos
            entry_idx, entry_date, entry_price, entry_price_type = resolve_entry_execution(
                signal_pos=pos,
                last_idx=last_idx,
                dates=date_series,
                opens=opens,
                closes=closes,
                execution_policy=execution_policy,
                stock_code=stock_code,
            )
            holding = True
            continue

        if holding and sell_exec[pos]:
            exit_idx, exit_date, exit_price, exit_price_type = resolve_exit_execution(
                sell_pos=pos,
                entry_idx=int(entry_idx),
                last_idx=last_idx,
                dates=date_series,
                opens=opens,
                closes=closes,
                execution_policy=execution_policy,
            )
            windows.append(
                ResolvedTradeWindow(
                    signal_pos=int(signal_pos),
                    sell_pos=pos,
                    entry_idx=int(entry_idx),
                    exit_idx=int(exit_idx),
                    entry_date=entry_date,
                    exit_date=exit_date,
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                    entry_price_type=entry_price_type,
                    exit_price_type=exit_price_type,
                    exit_is_forced=bool(semantics.sell_timing == "next_open" and pos >= last_idx),
                )
            )
            signal_pos = None
            entry_idx = None
            entry_date = None
            entry_price = np.nan
            entry_price_type = "open"
            holding = False

    if holding and signal_pos is not None and entry_idx is not None and entry_date is not None:
        exit_idx, exit_date, exit_price, exit_price_type = resolve_exit_execution(
            sell_pos=None,
            entry_idx=int(entry_idx),
            last_idx=last_idx,
            dates=date_series,
            opens=opens,
            closes=closes,
            execution_policy=execution_policy,
        )
        windows.append(
            ResolvedTradeWindow(
                signal_pos=int(signal_pos),
                sell_pos=None,
                entry_idx=int(entry_idx),
                exit_idx=int(exit_idx),
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                entry_price_type=entry_price_type,
                exit_price_type=exit_price_type,
                exit_is_forced=True,
            )
        )
    return windows


__all__ = [
    "ResolvedExecutionSemantics",
    "ResolvedTradeFlags",
    "ResolvedTradeWindow",
    "build_position_transition_flags",
    "build_trade_flags_from_direction",
    "iterate_trade_windows_from_flags",
    "normalize_direction_array",
    "resolve_trade_flags",
    "resolve_trade_flags_from_frames",
    "resolve_entry_execution",
    "resolve_execution_semantics",
    "resolve_exit_execution",
]
