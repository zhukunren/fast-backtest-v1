"""Strategy-fill replay helpers for broker shadow ledgers."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pandas as pd

from core.exceptions import DataValidationError
from trading.lot_rules import buy_qty_step, normalize_buy_qty

from ..costs import cost_from_config, is_cost_enabled


_CASH_TOLERANCE = 1e-9
_BuyGuard = Callable[[dict[str, Any], int, float], str | None]


def _float_value(value: Any) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    return float(converted) if pd.notna(converted) else 0.0


def _applied_fill_row(
    fill: dict[str, Any],
    *,
    side: str,
    applied_qty: int,
    cash_before: float,
    cash_delta: float,
    shares_before: int,
    commission: float | None = None,
    stamp_tax: float | None = None,
    fee_cost: float | None = None,
    reason: str = "",
    constraint_reason: str = "",
) -> dict[str, Any]:
    original_qty = max(int(fill.get("qty", 0) or 0), 1)
    applied_ratio = float(applied_qty) / float(original_qty)
    row = {
        key: value
        for key, value in fill.items()
        if key not in {"_seq", "_order_num"}
    }
    row.update(
        {
            "side": side,
            "qty": int(applied_qty),
            "requested_qty": int(original_qty),
            "filled_qty": int(applied_qty),
            "status": "filled",
            "reason": str(reason or ""),
            "constraint_reason": str(constraint_reason or ""),
            "source": str(fill.get("source", "strategy") or "strategy"),
            "commission": (
                _float_value(fill.get("commission")) * applied_ratio
                if commission is None
                else commission
            ),
            "stamp_tax": (
                _float_value(fill.get("stamp_tax")) * applied_ratio
                if stamp_tax is None
                else stamp_tax
            ),
            "slippage_cost": _float_value(fill.get("slippage_cost")) * applied_ratio,
            "total_cost": (
                _float_value(fill.get("total_cost")) * applied_ratio
                if fee_cost is None
                else fee_cost
            ),
            "cash_delta": float(cash_delta),
            "cash_before": float(cash_before),
            "cash_after": float(cash_before + cash_delta),
            "shares_before": int(shares_before),
            "shares_after": (
                int(shares_before + applied_qty)
                if side == "buy"
                else int(max(shares_before - applied_qty, 0))
            ),
        }
    )
    return row


def _rejected_fill_row(
    fill: dict[str, Any],
    *,
    side: str,
    reason: str,
    cash_before: float,
    shares_before: int,
) -> dict[str, Any]:
    requested_qty = max(int(fill.get("qty", 0) or 0), 0)
    row = {
        key: value
        for key, value in fill.items()
        if key not in {"_seq", "_order_num"}
    }
    row.update(
        {
            "side": side,
            "qty": int(requested_qty),
            "requested_qty": int(requested_qty),
            "filled_qty": 0,
            "status": "rejected",
            "reason": str(reason or ""),
            "constraint_reason": str(reason or ""),
            "source": str(fill.get("source", "strategy") or "strategy"),
            "cash_delta": 0.0,
            "cash_before": float(cash_before),
            "cash_after": float(cash_before),
            "shares_before": int(shares_before),
            "shares_after": int(shares_before),
        }
    )
    return row


def _execution_price(fill: dict[str, Any]) -> float:
    price = _float_value(fill.get("price"))
    return price if math.isfinite(price) and price > 0 else 0.0


def _partial_fill_costs(
    fill: dict[str, Any],
    *,
    side: str,
    qty: int,
    cost_cfg: Any,
) -> tuple[float, float, float, float] | None:
    """Reprice a constrained fill with the live cost configuration."""
    price = _execution_price(fill)
    if qty <= 0 or price <= 0:
        return None
    notional = float(qty) * price
    breakdown = cost_from_config(
        cost_cfg if is_cost_enabled(cost_cfg) else None,
        side=side,
        notional=notional,
        slippage_cost=0.0,
    )
    commission = float(breakdown.commission)
    stamp_tax = float(breakdown.stamp_tax)
    total_cost = float(breakdown.total)
    cash_delta = -(notional + total_cost) if side == "buy" else (notional - total_cost)
    return cash_delta, commission, stamp_tax, total_cost


def _max_affordable_buy_qty(
    fill: dict[str, Any],
    *,
    cash: float,
    cost_cfg: Any,
    lot_size: int,
    buy_guard: _BuyGuard | None,
) -> tuple[int, tuple[float, float, float, float] | None, str]:
    """Find the largest legal constrained buy, preserving lot rules."""
    code = str(fill.get("stock_code", "")).strip()
    requested_qty = max(int(fill.get("qty", 0) or 0), 0)
    upper_qty = normalize_buy_qty(code, requested_qty, lot_size=lot_size)
    if upper_qty <= 0:
        return 0, None, "below_lot_size"
    if _execution_price(fill) <= 0:
        return 0, None, "invalid_price"

    step = buy_qty_step(code, lot_size=lot_size)

    def _candidate(qty: int) -> tuple[tuple[float, float, float, float] | None, str]:
        costs = _partial_fill_costs(fill, side="buy", qty=qty, cost_cfg=cost_cfg)
        if costs is None:
            return None, "invalid_price"
        cash_delta = costs[0]
        if float(cash) + cash_delta < -_CASH_TOLERANCE:
            return costs, "insufficient_cash"
        if buy_guard is not None:
            rejection = buy_guard(fill, qty, cash_delta)
            if rejection:
                return costs, str(rejection)
        return costs, ""

    candidate_costs, candidate_reason = _candidate(upper_qty)
    if not candidate_reason:
        return upper_qty, candidate_costs, ""

    # Both cash affordability and position-cap checks are monotonic in buy
    # quantity, so binary search remains valid for large orders.
    left = 0
    right = upper_qty // step
    best_qty = 0
    best_costs: tuple[float, float, float, float] | None = None
    while left <= right:
        middle = (left + right) // 2
        qty = normalize_buy_qty(code, middle * step, lot_size=lot_size)
        if qty <= 0:
            left = middle + 1
            continue
        costs, reason = _candidate(qty)
        if not reason:
            best_qty = qty
            best_costs = costs
            left = middle + 1
        else:
            right = middle - 1
    if best_qty > 0:
        return best_qty, best_costs, candidate_reason
    return 0, None, candidate_reason


def apply_shadow_strategy_fills(
    ledger: Any,
    fills: list[dict[str, Any]],
    *,
    cost_cfg: Any,
    lot_size: int = 1,
    allow_partial_fill: bool = True,
    buy_guard: _BuyGuard | None = None,
    rejections: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Replay raw strategy fills with cash, inventory, and optional risk caps.

    The source fills describe the unscaled strategy.  Once risk trades have
    changed the shadow inventory, replaying an order verbatim can no longer be
    assumed affordable or executable.  Accepted rows remain fills; rejected
    attempts are emitted separately through ``rejections`` so a zero-quantity
    pseudo-fill can never distort the ledger.
    """
    applied_rows: list[dict[str, Any]] = []
    rejected_rows = rejections if rejections is not None else []
    for fill in fills:
        code = str(fill.get("stock_code", "")).strip()
        qty = int(fill.get("qty", 0) or 0)
        if not code or qty <= 0:
            continue
        side = str(fill.get("side", "")).strip().lower()
        try:
            cash_delta = float(fill.get("cash_delta", 0.0))
        except (TypeError, ValueError):
            raise DataValidationError("broker 风控影子账本的策略成交 cash_delta 必须为有限数值。")
        if not math.isfinite(cash_delta):
            raise DataValidationError("broker 风控影子账本的策略成交 cash_delta 必须为有限数值。")
        current_qty = int(ledger.qty.get(code, 0) or 0)
        if side == "buy":
            cash_before = float(ledger.cash)
            if cash_delta >= -_CASH_TOLERANCE:
                rejected_rows.append(
                    _rejected_fill_row(
                        fill,
                        side=side,
                        reason="invalid_cash_delta",
                        cash_before=cash_before,
                        shares_before=current_qty,
                    )
                )
                continue
            full_rejection = (
                "insufficient_cash"
                if cash_before + cash_delta < -_CASH_TOLERANCE
                else str(buy_guard(fill, qty, cash_delta) or "") if buy_guard is not None else ""
            )
            if not full_rejection:
                ledger.qty[code] = current_qty + qty
                ledger.cash += cash_delta
                price = _execution_price(fill)
                if price > 0:
                    ledger.last_prices[code] = price
                applied_rows.append(
                    _applied_fill_row(
                        fill,
                        side=side,
                        applied_qty=qty,
                        cash_before=cash_before,
                        cash_delta=cash_delta,
                        shares_before=current_qty,
                    )
                )
                continue

            if not allow_partial_fill:
                rejected_rows.append(
                    _rejected_fill_row(
                        fill,
                        side=side,
                        reason=full_rejection,
                        cash_before=cash_before,
                        shares_before=current_qty,
                    )
                )
                continue

            if _execution_price(fill) <= 0:
                rejected_rows.append(
                    _rejected_fill_row(
                        fill,
                        side=side,
                        reason="invalid_price",
                        cash_before=cash_before,
                        shares_before=current_qty,
                    )
                )
                continue
            buy_qty, costs, limiting_reason = _max_affordable_buy_qty(
                fill,
                cash=cash_before,
                cost_cfg=cost_cfg,
                lot_size=lot_size,
                buy_guard=buy_guard,
            )
            if buy_qty <= 0 or costs is None:
                rejected_rows.append(
                    _rejected_fill_row(
                        fill,
                        side=side,
                        reason=limiting_reason or full_rejection,
                        cash_before=cash_before,
                        shares_before=current_qty,
                    )
                )
                continue
            applied_cash_delta, commission, stamp_tax, fee_cost = costs
            ledger.qty[code] = current_qty + buy_qty
            ledger.cash += applied_cash_delta
            price = _execution_price(fill)
            if price > 0:
                ledger.last_prices[code] = price
            applied_rows.append(
                _applied_fill_row(
                    fill,
                    side=side,
                    applied_qty=buy_qty,
                    cash_before=cash_before,
                    cash_delta=applied_cash_delta,
                    shares_before=current_qty,
                    commission=commission,
                    stamp_tax=stamp_tax,
                    fee_cost=fee_cost,
                    reason="partial_fill",
                    constraint_reason=limiting_reason or full_rejection,
                )
            )
            continue
        if side != "sell":
            continue
        if cash_delta <= _CASH_TOLERANCE:
            rejected_rows.append(
                _rejected_fill_row(
                    fill,
                    side=side,
                    reason="invalid_cash_delta",
                    cash_before=float(ledger.cash),
                    shares_before=current_qty,
                )
            )
            continue
        sell_qty = min(current_qty, qty)
        if sell_qty <= 0:
            rejected_rows.append(
                _rejected_fill_row(
                    fill,
                    side=side,
                    reason="insufficient_position",
                    cash_before=float(ledger.cash),
                    shares_before=current_qty,
                )
            )
            continue
        remaining_qty = current_qty - sell_qty
        if remaining_qty > 0:
            ledger.qty[code] = remaining_qty
        else:
            ledger.qty.pop(code, None)
        cash_before = float(ledger.cash)
        if sell_qty == qty:
            ledger.cash += cash_delta
            price = _execution_price(fill)
            if price > 0:
                ledger.last_prices[code] = price
            applied_rows.append(
                _applied_fill_row(
                    fill,
                    side=side,
                    applied_qty=sell_qty,
                    cash_before=cash_before,
                    cash_delta=cash_delta,
                    shares_before=current_qty,
                )
            )
            continue
        costs = _partial_fill_costs(fill, side="sell", qty=sell_qty, cost_cfg=cost_cfg)
        if costs is None:
            rejected_rows.append(
                _rejected_fill_row(
                    fill,
                    side=side,
                    reason="invalid_price",
                    cash_before=float(ledger.cash),
                    shares_before=current_qty,
                )
            )
            if current_qty > 0:
                ledger.qty[code] = current_qty
            else:
                ledger.qty.pop(code, None)
            continue
        applied_cash_delta, commission, stamp_tax, fee_cost = costs
        ledger.cash += applied_cash_delta
        price = _execution_price(fill)
        if price > 0:
            ledger.last_prices[code] = price
        applied_rows.append(
            _applied_fill_row(
                fill,
                side=side,
                applied_qty=sell_qty,
                cash_before=cash_before,
                cash_delta=applied_cash_delta,
                shares_before=current_qty,
                commission=commission,
                stamp_tax=stamp_tax,
                fee_cost=fee_cost,
                reason="partial_fill",
                constraint_reason="insufficient_position",
            )
        )
    return applied_rows


def build_shadow_strategy_fills_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Publish applied strategy fills in deterministic ledger order."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["_publish_seq"] = pd.Series(range(len(frame)), index=frame.index, dtype="int64")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    order_number = (
        frame["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        if "order_id" in frame.columns
        else pd.Series(index=frame.index, dtype="object")
    )
    frame["_publish_order"] = pd.to_numeric(order_number, errors="coerce")
    frame = frame.sort_values(
        ["trade_date", "_publish_order", "_publish_seq"],
        kind="mergesort",
    )
    return frame.drop(
        columns=["_slot_event_seq", "_publish_order", "_publish_seq"],
        errors="ignore",
    ).reset_index(drop=True)


def build_shadow_strategy_rejections_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Publish rejected shadow strategy orders in the same deterministic order."""
    return build_shadow_strategy_fills_frame(rows)


def starting_risk_order_index(fills_df: pd.DataFrame) -> int:
    """Reserve risk order numbers after every strategy fill number."""
    if fills_df.empty:
        return 1
    values = fills_df["_order_num"] if "_order_num" in fills_df.columns else pd.Series(dtype="float64")
    order_numbers = pd.to_numeric(values, errors="coerce")
    max_order = int(order_numbers.max()) if order_numbers.notna().any() else 0
    return max(max_order, len(fills_df)) + 1


__all__ = [
    "apply_shadow_strategy_fills",
    "build_shadow_strategy_fills_frame",
    "build_shadow_strategy_rejections_frame",
    "starting_risk_order_index",
]
