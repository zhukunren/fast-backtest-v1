# Tools

`tools/` 只保留主项目 V1 回测、性能、数据和策略校验相关脚本。

先从 [`tools/INDEX.md`](INDEX.md) 按任务查找入口；本页保留详细协议、参数示例和运维说明。

日常开发先运行：

```powershell
python tools/verify_changed.py
python tools/verify_changed.py --execute
```

路径规则和验证矩阵由 `config/change-impact.json` 管理；完整质量门禁由 CI 和发布流程负责。

大多数脚本支持：

```powershell
python tools/<script>.py --help
```

## 本地验证入口

```powershell
python tools/verify_changed.py
python tools/verify_changed.py --execute
```

`--execute` 对性能敏感路径使用 `perf_smoke.py --quick` 和 `hotpath_smoke.py --quick`；它们只验证
本地测量链可完成，不比较或更新 canonical baseline。CI/release 的正式协议仍使用不带 `--quick` 的命令。

## 回归

- `tools/audit_full_release.py`：完整工作区开源候选的安全、路径、体积和默认入口审计。
- `tools/audit_ci_constraints.py`：验证 CI 安装闭包中的直接和传递依赖均有精确版本约束。
- `tools/audit_python_dependencies.py`：用 `pip-audit` 审计精确 CI 依赖闭包，并按零漏洞策略 fail closed。
- `tools/audit_frontend_bundle.py`：读取 Vite manifest，拒绝超过 500 KB 或缺失的生产 JavaScript chunk。
- `tools/audit_frontend_dependencies.py`：对完整及生产前端依赖树执行结构化 `npm audit`，并按机器可读策略拒绝新增漏洞。
- `tools/full_quality_gate.py`：完整工作区发布/交付门禁，串起全量 V1/Web、覆盖率、类型、审计、性能和前端检查，并输出 Git 绑定的 portable schema-v2 报告；Python 由可执行文件 SHA256/大小绑定，临时路径使用 `gate-tmp://`。运行前后仓库身份不一致时硬失败。启用 TopN 后，父进程还会读取结构化产物并独立复算输入 SHA256，再写入 `repo://` 引用。
- `tools/validate_full_gate_report.py`：校验 full-gate 报告的 profile、full Git SHA、起止 clean identity，以及 canonical step 顺序、命令语义、cwd 和严格整数返回码；拒绝 dirty、失败、不完整、包含宿主绝对路径及 `latest.json` 报告成为发布证据。`--require-topn-slice` 还要求报告绑定目标日期/TopN、`repo://` 输入引用、输入 SHA256、行数和 preview。
- `tools/audit_complexity.py`：活动源码圈复杂度债务门禁；30 为硬红线，15 以上热点使用精确总量棘轮。
- `tools/syntax_check.py`：不写 `.pyc` 的 Python 语法检查，避免 Windows 缓存权限噪声。
- `tools/run_curated_regression.py`：固定分组回归。
- `tools/llm_authoring_gate.py`：固定检查模板/scaffold、capability、六个 Codex 策略命令、结构化
  backtest 工具产物和 offline 引擎链，防止未来版本降低 LLM 策略生产能力。

```powershell
python tools/audit_full_release.py
python tools/audit_ci_constraints.py
python tools/audit_python_dependencies.py --json-out output/tools/python_dependency_audit.json
python tools/audit_frontend_dependencies.py --json-out output/tools/frontend_dependency_audit.json
python tools/full_quality_gate.py --profile quick --skip-perf --skip-hotpath
$fullGateSha = git rev-parse HEAD
python tools/full_quality_gate.py --profile ci --include-web-build --json-out "output/tools/full_quality_gate/local-ci-$fullGateSha.json"
python tools/validate_full_gate_report.py "output/tools/full_quality_gate/local-ci-$fullGateSha.json" --expected-profile ci --expected-commit $fullGateSha --require-web-build
python tools/run_curated_regression.py --group unit
python tools/run_curated_regression.py --group integration
python tools/run_curated_regression.py --group e2e
python tools/run_curated_regression.py --group llm_authoring
python tools/run_curated_regression.py --group gate --dry-run
python tools/llm_authoring_gate.py
```

固定 TopN 只在本地数据可用时加入 final evidence；执行和校验开关必须配对：

```powershell
$fullGateSha = git rev-parse HEAD
python tools/full_quality_gate.py --profile ci --include-web-build --include-topn-slice --json-out "output/tools/full_quality_gate/local-ci-topn-$fullGateSha.json"
python tools/validate_full_gate_report.py "output/tools/full_quality_gate/local-ci-topn-$fullGateSha.json" --expected-profile ci --expected-commit $fullGateSha --require-web-build --require-topn-slice
```

## 本地存储与清理

- `tools/output_gc.py paths`：解析 `.cache/jshs` 与 `.tmp/jshs` 的共享路径；可用
  `JSHS_CACHE_ROOT`、`JSHS_TMP_ROOT` 覆盖。
- `tools/output_gc.py migrate`：盘点旧 `paper_models`、文档 QA 依赖、formal/public/comparison
  工作区和旧 per-run `.worker_cache`；默认排除论文/发布研究证据。
- `tools/output_gc.py prune`：盘点过期临时工作区、显式源码包白名单内嵌套的 pytest/build/cache 目录和
  `.incomplete/.partial/.tmp` 缓存文件；`web/data` 证据与链接不递归触碰，`perf_smoke` 的逐次 scratch
  位于 `.tmp/jshs/perf-smoke`。
- `tools/manage_panel_snapshots.py`：对日常 flat panel 的 schema-v3 manifest 做显式完整内容校验，发布到
  `versions/<dataset_id>`，并原子切换 `current.json`；普通回测不会借此重复计算全 panel SHA-256。
  `freeze`、`inspect`、`activate` 分别负责发布、检查和切换；失败不会把 staging 半成品暴露为 current。
- `tools/output_gc.py logs`：集中旧 Web/后台进程日志，并按保留期清理 `output/logs`；
  `<run-output>/run_meta/`（默认 `output/pipeline/run_meta/`）内的 run 日志和 run event 不处理。

```powershell
python tools/output_gc.py paths
python tools/output_gc.py migrate --older-than-hours 24
python tools/output_gc.py prune --older-than-hours 168
python tools/manage_panel_snapshots.py freeze --panel data/panel_cache/stock_daily.parquet --snapshot-root data/panel_snapshots
python tools/manage_panel_snapshots.py inspect --snapshot-root data/panel_snapshots
python tools/manage_panel_snapshots.py inspect --snapshot-root data/panel_snapshots --verify-content
python tools/output_gc.py logs --retention-days 30
```

`migrate`、`prune` 均默认 dry-run；检查 JSON 后才显式追加 `--apply`。`PIN`、`ACTIVE`、新近路径、
链接和冲突目标会被保护。`output/` 保留明确命名的运行/审计证据；可重建依赖进入 `.cache`，可丢弃
worktree 和 scratch 进入 `.tmp`，非 run 运维日志进入 `output/logs`。三个根分别可用
`JSHS_CACHE_ROOT`、`JSHS_TMP_ROOT`、`JSHS_LOG_ROOT` 覆盖。

## Artifact generation 运维

- `tools/inspect_run_artifacts.py`：既可检查旧 run metadata/protocol 文件，也可通过 generation reader 校验当前或指定 generation。
- `tools/manage_artifact_generations.py`：确定性列出 READY generation、检查完整性、原子切换
  `current.json`，并按保护规则清理旧 generation。

```powershell
python tools/inspect_run_artifacts.py --generation-root output current
python tools/inspect_run_artifacts.py --generation-root output <generation-id>
python tools/manage_artifact_generations.py --output-root output list
python tools/manage_artifact_generations.py --output-root output inspect current
python tools/manage_artifact_generations.py --output-root output activate <generation-id>
python tools/manage_artifact_generations.py --output-root output prune --keep-last 3
python web/tools/manage_cache_generations.py prune --keep-last 3
```

direct run 的 inspector scope 为 `run_result_only`，完整 application session 为 `session_complete`。
`ArtifactPolicy.NONE` 不物化或发布 result generation；`ArtifactPolicy.FULL` 在组合执行后投影并发布。
`run_log`/`run_events` 是 `external_append_only`，不受
immutable hash 清单管理。`activate` 不切换 engine runtime，也不能作为真实 release rollback drill
的替代证据。prune 默认 dry-run；current、`PIN`/`--pin`、最近 N 代、未过期或 `ACTIVE` staging 以及
不可解析的 current/READY 状态均受保护，确认计划后才使用 `--apply`。

## 完整公共源码发布

- `tools/export_full_repo.py`：只从已跟踪、明确允许的文件生成完整 V1 公共源码候选。
- `tools/audit_full_public_surface.py`：检查候选路径、凭据、运行产物、策略与发布清单边界。
- `tools/smoke_full_public_repo.py`：从候选仓库内部验证离线引擎、Web 后端和前端构建链。

```powershell
python tools/export_full_repo.py --clean
python tools/audit_full_public_surface.py --root .tmp/jshs/public-repos/full_public_repo
Set-Location .tmp/jshs/public-repos/full_public_repo
python tools/smoke_full_public_repo.py
```

该导出链是唯一公共交付实现；不得再增加平行包、导出器或发布门禁。

## 性能

- `tools/perf_smoke.py`：local-price curve smoke 的性能与内存基线；每次调用使用唯一 fixture 子目录，且仍要求配置 TuShare 日历。默认采样 3 次，比较 `profile.backtest_elapsed_sec` 的中位数和完整子进程树的中位峰值 RSS。时间预算为 20%，RSS 预算为 5%；任一基线缺失或非法都会 fail closed。报告同时保留端到端 `elapsed_sec`、setup 耗时和单样本最大 RSS 用于排查。
- `tools/hotpath_smoke.py`：价格切片、缓存、Parquet L2 等局部热路径；schema-v2 固定 3 轮、每轮 5 个至少 100ms 的墙钟批量样本、3 次预热和循环 GC 隔离，按单位调用最小值比较并记录进程 CPU 诊断。schema/rows/rounds/repeat/协议/case 集必须与 canonical baseline 完全一致，缺失、损坏、非法样本或 tolerance 都会 fail closed，且不会隐式创建 baseline。
- 两个性能工具的 `--quick` 只运行轻量诊断协议，不读取或更新正式 baseline；它不能替代 canonical 回退比较。
- `tools/profile_backtest.py`：结构化 profile JSON。
- `tools/performance_budget.py`：对比 baseline 或预算。
- `tools/run_long_window_validation.py`：真实数据长窗口 profile 和可选预算门禁包装。
- `tools/benchmark_v1_panel_native.py`：固定输入、worker/candidate/paired 编排、candidate wall/RSS 和独立
  clean-worktree 正式配对 A/B；`tools/benchmark_v1_financial_digest.py` 独立拥有 canonical financial
  digest schema v1、二进制编码和首差异定位，CLI 仍只通过前者暴露。
- `tools/benchmark_panel_fastpath.py`：旧的探索性 panel profile；不构成本轮 breaking refactor 正式证据。
- `tools/audit_maintainability.py`：1200 行硬红线和 500 行存量大文件的数量/总行数棘轮。
- `tools/audit_complexity.py`：30 复杂度硬红线和 15 以上存量热点棘轮；覆盖 tracked/untracked 活动源码范围。

```powershell
python tools/perf_smoke.py --repeat-runs 3
python tools/hotpath_smoke.py
python tools/hotpath_smoke.py --update-baseline  # 仅在明确重冻结完整 canonical baseline 时执行
python tools/profile_backtest.py --case offline_smoke --json-out output/tools/profile/offline_smoke.json
python tools/performance_budget.py --report output/tools/profile/offline_smoke.json --budget docs/validation/performance_budget.example.json
python tools/run_long_window_validation.py --case mysql_real --start-date 20240101 --end-date 20251231 --stock-limit 500
python tools/benchmark_v1_panel_native.py verify-inputs
python tools/benchmark_v1_panel_native.py candidate --cases P0 --warmups 0 --repeats 1
python tools/audit_maintainability.py
python tools/audit_complexity.py
```

`candidate` 永远写出 `formal_eligible=false`，只验证当前实现、阶段计时、完整子进程树 peak RSS 和 digest
生成。正式协议固定 P1-P5，默认每边预热 3 次并采集 15 个交替顺序 pair；两个路径必须是独立 clean
worktree，任一 dirty 状态、输入/source hash 变化或 digest 差异都会 fail closed：

```powershell
python tools/benchmark_v1_panel_native.py paired `
  --baseline-worktree <baseline-clean-worktree> `
  --candidate-worktree <candidate-clean-worktree>
python tools/benchmark_v1_panel_native.py compare-digests `
  --baseline <baseline-manifest.json> `
  --candidate <candidate-manifest.json>
```

固定输入位于 `tools/benchmarks/v1_panel_native/`。正式验收阈值、计时边界和 bootstrap 定义见
`docs/architecture/V1_PANEL_NATIVE_PERFORMANCE_BREAKING_REFACTOR_PLAN_2026-08-05.md` 第 12 节；单次
candidate 或 smoke 不能替代该协议。

`verify-inputs` 始终读取并 SHA-256 校验冻结 panel、原始 manifest 和其他固定输入，且交叉检查
manifest 声明的 `file_sha256` 及 schema-v3 的四个稳定 identity 字段。日常回测只读取 manifest 的稳定身份，不执行这次全文件扫描。采用新
panel 时不得手工改旧 hash；必须先冻结 immutable snapshot，再明确执行下面的 `freeze-inputs` 入口，
由维护者审阅 spec/input diff 后在两个 clean worktree 上生成新的 evidence：

```powershell
python tools/benchmark_v1_panel_native.py freeze-inputs `
  --source-compiled-plan <compiled-plan.json> `
  --panel-path <frozen-panel.parquet>
```

`parquet` 是唯一 canonical panel source；`summary_table` 和 `auto` 不再是运行时入口，也没有
兼容回退。需要数据库数据时先用 builder 生成 Parquet，再把该文件冻结为 evidence。原冻结 panel
当前不能从 Git 历史恢复；若使用新 panel，必须建立新的 evidence series。

CI 与发布 workflow 通过 `constraints-ci.txt` 固定依赖证据环境。普通用户安装仍使用 `requirements.txt`/`pyproject.toml` 的兼容下界；复现 CI 时追加 `-c constraints-ci.txt`。

## 数据

- `tools/build_panel_parquet.py`：默认用 TuShare 拉正常上市股票列表，再依次从 MySQL 单股表全量流式写入本地 Parquet panel，并覆盖当前回测配置使用的 `data/panel_cache/stock_daily.parquet`。
- `tools/data_quality_audit.py`：数据质量审计。
- `tools/build_summary_table.py`：构建 `stock_daily_all` 总表。

```powershell
python tools/build_panel_parquet.py
python tools/build_panel_parquet.py --db stock_data --verify
python tools/build_panel_parquet.py --workers 4 --verify
python tools/build_panel_parquet.py --include 000001.SZ,600000.SH --dry-run
python tools/data_quality_audit.py --target local-source
python tools/data_quality_audit.py --target panel-native-storage
python tools/data_quality_audit.py --target panel-manifest --input data/panel_cache/stock_daily.parquet
python tools/data_quality_audit.py --target point-in-time --input data/factors.csv --available-at-col available_at
```

数据更新脚本不内置 TuShare token 或 MySQL 密码。运行需要通过环境变量提供凭据：

```powershell
$env:TUSHARE_TOKEN="token"        # 或 $env:TS_TOKEN="token"
$env:MYSQL_PASSWORD="..."
```

所有在线 TuShare 入口都要求上述显式配置或工具自身的显式 token 参数；不会调用无参 `pro_api()`，
也不会调用 `set_token()` 读写用户目录 `tk.csv`。多 token 更新使用重复的
`tools/updata_data.py --tushare-token ...` 参数，不使用隐式用户目录状态。

`build_panel_parquet.py` 只保留默认用途：TuShare 只用于 `stock_basic` 股票列表，行情从 `config.mysql.stock_db` 对应库里的单股表读取。默认输出 `data/panel_cache/stock_daily.parquet`，全量模式默认覆盖日常可变 panel；写入采用临时文件，成功后再替换旧文件。正式研究或 benchmark 输入应另行用 `manage_panel_snapshots.py freeze` 固化，避免把日常更新路径当作冻结 evidence。

默认 MySQL 库来自 `config.mysql.stock_db`，也可以显式传 `--db stock_data`。`--tushare-timeout` 控制 TuShare `stock_basic` 请求超时，默认 30 秒。

`--workers auto` 为默认值，只使用多线程读取 MySQL，不启动多进程；自动线程上限默认 8，可用 `PANEL_MYSQL_AUTO_WORKERS_MAX` 调整。Parquet 写入仍在主线程完成。

## 策略

- `tools/codex_strategy.py`：Codex 的字段/能力查询、中性 scaffold、默认 draft 诊断、backtest /
  validate 主入口；promotion 时显式启用严格 profile。worker 使用独立 `python -I` 进程和超时。
- `tools/codex_campaign.py`：计算 sealed protocol 语义 hash、预注册候选预算、查询状态和冻结 shortlist。
- `tools/agent_strategy_backtest.py`：Codex worker 调用的底层单候选 runner；不是另一个主动 agent 入口。
- `tools/strategy_static_check.py`：底层 AST authoring 诊断；读取 `STRATEGY_LIMITS` 并对复杂度超预算
  生成 advisory warning；`--enforce-independence` 还会拒绝完成策略导入、重复 signal family、
  高相似度信号骨架和仅改参数的策略簇变体。
- `tools/strategy_backtest_smoke.py`：本地价格 fixture 回测策略库，验证策略能否加载并跑完整 V1 回测链路；仍需配置 TuShare 日历。
- `tools/validate_strategy_spec.py`：校验 `STRATEGY_META` 声明式运行/研究契约，可用 `--strict` 将缺失研究字段视为失败。
- `tools/validate_strategy.py`：`codex_strategy.py validate` 调用的底层确定性验真工具；默认 diagnostic，
  `--promotion` 或 sealed holdout 使用严格 authoring 与研究合同检查。
- `tools/build_validation_protocol.py`：生成 rolling / walk-forward 诊断窗口；不会自动赋予 OOS 身份。
- `tools/multi_strategy_backtest.py`：每策略独立子进程的顺序回测；`--timeout-sec` 提供 wall-clock
  timeout（默认 1800 秒，0 为显式关闭），Windows 使用 `taskkill /T /F` 清理整个进程树。每个结果
  立即 flush 到 `summary.csv` 和原子 `checkpoint.json`；timeout 结构化记录后继续。默认
  `ArtifactPolicy.NONE`，只有 `--keep-artifacts` 才保留完整策略产物。
- `tools/check_topn_slice.py`：检查指定交易日 TopN 切片；默认按确定性顺序查找主流程、compare 结果及 `output/tools/*/current/strategies/*` 的多策略产物；`--json-out` 记录 schema、目标日期/TopN、resolved 输入路径、输入 SHA256/大小、行数和 preview。

```powershell
python tools/codex_strategy.py fields
python tools/codex_strategy.py fields --id net_mf_amount --json
python tools/codex_strategy.py fields --configured
python tools/codex_strategy.py capabilities --runtime v1 --available-only
python tools/codex_strategy.py capabilities --runtime v1 --id market_rank --json
python tools/codex_strategy.py scaffold --name codex_demo_alpha --profile rule --runtime v1 --capability market_rank
python tools/codex_strategy.py scaffold --name codex_demo_panel --profile panel --runtime v1 --capability market_rank
python tools/codex_strategy.py check --module strategy_library.generated.codex_demo_alpha
$sealedProtocolSha = (python tools/codex_campaign.py protocol-hash --protocol path\to\sealed_protocol.json | ConvertFrom-Json).protocol_sha256
python tools/codex_campaign.py init --campaign-id demo_20260714 --max-candidates 10 --sealed-protocol-sha256 $sealedProtocolSha --familywise-alpha 0.05
python tools/codex_strategy.py backtest --module strategy_library.generated.codex_demo_alpha --start-time 20240101 --end-time 20260626 --campaign-id demo_20260714 --candidate-id c001 --panel-source parquet --min-return 4.0 --max-drawdown 0.35 --min-win-rate 0.55 --min-slots 2x2
python tools/codex_strategy.py check --module strategy_library.generated.codex_demo_alpha --profile promotion
python tools/codex_strategy.py backtest --module strategy_library.generated.codex_demo_alpha --start-time 20240101 --end-time 20260626 --promotion
python tools/codex_strategy.py validate --module strategy_library.generated.codex_demo_alpha --protocol <protocol.json> --promotion
python tools/strategy_static_check.py strategies/templates/template.py
python tools/strategy_static_check.py strategy_library/generated/<name>.py --enforce-independence
python tools/strategy_backtest_smoke.py --no-web --limit 3
python tools/validate_strategy_spec.py --module strategy_library.builtin.offline_smoke
python tools/codex_strategy.py validate --module strategy_library.builtin.offline_smoke
python tools/build_validation_protocol.py --start 20240102 --end 20260630 --train-days 250 --test-days 60 --step-days 60 --out docs/validation/generated_walk_forward.json
python tools/multi_strategy_backtest.py --dir strategy_library/custom --timeout-sec 900
python tools/check_topn_slice.py --date 2025-05-16 --top 10
```

普通 OHLCV 策略直接从 `strategies/templates/iterative_template.py` 开始并运行固定 backtest，不要求
先执行上面的 fields、capabilities、scaffold 或独立 check。rule/factor scaffold 生成一次接收完整
panel 的作者 hook；公式必须按 `stock_code`, `trade_date` 稳定排序并用 grouped 时序操作，batch
helper 只调用一次并写入 panel-native 数组。panel scaffold 直接生成
`PanelView -> FeatureBatch/SignalBatch` hooks。三种 profile 选择的是作者组织方式，不是算法白名单。
市场 rank/breadth 在所有 profile 中都从完整 panel 的 feature stage 产生，最终进入同一执行链。

`capabilities --runtime v1` 会返回每项能力对标准 V1 的可用状态和原因，`--available-only` 只过滤
runtime 不可执行项，不保证数据源完整。scaffold 默认拒绝标准 V1 不可用的能力；仅为 schema 或
自备程序化 runtime 编写模块时才显式传 `--allow-unavailable-runtime`。该开关允许生成源码，不会
让标准 V1 session 获得缺失能力。`fields --configured` 同样只表示当前配置请求列，不保证底层列或
每行值实际存在。

默认 `check` 和 `backtest` 都使用 draft profile。AST authoring 发现不阻断回测；Codex 从
`static_check.json`、`execution_stages.json`、`summary.json` 以及日志、成交和持仓产物迭代。
`research_contract` 是 scaffold 的按需能力，晋级前再补齐。draft / skipped 不是晋级证据；
源码安全、敏感 override 拒绝和输出目录隔离始终硬阻断，skip 也不会跳过 security 或 compile。
`--campaign-db` 及 protocol 中的 `research_governance.campaign_db` 均只能写入
`output/codex_campaigns/`。
