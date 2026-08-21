"""
Rolling portfolio allocation from TopN daily signals.
"""
from __future__ import annotations

import sys
from functools import partial
from typing import Any, Dict, Literal, Optional, cast

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
)
from core.constants import DIRECTION_BUY, DIRECTION_SELL, SIGNAL_COL_DIRECTION
from core.exceptions import DataValidationError
from core.logging import log_info, log_warn
from trading.market_rules import infer_limit_pct
from backtest.panel_native import QuoteStore, SelectionBatch
from ..constraints import HardRuleConstraint, DiversifyConstraint

from ..pipeline_result import AllocationBuildResult
from ..trade_calendar import normalize_trade_days_index
from .state import (
    AllocationDiagnostics,
    AllocationInputs,
    AllocationState,
    EmptyReentryController,
    _finalize_slot_for_day,
    _prepare_day_slots_for_assignment,
    _prime_slot_schedule,
)


class _NullProgressBar:
    def __enter__(self) -> _NullProgressBar:
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        return False

    def update(self, _n: int = 1) -> None:
        return None


def _should_show_progress(*, cfg, total: int) -> bool:
    if bool(cfg.run.quiet):
        return False
    if total <= 256:
        return False
    return bool(sys.stdout.isatty())


def _make_progress_bar(*, cfg, total: int, desc: str):
    if not _should_show_progress(cfg=cfg, total=total):
        return _NullProgressBar()
    from tqdm import tqdm

    return tqdm(total=total, desc=desc, disable=False)



def _normalize_diversify_group_by(value: object) -> str:
    raw = str(value or "").strip()
    return raw or "none"


def _resolve_group_cap_settings(
    cfg,
) -> tuple[str, bool, int, str, bool]:
    diversify_group_by = _normalize_diversify_group_by(
        getattr(cfg.portfolio, "diversify_group_by", "none")
    )
    enable_group_diversify = bool(getattr(cfg.portfolio, "enable_group_diversify", False))
    max_per_group_per_day = int(getattr(cfg.portfolio, "max_per_group_per_day", 0) or 0)
    group_cap_fallback = (
        str(getattr(cfg.portfolio, "group_cap_fallback", "relax") or "").strip().lower() or "relax"
    )
    use_group_cap = (
        enable_group_diversify
        and max_per_group_per_day > 0
        and diversify_group_by.lower() != "none"
    )
    return (
        diversify_group_by,
        enable_group_diversify,
        max_per_group_per_day,
        group_cap_fallback,
        use_group_cap,
    )


def _select_next_candidate_signal(
    *,
    signals: tuple[tuple[str, str], ...],
    sig_idx: int,
    deferred_signals: list[tuple[str, str]],
    deferred_idx: int,
    use_group_cap: bool,
    group_cap_fallback: str,
    check_group,
    group_assign_count: dict[str, int],
) -> tuple[tuple[str, str] | None, bool, int, int, int, int]:
    deferred_inc = 0
    strict_reject_inc = 0
    while True:
        if sig_idx < len(signals):
            code, group_key = signals[sig_idx]
            sig_idx += 1
            if check_group is not None:
                ok, _ = check_group(
                    group_key=group_key,
                    group_assign_count=group_assign_count,
                )
                if not ok:
                    deferred_inc += 1
                    if group_cap_fallback == "relax":
                        deferred_signals.append((code, group_key))
                    else:
                        strict_reject_inc += 1
                    continue
            return (code, group_key), False, sig_idx, deferred_idx, deferred_inc, strict_reject_inc

        if use_group_cap and group_cap_fallback == "relax" and deferred_idx < len(deferred_signals):
            candidate = deferred_signals[deferred_idx]
            deferred_idx += 1
            return candidate, True, sig_idx, deferred_idx, deferred_inc, strict_reject_inc

        return None, False, sig_idx, deferred_idx, deferred_inc, strict_reject_inc


def _append_empty_slot_row(rows: list[dict], *, next_date: pd.Timestamp) -> None:
    rows.append(
        {
            COL_TRADE_DATE: next_date,
            COL_STOCK_CODE: None,
            "open": np.nan,
            "close": np.nan,
            "high": np.nan,
            "low": np.nan,
            SIGNAL_COL_DIRECTION: None,
            "volume": np.nan,
        }
    )


def _resolve_segment_end(
    meta: Dict[str, Any],
    *,
    idx: int,
    last_allowed_idx: int,
    sell_timing: str,
    buy_timing: str = "next_open",
    shift_t1_sell: bool = False,
    allow_intraday: bool = False,
) -> tuple[int, bool]:
    sell_pos_arr = meta["sell_pos"]
    next_sell_ptr = int(np.searchsorted(sell_pos_arr, idx + 1, side="left"))
    if next_sell_ptr >= len(sell_pos_arr):
        return last_allowed_idx, True
    natural_sell_idx = int(sell_pos_arr[next_sell_ptr])
    if natural_sell_idx > last_allowed_idx:
        return last_allowed_idx, True
    sell_timing_norm = str(sell_timing).strip().lower()
    if sell_timing_norm == "next_open":
        if natural_sell_idx < last_allowed_idx:
            natural_sell_idx += 1
        else:
            return last_allowed_idx, True
    elif sell_timing_norm == "signal_close":
        entry_idx = idx if str(buy_timing).strip().lower() == "signal_close" else idx + 1
        if natural_sell_idx == entry_idx and (bool(shift_t1_sell) or (not bool(allow_intraday))):
            shifted_idx = natural_sell_idx + 1
            if shifted_idx <= last_allowed_idx:
                return shifted_idx, True
    return min(natural_sell_idx, last_allowed_idx), False


def _write_assigned_segment(
    *,
    rows: list[dict],
    last_row: dict,
    meta: Dict[str, Any],
    idx: int,
    seg_end: int,
    force_sell_at_end: bool,
    row_is_empty: bool,
    append_segment_rows,
) -> None:
    if row_is_empty:
        last_row[COL_STOCK_CODE] = meta["stock_codes"][idx]
        last_row["open"] = meta["open"][idx]
        last_row["close"] = meta["close"][idx]
        last_row["high"] = meta["high"][idx]
        last_row["low"] = meta["low"][idx]
        last_row[SIGNAL_COL_DIRECTION] = DIRECTION_BUY
        volume_arr = meta["volume"]
        last_row["volume"] = volume_arr[idx] if volume_arr is not None else np.nan
        if seg_end > idx:
            append_segment_rows(
                rows,
                meta,
                idx + 1,
                seg_end,
                force_sell_at_end=force_sell_at_end,
            )
        return
    append_segment_rows(
        rows,
        meta,
        idx,
        seg_end,
        force_sell_at_end=force_sell_at_end,
        force_buy_at_start=True,
    )


def _apply_assigned_candidate(
    *,
    rows: list[dict],
    last_row: dict,
    meta: Dict[str, Any],
    idx: int,
    seg_end: int,
    force_sell_end: bool,
    row_is_empty: bool,
    group_key: str,
    relax_phase: bool,
    check_group,
    group_assign_count: dict[str, int],
    append_segment_rows,
    write_assigned_segment,
) -> int:
    write_assigned_segment(
        rows=rows,
        last_row=last_row,
        meta=meta,
        idx=idx,
        seg_end=seg_end,
        force_sell_at_end=force_sell_end,
        row_is_empty=row_is_empty,
        append_segment_rows=append_segment_rows,
    )
    if check_group is None:
        return 0
    group_assign_count[group_key] = group_assign_count.get(group_key, 0) + 1
    return int(relax_phase)


def _append_segment_rows(
    rows: list[dict],
    meta: Dict[str, Any],
    start: int,
    end: int,
    *,
    force_sell_at_end: bool = False,
    force_buy_at_start: bool = False,
) -> None:
    """Append rows for positions [start, end] from cached price-meta arrays."""
    dates = meta["dates"]
    codes = meta["stock_codes"]
    opens = meta["open"]
    closes = meta["close"]
    highs = meta["high"]
    lows = meta["low"]
    dirs = meta["dir"]
    volume_arr = meta["volume"]
    append_row = rows.append
    if volume_arr is None:
        for pos in range(start, end + 1):
            dir_val = DIRECTION_SELL if (force_sell_at_end and pos == end) else dirs[pos]
            if force_buy_at_start and pos == start:
                dir_val = DIRECTION_BUY
            if force_sell_at_end and pos != end and dir_val == DIRECTION_SELL:
                dir_val = None
            append_row(
                {
                    COL_TRADE_DATE: dates[pos],
                    COL_STOCK_CODE: codes[pos],
                    "open": opens[pos],
                    "close": closes[pos],
                    "high": highs[pos],
                    "low": lows[pos],
                    SIGNAL_COL_DIRECTION: dir_val,
                    "volume": np.nan,
                }
            )
        return
    for pos in range(start, end + 1):
        dir_val = DIRECTION_SELL if (force_sell_at_end and pos == end) else dirs[pos]
        if force_buy_at_start and pos == start:
            dir_val = DIRECTION_BUY
        if force_sell_at_end and pos != end and dir_val == DIRECTION_SELL:
            dir_val = None
        append_row(
            {
                COL_TRADE_DATE: dates[pos],
                COL_STOCK_CODE: codes[pos],
                "open": opens[pos],
                "close": closes[pos],
                "high": highs[pos],
                "low": lows[pos],
                SIGNAL_COL_DIRECTION: dir_val,
                "volume": volume_arr[pos],
            }
        )


def _log_constraint_summary(
    *,
    skip_missing_price: int,
    skip_no_next_bar: int,
    skip_no_trade: int,
    skip_limit: int,
    warn_missing_price: int,
) -> None:
    skipped_total = skip_missing_price + skip_no_next_bar + skip_no_trade + skip_limit
    if skipped_total <= 0 and warn_missing_price <= 0:
        return
    parts = []
    if skip_missing_price:
        parts.append(f"缺行情/异常过滤 {skip_missing_price}")
    if skip_no_next_bar:
        parts.append(f"无next_bar {skip_no_next_bar}")
    if skip_no_trade:
        parts.append(f"停牌/无量 {skip_no_trade}")
    if skip_limit:
        parts.append(f"涨跌停 {skip_limit}")
    if warn_missing_price:
        parts.append(f"缺行情/异常放行 {warn_missing_price}")
    log_info("滚动分配约束结果：" + "，".join(parts) + "。")


def _resolve_assignable_price_meta(
    code: str,
    *,
    date_np: np.datetime64,
    buy_timing: str,
    allow_missing_stock_data: bool,
    get_price_meta,
) -> tuple[Dict[str, Any] | None, int | None, int | None, str | None]:
    try:
        meta = get_price_meta(code)
    except KeyError:
        if not allow_missing_stock_data:
            raise
        return None, None, None, "missing_price"
    last_allowed_idx = meta["window_last_idx"]
    if last_allowed_idx < 0:
        return None, None, None, "missing_price"

    dates_arr = meta["dates"]
    idx = int(np.searchsorted(dates_arr, date_np, side="left"))
    if idx >= len(dates_arr) or dates_arr[idx] != date_np:
        return None, None, None, "missing_price"

    if buy_timing == "next_open":
        if idx >= meta["last_idx"] or idx + 1 > last_allowed_idx:
            return None, None, None, "no_next_bar"
    elif idx > last_allowed_idx:
        return None, None, None, "missing_price"

    return meta, idx, last_allowed_idx, None


def _resolve_assignable_segment(
    code: str,
    *,
    date_np: np.datetime64,
    buy_timing: str,
    sell_timing: str,
    allow_missing_stock_data: bool,
    cons_cfg,
    get_price_meta,
    check_meta,
    resolve_segment_end,
) -> tuple[Dict[str, Any] | None, int | None, int | None, bool, str | None, str | None]:
    meta, idx, last_allowed_idx, skip_reason = _resolve_assignable_price_meta(
        code,
        date_np=date_np,
        buy_timing=buy_timing,
        allow_missing_stock_data=allow_missing_stock_data,
        get_price_meta=get_price_meta,
    )
    if skip_reason is not None:
        return None, None, None, False, skip_reason, None
    if meta is None or idx is None or last_allowed_idx is None:
        return None, None, None, False, "missing_price", None

    ok, reason = check_meta(meta=meta, idx=idx, cons_cfg=cons_cfg)
    if not ok:
        return None, None, None, False, reason, None

    seg_end, force_sell_end = resolve_segment_end(
        meta,
        idx=idx,
        last_allowed_idx=last_allowed_idx,
        sell_timing=sell_timing,
    )
    if seg_end < idx:
        return None, None, None, False, "missing_price", None
    return meta, idx, seg_end, force_sell_end, None, reason


def _build_trade_day_runtime(
    *,
    trade_days: list[pd.Timestamp],
    full_window_end_ts: pd.Timestamp,
) -> tuple[
    dict[pd.Timestamp, pd.Timestamp],
    np.ndarray,
    dict[np.datetime64, int],
    np.datetime64,
    list[list[int]],
]:
    next_date_map = {trade_days[i]: trade_days[i + 1] for i in range(len(trade_days) - 1)}
    trade_days_np = np.array(trade_days, dtype="datetime64[ns]")
    trade_day_index = {trade_days_np[i]: i for i in range(len(trade_days_np))}
    full_window_end_np = np.datetime64(pd.Timestamp(full_window_end_ts).normalize(), "ns")
    avail_by_day_idx: list[list[int]] = [[] for _ in range(len(trade_days_np))]
    return next_date_map, trade_days_np, trade_day_index, full_window_end_np, avail_by_day_idx


def _log_group_cap_summary(
    *,
    diversify_group_by: str,
    max_per_group_per_day: int,
    group_cap_fallback: str,
    group_cap_deferred: int,
    group_cap_strict_reject: int,
    group_cap_relax_assign: int,
) -> None:
    parts = [
        f"group_by={diversify_group_by}",
        f"max_per_group_per_day={max_per_group_per_day}",
        f"fallback={group_cap_fallback}",
    ]
    if group_cap_deferred:
        parts.append(f"分组限额递延 {group_cap_deferred}")
    if group_cap_strict_reject:
        parts.append(f"严格拒绝 {group_cap_strict_reject}")
    if group_cap_relax_assign:
        parts.append(f"放宽回补 {group_cap_relax_assign}")
    log_info("滚动分散约束结果：" + "，".join(parts) + "。")


def _build_price_meta(
    code: str,
    *,
    quote_store: QuoteStore,
    selection: SelectionBatch,
    full_window_end_np: np.datetime64,
    cons_cfg,
) -> Dict[str, Any]:
    stock_id = quote_store.stock_id(code)
    stock_slice = quote_store.index.stock_slice(stock_id)
    dates = quote_store.index.trade_dates[stock_slice]
    sell_mask = selection.final_sell[stock_slice]
    sell_pos = np.flatnonzero(sell_mask)
    dirs: np.ndarray = np.full(len(dates), "", dtype=object)
    dirs[sell_pos] = DIRECTION_SELL
    stock_names = quote_store.columns.get("stock_name")
    stock_name = (
        str(stock_names[int(stock_slice.start or 0)]).strip()
        if stock_names is not None and len(dates)
        else None
    )
    meta = {
        "dates": dates,
        "stock_codes": quote_store.stock_code_by_row[stock_slice],
        "open": quote_store.column("open")[stock_slice],
        "close": quote_store.column("close")[stock_slice],
        "high": quote_store.column("high")[stock_slice],
        "low": quote_store.column("low")[stock_slice],
        "pre_close": (
            quote_store.column("pre_close")[stock_slice]
            if "pre_close" in quote_store.columns
            else None
        ),
        "dir": dirs,
        "volume": (
            quote_store.column("volume")[stock_slice]
            if "volume" in quote_store.columns
            else None
        ),
        "sell_pos": sell_pos,
        "last_idx": int(len(dates) - 1),
        "window_last_idx": int(np.searchsorted(dates, full_window_end_np, side="right")) - 1,
        "stock_name": stock_name,
    }
    up_pct, down_pct = infer_limit_pct(
        code,
        base_up_pct=float(cons_cfg.limit_up_pct),
        base_down_pct=float(cons_cfg.limit_down_pct),
        stock_name=meta["stock_name"],
    )
    meta["limit_up_pct"] = float(up_pct)
    meta["limit_down_pct"] = float(down_pct)
    return meta


def _build_allocation_slots(
    *,
    trade_days: list[pd.Timestamp],
    num_j: int,
    num_i: int,
    fill_all_init: bool,
) -> tuple[Dict[str, list[dict]], list[str], list[list[dict]]]:
    dfs_rows: Dict[str, list[dict]] = {}
    keys: list[str] = []

    for j in range(1, num_j + 1):
        init_dates = trade_days[:1] if fill_all_init else (trade_days[:j] if len(trade_days) >= j else trade_days)
        for i in range(1, num_i + 1):
            key = f"df_{j}_{i}"
            keys.append(key)
            dfs_rows[key] = [
                {
                    COL_TRADE_DATE: d,
                    COL_STOCK_CODE: None,
                    "open": np.nan,
                    "close": np.nan,
                    "high": np.nan,
                    "low": np.nan,
                    SIGNAL_COL_DIRECTION: None,
                }
                for d in init_dates
            ]

    return dfs_rows, keys, [dfs_rows[key] for key in keys]



def _selection_records_by_date(
    selection: SelectionBatch,
    quote_store: QuoteStore,
    *,
    trade_days,
    buy_timing: str,
    use_group_cap: bool,
    diversify_group_by: str,
) -> tuple[
    dict[pd.Timestamp, tuple[tuple[str, str], ...]],
    list[pd.Timestamp],
    int,
    int,
]:
    trade_days_idx = normalize_trade_days_index(trade_days, name="trade_days")
    if trade_days_idx.empty:
        raise DataValidationError("trade_days 过滤后为空。")
    allowed_days = trade_days_idx.to_numpy(dtype="datetime64[ns]", copy=False)
    last_day = allowed_days[-1]

    group_values = None
    if use_group_cap:
        group_name = _normalize_diversify_group_by(diversify_group_by)
        if group_name.casefold() in {"", "none", "code_prefix", "market"}:
            raise DataValidationError(
                "portfolio.diversify_group_by 不再支持内置分组；"
                "必须是 QuoteStore 中的自定义列。"
            )
        if group_name not in quote_store.columns:
            raise DataValidationError(
                f"portfolio.diversify_group_by={group_name!r} 不在 QuoteStore 中。"
            )
        group_values = quote_store.column(group_name)

    def _filter_rows(raw_rows: np.ndarray, *, label: str) -> tuple[np.ndarray, int]:
        rows = np.asarray(raw_rows, dtype=np.int64)
        if rows.ndim != 1:
            raise DataValidationError(f"{label} rows must be one-dimensional")
        if len(rows) and (rows.min() < 0 or rows.max() >= quote_store.index.row_count):
            raise DataValidationError(f"{label} rows contain an out-of-range position")
        dates = quote_store.index.trade_dates[rows]
        missing = np.unique(dates[~np.isin(dates, allowed_days)])
        if len(missing):
            sample = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in missing[:5]]
            raise DataValidationError(f"{label} rows contain dates outside trade_days: {sample}")
        if buy_timing != "next_open":
            return rows, 0
        terminal = dates == last_day
        return rows[~terminal], int(terminal.sum())

    selected_rows, selected_dropped = _filter_rows(
        selection.selected_rows,
        label="selected",
    )
    candidate_source = selection.candidate_rows[
        ~np.isin(selection.candidate_rows, selection.selected_rows)
    ]
    candidate_rows, candidate_dropped = _filter_rows(
        candidate_source,
        label="candidate",
    )
    if selected_dropped:
        log_warn(
            "窗口末日信号无法 next_open 执行："
            f"已过滤 {selected_dropped} 条 trade_date={pd.Timestamp(last_day).strftime('%Y-%m-%d')}。"
        )
    if candidate_dropped:
        log_warn(
            "窗口末日候补信号无法 next_open 执行："
            f"已过滤 {candidate_dropped} 条 trade_date={pd.Timestamp(last_day).strftime('%Y-%m-%d')}。"
        )

    records: dict[pd.Timestamp, list[tuple[str, str]]] = {}
    seen: set[tuple[np.datetime64, str]] = set()
    for source_rows in (selected_rows, candidate_rows):
        for row in source_rows:
            position = int(row)
            date_np = quote_store.index.trade_dates[position]
            code = str(quote_store.stock_code_by_row[position]).strip()
            identity = (date_np, code)
            if identity in seen:
                continue
            seen.add(identity)
            group_key = ""
            if group_values is not None:
                raw_group = group_values[position]
                group_key = "" if pd.isna(raw_group) else str(raw_group).strip()
                if not group_key:
                    raise DataValidationError(
                        f"portfolio.diversify_group_by={diversify_group_by!r} "
                        f"contains an empty value for {code} on {pd.Timestamp(date_np).date()}"
                    )
            date = pd.Timestamp(date_np).normalize()
            records.setdefault(date, []).append((code, group_key))
    return (
        {date: tuple(values) for date, values in records.items()},
        trade_days_idx.to_list(),
        selected_dropped,
        candidate_dropped,
    )


def _prepare_allocation_inputs(
    *,
    selection: SelectionBatch,
    quote_store: QuoteStore,
    trade_days,
    execution_policy,
    cfg,
    spec,
) -> AllocationInputs:
    if execution_policy is None:
        raise DataValidationError("execution_policy 不能为空：请显式传入 execution_policy。")
    if cfg is None:
        raise DataValidationError("build_trade_segments 需要显式传入 cfg。")
    allow_missing_stock_data = bool(cfg.run.allow_missing_stock_data)
    log_info("生成滚动交易列表中...")
    if not isinstance(selection, SelectionBatch):
        raise DataValidationError("build_trade_segments requires SelectionBatch")
    if not isinstance(quote_store, QuoteStore):
        raise DataValidationError("build_trade_segments requires QuoteStore")
    if not len(selection.selected_rows):
        raise DataValidationError("SelectionBatch.selected_rows 为空。")
    if spec is None:
        raise DataValidationError("build_trade_segments 需要显式传入 spec。")
    buy_timing = str(getattr(execution_policy, "buy_timing", "")).strip().lower() or "next_open"
    sell_timing = str(getattr(execution_policy, "sell_timing", "")).strip().lower() or "signal_close"
    diversify_group_by, _, max_per_group_per_day, group_cap_fallback, use_group_cap = (
        _resolve_group_cap_settings(cfg)
    )
    signals_by_date, trade_days_list, _, _ = _selection_records_by_date(
        selection,
        quote_store,
        trade_days=trade_days,
        buy_timing=buy_timing,
        use_group_cap=use_group_cap,
        diversify_group_by=diversify_group_by,
    )
    full_window_end_ts = pd.Timestamp(trade_days_list[-1]).normalize()
    next_date_map, trade_days_np, trade_day_index, full_window_end_np, _ = _build_trade_day_runtime(
        trade_days=trade_days_list,
        full_window_end_ts=full_window_end_ts,
    )
    num_j = int(spec.portfolio_num_j)
    num_i = int(spec.portfolio_num_i)
    empty_reentry_mode = str(getattr(cfg.portfolio, "empty_reentry_mode", "fill_all")).strip().lower() or "fill_all"
    slot_assignment_mode = str(getattr(cfg.portfolio, "slot_assignment_mode", "fixed")).strip().lower() or "fixed"
    if slot_assignment_mode not in {"fixed", "round_robin"}:
        raise DataValidationError(
            "portfolio.slot_assignment_mode 仅支持 fixed/round_robin，"
            f"当前为 {slot_assignment_mode!r}。"
        )
    return AllocationInputs(
        cfg=cfg,
        selection=selection,
        quote_store=quote_store,
        input_signal_rows=len(selection.selected_rows),
        prepared_signal_rows=sum(len(values) for values in signals_by_date.values()),
        signals_by_date=signals_by_date,
        trade_days=trade_days_list,
        next_date_map=next_date_map,
        trade_days_np=trade_days_np,
        trade_day_index=trade_day_index,
        full_window_end_np=full_window_end_np,
        buy_timing=buy_timing,
        sell_timing=sell_timing,
        shift_t1_sell=bool(getattr(execution_policy, "shift_t1_sell", False)),
        allow_intraday=bool(getattr(execution_policy, "allow_intraday", False)),
        allow_missing_stock_data=allow_missing_stock_data,
        empty_reentry_mode=empty_reentry_mode,
        slot_assignment_mode=slot_assignment_mode,
        diversify_group_by=diversify_group_by,
        max_per_group_per_day=max_per_group_per_day,
        group_cap_fallback=group_cap_fallback,
        use_group_cap=use_group_cap,
        num_j=num_j,
        num_i=num_i,
        cons_cfg=cfg.trade_constraints,
        out_cols=(
            COL_TRADE_DATE,
            COL_STOCK_CODE,
            "open",
            "close",
            "high",
            "low",
            SIGNAL_COL_DIRECTION,
            "volume",
        ),
    )


def _get_cached_price_meta(
    inputs: AllocationInputs,
    cache: dict[str, dict[str, Any]],
    code: str,
) -> dict[str, Any]:
    meta = cache.get(code)
    if meta is None:
        meta = _build_price_meta(
            code,
            quote_store=inputs.quote_store,
            selection=inputs.selection,
            full_window_end_np=inputs.full_window_end_np,
            cons_cfg=inputs.cons_cfg,
        )
        cache[code] = meta
    return meta


def _initialize_allocation_state(
    inputs: AllocationInputs,
) -> tuple[AllocationState, AllocationDiagnostics]:
    dfs_rows, keys, rows_list = _build_allocation_slots(
        trade_days=inputs.trade_days,
        num_j=inputs.num_j,
        num_i=inputs.num_i,
        fill_all_init=inputs.empty_reentry_mode == "fill_all",
    )
    price_meta_cache: dict[str, dict[str, Any]] = {}
    hard_constraint = HardRuleConstraint(buy_timing=inputs.buy_timing)
    diversify_constraint = (
        DiversifyConstraint(max_per_group=inputs.max_per_group_per_day)
        if inputs.use_group_cap
        else None
    )
    state = AllocationState(
        dfs_rows=dfs_rows,
        keys=keys,
        rows_list=rows_list,
        avail_by_day_idx=[[] for _ in inputs.trade_days_np],
        reentry_controller=EmptyReentryController(
            mode=inputs.empty_reentry_mode,
            num_j=inputs.num_j,
            num_i=inputs.num_i,
        ),
        price_meta_cache=price_meta_cache,
        get_price_meta=partial(
            _get_cached_price_meta,
            inputs,
            price_meta_cache,
        ),
        resolve_segment_end=partial(
            _resolve_segment_end,
            buy_timing=inputs.buy_timing,
            shift_t1_sell=inputs.shift_t1_sell,
            allow_intraday=inputs.allow_intraday,
        ),
        check_meta=hard_constraint.check_meta,
        check_group=(diversify_constraint.check_group if diversify_constraint is not None else None),
    )
    _prime_slot_schedule(
        rows_list=state.rows_list,
        trade_day_index=inputs.trade_day_index,
        avail_by_day_idx=state.avail_by_day_idx,
    )
    diagnostics = AllocationDiagnostics(
        input_signal_rows=inputs.input_signal_rows,
        prepared_signal_rows=inputs.prepared_signal_rows,
        trade_day_count=len(inputs.trade_days),
        slot_count=len(state.keys),
        buy_timing=inputs.buy_timing,
        sell_timing=inputs.sell_timing,
    )
    return state, diagnostics


def _assign_next_candidate(
    inputs: AllocationInputs,
    state: AllocationState,
    diagnostics: AllocationDiagnostics,
    *,
    rows: list[dict],
    last_row: dict,
    row_is_empty: bool,
    date_np: np.datetime64,
    signals: tuple[tuple[str, str], ...],
) -> bool:
    while True:
        candidate, relax_phase, state.sig_idx, state.deferred_idx, deferred_inc, strict_reject_inc = (
            _select_next_candidate_signal(
                signals=signals,
                sig_idx=state.sig_idx,
                deferred_signals=state.deferred_signals,
                deferred_idx=state.deferred_idx,
                use_group_cap=inputs.use_group_cap,
                group_cap_fallback=inputs.group_cap_fallback,
                check_group=state.check_group,
                group_assign_count=state.group_assign_count,
            )
        )
        diagnostics.group_cap_deferred += deferred_inc
        diagnostics.group_cap_strict_reject += strict_reject_inc
        if candidate is None:
            return False
        code, group_key = candidate
        meta, idx, seg_end, force_sell_end, skip_reason, warn_reason = _resolve_assignable_segment(
            code,
            date_np=date_np,
            buy_timing=inputs.buy_timing,
            sell_timing=inputs.sell_timing,
            allow_missing_stock_data=inputs.allow_missing_stock_data,
            cons_cfg=inputs.cons_cfg,
            get_price_meta=state.get_price_meta,
            check_meta=state.check_meta,
            resolve_segment_end=state.resolve_segment_end,
        )
        if skip_reason is not None:
            diagnostics.record_skip(skip_reason)
            continue
        if meta is None or idx is None or seg_end is None:
            diagnostics.skip_missing_price += 1
            continue
        if warn_reason == "missing_price":
            diagnostics.warn_missing_price += 1
        diagnostics.group_cap_relax_assign += _apply_assigned_candidate(
            rows=rows,
            last_row=last_row,
            meta=meta,
            idx=idx,
            seg_end=seg_end,
            force_sell_end=force_sell_end,
            row_is_empty=row_is_empty,
            group_key=group_key,
            relax_phase=relax_phase,
            check_group=state.check_group,
            group_assign_count=state.group_assign_count,
            append_segment_rows=_append_segment_rows,
            write_assigned_segment=_write_assigned_segment,
        )
        return True


def _process_one_slot(
    inputs: AllocationInputs,
    state: AllocationState,
    diagnostics: AllocationDiagnostics,
    *,
    slot_idx: int,
    date_np: np.datetime64,
    signals: tuple[tuple[str, str], ...],
    next_date: pd.Timestamp | None,
    eligible_open_slots: set[int] | None,
) -> None:
    rows = state.rows_list[slot_idx]
    last_row = rows[-1]
    stock_code = last_row[COL_STOCK_CODE]
    row_is_empty = stock_code is None or stock_code == ""
    allow_reentry = not row_is_empty and last_row[SIGNAL_COL_DIRECTION] == DIRECTION_SELL
    can_open_today = inputs.empty_reentry_mode == "fill_all" or (
        eligible_open_slots is not None and slot_idx in eligible_open_slots
    )
    assigned = False
    if can_open_today and (row_is_empty or allow_reentry):
        assigned = _assign_next_candidate(
            inputs,
            state,
            diagnostics,
            rows=rows,
            last_row=last_row,
            row_is_empty=row_is_empty,
            date_np=date_np,
            signals=signals,
        )
    _finalize_slot_for_day(
        assigned=assigned,
        next_date=next_date,
        rows=rows,
        slot_idx=slot_idx,
        append_empty_slot_row=_append_empty_slot_row,
        rows_list=state.rows_list,
        trade_day_index=inputs.trade_day_index,
        avail_by_day_idx=state.avail_by_day_idx,
    )


def _process_one_trade_day(
    inputs: AllocationInputs,
    state: AllocationState,
    diagnostics: AllocationDiagnostics,
    day_idx: int,
) -> None:
    date = inputs.trade_days[day_idx]
    signals = inputs.signals_by_date.get(date, ())
    state.reset_day_candidates()
    slots, next_date, eligible_open_slots = _prepare_day_slots_for_assignment(
        day_idx=day_idx,
        date=date,
        avail_by_day_idx=state.avail_by_day_idx,
        next_date_map=inputs.next_date_map,
        buy_timing=inputs.buy_timing,
        slot_assignment_mode=inputs.slot_assignment_mode,
        round_robin_cursor=state.round_robin_cursor,
        use_fill_all_reentry=inputs.empty_reentry_mode == "fill_all",
        reentry_controller=state.reentry_controller,
        signals_present=bool(signals),
        rows_list=state.rows_list,
    )
    for slot_idx in slots:
        _process_one_slot(
            inputs,
            state,
            diagnostics,
            slot_idx=slot_idx,
            date_np=cast(np.datetime64, inputs.trade_days_np[day_idx]),
            signals=signals,
            next_date=next_date,
            eligible_open_slots=eligible_open_slots,
        )
    if inputs.slot_assignment_mode == "round_robin" and signals and len(slots) > 1:
        state.round_robin_cursor = (state.round_robin_cursor + 1) % len(state.keys)


def _process_trade_days(
    inputs: AllocationInputs,
    state: AllocationState,
    diagnostics: AllocationDiagnostics,
) -> None:
    slot_count = len(state.keys)
    total = len(inputs.trade_days) * slot_count
    with _make_progress_bar(cfg=inputs.cfg, total=total, desc="处理中") as pbar:
        for day_idx in range(len(inputs.trade_days)):
            _process_one_trade_day(inputs, state, diagnostics, day_idx)
            pbar.update(slot_count)


def _finalize_allocation_result(
    inputs: AllocationInputs,
    state: AllocationState,
    diagnostics: AllocationDiagnostics,
) -> AllocationBuildResult:
    out = {
        name: pd.DataFrame(rows, columns=inputs.out_cols)
        for name, rows in state.dfs_rows.items()
    }
    _log_constraint_summary(
        skip_missing_price=diagnostics.skip_missing_price,
        skip_no_next_bar=diagnostics.skip_no_next_bar,
        skip_no_trade=diagnostics.skip_no_trade,
        skip_limit=diagnostics.skip_limit,
        warn_missing_price=diagnostics.warn_missing_price,
    )
    if inputs.use_group_cap:
        _log_group_cap_summary(
            diversify_group_by=inputs.diversify_group_by,
            max_per_group_per_day=inputs.max_per_group_per_day,
            group_cap_fallback=inputs.group_cap_fallback,
            group_cap_deferred=diagnostics.group_cap_deferred,
            group_cap_strict_reject=diagnostics.group_cap_strict_reject,
            group_cap_relax_assign=diagnostics.group_cap_relax_assign,
        )
    return AllocationBuildResult(
        trading_dfs=out,
        diagnostics=diagnostics.to_dict(),
    )


def build_trade_segments(
    *,
    selection: SelectionBatch,
    quote_store: QuoteStore,
    trade_days: Optional[pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame] = None,
    execution_policy=None,
    cfg=None,
    spec=None,
) -> AllocationBuildResult:
    inputs = _prepare_allocation_inputs(
        selection=selection,
        quote_store=quote_store,
        trade_days=trade_days,
        execution_policy=execution_policy,
        cfg=cfg,
        spec=spec,
    )
    state, diagnostics = _initialize_allocation_state(inputs)
    _process_trade_days(inputs, state, diagnostics)
    return _finalize_allocation_result(inputs, state, diagnostics)

__all__ = [
    "build_trade_segments",
]
