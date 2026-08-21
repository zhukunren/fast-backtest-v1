from __future__ import annotations

import math


STAR_MIN_BUY_QTY = 200
STAR_BUY_STEP = 1


def is_star_market_code(stock_code: str | None) -> bool:
    if stock_code is None:
        return False
    code = str(stock_code).strip().upper()
    code_no_suffix = code.split(".", 1)[0]
    return code_no_suffix.startswith("688") or code_no_suffix.startswith("689")


def _to_int_qty(qty: object) -> int:
    try:
        q = int(math.floor(float(qty)))
    except Exception:
        return 0
    return max(q, 0)


def _default_lot(lot_size: int | None) -> int:
    try:
        lot = int(lot_size or 1)
    except Exception:
        lot = 1
    return max(lot, 1)


def buy_min_qty(stock_code: str | None, *, lot_size: int = 100) -> int:
    return STAR_MIN_BUY_QTY if is_star_market_code(stock_code) else _default_lot(lot_size)


def buy_qty_step(stock_code: str | None, *, lot_size: int = 100) -> int:
    return STAR_BUY_STEP if is_star_market_code(stock_code) else _default_lot(lot_size)


def normalize_buy_qty(stock_code: str | None, qty: object, *, lot_size: int = 100) -> int:
    q = _to_int_qty(qty)
    min_qty = buy_min_qty(stock_code, lot_size=lot_size)
    step = buy_qty_step(stock_code, lot_size=lot_size)
    if q < min_qty:
        return 0
    return (q // step) * step


def normalize_sell_qty(
    stock_code: str | None,
    qty: object,
    *,
    position_qty: int,
    lot_size: int = 100,
) -> int:
    q = min(_to_int_qty(qty), _to_int_qty(position_qty))
    if q <= 0:
        return 0
    if q >= _to_int_qty(position_qty):
        return q
    min_qty = buy_min_qty(stock_code, lot_size=lot_size)
    step = buy_qty_step(stock_code, lot_size=lot_size)
    if q < min_qty:
        return 0
    return (q // step) * step


__all__ = [
    "STAR_BUY_STEP",
    "STAR_MIN_BUY_QTY",
    "buy_min_qty",
    "buy_qty_step",
    "is_star_market_code",
    "normalize_buy_qty",
    "normalize_sell_qty",
]
