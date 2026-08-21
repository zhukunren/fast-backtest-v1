# Validation 文档

本目录包含数据校验与数据契约相关文档。

- `pydantic.md`：Pydantic 模型、运行时校验与 SoftwareX 合成实验边界说明
- `external_validation.md`：外部行情身份、五引擎配对、券商对账、资源报告与 deferred 状态协议
- `long_window.md`：真实数据长窗口 profile、预算和夜间验证说明
- `performance_budget.example.json`：`tools/performance_budget.py` 的示例预算
- `strategy_validation.md`：统一策略验真入口、协议结构与报告输出说明
- `strategy_validation_protocol_template.json`：默认策略验真协议模板
- `docs/agent/CODEX_ONLY_WORKFLOW.md`：Codex campaign、冻结 shortlist、一次 sealed holdout claim 与 Bonferroni 原型边界
- `tools/codex_strategy.py`：Codex 可信本地独立进程 backtest / validate 主入口
- `tools/codex_campaign.py`：Codex campaign protocol-hash / init / status / freeze 主入口
- `tools/data_quality_audit.py --target panel-manifest`：审计 Parquet panel manifest、hash、覆盖范围和字段
- `tools/data_quality_audit.py --target point-in-time`：审计 `available_at <= trade_date` 的点时间约束
- `constraints-ci.txt`：固定 CI/发布验证所用的直接依赖版本；运行元数据同时记录关键库版本
- `tools/build_validation_protocol.py`：生成 rolling / walk-forward 诊断窗口；不会自动赋予 OOS 身份

默认协议中的 `diagnostic_first_half` / `diagnostic_second_half` 是可重复诊断窗口，不是 OOS。
协议生成器只划分窗口，不自动赋予 sealed 身份。一次性 holdout claim 必须先预注册入口源码候选
预算、冻结 shortlist 与入口文件，再通过独立未查看协议运行；当前统计 gate 使用
`familywise_alpha / max_candidates`。现有 campaign 尚未绑定依赖/config/window/data/engine/task 联合身份，
正均值 p-value 也未校准收益序列相关，所以当前流程只能作为受限治理原型，不能称为完整 sealed
确认。

`legacy` 和 `broker` 是独立、受支持的引擎，结果不要求一致。比较协议要求相同 engine 和相同
protocol/config/data identity；当前 campaign 不会自动跨 discovery attempt 强制这些条件，调用方或
实验 harness 必须显式比较。`broker_replay_full` 只作可选跨引擎观察。

长窗口工具：`tools/run_long_window_validation.py`

长窗口验证入口：

```powershell
python tools/run_long_window_validation.py --case mysql_real --start-date 20240101 --end-date 20251231 --stock-limit 500
```

夜间固定窗口任务由 `.github/workflows/nightly-long-window.yml` 在具备本地
数据快照和私有配置的 self-hosted runner 上执行；运行要求见 `long_window.md`。
