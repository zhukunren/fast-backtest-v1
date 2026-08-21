"""Schema and payload validation for Web cache generations."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from backtest.artifacts import ArtifactGenerationError, GenerationSnapshot
from core.artifact_schema import ARTIFACT_SCHEMA_VERSION


def read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArtifactGenerationError(
            f"cannot read Web cache artifact JSON: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ArtifactGenerationError(f"Web cache artifact root must be an object: {path}")
    return payload


def meta_status(payload: Mapping[str, Any]) -> str:
    extra = payload.get("extra")
    extra_status = extra.get("status") if isinstance(extra, Mapping) else None
    return str(extra_status or payload.get("status") or "").strip()


def _require_list(payload: Mapping[str, Any], key: str, *, filename: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ArtifactGenerationError(f"Web cache {filename} requires list field: {key}")
    return value


def _require_object(
    payload: Mapping[str, Any],
    key: str,
    *,
    filename: str,
) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ArtifactGenerationError(f"Web cache {filename} requires object field: {key}")
    return value


def _normalize_cache_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _validate_numeric_series(
    values: list[Any],
    *,
    filename: str,
    key: str,
    expected_length: int,
    strictly_positive: bool = False,
) -> None:
    if len(values) != expected_length:
        raise ArtifactGenerationError(
            f"Web cache {filename} field length mismatch: {key}"
        )
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ArtifactGenerationError(
                f"Web cache {filename} field contains a non-numeric value: "
                f"{key}={value!r}"
            ) from exc
        if not math.isfinite(number):
            raise ArtifactGenerationError(
                f"Web cache {filename} field contains a non-finite value: "
                f"{key}={value!r}"
            )
        if strictly_positive and number <= 0:
            raise ArtifactGenerationError(
                f"Web cache {filename} field must be positive: {key}={value!r}"
            )


def _validate_net_value_payload(payload: Mapping[str, Any]) -> None:
    filename = "net_value.json"
    dates = _require_list(payload, "dates", filename=filename)
    values = _require_list(payload, "net_value", filename=filename)
    if not dates or len(dates) != len(values):
        raise ArtifactGenerationError(
            "Web cache net_value dates and values must be non-empty and aligned"
        )
    normalized_dates = [_normalize_cache_date(value) for value in dates]
    if any(value is None for value in normalized_dates):
        raise ArtifactGenerationError("Web cache net_value contains an invalid date")
    if any(
        not isinstance(raw, str) or raw != normalized
        for raw, normalized in zip(dates, normalized_dates, strict=True)
    ):
        raise ArtifactGenerationError(
            "Web cache net_value dates must use canonical YYYY-MM-DD format"
        )
    if normalized_dates != sorted(set(normalized_dates)):
        raise ArtifactGenerationError(
            "Web cache net_value dates must be unique and strictly increasing"
        )
    _validate_numeric_series(
        values,
        filename=filename,
        key="net_value",
        expected_length=len(dates),
        strictly_positive=True,
    )
    for key in (
        "drawdown",
        "utilization",
        "benchmark_net_value",
        "excess_net_value",
    ):
        if key in payload:
            _validate_numeric_series(
                _require_list(payload, key, filename=filename),
                filename=filename,
                key=key,
                expected_length=len(dates),
            )
    portfolios = payload.get("portfolios")
    if portfolios is not None:
        if not isinstance(portfolios, Mapping):
            raise ArtifactGenerationError("Web cache net_value portfolios must be an object")
        for key, raw_series in portfolios.items():
            if not isinstance(raw_series, list):
                raise ArtifactGenerationError(
                    f"Web cache net_value portfolio must be a list: {key}"
                )
            _validate_numeric_series(
                raw_series,
                filename=filename,
                key=f"portfolios.{key}",
                expected_length=len(dates),
                strictly_positive=True,
            )


def _validate_payload_contract(payload: Mapping[str, Any], spec: Any) -> None:
    filename = str(spec.filename)
    if filename == "meta.json":
        status = meta_status(payload)
        if status not in {"completed", "no_signal", "failed", "config_failed"}:
            raise ArtifactGenerationError(f"Web cache meta has invalid status: {status!r}")
        return
    if filename == "net_value.json":
        _validate_net_value_payload(payload)
        return
    if filename == "daily_holdings.json":
        _require_list(payload, "dates", filename=filename)
        for key in ("holdings_by_date", "summary_by_date"):
            _require_object(payload, key, filename=filename)
        return
    if filename in {"positions.json", "position_changes.json"}:
        _require_list(payload, "records", filename=filename)
        return
    if filename == "trades.json":
        body_fields = {
            "records": list,
            "execution_records": list,
            "order_records": list,
            "diagnostics": Mapping,
            "execution_meta": Mapping,
            "order_meta": Mapping,
            "fills_ledger": Mapping,
        }
        if not any(key in payload for key in body_fields):
            raise ArtifactGenerationError(
                "Web cache trades requires at least one supported data field"
            )
        for key in ("records", "execution_records", "order_records"):
            if key in payload and not isinstance(payload.get(key), list):
                raise ArtifactGenerationError(
                    f"Web cache {filename} field must be a list: {key}"
                )
        for key in ("diagnostics", "execution_meta", "order_meta", "fills_ledger"):
            if key in payload and not isinstance(payload.get(key), Mapping):
                raise ArtifactGenerationError(
                    f"Web cache {filename} field must be an object: {key}"
                )
        return
    if filename == "market_state.json":
        _require_object(payload, "states", filename=filename)
        _require_list(payload, "observations", filename=filename)


def validate_payload(path: Path, spec: Any) -> dict[str, Any]:
    payload = read_payload(path)
    if payload.get("artifact_type") != spec.artifact_type:
        raise ArtifactGenerationError(
            f"Web cache artifact_type mismatch for {spec.filename}: "
            f"{payload.get('artifact_type')!r}"
        )
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactGenerationError(
            f"Web cache schema version mismatch for {spec.filename}: "
            f"{payload.get('schema_version')!r}"
        )
    _validate_payload_contract(payload, spec)
    return payload


def manifest_entries(snapshot: GenerationSnapshot) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for raw in snapshot.manifest.get("artifacts", []):
        if not isinstance(raw, dict):
            raise ArtifactGenerationError(
                "Web cache manifest contains a non-object artifact entry"
            )
        relative_path = str(raw.get("relative_path") or "")
        if relative_path in entries:
            raise ArtifactGenerationError(
                f"Web cache manifest contains duplicate path: {relative_path}"
            )
        entries[relative_path] = raw
    return entries


__all__ = [
    "manifest_entries",
    "meta_status",
    "read_payload",
    "validate_payload",
]
