"""DataFrame projections for reports and remaining portfolio consumers."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from core.constants import (
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_BUY,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
    SIGNAL_COL_SELL_RAW,
)
from core.schema import (
    SIGNAL_NORMALIZED_ATTR,
    SIGNAL_SORTED_DATE_CODE_ATTR,
)

from core.panel_contracts import SelectionBatch
from core.panel_native import ColumnStore, PricePanel, QuoteStore


def project_selection_frame(
    panel: PricePanel,
    store: ColumnStore,
    selection: SelectionBatch,
    rows: np.ndarray | Iterable[int],
) -> pd.DataFrame:
    return project_selection_from_quotes(
        panel.quote_store(store.columns),
        selection,
        rows,
    )


def project_selection_from_quotes(
    quotes: QuoteStore,
    selection: SelectionBatch,
    rows: np.ndarray | Iterable[int],
) -> pd.DataFrame:
    positions = np.asarray(
        list(rows) if not isinstance(rows, np.ndarray) else rows,
        dtype=np.int64,
    )
    columns = quotes.columns
    frame = pd.DataFrame({name: values[positions] for name, values in columns.items()})
    buy = selection.final_buy[positions]
    sell = selection.final_sell[positions]
    frame[SIGNAL_COL_BUY_RAW] = buy
    frame[SIGNAL_COL_SELL_RAW] = sell
    frame[SIGNAL_COL_BUY] = buy
    frame[SIGNAL_COL_SELL] = sell
    direction = np.full(len(frame), "", dtype=object)
    direction[buy] = DIRECTION_BUY
    direction[sell] = DIRECTION_SELL
    frame[SIGNAL_COL_DIRECTION] = direction
    frame.attrs[SIGNAL_NORMALIZED_ATTR] = True
    frame.attrs[SIGNAL_SORTED_DATE_CODE_ATTR] = True
    return frame


def project_daily_summary(
    panel: PricePanel,
    selection: SelectionBatch,
) -> pd.DataFrame:
    rows = selection.diagnostics.get("topn", {}).get("daily_counts", ())
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "signal_cnt", "candidate_cnt", "top_cnt"]
        )
    dates = panel.index.unique_trade_dates
    return pd.DataFrame(
        {
            "trade_date": [pd.Timestamp(dates[int(date_id)]) for date_id, *_ in rows],
            "signal_cnt": [int(signal_count) for _, signal_count, _, _ in rows],
            "candidate_cnt": [int(candidate_count) for _, _, candidate_count, _ in rows],
            "top_cnt": [int(top_count) for _, _, _, top_count in rows],
        }
    )


def build_basic_stock_summaries(
    panel: PricePanel,
    selection: SelectionBatch,
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for stock_id, code in enumerate(panel.index.stock_codes):
        stock_slice = panel.index.stock_slice(stock_id)
        buy_count = int(selection.final_buy[stock_slice].sum())
        if buy_count <= 0:
            continue
        summaries.append(
            {
                "stock_code": code,
                "代码": code,
                "买入信号数": buy_count,
                "卖出信号数": int(selection.final_sell[stock_slice].sum()),
                "_t1_shift_cnt": 0,
                "_t1_shift_fail_cnt": 0,
                "_t_read_sec": 0.0,
                "_t_state_machine_sec": 0.0,
            }
        )
    return summaries


__all__ = [
    "build_basic_stock_summaries",
    "project_daily_summary",
    "project_selection_frame",
    "project_selection_from_quotes",
]
