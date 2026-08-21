from __future__ import annotations

import os
import warnings
import uuid
from pathlib import Path

import pytest

from tests_support.pytest_logging import close_file_handlers_under
from tests_support.pytest_temp import (
    ManagedPytestRun,
    PytestTempCleanupError,
    is_ci_environment,
    start_managed_run,
)


_MANAGED_RUN_ATTRIBUTE = "_repository_managed_pytest_run"


def _managed_run(config) -> ManagedPytestRun | None:
    return getattr(config, _MANAGED_RUN_ATTRIBUTE, None)


def _configure_safe_dead_symlink_cleanup() -> None:
    import _pytest.pathlib as pathlib
    import _pytest.tmpdir as tmpdir

    if getattr(pathlib, "_repository_cleanup_dead_symlinks_patched", False):
        return
    original = pathlib.cleanup_dead_symlinks

    def _safe_cleanup(root) -> None:
        try:
            original(root)
        except PermissionError as exc:
            message = (
                f"pytest dead-symlink cleanup failed for {Path(root).absolute()}: "
                f"{type(exc).__name__}: {exc}"
            )
            if is_ci_environment():
                raise PermissionError(message) from exc
            warnings.warn(message, pytest.PytestWarning, stacklevel=2)

    pathlib.cleanup_dead_symlinks = _safe_cleanup
    tmpdir.cleanup_dead_symlinks = _safe_cleanup
    pathlib._repository_cleanup_dead_symlinks_patched = True


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config) -> None:
    """Give each default pytest session one repository-owned temporary root."""
    _configure_safe_dead_symlink_cleanup()
    if config.option.basetemp is not None:
        os.environ.setdefault(
            "WEB_TASK_STATE_DB",
            str(Path(config.option.basetemp) / "web_task_state.sqlite3"),
        )
        return
    try:
        managed_run = start_managed_run(Path(config.rootpath))
    except PytestTempCleanupError as exc:
        raise pytest.UsageError(str(exc)) from exc
    except OSError as exc:
        temp_root = Path(config.rootpath) / "_pytest_tmp"
        raise pytest.UsageError(
            f"pytest temp setup failed for {temp_root.absolute()}: {exc}"
        ) from exc
    setattr(config, _MANAGED_RUN_ATTRIBUTE, managed_run)
    config.option.basetemp = str(managed_run.run_dir)
    os.environ.setdefault(
        "WEB_TASK_STATE_DB",
        str(managed_run.run_dir / "web_task_state.sqlite3"),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session) -> None:
    managed_run = _managed_run(session.config)
    if managed_run is None:
        return
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is None or not hasattr(factory, "_basetemp"):
        raise pytest.UsageError(
            "unsupported pytest TempPathFactory contract; use the supported pytest 8.x release"
        )
    expected = managed_run.run_dir.resolve()
    given_basetemp = getattr(factory, "_given_basetemp", None)
    if given_basetemp is None:
        raise pytest.UsageError("pytest did not retain the managed basetemp")
    given = Path(given_basetemp).resolve()
    if given != expected:
        raise pytest.UsageError(
            f"pytest basetemp ownership mismatch: expected {expected}, got {given}"
        )
    # The directory already exists and is marker-protected; avoid pytest deleting
    # and recreating it with process-local Windows ACLs on first tmp_path use.
    factory._basetemp = expected


@pytest.fixture
def tmp_path(request) -> Path:
    managed_run = _managed_run(request.config)
    base = (
        managed_run.run_dir
        if managed_run is not None
        else request.config._tmp_path_factory.getbasetemp()
    )
    path = base / f"tmp_path_{uuid.uuid4().hex[:12]}"
    path.mkdir()
    return path


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config) -> None:
    managed_run = _managed_run(config)
    if managed_run is None:
        return
    close_file_handlers_under(managed_run.run_dir)
    try:
        managed_run.close(
            warning_sink=lambda message: warnings.warn(message, pytest.PytestWarning)
        )
    except PytestTempCleanupError as exc:
        warnings.warn(str(exc), pytest.PytestWarning, stacklevel=2)
        raise pytest.UsageError(str(exc)) from exc
