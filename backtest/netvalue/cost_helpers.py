"""Cost and sizing helpers for net-value cash execution."""
from __future__ import annotations

import numpy as np

from core.constants import FLOAT_TOLERANCE
from core.exceptions import ConfigurationError
from trading.lot_rules import buy_qty_step, normalize_buy_qty

from ..costs import cost_from_config, get_tc, is_cost_enabled


def _trade_cost_rates_from_config(cfg) -> tuple[float, float]:
    tc = get_tc(cfg)
    if not is_cost_enabled(cfg):
        return 0.0, 0.0

    buy_rate = 0.0
    sell_rate = 0.0
    if bool(getattr(tc, "include_buy_commission", True)):
        buy_rate += float(getattr(tc, "commission_rate", 0.0))
    if bool(getattr(tc, "include_sell_commission", True)):
        sell_rate += float(getattr(tc, "commission_rate", 0.0))
    sell_rate += float(getattr(tc, "stamp_duty_rate", 0.0))
    slippage_rate = float(getattr(tc, "slippage_bps", 0.0)) / 10000.0
    if slippage_rate > 0:
        buy_rate += slippage_rate
        sell_rate += slippage_rate
    return buy_rate, sell_rate


def _require_portfolio_fraction(value: object, *, name: str) -> float:
    try:
        pct = float(value)
    except Exception as exc:
        raise ConfigurationError(f"{name} 非法，必须是 (0, 1] 区间内的数值。") from exc
    if not (0.0 < pct <= 1.0):
        raise ConfigurationError(f"{name}={pct} 非法，必须在 (0, 1] 区间内。")
    return pct


def _resolve_slot_like_single_position_ratio(
    *,
    initial_capital: float,
    portfolio_initial_capital: float | None,
    max_single_position_pct: float,
) -> float:
    """Translate portfolio-level cap into a slot-level rolling cap ratio."""
    slot_capital = float(initial_capital)
    total_capital = float(portfolio_initial_capital if portfolio_initial_capital is not None else initial_capital)
    if not np.isfinite(slot_capital) or slot_capital <= 0:
        return float(max_single_position_pct)
    if not np.isfinite(total_capital) or total_capital <= 0:
        return float(max_single_position_pct)
    slot_count = total_capital / slot_capital
    rounded_slot_count = round(slot_count)
    if np.isclose(slot_count, rounded_slot_count, rtol=0.0, atol=1e-9):
        slot_count = float(rounded_slot_count)
    slot_count = max(float(slot_count), 1.0)
    return min(float(max_single_position_pct) * slot_count, 1.0)


def _calc_order_costs(
    *,
    qty: int,
    exec_price: float,
    raw_price: float,
    side: str,
    cfg,
    cost_enabled: bool,
) -> tuple[float, float, float, float, float]:
    if qty <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    notional = qty * exec_price
    if side == "buy":
        slippage_cost = max(exec_price - raw_price, 0.0) * qty
    else:
        slippage_cost = max(raw_price - exec_price, 0.0) * qty

    commission = 0.0
    stamp_tax = 0.0
    total_cost = 0.0
    if cost_enabled:
        breakdown = cost_from_config(cfg, side=side, notional=notional, slippage_cost=0.0)
        commission = breakdown.commission
        stamp_tax = breakdown.stamp_tax
        total_cost = breakdown.total
    return notional, slippage_cost, commission, stamp_tax, total_cost


def _max_affordable_buy_qty(
    *,
    cash: float,
    qty_limit: int,
    lot_size: int,
    exec_price: float,
    open_price: float,
    cfg,
    cost_enabled: bool,
    stock_code: str | None = None,
) -> tuple[int, float, float, float, float, float]:
    qty_limit = normalize_buy_qty(stock_code, qty_limit, lot_size=lot_size)
    if cash <= FLOAT_TOLERANCE or qty_limit <= 0 or exec_price <= FLOAT_TOLERANCE:
        return 0, 0.0, 0.0, 0.0, 0.0, 0.0

    best_qty = 0
    best_tuple = (0.0, 0.0, 0.0, 0.0, 0.0)
    left = 1
    step = buy_qty_step(stock_code, lot_size=lot_size)
    right = qty_limit // step
    while left <= right:
        mid = (left + right) // 2
        qty = normalize_buy_qty(stock_code, mid * step, lot_size=lot_size)
        order_tuple = _calc_order_costs(
            qty=qty,
            exec_price=exec_price,
            raw_price=open_price,
            side="buy",
            cfg=cfg,
            cost_enabled=cost_enabled,
        )
        needed = order_tuple[0] + order_tuple[4]
        if needed <= cash + FLOAT_TOLERANCE:
            best_qty = qty
            best_tuple = order_tuple
            left = mid + 1
        else:
            right = mid - 1

    return (best_qty, *best_tuple)
