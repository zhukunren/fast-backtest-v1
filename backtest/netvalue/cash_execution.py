"""Performance-sensitive cash execution for slot-like net-value engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.constants import FLOAT_TOLERANCE
from trading.lot_rules import normalize_buy_qty

from ..constraints import is_limit_locked, is_no_trade_day, max_trade_qty
from ..costs import apply_slippage
from .cost_helpers import _calc_order_costs, _max_affordable_buy_qty, _require_portfolio_fraction
from .quote_utils import build_last_valid_mark_prices, is_valid_quote_price


def last_valid_mark_price(
    open_arr: np.ndarray,
    close_arr: np.ndarray,
) -> float | None:
    marks = build_last_valid_mark_prices(open_arr, close_arr)
    if len(marks) == 0:
        return None
    price = marks[-1]
    return float(price) if is_valid_quote_price(price) else None


def _execution_price(
    raw_price: object,
    *,
    side: str,
    slippage_bps: object,
) -> float | None:
    if not is_valid_quote_price(raw_price):
        return None
    try:
        price = apply_slippage(float(raw_price), side, slippage_bps)
    except (ArithmeticError, TypeError, ValueError, OverflowError):
        return None
    return float(price) if is_valid_quote_price(price) else None


def _resolve_text(values: np.ndarray | None, idx: int) -> str:
    if values is None or idx < 0 or idx >= len(values):
        return ""
    value = values[idx]
    if value is None:
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() == "nan" else text


@dataclass(slots=True)
class _CashExecutionContext:
    open_arr: np.ndarray
    high_arr: np.ndarray
    low_arr: np.ndarray
    close_arr: np.ndarray
    vol_arr: np.ndarray | None
    prev_close_arr: np.ndarray
    trade_date_arr: np.ndarray | None
    stock_code_arr: np.ndarray | None
    stock_name_arr: np.ndarray | None
    buy_price: np.ndarray | None
    sell_price: np.ndarray | None
    commission_arr: np.ndarray | None
    stamp_arr: np.ndarray | None
    slippage_arr: np.ndarray | None
    total_cost_arr: np.ndarray | None
    trade_return_arr: np.ndarray | None
    collect_arrays: bool
    buy_timing: str
    sell_timing: str
    lot_size: int
    cash_buffer_pct: float
    allow_partial_fill: bool
    max_gross_exposure: float
    single_position_cap_ratio: float | None
    cons: Any
    cfg: Any
    tc: Any
    cost_enabled: bool
    fill_events: list[dict[str, object]]
    order_events: list[dict[str, object]]
    fill_event_seq: int = 0
    order_event_seq: int = 0

    @property
    def default_sell_price_type(self) -> str:
        return "open" if self.sell_timing == "next_open" else "close"

    def append_fill_event(
        self,
        *,
        idx: int,
        side: str,
        qty: int,
        exec_price: float,
        commission: float,
        stamp_tax: float,
        slippage_cost: float,
        total_cost: float,
        cash_before: float,
        cash_after: float,
        shares_before: int,
        shares_after: int,
        exec_price_type: str,
        note: str = "",
    ) -> None:
        if not self.collect_arrays or qty <= 0:
            return
        self.fill_event_seq += 1
        self.fill_events.append(
            {
                "_slot_event_seq": self.fill_event_seq,
                "trade_date": _resolve_text(self.trade_date_arr, idx),
                "stock_code": _resolve_text(self.stock_code_arr, idx),
                "stock_name": _resolve_text(self.stock_name_arr, idx),
                "side": str(side),
                "qty": int(qty),
                "price": float(exec_price),
                "commission": float(commission),
                "stamp_tax": float(stamp_tax),
                "slippage_cost": float(slippage_cost),
                "total_cost": float(total_cost),
                "cash_delta": float(cash_after) - float(cash_before),
                "cash_before": float(cash_before),
                "cash_after": float(cash_after),
                "shares_before": int(shares_before),
                "shares_after": int(shares_after),
                "exec_price_type": str(exec_price_type),
                "source": "strategy",
                "note": str(note or ""),
            }
        )

    def append_order_event(
        self,
        *,
        idx: int,
        side: str,
        qty: int,
        filled_qty: int,
        price: float,
        status: str,
        reason: str,
        cash_before: float,
        cash_after: float,
        shares_before: int,
        shares_after: int,
        exec_price_type: str,
        note: str = "",
    ) -> None:
        if not self.collect_arrays:
            return
        self.order_event_seq += 1
        self.order_events.append(
            {
                "_slot_event_seq": self.order_event_seq,
                "trade_date": _resolve_text(self.trade_date_arr, idx),
                "stock_code": _resolve_text(self.stock_code_arr, idx),
                "stock_name": _resolve_text(self.stock_name_arr, idx),
                "side": str(side),
                "qty": int(max(qty, 0)),
                "filled_qty": int(max(filled_qty, 0)),
                "price": float(price),
                "status": str(status),
                "reason": str(reason or ""),
                "cash_before": float(cash_before),
                "cash_after": float(cash_after),
                "shares_before": int(shares_before),
                "shares_after": int(shares_after),
                "exec_price_type": str(exec_price_type),
                "source": "strategy",
                "note": str(note or ""),
            }
        )


def _previous_close(ctx: _CashExecutionContext, idx: int) -> float | None:
    try:
        value = float(ctx.prev_close_arr[idx])
    except (IndexError, TypeError, ValueError):
        return None
    return value if np.isfinite(value) and value > FLOAT_TOLERANCE else None


def _buy_budget(ctx: _CashExecutionContext, *, cash: float, shares: int, close_price: float) -> float:
    if not np.isfinite(cash) or cash <= 0:
        return 0.0
    if shares > 0 and not is_valid_quote_price(close_price):
        return 0.0
    if shares <= 0:
        close_price = 0.0
    equity = cash + shares * close_price
    invested = shares * close_price
    remaining_target = max(equity * ctx.max_gross_exposure - invested, 0.0)
    budget = min(cash * (1.0 - ctx.cash_buffer_pct), remaining_target)
    if ctx.single_position_cap_ratio is None:
        return budget
    remaining_cap = max(equity * ctx.single_position_cap_ratio - invested, 0.0)
    return min(budget, remaining_cap)


def _buy_qty(ctx: _CashExecutionContext, *, code: str | None, budget: float, price: float) -> int:
    if not np.isfinite(budget) or budget <= 0 or not is_valid_quote_price(price):
        return 0
    return normalize_buy_qty(code, int(budget // price), lot_size=ctx.lot_size)


def _is_untradable(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    side: str,
    stock_code: str | None,
    stock_name: str | None,
) -> str | None:
    volume = ctx.vol_arr[idx] if ctx.vol_arr is not None else None
    if ctx.cons.enable_no_trade_day and is_no_trade_day(
        open_price=ctx.open_arr[idx],
        high_price=ctx.high_arr[idx],
        low_price=ctx.low_arr[idx],
        close_price=ctx.close_arr[idx],
        volume=volume,
    ):
        return "no_trade_day"
    if ctx.cons.enable_limit and is_limit_locked(
        open_price=ctx.open_arr[idx],
        high_price=ctx.high_arr[idx],
        low_price=ctx.low_arr[idx],
        close_price=ctx.close_arr[idx],
        prev_close=_previous_close(ctx, idx),
        limit_up_pct=ctx.cons.limit_up_pct,
        limit_down_pct=ctx.cons.limit_down_pct,
        side=side,
        stock_code=stock_code,
        stock_name=stock_name,
    ):
        return "limit_locked"
    return None


def _skip_order(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    side: str,
    qty: int,
    price: float,
    reason: str,
    cash: float,
    shares: int,
    price_type: str,
) -> None:
    ctx.append_order_event(
        idx=idx,
        side=side,
        qty=qty,
        filled_qty=0,
        price=price,
        status="skipped",
        reason=reason,
        cash_before=cash,
        cash_after=cash,
        shares_before=shares,
        shares_after=shares,
        exec_price_type=price_type,
    )


def _execute_buy(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    stock_code: str | None,
    stock_name: str | None,
    cash: float,
    shares: int,
    cycle_buy_cash: float,
    cycle_sell_cash: float,
    active_stock_code: str | None,
) -> tuple[float, int, float, float, str | None, bool]:
    price_type = "close" if ctx.buy_timing == "signal_close" else "open"
    raw_price = ctx.close_arr[idx] if ctx.buy_timing == "signal_close" else ctx.open_arr[idx]
    exec_price = _execution_price(
        raw_price,
        side="buy",
        slippage_bps=ctx.tc.slippage_bps if ctx.cost_enabled else 0.0,
    )
    if exec_price is None:
        _skip_order(
            ctx,
            idx=idx,
            side="buy",
            qty=0,
            price=raw_price,
            reason="invalid_price",
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
        return cash, shares, cycle_buy_cash, cycle_sell_cash, active_stock_code, False

    budget = _buy_budget(ctx, cash=cash, shares=shares, close_price=ctx.close_arr[idx])
    requested_qty = _buy_qty(ctx, code=stock_code, budget=budget, price=exec_price)
    rejection = _is_untradable(
        ctx,
        idx=idx,
        side="buy",
        stock_code=stock_code,
        stock_name=stock_name,
    )
    if rejection:
        _skip_order(
            ctx,
            idx=idx,
            side="buy",
            qty=requested_qty,
            price=exec_price,
            reason=rejection,
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
        return cash, shares, cycle_buy_cash, cycle_sell_cash, active_stock_code, False

    qty = requested_qty
    if ctx.cons.enable_volume_limit and ctx.vol_arr is not None:
        max_qty = max_trade_qty(
            volume=ctx.vol_arr[idx],
            participation_rate=float(ctx.cons.max_participation_rate),
            lot_size=ctx.lot_size,
            stock_code=stock_code,
            side="buy",
        )
        qty = min(qty, max_qty) if max_qty > 0 else 0
    if requested_qty > 0 and qty <= 0 and ctx.cons.enable_volume_limit and ctx.vol_arr is not None:
        _skip_order(
            ctx,
            idx=idx,
            side="buy",
            qty=requested_qty,
            price=exec_price,
            reason="volume_limit",
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
    elif requested_qty <= 0:
        reason = "below_lot_size" if budget > 0 and exec_price > 0 else "qty<=0"
        _skip_order(
            ctx,
            idx=idx,
            side="buy",
            qty=0,
            price=exec_price,
            reason=reason,
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
    if qty <= 0:
        return cash, shares, cycle_buy_cash, cycle_sell_cash, active_stock_code, False

    requested_qty = int(qty)
    costs = _calc_order_costs(
        qty=qty,
        exec_price=exec_price,
        raw_price=raw_price,
        side="buy",
        cfg=ctx.cfg,
        cost_enabled=ctx.cost_enabled,
    )
    notional, slippage_cost, commission, stamp_tax, total_cost = costs
    needed = notional + total_cost
    if needed > cash and ctx.allow_partial_fill:
        affordable = _max_affordable_buy_qty(
            cash=cash,
            qty_limit=qty,
            lot_size=ctx.lot_size,
            exec_price=exec_price,
            open_price=raw_price,
            cfg=ctx.cfg,
            cost_enabled=ctx.cost_enabled,
            stock_code=stock_code,
        )
        qty, notional, slippage_cost, commission, stamp_tax, total_cost = affordable
        needed = notional + total_cost if qty > 0 else 0.0
    elif needed > cash:
        ctx.append_order_event(
            idx=idx,
            side="buy",
            qty=requested_qty,
            filled_qty=0,
            price=exec_price,
            status="rejected",
            reason="insufficient_cash",
            cash_before=cash,
            cash_after=cash,
            shares_before=shares,
            shares_after=shares,
            exec_price_type=price_type,
        )
    if needed <= 0 or needed > cash or qty <= 0:
        if requested_qty > 0 and ctx.allow_partial_fill and qty <= 0:
            ctx.append_order_event(
                idx=idx,
                side="buy",
                qty=requested_qty,
                filled_qty=0,
                price=exec_price,
                status="rejected",
                reason="insufficient_cash",
                cash_before=cash,
                cash_after=cash,
                shares_before=shares,
                shares_after=shares,
                exec_price_type=price_type,
            )
        return cash, shares, cycle_buy_cash, cycle_sell_cash, active_stock_code, False

    cash_before = cash
    shares_before = shares
    if shares <= 0:
        cycle_buy_cash = 0.0
        cycle_sell_cash = 0.0
    cash -= needed
    shares += qty
    cycle_buy_cash += needed
    if ctx.collect_arrays:
        assert ctx.buy_price is not None
        assert ctx.commission_arr is not None
        assert ctx.stamp_arr is not None
        assert ctx.slippage_arr is not None
        assert ctx.total_cost_arr is not None
        ctx.buy_price[idx] = exec_price
        ctx.commission_arr[idx] = commission
        ctx.stamp_arr[idx] = stamp_tax
        ctx.slippage_arr[idx] = slippage_cost
        ctx.total_cost_arr[idx] = total_cost
    ctx.append_fill_event(
        idx=idx,
        side="buy",
        qty=qty,
        exec_price=exec_price,
        commission=commission,
        stamp_tax=stamp_tax,
        slippage_cost=slippage_cost,
        total_cost=total_cost,
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=shares,
        exec_price_type=price_type,
    )
    if shares_before <= 0:
        active_stock_code = stock_code
    ctx.append_order_event(
        idx=idx,
        side="buy",
        qty=requested_qty,
        filled_qty=qty,
        price=exec_price,
        status="filled",
        reason="partial_fill" if qty < requested_qty else "",
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=shares,
        exec_price_type=price_type,
    )
    return cash, shares, cycle_buy_cash, cycle_sell_cash, active_stock_code, True


def _execute_sell(
    ctx: _CashExecutionContext,
    *,
    idx: int,
    stock_code: str | None,
    stock_name: str | None,
    cash: float,
    shares: int,
    pending_sell: bool,
    pending_sell_price_type: str,
    active_stock_code: str | None,
    cycle_buy_cash: float,
    cycle_sell_cash: float,
) -> tuple[float, int, bool, str, str | None, float, float]:
    price_type = pending_sell_price_type if pending_sell else ctx.default_sell_price_type
    raw_price = ctx.open_arr[idx] if price_type == "open" else ctx.close_arr[idx]
    exec_price = _execution_price(
        raw_price,
        side="sell",
        slippage_bps=ctx.tc.slippage_bps if ctx.cost_enabled else 0.0,
    )
    if exec_price is None:
        _skip_order(
            ctx,
            idx=idx,
            side="sell",
            qty=shares,
            price=raw_price,
            reason="invalid_price",
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
        return cash, shares, True, "open", active_stock_code, cycle_buy_cash, cycle_sell_cash

    rejection = _is_untradable(
        ctx,
        idx=idx,
        side="sell",
        stock_code=stock_code,
        stock_name=stock_name,
    )
    if rejection:
        _skip_order(
            ctx,
            idx=idx,
            side="sell",
            qty=shares,
            price=raw_price,
            reason=rejection,
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
        return cash, shares, True, "open", active_stock_code, cycle_buy_cash, cycle_sell_cash

    requested_qty = int(shares)
    qty = shares
    if ctx.cons.enable_volume_limit and ctx.vol_arr is not None:
        max_qty = max_trade_qty(
            volume=ctx.vol_arr[idx],
            participation_rate=float(ctx.cons.max_participation_rate),
            lot_size=ctx.lot_size,
            stock_code=stock_code,
            side="sell",
            position_qty=shares,
        )
        qty = min(qty, max_qty) if max_qty > 0 else 0
    if qty <= 0:
        _skip_order(
            ctx,
            idx=idx,
            side="sell",
            qty=requested_qty,
            price=exec_price,
            reason="volume_limit",
            cash=cash,
            shares=shares,
            price_type=price_type,
        )
        return cash, shares, True, "open", active_stock_code, cycle_buy_cash, cycle_sell_cash

    cash_before = cash
    shares_before = shares
    notional, slippage_cost, commission, stamp_tax, total_cost = _calc_order_costs(
        qty=qty,
        exec_price=exec_price,
        raw_price=raw_price,
        side="sell",
        cfg=ctx.cfg,
        cost_enabled=ctx.cost_enabled,
    )
    cash += notional - total_cost
    cycle_sell_cash += notional - total_cost
    shares -= qty
    if ctx.collect_arrays:
        assert ctx.sell_price is not None
        assert ctx.commission_arr is not None
        assert ctx.stamp_arr is not None
        assert ctx.slippage_arr is not None
        assert ctx.total_cost_arr is not None
        ctx.sell_price[idx] = exec_price
        ctx.commission_arr[idx] += commission
        ctx.stamp_arr[idx] += stamp_tax
        ctx.slippage_arr[idx] += slippage_cost
        ctx.total_cost_arr[idx] += total_cost
    ctx.append_fill_event(
        idx=idx,
        side="sell",
        qty=qty,
        exec_price=exec_price,
        commission=commission,
        stamp_tax=stamp_tax,
        slippage_cost=slippage_cost,
        total_cost=total_cost,
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=shares,
        exec_price_type=price_type,
    )
    ctx.append_order_event(
        idx=idx,
        side="sell",
        qty=requested_qty,
        filled_qty=qty,
        price=exec_price,
        status="filled",
        reason="partial_fill" if qty < requested_qty else "",
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=shares,
        exec_price_type=price_type,
    )
    pending_sell = shares > 0
    pending_sell_price_type = "open" if pending_sell else ctx.default_sell_price_type
    if shares <= 0:
        active_stock_code = None
        if cycle_buy_cash > FLOAT_TOLERANCE:
            if ctx.collect_arrays:
                assert ctx.trade_return_arr is not None
                ctx.trade_return_arr[idx] = cycle_sell_cash / cycle_buy_cash - 1.0
            cycle_buy_cash = 0.0
            cycle_sell_cash = 0.0
    return (
        cash,
        shares,
        pending_sell,
        pending_sell_price_type,
        active_stock_code,
        cycle_buy_cash,
        cycle_sell_cash,
    )


def _force_window_close(
    ctx: _CashExecutionContext,
    *,
    cash: float,
    shares: int,
    cycle_buy_cash: float,
    cycle_sell_cash: float,
) -> tuple[float, int]:
    idx = len(ctx.close_arr) - 1
    raw_price = ctx.close_arr[idx]
    exec_price = _execution_price(
        raw_price,
        side="sell",
        slippage_bps=ctx.tc.slippage_bps if ctx.cost_enabled else 0.0,
    )
    if exec_price is None:
        _skip_order(
            ctx,
            idx=idx,
            side="sell",
            qty=shares,
            price=raw_price,
            reason="invalid_price",
            cash=cash,
            shares=shares,
            price_type="close",
        )
        return cash, shares

    qty = shares
    cash_before = cash
    shares_before = shares
    notional, slippage_cost, commission, stamp_tax, total_cost = _calc_order_costs(
        qty=qty,
        exec_price=exec_price,
        raw_price=raw_price,
        side="sell",
        cfg=ctx.cfg,
        cost_enabled=ctx.cost_enabled,
    )
    cash += notional - total_cost
    cycle_sell_cash += notional - total_cost
    shares = 0
    ctx.append_fill_event(
        idx=idx,
        side="sell",
        qty=qty,
        exec_price=exec_price,
        commission=commission,
        stamp_tax=stamp_tax,
        slippage_cost=slippage_cost,
        total_cost=total_cost,
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=0,
        exec_price_type="close",
        note="forced_window_close",
    )
    ctx.append_order_event(
        idx=idx,
        side="sell",
        qty=qty,
        filled_qty=qty,
        price=exec_price,
        status="filled",
        reason="forced_window_close",
        cash_before=cash_before,
        cash_after=cash,
        shares_before=shares_before,
        shares_after=0,
        exec_price_type="close",
        note="forced_window_close",
    )
    if ctx.collect_arrays:
        assert ctx.sell_price is not None
        assert ctx.commission_arr is not None
        assert ctx.stamp_arr is not None
        assert ctx.slippage_arr is not None
        assert ctx.total_cost_arr is not None
        assert ctx.trade_return_arr is not None
        ctx.sell_price[idx] = exec_price
        ctx.commission_arr[idx] += commission
        ctx.stamp_arr[idx] += stamp_tax
        ctx.slippage_arr[idx] += slippage_cost
        ctx.total_cost_arr[idx] += total_cost
        if cycle_buy_cash > FLOAT_TOLERANCE:
            ctx.trade_return_arr[idx] = cycle_sell_cash / cycle_buy_cash - 1.0
    return cash, shares


def _build_context(
    *,
    open_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    close_arr: np.ndarray,
    vol_arr: np.ndarray | None,
    trade_date_arr: np.ndarray | None,
    stock_code_arr: np.ndarray | None,
    stock_name_arr: np.ndarray | None,
    buy_price: np.ndarray | None,
    sell_price: np.ndarray | None,
    commission_arr: np.ndarray | None,
    stamp_arr: np.ndarray | None,
    slippage_arr: np.ndarray | None,
    total_cost_arr: np.ndarray | None,
    trade_return_arr: np.ndarray | None,
    collect_arrays: bool,
    buy_timing: str,
    sell_timing: str,
    lot_size: int,
    cash_buffer_pct: float,
    allow_partial_fill: bool,
    cons: Any,
    cfg: Any,
    tc: Any,
    cost_enabled: bool,
    max_single_position_ratio: float | None,
    fill_events: list[dict[str, object]],
    order_events: list[dict[str, object]],
    pre_close_arr: np.ndarray | None = None,
) -> _CashExecutionContext:
    prev_close_arr = np.empty(len(close_arr), dtype="float64")
    if len(close_arr) > 0:
        prev_close_arr[0] = np.nan
    if len(close_arr) > 1:
        prev_close_arr[1:] = close_arr[:-1]
    if pre_close_arr is not None:
        supplied = np.asarray(pre_close_arr, dtype="float64")
        if len(supplied) != len(close_arr):
            raise ValueError("pre_close_arr and close_arr must have the same length")
        valid_supplied = np.isfinite(supplied) & (supplied > FLOAT_TOLERANCE)
        prev_close_arr[valid_supplied] = supplied[valid_supplied]
    return _CashExecutionContext(
        open_arr=open_arr,
        high_arr=high_arr,
        low_arr=low_arr,
        close_arr=close_arr,
        vol_arr=vol_arr,
        prev_close_arr=prev_close_arr,
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
        buy_timing=str(buy_timing).strip().lower() or "next_open",
        sell_timing=str(sell_timing).strip().lower(),
        lot_size=lot_size,
        cash_buffer_pct=cash_buffer_pct,
        allow_partial_fill=allow_partial_fill,
        max_gross_exposure=_require_portfolio_fraction(
            getattr(cfg.portfolio, "max_gross_exposure", 1.0),
            name="portfolio.max_gross_exposure",
        ),
        single_position_cap_ratio=(None if max_single_position_ratio is None else float(max_single_position_ratio)),
        cons=cons,
        cfg=cfg,
        tc=tc,
        cost_enabled=cost_enabled,
        fill_events=fill_events,
        order_events=order_events,
    )
