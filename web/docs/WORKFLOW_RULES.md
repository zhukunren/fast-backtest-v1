# Web 文档规则

先阅读相关代码、`web/README.md` 和 `web/docs/README.md`，再实施 Web 修改。

只有用户流程、公开 API、目录边界、缓存协议或架构合同发生变化时，才更新对应文档；
普通修复与内部重构不新建计划文档。新增活动文档必须同步加入 `web/docs/README.md`。

修改后运行与风险相称的后端测试和前端类型/构建检查。涉及缓存、批量调度或热路径时，
同时按根目录 `AGENTS.md` 运行性能与 hotpath 门禁。

每个 `strategy_library/<name>/strategy.py` 必须自包含全部策略指标和信号计算。禁止导入、
动态加载或调用其他策略模块，禁止相对导入，也禁止在同一策略目录增加第二个 Python 辅助模块。
项目内只允许导入 `strategies.api`、`backtest.panel_native`、`core.dataframe_utils` 和
`data.timing_readers` 这四类运行框架接口；NumPy/Pandas 等基础依赖不受此限制。不得新建公共指标、
市场状态、排序或信号模块供多个策略复用。维护台上传、策略库健康检查和测试门禁必须共同执行这项约束。

历史方案放在 `web/docs/archive/`，不得作为当前事实源或要求后续实现持续回写进度。
