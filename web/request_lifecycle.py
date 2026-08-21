from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from flask_cors import CORS


def configure_cors(app, raw_origins: object) -> None:
    origins = [item.strip() for item in str(raw_origins or "").split(",") if item.strip()]
    if origins:
        CORS(app, resources={r"/api/*": {"origins": origins}})


def log_api_error_response(response, *, request, event_logger: Callable[..., Any]):
    if not request.path.startswith("/api/") or response.status_code < 400:
        return response
    start = request.environ.get("web.request_start")
    duration_ms = None
    if isinstance(start, (int, float)):
        duration_ms = round((perf_counter() - float(start)) * 1000, 2)
    event_logger(
        "ERROR" if response.status_code >= 500 else "WARNING",
        "api",
        "API request returned an error",
        method=request.method,
        path=request.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


def log_unhandled_request_exception(
    exc: BaseException | None,
    *,
    request,
    event_logger: Callable[..., Any],
) -> None:
    if exc is None:
        return
    event_logger(
        "ERROR",
        "api_exception" if request.path.startswith("/api/") else "web_exception",
        "Unhandled request exception",
        method=request.method,
        path=request.path,
        error=str(exc),
        exception_type=type(exc).__name__,
    )


__all__ = [
    "configure_cors",
    "log_api_error_response",
    "log_unhandled_request_exception",
]
