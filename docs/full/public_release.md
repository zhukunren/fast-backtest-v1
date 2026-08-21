# V1 Full Public Edition

This is the release contract for the complete V1 daily A-share backtesting
workspace, the project's only maintained public source surface. It includes the
Python engine, command-line workflow, data adapters, strategy API, Web service,
and Vue frontend.

## Clean install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item config\config.example.ini config\config.local.ini
```

Build the Web frontend before starting the service:

```powershell
cd web\frontend
npm ci
npm run build
cd ..\..
```

Configure TuShare, then run the local-price curve smoke first:

```powershell
$env:TUSHARE_TOKEN = "..."
python tests\test_offline_smoke.py
python main.py
python -m web
```

The Web service defaults to `http://127.0.0.1:5000`. The local fixture uses
synthetic price data and does not require a database, but curve/assess execution
still requires a configured TuShare calendar token.

## Market data

Users may provide a local Parquet panel, MySQL data, or TuShare-compatible data.
The required daily columns are `trade_date`, `stock_code`, `open`, `high`,
`low`, `close`, and `volume`. Real or vendor data is not included in this
repository; users are responsible for their provider agreement and schema.

Keep credentials outside Git:

```powershell
$env:MYSQL_PASSWORD = "..."
$env:TUSHARE_TOKEN = "..."
```

The Python data adapter uses the official TuShare service through the SDK.
Never publish a working personal token in source, examples, fixtures, logs, or
release artifacts.

## Web security

The Web service executes uploaded Python as trusted administrator code. It is
not a sandbox. Keep the service loopback-only for local use; remote deployment
requires a high-entropy `WEB_ADMIN_TOKEN`, HTTPS behind a trusted reverse proxy,
rate limiting, and an isolated process or container for untrusted strategy
code. See `docs/full/web.md` and `SECURITY.md` before exposing the service.

## Public strategy boundary

The CLI source includes the `offline_smoke` and `breakout_20` examples. The
Web catalog is intentionally seeded with the deterministic
`public_offline_smoke` demo; it has no private or generated strategy snapshots.
Generated, validated, private, and research strategies are excluded. Users can
add their own strategies under the documented strategy contract.

## Release verification

From a clean exported checkout, run:

```powershell
python tools\audit_full_public_surface.py
python tools\smoke_full_public_repo.py
```

The export source is generated from tracked files only:

```powershell
python tools\export_full_repo.py --clean
```

Development exports may temporarily include the exporter-owned release files
before they are committed. Before publishing, commit the reviewed release
changes, export from a clean worktree, and verify in
`FULL_PUBLIC_RELEASE_MANIFEST.json` that `source_worktree_dirty` is `false`,
`untracked_release_inputs` is empty, and `generated_from_tracked_files` is
`true`.

Do not publish `output/`, local data, caches, `config/config.local.ini`, or the
internal repository history. Publish the contents of the exported directory as
a new repository after an independent secret and license review.
