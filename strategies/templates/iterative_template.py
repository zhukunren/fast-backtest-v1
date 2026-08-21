"""Codex 单策略迭代的中性可运行接口。

策略只生成因果特征、买卖意图和同日 TopN 候选优先级。Codex 可以自由添加指标、函数、类、
模型和项目公开 API；这里没有 editable zone 或复杂度限制。固定 runtime 负责标准信号、下一根
K 线成交、可交易性、持仓、现金、费用、组合风控和净值。

普通策略只需修改作者 hooks。横截面、择时、额外字段和其他能力仅在策略确实需要时按需查询
capability 或框架源码，不在默认模板中展开。
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


# 最小 metadata 合同：
# - required_fields 记录全部真实输入依赖；基础 OHLCV 之外的源字段还必须同时写入
#   price_columns，后者才会请求运行时加载该列。
# - sort_spec 只声明同日买入候选优先级。候选行上的每个排序值必须是有限实数；bool、NaN、
#   +inf/-inf 和数字字符串都会使本轮无效。
# - top_n 和组合槽位总数由 portfolio_num_j * portfolio_num_i 推导，不要另写 top_n。
# - 横截面、择时、板块、额外字段、风控或 panel 能力只在策略需要时按需查询：
#   python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
#   详细合同见 strategies/API.md 和 strategies/templates/AI_STRATEGY_CONTRACT.md。


STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "name": "codex_iterative_strategy",
    "required_fields": [
        "trade_date",
        "stock_code",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ],
    "sort_spec": ["-candidate_priority"],
    "portfolio_num_j": 1,
    "portfolio_num_i": 1,
    "use_market_features": False,
    "market_rank_specs": [],
    "market_aggregate_specs": [],
    "market_feature_passthrough_cols": [],
    "use_index_timing": False,
    "use_board_timing": False,
    "require_board_member": False,
    "board_index_codes": [],
    "risk_control": {
        "enable_nv": False,
        "enable_dd": False,
    },
}

STRATEGY_SEMANTICS = {
    "strategy_type": "rule",
    "market": "A_share",
    "timeframe": "daily",
}

STRATEGY_DESCRIPTION: dict[str, str] = {}
STRATEGY_LIMITS: dict[str, int] = {}


# 数据合同（完整 panel 公式路径）
#
# 输入 df：
# - sealed panel 的完整日线行情；行已按 stock_code、trade_date 稳定升序排列。
# - 任何 rolling/shift/pct_change 都必须通过 df.groupby("stock_code", sort=False) 计算。
# - index 是无重复的 RangeIndex；不要自行假定其起点，全部返回 Series 必须严格使用实际 df.index。
# - trade_date 是无缺失的 pandas datetime 类型；stock_code 是非空证券代码。
# - open/high/low/close/volume 是无缺失数值列；还可能包含 price_columns 请求的额外行情列。
#
# _compute_panel_signals 返回：
# - buy_cond：bool Series，index 必须完全等于 out.index；True 仅表示进入买入候选。
# - sell_cond：bool Series，index 必须完全等于 out.index；True 仅表示退出意图。
# - out：与输入保持相同行数、trade_date/stock_code 键值、行序和原始 OHLCV，可新增派生列。
# - STRATEGY_META["sort_spec"] 引用的每个列都必须存在且为可排序数值，需显式处理 NaN/inf。
#
# 不要 dropna 删除行情行，不要改写原始 OHLCV，不要直接写标准信号列。公式必须保持完整 panel
# 行数、键和顺序；框架负责最终信号过滤、可成交性和组合执行。


def _compute_panel_signals(
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """生成对齐的买入候选、退出意图和候选优先级；当前中性实现不产生任何信号。"""
    out = df.copy()
    buy_cond = pd.Series(False, index=out.index, dtype=bool)
    sell_cond = pd.Series(False, index=out.index, dtype=bool)
    out["candidate_priority"] = 0.0
    return buy_cond, sell_cond, out


def _compute_panel_features(df: pd.DataFrame) -> pd.DataFrame:
    """返回相同键行和 metadata 声明的市场特征源。

    输入是完整 panel。输出必须保持全部 trade_date/stock_code 键行及顺序，并包含
    market_rank_specs、market_aggregate_specs 和 passthrough 声明的源列。框架随后按位置计算
    横截面排名或按日聚合。未声明市场特征时只返回两个键列即可。
    """
    return df.loc[:, ["trade_date", "stock_code"]].copy()


def build_index_timing_signal(index_df: pd.DataFrame) -> pd.Series:
    """从按日期升序的指数日线返回对齐 bool Series。

    默认字段为 trade_date/open/high/low/close/volume/amount。仅在 use_index_timing=True 时
    生效；False 只过滤新买入，不直接卖出现有持仓。
    """
    return pd.Series(True, index=index_df.index, dtype=bool)


def build_board_timing_signal(board_df: pd.DataFrame) -> pd.Series:
    """从单个板块指数的升序日线返回对齐 bool Series。

    默认字段为 trade_date/open/high/low/close/volume。仅在 use_board_timing=True 时生效；
    False 只过滤该板块的新买入，不直接卖出现有持仓。
    """
    return pd.Series(True, index=board_df.index, dtype=bool)


# 框架装配：回测引擎调用下列入口。普通策略只修改上方作者 hooks，不应在策略逻辑中主动调用、
# 复制或重写这些 wrapper、交易状态机或 guardrail。
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
