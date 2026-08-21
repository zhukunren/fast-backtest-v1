"""Current-generation cache validation and catalog assembly."""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from web import library_cache, library_cache_identity
from web.cache_generation import web_cache_current_pointer_path
from web.library_analysis import _curve_max_drawdown, _extract_net_value_points
from web.library_metadata import (
    LIBRARY_ROOT,
    _FAMILY_FILTER_ORDER,
    _STATUS_LABELS,
    _WEB_BACKTEST_END,
    _WEB_BACKTEST_START,
    _build_strategy_profile,
    _family_info,
    _is_local_strategy_module,
    _local_source_file_name,
    _module_basename,
    _normalize_yyyymmdd_text,
    _parse_bool_text,
    _parse_int_text,
    _parse_strategy_config,
    _parse_strategy_notes,
    _pretty_name,
    _relative_source_path,
    _resolve_library_strategy_file,
    _split_csv_text,
)

_LIBRARY_CATALOG_CACHE_TTL_SEC = 2.0
_library_catalog_cache_lock = threading.Lock()
_library_catalog_cache: tuple[float, tuple[Any, ...], dict[str, Any]] | None = None

# The catalog route is a navigation index.  Keep source-level strategy prose in
# the detail endpoint so a list refresh does not transfer every full manifest.
_CATALOG_SUMMARY_EXCLUDED_FIELDS = frozenset({
    "buy_logic",
    "sell_logic",
    "position_text",
    "strategy_description",
    "description_diagnostics",
})


def _catalog_path_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _library_catalog_source_signature() -> tuple[Any, ...]:
    """Track changes that can make a cached catalog entry stale."""
    try:
        root_key = str(LIBRARY_ROOT.resolve())
        directories = sorted(LIBRARY_ROOT.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return (root_key if "root_key" in locals() else str(LIBRARY_ROOT), None)

    children: list[tuple[Any, ...]] = []
    for directory in directories:
        try:
            if not directory.is_dir() or directory.name.startswith(("_", ".")):
                continue
            cfg = _parse_strategy_config(directory / "config.ini")
            source_path = _resolve_library_strategy_file(
                directory,
                cfg.get("strategy.module", ""),
            )
            children.append(
                (
                    directory.name,
                    _catalog_path_signature(directory),
                    _catalog_path_signature(directory / "config.ini"),
                    _catalog_path_signature(source_path) if source_path is not None else None,
                    _catalog_path_signature(directory / "strategy_description.json"),
                    _catalog_path_signature(web_cache_current_pointer_path(directory)),
                )
            )
        except OSError:
            children.append((directory.name, "unreadable"))
    return root_key, tuple(children)


def invalidate_library_catalog_cache() -> None:
    """Drop the short-lived catalog snapshot after cache mutations."""
    global _library_catalog_cache
    with _library_catalog_cache_lock:
        _library_catalog_cache = None


def _normalize_drawdown_metric(value: Any) -> Any:
    """Expose scalar drawdown metrics as positive magnitudes at the API boundary."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return abs(number) if math.isfinite(number) else value


def _derive_curve_metrics(nv_data: Any) -> dict[str, float]:
    if not isinstance(nv_data, dict):
        return {}
    points = _extract_net_value_points(nv_data)
    if not points:
        return {}
    values = [float(point["net_value"]) for point in points]
    derived = {
        "final_net_value": round(values[-1], 6),
        "cum_return": round(values[-1] - 1.0, 6),
    }
    if len(values) >= 2:
        derived["max_drawdown"] = round(_curve_max_drawdown([1.0, *values]), 6)
    return derived








def _parse_metric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _derive_comparable_metrics(
    result: dict[str, Any],
    window: dict[str, str],
) -> dict[str, float | None]:
    """Derive window-normalized metrics from persisted scalar run results."""
    cum_return = _parse_metric(result.get("cum_return"))
    drawdown = _parse_metric(result.get("max_drawdown"))
    ann_return = _parse_metric(
        result.get("ann_return")
        if result.get("ann_return") is not None
        else result.get("annualized_return")
    )
    if ann_return is None and cum_return is not None and 1.0 + cum_return > 0:
        try:
            start = datetime.strptime(window.get("start_time", ""), "%Y%m%d")
            end = datetime.strptime(window.get("end_time", ""), "%Y%m%d")
            days = max(1, (end - start).days)
            ann_return = (1.0 + cum_return) ** (365.25 / days) - 1.0
        except (TypeError, ValueError, OverflowError):
            ann_return = None
    calmar = _parse_metric(result.get("calmar"))
    if calmar is None and ann_return is not None and drawdown is not None and abs(drawdown) > 1e-12:
        calmar = ann_return / abs(drawdown)
    return {
        "ann_return": ann_return,
        "sharpe": _parse_metric(result.get("sharpe")),
        "calmar": calmar,
    }


def _build_library_entry(directory: Path) -> dict[str, Any]:
    cfg_path = directory / "config.ini"
    cfg = _parse_strategy_config(cfg_path)
    notes = _parse_strategy_notes(cfg_path)
    module = cfg.get("strategy.module", "").strip()
    family, family_label = _family_info(module)

    source_path = _resolve_library_strategy_file(directory, module)
    source_snapshot = (
        _local_source_file_name(directory, source_path)
        if _is_local_strategy_module(module)
        else None
    )
    snapshot, generation_diag = library_cache._resolve_library_cache_context(directory.name)
    cache_diag = library_cache._diagnose_library_cache(
        directory.name,
        snapshot=snapshot,
        generation_diag=generation_diag,
    )
    raw_cached_meta = (
        library_cache._read_cached_meta_raw(directory.name, snapshot=snapshot)
        if snapshot is not None
        else None
    )
    cached_meta = None
    if snapshot is not None and cache_diag.get("cache_status") in {"valid", "failed"}:
        cached_meta = library_cache._read_cached_meta(
            directory.name,
            expected_module=module,
            snapshot=snapshot,
        )
    # Keep scalar last-known-good metrics visible when the current generation
    # is stale, but never treat them as a valid result for ranking or detail
    # reads.  The status and cache_status fields make that distinction explicit.
    metric_meta = cached_meta
    if metric_meta is None and isinstance(raw_cached_meta, dict) and cache_diag.get("cache_status") == "invalid":
        metric_meta = raw_cached_meta
    # Meaningful strategy prose, including the title, comes only from the
    # verified LLM-authored manifest.  A config comment may provide an
    # explicit administrator display label, but a legacy ``策略:`` comment is
    # never interpreted as strategy metadata.
    fallback_title = _pretty_name(directory.name)
    profile = _build_strategy_profile(
        directory=directory,
        notes=notes,
        source_path=source_path,
        fallback_title=fallback_title,
    )
    # The explicit administrator label is useful for catalog identity, while
    # detail/profile consumers retain the LLM-authored title.
    title = profile.get("display_title") or profile["title"]
    summary = profile["summary"]

    start = _normalize_yyyymmdd_text(cfg.get("backtest.start_time") or _WEB_BACKTEST_START) or _WEB_BACKTEST_START
    end = _normalize_yyyymmdd_text(cfg.get("backtest.end_time") or _WEB_BACKTEST_END) or _WEB_BACKTEST_END
    offset_days = _parse_int_text(cfg.get("backtest.offset_days"))
    universe = _split_csv_text(cfg.get("backtest.universe_prefixes"))

    result = metric_meta.get("result", {}) if metric_meta is not None else {}
    if not isinstance(result, dict):
        result = {}
    status = "idle"
    if cached_meta is not None:
        status = library_cache._cached_status(cached_meta) or "cached"
    elif cache_diag.get("cache_status") == "invalid" and raw_cached_meta is not None:
        status = "stale"
    elif cache_diag.get("latest_attempt_status") in {"failed", "config_failed"}:
        status = str(cache_diag.get("latest_attempt_status"))
    error = library_cache._cached_error(cached_meta) or (
        str(cache_diag.get("latest_attempt_error") or "")
        if status in {"failed", "config_failed"}
        else ""
    )

    sparkline = None
    if isinstance(metric_meta, dict) and "sparkline" in metric_meta:
        sparkline = metric_meta["sparkline"]

    description_manifest = profile.get("strategy_description")
    description_runtime = (
        description_manifest.get("runtime", {})
        if isinstance(description_manifest, dict)
        else {}
    )
    top_n = _parse_int_text(description_runtime.get("top_n"))
    pj = _parse_int_text(description_runtime.get("portfolio_num_j"))
    pi = _parse_int_text(description_runtime.get("portfolio_num_i"))
    if top_n is None and pj is not None and pi is not None:
        top_n = pj * pi
    portfolio_label = f"{pj}x{pi}" if pj not in (None, "") and pi not in (None, "") else None
    version_summary = library_cache_identity._build_strategy_version_summary(
        directory,
        cfg,
        source_path=source_path,
        cached_meta=raw_cached_meta,
        cache_status=cache_diag.get("cache_status"),
    )

    evaluation_identity = (
        metric_meta.get("evaluation_identity")
        if isinstance(metric_meta, dict) and isinstance(metric_meta.get("evaluation_identity"), dict)
        else None
    )
    if isinstance(evaluation_identity, dict):
        evaluation_identity = dict(evaluation_identity)
        if not evaluation_identity.get("execution_config_hash"):
            evaluation_identity["execution_config_hash"] = evaluation_identity.get("resolved_config_hash") or None
    evaluation_id = str((evaluation_identity or {}).get("id") or "")
    evaluation_context_id = str((evaluation_identity or {}).get("context_id") or "")
    metric_window = library_cache_identity._cached_meta_window(metric_meta)
    if not metric_window["start_time"]:
        metric_window["start_time"] = str(cache_diag.get("cache_actual_start_time") or start)
    if not metric_window["end_time"]:
        metric_window["end_time"] = str(cache_diag.get("cache_actual_end_time") or end)
    comparable_metrics = _derive_comparable_metrics(result, metric_window)
    evaluation_window_label = (
        f"{metric_window['start_time']} 至 {metric_window['end_time']}"
        if metric_window["start_time"] or metric_window["end_time"]
        else ""
    )
    evaluation_comparable = (
        True
        if cache_diag.get("cache_status") == "valid"
        and bool(evaluation_context_id)
        and status in {"completed", "no_signal"}
        else None
    )

    return {
        "name": directory.name,
        "title": title,
        "summary": summary,
        "buy_logic": profile["buy_logic"],
        "sell_logic": profile["sell_logic"],
        "position_text": profile["position_text"],
        "style_label": profile["style_label"],
        "description_status": profile["description_status"],
        "description_diagnostics": profile["description_diagnostics"],
        "strategy_description": description_manifest,
        "module": module,
        "module_short": _module_basename(module),
        "family": family,
        "family_label": family_label,
        "source_file": _relative_source_path(source_path),
        "local_source_file": source_snapshot,
        **version_summary,
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "error": error,
        "has_cache": cache_diag.get("cache_status") == "valid" and cached_meta is not None,
        "cache_status": cache_diag.get("cache_status"),
        "cache_reason": cache_diag.get("cache_reason"),
        "cache_generation_id": cache_diag.get("cache_generation_id"),
        "cache_artifact_count": cache_diag.get("cache_artifact_count"),
        "cache_size_bytes": cache_diag.get("cache_size_bytes"),
        "cache_actual_start_time": cache_diag.get("cache_actual_start_time"),
        "cache_actual_end_time": cache_diag.get("cache_actual_end_time"),
        "cache_date_source": cache_diag.get("cache_date_source"),
        "latest_attempt_generation_id": cache_diag.get("latest_attempt_generation_id"),
        "latest_attempt_status": cache_diag.get("latest_attempt_status"),
        "latest_attempt_error": cache_diag.get("latest_attempt_error"),
        "latest_attempt_activated": cache_diag.get("latest_attempt_activated"),
        "latest_attempt_evaluation_id": cache_diag.get("latest_attempt_evaluation_id"),
        "latest_attempt_evaluation_context_id": cache_diag.get("latest_attempt_evaluation_context_id"),
        "latest_attempt_run_kind": cache_diag.get("latest_attempt_run_kind"),
        "evaluation_identity": evaluation_identity,
        "evaluation_id": evaluation_id or None,
        "evaluation_context_id": evaluation_context_id or None,
        "evaluation_status": "valid" if cache_diag.get("cache_status") == "valid" else status,
        "evaluation_status_label": "评估有效" if cache_diag.get("cache_status") == "valid" else _STATUS_LABELS.get(status, status),
        "evaluation_comparable": evaluation_comparable,
        "evaluation_window": metric_window,
        "evaluation_window_label": evaluation_window_label,
        "style": profile["style_label"],
        "portfolio": portfolio_label,
        "timestamp": metric_meta.get("timestamp") if metric_meta else None,
        "duration_sec": metric_meta.get("duration_sec") if metric_meta else None,
        "start_time": start,
        "end_time": end,
        "offset_days": offset_days,
        "universe_prefixes": universe,
        "exclude_bj": _parse_bool_text(cfg.get("backtest.exclude_bj")),
        "exclude_st": _parse_bool_text(cfg.get("backtest.exclude_st")),
        "top_n": top_n,
        "portfolio_label": portfolio_label,
        "portfolio_num_j": pj,
        "portfolio_num_i": pi,
        "cum_return": result.get("cum_return"),
        "ann_return": comparable_metrics["ann_return"],
        "sharpe": comparable_metrics["sharpe"],
        "calmar": comparable_metrics["calmar"],
        "max_drawdown": _normalize_drawdown_metric(result.get("max_drawdown")),
        "final_net_value": result.get("final_net_value"),
        "trade_count": result.get("actual_segment_trade_count"),
        "avg_trade_return": result.get("actual_segment_avg_trade_return"),
        "win_rate": result.get("actual_segment_win_rate"),
        "sparkline": sparkline,
        "tags": [],
    }


def _sort_library_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {
        "completed": 0,
        "no_signal": 1,
        "cached": 1,
        "stale": 2,
        "config_failed": 3,
        "failed": 4,
        "idle": 5,
    }

    def _risk_score(item: dict[str, Any]) -> float:
        for key in ("sharpe", "calmar", "ann_return", "cum_return"):
            value = _parse_metric(item.get(key))
            if value is not None:
                return value
        return float("-inf")

    entries.sort(key=lambda item: (
        status_order.get(str(item.get("status")), 9),
        -_risk_score(item),
        str(item.get("family", "")).casefold(),
        str(item.get("name", "")).casefold(),
    ))

    rank_by_context: dict[str, int] = {}
    for item in entries:
        if item.get("status") in {"completed", "no_signal", "cached"} and item.get("cum_return") is not None:
            context = str(item.get("evaluation_context_id") or "").strip()
            if not context:
                item["rank"] = None
                item["rank_scope"] = None
                continue
            rank_by_context[context] = rank_by_context.get(context, 0) + 1
            item["rank"] = rank_by_context[context]
            item["rank_scope"] = context
        else:
            item["rank"] = None
            item["rank_scope"] = None
    return entries


def _scan_library_entries() -> list[dict[str, Any]]:
    if not LIBRARY_ROOT.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for directory in sorted(LIBRARY_ROOT.iterdir(), key=lambda p: p.name.casefold()):
        try:
            if not directory.is_dir() or directory.name.startswith(("_", ".")):
                continue
            cfg_path = directory / "config.ini"
            if not cfg_path.is_file():
                continue
            entries.append(_build_library_entry(directory))
        except OSError:
            # A single unreadable strategy directory must not block the catalog.
            continue
    return _sort_library_entries(entries)


def _build_catalog_filters(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    families = [{"value": "all", "label": "全部来源", "count": len(entries)}]
    for family, label in _FAMILY_FILTER_ORDER:
        count = sum(1 for item in entries if item.get("family") == family)
        if count > 0:
            families.append({"value": family, "label": label, "count": count})

    status_order = ("completed", "no_signal", "stale", "config_failed", "failed", "idle")
    statuses = [{"value": "all", "label": "全部状态", "count": len(entries)}]
    for status in status_order:
        count = sum(1 for item in entries if item.get("status") == status)
        if count > 0:
            statuses.append({
                "value": status,
                "label": _STATUS_LABELS.get(status, status),
                "count": count,
            })

    def facet(
        field: str,
        *,
        all_label: str,
        value_label: Any = None,
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        for item in entries:
            value = str(item.get(field) or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
            labels[value] = str(value_label(value) if callable(value_label) else value)
        options = [{"value": "all", "label": all_label, "count": len(entries)}]
        options.extend(
            {"value": value, "label": labels[value], "count": counts[value]}
            for value in sorted(counts, key=lambda value: labels[value].casefold())
        )
        return options

    return {
        "families": families,
        "statuses": statuses,
        "styles": facet("style", all_label="全部风格"),
        "portfolios": facet("portfolio", all_label="全部持仓"),
        "evaluation_windows": facet(
            "evaluation_window_label",
            all_label="全部评估区间",
        ),
    }


def _build_catalog_overview(entries: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [
        item for item in entries
        if item.get("cache_status") == "valid"
        and item.get("status") in {"completed", "no_signal", "cached"}
        and item.get("cum_return") is not None
    ]
    completed = [item for item in valid if item.get("status") == "completed"]
    cached = valid
    failed = [
        item for item in entries
        if item.get("status") in {"failed", "config_failed"}
        or item.get("latest_attempt_status") in {"failed", "config_failed"}
    ]
    stale = [item for item in entries if item.get("status") == "stale" or item.get("cache_status") == "invalid"]
    idle = [item for item in entries if item.get("status") == "idle"]
    positive = [item for item in valid if (item.get("cum_return") or 0) > 0]
    high_risk = [
        item for item in valid
        if abs(item.get("max_drawdown") or 0) >= 0.25
    ]
    best = max(
        valid,
        key=lambda item: (
            _parse_metric(item.get("sharpe"))
            if _parse_metric(item.get("sharpe")) is not None
            else _parse_metric(item.get("calmar"))
            if _parse_metric(item.get("calmar")) is not None
            else _parse_metric(item.get("ann_return"))
            if _parse_metric(item.get("ann_return")) is not None
            else _parse_metric(item.get("cum_return")) or float("-inf")
        ),
        default=None,
    )
    avg_return = None
    if completed:
        avg_return = round(
            sum(float(item["cum_return"]) for item in completed if item["cum_return"] is not None)
            / len(completed),
            6,
        )
    latest_ts = max(
        (str(item["timestamp"]) for item in valid if item.get("timestamp")),
        default=None,
    )

    evaluation = _build_catalog_evaluation(valid)

    return {
        "total": len(entries),
        "cached": len(cached),
        "completed": len(completed),
        "valid": len(valid),
        "failed": len(failed),
        "stale": len(stale),
        "idle": len(idle),
        "waiting": len(idle),
        "positive": len(positive),
        "high_risk": len(high_risk),
        "completion_rate": round(len(valid) / len(entries), 4) if entries else 0.0,
        "avg_return": avg_return,
        "avg_ann_return": round(
            sum(float(item["ann_return"]) for item in valid if item.get("ann_return") is not None)
            / max(1, sum(1 for item in valid if item.get("ann_return") is not None)),
            6,
        ) if any(item.get("ann_return") is not None for item in valid) else None,
        "latest_timestamp": latest_ts,
        "best": {
            "name": best.get("name"),
            "title": best.get("title"),
            "cum_return": best.get("cum_return"),
            "ann_return": best.get("ann_return"),
            "sharpe": best.get("sharpe"),
            "calmar": best.get("calmar"),
            "max_drawdown": best.get("max_drawdown"),
            "timestamp": best.get("timestamp"),
        } if best else None,
        "_evaluation": evaluation,
    }


def _build_catalog_evaluation(entries: list[dict[str, Any]]) -> dict[str, Any]:
    valid = entries
    contexts = {
        str(item.get("evaluation_context_id"))
        for item in valid
        if str(item.get("evaluation_context_id") or "").strip()
    }
    windows = {
        str(item.get("evaluation_window_label"))
        for item in valid
        if str(item.get("evaluation_window_label") or "").strip()
    }
    evaluation_comparable: bool | None
    if len(contexts) == 1 and len(windows) <= 1 and len(valid) >= 2:
        evaluation_comparable = True
    elif len(contexts) > 1 or len(windows) > 1:
        evaluation_comparable = False
    else:
        evaluation_comparable = None

    common_window = None
    if len(windows) == 1:
        first_window = next(
            (
                item.get("evaluation_window")
                for item in valid
                if isinstance(item.get("evaluation_window"), dict)
            ),
            None,
        )
        if isinstance(first_window, dict):
            common_window = first_window
    first_identity = next(
        (
            item.get("evaluation_identity")
            for item in valid
            if isinstance(item.get("evaluation_identity"), dict)
        ),
        {},
    )
    return {
        "comparable": evaluation_comparable,
        "status": "comparable" if evaluation_comparable is True else "incompatible" if evaluation_comparable is False else "unknown",
        "status_label": "评估条件一致" if evaluation_comparable is True else "评估条件不一致" if evaluation_comparable is False else "评估条件待确认",
        "context_count": len(contexts),
        "window_count": len(windows),
        "effective_window": common_window,
        "data_cutoff": first_identity.get("data_cutoff") if isinstance(first_identity, dict) else None,
        "execution_config_hash": (
            first_identity.get("execution_config_hash")
            or first_identity.get("resolved_config_hash")
            if isinstance(first_identity, dict)
            else None
        ),
    }


def list_library() -> list[dict[str, Any]]:
    """扫描策略库，返回每个策略的概要信息。"""
    return _scan_library_entries()


def get_library_catalog() -> dict[str, Any]:
    global _library_catalog_cache
    now = time.monotonic()
    source_signature = _library_catalog_source_signature()
    with _library_catalog_cache_lock:
        cached = _library_catalog_cache
        if (
            cached is not None
            and now - cached[0] < _LIBRARY_CATALOG_CACHE_TTL_SEC
            and cached[1] == source_signature
        ):
            return json.loads(json.dumps(cached[2], ensure_ascii=False))

        entries = _scan_library_entries()
        overview = _build_catalog_overview(entries)
        overview.pop("_evaluation", None)
        catalog = {
            "overview": overview,
            "filters": _build_catalog_filters(entries),
            "items": entries,
        }
        _library_catalog_cache = (time.monotonic(), source_signature, catalog)
        return json.loads(json.dumps(catalog, ensure_ascii=False))


def get_library_catalog_summary() -> dict[str, Any]:
    """Return the lightweight catalog projection used by list consumers."""
    catalog = get_library_catalog()
    items = catalog.get("items")
    if not isinstance(items, list):
        items = []
    return {
        "overview": catalog.get("overview", {}),
        "filters": catalog.get("filters", {}),
        "evaluation": _build_catalog_evaluation(
            [item for item in items if isinstance(item, dict) and item.get("cache_status") == "valid"]
        ),
        "items": [
            {
                key: value
                for key, value in item.items()
                if key not in _CATALOG_SUMMARY_EXCLUDED_FIELDS
            }
            for item in items
            if isinstance(item, dict)
        ],
    }


# ---------------------------------------------------------------------------
# 单策略数据读取
# ---------------------------------------------------------------------------

def get_library_detail(name: str) -> dict[str, Any] | None:
    directory = LIBRARY_ROOT / name
    cfg_path = directory / "config.ini"
    strat_cfg = _parse_strategy_config(cfg_path) if cfg_path.is_file() else {}
    expected_module = strat_cfg.get("strategy.module", "")
    snapshot = library_cache._resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    meta = library_cache._read_cached_meta(
        name,
        expected_module=expected_module,
        snapshot=snapshot,
    )
    if meta is None or not library_cache_identity._cache_signature_matches_config(
        meta,
        strat_cfg,
        directory=directory,
    ):
        return None
    detail = dict(meta)
    cached_library_strategy = detail.get("library_strategy")
    if isinstance(cached_library_strategy, dict):
        # A directory rename moves the immutable generation as-is.  Present
        # the current library identity without rewriting and re-hashing cache
        # artifacts on disk.
        detail["library_strategy"] = {
            **cached_library_strategy,
            "name": name,
        }
    detail["name"] = name
    curve_metrics = {}
    # Curves own artifact reads; import lazily to keep the catalog/curve
    # modules acyclic while making the dependency explicit.
    from web.library_curves import get_library_net_value

    curve_metrics = _derive_curve_metrics(
        get_library_net_value(
            name,
            snapshot=snapshot,
            include_benchmark=False,
        )
    )
    if curve_metrics:
        detail.update(curve_metrics)
    if "max_drawdown" in detail:
        detail["max_drawdown"] = _normalize_drawdown_metric(detail.get("max_drawdown"))
    result = detail.get("result")
    if isinstance(result, dict):
        normalized_result = dict(result)
        if "max_drawdown" in normalized_result:
            normalized_result["max_drawdown"] = _normalize_drawdown_metric(
                normalized_result.get("max_drawdown")
            )
        if curve_metrics:
            normalized_result.update(curve_metrics)
        detail["result"] = normalized_result
    if directory.is_dir() and cfg_path.is_file():
        detail["catalog_entry"] = _build_library_entry(directory)
    return detail
