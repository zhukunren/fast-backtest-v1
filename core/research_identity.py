from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(
                value.items(),
                key=lambda item: str(item[0]).casefold(),
            )
        }
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=lambda item: repr(item).casefold())]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def stable_hash(payload: Any) -> str:
    text = json.dumps(
        _json_safe(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    resolved = Path(path).resolve()
    sha = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_strategy_source(module: str, *, repo_root: Path) -> Path | None:
    raw = str(module or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.suffix.casefold() == ".py" or "/" in raw or "\\" in raw:
        resolved = path if path.is_absolute() else repo_root / path
        resolved = resolved.resolve()
        return resolved if resolved.is_file() else None
    candidate = (repo_root / Path(*raw.split("."))).with_suffix(".py").resolve()
    if candidate.is_file():
        return candidate
    try:
        spec = importlib.util.find_spec(raw)
    except (ImportError, AttributeError, ValueError):
        return None
    origin = getattr(spec, "origin", None)
    if not origin or origin in {"built-in", "frozen"}:
        return None
    resolved = Path(str(origin)).resolve()
    return resolved if resolved.is_file() else None


def strategy_source_identity(module: str, *, repo_root: Path) -> dict[str, Any]:
    source_path = resolve_strategy_source(module, repo_root=repo_root)
    resolved_root = Path(repo_root).resolve()
    if source_path is None:
        return {
            "module": str(module or "").strip(),
            "path": "",
            "provenance_path": "",
            "exists": False,
            "sha256": "",
            "size_bytes": 0,
        }
    stat = source_path.stat()
    try:
        stable_path = source_path.relative_to(resolved_root).as_posix()
    except ValueError:
        # Keep external source locations as provenance only.  The module name
        # and source hash remain the stable identity for those sources.
        stable_path = f"<external>/{source_path.name}"
    return {
        "module": str(module or "").strip(),
        "path": stable_path,
        "provenance_path": str(source_path),
        "exists": True,
        "sha256": sha256_file(source_path),
        "size_bytes": int(stat.st_size),
    }


__all__ = [
    "resolve_strategy_source",
    "sha256_file",
    "stable_hash",
    "strategy_source_identity",
]
