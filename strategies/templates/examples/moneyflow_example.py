"""REFERENCE_ONLY: formula-bearing money-flow field example.

Use `strategies/templates/iterative_template.py` as the unique default. This
direct-panel fixture illustrates optional field declarations; its formula,
parameters, and layout are not authoring constraints. Inspect configured fields
with `python tools/codex_strategy.py fields --configured` when needed.
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
    "required_fields": [
        "trade_date",
        "stock_code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "net_mf_amount",
    ],
    "price_columns": ["amount", "net_mf_amount"],
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

    df["ret_5"] = grouped["close"].pct_change(5)
    df["ma20"] = (
        grouped["close"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    )
    df["amount"] = pd.to_numeric(df["amount"], errors="raise").fillna(0.0)
    df["net_mf_amount"] = pd.to_numeric(
        df["net_mf_amount"], errors="raise"
    ).fillna(0.0)

    amount_wan = (df["amount"] / 10.0).replace(0.0, np.nan)
    df["net_mf_rate"] = (df["net_mf_amount"] / amount_wan * 100.0).replace(
        [np.inf, -np.inf], np.nan
    )
    df["net_mf_amount_ma5"] = (
        df["net_mf_amount"].groupby(df["stock_code"], sort=False)
        .rolling(5, min_periods=5)
        .mean()
        .reset_index(level=0, drop=True)
    )
    df["net_mf_rate_ma5"] = (
        df["net_mf_rate"].groupby(df["stock_code"], sort=False)
        .rolling(5, min_periods=5)
        .mean()
        .reset_index(level=0, drop=True)
    )

    raw_score = (
        0.8 * df["ret_5"]
        + 0.002 * df["net_mf_rate_ma5"]
        + 0.00001 * df["net_mf_amount_ma5"]
    )
    df["score"] = raw_score.replace([np.inf, -np.inf], np.nan).fillna(-1e9).astype(float)

    buy_cond = (df["close"] > df["ma20"]) & (df["ret_5"] > 0) & (df["net_mf_rate_ma5"] > 0)
    sell_cond = (df["close"] < df["ma20"]) | (df["net_mf_rate_ma5"] < -2)
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
