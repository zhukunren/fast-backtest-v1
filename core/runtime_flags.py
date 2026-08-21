from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class RuntimeFlags:
    quiet: bool | None = None
    tag_log_path: str | None = None


_RUNTIME_FLAGS: ContextVar[RuntimeFlags] = ContextVar(
    "runtime_flags",
    default=RuntimeFlags(),
)


def get_runtime_flags() -> RuntimeFlags:
    return _RUNTIME_FLAGS.get()


def is_quiet_mode() -> bool:
    quiet = get_runtime_flags().quiet
    if quiet is not None:
        return bool(quiet)
    value = str(os.environ.get("RUN_QUIET", "")).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def get_tag_log_path() -> str:
    path = get_runtime_flags().tag_log_path
    if path is not None:
        return str(path).strip()
    return str(os.environ.get("TAG_LOG_PATH", "")).strip()


@contextmanager
def runtime_flags_scope(
    *,
    quiet: bool | None = None,
    tag_log_path: str | Path | None = None,
    sync_env: bool = False,
) -> Iterator[RuntimeFlags]:
    """Bind run flags to the current context without mutating process env by default."""
    current = get_runtime_flags()
    next_flags = RuntimeFlags(
        quiet=current.quiet if quiet is None else bool(quiet),
        tag_log_path=current.tag_log_path if tag_log_path is None else str(tag_log_path),
    )
    saved_env: dict[str, str | None] = {}
    if sync_env:
        for key, value in (
            ("RUN_QUIET", None if next_flags.quiet is None else ("1" if next_flags.quiet else "0")),
            ("TAG_LOG_PATH", next_flags.tag_log_path),
        ):
            saved_env[key] = os.environ.get(key)
            if value is None or str(value).strip() == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
    token = _RUNTIME_FLAGS.set(next_flags)
    try:
        yield next_flags
    finally:
        _RUNTIME_FLAGS.reset(token)
        if sync_env:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def runtime_flags_snapshot_scope(flags: RuntimeFlags | None) -> Iterator[RuntimeFlags | None]:
    """Bind a captured run snapshot while leaving an absent snapshot untouched."""
    if flags is None:
        yield None
        return
    with runtime_flags_scope(
        quiet=flags.quiet,
        tag_log_path=flags.tag_log_path,
        sync_env=False,
    ) as active_flags:
        yield active_flags


__all__ = [
    "RuntimeFlags",
    "get_runtime_flags",
    "get_tag_log_path",
    "is_quiet_mode",
    "runtime_flags_scope",
    "runtime_flags_snapshot_scope",
]
