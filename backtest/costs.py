from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading.market_rules import round_price_to_tick


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    stamp_tax: float
    slippage_cost: float
    total: float


def _to_float(value: float | int | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _resolve_tc(cfg):
    if cfg is None:
        return None
    if hasattr(cfg, "transaction_cost"):
        return cfg.transaction_cost
    return cfg


def is_cost_enabled(cfg) -> bool:
    tc = _resolve_tc(cfg)
    try:
        return bool(tc.enable)
    except Exception:
        return False


def get_tc(cfg):
    return _resolve_tc(cfg)


def slippage_multiplier(side: Side, slippage_bps: float) -> float:
    slip = _to_float(slippage_bps, 0.0) / 10000.0
    if slip <= 0:
        return 1.0
    if side == "buy":
        return 1.0 + slip
    return 1.0 - slip


def apply_slippage(price: float, side: Side, slippage_bps: float) -> float:
    if price is None:
        return price
    try:
        p = float(price)
    except Exception:
        return price
    multiplier = slippage_multiplier(side, slippage_bps)
    if multiplier == 1.0:
        return p
    mode = "up" if side == "buy" else "down"
    return round_price_to_tick(p * multiplier, mode=mode)


def calc_commission(notional: float, *, rate: float, min_commission: float) -> float:
    if notional <= 0:
        return 0.0
    fee = notional * max(rate, 0.0)
    if min_commission > 0:
        fee = max(fee, min_commission)
    return fee


def calc_stamp_duty(notional: float, *, rate: float) -> float:
    if notional <= 0:
        return 0.0
    return notional * max(rate, 0.0)


def calc_trade_costs(
    notional: float,
    *,
    side: Side,
    commission_rate: float,
    min_commission: float,
    stamp_duty_rate: float,
    include_buy_commission: bool = True,
    include_sell_commission: bool = True,
) -> CostBreakdown:
    if notional <= 0:
        return CostBreakdown(commission=0.0, stamp_tax=0.0, slippage_cost=0.0, total=0.0)

    commission = 0.0
    if side == "buy" and include_buy_commission:
        commission = calc_commission(notional, rate=commission_rate, min_commission=min_commission)
    elif side == "sell" and include_sell_commission:
        commission = calc_commission(notional, rate=commission_rate, min_commission=min_commission)

    stamp_tax = 0.0
    if side == "sell" and stamp_duty_rate > 0:
        stamp_tax = calc_stamp_duty(notional, rate=stamp_duty_rate)

    total = commission + stamp_tax
    return CostBreakdown(commission=commission, stamp_tax=stamp_tax, slippage_cost=0.0, total=total)


def cost_from_config(
    cfg,
    *,
    side: Side,
    notional: float,
    slippage_cost: float = 0.0,
) -> CostBreakdown:
    tc = _resolve_tc(cfg)
    if tc is None:
        return CostBreakdown(commission=0.0, stamp_tax=0.0, slippage_cost=slippage_cost, total=slippage_cost)
    breakdown = calc_trade_costs(
        notional,
        side=side,
        commission_rate=_to_float(tc.commission_rate, 0.0),
        min_commission=_to_float(tc.min_commission, 0.0),
        stamp_duty_rate=_to_float(tc.stamp_duty_rate, 0.0),
        include_buy_commission=bool(getattr(tc, "include_buy_commission", True)),
        include_sell_commission=bool(getattr(tc, "include_sell_commission", True)),
    )
    if slippage_cost:
        total = breakdown.total + slippage_cost
        return CostBreakdown(
            commission=breakdown.commission,
            stamp_tax=breakdown.stamp_tax,
            slippage_cost=slippage_cost,
            total=total,
        )
    return breakdown
