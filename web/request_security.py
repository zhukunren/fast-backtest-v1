"""Request authorization shared by Web maintenance and sensitive runtime APIs."""
from __future__ import annotations

import hmac
import ipaddress
import os
import re
from urllib.parse import urlsplit

from flask import Response, g, jsonify, request


SENSITIVE_API_ENDPOINTS = frozenset({
    "api_backtest_run",
    "api_backtest_status",
    "api_library_clear_cache",
    "api_library_config_ini",
    "api_logs",
    "api_logs_client",
    "api_logs_tail",
    "api_run_events",
    "api_run_events_detail",
})
_SENSITIVE_CONFIG_TOKENS = frozenset({
    "access_key",
    "api_key",
    "apikey",
    "credential",
    "dsn",
    "password",
    "passwd",
    "private_key",
    "pwd",
    "secret",
    "token",
})


def authorize_sensitive_request():
    """Authorize a local-only or token-authenticated sensitive request."""
    if request.method == "OPTIONS":
        return None

    origin_error = _validate_browser_origin()
    if origin_error is not None:
        return origin_error

    configured_token = str(os.environ.get("WEB_ADMIN_TOKEN") or "")
    if configured_token:
        supplied_token = str(request.headers.get("X-Web-Admin-Token") or "")
        if hmac.compare_digest(supplied_token, configured_token):
            return None
        return jsonify({"error": "admin_auth_required"}), 401

    if not _is_loopback_bind_host():
        return jsonify({
            "error": "sensitive_auth_not_configured",
            "message": "WEB_HOST 对外绑定时必须配置 WEB_ADMIN_TOKEN",
        }), 503
    if _is_loopback_request() and _is_loopback_request_host():
        return None
    return jsonify({
        "error": "sensitive_local_only",
        "message": "远程敏感请求需要配置 WEB_ADMIN_TOKEN",
    }), 403


def apply_security_response_headers(response: Response) -> Response:
    """Prevent UI redressing and caching of authenticated operational data."""
    response.headers["Content-Security-Policy"] = _content_security_policy()
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.blueprint == "admin_api" or request.endpoint in SENSITIVE_API_ENDPOINTS:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


def redact_ini_secrets(text: str) -> str:
    """Redact sensitive INI assignments, including indented continuation values."""
    lines: list[str] = []
    assignment = re.compile(r"^(\s*)([^#;][^=:]*?)(\s*[=:]\s*)(.*)$")
    sensitive_indent: int | None = None
    for line in str(text).splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.strip()
        indentation = body[:len(body) - len(body.lstrip(" \t"))]
        indent_width = len(indentation.expandtabs(8))
        if sensitive_indent is not None:
            if not stripped or stripped.startswith(("#", ";")):
                lines.append(line)
                continue
            if indent_width > sensitive_indent:
                lines.append(f"{indentation}<redacted>{ending}")
                continue
            sensitive_indent = None
        match = assignment.match(body)
        if match is None:
            lines.append(line)
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(2).strip().lower()).strip("_")
        bounded_key = f"_{key}_"
        if any(f"_{token}_" in bounded_key for token in _SENSITIVE_CONFIG_TOKENS):
            lines.append(f"{match.group(1)}{match.group(2)}{match.group(3)}<redacted>{ending}")
            sensitive_indent = indent_width
        else:
            lines.append(line)
    return "".join(lines)


def _is_loopback_request() -> bool:
    try:
        return ipaddress.ip_address(str(request.remote_addr or "").strip()).is_loopback
    except ValueError:
        return False


def _is_loopback_bind_host() -> bool:
    host = str(os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    return _is_loopback_host(host.strip("[]").lower())


def _is_loopback_request_host() -> bool:
    request_origin = _normalize_origin(request.host_url)
    return request_origin is not None and _is_loopback_host(request_origin[1])


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_browser_origin():
    origin = str(request.headers.get("Origin") or "").strip()
    if origin:
        normalized_origin = _normalize_origin(origin)
        allowed_origins = _allowed_browser_origins()
        if normalized_origin is not None and normalized_origin in allowed_origins:
            return None
        return jsonify({"error": "cross_site_request_rejected"}), 403

    fetch_site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site == "cross-site":
        return jsonify({"error": "cross_site_request_rejected"}), 403
    return None


def _allowed_browser_origins() -> set[tuple[str, str, int | None]]:
    origins: set[tuple[str, str, int | None]] = set()
    request_origin = _normalize_origin(request.host_url)
    if request_origin is not None and _is_loopback_host(request_origin[1]):
        origins.add(request_origin)
    public_origin = _normalize_origin(str(os.environ.get("WEB_PUBLIC_ORIGIN") or "").strip())
    if public_origin is not None and _origin_allowed_in_current_mode(public_origin):
        origins.add(public_origin)
    raw_origins = str(os.environ.get("WEB_CORS_ORIGINS") or "")
    for item in raw_origins.split(","):
        normalized = _normalize_origin(item.strip())
        if normalized is not None and _origin_allowed_in_current_mode(normalized):
            origins.add(normalized)
    return origins


def _origin_allowed_in_current_mode(origin: tuple[str, str, int | None]) -> bool:
    mode = str(os.environ.get("WEB_SERVE_MODE") or "development").strip().casefold()
    return mode != "production" or origin[0] == "https" or _is_loopback_host(origin[1])


def _origin_source(origin: tuple[str, str, int | None]) -> str:
    scheme, host, port = origin
    rendered_host = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port is not None else ""
    return f"{scheme}://{rendered_host}{suffix}"


def _content_security_policy() -> str:
    script_sources = ["'self'"]
    connect_sources = ["'self'"]
    image_sources = ["'self'", "data:", "blob:"]
    font_sources = ["'self'", "data:"]
    frontend_mode = str(getattr(g, "web_frontend_mode", "") or "")
    if frontend_mode == "dev":
        dev_origin = _normalize_origin(str(os.environ.get("WEB_VUE_DEV_SERVER") or "").strip())
        if dev_origin is not None:
            dev_source = _origin_source(dev_origin)
            websocket_scheme = "wss" if dev_origin[0] == "https" else "ws"
            websocket_source = _origin_source((websocket_scheme, dev_origin[1], dev_origin[2]))
            script_sources.append(dev_source)
            connect_sources.extend((dev_source, websocket_source))
            image_sources.append(dev_source)
            font_sources.append(dev_source)
    directives = (
        "default-src 'self'",
        "base-uri 'self'",
        f"connect-src {' '.join(connect_sources)}",
        f"font-src {' '.join(font_sources)}",
        "form-action 'self'",
        "frame-ancestors 'none'",
        f"img-src {' '.join(image_sources)}",
        "object-src 'none'",
        f"script-src {' '.join(script_sources)}",
        "style-src 'self' 'unsafe-inline'",
        "worker-src 'self' blob:",
    )
    return "; ".join(directives)


def _normalize_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        scheme = parsed.scheme.lower()
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.lower(), None if port in {None, default_port} else port


__all__ = [
    "SENSITIVE_API_ENDPOINTS",
    "apply_security_response_headers",
    "authorize_sensitive_request",
    "redact_ini_secrets",
]
