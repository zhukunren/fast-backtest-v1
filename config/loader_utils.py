"""
Shared helpers for config section loaders.
"""
from __future__ import annotations

import configparser
import os

from .parsing import parse_bool, parse_float, parse_int, pick_env


def _apply_bounds(value, *, minimum=None, maximum=None):
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def pick_raw(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str | None = None,
    fallback: str,
) -> str:
    raw = parser.get(section, option, fallback=fallback)
    return pick_env(env, raw) if env else raw


def pick_env_aliases(*names: str, fallback: str) -> str:
    for name in names:
        env_name = str(name or "").strip()
        if not env_name:
            continue
        if env_name in os.environ:
            return os.environ.get(env_name, "")
    return fallback


def pick_text(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str | None = None,
    fallback: str,
    default: str | None = None,
    lower: bool = False,
) -> str:
    value = str(pick_raw(parser, section, option, env=env, fallback=fallback)).strip()
    if lower:
        value = value.lower()
    if default is not None and not value:
        return default
    return value


def split_csv_items(raw: str | None) -> tuple[str, ...]:
    return tuple(item for item in (str(part).strip() for part in str(raw or "").split(",")) if item)


def pick_bool(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str | None = None,
    fallback: str,
    default: bool,
) -> bool:
    return parse_bool(pick_raw(parser, section, option, env=env, fallback=fallback), default)


def pick_int(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str | None = None,
    fallback: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = parse_int(pick_raw(parser, section, option, env=env, fallback=fallback), default)
    return int(_apply_bounds(value, minimum=minimum, maximum=maximum))


def pick_float(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str | None = None,
    fallback: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = parse_float(pick_raw(parser, section, option, env=env, fallback=fallback), default)
    return float(_apply_bounds(value, minimum=minimum, maximum=maximum))


__all__ = [
    "pick_bool",
    "pick_env_aliases",
    "pick_float",
    "pick_int",
    "pick_raw",
    "pick_text",
    "split_csv_items",
]
