"""Validated development and production launch policy for the Web application."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from web.task_coordinator import resolve_task_state_path, task_timing_from_environment

DEFAULT_WEB_THREADS = 4
MIN_WEB_THREADS = 1
MAX_WEB_THREADS = 64


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _parse_port(value: object) -> int:
    try:
        port = int(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("WEB_PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("WEB_PORT must be an integer between 1 and 65535")
    return port


def _enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true"}


def _parse_web_threads(value: object) -> int:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return DEFAULT_WEB_THREADS
    try:
        threads = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("WEB_THREADS must be an integer between 1 and 64") from exc
    if not MIN_WEB_THREADS <= threads <= MAX_WEB_THREADS:
        raise RuntimeError("WEB_THREADS must be an integer between 1 and 64")
    return threads


def _normalize_origin(value: object) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        scheme = parsed.scheme.casefold()
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.casefold(), None if port in {None, default_port} else port


def _render_origin(origin: tuple[str, str, int | None]) -> str:
    scheme, host, port = origin
    rendered_host = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port is not None else ""
    return f"{scheme}://{rendered_host}{suffix}"


def _validated_public_origin(env: Mapping[str, str], *, mode: str) -> str:
    raw_origin = str(env.get("WEB_PUBLIC_ORIGIN") or "").strip()
    if not raw_origin:
        return ""
    origin = _normalize_origin(raw_origin)
    if origin is None:
        raise RuntimeError("WEB_PUBLIC_ORIGIN must be an absolute HTTP(S) origin without a path")
    if mode == "production" and origin[0] != "https" and not _is_loopback_host(origin[1]):
        raise RuntimeError("production WEB_PUBLIC_ORIGIN must use HTTPS unless it is loopback")
    return _render_origin(origin)


def serve_web_app(
    app: Any,
    *,
    event_logger: Callable[..., None],
    environ: Mapping[str, str] | None = None,
) -> None:
    env = os.environ if environ is None else environ
    host = str(env.get("WEB_HOST") or "127.0.0.1").strip()
    port = _parse_port(env.get("WEB_PORT", "5000"))
    debug = _enabled(env.get("WEB_DEBUG", "0"))
    mode = str(env.get("WEB_SERVE_MODE") or "development").strip().casefold()
    if mode not in {"development", "production"}:
        raise RuntimeError("WEB_SERVE_MODE must be 'development' or 'production'")
    web_threads = _parse_web_threads(env.get("WEB_THREADS"))
    lease_timeout_sec, heartbeat_sec = task_timing_from_environment(env)
    task_state_path = resolve_task_state_path(env)
    if mode == "development" and not _is_loopback_host(host):
        raise RuntimeError(
            "the Flask development server only supports a loopback WEB_HOST; "
            "use WEB_SERVE_MODE=production behind a trusted TLS proxy"
        )
    if mode == "production" and debug:
        raise RuntimeError("WEB_DEBUG must be disabled in production mode")
    if mode == "production" and len(str(env.get("WEB_ADMIN_TOKEN") or "")) < 32:
        raise RuntimeError("production mode requires WEB_ADMIN_TOKEN with at least 32 characters")
    if mode == "production" and str(env.get("WEB_VUE_DEV_SERVER") or "").strip():
        raise RuntimeError("WEB_VUE_DEV_SERVER is not allowed in production mode")
    public_origin = _validated_public_origin(env, mode=mode)

    print(f"Strategy Library: http://{host}:{port}")
    event_logger(
        "INFO",
        "server",
        "Strategy Library server starting",
        host=host,
        port=port,
        debug=debug,
        serve_mode=mode,
        public_origin=public_origin,
        task_state_db=str(task_state_path),
        task_lease_timeout_sec=lease_timeout_sec,
        task_heartbeat_sec=heartbeat_sec,
        web_threads=web_threads,
    )
    if mode == "development":
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=False,
            threaded=True,
        )
        return

    from waitress import serve

    serve(
        app,
        host=host,
        port=port,
        threads=web_threads,
        clear_untrusted_proxy_headers=True,
    )


__all__ = ["serve_web_app"]
