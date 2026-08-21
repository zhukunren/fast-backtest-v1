"""
Structured logging helpers for the web subsystem.

The service writes JSON Lines into output/logs/web by default and exposes
small read helpers for Flask API endpoints. Logging must never block the
backtest path, so write failures are swallowed by log_event().
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
from typing import Any

from core.local_artifact_paths import operational_log_dir


_LOGGER_NAME = "web.log_service"
_SAFE_LOG_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.jsonl$")
_MAX_TEXT_LENGTH = 2000
_MAX_LIST_ITEMS = 80
_MAX_DICT_ITEMS = 120
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "access_key",
    "credential",
)

_lock = threading.RLock()
_configured_log_path: Path | None = None


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "web_event", None)
        if not isinstance(event, dict):
            event = {
                "timestamp": _now_text(),
                "level": record.levelname,
                "tag": record.name,
                "message": record.getMessage(),
            }
        event.setdefault("timestamp", _now_text())
        event.setdefault("level", record.levelname)
        event.setdefault("logger", record.name)
        return json.dumps(_sanitize_value(event), ensure_ascii=False, default=str)


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _truncate_text(value: str, limit: int = _MAX_TEXT_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated>"


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).strip().lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return _truncate_text(str(value))
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, Path):
        return _truncate_text(str(value))
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_DICT_ITEMS:
                result["__truncated__"] = True
                break
            text_key = _truncate_text(str(key), 160)
            if _is_sensitive_key(text_key):
                result[text_key] = "<redacted>"
            else:
                result[text_key] = _sanitize_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        result = [
            _sanitize_value(item, depth=depth + 1)
            for item in list(value)[:_MAX_LIST_ITEMS]
        ]
        if len(value) > _MAX_LIST_ITEMS:
            result.append({"__truncated__": True})
        return result
    if isinstance(value, set):
        items = sorted(value, key=lambda item: str(item).casefold())
        result = [
            _sanitize_value(item, depth=depth + 1)
            for item in items[:_MAX_LIST_ITEMS]
        ]
        if len(items) > _MAX_LIST_ITEMS:
            result.append({"__truncated__": True})
        return result
    return _truncate_text(str(value))


def _normalize_level(level: str | int | None) -> tuple[int, str]:
    if isinstance(level, int):
        level_name = logging.getLevelName(level)
        if isinstance(level_name, str):
            return level, level_name
        return level, "INFO"
    text = str(level or "INFO").strip().upper()
    if text not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        text = "INFO"
    return int(getattr(logging, text)), text


def get_log_dir() -> Path:
    raw = str(os.environ.get("WEB_LOG_DIR") or "").strip()
    return (
        Path(raw).expanduser().resolve()
        if raw
        else operational_log_dir("web")
    )


def _current_log_path() -> Path:
    return get_log_dir() / f"web-{datetime.now().strftime('%Y%m%d')}.jsonl"


def _configure_logger() -> logging.Logger:
    global _configured_log_path
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    log_path = _current_log_path()
    with _lock:
        if _configured_log_path == log_path and logger.handlers:
            return logger

        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(handler)
        _configured_log_path = log_path
    return logger


def get_web_logger() -> logging.Logger:
    return _configure_logger()


def log_event(level: str | int | None, tag: str, message: str, **fields: Any) -> dict[str, Any]:
    level_no, level_name = _normalize_level(level)
    event: dict[str, Any] = {
        "timestamp": _now_text(),
        "level": level_name,
        "tag": str(tag or "web").strip() or "web",
        "message": str(message or "").strip(),
        "pid": os.getpid(),
    }
    for key, value in fields.items():
        if _is_sensitive_key(key):
            event[str(key)] = "<redacted>"
        else:
            event[str(key)] = value

    sanitized = _sanitize_value(event)
    try:
        get_web_logger().log(level_no, sanitized.get("message", ""), extra={"web_event": sanitized})
    except Exception:
        pass
    return sanitized


def _safe_lines(value: Any, *, default: int = 200) -> int:
    try:
        lines = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(lines, 1), 1000)


def list_log_files(limit: int | None = 50) -> list[dict[str, Any]]:
    log_dir = get_log_dir()
    if not log_dir.is_dir():
        return []

    files: list[dict[str, Any]] = []
    for name in sorted(os.listdir(log_dir), key=str.casefold):
        if not _SAFE_LOG_FILE.fullmatch(name):
            continue
        path = log_dir / name
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({
            "name": name,
            "size": stat.st_size,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })

    if limit is not None and limit > 0:
        files = files[-limit:]
    return files


def _latest_log_file_name() -> str:
    files = list_log_files(limit=None)
    if not files:
        return ""
    return str(files[-1]["name"])


def _resolve_log_file(filename: str | None) -> Path | None:
    name = str(filename or "").strip() or _latest_log_file_name()
    if not name:
        return None
    if not _SAFE_LOG_FILE.fullmatch(name):
        raise ValueError("invalid log file name")

    log_dir = get_log_dir()
    path = (log_dir / name).resolve()
    try:
        path.relative_to(log_dir)
    except ValueError as exc:
        raise ValueError("invalid log file name") from exc
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _parse_log_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {
            "timestamp": "",
            "level": "INFO",
            "tag": "raw",
            "message": _truncate_text(line),
            "raw": _truncate_text(line),
        }
    if isinstance(payload, dict):
        return payload
    return {
        "timestamp": "",
        "level": "INFO",
        "tag": "raw",
        "message": _truncate_text(str(payload)),
        "raw": _truncate_text(line),
    }


def read_log_tail(filename: str | None = None, *, lines: int = 200) -> dict[str, Any]:
    line_count = _safe_lines(lines)
    path = _resolve_log_file(filename)
    if path is None:
        return {
            "file": "",
            "lines": line_count,
            "entries": [],
        }

    tail: deque[str] = deque(maxlen=line_count)
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if line:
                tail.append(line)

    return {
        "file": path.name,
        "lines": line_count,
        "entries": [_parse_log_line(line) for line in tail],
    }


__all__ = [
    "get_log_dir",
    "get_web_logger",
    "list_log_files",
    "log_event",
    "read_log_tail",
]
