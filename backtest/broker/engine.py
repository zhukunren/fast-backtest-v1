from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from typing import Dict, Literal, Optional

from .order import Order, Fill, Position
from ..costs import apply_slippage, cost_from_config, is_cost_enabled, get_tc
from trading.lot_rules import buy_qty_step, normalize_buy_qty, normalize_sell_qty

logger = logging.getLogger(__name__)


def _finite_positive_price(value: object) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _finite_nonnegative(value: float | int | str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _execution_price(
    price: float,
    *,
    side: Literal["buy", "sell"],
    slippage_bps: float | int | str | None,
) -> float:
    valid_price = _finite_positive_price(price)
    valid_slippage = _finite_nonnegative(slippage_bps)
    if valid_price is None or valid_slippage is None:
        return 0.0
    try:
        adjusted = apply_slippage(valid_price, side, valid_slippage)
    except (ArithmeticError, TypeError, ValueError):
        return 0.0
    return _finite_positive_price(adjusted) or 0.0


def _finite_sum(*values: float) -> float | None:
    try:
        total = float(math.fsum(values))
    except (OverflowError, ValueError):
        return None
    return total if math.isfinite(total) else None


@dataclass
class BrokerConfig:
    initial_cash: float
    lot_size: int = 100
    allow_partial_fill: bool = True
    cash_buffer_pct: float = 0.0
    max_positions: int = 0


def _buy_cost_breakdown(
    cost_cfg: object,
    *,
    qty: int,
    exec_price: float,
) -> tuple[float, float, float, float]:
    notional = float(qty) * float(exec_price)
    if notional <= 0:
        return 0.0, 0.0, 0.0, 0.0
    if not is_cost_enabled(cost_cfg):
        return notional, 0.0, 0.0, 0.0
    breakdown = cost_from_config(
        cost_cfg,
        side="buy",
        notional=notional,
        slippage_cost=0.0,
    )
    return (
        notional,
        float(breakdown.commission),
        float(breakdown.stamp_tax),
        float(breakdown.total),
    )


def _max_affordable_buy_qty(
    *,
    cash: float,
    qty_limit: int,
    lot_size: int,
    exec_price: float,
    cost_cfg: object,
    stock_code: str | None = None,
) -> tuple[int, float, float, float, float]:
    qty_limit = normalize_buy_qty(stock_code, qty_limit, lot_size=lot_size)
    if cash <= 0 or qty_limit <= 0 or exec_price <= 0:
        return 0, 0.0, 0.0, 0.0, 0.0

    best_qty = 0
    best_tuple = (0.0, 0.0, 0.0, 0.0)
    left = 1
    step = buy_qty_step(stock_code, lot_size=lot_size)
    right = qty_limit // step
    while left <= right:
        mid = (left + right) // 2
        qty = normalize_buy_qty(stock_code, mid * step, lot_size=lot_size)
        notional, commission, stamp_tax, total_cost = _buy_cost_breakdown(
            cost_cfg,
            qty=qty,
            exec_price=exec_price,
        )
        needed = float(math.fsum([notional, total_cost]))
        if needed <= cash:
            best_qty = qty
            best_tuple = (notional, commission, stamp_tax, total_cost)
            left = mid + 1
        else:
            right = mid - 1

    return (best_qty, *best_tuple)


@dataclass
class SimpleBroker:
    """Small per-run broker with explicit mutable lifecycle.

    A broker instance owns cash, positions, orders, fills, and missing-price
    log suppression for one portfolio run. Reuse across runs must call
    ``reset()`` first; normal runner code creates a fresh instance per run.
    """

    cfg: BrokerConfig
    cost_cfg: object
    cash: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    _missing_price_logs: set[tuple[str, str]] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.cash <= 0:
            self.cash = float(self.cfg.initial_cash)

    def reset(self, *, cash: float | None = None) -> None:
        """Clear run-local state so an explicitly reused instance is clean."""
        self.cash = float(self.cfg.initial_cash if cash is None else cash)
        self.positions.clear()
        self.orders.clear()
        self.fills.clear()
        self._missing_price_logs.clear()

    def _next_order_id(self) -> str:
        return f"order_{len(self.orders) + 1}"

    def _record_order(self, order: Order) -> None:
        self.orders.append(order)

    def _record_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def record_skipped_order(
        self,
        *,
        trade_date: str,
        stock_code: str,
        side: Literal["buy", "sell"],
        qty: int,
        price: float | None,
        reason: str,
    ) -> None:
        """Record a runner-level execution attempt that could not reach the broker."""
        try:
            requested_qty = max(int(qty), 0)
        except (TypeError, ValueError, OverflowError):
            requested_qty = 0
        self._record_order(
            Order(
                order_id=self._next_order_id(),
                trade_date=str(trade_date),
                stock_code=str(stock_code),
                side=side,
                qty=requested_qty,
                price=_finite_positive_price(price) or math.nan,
                status="skipped",
                reason=str(reason or ""),
            )
        )

    def position_qty(self, stock_code: str) -> int:
        pos = self.positions.get(stock_code)
        return int(pos.qty) if pos else 0

    def mark_to_market(
        self,
        price_map: Dict[str, float],
        trade_date: str | None = None,
        *,
        log_missing: bool = False,
    ) -> float:
        position_values: list[float] = []
        date_key = str(trade_date) if trade_date is not None else ""
        for code, pos in self.positions.items():
            price = _finite_positive_price(price_map.get(code))
            if price is None:
                if log_missing and trade_date is not None:
                    key = (code, date_key)
                    if key not in self._missing_price_logs:
                        logger.warning(
                            "mark_to_market_missing_price code=%s date=%s fallback=last_price last_price=%s",
                            code,
                            date_key,
                            pos.last_price,
                        )
                        self._missing_price_logs.add(key)
                price = _finite_positive_price(pos.last_price)
            if price is None:
                continue
            pos.last_price = price
            if trade_date is not None:
                pos.last_date = str(trade_date)
            position_values.append(float(pos.qty) * float(pos.last_price))
        return float(math.fsum([float(self.cash)] + position_values))

    def buy(
        self,
        *,
        trade_date: str,
        stock_code: str,
        price: float,
        qty: int,
    ) -> Optional[Fill]:
        raw_price = _finite_positive_price(price)
        if raw_price is None:
            return None

        if qty <= 0:
            self._record_order(
                Order(
                    order_id=self._next_order_id(),
                    trade_date=trade_date,
                    stock_code=stock_code,
                    side="buy",
                    qty=0,
                    price=raw_price,
                    status="skipped",
                    reason="qty<=0",
                )
            )
            return None

        lot = max(int(self.cfg.lot_size or 1), 1)
        qty = normalize_buy_qty(stock_code, qty, lot_size=lot)
        if qty <= 0:
            self._record_order(
                Order(
                    order_id=self._next_order_id(),
                    trade_date=trade_date,
                    stock_code=stock_code,
                    side="buy",
                    qty=0,
                    price=raw_price,
                    status="skipped",
                    reason="below_lot_size",
                )
            )
            return None

        max_positions = int(getattr(self.cfg, "max_positions", 0) or 0)
        if max_positions > 0 and stock_code not in self.positions and len(self.positions) >= max_positions:
            self._record_order(
                Order(
                    order_id=self._next_order_id(),
                    trade_date=trade_date,
                    stock_code=stock_code,
                    side="buy",
                    qty=qty,
                    price=raw_price,
                    status="rejected",
                    reason="max_positions",
                )
            )
            return None

        tc = get_tc(self.cost_cfg)
        slippage_bps = getattr(tc, "slippage_bps", None) if is_cost_enabled(self.cost_cfg) else 0.0
        exec_price = _execution_price(raw_price, side="buy", slippage_bps=slippage_bps)
        notional = float(qty) * float(exec_price)
        slippage_cost = max(exec_price - raw_price, 0.0) * qty

        if not math.isfinite(notional) or notional <= 0:
            self._record_order(
                Order(
                    order_id=self._next_order_id(),
                    trade_date=trade_date,
                    stock_code=stock_code,
                    side="buy",
                    qty=0,
                    price=float(exec_price),
                    status="rejected",
                    reason="notional<=0",
                )
            )
            return None

        notional, commission, stamp_tax, total_cost = _buy_cost_breakdown(
            self.cost_cfg,
            qty=qty,
            exec_price=exec_price,
        )

        needed = float(math.fsum([notional, float(total_cost)]))
        if needed > self.cash:
            if not self.cfg.allow_partial_fill:
                self._record_order(
                    Order(
                        order_id=self._next_order_id(),
                        trade_date=trade_date,
                        stock_code=stock_code,
                        side="buy",
                        qty=qty,
                        price=float(exec_price),
                        status="rejected",
                        reason="insufficient_cash",
                    )
                )
                return None
            qty, notional, commission, stamp_tax, total_cost = _max_affordable_buy_qty(
                cash=float(self.cash),
                qty_limit=qty,
                lot_size=lot,
                exec_price=float(exec_price),
                cost_cfg=self.cost_cfg,
                stock_code=stock_code,
            )
            if qty <= 0:
                self._record_order(
                    Order(
                        order_id=self._next_order_id(),
                        trade_date=trade_date,
                        stock_code=stock_code,
                        side="buy",
                        qty=0,
                        price=float(exec_price),
                        status="rejected",
                        reason="insufficient_cash",
                    )
                )
                return None
            slippage_cost = max(exec_price - raw_price, 0.0) * qty
            needed = float(math.fsum([notional, float(total_cost)]))

        order_id = self._next_order_id()
        self._record_order(
            Order(
                order_id=order_id,
                trade_date=trade_date,
                stock_code=stock_code,
                side="buy",
                qty=qty,
                price=float(exec_price),
                status="filled",
            )
        )
        self.cash = float(math.fsum([float(self.cash), -float(needed)]))

        pos = self.positions.get(stock_code)
        if pos is None:
            pos = Position(stock_code=stock_code, qty=0, avg_cost=0.0, last_price=float(exec_price))
            self.positions[stock_code] = pos
        new_qty = pos.qty + qty
        if new_qty > 0:
            weighted_cost = math.fsum([float(pos.avg_cost) * float(pos.qty), float(exec_price) * float(qty)])
            pos.avg_cost = float(weighted_cost / float(new_qty))
        pos.qty = new_qty
        pos.last_price = float(exec_price)
        pos.last_date = trade_date

        fill = Fill(
            order_id=order_id,
            trade_date=trade_date,
            stock_code=stock_code,
            side="buy",
            qty=qty,
            price=float(exec_price),
            commission=float(commission),
            stamp_tax=float(stamp_tax),
            slippage_cost=float(slippage_cost),
            total_cost=float(total_cost),
            cash_delta=-float(needed),
        )
        self._record_fill(fill)
        return fill

    def sell(
        self,
        *,
        trade_date: str,
        stock_code: str,
        price: float,
        qty: Optional[int] = None,
        note: str = "",
    ) -> Optional[Fill]:
        raw_price = _finite_positive_price(price)
        if raw_price is None:
            return None

        pos = self.positions.get(stock_code)
        if pos is None or pos.qty <= 0:
            self._record_order(
                Order(
                    order_id=self._next_order_id(),
                    trade_date=trade_date,
                    stock_code=stock_code,
                    side="sell",
                    qty=0,
                    price=raw_price,
                    status="skipped",
                    reason="no_position",
                )
            )
            return None

        requested_qty = pos.qty if qty is None else min(int(qty), pos.qty)
        qty_to_sell = normalize_sell_qty(
            stock_code,
            requested_qty,
            position_qty=int(pos.qty),
            lot_size=max(int(self.cfg.lot_size or 1), 1),
        )
        if qty_to_sell <= 0:
            return None

        tc = get_tc(self.cost_cfg)
        slippage_bps = getattr(tc, "slippage_bps", None) if is_cost_enabled(self.cost_cfg) else 0.0
        exec_price = _execution_price(raw_price, side="sell", slippage_bps=slippage_bps)
        notional = float(qty_to_sell) * float(exec_price)
        slippage_cost = max(raw_price - exec_price, 0.0) * qty_to_sell
        if not math.isfinite(notional) or notional <= 0:
            return None

        total_cost = 0.0
        commission = 0.0
        stamp_tax = 0.0
        if is_cost_enabled(self.cost_cfg):
            breakdown = cost_from_config(
                self.cost_cfg,
                side="sell",
                notional=notional,
                slippage_cost=0.0,
            )
            commission = breakdown.commission
            stamp_tax = breakdown.stamp_tax
            total_cost = breakdown.total
        else:
            total_cost = 0.0

        proceeds = _finite_sum(float(notional), -float(total_cost))
        if proceeds is None:
            return None
        updated_cash = _finite_sum(float(self.cash), proceeds)
        if updated_cash is None:
            return None
        order_id = self._next_order_id()
        self._record_order(
            Order(
                order_id=order_id,
                trade_date=trade_date,
                stock_code=stock_code,
                side="sell",
                qty=qty_to_sell,
                price=float(exec_price),
                status="filled",
            )
        )
        self.cash = updated_cash
        pos.qty -= qty_to_sell
        pos.last_price = float(exec_price)
        pos.last_date = trade_date
        if pos.qty <= 0:
            self.positions.pop(stock_code, None)

        fill = Fill(
            order_id=order_id,
            trade_date=trade_date,
            stock_code=stock_code,
            side="sell",
            qty=qty_to_sell,
            price=float(exec_price),
            commission=float(commission),
            stamp_tax=float(stamp_tax),
            slippage_cost=float(slippage_cost),
            total_cost=float(total_cost),
            cash_delta=float(proceeds),
            note=str(note or ""),
        )
        self._record_fill(fill)
        return fill

    def close_all(self, *, trade_date: str, price_map: Dict[str, float], note: str = "") -> None:
        for code in list(self.positions.keys()):
            price = price_map.get(code)
            if price is None:
                continue
            self.sell(trade_date=trade_date, stock_code=code, price=price, qty=None, note=note)

    def equity(self, price_map: Dict[str, float]) -> float:
        return self.mark_to_market(price_map)
