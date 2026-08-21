from __future__ import annotations

import numpy as np

from strategies.api import BaseStrategy, FeatureBatch, PanelView, SignalBatch


STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "top_n": 1,
    "sort_spec": ["-score"],
    "portfolio_num_j": 1,
    "portfolio_num_i": 1,
    "fixture_holding_period": 5,
}


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META

    def compute_features(self, panel: PanelView) -> FeatureBatch:
        return FeatureBatch(
            columns={"score": np.ones(panel.row_count, dtype=np.float64)}
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        buy = np.zeros(panel.row_count, dtype=bool)
        sell = np.zeros(panel.row_count, dtype=bool)
        if panel.row_count:
            stock_ids = np.asarray(panel.stock_id_by_row, dtype=np.int64)
            starts = np.empty(panel.row_count, dtype=bool)
            starts[0] = True
            if panel.row_count > 1:
                starts[1:] = stock_ids[1:] != stock_ids[:-1]
            buy[starts] = True
            rows = np.arange(panel.row_count, dtype=np.int64)
            start_rows = np.maximum.accumulate(np.where(starts, rows, 0))
            sell[rows - start_rows == int(self.STRATEGY_META["fixture_holding_period"])] = True
        return SignalBatch(buy_raw=buy, sell_raw=sell, columns={})
