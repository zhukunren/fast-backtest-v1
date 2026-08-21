"""
Shared progress-state helpers for Web background runners.
"""
from __future__ import annotations

import time
import threading
import copy
from itertools import count
from typing import Any, Mapping


_TASK_COUNTER = count(1)


def _new_task_id() -> str:
    return f"task-{int(time.time() * 1000)}-{next(_TASK_COUNTER)}"


def _touch_progress(progress: dict[str, Any], *, finished: bool = False) -> None:
    if "updated_at" in progress:
        progress["updated_at"] = time.time()
    if finished and "finished_at" in progress:
        progress["finished_at"] = progress.get("updated_at") or time.time()


def _update_task_age(progress: dict[str, Any]) -> None:
    if "task_age_sec" not in progress:
        return
    start_time = float(progress.get("start_time", 0.0) or 0.0)
    if start_time <= 0:
        progress["task_age_sec"] = 0.0
        return
    finished_at = float(progress.get("finished_at", 0.0) or 0.0)
    end_time = finished_at if not bool(progress.get("running")) and finished_at > 0 else time.time()
    progress["task_age_sec"] = round(max(end_time - start_time, 0.0), 1)


def empty_progress(
    *,
    current_key: str,
    include_error: bool = False,
    include_task_metadata: bool = False,
) -> dict[str, Any]:
    progress = {
        "running": False,
        "total": 0,
        "completed": 0,
        current_key: "",
        "results": [],
        "elapsed_sec": 0.0,
        "start_time": 0.0,
    }
    if include_error:
        progress["error"] = ""
    if include_task_metadata:
        progress["task_id"] = ""
        progress["updated_at"] = 0.0
        progress["finished_at"] = 0.0
        progress["task_age_sec"] = 0.0
        progress["cancelled"] = False
    return progress


def start_progress(
    *,
    total: int,
    current_key: str,
    include_error: bool = False,
    include_task_metadata: bool = False,
    task_id: str | None = None,
) -> dict[str, Any]:
    progress = empty_progress(
        current_key=current_key,
        include_error=include_error,
        include_task_metadata=include_task_metadata,
    )
    progress["running"] = True
    progress["total"] = total
    progress["start_time"] = time.time()
    if include_task_metadata:
        progress["task_id"] = str(task_id or "").strip() or _new_task_id()
        progress["updated_at"] = progress["start_time"]
    return progress


def snapshot_progress(progress: Mapping[str, Any]) -> dict[str, Any]:
    """Return a polling-safe snapshot detached from the live mutable progress state."""
    out = dict(progress)
    results = out.get("results")
    if isinstance(results, list):
        out["results"] = [dict(item) if isinstance(item, dict) else item for item in results]
    if bool(out.get("running")):
        out["elapsed_sec"] = round(time.time() - float(out.get("start_time", 0.0) or 0.0), 1)
    _update_task_age(out)
    return out


def finish_progress(progress: dict[str, Any], *, current_key: str) -> dict[str, Any]:
    progress["running"] = False
    progress[current_key] = ""
    progress["elapsed_sec"] = round(time.time() - float(progress.get("start_time", 0.0) or 0.0), 1)
    _touch_progress(progress, finished=True)
    _update_task_age(progress)
    return progress


def cancel_progress(progress: dict[str, Any], *, current_key: str, reason: str = "") -> dict[str, Any]:
    progress["running"] = False
    progress[current_key] = ""
    progress["cancelled"] = True
    if "error" in progress:
        progress["error"] = str(reason or "cancelled")
    progress["elapsed_sec"] = round(time.time() - float(progress.get("start_time", 0.0) or 0.0), 1)
    _touch_progress(progress, finished=True)
    _update_task_age(progress)
    return progress


class ProgressState:
    """Own mutable background-run progress for one Web runner."""

    def __init__(
        self,
        *,
        current_key: str,
        include_error: bool = False,
        include_task_metadata: bool = False,
    ) -> None:
        self._current_key = current_key
        self._include_error = include_error
        self._include_task_metadata = include_task_metadata
        self._lock = threading.Lock()
        self._progress = empty_progress(
            current_key=current_key,
            include_error=include_error,
            include_task_metadata=include_task_metadata,
        )

    def empty(self) -> dict[str, Any]:
        return empty_progress(
            current_key=self._current_key,
            include_error=self._include_error,
            include_task_metadata=self._include_task_metadata,
        )

    def snapshot(self, *, deep: bool = False) -> dict[str, Any]:
        with self._lock:
            if deep:
                out = copy.deepcopy(self._progress)
                if bool(out.get("running")):
                    out["elapsed_sec"] = round(time.time() - float(out.get("start_time", 0.0) or 0.0), 1)
                _update_task_age(out)
                return out
            return snapshot_progress(self._progress)

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._progress["running"])

    def is_stale(self, *, stale_after_sec: float) -> bool:
        with self._lock:
            if not bool(self._progress.get("running")):
                return False
            updated_at = float(self._progress.get("updated_at", 0.0) or self._progress.get("start_time", 0.0) or 0.0)
            if updated_at <= 0:
                return False
            return (time.time() - updated_at) > float(stale_after_sec)

    def reset(self, *, deep: bool = False) -> dict[str, Any]:
        with self._lock:
            self._progress = self.empty()
            return copy.deepcopy(self._progress) if deep else snapshot_progress(self._progress)

    def replace(self, progress: Mapping[str, Any]) -> None:
        with self._lock:
            self._progress = dict(progress)
            results = self._progress.get("results")
            if isinstance(results, list):
                self._progress["results"] = list(results)

    def start(self, *, total: int, task_id: str | None = None) -> bool:
        with self._lock:
            if self._progress["running"]:
                return False
            self._progress = start_progress(
                total=total,
                current_key=self._current_key,
                include_error=self._include_error,
                include_task_metadata=self._include_task_metadata,
                task_id=task_id,
            )
            return True

    def set_current(self, current: str) -> None:
        with self._lock:
            self._progress[self._current_key] = current
            _touch_progress(self._progress)

    def append_result(self, result: Any, *, deep: bool = False) -> None:
        with self._lock:
            item = copy.deepcopy(result) if deep else result
            self._progress["results"].append(item)
            self._progress["completed"] += 1
            _touch_progress(self._progress)

    def finish(self, *, deep: bool = False) -> dict[str, Any]:
        with self._lock:
            finish_progress(self._progress, current_key=self._current_key)
            return copy.deepcopy(self._progress) if deep else snapshot_progress(self._progress)

    def cancel(self, *, reason: str = "", deep: bool = False) -> dict[str, Any]:
        with self._lock:
            cancel_progress(self._progress, current_key=self._current_key, reason=reason)
            return copy.deepcopy(self._progress) if deep else snapshot_progress(self._progress)


__all__ = [
    "ProgressState",
    "cancel_progress",
    "empty_progress",
    "finish_progress",
    "snapshot_progress",
    "start_progress",
]
