"""
Evaluation helpers.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_IMPORTS = {
    "build_assess_summary_from_broker": (".assess", "build_assess_summary_from_broker"),
    "build_assess_summary_from_segments": (".assess", "build_assess_summary_from_segments"),
    "load_assess_broker_fills": (".assess", "load_assess_broker_fills"),
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
    "build_assess_summary_from_broker",
    "build_assess_summary_from_segments",
    "load_assess_broker_fills",
]
