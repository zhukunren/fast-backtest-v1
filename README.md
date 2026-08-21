# JSHS V1

[中文](README_CN.md) | English

> **An LLM-driven A-share strategy production factory:** describe a research objective, let the Agent author and backtest a deterministic strategy, audit the domain contracts, and return a qualified strategy with evidence.

## How To Use

1. Install the project and download the [full Panel data package](https://github.com/zhukunren/fast-backtest-v1/releases/tag/panel-v1.0.0).
2. Open your Agent in the project directory and describe the research objective in natural language.
3. The Agent fixes the measurement contract, authors or revises one strategy, reruns the backtest, and reports `Passed` or the reason it cannot pass.
4. Review the strategy source, summary, and audit artifacts before using the result in your own research process.

Example request:

> Research a daily TopN momentum strategy on the 2021-2025 A-share daily Panel. Avoid look-ahead data, require cumulative return >= 300%, maximum drawdown <= 30%, actual trade-segment win rate >= 55%, and minimum portfolio size 2x2. Choose the appropriate execution setup and return auditable source and backtest evidence.

The Agent handles strategy templates, mode and engine selection, iteration, failure diagnosis, and artifact interpretation. Ask the Agent for implementation details when you need them.

These are executable objective thresholds, not descriptive text. The Agent reads the actual backtest metrics: all checks must pass for `Passed`; a valid run that misses a target is `Unmet`; data, causality, schema, or runtime-contract failures are `Invalid`. The date window, data, mode, engine, and thresholds are not silently relaxed during iteration.

## Three Research Views

You do not need to remember the internal names. Tell the Agent what you want to understand:

| What you want to know | System view |
| --- | --- |
| Overall returns and drawdown of a strategy | Full portfolio backtest (`curve`) |
| Quality of signals and individual trades | Trade-quality evaluation (`assess`) |
| Signal and execution behavior for one stock | Single-stock check (`single`) |

## Five Portfolio Setups

The Agent chooses the appropriate setup. You only need the plain-language meaning:

| Portfolio setup | Best for |
| --- | --- |
| Standard portfolio backtest (`legacy`) | Overall historical behavior of a standard strategy. |
| Slot-based allocation (`slot`) | Fixed position slots, cash allocation, and rotation. |
| Slots plus execution records (`slot_broker`) | Slot rules together with trade accounting. |
| Full trading ledger (`broker`) | Orders, fills, cash, positions, and risk changes. |
| Target-weight rebalancing (`fixed_rebalance`) | Periodic allocation changes without a stock-selection strategy. |

These are Agent execution choices, not a learning barrier. The Agent keeps comparison conditions consistent.

## What You Get

- Runnable and reviewable A-share strategy source;
- Backtest reports, objective checks, and static checks;
- data identity, window, resolved configuration, and engine records;
- structured signals, fills, positions, equity, and trade-segment artifacts;
- audit results for data quality, point-in-time safety, schema, causality, tradability, costs, and NAV invariants.

`Passed` means the strategy passed its declared historical backtest contract. It is not a promise of future profitability, live-trading readiness, or investment advice.

## Install And Run

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item config\config.example.ini config\config.local.ini
```

Extract the Panel into `data/panel_cache/` and verify its manifest hash:

```powershell
python tools\data_quality_audit.py --target panel-manifest --input data\panel_cache\stock_daily.parquet
```

Start the Web workspace:

```powershell
Set-Location web\frontend
npm ci
npm run build
Set-Location ..\..
python -m web
```

Open `http://127.0.0.1:5000`. Full portfolio backtests and trade-quality evaluations currently require a configured trading-calendar provider; TuShare is the default. Users remain responsible for data licensing.

## Boundary

The public repository contains the complete V1 source only. Paper evidence, private research records, credentials, runtime caches, and V2/V3 projects are excluded. Uploaded Python is trusted administrator code, not a sandbox. MIT License; see `LICENSE` and `SECURITY.md`.
