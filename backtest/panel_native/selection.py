"""Position-based raw-signal finalization, tradability checks, and TopN."""
from __future__ import annotations

from collections.abc import Iterable
import math

import numpy as np
import pandas as pd

from core.constants import (
    FLOAT_TOLERANCE,
    LIMIT_UP_PCT_BEIJING,
    LIMIT_UP_PCT_CYTECH,
    LIMIT_UP_PCT_ST,
    PRICE_TICK_A_SHARE,
)
from core.exceptions import DataValidationError
from core.schema import normalize_sort_spec
from core.timing_flags import coerce_timing_flag_series
from trading.market_rules import _ST_NAME_RE, is_st_stock

from core.panel_contracts import SelectionBatch
from core.panel_native import ColumnStore, PanelView, PricePanel, QuoteStore


def _readonly(values: np.ndarray, *, dtype: np.dtype | None = None) -> np.ndarray:
    array = np.asarray(values, dtype=dtype)
    if not array.flags.c_contiguous:
        array = np.ascontiguousarray(array)
    array.setflags(write=False)
    return array


def _normalized_timestamp(value: object | None) -> np.datetime64 | None:
    if value is None:
        return None
    return np.datetime64(pd.Timestamp(value).normalize(), "ns")


def _buy_timing(execution_policy) -> str:
    if execution_policy is None:
        raise DataValidationError("execution_policy is required for signal finalization")
    timing = str(getattr(execution_policy, "buy_timing", "") or "next_open").strip().casefold()
    if timing not in {"next_open", "signal_close"}:
        raise DataValidationError(f"unsupported buy_timing={timing!r}")
    return timing


def _coerce_aligned_flag(
    values: np.ndarray,
    *,
    name: str,
    row_count: int,
) -> np.ndarray:
    series = pd.Series(values, copy=False)
    if len(series) != row_count:
        raise DataValidationError(
            f"{name} length does not match panel ({len(series)} vs {row_count})"
        )
    return coerce_timing_flag_series(
        series,
        column_name=name,
        error_cls=DataValidationError,
    ).to_numpy(dtype=bool, copy=False)


def finalize_raw_signals(
    panel: PricePanel,
    store: ColumnStore,
    *,
    buy_raw: np.ndarray,
    sell_raw: np.ndarray,
    spec: object,
    execution_policy,
    start_eval: object | None,
    end_eval: object | None,
    additional_buy_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, int | str]]:
    """Apply framework-owned window, timing, and terminal-buy rules once."""
    row_count = panel.row_count
    buy = np.asarray(buy_raw)
    sell = np.asarray(sell_raw)
    if buy.dtype != np.dtype(bool) or sell.dtype != np.dtype(bool):
        raise DataValidationError("raw signal arrays must use bool dtype")
    if buy.ndim != 1 or sell.ndim != 1 or len(buy) != row_count or len(sell) != row_count:
        raise DataValidationError("raw signal arrays must align with every panel row")
    buy = buy.copy()
    sell = sell.copy()
    dates = panel.index.trade_dates
    start = _normalized_timestamp(start_eval)
    end = _normalized_timestamp(end_eval)
    inside = np.ones(row_count, dtype=bool)
    if start is not None:
        inside &= dates >= start
    if end is not None:
        inside &= dates <= end
    buy &= inside
    sell &= inside

    view = PanelView(panel, store)
    if bool(getattr(spec, "use_index_timing", False)):
        if "time_ok" not in view.column_names:
            raise DataValidationError(
                "panel-native index timing requires an aligned time_ok column"
            )
        buy &= _coerce_aligned_flag(
            view.column("time_ok"),
            name="time_ok",
            row_count=row_count,
        )
    if additional_buy_mask is not None:
        mask = np.asarray(additional_buy_mask)
        if mask.dtype != np.dtype(bool) or mask.ndim != 1 or len(mask) != row_count:
            raise DataValidationError("additional_buy_mask must be an aligned bool array")
        buy &= mask

    timing = _buy_timing(execution_policy)
    terminal_removed = 0
    if timing == "next_open":
        terminal = panel.index.next_row_by_row < 0
        if end is not None:
            terminal |= dates == end
        terminal_removed = int(np.count_nonzero(buy & terminal))
        buy &= ~terminal
    return (
        _readonly(buy, dtype=np.dtype(bool)),
        _readonly(sell, dtype=np.dtype(bool)),
        {
            "inside_window_rows": int(inside.sum()),
            "terminal_buy_removed": terminal_removed,
            "buy_timing": timing,
        },
    )


def _limit_up_pct_by_row(
    quotes: QuoteStore,
    rows: np.ndarray,
    *,
    default_pct: float,
) -> np.ndarray:
    output = np.full(len(rows), float(default_pct), dtype=np.float64)
    names = quotes.columns.get("stock_name")
    for stock_id in np.unique(quotes.index.stock_id_by_row[rows]):
        mask = quotes.index.stock_id_by_row[rows] == stock_id
        code = quotes.stock_codes[int(stock_id)]
        code_no_suffix = code.split(".")[0]
        if code.endswith(".BJ") or code_no_suffix.startswith("BJ"):
            output[mask] = LIMIT_UP_PCT_BEIJING
        elif code_no_suffix.startswith(("688", "689", "300", "301")):
            output[mask] = LIMIT_UP_PCT_CYTECH
        elif is_st_stock(code):
            output[mask] = LIMIT_UP_PCT_ST
    if names is not None and len(rows):
        st_names = (
            pd.Series(names[rows], dtype=object)
            .astype(str)
            .str.strip()
            .str.match(_ST_NAME_RE, na=False)
            .to_numpy(dtype=bool)
        )
        output[st_names] = LIMIT_UP_PCT_ST
    return output


def precheck_tradable_rows(
    quotes: QuoteStore,
    buy_rows: np.ndarray,
    *,
    cfg,
    execution_policy,
    end_eval: object | None,
) -> tuple[np.ndarray, dict[str, int | str | bool]]:
    """Filter buy positions by direct quote gathers, never by per-stock frames."""
    rows = np.asarray(buy_rows, dtype=np.int64)
    if rows.ndim != 1:
        raise DataValidationError("buy_rows must be one-dimensional")
    if len(rows) and (rows.min() < 0 or rows.max() >= quotes.index.row_count):
        raise DataValidationError("buy_rows contains out-of-range positions")
    run_cfg = getattr(cfg, "run", None)
    constraints = getattr(cfg, "trade_constraints", None)
    if constraints is None or not bool(getattr(run_cfg, "precheck_tradable_topn", False)):
        return _readonly(rows, dtype=np.dtype(np.int64)), {
            "enabled": False,
            "input_rows": int(len(rows)),
            "output_rows": int(len(rows)),
        }

    timing = _buy_timing(execution_policy)
    require_next = bool(getattr(constraints, "require_next_bar", True))
    signal_close = timing == "signal_close"
    exec_rows = rows.copy() if signal_close else quotes.index.next_row_by_row[rows].copy()
    end = _normalized_timestamp(end_eval)
    valid_exec = exec_rows >= 0
    if end is not None and bool(valid_exec.any()):
        valid_positions = np.flatnonzero(valid_exec)
        beyond = quotes.index.trade_dates[exec_rows[valid_positions]] > end
        valid_exec[valid_positions[beyond]] = False

    keep = np.ones(len(rows), dtype=bool)
    warn_missing = 0
    drop_missing = 0
    missing = ~valid_exec
    if bool(missing.any()):
        count = int(missing.sum())
        if require_next:
            keep[missing] = False
            drop_missing += count
        else:
            warn_missing += count

    active_positions = np.flatnonzero(valid_exec & keep)
    drop_no_trade = 0
    drop_limit = 0
    if len(active_positions):
        signal_rows = rows[active_positions]
        execution_rows = exec_rows[active_positions]
        required_columns = ("open", "high", "low", "close")
        missing_columns = sorted(set(required_columns) - set(quotes.columns), key=str.casefold)
        if missing_columns:
            raise DataValidationError(f"QuoteStore is missing precheck columns: {missing_columns}")
        open_price = np.asarray(quotes.column("open")[execution_rows], dtype=np.float64)
        high_price = np.asarray(quotes.column("high")[execution_rows], dtype=np.float64)
        low_price = np.asarray(quotes.column("low")[execution_rows], dtype=np.float64)
        close_price = np.asarray(quotes.column("close")[execution_rows], dtype=np.float64)
        previous_close = np.full(len(active_positions), np.nan, dtype=np.float64)
        supplied_pre_close = None
        if "pre_close" in quotes.columns:
            supplied_pre_close = np.asarray(
                quotes.column("pre_close")[execution_rows],
                dtype=np.float64,
            )
            valid_supplied = np.isfinite(supplied_pre_close) & (supplied_pre_close > FLOAT_TOLERANCE)
            previous_close[valid_supplied] = supplied_pre_close[valid_supplied]
        else:
            valid_supplied = np.zeros(len(active_positions), dtype=bool)
        needs_fallback = ~valid_supplied
        if signal_close:
            previous_rows = signal_rows - 1
            same_stock = (
                previous_rows >= 0
            ) & (
                quotes.index.stock_id_by_row[np.maximum(previous_rows, 0)]
                == quotes.index.stock_id_by_row[signal_rows]
            )
            fallback = same_stock & needs_fallback
            previous_close[fallback] = quotes.column("close")[previous_rows[fallback]]
            finite = (
                np.isfinite(open_price)
                & np.isfinite(high_price)
                & np.isfinite(low_price)
                & np.isfinite(close_price)
            )
        else:
            previous_close[needs_fallback] = quotes.column("close")[signal_rows[needs_fallback]]
            finite = (
                np.isfinite(previous_close)
                & np.isfinite(open_price)
                & np.isfinite(high_price)
                & np.isfinite(low_price)
                & np.isfinite(close_price)
            )
        if not bool(finite.all()):
            invalid_positions = active_positions[~finite]
            count = len(invalid_positions)
            if require_next:
                keep[invalid_positions] = False
                drop_missing += count
            else:
                warn_missing += count

        active = finite.copy()
        if bool(getattr(constraints, "enable_no_trade_day", False)) and bool(active.any()):
            volume_values = quotes.columns.get("volume")
            if volume_values is None:
                no_trade = active.copy()
            else:
                volume = np.asarray(volume_values[execution_rows], dtype=np.float64)
                zero_price = (
                    (np.abs(open_price) <= FLOAT_TOLERANCE)
                    | (np.abs(high_price) <= FLOAT_TOLERANCE)
                    | (np.abs(low_price) <= FLOAT_TOLERANCE)
                    | (np.abs(close_price) <= FLOAT_TOLERANCE)
                )
                no_trade = active & (
                    zero_price | ~np.isfinite(volume) | (volume <= FLOAT_TOLERANCE)
                )
            if bool(no_trade.any()):
                rejected = active_positions[no_trade]
                keep[rejected] = False
                drop_no_trade = int(no_trade.sum())
            active &= ~no_trade

        if bool(getattr(constraints, "enable_limit", False)) and bool(active.any()):
            up_pct = _limit_up_pct_by_row(
                quotes,
                execution_rows,
                default_pct=float(getattr(constraints, "limit_up_pct", 0.1)),
            )
            raw_limit = previous_close * (1.0 + up_pct)
            limit_up = (
                np.floor(raw_limit / PRICE_TICK_A_SHARE + 0.5 + 1e-12)
                * PRICE_TICK_A_SHARE
            )
            locked = (
                active
                & (previous_close > 0)
                & (low_price >= limit_up - FLOAT_TOLERANCE)
                & (close_price >= limit_up - FLOAT_TOLERANCE)
            )
            if bool(locked.any()):
                rejected = active_positions[locked]
                keep[rejected] = False
                drop_limit = int(locked.sum())

    kept = rows[keep]
    return _readonly(kept, dtype=np.dtype(np.int64)), {
        "enabled": True,
        "input_rows": int(len(rows)),
        "output_rows": int(len(kept)),
        "buy_timing": timing,
        "require_next_bar": require_next,
        "warn_missing_price": int(warn_missing),
        "drop_missing_price": int(drop_missing),
        "drop_no_trade": int(drop_no_trade),
        "drop_limit": int(drop_limit),
    }


def _candidate_factor(cfg) -> float:
    value = float(getattr(getattr(cfg, "run", None), "topn_candidate_factor", 1.0) or 1.0)
    if not math.isfinite(value) or value < 1.0:
        raise DataValidationError(
            f"topn_candidate_factor={value!r} must be finite and >= 1.0"
        )
    return value


def _validate_sort_column(values: np.ndarray, *, name: str) -> np.ndarray:
    array = np.asarray(values)
    if np.issubdtype(array.dtype, np.bool_) or np.issubdtype(array.dtype, np.complexfloating):
        raise DataValidationError(f"TopN sort column {name!r} must contain real numbers")
    if not np.issubdtype(array.dtype, np.number):
        raise DataValidationError(f"TopN sort column {name!r} must contain numeric values")
    return array.astype(np.float64, copy=False)


def _stable_sort_rows(
    rows: np.ndarray,
    *,
    view: PanelView,
    sort_spec: list[tuple[str, bool]],
) -> np.ndarray:
    ordered = np.asarray(rows, dtype=np.int64).copy()
    # date_rows starts in normalized stock-code order, the final tie-break.
    for name, ascending in reversed(sort_spec):
        values = _validate_sort_column(view.column(name), name=name)[ordered]
        finite = np.isfinite(values)
        finite_positions = np.flatnonzero(finite)
        missing_positions = np.flatnonzero(~finite)
        finite_values = values[finite]
        keys = finite_values if ascending else -finite_values
        value_order = np.argsort(keys, kind="stable")
        ordered = np.concatenate(
            (ordered[finite_positions[value_order]], ordered[missing_positions])
        )
    return ordered


def select_topn_rows(
    panel: PricePanel,
    store: ColumnStore,
    tradable_rows: np.ndarray,
    *,
    spec: object,
    cfg,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Select stable per-date candidate and TopN row positions."""
    rows = np.asarray(tradable_rows, dtype=np.int64)
    sort_spec = normalize_sort_spec(getattr(spec, "sort_spec", ()), allow_empty=False)
    view = PanelView(panel, store)
    missing = sorted({name for name, _ in sort_spec} - set(view.column_names), key=str.casefold)
    if missing:
        raise DataValidationError(f"TopN sort_spec columns missing: {missing}")
    top_n = int(getattr(spec, "top_n", 0))
    if top_n <= 0:
        raise DataValidationError("TopN requires top_n > 0")
    candidate_n = max(int(math.ceil(top_n * _candidate_factor(cfg))), top_n)
    row_mask = np.zeros(panel.row_count, dtype=bool)
    row_mask[rows] = True
    candidates: list[np.ndarray] = []
    selected: list[np.ndarray] = []
    daily_counts: list[tuple[int, int, int, int]] = []
    for date_id in range(len(panel.index.date_offsets) - 1):
        date_rows = panel.index.date_rows(date_id)
        available = date_rows[row_mask[date_rows]]
        if not len(available):
            continue
        ordered = _stable_sort_rows(available, view=view, sort_spec=sort_spec)
        date_candidates = ordered[:candidate_n]
        date_selected = date_candidates[:top_n]
        candidates.append(date_candidates)
        selected.append(date_selected)
        daily_counts.append(
            (date_id, len(ordered), len(date_candidates), len(date_selected))
        )
    candidate_rows = (
        np.concatenate(candidates).astype(np.int64, copy=False)
        if candidates
        else np.empty(0, dtype=np.int64)
    )
    selected_rows = (
        np.concatenate(selected).astype(np.int64, copy=False)
        if selected
        else np.empty(0, dtype=np.int64)
    )
    return (
        _readonly(candidate_rows, dtype=np.dtype(np.int64)),
        _readonly(selected_rows, dtype=np.dtype(np.int64)),
        {
            "candidate_n": candidate_n,
            "top_n": top_n,
            "daily_counts": tuple(daily_counts),
            "sort_spec": tuple(sort_spec),
        },
    )


def build_selection(
    panel: PricePanel,
    store: ColumnStore,
    *,
    buy_raw: np.ndarray,
    sell_raw: np.ndarray,
    spec: object,
    cfg,
    execution_policy,
    start_eval: object | None,
    end_eval: object | None,
    trade_days: Iterable[object] | None = None,
    board_keep_mask: np.ndarray | None = None,
) -> SelectionBatch:
    additional_mask = board_keep_mask
    view = PanelView(panel, store)
    if additional_mask is None and bool(
        getattr(spec, "use_board_timing", False)
        or getattr(spec, "require_board_member", False)
    ):
        if "board_time_ok" not in view.column_names:
            raise DataValidationError(
                "panel-native board filtering requires an aligned board_time_ok column"
            )
        additional_mask = _coerce_aligned_flag(
            view.column("board_time_ok"),
            name="board_time_ok",
            row_count=panel.row_count,
        )
    final_buy, final_sell, final_diagnostics = finalize_raw_signals(
        panel,
        store,
        buy_raw=buy_raw,
        sell_raw=sell_raw,
        spec=spec,
        execution_policy=execution_policy,
        start_eval=start_eval,
        end_eval=end_eval,
        additional_buy_mask=additional_mask,
    )
    buy_rows = np.flatnonzero(final_buy).astype(np.int64, copy=False)
    if trade_days is not None:
        allowed_days = np.asarray(
            [np.datetime64(pd.Timestamp(value).normalize(), "ns") for value in trade_days],
            dtype="datetime64[ns]",
        )
        buy_rows = buy_rows[np.isin(panel.index.trade_dates[buy_rows], allowed_days)]
    quotes = panel.quote_store(store.columns)
    tradable_rows, precheck_diagnostics = precheck_tradable_rows(
        quotes,
        buy_rows,
        cfg=cfg,
        execution_policy=execution_policy,
        end_eval=end_eval,
    )
    candidate_rows, selected_rows, topn_diagnostics = select_topn_rows(
        panel,
        store,
        tradable_rows,
        spec=spec,
        cfg=cfg,
    )
    return SelectionBatch(
        buy_rows=buy_rows,
        tradable_rows=tradable_rows,
        candidate_rows=candidate_rows,
        selected_rows=selected_rows,
        final_buy=final_buy,
        final_sell=final_sell,
        diagnostics={
            "finalization": final_diagnostics,
            "precheck": precheck_diagnostics,
            "topn": topn_diagnostics,
        },
    )


__all__ = [
    "build_selection",
    "finalize_raw_signals",
    "precheck_tradable_rows",
    "select_topn_rows",
]
