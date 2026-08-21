"""Materialize and publish immutable Web library cache generations."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from backtest.reporting.market_state import (
    compute_market_state_from_net_value_payload,
    summarize_market_state_payload,
)
from backtest.result import CurvePayload, RunResult
from core.artifact_schema import (
    MARKET_STATE_SCHEMA_VERSION,
    RUN_METADATA_SCHEMA_VERSION,
    artifact_schema_fields,
)
from web.cache_generation import publish_web_cache_generation
from web.exporter import (
    build_daily_holdings_payload,
    build_net_value_payload,
    build_position_changes_payload,
    build_positions_cache_payload,
    build_trades_payload,
)
from web.library_catalog import invalidate_library_catalog_cache
from web.library_curves import _build_benchmark_curves
from web.library_metadata import LIBRARY_ROOT, _local_source_file_name
from web.log_service import log_event


def _write_json_atomic(path: Path, payload: Any, *, default: Any | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, default=default)
    tmp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_cache_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_json_or_remove(
    path: Path,
    payload: Any,
    *,
    default: Any | None = None,
) -> None:
    if payload is None:
        _remove_cache_file(path)
        return
    if isinstance(payload, (list, dict)) and not payload:
        _remove_cache_file(path)
        return
    _write_json_atomic(path, payload, default=default)


def _export_cache(
    run_result: RunResult,
    meta: dict[str, Any],
    cache_dir: Path,
    *,
    strategy_name: str = "",
) -> None:
    """Export one run result into a materialized cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Build the validated net-value artifact once and derive the sparkline from
    # that same data. Reading the raw DataFrame here used to turn NaN values
    # into fake 1.0 points (or serialize Infinity) even when the net-value
    # artifact itself was rejected as invalid.
    nv_data = build_net_value_payload(run_result)
    sparkline = None
    if isinstance(nv_data, dict):
        raw_values = nv_data.get("net_value")
        if isinstance(raw_values, list):
            try:
                values = [float(value) for value in raw_values]
            except (TypeError, ValueError):
                values = []
            if values and all(math.isfinite(value) and value > 0 for value in values):
                arr = [round(value, 6) for value in values]
                step = max(1, len(arr) // 60)
                sparkline = arr[::step]
    meta = {
        **meta,
        **artifact_schema_fields("run_metadata", RUN_METADATA_SCHEMA_VERSION),
    }
    meta["sparkline"] = sparkline
    _write_json_atomic(cache_dir / "meta.json", meta, default=str)

    _write_json_or_remove(cache_dir / "net_value.json", nv_data)

    market_state_nv_data = dict(nv_data) if isinstance(nv_data, dict) else None
    if market_state_nv_data is not None:
        benchmark_data = _build_benchmark_curves(market_state_nv_data)
        if benchmark_data:
            market_state_nv_data.update(benchmark_data)
    market_state = (
        compute_market_state_from_net_value_payload(
            market_state_nv_data,
            name=strategy_name or cache_dir.parent.name,
        )
        if isinstance(market_state_nv_data, dict)
        else None
    )
    if isinstance(market_state, dict):
        market_state = {
            **market_state,
            **artifact_schema_fields("market_state", MARKET_STATE_SCHEMA_VERSION),
        }
    _write_json_or_remove(cache_dir / "market_state.json", market_state)
    print(
        summarize_market_state_payload(
            market_state,
            name=strategy_name or cache_dir.parent.name,
        )
    )

    trades_data = build_trades_payload(run_result)
    _write_json_or_remove(cache_dir / "trades.json", trades_data)

    initial_asset_hint = (
        meta.get("config", {}).get("portfolio", {}).get("initial_capital")
        if isinstance(meta, dict)
        else None
    )
    daily_holdings = build_daily_holdings_payload(
        run_result,
        initial_asset_hint=initial_asset_hint,
    )
    _write_json_or_remove(cache_dir / "daily_holdings.json", daily_holdings)

    positions_data = build_positions_cache_payload(run_result)
    _write_json_or_remove(cache_dir / "positions.json", positions_data)
    position_changes = build_position_changes_payload(run_result)
    _write_json_or_remove(cache_dir / "position_changes.json", position_changes)


def _cache_generation_id(
    meta: dict[str, Any],
    run_result: RunResult | None = None,
) -> str:
    run_id = str(meta.get("run_id") or "").strip()
    if not run_id and isinstance(run_result, RunResult):
        run_id = str(run_result.identity.run_id or "").strip()
    if run_id:
        return run_id
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"web_{stamp}_{uuid4().hex[:8]}"


def _publish_cache(
    run_result: RunResult,
    meta: dict[str, Any],
    strategy_dir: Path,
) -> None:
    strategy_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".web-cache-",
        dir=str(strategy_dir),
    ) as temp_dir:
        materialized = Path(temp_dir)
        _export_cache(
            run_result,
            meta,
            materialized,
            strategy_name=strategy_dir.name,
        )
        evaluation = meta.get("evaluation_identity")
        run_kind = (
            str(evaluation.get("run_kind") or "")
            if isinstance(evaluation, dict)
            else ""
        )
        publish_web_cache_generation(
            strategy_dir,
            _cache_generation_id(meta, run_result),
            materialized,
            activate=run_kind != "ad_hoc",
        )
        invalidate_library_catalog_cache()


def _persist_library_outputs(run_result: RunResult, cfg: Any | None) -> None:
    if not isinstance(run_result, RunResult) or cfg is None:
        return
    try:
        from backtest.reporting.artifact_outputs import write_run_result_outputs

        output_cfg = SimpleNamespace(
            run=SimpleNamespace(
                topn_candidate_factor=float(
                    getattr(getattr(cfg, "run", None), "topn_candidate_factor", 1.0)
                    or 1.0
                ),
            ),
            portfolio=SimpleNamespace(
                engine=str(getattr(getattr(cfg, "portfolio", None), "engine", "legacy")),
            ),
            paths=SimpleNamespace(
                signal_data=Path(getattr(getattr(cfg, "paths", None), "signal_data")),
                return_date=Path(getattr(getattr(cfg, "paths", None), "return_date")),
                return_date_with_returns=Path(
                    getattr(getattr(cfg, "paths", None), "return_date_with_returns")
                ),
                net_value_unique_date=Path(
                    getattr(getattr(cfg, "paths", None), "net_value_unique_date")
                ),
            ),
        )
        write_run_result_outputs(cfg=output_cfg, result=run_result)
    except Exception as exc:
        log_event(
            "WARNING",
            "backtest_output_persistence",
            "传统回测产物写盘失败，Web 缓存仍已发布",
            run_id=getattr(run_result, "run_id", ""),
            error=str(exc),
        )


def _enrich_meta_with_run_result(
    meta: dict[str, Any],
    run_result: RunResult,
    *,
    status: str,
) -> dict[str, Any]:
    if not isinstance(meta, dict):
        meta = {}
    extra = meta.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        meta["extra"] = extra
    extra["status"] = status

    if not isinstance(run_result, RunResult):
        raise TypeError("_enrich_meta_with_run_result requires RunResult")

    result = meta.get("result")
    if not isinstance(result, dict):
        result = {}
        meta["result"] = result

    payload = run_result.payload
    metrics = payload.metrics if isinstance(payload, CurvePayload) else None
    if metrics is not None:
        result["final_net_value"] = float(metrics.final_net_value)
        result["cum_return"] = float(metrics.final_net_value) - 1.0
        result["max_drawdown"] = float(metrics.max_drawdown)
        result["actual_segment_trade_count"] = int(metrics.actual_segment_trade_count)
        result["actual_segment_avg_trade_return"] = float(
            metrics.actual_segment_avg_trade_return
        )
        result["actual_segment_win_rate"] = float(metrics.actual_segment_win_rate)

    if meta.get("duration_sec") in (None, "") and run_result.duration_sec is not None:
        meta["duration_sec"] = float(run_result.duration_sec)

    return meta


def _write_failure_cache(
    name: str,
    *,
    module: str,
    status: str,
    error: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    strategy_dir = LIBRARY_ROOT / name
    failure_meta = dict(meta or {})
    failure_meta.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
    if module:
        failure_meta["strategy_module"] = module
    extra = failure_meta.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        failure_meta["extra"] = extra
    extra["status"] = status
    if error:
        extra["error"] = str(error)
        failure_meta["error"] = str(error)
    failure_meta.update(
        artifact_schema_fields("run_metadata", RUN_METADATA_SCHEMA_VERSION)
    )
    strategy_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".web-cache-",
        dir=str(strategy_dir),
    ) as temp_dir:
        materialized = Path(temp_dir)
        _write_json_atomic(materialized / "meta.json", failure_meta, default=str)
        publish_web_cache_generation(
            strategy_dir,
            _cache_generation_id(failure_meta),
            materialized,
            activate=False,
        )
        invalidate_library_catalog_cache()


def _normalize_library_cache_meta(
    meta: dict[str, Any],
    *,
    name: str,
    module: str,
    source_path: Path | None,
) -> dict[str, Any]:
    meta["strategy_module"] = module
    meta["library_strategy"] = {
        "name": name,
        "module": module,
        "source_file": _local_source_file_name(LIBRARY_ROOT / name, source_path),
    }
    config = meta.get("config")
    if isinstance(config, dict):
        strategy_config = config.get("strategy")
        if isinstance(strategy_config, dict):
            strategy_config["module"] = module
    return meta
