"""Build optional TopN trade-segment reports from panel-native rows."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_DIRECTION,
)
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.logging import log_info, log_warn

from backtest.panel_native import QuoteStore, SelectionBatch

from ..broker.plan import TradePlan, build_trade_plan
from ..context import BacktestContext
from ..pipeline_result import TradeSegmentsBuildResult
from ..topn_trade_segments import (
    build_topn_trade_segments_summary_df,
    finalize_topn_trade_segments_df,
)
from ..trade_calendar import normalize_trade_days_index


def _trade_prices(
    plan: TradePlan,
    quotes: QuoteStore,
) -> tuple[float, float, float]:
    entry_price = float(quotes.column(plan.entry_price_type)[plan.entry_row])
    exit_price = float(quotes.column(plan.exit_price_type)[plan.exit_row])
    trade_return = (
        float(exit_price / entry_price - 1.0)
        if np.isfinite(entry_price)
        and np.isfinite(exit_price)
        and abs(entry_price) > 1e-12
        else 0.0
    )
    return entry_price, exit_price, trade_return


def _segment_frame(
    plan: TradePlan,
    quotes: QuoteStore,
    selection: SelectionBatch,
) -> tuple[pd.DataFrame, dict[str, object]]:
    rows = np.arange(plan.signal_row, plan.exit_row + 1, dtype=np.int64)
    names = [
        name
        for name in (
            COL_TRADE_DATE,
            COL_STOCK_CODE,
            "open",
            "close",
            "high",
            "low",
            "volume",
        )
        if name in quotes.columns
    ]
    frame = quotes.to_frame(rows, columns=names)
    direction = np.full(len(rows), "", dtype=object)
    direction[selection.final_buy[rows]] = DIRECTION_BUY
    direction[selection.final_sell[rows]] = DIRECTION_SELL
    frame[SIGNAL_COL_DIRECTION] = direction
    frame["buy_price"] = np.nan
    frame["sell_price"] = np.nan
    frame["trade_return"] = np.nan
    entry_price, exit_price, trade_return = _trade_prices(plan, quotes)
    frame.loc[plan.entry_row - plan.signal_row, "buy_price"] = entry_price
    frame.loc[plan.exit_row - plan.signal_row, "sell_price"] = exit_price
    frame.loc[plan.exit_row - plan.signal_row, "trade_return"] = trade_return
    detail = {
        COL_TRADE_DATE: pd.Timestamp(
            quotes.index.trade_dates[plan.signal_row]
        ).normalize(),
        COL_STOCK_CODE: plan.stock_code,
        "buy_date": pd.Timestamp(quotes.index.trade_dates[plan.entry_row]).normalize(),
        "sell_date": pd.Timestamp(quotes.index.trade_dates[plan.exit_row]).normalize(),
        "trade_return": trade_return,
        "segment_rows": int(plan.exit_row - plan.signal_row + 1),
    }
    return frame, detail


def build_all_trade_segments(
    selection: SelectionBatch,
    quote_store: QuoteStore,
    *,
    execution_policy=None,
    ctx: BacktestContext | None = None,
    cfg=None,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame | None = None,
) -> TradeSegmentsBuildResult:
    cfg = cfg or getattr(ctx, "cfg", None)
    if cfg is None:
        raise DataValidationError(
            "build_all_trade_segments 需要显式传入 cfg（或提供包含 cfg 的 ctx）。"
        )
    execution_policy = execution_policy or getattr(ctx, "execution_policy", None)
    if execution_policy is None:
        raise DataValidationError(
            "build_all_trade_segments 需要显式传入 execution_policy。"
        )
    if not isinstance(selection, SelectionBatch):
        raise DataValidationError("build_all_trade_segments requires SelectionBatch")
    if not isinstance(quote_store, QuoteStore):
        raise DataValidationError("build_all_trade_segments requires QuoteStore")
    if trade_days is None:
        trade_days_idx = quote_store.trade_days
    else:
        trade_days_idx = normalize_trade_days_index(trade_days, name="trade_days")
    if trade_days_idx.empty:
        raise DataValidationError("trade_days 为空，无法生成交易段报告。")

    plans = build_trade_plan(
        selection,
        quote_store,
        trade_days=trade_days_idx,
        execution_policy=execution_policy,
        cfg=cfg,
        include_candidates=False,
    )
    plans_by_signal_row = {plan.signal_row: plan for plan in plans}
    last_day = trade_days_idx.to_numpy(dtype="datetime64[ns]", copy=False)[-1]
    buy_timing = str(getattr(execution_policy, "buy_timing", "")).strip().lower()

    segments: list[pd.DataFrame] = []
    trade_rows: list[dict[str, object]] = []
    report_rows: list[dict[str, object]] = []
    for row in selection.selected_rows:
        position = int(row)
        date = pd.Timestamp(quote_store.index.trade_dates[position]).normalize()
        code = str(quote_store.stock_code_by_row[position]).strip()
        plan = plans_by_signal_row.get(position)
        if plan is None:
            reason = (
                "window_end_no_next_open"
                if buy_timing == "next_open"
                and quote_store.index.trade_dates[position] == last_day
                else "overlap_existing_position"
            )
            report_rows.append(
                {
                    COL_TRADE_DATE: date,
                    COL_STOCK_CODE: code,
                    "status": "skipped",
                    "reason": reason,
                }
            )
            continue
        segment, detail = _segment_frame(plan, quote_store, selection)
        segments.append(segment)
        trade_rows.append(detail)
        report_rows.append(
            {
                COL_TRADE_DATE: date,
                COL_STOCK_CODE: code,
                "status": "ok",
                "reason": "",
                "segment_rows": detail["segment_rows"],
            }
        )

    base_cols = [
        COL_TRADE_DATE,
        COL_STOCK_CODE,
        "open",
        "close",
        SIGNAL_COL_DIRECTION,
        "buy_price",
        "sell_price",
        "trade_return",
    ]
    all_trade_dates = (
        pd.concat(segments, ignore_index=True)
        if segments
        else pd.DataFrame(columns=base_cols)
    )
    if not all_trade_dates.empty:
        all_trade_dates = ensure_trade_date_normalized(
            all_trade_dates[base_cols],
            col=COL_TRADE_DATE,
        )
        all_trade_dates = all_trade_dates.sort_values(
            [COL_TRADE_DATE, COL_STOCK_CODE],
            kind="mergesort",
        ).reset_index(drop=True)

    topn_trade_segments_df = finalize_topn_trade_segments_df(pd.DataFrame(trade_rows))
    topn_trade_segments_summary_df = build_topn_trade_segments_summary_df(
        topn_trade_segments_df
    )
    report_df = pd.DataFrame(report_rows)
    if not report_df.empty:
        report_df = ensure_trade_date_normalized(report_df, col=COL_TRADE_DATE)
        report_df = report_df.sort_values(
            [COL_TRADE_DATE, COL_STOCK_CODE],
            kind="mergesort",
        ).reset_index(drop=True)

    output_dir = str(cfg.paths.signal_data)
    report_path = os.path.join(output_dir, "all_trade_dates_report.csv")
    total = len(report_df)
    ok = int((report_df["status"] == "ok").sum()) if total else 0
    skipped = int((report_df["status"] == "skipped").sum()) if total else 0
    report_loc = f"结果对象(all_trade_dates_report_df) -> {report_path}"
    log_info(
        f"交易段落提取：总数 {total}，成功 {ok}，失败 0，跳过 {skipped}（详情：{report_loc}）"
    )
    log_info("交易信号已写入结果对象 all_trade_dates_df")
    log_info("TopN后交易段汇总已写入结果对象 topn_trade_segments_df")
    if skipped:
        log_warn(
            f"交易段落提取：跳过 {skipped} 条窗口末日或持仓内重复信号（详见报告）。"
        )
    return TradeSegmentsBuildResult(
        all_trade_dates_df=all_trade_dates,
        report_df=report_df,
        topn_trade_segments_df=topn_trade_segments_df,
        topn_trade_segments_summary_df=topn_trade_segments_summary_df,
    )


__all__ = ["build_all_trade_segments"]
