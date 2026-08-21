"""REFERENCE_ONLY: expert direct full-panel market-rank example.

Use `strategies/templates/iterative_template.py` as the unique default. This
fixture illustrates direct _compute_panel_features/_compute_panel_signals hooks for a
strategy that genuinely needs complete-market work; its factors and thresholds
are not defaults or authoring constraints.
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
    "sort_spec": ["-composite_score", "+mom_rank", "+low_vol_rank", "+volume_rank"],
    "use_market_features": True,
    "market_rank_specs": [
        {
            "feature_col": "mom_factor",
            "rank_col": "mom_rank",
            "pct_col": "mom_pct_rank",
            "count_col": "mom_rank_count",
            "ascending": False,
        },
        {
            "feature_col": "low_vol_factor",
            "rank_col": "low_vol_rank",
            "pct_col": "low_vol_pct_rank",
            "count_col": "low_vol_rank_count",
            "ascending": True,
        },
        {
            "feature_col": "volume_factor",
            "rank_col": "volume_rank",
            "pct_col": "volume_pct_rank",
            "count_col": "volume_rank_count",
            "ascending": False,
        },
    ],
    "market_feature_passthrough_cols": ["mom_20", "vol_20", "vol_ratio"],
    "use_index_timing": False,
    "use_board_timing": False,
    "require_board_member": False,
    "portfolio_num_j": 3,
    "portfolio_num_i": 2,
    "risk_control": {"enable_nv": False, "enable_dd": False},
}

STRATEGY_SEMANTICS = {
    "strategy_type": "factor",
    "holding_period": "medium",
    "market": "A_share",
    "timeframe": "daily",
    "primary_signals": ["cross_section_momentum", "volume"],
    "risk_profile": "balanced",
}

STRATEGY_DESCRIPTION = {
    "entry": "动量、低波和量能三组横截面排名同时靠前时进入候选池。",
    "exit": "任一核心横截面排名显著转弱或动量转负时退出候选。",
    "ranking": "按 composite_score 降序，并以三组 rank 升序作为稳定次级排序参与 TopN 轮动。",
    "market_features": "完整 panel 公式生成 mom_factor/low_vol_factor/volume_factor，框架按 market_rank_specs 注入三组 rank。",
    "risk": "成交、T+1、涨跌停/停牌/无量过滤、交易成本和风控由回测引擎处理。",
}

# Optional research notes only. Codex-authored strategies have no default
# indicator, condition, rolling-call, code-size, or complexity ceiling.
STRATEGY_LIMITS = {}


def compute_rank_source_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True).copy()
    grouped = df.groupby("stock_code", sort=False)
    df["mom_20"] = grouped["close"].pct_change(20)
    df["mom_60"] = grouped["close"].pct_change(60)
    df["ret_1"] = grouped["close"].pct_change(1)
    df["vol_20"] = grouped["ret_1"].transform(lambda s: s.rolling(20, min_periods=20).std())
    df["vol_ma20"] = grouped["volume"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["vol_ratio"] = (df["volume"] / df["vol_ma20"]).replace([np.inf, -np.inf], np.nan)
    df["mom_factor"] = (0.6 * df["mom_20"] + 0.4 * df["mom_60"]).replace([np.inf, -np.inf], np.nan)
    df["low_vol_factor"] = df["vol_20"].replace([np.inf, -np.inf], np.nan)
    df["volume_factor"] = df["vol_ratio"].replace([np.inf, -np.inf], np.nan)
    return df


def _compute_panel_features(panel: pd.DataFrame) -> pd.DataFrame:
    return compute_rank_source_features(panel.copy())


def _pct_rank(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(1.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(1.0).clip(lower=0.0, upper=1.0)


def _compute_panel_signals(
    panel: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    df = panel.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True).copy()
    required = {
        "mom_20",
        "mom_factor",
        "mom_rank",
        "mom_pct_rank",
        "low_vol_rank",
        "low_vol_pct_rank",
        "volume_rank",
        "volume_pct_rank",
    }
    missing = sorted(required - set(df.columns), key=str.casefold)
    if missing:
        raise ValueError(f"market-rank signal stage is missing columns: {missing}")

    mom_pct = _pct_rank(df, "mom_pct_rank")
    low_vol_pct = _pct_rank(df, "low_vol_pct_rank")
    volume_pct = _pct_rank(df, "volume_pct_rank")
    df["composite_score"] = (
        0.45 * (1.0 - mom_pct)
        + 0.35 * (1.0 - low_vol_pct)
        + 0.20 * (1.0 - volume_pct)
    ).astype(float)

    buy_cond = (
        (mom_pct <= 0.25)
        & (low_vol_pct <= 0.40)
        & (volume_pct <= 0.45)
        & (df["mom_20"] > 0.02)
    )
    sell_cond = (
        (mom_pct > 0.60)
        | (low_vol_pct > 0.75)
        | (volume_pct > 0.80)
        | (df["mom_20"] < -0.05)
    )
    buy_cond = buy_cond.fillna(False)
    sell_cond = sell_cond.fillna(False)

    return apply_guardrails(df, buy_cond, sell_cond, STRATEGY_META["sort_spec"])


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META
    STRATEGY_SEMANTICS = STRATEGY_SEMANTICS
    STRATEGY_DESCRIPTION = STRATEGY_DESCRIPTION
    STRATEGY_LIMITS = STRATEGY_LIMITS
    def compute_features(self, panel: PanelView) -> FeatureBatch:
        return compute_feature_batch(
            panel,
            _compute_panel_features,
            strategy_meta=self.STRATEGY_META,
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        return compute_signal_batch(panel, _compute_panel_signals)
