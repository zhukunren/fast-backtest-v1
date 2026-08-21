# Roadmap

Status: active
Last verified: 2026-08-18

JSHS 只有一个受支持的实现：完整 V1 工作区。这里记录该工作区的后续演进；
独立 V2/V3 项目和论文归档不属于本路线。已完成的迁移与清债证据保留在 Git
历史以及 `docs/architecture/MAINTAINABILITY_DEBT_REGISTER.md`。

## 当前优先级

- 保持策略语义边界：Web 只消费经过校验的 LLM `strategy_description.json`，不解析源码、不编译条件树、不提供语义 fallback。
- 保持单一运行时：完整 V1 的 `strategies`、`backtest`、`web` 和工具链是唯一实现；删除的轻量公共包、旧 schema 和兼容别名不得回归。
- 保持发布可复现：`tools/export_full_repo.py` 生成唯一 full-public 候选，随后执行完整 surface audit、离线 smoke 和结构化门禁。
- 保持策略作者合同：模板、authoring capability、静态检查和 panel-native 执行链共享同一公共 API，所有输入键、行序和 OHLCV 不变量 fail closed。
- 保持 Web 可运营性：策略缓存使用 schema-v2 不可变 generation，管理操作可恢复，管理员代码执行边界由部署层隔离。

## 近期工作

- 补齐 full workspace 的 Python、Web 前端、发布候选和性能门禁，并将报告绑定到工作树身份。
- 降低普通框架的文件体积、圈复杂度和覆盖率缺口；策略生产源码不通过复制实现或薄 wrapper 规避约束。
- 为真实数据、全引擎配对、容量、RSS、发布演练和券商成交校准建立独立的可复现证据；离线 fixture 不得冒充生产认证。
- 将部署要求落地到运行手册：不可信策略使用独立进程或容器，跨主机 Web 使用部署层协调服务。

## 长期工作

- 在明确 owner、数据合同和门禁后扩展 PIT、position policy、portfolio construction 等能力；未具备合同前继续 fail closed。
- 持续维护 full-public 导出边界、依赖锁定、许可证和安全审计，确保导出内容与完整 V1 源码身份一致。
- 只在有行为等价证据时合并架构改动；任何破坏性迁移都删除旧入口，不建立兼容过渡层。

## 不在路线中

- 不恢复 `src/jshs` 轻量包、独立 wheel/TestPyPI 发布、旧 public CI 或旧 `execution_rules.json` schema。
- 不把 Web 变成解释器、语义编译器或策略执行器；回测执行仍由 V1 engine owner 负责。
- 不将历史论文/投稿材料中的旧 alpha 工件描述当作当前产品合同。
