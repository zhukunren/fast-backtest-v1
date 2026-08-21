from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["buy", "sell"]
OrderStatus = Literal["new", "filled", "rejected", "skipped"]


@dataclass
class Order:
    order_id: str
    trade_date: str
    stock_code: str
    side: Side
    qty: int
    price: float
    status: OrderStatus
    reason: str = ""


@dataclass
class Fill:
    order_id: str
    trade_date: str
    stock_code: str
    side: Side
    qty: int
    price: float
    commission: float
    stamp_tax: float
    slippage_cost: float
    total_cost: float
    cash_delta: float
    note: str = ""


@dataclass
class Position:
    stock_code: str
    qty: int
    avg_cost: float
    last_price: float
    last_date: Optional[str] = None
