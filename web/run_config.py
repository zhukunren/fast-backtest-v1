from __future__ import annotations

from pathlib import Path
from typing import Any


_LIBRARY_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        "strategy.module",
        "backtest.start_time",
        "backtest.end_time",
        "backtest.offset_days",
        "backtest.universe_prefixes",
        "backtest.exclude_bj",
        "backtest.exclude_st",
        "run.run_mode",
        "run.compute_metrics",
        "run.build_topn_trade_segments_report",
        "run.exec_buy_timing",
        "run.exec_sell_timing",
        "run.exec_shift_t1",
        "transaction_cost.enable",
        "portfolio.engine",
        "portfolio.initial_capital",
        "fixed_rebalance.target_weights",
        "fixed_rebalance.symbol_type",
        "fixed_rebalance.rebalance_frequency",
        "fixed_rebalance.rebalance_dates",
        "fixed_rebalance.buy_price_col",
        "fixed_rebalance.sell_price_col",
        "fixed_rebalance.mark_price_col",
        "fixed_rebalance.normalize_weights",
        "panel.source",
        "mysql.daily_fields",
        "mysql.etf_db",
    }
)

_LIBRARY_KEY_ALIASES: dict[str, str] = {
    "run.build_trade_segments": "run.build_topn_trade_segments_report",
}


def build_output_path_overrides(output_root: Path) -> dict[str, Any]:
    root = Path(output_root)
    return {
        "paths.signal_data": root / "signal_data",
        "paths.stock_data": root / "stock_data",
        "paths.trade_data": root / "trade_data",
        "paths.return_date": root / "return_date",
        "paths.return_date_with_returns": root / "return_date_with_returns",
        "paths.net_value_unique_date": root / "net_value_unique_date",
    }


def build_batch_run_overrides(
    module: str,
    *,
    output_root: Path,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "strategy.module": module,
        "run.quiet": True,
        "run.plot_net_value": False,
        "run.clear_outputs": False,
        "run.clear_all_tables": False,
    }
    overrides.update(build_output_path_overrides(output_root))
    return overrides


def build_library_run_overrides(
    strat_cfg: dict[str, str],
    *,
    output_root: Path,
    date_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, value in sorted(strat_cfg.items(), key=lambda item: item[0].casefold()):
        mapped_key = _LIBRARY_KEY_ALIASES.get(key, key)
        if mapped_key in _LIBRARY_OVERRIDE_KEYS:
            overrides[mapped_key] = value

    if isinstance(date_overrides, dict):
        start_time = date_overrides.get("start_time")
        end_time = date_overrides.get("end_time")
        if start_time:
            overrides["backtest.start_time"] = start_time
        if end_time:
            overrides["backtest.end_time"] = end_time

    overrides["run.run_mode"] = "curve"
    overrides["run.quiet"] = True
    overrides["run.plot_net_value"] = False
    overrides["run.clear_outputs"] = False
    overrides["run.clear_all_tables"] = False
    overrides.update(build_output_path_overrides(output_root))
    return overrides


__all__ = [
    "build_batch_run_overrides",
    "build_library_run_overrides",
    "build_output_path_overrides",
]
