# Iterative Strategy Template

`iterative_template.py` 是唯一默认策略起点。`curve` / `assess` 和默认作者接口都按完整 panel
执行：策略作者实现因果特征、买卖意图和候选优先级，batch helper 负责一次性投影为
panel-native 数组，框架负责最终信号。作者可以自由增加指标、函数、类、模型和 `strategies.api` 公共调用；没有
editable zone、指标数量、代码体积或复杂度上限。

## 默认 rule/factor 路径

```python
STRATEGY_META = {
    "execution_mode": "panel",
    "sort_spec": ["-candidate_priority"],
}

def build_strategy_signals(df: pd.DataFrame):
    # df 是完整 panel；公式必须先按 stock_code/trade_date 稳定排序。
    # rolling/shift/pct_change 通过 df.groupby("stock_code", sort=False) 计算。
    out = df.copy()
    buy_cond = pd.Series(False, index=out.index, dtype=bool)
    sell_cond = pd.Series(False, index=out.index, dtype=bool)
    out["candidate_priority"] = 0.0
    return buy_cond, sell_cond, out
```

普通策略只修改作者 hook，不复制 wrapper、guardrail、交易状态机或组合逻辑。需要 market
rank/breadth 时，同一个完整 panel 公式产生声明的源列。框架取得完整市场
位置视图后计算
同日排名或聚合。

## 专家 full-panel 路径

只有策略确实需要整市场向量化或跨股票计算时，才使用
`python tools/codex_strategy.py scaffold --profile panel --runtime v1 ...` 并直接实现：

```python
def compute_features(panel: PanelView) -> FeatureBatch:
    # 每个输出数组与 sealed panel 等长；所有市场 source 列也在这里生成。
    return FeatureBatch(columns={"score": panel.column("close").copy()})

def compute_signals(panel: PanelView) -> SignalBatch:
    return SignalBatch(
        buy_raw=np.zeros(panel.row_count, dtype=bool),
        sell_raw=np.zeros(panel.row_count, dtype=bool),
        columns={"score": panel.column("score").copy()},
    )
```

直接 full-panel hooks 必须返回与 panel 行位置对齐的数组，不得覆盖键和 OHLCV。所有
`shift`、`rolling`、`pct_change` 使用 stock id 或 stock_code 分组保持股票内日期顺序。成交、持仓、
现金、费用、涨跌停、T+1、TopN、风险和 NAV 由引擎处理。

需要单票运行时，显式改为 `execution_mode="single"` 并提供 `build_single_signals(frame)`；
它不是 panel 模式的隐式回退。`fixed_rebalance` 只读取声明式目标权重配置；当前 V1 不调用策略
`build_target_weights()` hook。

`template.py`、`factor_template.py` 和 `examples/` 都是 `REFERENCE_ONLY` 公式型参考。不要把其中
的公式、固定日期或章节边界当作默认策略结构。

## 按需工具

```powershell
python tools/codex_strategy.py fields --configured
python tools/codex_strategy.py capabilities --runtime v1 --available-only
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
```

普通 OHLCV 策略无需先运行这些命令。`capabilities` 的 runtime 状态用于区分“合同已定义”和
“标准 V1 可执行”；`fields --configured` 显示当前配置请求的行情列，不承诺底层数据每行都非空。

## Iteration Loop

固定日期、数据、股票池、引擎、费用和指标口径，反复运行同一个 backtest 命令。先读取
`summary.json`；运行无效就修复同一个文件，有效但未达标就继续，所有目标达标后立即停止。
