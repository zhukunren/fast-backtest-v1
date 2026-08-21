"""Current-generation cache access and diagnostics for the Web library."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backtest.artifacts import ArtifactGenerationError
from web.cache_generation import (
    WebCacheSnapshot,
    resolve_current_web_cache,
    web_cache_current_pointer_path,
    web_cache_latest_attempt_pointer_path,
)
from web.library_cache_identity import (
    _cache_signature_error,
    _cache_signature_matches_config,
    _cached_meta_matches_module,
    _cached_meta_window,
)
from web.library_metadata import (
    LIBRARY_ROOT,
    _parse_strategy_config,
    _strategy_config_parse_error,
)


def _resolve_library_cache_context(
    name: str,
) -> tuple[WebCacheSnapshot | None, dict[str, Any]]:
    strategy_dir = LIBRARY_ROOT / name
    try:
        snapshot = resolve_current_web_cache(strategy_dir)
    except ArtifactGenerationError as exc:
        return None, {
            "ok": False,
            "has_pointer": web_cache_current_pointer_path(strategy_dir).is_file(),
            "generation_id": "",
            "cache_dir": "",
            "artifact_count": 0,
            "size_bytes": 0,
            "error": str(exc),
        }
    summary = snapshot.manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    return snapshot, {
        "ok": True,
        "has_pointer": True,
        "generation_id": snapshot.generation_id,
        "cache_dir": str(snapshot.cache_dir),
        "artifact_count": int(summary.get("artifact_count") or 0),
        "size_bytes": int(summary.get("size_bytes") or 0),
        "error": "",
    }


def _resolve_library_cache_snapshot(name: str) -> WebCacheSnapshot | None:
    snapshot, _ = _resolve_library_cache_context(name)
    return snapshot


def _read_latest_attempt(name: str) -> dict[str, Any]:
    """Read the atomic attempt sidecar without revalidating cache artifacts."""
    path = web_cache_latest_attempt_pointer_path(LIBRARY_ROOT / name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("artifact_type") != "web_cache_latest_attempt":
        return {}
    return payload


def _snapshot_artifact_path(
    snapshot: WebCacheSnapshot,
    filename: str,
) -> Path | None:
    try:
        return snapshot.artifact_path(filename)
    except (ArtifactGenerationError, KeyError):
        return None


def _cached_status(cached_meta: dict[str, Any] | None) -> str:
    if not isinstance(cached_meta, dict):
        return ""
    extra = cached_meta.get("extra")
    if isinstance(extra, dict):
        status = str(extra.get("status") or "").strip()
        if status:
            return status
    return str(cached_meta.get("status") or "").strip()


def _cached_error(cached_meta: dict[str, Any] | None) -> str:
    if not isinstance(cached_meta, dict):
        return ""
    for key in ("error", "reason", "message"):
        text = str(cached_meta.get(key) or "").strip()
        if text:
            return text
    extra = cached_meta.get("extra")
    if isinstance(extra, dict):
        for key in ("error", "reason", "message"):
            text = str(extra.get(key) or "").strip()
            if text:
                return text
    return ""


def _read_cached_meta_raw(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    p = _snapshot_artifact_path(snapshot, "meta.json")
    if p is None or not p.is_file():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def _read_cached_meta(
    name: str,
    *,
    expected_module: Any = "",
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    meta = _read_cached_meta_raw(name, snapshot=snapshot)
    if meta is None:
        return None
    if not _cached_meta_matches_module(meta, expected_module):
        return None
    return meta


def _diagnose_library_cache(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None,
    generation_diag: dict[str, Any],
) -> dict[str, Any]:
    directory = LIBRARY_ROOT / name
    cfg_path = directory / "config.ini"
    base: dict[str, Any] = {
        "name": name,
        "cache_status": "missing",
        "cache_reason": "",
        "has_cache_dir": bool(generation_diag.get("ok")),
        "cache_generation_id": str(generation_diag.get("generation_id") or ""),
        "cache_artifact_count": int(generation_diag.get("artifact_count") or 0),
        "cache_size_bytes": int(generation_diag.get("size_bytes") or 0),
        "cache_actual_start_time": "",
        "cache_actual_end_time": "",
        "cache_date_source": "",
        "evaluation_id": "",
        "evaluation_context_id": "",
        "evaluation_run_kind": "",
        "latest_attempt_generation_id": "",
        "latest_attempt_status": "",
        "latest_attempt_error": "",
        "latest_attempt_activated": False,
    }
    latest = _read_latest_attempt(name)
    if latest:
        base.update(
            {
                "latest_attempt_generation_id": str(latest.get("generation_id") or ""),
                "latest_attempt_status": str(latest.get("status") or ""),
                "latest_attempt_error": str(latest.get("error") or ""),
                "latest_attempt_activated": bool(latest.get("activated")),
                "latest_attempt_evaluation_id": str(latest.get("evaluation_id") or ""),
                "latest_attempt_evaluation_context_id": str(
                    latest.get("evaluation_context_id") or ""
                ),
                "latest_attempt_run_kind": str(latest.get("run_kind") or ""),
            }
        )
    if not bool(generation_diag.get("ok")):
        if bool(generation_diag.get("has_pointer")):
            base.update({
                "cache_status": "invalid",
                "cache_reason": str(generation_diag.get("error") or "Web cache generation 校验失败"),
            })
        else:
            base["cache_reason"] = "缺少 schema-v2 Web cache current generation"
        return base
    if not cfg_path.is_file():
        base.update({
            "cache_status": "invalid",
            "cache_reason": "缺少 config.ini",
        })
        return base

    config_error = _strategy_config_parse_error(cfg_path)
    if config_error:
        base.update({
            "cache_status": "invalid",
            "cache_reason": f"config.ini 解析失败：{config_error}",
        })
        return base

    strat_cfg = _parse_strategy_config(cfg_path)
    cached_meta = _read_cached_meta_raw(name, snapshot=snapshot)
    window = _cached_meta_window(cached_meta)
    base["cache_actual_start_time"] = window["start_time"]
    base["cache_actual_end_time"] = window["end_time"]
    if isinstance(cached_meta, dict):
        evaluation = cached_meta.get("evaluation_identity")
        if isinstance(evaluation, dict):
            base["evaluation_id"] = str(evaluation.get("id") or cached_meta.get("evaluation_id") or "")
            base["evaluation_context_id"] = str(
                evaluation.get("context_id") or cached_meta.get("evaluation_context_id") or ""
            )
            base["evaluation_run_kind"] = str(evaluation.get("run_kind") or "")
        signature = cached_meta.get("cache_config")
        if isinstance(signature, dict):
            base["cache_date_source"] = str(signature.get("date_source") or "")
    if cached_meta is None:
        base.update({
            "cache_status": "invalid",
            "cache_reason": "缺少 meta.json",
        })
        return base

    if not _cache_signature_matches_config(cached_meta, strat_cfg, directory=directory):
        base.update({
            "cache_status": "invalid",
            "cache_reason": _cache_signature_error(cached_meta, strat_cfg, directory=directory),
        })
        return base

    status = _cached_status(cached_meta)
    if status in {"failed", "config_failed"}:
        base.update({
            "cache_status": "failed",
            "cache_reason": _cached_error(cached_meta),
        })
        return base

    base["cache_status"] = "valid"
    return base


def diagnose_library_cache(name: str) -> dict[str, Any]:
    snapshot, generation_diag = _resolve_library_cache_context(name)
    return _diagnose_library_cache(
        name,
        snapshot=snapshot,
        generation_diag=generation_diag,
    )


def _cache_current_for_config(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> bool:
    directory = LIBRARY_ROOT / name
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return False
    cfg_path = directory / "config.ini"
    if not cfg_path.is_file():
        return False
    strat_cfg = _parse_strategy_config(cfg_path)
    expected_module = strat_cfg.get("strategy.module", "")
    cached_meta = _read_cached_meta(
        name,
        expected_module=expected_module,
        snapshot=snapshot,
    )
    if cached_meta is None:
        return False
    if not _cache_signature_matches_config(cached_meta, strat_cfg, directory=directory):
        return False
    return _cached_status(cached_meta) not in {"failed", "config_failed"}
