# 策略验真

本文档说明如何使用统一的策略验真入口，对当前策略模块跑一组固定 case，并生成可落盘的正式报告。

## 目标

- 用同一份协议定义同引擎多窗口、成本压力和可选跨引擎观测 case
- 将“是否可晋级到 `strategy_library.validated`”收口为显式 gate
- 输出可归档的 JSON/Markdown 双报告
- 区分可重复 diagnostic 与一次性 sealed holdout，避免把反复查看的窗口误称为 OOS

## 入口

```powershell
python tools/codex_strategy.py validate --module strategy_library.generated.<name>
```

Codex 是唯一主动 agent，`tools/codex_strategy.py` 是 canonical 入口。它在可信本地独立 worker
中调用底层 `tools/validate_strategy.py`；后者保留为确定性实现和兼容入口，不负责研究决策。

默认行为：

- Codex 主入口要求显式 `--module`；底层兼容工具 `tools/validate_strategy.py` 仍可默认取当前 `config` 的 `[strategy].module`
- `--protocol` 默认取 `docs/validation/strategy_validation_protocol_template.json`
- 协议模板默认 `promotion_target=strategy_library.validated`
- 报告默认输出到 `output/strategy_validation/<run_id>_<strategy_slug>/`
- 默认协议的 full-window case 从 `20240102` 开始，和当前 Parquet panel manifest 的最早交易日对齐。

可选参数：

```powershell
python tools/codex_strategy.py validate `
  --module strategy_library.generated.focus_medium_frontier_i6 `
  --protocol docs/validation/strategy_validation_protocol_template.json `
  --output-root output/strategy_validation/manual_run_focus_medium_frontier_i6
```

返回码：

- `0`：所有 required case 完成，且所有 hard gate 通过
- `1`：存在 required case 失败，或存在 hard gate 未通过

## 输出结构

```text
output/strategy_validation/<run_id>_<strategy_slug>/
  protocol_resolved.json
  validation_report.json
  validation_report.md
  cases/
    base_full/
      logs/
      run_meta/
      signal_data/
      stock_data/
      trade_data/
      return_date/
      return_date_with_returns/
      net_value_unique_date/
```

说明：

- 每个 case 都使用独立输出目录，避免污染主 `output/`
- `run_meta/<case_run_id>.json` 仍是原回测主链生成的元信息
- 诊断文件仍由 `app/diagnostics_service.py` 生成
- 策略模块在每个新 session 中重新加载；环境解析后的参数写入 `StrategySpec.runtime_parameters`，用于复现实验而不是依赖进程首次 import 的隐式值
- 报告身份包含策略源码、协议、case resolved config、数据和 Git 标识；promotion 会重新核对源码 hash

## 协议格式

协议模板见：

- `docs/validation/strategy_validation_protocol_template.json`

顶层字段：

- `version`
- `description`
- `promotion_target`
- `research_governance`
- `defaults.overrides`
- `cases`
- `gates`

`research_governance.role` 的常用值：

- `diagnostic`：公开、可重复使用的诊断协议；默认模板使用该角色。
- `sealed_holdout`：冻结 shortlist 后一次性打开的确认协议；必须绑定 campaign 和 candidate。

### case 字段

- `name`
- `description`
- `start_time`
- `end_time`
- `gate_required`
- `overrides`

### gate 字段

- `name`
- `kind`
  - `threshold`
  - `max_relative_drop`
  - `min_fraction_pass`
  - `worst_case_threshold`
- `case`
- `path`
- `value`
- `op`：仅 `threshold` 使用
- `base_case`：仅 `max_relative_drop` 使用
- `min_fraction`：仅 `min_fraction_pass` 使用
- `level`
  - `hard`
  - `warn`
- `description`

`min_fraction_pass` 和 `worst_case_threshold` 的 `case` 支持前缀通配，例如 `wf_*`，用于 rolling
/ walk-forward case 组。只有候选和选择规则在这些窗口首次打开前已经冻结，才可把它们解释为
OOS；反复用于调参后只能称为 diagnostic。

## 路径语义

`gate.path` 基于单个 case 的结果对象解析，常用路径示例：

- `result.cum_return`
- `result.max_drawdown`
- `result.actual_segment_trade_count`
- `result.actual_segment_win_rate`
- `diagnostics.concentration.top1_slot_profit_share`
- `diagnostics.concentration.trade_return_hhi`
- `diagnostics.multi_start.robust_score`
- `diagnostics.capacity.p95_participation_rate`
- `diagnostics.capacity.impact_cost_bps_estimate`
- `diagnostics.capacity.exposure.max_single_name_weight`

`max_relative_drop` 会用同一路径同时读取：

- 当前 `case`
- `base_case`

然后计算：

```text
max(0, (base_value - actual_value) / abs(base_value))
```

`min_fraction_pass` 会对所有匹配 case 逐个执行 `op/value`，再要求通过比例不低于
`min_fraction`。`worst_case_threshold` 会取匹配 case 中最差值：`>=`/`>` 使用最小值，
`<=`/`<` 使用最大值。

## Rolling / Walk-forward 协议生成

可用工具生成 rolling / walk-forward 协议：

```powershell
python tools/build_validation_protocol.py `
  --start 20240102 `
  --end 20260630 `
  --train-days 250 `
  --test-days 60 `
  --step-days 60 `
  --out docs/validation/generated_walk_forward.json
```

生成后继续使用统一入口：

```powershell
python tools/codex_strategy.py validate `
  --module strategy_library.generated.<name> `
  --protocol docs/validation/generated_walk_forward.json
```

协议生成器只负责窗口划分，不会自动赋予 OOS 或 sealed 身份。研究者已经看过的窗口、用于
候选筛选的窗口和反复运行的窗口都属于 diagnostic。

## 默认协议说明

模板当前包含 5 个 case：

- `base_full`
- `diagnostic_first_half`
- `diagnostic_second_half`
- `cost_stress_full`
- `broker_replay_full`

其中：

- `diagnostic_first_half` 和 `diagnostic_second_half` 可重复用于稳定性诊断，但不叫 OOS，也不能作为 sealed holdout 证据
- `broker_replay_full` 仅作可选跨引擎观测，不参与 required case 阻断
- `base_full` 开启 `multi_start`
- `cost_stress_full` 开启交易成本压力
- 容量/冲击成本 gate 目前为 `warn`，读取 `diagnostics.capacity.*`，不改变成交价格

## 引擎比较合同

`legacy` 与 `broker` 是两套独立、受支持的 V1 引擎，结果不要求一致。协议内的相对 gate、
候选 A/B 比较和晋级排序必须使用相同：

- `portfolio.engine`
- protocol 内容和 `protocol_sha256`
- resolved config
- data identity
- 时间窗口

`broker_replay_full` 改变 engine，因此只能观察另一种成交/账务口径。它不是 parity test，不能把
与 `base_full` 的收益、回撤或交易数差异当作失败。若要比较两个候选的 broker 结果，应让两个
候选都运行同一份 broker protocol，再在该 protocol 内比较。

## Campaign 与 Sealed Holdout

当前实现的强制边界必须先区分清楚：campaign 数据库绑定入口策略文件 SHA-256 和 sealed protocol
语义 SHA-256，限制候选数、freeze 后新增候选和每个冻结候选的重复 claim。它尚未绑定入口文件的
传递依赖、Git tree、discovery resolved config、评价窗口、数据 manifest、股票池、engine、任务或目标，也不
跨 attempt 比较这些身份。因此当前能力是受限治理状态机，不是完整假设冻结；在联合候选身份门禁
落地前，不得据此声称正式策略确认。

完整命令顺序见 `docs/agent/CODEX_ONLY_WORKFLOW.md`。治理顺序固定为：

```text
预注册 max_candidates / familywise_alpha
-> discovery 与 reusable diagnostics
-> 冻结 shortlist 和候选源码 hash
-> 每个冻结候选一次 sealed holdout claim
-> 记录结论，不再按 holdout 结果调参
```

Campaign 主入口：

```powershell
$sealedProtocolSha = (python tools/codex_campaign.py protocol-hash --protocol path\to\sealed_protocol.json | ConvertFrom-Json).protocol_sha256
python tools/codex_campaign.py init --campaign-id demo_20260714 --max-candidates 20 --sealed-protocol-sha256 $sealedProtocolSha --familywise-alpha 0.05
python tools/codex_campaign.py freeze --campaign-id demo_20260714 --candidate-id c001 --candidate-id c007
python tools/codex_campaign.py status --campaign-id demo_20260714
```

sealed protocol 的 SHA-256 必须在 campaign 初始化、任何候选登记之前预注册。
该值必须由 `tools/codex_campaign.py protocol-hash` 计算规范化协议语义 hash，不能用文件原始字节 hash 替代。
协议必须使用 `research_governance.role=sealed_holdout`，并在执行时传入冻结的
`--campaign-id` 与 `--candidate-id`。`--promotion` 本身不会赋予 sealed 身份。系统会绑定入口
source SHA-256 和 protocol SHA-256；同一 campaign 内每个冻结候选只能 claim 一次，入口文件在
freeze 后变化也会拒绝执行。调用方必须核对报告中的 `holdout_claimed=true`，且不能把未覆盖的
import 依赖误写为已冻结源码。

sealed holdout 会追加 hard Bonferroni gate：

```text
corrected_alpha = familywise_alpha / max_candidates
```

修正分母使用 campaign 开始时预注册的最大入口源码候选数，不使用最终 shortlist 数量，也不能在
看过结果后缩小。服务读取 required case 的单侧正均值 `positive_mean_p_value`，取最差值并要求
`p_value <= corrected_alpha`。当前 p-value 使用日收益样本标准差和正态近似，未处理序列相关；
当前分母也未覆盖配置、数据、引擎或依赖变化形成的新假设。因此这一 gate 只验证代码路径和状态机，
不能声称有效控制真实研究的 family-wise error。正式使用前必须实现联合候选身份，并预注册
HAC/Newey-West 或 block bootstrap 等经校准的时间序列检验。统计门槛仍不替代收益、回撤、成交数、
容量、机制合理性和人工晋级审核。

## 报告判定

最终结论由两部分共同决定：

1. 所有 `gate_required=true` 的 case 必须成功完成
2. 所有 `level=hard` 的 gate 必须通过

`warn` gate 只进入报告，不阻断结论。

报告还包含：

- `preflight`：策略静态与 generated-source 安全检查。
- `identity`：策略源码、protocol、数据和每个 case resolved config 的 hash/identity。
- `research_governance`：diagnostic/sealed 角色、campaign 状态、候选预算、holdout claim 和 multiplicity gate。

晋级还要求 Git commit 身份存在且工作树干净，并且数据存在已验证的内容或快照 hash。接受层必须
显式检查这些条件；不能只依赖 `validate --promotion` 返回码，因为 Git dirty 在单次 validation
audit 中可能只是 warning。
当前验真不能消除历史股票池幸存者偏差：
历史上市/退市、历史名称/ST 和板块归属时点数据未补齐前，该风险必须作为报告限制保留。

## 实现边界

当前实现已按职责拆分：

- `app/strategy_validation_service.py`
  - 保留薄 orchestration 入口，供 `tools/validate_strategy.py` 调用
- `app/strategy_validation/models.py`
  - 协议 dataclass 定义
- `app/strategy_validation/protocol.py`
  - 协议 JSON loader / serializer
- `app/strategy_validation/runner.py`
  - 单 case 执行、路径覆盖与结果序列化
- `app/strategy_validation/gates.py`
  - gate 计算与最终 verdict 汇总
- `app/strategy_validation/reporting.py`
  - JSON/Markdown 报告写盘

这样做的目的：

- `StrategyValidationService` 保持稳定入口，不再堆积协议解析、gate 计算和 Markdown 渲染细节
- 单元测试可直接覆盖协议 loader、gate 计算与报告渲染，而不必都绕过 service 私有方法
- 后续如果增加新 gate kind、报告格式或协议版本，可以优先落在子模块，而不是继续膨胀单文件 service

## 实现位置

- `app/strategy_validation_service.py`
- `app/strategy_validation/`
- `tools/validate_strategy.py`
- `docs/validation/strategy_validation_protocol_template.json`
