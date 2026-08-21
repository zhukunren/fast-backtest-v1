"""Shared helpers for the official Tushare client."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any, TypeVar

from core.exceptions import DataSourceError

_ClientT = TypeVar("_ClientT")


def redact_tushare_error(error: object, *, token: object = "") -> str:
    """Remove credentials from SDK/vendor error text before logging it."""

    text = str(error)
    token_text = str(token or "").strip()
    if token_text:
        text = text.replace(token_text, "<redacted>")
    return re.sub(
        r"(?i)((?:传过来的是|token\s*[=:：])\s*)[A-Za-z0-9._-]+",
        r"\1<redacted>",
        text,
    )


def require_tushare_token(token: object, *, source: str = "TuShare") -> str:
    """Reject empty credentials before the SDK can fall back to ``~/tk.csv``."""

    value = str(token or "").strip()
    if not value:
        raise DataSourceError(
            f"{source} token 未配置；请通过配置或 TUSHARE_TOKEN/TS_TOKEN 显式提供，"
            "禁止读取用户目录下的 tk.csv。"
        )
    return value


def resolve_required_tushare_token(*, cfg: object | None = None, config_loader: Callable[[], object] | None = None) -> str:
    """Resolve an environment token first, then configured values, fail closed."""

    for env_name in ("TUSHARE_TOKEN", "TS_TOKEN"):
        env_token = str(os.environ.get(env_name) or "").strip()
        if env_token:
            return env_token

    target = cfg
    token = getattr(getattr(target, "tushare", None), "token", "") if target is not None else ""
    if not str(token or "").strip() and config_loader is not None:
        target = config_loader()
        token = getattr(getattr(target, "tushare", None), "token", "")
    return require_tushare_token(token)


def create_tushare_client(
    token: object,
    *,
    timeout: float | None = None,
) -> Any:
    """Create an official TuShare client with an explicit token."""

    token_text = require_tushare_token(token)
    try:
        import tushare as ts
    except Exception as exc:  # pragma: no cover - depends on optional SDK
        raise DataSourceError(f"tushare 未安装，无法创建 client: {exc}") from exc
    kwargs = {"timeout": float(timeout)} if timeout is not None else {}
    try:
        client = ts.pro_api(token_text, **kwargs)
    except Exception as exc:
        message = redact_tushare_error(exc, token=token_text)
        raise DataSourceError(f"TuShare client 初始化失败: {message}") from None
    return client


def create_tushare_client_from_factory(
    factory: Callable[..., _ClientT],
    token: object,
    **kwargs: object,
) -> _ClientT:
    """Create a client from an SDK factory without permitting implicit auth."""

    token_text = require_tushare_token(token)
    try:
        client = factory(token=token_text, **kwargs)
    except Exception as exc:
        message = redact_tushare_error(exc, token=token_text)
        raise DataSourceError(f"TuShare client 初始化失败: {message}") from None
    return client


__all__ = [
    "create_tushare_client",
    "create_tushare_client_from_factory",
    "redact_tushare_error",
    "require_tushare_token",
    "resolve_required_tushare_token",
]
