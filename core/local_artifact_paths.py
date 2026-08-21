"""Shared local cache and temporary-workspace paths.

The repository output tree is reserved for reproducible evidence.  Downloaded
dependencies and disposable workspaces use the paths in this module instead.
All functions are side-effect free; callers create the returned directories
only when they actually need them.
"""
from __future__ import annotations

import importlib.metadata
import os
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = REPOSITORY_ROOT / ".cache" / "jshs"
DEFAULT_TEMP_ROOT = REPOSITORY_ROOT / ".tmp" / "jshs"
DEFAULT_LOG_ROOT = REPOSITORY_ROOT / "output" / "logs"

_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _configured_root(environment_name: str, default: Path) -> Path:
    raw = str(os.environ.get(environment_name, "") or "").strip()
    candidate = Path(raw).expanduser() if raw else default
    if not candidate.is_absolute():
        candidate = REPOSITORY_ROOT / candidate
    return candidate.resolve()


def cache_root() -> Path:
    """Return the shared, reusable cache root."""

    return _configured_root("JSHS_CACHE_ROOT", DEFAULT_CACHE_ROOT)


def temp_root() -> Path:
    """Return the disposable workspace root."""

    return _configured_root("JSHS_TMP_ROOT", DEFAULT_TEMP_ROOT)


def log_root() -> Path:
    """Return the root for operational logs that are not run artifacts."""

    return _configured_root("JSHS_LOG_ROOT", DEFAULT_LOG_ROOT)


def _component(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not _COMPONENT_RE.fullmatch(text):
        raise ValueError(f"invalid {label}: {value!r}")
    return text


def _slug(value: str, *, label: str) -> str:
    text = str(value or "").strip().replace("\\", "--").replace("/", "--")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    text = text.strip("-.")
    return _component(text, label=label)


def model_cache_dir(model_name: str, revision: str) -> Path:
    """Return a revision-pinned directory for one reusable model."""

    return (
        cache_root()
        / "models"
        / _slug(model_name, label="model name")
        / _component(revision, label="model revision")
    )


def document_tool_cache_dir(tool_name: str, version: str) -> Path:
    """Return a versioned directory for a document QA dependency."""

    return (
        cache_root()
        / "document-tools"
        / _slug(tool_name, label="document tool name")
        / _component(version, label="document tool version")
    )


def _numba_version() -> str:
    try:
        value = importlib.metadata.version("numba")
    except importlib.metadata.PackageNotFoundError:
        value = "unknown"
    return _slug(value, label="Numba version")


def codex_worker_cache_root() -> Path:
    """Return a cache namespace compatible with this Python/Numba runtime."""

    python_tag = str(getattr(sys.implementation, "cache_tag", "python"))
    return (
        cache_root()
        / "codex-worker"
        / _component(python_tag, label="Python cache tag")
        / f"numba-{_numba_version()}"
    )


def codex_worker_cache_dirs() -> tuple[Path, Path]:
    root = codex_worker_cache_root()
    return root / "pycache", root / "numba"


def temporary_workspace_dir(kind: str, name: str | None = None) -> Path:
    """Return a path below the disposable workspace namespace."""

    kind_path = _slug(kind, label="temporary workspace kind")
    root = temp_root() / kind_path
    if name is None or not str(name).strip():
        return root
    return root / _slug(name, label="temporary workspace name")


def formal_worktree_root(name: str | None = None) -> Path:
    return temporary_workspace_dir("formal-worktrees", name)


def public_repo_workspace_root(name: str | None = None) -> Path:
    return temporary_workspace_dir("public-repos", name)


def comparison_workspace_root(name: str | None = None) -> Path:
    return temporary_workspace_dir("comparisons", name)


def performance_workspace_root(name: str | None = None) -> Path:
    return temporary_workspace_dir("perf-smoke", name)


def operational_log_dir(component: str | None = None) -> Path:
    """Return one component namespace below the operational log root."""

    root = log_root()
    if component is None or not str(component).strip():
        return root
    return root / _component(component, label="log component")


__all__ = [
    "DEFAULT_CACHE_ROOT",
    "DEFAULT_LOG_ROOT",
    "DEFAULT_TEMP_ROOT",
    "REPOSITORY_ROOT",
    "cache_root",
    "codex_worker_cache_dirs",
    "codex_worker_cache_root",
    "comparison_workspace_root",
    "document_tool_cache_dir",
    "formal_worktree_root",
    "log_root",
    "model_cache_dir",
    "operational_log_dir",
    "performance_workspace_root",
    "public_repo_workspace_root",
    "temp_root",
    "temporary_workspace_dir",
]
