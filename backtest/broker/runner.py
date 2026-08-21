"""
Broker portfolio run loop.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import logging
from typing import Any

import numpy as np
import pandas as pd

from core.constants import (
    COL_TRADE_DATE,
    DEFAULT_LOT_SIZE,
)
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.logging import close_run_logger_safely, get_run_logger, log_warn

from ..constraints import is_limit_locked, is_no_trade_day, max_trade_qty
from ..force_close import resolve_force_close_mode
from ..panel_native import QuoteStore, SelectionBatch
from ..risk_control import _resolve_effective_position_ratio, apply_risk_fsm
from .engine import BrokerConfig, SimpleBroker, _finite_positive_price
from .plan import (
    EntryRetryIntent,
    TradePlan,
    build_entry_retry_intents,
    build_trade_plan,
    ordered_selection_rows,
)
from .portfolio_allocator import allocate_code_weights
from .risk_fills import build_synthetic_risk_fill_result as _build_synthetic_risk_fill_result
from .risk_overlay import daily_return_from_net_value as _daily_return_from_net_value
from .risk_overlay import risk_execution_flags as _risk_execution_flags
from .result import BrokerPortfolioResult
from .snapshots import (
    build_current_quote_validity as _build_current_quote_validity,
    build_execution_state as _build_execution_state,
    snapshot_positions as _snapshot_positions,
)
from trading.lot_rules import normalize_buy_qty

logger = logging.getLogger(__name__)


def _require_fraction(value: object, *, name: str) -> float:
    try:
        pct = float(value)
    except Exception as exc:
        raise DataValidationError(f"{name} 非法，必须是 (0, 1] 区间内的数值。") from exc
    if not (0.0 < pct <= 1.0):
        raise DataValidationError(f"{name}={pct} 非法，必须在 (0, 1] 区间内。")
    return pct


def _build_price_meta(quotes: QuoteStore, code: str) -> dict[str, Any]:
    stock_slice = quotes.stock_slice(code)
    dates = quotes.index.trade_dates[stock_slice]
    meta: dict[str, Any] = {
        "dates": dates,
        "open": np.asarray(quotes.column("open")[stock_slice], dtype=np.float64),
        "high": np.asarray(quotes.column("high")[stock_slice], dtype=np.float64),
        "low": np.asarray(quotes.column("low")[stock_slice], dtype=np.float64),
        "close": np.asarray(quotes.column("close")[stock_slice], dtype=np.float64),
        "pre_close": None,
        "volume": None,
        "stock_name": None,
    }
    if "pre_close" in quotes.columns:
        meta["pre_close"] = np.asarray(quotes.column("pre_close")[stock_slice], dtype=np.float64)
    if "volume" in quotes.columns:
        meta["volume"] = np.asarray(quotes.column("volume")[stock_slice], dtype=np.float64)
    if "stock_name" in quotes.columns:
        meta["stock_name"] = quotes.column("stock_name")[stock_slice]
    return meta


def _find_date_idx(meta: dict[str, Any], date_np: np.datetime64) -> int:
    dates = meta["dates"]
    idx = int(np.searchsorted(dates, date_np, side="left"))
    if idx >= len(dates) or dates[idx] != date_np:
        return -1
    return idx


def _safe_value(arr: np.ndarray | None, idx: int) -> float | None:
    if arr is None or idx < 0 or idx >= len(arr):
        return None
    val = arr[idx]
    try:
        f = float(val)
    except Exception:
        return None
    if not np.isfinite(f):
        return None
    return f


def _safe_price(arr: np.ndarray | None, idx: int) -> float | None:
    return _finite_positive_price(_safe_value(arr, idx))


def _get_exec_price(meta: dict[str, Any], idx: int, price_type: str) -> float | None:
    field = "open" if str(price_type).strip().lower() == "open" else "close"
    return _safe_price(meta.get(field), idx)


def _get_raw_exec_price(meta: dict[str, Any], idx: int, price_type: str) -> float | None:
    field = "open" if str(price_type).strip().lower() == "open" else "close"
    return _safe_value(meta.get(field), idx)


def _get_prev_close(meta: dict[str, Any], idx: int) -> float | None:
    supplied = _safe_price(meta.get("pre_close"), idx)
    if supplied is not None:
        return supplied
    if idx <= 0:
        return None
    return _safe_price(meta.get("close"), idx - 1)


def _get_price_row(meta: dict[str, Any], idx: int) -> dict[str, Any] | None:
    if idx < 0:
        return None
    return {
        "open": _safe_price(meta.get("open"), idx),
        "high": _safe_price(meta.get("high"), idx),
        "low": _safe_price(meta.get("low"), idx),
        "close": _safe_price(meta.get("close"), idx),
        "volume": _safe_value(meta.get("volume"), idx),
        "stock_name": (str(meta["stock_name"][idx]).strip() if meta.get("stock_name") is not None and 0 <= idx < len(meta["stock_name"]) else None),
    }


def _record_skipped_execution(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    stock_code: str,
    side: str,
    qty: int,
    price: float | None,
    reason: str,
) -> None:
    run.broker.record_skipped_order(
        trade_date=str(date.date()),
        stock_code=stock_code,
        side=side,
        qty=qty,
        price=price,
        reason=reason,
    )


def _build_price_map_for_codes(
    price_meta: dict[str, dict[str, Any]],
    *,
    codes: list[str] | tuple[str, ...],
    date_np: np.datetime64,
    field: str,
) -> dict[str, float]:
    out_map: dict[str, float] = {}
    for code in codes:
        meta = price_meta.get(code)
        if meta is None:
            continue
        idx = _find_date_idx(meta, date_np)
        if idx < 0:
            continue
        val = _safe_price(meta.get(field), idx)
        if val is not None:
            out_map[code] = val
    return out_map


@dataclass(slots=True)
class _BrokerExecution:
    cfg: Any
    spec: Any
    execution_policy: Any
    risk_cfg: Any
    logger: Any
    quote_store: QuoteStore
    trade_days: list[pd.Timestamp]
    trade_days_np: np.ndarray
    last_trade_date: pd.Timestamp
    entries_by_date: dict[pd.Timestamp, list[TradePlan]]
    retry_entries_by_date: dict[pd.Timestamp, list[EntryRetryIntent]]
    exits_by_date: dict[pd.Timestamp, list[TradePlan]]
    intraday_exits_by_date: dict[pd.Timestamp, list[TradePlan]]
    allow_intraday: bool
    lot_size: int
    broker: SimpleBroker
    score_col: str | None
    score_values: np.ndarray | None
    price_meta: dict[str, dict[str, Any]]
    constraints: Any
    force_close_mode: str
    max_gross_exposure: float
    max_single_position_pct: float
    max_positions: int
    pending_exits: dict[str, str] = field(default_factory=dict)
    filled_entry_cycles: set[int] = field(default_factory=set)
    daily_rows: list[dict[str, Any]] = field(default_factory=list)
    daily_position_snapshots: list[dict[str, Any]] = field(default_factory=list)


def _empty_broker_result(trade_days: list[pd.Timestamp], *, initial_capital: float) -> BrokerPortfolioResult:
    df_out = pd.DataFrame(
        {
            COL_TRADE_DATE: trade_days,
            "equity": [float(initial_capital)] * len(trade_days),
            "cash": [float(initial_capital)] * len(trade_days),
            "position_value": [0.0] * len(trade_days),
            "utilization_rate": [0.0] * len(trade_days),
            "net_value": [1.0] * len(trade_days),
            "daily_return": [0.0] * len(trade_days),
            "sum_net_value": [1.0] * len(trade_days),
        }
    )
    return BrokerPortfolioResult(
        merged_df=df_out,
        orders_df=pd.DataFrame(),
        fills_df=pd.DataFrame(),
        risk_fills_df=pd.DataFrame(),
        daily_position_snapshots=(),
        strategy_rejections_df=pd.DataFrame(),
    )


def _log_signal_preparation(
    *,
    signal_rows: int,
    trade_days: int,
    dropped_selected: int,
    dropped_candidate: int,
    logger: Any,
    last_trade_date: pd.Timestamp,
) -> None:
    if logger:
        logger.info(
            "broker signals loaded rows=%d trade_days=%d",
            signal_rows,
            trade_days,
        )
    if dropped_selected:
        log_warn(
            "broker 窗口末日信号无法 next_open 执行："
            f"已过滤 {dropped_selected} 条 "
            f"trade_date={last_trade_date.strftime('%Y-%m-%d')}。",
            logger,
        )
        if logger:
            logger.info(
                "broker_end_day_filtered trade_date=%s dropped=%d",
                last_trade_date.strftime("%Y-%m-%d"),
                dropped_selected,
            )
    if dropped_candidate:
        log_warn(
            "broker 窗口末日候补信号无法 next_open 执行："
            f"已过滤 {dropped_candidate} 条 "
            f"trade_date={last_trade_date.strftime('%Y-%m-%d')}。",
            logger,
        )


def _group_trade_plans(
    plans: list[TradePlan],
    *,
    allow_intraday: bool,
) -> tuple[
    dict[pd.Timestamp, list[TradePlan]],
    dict[pd.Timestamp, list[TradePlan]],
    dict[pd.Timestamp, list[TradePlan]],
]:
    entries_by_date: dict[pd.Timestamp, list[TradePlan]] = defaultdict(list)
    exits_by_date: dict[pd.Timestamp, list[TradePlan]] = defaultdict(list)
    intraday_exits_by_date: dict[pd.Timestamp, list[TradePlan]] = defaultdict(list)
    for plan in plans:
        entries_by_date[plan.entry_date].append(plan)
        if plan.exit_date is None:
            continue
        if allow_intraday and plan.exit_date == plan.entry_date:
            intraday_exits_by_date[plan.exit_date].append(plan)
        else:
            exits_by_date[plan.exit_date].append(plan)
    return entries_by_date, exits_by_date, intraday_exits_by_date


def _group_retry_entry_intents(
    intents: list[EntryRetryIntent],
) -> dict[pd.Timestamp, list[EntryRetryIntent]]:
    by_date: dict[pd.Timestamp, list[EntryRetryIntent]] = defaultdict(list)
    for intent in intents:
        by_date[intent.entry_date].append(intent)
    return by_date


def _resolve_max_positions(cfg: Any, spec: Any) -> int:
    configured = int(cfg.portfolio.max_positions) if cfg.portfolio.max_positions is not None else 0
    if configured <= 0:
        configured = int(spec.portfolio_num_j) * int(spec.portfolio_num_i)
    return max(configured, 0)


def _prepare_broker_execution(
    selection: SelectionBatch,
    quote_store: QuoteStore,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp],
    *,
    cfg: Any,
    spec: Any,
    execution_policy: Any,
    risk_cfg: Any,
    logger: Any,
) -> _BrokerExecution | BrokerPortfolioResult:
    buy_timing = str(
        getattr(execution_policy, "buy_timing", getattr(cfg.run, "exec_buy_timing", ""))
    ).strip().lower()
    signal_rows, trade_days_idx, dropped_selected, dropped_candidate = ordered_selection_rows(
        selection,
        quote_store,
        trade_days=trade_days,
        buy_timing=buy_timing,
    )
    trade_days_list = trade_days_idx.to_list()
    last_trade_date = trade_days_list[-1]
    _log_signal_preparation(
        signal_rows=len(signal_rows),
        trade_days=len(trade_days_list),
        dropped_selected=dropped_selected,
        dropped_candidate=dropped_candidate,
        logger=logger,
        last_trade_date=last_trade_date,
    )
    plans = build_trade_plan(
        selection,
        quote_store,
        trade_days=trade_days_list,
        execution_policy=execution_policy,
        cfg=cfg,
    )
    retry_entry_intents = build_entry_retry_intents(
        selection,
        quote_store,
        trade_days=trade_days_list,
        execution_policy=execution_policy,
        cfg=cfg,
    )
    if not plans:
        log_warn("broker 模式下无可执行交易计划，返回空仓净值曲线。", logger)
        return _empty_broker_result(
            trade_days_list,
            initial_capital=float(cfg.portfolio.initial_capital),
        )

    sell_timing = str(getattr(execution_policy, "sell_timing", "")).strip().lower()
    allow_intraday = bool(getattr(execution_policy, "allow_intraday", False))
    if sell_timing != "signal_close":
        allow_intraday = False
    entries_by_date, exits_by_date, intraday_exits_by_date = _group_trade_plans(
        plans,
        allow_intraday=allow_intraday,
    )
    retry_entries_by_date = _group_retry_entry_intents(retry_entry_intents)
    lot_size = int(cfg.portfolio.lot_size or DEFAULT_LOT_SIZE)
    broker = SimpleBroker(
        cfg=BrokerConfig(
            initial_cash=float(cfg.portfolio.initial_capital),
            lot_size=lot_size,
            allow_partial_fill=bool(cfg.portfolio.allow_partial_fill),
            cash_buffer_pct=float(cfg.portfolio.cash_buffer_pct),
        ),
        cost_cfg=cfg.transaction_cost,
    )
    score_col = spec.sort_spec[0][0] if spec.sort_spec else None
    score_values = None
    if score_col and score_col in quote_store.columns:
        score_values = np.asarray(quote_store.column(score_col))
    price_meta: dict[str, dict[str, Any]] = {}
    for code in sorted({str(plan.stock_code).strip() for plan in plans}, key=str.casefold):
        price_meta[code] = _build_price_meta(quote_store, code)
    return _BrokerExecution(
        cfg=cfg,
        spec=spec,
        execution_policy=execution_policy,
        risk_cfg=risk_cfg,
        logger=logger,
        quote_store=quote_store,
        trade_days=trade_days_list,
        trade_days_np=np.array(trade_days_list, dtype="datetime64[ns]"),
        last_trade_date=last_trade_date,
        entries_by_date=entries_by_date,
        retry_entries_by_date=retry_entries_by_date,
        exits_by_date=exits_by_date,
        intraday_exits_by_date=intraday_exits_by_date,
        allow_intraday=allow_intraday,
        lot_size=lot_size,
        broker=broker,
        score_col=score_col,
        score_values=score_values,
        price_meta=price_meta,
        constraints=cfg.trade_constraints,
        force_close_mode=resolve_force_close_mode(cfg, default="force"),
        max_gross_exposure=_require_fraction(
            getattr(cfg.portfolio, "max_gross_exposure", 1.0),
            name="portfolio.max_gross_exposure",
        ),
        max_single_position_pct=_require_fraction(
            getattr(cfg.portfolio, "max_single_position_pct", 1.0),
            name="portfolio.max_single_position_pct",
        ),
        max_positions=_resolve_max_positions(cfg, spec),
    )


def _try_exit(
    run: _BrokerExecution,
    *,
    code: str,
    price_type: str,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    broker = run.broker
    requested_qty = broker.position_qty(code)
    if requested_qty <= 0:
        return
    meta = run.price_meta.get(code)
    if meta is None:
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=None,
            reason="missing_price",
        )
        run.pending_exits[code] = "open"
        return
    idx = _find_date_idx(meta, date_np)
    row = _get_price_row(meta, idx)
    price = _get_exec_price(meta, idx, price_type)
    raw_price = _get_raw_exec_price(meta, idx, price_type)
    if row is None:
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=raw_price,
            reason="missing_price",
        )
        run.pending_exits[code] = "open"
        return
    if price is None:
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=raw_price,
            reason="invalid_price",
        )
        run.pending_exits[code] = "open"
        return
    constraints = run.constraints
    if constraints.enable_no_trade_day and is_no_trade_day(
        open_price=row.get("open"),
        high_price=row.get("high"),
        low_price=row.get("low"),
        close_price=row.get("close"),
        volume=row.get("volume") if "volume" in row else None,
    ):
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=price,
            reason="no_trade_day",
        )
        run.pending_exits[code] = "open"
        return
    if constraints.enable_limit and is_limit_locked(
        open_price=row.get("open"),
        high_price=row.get("high"),
        low_price=row.get("low"),
        close_price=row.get("close"),
        prev_close=_get_prev_close(meta, idx),
        limit_up_pct=constraints.limit_up_pct,
        limit_down_pct=constraints.limit_down_pct,
        side="sell",
        stock_code=code,
        stock_name=row.get("stock_name"),
    ):
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=price,
            reason="limit_locked",
        )
        run.pending_exits[code] = "open"
        return
    qty = requested_qty
    if constraints.enable_volume_limit and row.get("volume") is not None:
        max_qty = max_trade_qty(
            volume=row.get("volume"),
            participation_rate=constraints.max_participation_rate,
            lot_size=run.lot_size,
            stock_code=code,
            side="sell",
            position_qty=qty,
        )
        qty = min(qty, max_qty) if max_qty > 0 else 0
    if qty <= 0:
        _record_skipped_execution(
            run,
            date=date,
            stock_code=code,
            side="sell",
            qty=requested_qty,
            price=price,
            reason="volume_limit" if constraints.enable_volume_limit else "qty<=0",
        )
        run.pending_exits[code] = "open"
        return
    broker.sell(trade_date=str(date.date()), stock_code=code, price=price, qty=qty)
    if broker.position_qty(code) > 0:
        run.pending_exits[code] = "open"


def _execute_exit_plans(
    run: _BrokerExecution,
    plans: list[TradePlan],
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    exit_map: dict[str, str] = {}
    for plan in plans:
        if plan.stock_code not in exit_map:
            exit_map[plan.stock_code] = plan.exit_price_type
    for code, price_type in exit_map.items():
        _try_exit(run, code=code, price_type=price_type, date=date, date_np=date_np)


def _execution_phase(price_type: str) -> str:
    return "open" if str(price_type).strip().lower() == "open" else "close"


def _plans_for_phase(
    plans: list[TradePlan | EntryRetryIntent],
    *,
    price_attr: str,
    phase: str,
) -> list[TradePlan | EntryRetryIntent]:
    return [
        plan
        for plan in plans
        if _execution_phase(str(getattr(plan, price_attr))) == phase
    ]


def _entry_plans_for_phase(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    phase: str,
) -> list[TradePlan | EntryRetryIntent]:
    plans: list[TradePlan | EntryRetryIntent] = [
        *run.entries_by_date.get(date, []),
        *run.retry_entries_by_date.get(date, []),
    ]
    plans.sort(key=lambda item: (int(item.priority), item.stock_code))
    return _plans_for_phase(plans, price_attr="entry_price_type", phase=phase)


def _process_pending_open_exits(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> set[str]:
    pending_codes = tuple(sorted(run.pending_exits, key=str.casefold))
    run.pending_exits.clear()
    for code in pending_codes:
        _try_exit(run, code=code, price_type="open", date=date, date_np=date_np)
    return set(pending_codes)


def _process_open_exits(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    pending_codes = _process_pending_open_exits(run, date=date, date_np=date_np)
    plans = _plans_for_phase(
        run.exits_by_date.get(date, []),
        price_attr="exit_price_type",
        phase="open",
    )
    if pending_codes:
        plans = [plan for plan in plans if plan.stock_code not in pending_codes]
    if plans:
        _execute_exit_plans(run, plans, date=date, date_np=date_np)


def _process_close_exits(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    plans = _plans_for_phase(
        run.exits_by_date.get(date, []),
        price_attr="exit_price_type",
        phase="close",
    )
    if plans:
        _execute_exit_plans(run, plans, date=date, date_np=date_np)


def _build_entry_scores(
    run: _BrokerExecution,
    entries: list[TradePlan | EntryRetryIntent],
) -> np.ndarray | None:
    if not run.score_col or run.score_values is None:
        return None
    rows = np.asarray([plan.signal_row for plan in entries], dtype=np.int64)
    if len(rows) and (rows.min() < 0 or rows.max() >= len(run.score_values)):
        return None
    converted = pd.to_numeric(pd.Series(run.score_values[rows]), errors="coerce")
    if converted.isna().any():
        return None
    return converted.to_numpy(dtype=np.float64, copy=False)


def _entry_price_context(
    run: _BrokerExecution,
    plan: TradePlan | EntryRetryIntent,
    *,
    date_np: np.datetime64,
) -> tuple[dict[str, Any] | None, float | None, str | None]:
    meta = run.price_meta.get(plan.stock_code)
    if meta is None:
        return None, None, "missing_price"
    idx = _find_date_idx(meta, date_np)
    row = _get_price_row(meta, idx)
    price = _get_exec_price(meta, idx, plan.entry_price_type)
    if row is None:
        return None, None, "missing_price"
    if price is None:
        return row, None, "invalid_price"
    constraints = run.constraints
    if constraints.enable_no_trade_day and is_no_trade_day(
        open_price=row.get("open"),
        high_price=row.get("high"),
        low_price=row.get("low"),
        close_price=row.get("close"),
        volume=row.get("volume") if "volume" in row else None,
    ):
        return row, price, "no_trade_day"
    if constraints.enable_limit and is_limit_locked(
        open_price=row.get("open"),
        high_price=row.get("high"),
        low_price=row.get("low"),
        close_price=row.get("close"),
        prev_close=_get_prev_close(meta, idx),
        limit_up_pct=constraints.limit_up_pct,
        limit_down_pct=constraints.limit_down_pct,
        side="buy",
        stock_code=plan.stock_code,
        stock_name=row.get("stock_name"),
    ):
        return row, price, "limit_locked"
    return row, price, None


def _entry_qty(
    run: _BrokerExecution,
    plan: TradePlan | EntryRetryIntent,
    *,
    row: dict[str, Any],
    price: float,
    budget: float,
) -> tuple[int, str | None]:
    valid_price = _finite_positive_price(price)
    try:
        valid_budget = float(budget)
    except (TypeError, ValueError, OverflowError):
        return 0, "qty<=0"
    if valid_price is None or not np.isfinite(valid_budget) or valid_budget <= 0:
        return 0, "qty<=0"
    requested_qty = normalize_buy_qty(
        plan.stock_code,
        int(valid_budget // valid_price),
        lot_size=run.lot_size,
    )
    if requested_qty <= 0:
        return 0, "below_lot_size"
    qty = requested_qty
    if run.constraints.enable_volume_limit and row.get("volume") is not None:
        max_qty = max_trade_qty(
            volume=row.get("volume"),
            participation_rate=run.constraints.max_participation_rate,
            lot_size=run.lot_size,
            stock_code=plan.stock_code,
            side="buy",
        )
        qty = min(qty, max_qty) if max_qty > 0 else 0
    if qty <= 0:
        return 0, "volume_limit" if run.constraints.enable_volume_limit else "qty<=0"
    return qty, None


def _entry_cycle_signal_row(plan: TradePlan | EntryRetryIntent) -> int:
    if isinstance(plan, EntryRetryIntent):
        return int(plan.cycle_signal_row)
    return int(plan.signal_row)


def _entry_is_retry(plan: TradePlan | EntryRetryIntent) -> bool:
    return isinstance(plan, EntryRetryIntent)


def _process_entries(
    run: _BrokerExecution,
    entries: list[TradePlan | EntryRetryIntent],
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
    equity_for_budget: float,
    invested_value: float,
    price_map_for_budget: dict[str, float],
) -> None:
    if not entries:
        return
    broker = run.broker
    scores_today = _build_entry_scores(run, entries)
    positions_left = run.max_positions - len(broker.positions) if run.max_positions > 0 else None
    if positions_left is not None and positions_left <= 0:
        return
    remaining_target = max(equity_for_budget * run.max_gross_exposure - invested_value, 0.0)
    available_cash = max(broker.cash * (1.0 - float(run.cfg.portfolio.cash_buffer_pct)), 0.0)
    remaining_buy_budget = min(available_cash, remaining_target)
    for entry_idx, plan in enumerate(entries):
        cycle_signal_row = _entry_cycle_signal_row(plan)
        if _entry_is_retry(plan) and cycle_signal_row in run.filled_entry_cycles:
            continue
        if broker.position_qty(plan.stock_code) > 0:
            _record_skipped_execution(
                run,
                date=date,
                stock_code=plan.stock_code,
                side="buy",
                qty=0,
                price=None,
                reason="already_holding",
            )
            continue
        positions_left = run.max_positions - len(broker.positions) if run.max_positions > 0 else None
        if (positions_left is not None and positions_left <= 0) or remaining_buy_budget <= 0:
            break
        remaining_entries = entries[entry_idx:]
        slots_for_weights = positions_left if positions_left is not None else len(remaining_entries)
        remaining_scores = scores_today[entry_idx:] if scores_today is not None else None
        weights = allocate_code_weights(
            (item.stock_code for item in remaining_entries),
            method=run.cfg.portfolio.position_size_method,
            scores=remaining_scores,
            max_positions=slots_for_weights if slots_for_weights > 0 else 0,
        )
        current_weight = float(weights[0][1]) if weights else 0.0
        if current_weight <= 0:
            continue
        row, price, skip_reason = _entry_price_context(run, plan, date_np=date_np)
        if skip_reason is not None:
            _record_skipped_execution(
                run,
                date=date,
                stock_code=plan.stock_code,
                side="buy",
                qty=0,
                price=price,
                reason=skip_reason,
            )
            continue
        assert row is not None and price is not None
        budget = remaining_buy_budget * current_weight
        position = broker.positions.get(plan.stock_code)
        existing_value = 0.0
        if position is not None:
            ref_price = (
                _finite_positive_price(price_map_for_budget.get(plan.stock_code))
                or _finite_positive_price(position.last_price)
                or price
            )
            existing_value = float(position.qty) * float(ref_price)
        remaining_allowance = max(
            equity_for_budget * run.max_single_position_pct - existing_value,
            0.0,
        )
        budget = min(budget, remaining_allowance)
        if budget <= 0:
            continue
        qty, skip_reason = _entry_qty(run, plan, row=row, price=price, budget=budget)
        if qty <= 0:
            _record_skipped_execution(
                run,
                date=date,
                stock_code=plan.stock_code,
                side="buy",
                qty=0,
                price=price,
                reason=skip_reason or "qty<=0",
            )
            continue
        cash_before = float(broker.cash)
        fill = broker.buy(
            trade_date=str(date.date()),
            stock_code=plan.stock_code,
            price=price,
            qty=qty,
        )
        if fill is not None:
            run.filled_entry_cycles.add(cycle_signal_row)
            remaining_buy_budget = max(
                remaining_buy_budget - max(cash_before - float(broker.cash), 0.0),
                0.0,
            )


def _process_intraday_exits(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    if not run.allow_intraday:
        return
    plans = run.intraday_exits_by_date.get(date, [])
    if plans:
        _execute_exit_plans(run, plans, date=date, date_np=date_np)


def _process_window_close(
    run: _BrokerExecution,
    *,
    date: pd.Timestamp,
    date_np: np.datetime64,
) -> None:
    broker = run.broker
    if date != run.last_trade_date or not broker.positions:
        return
    if run.force_close_mode == "tradable_only":
        for code in tuple(broker.positions):
            _try_exit(run, code=code, price_type="close", date=date, date_np=date_np)
        return
    if run.force_close_mode != "force":
        return
    final_close_map = _build_price_map_for_codes(
        run.price_meta,
        codes=tuple(broker.positions),
        date_np=date_np,
        field="close",
    )
    for code, position in list(broker.positions.items()):
        if code not in final_close_map and position.last_price is not None:
            final_close_map[code] = position.last_price
            if run.logger:
                run.logger.warning(
                    "force_close_missing_price code=%s date=%s fallback=last_price",
                    code,
                    str(date.date()),
                )
        elif code not in final_close_map and run.logger:
            run.logger.warning(
                "force_close_missing_price code=%s date=%s fallback=none",
                code,
                str(date.date()),
            )
    broker.close_all(
        trade_date=str(date.date()),
        price_map=final_close_map,
        note="forced_window_close",
    )


def _run_broker_days(run: _BrokerExecution) -> None:
    broker = run.broker
    price_meta = run.price_meta
    for day_idx, date in enumerate(run.trade_days):
        date_np = run.trade_days_np[day_idx]
        # Execute each bar in chronological order. In particular, a close fill
        # cannot finance an entry that was supposed to occur at that day's open.
        _process_open_exits(run, date=date, date_np=date_np)
        open_map_for_budget = _build_price_map_for_codes(
            price_meta,
            codes=tuple(broker.positions),
            date_np=date_np,
            field="open",
        )
        open_equity_for_budget = broker.mark_to_market(
            open_map_for_budget,
            trade_date=str(date.date()),
            log_missing=True,
        )
        open_invested_value = open_equity_for_budget - broker.cash
        _process_entries(
            run,
            _entry_plans_for_phase(run, date=date, phase="open"),
            date=date,
            date_np=date_np,
            equity_for_budget=open_equity_for_budget,
            invested_value=open_invested_value,
            price_map_for_budget=open_map_for_budget,
        )

        _process_close_exits(run, date=date, date_np=date_np)
        close_map_for_budget = _build_price_map_for_codes(
            price_meta,
            codes=tuple(broker.positions),
            date_np=date_np,
            field="close",
        )
        close_equity_for_budget = broker.mark_to_market(
            close_map_for_budget,
            trade_date=str(date.date()),
            log_missing=True,
        )
        close_invested_value = close_equity_for_budget - broker.cash
        _process_entries(
            run,
            _entry_plans_for_phase(run, date=date, phase="close"),
            date=date,
            date_np=date_np,
            equity_for_budget=close_equity_for_budget,
            invested_value=close_invested_value,
            price_map_for_budget=close_map_for_budget,
        )
        _process_intraday_exits(run, date=date, date_np=date_np)
        _process_window_close(run, date=date, date_np=date_np)

        close_map_after = _build_price_map_for_codes(
            price_meta,
            codes=tuple(broker.positions),
            date_np=date_np,
            field="close",
        )
        equity = broker.mark_to_market(close_map_after, trade_date=str(date.date()), log_missing=True)
        position_value = equity - broker.cash
        run.daily_rows.append(
            {
                COL_TRADE_DATE: date,
                "equity": equity,
                "cash": broker.cash,
                "position_value": position_value,
                "utilization_rate": position_value / equity if equity else 0.0,
            }
        )
        run.daily_position_snapshots.append(
            {
                "trade_date": str(date.date()),
                "positions": _snapshot_positions(
                    broker,
                    close_map=close_map_after,
                    current_quote_validity=_build_current_quote_validity(
                        price_meta,
                        codes=tuple(broker.positions),
                        date_np=date_np,
                    ),
                    execution_state_by_code=_build_execution_state(
                        price_meta,
                        codes=tuple(broker.positions),
                        date_np=date_np,
                    ),
                ),
            }
        )


def _build_broker_frames(run: _BrokerExecution) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_out = ensure_trade_date_normalized(pd.DataFrame(run.daily_rows), col=COL_TRADE_DATE)
    df_out = df_out.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    df_out["net_value"] = df_out["equity"] / float(run.cfg.portfolio.initial_capital)
    df_out["daily_return"] = _daily_return_from_net_value(df_out["net_value"])
    df_out["sum_net_value"] = df_out["net_value"]
    orders_df = pd.DataFrame([order.__dict__ for order in run.broker.orders]) if run.broker.orders else pd.DataFrame()
    fills_df = pd.DataFrame([fill.__dict__ for fill in run.broker.fills]) if run.broker.fills else pd.DataFrame()
    if not fills_df.empty and "source" not in fills_df.columns:
        fills_df["source"] = "strategy"
    return df_out, orders_df, fills_df


def _apply_risk_overlay(
    run: _BrokerExecution,
    df_out: pd.DataFrame,
    fills_df: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    tuple[dict[str, Any], ...],
]:
    risk_cfg = run.risk_cfg
    if risk_cfg is None or not bool(getattr(risk_cfg, "enabled", False)):
        snapshots = tuple(dict(item) for item in run.daily_position_snapshots)
        return df_out, fills_df, pd.DataFrame(), pd.DataFrame(), snapshots
    position, buy_exec, sell_exec = _risk_execution_flags(df_out, fills_df)
    raw_position_ratio = (
        pd.to_numeric(df_out["utilization_rate"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype="float64", copy=False)
    )
    daily_returns = pd.to_numeric(df_out["daily_return"], errors="coerce").fillna(0.0).to_numpy(
        dtype="float64",
        copy=False,
    )
    risk_cols = apply_risk_fsm(
        daily_returns,
        buy_exec,
        sell_exec,
        position,
        enable_nv=bool(getattr(risk_cfg, "enable_nv", False)),
        enable_dd=bool(getattr(risk_cfg, "enable_dd", False)),
        warn_dd=float(getattr(risk_cfg, "warn_dd", -0.10)),
        danger_dd=float(getattr(risk_cfg, "danger_dd", -0.20)),
        lock_dd=float(getattr(risk_cfg, "lock_dd", -0.30)),
        nv_warn=float(getattr(risk_cfg, "nv_warn", 0.9)),
        nv_danger=float(getattr(risk_cfg, "nv_danger", 0.8)),
        nv_clear=float(getattr(risk_cfg, "nv_clear", 0.5)),
        nv_ratios=tuple(float(value) for value in getattr(risk_cfg, "nv_ratios", (1.0, 0.8, 0.5, 0.3))),
        dd_ratios=tuple(float(value) for value in getattr(risk_cfg, "dd_ratios", (1.0, 0.8, 0.5, 0.3))),
        daily_return_scope="portfolio",
        portfolio_position_ratio=raw_position_ratio,
    )
    for key, value in risk_cols.items():
        df_out[key] = value
    raw_columns = {
        "net_value_raw": "net_value",
        "equity_raw": "equity",
        "cash_raw": "cash",
        "position_value_raw": "position_value",
    }
    for raw_name, source_name in raw_columns.items():
        if raw_name not in df_out.columns:
            df_out[raw_name] = df_out[source_name]
    if "utilization_rate_raw" not in df_out.columns:
        df_out["utilization_rate_raw"] = pd.to_numeric(
            df_out["utilization_rate"],
            errors="coerce",
        ).fillna(0.0)
    effective_ratio = _resolve_effective_position_ratio(
        raw_ratio=df_out["utilization_rate_raw"],
        final_ratio=df_out["final_position_ratio"],
    )
    risk_equity_model = (
        pd.to_numeric(df_out["equity_final"], errors="coerce").fillna(0.0)
        * float(run.cfg.portfolio.initial_capital)
    )
    df_out["effective_position_ratio"] = effective_ratio
    df_out["equity_risk_model"] = risk_equity_model
    risk_fill_result = _build_synthetic_risk_fill_result(
        df_out=df_out,
        fills_df=fills_df,
        daily_position_snapshots=run.daily_position_snapshots,
        initial_cash=float(run.cfg.portfolio.initial_capital),
        lot_size=run.lot_size,
        allow_partial_fill=bool(run.cfg.portfolio.allow_partial_fill),
        cost_cfg=run.cfg.transaction_cost,
        trade_constraints=run.constraints,
    )
    risk_fills_df = risk_fill_result.fills_df
    accounting_df = risk_fill_result.daily_accounting_df
    if accounting_df.empty or len(accounting_df) != len(df_out):
        raise DataValidationError("broker 风控影子账本缺失，无法闭合 cash + position_value = equity。")
    accounting_df = ensure_trade_date_normalized(accounting_df, col=COL_TRADE_DATE)
    accounting_df = accounting_df.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    output_dates = pd.to_datetime(df_out[COL_TRADE_DATE], errors="coerce").dt.normalize()
    accounting_dates = pd.to_datetime(accounting_df[COL_TRADE_DATE], errors="coerce").dt.normalize()
    if not output_dates.reset_index(drop=True).equals(accounting_dates.reset_index(drop=True)):
        raise DataValidationError("broker 风控影子账本日期与净值日期不一致。")

    accounting_columns = (
        "cash",
        "position_value",
        "equity",
        "utilization_rate",
        "risk_fill_count",
        "risk_buy_count",
        "risk_sell_count",
        "risk_commission",
        "risk_stamp_tax",
        "risk_slippage_cost",
        "risk_fee_cost",
        "risk_total_cost",
        "strategy_fill_count",
        "strategy_buy_count",
        "strategy_sell_count",
        "strategy_commission",
        "strategy_stamp_tax",
        "strategy_slippage_cost",
        "strategy_fee_cost",
        "strategy_total_cost",
    )
    for column in accounting_columns:
        df_out[column] = pd.to_numeric(accounting_df[column], errors="raise").to_numpy(copy=False)
    initial_capital = float(run.cfg.portfolio.initial_capital)
    df_out["net_value"] = df_out["equity"] / initial_capital
    df_out["equity_final"] = df_out["net_value"]
    df_out["sum_net_value"] = df_out["net_value"]
    df_out["daily_return"] = _daily_return_from_net_value(df_out["net_value"])
    df_out["cash_equity"] = df_out["cash"] / initial_capital
    df_out["invested_equity"] = df_out["position_value"] / initial_capital
    peak = df_out["net_value"].cummax()
    df_out["drawdown"] = df_out["net_value"] / peak - 1.0
    return (
        df_out,
        risk_fill_result.strategy_fills_df,
        risk_fills_df,
        risk_fill_result.strategy_rejections_df,
        risk_fill_result.daily_position_snapshots,
    )


def run_broker_portfolio(
    selection: SelectionBatch,
    quote_store: QuoteStore,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp],
    *,
    logger=None,
    run_id: str | None = None,
    log_dir=None,
    cfg=None,
    spec=None,
    execution_policy=None,
    risk_cfg=None,
) -> BrokerPortfolioResult:
    if cfg is None or spec is None or execution_policy is None:
        raise DataValidationError(
            "run_broker_portfolio 需要显式传入 cfg/spec/execution_policy。"
        )
    owned_logger = None
    if logger is None and run_id and log_dir:
        logger = get_run_logger(run_id, log_dir)
        owned_logger = logger
    try:
        if not isinstance(selection, SelectionBatch) or not len(selection.selected_rows):
            raise DataValidationError("SelectionBatch.selected_rows 为空，无法执行 broker 回测。")
        if not isinstance(quote_store, QuoteStore):
            raise DataValidationError("broker requires QuoteStore")
        if trade_days is None or len(trade_days) == 0:
            raise DataValidationError("trade_days 为空，无法执行 broker 回测。")
        prepared = _prepare_broker_execution(
            selection,
            quote_store,
            trade_days,
            cfg=cfg,
            spec=spec,
            execution_policy=execution_policy,
            risk_cfg=risk_cfg,
            logger=logger,
        )
        if isinstance(prepared, BrokerPortfolioResult):
            return prepared
        _run_broker_days(prepared)
        df_out, orders_df, fills_df = _build_broker_frames(prepared)
        (
            df_out,
            fills_df,
            risk_fills_df,
            strategy_rejections_df,
            daily_position_snapshots,
        ) = _apply_risk_overlay(
            prepared,
            df_out,
            fills_df,
        )
        return BrokerPortfolioResult(
            merged_df=df_out,
            orders_df=orders_df,
            fills_df=fills_df,
            risk_fills_df=risk_fills_df,
            daily_position_snapshots=daily_position_snapshots,
            strategy_rejections_df=strategy_rejections_df,
        )
    finally:
        close_run_logger_safely(owned_logger)


__all__ = ["run_broker_portfolio"]
