"""
Core package public surface.

Prefer importing concrete submodules directly. This package only keeps a
minimal stable facade for commonly used types/functions.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_IMPORTS = {
    "BacktestError": (".exceptions", "BacktestError"),
    "ConfigurationError": (".exceptions", "ConfigurationError"),
    "DataValidationError": (".exceptions", "DataValidationError"),
    "PriceDataError": (".exceptions", "PriceDataError"),
    "SignalExtractionError": (".exceptions", "SignalExtractionError"),
    "StrategyError": (".exceptions", "StrategyError"),
    "TradeSegmentError": (".exceptions", "TradeSegmentError"),
    "ValidationError": (".exceptions", "ValidationError"),
    "ExecutionPolicy": (".execution_policy", "ExecutionPolicy"),
    "get_execution_policy": (".execution_policy", "get_execution_policy"),
    "UniverseResolution": (".universe", "UniverseResolution"),
    "UniverseSpec": (".universe", "UniverseSpec"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "BacktestError",
    "ConfigurationError",
    "DataValidationError",
    "ExecutionPolicy",
    "PriceDataError",
    "SignalExtractionError",
    "StrategyError",
    "TradeSegmentError",
    "UniverseResolution",
    "UniverseSpec",
    "ValidationError",
    "get_execution_policy",
]
