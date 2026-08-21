# Web Vue 3 Frontend

该目录用于承载 `web/` 子系统的 Vue 3 + TypeScript + Vite 前端工程。

当前定位：

- 作为 `web` 策略库的默认 Vue 3 前端
- 主信息架构为策略首页、回测详情、策略对比和受限管理台
- 生产入口通过 Vite manifest 加载构建产物

## 启动

先启动现有 Flask 服务：

```powershell
python -m web
```

再启动 Vue 开发服务器：

```powershell
cd web/frontend
npm install
npm run dev
```

默认访问：

```text
http://127.0.0.1:5173
```

开发模式下，Vite 会把 `/api/*` 代理到：

```text
http://127.0.0.1:5000
```

如需修改代理目标，可设置环境变量：

```powershell
$env:VITE_DEV_PROXY_TARGET='http://127.0.0.1:5000'
```

如果要让 Flask 页面在开发期直接加载 Vite 开发服务器，可设置：

```powershell
$env:WEB_VUE_DEV_SERVER='http://127.0.0.1:5173'
```

此时访问 Flask 的 `/`、`/strategy/<name>` 时，会直接注入 Vite 开发入口。

## 验证与构建

当前 Vite 8 与 Vitest 4 工具链支持 Node.js
`^20.19.0 || ^22.12.0 || >=24.0.0`，本仓库已使用 Node.js `24.6.0`
验证。安装锁定依赖并依次运行单元测试、类型检查和生产构建：

```powershell
cd web/frontend
npm ci
npm run audit:dependencies
npm run test:unit
npm run test:coverage:all
npm run typecheck
npm run build
```

`npm run test:unit` 使用 Vitest、Vue Test Utils、happy-dom 和 V8 coverage，
不会启动 watch 模式。当前稳定白名单包括：

- `src/api/**/*.ts`
- `src/utils/chart.ts`
- `src/utils/returnHeatmap.ts`
- `src/features/detail/holdingsAggregation.ts`
- `src/components/EmptyState.vue`

白名单对每个文件分别要求 statements `>=90%`、branches `>=80%`；当前
聚合报告为 statements `98.64%`、branches `92.74%`。独立的
`npm run test:coverage:all` 会纳入全部可执行 TypeScript/Vue 源码，只排除
测试夹具和纯类型声明，并硬性要求 statements `>=60%`、branches `>=50%`、
functions `>=55%`、lines `>=60%`。当前全源结果依次为 `62.75%`、`53.13%`、
`58.55%`、`65.03%`；白名单成绩不会被表述成整体覆盖率。

仓库级 full quality gate 使用 `--include-web-build` 时，会先执行 required
的 `npm run test:unit` 和 `npm run test:coverage:all`，通过后再执行
`npm run build`，最后审计前端 bundle。

`npm run audit:dependencies` 会分别审计完整依赖树和仅生产依赖树，并按
`dependency-audit-policy.json` 的零漏洞预算 fail-closed。当前 lockfile 的两个
scope 都是 `0` 项。Vite、PostCSS、Vue 和 ECharts 已升级到公告修复版；
`@vue/test-utils` 的旧 `js-beautify` 依赖由精确 override 升级，并固定其仅供
CLI 使用的 `nopt` 到兼容当前 Node 范围的版本。不得使用
`npm audit fix --force` 绕过这些兼容约束。

## 当前能力

- 策略首页：搜索、来源/状态筛选、排序、策略结果卡片、单策略/全部策略回测
- 首页回测：轻量区间弹窗、全局运行状态、完成后自动刷新
- 回测详情：关键指标、研究评分、净值、收益分析、持仓与交易、成交明细
- 详情操作：区间切换、单策略重跑、CSV/Excel/报告导出
- 数据接口：策略目录、详情、指标、净值、持仓、交易、复盘和回测状态
- Flask 默认入口加载 Vue 构建产物：
  - 生产构建通过 manifest 注入
  - 缺少构建产物时返回 `503 frontend build not found`
- 前端体积控制：
  - 详情页按路由异步加载
  - 图表面板按组件异步加载
  - ECharts 改为按需注册
  - Vite 构建会拆分 `vendor-vue` 与 `vendor-echarts`
- `api/`、`components/`、`features/`、`utils/` 按职责组织

对比页和管理台由主前端直接使用；日志接口仍作为运行诊断能力由后端提供。
