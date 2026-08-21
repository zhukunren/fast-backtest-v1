"""
Allocation package.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_IMPORTS = {
    "build_trade_segments": (".allocator", "build_trade_segments"),
    "extract_trade_segment": (".segment", "extract_trade_segment"),
    "build_all_trade_segments": (".trade_extraction", "build_all_trade_segments"),
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
    "build_trade_segments",
    "extract_trade_segment",
    "build_all_trade_segments",
]
