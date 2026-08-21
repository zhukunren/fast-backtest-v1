"""Lazy quote lookup for cash-accounted slot positions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from backtest.panel_native import QuoteStore

from .cash_execution import build_last_valid_mark_prices

if TYPE_CHECKING:
    from .cash_execution import _CashExecutionContext


@dataclass(frozen=True, slots=True)
class CashQuote:
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    volume: float
    stock_name: str
    tradable: bool


CashQuoteResolver = Callable[[str, str], CashQuote | None]
ScheduledCashQuote = tuple[object, object, object, object, object, object, object, object]


@dataclass(frozen=True, slots=True)
class _PriceFrameIndex:
    dates: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    mark_close: np.ndarray
    previous_close: np.ndarray
    volume: np.ndarray | None
    stock_name: str


def _exact_quote(index: _PriceFrameIndex, position: int) -> CashQuote:
    volume = float(index.volume[position]) if index.volume is not None else np.nan
    return CashQuote(
        open=float(index.open[position]),
        high=float(index.high[position]),
        low=float(index.low[position]),
        close=float(index.close[position]),
        previous_close=float(index.previous_close[position]),
        volume=volume,
        stock_name=index.stock_name,
        tradable=True,
    )


def _stale_quote(index: _PriceFrameIndex, position: int) -> CashQuote:
    last_close = float(index.mark_close[position])
    return CashQuote(
        open=last_close,
        high=last_close,
        low=last_close,
        close=last_close,
        previous_close=last_close,
        volume=0.0,
        stock_name=index.stock_name,
        tradable=False,
    )


def replace_with_held_quote(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    active_stock_code: str,
    held_quote: CashQuote,
) -> ScheduledCashQuote:
    scheduled_quote: ScheduledCashQuote = (
        ctx.open_arr[idx],
        ctx.high_arr[idx],
        ctx.low_arr[idx],
        ctx.close_arr[idx],
        ctx.vol_arr[idx] if ctx.vol_arr is not None else None,
        ctx.prev_close_arr[idx],
        ctx.stock_code_arr[idx] if ctx.stock_code_arr is not None else None,
        ctx.stock_name_arr[idx] if ctx.stock_name_arr is not None else None,
    )
    ctx.open_arr[idx] = held_quote.open
    ctx.high_arr[idx] = held_quote.high
    ctx.low_arr[idx] = held_quote.low
    ctx.close_arr[idx] = held_quote.close
    ctx.prev_close_arr[idx] = held_quote.previous_close
    if ctx.vol_arr is not None:
        ctx.vol_arr[idx] = held_quote.volume
    if ctx.stock_code_arr is not None:
        ctx.stock_code_arr[idx] = active_stock_code
    if ctx.stock_name_arr is not None:
        ctx.stock_name_arr[idx] = held_quote.stock_name
    return scheduled_quote


def restore_scheduled_quote(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    scheduled_quote: ScheduledCashQuote,
) -> None:
    (
        ctx.open_arr[idx],
        ctx.high_arr[idx],
        ctx.low_arr[idx],
        ctx.close_arr[idx],
        scheduled_volume,
        ctx.prev_close_arr[idx],
        scheduled_stock_code,
        scheduled_stock_name,
    ) = scheduled_quote
    if ctx.vol_arr is not None and scheduled_volume is not None:
        ctx.vol_arr[idx] = scheduled_volume
    if ctx.stock_code_arr is not None:
        ctx.stock_code_arr[idx] = scheduled_stock_code
    if ctx.stock_name_arr is not None:
        ctx.stock_name_arr[idx] = scheduled_stock_name


def _index_quote_store(code: str, quotes: QuoteStore) -> _PriceFrameIndex:
    stock_slice = quotes.stock_slice(code)
    dates = quotes.index.trade_dates[stock_slice]
    opens = np.asarray(quotes.column("open")[stock_slice], dtype=np.float64)
    closes = np.asarray(quotes.column("close")[stock_slice], dtype=np.float64)
    previous_closes = np.empty(len(closes), dtype=np.float64)
    if len(closes):
        previous_closes[0] = np.nan
    if len(closes) > 1:
        previous_closes[1:] = closes[:-1]
    if "pre_close" in quotes.columns:
        supplied = np.asarray(quotes.column("pre_close")[stock_slice], dtype=np.float64)
        previous_closes = np.where(
            np.isfinite(supplied) & (supplied > 0),
            supplied,
            previous_closes,
        )
    names = quotes.columns.get("stock_name")
    return _PriceFrameIndex(
        dates=dates,
        open=opens,
        high=np.asarray(quotes.column("high")[stock_slice], dtype=np.float64),
        low=np.asarray(quotes.column("low")[stock_slice], dtype=np.float64),
        close=closes,
        mark_close=build_last_valid_mark_prices(opens, closes),
        previous_close=previous_closes,
        volume=(
            np.asarray(quotes.column("volume")[stock_slice], dtype=np.float64) if "volume" in quotes.columns else None
        ),
        stock_name=(str(names[int(stock_slice.start or 0)]).strip() if names is not None and len(dates) else ""),
    )


def build_cash_quote_resolver(
    quote_store: QuoteStore | None,
) -> CashQuoteResolver | None:
    if quote_store is None:
        return None

    cache: dict[str, _PriceFrameIndex | None] = {}

    def load(code: str) -> _PriceFrameIndex | None:
        if code in cache:
            return cache[code]
        try:
            indexed = _index_quote_store(code, quote_store)
        except KeyError:
            indexed = None
        cache[code] = indexed
        return indexed

    def resolve(code: str, trade_date: str) -> CashQuote | None:
        index = load(code)
        if index is None:
            return None
        target = np.datetime64(pd.Timestamp(trade_date).normalize(), "ns")
        position = int(np.searchsorted(index.dates, target, side="left"))
        exact = position < len(index.dates) and index.dates[position] == target
        mark_position = position if exact else position - 1
        if mark_position < 0:
            return None
        return _exact_quote(index, mark_position) if exact else _stale_quote(index, mark_position)

    return resolve


__all__ = [
    "CashQuote",
    "CashQuoteResolver",
    "ScheduledCashQuote",
    "build_cash_quote_resolver",
    "replace_with_held_quote",
    "restore_scheduled_quote",
]
