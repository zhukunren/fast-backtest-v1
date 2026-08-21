# JSHS V1 Full Public Edition

[中文](README_CN.md) | English

Chinese setup guide: `README_CN.md`.

JSHS V1 is an LLM-driven A-share strategy production factory. A user supplies
a research objective; the workflow fixes the measurement contract, iteratively
authors one deterministic strategy, runs the real backtest, and emits
auditable evidence. A strategy is qualified only when its data, point-in-time,
schema, causality, signal, tradability, execution, cost, and NAV checks pass
under the declared contract. This qualification is not a promise of future
profitability or a live-trading recommendation.

JSHS V1 is a daily A-share backtesting workspace with a Python engine, CLI,
data adapters, strategy library, batch tools, Web strategy review service, and
Vue frontend. This public edition is a curated source release; it does not
contain real market data, vendor responses, credentials, private research
strategies, V2/V3 projects, or paper evidence.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
Copy-Item config\config.example.ini config\config.local.ini
$env:TUSHARE_TOKEN = "..."
python tests\test_offline_smoke.py
```

For the Web UI:

```powershell
cd web\frontend
npm ci
npm run build
cd ..\..
python -m web
```

Open `http://127.0.0.1:5000`. Full setup, data onboarding, and deployment
security are documented in `docs/full/public_release.md`,
`docs/full/data_onboarding.md`, and `docs/full/web.md`.

## Data and credentials

Use your own licensed daily OHLCV data. Configure MySQL and the official
TuShare service through environment variables such as `MYSQL_PASSWORD` and
`TUSHARE_TOKEN`. No real data or working token is distributed with this
release. The local-price smoke replaces only OHLCV input; its curve execution
still requires TuShare calendar access.

## Full Panel Data Package

The complete A-share daily panel is distributed separately from the source in
the [panel-v1.0.0 Release](https://github.com/zhukunren/fast-backtest-v1/releases/tag/panel-v1.0.0).
Download the ZIP, verify its SHA-256 and the included manifest before placing
the Parquet file at `data/panel_cache/stock_daily.parquet`.

## Scope

This edition is an alpha research and engineering tool, not an investment
adviser or live-trading system. It supports daily backtests and Web review of
the deterministic `public_offline_smoke` demo; the CLI also includes the
`breakout_20` example. Minute bars, live trading, broker execution, real-market
fixtures, private strategy research, and V2/V3 projects are outside the public
release boundary.

## License

MIT. See `LICENSE` and `SECURITY.md`.
