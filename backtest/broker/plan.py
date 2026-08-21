"""Broker trade-plan construction from panel-native row positions."""
from __future__ import annotations

from dataclasses import dataclass
import logging

import numpy as np
import pandas as pd

from core.exceptions import ConfigurationError, DataValidationError

from backtest.panel_native import QuoteStore, SelectionBatch

from ..execution_semantics import (
    iterate_trade_windows_from_flags,
    resolve_execution_semantics,
)
from ..force_close import resolve_force_close_mode
from ..trade_calendar import normalize_trade_days_index
from ..trade_state_machine import apply_trade_state_machine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TradePlan:
    stock_code: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp | None
    entry_price_type: str
    exit_price_type: str
    exit_is_forced: bool = False
    signal_row: int = -1
    entry_row: int = -1
    exit_row: int = -1
    priority: int = 10**9


@dataclass(frozen=True, slots=True)
class EntryRetryIntent:
    """A later signal that may replace an unfilled entry in the same cycle."""

    stock_code: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price_type: str
    signal_row: int
    entry_row: int
    cycle_signal_row: int
    priority: int


def ordered_selection_rows(
    selection: SelectionBatch,
    quotes: QuoteStore,
    *,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame,
    buy_timing: str,
    include_candidates: bool = True,
) -> tuple[np.ndarray, pd.DatetimeIndex, int, int]:
    """Merge selected and candidate rows per date while preserving stable rank."""
    if not isinstance(selection, SelectionBatch):
        raise DataValidationError("broker requires SelectionBatch")
    if not isinstance(quotes, QuoteStore):
        raise DataValidationError("broker requires QuoteStore")
    trade_days_idx = normalize_trade_days_index(trade_days, name="trade_days")
    if trade_days_idx.empty:
        raise DataValidationError("trade_days 为空，无法执行 broker 回测。")

    allowed_days = trade_days_idx.to_numpy(dtype="datetime64[ns]", copy=False)
    last_day = allowed_days[-1]
    by_date: dict[np.datetime64, list[int]] = {}
    seen: set[int] = set()
    dropped_selected = 0
    dropped_candidate = 0

    sources = [("selected", selection.selected_rows)]
    if include_candidates:
        sources.append(("candidate", selection.candidate_rows))
    for source_name, source_rows in sources:
        rows = np.asarray(source_rows, dtype=np.int64)
        if rows.ndim != 1:
            raise DataValidationError(f"{source_name} rows must be one-dimensional")
        if len(rows) and (rows.min() < 0 or rows.max() >= quotes.index.row_count):
            raise DataValidationError(f"{source_name} rows contain an out-of-range position")
        dates = quotes.index.trade_dates[rows]
        outside = np.unique(dates[~np.isin(dates, allowed_days)])
        if len(outside):
            sample = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in outside[:5]]
            raise DataValidationError(
                f"{source_name} rows contain dates outside trade_days: {sample}"
            )
        for row, date in zip(rows.tolist(), dates):
            position = int(row)
            if position in seen:
                continue
            if buy_timing == "next_open" and date == last_day:
                if source_name == "selected":
                    dropped_selected += 1
                else:
                    dropped_candidate += 1
                seen.add(position)
                continue
            seen.add(position)
            by_date.setdefault(date, []).append(position)

    ordered = [
        row
        for date in allowed_days
        for row in by_date.get(date, ())
    ]
    return (
        np.asarray(ordered, dtype=np.int64),
        trade_days_idx,
        dropped_selected,
        dropped_candidate,
    )


def build_trade_plan(
    selection: SelectionBatch,
    quotes: QuoteStore,
    *,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame,
    execution_policy=None,
    cfg=None,
    include_candidates: bool = True,
) -> list[TradePlan]:
    if cfg is None:
        raise ConfigurationError("build_trade_plan 需要显式传入 cfg。")
    if execution_policy is None:
        raise ConfigurationError("build_trade_plan 需要显式传入 execution_policy。")
    semantics = resolve_execution_semantics(execution_policy)
    signal_rows, trade_days_idx, _, _ = ordered_selection_rows(
        selection,
        quotes,
        trade_days=trade_days,
        buy_timing=semantics.buy_timing,
        include_candidates=include_candidates,
    )
    if not len(signal_rows):
        return []

    window_end = np.datetime64(pd.Timestamp(trade_days_idx[-1]).normalize(), "ns")
    rank_by_row = {int(row): rank for rank, row in enumerate(signal_rows.tolist())}
    signal_mask = np.zeros(quotes.index.row_count, dtype=bool)
    signal_mask[signal_rows] = True
    stock_ids = np.unique(quotes.index.stock_id_by_row[signal_rows])
    force_close_mode = resolve_force_close_mode(cfg, default="force")
    opens_all = quotes.column("open")
    closes_all = quotes.column("close")

    plans: list[TradePlan] = []
    for stock_id in stock_ids:
        stock_id_int = int(stock_id)
        code = quotes.stock_codes[stock_id_int]
        stock_slice = quotes.index.stock_slice(stock_id_int)
        stock_start = int(stock_slice.start or 0)
        dates_all = quotes.index.trade_dates[stock_slice]
        stop = int(np.searchsorted(dates_all, window_end, side="right"))
        if stop <= 0:
            continue
        dates = dates_all[:stop]
        buy_raw = signal_mask[stock_slice][:stop]
        sell_raw = selection.final_sell[stock_slice][:stop]
        buy_trade, sell_trade = apply_trade_state_machine(
            buy_raw,
            sell_raw,
            trade_dates=None,
            stock_code=code,
            print_on_shift=False,
            shift_t1_sell=semantics.shift_t1_sell,
            allow_terminal_buy=semantics.allow_terminal_buy,
            cfg=cfg,
        )
        windows = iterate_trade_windows_from_flags(
            buy_trade=buy_trade,
            sell_trade=sell_trade,
            dates=dates,
            opens=np.asarray(opens_all[stock_slice][:stop], dtype=np.float64),
            closes=np.asarray(closes_all[stock_slice][:stop], dtype=np.float64),
            execution_policy=execution_policy,
            stock_code=code,
        )
        for window in windows:
            signal_row = stock_start + int(window.signal_pos)
            exit_date = None
            if not (force_close_mode == "mark_to_market" and window.exit_is_forced):
                exit_date = pd.Timestamp(window.exit_date).normalize()
            plans.append(
                TradePlan(
                    stock_code=code,
                    signal_date=pd.Timestamp(dates[window.signal_pos]).normalize(),
                    entry_date=pd.Timestamp(window.entry_date).normalize(),
                    exit_date=exit_date,
                    entry_price_type=window.entry_price_type,
                    exit_price_type=window.exit_price_type,
                    exit_is_forced=bool(window.exit_is_forced),
                    signal_row=signal_row,
                    entry_row=stock_start + int(window.entry_idx),
                    exit_row=stock_start + int(window.exit_idx),
                    priority=rank_by_row.get(signal_row, 10**9),
                )
            )

    plans.sort(
        key=lambda item: (
            item.entry_date,
            rank_by_row.get(item.signal_row, 10**9),
            item.stock_code,
        )
    )
    return plans


def build_entry_retry_intents(
    selection: SelectionBatch,
    quotes: QuoteStore,
    *,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame,
    execution_policy=None,
    cfg=None,
    include_candidates: bool = True,
) -> list[EntryRetryIntent]:
    """Return suppressed same-cycle buy signals as conditional broker intents.

    The state machine intentionally emits one entry per uninterrupted signal
    cycle. Broker fills are not known when that static plan is built, though.
    A later raw buy becomes eligible only when the cycle's earlier entry never
    filled; the runner enforces that condition against its real ledger.
    """
    if cfg is None:
        raise ConfigurationError("build_entry_retry_intents 需要显式传入 cfg。")
    if execution_policy is None:
        raise ConfigurationError("build_entry_retry_intents 需要显式传入 execution_policy。")
    semantics = resolve_execution_semantics(execution_policy)
    signal_rows, trade_days_idx, _, _ = ordered_selection_rows(
        selection,
        quotes,
        trade_days=trade_days,
        buy_timing=semantics.buy_timing,
        include_candidates=include_candidates,
    )
    if not len(signal_rows):
        return []

    window_end = np.datetime64(pd.Timestamp(trade_days_idx[-1]).normalize(), "ns")
    rank_by_row = {int(row): rank for rank, row in enumerate(signal_rows.tolist())}
    signal_mask = np.zeros(quotes.index.row_count, dtype=bool)
    signal_mask[signal_rows] = True
    stock_ids = np.unique(quotes.index.stock_id_by_row[signal_rows])
    intents: list[EntryRetryIntent] = []

    for stock_id in stock_ids:
        stock_id_int = int(stock_id)
        code = quotes.stock_codes[stock_id_int]
        stock_slice = quotes.index.stock_slice(stock_id_int)
        stock_start = int(stock_slice.start or 0)
        dates_all = quotes.index.trade_dates[stock_slice]
        stop = int(np.searchsorted(dates_all, window_end, side="right"))
        if stop <= 0:
            continue
        dates = dates_all[:stop]
        buy_raw = signal_mask[stock_slice][:stop]
        sell_raw = selection.final_sell[stock_slice][:stop]
        buy_trade, _sell_trade = apply_trade_state_machine(
            buy_raw,
            sell_raw,
            trade_dates=None,
            stock_code=code,
            print_on_shift=False,
            shift_t1_sell=semantics.shift_t1_sell,
            allow_terminal_buy=semantics.allow_terminal_buy,
            cfg=cfg,
        )
        cycle_signal_pos: int | None = None
        date_series = pd.Series(pd.DatetimeIndex(dates), copy=False)
        last_idx = len(dates) - 1

        for pos in range(len(dates)):
            if buy_trade[pos]:
                cycle_signal_pos = pos
                continue
            if cycle_signal_pos is None:
                continue
            # A sale signal ends the static cycle even when its actual sell is
            # T+1-shifted. Its same-bar buy signal was intentionally suppressed.
            if sell_raw[pos]:
                cycle_signal_pos = None
                continue
            if not buy_raw[pos]:
                continue
            if semantics.buy_timing == "next_open" and pos >= last_idx:
                continue
            entry_idx = pos if semantics.buy_timing == "signal_close" else pos + 1
            entry_date = pd.Timestamp(date_series.iloc[entry_idx]).normalize()
            entry_price_type = "close" if semantics.buy_timing == "signal_close" else "open"
            intents.append(
                EntryRetryIntent(
                    stock_code=code,
                    signal_date=pd.Timestamp(dates[pos]).normalize(),
                    entry_date=pd.Timestamp(entry_date).normalize(),
                    entry_price_type=entry_price_type,
                    signal_row=stock_start + pos,
                    entry_row=stock_start + int(entry_idx),
                    cycle_signal_row=stock_start + int(cycle_signal_pos),
                    priority=rank_by_row.get(stock_start + pos, 10**9),
                )
            )

    intents.sort(
        key=lambda item: (
            item.entry_date,
            rank_by_row.get(item.signal_row, 10**9),
            item.stock_code,
        )
    )
    return intents


__all__ = [
    "EntryRetryIntent",
    "TradePlan",
    "build_entry_retry_intents",
    "build_trade_plan",
    "ordered_selection_rows",
]
