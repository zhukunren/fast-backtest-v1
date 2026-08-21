"""Full, measured rebuild of Web strategy-cache generations."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_ROOT = Path(__file__).resolve().parents[1] / "strategy_library"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.artifact_schema import ARTIFACT_SCHEMA_VERSION, artifact_schema_fields
from web.cache_management import inspect_current_web_cache
from web import (
    library_cache,
    library_cache_publication,
    library_catalog,
    library_metadata,
    library_runtime,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _process_tree_rss_bytes(root: psutil.Process) -> int:
    processes = [root]
    try:
        processes.extend(root.children(recursive=True))
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    total = 0
    seen: set[int] = set()
    for process in processes:
        if process.pid in seen:
            continue
        seen.add(process.pid)
        try:
            total += int(process.memory_info().rss)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return total


class _PeakRssSampler:
    def __init__(self, interval_sec: float = 0.05) -> None:
        self._interval_sec = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss_bytes = 0

    def start(self) -> None:
        root = psutil.Process(os.getpid())

        def _sample() -> None:
            while not self._stop.is_set():
                self.peak_rss_bytes = max(
                    self.peak_rss_bytes,
                    _process_tree_rss_bytes(root),
                )
                self._stop.wait(self._interval_sec)
            self.peak_rss_bytes = max(
                self.peak_rss_bytes,
                _process_tree_rss_bytes(root),
            )

        self._thread = threading.Thread(target=_sample, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _strategy_names(library_root: Path, selected: list[str] | None) -> list[str]:
    root = Path(library_root)
    if selected:
        available = {
            path.name
            for path in root.iterdir()
            if path.is_dir() and (path / "config.ini").is_file()
        } if root.is_dir() else set()
        requested = {str(item).strip() for item in selected if str(item).strip()}
        unknown = sorted(requested - available, key=str.casefold)
        if unknown:
            raise ValueError(f"unknown strategies: {unknown}")
        return sorted(requested, key=str.casefold)
    if not root.is_dir():
        return []
    return sorted(
        [
            path.name
            for path in root.iterdir()
            if path.is_dir() and (path / "config.ini").is_file()
        ],
        key=str.casefold,
    )


def rebuild_cache_generations(
    library_root: Path,
    names: list[str] | None = None,
    *,
    clear_outputs: bool = True,
) -> dict[str, Any]:
    root = Path(library_root).resolve()
    selected = _strategy_names(root, names)
    if not selected:
        raise ValueError("no strategies selected for Web cache rebuild")

    started_at = _utc_now()
    started = time.perf_counter()
    sampler = _PeakRssSampler()
    previous_library_root = library_runtime.LIBRARY_ROOT
    previous_catalog_root = library_catalog.LIBRARY_ROOT
    previous_cache_root = library_cache.LIBRARY_ROOT
    previous_publication_root = library_cache_publication.LIBRARY_ROOT
    previous_metadata_root = library_metadata.LIBRARY_ROOT
    library_runtime.LIBRARY_ROOT = root
    library_catalog.LIBRARY_ROOT = root
    library_cache.LIBRARY_ROOT = root
    library_cache_publication.LIBRARY_ROOT = root
    library_metadata.LIBRARY_ROOT = root
    sampler.start()
    results: list[dict[str, Any]] = []
    try:
        for name in selected:
            strategy_dir = root / name
            before = inspect_current_web_cache(strategy_dir)
            item_started = time.perf_counter()
            result = library_runtime._run_single(name, clear_outputs=clear_outputs)
            elapsed = time.perf_counter() - item_started
            after = inspect_current_web_cache(strategy_dir)
            status = str(result.get("status") or "failed")
            results.append({
                "name": name,
                "status": status,
                "ok": status in {"completed", "no_signal"} and bool(after.get("ok")),
                "error": str(result.get("error") or after.get("error") or ""),
                "duration_sec": round(elapsed, 6),
                "previous_generation_id": str(before.get("generation_id") or ""),
                "generation_id": str(after.get("generation_id") or ""),
                "artifact_count": int(after.get("artifact_count") or 0),
                "size_bytes": int(after.get("size_bytes") or 0),
            })
    finally:
        sampler.stop()
        library_runtime.LIBRARY_ROOT = previous_library_root
        library_catalog.LIBRARY_ROOT = previous_catalog_root
        library_cache.LIBRARY_ROOT = previous_cache_root
        library_cache_publication.LIBRARY_ROOT = previous_publication_root
        library_metadata.LIBRARY_ROOT = previous_metadata_root

    elapsed_sec = time.perf_counter() - started
    succeeded = sum(1 for item in results if item["ok"])
    failed = len(results) - succeeded
    return {
        **artifact_schema_fields("web_cache_rebuild_report", ARTIFACT_SCHEMA_VERSION),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "library_root": str(root),
        "clear_outputs": bool(clear_outputs),
        "summary": {
            "strategy_count": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "elapsed_sec": round(elapsed_sec, 6),
            "peak_rss_bytes": int(sampler.peak_rss_bytes),
            "generation_size_bytes": sum(int(item["size_bytes"]) for item in results),
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全量重建 Web 策略库 schema-v2 cache generations")
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--name", dest="names", action="append")
    parser.add_argument(
        "--preserve-outputs",
        action="store_true",
        help="保留策略目录中已有输出文件。默认清理每个策略的 CSV/XLS 输出后再重建。",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        report = rebuild_cache_generations(
            args.library_root,
            args.names,
            clear_outputs=not args.preserve_outputs,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    report_path = args.report
    if report_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = ROOT / "output" / "tools" / "web_cache_rebuild" / f"{stamp}.json"
    _write_json_atomic(report_path, report)
    print(json.dumps({**report["summary"], "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0 if int(report["summary"]["failed"]) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
