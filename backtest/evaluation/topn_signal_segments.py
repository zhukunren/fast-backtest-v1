"""Lightweight TopN trade-segment evaluation over panel-native rows."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.constants import COL_STOCK_CODE, COL_TRADE_DATE
from core.exceptions import DataValidationError
from core.logging import log_info, log_warn

from backtest.panel_native import QuoteStore, SelectionBatch

from ..broker.plan import build_trade_plan
from ..topn_trade_segments import (
    build_topn_trade_segments_summary_df,
    empty_topn_trade_segments_df,
    empty_topn_trade_segments_summary_df,
    finalize_topn_trade_segments_df,
)
from ..trade_calendar import normalize_trade_days_index

_DETAIL_COLS = empty_topn_trade_segments_df().columns.tolist()


def _trade_return(entry_price: object, exit_price: object) -> float:
    try:
        entry = float(entry_price)
        exit_value = float(exit_price)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(entry) or not np.isfinite(exit_value) or abs(entry) <= 1e-12:
        return 0.0
    return float(exit_value / entry - 1.0)


def evaluate_topn_signal_trade_segments(
    selection: SelectionBatch,
    quote_store: QuoteStore,
    *,
    execution_policy=None,
    cfg=None,
    logger=None,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if cfg is None:
        raise DataValidationError(
            "evaluate_topn_signal_trade_segments 需要显式传入 cfg。"
        )
    if execution_policy is None:
        raise DataValidationError(
            "evaluate_topn_signal_trade_segments 需要显式传入 execution_policy。"
        )
    if not isinstance(selection, SelectionBatch):
        raise DataValidationError("TopN evaluation requires SelectionBatch")
    if not isinstance(quote_store, QuoteStore):
        raise DataValidationError("TopN evaluation requires QuoteStore")
    if not len(selection.selected_rows):
        return empty_topn_trade_segments_df(), empty_topn_trade_segments_summary_df()
    if trade_days is None:
        trade_days_idx = quote_store.trade_days
    else:
        trade_days_idx = normalize_trade_days_index(trade_days, name="trade_days")
    if trade_days_idx.empty:
        raise DataValidationError("trade_days 为空，无法评估 TopN 交易段。")

    selected_dates = quote_store.index.trade_dates[selection.selected_rows]
    allowed_days = trade_days_idx.to_numpy(dtype="datetime64[ns]", copy=False)
    outside = np.unique(selected_dates[~np.isin(selected_dates, allowed_days)])
    if len(outside):
        sample = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in outside[:5]]
        raise DataValidationError(f"selected rows contain dates outside trade_days: {sample}")

    timing = str(getattr(execution_policy, "buy_timing", "")).strip().lower()
    skipped = int(np.count_nonzero(selected_dates == allowed_days[-1])) if timing == "next_open" else 0
    plans = build_trade_plan(
        selection,
        quote_store,
        trade_days=trade_days_idx,
        execution_policy=execution_policy,
        cfg=cfg,
        include_candidates=False,
    )
    rows: list[dict[str, object]] = []
    for plan in plans:
        entry_column = quote_store.column(plan.entry_price_type)
        exit_column = quote_store.column(plan.exit_price_type)
        rows.append(
            {
                COL_TRADE_DATE: pd.Timestamp(
                    quote_store.index.trade_dates[plan.signal_row]
                ).normalize(),
                COL_STOCK_CODE: plan.stock_code,
                "buy_date": pd.Timestamp(
                    quote_store.index.trade_dates[plan.entry_row]
                ).normalize(),
                "sell_date": pd.Timestamp(
                    quote_store.index.trade_dates[plan.exit_row]
                ).normalize(),
                "trade_return": _trade_return(
                    entry_column[plan.entry_row],
                    exit_column[plan.exit_row],
                ),
                "segment_rows": int(plan.exit_row - plan.signal_row + 1),
            }
        )

    details_df = finalize_topn_trade_segments_df(
        pd.DataFrame(rows, columns=_DETAIL_COLS)
    )
    summary_df = build_topn_trade_segments_summary_df(details_df)
    total = int(len(selection.selected_rows))
    suppressed = max(total - skipped - len(plans), 0)
    log_info(
        f"TopN后交易段评估：总数 {total}，成功 {len(details_df)}，失败 0，跳过 {skipped}",
        logger,
    )
    if suppressed:
        log_info(
            f"TopN后交易段评估：忽略 {suppressed} 条持仓内重复 TopN 信号。",
            logger,
        )
    if skipped:
        log_warn(
            f"TopN后交易段评估：跳过 {skipped} 条窗口末日无法 next_open 的信号。",
            logger,
        )
    return details_df, summary_df


__all__ = ["evaluate_topn_signal_trade_segments"]
