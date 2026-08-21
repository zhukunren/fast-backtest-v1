# Docs Index

Status: active
Last verified: 2026-08-21

本页是主项目文档的唯一总入口。子系统细节由各自索引维护；已完成计划、旧审计和评分
快照不留在活动文档树中，需要时从 Git 历史取回。

当前公共发行面只有完整 V1 工作区；独立 alpha 公共包已退役，Web catalog 只读取 canonical
策略源码和外部 LLM 描述，不保留第二套语义实现。

## 权威层级

- **canonical**：`docs/handbook.md`、`docs/architecture/CURRENT_ARCHITECTURE.md`、
  `docs/PRODUCT_BOUNDARY.md` 和对应的 API/validation 合同；描述当前实现事实。
- **reference**：`docs/CHANGE_RECIPES.md`、本页和各子系统 README；负责入口和验证路由，规则源是
  `config/change-impact.json`。
- **historical**：已关闭计划、审计快照和论文材料；除非明确标注当前边界，否则不能作为实现依据。

## 从这里开始

- `docs/agent/CODEX_PLUGIN.md` - JSHS companion plugin 的安装形态、完整 checkout 依赖和公开发布边界。
- `OPEN_SOURCE_RELEASE.md` - 开源边界、完整公共源码导出、数据授权和发布前检查。
- `docs/full/public_release.md` - V1 完整功能公共发行版的范围、安装、数据、Web 安全和发布验证。
- `docs/full/public_readme_zh.md` - 公共仓库根目录 `README_CN.md` 的中文安装、运行和边界说明。
- `docs/handbook.md` - V1 panel/single/fixed-rebalance 唯一运行合同、配置、交易语义、缓存、Web 和验证手册。
- `docs/CHANGE_RECIPES.md` - 按变更面选择本地最小验证和 CI/full gate 的路由表；机器规则见 `config/change-impact.json`。
- `tools/output_gc.py` - `output` 历史缓存/临时目录迁移与共享 `.cache`、`.tmp` 过期项清理；所有变更默认 dry-run，研究证据目录默认排除。
- `docs/paper_llm_agent_strategy_engine_draft.md` - Codex 驱动纯规则策略搜索与回测系统的 SoftwareX 中文初稿；已填写 22 轮首次达标轨迹、三次最终重放、合同基准、内部等价性能和真实命令扩展结果。
- `docs/paper_submission/README.md` - SoftwareX 英文主稿、补充材料、22 轮纵向轨迹、无任务级上限的六任务 28 候选活动、六次 2024-2025 封存失败、双重审计、排除实验、敏感性与重放证据、投稿图和 Word 稿入口；三位作者顺序、共同一作、单位、邮箱、通讯地址、ORCID、CRediT、无利益冲突声明、公开 release 和真实归档 DOI 均已记录，597 文件候选已通过两轮清单审计、27 项专项测试、离线 smoke 和轨迹复算；2026-08-17 的 SoftwareX/Elsevier 政策与本地字段渲染核验已记录，仍需 Editorial Manager 转换检查。
- `docs/paper_submission/research_snapshot_release.md` - 论文所评估 V1 软件的公开研究快照边界、候选构建、快照内测试、22 候选轨迹及六任务开放式活动复算命令，并记录 2026-08-17 的 597 文件最终候选验证结果、GitHub release `softwarex-v1.0.0` 和 Zenodo DOI `10.5281/zenodo.21979118`；同时记录官方 SoftwareX 模板哈希和投稿包政策核验。
- `docs/paper_submission/independent_reproduction_protocol.md` - 独立第三方分阶段复现协议：先审计不可变 release，再在配置 TuShare 日历访问但无外部行情文件的条件下做禁用缓存的本地行情检查和 162 样本合同基准，最后由有授权数据的复现者重放冻结策略；明确不把已知封存结果包装成新的封存验证。
- `docs/chinese_journal_submission/README.md` - 不绑定具体刊物模板的中文期刊备选投稿包；定位为 Codex 辅助规则策略开发的专用迭代工程系统，包含中英文摘要、六任务 28 候选与六次封存结果、162 样本窄基线、中文独立第三方复现协议、经逐页审计的 A4 Word 主稿、7 张表、4 幅正文中文图、完整 V1 快照边界和投稿前信息清单。
- `docs/PRODUCT_BOUNDARY.md` - 产品保留面、排除面、Web 可信管理员威胁模型和防回潮规则。
- `docs/architecture/CURRENT_ARCHITECTURE.md` - 当前架构、panel/single/fixed-rebalance 唯一执行合同、
  稳定 panel identity、immutable snapshot/current、分层 hash 校验、OHLCV 审计策略、Web 可配置线程、
  同机 SQLite 协调边界与 Web catalog 直引 canonical 策略源码合同。
- `docs/architecture/MAINTAINABILITY_DEBT_REGISTER.md` - V1 当前体积/复杂度/覆盖率棘轮、已完成债务和
  外部进程/容器隔离等剩余风险。
- `docs/architecture/V1_PANEL_NATIVE_PERFORMANCE_BREAKING_REFACTOR_PLAN_2026-08-05.md` -
  `CANDIDATE_IMPLEMENTED_FORMAL_AB_PENDING` 的 V1 panel-native 性能破坏性重构记录；候选切换和本地
  门禁已落地，原冻结 panel 当前不能从 Git 恢复，独立 clean-worktree 正式配对 A/B 仍待完成。
- `docs/architecture/PANEL_HYBRID_EXECUTION_IMPLEMENTATION_PLAN_2026-08-05.md` - `ABANDONED` 的混合执行
  历史方案，仅记录被否决的逐股多进程和兼容路径设计。
- `docs/architecture/V1_UNIFIED_BREAKING_CUTOVER_PLAN_2026-07-28.md` - 已以 `DONE` 关闭的初始短工期
  代码计划，仅保留历史范围与预算，不描述当前执行合同，也不作为后续改进入口。
- `docs/architecture/V1_DEBT_ARCHITECTURE_CLOSEOUT_2026-07-25.md` - V1 清债最终分类和可恢复证据协议。

## 运行与验证

```powershell
python main.py
python -m web
python tools/verify_changed.py
python tools/verify_changed.py --execute
python tools/benchmark_v1_panel_native.py verify-inputs
```

完整 `full_quality_gate`、覆盖率、类型和前端构建默认由 `.github/workflows/full-ci.yml` 在 push/PR 中运行；
发布候选的 `full` / TopN / D4 协议仍按 `docs/handbook.md` 执行。

运行时交易日历只使用配置的 TuShare client；`LOCAL_DATA_DIR` / `SAMPLE_DATA_DIR` 只可提供本地行情和
股票池 fixture，不能绕过交易日历凭据。所谓 offline smoke 只自带价格 fixture，curve/assess 运行仍需要
有效的 TuShare 日历访问。client 直接使用官方 TuShare SDK，endpoint 由 SDK 管理；token 优先读取非空
`TUSHARE_TOKEN`/`TS_TOKEN`，配置文件中的 token 仅作兜底。

`fixed_rebalance` 会在每日开盘阶段冻结目标数量，再按执行阶段处理订单：
`open -> close` 时开盘卖出所得可用于收盘买入，反向的 `close -> open` 不会
提前使用收盘卖出资金。成交价只允许 `open` 或 `close`，避免用 `high/low`
或同日收盘结果反推同日订单数量。

共享 V1 输出根在一次会话的清理、回测、诊断和 generation 发布期间由进程锁
串行保护；等待超时可通过 `BACKTEST_OUTPUT_LOCK_TIMEOUT_SEC` 调整。

日常可变 panel 与正式冻结输入分开管理。先用 `tools/manage_panel_snapshots.py freeze` 生成
`versions/<dataset_id>` 和原子 `current.json`，再在 benchmark 明确执行 `freeze-inputs` 更新新 spec；
`verify-inputs` 会对冻结文件做完整 SHA-256 并 fail closed。当前命令失败表示旧冻结输入不可用，不能
直接改旧 spec hash 来修绿；schema-v2/flat 旧 manifest 也不能直接 freeze，必须先用当前 builder 重建。
需要恢复原 panel 或开启新的显式 evidence series。

涉及热路径、行情切片、缓存、状态机、净值、批量调度或 panel 路径时，本地规划器会追加 quick 诊断；正式
性能证据仍按 handbook 的 canonical 协议执行：

```powershell
python tools/hotpath_smoke.py --quick
python tools/check_topn_slice.py --date 2025-05-16 --top 10
```

固定回归组、完整门禁和各类专项验证以 `docs/handbook.md` 的“验证”章节为准，不在索引中
复制维护。

## 分类索引

- `docs/architecture/README.md` - 当前架构、唯一活动代码计划、维护性债务和长期约束。
- `docs/validation/README.md` - 数据、策略和长窗口验证协议。
- `docs/validation/pydantic.md` - Pydantic 抽样边界，以及 PIT、目标权重、行为探针、OHLCV 和 TopN 的
  非抽样验证合同。
- `docs/validation/external_validation.md` - 未来外部行情、全引擎配对、券商对账与资源报告的严格状态协议。
- `docs/agent/README.md` - Codex 单策略高速迭代入口，以及显式正式验证和兼容边界索引；目录内已实施
  proposal 仅作历史记录，并显式指向当前合同。
- `docs/strategy_field_dictionary.md` - V1 日线策略字段、单位、加载声明、`fields` 查询和保留列口径。
- `docs/full/README.md` - 完整工作区交付说明；正式候选步骤由 `docs/full/release_candidate.md` 定义，公共源码发布合同见 `docs/full/public_release.md`。
- `tools/export_full_repo.py` - 只从 Git 已跟踪文件导出 V1 完整功能公共候选，不改写内部历史。
- `tools/audit_full_public_surface.py` / `tools/smoke_full_public_repo.py` - 完整公共候选的卫生审计和离线/Web smoke。
- `tools/INDEX.md` - 101 个 Python 工具入口与内部模块的全量分类索引；详细命令见 `tools/README.md`。
- `tools/manage_panel_snapshots.py` - panel snapshot 的 `freeze`、`inspect`、`activate`；普通回测只
  使用 manifest/READY 身份，不重复扫描整个 panel。
- `web/README.md` - Web 策略库、服务器策略管理（含策略重命名）、数据更新和 GitHub 更新运维。
- `web/docs/README.md` - Web 子系统规则和发布证据索引。
- `strategies/templates/README.md` - 唯一默认 `iterative_template.py`、rule/factor grouped panel batch 路径、专家
  full-panel 路径、按需能力和严格输出合同。
- `strategies/API.md` - 策略作者唯一公共 API、三阶段列所有权、活动 V1 能力清单、descriptor 生命周期、
  panel/PIT/参数工具与运行时能力边界。

## 历史与独立项目

- `docs/archive/README.md` - 已删除历史文档的 Git 取回方法和替代入口。
- `projects/engine_v2/README.md` - 独立 V2 项目，本轮不处理。
- `projects/engine_v3/README.md` - 独立 V3 项目，本轮不处理。
