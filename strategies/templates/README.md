# Strategy Templates

`iterative_template.py` 是唯一默认作者入口。普通策略只维护一个活动策略文件，固定条件运行回测，
先读 `summary.json`，无效则修复、有效但未达标则继续，全部目标达标后立即停止。capability、scaffold
和独立 draft check 都是按需工具，不是开始策略研究的前置步骤。

## 文件角色

- `iterative_template.py`：中性、可运行的默认起点。作者编辑一次接收完整 panel 的公式，类式公共
  hook 通过 batch helper 返回 panel-native 数组合同。
- `guardrails.py`：固定排序和原始意图校验边界；普通策略不复制或改写该实现。
- `template.py`、`factor_template.py`、`examples/`：标有 `REFERENCE_ONLY` 的公式型参考和回归夹具，
  不是默认起点。文件中的公式、日期和章节边界都不是策略约束。
- `AI_STRATEGY_CONTRACT.md`：需要完整合同细节时再读取。

## 三种作者 profile、一个执行合同

`curve` / `assess` 的执行合同始终是 panel-native：

- `rule` / `factor`：特征、条件和优先级在一次完整 panel DataFrame 上编写；先按
  `stock_code`, `trade_date` 做稳定排序，再用 grouped `rolling`/`shift`/`pct_change`，并恢复
  原始行序。`compute_feature_batch()` / `compute_signal_batch()` 各只调用公式一次。需要市场
  rank/breadth 时，由同一个 panel 公式产生因果源列，框架随后按交易日聚合或排名。
- 专家 full-panel 路径：只在确实需要整市场向量化或跨股票计算时使用 `scaffold --profile panel`，
  直接实现 `compute_features(PanelView) -> FeatureBatch` 和
  `compute_signals(PanelView) -> SignalBatch`。

三种 profile 都可以自由增加因果指标、函数、类、模型和 `strategies.api` 公共工具。profile 决定
作者输入形状和装配方式，不是算法、代码体积或复杂度白名单；最终都进入同一 panel-native 执行链。

## 执行合同

策略必须在 `STRATEGY_META["execution_mode"]` 中明确选择模式：

- `panel`：对外提供 `compute_features(panel)` 和 `compute_signals(panel)`，分别返回
  `FeatureBatch` 和 `SignalBatch`；这是 `curve` / `assess` 的唯一信号入口。
- `single`：提供 `build_single_signals(frame)`，只用于单票运行。
- `fixed_rebalance`：使用声明式目标权重配置；当前 V1 不调用策略 `build_target_weights()` hook。

panel 阶段顺序固定为：

```text
读取并 seal PricePanel -> compute_features -> 声明的市场 rank/breadth ->
横截面变换 -> compute_signals -> 框架 finalization/预检/TopN -> 组合处理
```

标准 panel 公式必须保持输入 frame 的键、行序和原始 OHLCV。panel hook 返回的每个数组必须与 sealed
panel 等长，不得覆盖键、OHLCV 或框架列，也不得共享可写的基础存储。策略只产生 raw buy/sell
意图；窗口、择时、冲突、T+1、可成交预检、TopN 和最终信号均由框架处理。缺少输入或 hook 时直接
失败，不回退到另一条路径。

## 按需发现

```powershell
python tools/codex_strategy.py fields --configured
python tools/codex_strategy.py capabilities --runtime v1 --available-only
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
python tools/codex_strategy.py scaffold --name <name> --profile <rule|factor|panel> --runtime v1
```

只有确实需要横截面、择时、额外字段、PIT 数据或组合扩展时才查询对应能力。标准 V1 不可用的
能力默认不能 scaffold；`--allow-unavailable-runtime` 只用于 schema 或自备程序化 runtime 工作，
生成源码不表示它能在标准 V1 session 回测。
