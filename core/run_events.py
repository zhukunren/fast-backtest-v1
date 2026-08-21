"""Structured JSONL run events for local observability."""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Iterator


RUN_EVENT_SCHEMA_VERSION = 1


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def run_events_enabled_from_env() -> bool:
    return _truthy_env("RUN_EVENTS_JSONL", default=False)


def default_run_events_path(output_dir: Path | str, *, run_id: str | None = None) -> Path:
    name = "run_events.jsonl" if not run_id else f"run_events_{run_id}.jsonl"
    return Path(output_dir) / name


def _safe_lines(value: object, *, default: int = 200, upper: int = 1000) -> int:
    try:
        lines = int(value)
    except (TypeError, ValueError):
        lines = default
    return min(max(lines, 1), upper)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(raw) for key, raw in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def _parse_run_event_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"event": "raw", "raw": line}
    if isinstance(payload, dict):
        return payload
    return {"event": "raw", "raw": str(payload)}


def read_run_event_entries(path: Path | str, *, lines: int | None = None) -> list[dict[str, Any]]:
    """Read JSONL run events, optionally limiting to the newest `lines` entries."""
    path = Path(path)
    if lines is None:
        entries: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\r\n")
                if line:
                    entries.append(_parse_run_event_line(line))
        return entries

    tail: deque[str] = deque(maxlen=_safe_lines(lines))
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if line:
                tail.append(line)
    return [_parse_run_event_line(line) for line in tail]


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_run_events(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact status/stage summary from parsed run event entries."""
    rows = [row for row in entries if isinstance(row, dict)]
    event_names = [str(row.get("event") or "") for row in rows]
    counts = Counter(name for name in event_names if name)

    run_id = ""
    status = ""
    error = ""
    error_type = ""
    stages: list[dict[str, Any]] = []
    total_stage_duration_ms = 0.0

    for row in rows:
        if not run_id and row.get("run_id"):
            run_id = str(row.get("run_id"))
        event = str(row.get("event") or "")
        if event == "stage_end":
            duration = _coerce_float(row.get("duration_ms"))
            stage_row = {
                "stage": str(row.get("stage") or ""),
                "duration_ms": duration,
            }
            stages.append(stage_row)
            if duration is not None:
                total_stage_duration_ms += duration
        elif event == "run_complete":
            status = str(row.get("status") or "completed")
        elif event == "run_failed":
            status = str(row.get("status") or "failed")
            error = str(row.get("error") or "")
            error_type = str(row.get("error_type") or "")

    return {
        "run_id": run_id,
        "status": status,
        "started_at": str(rows[0].get("timestamp") or "") if rows else "",
        "finished_at": str(rows[-1].get("timestamp") or "") if rows else "",
        "last_event": event_names[-1] if event_names else "",
        "event_count": len(rows),
        "event_counts": dict(sorted(counts.items(), key=lambda item: item[0].casefold())),
        "stage_count": len(stages),
        "total_stage_duration_ms": round(total_stage_duration_ms, 6),
        "stages": stages,
        "error": error,
        "error_type": error_type,
    }


def build_run_event_snapshot(
    event_logger: object | None,
    *,
    lines: int = 200,
) -> dict[str, Any] | None:
    """Return a tail summary for a JSONL event logger, or None when unavailable."""
    if event_logger is None:
        return None
    if not bool(getattr(event_logger, "enabled", False)):
        return None
    path = getattr(event_logger, "path", None)
    if path is None:
        return None
    try:
        path = Path(path)
        if not path.exists():
            return None
        return read_run_event_tail(path, lines=lines)
    except Exception:
        return None


def read_run_event_tail(path: Path | str, *, lines: int = 200) -> dict[str, Any]:
    """Return the newest JSONL run events plus a compact summary."""
    path = Path(path)
    line_count = _safe_lines(lines)
    entries = read_run_event_entries(path, lines=line_count)
    return {
        "file": path.name,
        "path": str(path),
        "lines": line_count,
        "entries": entries,
        "summary": summarize_run_events(entries),
    }


@dataclass(slots=True)
class JsonlRunEventLogger:
    path: Path | str
    run_id: str
    enabled: bool = True
    common: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload = {
            "schema_version": RUN_EVENT_SCHEMA_VERSION,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "run_id": str(self.run_id),
            "event": str(event),
        }
        payload.update(_jsonable(self.common))
        payload.update(_jsonable(fields))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")

    @contextmanager
    def span(self, stage: str, **fields: Any) -> Iterator[None]:
        started = time.perf_counter()
        self.emit("stage_start", stage=stage, **fields)
        try:
            yield
        except Exception as exc:
            self.emit(
                "stage_error",
                stage=stage,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 6),
                error_type=type(exc).__name__,
                error=str(exc),
                **fields,
            )
            raise
        else:
            self.emit(
                "stage_end",
                stage=stage,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 6),
                **fields,
            )


__all__ = [
    "RUN_EVENT_SCHEMA_VERSION",
    "JsonlRunEventLogger",
    "build_run_event_snapshot",
    "default_run_events_path",
    "read_run_event_entries",
    "read_run_event_tail",
    "run_events_enabled_from_env",
    "summarize_run_events",
]
