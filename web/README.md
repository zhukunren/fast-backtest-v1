# Web 策略库

`web/` 是主项目的策略库界面，并包含一个受限的本地维护台。前端以策略首页和回测详情页为主：
首页集中浏览、筛选和回测策略，详情页查看完整回测结果并执行单策略重跑或导出。

## 启动

```powershell
python -m web
```

默认地址：

```text
http://127.0.0.1:5000
```

回测/维护进度、owner lease 和互斥记录持久化到本地 SQLite，默认数据库为
`web/data/task_state.sqlite3`。它用于审计、stale-owner 恢复和所有权检查；同机外部 WSGI worker
共享 `WEB_TASK_STATE_DB` 时由 SQLite 事务协调任务，但这不是策略沙箱。development 模式只允许
loopback，启用 threaded Flask 并固定 `use_reloader=False`；远程
部署必须显式设置 `WEB_SERVE_MODE=production`、关闭 debug，并由 Waitress 运行在可信 TLS 反向代理
之后。`WEB_THREADS` 默认 4，可配置为 1 至 64。

本地状态默认每 5 秒 heartbeat，120 秒未更新的 owner lease 会被标记为取消；可用
`WEB_TASK_HEARTBEAT_SEC` 和 `WEB_TASK_LEASE_TIMEOUT_SEC` 调整，heartbeat 必须小于 lease 的一半。
跨主机共享协调必须由部署层外部服务提供。不可信策略必须在部署层独立进程或容器中隔离文件系统、
网络、凭据和资源；可配置线程、SQLite、静态检查和 owner lease 都不是沙箱。

## 边界

- 主产品面只服务策略库；维护台只执行受限策略管理和固定本地运维任务。
- 只调用主项目 V1 session facade。
- 不提供通用命令/任意文件 API、生成终端、生成服务、替代引擎接口或研究脚本入口；可信管理员上传
  的策略代码属于“维护台”章节单独披露的高权限执行边界。

## 策略目录

```text
web/strategy_library/
  strategy_name/
    config.ini
    strategy.py
    web_cache/artifact_generations/v1-schema-v2/
      current.json
      .staging/<generation_id>/
      generations/<generation_id>/
        manifest.json
        READY.json
        cache/
          meta.json
          net_value.json
          market_state.json
          trades.json
          daily_holdings.json
          positions.json
          position_changes.json
```

22 个与根策略库同名的目录通过 `config.ini` 直接引用 `strategy_library.*` canonical module，目录中
不再保存第二份 `strategy.py`。其源码 hash、详情描述和缓存身份都由同一个 resolver 绑定到根源码。
5 个 Web-only 策略和后台上传策略仍由 Web 侧拥有，并使用目录内相对 `.py` 路径。维护台只允许修改
Web-owned 包；canonical 条目为只读，避免 Web 管理动作改变根策略所有权。

缓存 reader 只接受 `current.json` 指向且通过 manifest、READY、schema、大小和 SHA-256
校验的 generation，不回退读取旧平面 `cache/`。失败回测也会原子发布仅含失败
`meta.json` 的 generation，从而隐藏过期成功结果。

缓存检查、回切和全量重建：

```powershell
python web/tools/manage_cache_generations.py list
python web/tools/manage_cache_generations.py inspect <strategy-name>
python web/tools/manage_cache_generations.py activate <strategy-name> <generation-id>
python web/tools/rebuild_cache_generations.py --report output/tools/web_cache_rebuild/final.json
python web/tools/check_strategy_library_health.py --strict --json
```

## 前端

Vue 3 前端位于 `web/frontend/`：

- `/`：策略列表、搜索、来源/状态筛选、排序、策略对比选择、单策略与批量回测。
- `/strategy/<name>`：指标、净值、收益分析、持仓、成交明细、重跑和导出。
- `/compare?names=a,b`：2-4 个策略的净值、超额、收益热力图、相关性和核心指标对比。
- `/admin`：受限策略安装/可恢复删除、固定行情/panel 更新、版本检查和 GitHub fast-forward 更新。

首页通过轻量区间弹窗发起回测，并在运行期间显示统一进度；勾选 2-4 个策略后可进入独立对比页。

```powershell
cd web/frontend
npm install
npm run dev
```

生产构建：

```powershell
cd web/frontend
npm run build
```

Flask 开发代理：

```powershell
$env:WEB_VUE_DEV_SERVER="http://127.0.0.1:5173"
python -m web
```

## 维护台

策略上传只接受 `.py` 或 `.zip`，并执行路径、文件数/大小、静态源码风险筛查和策略合同校验；覆盖与
删除会把旧目录移到 `web/data/admin/deleted_strategies/`。该筛查不是沙箱：上传的 Python 必须来自
可信管理员，策略运行时拥有 Web 服务进程可访问的文件、网络和凭据权限。`WEB_ADMIN_TOKEN` 因而等价
于代码执行凭据，不得用于接收或试跑不可信第三方代码。需要不可信代码支持时，必须在独立进程或容器
中隔离文件系统、网络、凭据和资源，本项目当前不提供该能力。

其他维护 API 与回测任务互斥，只调用服务端固定白名单中的脚本和 Git 命令。除可信策略安装这一明确
边界外，维护面不提供通用 shell 或任意文件 API。管理 API，以及策略 config 读取、cache 删除、日志读写、
run-event 读取、回测启动和回测状态 API，共用同一授权边界且响应 `Cache-Control: no-store`：默认只允许
loopback 绑定和 loopback 调用；只要配置了 token，
这些敏感接口都必须提供该 token。未配置 token 时，绑定地址、调用方地址和请求 `Host` 必须同时为
loopback；因此本机反向代理若要对外服务也必须配置 token。`WEB_HOST` 对外绑定但未配置 token 时，
敏感接口会 fail closed。
单个 `.py` 会在服务器端生成对应 `config.ini`；安装成功后立即出现在 `/` 的策略目录和
`/admin` 的服务器策略列表中。远程维护必须通过 TLS 终止的可信反向代理或受控 VPN 访问，并使用
密码学随机生成的高熵 token；推荐让内置服务继续只监听 loopback：

服务器策略列表支持重命名。重命名会校验新的策略标识和显示标题，移动完整策略目录并保留其中的
`web_cache`、运行记录和其他本地结果；目标标识已存在或维护任务正在运行时会拒绝操作。管理员也可以
只提交新标识，保留现有显示标题。

```powershell
$env:WEB_ADMIN_TOKEN="<secret>"
$env:WEB_HOST="127.0.0.1"
$env:WEB_SERVE_MODE="production"
$env:WEB_PUBLIC_ORIGIN="https://backtest.example.com"
python -m web
```

反向代理必须只把 HTTPS 请求转发到该 loopback 监听端，限制请求速率/上传大小，保护访问日志，并且
不得信任来自公网客户端的转发头。`WEB_PUBLIC_ORIGIN` 必须是反向代理对浏览器公开的单一 HTTPS origin；
服务只使用该显式值校验同源请求，不使用 `Forwarded` 或 `X-Forwarded-*` 推导授权来源。production 模式会
拒绝 `WEB_DEBUG=1`、非 HTTPS 的公开 origin 和少于 32 字符的 token，
并让 Waitress 清除不可信代理头；绝不能通过明文 HTTP 发送 token。远程客户端在
`X-Web-Admin-Token` 请求头中提供该值；主前端会复用 `/admin` 当前会话保存的 token。浏览器敏感请求
只接受同源 `Origin`，或 `WEB_CORS_ORIGINS` 中逐项列出的可信来源；通配符不授予敏感接口权限。无
`Origin` 的 CLI 和 Flask test client 保持兼容。版本更新要求 GitHub remote、clean
worktree、非 detached branch、无分叉，并且只执行 fast-forward；更新完成后可能需要重启服务。

主 Vue 前端复用 `/admin` 当前会话保存的 session token；敏感请求继续通过统一的
`X-Web-Admin-Token` 头授权。图表依赖由 Vite 构建并随前端产物提供，不加载外部 CDN 脚本。

## API

常用接口：

- `GET /api/library`
- `GET /api/library/<name>/detail`
- `GET /api/library/<name>/net_value`
- `GET /api/library/<name>/trades/summary`（详情页首屏摘要；仅返回列、计数、最近信号和有限预览）
- `GET /api/library/<name>/trades/records`（按需加载完整交易记录）
- `GET /api/library/<name>/holdings/summary`（详情页首屏摘要；仅返回日期、日摘要和最新有效持仓日预览）
- `GET /api/library/<name>/holdings/history`（按需加载完整持仓历史）
- `GET /api/library/<name>/market_states`
- `GET /api/library/<name>/review`
- `GET /api/compare/enhanced?names=a,b`
- `POST /api/backtest/run`
- `GET /api/backtest/status`
- `GET /api/admin/status`
- `GET /api/admin/task/status`
- `POST /api/admin/strategies/upload`
- `PATCH /api/admin/strategies/<name>`（JSON：`{"name":"new_name","title":"新标题"}`）
- `DELETE /api/admin/strategies/<name>`
- `POST /api/admin/data-update/run`
- `POST /api/admin/version-update/run`

对比、运行状态和维护接口由主前端直接使用。维护面不开放通用命令或任意文件 API；可信管理员上传的
策略代码属于上文明确披露的代码执行边界。

## 本地数据

- Web 运维日志默认写入 `output/logs/web/`，可用 `WEB_LOG_DIR` 覆盖；单次回测日志随 run 保存在
  `<run-output>/run_meta/`（默认 `output/pipeline/run_meta/`），不参与运维日志保留期清理。
- 前端构建产物在 `web/frontend/dist/`。
- 策略回测缓存写入各策略目录的 `web_cache/artifact_generations/`。
- 全量重建报告默认写入 `output/tools/web_cache_rebuild/`。
