# AI Strategy Contract

Status: optional deep reference

策略模块只从 `strategies.api` 导入公共接口，并导出零参数 `Strategy`。执行模式必须明确：

| 模式 | 必需入口 | 适用运行 |
| --- | --- | --- |
| `panel` | `compute_features(PanelView)`、`compute_signals(PanelView)` | `curve`、`assess` |
| `single` | `build_single_signals(frame)` | `single` |
| `fixed_rebalance` | 无策略信号 hook | 声明式目标权重再平衡 |

## Authoring paths

panel 是 `curve` / `assess` 的唯一执行和作者输入形状：

- 默认 `rule` / `factor`：从 `iterative_template.py` 开始，在完整 panel DataFrame 上实现作者公式；
  `compute_feature_batch()` / `compute_signal_batch()` 各调用一次，并要求 rolling/shift/pct_change
  先按 `stock_code`, `trade_date` 稳定排序、按股票分组。市场 rank/breadth 的 source 列由同一个
  panel 公式产生。
- 专家数组路径：需要更低开销时使用 `scaffold --profile panel`，直接实现
  `compute_features(PanelView) -> FeatureBatch` 和 `compute_signals(PanelView) -> SignalBatch`；所有声明的
  市场 source 列在 feature batch 中产生。两种 profile 都不拆分逐股 DataFrame。

`template.py`、`factor_template.py` 和 `examples/` 是 `REFERENCE_ONLY` 公式型参考，不是默认模板；
其中的公式、日期和章节边界都不限制真实策略实现。

## Panel lifecycle

```text
完整行情 panel
-> 声明的辅助数据
-> compute_features -> FeatureBatch
-> market rank/breadth
-> cross_section_transform_specs
-> compute_signals -> SignalBatch
-> framework finalization / precheck / TopN / broker
```

feature 和 signal batch 中的每个数组都必须与 sealed panel 等长，不得覆盖键、OHLCV 或框架列，
也不得共享可写的基础存储。市场 rank 和 breadth 由完整 panel 计算；策略不要在 stock-local transform 中伪造
横截面结果。策略返回 raw bool 意图，框架统一完成最终信号生成，普通作者不重写该阶段。

## Signal contract

- `buy_cond=True` 只表示进入候选池。
- `sell_cond=True` 只表示退出意图。
- `sort_spec` 只决定同日候选优先级，不是仓位权重。
- 策略不得直接写 `direction`、`buy_signal_raw`、`sell_signal_raw`、`buy_signal`、
  `sell_signal` 或 `next_up_limit_ok`。
- 不得使用未来数据；时间序列运算前按 `stock_code`、`trade_date` 稳定排序。
- 成交、持仓、现金、费用、涨跌停、T+1、组合风控和 NAV 始终归引擎所有。

## Optional capabilities

只有确实需要时才声明 `market_rank_specs`、`market_aggregate_specs`、择时、额外字段、PIT
辅助数据、参考序列或横截面变换。声明能力后，完整 panel 和对应输入缺失会直接失败；不会
静默忽略，也不会切换到其他信号路径。能力注册表是发现入口，不是指标、模型或代码白名单。

```powershell
python tools/codex_strategy.py fields --configured
python tools/codex_strategy.py capabilities --runtime v1 --available-only
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
```

runtime 状态区分“注册表存在该合同”和“标准 V1 能执行该合同”。scaffold 默认拒绝标准 V1 不可用
的能力；`--allow-unavailable-runtime` 只用于 schema 或自备程序化 runtime 工作，不会让标准 session
获得该能力。

## Iteration

固定回测命令和用户目标，修改同一个策略文件，先读取 `summary.json`。无效运行修复合同问题，
有效但未达标继续调整，所有指标达标后立即停止。独立 check、campaign、promotion 和 sealed
validation 不属于默认循环。
