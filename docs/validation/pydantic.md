# Pydantic 数据验证

Pydantic 行级校验用于数据审计和问题排查，不属于默认热路径。

## 开关

```powershell
$env:PYDANTIC_VALIDATE="1"
$env:PYDANTIC_VALIDATE_SAMPLE="200"
$env:TUSHARE_TOKEN="..."
python tests/test_offline_smoke.py
```

或在 `config/config.ini`：

```ini
[run]
pydantic_validate = 1
pydantic_validate_sample = 200
```

说明：

- `pydantic_validate=0` 是默认值，避免批量回测为逐行校验支付成本。
- `pydantic_validate_sample <= 0` 表示全量校验。
- 推荐用于新数据源接入、脏数据复现、CI 审计和本地排查。

## 审计入口

```powershell
python tools/data_quality_audit.py --target offline-smoke
python tools/data_quality_audit.py --target local-source
python tools/data_quality_audit.py --target panel-native-storage
python tools/data_quality_audit.py --target panel-manifest --input data/panel_cache/stock_daily.parquet
python tools/data_quality_audit.py --target point-in-time --input output/features.csv --available-at-col available_at
python tools/run_curated_regression.py --group gate
```

## 当前接入点

- `core.dataframe_schema.normalize_signal_frame()` -> `SignalRecord`
- `core.dataframe_schema.normalize_price_frame()` -> `PriceRecord`
- 交易记录 -> `TradeRecord`
- 回测指标 -> `BacktestMetrics`
- 未来外部验证报告 -> `core.external_validation.ExternalValidationReport`；该模型始终严格校验，
  不受 `PYDANTIC_VALIDATE` 抽样开关影响。

## 边界

- `config/coverage-groups-v1.json` 的六组 statement/branch/combined 棘轮和
  `mypy-v1-progressive.ini` 的渐进类型检查属于工程门禁，不受 `PYDANTIC_VALIDATE` 开关影响。
  coverage inventory 必须对 tracked 产品 Python 文件唯一归组或具名排除；全局 combined 下一目标
  保持 82%，不能用降低现有 75% 门槛替代分组改善。2026-08-07 overall 实测
  statement/branch/combined 为 `85.48 / 70.52 / 81.68`，活动 overall floor 为
  `84.70 / 70.30 / 81.05`；策略组实测 `83.98 / 65.30 / 79.34`，floor 为
  `83.80 / 65.10 / 79.10`。manifest floor 必须与 `tools/coverage_grouping.py` 的独立 V1 精确合同及
  回归测试锚点一致，单独回退任一侧都会失败。
- `docs/agent/CODEX_ONLY_WORKFLOW.md` 中的 campaign、shortlist freeze、sealed holdout、
  Bonferroni 和 engine/protocol 可比性同样属于显式正式研究治理，不属于普通策略生产或 Pydantic
  行级校验。Codex 是唯一主动 agent；默认生产只调用 `tools/codex_strategy.py backtest`，
  `tools/codex_campaign.py` 仅在用户明确要求正式验证时进入。
  sealed protocol 语义 hash 必须在候选 discovery 前预注册，这一门禁也不受
  `PYDANTIC_VALIDATE` 开关影响。当前 campaign 只强制绑定入口策略文件与 sealed protocol hash，
  尚未把依赖闭包、Git tree、resolved config、评价窗口、数据、股票池、engine、任务和目标绑定为联合候选身份；
  runtime 报告中的这些 identity 也未由 campaign 跨 attempt 强制比较。当前 Bonferroni gate 使用的
  日收益正态近似未校准序列相关，因此只能作为治理状态机原型，不能单独作为正式确认依据。
- 配置级语义校验不依赖 Pydantic 开关。`exec_buy_timing=signal_close` 需要显式声明
  `signal_available_at=before_close`，否则会被 `core.config_validator.validate_config()` 拒绝。
  application 和批量工具会把 `backtest.engines.ENGINE_REGISTRY` 的只读 catalog view 显式传入
  config validator；`core/` 不反向 import `backtest/`，也不保存第二份 engine ID 白名单。
- 运行时配置校验要求非空 TuShare token。`check_trade_calendar` 的完整性检查仅使用配置的 TuShare
  交易日历；本地行情或 smoke fixture 的 `LOCAL_DATA_DIR` / `SAMPLE_DATA_DIR` 不提供日历回退，价格
  panel 也不能替代官方日历。offline smoke 显式关闭该完整性检查，但 curve/assess 仍通过配置的
  TuShare client 获得执行日序列。
- `fixed_rebalance` 的目标数量在开盘价上冻结，订单按 `open`/`close` phase
  顺序执行；`high`/`low` 不能作为成交价，因为它们无法定义可审计的因果执行阶段。
  因此开盘卖出资金可以支持收盘买入，收盘卖出资金不能支持同日开盘买入。
- V1 共享输出根在整个 backtest session 结束前持有进程锁，覆盖清理、结果投影、
  诊断、协议元数据和 generation publish，避免并发运行互相覆盖固定输出文件。
- Artifact schema/generation 校验同样不依赖 Pydantic 开关。frozen `ArtifactSpec` 在 staging/reader
  边界校验显式 catalog key、正向 path rule、cardinality、owner/schema、size 和 SHA-256；这不新增
  行级 Pydantic 模型，也不改变 `v1-schema-v2` manifest 协议。
- panel-native 合同也不依赖 Pydantic 开关。`PricePanel`/`PanelIndex` 在 seal 时验证唯一键、确定性
  排序和 next-row 边界，`ColumnStore`/`FeatureBatch`/`SignalBatch` 验证长度、dtype、列所有权和
  写保护，`QuoteStore` 验证位置与日期 lookup。`ArtifactPolicy.NONE` 不物化报告 DataFrame，`FULL`
  只在 portfolio 完成后投影；两者的财务结果必须完全一致。
- 不完整必需 OHLCV 策略也不依赖 Pydantic 抽样。`panel.incomplete_ohlcv_policy=drop_warn` 是默认兼容
  模式，必须在丢弃前生成含输入/保留/丢弃行数、必需列和有界确定性样本的结构化诊断；`reject` 在任一
  不完整行上失败。全部行无效时两种模式都失败。读取结果通过 DataFrame attrs 暴露诊断，panel builder
  还把聚合诊断写入相邻 manifest，不能静默删除或用 Pydantic 关闭开关绕过。
- `point_in_time_join()` 的 key/date 唯一性、向后 as-of、exact join、必需参考缺失、禁止未来数据、
  多次关联日期对齐及输入行序/行数/OHLCV 不变量有直接测试；`validate_target_weights()` 的现金缓冲、
  单标的上限、换手、重复标的和有限非布尔数值合同，以及 `probe_strategy_behavior()` 的顺序不变性、
  prefix causality 与明确失败诊断，同样不由 Pydantic 行级开关控制。
- TopN 证据要求 `rows >= top`。`rows == 0` 输出 `topn_slice_missing`，`0 < rows < top` 输出
  `topn_slice_incomplete`；二者都非零退出，promotion/full-gate validator 不能把旧的
  `topn_slice_ok` 文本或缺 `top` 的 JSON 当作完整证据。
- `tools/benchmark_v1_panel_native.py` 的输入/source SHA-256、worker/paired 协议，以及
  `tools/benchmark_v1_financial_digest.py` 独立拥有的 canonical financial digest schema v1、有限浮点
  bit comparison、NaN payload normalization 和 first-difference 报告，是独立性能/财务证据
  合同，不受 Pydantic 抽样开关影响。dirty worktree 只能生成 `formal_eligible=false` 的 candidate，
  不能建立正式 baseline 或完成 paired A/B。
- 外部验证就绪协议同样不依赖行级开关。`docs/validation/external_validation.md` 固定行情快照身份、
  五引擎配对、券商对账、资源测量和三态分类；缺可信输入时只能报告
  `deferred_external_data` 或 `not_production_capital_certification`。
- 策略元信息校验也不依赖 Pydantic 开关。`strategies.spec.get_strategy_spec()` 接受
  `feature_scope`、`use_market_features`、`market_rank_specs`、`market_aggregate_specs`、
  `market_feature_passthrough_cols`，
  并拒绝已移除的 `use_market_rank`、`market_feature_source_col`、`market_rank_ascending`
  等旧单 rank 字段。
  `feature_scope` 只接受 `stock`（默认）或 `cross_section`。`stock` 只允许股票内特征，并通过完整
  panel 与单股输入的特征结果对照执行隔离测试；`cross_section` 允许完整 panel 横截面状态，不要求
  单股等价，但仍必须通过完整 panel 验证、输入顺序不变和 prefix causality。两者都必须保持键、行序
  和原始 OHLCV 不变。
  `use_market_features=True` 时必须至少声明非空 `market_rank_specs` 或 `market_aggregate_specs`；
  `market_aggregate_specs[].agg` 仅支持 `mean/sum/median/count/min/max/std`，输出列不能和源列、
  rank 输出列或 passthrough 列重名。
  策略可选研究契约字段包括 `name`、`hypothesis`、`required_fields`、`universe_assumption`、
  `validation` 和 `expected_market_regime(s)`；缺失这些字段默认只生成 warning，使用
  `python tools/validate_strategy_spec.py --module <module> --strict` 可将缺失项作为失败处理。
  普通策略生产不要求创建或维护研究合同；现有 scaffold 也允许 hypothesis、universe、validation
  和 regimes 保持空值。用户明确要求晋级时才补齐对应字段，首次及后续目标窗口回测都不以此为
  前置条件。
  `required_fields` 只记录研究审计依赖；基础 OHLCV 之外的运行时加载列由 `price_columns`
  声明。V1 panel 公式必须保持 `trade_date/stock_code` 行、顺序和原始 OHLCV；panel 策略返回的
  `FeatureBatch` / `SignalBatch` 必须与 sealed panel 等长且不得覆盖基础列。rolling/shift/pct_change
  必须在稳定的 stock_code 分组内计算。字符串信号、错位条件索引、错误 dtype 和共享可写数组都会被拒绝；
  这些 fail-fast 契约不受 `PYDANTIC_VALIDATE` 开关影响。
  `sort_spec` 引用列在买入候选行上必须是非布尔的有限实数；数字字符串、`NaN` 和正负无穷不会
  隐式转换。策略 guardrail 与 TopN 汇总层执行同一校验，任一层失败都会使本轮回测无效。
  panel 策略的 `feature_scope` 只接受 `stock`（默认）或 `cross_section`：前者要求
  `compute_features` 的特征可由单股历史独立重现，后者允许完整 panel 横截面状态；两者都必须通过
  完整 panel、输入顺序和 prefix causality 验证，并保持键、行序与原始 OHLCV 不变。该字段只描述
  特征所有权边界，不创建备用执行路径，也不会让策略绕过 `FeatureBatch` / `SignalBatch` 合同。
  `tools/agent_strategy_backtest.py` 写出的 `summary.suggested_decision`、`decision_authority=advisory`
  和 `evidence_policy` 是 agent 导航索引语义，不属于 Pydantic 校验模型；Codex 仍可读取源码、
  日志、CSV、run metadata 和完整协议产物，并在记录原因后覆盖 runner 建议。
  `universe_assumption` 只声明策略适用股票池假设，不负责读取或过滤股票池；运行层会解析实际
  Universe 并写入 run metadata / compiled plan，research audit 会对可验证的 symbols、prefixes、
  `exclude_bj`、`exclude_st` 不一致生成 warning，声明 `universe_locked` 时不一致会升级为 error。
  `STRATEGY_SEMANTICS`、`STRATEGY_DESCRIPTION`、`STRATEGY_LIMITS` 只允许作为 dict 静态声明，不驱动回测执行。
- 旧策略迁移合同也不依赖 Pydantic 抽样。`tools/retired_panel_path_gate.py` 拒绝旧 feature/market
  helper、逐股 fallback、跨模块私有 helper、信号阶段回调 feature builder、框架 market 输出和标准
  signal/finalization 所有权回流；即使 `STRATEGY_META` 含 `dict(PARAMS)` 等非字面量字段，也会独立解析
  rank/aggregate/cross-section 声明。`test_web_strategy_panel_migration.py` 对全部活动 V1 模块执行顺序、
  完整 panel 和 prefix causality 验证，并仅对 `feature_scope=stock` 执行单股特征隔离。
- `STRATEGY_LIMITS` 默认可以为空。显式声明时，`tools/strategy_static_check.py` 对超预算的 rolling
  窗口/次数、df 列赋值数量和 buy/sell 条件复杂度只生成 warning；文件体积、圈复杂度和这些
  advisory warning 都不会阻断 Codex 策略生产。
- `python tools/codex_strategy.py check --module strategy_library.generated.<name>` 默认使用 `draft`
  profile：authoring 发现属于诊断，不会单独阻断真实回测。普通循环无需先独立运行该命令，因为
  backtest 已包含检查并把 security、compile、import、loader、config 和运行结果分阶段落盘。
  draft 中报告的未来数据、标准信号归属、schema、保留列或行情不变量 error 仍会使该轮绩效无效，
  即使回测命令继续执行或指标达标也不能保存为合格策略。
- `python tools/llm_authoring_gate.py` 是独立于 Pydantic 行级开关的长期作者能力门禁。它固定检查
  rule/factor/panel scaffold、权威模板与作者文档、capability 公共表面、`backtest`、`validate`、
  `capabilities`、`fields`、`scaffold`、`check` 六个 canonical 命令、结构化 backtest 产物和 offline
  引擎链。模板、authoring、策略公共 API、静态检查或 Codex 工具变更不得跳过或删除该门禁。
- 新策略进入 Web 库前还必须运行 `python tools/strategy_static_check.py
  strategy_library/generated/<name>.py --enforce-independence`。该独立性门禁拒绝完成策略导入，
  要求唯一的 `signal_family` 与 `raw_market_data` 研究基础，并扫描既有 canonical/Web 源码，阻止
  高相似度信号骨架或仅改参数的策略簇变体。
- SoftwareX 论文的主证据是冻结历史测量合同下的真实 Codex 规则策略首次达标轨迹：只修改同一个
  generated 模块，所有轮次保留为 `Invalid / Unmet / Passed`，首次合同有效且所有严格目标通过后停止。
  `protocol_longitudinal_search_20260812.json` 不设置最大迭代数或搜索时间；worker timeout 只隔离基础设施
  故障，不是搜索预算。最终规则源码由 SHA-256 冻结，且 `fitted_model=false`。
- `tools/run_paper_experiments.py` 只承担论文支撑验证，不替代上述 authoring gate、Pydantic 行级校验或
  P1-P5 发布 benchmark。`protocol_codex_system_validation_v2_20260812.json` 绑定七个内部
  index/selection/quote case、每 case 20 对和 exact canonical output 有效性规则；
  `protocol_contract_benchmark_20260812.json` 绑定九类、108 个建模错误和 54 个干净控制；
  `final_strategy_replay.py` 用三个新进程比较规范化金融摘要；`real_workflow_scaling.py` 用四个历史窗口、
  每窗口三次新进程测量真实 backtest 命令。任一 worker 缺失、样本数不全或摘要不一致都会使报告无效。
  合同基准中的普通 Python compile/单次执行基线检出 `0/108` 个建模错误，JSHS 合同检出 `108/108`，
  两者都放行 `54/54` 个干净控制；该窄基线只隔离领域合同新增的故障可见性，不是 agent feedback、
  开发效率或替代回测器效果比较。独立复现应按 `docs/paper_submission/independent_reproduction_protocol.md`
  （中文对照见 `docs/chinese_journal_submission/independent_reproduction_protocol_zh.md`）分开报告工件审计、
  禁用缓存的本地行情 smoke（配置 TuShare 日历访问但无外部行情文件）、合成合同实验和授权行情重放。
  所有论文 runner、snapshot builder 和 DOCX builder 必须显式提供 `--output-dir`、`--target` 或
  `--output`；框架不再用隐式 `output/paper_experiments` 目标混合不同轮次。模型和文档工具缓存位于
  版本化 `.cache/jshs`，comparison/replay scratch 位于 `.tmp/jshs`，两者都不是验证证据。
  `output_gc.py` 和 generation prune 默认 dry-run，其清理计划或释放空间本身不能替代协议报告、hash
  校验或 sealed/promotion 证据。
  历史 agent-repair/Qwen/Codex CLI 脚本可以保留作旧实验工具，但不属于当前投稿的主张或复现命令。
- `check --profile promotion`、`backtest --promotion` 和 `validate --promotion` 才把严格 authoring 与
  研究合同作为晋级门禁；`research_governance.role=sealed_holdout` 会强制该 profile。draft、legacy
  和 skipped 报告都不是 promotion 证据。`--promotion` 不会反向把默认 diagnostic protocol 变成
  sealed；sealed 调用必须显式提供 role、campaign、candidate 和 protocol，并核对
  `holdout_claimed=true`。`--skip-static-check` 只省略 AST 诊断，仍执行 compile 与源码安全检查。
- `research_governance.campaign_db` 只允许解析到仓库 `output/codex_campaigns/`；显式
  `--campaign-db` 优先于 protocol 值，但受同一目录边界约束。
- 源码安全、敏感配置拒绝和输出目录隔离不受 profile 或 `PYDANTIC_VALIDATE` 开关影响。静态检查
  只保留有限、确定性的源码检查；导入、loader、factory 和运行期 schema 问题由真实框架报告。
- `core.schema` 保持轻量，只保留 attrs、sort helper 和 `SignalSchema`。
- pandas-heavy 规范化逻辑在 `core.dataframe_schema`。
- `core.validation` 只在校验开关开启且实际调用 `maybe_validate_*()` 时加载 Pydantic 模型。
- `projects/engine_v2/` 独立项目不参与主项目 Pydantic 行级校验链。
- 策略验真协议窗口必须落在当前数据 manifest 覆盖范围内；默认 full-window 起点使用
  `20240102`，避免早于 Parquet panel 最早交易日。
- `panel-manifest` 审计检查 Parquet panel manifest 的行数、股票数、日期范围、重复键、必要列、
  source 配置 hash 和逐股票覆盖摘要；CLI 还会重算当前 Parquet 实际 SHA-256。
  panel/manifest 缺失、`file_sha256` 缺失或实际内容不匹配都是 hard failure，不是 warning。
- Panel manifest 当前为 schema-v3；identity 与行级 Pydantic 校验是两层独立合同：稳定 identity 包含 `dataset_id`、
  `content_sha256`、`semantic_fingerprint`、`source_config_hash` 和 canonical manifest hash；
  `created_at_utc`、绝对路径、输出名和 provenance 不参与 identity。普通 session 可读取 manifest/READY
  缓存身份而不重算整 panel SHA-256。schema-v2 或缺少 nested `source_config` 的旧 manifest 只能
  metadata fallback 并发 warning，不能 freeze 或用于 strict validation/benchmark；snapshot 控制面损坏、
  panel build、promotion/sealed 的严格 validation 和 benchmark `verify-inputs` 仍 fail closed 并执行完整内容 hash。SHA-256 用于复现与
  缓存完整性，不提供签名/HMAC 级恶意篡改保证。
- `docs/architecture/V1_PANEL_NATIVE_PERFORMANCE_BREAKING_REFACTOR_PLAN_2026-08-05.md` 定义的是尚待
  实施的破坏性框架合同和 A/B 协议，不改变当前 Pydantic 行级开关。候选实现必须在计时范围外对 keys、
  行位置、dtype、OHLCV、特征、rank、信号、TopN、成交、账本和净值生成 canonical digest；任一不一致
  都是 hard failure，不能通过打开/关闭 Pydantic、提高性能基线或放宽现有门禁处理。
- `point-in-time` 审计要求 `available_at <= trade_date`，用于因子、财报、行业或股票池快照等
  非价格数据的可见时间检查；缺少 `available_at` 或出现未来可见时间会失败。
