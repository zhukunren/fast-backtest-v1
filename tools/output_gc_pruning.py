"""Discover and prune stale temporary workspaces and partial cache files."""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from core.local_artifact_paths import REPOSITORY_ROOT, cache_root, temp_root
from tools.output_gc_safety import (
    ACTIVE_NAME,
    PARTIAL_SUFFIXES,
    PIN_NAME,
    has_marker,
    has_pinned_ancestor,
    is_inside,
    is_link,
    modification_time,
    path_size,
)


_WEB_TEMP_RE = re.compile(r"tmp[a-z0-9_]{8}\Z", re.IGNORECASE)
_REPOSITORY_CACHE_NAMES = {
    "__pycache__",
    ".codex_tmp",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_REPOSITORY_SOURCE_ROOTS = (
    "app",
    "backtest",
    "config",
    "core",
    "data",
    "runtime_lifecycle",
    "strategies",
    "strategy_library",
    "tests",
    "tests_support",
    "tools",
    "trading",
    "web",
)


def _repository_relative_parts(path: Path, repository_root: Path) -> tuple[str, ...] | None:
    try:
        parts = path.resolve().relative_to(repository_root.resolve()).parts
    except ValueError:
        return None
    return tuple(part.casefold() for part in parts)


def _repository_root_temp_kind(parts: tuple[str, ...]) -> str:
    if len(parts) != 1:
        return ""
    name = parts[0]
    if name in _REPOSITORY_CACHE_NAMES:
        return "repository_cache"
    if name == "_pytest_tmp" or name.startswith(".pytest_tmp_"):
        return "repository_test_temp"
    if name == "build" or name.endswith(".egg-info"):
        return "repository_build"
    return ""


def _repository_web_temp_kind(parts: tuple[str, ...]) -> str:
    if len(parts) == 2 and parts[0] == "web":
        name = parts[1]
        if name == "__pycache__":
            return "repository_cache"
        if name.startswith(".pytest_tmp"):
            return "repository_test_temp"
        if _WEB_TEMP_RE.fullmatch(name):
            return "repository_web_temp"
    if len(parts) == 3 and parts[:2] == ("web", "data") and parts[2].startswith("pytest_"):
        return "repository_test_temp"
    return ""


def _repository_scratch_kind(parts: tuple[str, ...]) -> str:
    if len(parts) == 2 and parts[0] == ".tmp" and parts[1] != "jshs":
        return "repository_scratch"
    return ""


def _repository_nested_cache_kind(parts: tuple[str, ...]) -> str:
    if not parts or parts[0] not in _REPOSITORY_SOURCE_ROOTS:
        return ""
    # ``web/data`` contains retained admin and audit evidence; only its
    # explicit pytest scratch roots are handled by the shallow scan above.
    if parts[0] == "web" and len(parts) > 1 and parts[1] == "data":
        return ""
    if parts[-1] in _REPOSITORY_CACHE_NAMES:
        return "repository_cache"
    return ""


def _repository_temp_kind(path: Path, repository_root: Path) -> str:
    parts = _repository_relative_parts(path, repository_root)
    if parts is None:
        return ""
    return (
        _repository_root_temp_kind(parts)
        or _repository_web_temp_kind(parts)
        or _repository_scratch_kind(parts)
        or _repository_nested_cache_kind(parts)
    )


def _repository_shallow_entries(root: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for parent in (root, root / "web", root / "web" / "data", root / ".tmp"):
        if not parent.is_dir():
            continue
        for path in sorted(parent.iterdir(), key=lambda item: item.name.casefold()):
            kind = _repository_temp_kind(path, root)
            if not kind or (not path.is_dir() and not is_link(path)):
                continue
            entries.append((path, kind))
    return entries


def _repository_source_cache_entries(root: Path) -> list[Path]:
    entries: list[Path] = []
    # Source-package caches are disposable too, but the traversal is bounded
    # to explicit package roots and never descends into ``web/data`` evidence.
    for source_name in _REPOSITORY_SOURCE_ROOTS:
        source_root = root / source_name
        if not source_root.is_dir() or is_link(source_root):
            continue
        for current, directories, _files in os.walk(
            source_root,
            topdown=True,
            followlinks=False,
            onerror=lambda _error: None,
        ):
            current_path = Path(current)
            kept: list[str] = []
            for name in sorted(directories, key=str.casefold):
                path = current_path / name
                folded_name = name.casefold()
                if source_name == "web" and current_path == source_root and folded_name == "data":
                    continue
                if is_link(path):
                    continue
                if folded_name in _REPOSITORY_CACHE_NAMES:
                    entries.append(path)
                    continue
                kept.append(name)
            directories[:] = kept
    return entries


def _append_repository_candidate(
    items: list[dict[str, Any]],
    seen: set[Path],
    path: Path,
    kind: str,
    *,
    root: Path,
    older_than_seconds: float,
    now: float,
) -> None:
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    age = max(0.0, now - modification_time(path))
    reasons: list[str] = []
    if is_link(path):
        reasons.append("source_is_link")
    else:
        if age < older_than_seconds:
            reasons.append("not_stale")
        if has_pinned_ancestor(path, stop=root):
            reasons.append("pin_marker")
        if has_marker(path, ACTIVE_NAME):
            reasons.append("active_marker")
    items.append(
        {
            "kind": kind,
            "path": str(resolved),
            "size_bytes": path_size(path),
            "age_seconds": age,
            "status": "protected" if reasons else "candidate",
            "reasons": reasons,
        }
    )


def _repository_temp_candidates(
    repository_root: Path,
    *,
    older_than_seconds: float,
    now: float,
) -> list[dict[str, Any]]:
    root = Path(repository_root).resolve()
    if root == Path(root.anchor) or not root.name:
        raise ValueError(f"refusing broad repository root: {root}")
    items: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path, kind in _repository_shallow_entries(root):
        _append_repository_candidate(
            items,
            seen,
            path,
            kind,
            root=root,
            older_than_seconds=older_than_seconds,
            now=now,
        )
    for path in _repository_source_cache_entries(root):
        _append_repository_candidate(
            items,
            seen,
            path,
            "repository_cache",
            root=root,
            older_than_seconds=older_than_seconds,
            now=now,
        )
    return items


def _temp_candidates(*, older_than_seconds: float, now: float) -> list[dict[str, Any]]:
    root = temp_root().resolve()
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for kind in ("formal-worktrees", "public-repos", "comparisons", "perf-smoke"):
        kind_root = root / kind
        if not kind_root.is_dir():
            continue
        for path in sorted(kind_root.iterdir(), key=lambda item: item.name.casefold()):
            if is_link(path):
                continue
            age = max(0.0, now - modification_time(path))
            reasons: list[str] = []
            if age < older_than_seconds:
                reasons.append("not_stale")
            if has_marker(path, PIN_NAME):
                reasons.append("pin_marker")
            if has_marker(path, ACTIVE_NAME):
                reasons.append("active_marker")
            items.append(
                {
                    "kind": kind,
                    "path": str(path.resolve()),
                    "size_bytes": path_size(path),
                    "age_seconds": age,
                    "status": "protected" if reasons else "candidate",
                    "reasons": reasons,
                }
            )
    return items


def _stale_cache_partials(*, older_than_seconds: float, now: float) -> list[dict[str, Any]]:
    root = cache_root().resolve()
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(directories, key=str.casefold)
        for name in sorted(files, key=str.casefold):
            path = Path(current) / name
            if not name.casefold().endswith(PARTIAL_SUFFIXES):
                continue
            age = max(0.0, now - modification_time(path))
            reasons: list[str] = []
            if age < older_than_seconds:
                reasons.append("not_stale")
            if has_pinned_ancestor(path, stop=root):
                reasons.append("pin_marker")
            items.append(
                {
                    "kind": "cache_partial",
                    "path": str(path.resolve()),
                    "size_bytes": path_size(path),
                    "age_seconds": age,
                    "status": "protected" if reasons else "candidate",
                    "reasons": reasons,
                }
            )
    return items


def _validate_live_candidate(path: Path, *, older_than_seconds: float, now: float) -> None:
    live_age = max(0.0, now - modification_time(path))
    if live_age < older_than_seconds:
        raise ValueError("path became active before apply")
    if is_link(path) or has_marker(path, PIN_NAME) or has_marker(path, ACTIVE_NAME):
        raise ValueError("marker appeared before apply")


def _remove_cache_partial(path: Path) -> None:
    if not is_inside(path, cache_root()):
        raise ValueError("cache partial escaped cache root")
    if has_pinned_ancestor(path, stop=cache_root()):
        raise ValueError("PIN marker appeared before apply")
    path.unlink()


def _remove_repository_temporary(path: Path, *, kind: str, repository_root: Path) -> None:
    if not is_inside(path, repository_root):
        raise ValueError("repository temporary path escaped its root")
    if _repository_temp_kind(path, repository_root) != kind:
        raise ValueError("repository temporary path no longer matches its allowlist")
    if has_pinned_ancestor(path, stop=repository_root):
        raise ValueError("PIN marker appeared before apply")
    shutil.rmtree(path)


def _remove_workspace_temporary(path: Path) -> None:
    if not is_inside(path, temp_root()):
        raise ValueError("temporary workspace escaped temp root")
    shutil.rmtree(path)


def _remove_candidate(
    item: dict[str, Any],
    *,
    older_than_seconds: float,
    now: float,
    repository_root: Path,
) -> dict[str, Any]:
    path = Path(str(item["path"])).resolve()
    _validate_live_candidate(path, older_than_seconds=older_than_seconds, now=now)
    kind = str(item["kind"])
    if kind == "cache_partial":
        _remove_cache_partial(path)
    elif kind.startswith("repository_"):
        _remove_repository_temporary(path, kind=kind, repository_root=repository_root)
    else:
        _remove_workspace_temporary(path)
    return {**item, "status": "removed"}


def _apply_candidates(
    items: list[dict[str, Any]],
    *,
    apply: bool,
    older_than_seconds: float,
    now: float,
    repository_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = [item for item in items if item["status"] != "candidate"]
    if not apply:
        return removed, protected
    for item in items:
        if item["status"] != "candidate":
            continue
        try:
            removed.append(
                _remove_candidate(
                    item,
                    older_than_seconds=older_than_seconds,
                    now=now,
                    repository_root=repository_root,
                )
            )
        except (OSError, ValueError) as exc:
            protected.append(
                {
                    **item,
                    "status": "protected",
                    "reasons": [f"apply:{type(exc).__name__}:{exc}"],
                }
            )
    return removed, protected


def prune_temporary_storage(
    *,
    older_than_seconds: float = 7 * 24 * 60 * 60,
    apply: bool = False,
    now: float | None = None,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds must be non-negative")
    now_epoch = float(time.time() if now is None else now)
    repository_root = Path(repository_root).resolve()
    items = _temp_candidates(older_than_seconds=older_than_seconds, now=now_epoch)
    items.extend(_stale_cache_partials(older_than_seconds=older_than_seconds, now=now_epoch))
    items.extend(
        _repository_temp_candidates(
            repository_root,
            older_than_seconds=older_than_seconds,
            now=now_epoch,
        )
    )
    items.sort(key=lambda item: str(item["path"]).casefold())
    removed, protected = _apply_candidates(
        items,
        apply=apply,
        older_than_seconds=older_than_seconds,
        now=now_epoch,
        repository_root=repository_root,
    )
    return {
        "schema_version": 1,
        "dry_run": not apply,
        "older_than_seconds": float(older_than_seconds),
        "items": items,
        "removed": removed,
        "protected": sorted(protected, key=lambda item: str(item["path"]).casefold()),
        "summary": {
            "item_count": len(items),
            "candidate_count": sum(1 for item in items if item["status"] == "candidate"),
            "removed_count": len(removed),
            "removed_size_bytes": sum(int(item["size_bytes"]) for item in removed),
            "protected_count": len(protected),
        },
    }
