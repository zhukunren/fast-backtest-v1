from __future__ import annotations

from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .loader_utils import split_csv_items
from .models import AppConfig


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_override_value(
    cfg: AppConfig,
    *,
    field_name: str,
    current: Any,
    value: Any,
) -> Any:
    if value is None:
        return None
    if isinstance(current, bool):
        return _parse_bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return _coerce_path_override_value(cfg, field_name=field_name, value=value)
    if isinstance(current, tuple):
        return _coerce_tuple_override_value(value)
    if isinstance(current, str):
        return str(value)
    return value


def _coerce_path_override_value(cfg: AppConfig, *, field_name: str, value: Any) -> Path:
    path = value if isinstance(value, Path) else Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    base_dir = cfg.paths.repo_root if field_name == "repo_root" else cfg.paths.data_root
    return (base_dir / path).resolve()


def _coerce_tuple_override_value(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return split_csv_items(value)
    return tuple(str(item).strip() for item in value if str(item).strip())


def _group_raw_overrides(cfg: AppConfig, overrides: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for dotted_key, raw_value in overrides.items():
        key = str(dotted_key or "").strip()
        if "." not in key:
            raise KeyError(f"invalid config override key: {dotted_key!r}")
        section_name, field_name = key.split(".", 1)
        section = getattr(cfg, section_name, None)
        if not is_dataclass(section):
            raise KeyError(f"unknown config section: {section_name!r}")
        if not hasattr(section, field_name):
            raise KeyError(f"unknown config field: {key!r}")
        grouped.setdefault(section_name, {})[field_name] = raw_value
    return grouped


def _apply_section_overrides(cfg: AppConfig, section_name: str, raw_updates: Mapping[str, Any]):
    section = getattr(cfg, section_name)
    current_cfg = cfg
    current_section = section
    ordered_fields = tuple(section.__dataclass_fields__)
    for field_name in ordered_fields:
        if field_name not in raw_updates:
            continue
        current_value = getattr(current_section, field_name)
        coerced_value = _coerce_override_value(
            current_cfg,
            field_name=field_name,
            current=current_value,
            value=raw_updates[field_name],
        )
        current_section = replace(current_section, **{field_name: coerced_value})
        current_cfg = replace(current_cfg, **{section_name: current_section})
    return current_section


def apply_config_overrides(cfg: AppConfig, overrides: Mapping[str, Any] | None = None) -> AppConfig:
    if not overrides:
        return cfg

    result = cfg
    for section_name, raw_updates in _group_raw_overrides(cfg, overrides).items():
        updated_section = _apply_section_overrides(result, section_name, raw_updates)
        result = replace(result, **{section_name: updated_section})
    return result


__all__ = ["apply_config_overrides"]
