# Full Edition Web

The Web app is a strategy library UI for the V1 full workspace.

Start the backend:

```powershell
python -m web
```

Default URL:

```text
http://127.0.0.1:5000
```

Backtest and maintenance progress, owner leases, and mutual-exclusion records
are persisted in local SQLite at `output/web/task_state.sqlite3` by default.
This provides local audit and stale-owner recovery, not a strategy sandbox.
Development mode uses threaded Flask, disables the reloader, and only accepts a
loopback bind. For remote maintenance, set
`WEB_SERVE_MODE=production`, keep `WEB_HOST=127.0.0.1`, disable debug, configure
a `WEB_ADMIN_TOKEN` of at least 32 characters, and set `WEB_PUBLIC_ORIGIN` to the
reverse proxy's exact public HTTPS origin. Waitress uses `WEB_THREADS`, from 1
through 64 with a default of 4. It must remain behind a trusted TLS reverse
proxy that rate-limits requests and protects logs.

Same-host external WSGI workers are supported only when every worker shares
`WEB_TASK_STATE_DB`; SQLite transactions and owner leases then coordinate the
task slot. This coordination does not isolate strategy code.

`WEB_TASK_HEARTBEAT_SEC` defaults to 5 seconds and
`WEB_TASK_LEASE_TIMEOUT_SEC` defaults to 120 seconds. The heartbeat must remain
less than half the lease timeout. A dead owner's task is marked cancelled when
the lease expires. Cross-host coordination must be supplied by deployment-owned
infrastructure. Running untrusted strategies additionally requires separate
processes or containers with filesystem, network, credential, and resource
isolation; configurable threads do not provide that boundary.

Sensitive config/cache/log/run-event/backtest-run/backtest-status responses are
authenticated through the same token boundary and are always `no-store`.
Browser origin authorization uses only `WEB_PUBLIC_ORIGIN` and explicit
`WEB_CORS_ORIGINS`; it does not trust `Forwarded` or `X-Forwarded-*`. Both Vue
and legacy clients use the same session token. The legacy ECharts URL is exact
and carries SHA-384 SRI plus `crossorigin=anonymous`; its CSP source is locked by
backend contract tests.

For frontend development:

```powershell
cd web\frontend
npm ci
npm run dev
```

The Vite 8 and Vitest 4 toolchain supports Node
`^20.19.0 || ^22.12.0 || >=24.0.0`. The current workspace has also been
verified with Node `24.6.0`.

Use the Vite dev server through Flask:

```powershell
$env:WEB_VUE_DEV_SERVER = "http://127.0.0.1:5173"
python -m web
```

Run the frontend verification sequence before producing a release build:

```powershell
cd web\frontend
npm ci
npm run test:unit
npm run test:coverage:all
npm run typecheck
npm run build
```

`test:unit` is non-interactive and keeps the stable whitelist at per-file
statements `>=90%` and branches `>=80%`; its current aggregate is `98.64%` /
`92.74%`. `test:coverage:all` independently includes all executable
TypeScript/Vue sources and enforces `60%` statements, `50%` branches, `55%`
functions, and `60%` lines. Its current result is `62.75%` / `53.13%` / `58.55%`
/ `65.03%`.

From the repository root, `--include-web-build` makes the full quality gate run
both required coverage steps before the build and then audit the bundle:

```powershell
python tools\full_quality_gate.py --profile ci --include-web-build
```

Run `python tools/audit_frontend_dependencies.py` from the repository root to
audit both the complete and production-only lockfile scopes. The machine-readable
policy allows zero vulnerabilities in either scope and fails closed on registry,
JSON, count, or exit-status errors. Both scopes are currently at zero after the
Vite, PostCSS, Vue, and ECharts upgrades. Do not use `npm audit fix --force`;
dependency updates must preserve the supported Node/Vite/Vitest combination.

The Web app reads strategy entries from `web/strategy_library/<name>/` with:

- `strategy.py`
- `config.ini`

Runtime cache, logs, and generated result files are local artifacts. They must
not be committed.
