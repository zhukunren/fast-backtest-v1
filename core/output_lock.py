"""Process-safe serialization for shared V1 output roots."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
from typing import Iterator


class OutputRunLockTimeout(RuntimeError):
    """Raised when another run still owns a shared output root."""


_LOCKS_GUARD = threading.Lock()
_LOCKS_BY_PATH: dict[str, threading.RLock] = {}
_HELD_LOCKS = threading.local()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS_BY_PATH.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS_BY_PATH[key] = lock
        return lock


def _held_depths() -> dict[str, int]:
    depths = getattr(_HELD_LOCKS, "depths", None)
    if not isinstance(depths, dict):
        depths = {}
        _HELD_LOCKS.depths = depths
    return depths


def _timeout_seconds() -> float:
    raw = os.environ.get("BACKTEST_OUTPUT_LOCK_TIMEOUT_SEC", "1800")
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return 1800.0
    return timeout if timeout > 0 else 1800.0


def _try_lock(handle) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_owner(handle, *, run_id: str | None, purpose: str) -> None:
    payload = {
        "pid": os.getpid(),
        "run_id": str(run_id or ""),
        "purpose": str(purpose or "run"),
        "acquired_at": time.time(),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    handle.seek(0)
    handle.truncate()
    handle.write(encoded)
    handle.flush()
    os.fsync(handle.fileno())


@contextmanager
def output_run_lock(
    output_root: Path | str,
    *,
    run_id: str | None = None,
    purpose: str = "run",
) -> Iterator[None]:
    """Serialize writers that retain backwards-compatible fixed output paths."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = str(root).casefold()
    depths = _held_depths()
    if key in depths:
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
            if depths[key] <= 0:
                depths.pop(key, None)
        return

    lock = _lock_for(root)
    timeout = _timeout_seconds()
    deadline = time.monotonic() + timeout
    with lock:
        lock_path = root / ".backtest_output.lock"
        with lock_path.open("a+b") as handle:
            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            while not _try_lock(handle):
                if time.monotonic() >= deadline:
                    raise OutputRunLockTimeout(
                        "timed out waiting for shared backtest output root: "
                        f"{root}"
                    )
                time.sleep(0.05)
            try:
                _write_owner(handle, run_id=run_id, purpose=purpose)
                depths[key] = 1
                yield
            finally:
                depths.pop(key, None)
                try:
                    _unlock(handle)
                except OSError:
                    pass


__all__ = ["OutputRunLockTimeout", "output_run_lock"]
