"""Build synthetic risk fills for independently funded portfolio slots."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from typing import Any, cast

import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from trading.lot_rules import buy_qty_step, normalize_buy_qty

from .broker.engine import _max_affordable_buy_qty
from .broker.shadow_costs import apply_shadow_strategy_fills as _apply_strategy_fills
from .broker.shadow_costs import build_shadow_strategy_fills_frame as _strategy_fills_frame
from .broker.shadow_costs import build_shadow_strategy_rejections_frame as _strategy_rejections_frame
from .constraints import is_limit_locked, is_no_trade_day, max_trade_qty
from .costs import apply_slippage, cost_from_config, get_tc, is_cost_enabled
from .risk_fill_metadata import resolve_risk_reason as _resolve_risk_reason
from .risk_lot_rules import feasible_risk_target_quantity as _feasible_target_quantity
from .risk_lot_rules import normalize_risk_sell_qty as _normalize_risk_sell_qty

_USE_RISK_COST_CONFIG = object()


@dataclass(frozen=True, slots=True)
class _RiskDayContext:
    slot_id: str
    trade_date: pd.Timestamp
    raw_positions: dict[str, dict[str, Any]]
    raw_ratio: float
    effective_ratio: float
    risk_state: str
    dd_state: str
    risk_reason: str
    note: str

    @property
    def trade_date_text(self) -> str:
        return self.trade_date.strftime("%Y-%m-%d")


@dataclass(slots=True)
class _SlotShadowState:
    cash: float
    initial_capital: float
    next_order_idx: int
    quantities: dict[str, int] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, object]] = field(default_factory=list)
    strategy_rows: list[dict[str, object]] = field(default_factory=list)
    strategy_rejections: list[dict[str, object]] = field(default_factory=list)
    daily_rows: list[dict[str, object]] = field(default_factory=list)
    has_prior_risk_reduction: bool = False

    @property
    def qty(self) -> dict[str, int]:
        return self.quantities


@dataclass(frozen=True, slots=True)
class SlotBrokerRiskFillResult:
    fills_df: pd.DataFrame
    strategy_fills_df: pd.DataFrame
    strategy_rejections_df: pd.DataFrame
    daily_accounting_by_slot: dict[str, pd.DataFrame]


def _build_risk_note(
    *,
    raw_ratio: float,
    effective_ratio: float,
    risk_state: str,
    dd_state: str,
) -> tuple[str, str]:
    risk_reason = _resolve_risk_reason(
        before_ratio=raw_ratio,
        after_ratio=effective_ratio,
        risk_state=risk_state,
        dd_state=dd_state,
    )
    ratio_note = (
        f"风控调仓 槽位仓位 {raw_ratio:.4f} -> {effective_ratio:.4f}"
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
    note = f"{risk_reason}；{ratio_note}；{state_note}" if state_note else f"{risk_reason}；{ratio_note}"
    return risk_reason, note


def _build_risk_day_context(
    *,
    slot_id: str,
    row: pd.Series,
    raw_positions: dict[str, dict[str, Any]],
    raw_util_col: str,
) -> _RiskDayContext:
    trade_date = pd.Timestamp(row[COL_TRADE_DATE]).normalize()
    raw_value = row.get(raw_util_col)
    raw_ratio = float(pd.to_numeric(raw_value, errors="coerce")) if pd.notna(raw_value) else 0.0
    effective_value = row.get("effective_position_ratio")
    effective_ratio = (
        float(pd.to_numeric(effective_value, errors="coerce"))
        if pd.notna(effective_value)
        else raw_ratio
    )
    risk_state = str(row.get("risk_state", "") or "").strip()
    dd_state = str(row.get("dd_state", "") or "").strip()
    risk_reason, note = _build_risk_note(
        raw_ratio=raw_ratio,
        effective_ratio=effective_ratio,
        risk_state=risk_state,
        dd_state=dd_state,
    )
    return _RiskDayContext(
        slot_id=str(slot_id),
        trade_date=trade_date,
        raw_positions=raw_positions,
        raw_ratio=raw_ratio,
        effective_ratio=effective_ratio,
        risk_state=risk_state,
        dd_state=dd_state,
        risk_reason=risk_reason,
        note=note,
    )


def _normalize_strategy_slot_fills(
    df: pd.DataFrame | None,
) -> dict[pd.Timestamp, list[dict[str, object]]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    normalized = df.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"], errors="coerce").dt.normalize()
    normalized["stock_code"] = normalized["stock_code"].astype(str).str.strip()
    normalized["side"] = normalized["side"].astype(str).str.strip().str.lower()
    normalized["qty"] = pd.to_numeric(normalized["qty"], errors="coerce").fillna(0).astype(int)
    cash_delta_series = normalized.get("cash_delta")
    normalized["cash_delta"] = pd.to_numeric(cash_delta_series, errors="coerce") if cash_delta_series is not None else None
    if "_slot_event_seq" not in normalized.columns:
        normalized["_slot_event_seq"] = pd.Series(
            range(1, len(normalized) + 1),
            index=normalized.index,
            dtype="int64",
        )
    if "source" in normalized.columns:
        source = normalized["source"].astype(str).str.strip().str.lower()
        normalized = normalized.loc[source.ne("risk")]
    normalized = normalized.sort_values(
        ["trade_date", "_slot_event_seq"],
        kind="mergesort",
    ).reset_index(drop=True)
    grouped: dict[pd.Timestamp, list[dict[str, object]]] = defaultdict(list)
    for _, row in normalized.iterrows():
        trade_date = row.get("trade_date")
        if pd.isna(trade_date):
            continue
        grouped[pd.Timestamp(trade_date).normalize()].append(cast(dict[str, object], row.to_dict()))
    return grouped


def _build_slot_snapshot_by_date(
    df: pd.DataFrame,
) -> dict[pd.Timestamp, dict[str, dict[str, Any]]]:
    """Build last-row-wins snapshots with separate valuation and trade prices.

    ``mark_price`` is emitted by cash accounting and is valid for valuation
    only.  A risk rebalance still requires a current valid raw close through
    ``trade_price``; carrying a mark forward must never create a synthetic
    executable quote.
    """
    snapshots: dict[pd.Timestamp, dict[str, dict[str, Any]]] = {}
    work = ensure_trade_date_normalized(df.copy(), col=COL_TRADE_DATE)
    work = work.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    has_mark_price = "mark_price" in work.columns
    has_open = "open" in work.columns
    has_high = "high" in work.columns
    has_low = "low" in work.columns
    has_volume = "volume" in work.columns
    has_pre_close = "pre_close" in work.columns
    has_stock_name = "stock_name" in work.columns
    last_close_by_code: dict[str, float] = {}
    for row in work.itertuples(index=False):
        trade_date = pd.Timestamp(getattr(row, COL_TRADE_DATE)).normalize()
        stock_code = str(getattr(row, "stock_code", "") or "").strip()
        shares = int(pd.to_numeric(getattr(row, "shares", 0), errors="coerce") or 0)
        close_value = pd.to_numeric(getattr(row, "close", 0.0), errors="coerce")
        close = float(close_value) if pd.notna(close_value) else 0.0
        open_value = pd.to_numeric(getattr(row, "open", None), errors="coerce") if has_open else None
        high_value = pd.to_numeric(getattr(row, "high", None), errors="coerce") if has_high else None
        low_value = pd.to_numeric(getattr(row, "low", None), errors="coerce") if has_low else None
        volume_value = pd.to_numeric(getattr(row, "volume", None), errors="coerce") if has_volume else None
        pre_close_value = pd.to_numeric(getattr(row, "pre_close", None), errors="coerce") if has_pre_close else None
        open_price = float(open_value) if pd.notna(open_value) else None
        high_price = float(high_value) if pd.notna(high_value) else None
        low_price = float(low_value) if pd.notna(low_value) else None
        volume = float(volume_value) if pd.notna(volume_value) else None
        previous_close = float(pre_close_value) if pd.notna(pre_close_value) else None
        if previous_close is None or not math.isfinite(previous_close) or previous_close <= 0:
            previous_close = last_close_by_code.get(stock_code)
        stock_name = (
            str(getattr(row, "stock_name", "") or "").strip() or None
            if has_stock_name
            else None
        )
        if stock_code and math.isfinite(close) and close > 0:
            last_close_by_code[stock_code] = close
        mark_value = pd.to_numeric(getattr(row, "mark_price", None), errors="coerce")
        mark_price = float(mark_value) if pd.notna(mark_value) else 0.0
        if not math.isfinite(mark_price) or mark_price <= 0:
            mark_price = close if math.isfinite(close) and close > 0 else 0.0
        if not stock_code or shares <= 0:
            snapshots[trade_date] = {}
            continue
        item: dict[str, Any] = {
            "qty": shares,
            "close": close if math.isfinite(close) and close > 0 else 0.0,
        }
        if has_open:
            item["open"] = open_price
        if has_high:
            item["high"] = high_price
        if has_low:
            item["low"] = low_price
        if has_volume:
            item["volume"] = volume
        if has_pre_close or previous_close is not None:
            item["pre_close"] = previous_close
        if has_stock_name:
            item["stock_name"] = stock_name
        if has_mark_price:
            item["mark_price"] = mark_price
        quote_valid_value = getattr(row, "current_quote_valid", None)
        if quote_valid_value is not None and pd.notna(quote_valid_value):
            current_quote_valid = bool(quote_valid_value)
        else:
            current_quote_valid = volume is None or (math.isfinite(volume) and volume > 0)
        if not current_quote_valid or not math.isfinite(close) or close <= 0:
            item["trade_price"] = 0.0
        snapshots[trade_date] = {stock_code: item}
    return snapshots


def _append_risk_fill(
    state: _SlotShadowState,
    fill: dict[str, object],
) -> None:
    code = str(fill.get("stock_code", "")).strip()
    qty = int(cast(Any, fill.get("qty", 0)) or 0)
    if not code or qty <= 0:
        return
    side = str(fill.get("side", "")).strip().lower()
    current_qty = int(state.quantities.get(code, 0) or 0)
    if side == "buy":
        state.quantities[code] = current_qty + qty
    elif side == "sell":
        if qty > current_qty:
            raise DataValidationError("slot 风控成交卖出数量超过影子账本持仓。")
        remaining_qty = current_qty - qty
        if remaining_qty > 0:
            state.quantities[code] = remaining_qty
        else:
            state.quantities.pop(code, None)
    else:
        return
    state.cash = float(cast(Any, fill["cash_after"]))
    state.rows.append(fill)
    state.next_order_idx += 1


def _replay_strategy_fills(
    state: _SlotShadowState,
    fills: list[dict[str, object]],
    *,
    slot_id: str,
    day: _RiskDayContext,
    lot_size: int,
    allow_partial_fill: bool,
    strategy_cost_cfg: Any,
) -> list[dict[str, object]]:
    rejected_rows: list[dict[str, Any]] = []
    applied_rows = _apply_strategy_fills(
        state,
        cast(list[dict[str, Any]], fills),
        cost_cfg=strategy_cost_cfg,
        lot_size=lot_size,
        allow_partial_fill=allow_partial_fill,
        buy_guard=_strategy_buy_guard(state, day),
        rejections=rejected_rows,
    )
    for row in applied_rows:
        row["slot_id"] = str(row.get("slot_id", slot_id) or slot_id)
    for row in rejected_rows:
        row["slot_id"] = str(row.get("slot_id", slot_id) or slot_id)
    state.strategy_rejections.extend(cast(list[dict[str, object]], rejected_rows))
    return applied_rows


def _valuation_price(
    state: _SlotShadowState,
    day: _RiskDayContext,
    stock_code: str,
) -> float:
    item = day.raw_positions.get(stock_code, {})
    value = pd.to_numeric(
        item.get("mark_price", item.get("close", 0.0)),
        errors="coerce",
    )
    price = float(value) if pd.notna(value) else 0.0
    if not math.isfinite(price) or price <= 0:
        price = float(state.last_prices.get(stock_code, 0.0) or 0.0)
    return price if math.isfinite(price) and price > 0 else 0.0


def _trade_price(day: _RiskDayContext, stock_code: str) -> float:
    item = day.raw_positions.get(stock_code, {})
    value = pd.to_numeric(
        item.get("trade_price", item.get("close", 0.0)),
        errors="coerce",
    )
    price = float(value) if pd.notna(value) else 0.0
    return price if math.isfinite(price) and price > 0 else 0.0


def _has_execution_state(item: dict[str, Any]) -> bool:
    return any(
        key in item
        for key in ("open", "high", "low", "volume", "pre_close", "stock_name")
    )


def _risk_trade_rejection(
    day: _RiskDayContext,
    *,
    stock_code: str,
    side: str,
    trade_constraints: Any,
) -> str | None:
    """Apply the shared execution constraints before creating a risk fill."""
    if trade_constraints is None:
        return None
    item = day.raw_positions.get(stock_code, {})
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
        stock_code=stock_code,
        stock_name=item.get("stock_name"),
    ):
        return "limit_locked"
    return None


def _overlay_snapshot_execution_quote(
    raw_positions: dict[pd.Timestamp, dict[str, dict[str, Any]]],
    *,
    quote_resolver: Any,
) -> None:
    """Fill execution metadata from the canonical quote source when available."""
    if quote_resolver is None:
        return
    for trade_date, positions in raw_positions.items():
        for stock_code, item in positions.items():
            quote = quote_resolver(stock_code, trade_date.strftime("%Y-%m-%d"))
            if quote is None:
                item["trade_price"] = 0.0
                continue
            item.update(
                {
                    "open": quote.open,
                    "high": quote.high,
                    "low": quote.low,
                    "volume": quote.volume,
                    "pre_close": quote.previous_close,
                    "stock_name": quote.stock_name,
                    "execution_close": quote.close,
                }
            )
            if not bool(quote.tradable):
                item["trade_price"] = 0.0
            elif math.isfinite(float(quote.close)) and float(quote.close) > 0:
                item["trade_price"] = float(quote.close)
            else:
                item["trade_price"] = 0.0


def _constrained_risk_qty(
    day: _RiskDayContext,
    *,
    stock_code: str,
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
    volume = day.raw_positions.get(stock_code, {}).get("volume")
    if volume is None:
        return max(int(qty), 0)
    max_qty = max_trade_qty(
        volume=volume,
        participation_rate=float(getattr(trade_constraints, "max_participation_rate", 0.0)),
        lot_size=lot_size,
        stock_code=stock_code,
        side=side,
        position_qty=position_qty,
    )
    return min(max(int(qty), 0), max_qty)


def _project_post_sell_account(
    state: _SlotShadowState,
    day: _RiskDayContext,
    desired: dict[str, int],
    *,
    cost_cfg: Any,
) -> tuple[float, float, float]:
    cash = float(state.cash)
    position_value = 0.0
    for stock_code, current_qty in sorted(
        state.quantities.items(),
        key=lambda item: item[0].casefold(),
    ):
        target_qty = min(int(desired.get(stock_code, 0) or 0), int(current_qty))
        sell_qty = int(current_qty) - target_qty
        valuation_price = _valuation_price(state, day, stock_code)
        if sell_qty > 0:
            price = _trade_price(day, stock_code)
            if price <= 0:
                return math.nan, math.nan, math.nan
            projected = _build_risk_fill_row(
                day,
                state,
                stock_code=stock_code,
                side="sell",
                qty=sell_qty,
                raw_price=price,
                cost_cfg=cost_cfg,
            )
            if projected is None:
                return math.nan, math.nan, math.nan
            cash += float(cast(Any, projected["cash_delta"]))
        position_value += target_qty * valuation_price
    equity = cash + position_value
    return cash, position_value, equity


def _position_ratio_tolerance(
    state: _SlotShadowState,
    day: _RiskDayContext,
    quantities: dict[str, int],
    *,
    lot_size: int,
    equity: float,
) -> float:
    if equity <= 0:
        return 0.0
    step_values = [
        buy_qty_step(stock_code, lot_size=lot_size)
        * _valuation_price(state, day, stock_code)
        for stock_code, qty in quantities.items()
        if int(qty) > 0 and _valuation_price(state, day, stock_code) > 0
    ]
    return min(step_values) / equity if step_values else 0.0


def _adjust_targets_for_sell_costs(
    state: _SlotShadowState,
    day: _RiskDayContext,
    desired: dict[str, int],
    *,
    lot_size: int,
    cost_cfg: Any,
) -> dict[str, int]:
    adjusted = dict(desired)
    max_iterations = sum(max(int(qty), 0) for qty in state.quantities.values()) + 1
    for _ in range(max_iterations):
        cash, position_value, equity = _project_post_sell_account(
            state,
            day,
            adjusted,
            cost_cfg=cost_cfg,
        )
        if not all(math.isfinite(value) for value in (cash, position_value, equity)):
            return adjusted
        tolerance = _position_ratio_tolerance(
            state,
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
                stock_code
                for stock_code, current_qty in state.quantities.items()
                if min(int(adjusted.get(stock_code, 0) or 0), int(current_qty)) > 0
            ),
            key=lambda stock_code: (
                -min(
                    int(adjusted.get(stock_code, 0) or 0),
                    int(state.quantities.get(stock_code, 0) or 0),
                )
                * _valuation_price(state, day, stock_code),
                stock_code.casefold(),
            ),
        )
        if not candidates:
            break
        stock_code = candidates[0]
        current_target = min(
            int(adjusted.get(stock_code, 0) or 0),
            int(state.quantities.get(stock_code, 0) or 0),
        )
        position_qty = int(state.quantities.get(stock_code, 0) or 0)
        sell_qty = _normalize_risk_sell_qty(
            stock_code,
            position_qty - current_target + 1,
            position_qty=position_qty,
            lot_size=lot_size,
        )
        next_target = position_qty - sell_qty
        if next_target > 0:
            adjusted[stock_code] = next_target
        else:
            adjusted.pop(stock_code, None)
    raise DataValidationError(
        f"{day.slot_id} {day.trade_date_text}: 风控目标无法同时满足非负现金与仓位上限。"
    )


def _desired_quantities(
    state: _SlotShadowState,
    day: _RiskDayContext,
    *,
    lot_size: int,
    cost_cfg: Any,
) -> dict[str, int]:
    current_position_value = sum(
        int(qty) * _valuation_price(state, day, stock_code)
        for stock_code, qty in sorted(
            state.quantities.items(),
            key=lambda item: item[0].casefold(),
        )
    )
    equity = float(state.cash + current_position_value)
    target_position_value = max(equity, 0.0) * max(float(day.effective_ratio), 0.0)
    raw_values = {
        stock_code: int(item["qty"]) * _valuation_price(state, day, stock_code)
        for stock_code, item in sorted(
            day.raw_positions.items(),
            key=lambda item: item[0].casefold(),
        )
    }
    raw_total_value = float(sum(raw_values.values()))
    desired: dict[str, int] = {}
    if raw_total_value <= 0 or target_position_value <= 0:
        return _adjust_targets_for_sell_costs(
            state,
            day,
            desired,
            lot_size=lot_size,
            cost_cfg=cost_cfg,
        )
    for code, item in sorted(day.raw_positions.items(), key=lambda item: item[0].casefold()):
        qty = int(item["qty"])
        price = _valuation_price(state, day, code)
        allocated_value = target_position_value * raw_values[code] / raw_total_value
        qty_by_value = int((allocated_value + 1e-9) / price) if price > 0 else 0
        target_qty = _feasible_target_quantity(
            code,
            current_qty=int(state.quantities.get(code, 0) or 0),
            target_limit=min(qty, qty_by_value),
            lot_size=lot_size,
        )
        target_qty = min(target_qty, qty)
        if target_qty > 0:
            desired[code] = target_qty
    return _adjust_targets_for_sell_costs(
        state,
        day,
        desired,
        lot_size=lot_size,
        cost_cfg=cost_cfg,
    )


def _strategy_buy_guard(
    state: _SlotShadowState,
    day: _RiskDayContext,
) -> Any:
    """Cap later strategy entries after a prior risk-driven reduction."""
    cap = max(float(day.effective_ratio), 0.0)
    if not state.has_prior_risk_reduction or cap >= 1.0 - 1e-12:
        return None

    def _guard(fill: dict[str, Any], qty: int, cash_delta: float) -> str | None:
        stock_code = str(fill.get("stock_code", "")).strip()
        quantities = dict(state.quantities)
        quantities[stock_code] = int(quantities.get(stock_code, 0) or 0) + int(qty)
        position_value = 0.0
        for held_code, held_qty in quantities.items():
            if int(held_qty) <= 0:
                continue
            price = _valuation_price(state, day, held_code)
            if held_code == stock_code and (not math.isfinite(price) or price <= 0):
                value = pd.to_numeric(fill.get("price"), errors="coerce")
                price = float(value) if pd.notna(value) else 0.0
            if not math.isfinite(price) or price <= 0:
                return "missing_valuation_price"
            position_value += int(held_qty) * price
        equity = float(state.cash + cash_delta + position_value)
        if not math.isfinite(equity) or equity <= 0:
            return "risk_position_cap"
        if position_value > cap * equity + 1e-9:
            return "risk_position_cap"
        return None

    return _guard


def _build_risk_fill_row(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    stock_code: str,
    side: str,
    qty: int,
    raw_price: float,
    cost_cfg: Any,
) -> dict[str, object] | None:
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
    slippage_cost = (
        max(exec_price - float(raw_price), 0.0) * qty
        if side == "buy"
        else max(float(raw_price) - exec_price, 0.0) * qty
    )
    breakdown = cost_from_config(
        cost_cfg if cost_enabled else None,
        side=side,
        notional=notional,
        slippage_cost=0.0,
    )
    total_cost = float(breakdown.total)
    cash_delta = -(notional + total_cost) if side == "buy" else (notional - total_cost)
    shares_before = int(state.quantities.get(stock_code, 0) or 0)
    shares_after = shares_before + qty if side == "buy" else max(shares_before - qty, 0)
    is_recovery_buy = side == "buy" and any(
        str(previous.get("stock_code", "")).strip() == stock_code
        and str(previous.get("side", "")).strip().lower() == "sell"
        for previous in state.rows
    )
    fill_reason = "风控恢复" if is_recovery_buy else str(day.risk_reason or "")
    fill_note = str(day.note or "")
    if fill_reason != str(day.risk_reason or ""):
        _, separator, suffix = fill_note.partition("；")
        fill_note = f"{fill_reason}{separator}{suffix}" if separator else fill_reason
    return {
        "order_id": f"risk_{state.next_order_idx}",
        "slot_id": day.slot_id,
        "trade_date": day.trade_date_text,
        "stock_code": stock_code,
        "stock_name": "",
        "side": side,
        "qty": int(qty),
        "price": exec_price,
        "commission": float(breakdown.commission),
        "stamp_tax": float(breakdown.stamp_tax),
        "slippage_cost": float(slippage_cost),
        "total_cost": total_cost,
        "cash_delta": float(cash_delta),
        "cash_before": float(state.cash),
        "cash_after": float(state.cash + cash_delta),
        "shares_before": shares_before,
        "shares_after": shares_after,
        "exec_price_type": "close",
        "source": "risk",
        "note": fill_note,
        "before_ratio": float(day.raw_ratio),
        "after_ratio": float(day.effective_ratio),
        "risk_reason": fill_reason,
        "risk_state": str(day.risk_state or ""),
        "dd_state": str(day.dd_state or ""),
    }


def _buy_preserves_account_constraints(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    stock_code: str,
    qty: int,
    lot_size: int,
    cost_cfg: Any,
) -> bool:
    projected = _build_risk_fill_row(
        day,
        state,
        stock_code=stock_code,
        side="buy",
        qty=qty,
        raw_price=_trade_price(day, stock_code),
        cost_cfg=cost_cfg,
    )
    if projected is None:
        return False
    cash = float(state.cash + float(cast(Any, projected["cash_delta"])))
    position_value = sum(
        int(current_qty) * _valuation_price(state, day, current_code)
        for current_code, current_qty in state.quantities.items()
    ) + qty * _valuation_price(state, day, stock_code)
    equity = cash + position_value
    if cash < -1e-9 or equity <= 0:
        return False
    tolerance = (
        buy_qty_step(stock_code, lot_size=lot_size)
        * _valuation_price(state, day, stock_code)
        / equity
    )
    return position_value / equity <= max(float(day.effective_ratio), 0.0) + tolerance + 1e-12


def _affordable_risk_buy_qty(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    stock_code: str,
    qty: int,
    price: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
) -> int:
    if not math.isfinite(price) or price <= 0:
        return 0
    normalized_qty = normalize_buy_qty(stock_code, qty, lot_size=lot_size)
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
            cash=float(state.cash),
            qty_limit=qty,
            lot_size=lot_size,
            exec_price=exec_price,
            cost_cfg=cost_cfg,
            stock_code=stock_code,
        )
        while affordable_qty > 0:
            if _buy_preserves_account_constraints(
                day,
                state,
                stock_code=stock_code,
                qty=affordable_qty,
                lot_size=lot_size,
                cost_cfg=cost_cfg,
            ):
                return affordable_qty
            affordable_qty = normalize_buy_qty(
                stock_code,
                affordable_qty - 1,
                lot_size=lot_size,
            )
        return 0
    return (
        qty
        if _buy_preserves_account_constraints(
            day,
            state,
            stock_code=stock_code,
            qty=qty,
            lot_size=lot_size,
            cost_cfg=cost_cfg,
        )
        else 0
    )


def _apply_risk_delta(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    stock_code: str,
    target_qty: int,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any,
) -> None:
    current_qty = int(state.quantities.get(stock_code, 0) or 0)
    delta = target_qty - current_qty
    if delta == 0:
        return
    side = "sell" if delta < 0 else "buy"
    if _risk_trade_rejection(
        day,
        stock_code=stock_code,
        side=side,
        trade_constraints=trade_constraints,
    ) is not None:
        return
    price_value = pd.to_numeric(
        day.raw_positions.get(stock_code, {}).get(
            "trade_price",
            day.raw_positions.get(stock_code, {}).get("close", 0.0),
        ),
        errors="coerce",
    )
    price = float(price_value) if pd.notna(price_value) else 0.0
    if delta != 0 and (not math.isfinite(price) or price <= 0):
        return
    if delta < 0:
        qty = _normalize_risk_sell_qty(
            stock_code,
            -delta,
            position_qty=current_qty,
            lot_size=lot_size,
        )
        qty = _constrained_risk_qty(
            day,
            stock_code=stock_code,
            side=side,
            qty=qty,
            position_qty=current_qty,
            lot_size=lot_size,
            trade_constraints=trade_constraints,
        )
    elif delta > 0:
        qty = _affordable_risk_buy_qty(
            day,
            state,
            stock_code=stock_code,
            qty=delta,
            price=price,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            cost_cfg=cost_cfg,
        )
        qty = _constrained_risk_qty(
            day,
            stock_code=stock_code,
            side=side,
            qty=qty,
            position_qty=None,
            lot_size=lot_size,
            trade_constraints=trade_constraints,
        )
    if qty <= 0:
        return
    risk_row = _build_risk_fill_row(
        day,
        state,
        stock_code=stock_code,
        side=side,
        qty=qty,
        raw_price=price,
        cost_cfg=cost_cfg,
    )
    if risk_row is not None:
        _append_risk_fill(state, risk_row)
        if side == "sell":
            state.has_prior_risk_reduction = True


def _reconcile_risk_day(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    trade_constraints: Any,
) -> None:
    desired = _desired_quantities(
        state,
        day,
        lot_size=lot_size,
        cost_cfg=cost_cfg,
    )
    stock_codes = sorted(set(state.quantities) | set(desired), key=str.casefold)
    for stock_code in stock_codes:
        target_qty = int(desired.get(stock_code, 0) or 0)
        if target_qty < int(state.quantities.get(stock_code, 0) or 0):
            _apply_risk_delta(
                day,
                state,
                stock_code=stock_code,
                target_qty=target_qty,
                lot_size=lot_size,
                allow_partial_fill=allow_partial_fill,
                cost_cfg=cost_cfg,
                trade_constraints=trade_constraints,
            )
    for stock_code in stock_codes:
        target_qty = int(desired.get(stock_code, 0) or 0)
        if target_qty > int(state.quantities.get(stock_code, 0) or 0):
            _apply_risk_delta(
                day,
                state,
                stock_code=stock_code,
                target_qty=target_qty,
                lot_size=lot_size,
                allow_partial_fill=allow_partial_fill,
                cost_cfg=cost_cfg,
                trade_constraints=trade_constraints,
            )


def _record_daily_accounting(
    day: _RiskDayContext,
    state: _SlotShadowState,
    *,
    emitted_rows: list[dict[str, object]],
    strategy_rows: list[dict[str, object]],
) -> None:
    for stock_code, item in day.raw_positions.items():
        close_value = pd.to_numeric(
            item.get("mark_price", item.get("close", 0.0)),
            errors="coerce",
        )
        close = float(close_value) if pd.notna(close_value) else 0.0
        if math.isfinite(close) and close > 0:
            state.last_prices[stock_code] = close
    position_value = sum(
        int(qty) * float(state.last_prices.get(stock_code, 0.0) or 0.0)
        for stock_code, qty in sorted(state.quantities.items(), key=lambda item: item[0].casefold())
    )
    equity = float(state.cash + position_value)
    previous_equity = (
        float(cast(Any, state.daily_rows[-1]["equity"]))
        if state.daily_rows
        else float(state.initial_capital)
    )
    commission = sum(float(cast(Any, row.get("commission", 0.0)) or 0.0) for row in emitted_rows)
    stamp_tax = sum(float(cast(Any, row.get("stamp_tax", 0.0)) or 0.0) for row in emitted_rows)
    slippage_cost = sum(float(cast(Any, row.get("slippage_cost", 0.0)) or 0.0) for row in emitted_rows)
    fee_cost = sum(float(cast(Any, row.get("total_cost", 0.0)) or 0.0) for row in emitted_rows)
    risk_buy_count = sum(str(row.get("side", "")).strip().lower() == "buy" for row in emitted_rows)
    risk_sell_count = sum(str(row.get("side", "")).strip().lower() == "sell" for row in emitted_rows)
    strategy_commission = sum(
        float(cast(Any, row.get("commission", 0.0)) or 0.0) for row in strategy_rows
    )
    strategy_stamp_tax = sum(
        float(cast(Any, row.get("stamp_tax", 0.0)) or 0.0) for row in strategy_rows
    )
    strategy_slippage_cost = sum(
        float(cast(Any, row.get("slippage_cost", 0.0)) or 0.0) for row in strategy_rows
    )
    strategy_fee_cost = sum(
        float(cast(Any, row.get("total_cost", 0.0)) or 0.0) for row in strategy_rows
    )
    strategy_buy_count = sum(
        str(row.get("side", "")).strip().lower() == "buy" for row in strategy_rows
    )
    strategy_sell_count = sum(
        str(row.get("side", "")).strip().lower() == "sell" for row in strategy_rows
    )
    state.daily_rows.append(
        {
            "slot_id": day.slot_id,
            COL_TRADE_DATE: day.trade_date,
            "cash": float(state.cash),
            "shares": int(sum(state.quantities.values())),
            "position_value_abs": float(position_value),
            "equity": equity,
            "net_value": equity / state.initial_capital,
            "daily_return": equity / previous_equity - 1.0 if previous_equity else 0.0,
            "utilization_rate": float(position_value / equity) if equity else 0.0,
            "risk_fill_count": len(emitted_rows),
            "risk_buy_count": int(risk_buy_count),
            "risk_sell_count": int(risk_sell_count),
            "risk_commission": float(commission),
            "risk_stamp_tax": float(stamp_tax),
            "risk_slippage_cost": float(slippage_cost),
            "risk_fee_cost": float(fee_cost),
            "risk_total_cost": float(fee_cost + slippage_cost),
            "strategy_commission": float(strategy_commission),
            "strategy_stamp_tax": float(strategy_stamp_tax),
            "strategy_slippage_cost": float(strategy_slippage_cost),
            "strategy_fee_cost": float(strategy_fee_cost),
            "strategy_total_cost": float(strategy_fee_cost + strategy_slippage_cost),
            "strategy_buy_count": int(strategy_buy_count),
            "strategy_sell_count": int(strategy_sell_count),
        }
    )


def _build_slot_risk_rows(
    *,
    slot_id: str,
    raw_df: pd.DataFrame,
    risk_df: pd.DataFrame,
    strategy_fills_df: pd.DataFrame | None,
    state: _SlotShadowState,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    strategy_cost_cfg: Any,
    trade_constraints: Any,
    quote_resolver: Any,
) -> None:
    fills_by_date = _normalize_strategy_slot_fills(strategy_fills_df)
    snapshots_by_date = _build_slot_snapshot_by_date(raw_df)
    _overlay_snapshot_execution_quote(
        snapshots_by_date,
        quote_resolver=quote_resolver,
    )
    work = risk_df.copy()
    work[COL_TRADE_DATE] = pd.to_datetime(work[COL_TRADE_DATE], errors="coerce").dt.normalize()
    if work[COL_TRADE_DATE].isna().any():
        raise DataValidationError(f"{slot_id}: risk processed trade_date 存在 NaT。")
    work = work.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    raw_util_col = "utilization_rate_raw" if "utilization_rate_raw" in work.columns else "utilization_rate"
    for _, row in work.iterrows():
        trade_date = pd.Timestamp(row[COL_TRADE_DATE]).normalize()
        day = _build_risk_day_context(
            slot_id=slot_id,
            row=row,
            raw_positions=snapshots_by_date.get(trade_date, {}),
            raw_util_col=raw_util_col,
        )
        strategy_rows = _replay_strategy_fills(
            state,
            fills_by_date.get(trade_date, []),
            slot_id=slot_id,
            day=day,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            strategy_cost_cfg=strategy_cost_cfg,
        )
        state.strategy_rows.extend(strategy_rows)
        emitted_start = len(state.rows)
        _reconcile_risk_day(
            day,
            state,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            cost_cfg=cost_cfg,
            trade_constraints=trade_constraints,
        )
        _record_daily_accounting(
            day,
            state,
            emitted_rows=state.rows[emitted_start:],
            strategy_rows=strategy_rows,
        )


def build_slot_broker_risk_fill_result(
    *,
    raw_processed_dfs: dict[str, pd.DataFrame] | None,
    risk_processed_dfs: dict[str, pd.DataFrame] | None,
    strategy_fills_dfs: dict[str, pd.DataFrame] | None,
    initial_slot_capital: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    strategy_cost_cfg: Any = _USE_RISK_COST_CONFIG,
    starting_order_index: int = 1,
    trade_constraints: Any = None,
    quote_resolver: Any = None,
) -> SlotBrokerRiskFillResult:
    if not isinstance(raw_processed_dfs, dict) or not isinstance(risk_processed_dfs, dict):
        return SlotBrokerRiskFillResult(
            fills_df=pd.DataFrame(),
            strategy_fills_df=pd.DataFrame(),
            strategy_rejections_df=pd.DataFrame(),
            daily_accounting_by_slot={},
        )
    strategy_fills = strategy_fills_dfs if isinstance(strategy_fills_dfs, dict) else {}
    resolved_strategy_cost_cfg = (
        cost_cfg if strategy_cost_cfg is _USE_RISK_COST_CONFIG else strategy_cost_cfg
    )
    state = _SlotShadowState(
        cash=float(initial_slot_capital),
        initial_capital=float(initial_slot_capital),
        next_order_idx=int(starting_order_index),
    )
    daily_accounting_by_slot: dict[str, pd.DataFrame] = {}
    for slot_id, raw_df in sorted(raw_processed_dfs.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
            continue
        risk_df = risk_processed_dfs.get(slot_id)
        if not isinstance(risk_df, pd.DataFrame) or risk_df.empty:
            continue
        if "effective_position_ratio" not in risk_df.columns:
            continue
        slot_state = _SlotShadowState(
            cash=float(initial_slot_capital),
            initial_capital=float(initial_slot_capital),
            next_order_idx=state.next_order_idx,
        )
        _build_slot_risk_rows(
            slot_id=str(slot_id),
            raw_df=raw_df,
            risk_df=risk_df,
            strategy_fills_df=strategy_fills.get(slot_id),
            state=slot_state,
            lot_size=lot_size,
            allow_partial_fill=allow_partial_fill,
            cost_cfg=cost_cfg,
            strategy_cost_cfg=resolved_strategy_cost_cfg,
            trade_constraints=trade_constraints,
            quote_resolver=quote_resolver,
        )
        state.rows.extend(slot_state.rows)
        state.strategy_rows.extend(slot_state.strategy_rows)
        state.strategy_rejections.extend(slot_state.strategy_rejections)
        state.next_order_idx = slot_state.next_order_idx
        daily_accounting_by_slot[str(slot_id)] = (
            pd.DataFrame(slot_state.daily_rows) if slot_state.daily_rows else pd.DataFrame()
        )
    return SlotBrokerRiskFillResult(
        fills_df=pd.DataFrame(state.rows) if state.rows else pd.DataFrame(),
        strategy_fills_df=_strategy_fills_frame(cast(list[dict[str, Any]], state.strategy_rows)),
        strategy_rejections_df=_strategy_rejections_frame(
            cast(list[dict[str, Any]], state.strategy_rejections)
        ),
        daily_accounting_by_slot=daily_accounting_by_slot,
    )


def build_slot_broker_risk_fills(
    *,
    raw_processed_dfs: dict[str, pd.DataFrame] | None,
    risk_processed_dfs: dict[str, pd.DataFrame] | None,
    strategy_fills_dfs: dict[str, pd.DataFrame] | None,
    initial_slot_capital: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg: Any,
    strategy_cost_cfg: Any = _USE_RISK_COST_CONFIG,
    starting_order_index: int = 1,
    trade_constraints: Any = None,
    quote_resolver: Any = None,
) -> pd.DataFrame:
    return build_slot_broker_risk_fill_result(
        raw_processed_dfs=raw_processed_dfs,
        risk_processed_dfs=risk_processed_dfs,
        strategy_fills_dfs=strategy_fills_dfs,
        initial_slot_capital=initial_slot_capital,
        lot_size=lot_size,
        allow_partial_fill=allow_partial_fill,
        cost_cfg=cost_cfg,
        strategy_cost_cfg=strategy_cost_cfg,
        starting_order_index=starting_order_index,
        trade_constraints=trade_constraints,
        quote_resolver=quote_resolver,
    ).fills_df


__all__ = [
    "SlotBrokerRiskFillResult",
    "build_slot_broker_risk_fill_result",
    "build_slot_broker_risk_fills",
]
