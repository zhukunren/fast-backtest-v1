"""Strategy library source and cache health diagnostics."""
from __future__ import annotations

from typing import Any

from web.library_cache import (
    _diagnose_library_cache,
    _read_cached_meta_raw,
    _resolve_library_cache_context,
    _snapshot_artifact_path,
)
from web.library_cache_identity import _build_strategy_version_summary
from web.library_metadata import (
    LIBRARY_ROOT,
    _library_strategy_source_issue,
    _parse_strategy_config,
    _resolve_library_strategy_file,
    _strategy_config_parse_error,
)

_LIBRARY_HEALTH_ERROR_ISSUES = frozenset(
    {
        "missing_config",
        "missing_strategy_module",
        "invalid_config",
        "external_module_reference",
        "strategy_file_not_found",
    }
)


def _library_health_status(issues: list[str]) -> str:
    if any(issue in _LIBRARY_HEALTH_ERROR_ISSUES for issue in issues):
        return "error"
    return "warning" if issues else "ok"


def get_library_health() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    if not LIBRARY_ROOT.is_dir():
        return {
            "overview": {
                "total": 0,
                "ok": 0,
                "warning": 0,
                "error": 0,
                "valid_cache": 0,
                "invalid_cache": 0,
                "failed_cache": 0,
                "missing_cache": 0,
            },
            "items": entries,
        }

    for directory in sorted(LIBRARY_ROOT.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir() or directory.name.startswith(("_", ".")):
            continue
        cfg_path = directory / "config.ini"
        issues: list[str] = []
        cfg = {}
        config_error = _strategy_config_parse_error(cfg_path) if cfg_path.is_file() else ""
        if not config_error and cfg_path.is_file():
            cfg = _parse_strategy_config(cfg_path)
        module = str(cfg.get("strategy.module") or "").strip()
        if not cfg_path.is_file():
            issues.append("missing_config")
        elif config_error:
            issues.append("invalid_config")
        else:
            source_issue = _library_strategy_source_issue(directory, module)
            if source_issue:
                issues.append(source_issue)

        snapshot, generation_diag = _resolve_library_cache_context(directory.name)
        diag = _diagnose_library_cache(
            directory.name,
            snapshot=snapshot,
            generation_diag=generation_diag,
        )
        cache_status = str(diag.get("cache_status") or "missing")
        cache_reason = str(diag.get("cache_reason") or "")
        if cache_status == "missing":
            issues.append("missing_cache")
        elif cache_status == "invalid":
            issues.append("invalid_cache")
        elif cache_status == "failed":
            issues.append("failed_cache")
        elif cache_status == "valid":
            for filename, issue in (
                ("net_value.json", "missing_net_value"),
                ("trades.json", "missing_trades"),
                ("daily_holdings.json", "missing_daily_holdings"),
            ):
                artifact_path = (
                    _snapshot_artifact_path(snapshot, filename)
                    if snapshot is not None
                    else None
                )
                if artifact_path is None or not artifact_path.is_file():
                    issues.append(issue)

        status = _library_health_status(issues)

        entries.append(
            {
                "name": directory.name,
                "status": status,
                "module": module,
                **_build_strategy_version_summary(
                    directory,
                    cfg,
                    source_path=_resolve_library_strategy_file(directory, module),
                    cached_meta=(
                        _read_cached_meta_raw(directory.name, snapshot=snapshot)
                        if snapshot is not None
                        else None
                    ),
                    cache_status=cache_status,
                ),
                "cache_status": cache_status,
                "cache_reason": cache_reason,
                "cache_generation_id": diag.get("cache_generation_id") or "",
                "cache_artifact_count": int(diag.get("cache_artifact_count") or 0),
                "cache_size_bytes": int(diag.get("cache_size_bytes") or 0),
                "issues": issues,
                "cache_actual_start_time": diag.get("cache_actual_start_time") or "",
                "cache_actual_end_time": diag.get("cache_actual_end_time") or "",
                "cache_date_source": diag.get("cache_date_source") or "",
            }
        )

    overview = {
        "total": len(entries),
        "ok": sum(1 for item in entries if item["status"] == "ok"),
        "warning": sum(1 for item in entries if item["status"] == "warning"),
        "error": sum(1 for item in entries if item["status"] == "error"),
        "valid_cache": sum(1 for item in entries if item["cache_status"] == "valid"),
        "invalid_cache": sum(1 for item in entries if item["cache_status"] == "invalid"),
        "failed_cache": sum(1 for item in entries if item["cache_status"] == "failed"),
        "missing_cache": sum(1 for item in entries if item["cache_status"] == "missing"),
    }
    return {"overview": overview, "items": entries}
