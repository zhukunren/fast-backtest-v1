# Complete V1 Workspace

This is the complete V1 daily A-share backtesting workspace and the only
maintained implementation surface.

For a public source release, use the curated full-workspace export and its
dedicated candidate checks described in `docs/full/public_release.md`. The
development checkout still contains private research, local data, and
historical Git context and must not be pushed as-is.

The workspace provides:

- `python main.py` for the V1 CLI flow
- `python -m web` for the strategy library UI
- `app`, `backtest`, `core`, `data`, `trading`, and `runtime_lifecycle`
- `strategy_library/builtin`, `strategy_library/custom`,
  `strategy_library/validated`, and explicit generated-strategy experiments
- MySQL, TuShare, local CSV, and Parquet panel adapters
- multi-strategy backtests and performance tools under `tools/`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Web frontend build:

```powershell
cd web\frontend
npm ci
npm run build
```

For a full public release candidate, the frontend build is required. For a
backend-only local smoke check, it can be skipped.

## Configuration

Keep `config/config.ini` as the repository default. Put local overrides in
`config/config.local.ini`:

```powershell
Copy-Item config\config.example.ini config\config.local.ini
```

Provide credentials through environment variables, not committed files:

```powershell
$env:MYSQL_PASSWORD = "..."
$env:TUSHARE_TOKEN = "..."
```

The default strategy must stay in `strategy_library/builtin` or
`strategy_library/validated`. Generated strategies are experiments and require
explicit selection.

## First Run

All curve/assess runs, including the local-price smoke, require a configured TuShare token for the
official execution calendar. The fixture avoids a database and external price file only:

```powershell
$env:TUSHARE_TOKEN = "..."
python tests\test_offline_smoke.py
python main.py
```

Then run the Web strategy library:

```powershell
python -m web
```

Default Web URL:

```text
http://127.0.0.1:5000
```

## Validation

Run the commit-bound full gate before publishing or handing the workspace to
users. The worktree must be clean and the fixed TopN input must be available:

```powershell
python tools\audit_full_release.py
$fullGateSha = git rev-parse HEAD
$fullGateReport = "output/tools/full_quality_gate/full-$fullGateSha.json"
python tools\full_quality_gate.py --profile full --include-web-build --include-topn-slice --json-out $fullGateReport
python tools\validate_full_gate_report.py $fullGateReport --expected-profile full --expected-commit $fullGateSha --require-web-build --require-topn-slice
```

For a faster CI or pre-commit check:

```powershell
python tools\full_quality_gate.py --profile quick --skip-perf --skip-hotpath
```

See also:

- `docs/full/data_onboarding.md`
- `docs/full/web.md`
- `docs/full/engine_boundary.md`
- `docs/full/release_candidate.md`
