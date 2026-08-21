"""Helpers for building schema-v2 Web cache fixtures in tests."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from core.artifact_schema import ARTIFACT_SCHEMA_VERSION, artifact_schema_fields
from web.cache_generation import WEB_CACHE_ARTIFACT_SPECS, publish_web_cache_generation
from web.library_cache_identity import _build_cache_config_signature
from web.library_evaluation_identity import _attach_evaluation_identity
from web.library_metadata import (
    _parse_strategy_config,
    _resolve_library_strategy_file,
)


def _normalized_payload(filename: str, payload: Any, generation_id: str) -> dict[str, Any]:
    spec = WEB_CACHE_ARTIFACT_SPECS[filename]
    if filename == "positions.json" and isinstance(payload, list):
        payload = {"records": payload}
    if filename == "position_changes.json" and isinstance(payload, list):
        payload = {"records": payload}
    if not isinstance(payload, dict):
        raise TypeError(f"fixture payload must be an object: {filename}")
    normalized = {
        **payload,
        **artifact_schema_fields(spec.artifact_type, ARTIFACT_SCHEMA_VERSION),
    }
    if filename == "meta.json":
        normalized.setdefault("run_id", generation_id)
        extra = normalized.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            normalized["extra"] = extra
        extra.setdefault("status", "completed")
    return normalized


def publish_web_cache_fixture(
    strategy_dir: Path,
    *,
    flat_cache_dir: Path | None = None,
    generation_id: str = "fixture-run",
    with_cache_config: bool = True,
) -> Path:
    """Publish legacy-shaped fixture JSON through the production generation writer."""
    strategy_dir = Path(strategy_dir)
    source_dir = Path(flat_cache_dir) if flat_cache_dir is not None else strategy_dir / "cache"
    with tempfile.TemporaryDirectory(prefix=".web-cache-fixture-", dir=str(strategy_dir)) as temp_dir:
        materialized = Path(temp_dir)
        for filename in sorted(WEB_CACHE_ARTIFACT_SPECS, key=str.casefold):
            source_path = source_dir / filename
            if not source_path.is_file():
                continue
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            normalized = _normalized_payload(filename, payload, generation_id)
            if filename == "meta.json" and with_cache_config:
                config_path = strategy_dir / "config.ini"
                strat_cfg = _parse_strategy_config(config_path) if config_path.is_file() else {}
                normalized.setdefault(
                    "strategy_module",
                    str(strat_cfg.get("strategy.module") or "").strip(),
                )
                source_path = _resolve_library_strategy_file(
                    strategy_dir,
                    strat_cfg.get("strategy.module", ""),
                )
                normalized["cache_config"] = _build_cache_config_signature(
                    strat_cfg,
                    effective_window={
                        "start_time": strat_cfg.get("backtest.start_time", ""),
                        "end_time": strat_cfg.get("backtest.end_time", ""),
                    },
                    strategy_dir=strategy_dir,
                    source_path=source_path,
                )
            (materialized / filename).write_text(
                json.dumps(normalized, ensure_ascii=False),
                encoding="utf-8",
            )
        if not (materialized / "meta.json").is_file():
            meta = _normalized_payload("meta.json", {}, generation_id)
            if with_cache_config:
                config_path = strategy_dir / "config.ini"
                strat_cfg = _parse_strategy_config(config_path) if config_path.is_file() else {}
                meta["strategy_module"] = str(
                    strat_cfg.get("strategy.module") or ""
                ).strip()
                source_path = _resolve_library_strategy_file(
                    strategy_dir,
                    strat_cfg.get("strategy.module", ""),
                )
                meta["cache_config"] = _build_cache_config_signature(
                    strat_cfg,
                    effective_window={
                        "start_time": strat_cfg.get("backtest.start_time", ""),
                        "end_time": strat_cfg.get("backtest.end_time", ""),
                    },
                    strategy_dir=strategy_dir,
                    source_path=source_path,
                )
            (materialized / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False),
                encoding="utf-8",
            )
        if not (materialized / "net_value.json").is_file():
            meta_path = materialized / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            extra = meta.get("extra") if isinstance(meta, dict) else None
            if isinstance(extra, dict) and extra.get("status") == "completed":
                extra["status"] = "no_signal"
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if with_cache_config:
            meta_path = materialized / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cache_config = meta.get("cache_config")
            if isinstance(cache_config, dict):
                config_path = strategy_dir / "config.ini"
                strat_cfg = _parse_strategy_config(config_path) if config_path.is_file() else {}
                effective_window = cache_config.get("effective_window")
                if not isinstance(effective_window, dict):
                    effective_window = {
                        "start_time": strat_cfg.get("backtest.start_time", ""),
                        "end_time": strat_cfg.get("backtest.end_time", ""),
                    }
                _attach_evaluation_identity(
                    meta,
                    name=strategy_dir.name,
                    module=str(meta.get("strategy_module") or strat_cfg.get("strategy.module") or ""),
                    cache_config=cache_config,
                    resolved_config={},
                    effective_window=effective_window,
                    date_overrides=None,
                )
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        snapshot = publish_web_cache_generation(strategy_dir, generation_id, materialized)
    return snapshot.cache_dir


__all__ = ["publish_web_cache_fixture"]
