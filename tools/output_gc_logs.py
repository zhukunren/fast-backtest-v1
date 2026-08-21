"""Consolidate legacy operational logs and enforce bounded retention."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any

from core.local_artifact_paths import log_root
from tools.output_gc_safety import (
    ACTIVE_NAME,
    has_pinned_ancestor,
    is_inside,
    is_link,
    modification_time,
    path_size,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output"
LEGACY_WEB_LOG_ROOT = ROOT / "web" / "data" / "logs"
_LOG_SUFFIXES = (".jsonl", ".log", ".out", ".stderr", ".stdout")
_DUPLICATE_QUIET_SECONDS = 5 * 60.0


def _is_log_file(path: Path) -> bool:
    return path.is_file() and path.name.casefold().endswith(_LOG_SUFFIXES)


def _is_run_artifact_log(path: Path) -> bool:
    name = path.name.casefold()
    return (name.startswith("backtest_") and name.endswith(".log")) or name.startswith(
        "run_events"
    )


def _same_file_content(left: Path, right: Path) -> bool:
    if any(is_link(path) or not path.is_file() for path in (left, right)):
        return False
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        digests: list[str] = []
        for path in (left, right):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digests.append(digest.hexdigest())
        return digests[0] == digests[1]
    except OSError:
        return False


def _can_remove_duplicate(source: Path, destination: Path, *, age: float) -> bool:
    return age >= _DUPLICATE_QUIET_SECONDS and _same_file_content(
        source,
        destination,
    )


def _legacy_roots(
    repository_root: Path,
    output_root: Path,
    legacy_web_log_root: Path,
) -> dict[str, Path]:
    return {
        "repository": repository_root.resolve(),
        "backtest_process": (repository_root / "backtest" / "reporting").resolve(),
        "output_process": output_root.resolve(),
        "web_process": (output_root / "web").resolve(),
        "web_event": legacy_web_log_root.resolve(),
    }


def _legacy_group(path: Path, roots: dict[str, Path]) -> str:
    try:
        parent = path.parent.resolve()
    except OSError:
        return ""
    name = path.name.casefold()
    if parent == roots["repository"] and name.endswith(".log"):
        return "repository"
    if (
        parent == roots["backtest_process"]
        and name.startswith(".web-codex-")
        and name.endswith(".log")
    ):
        return "backtest_process"
    if parent == roots["output_process"] and name.endswith(".log"):
        return "output_process"
    if parent == roots["web_process"] and name.endswith(".log"):
        return "web_process"
    if parent == roots["web_event"] and name.endswith(".jsonl"):
        return "web_event"
    return ""


def _destination(central_root: Path, group: str, path: Path) -> Path:
    if group == "web_event":
        return central_root / "web" / path.name
    if group in {"output_process", "web_process"}:
        return central_root / "web" / "process" / path.name
    if group == "backtest_process":
        return central_root / "backtest" / "process" / path.name.lstrip(".")
    return central_root / "misc" / path.name


def _legacy_files(roots: dict[str, Path]) -> list[Path]:
    files: list[Path] = []
    patterns = {
        "repository": "*.log",
        "backtest_process": ".web-codex-*.log",
        "output_process": "*.log",
        "web_process": "*.log",
        "web_event": "*.jsonl",
    }
    for group in sorted(patterns, key=str.casefold):
        root = roots[group]
        if not root.is_dir():
            continue
        files.extend(
            path
            for path in sorted(root.glob(patterns[group]), key=lambda item: item.name.casefold())
            if path.is_file()
            and not (
                group in {"output_process", "web_process"}
                and _is_run_artifact_log(path)
            )
        )
    return sorted(set(files), key=lambda item: str(item).casefold())


def _central_log_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(
            [name for name in directories if not is_link(Path(current) / name)],
            key=str.casefold,
        )
        for name in sorted(names, key=str.casefold):
            path = Path(current) / name
            if _is_log_file(path) and not is_link(path) and not _is_run_artifact_log(path):
                files.append(path)
    return files


def _entry(
    path: Path,
    *,
    kind: str,
    group: str,
    action: str,
    destination: Path | None,
    retention_seconds: float,
    now: float,
    marker_root: Path,
) -> dict[str, Any]:
    age = max(0.0, now - modification_time(path))
    reasons: list[str] = []
    if is_link(path):
        reasons.append("source_is_link")
    if has_pinned_ancestor(path, stop=marker_root):
        reasons.append("pin_marker")
    if (path.parent / ACTIVE_NAME).exists():
        reasons.append("active_marker")
    if kind == "central_log" and age < retention_seconds:
        reasons.append("within_retention")
    if (
        destination is not None
        and destination.exists()
        and action != "remove_duplicate"
    ):
        reasons.append("destination_exists")
    return {
        "kind": kind,
        "group": group,
        "action": action,
        "source": str(path.resolve()),
        "destination": str(destination.resolve()) if destination is not None else "",
        "size_bytes": path_size(path),
        "age_seconds": age,
        "status": "protected" if reasons else "candidate",
        "reasons": reasons,
    }


def plan_log_cleanup(
    *,
    retention_seconds: float = 30 * 24 * 60 * 60,
    now: float | None = None,
    repository_root: Path = ROOT,
    output_root: Path = OUTPUT_ROOT,
    legacy_web_log_root: Path = LEGACY_WEB_LOG_ROOT,
) -> dict[str, Any]:
    """Build a deterministic consolidation and retention plan."""

    if retention_seconds < 0:
        raise ValueError("retention_seconds must be non-negative")
    repository_root = Path(repository_root).resolve()
    output_root = Path(output_root).resolve()
    legacy_web_log_root = Path(legacy_web_log_root).resolve()
    central_root = log_root().resolve()
    if not is_inside(output_root, repository_root):
        raise ValueError("output root must be inside repository")
    roots = _legacy_roots(repository_root, output_root, legacy_web_log_root)
    now_epoch = float(time.time() if now is None else now)
    items: list[dict[str, Any]] = []

    for path in _legacy_files(roots):
        group = _legacy_group(path, roots)
        age = max(0.0, now_epoch - modification_time(path))
        action = "remove" if age >= retention_seconds else "move"
        destination = None if action == "remove" else _destination(central_root, group, path)
        if (
            destination is not None
            and destination.exists()
            and _can_remove_duplicate(path, destination, age=age)
        ):
            action = "remove_duplicate"
        items.append(
            _entry(
                path,
                kind="legacy_log",
                group=group,
                action=action,
                destination=destination,
                retention_seconds=retention_seconds,
                now=now_epoch,
                marker_root=roots[group],
            )
        )

    for path in _central_log_files(central_root):
        items.append(
            _entry(
                path,
                kind="central_log",
                group="central",
                action="remove",
                destination=None,
                retention_seconds=retention_seconds,
                now=now_epoch,
                marker_root=central_root,
            )
        )

    items.sort(key=lambda item: str(item["source"]).casefold())
    candidates = [item for item in items if item["status"] == "candidate"]
    protected = [item for item in items if item["status"] != "candidate"]
    return {
        "schema_version": 1,
        "dry_run": True,
        "repository_root": str(repository_root),
        "output_root": str(output_root),
        "legacy_web_log_root": str(legacy_web_log_root),
        "log_root": str(central_root),
        "planned_at": now_epoch,
        "retention_seconds": float(retention_seconds),
        "items": items,
        "moved": [],
        "removed": [],
        "protected": protected,
        "summary": {
            "item_count": len(items),
            "candidate_count": len(candidates),
            "candidate_size_bytes": sum(int(item["size_bytes"]) for item in candidates),
            "move_count": sum(1 for item in candidates if item["action"] == "move"),
            "remove_count": sum(
                1
                for item in candidates
                if item["action"] in {"remove", "remove_duplicate"}
            ),
            "duplicate_remove_count": sum(
                1 for item in candidates if item["action"] == "remove_duplicate"
            ),
            "protected_count": len(protected),
        },
    }


def _resolve_cleanup_target(
    item: dict[str, Any],
    *,
    roots: dict[str, Path],
    central_root: Path,
) -> tuple[Path, str, Path, Path | None]:
    source = Path(str(item["source"])).resolve()
    action = str(item.get("action") or "")
    if not source.is_file() or is_link(source) or not _is_log_file(source):
        raise ValueError("source is not a regular log file")
    if item.get("kind") == "central_log":
        if action != "remove" or not is_inside(source, central_root):
            raise ValueError("central log escaped its root")
        return source, action, central_root, None

    group = _legacy_group(source, roots)
    if not group or group != item.get("group"):
        raise ValueError("legacy log escaped its allowed root")
    marker_root = roots[group]
    if action == "remove":
        return source, action, marker_root, None
    if action not in {"move", "remove_duplicate"}:
        raise ValueError("unsupported log action")
    destination = Path(str(item.get("destination") or "")).resolve()
    expected = _destination(central_root, group, source).resolve()
    if destination != expected or not is_inside(destination, central_root):
        raise ValueError("log destination is outside the central root")
    if action == "move" and destination.exists():
        raise FileExistsError(destination)
    if action == "remove_duplicate" and not _same_file_content(source, destination):
        raise ValueError("duplicate log content changed before apply")
    return source, action, marker_root, destination


def apply_log_cleanup(report: dict[str, Any]) -> dict[str, Any]:
    """Apply a previously reviewed plan with live path and age checks."""

    repository_root = Path(str(report["repository_root"])).resolve()
    output_root = Path(str(report["output_root"])).resolve()
    legacy_web_log_root = Path(str(report["legacy_web_log_root"])).resolve()
    central_root = Path(str(report["log_root"])).resolve()
    roots = _legacy_roots(repository_root, output_root, legacy_web_log_root)
    retention_seconds = float(report.get("retention_seconds") or 0.0)
    apply_now = max(time.time(), float(report.get("planned_at") or 0.0))
    moved: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    newly_protected: list[dict[str, Any]] = []

    for item in report.get("items", []):
        if item.get("status") != "candidate":
            continue
        try:
            source, action, marker_root, destination = _resolve_cleanup_target(
                item,
                roots=roots,
                central_root=central_root,
            )
            if has_pinned_ancestor(source, stop=marker_root):
                raise ValueError("PIN marker appeared before apply")
            if (source.parent / ACTIVE_NAME).exists():
                raise ValueError("ACTIVE marker appeared before apply")
            live_age = max(0.0, apply_now - modification_time(source))
            if action == "remove" and live_age < retention_seconds:
                raise ValueError("log became fresh before apply")
            if (
                action == "remove_duplicate"
                and live_age < _DUPLICATE_QUIET_SECONDS
            ):
                raise ValueError("duplicate log became active before apply")
            if action == "move":
                assert destination is not None
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append({**item, "status": "moved"})
            else:
                source.unlink()
                removed.append({**item, "status": "removed"})
        except (OSError, ValueError) as exc:
            newly_protected.append(
                {
                    **item,
                    "status": "protected",
                    "reasons": [f"apply:{type(exc).__name__}:{exc}"],
                }
            )

    report["dry_run"] = False
    report["moved"] = moved
    report["removed"] = removed
    report["protected"] = sorted(
        [*report.get("protected", []), *newly_protected],
        key=lambda item: str(item.get("source", "")).casefold(),
    )
    report["summary"]["moved_count"] = len(moved)
    report["summary"]["removed_count"] = len(removed)
    report["summary"]["removed_size_bytes"] = sum(
        int(item["size_bytes"]) for item in removed
    )
    report["summary"]["apply_protected_count"] = len(newly_protected)
    return report


__all__ = ["apply_log_cleanup", "plan_log_cleanup"]
