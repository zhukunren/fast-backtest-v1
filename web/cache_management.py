"""Health inspection and retention operations for Web cache generations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest.artifacts import ArtifactGenerationError
from backtest.artifacts.generation_pruning import (
    plan_generation_prune,
    prune_artifact_generations,
)
from web.cache_generation import (
    invalidate_web_cache_snapshot_cache,
    resolve_current_web_cache,
    resolve_latest_web_cache_attempt,
    web_cache_current_pointer_path,
    web_cache_output_root,
)


def inspect_current_web_cache(strategy_dir: Path) -> dict[str, Any]:
    pointer = web_cache_current_pointer_path(strategy_dir)
    latest: dict[str, Any] = {}
    latest_error = ""
    try:
        latest_attempt = resolve_latest_web_cache_attempt(strategy_dir)
        if latest_attempt is not None:
            _, latest = latest_attempt
    except ArtifactGenerationError as exc:
        latest_error = str(exc)
    latest_fields = {
        "latest_attempt_generation_id": str(latest.get("generation_id") or ""),
        "latest_attempt_status": str(latest.get("status") or ""),
        "latest_attempt_error": str(latest.get("error") or latest_error),
        "latest_attempt_activated": bool(latest.get("activated")),
        "latest_attempt_evaluation_id": str(latest.get("evaluation_id") or ""),
        "latest_attempt_evaluation_context_id": str(
            latest.get("evaluation_context_id") or ""
        ),
        "latest_attempt_run_kind": str(latest.get("run_kind") or ""),
    }
    try:
        snapshot = resolve_current_web_cache(strategy_dir, force_validate=True)
    except ArtifactGenerationError as exc:
        return {
            "ok": False,
            "has_pointer": pointer.is_file(),
            "generation_id": "",
            "cache_dir": "",
            "artifact_count": 0,
            "size_bytes": 0,
            "error": str(exc),
            **latest_fields,
        }
    summary = snapshot.manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    return {
        "ok": True,
        "has_pointer": True,
        "generation_id": snapshot.generation_id,
        "cache_dir": str(snapshot.cache_dir),
        "artifact_count": int(summary.get("artifact_count") or 0),
        "size_bytes": int(summary.get("size_bytes") or 0),
        "error": "",
        **latest_fields,
    }


def plan_web_cache_prune(
    strategy_dir: Path,
    *,
    keep_last: int = 3,
    pinned_generation_ids: tuple[str, ...] = (),
    stale_staging_seconds: float = 24 * 60 * 60,
    now: float | None = None,
) -> dict[str, Any]:
    report = plan_generation_prune(
        web_cache_output_root(strategy_dir),
        keep_last=keep_last,
        pinned_generation_ids=pinned_generation_ids,
        stale_staging_seconds=stale_staging_seconds,
        now=now,
    )
    report["strategy_dir"] = str(Path(strategy_dir).resolve())
    return report


def prune_web_cache_generations(
    strategy_dir: Path,
    *,
    keep_last: int = 3,
    pinned_generation_ids: tuple[str, ...] = (),
    stale_staging_seconds: float = 24 * 60 * 60,
    apply: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    report = prune_artifact_generations(
        web_cache_output_root(strategy_dir),
        keep_last=keep_last,
        pinned_generation_ids=pinned_generation_ids,
        stale_staging_seconds=stale_staging_seconds,
        apply=apply,
        now=now,
    )
    report["strategy_dir"] = str(Path(strategy_dir).resolve())
    if apply:
        invalidate_web_cache_snapshot_cache(strategy_dir)
    return report


__all__ = [
    "inspect_current_web_cache",
    "plan_web_cache_prune",
    "prune_web_cache_generations",
]
