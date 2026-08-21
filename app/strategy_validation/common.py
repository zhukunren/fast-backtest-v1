from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any


def ensure_dict(value: Any, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return {str(key): raw for key, raw in value.items()}


def slugify(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
    text = text.strip("._-")
    return text or "strategy"


def serialize_dataclass(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def get_nested_value(payload: Mapping[str, Any] | None, path: str) -> Any:
    if not isinstance(payload, Mapping):
        return None
    node: Any = payload
    for token in [part.strip() for part in str(path or "").split(".") if part.strip()]:
        if not isinstance(node, Mapping) or token not in node:
            return None
        node = node[token]
    return node


__all__ = [
    "ensure_dict",
    "get_nested_value",
    "serialize_dataclass",
    "slugify",
    "to_float",
]
