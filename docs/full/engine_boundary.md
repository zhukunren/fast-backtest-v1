# Full Edition Engine Boundary

The full workspace exposes the V1 daily backtesting system as the user-facing
runtime.

V1 includes:

- `main.py`
- `app/`
- `backtest/`
- `core/`
- `data/`
- `runtime_lifecycle/`
- `strategies/`
- `strategy_library/`
- `trading/`
- `web/`
- supported `tools/`

`projects/engine_v2/` and `projects/engine_v3/` remain independent project
tracks. They are not selected by the main CLI, Web routes, configuration, or
strategy loader.

For user-facing documentation, describe the V1 flow first. Treat V2/V3 as
experimental or independent unless a future release deliberately promotes them.
