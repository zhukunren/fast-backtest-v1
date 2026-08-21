"""REFERENCE_ONLY: formula-bearing factor strategy and runtime regression fixture.

Use `strategies/templates/iterative_template.py` as the unique default authoring
entrypoint. The momentum formula, fixed validation dates, metadata, and section
layout below are examples, not constraints or defaults to copy. Use optional
`scaffold --profile factor --runtime v1` only when generated structure is useful.
"""
from __future__ import annotations

import pandas as pd

from strategies.api import (
    BaseStrategy,
    FeatureBatch,
    PanelView,
    SignalBatch,
    build_board_time_ok as _build_board_time_ok,
    build_index_time_ok,
    compute_feature_batch,
    compute_signal_batch,
)


# 因子策略模板：通过因子值排序持续持有TopN，无需显式买卖条件

STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "name": "template_causal_momentum_factor_topn",
    "hypothesis": "Causally normalized medium-term momentum can rank same-day TopN candidates.",
    "required_fields": ["trade_date", "stock_code", "open", "high", "low", "close", "volume"],
    "universe_assumption": {
        "source": "runtime",
        "locked": False,
        "notes": "No hard-coded symbols; the runner supplies the actual UniverseSpec.",
    },
    "validation": {
        "train_period": ["2020-01-01", "2023-12-31"],
        "test_period": ["2024-01-01", "2025-12-31"],
    },
    "expected_market_regimes": ["trend", "risk_on"],
    "sort_spec": ["+factor_rank", "-factor_value"],
    "use_market_features": True,  # 因子策略启用市场特征预计算/注入层
    "market_rank_specs": [
        {
            "feature_col": "factor_value",
            "rank_col": "factor_rank",
            "pct_col": "factor_pct_rank",
            "count_col": "factor_rank_count",
            "ascending": False,
        },
    ],
    "index_code": "399006.SZ",
    "use_index_timing": False,
    "use_board_timing": False,
    "portfolio_num_j": 10,
    "portfolio_num_i": 1,
    "risk_control": {"enable_nv": False, "enable_dd": False},
}

STRATEGY_SEMANTICS = {
    "strategy_type": "factor",
    "holding_period": "medium",
    "market": "A_share",
    "timeframe": "daily",
    "primary_signals": ["momentum"],
    "risk_profile": "balanced",
}

STRATEGY_DESCRIPTION = {
    "entry": "所有具有有效 factor_value 的股票进入候选池。",
    "exit": "策略不主动生成因子外退出条件，由 TopN 轮动调仓移出弱势候选。",
    "ranking": "按 factor_rank 升序、factor_value 降序参与 TopN 轮动。",
    "market_features": "完整 panel 公式生成 factor_value，框架按 market_rank_specs 注入 factor_rank/factor_pct_rank/factor_rank_count。",
    "risk": "成交、T+1、涨跌停/停牌/无量过滤、交易成本和风控由回测引擎处理。",
}

# Optional advisory research notes; no coding-complexity ceiling is imposed.
STRATEGY_LIMITS = {}


def build_index_timing_signal(index_df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=index_df.index)


def build_board_timing_signal(board_df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=board_df.index)


def _add_factor_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    grouped = df.groupby("stock_code", sort=False)
    # 示例：20日动量因子
    df["mom_20"] = grouped["close"].pct_change(20)

    # 仅使用截至当日的滚动分布，避免全样本统计把未来数据带入历史因子。
    rolling_mean = (
        df["mom_20"].groupby(df["stock_code"], sort=False)
        .rolling(250, min_periods=60)
        .mean()
        .reset_index(level=0, drop=True)
    )
    rolling_std = (
        df["mom_20"].groupby(df["stock_code"], sort=False)
        .rolling(250, min_periods=60)
        .std()
        .reset_index(level=0, drop=True)
    )
    clipped = df["mom_20"].clip(
        lower=rolling_mean - 3.0 * rolling_std,
        upper=rolling_mean + 3.0 * rolling_std,
    )
    df["factor_value"] = (clipped - rolling_mean) / rolling_std.where(rolling_std > 0.0)
    return df


def _compute_panel_features(df: pd.DataFrame) -> pd.DataFrame:
    df = _add_factor_features(df)
    return df.loc[:, ["trade_date", "stock_code", "factor_value"]].copy()


def _compute_panel_signals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    if "factor_value" not in df.columns:
        raise ValueError("factor signal stage is missing feature column: 'factor_value'")
    buy_cond = df["factor_value"].notna().fillna(False)
    sell_cond = pd.Series(False, index=df.index)
    return buy_cond, sell_cond, df


def build_time_ok(stock_code: str) -> pd.DataFrame:
    return build_index_time_ok(
        strategy_meta=STRATEGY_META,
        build_index_timing_signal=build_index_timing_signal,
        stock_code=stock_code,
    )


def build_board_time_ok(board_code: str) -> pd.DataFrame:
    return _build_board_time_ok(
        strategy_meta=STRATEGY_META,
        build_board_timing_signal=build_board_timing_signal,
        board_code=board_code,
    )


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
    build_time_ok = staticmethod(build_time_ok)
    build_board_time_ok = staticmethod(build_board_time_ok)
