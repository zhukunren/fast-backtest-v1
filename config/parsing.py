"""
Primitive parsing helpers for app config.
"""
from __future__ import annotations

import os
from pathlib import Path


def _strip_text(value: object) -> str:
    return str(value).strip()


def _parse_number(value: str | None, default: int | float, *, kind: str, cast) -> int | float:
    if value is None:
        return default
    text = _strip_text(value)
    if text == "":
        raise ValueError(f"无法解析为 {kind}: 空字符串")
    try:
        return cast(text)
    except Exception:
        raise ValueError(f"无法解析为 {kind}: {value!r}") from None


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    s = _strip_text(value).lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"无法解析为 bool: {value!r} (支持 1/0, true/false, yes/no, on/off)")


def parse_int(value: str | None, default: int) -> int:
    return int(_parse_number(value, default, kind="int", cast=int))


def parse_float(value: str | None, default: float) -> float:
    return float(_parse_number(value, default, kind="float", cast=float))


def pick_env(name: str, fallback: str) -> str:
    if name in os.environ:
        return os.environ.get(name, "")
    return fallback


def resolve_path(base: Path, raw: str) -> Path:
    raw_text = _strip_text(raw)
    if not raw_text:
        return base
    path = Path(raw_text)
    if path.is_absolute():
        return path
    return (base / path).resolve()


__all__ = [
    "parse_bool",
    "parse_float",
    "parse_int",
    "pick_env",
    "resolve_path",
]
