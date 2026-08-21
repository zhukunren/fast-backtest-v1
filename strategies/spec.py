"""Canonical strategy metadata, runtime, risk, and advanced contracts."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any

from core.constants import OHLCV_COLS, RISK_COL_NEXT_UP_LIMIT_OK, SIGNAL_COLS_ALL
from core.exceptions import ConfigurationError, StrategyError

from .advanced import (
    ADVANCED_CONTRACT_VERSION,
    AdvancedStrategySpec,
    AuxiliaryDataSpec,
    BoardTimingSpec,
    CrossSectionTransformSpec,
    IndexTimingSpec,
    PortfolioConstructionSpec,
    PositionPolicySpec,
    ReferenceSeriesSpec,
    StrategyParameterSpec,
    resolve_strategy_parameters,
)


COMPLETE_SPEC_SCHEMA_VERSION = "3.0"


@dataclass(frozen=True)
class MarketRankSpec:
    feature_col: str
    rank_col: str
    pct_col: str
    count_col: str
    ascending: bool

    def to_dict(self) -> dict:
        return {
            "feature_col": str(self.feature_col),
            "rank_col": str(self.rank_col),
            "pct_col": str(self.pct_col),
            "count_col": str(self.count_col),
            "ascending": bool(self.ascending),
        }


@dataclass(frozen=True)
class MarketAggregateSpec:
    source_col: str
    output_col: str
    agg: str

    def to_dict(self) -> dict:
        return {
            "source_col": str(self.source_col),
            "output_col": str(self.output_col),
            "agg": str(self.agg),
        }


@dataclass(frozen=True)
class StrategySpec:
    top_n: int
    sort_spec: tuple[tuple[str, bool], ...]  # (col, ascending), required
    price_columns: tuple[str, ...]
    use_index_timing: bool
    use_board_timing: bool
    board_index_codes: tuple[str, ...]  # 支持多个板块
    require_board_member: bool
    portfolio_num_j: int
    portfolio_num_i: int
    use_market_features: bool = False
    feature_scope: str = "stock"
    market_rank_specs: tuple[MarketRankSpec, ...] = ()
    market_aggregate_specs: tuple[MarketAggregateSpec, ...] = ()
    market_feature_passthrough_cols: tuple[str, ...] = ()
    name: str = ""
    hypothesis: str = ""
    required_fields: tuple[str, ...] = ()
    universe_assumption: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    expected_market_regimes: tuple[str, ...] = ()
    runtime_parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "top_n": int(self.top_n),
            "sort_spec": [{"col": c, "ascending": bool(a)} for c, a in self.sort_spec],
            "price_columns": list(self.price_columns),
            "use_index_timing": bool(self.use_index_timing),
            "use_board_timing": bool(self.use_board_timing),
            "board_index_codes": list(self.board_index_codes),
            "require_board_member": bool(self.require_board_member),
            "portfolio_num_j": int(self.portfolio_num_j),
            "portfolio_num_i": int(self.portfolio_num_i),
            "use_market_features": bool(self.use_market_features),
            "feature_scope": str(self.feature_scope),
            "market_rank_specs": [item.to_dict() for item in self.market_rank_specs],
            "market_aggregate_specs": [item.to_dict() for item in self.market_aggregate_specs],
            "market_feature_passthrough_cols": list(self.market_feature_passthrough_cols),
            "name": str(self.name),
            "hypothesis": str(self.hypothesis),
            "required_fields": list(self.required_fields),
            "universe_assumption": dict(self.universe_assumption),
            "validation": dict(self.validation),
            "expected_market_regimes": list(self.expected_market_regimes),
            "runtime_parameters": dict(self.runtime_parameters),
        }


@dataclass(frozen=True)
class RiskControlSpec:
    enable_nv: bool
    enable_dd: bool
    warn_dd: float
    danger_dd: float
    lock_dd: float
    nv_warn: float
    nv_danger: float
    nv_clear: float
    nv_ratios: tuple[float, float, float, float]
    dd_ratios: tuple[float, float, float, float]

    @property
    def enabled(self) -> bool:
        return bool(self.enable_nv) or bool(self.enable_dd)

    def to_dict(self) -> dict:
        return {
            "enable_nv": bool(self.enable_nv),
            "enable_dd": bool(self.enable_dd),
            "warn_dd": float(self.warn_dd),
            "danger_dd": float(self.danger_dd),
            "lock_dd": float(self.lock_dd),
            "nv_warn": float(self.nv_warn),
            "nv_danger": float(self.nv_danger),
            "nv_clear": float(self.nv_clear),
            "nv_ratios": list(self.nv_ratios),
            "dd_ratios": list(self.dd_ratios),
        }


def _require_bool(value: Any, *, field: str) -> bool:
    if value is None:
        raise ConfigurationError(f"{field} 不能为空（需要 bool 或可解析的字符串）")
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigurationError(f"{field} 无法解析为 bool: {value!r} (支持 1/0, true/false, yes/no, on/off)")


def _require_int(value: Any, *, field: str, min_value: int = 1) -> int:
    if value is None:
        raise ConfigurationError(f"{field} 不能为空（需要 int 或可解析的字符串）")
    try:
        v = int(str(value).strip())
    except Exception:
        raise ConfigurationError(f"{field} 无法解析为 int: {value!r}") from None
    if v < min_value:
        raise ConfigurationError(f"{field} 必须 >= {min_value}，实际为 {v}")
    return v


def _optional_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    return _require_bool(value, field=field)


def _optional_string(value: Any, *, field: str, default: str) -> str:
    if value is None:
        return default
    out = str(value).strip()
    if not out:
        raise ConfigurationError(f"{field} 不能为空字符串。")
    return out


def _optional_float(value: Any, *, field: str, default: float) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except Exception:
        raise ConfigurationError(f"{field} 无法解析为 float: {value!r}") from None


def _parse_ratio_tuple(value: Any, *, field: str, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if value is None:
        return default
    if isinstance(value, str):
        parts = [x.strip() for x in value.split(",") if x.strip()]
        value = parts
    if isinstance(value, (list, tuple)):
        if len(value) != 4:
            raise ConfigurationError(f"{field} 需要 4 个数值，实际为 {len(value)}: {value!r}")
        try:
            return tuple(float(x) for x in value)  # type: ignore[return-value]
        except Exception:
            raise ConfigurationError(f"{field} 无法解析为 float 列表: {value!r}") from None
    raise ConfigurationError(f"{field} 类型不支持: {type(value).__name__} (支持 list/tuple/逗号分隔字符串)")


def parse_sort_spec(raw: Any) -> tuple[tuple[str, bool], ...]:
    """
    支持的格式：
    - "-a, b, +c"
    - ["-a", "b", "+c"]
    - [("a", False), ("b", True)]
    """
    if raw is None:
        raise ConfigurationError("sort_spec 不能为空（需要至少一个排序字段）")

    tokens: list[Any]
    if isinstance(raw, str):
        tokens = [x.strip() for x in str(raw).split(",")]
    elif isinstance(raw, (list, tuple)):
        tokens = list(raw)
    else:
        raise ConfigurationError(f"sort_spec 类型不支持: {type(raw).__name__} (支持 str/list/tuple)")

    out: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def _add(col: Any, asc: Any) -> None:
        if col is None:
            return
        name = str(col).strip()
        if not name:
            return
        if name.lower() == "trade_date":
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        out.append((name, bool(asc)))

    for tok in tokens:
        if tok is None:
            continue

        # tuple/list: (col, ascending)
        if isinstance(tok, (list, tuple)) and len(tok) == 2:
            _add(tok[0], _require_bool(tok[1], field="sort_spec[].ascending"))
            continue

        s = str(tok).strip()
        if not s:
            continue
        ascending = True
        if s.startswith("-"):
            ascending = False
            s = s[1:].strip()
        elif s.startswith("+"):
            s = s[1:].strip()
        if not s:
            continue
        _add(s, ascending)

    if not out:
        raise ConfigurationError("sort_spec 不能为空（需要至少一个排序字段）")
    return tuple(out)


def parse_price_columns(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        tokens = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        tokens = [str(item).strip() for item in raw]
    else:
        raise ConfigurationError(
            f"price_columns 类型不支持: {type(raw).__name__} (支持 str/list/tuple/set)"
        )
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token:
            continue
        name = str(token).strip()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return tuple(out)


def parse_string_columns(raw: Any, *, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        tokens = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        tokens = [str(item).strip() for item in raw]
    else:
        raise ConfigurationError(
            f"{field} 类型不支持: {type(raw).__name__} (支持 str/list/tuple/set)"
        )
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token:
            continue
        key = str(token).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(str(token))
    return tuple(out)


def _normalize_json_value(value: Any, *, field: str) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ConfigurationError(f"{field} 只允许非空字符串 key。")
            out[str(key)] = _normalize_json_value(item, field=f"{field}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item, field=f"{field}[]") for item in value]
    raise ConfigurationError(
        f"{field} 包含不支持 JSON 序列化的值: {type(value).__name__}"
    )


def _optional_json_object(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"{field} 类型不支持: {type(value).__name__} (需要 dict)")
    normalized = _normalize_json_value(value, field=field)
    if not isinstance(normalized, dict):
        raise ConfigurationError(f"{field} 规范化结果必须是 dict")
    return normalized


def _first_present(meta: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in meta:
            return meta.get(key)
    return None


def _required_string(value: Any, *, field: str) -> str:
    if value is None:
        raise ConfigurationError(f"{field} 不能为空字符串。")
    out = str(value).strip()
    if not out:
        raise ConfigurationError(f"{field} 不能为空字符串。")
    return out


def _parse_market_rank_spec_item(raw: Any, *, index: int) -> MarketRankSpec:
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"market_rank_specs[{index}] 类型不支持: {type(raw).__name__} (需要 dict)"
        )
    prefix = f"market_rank_specs[{index}]"
    required_keys = ("feature_col", "rank_col", "pct_col", "count_col", "ascending")
    missing = [key for key in required_keys if key not in raw]
    if missing:
        raise ConfigurationError(f"{prefix} 缺少字段: {missing}")
    return MarketRankSpec(
        feature_col=_required_string(raw.get("feature_col"), field=f"{prefix}.feature_col"),
        rank_col=_required_string(raw.get("rank_col"), field=f"{prefix}.rank_col"),
        pct_col=_required_string(raw.get("pct_col"), field=f"{prefix}.pct_col"),
        count_col=_required_string(raw.get("count_col"), field=f"{prefix}.count_col"),
        ascending=_require_bool(raw.get("ascending"), field=f"{prefix}.ascending"),
    )


_MARKET_AGGREGATE_AGGS = {"mean", "sum", "median", "count", "min", "max", "std"}
_MARKET_FEATURE_INTERNAL_COLUMNS = {
    "__market_rank_row_order__",
    "__risk_portfolio_position_ratio_input__",
}
_MARKET_FEATURE_RESERVED_COLUMNS = frozenset(
    str(column).casefold()
    for column in (
        *OHLCV_COLS,
        *SIGNAL_COLS_ALL,
        RISK_COL_NEXT_UP_LIMIT_OK,
        *_MARKET_FEATURE_INTERNAL_COLUMNS,
    )
)


def _parse_market_aggregate_spec_item(raw: Any, *, index: int) -> MarketAggregateSpec:
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"market_aggregate_specs[{index}] 类型不支持: {type(raw).__name__} (需要 dict)"
        )
    prefix = f"market_aggregate_specs[{index}]"
    required_keys = ("source_col", "output_col", "agg")
    missing = [key for key in required_keys if key not in raw]
    if missing:
        raise ConfigurationError(f"{prefix} 缺少字段: {missing}")
    agg = _required_string(raw.get("agg"), field=f"{prefix}.agg").casefold()
    if agg not in _MARKET_AGGREGATE_AGGS:
        allowed = ", ".join(sorted(_MARKET_AGGREGATE_AGGS))
        raise ConfigurationError(f"{prefix}.agg={agg!r} 不支持；可选: {allowed}")
    return MarketAggregateSpec(
        source_col=_required_string(raw.get("source_col"), field=f"{prefix}.source_col"),
        output_col=_required_string(raw.get("output_col"), field=f"{prefix}.output_col"),
        agg=agg,
    )


def parse_market_aggregate_specs(raw: Any) -> tuple[MarketAggregateSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigurationError(
            f"market_aggregate_specs 类型不支持: {type(raw).__name__} (需要 list/tuple)"
        )
    specs = tuple(_parse_market_aggregate_spec_item(item, index=idx) for idx, item in enumerate(raw))
    seen_output_cols: set[str] = set()
    for spec in specs:
        key = spec.output_col.casefold()
        if key in seen_output_cols:
            raise ConfigurationError(f"market_aggregate_specs 输出列重复: {spec.output_col!r}")
        seen_output_cols.add(key)
    return specs


def parse_market_rank_specs(raw: Any, *, use_market_features: bool) -> tuple[MarketRankSpec, ...]:
    if raw is None:
        if use_market_features:
            raise ConfigurationError("use_market_features=True 时必须配置非空 market_rank_specs。")
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigurationError(
            f"market_rank_specs 类型不支持: {type(raw).__name__} (需要 list/tuple)"
        )
    specs = tuple(_parse_market_rank_spec_item(item, index=idx) for idx, item in enumerate(raw))
    if use_market_features and not specs:
        raise ConfigurationError("use_market_features=True 时必须配置非空 market_rank_specs。")
    seen_output_cols: set[str] = set()
    feature_col_keys = {spec.feature_col.casefold() for spec in specs}
    for spec in specs:
        for field_name, col in (
            ("rank_col", spec.rank_col),
            ("pct_col", spec.pct_col),
            ("count_col", spec.count_col),
        ):
            key = col.casefold()
            if key in feature_col_keys:
                raise ConfigurationError(
                    f"market_rank_specs {field_name} 不能与 feature_col 重名: {col!r}"
                )
            if key in seen_output_cols:
                raise ConfigurationError(f"market_rank_specs 输出列重复: {col!r}")
            seen_output_cols.add(key)
    return specs


def validate_market_feature_specs(
    *,
    use_market_features: bool,
    market_rank_specs: tuple[MarketRankSpec, ...],
    market_aggregate_specs: tuple[MarketAggregateSpec, ...],
    market_feature_passthrough_cols: tuple[str, ...] = (),
) -> None:
    declarations: list[tuple[str, str]] = []
    for index, rank_spec in enumerate(market_rank_specs):
        declarations.extend(
            (
                (f"market_rank_specs[{index}].feature_col", rank_spec.feature_col),
                (f"market_rank_specs[{index}].rank_col", rank_spec.rank_col),
                (f"market_rank_specs[{index}].pct_col", rank_spec.pct_col),
                (f"market_rank_specs[{index}].count_col", rank_spec.count_col),
            )
        )
    for index, aggregate_spec in enumerate(market_aggregate_specs):
        declarations.extend(
            (
                (f"market_aggregate_specs[{index}].source_col", aggregate_spec.source_col),
                (f"market_aggregate_specs[{index}].output_col", aggregate_spec.output_col),
            )
        )
    declarations.extend(
        (f"market_feature_passthrough_cols[{index}]", column)
        for index, column in enumerate(market_feature_passthrough_cols)
    )
    reserved = sorted(
        (
            f"{field}={column!r}"
            for field, column in declarations
            if column.casefold() in _MARKET_FEATURE_RESERVED_COLUMNS
            or (column.startswith("__") and column.endswith("__"))
        ),
        key=str.casefold,
    )
    if reserved:
        raise ConfigurationError(f"市场特征声明不能使用框架保留列: {reserved}")

    if use_market_features and not market_rank_specs and not market_aggregate_specs:
        raise ConfigurationError(
            "use_market_features=True 时必须配置非空 market_rank_specs 或 market_aggregate_specs。"
        )
    rank_feature_keys = {spec.feature_col.casefold() for spec in market_rank_specs}
    aggregate_source_keys = {spec.source_col.casefold() for spec in market_aggregate_specs}
    source_keys = rank_feature_keys | aggregate_source_keys
    output_cols: dict[str, str] = {}
    for rank_spec in market_rank_specs:
        for field_name, col in (
            ("rank_col", rank_spec.rank_col),
            ("pct_col", rank_spec.pct_col),
            ("count_col", rank_spec.count_col),
        ):
            key = col.casefold()
            if key in source_keys:
                raise ConfigurationError(
                    f"market_rank_specs {field_name} 不能与市场特征源列重名: {col!r}"
                )
            if key in output_cols:
                raise ConfigurationError(f"市场特征输出列重复: {col!r}")
            output_cols[key] = col
    for aggregate_spec in market_aggregate_specs:
        key = aggregate_spec.output_col.casefold()
        if key in source_keys:
            raise ConfigurationError(
                f"market_aggregate_specs output_col 不能与市场特征源列重名: {aggregate_spec.output_col!r}"
            )
        if key in output_cols:
            raise ConfigurationError(f"市场特征输出列重复: {aggregate_spec.output_col!r}")
        output_cols[key] = aggregate_spec.output_col
    passthrough_keys = {col.casefold() for col in market_feature_passthrough_cols}
    duplicate_passthrough = passthrough_keys & set(output_cols)
    if duplicate_passthrough:
        dup = sorted(duplicate_passthrough)
        raise ConfigurationError(f"market_feature_passthrough_cols 不能与市场特征输出列重名: {dup}")


def _contract_issue(*, severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _period_value(validation: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in validation:
            return validation.get(key)
    return None


def _period_is_valid(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    return all(str(item).strip() for item in value)


def validate_strategy_contract(spec: StrategySpec, *, strict: bool = False) -> dict[str, Any]:
    """
    Validate optional research contract fields.

    Runtime-required fields are already enforced while building ``StrategySpec``.
    Missing research fields stay warnings by default so old strategies remain
    loadable while new strategies can opt into a stronger declarative contract.
    """
    issues: list[dict[str, str]] = []

    if not str(spec.name or "").strip():
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_strategy_name",
                message="STRATEGY_META.name is recommended for run comparison and reports.",
            )
        )
    if not str(spec.hypothesis or "").strip():
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_hypothesis",
                message="STRATEGY_META.hypothesis is recommended for auditability.",
            )
        )
    if not spec.required_fields:
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_required_fields",
                message="STRATEGY_META.required_fields is recommended to document input data dependencies.",
            )
        )
    if not spec.universe_assumption:
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_universe_assumption",
                message="STRATEGY_META.universe_assumption is recommended to document stock-pool assumptions.",
            )
        )
    if not spec.expected_market_regimes:
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_expected_market_regimes",
                message="STRATEGY_META.expected_market_regime(s) is recommended to record expected market regimes.",
            )
        )

    validation = spec.validation
    if not validation:
        issues.append(
            _contract_issue(
                severity="warning",
                code="missing_validation_plan",
                message="STRATEGY_META.validation is recommended to document IS/OOS periods.",
            )
        )
    else:
        train_period = _period_value(
            validation,
            ("train_period", "in_sample_period", "is_period"),
        )
        test_period = _period_value(
            validation,
            ("test_period", "out_of_sample_period", "oos_period"),
        )
        if train_period is None:
            issues.append(
                _contract_issue(
                    severity="warning",
                    code="missing_train_period",
                    message="STRATEGY_META.validation should include train_period or in_sample_period.",
                )
            )
        elif not _period_is_valid(train_period):
            issues.append(
                _contract_issue(
                    severity="error",
                    code="invalid_train_period",
                    message="STRATEGY_META.validation train period must be a two-item sequence.",
                )
            )
        if test_period is None:
            issues.append(
                _contract_issue(
                    severity="warning",
                    code="missing_test_period",
                    message="STRATEGY_META.validation should include test_period or out_of_sample_period.",
                )
            )
        elif not _period_is_valid(test_period):
            issues.append(
                _contract_issue(
                    severity="error",
                    code="invalid_test_period",
                    message="STRATEGY_META.validation test period must be a two-item sequence.",
                )
            )

    has_errors = any(issue["severity"] == "error" for issue in issues)
    has_warnings = any(issue["severity"] == "warning" for issue in issues)
    return {
        "ok": (not has_errors) and ((not strict) or (not has_warnings)),
        "strict": bool(strict),
        "status": "fail" if has_errors or (strict and has_warnings) else ("warn" if has_warnings else "pass"),
        "issues": issues,
    }


def _collect_strategy_meta(module: Any) -> dict[str, Any]:
    """从模块收集策略元信息"""
    meta: dict[str, Any] = {}
    for name in ("STRATEGY_META", "STRATEGY_SPEC", "STRATEGY_CONFIG"):
        v = getattr(module, name, None)
        if isinstance(v, Mapping):
            meta.update(v)

    return meta


def get_strategy_meta(*, module: Any) -> Mapping[str, Any]:
    """Return one immutable merged view of all supported metadata declarations."""
    return MappingProxyType(_collect_strategy_meta(module))


def _collect_risk_meta(meta: dict[str, Any]) -> dict[str, Any]:
    risk: dict[str, Any] = {}
    for key in ("risk_control", "risk_config", "risk"):
        raw = meta.get(key)
        if isinstance(raw, dict):
            risk.update(raw)
    for k, v in meta.items():
        if not isinstance(k, str):
            continue
        if k.startswith("risk_") and k not in risk:
            risk[k] = v
    return risk


def _build_risk_config_from_meta(meta: dict[str, Any]) -> RiskControlSpec:
    risk = _collect_risk_meta(meta)

    enable_nv = _optional_bool(
        risk.get("enable_nv", risk.get("risk_nv_enable", risk.get("risk_nv_enabled"))),
        field="risk.enable_nv",
        default=False,
    )
    enable_dd = _optional_bool(
        risk.get("enable_dd", risk.get("risk_dd_enable", risk.get("risk_dd_enabled"))),
        field="risk.enable_dd",
        default=False,
    )

    warn_dd = _optional_float(risk.get("warn_dd", risk.get("risk_warn_dd")), field="risk.warn_dd", default=-0.10)
    danger_dd = _optional_float(risk.get("danger_dd", risk.get("risk_danger_dd")), field="risk.danger_dd", default=-0.20)
    lock_dd = _optional_float(risk.get("lock_dd", risk.get("risk_lock_dd")), field="risk.lock_dd", default=-0.30)
    nv_warn = _optional_float(
        risk.get("nv_warn", risk.get("nv_warning", risk.get("risk_nv_warn", risk.get("risk_nv_warning")))),
        field="risk.nv_warn",
        default=0.9,
    )
    nv_danger = _optional_float(
        risk.get("nv_danger", risk.get("risk_nv_danger")),
        field="risk.nv_danger",
        default=0.8,
    )
    nv_clear = _optional_float(
        risk.get("nv_clear", risk.get("risk_nv_clear")),
        field="risk.nv_clear",
        default=0.5,
    )

    nv_ratio_normal = _optional_float(
        risk.get("nv_ratio_normal", risk.get("risk_nv_ratio_normal")),
        field="risk.nv_ratio_normal",
        default=1.0,
    )
    nv_ratio_warning = _optional_float(
        risk.get("nv_ratio_warning", risk.get("risk_nv_ratio_warning")),
        field="risk.nv_ratio_warning",
        default=0.8,
    )
    nv_ratio_danger = _optional_float(
        risk.get("nv_ratio_danger", risk.get("risk_nv_ratio_danger")),
        field="risk.nv_ratio_danger",
        default=0.5,
    )
    nv_ratio_clear = _optional_float(
        risk.get("nv_ratio_clear", risk.get("risk_nv_ratio_clear")),
        field="risk.nv_ratio_clear",
        default=0.3,
    )
    dd_ratio_normal = _optional_float(
        risk.get("dd_ratio_normal", risk.get("risk_dd_ratio_normal")),
        field="risk.dd_ratio_normal",
        default=1.0,
    )
    dd_ratio_warning = _optional_float(
        risk.get("dd_ratio_warning", risk.get("risk_dd_ratio_warning")),
        field="risk.dd_ratio_warning",
        default=0.8,
    )
    dd_ratio_danger = _optional_float(
        risk.get("dd_ratio_danger", risk.get("risk_dd_ratio_danger")),
        field="risk.dd_ratio_danger",
        default=0.5,
    )
    dd_ratio_lock = _optional_float(
        risk.get("dd_ratio_lock", risk.get("risk_dd_ratio_lock")),
        field="risk.dd_ratio_lock",
        default=0.3,
    )

    nv_ratios = _parse_ratio_tuple(
        risk.get("nv_ratios", risk.get("risk_nv_ratios")),
        field="risk.nv_ratios",
        default=(nv_ratio_normal, nv_ratio_warning, nv_ratio_danger, nv_ratio_clear),
    )
    dd_ratios = _parse_ratio_tuple(
        risk.get("dd_ratios", risk.get("risk_dd_ratios")),
        field="risk.dd_ratios",
        default=(dd_ratio_normal, dd_ratio_warning, dd_ratio_danger, dd_ratio_lock),
    )

    return RiskControlSpec(
        enable_nv=enable_nv,
        enable_dd=enable_dd,
        warn_dd=warn_dd,
        danger_dd=danger_dd,
        lock_dd=lock_dd,
        nv_warn=nv_warn,
        nv_danger=nv_danger,
        nv_clear=nv_clear,
        nv_ratios=nv_ratios,
        dd_ratios=dd_ratios,
    )


def get_strategy_risk_config(*, module: Any) -> RiskControlSpec:
    """
    从策略模块读取风控配置（净值/回撤独立开关）。
    """
    return _build_risk_config_from_meta(_collect_strategy_meta(module))


def validate_board_timing_config(
    *,
    use_board_timing: bool,
    board_index_codes: tuple[str, ...],
    require_board_member: bool,
) -> None:
    if use_board_timing and (not require_board_member) and (not board_index_codes):
        raise ConfigurationError(
            "use_board_timing=True 且 require_board_member=False 时，"
            "必须显式配置 board_index_codes；"
            "否则会退化为“任一板块放行即全市场放行”的日级总开关。"
        )


def _build_strategy_spec_from_meta(meta: dict[str, Any]) -> StrategySpec:
    if not meta:
        raise StrategyError(
            "策略必须定义 `STRATEGY_META`（或 STRATEGY_SPEC/STRATEGY_CONFIG）字典，"
            "包含: sort_spec, portfolio_num_j, portfolio_num_i。"
        )
    removed_meta_keys = {
        "use_market_rank": "use_market_features",
        "market_feature_source_col": "market_rank_specs",
        "market_rank_feature_col": "market_rank_specs",
        "market_rank_passthrough_cols": "market_feature_passthrough_cols",
        "market_rank_col": "market_rank_specs",
        "market_rank_ascending": "market_rank_specs",
        "market_rank_output_col": "market_rank_specs",
        "market_rank_pct_col": "market_rank_specs",
        "market_rank_count_col": "market_rank_specs",
    }
    for old_key, new_key in removed_meta_keys.items():
        if old_key in meta:
            raise ConfigurationError(f"{old_key} 已移除；请改用 {new_key}。")

    missing = [
        k
        for k in ("sort_spec", "portfolio_num_j", "portfolio_num_i")
        if k not in meta
    ]
    if missing:
        raise StrategyError(f"STRATEGY_META 缺少字段: {missing}")

    num_j = _require_int(meta.get("portfolio_num_j"), field="portfolio_num_j", min_value=1)
    num_i = _require_int(meta.get("portfolio_num_i"), field="portfolio_num_i", min_value=1)
    top_n = num_j * num_i
    raw_top_n = meta.get("top_n")
    if raw_top_n is not None:
        top_n_val = _require_int(raw_top_n, field="top_n", min_value=1)
        if top_n_val != top_n:
            raise ConfigurationError(
                f"top_n 已由 portfolio_num_j*portfolio_num_i 推导，"
                f"无需显式配置（当前推导为 {top_n}，收到 {top_n_val}）"
            )
    sort_spec = parse_sort_spec(meta.get("sort_spec"))
    price_columns = parse_price_columns(meta.get("price_columns", meta.get("required_price_columns")))
    use_index_timing = _require_bool(meta.get("use_index_timing", False), field="use_index_timing")
    use_board_timing = _require_bool(meta.get("use_board_timing", False), field="use_board_timing")
    use_market_features = _optional_bool(meta.get("use_market_features"), field="use_market_features", default=False)
    feature_scope = _optional_string(
        meta.get("feature_scope"),
        field="feature_scope",
        default="stock",
    ).casefold()
    if feature_scope not in {"stock", "cross_section"}:
        raise ConfigurationError(
            "feature_scope 必须为 'stock' 或 'cross_section'。"
        )
    market_aggregate_specs = parse_market_aggregate_specs(meta.get("market_aggregate_specs"))
    market_rank_specs = parse_market_rank_specs(
        meta.get("market_rank_specs"),
        use_market_features=False,
    )
    market_feature_passthrough_cols = parse_string_columns(
        meta.get("market_feature_passthrough_cols"),
        field="market_feature_passthrough_cols",
    )
    validate_market_feature_specs(
        use_market_features=use_market_features,
        market_rank_specs=market_rank_specs,
        market_aggregate_specs=market_aggregate_specs,
        market_feature_passthrough_cols=market_feature_passthrough_cols,
    )
    board_raw = meta.get("board_index_code", meta.get("board_index_codes", ""))
    if isinstance(board_raw, str):
        board_token_list = [s.strip() for s in str(board_raw).split(",") if s.strip()]
    elif isinstance(board_raw, (list, tuple)):
        board_token_list = [str(s).strip() for s in board_raw if str(s).strip()]
    else:
        board_token_list = []
    board_tokens = tuple(dict.fromkeys(board_token_list))  # 去重保持顺序
    require_board_member = _require_bool(meta.get("require_board_member", bool(board_tokens)), field="require_board_member")
    validate_board_timing_config(
        use_board_timing=use_board_timing,
        board_index_codes=board_tokens,
        require_board_member=require_board_member,
    )
    name = _optional_string(
        _first_present(meta, ("name", "strategy_name")),
        field="name",
        default="",
    )
    hypothesis = _optional_string(
        _first_present(meta, ("hypothesis", "research_hypothesis")),
        field="hypothesis",
        default="",
    )
    required_fields = parse_string_columns(
        _first_present(meta, ("required_fields", "input_columns")),
        field="required_fields",
    )
    universe_assumption = _optional_json_object(
        meta.get("universe_assumption"),
        field="universe_assumption",
    )
    validation = _optional_json_object(
        _first_present(meta, ("validation", "validation_plan")),
        field="validation",
    )
    expected_market_regimes = parse_string_columns(
        _first_present(meta, ("expected_market_regimes", "expected_market_regime")),
        field="expected_market_regimes",
    )
    runtime_parameters = _optional_json_object(
        meta.get("runtime_parameters"),
        field="runtime_parameters",
    )

    return StrategySpec(
        top_n=top_n,
        sort_spec=sort_spec,
        price_columns=price_columns,
        use_index_timing=use_index_timing,
        use_board_timing=use_board_timing,
        board_index_codes=board_tokens,
        require_board_member=require_board_member,
        portfolio_num_j=num_j,
        portfolio_num_i=num_i,
        use_market_features=use_market_features,
        feature_scope=feature_scope,
        market_rank_specs=market_rank_specs,
        market_aggregate_specs=market_aggregate_specs,
        market_feature_passthrough_cols=market_feature_passthrough_cols,
        name=name,
        hypothesis=hypothesis,
        required_fields=required_fields,
        universe_assumption=universe_assumption,
        validation=validation,
        expected_market_regimes=expected_market_regimes,
        runtime_parameters=runtime_parameters,
    )


def _build_strategy_spec_from_module(module: Any) -> StrategySpec:
    return _build_strategy_spec_from_meta(_collect_strategy_meta(module))


def get_strategy_spec(*, module: Any) -> StrategySpec:
    return _build_strategy_spec_from_module(module)


def get_strategy_runtime_specs(*, module: Any) -> tuple[StrategySpec, RiskControlSpec]:
    meta = _collect_strategy_meta(module)
    return _build_strategy_spec_from_meta(meta), _build_risk_config_from_meta(meta)


def get_advanced_strategy_spec(*, module: Any) -> AdvancedStrategySpec:
    return AdvancedStrategySpec.from_meta(_collect_strategy_meta(module))


def resolve_complete_strategy_parameters(
    strategy_spec: StrategySpec,
    advanced_spec: AdvancedStrategySpec,
) -> Mapping[str, Any]:
    overrides = dict(strategy_spec.runtime_parameters or {})
    if not advanced_spec.parameters:
        return MappingProxyType(overrides)
    return resolve_strategy_parameters(advanced_spec.parameters, overrides)


def get_complete_strategy_specs(
    *,
    module: Any,
) -> tuple[StrategySpec, RiskControlSpec, AdvancedStrategySpec]:
    complete = get_complete_strategy_spec(module=module)
    return complete.strategy, complete.risk_control, complete.advanced


@dataclass(frozen=True, slots=True)
class CompleteStrategySpec:
    strategy: StrategySpec
    risk_control: RiskControlSpec
    advanced: AdvancedStrategySpec
    resolved_parameters: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    schema_version: str = COMPLETE_SPEC_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy": self.strategy.to_dict(),
            "risk_control": self.risk_control.to_dict(),
            "advanced": self.advanced.to_dict(),
            "resolved_parameters": dict(self.resolved_parameters),
        }

    @property
    def contract_hash(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def get_complete_strategy_spec(*, module: Any) -> CompleteStrategySpec:
    meta = _collect_strategy_meta(module)
    strategy = _build_strategy_spec_from_meta(meta)
    risk_control = _build_risk_config_from_meta(meta)
    advanced = AdvancedStrategySpec.from_meta(meta)
    return CompleteStrategySpec(
        strategy=strategy,
        risk_control=risk_control,
        advanced=advanced,
        resolved_parameters=resolve_complete_strategy_parameters(strategy, advanced),
    )


def _write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_complete_strategy_spec(path: str | Path, spec: CompleteStrategySpec) -> None:
    """Write the canonical strategy contract, including its content hash."""
    if not isinstance(spec, CompleteStrategySpec):
        raise TypeError("spec must be CompleteStrategySpec")
    payload = spec.to_dict()
    payload["contract_hash"] = spec.contract_hash
    _write_json_atomic(path, payload)


__all__ = [
    "ADVANCED_CONTRACT_VERSION",
    "AdvancedStrategySpec",
    "AuxiliaryDataSpec",
    "BoardTimingSpec",
    "COMPLETE_SPEC_SCHEMA_VERSION",
    "CompleteStrategySpec",
    "CrossSectionTransformSpec",
    "IndexTimingSpec",
    "MarketAggregateSpec",
    "MarketRankSpec",
    "PortfolioConstructionSpec",
    "PositionPolicySpec",
    "ReferenceSeriesSpec",
    "RiskControlSpec",
    "StrategyParameterSpec",
    "StrategySpec",
    "get_advanced_strategy_spec",
    "get_complete_strategy_spec",
    "get_complete_strategy_specs",
    "get_strategy_meta",
    "get_strategy_risk_config",
    "get_strategy_runtime_specs",
    "get_strategy_spec",
    "parse_market_aggregate_specs",
    "parse_market_rank_specs",
    "parse_price_columns",
    "parse_sort_spec",
    "parse_string_columns",
    "resolve_complete_strategy_parameters",
    "resolve_strategy_parameters",
    "validate_board_timing_config",
    "validate_market_feature_specs",
    "validate_strategy_contract",
    "write_complete_strategy_spec",
]
