"""Rebuild synthetic broker fills produced by portfolio-level risk scaling."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from typing import Any, cast

import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError
from trading.lot_rules import buy_qty_step, normalize_buy_qty

from ..constraints import is_limit_locked, is_no_trade_day, max_trade_qty
from ..costs import apply_slippage, cost_from_config, get_tc, is_cost_enabled
from ..risk_fill_metadata import resolve_risk_reason as _resolve_risk_reason
from ..risk_lot_rules import feasible_risk_target_quantity as _feasible_target_quantity
from ..risk_lot_rules import normalize_risk_sell_qty as _normalize_risk_sell_qty
from .engine import _max_affordable_buy_qty
from .shadow_costs import apply_shadow_strategy_fills as _apply_strategy_fills
from .shadow_costs import build_shadow_strategy_fills_frame as _strategy_fills_frame
from .shadow_costs import build_shadow_strategy_rejections_frame as _strategy_rejections_frame
from .shadow_costs import starting_risk_order_index as _starting_risk_order_index


def _build_risk_fill_row(
    *,
    trade_date: str,
    stock_code: str,
    side: str,
    qty: int,
    raw_price: float,
    cost_cfg: Any,
    note: str,
    before_ratio: float,
    after_ratio: float,
    risk_reason: str,
    risk_state: str,
    dd_state: str,
    order_idx: int,
) -> dict[str, Any] | None:
    if qty <= 0 or not math.isfinite(raw_price) or raw_price <= 0:
        return None
    cost_enabled = is_cost_enabled(cost_cfg)
    slippage_bps = (
        float(getattr(get_tc(cost_cfg), "slippage_bps", 0.0) or 0.0)
        if cost_enabled
        else 0.0
    )
    exec_price = float(apply_slippage(raw_price, side, slippage_bps))
    notional = float(qty) * exec_price
    if side == "buy":
        slippage_cost = max(exec_price - float(raw_price), 0.0) * qty
    else:
        slippage_cost = max(float(raw_price) - exec_price, 0.0) * qty
    breakdown = cost_from_config(
        cost_cfg if cost_enabled else None,
        side=side,
        notional=notional,
        slippage_cost=0.0,
    )
    total_cost = float(breakdown.total)
    cash_delta = -(notional + total_cost) if side == "buy" else (notional - total_cost)
    return {
        "order_id": f"risk_{order_idx}",
        "trade_date": trade_date,
        "stock_code": stock_code,
        "side": side,
        "qty": int(qty),
        "price": exec_price,
        "commission": float(breakdown.commission),
        "stamp_tax": float(breakdown.stamp_tax),
        "slippage_cost": float(slippage_cost),
        "total_cost": total_cost,
        "cash_delta": float(cash_delta),
        "note": note,
        "source": "risk",
        "before_ratio": float(before_ratio),
        "after_ratio": float(after_ratio),
        "risk_reason": str(risk_reason or ""),
        "risk_state": str(risk_state or ""),
        "dd_state": str(dd_state or ""),
    }


def _normalize_strategy_fills(fills_df: pd.DataFrame) -> pd.DataFrame:
    normalized = fills_df.copy() if isinstance(fills_df, pd.DataFrame) else pd.DataFrame()
    if normalized.empty:
        return normalized
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"], errors="coerce").dt.normalize()
    normalized["stock_code"] = normalized["stock_code"].astype(str).str.strip()
    normalized["side"] = normalized["side"].astype(str).str.strip().str.lower()
    normalized["qty"] = pd.to_numeric(normalized["qty"], errors="coerce").fillna(0).astype(int)
    normalized["cash_delta"] = pd.to_numeric(normalized["cash_delta"], errors="coerce")
    normalized["_seq"] = pd.Series(range(len(normalized)), index=normalized.index, dtype="int64")
    if "order_id" in normalized.columns:
        order_num = normalized["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
    else:
        order_num = pd.Series(index=normalized.index, dtype="object")
    normalized["_order_num"] = pd.to_numeric(order_num, errors="coerce")
    if "source" in normalized.columns:
        source = normalized["source"].astype(str).str.strip().str.lower()
        normalized = normalized.loc[~source.eq("risk")].copy()
    return normalized.sort_values(
        ["trade_date", "_order_num", "_seq"],
        kind="mergesort",
    ).reset_index(drop=True)


def _group_fills_by_date(fills_df: pd.DataFrame) -> dict[pd.Timestamp, list[dict[str, Any]]]:
    grouped: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for _, row in fills_df.iterrows():
        trade_date = row.get("trade_date")
        if pd.isna(trade_date):
            continue
        grouped[pd.Timestamp(trade_date).normalize()].append(cast(dict[str, Any], row.to_dict()))
    return grouped


def _positions_by_date(
    snapshots: list[dict[str, Any]],
) -> dict[pd.Timestamp, dict[str, dict[str, Any]]]:
    by_date: dict[pd.Timestamp, dict[str, dict[str, Any]]] = {}
    for snapshot in snapshots:
        trade_date = pd.to_datetime(cast(Any, snapshot.get("trade_date")), errors="coerce")
        if pd.isna(trade_date):
            continue
        positions: dict[str, dict[str, Any]] = {}
        for item in snapshot.get("positions", []) or []:
            code = str(item.get("stock_code", "")).strip()
            qty = int(item.get("qty", 0) or 0)
            price_value = pd.to_numeric(item.get("close"), errors="coerce")
            price = float(price_value) if pd.notna(price_value) else 0.0
            if not code or qty <= 0:
                continue
            current_quote_valid = item.get("current_quote_valid")
            if current_quote_valid is None:
                current_quote_valid = math.isfinite(price) and price > 0
            position: dict[str, Any] = {
                "qty": qty,
                "close": price if math.isfinite(price) and price > 0 else 0.0,
                "trade_price": price if bool(current_quote_valid) else 0.0,
            }
            for key in (
                "open",
                "high",
                "low",
                "volume",
                "pre_close",
                "stock_name",
                "execution_close",
            ):
                if key in item:
                    position[key] = item[key]
            if "execution_close" in position:
                execution_close = _float_value(position["execution_close"])
                position["trade_price"] = (
                    execution_close
                    if bool(current_quote_valid)
                    and math.isfinite(execution_close)
                    and execution_close > 0
                    else 0.0
                )
            positions[code] = position
        by_date[pd.Timestamp(trade_date).normalize()] = positions
    return by_date


@dataclass(slots=True)
class _RiskLedger:
    cash: float
    qty: dict[str, int] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    strategy_rows: list[dict[str, Any]] = field(default_factory=list)
    strategy_rejections: list[dict[str, Any]] = field(default_factory=list)
    daily_rows: list[dict[str, Any]] = field(default_factory=list)
    daily_snapshots: list[dict[str, Any]] = field(default_factory=list)
    next_order_idx: int = 1
    risk_reduced_codes: set[str] = field(default_factory=set)

    def append(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        self.next_order_idx += 1
        self.cash += float(row["cash_delta"])


@dataclass(frozen=True, slots=True)
class _RiskDay:
    trade_date: pd.Timestamp
    trade_date_text: str
    raw_positions: dict[str, dict[str, Any]]
    raw_ratio: float
    effective_ratio: float
    risk_state: str
    dd_state: str
    risk_reason: str
    note: str


@dataclass(frozen=True, slots=True)
class SyntheticRiskFillResult:
    fills_df: pd.DataFrame
    strategy_fills_df: pd.DataFrame
    strategy_rejections_df: pd.DataFrame
    daily_accounting_df: pd.DataFrame
    daily_position_snapshots: tuple[dict[str, Any], ...]


def _float_value(value: Any, *, default: float = 0.0) -> float:
    converted = pd.to_numeric(value, errors="coerce")
    return float(converted) if pd.notna(converted) else float(default)


def _build_risk_day(
    row: pd.Series,
    *,
    raw_positions: dict[str, dict[str, Any]],
    raw_util_col: str,
) -> _RiskDay:
    trade_date = pd.Timestamp(row[COL_TRADE_DATE]).normalize()
    raw_ratio = _float_value(row.get(raw_util_col))
    effective_ratio = _float_value(row.get("effective_position_ratio"), default=raw_ratio)
    risk_state = str(row.get("risk_state", "") or "").strip()
    dd_state = str(row.get("dd_state", "") or "").strip()
    reason = _resolve_risk_reason(
        before_ratio=raw_ratio,
        after_ratio=effective_ratio,
        risk_state=risk_state,
        dd_state=dd_state,
    )
    ratio_note = (
        f"风控调仓 组合仓位 {raw_ratio:.4f} -> {effective_ratio:.4f}"
        if raw_ratio > 1e-12 or effective_ratio > 1e-12
        else "风控调仓"
    )
    state_note = "；".join(
        part
        for part in (
            f"净值风控 {risk_state}" if risk_state else "",
            f"回撤风控 {dd_state}" if dd_state else "",
        )
        if part
    )
    note = f"{reason}；{ratio_note}；{state_note}" if state_note else f"{reason}；{ratio_note}"
    return _RiskDay(
        trade_date=trade_date,
        trade_date_text=trade_date.strftime("%Y-%m-%d"),
        raw_positions=raw_positions,
        raw_ratio=raw_ratio,
        effective_ratio=effective_ratio,
        risk_state=risk_state,
        dd_state=dd_state,
        risk_reason=reason,
        note=note,
    )


def _valuation_price(ledger: _RiskLedger, day: _RiskDay, code: str) -> float:
    price = _float_value(day.raw_positions.get(code, {}).get("close", 0.0))
    if not math.isfinite(price) or price <= 0:
        price = _float_value(ledger.last_prices.get(code, 0.0))
    return price if math.isfinite(price) and price > 0 else 0.0


def _trade_price(day: _RiskDay, code: str) -> float:
    item = day.raw_positions.get(code, {})
    price = _float_value(item.get("trade_price", item.get("close", 0.0)))
    return price if math.isfinite(price) and price > 0 else 0.0


def _risk_execution_price(
    day: _RiskDay,
    code: str,
    *,
    trade_constraints: Any,
) -> float:
    """Use the raw close when the configured constraints authorize execution."""
    item = day.raw_positions.get(code, {})
    if trade_constraints is not None and _has_execution_state(item):
        price = _float_value(item.get("execution_close", item.get("close", 0.0)))
        return price if math.isfinite(price) and price > 0 else 0.0
    return _trade_price(day, code)


def _has_execution_state(item: dict[str, Any]) -> bool:
    return any(
        key in item
        for key in ("open", "high", "low", "volume", "pre_close", "stock_name")
    )


def _risk_trade_rejection(
    day: _RiskDay,
    *,
    code: str,
    side: str,
    trade_constraints: Any,
) -> str | None:
    """Check execution constraints before recording a synthetic risk fill."""
    if trade_constraints is None:
        return None
    item = day.raw_positions.get(code, {})
    if not _has_execution_state(item):
        return "missing_execution_state"
    if bool(getattr(trade_constraints, "enable_no_trade_day", False)) and is_no_trade_day(
        open_price=item.get("open"),
        high_price=item.get("high"),
        low_price=item.get("low"),
        close_price=item.get("execution_close", item.get("close")),
        volume=item.get("volume"),
    ):
        return "no_trade_day"
    if bool(getattr(trade_constraints, "enable_limit", False)) and is_limit_locked(
        open_price=item.get("open"),
        high_price=item.get("high"),
        low_price=item.get("low"),
        close_price=item.get("execution_close", item.get("close")),
        prev_close=item.get("pre_close"),
        limit_up_pct=float(getattr(trade_constraints, "limit_up_pct", 0.098)),
        limit_down_pct=float(getattr(trade_constraints, "limit_down_pct", 0.098)),
        side=side,
        stock_code=code,
        stock_name=item.get("stock_name"),
    ):
        return "limit_locked"
    return None


def _constrained_risk_qty(
    day: _RiskDay,
    *,
    code: str,
    side: str,
    qty: int,
    position_qty: int | None,
    lot_size: int,
    trade_constraints: Any,
) -> int:
    if qty <= 0 or trade_constraints is None or not bool(
        getattr(trade_constraints, "enable_volume_limit", False)
    ):
        return max(int(qty), 0)
    volume = day.raw_positions.get(code, {}).get("volume")
    if volume is None:
        return max(int(qty), 0)
    max_qty = max_trade_qty(
        volume=volume,
        participation_rate=float(getattr(trade_constraints, "max_participation_rate", 0.0)),
        lot_size=lot_size,
        stock_code=code,
        side=side,
        position_qty=position_qty,
    )
    return min(max(int(qty), 0), max_qty)


def _project_post_sell_account(
    ledger: _RiskLedger,
    day: _RiskDay,
    desired: dict[str, int],
    *,
    cost_cfg: Any,
) -> tuple[float, float, float]:
    cash = float(ledger.cash)
    position_value = 0.0
    for code, current_qty in sorted(ledger.qty.items(), key=lambda item: item[0].casefold()):
        target_qty = min(int(desired.get(code, 0) or 0), int(current_qty))
        sell_qty = int(current_qty) - target_qty
        valuation_price = _valuation_price(ledger, day, code)
        if sell_qty > 0:
            price = _trade_price(day, code)
            if price <= 0:
                return math.nan, math.nan, math.nan
            projected = _build_risk_fill_row(
                trade_date=day.trade_date_text,
                stock_code=code,
                side="sell",
                qty=sell_qty,
                raw_price=price,
                cost_cfg=cost_cfg,
                note=day.note,
                before_ratio=day.raw_ratio,
                after_ratio=day.effective_ratio,
                risk_reason=day.risk_reason,
                risk_state=day.risk_state,
                dd_state=day.dd_state,
                order_idx=ledger.next_order_idx,
            )
            if projected is None:
                return math.nan, math.nan, math.nan
            cash += float(projected["cash_delta"])
        position_value += target_qty * valuation_price
    equity = cash + position_value
    return cash, position_value, equity


def _position_ratio_tolerance(
    ledger: _RiskLedger,
    day: _RiskDay,
    quantities: dict[str, int],
    *,
    lot_size: int,
    equity: float,
) -> float:
    if equity <= 0:
        return 0.0
    step_values = [
        buy_qty_step(code, lot_size=lot_size) * _valuation_price(ledger, day, code)
        for code, qty in quantities.items()
        if int(qty) > 0 and _valuation_price(ledger, day, code) > 0
    ]
    return min(step_values) / equity if step_values else 0.0


def _adjust_targets_for_sell_costs(
    ledger: _RiskLedger,
    day: _RiskDay,
    desired: dict[str, int],
    *,
    lot_size: int,
    cost_cfg: Any,
) -> dict[str, int]:
    adjusted = dict(desired)
    max_iterations = sum(max(int(qty), 0) for qty in ledger.qty.values()) + 1
    for _ in range(max_iterations):
        cash, position_value, equity = _project_post_sell_account(
            ledger,
            day,
            adjusted,
            cost_cfg=cost_cfg,
        )
        if not all(math.isfinite(value) for value in (cash, position_value, equity)):
            return adjusted
        tolerance = _position_ratio_tolerance(
            ledger,
            day,
            adjusted,
            lot_size=lot_size,
            equity=equity,
        )
        utilization = position_value / equity if equity > 0 else math.inf
        cap = max(float(day.effective_ratio), 0.0)
        if cash >= -1e-9 and utilization <= cap + tolerance + 1e-12:
            return adjusted
        candidates = sorted(
            (
                code
                for code, current_qty in ledger.qty.items()
                if min(int(adjusted.get(code, 0) or 0), int(current_qty)) > 0
            ),
            key=lambda code: (
                -min(int(adjusted.get(code, 0) or 0), int(ledger.qty.get(code, 0) or 0))
                * _valuation_price(ledger, day, code),
                code.casefold(),
            ),
        )
        if not candidates:
            break
        code = candidates[0]
        current_target = min(
            int(adjusted.get(code, 0) or 0),
            int(ledger.qty.get(code, 0) or 0),
        )
        position_qty = int(ledger.qty.get(code, 0) or 0)
        sell_qty = _normalize_risk_sell_qty(
            code,
            position_qty - current_target + 1,
            position_qty=position_qty,
            lot_size=lot_size,
        )
        next_target = position_qty - sell_qty
        if next_target > 0:
            adjusted[code] = next_target
        else:
            adjusted.pop(code, None)
    raise DataValidationError(
        f"broker {day.trade_date_text}: 风控目标无法同时满足非负现金与仓位上限。"
    )


def _desired_quantities(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    lot_size: int,
    cost_cfg: Any,
) -> dict[str, int]:
    current_position_value = sum(
        int(qty) * _valuation_price(ledger, day, code)
        for code, qty in sorted(ledger.qty.items(), key=lambda item: item[0].casefold())
    )
    equity = float(ledger.cash + current_position_value)
    target_position_value = max(equity, 0.0) * max(float(day.effective_ratio), 0.0)
    raw_values = {
        code: int(item["qty"]) * _valuation_price(ledger, day, code)
        for code, item in sorted(day.raw_positions.items(), key=lambda item: item[0].casefold())
    }
    raw_total_value = float(sum(raw_values.values()))
    desired: dict[str, int] = {}
    if raw_total_value <= 0 or target_position_value <= 0:
        return _adjust_targets_for_sell_costs(
            ledger,
            day,
            desired,
            lot_size=lot_size,
            cost_cfg=cost_cfg,
        )
    for code, item in sorted(day.raw_positions.items(), key=lambda item: item[0].casefold()):
        qty = int(item["qty"])
        price = _valuation_price(ledger, day, code)
        allocated_value = target_position_value * raw_values[code] / raw_total_value
        qty_by_value = int((allocated_value + 1e-9) / price) if price > 0 else 0
        target_qty = _feasible_target_quantity(
            code,
            current_qty=int(ledger.qty.get(code, 0) or 0),
            target_limit=min(qty, qty_by_value),
            lot_size=lot_size,
        )
        target_qty = min(target_qty, qty)
        if target_qty > 0:
            desired[code] = target_qty
    return _adjust_targets_for_sell_costs(
        ledger,
        day,
        desired,
        lot_size=lot_size,
        cost_cfg=cost_cfg,
    )


def _strategy_buy_guard(
    ledger: _RiskLedger,
    day: _RiskDay,
) -> Any:
    """Cap later strategy entries after a prior risk-driven reduction.

    The first day's raw strategy fills are replayed before that day's risk
    adjustment to preserve the established execution ordering.  On later days
    the source ledger can be stale because a risk sale changed inventory and
    cash.  Preventing a new source buy from exceeding the current effective
    ratio avoids relying on a later synthetic sale that may itself be blocked.
    """
    cap = max(float(day.effective_ratio), 0.0)
    if not ledger.risk_reduced_codes or cap >= 1.0 - 1e-12:
        return None

    def _guard(fill: dict[str, Any], qty: int, cash_delta: float) -> str | None:
        code = str(fill.get("stock_code", "")).strip()
        quantities = dict(ledger.qty)
        quantities[code] = int(quantities.get(code, 0) or 0) + int(qty)
        position_value = 0.0
        for held_code, held_qty in quantities.items():
            if int(held_qty) <= 0:
                continue
            price = _valuation_price(ledger, day, held_code)
            if held_code == code and (not math.isfinite(price) or price <= 0):
                price = _float_value(fill.get("price"))
            if not math.isfinite(price) or price <= 0:
                return "missing_valuation_price"
            position_value += int(held_qty) * price
        equity = float(ledger.cash + cash_delta + position_value)
        if not math.isfinite(equity) or equity <= 0:
            return "risk_position_cap"
        # The replay can binary-search a legal lot, so use an exact cap rather
        # than accepting an overage that a later blocked risk sell cannot undo.
        if position_value > cap * equity + 1e-9:
            return "risk_position_cap"
        return None

    return _guard


def _new_risk_row(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    code: str,
    side: str,
    qty: int,
    price: float,
    cost_cfg: Any,
) -> dict[str, Any] | None:
    row = _build_risk_fill_row(
        trade_date=day.trade_date_text,
        stock_code=code,
        side=side,
        qty=qty,
        raw_price=price,
        cost_cfg=cost_cfg,
        note=day.note,
        before_ratio=day.raw_ratio,
        after_ratio=day.effective_ratio,
        risk_reason=day.risk_reason,
        risk_state=day.risk_state,
        dd_state=day.dd_state,
        order_idx=ledger.next_order_idx,
    )
    if row is None:
        return None
    is_recovery_buy = side == "buy" and any(
        str(previous.get("stock_code", "")).strip() == code
        and str(previous.get("side", "")).strip().lower() == "sell"
        for previous in ledger.rows
    )
    if is_recovery_buy:
        _, separator, suffix = str(row.get("note", "") or "").partition("；")
        row["risk_reason"] = "风控恢复"
        row["note"] = f"风控恢复{separator}{suffix}" if separator else "风控恢复"
    shares_before = int(ledger.qty.get(code, 0) or 0)
    shares_after = shares_before + qty if side == "buy" else max(shares_before - qty, 0)
    row.update(
        {
            "cash_before": float(ledger.cash),
            "cash_after": float(ledger.cash + float(row["cash_delta"])),
            "shares_before": shares_before,
            "shares_after": shares_after,
        }
    )
    return row


def _apply_risk_sell(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    code: str,
    current_qty: int,
    target_qty: int,
    price: float,
    lot_size: int,
    cost_cfg: Any,
    trade_constraints: Any,
) -> None:
    sell_qty = _normalize_risk_sell_qty(
        code,
        current_qty - target_qty,
        position_qty=current_qty,
        lot_size=lot_size,
    )
    sell_qty = _constrained_risk_qty(
        day,
        code=code,
        side="sell",
        qty=sell_qty,
        position_qty=current_qty,
        lot_size=lot_size,
        trade_constraints=trade_constraints,
    )
    row = _new_risk_row(
        ledger,
        day,
        code=code,
        side="sell",
        qty=sell_qty,
        price=price,
        cost_cfg=cost_cfg,
    )
    if row is None:
        return
    remaining_qty = current_qty - sell_qty
    if remaining_qty > 0:
        ledger.qty[code] = remaining_qty
    else:
        ledger.qty.pop(code, None)
    ledger.append(row)
    ledger.risk_reduced_codes.add(code)


def _buy_preserves_account_constraints(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    code: str,
    qty: int,
    price: float,
    lot_size: int,
    cost_cfg: Any,
) -> bool:
    row = _new_risk_row(
        ledger,
        day,
        code=code,
        side="buy",
        qty=qty,
        price=price,
        cost_cfg=cost_cfg,
    )
    if row is None:
        return False
    cash = float(ledger.cash + float(row["cash_delta"]))
    position_value = sum(
        int(current_qty) * _valuation_price(ledger, day, current_code)
        for current_code, current_qty in ledger.qty.items()
    ) + qty * _valuation_price(ledger, day, code)
    equity = cash + position_value
    if cash < -1e-9 or equity <= 0:
        return False
    tolerance = (
        buy_qty_step(code, lot_size=lot_size) * _valuation_price(ledger, day, code) / equity
    )
    return position_value / equity <= max(float(day.effective_ratio), 0.0) + tolerance + 1e-12


def _affordable_risk_buy_qty(
    ledger: _RiskLedger,
    *,
    code: str,
    qty: int,
    price: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    day: _RiskDay,
) -> int:
    if not math.isfinite(price) or price <= 0:
        return 0
    normalized_qty = normalize_buy_qty(code, qty, lot_size=lot_size)
    if normalized_qty <= 0 or (not allow_partial_fill and normalized_qty != qty):
        return 0
    qty = normalized_qty
    if allow_partial_fill:
        slippage_bps = (
            float(getattr(get_tc(cost_cfg), "slippage_bps", 0.0) or 0.0)
            if is_cost_enabled(cost_cfg)
            else 0.0
        )
        exec_price = float(apply_slippage(price, "buy", slippage_bps))
        affordable_qty, *_ = _max_affordable_buy_qty(
            cash=ledger.cash,
            qty_limit=qty,
            lot_size=lot_size,
            exec_price=exec_price,
            cost_cfg=cost_cfg,
            stock_code=code,
        )
        while affordable_qty > 0:
            if _buy_preserves_account_constraints(
                ledger,
                day,
                code=code,
                qty=affordable_qty,
                price=price,
                lot_size=lot_size,
                cost_cfg=cost_cfg,
            ):
                return affordable_qty
            affordable_qty = normalize_buy_qty(
                code,
                affordable_qty - 1,
                lot_size=lot_size,
            )
        return 0
    return (
        qty
        if _buy_preserves_account_constraints(
            ledger,
            day,
            code=code,
            qty=qty,
            price=price,
            lot_size=lot_size,
            cost_cfg=cost_cfg,
        )
        else 0
    )


def _apply_risk_buy(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    code: str,
    current_qty: int,
    target_qty: int,
    price: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any,
) -> None:
    qty = _affordable_risk_buy_qty(
        ledger,
        code=code,
        qty=target_qty - current_qty,
        price=price,
        lot_size=lot_size,
        allow_partial_fill=allow_partial_fill,
        cost_cfg=cost_cfg,
        day=day,
    )
    qty = _constrained_risk_qty(
        day,
        code=code,
        side="buy",
        qty=qty,
        position_qty=None,
        lot_size=lot_size,
        trade_constraints=trade_constraints,
    )
    if qty <= 0:
        return
    row = _new_risk_row(
        ledger,
        day,
        code=code,
        side="buy",
        qty=qty,
        price=price,
        cost_cfg=cost_cfg,
    )
    if row is None:
        return
    ledger.qty[code] = current_qty + qty
    ledger.append(row)


def _reconcile_risk_positions(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any,
) -> None:
    desired = _desired_quantities(
        ledger,
        day,
        lot_size=lot_size,
        cost_cfg=cost_cfg,
    )
    codes = sorted(set(ledger.qty) | set(desired), key=str.casefold)
    for code in codes:
        current_qty = int(ledger.qty.get(code, 0) or 0)
        target_qty = int(desired.get(code, 0) or 0)
        if target_qty >= current_qty:
            continue
        price = _risk_execution_price(day, code, trade_constraints=trade_constraints)
        if not math.isfinite(price) or price <= 0:
            if trade_constraints is not None:
                continue
            raise DataValidationError(
                f"broker {day.trade_date_text}: 风控调仓缺少 {code} 的有效收盘价。"
            )
        if _risk_trade_rejection(
            day,
            code=code,
            side="sell",
            trade_constraints=trade_constraints,
        ) is not None:
            continue
        _apply_risk_sell(
            ledger,
            day,
            code=code,
            current_qty=current_qty,
            target_qty=target_qty,
            price=price,
            lot_size=lot_size,
            cost_cfg=cost_cfg,
            trade_constraints=trade_constraints,
        )
    for code in codes:
        current_qty = int(ledger.qty.get(code, 0) or 0)
        target_qty = int(desired.get(code, 0) or 0)
        if target_qty > current_qty:
            price = _risk_execution_price(day, code, trade_constraints=trade_constraints)
            if not math.isfinite(price) or price <= 0:
                if trade_constraints is not None:
                    continue
                raise DataValidationError(
                    f"broker {day.trade_date_text}: 风控调仓缺少 {code} 的有效收盘价。"
                )
            if _risk_trade_rejection(
                day,
                code=code,
                side="buy",
                trade_constraints=trade_constraints,
            ) is not None:
                continue
            _apply_risk_buy(
                ledger,
                day,
                code=code,
                current_qty=current_qty,
                target_qty=target_qty,
                price=price,
                lot_size=lot_size,
                allow_partial_fill=allow_partial_fill,
                cost_cfg=cost_cfg,
                trade_constraints=trade_constraints,
            )


def _record_daily_accounting(
    ledger: _RiskLedger,
    day: _RiskDay,
    *,
    emitted_rows: list[dict[str, Any]],
    strategy_rows: list[dict[str, Any]],
) -> None:
    for code, item in day.raw_positions.items():
        # A snapshot close is an end-of-day valuation mark.  It remains valid
        # for NAV even when the quote is not eligible to create a risk fill.
        price = _float_value(item.get("close", item.get("trade_price", 0.0)))
        if math.isfinite(price) and price > 0:
            ledger.last_prices[code] = price
    position_value = sum(
        int(qty) * float(ledger.last_prices.get(code, 0.0) or 0.0)
        for code, qty in sorted(ledger.qty.items(), key=lambda item: item[0].casefold())
    )
    equity = float(ledger.cash + position_value)
    commission = sum(float(row.get("commission", 0.0) or 0.0) for row in emitted_rows)
    stamp_tax = sum(float(row.get("stamp_tax", 0.0) or 0.0) for row in emitted_rows)
    slippage_cost = sum(float(row.get("slippage_cost", 0.0) or 0.0) for row in emitted_rows)
    fee_cost = sum(float(row.get("total_cost", 0.0) or 0.0) for row in emitted_rows)
    risk_buy_count = sum(str(row.get("side", "")).strip().lower() == "buy" for row in emitted_rows)
    risk_sell_count = sum(str(row.get("side", "")).strip().lower() == "sell" for row in emitted_rows)
    strategy_commission = sum(
        float(row.get("commission", 0.0) or 0.0) for row in strategy_rows
    )
    strategy_stamp_tax = sum(
        float(row.get("stamp_tax", 0.0) or 0.0) for row in strategy_rows
    )
    strategy_slippage_cost = sum(
        float(row.get("slippage_cost", 0.0) or 0.0) for row in strategy_rows
    )
    strategy_fee_cost = sum(
        float(row.get("total_cost", 0.0) or 0.0) for row in strategy_rows
    )
    strategy_buy_count = sum(
        str(row.get("side", "")).strip().lower() == "buy" for row in strategy_rows
    )
    strategy_sell_count = sum(
        str(row.get("side", "")).strip().lower() == "sell" for row in strategy_rows
    )
    ledger.daily_rows.append(
        {
            COL_TRADE_DATE: day.trade_date,
            "cash": float(ledger.cash),
            "position_value": float(position_value),
            "equity": equity,
            "utilization_rate": float(position_value / equity) if equity else 0.0,
            "risk_fill_count": len(emitted_rows),
            "risk_buy_count": int(risk_buy_count),
            "risk_sell_count": int(risk_sell_count),
            "risk_commission": float(commission),
            "risk_stamp_tax": float(stamp_tax),
            "risk_slippage_cost": float(slippage_cost),
            "risk_fee_cost": float(fee_cost),
            "risk_total_cost": float(fee_cost + slippage_cost),
            "strategy_fill_count": len(strategy_rows),
            "strategy_buy_count": int(strategy_buy_count),
            "strategy_sell_count": int(strategy_sell_count),
            "strategy_commission": float(strategy_commission),
            "strategy_stamp_tax": float(strategy_stamp_tax),
            "strategy_slippage_cost": float(strategy_slippage_cost),
            "strategy_fee_cost": float(strategy_fee_cost),
            "strategy_total_cost": float(strategy_fee_cost + strategy_slippage_cost),
        }
    )
    snapshot_positions = [
        {
            "stock_code": code,
            "qty": int(qty),
            "close": float(ledger.last_prices.get(code, 0.0) or 0.0),
            "current_quote_valid": _trade_price(day, code) > 0,
        }
        for code, qty in sorted(ledger.qty.items(), key=lambda item: item[0].casefold())
        if int(qty) > 0
    ]
    ledger.daily_snapshots.append(
        {
            "trade_date": day.trade_date_text,
            "positions": snapshot_positions,
        }
    )


def build_synthetic_risk_fill_result(
    *,
    df_out: pd.DataFrame,
    fills_df: pd.DataFrame,
    daily_position_snapshots: list[dict[str, Any]],
    initial_cash: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any = None,
) -> SyntheticRiskFillResult:
    if not math.isfinite(initial_cash):
        raise DataValidationError("broker 风控影子账本 initial_cash 必须为有限数值。")
    if not daily_position_snapshots or "effective_position_ratio" not in df_out.columns:
        return SyntheticRiskFillResult(
            fills_df=pd.DataFrame(),
            strategy_fills_df=pd.DataFrame(),
            strategy_rejections_df=pd.DataFrame(),
            daily_accounting_df=pd.DataFrame(),
            daily_position_snapshots=(),
        )
    normalized_fills = _normalize_strategy_fills(fills_df)
    fills_by_date = _group_fills_by_date(normalized_fills)
    snapshots_by_date = _positions_by_date(daily_position_snapshots)
    ledger = _RiskLedger(
        cash=float(initial_cash),
        next_order_idx=_starting_risk_order_index(normalized_fills),
    )
    work = df_out.copy()
    work[COL_TRADE_DATE] = pd.to_datetime(work[COL_TRADE_DATE], errors="coerce").dt.normalize()
    if work[COL_TRADE_DATE].isna().any():
        raise DataValidationError("broker 风控净值 trade_date 存在 NaT。")
    work = work.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    raw_util_col = "utilization_rate_raw" if "utilization_rate_raw" in work.columns else "utilization_rate"
    for _, row in work.iterrows():
        trade_date = pd.Timestamp(row[COL_TRADE_DATE]).normalize()
        day = _build_risk_day(
            row,
            raw_positions=snapshots_by_date.get(trade_date, {}),
            raw_util_col=raw_util_col,
        )
        strategy_rows = _apply_strategy_fills(
            ledger,
            fills_by_date.get(trade_date, []),
            cost_cfg=cost_cfg,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            buy_guard=_strategy_buy_guard(ledger, day),
            rejections=ledger.strategy_rejections,
        )
        ledger.strategy_rows.extend(strategy_rows)
        emitted_start = len(ledger.rows)
        _reconcile_risk_positions(
            ledger,
            day,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            cost_cfg=cost_cfg,
            trade_constraints=trade_constraints,
        )
        _record_daily_accounting(
            ledger,
            day,
            emitted_rows=ledger.rows[emitted_start:],
            strategy_rows=strategy_rows,
        )
    return SyntheticRiskFillResult(
        fills_df=pd.DataFrame(ledger.rows) if ledger.rows else pd.DataFrame(),
        strategy_fills_df=_strategy_fills_frame(ledger.strategy_rows),
        strategy_rejections_df=_strategy_rejections_frame(ledger.strategy_rejections),
        daily_accounting_df=pd.DataFrame(ledger.daily_rows) if ledger.daily_rows else pd.DataFrame(),
        daily_position_snapshots=tuple(ledger.daily_snapshots),
    )


def build_synthetic_risk_fills_df(
    *,
    df_out: pd.DataFrame,
    fills_df: pd.DataFrame,
    daily_position_snapshots: list[dict[str, Any]],
    initial_cash: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any = None,
) -> pd.DataFrame:
    return build_synthetic_risk_fill_result(
        df_out=df_out,
        fills_df=fills_df,
        daily_position_snapshots=daily_position_snapshots,
        initial_cash=initial_cash,
        lot_size=lot_size,
        allow_partial_fill=allow_partial_fill,
        cost_cfg=cost_cfg,
        trade_constraints=trade_constraints,
    ).fills_df


__all__ = ["SyntheticRiskFillResult", "build_synthetic_risk_fill_result", "build_synthetic_risk_fills_df"]
