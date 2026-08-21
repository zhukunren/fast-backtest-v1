from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = WEB_ROOT / "strategy_library"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web import library_cache
from web.cache_generation import clear_web_cache_generations, web_cache_output_root


@dataclass(slots=True)
class CacheCleanResult:
    name: str
    cache_dir: Path
    cache_status: str
    cache_reason: str
    removed: bool


def _sync_library_root() -> None:
    library_cache.LIBRARY_ROOT = LIBRARY_ROOT


def _resolve_strategy_names(selected: list[str] | None) -> list[str]:
    _sync_library_root()
    if selected:
        return sorted({str(name).strip() for name in selected if str(name).strip()}, key=str.casefold)
    if not LIBRARY_ROOT.is_dir():
        return []
    return sorted(
        [
            path.name
            for path in LIBRARY_ROOT.iterdir()
            if path.is_dir() and not path.name.startswith(("_", "."))
        ],
        key=str.casefold,
    )


def _safe_cache_dir(name: str) -> Path:
    strategy_dir = (LIBRARY_ROOT / name).resolve()
    cache_dir = web_cache_output_root(strategy_dir).resolve()
    library_root = LIBRARY_ROOT.resolve()
    cache_dir.relative_to(library_root)
    if cache_dir.name != "web_cache" or cache_dir.parent != strategy_dir:
        raise ValueError(f"拒绝删除非 Web cache generation 目录: {cache_dir}")
    return cache_dir


def inspect_cache(name: str) -> CacheCleanResult | None:
    _sync_library_root()
    cache_dir = _safe_cache_dir(name)
    if not cache_dir.is_dir():
        return None
    diag: dict[str, Any] = library_cache.diagnose_library_cache(name)
    return CacheCleanResult(
        name=name,
        cache_dir=cache_dir,
        cache_status=str(diag.get("cache_status") or ""),
        cache_reason=str(diag.get("cache_reason") or ""),
        removed=False,
    )


def clean_invalid_library_caches(
    names: list[str] | None = None,
    *,
    dry_run: bool = True,
) -> list[CacheCleanResult]:
    results: list[CacheCleanResult] = []
    for name in _resolve_strategy_names(names):
        result = inspect_cache(name)
        if result is None or result.cache_status != "invalid":
            continue
        if not dry_run:
            result.removed = clear_web_cache_generations(LIBRARY_ROOT / name)
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 web 策略库中与当前配置不兼容的 cache 目录")
    parser.add_argument(
        "--name",
        dest="names",
        action="append",
        help="指定策略目录名；可重复传入多次。默认扫描全部策略。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际删除无效 cache。默认仅 dry-run 预览。",
    )
    args = parser.parse_args(argv)

    results = clean_invalid_library_caches(args.names, dry_run=not args.apply)
    if not results:
        print("未发现无效缓存。")
        return 0

    action = "REMOVED" if args.apply else "DRY-RUN"
    for item in results:
        print(f"[{action}] {item.name}: {item.cache_reason or item.cache_status} -> {item.cache_dir}")
    if not args.apply:
        print("")
        print("如需删除，请追加 --apply。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
