"""Lot-feasible target helpers shared by synthetic risk ledgers."""
from __future__ import annotations

from trading.lot_rules import (
    buy_min_qty,
    buy_qty_step,
    normalize_buy_qty,
    normalize_sell_qty,
)


def normalize_risk_sell_qty(
    stock_code: str,
    qty: int,
    *,
    position_qty: int,
    lot_size: int,
) -> int:
    position = max(int(position_qty), 0)
    requested = min(max(int(qty), 0), position)
    if requested <= 0 or requested >= position:
        return requested
    step = buy_qty_step(stock_code, lot_size=lot_size)
    rounded_up = max(
        buy_min_qty(stock_code, lot_size=lot_size),
        ((requested + step - 1) // step) * step,
    )
    return normalize_sell_qty(
        stock_code,
        rounded_up,
        position_qty=position,
        lot_size=lot_size,
    )


def feasible_risk_target_quantity(
    stock_code: str,
    *,
    current_qty: int,
    target_limit: int,
    lot_size: int,
) -> int:
    current = max(int(current_qty), 0)
    target = max(int(target_limit), 0)
    if target < current:
        sell_qty = normalize_risk_sell_qty(
            stock_code,
            current - target,
            position_qty=current,
            lot_size=lot_size,
        )
        return current - sell_qty
    if target > current:
        return current + normalize_buy_qty(
            stock_code,
            target - current,
            lot_size=lot_size,
        )
    return current


__all__ = ["feasible_risk_target_quantity", "normalize_risk_sell_qty"]
