"""REFERENCE_ONLY: compact formula-bearing OHLCV example.

Use `strategies/templates/iterative_template.py` as the unique default for real
strategies. This file directly wires panel hooks only to demonstrate and regress
the public signal contract; its formula and layout are not defaults to copy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.api import (
    BaseStrategy,
    FeatureBatch,
    PanelView,
    SignalBatch,
    apply_guardrails,
    compute_feature_batch,
    compute_signal_batch,
)


STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "sort_spec": ["-score"],
    "use_market_features": False,
    "use_index_timing": False,
    "use_board_timing": False,
    "require_board_member": False,
    "portfolio_num_j": 2,
    "portfolio_num_i": 2,
    "risk_control": {"enable_nv": False, "enable_dd": False},
}


def _build_panel_frame(
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    df = df.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True).copy()
    grouped = df.groupby("stock_code", sort=False)

    df["ret_1"] = grouped["close"].pct_change(1)
    df["mom_20"] = grouped["close"].pct_change(20)
    df["ma20"] = (
        grouped["close"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    )
    df["ma60"] = (
        grouped["close"].rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
    )
    df["vol_ma20"] = (
        grouped["volume"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    )
    df["vol_ratio"] = (df["volume"] / df["vol_ma20"]).replace([np.inf, -np.inf], np.nan)

    trend = (df["ma20"] / df["ma60"] - 1.0).replace([np.inf, -np.inf], np.nan)
    df["score"] = (trend + df["mom_20"] + 0.2 * (df["vol_ratio"] - 1.0)).fillna(-1e9).astype(float)

    buy_cond = (df["ma20"] > df["ma60"]) & (df["mom_20"] > 0.03) & (df["vol_ratio"] > 1.1)
    sell_cond = (df["ma20"] < df["ma60"]) | (df["mom_20"] < -0.05)
    buy_cond = buy_cond.fillna(False)
    sell_cond = sell_cond.fillna(False)

    return apply_guardrails(df, buy_cond, sell_cond, STRATEGY_META["sort_spec"])


def _compute_panel_features(panel: pd.DataFrame) -> pd.DataFrame:
    return _build_panel_frame(panel)[2]


def _compute_panel_signals(
    panel: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    return _build_panel_frame(panel)


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META
    def compute_features(self, panel: PanelView) -> FeatureBatch:
        return compute_feature_batch(
            panel,
            _compute_panel_features,
            strategy_meta=self.STRATEGY_META,
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        return compute_signal_batch(panel, _compute_panel_signals)
