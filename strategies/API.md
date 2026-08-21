# Strategy Authoring API

Status: active

`strategies.api` 是策略模块唯一允许依赖的公共入口。策略不得从
`strategies.base`、`strategies.factory`、`strategies.templates.runtime`、`core`、`data` 或
`trading` 的实现模块导入作者接口。仓库不提供旧路径转发、别名 shim、双轨解析或静默降级。

## 最小策略合同

`execution_mode` 决定引擎入口：`panel` 用于 V1 `curve` / `assess`，`single` 只接受单票 frame，
`fixed_rebalance` 只走声明式目标权重配置。所有 panel 策略都导出零参数 `Strategy`，并实现同一对
公共 hook：

```python
import numpy as np

from strategies.api import BaseStrategy, FeatureBatch, PanelView, SignalBatch


class Strategy(BaseStrategy):
    STRATEGY_META = {
        "strategy_contract_version": "2.0",
        "execution_mode": "panel",
        "feature_scope": "stock",
        "name": "my_daily_strategy",
        "sort_spec": ["-score"],
        "portfolio_num_j": 2,
        "portfolio_num_i": 2,
    }

    def compute_features(self, panel: PanelView) -> FeatureBatch:
        close = panel.series("close")
        momentum = close.groupby(panel.stock_id_by_row, sort=False).pct_change(20)
        return FeatureBatch(
            columns={"momentum": momentum.to_numpy(copy=False)}
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        momentum = np.asarray(panel.column("momentum"), dtype=float)
        return SignalBatch(
            buy_raw=np.asarray(momentum > 0.1, dtype=bool),
            sell_raw=np.asarray(momentum < 0.0, dtype=bool),
            columns={"score": momentum.copy()},
        )
```

普通 `rule` / `factor` 策略以 `strategies/templates/iterative_template.py` 为唯一默认起点。作者在
一次完整 panel DataFrame 上编写 rolling、shift、指标和条件；先按 `stock_code`, `trade_date` 做
稳定排序，再进行 grouped 时序运算并恢复输入行序。`compute_feature_batch()` /
`compute_signal_batch()` 各只调用公式一次。需要更低开销的数组实现时可使用
`scaffold --profile panel`，直接实现上述数组合同。profile 只决定作者组织方式，不限制算法、模型或辅助代码。

策略只返回原始买卖意图，不写 `direction`、最终信号、下一根行情或成交状态。框架统一拥有窗口、
择时、板块、冲突、T+1、可成交预检、TopN、费用、持仓和净值。所有作者合同只从
`strategies.api` 导入；框架实现模块不是稳定作者接口。

`sort_spec` 只能引用策略生成的数值优先级列。所有买入候选行上的排序值必须是非布尔的有限实数；
数字字符串、`NaN` 和正负无穷不会被转换或降级处理。`stock_code` 由框架自动追加为确定性并列键，
不应写进 `sort_spec`。

## 加载生命周期

`load_strategy_descriptor()` 是加载后的唯一事实源：

```text
import module
-> instantiate Strategy
-> parse CompleteStrategySpec once
-> resolve typed parameters once
-> derive hooks and StrategyRequirements
-> publish LoadedStrategyDescriptor
-> runtime preflight
-> execute
```

`LoadedStrategyDescriptor.contract` 持有完整合同。`spec`、`risk_control`、`advanced` 和
`parameters` 是该合同的只读投影；runner、metadata 和 `strategy_spec.json` 使用同一个合同及
`contract_hash`，不会再次从模块拼装配置。

## Panel 阶段

引擎看到的 panel 阶段顺序固定为：

```text
read and normalize price panel
-> inject declared PIT auxiliary data and reference series
-> compute_features(PanelView) -> FeatureBatch
-> inject market rank and breadth columns
-> apply declared cross-section transforms
-> compute_signals(PanelView) -> SignalBatch
-> framework signal finalization, precheck and stable TopN
-> portfolio engine and result projection
```

`FeatureBatch` 的每列和 `SignalBatch.buy_raw/sell_raw` 必须是一维、长度等于 panel 行数的位置对齐
数组。基础键、OHLCV 和框架列不可覆盖，共享可写底层数组会被拒绝。横截面变换由
`cross_section_transform_specs` 声明；不要在单票 frame 中伪造市场排名。

`feature_scope` 是 panel 策略的显式特征合同，默认值为 `stock`：

- `stock`：`compute_features` 只允许使用各股票自身的历史行；迁移门禁会把完整 panel 中的单股特征
  与同一股票的独立输入结果对照，执行单股隔离测试。
- `cross_section`：`compute_features` 可以使用完整 panel 的横截面状态，因此不要求单股输入与完整
  panel 子集相同；它仍必须接受完整 panel 流水线验证，保持输入顺序不变，并满足 prefix causality。

两种 scope 都不能改变键、行数、行序或 OHLCV，也不能绕过 `FeatureBatch` / `SignalBatch` 的位置对齐
和只读合同。`feature_scope` 不创建第二条运行时路径。

rule/factor profile 的完整 panel 公式由 batch helper 一次调用并直接产生对齐列；panel profile 直接读取
`PanelView`。两种 profile 最终都走同一个 `FeatureBatch`、
`SignalBatch`、`SelectionBatch` 和 `QuoteStore` 执行链，不存在第二条运行时路径。

`apply_guardrails(frame, buy_cond, sell_cond, sort_spec)` 只校验条件对齐、boolean-compatible 值和买入
候选排序值，返回 `(buy_raw, sell_raw, frame)`；它不写 `direction` 或任何标准 signal 列。
`compute_signal_batch()` 将该 tuple 投影为对齐的 `SignalBatch`，之后的 finalization 完全属于框架。

三阶段列所有权不可互换：`stock` scope 的 feature 阶段只生成股票内特征，`cross_section` scope
可以生成依赖完整 panel 的横截面状态；声明的 `mf_*` 市场源及框架 rank、aggregate 和
cross-section 输出仍由各自所有者生成。signal 阶段可组合这些列形成分数和 raw bool 条件，但不得回调
feature builder、覆盖框架输出、读取未来 next-open，或写 `direction`、`buy_signal*`、
`sell_signal*`、`next_up_limit_ok`。仓库不保留 `_add_features()`、
`_add_market_source_features()` 别名。`tools/retired_panel_path_gate.py` 和活动策略迁移测试共同阻止这些
旧路径恢复。

`ArtifactPolicy` 不是策略能力，也不能改变策略或组合执行。程序化 `run_backtest()` 调用可选择 `none`
或 `full`：`none` 不物化报告 DataFrame，`full` 在组合完成后投影并写盘；两者共享同一
`SelectionBatch`、`QuoteStore` 和 financial result，任何财务差异都是合同错误。

panel、single、fixed_rebalance 是互斥执行阶段。panel 和 `use_market_features` 策略固定读取
唯一 canonical 的 `parquet` 完整 panel；缺少 panel 或所需 hook 会在执行前失败，不会回退到
另一条路径。single 只解析 `build_single_signals(frame)`，fixed_rebalance 不解析信号 hook。

## 类型化参数

参数 schema 和运行覆盖均写在 `STRATEGY_META`：

```python
from strategies.api import resolve_declared_parameters


STRATEGY_META = {
    "strategy_parameter_specs": {
        "lookback": {
            "type": "int",
            "default": 20,
            "min": 2,
            "max": 500,
            "description": "Rolling lookback",
        }
    },
    "runtime_parameters": {"lookback": 60},
}

PARAMETERS = resolve_declared_parameters(STRATEGY_META)
```

未声明的覆盖、错误类型、越界值和非法 choice 都直接失败。resolved parameters 会进入完整合同和
hash，保证同一源码在不同参数下具有不同、可审计的运行身份。

## PIT 与参考序列

公共工具包括：

- `StrategyDataInputs`
- `point_in_time_join()` / `point_in_time_join_many()`
- `align_reference_series()`
- `apply_declared_data_inputs()`

辅助数据必须使用真实 `available_at`，框架只做向后 as-of 或精确日期连接，并验证输出未使用晚于
信号日的信息。声明 PIT 或 reference 输入会强制要求完整 panel 数据源；程序化 batch 调用通过
`strategy_data_inputs` 提供已加载 frame。当前标准
`main.py` session 没有通用数据 provider，因此 bootstrap 不宣称支持该能力；声明后会在
preflight 显式失败，而不是忽略数据合同。

## 组合高级合同

以下公共类型和校验器已经可用于 schema、scaffold、静态检查及独立工具：

- `PositionPolicySpec`
- `PortfolioConstructionSpec`
- `TargetWeightContext`
- `TargetWeightPlan`
- `validate_target_weights()`
- `build_target_weights` hook 合同

现有 V1 signal engines 尚未消费 `PositionPolicySpec`，配置驱动的 `fixed_rebalance` 也不会调用策略
的 `build_target_weights`。标准 bootstrap 因此对 `position_policy`、`portfolio_construction` 和
`target_weight_decision` 执行 fail-closed preflight。新增执行支持时应使用独立 engine ownership，
不能把策略权重语义隐式塞进现有 `fixed_rebalance`，也不能保留两套解释路径。

## 当前运行能力

| 能力 | 标准 V1 session | 程序化工具 | 行为 |
| --- | --- | --- | --- |
| 单票时序、TopN、择时、市场 rank/breadth | 支持 | 支持 | 现有 signal engines 执行 |
| panel features / panel signals | 支持 | 支持 | 强制完整 panel 和 `execution_mode=panel` |
| cross-section transforms | 支持 | 支持 | 在 panel signals 前执行 |
| typed parameters | 支持 | 支持 | 加载时解析并写入合同 hash |
| PIT auxiliary / reference series | 无通用 provider | 支持传入 `StrategyDataInputs` | 标准 session preflight 拒绝 |
| position / portfolio / target weight | 不支持 | schema 和校验工具可用 | 标准 session preflight 拒绝 |

当前活动清单为 69 个 V1 运行模块：67 panel、1 single、1 fixed-rebalance。能力审计计数为
68 time-series rules、67 panel features、67 panel signals、40 extra price columns、
30 market features、30 market breadth、20 index timing、13 market rank、1 board membership、
1 drawdown risk 和 1 single signal。全部 67 个 panel 模块接受完整流水线、输入顺序不变与 prefix
causality 测试；其中 `feature_scope=stock` 的模块还接受单股特征隔离测试，`cross_section` 模块则验证
完整 panel 横截面合同，不宣称单股隔离。
27 个活动 Web 策略还在有效本地数据窗口经真实 Web execution chain 全部完成。该清单证明现有能力已
迁入唯一运行合同，不表示上表中 fail-closed 的高级能力已获得标准 session provider。

能力注册表描述依赖、输入、hook 和运行前提，不是指标或模型白名单。日线 OHLCV 策略通常已经由
时序特征、市场 rank/breadth、panel、择时、类型化参数和引擎风控覆盖；只有策略确实需要新的
数据所有权或执行语义时，才应增加高级工具。

查询时显式传 `--runtime v1`，使每项记录同时返回当前标准 runtime 的 `status`、缺失能力和原因。
`--available-only` 只隐藏当前 runtime 不可执行的注册项，不代表底层数据一定完整。字段目录与当前
配置请求列分别通过 `fields` 和 `fields --configured` 查看：

```powershell
python tools/codex_strategy.py capabilities --runtime v1 --available-only
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
python tools/codex_strategy.py fields --id <field> --json
python tools/codex_strategy.py fields --configured
```

## 验证入口

```powershell
python tools/codex_strategy.py capabilities --runtime v1 --id <capability> --json
python tools/codex_strategy.py fields --configured
python tools/codex_strategy.py scaffold --name <name> --profile <rule|factor|panel> --runtime v1
python tools/codex_strategy.py backtest --module strategy_library.generated.<name> --start-time <yyyymmdd> --end-time <yyyymmdd>
python tools/codex_strategy.py check --module strategy_library.generated.<name>
python tools/validate_strategy_spec.py strategy_library.generated.<name>
python tools/strategy_static_check.py strategy_library/generated/<name>.py --enforce-independence
```

普通单策略循环直接运行固定的 `backtest` 命令；独立 `check` 和 spec validator 是按需诊断，不是
前置门禁。capability 和 scaffold 同样不是普通 OHLCV 策略的前置步骤。scaffold 默认拒绝标准 V1
不可用的能力；`--allow-unavailable-runtime` 只允许为 schema 或自备程序化 runtime 生成源码，
不会使该模块在标准 V1 session 中变为可执行。

当策略需要进入 Web 库或作为新策略集合交付时，必须启用 `--enforce-independence`。门禁拒绝
完成策略命名空间（`builtin`、`generated`、`validated`、`web.strategy_library`）导入，要求
`STRATEGY_META.independence.signal_family` 唯一且 `research_basis=raw_market_data`，并把目标源码
与仓库中既有 canonical/Web 参考的归一化信号 AST 相似度限制在 0.92 以下；参数改名或阈值微调
不能绕过该规则。研究只允许从原始 OHLCV/amount 重新构造信号逻辑。

`validate_strategy_module()`、`probe_strategy_behavior()` 和 runtime preflight 均从
`strategies.api` 暴露。不存在第二套 spec validator 或可选 loader fallback。
