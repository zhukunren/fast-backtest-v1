# JSHS V1

[中文](README_CN.md) | English

> **An LLM-driven A-share strategy production factory:** describe a research objective, let the Agent author and backtest a deterministic strategy, audit the domain contracts, and return a qualified strategy with evidence.

## How To Use

1. Install the project and download the [full Panel data package](https://github.com/zhukunren/fast-backtest-v1/releases/tag/panel-v1.0.0).
2. Open your Agent in the project directory and describe the research objective in natural language.
3. The Agent fixes the measurement contract, authors or revises one strategy, reruns the backtest, and reports `Passed` or the reason it cannot pass.
4. Review the strategy source, summary, and audit artifacts before using the result in your own research process.

Example request:

> Research a daily TopN momentum strategy on the 2021-2025 A-share daily Panel. Use `curve` mode and the `broker` engine, avoid look-ahead data, and require cumulative return >= 20%, maximum drawdown <= 15%, actual trade-segment win rate >= 55%, and minimum portfolio size 2x2. Return auditable source and backtest evidence.

The Agent handles strategy templates, mode and engine selection, iteration, failure diagnosis, and artifact interpretation. Ask the Agent for implementation details when you need them.

These are executable objective thresholds, not descriptive text. The Agent reads the actual backtest metrics: all checks must pass for `Passed`; a valid run that misses a target is `Unmet`; data, causality, schema, or runtime-contract failures are `Invalid`. The date window, data, mode, engine, and thresholds are not silently relaxed during iteration.

## Three Modes

| Mode | Use |
| --- | --- |
| `curve` | Full-Panel signals, TopN selection, portfolio execution, and equity curve. |
| `assess` | Full-Panel signals and trade-segment evaluation for fast signal research. |
| `single` | Single-stock signal and equity testing for focused debugging. |

## Five Engines

| Engine | Use |
| --- | --- |
| `legacy` | Vectorized portfolio research for standard strategies. |
| `slot` | Slot-based cash and position management. |
| `slot_broker` | Slot allocation combined with cash and execution ledgers. |
| `broker` | Order, fill, cash, position, and risk ledgers for strict trading semantics. |
| `fixed_rebalance` | Periodic target-weight rebalancing without a signal strategy. |

The Agent selects the mode and engine for the research question and keeps the measurement conditions fixed within a task. Compare strategies only under the same mode, engine, data, and window.

## What You Get

- Runnable and reviewable A-share strategy source;
- `summary.json`, objective checks, and static checks;
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

Open `http://127.0.0.1:5000`. Current `curve`/`assess` execution still requires a configured trading-calendar provider; TuShare is the default. Users remain responsible for data licensing.

## Boundary

The public repository contains the complete V1 source only. Paper evidence, private research records, credentials, runtime caches, and V2/V3 projects are excluded. Uploaded Python is trusted administrator code, not a sandbox. MIT License; see `LICENSE` and `SECURITY.md`.
