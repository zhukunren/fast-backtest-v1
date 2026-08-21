"""REFERENCE_ONLY: formula-bearing mean-reversion panel example.

Use `strategies/templates/iterative_template.py` as the unique default. This
direct-panel example exists for illustration and regression; its hypothesis,
formula, parameters, and layout are not authoring constraints.
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
    df["ret_5"] = grouped["close"].pct_change(5)
    df["ma20"] = (
        grouped["close"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    )
    df["ma60"] = (
        grouped["close"].rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
    )
    df["vol_20"] = (
        df["ret_1"].groupby(df["stock_code"], sort=False)
        .rolling(20, min_periods=20)
        .std()
        .reset_index(level=0, drop=True)
    )
    df["discount_to_ma20"] = (df["close"] / df["ma20"] - 1.0).replace([np.inf, -np.inf], np.nan)

    rebound_room = (-df["discount_to_ma20"]).clip(lower=0.0, upper=0.25)
    trend_filter = (df["ma20"] / df["ma60"] - 1.0).clip(lower=-0.20, upper=0.20)
    vol_penalty = df["vol_20"].clip(lower=0.0, upper=0.15)
    df["score"] = (rebound_room + 0.3 * trend_filter - 0.5 * vol_penalty).fillna(-1e9).astype(float)

    buy_cond = (
        (df["discount_to_ma20"] <= -0.06)
        & (df["ret_5"] <= -0.03)
        & (df["ma20"] >= df["ma60"] * 0.97)
        & (pd.to_numeric(df["volume"], errors="coerce").fillna(0.0) > 0)
    )
    sell_cond = (df["close"] >= df["ma20"]) | (df["discount_to_ma20"] <= -0.14)
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
