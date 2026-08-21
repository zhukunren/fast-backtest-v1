# 工具索引

Status: active  
Last verified: 2026-08-21

本页是 `tools/` 的全量导航，覆盖 101 个 Python 文件：67 个可直接执行入口和 34 个内部模块。
详细参数、协议说明和常用命令见 [`tools/README.md`](README.md)；具体参数以脚本的 `--help`
为准。

## 使用约定

- 顶层入口通常使用 `python tools/<name>.py --help`。
- `paper_experiments` 包入口优先使用 `python -m tools.paper_experiments.<name> --help`。
- 标为“worker”或“内部”的模块由上层工具调用，不应作为日常命令。
- 论文复现、D4、发布和基线更新工具受固定协议约束，运行前先阅读对应文档。

## 快速查找

| 目标 | 首选入口 | 说明 |
| --- | --- | --- |
| 创建、检查、回测策略 | [`codex_strategy.py`](codex_strategy.py) | 策略生产主入口 |
| 批量回测策略 | [`multi_strategy_backtest.py`](multi_strategy_backtest.py) | 独立子进程、checkpoint 和汇总 |
| 重建本地 panel | [`build_panel_parquet.py`](build_panel_parquet.py) | 构建 Parquet panel，并可执行写后校验 |
| 更新 TuShare/MySQL 数据 | [`updata_data.py`](updata_data.py) | 项目保留的历史文件名 |
| 审计数据质量 | [`data_quality_audit.py`](data_quality_audit.py) | local source、manifest、PIT 等检查 |
| 运行固定回归 | [`run_curated_regression.py`](run_curated_regression.py) | unit、integration、e2e、gate 分组 |
| 验证策略作者能力 | [`llm_authoring_gate.py`](llm_authoring_gate.py) | 模板、scaffold、CLI 和本地价格链门禁（需 TuShare 日历） |
| 运行完整交付门禁 | [`full_quality_gate.py`](full_quality_gate.py) | 发布级综合编排 |
| 按变更面规划验证 | [`verify_changed.py`](verify_changed.py) | 输出并可执行本地最小充分验证 |
| 检查性能回退 | [`perf_smoke.py`](perf_smoke.py) / [`hotpath_smoke.py`](hotpath_smoke.py) | 端到端与热路径基线 |
| 检查运行产物 | [`inspect_run_artifacts.py`](inspect_run_artifacts.py) | run metadata 和 generation |
| 盘点/清理本地存储 | [`output_gc.py`](output_gc.py) | 迁移旧 output 缓存、集中日志并清理共享临时目录；默认 dry-run |
| 导出公共候选 | [`export_full_repo.py`](export_full_repo.py) | 完整 V1 公共源码唯一导出入口 |
| 运行论文实验 | [`run_paper_experiments.py`](run_paper_experiments.py) | 冻结论文实验的统一编排入口 |

## 策略与回测

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`agent_strategy_backtest.py`](agent_strategy_backtest.py) | worker | `codex_strategy` 使用的单策略隔离回测执行器 |
| [`build_validation_protocol.py`](build_validation_protocol.py) | 入口 | 生成 rolling / walk-forward 验证协议 |
| [`check_topn_slice.py`](check_topn_slice.py) | 入口 | 检查指定交易日的确定性 TopN 切片 |
| [`codex_campaign.py`](codex_campaign.py) | 入口 | 管理显式 sealed strategy campaign |
| [`codex_strategy.py`](codex_strategy.py) | 主入口 | fields、capabilities、scaffold、check、backtest、validate |
| [`multi_strategy_backtest.py`](multi_strategy_backtest.py) | 入口 | 顺序批量回测、超时隔离、checkpoint 和汇总 |
| [`run_static_allocation_backtest.py`](run_static_allocation_backtest.py) | 入口 | 运行声明式固定权重 ETF 配置回测 |
| [`strategy_backtest_smoke.py`](strategy_backtest_smoke.py) | 门禁 | 用本地价格 fixture 验证策略库完整回测链，仍需 TuShare 日历 |
| [`strategy_static_check.py`](strategy_static_check.py) | 门禁 | 策略 AST、安全、独立性和合同静态检查 |
| [`validate_strategy.py`](validate_strategy.py) | 入口 | 按结构化协议执行诊断或 promotion 验真 |
| [`validate_strategy_spec.py`](validate_strategy_spec.py) | 门禁 | 校验声明式 `STRATEGY_META` 合同 |

## 数据与行情

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`build_panel_parquet.py`](build_panel_parquet.py) | 入口 | 从 TuShare 股票列表和 MySQL 单股表构建 Parquet panel |
| [`build_summary_table.py`](build_summary_table.py) | 入口 | 将 MySQL 单股表聚合为全市场 summary table |
| [`data_quality_audit.py`](data_quality_audit.py) | 门禁 | 运行本地数据、manifest、PIT 等专项审计 |
| [`manage_panel_snapshots.py`](manage_panel_snapshots.py) | 运维 | 冻结、检查并原子切换 immutable panel snapshot |
| [`updata_data.py`](updata_data.py) | 入口 | TuShare 数据更新 CLI，文件名为保留兼容名称 |

## 回归、质量与报告校验

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`audit_ci_constraints.py`](audit_ci_constraints.py) | 门禁 | 检查 CI 依赖闭包是否全部精确固定 |
| [`audit_complexity.py`](audit_complexity.py) | 门禁 | 检查活动 Python 源码圈复杂度棘轮 |
| [`audit_frontend_bundle.py`](audit_frontend_bundle.py) | 门禁 | 检查生产 JavaScript chunk 体积 |
| [`audit_frontend_dependencies.py`](audit_frontend_dependencies.py) | 门禁 | 审计前端完整和生产依赖树 |
| [`audit_full_release.py`](audit_full_release.py) | 门禁 | 审计完整工作区 release candidate |
| [`audit_grouped_coverage.py`](audit_grouped_coverage.py) | 门禁 | 按 manifest 分组审计语句和分支覆盖率 |
| [`audit_maintainability.py`](audit_maintainability.py) | 门禁 | 检查活动 Python 文件体积债务 |
| [`audit_python_dependencies.py`](audit_python_dependencies.py) | 门禁 | 审计 Python CI 依赖漏洞 |
| [`full_quality_gate.py`](full_quality_gate.py) | 主门禁 | 编排 V1、Web、覆盖率、审计、性能和前端检查 |
| [`git_path_guard.py`](git_path_guard.py) | 门禁 | 校验 staged 路径的项目轨道隔离规则 |
| [`llm_authoring_gate.py`](llm_authoring_gate.py) | 门禁 | 固定 Codex 策略生产能力回归 |
| [`retired_panel_path_gate.py`](retired_panel_path_gate.py) | 门禁 | 拒绝已退役的逐股 panel 兼容路径回流 |
| [`run_curated_regression.py`](run_curated_regression.py) | 主入口 | 按固定分组运行回归测试 |
| [`syntax_check.py`](syntax_check.py) | 门禁 | 不生成 `.pyc` 的 Python 语法检查 |
| [`validate_external_validation_report.py`](validate_external_validation_report.py) | 门禁 | 校验未来外部数据证据报告 |
| [`validate_full_gate_report.py`](validate_full_gate_report.py) | 门禁 | 校验 full-gate 报告完整性及 Git 身份绑定 |
| [`verify_changed.py`](verify_changed.py) | 主入口 | 根据 change-impact manifest 路由本地验证 |

## 性能与基准

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`benchmark_panel_fastpath.py`](benchmark_panel_fastpath.py) | 探索 | 比较受支持 panel 数据源的旧探索性基准 |
| [`benchmark_v1_panel_native.py`](benchmark_v1_panel_native.py) | 正式入口 | 固定输入 candidate、paired A/B 和 digest 比较 |
| [`hotpath_smoke.py`](hotpath_smoke.py) | 门禁 | 价格切片、缓存和 Parquet L2 热路径基线 |
| [`perf_smoke.py`](perf_smoke.py) | 门禁 | 本地价格回测耗时和进程树峰值内存基线，仍需 TuShare 日历 |
| [`performance_budget.py`](performance_budget.py) | 门禁 | 对结构化 profile 应用耗时和阶段预算 |
| [`profile_backtest.py`](profile_backtest.py) | 入口 | 运行结构化回测 profile 并输出 JSON |
| [`run_long_window_validation.py`](run_long_window_validation.py) | 入口 | 包装真实数据长窗口 profile 与预算检查 |

## 产物与实验记录

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`compare_experiments.py`](compare_experiments.py) | 入口 | 比较两个标准化 experiment registry 记录 |
| [`inspect_run_artifacts.py`](inspect_run_artifacts.py) | 入口 | 检查 run protocol 产物和 generation |
| [`list_experiments.py`](list_experiments.py) | 入口 | 列出标准化 experiment registry 记录 |
| [`manage_artifact_generations.py`](manage_artifact_generations.py) | 运维 | 列出、检查、原子切换并保护性清理 immutable generation |
| [`output_gc.py`](output_gc.py) | 运维 | 查看共享路径、迁移旧 output 缓存、集中日志并清理过期临时/partial 文件 |

## 公共源码交付

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`audit_full_public_surface.py`](audit_full_public_surface.py) | 门禁 | 审计公共候选的路径、凭据、产物和策略边界 |
| [`export_full_repo.py`](export_full_repo.py) | 主入口 | 从 Git tracked 文件导出完整 V1 公共候选 |
| [`smoke_full_public_repo.py`](smoke_full_public_repo.py) | 门禁 | 在导出候选内部验证本地价格、Web 和前端链，仍需 TuShare 日历 |

## 专项协议入口

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`run_paper_experiments.py`](run_paper_experiments.py) | 编排 | 论文 performance、contracts、agent workflow 和 replay 统一入口 |
| [`v1_d4_release_evidence.py`](v1_d4_release_evidence.py) | 正式协议 | 构建跨 release 真实数据、性能和 rollback 证据 |

## 顶层内部模块

这些文件没有独立 `__main__`，由相邻入口导入。

| 模块 | 所属入口 |
| --- | --- |
| [`benchmark_v1_financial_digest.py`](benchmark_v1_financial_digest.py) | `benchmark_v1_panel_native.py` 的金融摘要编码 |
| [`benchmark_v1_input_contracts.py`](benchmark_v1_input_contracts.py) | `benchmark_v1_panel_native.py` 的固定输入、spec 与依赖合同 |
| [`benchmark_v1_input_freeze.py`](benchmark_v1_input_freeze.py) | benchmark 固定输入 bundle 的 staging、发布与 rollback |
| [`benchmark_v1_input_support.py`](benchmark_v1_input_support.py) | benchmark 输入路径和依赖版本支持函数 |
| [`coverage_grouping.py`](coverage_grouping.py) | grouped coverage 清单和聚合实现 |
| [`hotpath_protocol.py`](hotpath_protocol.py) | `hotpath_smoke.py` 报告验证原语 |
| [`maintainability_scope.py`](maintainability_scope.py) | 维护性审计统一源码范围 |
| [`output_gc_logs.py`](output_gc_logs.py) | `output_gc.py logs` 的日志集中、保留期与安全执行实现 |
| [`output_gc_migration.py`](output_gc_migration.py) | `output_gc.py migrate` 的迁移盘点与执行实现 |
| [`output_gc_pruning.py`](output_gc_pruning.py) | `output_gc.py prune` 的过期项盘点与清理实现 |
| [`output_gc_safety.py`](output_gc_safety.py) | `output_gc.py` 的路径边界、链接与目录大小安全原语 |
| [`panel_build_config.py`](panel_build_config.py) | Parquet panel 构建的 TuShare timeout 与 worker 配置 |
| [`quality_gate_output.py`](quality_gate_output.py) | full-gate portable 输出实现 |
| [`quality_gate_runtime.py`](quality_gate_runtime.py) | full-gate 运行时与步骤执行实现 |
| [`update_data_runner.py`](update_data_runner.py) | 数据更新的唯一逐标的编排和 CLI 实现 |
| [`v1_d4_probe.py`](v1_d4_probe.py) | D4 单 release 探针 |
| [`v1_d4_process.py`](v1_d4_process.py) | D4 进程和 worktree 身份工具 |
| [`v1_d4_protocol.py`](v1_d4_protocol.py) | D4 跨 release 快照合同 |
| [`v1_d4_rollback.py`](v1_d4_rollback.py) | D4 隔离 rollback drill |
| [`v1_d4_schedule.py`](v1_d4_schedule.py) | D4 采样调度与身份约束 |
| [`v1_d4_stage_evidence.py`](v1_d4_stage_evidence.py) | D4 logical-stage 对比 |
| [`v1_d4_statistics.py`](v1_d4_statistics.py) | D4 fail-closed 配对统计 |
| [`v1_d4_supporting_evidence.py`](v1_d4_supporting_evidence.py) | D4 TopN 和 Web generation 支持证据 |

## 论文实验入口

这些入口只用于冻结研究协议和投稿工件，不是日常策略开发工具。所有生成入口要求显式
`--output-dir`、`--output` 或 `--target`，不会自行选择 `output/paper_experiments`。复现顺序以
[`docs/paper_submission/README.md`](../docs/paper_submission/README.md) 为准。

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| [`audit_research_snapshot.py`](paper_experiments/audit_research_snapshot.py) | 门禁 | 审计研究快照文件、哈希和复现边界 |
| [`build_chinese_journal_docx.py`](paper_experiments/build_chinese_journal_docx.py) | 构建 | 生成中文期刊 Word 稿 |
| [`build_research_snapshot.py`](paper_experiments/build_research_snapshot.py) | 构建 | 构建论文评估的可复现研究快照 |
| [`build_submission_docx.py`](paper_experiments/build_submission_docx.py) | 构建 | 生成 SoftwareX 投稿 Word 稿 |
| [`clean_snapshot_replay.py`](paper_experiments/clean_snapshot_replay.py) | 复现 | 在无 Git 元数据的 clean snapshot 中重放 |
| [`download_agent_model.py`](paper_experiments/download_agent_model.py) | 准备 | 下载并校验冻结的本地 agent 模型 |
| [`final_strategy_replay.py`](paper_experiments/final_strategy_replay.py) | 复现 | 多进程重放最终策略并比较规范化摘要 |
| [`integrate_workflow_results.py`](paper_experiments/integrate_workflow_results.py) | 整理 | 集成 workflow 实验结果和报告 |
| [`longitudinal_search.py`](paper_experiments/longitudinal_search.py) | 实验 | 执行冻结的纵向策略搜索轨迹 |
| [`prospective_multitask_workflow.py`](paper_experiments/prospective_multitask_workflow.py) | 实验 | 执行六任务 prospective workflow 和审计 |
| [`real_workflow_scaling.py`](paper_experiments/real_workflow_scaling.py) | 实验 | 评估真实工作流跨窗口耗时与内存 |
| [`render_chinese_journal_figures.py`](paper_experiments/render_chinese_journal_figures.py) | 渲染 | 生成中文稿图片 |
| [`render_submission_figures.py`](paper_experiments/render_submission_figures.py) | 渲染 | 生成 SoftwareX 投稿图片 |
| [`trading_sensitivity.py`](paper_experiments/trading_sensitivity.py) | 实验 | 执行交易语义敏感性分析 |
| [`workflow_comparison.py`](paper_experiments/workflow_comparison.py) | 实验 | 执行冻结的 workflow comparison |
| [`workflow_comparison_worker.py`](paper_experiments/workflow_comparison_worker.py) | worker | workflow comparison 隔离执行器 |
| [`workflow_trace_audit.py`](paper_experiments/workflow_trace_audit.py) | 门禁 | 复算纵向轨迹哈希、状态和连续性 |

## 论文实验内部模块

| 模块 | 用途 |
| --- | --- |
| [`__init__.py`](paper_experiments/__init__.py) | 论文实验包标记 |
| [`agent_workflow.py`](paper_experiments/agent_workflow.py) | 本地 agent workflow 实现 |
| [`codex_cli_agent_workflow.py`](paper_experiments/codex_cli_agent_workflow.py) | Codex CLI agent workflow 实现 |
| [`codex_cli_revalidation.py`](paper_experiments/codex_cli_revalidation.py) | Codex 候选重新验证实现 |
| [`common.py`](paper_experiments/common.py) | 协议、哈希和通用 I/O |
| [`contracts.py`](paper_experiments/contracts.py) | quantitative-domain contract benchmark |
| [`performance.py`](paper_experiments/performance.py) | 配对性能实验实现 |
| [`replay.py`](paper_experiments/replay.py) | 确定性 replay 系列实现 |
| [`synthetic.py`](paper_experiments/synthetic.py) | 论文实验合成输入生成器 |

## 策略作者内部模块

| 模块 | 用途 |
| --- | --- |
| [`__init__.py`](strategy_authoring/__init__.py) | strategy authoring 工具包导出 |
| [`names.py`](strategy_authoring/names.py) | 策略模块名和路径规范化 |

## 非 Python 支持文件

- [`benchmarks/v1_panel_native/`](benchmarks/v1_panel_native/)：正式 panel-native 基准 spec 和冻结 universe。
- [`perf_baseline.json`](perf_baseline.json)：端到端性能基线。
- [`hotpath_baseline.json`](hotpath_baseline.json)：热路径性能基线。
- [`paper_experiments/`](paper_experiments/) 中的 JSON、requirements 和 Dockerfile：冻结论文协议、
  manifest 与独立重放环境；不要脱离论文复现文档单独修改。

## 维护规则

新增、删除或重命名 `tools/**/*.py` 时必须同步本页。`tests/test_tools_index.py` 会检查每个 Python
文件都有对应链接，并拒绝指向不存在脚本的索引项。
