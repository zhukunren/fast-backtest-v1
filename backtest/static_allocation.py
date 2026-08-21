"""Target-weight static allocation over a shared panel-native QuoteStore."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError
from trading.lot_rules import normalize_buy_qty

from .broker.engine import BrokerConfig, SimpleBroker
from .constraints import is_limit_locked, is_no_trade_day, max_trade_qty
from .panel_native import QuoteStore
from .static_allocation_contracts import (
    StaticAllocationConfig,
    StaticAllocationCostConfig,
    StaticAllocationResult,
    _collect_target_codes,
    _normalize_bound,
    _target_weights_for_date,
    normalize_target_weight_schedule,
    normalize_target_weights,
    validate_explicit_trade_days,
)


_EXECUTION_PHASE_BY_PRICE_COLUMN = {"open": 0, "close": 1}
_REBALANCE_SIZING_PRICE_COLUMN = "open"


def _validate_quote_store(
    quotes: QuoteStore,
    *,
    output_codes: tuple[str, ...],
    start_date: pd.Timestamp | None,
    end_date: pd.Timestamp | None,
) -> None:
    if not isinstance(quotes, QuoteStore) or quotes.index.row_count <= 0:
        raise DataValidationError("QuoteStore must be non-empty.")
    missing_columns = sorted(
        {"trade_date", "stock_code", "open", "high", "low", "close"}
        - set(quotes.columns),
        key=str.casefold,
    )
    if missing_columns:
        raise DataValidationError(f"QuoteStore missing columns: {missing_columns}")
    missing_codes = [code for code in output_codes if code not in quotes.stock_codes]
    if missing_codes:
        raise DataValidationError(f"missing quotes for target assets: {missing_codes}")

    for code in output_codes:
        stock_slice = quotes.stock_slice(code)
        dates = quotes.index.trade_dates[stock_slice]
        inside = np.ones(len(dates), dtype=bool)
        if start_date is not None:
            inside &= dates >= np.datetime64(start_date, "ns")
        if end_date is not None:
            inside &= dates <= np.datetime64(end_date, "ns")
        if not bool(inside.any()):
            raise DataValidationError(f"{code}: no price rows inside requested window.")
        for column in ("open", "high", "low", "close"):
            try:
                values = np.asarray(quotes.column(column)[stock_slice][inside], dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise DataValidationError(f"{code}: invalid {column} dtype") from exc
            invalid = ~np.isfinite(values) | (values <= 0)
            if bool(invalid.any()):
                bad_dates = dates[inside][invalid][:5]
                sample = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in bad_dates]
                raise DataValidationError(
                    f"{code}: invalid {column} values, dates={sample}"
                )


def _build_period_start_days(
    trade_days: pd.DatetimeIndex,
    period_freq: str,
) -> set[pd.Timestamp]:
    out: set[pd.Timestamp] = set()
    previous_period = None
    for day in trade_days:
        period = pd.Timestamp(day).to_period(period_freq)
        if period != previous_period:
            out.add(pd.Timestamp(day).normalize())
            previous_period = period
    return out


def _build_custom_rebalance_days(
    trade_days: pd.DatetimeIndex,
    raw_dates: tuple[object, ...],
) -> set[pd.Timestamp]:
    if len(trade_days) == 0 or not raw_dates:
        return set()
    days = pd.DatetimeIndex(trade_days).sort_values()
    out: set[pd.Timestamp] = set()
    for raw_date in raw_dates:
        target = _normalize_bound(raw_date)
        if target is None:
            continue
        idx = int(days.searchsorted(target, side="left"))
        if idx < len(days):
            out.add(pd.Timestamp(days[idx]).normalize())
    return out


def _build_rebalance_days(
    trade_days: pd.DatetimeIndex,
    frequency: str,
    custom_dates: tuple[object, ...] = (),
) -> set[pd.Timestamp]:
    if custom_dates:
        return _build_custom_rebalance_days(trade_days, custom_dates)
    value = str(frequency or "month_start").strip().lower()
    if value in {"month_start", "monthly", "month"}:
        return _build_period_start_days(trade_days, "M")
    if value in {"quarter_start", "quarterly", "quarter", "q"}:
        return _build_period_start_days(trade_days, "Q-DEC")
    if value in {"year_start", "yearly", "annual", "year", "y"}:
        return _build_period_start_days(trade_days, "Y-DEC")
    if value in {"daily", "day", "every_day"}:
        return {pd.Timestamp(day).normalize() for day in trade_days}
    if value in {"custom", "fixed_dates", "dates"}:
        return set()
    raise DataValidationError(f"unsupported rebalance_frequency: {frequency!r}")


def _price_map_for_date(
    quotes: QuoteStore,
    *,
    codes: tuple[str, ...],
    date: pd.Timestamp,
    column: str,
) -> dict[str, float]:
    if column not in quotes.columns:
        return {}
    values = quotes.column(column)
    out: dict[str, float] = {}
    for code in codes:
        row = quotes.position(code, date)
        if row < 0:
            continue
        try:
            price = float(values[row])
        except (TypeError, ValueError):
            continue
        if np.isfinite(price) and price > 0:
            out[code] = price
    return out


def _quote_value(quotes: QuoteStore, column: str, row: int) -> float | None:
    if column not in quotes.columns:
        return None
    try:
        value = float(quotes.column(column)[row])
    except (TypeError, ValueError, OverflowError):
        return None
    return value if np.isfinite(value) else None


def _positive_quote_value(quotes: QuoteStore, column: str, row: int) -> float | None:
    value = _quote_value(quotes, column, row)
    return value if value is not None and value > 0 else None


def _quote_bar_for_date(
    quotes: QuoteStore,
    *,
    code: str,
    date: pd.Timestamp,
) -> dict[str, float | str | None] | None:
    row = quotes.position(code, date)
    if row < 0:
        return None
    stock_slice = quotes.stock_slice(code)
    previous_close = _positive_quote_value(quotes, "pre_close", row)
    if previous_close is None and row > int(stock_slice.start or 0):
        previous_close = _positive_quote_value(quotes, "close", row - 1)
    stock_name = None
    if "stock_name" in quotes.columns:
        raw_name = quotes.column("stock_name")[row]
        stock_name = str(raw_name).strip() if raw_name is not None else None
    return {
        "open": _quote_value(quotes, "open", row),
        "high": _quote_value(quotes, "high", row),
        "low": _quote_value(quotes, "low", row),
        "close": _quote_value(quotes, "close", row),
        "volume": _quote_value(quotes, "volume", row),
        "previous_close": previous_close,
        "stock_name": stock_name,
    }


def _trade_rejection(
    *,
    bar: dict[str, float | str | None] | None,
    code: str,
    side: str,
    config: StaticAllocationConfig,
) -> str | None:
    constraints = config.trade_constraints
    if constraints is None:
        return None
    if bar is None:
        return "missing_quote"
    if bool(getattr(constraints, "enable_no_trade_day", False)) and is_no_trade_day(
        open_price=bar["open"],
        high_price=bar["high"],
        low_price=bar["low"],
        close_price=bar["close"],
        volume=bar["volume"],
    ):
        return "no_trade_day"
    if bool(getattr(constraints, "enable_limit", False)) and is_limit_locked(
        open_price=bar["open"],
        high_price=bar["high"],
        low_price=bar["low"],
        close_price=bar["close"],
        prev_close=bar["previous_close"],
        limit_up_pct=float(getattr(constraints, "limit_up_pct", 0.098)),
        limit_down_pct=float(getattr(constraints, "limit_down_pct", 0.098)),
        side=side,
        stock_code=code,
        stock_name=bar["stock_name"],
    ):
        return "limit_locked"
    return None


def _constrained_qty(
    *,
    qty: int,
    bar: dict[str, float | str | None] | None,
    code: str,
    side: str,
    position_qty: int | None,
    config: StaticAllocationConfig,
) -> int:
    constraints = config.trade_constraints
    if qty <= 0 or constraints is None or not bool(getattr(constraints, "enable_volume_limit", False)):
        return max(int(qty), 0)
    if bar is None:
        return 0
    max_qty = max_trade_qty(
        volume=bar["volume"],
        participation_rate=float(getattr(constraints, "max_participation_rate", 0.0)),
        lot_size=int(config.lot_size),
        stock_code=code,
        side=side,
        position_qty=position_qty,
    )
    return min(max(int(qty), 0), max_qty)


def _position_value(broker: SimpleBroker, code: str, price: float | None) -> float:
    position = broker.positions.get(code)
    if position is None or int(position.qty) <= 0:
        return 0.0
    value_price = position.last_price if price is None else price
    try:
        value_price = float(value_price)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(value_price) or value_price <= 0:
        return 0.0
    return float(position.qty) * value_price


def _normalize_execution_price_column(value: object, *, field_name: str) -> str:
    column = str(value or "").strip().lower()
    if column not in _EXECUTION_PHASE_BY_PRICE_COLUMN:
        raise DataValidationError(
            f"{field_name} must be open or close; high/low cannot define a causal execution phase."
        )
    return column


def _planned_rebalance_delta(
    *,
    code: str,
    current_qty: int,
    target_value: float,
    sizing_price: float | None,
    lot_size: int,
) -> tuple[str, int, float] | None:
    if sizing_price is None or sizing_price <= 0:
        return None
    try:
        current_value = float(current_qty) * float(sizing_price)
    except (OverflowError, ValueError):
        return None
    excess_value = current_value - float(target_value)
    if excess_value > sizing_price:
        try:
            qty = int(excess_value // sizing_price)
        except (OverflowError, ValueError):
            return None
        return ("sell", qty, excess_value) if qty > 0 else None

    gap_value = float(target_value) - current_value
    if gap_value > sizing_price:
        try:
            raw_qty = int(gap_value // sizing_price)
        except (OverflowError, ValueError):
            return None
        qty = normalize_buy_qty(code, raw_qty, lot_size=lot_size)
        return ("buy", qty, gap_value) if qty > 0 else None
    return None


def _run_rebalance(
    broker: SimpleBroker,
    *,
    date: pd.Timestamp,
    quotes: QuoteStore,
    output_codes: tuple[str, ...],
    target_weights: Mapping[str, float],
    config: StaticAllocationConfig,
    buy_price_col: str,
    sell_price_col: str,
) -> None:
    sell_prices = _price_map_for_date(
        quotes,
        codes=output_codes,
        date=date,
        column=sell_price_col,
    )
    buy_prices = _price_map_for_date(
        quotes,
        codes=output_codes,
        date=date,
        column=buy_price_col,
    )

    # Target shares are frozen at the opening phase.  high/low/close are
    # end-of-day reference fills, so their realized prices cannot feed back
    # into same-day order quantities.
    sizing_prices = _price_map_for_date(
        quotes,
        codes=output_codes,
        date=date,
        column=_REBALANCE_SIZING_PRICE_COLUMN,
    )
    equity_before = broker.mark_to_market(
        sizing_prices,
        trade_date=str(date.date()),
        log_missing=True,
    )
    if equity_before <= 0:
        return
    targets = {
        code: float(equity_before) * weight
        for code, weight in target_weights.items()
    }

    sell_plans: list[tuple[str, int]] = []
    buy_plans: list[tuple[str, int, float]] = []
    plan_codes = sorted(set(broker.positions) | set(target_weights), key=str.casefold)
    for code in plan_codes:
        current_qty = broker.position_qty(code)
        if code not in target_weights:
            if current_qty > 0:
                sell_plans.append((code, current_qty))
            continue
        planned_delta = _planned_rebalance_delta(
            code=code,
            current_qty=current_qty,
            target_value=targets[code],
            sizing_price=sizing_prices.get(code),
            lot_size=int(config.lot_size),
        )
        if planned_delta is None:
            continue
        side, qty, value_delta = planned_delta
        if side == "sell":
            sell_plans.append((code, qty))
        else:
            buy_plans.append((code, qty, value_delta))

    def _execute_sells() -> None:
        for code, planned_qty in sell_plans:
            price = sell_prices.get(code)
            if price is None or price <= 0:
                continue
            bar = _quote_bar_for_date(quotes, code=code, date=date)
            if _trade_rejection(bar=bar, code=code, side="sell", config=config) is not None:
                continue
            qty = _constrained_qty(
                qty=planned_qty,
                bar=bar,
                code=code,
                side="sell",
                position_qty=broker.position_qty(code),
                config=config,
            )
            if qty > 0:
                broker.sell(
                    trade_date=str(date.date()),
                    stock_code=code,
                    price=price,
                    qty=qty,
                    note="rebalance",
                )

    def _execute_buys() -> None:
        for code, planned_qty, _gap_value in sorted(
            buy_plans,
            key=lambda item: (-item[2], item[0].casefold()),
        ):
            price = buy_prices.get(code)
            if price is None or price <= 0:
                continue
            bar = _quote_bar_for_date(quotes, code=code, date=date)
            if _trade_rejection(bar=bar, code=code, side="buy", config=config) is not None:
                continue
            available_cash = float(broker.cash) * (1.0 - float(config.cash_buffer_pct))
            if available_cash <= 0:
                break
            cash_limited_qty = normalize_buy_qty(
                code,
                int(available_cash // price),
                lot_size=int(config.lot_size),
            )
            qty = min(planned_qty, cash_limited_qty)
            qty = _constrained_qty(
                qty=qty,
                bar=bar,
                code=code,
                side="buy",
                position_qty=None,
                config=config,
            )
            if qty > 0:
                broker.buy(
                    trade_date=str(date.date()),
                    stock_code=code,
                    price=price,
                    qty=qty,
                )

    if _EXECUTION_PHASE_BY_PRICE_COLUMN[sell_price_col] <= _EXECUTION_PHASE_BY_PRICE_COLUMN[buy_price_col]:
        _execute_sells()
        _execute_buys()
    else:
        _execute_buys()
        _execute_sells()


def _build_daily_row(
    broker: SimpleBroker,
    *,
    date: pd.Timestamp,
    quotes: QuoteStore,
    output_codes: tuple[str, ...],
    mark_price_col: str,
    initial_capital: float,
    rebalance: bool,
) -> dict[str, object]:
    close_map = _price_map_for_date(
        quotes,
        codes=output_codes,
        date=date,
        column=mark_price_col,
    )
    equity = broker.mark_to_market(close_map, trade_date=str(date.date()), log_missing=True)
    position_value = max(float(equity) - float(broker.cash), 0.0)
    row: dict[str, object] = {
        COL_TRADE_DATE: pd.Timestamp(date).normalize(),
        "equity": float(equity),
        "cash": float(broker.cash),
        "position_value": float(position_value),
        "utilization_rate": float(position_value / equity) if equity else 0.0,
        "net_value": float(equity / initial_capital) if initial_capital else np.nan,
        "sum_net_value": float(equity / initial_capital) if initial_capital else np.nan,
        "rebalance": bool(rebalance),
    }
    for code in output_codes:
        value = _position_value(broker, code, close_map.get(code))
        row[f"weight_{code}"] = float(value / equity) if equity else 0.0
        row[f"qty_{code}"] = int(broker.position_qty(code))
    return row


def _positions_frame(broker: SimpleBroker) -> pd.DataFrame:
    rows = [
        {
            "stock_code": code,
            "qty": int(position.qty),
            "avg_cost": float(position.avg_cost),
            "last_price": float(position.last_price),
            "last_date": position.last_date,
        }
        for code, position in sorted(
            broker.positions.items(), key=lambda item: item[0].casefold()
        )
        if int(position.qty) > 0
    ]
    return pd.DataFrame(
        rows,
        columns=["stock_code", "qty", "avg_cost", "last_price", "last_date"],
    )


def _orders_frame(broker: SimpleBroker) -> pd.DataFrame:
    return (
        pd.DataFrame([order.__dict__ for order in broker.orders])
        if broker.orders
        else pd.DataFrame()
    )


def _fills_frame(broker: SimpleBroker) -> pd.DataFrame:
    return (
        pd.DataFrame([fill.__dict__ for fill in broker.fills])
        if broker.fills
        else pd.DataFrame()
    )


def run_static_allocation_backtest(
    quotes: QuoteStore,
    config: StaticAllocationConfig,
) -> StaticAllocationResult:
    if config.initial_capital <= 0:
        raise DataValidationError("initial_capital must be positive.")
    if config.lot_size < 1:
        raise DataValidationError("lot_size must be at least 1.")
    if not (0.0 <= float(config.cash_buffer_pct) < 1.0):
        raise DataValidationError("cash_buffer_pct must be in [0, 1).")
    buy_price_col = _normalize_execution_price_column(
        config.buy_price_col,
        field_name="buy_price_col",
    )
    sell_price_col = _normalize_execution_price_column(
        config.sell_price_col,
        field_name="sell_price_col",
    )

    target_weights = normalize_target_weights(
        config.target_weights,
        normalize=bool(config.normalize_weights),
    )
    target_weight_schedule = normalize_target_weight_schedule(
        config.target_weight_schedule,
        normalize=bool(config.normalize_weights),
    )
    output_codes = _collect_target_codes(target_weights, target_weight_schedule)
    start_date = _normalize_bound(config.start_date)
    end_date = _normalize_bound(config.end_date)
    _validate_quote_store(
        quotes,
        output_codes=output_codes,
        start_date=start_date,
        end_date=end_date,
    )
    trade_days = validate_explicit_trade_days(
        config.trade_days,
        start_date=start_date,
        end_date=end_date,
        name="StaticAllocationConfig.trade_days",
    )
    rebalance_days = _build_rebalance_days(
        trade_days,
        config.rebalance_frequency,
        tuple(config.rebalance_dates or ()),
    )
    broker = SimpleBroker(
        cfg=BrokerConfig(
            initial_cash=float(config.initial_capital),
            lot_size=int(config.lot_size),
            allow_partial_fill=True,
            cash_buffer_pct=float(config.cash_buffer_pct),
        ),
        cost_cfg=config.cost,
    )

    rows: list[dict[str, object]] = []
    for day in trade_days:
        date = pd.Timestamp(day).normalize()
        active_weights = _target_weights_for_date(
            target_weights,
            target_weight_schedule,
            date,
        )
        rebalance = date in rebalance_days
        if rebalance:
            _run_rebalance(
                broker,
                date=date,
                quotes=quotes,
                output_codes=output_codes,
                target_weights=active_weights,
                config=config,
                buy_price_col=buy_price_col,
                sell_price_col=sell_price_col,
            )
        rows.append(
            _build_daily_row(
                broker,
                date=date,
                quotes=quotes,
                output_codes=output_codes,
                mark_price_col=config.mark_price_col,
                initial_capital=float(config.initial_capital),
                rebalance=rebalance,
            )
        )

    merged_df = pd.DataFrame(rows)
    if not merged_df.empty:
        merged_df = merged_df.sort_values(
            COL_TRADE_DATE,
            kind="mergesort",
        ).reset_index(drop=True)
        daily_return = pd.to_numeric(merged_df["net_value"], errors="coerce").pct_change()
        if len(daily_return) > 0:
            daily_return.iloc[0] = float(merged_df["net_value"].iloc[0]) - 1.0
        merged_df["daily_return"] = daily_return.replace(
            [np.inf, -np.inf],
            np.nan,
        ).fillna(0.0)

    return StaticAllocationResult(
        merged_df=merged_df,
        orders_df=_orders_frame(broker),
        fills_df=_fills_frame(broker),
        positions_df=_positions_frame(broker),
        target_weights=dict(target_weights),
    )


__all__ = [
    "StaticAllocationConfig",
    "StaticAllocationCostConfig",
    "StaticAllocationResult",
    "normalize_target_weights",
    "normalize_target_weight_schedule",
    "run_static_allocation_backtest",
]
