# Full Edition Data Onboarding

The full workspace supports three data modes.

## Local-Price Smoke

Use this first after configuring a TuShare token. It requires no database or external price file, but
curve execution still obtains the official trading calendar from TuShare:

```powershell
$env:TUSHARE_TOKEN = "..."
python tests\test_offline_smoke.py
python tools\data_quality_audit.py --target offline-smoke --sample-size 200
```

## Local Parquet Panel

Prepare daily OHLCV data with at least:

- `trade_date`
- `stock_code`
- `open`
- `high`
- `low`
- `close`
- `volume`

Then build or point `panel.path` to a local Parquet dataset:

```ini
[panel]
source = parquet
path = data/panel_cache/stock_daily.parquet
```

运行时交易日历始终要求配置 TuShare token；不要在本地数据目录放置 `trade_calendar.csv`，也不要将
价格 panel 的日期当作交易日历后备。

Validate before running long jobs:

```powershell
python tools\data_quality_audit.py --target local-source
python tools\hotpath_smoke.py
```

## MySQL and TuShare

Use environment variables for secrets:

```powershell
$env:MYSQL_PASSWORD = "..."
$env:TUSHARE_TOKEN = "..."
```

Build and verify the panel:

```powershell
python tools\build_panel_parquet.py --verify
```

Run a fixed slice check when real data is available:

```powershell
python tools\check_topn_slice.py --date 2025-05-16 --top 10
```

Do not commit vendor data, generated panels, database dumps, or local absolute
paths.
