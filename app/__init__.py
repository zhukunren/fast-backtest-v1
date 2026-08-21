"""Application layer entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _load_run_application_deps() -> tuple[
    Callable[..., Any],
    Callable[..., Any],
    Callable[..., Any],
    Callable[..., Any],
]:
    from config import get_config

    from .backtest_session_service import run_backtest_session
    from .session_metadata import read_session_meta_reason, read_session_meta_status

    return get_config, run_backtest_session, read_session_meta_status, read_session_meta_reason


def _failed_reason_exit_code(reason: str) -> int:
    reason = str(reason or "")
    if reason.startswith("config_validation"):
        return 2
    if reason.startswith("load_strategy"):
        return 3
    return 4


def run_application() -> int:
    error_emitted = False

    def raw_print(*args, **kwargs) -> None:
        nonlocal error_emitted
        if any(str(arg).lstrip().startswith("[ERROR]") for arg in args):
            error_emitted = True
        print(*args, **kwargs)

    try:
        (
            get_config,
            run_backtest_session,
            read_session_meta_status,
            read_session_meta_reason,
        ) = _load_run_application_deps()
        outcome = run_backtest_session(
            get_config(),
            raw_print=raw_print,
            print_summary=True,
        )
    except Exception as exc:
        if not error_emitted:
            raw_print(f"[ERROR] 回测启动或执行失败: {type(exc).__name__}: {exc}")
        return 4
    if outcome is None:
        return 4

    meta = getattr(getattr(outcome, "ctx", None), "meta", None)
    status = read_session_meta_status(meta)
    if getattr(outcome, "result", None) is not None or status == "completed":
        return 0
    if status == "failed":
        return _failed_reason_exit_code(read_session_meta_reason(meta))
    return 4

__all__ = ["run_application"]
