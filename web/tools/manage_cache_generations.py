"""Inspect and activate Web strategy-cache generations."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.cache_generation import (
    activate_web_cache_generation,
    list_web_cache_generations,
    resolve_current_web_cache,
    resolve_web_cache_generation,
)
from web.cache_management import inspect_current_web_cache, prune_web_cache_generations


DEFAULT_LIBRARY_ROOT = Path(__file__).resolve().parents[1] / "strategy_library"
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")


def _strategy_dir(library_root: Path, name: str) -> Path:
    clean_name = str(name or "").strip()
    if not _SAFE_NAME.fullmatch(clean_name):
        raise ValueError(f"invalid strategy name: {name!r}")
    root = Path(library_root).resolve()
    strategy_dir = (root / clean_name).resolve()
    strategy_dir.relative_to(root)
    if strategy_dir.parent != root:
        raise ValueError(f"strategy path escapes library root: {name!r}")
    return strategy_dir


def _strategy_names(library_root: Path, selected: list[str] | None = None) -> list[str]:
    if selected:
        names = {str(item).strip() for item in selected if str(item).strip()}
        return sorted(names, key=str.casefold)
    root = Path(library_root)
    if not root.is_dir():
        return []
    return sorted(
        [
            path.name
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith((".", "_"))
        ],
        key=str.casefold,
    )


def list_cache_generations(
    library_root: Path,
    names: list[str] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for name in _strategy_names(library_root, names):
        strategy_dir = _strategy_dir(library_root, name)
        inspection = inspect_current_web_cache(strategy_dir)
        items.append({
            "name": name,
            **inspection,
            "ready_generations": list(list_web_cache_generations(strategy_dir)),
        })
    return {
        "library_root": str(Path(library_root).resolve()),
        "summary": {
            "strategy_count": len(items),
            "valid_current": sum(1 for item in items if item["ok"]),
            "invalid_current": sum(
                1 for item in items if item["has_pointer"] and not item["ok"]
            ),
            "missing_current": sum(1 for item in items if not item["has_pointer"]),
            "size_bytes": sum(int(item["size_bytes"]) for item in items if item["ok"]),
        },
        "items": items,
    }


def inspect_cache_generation(
    library_root: Path,
    name: str,
    generation_id: str | None = None,
) -> dict[str, Any]:
    strategy_dir = _strategy_dir(library_root, name)
    snapshot = (
        resolve_web_cache_generation(strategy_dir, generation_id)
        if generation_id
        else resolve_current_web_cache(strategy_dir)
    )
    return {
        "name": name,
        "generation_id": snapshot.generation_id,
        "cache_dir": str(snapshot.cache_dir),
        "summary": snapshot.manifest.get("summary", {}),
        "artifacts": snapshot.manifest.get("artifacts", []),
    }


def activate_cache_generation(
    library_root: Path,
    name: str,
    generation_id: str,
) -> dict[str, Any]:
    strategy_dir = _strategy_dir(library_root, name)
    snapshot = activate_web_cache_generation(strategy_dir, generation_id)
    verified = resolve_current_web_cache(strategy_dir)
    return {
        "name": name,
        "activated_generation_id": snapshot.generation_id,
        "current_generation_id": verified.generation_id,
        "verified": verified.generation_id == snapshot.generation_id,
    }


def prune_cache_generations(
    library_root: Path,
    names: list[str] | None = None,
    *,
    keep_last: int = 3,
    pinned_generation_ids: tuple[str, ...] = (),
    stale_staging_seconds: float = 24 * 60 * 60,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan or apply protected pruning for selected Web strategy caches."""

    items: list[dict[str, Any]] = []
    for name in _strategy_names(library_root, names):
        report = prune_web_cache_generations(
            _strategy_dir(library_root, name),
            keep_last=keep_last,
            pinned_generation_ids=pinned_generation_ids,
            stale_staging_seconds=stale_staging_seconds,
            apply=apply,
        )
        items.append({"name": name, **report})
    return {
        "library_root": str(Path(library_root).resolve()),
        "dry_run": not apply,
        "items": items,
        "summary": {
            "strategy_count": len(items),
            "candidate_count": sum(
                int(item["summary"].get("candidate_count") or 0) for item in items
            ),
            "removed_count": sum(
                int(item["summary"].get("removed_count") or 0) for item in items
            ),
            "removed_size_bytes": sum(
                int(item["summary"].get("removed_size_bytes") or 0) for item in items
            ),
        },
    }


def _write_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理 Web 策略库 schema-v2 cache generations")
    parser.add_argument(
        "--library-root",
        type=Path,
        default=DEFAULT_LIBRARY_ROOT,
        help="策略库根目录。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出 current 与 READY generations。")
    list_parser.add_argument("--name", dest="names", action="append")

    inspect_parser = subparsers.add_parser("inspect", help="校验 current 或指定 generation。")
    inspect_parser.add_argument("name")
    inspect_parser.add_argument("--generation")

    activate_parser = subparsers.add_parser("activate", help="原子激活已验证 generation。")
    activate_parser.add_argument("name")
    activate_parser.add_argument("generation_id")

    prune_parser = subparsers.add_parser(
        "prune",
        help="按 current/PIN/keep-last/staging 保护规则清理 generations；默认 dry-run。",
    )
    prune_parser.add_argument("--name", dest="names", action="append")
    prune_parser.add_argument("--keep-last", type=int, default=3)
    prune_parser.add_argument("--pin", dest="pinned_generation_ids", action="append", default=[])
    prune_parser.add_argument("--stale-staging-hours", type=float, default=24.0)
    prune_parser.add_argument("--apply", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            _write_json(list_cache_generations(args.library_root, args.names))
        elif args.command == "inspect":
            _write_json(inspect_cache_generation(args.library_root, args.name, args.generation))
        elif args.command == "activate":
            _write_json(
                activate_cache_generation(
                    args.library_root,
                    args.name,
                    args.generation_id,
                )
            )
        elif args.command == "prune":
            _write_json(
                prune_cache_generations(
                    args.library_root,
                    args.names,
                    keep_last=args.keep_last,
                    pinned_generation_ids=tuple(args.pinned_generation_ids),
                    stale_staging_seconds=max(0.0, args.stale_staging_hours * 3600.0),
                    apply=bool(args.apply),
                )
            )
    except Exception as exc:
        _write_json({"ok": False, "error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
