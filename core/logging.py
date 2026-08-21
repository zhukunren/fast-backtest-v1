from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from .runtime_flags import get_tag_log_path, is_quiet_mode


_RUN_LOGGER_OWNED = "_jshs_run_logger_owned"
_RUN_LOGGER_LOCK = RLock()


class _RunFileHandler(logging.FileHandler):
    """File handler owned by one framework run logger."""


class RunLoggerFacade:
    """Small facade that routes plain, tagged and JSONL event logs through one object."""

    def __init__(self, logger: Any = None, *, event_logger: Any = None) -> None:
        self.logger = logger
        self.event_logger = event_logger

    def info(self, msg: str) -> None:
        log_info(msg, self.logger)

    def warning(self, msg: str) -> None:
        log_warn(msg, self.logger)

    def error(self, msg: str) -> None:
        log_error(msg, self.logger)

    def event(self, event: str, **fields: Any) -> None:
        emit = getattr(self.event_logger, "emit", None)
        if callable(emit):
            emit(event, **fields)

    def span(self, stage: str, **fields: Any):
        span = getattr(self.event_logger, "span", None)
        if callable(span):
            return span(stage, **fields)
        from contextlib import nullcontext

        return nullcontext()


def create_run_logger_facade(logger: Any = None, *, event_logger: Any = None) -> RunLoggerFacade:
    return RunLoggerFacade(logger, event_logger=event_logger)


def get_run_logger(run_id: str, log_dir: Path, *, name: str = "backtest") -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    with _RUN_LOGGER_LOCK:
        logger = logging.getLogger(name).getChild(run_id)
        if logger.handlers:
            return logger

        logger.setLevel(logging.INFO)
        logger.propagate = False

        log_path = log_dir / f"{name}_{run_id}.log"
        file_handler = _RunFileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        setattr(logger, _RUN_LOGGER_OWNED, True)

        return logger


def close_run_logger(logger: logging.Logger | None) -> None:
    """Release handlers owned by ``get_run_logger`` without touching external handlers."""
    if logger is None or not bool(getattr(logger, _RUN_LOGGER_OWNED, False)):
        return

    with _RUN_LOGGER_LOCK:
        for handler in tuple(logger.handlers):
            if not isinstance(handler, _RunFileHandler):
                continue
            try:
                logger.removeHandler(handler)
            except Exception:
                pass
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass

        if logger.handlers:
            return
        try:
            registry = logger.manager.loggerDict
            if registry.get(logger.name) is logger:
                registry.pop(logger.name, None)
        except Exception:
            pass


def close_run_logger_safely(logger: logging.Logger | None) -> None:
    """Best-effort owner cleanup that never replaces a run result or exception."""
    if logger is None:
        return
    try:
        close_run_logger(logger)
    except Exception:
        pass


def log_kv(logger: logging.Logger, event: str, **fields) -> None:
    parts = [f"{k}={v}" for k, v in fields.items()]
    msg = f"{event} " + " ".join(parts) if parts else event
    logger.info(msg)


def _format_timestamp() -> str:
    now = datetime.now()
    base = now.strftime("%Y-%m-%d %H:%M:%S")
    ms = int(now.microsecond / 1000)
    return f"{base},{ms:03d}"


def _infer_level(tag: str) -> str:
    if tag in ("[WARN]", "[CALENDAR_GAP]"):
        return "WARNING"
    return "INFO"


def _extract_tag(msg: str) -> str:
    stripped = msg.lstrip()
    if not stripped.startswith("["):
        return ""
    end = stripped.find("]")
    if end <= 1:
        return ""
    return stripped[: end + 1]


def tag_print(msg: str) -> None:
    """
    Print tag messages to the tag log when TAG_LOG_PATH is set; otherwise fallback to print.
    """
    log_path = get_tag_log_path()
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            tag = _extract_tag(msg)
            level = _infer_level(tag)
            line = f"{_format_timestamp()} {level} print_tag {msg}\n"
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except Exception:
            pass
    print(msg)


def _maybe_tag(msg: str, tag: str) -> str:
    stripped = msg.lstrip()
    if stripped.startswith("["):
        return msg
    return f"{tag} {msg}"


def log_info(msg: str, logger: Any = None) -> None:
    quiet = is_quiet_mode()
    tagged = msg.lstrip().startswith("[")
    tag_log_path = get_tag_log_path()
    if logger is not None and (not tagged or not tag_log_path):
        try:
            logger.info(msg)
        except Exception:
            pass
    if tagged:
        if quiet and not tag_log_path:
            return
        tag_print(msg)
        return
    if quiet:
        return
    print(msg)


def log_warn(msg: str, logger: Any = None) -> None:
    tagged_msg = _maybe_tag(msg, "[WARN]")
    tag_log_path = get_tag_log_path()
    if logger is not None and not tag_log_path:
        try:
            logger.warning(tagged_msg)
        except Exception:
            pass
    tag_print(tagged_msg)


def log_error(msg: str, logger: Any = None) -> None:
    tagged_msg = _maybe_tag(msg, "[ERROR]")
    tag_log_path = get_tag_log_path()
    if logger is not None and not tag_log_path:
        try:
            logger.error(tagged_msg)
        except Exception:
            pass
    tag_print(tagged_msg)


def log_metric(name: str, logger: Any = None, **kwargs) -> None:
    items = " ".join(f"{k}={v}" for k, v in kwargs.items())
    msg = f"[METRIC] {name}"
    if items:
        msg = f"{msg} {items}"
    log_info(msg, logger)


def log_stage(stage: str, status: str, *, logger: Any = None, duration_ms: float | None = None) -> None:
    msg = f"[STAGE] {stage} {status}"
    if duration_ms is not None:
        msg = f"{msg} ({duration_ms:.0f}ms)"
    log_info(msg, logger)


__all__ = [
    "RunLoggerFacade",
    "close_run_logger",
    "close_run_logger_safely",
    "create_run_logger_facade",
    "get_run_logger",
    "log_error",
    "log_info",
    "log_kv",
    "log_metric",
    "log_stage",
    "log_warn",
    "tag_print",
]
