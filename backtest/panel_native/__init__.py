"""Lazy public surface for the V1 panel-native runtime."""
from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "ColumnStore": ("core.panel_native", "ColumnStore"),
    "FeatureBatch": ("core.panel_contracts", "FeatureBatch"),
    "PanelIndex": ("core.panel_native", "PanelIndex"),
    "PanelView": ("core.panel_native", "PanelView"),
    "PricePanel": ("core.panel_native", "PricePanel"),
    "QuoteStore": ("core.panel_native", "QuoteStore"),
    "SelectionBatch": ("core.panel_contracts", "SelectionBatch"),
    "SignalBatch": ("core.panel_contracts", "SignalBatch"),
    "apply_cross_section_transforms": (
        "strategies.market_features",
        "apply_cross_section_transforms",
    ),
    "apply_market_features": ("strategies.market_features", "apply_market_features"),
    "build_basic_stock_summaries": (
        "backtest.panel_native.projection",
        "build_basic_stock_summaries",
    ),
    "build_selection": ("backtest.panel_native.selection", "build_selection"),
    "finalize_raw_signals": (
        "backtest.panel_native.selection",
        "finalize_raw_signals",
    ),
    "precheck_tradable_rows": (
        "backtest.panel_native.selection",
        "precheck_tradable_rows",
    ),
    "project_daily_summary": (
        "backtest.panel_native.projection",
        "project_daily_summary",
    ),
    "project_selection_frame": (
        "backtest.panel_native.projection",
        "project_selection_frame",
    ),
    "project_selection_from_quotes": (
        "backtest.panel_native.projection",
        "project_selection_from_quotes",
    ),
    "resolve_board_keep_mask": (
        "backtest.panel_native.filters",
        "resolve_board_keep_mask",
    ),
    "resolve_index_timing_mask": (
        "backtest.panel_native.filters",
        "resolve_index_timing_mask",
    ),
    "select_topn_rows": ("backtest.panel_native.selection", "select_topn_rows"),
}

__all__ = tuple(sorted(_EXPORTS, key=str.casefold))


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__), key=str.casefold)
