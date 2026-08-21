"""Backtest package."""

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    if name != "reporting":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(".reporting", __name__)
    globals()[name] = module
    return module


__all__ = ["reporting"]
