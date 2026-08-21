"""SQLite-backed progress and mutual exclusion for Web background tasks."""
from __future__ import annotations

import copy
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from web.progress_state import (
    cancel_progress,
    empty_progress,
    finish_progress,
    snapshot_progress,
    start_progress,
)
from web.task_store import SQLiteTaskStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = Path(__file__).resolve().parent
# Keep the default coordination database inside the Web subsystem.  The
# repository-level ``output`` directory may be read-only in packaged or
# sandboxed launches, while task coordination must be writable before any
# backtest request can be accepted.  Deployments that share state across
# workers can still set WEB_TASK_STATE_DB explicitly.
DEFAULT_TASK_STATE_PATH = WEB_ROOT / "data" / "task_state.sqlite3"
DEFAULT_LEASE_TIMEOUT_SEC = 120.0
DEFAULT_HEARTBEAT_SEC = 5.0
DEFAULT_EXCLUSIVE_GROUP = "web-exclusive-task"
_HEARTBEAT_THREAD_FACTORY = threading.Thread


class TaskOwnershipError(RuntimeError):
    """Raised when a stale worker tries to mutate a task it no longer owns."""


def resolve_task_state_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = str(env.get("WEB_TASK_STATE_DB") or "").strip()
    path = Path(raw) if raw else DEFAULT_TASK_STATE_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _positive_float(
    raw: object,
    *,
    name: str,
    default: float,
) -> float:
    text = str(raw or "").strip()
    if not text:
        return float(default)
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


def task_timing_from_environment(
    environ: Mapping[str, str] | None = None,
) -> tuple[float, float]:
    env = os.environ if environ is None else environ
    lease_timeout = _positive_float(
        env.get("WEB_TASK_LEASE_TIMEOUT_SEC"),
        name="WEB_TASK_LEASE_TIMEOUT_SEC",
        default=DEFAULT_LEASE_TIMEOUT_SEC,
    )
    heartbeat = _positive_float(
        env.get("WEB_TASK_HEARTBEAT_SEC"),
        name="WEB_TASK_HEARTBEAT_SEC",
        default=DEFAULT_HEARTBEAT_SEC,
    )
    if heartbeat >= lease_timeout / 2:
        raise RuntimeError(
            "WEB_TASK_HEARTBEAT_SEC must be less than half of "
            "WEB_TASK_LEASE_TIMEOUT_SEC"
        )
    return lease_timeout, heartbeat


@dataclass(frozen=True)
class TaskLease:
    """Immutable ownership handle for one background task execution."""

    _state: "SharedProgressState" = field(repr=False, compare=False)
    task_id: str
    _owner_token: str = field(repr=False, compare=False)
    _heartbeat_stop: threading.Event = field(repr=False, compare=False)

    def checkpoint(self) -> None:
        """Verify and refresh ownership before the next unit of work."""
        self._state._checkpoint_lease(self)

    def set_current(self, current: str) -> None:
        self._state._mutate_lease(
            self,
            lambda progress: progress.__setitem__(self._state._current_key, current),
        )

    def append_result(self, result: Any, *, deep: bool = False) -> None:
        item = copy.deepcopy(result) if deep else result

        def _append(progress: dict[str, Any]) -> None:
            progress.setdefault("results", []).append(item)
            progress["completed"] = int(progress.get("completed", 0) or 0) + 1

        self._state._mutate_lease(self, _append)

    def update_metadata(self, **values: Any) -> None:
        self._state._mutate_lease(self, lambda progress: progress.update(values))

    def replace(self, progress: Mapping[str, Any]) -> None:
        self._state._replace_lease(self, progress)

    def finish(self, *, deep: bool = False) -> dict[str, Any]:
        payload = self._state._mutate_lease(
            self,
            lambda progress: finish_progress(progress, current_key=self._state._current_key),
        )
        self._state._stop_heartbeat(self)
        self._state._clear_thread_lease(self)
        return copy.deepcopy(payload) if deep else snapshot_progress(payload)

    def cancel(self, *, reason: str = "", deep: bool = False) -> dict[str, Any]:
        payload = self._state._mutate_lease(
            self,
            lambda progress: cancel_progress(
                progress,
                current_key=self._state._current_key,
                reason=reason,
            ),
        )
        self._state._stop_heartbeat(self)
        self._state._clear_thread_lease(self)
        return copy.deepcopy(payload) if deep else snapshot_progress(payload)

    def reset(self, *, deep: bool = False) -> dict[str, Any]:
        return self._state._reset_lease(self, deep=deep)


class SharedProgressState:
    """Shared Web progress state backed by a SQLite task store."""

    def __init__(
        self,
        *,
        store: SQLiteTaskStore,
        state_key: str,
        current_key: str,
        exclusive_group: str = DEFAULT_EXCLUSIVE_GROUP,
        include_error: bool = False,
        include_task_metadata: bool = True,
        heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC,
    ) -> None:
        self._store = store
        self._state_key = str(state_key)
        self._current_key = str(current_key)
        self._exclusive_group = str(exclusive_group)
        self._include_error = bool(include_error)
        self._include_task_metadata = bool(include_task_metadata)
        self._heartbeat_sec = float(heartbeat_sec)
        self._owner_prefix = f"{socket.gethostname()}:{os.getpid()}"
        self._thread_leases = threading.local()

    def empty(self) -> dict[str, Any]:
        return empty_progress(
            current_key=self._current_key,
            include_error=self._include_error,
            include_task_metadata=self._include_task_metadata,
        )

    def snapshot(self, *, deep: bool = False) -> dict[str, Any]:
        payload = self._store.read(self._state_key) or self.empty()
        if deep:
            out = copy.deepcopy(payload)
            return snapshot_progress(out)
        return snapshot_progress(payload)

    def is_running(self) -> bool:
        return bool(self.snapshot().get("running"))

    def is_stale(self, *, stale_after_sec: float) -> bool:
        progress = self.snapshot()
        if not bool(progress.get("running")):
            return False
        updated = float(progress.get("updated_at", 0.0) or progress.get("start_time", 0.0) or 0.0)
        return updated > 0 and time.time() - updated > float(stale_after_sec)

    def _new_lease(self, task_id: str) -> TaskLease:
        return TaskLease(
            self,
            task_id=str(task_id),
            _owner_token=f"{self._owner_prefix}:{uuid4().hex}",
            _heartbeat_stop=threading.Event(),
        )

    def _bind_thread_lease(self, lease: TaskLease) -> None:
        self._thread_leases.lease = lease

    def _thread_lease(self) -> TaskLease | None:
        lease = getattr(self._thread_leases, "lease", None)
        return lease if isinstance(lease, TaskLease) and lease._state is self else None

    def _require_thread_lease(self) -> TaskLease:
        lease = self._thread_lease()
        if lease is None:
            raise TaskOwnershipError(
                f"no task lease is bound for {self._state_key}; use start_task() in workers"
            )
        return lease

    def _clear_thread_lease(self, lease: TaskLease) -> None:
        if self._thread_lease() is lease:
            self._thread_leases.lease = None

    def _start_heartbeat(self, lease: TaskLease) -> None:
        stop = lease._heartbeat_stop

        def _heartbeat() -> None:
            while not stop.wait(self._heartbeat_sec):
                payload = self._store.mutate(
                    state_key=self._state_key,
                    owner_token=lease._owner_token,
                    task_id=lease.task_id,
                    transform=lambda _progress: None,
                )
                if payload is None:
                    self._stop_heartbeat(lease)
                    return

        _HEARTBEAT_THREAD_FACTORY(
            target=_heartbeat,
            name=f"web-task-heartbeat-{self._state_key}",
            daemon=True,
        ).start()

    @staticmethod
    def _stop_heartbeat(lease: TaskLease) -> None:
        lease._heartbeat_stop.set()

    def _ownership_lost(self, lease: TaskLease) -> TaskOwnershipError:
        self._stop_heartbeat(lease)
        self._clear_thread_lease(lease)
        return TaskOwnershipError(
            f"task ownership lost for {self._state_key}:{lease.task_id}"
        )

    def _ownership_lost_without_expiry(self, lease: TaskLease) -> TaskOwnershipError:
        self._stop_heartbeat(lease)
        self._clear_thread_lease(lease)
        return TaskOwnershipError(
            f"task ownership lost for {self._state_key}:{lease.task_id}"
        )

    def _lease_matches_current_record(self, lease: TaskLease) -> bool:
        connection = self._store._connect()
        try:
            row = connection.execute(
                "SELECT owner_token, task_id, payload_json FROM web_task_state WHERE state_key = ?",
                (self._state_key,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return False
        payload = self._store._row_payload(row)
        return (
            bool(payload.get("running"))
            and str(row["owner_token"]) == lease._owner_token
            and str(row["task_id"]) == lease.task_id
        )

    def _mutate_lease(
        self,
        lease: TaskLease,
        transform: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        # ``SQLiteTaskStore.mutate`` may expire an unrelated replacement while
        # checking a stale lease. Reject that lease before invoking store logic.
        if not self._lease_matches_current_record(lease):
            raise self._ownership_lost_without_expiry(lease)
        payload = self._store.mutate(
            state_key=self._state_key,
            owner_token=lease._owner_token,
            task_id=lease.task_id,
            transform=transform,
        )
        if payload is None:
            raise self._ownership_lost(lease)
        return payload

    def _checkpoint_lease(self, lease: TaskLease) -> None:
        self._mutate_lease(lease, lambda _progress: None)

    def _replace_lease(self, lease: TaskLease, progress: Mapping[str, Any]) -> None:
        payload = dict(progress)
        payload_task_id = str(payload.get("task_id") or "")
        if payload_task_id and payload_task_id != lease.task_id:
            raise ValueError("task lease cannot replace progress for another task")
        if self._include_task_metadata:
            payload["task_id"] = lease.task_id
        replaced = self._store.replace(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=lease._owner_token,
            task_id=lease.task_id,
            payload=payload,
            expected_owner_token=lease._owner_token,
            expected_task_id=lease.task_id,
        )
        if not replaced:
            raise self._ownership_lost(lease)
        if not bool(payload.get("running")):
            self._stop_heartbeat(lease)
            self._clear_thread_lease(lease)

    def _reset_lease(self, lease: TaskLease, *, deep: bool) -> dict[str, Any]:
        payload = self.empty()
        replaced = self._store.replace(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=lease._owner_token,
            task_id="",
            payload=payload,
            expected_owner_token=lease._owner_token,
            expected_task_id=lease.task_id,
        )
        if not replaced:
            raise self._ownership_lost(lease)
        self._stop_heartbeat(lease)
        self._clear_thread_lease(lease)
        return copy.deepcopy(payload) if deep else snapshot_progress(payload)

    def replace(self, progress: Mapping[str, Any]) -> None:
        lease = self._thread_lease()
        if lease is not None:
            lease.replace(progress)
            return

        payload = dict(progress)
        task_id = str(payload.get("task_id") or "")
        if bool(payload.get("running")):
            task_id = task_id or f"task-{uuid4().hex}"
            if self._include_task_metadata:
                payload["task_id"] = task_id
        owner_token = f"{self._owner_prefix}:replace:{uuid4().hex}"
        replaced = self._store.replace(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=owner_token,
            task_id=task_id,
            payload=payload,
        )
        if not replaced:
            raise TaskOwnershipError(
                f"cannot replace progress owned by another task: {self._state_key}"
            )
        if bool(payload.get("running")):
            replacement_lease = TaskLease(
                self,
                task_id=task_id,
                _owner_token=owner_token,
                _heartbeat_stop=threading.Event(),
            )
            self._bind_thread_lease(replacement_lease)
            self._start_heartbeat(replacement_lease)

    def start_task(self, *, total: int, task_id: str | None = None) -> TaskLease | None:
        selected_task_id = str(task_id or "").strip() or f"task-{uuid4().hex}"
        payload = start_progress(
            total=total,
            current_key=self._current_key,
            include_error=self._include_error,
            include_task_metadata=self._include_task_metadata,
            task_id=selected_task_id,
        )
        lease = self._new_lease(selected_task_id)
        started = self._store.try_start(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=lease._owner_token,
            task_id=selected_task_id,
            payload=payload,
        )
        if not started:
            return None
        self._start_heartbeat(lease)
        return lease

    def start(self, *, total: int, task_id: str | None = None) -> bool:
        """Compatibility entry point for synchronous, same-thread callers."""
        lease = self.start_task(total=total, task_id=task_id)
        if lease is None:
            return False
        self._bind_thread_lease(lease)
        return True

    def set_current(self, current: str) -> None:
        self._require_thread_lease().set_current(current)

    def append_result(self, result: Any, *, deep: bool = False) -> None:
        self._require_thread_lease().append_result(result, deep=deep)

    def update_metadata(self, **values: Any) -> None:
        lease = self._thread_lease()
        if lease is not None:
            lease.update_metadata(**values)
            return
        payload = self.snapshot(deep=True)
        if bool(payload.get("running")):
            raise TaskOwnershipError(
                f"cannot update metadata for task owned by another process: {self._state_key}"
            )
        payload.update(values)
        replaced = self._store.replace(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=f"{self._owner_prefix}:metadata:{uuid4().hex}",
            task_id=str(payload.get("task_id") or ""),
            payload=payload,
        )
        if not replaced:
            raise TaskOwnershipError(
                f"cannot update metadata for task owned by another process: {self._state_key}"
            )

    def finish(self, *, deep: bool = False) -> dict[str, Any]:
        return self._require_thread_lease().finish(deep=deep)

    def cancel(self, *, reason: str = "", deep: bool = False) -> dict[str, Any]:
        return self._require_thread_lease().cancel(reason=reason, deep=deep)

    def reset(self, *, deep: bool = False) -> dict[str, Any]:
        lease = self._thread_lease()
        if lease is not None:
            return lease.reset(deep=deep)
        payload = self.empty()
        replaced = self._store.replace(
            state_key=self._state_key,
            exclusive_group=self._exclusive_group,
            current_key=self._current_key,
            owner_token=f"{self._owner_prefix}:reset:{uuid4().hex}",
            task_id="",
            payload=payload,
        )
        if not replaced:
            raise TaskOwnershipError(
                f"cannot reset progress owned by another task: {self._state_key}"
            )
        return copy.deepcopy(payload) if deep else snapshot_progress(payload)


def create_web_progress_state(
    *,
    state_key: str,
    current_key: str,
    include_error: bool = False,
    include_task_metadata: bool = True,
    exclusive_group: str = DEFAULT_EXCLUSIVE_GROUP,
    environ: Mapping[str, str] | None = None,
) -> SharedProgressState:
    lease_timeout, heartbeat = task_timing_from_environment(environ)
    store = SQLiteTaskStore(
        resolve_task_state_path(environ),
        lease_timeout_sec=lease_timeout,
    )
    return SharedProgressState(
        store=store,
        state_key=state_key,
        current_key=current_key,
        exclusive_group=exclusive_group,
        include_error=include_error,
        include_task_metadata=include_task_metadata,
        heartbeat_sec=heartbeat,
    )


__all__ = [
    "DEFAULT_EXCLUSIVE_GROUP",
    "DEFAULT_HEARTBEAT_SEC",
    "DEFAULT_LEASE_TIMEOUT_SEC",
    "DEFAULT_TASK_STATE_PATH",
    "SQLiteTaskStore",
    "SharedProgressState",
    "TaskLease",
    "TaskOwnershipError",
    "create_web_progress_state",
    "resolve_task_state_path",
    "task_timing_from_environment",
]
