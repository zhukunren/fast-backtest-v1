"""Shared state and fixed-command execution for Web maintenance tasks."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web.log_service import log_event
from web.task_coordinator import TaskLease, TaskOwnershipError
from web.task_states import MAINTENANCE_PROGRESS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_TAIL_BYTES = 12_000
class MaintenanceRequestError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class MaintenanceTask:
    kind: str
    label: str
    command: tuple[str, ...]


def command_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _read_binary_tail(handle, *, max_bytes: int = OUTPUT_TAIL_BYTES) -> str:
    handle.flush()
    size = handle.tell()
    handle.seek(max(0, size - max_bytes))
    return handle.read().decode("utf-8", errors="replace")


def run_command(task: MaintenanceTask, *, timeout: float | None = None) -> dict[str, Any]:
    started = time.time()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            completed = subprocess.run(
                list(task.command),
                cwd=str(PROJECT_ROOT),
                env=command_environment(),
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                check=False,
            )
            return_code: int | None = completed.returncode
            status = "completed" if completed.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            return_code = None
            status = "failed"
            timeout_text = f"{timeout:.0f}" if timeout is not None else "unknown"
            stderr_file.write(f"命令执行超时（{timeout_text} 秒）".encode("utf-8"))
        except OSError as exc:
            return_code = None
            status = "failed"
            stderr_file.write(str(exc).encode("utf-8", errors="replace"))
        return {
            "task": task.kind,
            "label": task.label,
            "status": status,
            "return_code": return_code,
            "duration_sec": round(time.time() - started, 1),
            "stdout_tail": _read_binary_tail(stdout_file),
            "stderr_tail": _read_binary_tail(stderr_file),
        }


def set_last_task_kind(kind: str, *, task_lease: TaskLease | None = None) -> None:
    target = task_lease or MAINTENANCE_PROGRESS
    target.update_metadata(task_kind=str(kind))


def start_maintenance_task(*, total: int) -> TaskLease | Any | None:
    """Start maintenance work and return the task-bound progress writer."""
    start_task = getattr(MAINTENANCE_PROGRESS, "start_task", None)
    if callable(start_task):
        return start_task(total=total)
    if MAINTENANCE_PROGRESS.start(total=total):
        return MAINTENANCE_PROGRESS
    return None


def checkpoint_task(task_lease: TaskLease | Any) -> None:
    """Stop a worker before it starts more work after lease loss."""
    checkpoint = getattr(task_lease, "checkpoint", None)
    if callable(checkpoint):
        checkpoint()


def get_maintenance_progress() -> dict[str, Any]:
    progress = MAINTENANCE_PROGRESS.snapshot(deep=True)
    progress.setdefault("task_kind", "")
    return progress


def is_maintenance_running() -> bool:
    return MAINTENANCE_PROGRESS.is_running()


def finish_task(
    kind: str,
    *,
    failed_count: int,
    task_lease: TaskLease | None = None,
) -> bool:
    target = task_lease or MAINTENANCE_PROGRESS
    try:
        progress = target.finish(deep=True)
    except TaskOwnershipError as exc:
        log_event(
            "WARNING",
            "maintenance",
            "Maintenance worker did not finish after task ownership loss",
            task_kind=kind,
            error=str(exc),
        )
        return False
    log_event(
        "ERROR" if failed_count else "INFO",
        "maintenance",
        "Maintenance task finished",
        task_kind=kind,
        failed=failed_count,
        elapsed_sec=progress.get("elapsed_sec"),
    )
    return True


__all__ = [
    "MAINTENANCE_PROGRESS",
    "PROJECT_ROOT",
    "MaintenanceRequestError",
    "MaintenanceTask",
    "command_environment",
    "checkpoint_task",
    "finish_task",
    "get_maintenance_progress",
    "is_maintenance_running",
    "run_command",
    "set_last_task_kind",
    "start_maintenance_task",
]
