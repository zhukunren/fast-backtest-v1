# Long-Window Validation

Long-window validation is the reproducible evidence path for real-data runs that
are too expensive or environment-specific for the default smoke suite.

## Local Or Nightly Command

```powershell
python tools/run_long_window_validation.py --case mysql_real --start-date 20240101 --end-date 20251231 --stock-limit 500 --json-out output/tools/long_window_validation/latest.json
```

Use `--allow-skip-on-error` only on developer machines where MySQL or panel data
is intentionally unavailable:

```powershell
python tools/run_long_window_validation.py --allow-skip-on-error
```

CI or nightly jobs should omit `--allow-skip-on-error`, so missing data,
misconfigured credentials, or performance-budget failures stop the run.

## Scheduled Validation

`.github/workflows/nightly-long-window.yml` runs the fixed 2024-01-01 through
2025-12-31 window at 02:30 Asia/Shanghai on business days. It uses the
`self-hosted`, `windows`, and `long-window-data` runner labels because the
runner must own `data/panel_cache/stock_daily.parquet`, its matching manifest,
and private local configuration. The profile reads selected stock codes from
the Parquet manifest rather than enumerating MySQL tables.

The workflow deliberately does not use `--allow-skip-on-error`. A missing
snapshot, unavailable MySQL instance, invalid credentials, or failed profile
must fail the job. It uploads the wrapper report and profile JSON from
`output/tools/long_window_validation/` on every run for diagnosis.

## Performance Budget

Pass a budget file when the run should enforce elapsed or stage limits:

```powershell
python tools/run_long_window_validation.py --budget docs/validation/performance_budget.example.json --baseline output/tools/long_window_validation/baseline.json
```

The wrapper writes:

- a profile JSON from `tools/profile_backtest.py`;
- an optional budget JSON from `tools/performance_budget.py`;
- a top-level long-window report containing commands, paths, return codes, and
  final status.

## Data Requirements

The `mysql_real` case supports either local MySQL or a configured Parquet
panel. Secrets must come from environment variables such as `MYSQL_PASSWORD`;
do not commit local credentials or generated reports.
