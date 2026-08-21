"""
Shared result payload builders for Web batch/library background runs.
"""
from __future__ import annotations

import time
from typing import Any


def build_batch_strategy_result(
    module: str,
    *,
    status: str = "failed",
    duration_sec: float = 0.0,
    error: str | None = None,
    cum_return: float | None = None,
    max_drawdown: float | None = None,
    final_net_value: float | None = None,
    trade_count: int | None = None,
    win_rate: float | None = None,
) -> dict[str, Any]:
    return {
        "module": module,
        "short_name": module.split(".")[-1],
        "status": status,
        "duration_sec": duration_sec,
        "cum_return": cum_return,
        "max_drawdown": max_drawdown,
        "final_net_value": final_net_value,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "error": error,
    }


def build_library_run_result(
    name: str,
    *,
    module: str = "",
    status: str = "failed",
    duration_sec: float = 0.0,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "module": module,
        "status": status,
        "duration_sec": duration_sec,
        "error": error,
    }


def apply_duration(result: dict[str, Any], *, started_at: float) -> dict[str, Any]:
    result["duration_sec"] = round(time.time() - started_at, 2)
    return result


__all__ = [
    "apply_duration",
    "build_batch_strategy_result",
    "build_library_run_result",
]
