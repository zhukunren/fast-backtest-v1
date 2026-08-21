"""Persist protocol JSON and describe the completed artifact set."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.artifact_schema import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    artifact_schema_fields,
)

from ..runtime import RuntimeContext
from .compiled_plan import _json_safe


_HASH_MAX_BYTES = 50 * 1024 * 1024
_ROWS_MAX_BYTES = 50 * 1024 * 1024
_REQUIRED_MANIFEST_ARTIFACTS = frozenset(
    {
        "audit_research",
        "compiled_plan",
        "robustness",
        "run_metadata",
    }
)


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        _json_safe(payload),
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def _hash_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > _HASH_MAX_BYTES:
            return None
    except OSError:
        return None
    sha = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
    except OSError:
        return None
    return sha.hexdigest()


def _csv_rows(path: Path) -> int | None:
    try:
        if path.suffix.casefold() != ".csv" or path.stat().st_size > _ROWS_MAX_BYTES:
            return None
    except OSError:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            line_count = sum(1 for _ in handle)
    except OSError:
        return None
    return max(line_count - 1, 0)


def _file_entry(
    name: str,
    path: Path,
    *,
    required: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(name),
        "path": str(path),
        "exists": False,
        "required": bool(required),
    }
    try:
        stat = path.stat()
    except OSError:
        return entry
    entry.update(
        {
            "exists": True,
            "size_bytes": int(stat.st_size),
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(
                timespec="seconds"
            ),
            "sha256": _hash_file(path),
        }
    )
    rows = _csv_rows(path)
    if rows is not None:
        entry["rows"] = rows
    return entry


def _known_artifact_paths(
    ctx: RuntimeContext,
    paths: dict[str, Path],
) -> dict[str, Path]:
    signal_dir = Path(ctx.cfg.paths.signal_data)
    output_root = Path(ctx.output_root)
    net_value_dir = Path(ctx.cfg.paths.net_value_unique_date)
    known = {
        "run_metadata": Path(ctx.run_meta_dir) / f"{ctx.run_id}.json",
        "backtest_summary": output_root / "backtest_summary.csv",
        "market_state": output_root / "market_state.json",
        "merged_signals": signal_dir / "merged_signals.csv",
        "merged_signals_candidates": signal_dir / "merged_signals_candidates.csv",
        "signal_input_report": signal_dir / "signal_input_report.csv",
        "signal_daily_summary": signal_dir / "signal_daily_summary.csv",
        "topn_trade_segments": signal_dir / "topn_trade_segments.csv",
        "topn_trade_segments_summary": signal_dir
        / "topn_trade_segments_summary.csv",
        "net_value": net_value_dir / "merged_net_value_with_sum.csv",
        "trade_segments_summary": net_value_dir / "trade_segments_summary.csv",
        "portfolio_orders": net_value_dir / "portfolio_orders.csv",
        "portfolio_fills": net_value_dir / "portfolio_fills.csv",
        "risk_control_summary": net_value_dir / "risk_control_summary.csv",
        "risk_control_events": net_value_dir / "risk_control_events.csv",
        "portfolio_daily_state": net_value_dir / "portfolio_daily_state.csv",
    }
    if getattr(ctx, "strategy_spec", None) is not None:
        known["strategy_spec"] = signal_dir / "strategy_spec.json"
    known.update(paths)
    return known


def write_artifact_manifest(
    ctx: RuntimeContext,
    *,
    paths: dict[str, Path],
) -> Path:
    manifest_path = paths["artifact_manifest"]
    known = {
        name: path
        for name, path in _known_artifact_paths(ctx, paths).items()
        if name != "artifact_manifest"
    }
    entries = [
        _file_entry(
            name,
            path,
            required=name in _REQUIRED_MANIFEST_ARTIFACTS,
        )
        for name, path in sorted(
            known.items(),
            key=lambda item: str(item[0]).casefold(),
        )
    ]
    missing_required = [
        entry["name"]
        for entry in entries
        if bool(entry.get("required")) and not bool(entry.get("exists"))
    ]
    missing_optional = [
        entry["name"]
        for entry in entries
        if not bool(entry.get("required")) and not bool(entry.get("exists"))
    ]
    payload = {
        **artifact_schema_fields(
            "artifact_manifest",
            ARTIFACT_MANIFEST_SCHEMA_VERSION,
        ),
        "run_id": ctx.run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_root": str(ctx.output_root),
        "required_artifacts": sorted(
            _REQUIRED_MANIFEST_ARTIFACTS,
            key=str.casefold,
        ),
        "summary": {
            "total": len(entries),
            "existing": sum(1 for entry in entries if bool(entry.get("exists"))),
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        },
        "artifacts": entries,
    }
    return _write_json(manifest_path, payload)


__all__ = ["write_artifact_manifest"]
