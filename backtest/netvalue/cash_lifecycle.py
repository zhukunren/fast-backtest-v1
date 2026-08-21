"""Slot ownership lifecycle for cash-accounted execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.constants import FLOAT_TOLERANCE
from core.exceptions import DataValidationError

from .cash_execution import (
    _CashExecutionContext,
    _build_context,
    _execute_buy,
    _execute_sell,
    _force_window_close,
    last_valid_mark_price,
    _resolve_text,
    _skip_order,
)
from .cash_quotes import (
    CashQuoteResolver,
    ScheduledCashQuote,
    replace_with_held_quote,
    restore_scheduled_quote,
)


@dataclass(slots=True)
class _CashExecutionState:
    cash: float
    shares: int
    pending_sell: bool
    pending_sell_price_type: str
    active_stock_code: str | None
    cycle_buy_cash: float
    cycle_sell_cash: float
    last_sell_attempt_date: str | None = None


@dataclass(frozen=True, slots=True)
class _ExecutionRow:
    stock_code: str | None
    stock_name: str | None
    trade_date: str
    displaced: bool = False
    held_quote_tradable: bool = True
    scheduled_quote: ScheduledCashQuote | None = None


def _apply_resolved_pre_close(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    stock_code: str | None,
    trade_date: str,
    quote_resolver: CashQuoteResolver | None,
) -> None:
    if quote_resolver is None or not stock_code or not trade_date:
        return
    quote = quote_resolver(stock_code, trade_date)
    if quote is None:
        return
    try:
        previous_close = float(quote.previous_close)
    except (TypeError, ValueError):
        return
    if np.isfinite(previous_close) and previous_close > FLOAT_TOLERANCE:
        ctx.prev_close_arr[idx] = previous_close


def _resolve_execution_row(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    state: _CashExecutionState,
    quote_resolver: CashQuoteResolver | None,
) -> _ExecutionRow:
    stock_code = _resolve_text(ctx.stock_code_arr, idx) or None
    stock_name = _resolve_text(ctx.stock_name_arr, idx) or None
    trade_date = _resolve_text(ctx.trade_date_arr, idx)
    active_stock_code = state.active_stock_code
    _apply_resolved_pre_close(
        ctx,
        idx=idx,
        stock_code=stock_code,
        trade_date=trade_date,
        quote_resolver=quote_resolver,
    )
    if state.shares <= 0 or ctx.stock_code_arr is None or not active_stock_code:
        return _ExecutionRow(stock_code, stock_name, trade_date)
    if stock_code == active_stock_code:
        return _ExecutionRow(stock_code, stock_name, trade_date)
    if not state.pending_sell or quote_resolver is None:
        raise DataValidationError(
            "slot cash executor detected stock_code switch while a sell is pending or shares remain: "
            f"held={active_stock_code!r}, row={stock_code!r}, trade_date={trade_date!r}. "
            "A held-quote resolver is required to preserve slot ownership."
        )
    held_quote = quote_resolver(active_stock_code, trade_date)
    if held_quote is None:
        raise DataValidationError(
            "slot cash executor cannot resolve held stock quote: "
            f"held={active_stock_code!r}, trade_date={trade_date!r}."
        )
    scheduled_quote = replace_with_held_quote(
        ctx,
        idx=idx,
        active_stock_code=active_stock_code,
        held_quote=held_quote,
    )
    return _ExecutionRow(
        active_stock_code,
        held_quote.stock_name or None,
        trade_date,
        displaced=True,
        held_quote_tradable=held_quote.tradable,
        scheduled_quote=scheduled_quote,
    )


def _normalize_flat_state(ctx: _CashExecutionContext, state: _CashExecutionState) -> None:
    if state.shares > 0:
        return
    if state.pending_sell:
        state.pending_sell = False
        state.pending_sell_price_type = ctx.default_sell_price_type
    state.active_stock_code = None


def _ensure_active_stock_code(
    ctx: _CashExecutionContext,
    state: _CashExecutionState,
    row: _ExecutionRow,
) -> None:
    if state.shares > 0 and ctx.stock_code_arr is not None and state.active_stock_code is None:
        state.active_stock_code = row.stock_code


def _resolve_execution_flags(
    *,
    has_position: bool,
    entry_requested: bool,
    exit_requested: bool,
    allow_intraday: bool,
    normalized_sell_timing: str,
) -> tuple[bool, bool, bool]:
    if has_position:
        return False, exit_requested, False
    if not entry_requested:
        return False, False, False
    if not exit_requested or allow_intraday:
        return True, exit_requested, False
    if normalized_sell_timing == "signal_close":
        return True, True, False
    return True, False, True


def _execute_requested_buy(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    row: _ExecutionRow,
    state: _CashExecutionState,
    buy_exec: bool,
    pending_sell_on_buy: bool,
) -> None:
    if not buy_exec:
        return
    (
        state.cash,
        state.shares,
        state.cycle_buy_cash,
        state.cycle_sell_cash,
        state.active_stock_code,
        buy_filled,
    ) = _execute_buy(
        ctx,
        idx=idx,
        stock_code=row.stock_code,
        stock_name=row.stock_name,
        cash=state.cash,
        shares=state.shares,
        cycle_buy_cash=state.cycle_buy_cash,
        cycle_sell_cash=state.cycle_sell_cash,
        active_stock_code=state.active_stock_code,
    )
    if buy_filled and pending_sell_on_buy:
        state.pending_sell = True
        state.pending_sell_price_type = "open"


def _skip_untradable_held_sell(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    state: _CashExecutionState,
) -> None:
    price_type = state.pending_sell_price_type if state.pending_sell else ctx.default_sell_price_type
    raw_price = ctx.open_arr[idx] if price_type == "open" else ctx.close_arr[idx]
    _skip_order(
        ctx,
        idx=idx,
        side="sell",
        qty=state.shares,
        price=raw_price,
        reason="missing_quote",
        cash=state.cash,
        shares=state.shares,
        price_type=price_type,
    )
    state.pending_sell = True
    state.pending_sell_price_type = "open"


def _execute_requested_sell(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    row: _ExecutionRow,
    state: _CashExecutionState,
    sell_exec: bool,
) -> None:
    if not sell_exec or state.shares <= 0:
        return
    state.last_sell_attempt_date = row.trade_date or state.last_sell_attempt_date
    if row.displaced and not row.held_quote_tradable:
        _skip_untradable_held_sell(ctx, idx=idx, state=state)
        return
    (
        state.cash,
        state.shares,
        state.pending_sell,
        state.pending_sell_price_type,
        state.active_stock_code,
        state.cycle_buy_cash,
        state.cycle_sell_cash,
    ) = _execute_sell(
        ctx,
        idx=idx,
        stock_code=row.stock_code,
        stock_name=row.stock_name,
        cash=state.cash,
        shares=state.shares,
        pending_sell=state.pending_sell,
        pending_sell_price_type=state.pending_sell_price_type,
        active_stock_code=state.active_stock_code,
        cycle_buy_cash=state.cycle_buy_cash,
        cycle_sell_cash=state.cycle_sell_cash,
    )


def _finalize_execution_row(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    row: _ExecutionRow,
    state: _CashExecutionState,
    entry: np.ndarray,
    suppressed_signal_indices: list[int],
) -> None:
    if not row.displaced:
        return
    if state.shares > 0:
        suppressed_signal_indices.append(idx)
        if ctx.buy_timing == "next_open" and idx + 1 < len(entry):
            entry[idx + 1] = False
        return
    if row.scheduled_quote is not None:
        restore_scheduled_quote(ctx, idx=idx, scheduled_quote=row.scheduled_quote)


def _record_execution_state(
    *,
    idx: int,
    state: _CashExecutionState,
    shares_arr: np.ndarray | None,
    cash_arr: np.ndarray | None,
) -> None:
    if shares_arr is None or cash_arr is None:
        return
    shares_arr[idx] = state.shares
    cash_arr[idx] = state.cash


def simulate_cash_execution(
    *,
    open_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    close_arr: np.ndarray,
    vol_arr: np.ndarray | None,
    trade_date_arr: np.ndarray | None,
    stock_code_arr: np.ndarray | None,
    stock_name_arr: np.ndarray | None,
    entry: np.ndarray,
    exit_: np.ndarray,
    initial_capital: float,
    allow_intraday: bool,
    buy_timing: str,
    sell_timing: str,
    lot_size: int,
    cash_buffer_pct: float,
    allow_partial_fill: bool,
    cons: Any,
    cfg: Any,
    tc: Any,
    cost_enabled: bool,
    max_single_position_ratio: float | None = None,
    quote_resolver: CashQuoteResolver | None = None,
    collect_arrays: bool = True,
    pre_close_arr: np.ndarray | None = None,
) -> dict[str, Any]:
    n = len(open_arr)
    normalized_sell_timing = str(sell_timing).strip().lower()
    state = _CashExecutionState(
        cash=float(initial_capital),
        shares=0,
        pending_sell=False,
        pending_sell_price_type=("open" if normalized_sell_timing == "next_open" else "close"),
        active_stock_code=None,
        cycle_buy_cash=0.0,
        cycle_sell_cash=0.0,
    )
    shares_arr = np.zeros(n, dtype=int) if collect_arrays else None
    cash_arr = np.zeros(n, dtype="float64") if collect_arrays else None
    buy_price = np.full(n, np.nan) if collect_arrays else None
    sell_price = np.full(n, np.nan) if collect_arrays else None
    commission_arr = np.zeros(n, dtype="float64") if collect_arrays else None
    stamp_arr = np.zeros(n, dtype="float64") if collect_arrays else None
    slippage_arr = np.zeros(n, dtype="float64") if collect_arrays else None
    total_cost_arr = np.zeros(n, dtype="float64") if collect_arrays else None
    trade_return_arr = np.full(n, np.nan, dtype="float64") if collect_arrays else None
    fill_events: list[dict[str, object]] = []
    order_events: list[dict[str, object]] = []
    suppressed_signal_indices: list[int] = []
    ctx = _build_context(
        open_arr=open_arr,
        high_arr=high_arr,
        low_arr=low_arr,
        close_arr=close_arr,
        vol_arr=vol_arr,
        trade_date_arr=trade_date_arr,
        stock_code_arr=stock_code_arr,
        stock_name_arr=stock_name_arr,
        buy_price=buy_price,
        sell_price=sell_price,
        commission_arr=commission_arr,
        stamp_arr=stamp_arr,
        slippage_arr=slippage_arr,
        total_cost_arr=total_cost_arr,
        trade_return_arr=trade_return_arr,
        collect_arrays=collect_arrays,
        buy_timing=buy_timing,
        sell_timing=sell_timing,
        lot_size=lot_size,
        cash_buffer_pct=cash_buffer_pct,
        allow_partial_fill=allow_partial_fill,
        cons=cons,
        cfg=cfg,
        tc=tc,
        cost_enabled=cost_enabled,
        max_single_position_ratio=max_single_position_ratio,
        fill_events=fill_events,
        order_events=order_events,
        pre_close_arr=pre_close_arr,
    )

    for idx in range(n):
        _normalize_flat_state(ctx, state)
        row = _resolve_execution_row(
            ctx,
            idx=idx,
            state=state,
            quote_resolver=quote_resolver,
        )
        _ensure_active_stock_code(ctx, state, row)
        entry_requested = bool(entry[idx]) and not row.displaced
        exit_requested = bool(exit_[idx]) or state.pending_sell
        repeated_sell_date = (
            state.pending_sell and bool(row.trade_date) and state.last_sell_attempt_date == row.trade_date
        )
        buy_exec, sell_exec, pending_sell_on_buy = _resolve_execution_flags(
            has_position=state.shares > 0,
            entry_requested=entry_requested,
            exit_requested=exit_requested and not repeated_sell_date,
            allow_intraday=allow_intraday,
            normalized_sell_timing=normalized_sell_timing,
        )
        _execute_requested_buy(
            ctx,
            idx=idx,
            row=row,
            state=state,
            buy_exec=buy_exec,
            pending_sell_on_buy=pending_sell_on_buy,
        )
        _execute_requested_sell(
            ctx,
            idx=idx,
            row=row,
            state=state,
            sell_exec=sell_exec,
        )
        _finalize_execution_row(
            ctx,
            idx=idx,
            row=row,
            state=state,
            entry=entry,
            suppressed_signal_indices=suppressed_signal_indices,
        )
        _record_execution_state(
            idx=idx,
            state=state,
            shares_arr=shares_arr,
            cash_arr=cash_arr,
        )

    if n > 0 and normalized_sell_timing == "next_open" and state.shares > 0:
        state.cash, state.shares = _force_window_close(
            ctx,
            cash=state.cash,
            shares=state.shares,
            cycle_buy_cash=state.cycle_buy_cash,
            cycle_sell_cash=state.cycle_sell_cash,
        )
        if collect_arrays:
            assert shares_arr is not None
            assert cash_arr is not None
            shares_arr[-1] = state.shares
            cash_arr[-1] = state.cash
        if state.shares > 0 and state.cycle_buy_cash > FLOAT_TOLERANCE:
            mark_price = last_valid_mark_price(ctx.open_arr, ctx.close_arr)
            if mark_price is not None and collect_arrays:
                assert trade_return_arr is not None
                marked_value = state.cycle_sell_cash + state.shares * mark_price
                trade_return_arr[-1] = marked_value / state.cycle_buy_cash - 1.0
    elif collect_arrays and n > 0 and state.shares > 0 and state.cycle_buy_cash > FLOAT_TOLERANCE:
        assert trade_return_arr is not None
        mark_price = last_valid_mark_price(ctx.open_arr, ctx.close_arr)
        if mark_price is not None:
            marked_value = state.cycle_sell_cash + state.shares * mark_price
            trade_return_arr[-1] = marked_value / state.cycle_buy_cash - 1.0

    if collect_arrays:
        return {
            "shares": shares_arr,
            "cash": cash_arr,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "commission": commission_arr,
            "stamp_tax": stamp_arr,
            "slippage_cost": slippage_arr,
            "total_cost": total_cost_arr,
            "trade_return": trade_return_arr,
            "fill_events": fill_events,
            "order_events": order_events,
            "prev_close_arr": ctx.prev_close_arr,
            "suppressed_signal_indices": suppressed_signal_indices,
        }
    mark_price = last_valid_mark_price(ctx.open_arr, ctx.close_arr)
    marked_position_value = state.shares * mark_price if mark_price is not None else 0.0
    final_equity = state.cash + marked_position_value
    return {"final_equity": final_equity}


__all__ = ["simulate_cash_execution"]
