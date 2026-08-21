from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.api import BaseStrategy, FeatureBatch, PanelView, SignalBatch


STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "top_n": 10,
    "sort_spec": ["-score"],
    "portfolio_num_j": 5,
    "portfolio_num_i": 2,
}


def _group_rolling(values: pd.Series, groups: np.ndarray, window: int, operation: str):
    return values.groupby(groups, sort=False).transform(
        lambda values: getattr(values.rolling(window, min_periods=window), operation)()
    )


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META

    def compute_features(self, panel: PanelView) -> FeatureBatch:
        groups = panel.stock_id_by_row
        close = pd.to_numeric(panel.series("close"), errors="raise")
        volume = pd.to_numeric(panel.series("volume"), errors="raise")
        prior_high_20 = _group_rolling(close, groups, 20, "max").groupby(
            groups, sort=False
        ).shift(1)
        prior_low_10 = _group_rolling(close, groups, 10, "min").groupby(
            groups, sort=False
        ).shift(1)
        ma20 = _group_rolling(close, groups, 20, "mean")
        avg_volume_20 = _group_rolling(volume, groups, 20, "mean")
        momentum_5 = close.groupby(groups, sort=False).pct_change(5)
        breakout_strength = (close / prior_high_20 - 1.0).replace(
            [np.inf, -np.inf], np.nan
        )
        liquidity = (volume / avg_volume_20).replace([np.inf, -np.inf], np.nan)
        return FeatureBatch(
            columns={
                "prior_high_20": prior_high_20.to_numpy(copy=False),
                "prior_low_10": prior_low_10.to_numpy(copy=False),
                "ma20": ma20.to_numpy(copy=False),
                "avg_volume_20": avg_volume_20.to_numpy(copy=False),
                "momentum_5": momentum_5.to_numpy(copy=False),
                "breakout_strength": breakout_strength.to_numpy(copy=False),
                "liquidity": liquidity.to_numpy(copy=False),
            }
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        close = pd.to_numeric(panel.series("close"), errors="raise")
        volume = pd.to_numeric(panel.series("volume"), errors="raise")
        prior_high_20 = panel.series("prior_high_20")
        prior_low_10 = panel.series("prior_low_10")
        ma20 = panel.series("ma20")
        avg_volume_20 = panel.series("avg_volume_20")
        buy = (close > prior_high_20) & (close >= ma20) & (
            volume >= avg_volume_20.fillna(0.0)
        )
        sell = (close < prior_low_10) | (close < ma20)
        score = (
            panel.series("breakout_strength").fillna(-1.0) * 0.6
            + panel.series("momentum_5").fillna(-1.0) * 0.3
            + panel.series("liquidity").fillna(0.0).clip(lower=0.0, upper=5.0) * 0.1
        ).astype(float)
        return SignalBatch(
            buy_raw=buy.fillna(False).to_numpy(dtype=bool, copy=False),
            sell_raw=sell.fillna(False).to_numpy(dtype=bool, copy=False),
            columns={"score": score.to_numpy(copy=False)},
        )
