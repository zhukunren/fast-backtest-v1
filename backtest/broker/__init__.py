"""Lazy public exports for the optional broker engine."""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_IMPORTS = {
    "BrokerConfig": (".engine", "BrokerConfig"),
    "SimpleBroker": (".engine", "SimpleBroker"),
    "Fill": (".order", "Fill"),
    "Order": (".order", "Order"),
    "Position": (".order", "Position"),
    "Side": (".order", "Side"),
    "TradePlan": (".plan", "TradePlan"),
    "build_trade_plan": (".plan", "build_trade_plan"),
    "BrokerPortfolioResult": (".result", "BrokerPortfolioResult"),
    "run_broker_portfolio": (".runner", "run_broker_portfolio"),
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
    "BrokerConfig",
    "BrokerPortfolioResult",
    "SimpleBroker",
    "Side",
    "Order",
    "Fill",
    "Position",
    "TradePlan",
    "build_trade_plan",
    "run_broker_portfolio",
]
