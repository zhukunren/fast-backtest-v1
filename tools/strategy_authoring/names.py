from __future__ import annotations

import re

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_strategy_name(name: str, *, default: str = "strategy") -> str:
    raw = str(name or "").strip()
    if not raw:
        return default
    cleaned = raw.replace("\\", "_").replace("/", "_")
    cleaned = _SAFE_NAME_RE.sub("_", cleaned).strip("._-")
    if not cleaned or cleaned in {".", ".."}:
        return default
    return cleaned[:80]


__all__ = ["sanitize_strategy_name"]
