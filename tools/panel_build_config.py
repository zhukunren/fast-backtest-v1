"""Validated runtime options for the panel builder."""

from __future__ import annotations

import os

from core.exceptions import DataValidationError


def resolve_tushare_timeout(raw: object) -> float:
    text = str(raw if raw is not None else os.getenv("TUSHARE_TIMEOUT_SEC", "30")).strip() or "30"
    try:
        timeout = float(text)
    except ValueError as exc:
        raise DataValidationError(f"--tushare-timeout 必须是正数: {raw!r}") from exc
    if timeout <= 0:
        raise DataValidationError(f"--tushare-timeout 必须是正数: {raw!r}")
    return timeout


def resolve_workers(raw: object, *, stock_count: int) -> int:
    text = str(raw if raw is not None else "auto").strip().lower()
    if text in {"", "auto"}:
        auto_cap = max(1, int(os.getenv("PANEL_MYSQL_AUTO_WORKERS_MAX", "8") or "8"))
        cpu_guess = max(1, (os.cpu_count() or 4) // 2)
        return max(1, min(max(1, int(stock_count)), auto_cap, cpu_guess))
    try:
        workers = int(text)
    except ValueError as exc:
        raise DataValidationError(f"--workers 必须是正整数或 auto: {raw!r}") from exc
    if workers <= 0:
        raise DataValidationError(f"--workers 必须是正整数或 auto: {raw!r}")
    return max(1, min(workers, max(1, int(stock_count))))


__all__ = ["resolve_tushare_timeout", "resolve_workers"]
