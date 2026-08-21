"""Explicit owner-module handles for Web regression tests.

The production Web package has no aggregate ``web.library`` facade.  This
small test-only handle keeps older regression fixtures readable while routing
each patched name to its owning module explicitly.
"""
from __future__ import annotations

from typing import Any

from web import (
    library_cache,
    library_cache_publication,
    library_cache_identity,
    library_catalog,
    library_curves,
    library_metadata,
    library_reports,
    library_runtime,
    task_states,
)
from web.insights import health, holding_review, metrics, strategy_analysis


_OWNER_BY_NAME: dict[str, tuple[Any, str]] = {
    "_build_benchmark_curves": (library_curves, "_build_benchmark_curves"),
    "_build_cache_config_signature": (library_cache_identity, "_build_cache_config_signature"),
    "_cache_static_signature": (library_cache_identity, "_cache_static_signature"),
    "_empty_progress": (task_states.BACKTEST_PROGRESS, "empty"),
    "_export_cache": (library_cache_publication, "_export_cache"),
    "_latest_trade_calendar_day": (library_metadata, "_latest_trade_calendar_day"),
    "_parse_strategy_config": (library_metadata, "_parse_strategy_config"),
    "_persist_library_outputs": (library_cache_publication, "_persist_library_outputs"),
    "_progress_state": (library_runtime, "_progress_state"),
    "_probe_latest_stock_data_date": (library_metadata, "_probe_latest_stock_data_date"),
    "_project_root": (library_metadata, "_project_root"),
    "_publish_cache": (library_cache_publication, "_publish_cache"),
    "_read_benchmark_daily": (library_curves, "_read_benchmark_daily"),
    "_resolve_effective_backtest_window": (library_metadata, "_resolve_effective_backtest_window"),
    "_resolve_latest_backtest_end_time": (library_metadata, "_resolve_latest_backtest_end_time"),
    "_resolve_module_file": (library_metadata, "_resolve_module_file"),
    "_run_single": (library_runtime, "_run_single"),
    "_sort_library_entries": (library_catalog, "_sort_library_entries"),
    "_today_yyyymmdd": (library_metadata, "_today_yyyymmdd"),
    "_write_json_atomic": (library_cache_publication, "_write_json_atomic"),
    "build_library_catalog_csv": (library_reports, "build_library_catalog_csv"),
    "build_daily_holdings_payload": (library_cache_publication, "build_daily_holdings_payload"),
    "build_library_run_overrides": (library_runtime, "build_library_run_overrides"),
    "build_net_value_payload": (library_cache_publication, "build_net_value_payload"),
    "build_position_changes_payload": (library_cache_publication, "build_position_changes_payload"),
    "build_positions_cache_payload": (library_cache_publication, "build_positions_cache_payload"),
    "build_strategy_markdown_report": (library_reports, "build_strategy_markdown_report"),
    "build_trades_payload": (library_cache_publication, "build_trades_payload"),
    "clear_library_cache": (library_runtime, "clear_library_cache"),
    "compute_derived_metrics": (metrics, "compute_derived_metrics"),
    "compute_enhanced_compare": (strategy_analysis, "compute_enhanced_compare"),
    "compute_library_holding_trade_review": (holding_review, "compute_library_holding_trade_review"),
    "compute_library_market_state_analysis": (strategy_analysis, "compute_library_market_state_analysis"),
    "compute_library_strategy_analysis": (strategy_analysis, "compute_library_strategy_analysis"),
    "execute_web_backtest": (library_runtime, "execute_web_backtest"),
    "get_config": (library_runtime, "get_config"),
    "get_library_catalog": (library_catalog, "get_library_catalog"),
    "get_library_daily_holdings": (library_curves, "get_library_daily_holdings"),
    "get_library_detail": (library_catalog, "get_library_detail"),
    "get_library_health": (health, "get_library_health"),
    "get_library_net_value": (library_curves, "get_library_net_value"),
    "get_library_position_changes": (library_curves, "get_library_position_changes"),
    "get_library_trades": (library_curves, "get_library_trades"),
    "get_progress": (task_states, "get_backtest_progress"),
    "is_running": (task_states, "is_backtest_running"),
    "list_library": (library_runtime, "list_library"),
    "reset_progress_state": (task_states, "reset_backtest_progress"),
    "start_library_backtest": (library_runtime, "start_library_backtest"),
}


class _LibraryOwners:
    def __getattr__(self, name: str) -> Any:
        if name == "LIBRARY_ROOT":
            return library_metadata.LIBRARY_ROOT
        target = _OWNER_BY_NAME.get(name)
        if target is None:
            raise AttributeError(name)
        owner, attribute = target
        return getattr(owner, attribute)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "LIBRARY_ROOT":
            for module in (
                library_metadata,
                library_cache,
                library_cache_publication,
                library_catalog,
                library_curves,
                library_runtime,
                health,
                holding_review,
                metrics,
                strategy_analysis,
            ):
                setattr(module, name, value)
            return
        target = _OWNER_BY_NAME.get(name)
        if target is None:
            object.__setattr__(self, name, value)
            return
        owner, attribute = target
        setattr(owner, attribute, value)


library = _LibraryOwners()

__all__ = ["library"]
