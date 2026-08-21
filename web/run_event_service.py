"""Read helpers for backtest JSONL run events."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
from typing import Any

from core.run_events import read_run_event_tail

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_EVENT_FILE = re.compile(r"^run_events_[A-Za-z0-9_.-]+\.jsonl$")


def get_run_event_roots() -> list[Path]:
    raw = str(os.environ.get("WEB_RUN_EVENT_ROOTS") or "").strip()
    if raw:
        parts = [part.strip() for part in raw.split(os.pathsep) if part.strip()]
        return [Path(part).expanduser().resolve() for part in parts]
    return [
        (_PROJECT_ROOT / "output" / "run_meta").resolve(),
        (_PROJECT_ROOT / "web" / "strategy_library").resolve(),
    ]


def _is_safe_run_id(run_id: object) -> bool:
    return isinstance(run_id, str) and bool(_SAFE_RUN_ID.fullmatch(run_id))


def _safe_lines(value: Any, *, default: int = 200) -> int:
    try:
        lines = int(value)
    except (TypeError, ValueError):
        lines = default
    return min(max(lines, 1), 1000)


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_run_event_file(run_id: str, *, roots: list[Path] | None = None) -> Path:
    if not _is_safe_run_id(run_id):
        raise ValueError("invalid run id")
    filename = f"run_events_{run_id}.jsonl"
    search_roots = roots or get_run_event_roots()
    for root in search_roots:
        root = Path(root).expanduser().resolve()
        direct = (root / filename).resolve()
        if direct.is_file() and _is_under_root(direct, root):
            return direct
        if root.is_dir():
            for path in sorted(root.glob(f"*/run_meta/{filename}"), key=lambda p: str(p).casefold()):
                resolved = path.resolve()
                if resolved.is_file() and _is_under_root(resolved, root):
                    return resolved
    raise FileNotFoundError(filename)


def list_run_event_files(*, limit: int = 50, roots: list[Path] | None = None) -> list[dict[str, Any]]:
    search_roots = roots or get_run_event_roots()
    items: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in search_roots:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        candidates = list(root.glob("run_events_*.jsonl")) + list(root.glob("*/run_meta/run_events_*.jsonl"))
        for path in sorted(candidates, key=lambda p: str(p).casefold()):
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file() or not _is_under_root(resolved, root):
                continue
            if not _SAFE_EVENT_FILE.fullmatch(resolved.name):
                continue
            try:
                stat = resolved.stat()
            except OSError:
                continue
            stem = resolved.name[: -len(".jsonl")]
            run_id = stem[len("run_events_") :]
            items.append(
                {
                    "run_id": run_id,
                    "name": resolved.name,
                    "path": str(resolved),
                    "size": stat.st_size,
                    "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
            seen.add(resolved)

    items.sort(key=lambda item: (str(item["modified_time"]), str(item["name"]).casefold()))
    if limit > 0:
        items = items[-limit:]
    return items


def read_run_events_for_web(
    run_id: str,
    *,
    lines: int = 200,
    roots: list[Path] | None = None,
) -> dict[str, Any]:
    line_count = _safe_lines(lines)
    path = resolve_run_event_file(run_id, roots=roots)
    payload = read_run_event_tail(path, lines=line_count)
    payload["run_id"] = run_id
    return payload


__all__ = [
    "get_run_event_roots",
    "list_run_event_files",
    "read_run_events_for_web",
    "resolve_run_event_file",
]
