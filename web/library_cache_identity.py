"""Cache identity contracts for the Web strategy library."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from web.library_evaluation_identity import EVALUATION_IDENTITY_SCHEMA_VERSION
from web.library_metadata import (
    _WEB_BACKTEST_END,
    _WEB_BACKTEST_START,
    _expected_source_signature,
    _normalize_module_id,
    _normalize_yyyymmdd_text,
    _parse_bool_text,
    _parse_int_text,
    _project_root,
    _short_hash,
    _source_file_signature,
    _split_csv_text,
    _stable_payload_hash,
)


def _cached_meta_matches_module(cached_meta: dict[str, Any], expected_module: Any) -> bool:
    expected = _normalize_module_id(expected_module)
    actual = _normalize_module_id(cached_meta.get("strategy_module"))
    if expected and not actual:
        return False
    if not expected:
        return True
    return actual == expected


def _configured_backtest_window(strat_cfg: dict[str, Any]) -> dict[str, str]:
    start = _normalize_yyyymmdd_text(strat_cfg.get("backtest.start_time") or _WEB_BACKTEST_START) or _WEB_BACKTEST_START
    end = _normalize_yyyymmdd_text(strat_cfg.get("backtest.end_time") or _WEB_BACKTEST_END) or _WEB_BACKTEST_END
    return {
        "start_time": start,
        "end_time": end,
    }


def _cache_static_signature(strat_cfg: dict[str, Any]) -> dict[str, Any]:
    segment_flag = (
        strat_cfg.get("run.build_topn_trade_segments_report")
        if strat_cfg.get("run.build_topn_trade_segments_report") is not None
        else strat_cfg.get("run.build_trade_segments")
    )
    static = {
        "module": _normalize_module_id(strat_cfg.get("strategy.module")),
        "backtest": {
            "offset_days": _parse_int_text(strat_cfg.get("backtest.offset_days")),
            "universe_prefixes": _split_csv_text(strat_cfg.get("backtest.universe_prefixes")),
            "exclude_bj": _parse_bool_text(strat_cfg.get("backtest.exclude_bj")),
            "exclude_st": _parse_bool_text(strat_cfg.get("backtest.exclude_st")),
        },
        "run": {
            "compute_metrics": _parse_bool_text(strat_cfg.get("run.compute_metrics")),
            "build_topn_trade_segments_report": _parse_bool_text(segment_flag),
            "exec_buy_timing": str(strat_cfg.get("run.exec_buy_timing") or "").strip(),
            "exec_sell_timing": str(strat_cfg.get("run.exec_sell_timing") or "").strip(),
            "exec_shift_t1": _parse_bool_text(strat_cfg.get("run.exec_shift_t1")),
        },
        "portfolio": {
            "engine": str(strat_cfg.get("portfolio.engine") or "").strip(),
            "initial_capital": str(strat_cfg.get("portfolio.initial_capital") or "").strip(),
        },
        "transaction_cost": {
            "enable": _parse_bool_text(strat_cfg.get("transaction_cost.enable")),
        },
        # Keep every strategy-local override in the identity.  The selected
        # cost and execution switches are not limited to the small legacy
        # subset below, and silently omitting one makes an old curve look
        # current after config edits.
        "strategy_overrides": {
            str(key): str(value or "").strip()
            for key, value in sorted(strat_cfg.items(), key=lambda item: str(item[0]).casefold())
            if not str(key).startswith("paths.")
        },
    }
    global_config_path = _project_root / "config" / "config.ini"
    try:
        static["global_config_sha256"] = _source_file_signature(
            _project_root,
            global_config_path,
        ).get("sha256", "")
    except OSError:
        static["global_config_sha256"] = ""
    if "panel.source" in strat_cfg:
        static["panel"] = {
            "source": str(strat_cfg.get("panel.source") or "").strip(),
        }
    if "mysql.daily_fields" in strat_cfg:
        static.setdefault("mysql", {})["daily_fields"] = _split_csv_text(
            strat_cfg.get("mysql.daily_fields")
        )
    if "mysql.etf_db" in strat_cfg:
        static.setdefault("mysql", {})["etf_db"] = str(
            strat_cfg.get("mysql.etf_db") or ""
        ).strip()

    fixed_rebalance_keys = (
        "target_weights",
        "symbol_type",
        "rebalance_frequency",
        "rebalance_dates",
        "buy_price_col",
        "sell_price_col",
        "mark_price_col",
        "normalize_weights",
    )
    fixed_rebalance: dict[str, Any] = {}
    for key in fixed_rebalance_keys:
        config_key = f"fixed_rebalance.{key}"
        if config_key not in strat_cfg:
            continue
        value: Any = str(strat_cfg.get(config_key) or "").strip()
        if key == "rebalance_dates":
            value = _split_csv_text(value)
        elif key == "normalize_weights":
            value = _parse_bool_text(value)
        fixed_rebalance[key] = value
    if fixed_rebalance:
        static["fixed_rebalance"] = fixed_rebalance
    return static


def _build_strategy_version_summary(
    directory: Path,
    strat_cfg: dict[str, Any],
    *,
    source_path: Path | None,
    cached_meta: dict[str, Any] | None,
    cache_status: Any = "",
) -> dict[str, Any]:
    source_signature = _source_file_signature(directory, source_path)
    static_signature = _cache_static_signature(strat_cfg)
    config_hash = _stable_payload_hash({
        "static": static_signature,
        "configured_window": _configured_backtest_window(strat_cfg),
    })
    version_hash = _stable_payload_hash({
        "source": source_signature,
        "config_hash": config_hash,
    })

    cached_signature = {}
    if isinstance(cached_meta, dict):
        signature = cached_meta.get("cache_config")
        if isinstance(signature, dict):
            cached_signature = signature

    cached_source = cached_signature.get("source") if isinstance(cached_signature, dict) else {}
    cached_static = cached_signature.get("static") if isinstance(cached_signature, dict) else {}
    cached_window = cached_signature.get("configured_window") if isinstance(cached_signature, dict) else {}
    cached_config_hash = ""
    if cached_static or cached_window:
        cached_config_hash = _stable_payload_hash({
            "static": cached_static,
            "configured_window": cached_window,
        })

    status = "untracked"
    status_label = "待回测"
    if source_signature:
        status = "current"
        status_label = "当前版本"
    if str(cache_status or "").strip().lower() in {"valid", "failed"} and cached_meta is not None:
        status = "verified" if str(cache_status).strip().lower() == "valid" else "failed"
        status_label = "已验证" if status == "verified" else "运行失败"
    elif cached_signature or cached_meta is not None:
        status = "changed"
        status_label = "已变更"

    return {
        "strategy_version": f"v-{_short_hash(version_hash, 8)}" if version_hash else "",
        "version_status": status,
        "version_status_label": status_label,
        "version_hash": version_hash,
        "source_sha256": source_signature.get("sha256") if source_signature else "",
        "source_hash": _short_hash(source_signature.get("sha256") if source_signature else ""),
        "source_size": source_signature.get("size") if source_signature else None,
        "config_hash": _short_hash(config_hash),
        "full_config_hash": config_hash,
        "run_source_hash": _short_hash(cached_source.get("sha256") if isinstance(cached_source, dict) else ""),
        "run_config_hash": _short_hash(cached_config_hash),
        "run_id": cached_meta.get("run_id") if isinstance(cached_meta, dict) else None,
        "git_commit": (
            cached_meta.get("git", {}).get("commit")
            if isinstance(cached_meta, dict) and isinstance(cached_meta.get("git"), dict)
            else ""
        ),
        "git_describe": (
            cached_meta.get("git", {}).get("describe")
            if isinstance(cached_meta, dict) and isinstance(cached_meta.get("git"), dict)
            else ""
        ),
        "git_dirty": (
            cached_meta.get("git", {}).get("dirty")
            if isinstance(cached_meta, dict) and isinstance(cached_meta.get("git"), dict)
            else None
        ),
    }


def _date_overrides_change_config_window(
    strat_cfg: dict[str, Any],
    date_overrides: dict[str, Any] | None,
) -> bool:
    if not isinstance(date_overrides, dict):
        return False
    configured = _configured_backtest_window(strat_cfg)
    override_start = _normalize_yyyymmdd_text(date_overrides.get("start_time")) or configured["start_time"]
    override_end = _normalize_yyyymmdd_text(date_overrides.get("end_time")) or configured["end_time"]
    return override_start != configured["start_time"] or override_end != configured["end_time"]


def _build_cache_config_signature(
    strat_cfg: dict[str, Any],
    *,
    effective_window: dict[str, Any],
    date_overrides: dict[str, Any] | None = None,
    strategy_dir: Path | None = None,
    source_path: Path | None = None,
) -> dict[str, Any]:
    configured = _configured_backtest_window(strat_cfg)
    effective = {
        "start_time": _normalize_yyyymmdd_text(effective_window.get("start_time")) or configured["start_time"],
        "end_time": _normalize_yyyymmdd_text(effective_window.get("end_time")) or configured["end_time"],
    }
    source_signature = (
        _source_file_signature(strategy_dir, source_path)
        if strategy_dir is not None and source_path is not None
        else _expected_source_signature(strategy_dir, strat_cfg)
    )
    if _date_overrides_change_config_window(strat_cfg, date_overrides):
        date_source = "override"
    elif effective != configured:
        # The runtime clips a configured future end date to the latest
        # available market/calendar date.  Keep that fact explicit instead
        # of publishing a signature that the catalog must reject.
        date_source = "latest_available"
    else:
        date_source = "config"
    return {
        "schema": 2,
        "static": _cache_static_signature(strat_cfg),
        "source": source_signature,
        "configured_window": configured,
        "effective_window": effective,
        "date_source": date_source,
    }


def _cache_window_error(
    signature: dict[str, Any],
    strat_cfg: dict[str, Any],
) -> str:
    date_source = str(signature.get("date_source") or "")
    if date_source not in {"config", "latest_available"}:
        return "缓存日期来源无效"
    configured_window = signature.get("configured_window")
    effective_window = signature.get("effective_window")
    if configured_window != _configured_backtest_window(strat_cfg):
        return "回测配置窗口不一致"
    if not isinstance(effective_window, dict):
        return "缓存缺少有效回测窗口"
    normalized_effective = {
        "start_time": _normalize_yyyymmdd_text(effective_window.get("start_time")),
        "end_time": _normalize_yyyymmdd_text(effective_window.get("end_time")),
    }
    if normalized_effective != effective_window or not normalized_effective["start_time"]:
        return "缓存有效回测窗口无效"
    if date_source == "config" and effective_window != configured_window:
        return "缓存配置窗口与有效窗口不一致"
    if date_source == "latest_available":
        configured_start = str(configured_window.get("start_time") or "")
        if effective_window["start_time"] != configured_start:
            return "缓存最新可用窗口起始日不一致"
        if effective_window["end_time"] > configured_window["end_time"]:
            return "缓存最新可用窗口超出配置截止日"
    return ""


def _current_cache_signature(
    cached_meta: dict[str, Any],
    strat_cfg: dict[str, Any],
    *,
    directory: Path | None = None,
) -> tuple[bool, str]:
    expected_module = strat_cfg.get("strategy.module", "")
    if expected_module and not _cached_meta_matches_module(cached_meta, expected_module):
        return False, "策略模块不一致"
    signature = cached_meta.get("cache_config")
    if not isinstance(signature, dict) or signature.get("schema") != 2:
        return False, "缓存缺少 schema-v2 完整签名"
    if signature.get("static") != _cache_static_signature(strat_cfg):
        return False, "关键配置不一致"
    evaluation = cached_meta.get("evaluation_identity")
    if not isinstance(evaluation, dict):
        return False, "缓存缺少 evaluation identity"
    if evaluation.get("schema") != EVALUATION_IDENTITY_SCHEMA_VERSION:
        return False, "缓存 evaluation identity 版本不匹配"
    if str(evaluation.get("run_kind") or "") != "default" or not bool(
        evaluation.get("default_eligible")
    ):
        return False, "缓存不是默认评估结果"
    if not str(evaluation.get("context_id") or ""):
        return False, "缓存缺少评估口径身份"
    expected_source = _expected_source_signature(directory, strat_cfg)
    cached_source = signature.get("source")
    if expected_source and cached_source != expected_source:
        return False, "策略源码不一致"
    if expected_source and not isinstance(cached_source, dict):
        return False, "缓存缺少源码指纹"
    window_error = _cache_window_error(signature, strat_cfg)
    if window_error:
        return False, window_error
    return True, ""


def _cache_signature_matches_config(
    cached_meta: dict[str, Any],
    strat_cfg: dict[str, Any],
    *,
    directory: Path | None = None,
) -> bool:
    matches, _ = _current_cache_signature(cached_meta, strat_cfg, directory=directory)
    return matches


def _cached_meta_window(cached_meta: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(cached_meta, dict):
        return {"start_time": "", "end_time": ""}

    signature = cached_meta.get("cache_config")
    if isinstance(signature, dict) and signature.get("schema") == 2:
        for key in ("effective_window", "configured_window"):
            window = signature.get(key)
            if isinstance(window, dict):
                start = _normalize_yyyymmdd_text(window.get("start_time"))
                end = _normalize_yyyymmdd_text(window.get("end_time"))
                if start or end:
                    return {"start_time": start, "end_time": end}

    return {"start_time": "", "end_time": ""}


def _cache_signature_error(
    cached_meta: dict[str, Any] | None,
    strat_cfg: dict[str, Any],
    *,
    directory: Path | None = None,
) -> str:
    if not isinstance(cached_meta, dict):
        return "缺少 meta.json"
    expected_module = _normalize_module_id(strat_cfg.get("strategy.module"))
    actual_module = _normalize_module_id(cached_meta.get("strategy_module"))
    if expected_module and not actual_module:
        return "缓存缺少策略模块信息"
    if expected_module and actual_module and expected_module != actual_module:
        return f"策略模块不一致：缓存 {actual_module}，配置 {expected_module}"

    _, reason = _current_cache_signature(cached_meta, strat_cfg, directory=directory)
    return reason
