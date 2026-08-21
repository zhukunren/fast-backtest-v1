"""Process-local handles for the shared Web task coordinator."""
from web.task_coordinator import create_web_progress_state


BACKTEST_PROGRESS = create_web_progress_state(
    state_key="backtest",
    current_key="current",
    include_task_metadata=True,
)
MAINTENANCE_PROGRESS = create_web_progress_state(
    state_key="maintenance",
    current_key="current",
    include_error=True,
    include_task_metadata=True,
)


def get_backtest_progress() -> dict:
    return BACKTEST_PROGRESS.snapshot(deep=True)


def is_backtest_running() -> bool:
    return BACKTEST_PROGRESS.is_running()


def reset_backtest_progress() -> dict:
    return BACKTEST_PROGRESS.reset(deep=True)


def cancel_backtest_progress(reason: str = "cancelled") -> dict:
    return BACKTEST_PROGRESS.cancel(reason=reason, deep=True)


__all__ = [
    "BACKTEST_PROGRESS",
    "MAINTENANCE_PROGRESS",
    "cancel_backtest_progress",
    "get_backtest_progress",
    "is_backtest_running",
    "reset_backtest_progress",
]
