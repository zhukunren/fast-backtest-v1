"""Stable evaluation identities for Web library backtest results."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from backtest.result import CurvePayload, RunResult
from config import apply_config_overrides


EVALUATION_IDENTITY_SCHEMA_VERSION = 1
_SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "access_key",
        "api_key",
        "password",
        "secret",
        "secret_key",
        "token",
    }
)


def _canonical_identity_value(value: Any, *, key: str = "") -> Any:
    if key.casefold() in _SENSITIVE_CONFIG_KEYS:
        return "<set>" if value not in (None, "") else ""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(raw_key): _canonical_identity_value(raw_value, key=str(raw_key))
            for raw_key, raw_value in sorted(
                value.items(),
                key=lambda item: str(item[0]).casefold(),
            )
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical_identity_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        return items
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _stable_identity_hash(payload: Mapping[str, Any]) -> str:
    canonical = _canonical_identity_value(payload)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolved_execution_config(
    base_cfg: Any,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the full resolved semantic config without output paths or secrets."""
    try:
        resolved = apply_config_overrides(base_cfg, overrides)
        raw = asdict(resolved) if is_dataclass(resolved) else {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    raw.pop("paths", None)
    strategy = raw.get("strategy")
    if isinstance(strategy, dict):
        strategy.pop("module", None)
        if not strategy:
            raw.pop("strategy", None)
    canonical = _canonical_identity_value(raw)
    return canonical if isinstance(canonical, dict) else {}


def _normalize_identity_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        try:
            return str(value.strftime("%Y%m%d"))
        except Exception:
            pass
    text = str(value).strip()
    digits = "".join(character for character in text[:10] if character.isdigit())
    if len(digits) != 8:
        return ""
    try:
        datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return ""
    return digits


def _observed_data_cutoff(
    run_result: RunResult | None,
    effective_window: Mapping[str, Any],
) -> str:
    if isinstance(run_result, RunResult):
        payload = run_result.payload
        curve = payload.equity_curve if isinstance(payload, CurvePayload) else None
        if curve is not None and not curve.empty:
            for column in ("trade_date", "date"):
                if column not in curve.columns:
                    continue
                dates = [
                    normalized
                    for normalized in (
                        _normalize_identity_date(value) for value in curve[column].tolist()
                    )
                    if normalized
                ]
                if dates:
                    return max(dates)
        request_end = _normalize_identity_date(run_result.request.end_time)
        if request_end:
            return request_end
    return _normalize_identity_date(effective_window.get("end_time"))


def _build_evaluation_identity(
    *,
    name: str,
    module: str,
    cache_config: Mapping[str, Any],
    resolved_config: Mapping[str, Any],
    effective_window: Mapping[str, Any],
    date_overrides: Mapping[str, Any] | None,
    run_result: RunResult | None,
    meta: Mapping[str, Any],
) -> dict[str, Any]:
    configured_window = cache_config.get("configured_window")
    configured_window = (
        dict(configured_window) if isinstance(configured_window, Mapping) else {}
    )
    normalized_effective_window = {
        "start_time": _normalize_identity_date(effective_window.get("start_time")),
        "end_time": _normalize_identity_date(effective_window.get("end_time")),
    }
    date_source = str(cache_config.get("date_source") or "").strip()
    if date_source:
        run_kind = "ad_hoc" if date_source == "override" else "default"
    else:
        normalized_configured_window = {
            "start_time": _normalize_identity_date(configured_window.get("start_time")),
            "end_time": _normalize_identity_date(configured_window.get("end_time")),
        }
        run_kind = (
            "ad_hoc"
            if date_overrides is not None
            and normalized_effective_window != normalized_configured_window
            else "default"
        )
    data_cutoff = _observed_data_cutoff(run_result, normalized_effective_window)
    resolved_config_payload = _canonical_identity_value(resolved_config)
    if not isinstance(resolved_config_payload, dict):
        resolved_config_payload = {}
    resolved_config_hash = _stable_identity_hash(resolved_config_payload)

    context_material = {
        "schema": EVALUATION_IDENTITY_SCHEMA_VERSION,
        "effective_window": normalized_effective_window,
        "data_cutoff": data_cutoff,
        "resolved_config": resolved_config_payload,
    }
    context_id = _stable_identity_hash(context_material)

    source = cache_config.get("source")
    source = dict(source) if isinstance(source, Mapping) else {}
    provenance: dict[str, Any] = {}
    if isinstance(run_result, RunResult):
        provenance = {
            "strategy_source_hash": run_result.identity.strategy_source_hash,
            "config_hash": run_result.provenance.config_hash,
            "data_hash": run_result.provenance.data_hash,
            "code_hash": run_result.provenance.code_hash,
        }
    data_manifest = meta.get("data_manifest")
    data_manifest_hash = (
        _stable_identity_hash(data_manifest)
        if isinstance(data_manifest, Mapping)
        else ""
    )
    identity_material = {
        "schema": EVALUATION_IDENTITY_SCHEMA_VERSION,
        "context_id": context_id,
        "run_kind": run_kind,
        "strategy": {
            "name": name,
            "module": module,
            "source": source,
        },
        "provenance": provenance,
        "data_manifest_hash": data_manifest_hash,
    }
    evaluation_id = _stable_identity_hash(identity_material)
    return {
        "schema": EVALUATION_IDENTITY_SCHEMA_VERSION,
        "id": evaluation_id,
        "context_id": context_id,
        "run_kind": run_kind,
        "default_eligible": run_kind == "default",
        "configured_window": configured_window,
        "effective_window": normalized_effective_window,
        "data_cutoff": data_cutoff,
        "resolved_config_hash": resolved_config_hash,
        "resolved_config": resolved_config_payload,
        "strategy": identity_material["strategy"],
        "provenance": provenance,
        "data_manifest_hash": data_manifest_hash,
    }


def _attach_evaluation_identity(
    meta: dict[str, Any],
    *,
    name: str,
    module: str,
    cache_config: dict[str, Any],
    resolved_config: Mapping[str, Any],
    effective_window: Mapping[str, Any],
    date_overrides: Mapping[str, Any] | None,
    run_result: RunResult | None = None,
) -> dict[str, Any]:
    evaluation = _build_evaluation_identity(
        name=name,
        module=module,
        cache_config=cache_config,
        resolved_config=resolved_config,
        effective_window=effective_window,
        date_overrides=date_overrides,
        run_result=run_result,
        meta=meta,
    )
    meta["evaluation_identity"] = evaluation
    meta["evaluation_id"] = evaluation["id"]
    meta["evaluation_context_id"] = evaluation["context_id"]
    cache_config.update(
        {
            "evaluation_id": evaluation["id"],
            "evaluation_context_id": evaluation["context_id"],
            "run_kind": evaluation["run_kind"],
            "default_eligible": evaluation["default_eligible"],
            "resolved_config_hash": evaluation["resolved_config_hash"],
            "data_cutoff": evaluation["data_cutoff"],
        }
    )
    meta["cache_config"] = cache_config
    return meta
