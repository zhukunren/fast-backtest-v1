"""Configuration, identity, and authored-description readers for the Web library.

Strategy meaning is authored outside the Web subsystem.  This module only
validates and exposes ``strategy_description.json``; it never parses strategy
source code to infer metadata or execution semantics.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


_project_root = Path(__file__).resolve().parent.parent
_web_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

LIBRARY_ROOT = _web_root / "strategy_library"
_WEB_BACKTEST_START = "20240101"
_WEB_BACKTEST_END = "20261231"
_STRATEGY_DESCRIPTION_MANIFEST_NAME = "strategy_description.json"
_STRATEGY_DESCRIPTION_SCHEMA_VERSION = 2
_CANONICAL_STRATEGY_MODULE_PREFIX = "strategy_library."
_DESCRIPTION_PROFILE_FIELDS = (
    "title",
    "summary",
    "buy_logic",
    "sell_logic",
    "position_text",
    "style_label",
)

_FAMILY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("strategy_library.builtin", "builtin", "内置"),
    ("strategy_library.generated", "generated", "生成"),
    ("strategy_library.custom", "custom", "自定义"),
    ("strategy_library.validated", "validated", "已验证"),
)
_LOCAL_FAMILY = ("library", "策略库")
_FAMILY_FILTER_ORDER: tuple[tuple[str, str], ...] = (
    _LOCAL_FAMILY,
    ("builtin", "内置"),
    ("generated", "生成"),
    ("custom", "自定义"),
    ("validated", "已验证"),
    ("unknown", "未知"),
)

_STATUS_LABELS: dict[str, str] = {
    "completed": "已回测",
    "no_signal": "无信号",
    "config_failed": "回测失败",
    "failed": "回测失败",
    "running": "运行中",
    "cached": "已回测",
    "idle": "待回测",
    "stale": "结果已过期",
}

_DESCRIPTION_STATUS_TEXT = {
    "missing": "策略描述文件缺失",
    "invalid": "策略描述文件无效",
    "stale": "策略描述与当前源码不一致",
    "unavailable": "策略描述无法校验",
}

_BENCHMARK_NAME = "创业板指"
_BENCHMARK_DISPLAY_CODE = "399006.SZ"
_BENCHMARK_SOURCE_CODES: tuple[str, ...] = (_BENCHMARK_DISPLAY_CODE, "399006.SH")


def _parse_strategy_config(cfg_path: Path) -> dict[str, str]:
    """Parse a strategy-local config into flattened ``section.key`` values."""
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return {}
    flat: dict[str, str] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            flat[f"{section}.{key}"] = value
    return flat


def _strategy_config_parse_error(cfg_path: Path) -> str:
    if not cfg_path.is_file():
        return ""
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError) as exc:
        return str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
    return ""


def _parse_strategy_notes(cfg_path: Path) -> dict[str, str]:
    """Read only the explicit administrator display label from ``config.ini``."""
    display_title = ""
    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {"display_title": ""}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("["):
            break
        if not line.startswith("#"):
            continue
        note = line.lstrip("#").strip().replace("：", ":")
        if note.startswith("显示标题:"):
            display_title = note.split(":", 1)[1].strip()
    return {"display_title": display_title}


def _split_csv_text(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _parse_bool_text(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_int_text(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_yyyymmdd_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).strftime("%Y%m%d")
    except ValueError:
        return text.replace("-", "")


def _today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _min_yyyymmdd_text(*values: Any) -> str:
    normalized = [
        text
        for value in values
        if (text := _normalize_yyyymmdd_text(value))
    ]
    return min(normalized) if normalized else ""


def _latest_trade_calendar_day(start_date: str, end_date: str) -> str:
    from config import get_config
    from data import get_trade_calendar

    try:
        calendar = get_trade_calendar(start_date, end_date, cfg=get_config(reload=True))
    except Exception:
        return ""
    if calendar is None or calendar.empty or "cal_date" not in calendar.columns:
        return ""
    try:
        return calendar["cal_date"].iloc[-1].strftime("%Y%m%d")
    except (AttributeError, IndexError, TypeError, ValueError):
        return ""


def _probe_latest_stock_data_date(start_date: str, end_date: str) -> str:
    try:
        from config import get_config
        from data import read_daily_from_mysql
    except ImportError:
        return ""

    try:
        cfg = get_config(reload=True)
    except Exception:
        return ""
    symbol_code = str(getattr(cfg.backtest, "default_stock_code", "") or "").strip()
    if not symbol_code:
        return ""
    try:
        frame = read_daily_from_mysql(
            symbol_code,
            start_date=start_date,
            end_date=end_date,
            columns=["trade_date", "close"],
            cfg=cfg,
        )
    except Exception:
        return ""
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return ""
    try:
        latest = frame["trade_date"].iloc[-1]
    except (IndexError, KeyError):
        return ""
    if hasattr(latest, "strftime"):
        try:
            return latest.strftime("%Y%m%d")
        except (AttributeError, TypeError, ValueError):
            pass
    return _normalize_yyyymmdd_text(latest)


def _resolve_latest_backtest_end_time(start_time: Any) -> str:
    start_date = _normalize_yyyymmdd_text(start_time)
    today = _today_yyyymmdd()
    if not start_date:
        return today
    trade_latest = _latest_trade_calendar_day(start_date, today) or today
    stock_latest = _probe_latest_stock_data_date(start_date, trade_latest)
    if stock_latest:
        return _min_yyyymmdd_text(trade_latest, stock_latest) or trade_latest
    return trade_latest


def _resolve_effective_backtest_window(
    strat_cfg: dict[str, Any] | None = None,
    date_overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    strat_cfg = strat_cfg or {}
    date_overrides = date_overrides or {}
    start_time = _normalize_yyyymmdd_text(
        date_overrides.get("start_time")
        or strat_cfg.get("backtest.start_time")
        or _WEB_BACKTEST_START
    )
    requested_end = _normalize_yyyymmdd_text(
        date_overrides.get("end_time")
        or strat_cfg.get("backtest.end_time")
        or _WEB_BACKTEST_END
    )
    latest_end = _resolve_latest_backtest_end_time(start_time)
    effective_end = _min_yyyymmdd_text(requested_end, latest_end) or requested_end or latest_end
    return {"start_time": start_time, "end_time": effective_end}


def _module_basename(module: str) -> str:
    return module.rsplit(".", 1)[-1] if "." in module else module


def _is_local_strategy_module(module: Any) -> bool:
    text = str(module or "").strip()
    return bool(text) and ("/" in text or "\\" in text or text.lower().endswith(".py"))


def _is_canonical_strategy_module(module: Any) -> bool:
    """Return whether ``module`` names a repository-owned strategy module."""
    text = str(module or "").strip()
    return text.startswith(_CANONICAL_STRATEGY_MODULE_PREFIX)


def _resolve_library_strategy_file(directory: Path, module: Any) -> Path | None:
    """Resolve either a Web-local upload or a repository-owned strategy module.

    Web-local paths are deliberately confined to ``directory``.  Dotted
    modules are a separate ownership domain and may only point into the
    repository's ``strategy_library`` package; arbitrary importable modules
    are never accepted as strategy sources.
    """
    text = str(module or "").strip()
    if _is_local_strategy_module(text):
        candidate = Path(text)
        if candidate.is_absolute():
            return None
        try:
            base = directory.resolve()
            resolved = (directory / candidate).resolve()
            resolved.relative_to(base)
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved if resolved.is_file() else None
    if _is_canonical_strategy_module(text):
        return _resolve_module_file(text)
    return None


def _library_strategy_source_issue(directory: Path, module: Any) -> str:
    text = str(module or "").strip()
    if not text:
        return "missing_strategy_module"
    if not (_is_local_strategy_module(text) or _is_canonical_strategy_module(text)):
        return "external_module_reference"
    if _resolve_library_strategy_file(directory, text) is None:
        return "strategy_file_not_found"
    return ""


def _local_source_file_name(directory: Path, source_path: Path | None) -> str | None:
    """Return the stable source identifier used by cache metadata.

    Uploaded strategies use a path relative to their Web directory.  Canonical
    strategies use a path relative to the repository root so every cache and
    description points at the same authoritative file.
    """
    if source_path is None:
        return None
    try:
        return source_path.resolve().relative_to(directory.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        try:
            return source_path.resolve().relative_to(_project_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return source_path.name


def _source_file_signature(directory: Path, source_path: Path | None) -> dict[str, Any]:
    if source_path is None or not source_path.is_file():
        return {}
    try:
        data = source_path.read_bytes()
    except OSError:
        return {}
    return {
        "file": _local_source_file_name(directory, source_path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _stable_payload_hash(payload: Any) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short_hash(value: Any, length: int = 12) -> str:
    text = str(value or "").strip()
    return text[:length] if text else ""


def _expected_source_signature(directory: Path | None, strat_cfg: dict[str, Any]) -> dict[str, Any]:
    if directory is None:
        return {}
    module = strat_cfg.get("strategy.module")
    return _source_file_signature(
        directory,
        _resolve_library_strategy_file(directory, module),
    )


def _normalize_module_id(module: Any) -> str:
    text = str(module or "").strip().replace("\\", ".").replace("/", ".")
    return ".".join(part for part in text.split(".") if part)


def _pretty_name(value: str) -> str:
    tokens = re.split(r"[_\-\s]+", value.strip())
    output: list[str] = []
    for token in tokens:
        if not token:
            continue
        if token.isdigit() or token.isupper():
            output.append(token)
        elif len(token) <= 3 and token.isalpha():
            output.append(token.upper())
        else:
            output.append(token.capitalize())
    return " ".join(output) or value


def _family_info(module: str) -> tuple[str, str]:
    if _is_local_strategy_module(module):
        return _LOCAL_FAMILY
    for prefix, family, label in _FAMILY_SPECS:
        if module.startswith(prefix):
            return family, label
    return "unknown", "未知"


def _resolve_module_file(module: str) -> Path | None:
    module = str(module or "").strip()
    if (
        not module
        or not module.startswith(_CANONICAL_STRATEGY_MODULE_PREFIX)
        or ".." in module
        or "/" in module
        or "\\" in module
    ):
        return None
    parts = module.split(".")
    if any(
        not part
        or part.startswith("_")
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part) is None
        for part in parts
    ):
        return None
    project_root = _project_root.resolve()
    path = _project_root.joinpath(*parts).with_suffix(".py")
    try:
        resolved = path.resolve()
        resolved.relative_to(project_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _relative_source_path(source_path: Path | None) -> str | None:
    if source_path is None:
        return None
    try:
        return source_path.resolve().relative_to(_project_root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return str(source_path)


def _description_result(
    status: str,
    diagnostics: list[str],
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "manifest_file": _STRATEGY_DESCRIPTION_MANIFEST_NAME,
        "diagnostics": diagnostics,
        "manifest": manifest,
    }


def _validate_profile(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["profile 必须是对象"]
    failures: list[str] = []
    for field in _DESCRIPTION_PROFILE_FIELDS:
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(f"profile.{field} 必须是非空字符串")
    return failures


def _validate_runtime(runtime: Any) -> list[str]:
    if not isinstance(runtime, dict):
        return ["runtime 必须是对象"]
    failures: list[str] = []
    for field in ("top_n", "portfolio_num_j", "portfolio_num_i"):
        value = runtime.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            failures.append(f"runtime.{field} 必须是正整数")
    for field in ("use_index_timing", "use_board_timing", "require_board_member"):
        value = runtime.get(field)
        if value is not None and not isinstance(value, bool):
            failures.append(f"runtime.{field} 必须是布尔值")
    risk_control = runtime.get("risk_control")
    if risk_control is not None and not isinstance(risk_control, dict):
        failures.append("runtime.risk_control 必须是对象或 null")
    return failures


def _validate_investor_explanation(explanation: Any) -> list[str]:
    if not isinstance(explanation, dict):
        return ["investor_explanation 必须是对象"]
    failures: list[str] = []
    for side in ("buy", "sell"):
        section = explanation.get(side)
        if not isinstance(section, dict):
            failures.append(f"investor_explanation.{side} 必须是对象")
            continue
        if not isinstance(section.get("summary"), str) or not section["summary"].strip():
            failures.append(f"investor_explanation.{side}.summary 必须是非空字符串")
        groups = section.get("groups")
        if not isinstance(groups, list) or not groups:
            failures.append(f"investor_explanation.{side}.groups 必须是非空数组")
            continue
        for group_index, group in enumerate(groups):
            prefix = f"investor_explanation.{side}.groups[{group_index}]"
            if not isinstance(group, dict):
                failures.append(f"{prefix} 必须是对象")
                continue
            if not isinstance(group.get("title"), str) or not group["title"].strip():
                failures.append(f"{prefix}.title 必须是非空字符串")
            items = group.get("items")
            if not isinstance(items, list) or not items:
                failures.append(f"{prefix}.items 必须是非空数组")
                continue
            for item_index, item in enumerate(items):
                item_prefix = f"{prefix}.items[{item_index}]"
                if not isinstance(item, dict):
                    failures.append(f"{item_prefix} 必须是对象")
                    continue
                for field in ("title", "description"):
                    value = item.get(field)
                    if not isinstance(value, str) or not value.strip():
                        failures.append(f"{item_prefix}.{field} 必须是非空字符串")
    return failures


def _validate_indicators(indicators: Any) -> list[str]:
    if indicators is None:
        return []
    if not isinstance(indicators, list):
        return ["indicators 必须是数组"]
    failures: list[str] = []
    for index, indicator in enumerate(indicators):
        prefix = f"indicators[{index}]"
        if not isinstance(indicator, dict):
            failures.append(f"{prefix} 必须是对象")
            continue
        if not isinstance(indicator.get("name"), str) or not indicator["name"].strip():
            failures.append(f"{prefix}.name 必须是非空字符串")
        formula = indicator.get("formula")
        valid_formula = isinstance(formula, str) and bool(formula.strip())
        valid_formula = valid_formula or (
            isinstance(formula, list)
            and bool(formula)
            and all(isinstance(line, str) and line.strip() for line in formula)
        )
        if not valid_formula:
            failures.append(f"{prefix}.formula 必须是非空字符串或字符串数组")
    return failures


def _read_strategy_description_manifest(
    directory: Path,
    source_path: Path | None,
) -> dict[str, Any]:
    """Read one LLM-authored description and verify its exact source binding."""
    manifest_path = directory / _STRATEGY_DESCRIPTION_MANIFEST_NAME
    if not manifest_path.is_file():
        return _description_result(
            "missing",
            [f"缺少 {_STRATEGY_DESCRIPTION_MANIFEST_NAME}"],
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _description_result(
            "invalid",
            [f"{_STRATEGY_DESCRIPTION_MANIFEST_NAME} JSON 格式无效：第 {exc.lineno} 行"],
        )
    except (OSError, UnicodeError) as exc:
        return _description_result(
            "unavailable",
            [f"无法读取 {_STRATEGY_DESCRIPTION_MANIFEST_NAME}：{exc}"],
        )
    if not isinstance(raw, dict):
        return _description_result("invalid", ["策略描述顶层必须是对象"])

    failures: list[str] = []
    if raw.get("schema_version") != _STRATEGY_DESCRIPTION_SCHEMA_VERSION:
        failures.append(
            f"schema_version 必须为 {_STRATEGY_DESCRIPTION_SCHEMA_VERSION}"
        )
    authoring = raw.get("authoring")
    if not isinstance(authoring, dict):
        failures.append("authoring 必须是对象")
    elif authoring.get("method") != "llm_source_review":
        failures.append("authoring.method 必须为 llm_source_review")
    failures.extend(_validate_profile(raw.get("profile")))
    failures.extend(_validate_runtime(raw.get("runtime")))
    failures.extend(_validate_investor_explanation(raw.get("investor_explanation")))
    failures.extend(_validate_indicators(raw.get("indicators")))
    for side in ("buy", "sell"):
        if not isinstance(raw.get(side), (list, dict)) or not raw[side]:
            failures.append(f"{side} 必须是非空数组或对象")

    source = raw.get("source")
    if not isinstance(source, dict):
        failures.append("source 必须是对象")
        source = {}
    declared_file = str(source.get("file") or "").strip().replace("\\", "/")
    declared_sha256 = str(source.get("sha256") or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", declared_sha256):
        failures.append("source.sha256 必须是 64 位小写十六进制摘要")
    expected_file = _local_source_file_name(directory, source_path)
    if source_path is None or expected_file is None:
        failures.append("策略源码不存在，无法绑定描述")
    elif declared_file != expected_file:
        failures.append(
            f"source.file={declared_file or '<empty>'} 与策略文件 {expected_file} 不一致"
        )
    if failures:
        return _description_result("invalid", failures)

    try:
        actual_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    except OSError as exc:
        return _description_result("unavailable", [f"无法读取策略源码：{exc}"])
    if actual_sha256 != declared_sha256:
        return _description_result(
            "stale",
            ["策略描述的 source.sha256 与当前策略源码不一致"],
        )
    return _description_result("verified", [], manifest=raw)


def _build_strategy_profile(
    *,
    directory: Path,
    notes: dict[str, str],
    source_path: Path | None,
    fallback_title: str,
) -> dict[str, Any]:
    description = _read_strategy_description_manifest(directory, source_path)
    status = str(description["status"])
    manifest = description.get("manifest")
    display_title = str(notes.get("display_title") or "").strip()

    if isinstance(manifest, dict):
        profile = manifest["profile"]
        return {
            "title": str(profile["title"]).strip(),
            "display_title": display_title,
            "summary": str(profile["summary"]).strip(),
            "buy_logic": str(profile["buy_logic"]).strip(),
            "sell_logic": str(profile["sell_logic"]).strip(),
            "position_text": str(profile["position_text"]).strip(),
            "style_label": str(profile["style_label"]).strip(),
            "description_status": status,
            "description_diagnostics": [],
            "strategy_description": manifest,
        }

    status_text = _DESCRIPTION_STATUS_TEXT.get(status, "策略描述不可用")
    return {
        "title": fallback_title,
        "display_title": display_title,
        "summary": status_text,
        "buy_logic": "",
        "sell_logic": "",
        "position_text": "-",
        "style_label": "未声明",
        "description_status": status,
        "description_diagnostics": list(description.get("diagnostics") or []),
        "strategy_description": None,
    }
