from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
import uuid
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from tests_support.pytest_file_lock import try_lock_file, unlock_file


DEFAULT_TEMP_ROOT_NAME = "_pytest_tmp"
STALE_LEGACY_GRACE_SECONDS = 24 * 60 * 60
ORPHAN_MARKER_GRACE_SECONDS = 60
ACTIVE_MARKER_SUFFIX = ".active"

_CURRENT_RUN_PATTERN = re.compile(
    r"^run_[0-9]{8}T[0-9]{12}Z_[0-9]+_[0-9a-f]{12}$"
)
_LEGACY_RUN_PATTERN = re.compile(
    r"^run_[0-9]{10}_[0-9]+_[0-9a-f]{6}$"
)
_LEGACY_TMP_PATTERN = re.compile(r"^tmp_path_[0-9a-f]{8}$")

RemoveTree = Callable[[Path], None]
WarningSink = Callable[[str], None]


@dataclass(frozen=True)
class CleanupFailure:
    path: Path
    operation: str
    detail: str

    def describe(self) -> str:
        return f"{self.operation} failed for {self.path.absolute()}: {self.detail}"


class PytestTempCleanupError(RuntimeError):
    def __init__(self, failures: list[CleanupFailure]) -> None:
        self.failures = tuple(failures)
        super().__init__("; ".join(failure.describe() for failure in failures))


def is_ci_environment(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    value = source.get("CI", "")
    return value.strip().casefold() not in {"", "0", "false", "no", "off"}


def _is_reparse_point(path: Path) -> bool:
    path_stat = path.lstat()
    return _stat_is_reparse_point(path_stat)


def _stat_is_reparse_point(path_stat: os.stat_result) -> bool:
    file_attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(path_stat.st_mode) or bool(file_attributes & reparse_flag)


def _matches_owned_directory_name(name: str) -> bool:
    return bool(
        _CURRENT_RUN_PATTERN.fullmatch(name)
        or _LEGACY_RUN_PATTERN.fullmatch(name)
        or _LEGACY_TMP_PATTERN.fullmatch(name)
    )


def _is_safe_cleanup_target_checked(base: Path, candidate: Path) -> bool:
    base_absolute = base.absolute()
    candidate_absolute = candidate.absolute()
    if candidate_absolute.parent != base_absolute:
        return False
    if not _matches_owned_directory_name(candidate.name):
        return False
    candidate_stat = candidate.lstat()
    if _is_reparse_point(candidate) or not stat.S_ISDIR(candidate_stat.st_mode):
        return False
    return candidate.resolve(strict=True).parent == base.resolve(strict=True)


def is_safe_cleanup_target(base: Path, candidate: Path) -> bool:
    """Return whether candidate is an owned, direct, non-reparse child of base."""
    try:
        return _is_safe_cleanup_target_checked(base, candidate)
    except OSError:
        return False


def _validate_temp_root(rootpath: Path, base: Path) -> None:
    try:
        base_stat = base.lstat()
        if _is_reparse_point(base) or not stat.S_ISDIR(base_stat.st_mode):
            raise OSError("pytest temp root is not a regular directory")
        if base.resolve(strict=True).parent != rootpath.resolve(strict=True):
            raise OSError("pytest temp root escapes the repository root")
    except OSError as exc:
        raise OSError(f"unsafe pytest temp root {base.absolute()}: {exc}") from exc


def _remove_tree(path: Path) -> None:
    last_error: Exception | None = None
    for delay in (0.0, 0.02, 0.08, 0.2):
        if not path.exists():
            return
        if delay:
            time.sleep(delay)
        retry_errors: list[Exception] = []

        def _on_error(func, filename, exc_info) -> None:
            failure = exc_info[1]
            if isinstance(failure, FileNotFoundError):
                return
            try:
                target = Path(filename)
                target_stat = target.lstat()
                if _stat_is_reparse_point(target_stat):
                    retry_errors.append(failure)
                    return
                os.chmod(target, target_stat.st_mode | stat.S_IWRITE)
                func(filename)
            except FileNotFoundError:
                return
            except Exception as exc:  # pragma: no cover - platform-specific ACL retry
                retry_errors.append(exc)

        try:
            shutil.rmtree(path, onerror=_on_error)
        except FileNotFoundError:
            return
        except Exception as exc:
            retry_errors.append(exc)
        if not path.exists():
            return
        if retry_errors:
            last_error = retry_errors[-1]
    detail = str(last_error) if last_error is not None else "path still exists"
    raise OSError(f"path still exists after recursive removal: {detail}") from last_error


def _marker_path(base: Path, run_name: str) -> Path:
    return base / f".{run_name}{ACTIVE_MARKER_SUFFIX}"


def _try_lock_existing_marker(marker: Path) -> BinaryIO | None:
    marker_stat = marker.lstat()
    if _is_reparse_point(marker) or not stat.S_ISREG(marker_stat.st_mode):
        raise OSError("active marker is not a regular file")
    handle = marker.open("r+b")
    try:
        if marker.stat().st_size == 0:
            handle.write(b"\n")
            handle.flush()
        if not try_lock_file(handle):
            handle.close()
            return None
        return handle
    except BaseException:
        handle.close()
        raise


def _release_marker(handle: BinaryIO) -> None:
    try:
        unlock_file(handle)
    finally:
        handle.close()


def _entry_age_seconds(path: Path, now: float) -> float:
    return max(0.0, now - path.stat().st_mtime)


def _record_failure(
    failures: list[CleanupFailure],
    path: Path,
    operation: str,
    exc: BaseException,
) -> None:
    failures.append(CleanupFailure(path, operation, f"{type(exc).__name__}: {exc}"))


def _report_failures(
    failures: list[CleanupFailure],
    *,
    strict: bool,
    warning_sink: WarningSink | None,
) -> None:
    if not failures:
        return
    if strict:
        raise PytestTempCleanupError(failures)
    sink = warning_sink or (
        lambda message: warnings.warn(message, RuntimeWarning, stacklevel=3)
    )
    for failure in failures:
        sink(failure.describe())


def _remove_stale_run(
    entry: Path,
    marker: Path | None,
    *,
    remove_tree: RemoveTree,
    failures: list[CleanupFailure],
) -> bool:
    marker_handle: BinaryIO | None = None
    marker_exists = marker is not None and marker.exists()
    if marker_exists:
        try:
            marker_handle = _try_lock_existing_marker(marker)
        except FileNotFoundError:
            return False
        except OSError as exc:
            _record_failure(failures, marker, "inspect active marker", exc)
            return False
        if marker_handle is None:
            return False

    removed = False
    try:
        remove_tree(entry)
        if entry.exists():
            raise OSError("path still exists after recursive removal")
        removed = True
    except FileNotFoundError:
        removed = True
    except Exception as exc:
        _record_failure(failures, entry, "remove stale pytest directory", exc)
    finally:
        if marker_handle is not None:
            try:
                _release_marker(marker_handle)
            except Exception as exc:
                assert marker is not None
                _record_failure(failures, marker, "release active marker", exc)

    if marker_exists and removed:
        assert marker is not None
        try:
            marker.unlink(missing_ok=True)
        except Exception as exc:
            _record_failure(failures, marker, "remove stale active marker", exc)
    return removed


def _remove_orphan_markers(
    entries: list[Path],
    run_names: set[str],
    *,
    now: float,
    failures: list[CleanupFailure],
) -> None:
    for entry in entries:
        if not entry.name.startswith(".run_") or not entry.name.endswith(
            ACTIVE_MARKER_SUFFIX
        ):
            continue
        run_name = entry.name[1 : -len(ACTIVE_MARKER_SUFFIX)]
        if run_name in run_names or not (
            _CURRENT_RUN_PATTERN.fullmatch(run_name)
            or _LEGACY_RUN_PATTERN.fullmatch(run_name)
        ):
            continue
        try:
            if _entry_age_seconds(entry, now) < ORPHAN_MARKER_GRACE_SECONDS:
                continue
            handle = _try_lock_existing_marker(entry)
            if handle is None:
                continue
            _release_marker(handle)
            entry.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
        except Exception as exc:
            _record_failure(failures, entry, "remove orphan active marker", exc)


def cleanup_stale_entries(
    base: Path,
    *,
    strict: bool = False,
    now: float | None = None,
    legacy_grace_seconds: float = STALE_LEGACY_GRACE_SECONDS,
    remove_tree: RemoveTree = _remove_tree,
    warning_sink: WarningSink | None = None,
) -> list[Path]:
    """Remove stale owned entries while preserving active and untrusted paths."""
    if not base.exists():
        return []

    failures: list[CleanupFailure] = []
    removed: list[Path] = []
    current_time = time.time() if now is None else now
    try:
        entries = sorted(base.iterdir(), key=lambda entry: entry.name.casefold())
    except Exception as exc:
        _record_failure(failures, base, "enumerate pytest temp root", exc)
        _report_failures(failures, strict=strict, warning_sink=warning_sink)
        return removed

    run_names = {
        entry.name
        for entry in entries
        if _CURRENT_RUN_PATTERN.fullmatch(entry.name)
        or _LEGACY_RUN_PATTERN.fullmatch(entry.name)
    }
    for entry in entries:
        is_run = entry.name in run_names
        is_legacy_tmp = bool(_LEGACY_TMP_PATTERN.fullmatch(entry.name))
        if not (is_run or is_legacy_tmp):
            continue
        try:
            safe_target = _is_safe_cleanup_target_checked(base, entry)
        except FileNotFoundError:
            continue
        except OSError as exc:
            _record_failure(failures, entry, "inspect pytest cleanup target", exc)
            continue
        if not safe_target:
            continue

        marker = _marker_path(base, entry.name) if is_run else None
        if marker is not None and marker.exists():
            if _remove_stale_run(
                entry,
                marker,
                remove_tree=remove_tree,
                failures=failures,
            ):
                removed.append(entry)
            continue

        try:
            stale = _entry_age_seconds(entry, current_time) >= legacy_grace_seconds
        except Exception as exc:
            _record_failure(failures, entry, "inspect pytest directory age", exc)
            continue
        if not stale:
            continue
        if _remove_stale_run(
            entry,
            marker,
            remove_tree=remove_tree,
            failures=failures,
        ):
            removed.append(entry)

    _remove_orphan_markers(
        entries,
        run_names,
        now=current_time,
        failures=failures,
    )

    _report_failures(failures, strict=strict, warning_sink=warning_sink)
    return removed


@dataclass
class ManagedPytestRun:
    base: Path
    run_dir: Path
    marker_path: Path
    _marker_handle: BinaryIO
    strict_cleanup: bool
    _closed: bool = False

    def close(
        self,
        *,
        remove_tree: RemoveTree = _remove_tree,
        warning_sink: WarningSink | None = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        failures: list[CleanupFailure] = []
        run_removed = False
        try:
            remove_tree(self.run_dir)
            if self.run_dir.exists():
                raise OSError("path still exists after recursive removal")
            run_removed = True
        except FileNotFoundError:
            run_removed = True
        except Exception as exc:
            _record_failure(failures, self.run_dir, "remove pytest session directory", exc)
        finally:
            try:
                _release_marker(self._marker_handle)
            except Exception as exc:
                _record_failure(failures, self.marker_path, "release active marker", exc)

        if run_removed:
            try:
                self.marker_path.unlink(missing_ok=True)
            except Exception as exc:
                _record_failure(failures, self.marker_path, "remove active marker", exc)
        _report_failures(
            failures,
            strict=self.strict_cleanup,
            warning_sink=warning_sink,
        )


def start_managed_run(
    rootpath: Path,
    *,
    strict_cleanup: bool | None = None,
    now: datetime | None = None,
    pid: int | None = None,
    token: str | None = None,
    remove_tree: RemoveTree = _remove_tree,
    warning_sink: WarningSink | None = None,
) -> ManagedPytestRun:
    """Create the repository-owned basetemp for one pytest session."""
    strict = is_ci_environment() if strict_cleanup is None else strict_cleanup
    base = rootpath / DEFAULT_TEMP_ROOT_NAME
    base.mkdir(parents=True, exist_ok=True)
    _validate_temp_root(rootpath, base)
    cleanup_stale_entries(
        base,
        strict=strict,
        remove_tree=remove_tree,
        warning_sink=warning_sink,
    )

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_token = (token or uuid.uuid4().hex)[:12].casefold()
    run_pid = os.getpid() if pid is None else pid
    run_name = f"run_{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{run_pid}_{run_token}"
    if not _CURRENT_RUN_PATTERN.fullmatch(run_name):
        raise ValueError(f"invalid pytest run identity: {run_name}")

    marker_path = _marker_path(base, run_name)
    marker_handle = marker_path.open("x+b")
    try:
        payload = {
            "created_at": timestamp.isoformat(),
            "pid": run_pid,
            "run": run_name,
            "token": run_token,
            "version": 1,
        }
        marker_handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
        marker_handle.flush()
        if not try_lock_file(marker_handle):
            raise OSError(f"could not lock new active marker: {marker_path.absolute()}")
        run_dir = base / run_name
        run_dir.mkdir()
    except BaseException:
        try:
            _release_marker(marker_handle)
        except BaseException:
            marker_handle.close()
        marker_path.unlink(missing_ok=True)
        raise

    return ManagedPytestRun(
        base=base,
        run_dir=run_dir,
        marker_path=marker_path,
        _marker_handle=marker_handle,
        strict_cleanup=strict,
    )
