# Product Boundary

Status: active
Last verified: 2026-08-18

主产品线是性能优先的日线级 V1 回测系统。支持范围包括：

- V1 回测引擎、结果合同、数据适配器和发布门禁。
- slot ownership、现金执行、订单、成交和净值生命周期。
- 版本化策略源码与配置。
- Web 策略目录、详情、比较、回测、导出和 cache generation。
- 受限 Web 维护台：经校验的策略安装/可恢复删除、固定行情/panel 更新和 GitHub fast-forward 版本更新。
- 从已跟踪文件生成并独立验证的完整 V1 公共源码候选。

不属于主产品线的内容：

- `projects/engine_v2/` 和 `projects/engine_v3/`；二者是独立项目，主项目不得 import、配置或调用。
- publication control plane、生成语料、review bundle 或外部实验编排。
- 已移除的研究与生成式运行面。
- 通用任意文件写入/命令执行 API 或非 fast-forward 版本切换；可信管理员上传并运行策略代码是下文
  单独披露的高权限边界，不属于不可信代码执行能力。
- 未经 loopback 限制或 `WEB_ADMIN_TOKEN` 鉴权的远程维护调用。
- 外部 engine 注册、目录扫描、普通配置任意 import、package entry point 自动发现或 Web engine
  注册接口；五个 V1 engine 只来自代码内冻结的 built-in registry。
- 生成缓存、日志、构建产物和本地凭据的 Git 跟踪。
- 第二套策略、回测或数据实现，以及与完整工作区平行的包、导出器、CI 或发布流程。

## 变更规则

- 不为单个策略向 engine、reader 或 cache 增加例外分支。
- pending/partial sell 在股数归零前继续占有 slot。
- held-stock 缺行情时可以用 last close 估值，但不得生成成交。
- 没有 held-quote resolver 的底层现金执行在替换活跃持仓时必须 fail fast。
- cache generation 只从权威策略源码和配置重建，不复制旧缓存进入 release。
- 维护 API 只能调用代码内固定的任务白名单，必须校验参数，并与回测任务互斥。
- 策略上传只接受受限 `.py/.zip`，必须经过路径、大小、静态风险筛查和策略合同校验；覆盖与删除必须
  可恢复。静态筛查不是沙箱，上传者必须是可信管理员，且 `WEB_ADMIN_TOKEN` 等价于服务进程权限下的
  代码执行凭据；不可信代码必须在本项目之外使用 OS/容器级文件、网络、凭据和资源隔离。
- 未配置 token 时维护 API 仅接受 loopback；远程访问必须配置 `WEB_ADMIN_TOKEN`。
- 远程维护还必须经 TLS 或受控 VPN/可信反向代理，使用高熵 token、请求限速和受保护审计日志；不得
  通过明文 HTTP 发送 token。
- Flask development 启动模式只允许 loopback；远程维护必须使用 `WEB_SERVE_MODE=production`、关闭
  debug、配置至少 32 字符的 token，并由受控线程数的 Waitress 在可信 TLS 代理后运行。同机多进程
  必须共享 `WEB_TASK_STATE_DB`，由 SQLite owner lease 和事务互斥协调后台任务。
- 不新增包装单一调用点的通用 facade、manager、plugin 或平行 cache 入口。
- 不建立轻量包或兼容层；公共交付只裁剪完整工作区，不复制领域实现。
- 不恢复已删除运行面，不把独立 V2/V3 接回 V1 CLI、Web、配置或策略加载链。
- 不为 engine registry 增加运行期 mutation、第三方 discovery 或任意 artifact writer；外部扩展必须
  先有具名消费者、API/Artifact ownership、安全隔离和供应链方案，再单独批准产品边界变更。
- 不按文件数量硬砍仓库；只有明确职责、测试边界和债务指标时才拆分。
- 活动文档只描述当前事实或当前计划，完成记录交给 Git 历史。

## 外部 Engine 重开缺口

当前没有具名真实消费者或可运行的自定义 engine 原型，因此不重开外部注册。未来评审前还必须同时
补齐以下缺口：

- 自定义 artifact 的命名空间、schema owner 和 generation path/cardinality 方案。
- engine API version、兼容周期、弃用责任和故障隔离合同。
- 对“自定义 engine 等价于受信任 Python 代码”的明确披露，不得宣称当前进程内执行是沙箱。
- 安装包 allowlist、依赖锁定、供应链审计和部署权限边界。
- 进程隔离、超时、CPU/RSS 预算以及数据库和凭据访问评估。
- 独立批准的产品边界变更，并继续保持 V2/V3 与 V1 隔离。

即使完成重开评审，也禁止目录扫描和普通配置任意 import；package entry point 只能作为另案评估的
显式 allowlist 候选，不属于当前已批准能力。

## 验证

边界或文档入口变化至少运行：

```powershell
python -m pytest tests/test_documentation_contracts.py tests/test_import_boundaries.py -q
```

代码或架构变化还必须遵守 `AGENTS.md` 和 `docs/handbook.md` 的性能、热路径及完整门禁要求。
