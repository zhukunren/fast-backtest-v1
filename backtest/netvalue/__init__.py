"""
Net value package.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_IMPORTS = {
    "merge_net_value_result_from_trading_dfs": (".merge", "merge_net_value_result_from_trading_dfs"),
    "plot_net_value": (".plot", "plot_net_value"),
    "add_position_and_returns": (".positions", "add_position_and_returns"),
    "add_position_and_returns_cash": (".positions", "add_position_and_returns_cash"),
    "add_position_and_returns_close": (".positions", "add_position_and_returns_close"),
    "add_position_and_returns_close_auto": (".positions", "add_position_and_returns_close_auto"),
    "add_position_and_returns_close_jit": (".positions", "add_position_and_returns_close_jit"),
    "reset_net_value_jit_warning": (".positions", "reset_net_value_jit_warning"),
    "NetValueMergeResult": (".result", "NetValueMergeResult"),
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
    "merge_net_value_result_from_trading_dfs",
    "NetValueMergeResult",
    "plot_net_value",
    "add_position_and_returns",
    "add_position_and_returns_cash",
    "add_position_and_returns_close",
    "add_position_and_returns_close_auto",
    "add_position_and_returns_close_jit",
    "reset_net_value_jit_warning",
]
