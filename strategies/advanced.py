"""Declarative contracts for advanced daily-strategy capabilities.

These objects describe strategy intent. Stateful execution and point-in-time
data fulfillment remain owned by the backtest and data layers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from numbers import Integral, Real
from types import MappingProxyType
from typing import Any, Mapping


ADVANCED_CONTRACT_VERSION = "2.0"
_PARAMETER_KINDS = frozenset({"bool", "choice", "float", "int", "str"})
_REBALANCE_FREQUENCIES = frozenset({"calendar", "daily", "month_end", "month_start", "weekly"})
_CROSS_SECTION_METHODS = frozenset(
    {"neutralize", "pct_rank", "rank", "winsorize", "winsorized_zscore", "zscore"}
)
_JOIN_POLICIES = frozenset({"asof_backward", "exact"})
_DEFAULT_INDEX_TIMING_COLUMNS = (
    "trade_date",
    "close",
    "volume",
    "amount",
    "high",
    "low",
    "open",
)
_DEFAULT_BOARD_TIMING_COLUMNS = (
    "trade_date",
    "close",
    "volume",
    "open",
    "high",
    "low",
)
_RESERVED_OUTPUT_FIELDS = frozenset(
    {
        "trade_date",
        "stock_code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_signal",
        "buy_signal_raw",
        "direction",
        "sell_signal",
        "sell_signal_raw",
        "next_up_limit_ok",
    }
)


def _require_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _string_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: tuple[object, ...]
    if isinstance(value, str):
        raw = tuple(value.split(","))
    elif isinstance(value, (list, tuple)):
        raw = tuple(value)
    else:
        raise ValueError(f"{field_name} must be a string or sequence")
    result = tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
    return result


def _finite_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric, not bool")
    try:
        if isinstance(value, str):
            parsed = float(value)
        elif isinstance(value, Real):
            parsed = float(value)
        else:
            raise TypeError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _optional_finite_float(value: object, *, field_name: str) -> float | None:
    return None if value is None else _finite_float(value, field_name=field_name)


def _non_negative_int(value: object, *, field_name: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{field_name} must be a non-negative integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _validate_output_name(value: str, *, field_name: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError(f"{field_name} must not be empty")
    if name in _RESERVED_OUTPUT_FIELDS or name.startswith("__"):
        raise ValueError(f"{field_name} is framework-owned: {name!r}")
    return name


@dataclass(frozen=True, slots=True)
class PositionPolicySpec:
    min_hold_days: int = 0
    max_hold_days: int | None = None
    stop_loss_return: float | None = None
    take_profit_return: float | None = None
    trailing_drawdown: float | None = None
    cooldown_after_exit_days: int = 0

    @classmethod
    def from_value(cls, value: object) -> PositionPolicySpec:
        raw = _require_mapping(value, field_name="position_policy")
        max_hold_raw = raw.get("max_hold_days")
        spec = cls(
            min_hold_days=_non_negative_int(raw.get("min_hold_days"), field_name="min_hold_days"),
            max_hold_days=(
                None
                if max_hold_raw is None
                else _non_negative_int(max_hold_raw, field_name="max_hold_days")
            ),
            stop_loss_return=_optional_finite_float(
                raw.get("stop_loss_return"), field_name="stop_loss_return"
            ),
            take_profit_return=_optional_finite_float(
                raw.get("take_profit_return"), field_name="take_profit_return"
            ),
            trailing_drawdown=_optional_finite_float(
                raw.get("trailing_drawdown"), field_name="trailing_drawdown"
            ),
            cooldown_after_exit_days=_non_negative_int(
                raw.get("cooldown_after_exit_days"),
                field_name="cooldown_after_exit_days",
            ),
        )
        if spec.max_hold_days is not None and spec.max_hold_days < spec.min_hold_days:
            raise ValueError("max_hold_days must be >= min_hold_days")
        if spec.stop_loss_return is not None and spec.stop_loss_return > 0:
            raise ValueError("stop_loss_return must be <= 0")
        if spec.take_profit_return is not None and spec.take_profit_return < 0:
            raise ValueError("take_profit_return must be >= 0")
        if spec.trailing_drawdown is not None and spec.trailing_drawdown > 0:
            raise ValueError("trailing_drawdown must be <= 0")
        return spec

    @property
    def enabled(self) -> bool:
        return any(
            (
                self.min_hold_days,
                self.max_hold_days is not None,
                self.stop_loss_return is not None,
                self.take_profit_return is not None,
                self.trailing_drawdown is not None,
                self.cooldown_after_exit_days,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_hold_days": self.min_hold_days,
            "max_hold_days": self.max_hold_days,
            "stop_loss_return": self.stop_loss_return,
            "take_profit_return": self.take_profit_return,
            "trailing_drawdown": self.trailing_drawdown,
            "cooldown_after_exit_days": self.cooldown_after_exit_days,
        }


@dataclass(frozen=True, slots=True)
class PortfolioConstructionSpec:
    mode: str = "top_n"
    rebalance_frequency: str = "daily"
    rebalance_dates: tuple[str, ...] = ()
    cash_buffer: float = 0.0
    max_single_weight: float | None = None
    max_turnover: float | None = None
    normalize_weights: bool = True

    @classmethod
    def from_value(cls, value: object) -> PortfolioConstructionSpec:
        raw = _require_mapping(value, field_name="portfolio_construction")
        frequency = str(raw.get("rebalance_frequency", "daily") or "daily").strip().casefold()
        if frequency not in _REBALANCE_FREQUENCIES:
            raise ValueError(
                "rebalance_frequency must be one of "
                f"{sorted(_REBALANCE_FREQUENCIES, key=str.casefold)}"
            )
        mode = str(raw.get("mode", "top_n") or "top_n").strip().casefold()
        if mode not in {"equal_weight", "fixed_weight", "inverse_volatility", "score_weight", "top_n"}:
            raise ValueError(f"unsupported portfolio_construction mode: {mode!r}")
        cash_buffer = _finite_float(
            raw.get("cash_buffer", 0.0) or 0.0,
            field_name="cash_buffer",
        )
        max_single_weight = _optional_finite_float(
            raw.get("max_single_weight"), field_name="max_single_weight"
        )
        max_turnover = _optional_finite_float(raw.get("max_turnover"), field_name="max_turnover")
        if not 0 <= cash_buffer < 1:
            raise ValueError("cash_buffer must be between 0 (inclusive) and 1 (exclusive)")
        for field_name, number in (
            ("max_single_weight", max_single_weight),
            ("max_turnover", max_turnover),
        ):
            if number is not None and not 0 <= number <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        normalize = raw.get("normalize_weights", True)
        if not isinstance(normalize, bool):
            raise ValueError("normalize_weights must be bool")
        return cls(
            mode=mode,
            rebalance_frequency=frequency,
            rebalance_dates=_string_tuple(raw.get("rebalance_dates"), field_name="rebalance_dates"),
            cash_buffer=cash_buffer,
            max_single_weight=max_single_weight,
            max_turnover=max_turnover,
            normalize_weights=normalize,
        )

    @property
    def requires_target_weight_engine(self) -> bool:
        return self.mode != "top_n"

    @property
    def enabled(self) -> bool:
        return any(
            (
                self.mode != "top_n",
                self.rebalance_frequency != "daily",
                bool(self.rebalance_dates),
                self.cash_buffer != 0.0,
                self.max_single_weight is not None,
                self.max_turnover is not None,
                not self.normalize_weights,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "rebalance_frequency": self.rebalance_frequency,
            "rebalance_dates": list(self.rebalance_dates),
            "cash_buffer": self.cash_buffer,
            "max_single_weight": self.max_single_weight,
            "max_turnover": self.max_turnover,
            "normalize_weights": self.normalize_weights,
        }


@dataclass(frozen=True, slots=True)
class AuxiliaryDataSpec:
    dataset: str
    fields: tuple[str, ...]
    key_columns: tuple[str, ...] = ("stock_code",)
    available_at_column: str = "available_at"
    available_at_output_col: str = ""
    join_policy: str = "asof_backward"
    required: bool = True

    @classmethod
    def from_value(cls, value: object, *, index: int = 0) -> AuxiliaryDataSpec:
        raw = _require_mapping(value, field_name=f"auxiliary_data[{index}]")
        dataset = str(raw.get("dataset", "") or "").strip()
        if not dataset:
            raise ValueError(f"auxiliary_data[{index}].dataset is required")
        fields = _string_tuple(raw.get("fields"), field_name=f"auxiliary_data[{index}].fields")
        if not fields:
            raise ValueError(f"auxiliary_data[{index}].fields is required")
        fields = tuple(
            _validate_output_name(
                name,
                field_name=f"auxiliary_data[{index}].fields",
            )
            for name in fields
        )
        join_policy = str(raw.get("join_policy", "asof_backward") or "asof_backward").strip().casefold()
        if join_policy not in _JOIN_POLICIES:
            raise ValueError(f"unsupported auxiliary join_policy: {join_policy!r}")
        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(f"auxiliary_data[{index}].required must be bool")
        safe_dataset = "".join(
            char if char.isalnum() else "_"
            for char in dataset
        ).strip("_") or "auxiliary"
        default_available_output = f"{safe_dataset}_available_at"
        key_columns = _string_tuple(
            raw.get("key_columns", ("stock_code",)),
            field_name=f"auxiliary_data[{index}].key_columns",
        )
        available_at_column = str(raw.get("available_at_column", "available_at") or "").strip()
        if not available_at_column:
            raise ValueError(f"auxiliary_data[{index}].available_at_column is required")
        available_at_output_col = str(
            raw.get("available_at_output_col", default_available_output) or ""
        ).strip()
        if available_at_output_col:
            available_at_output_col = _validate_output_name(
                available_at_output_col,
                field_name=f"auxiliary_data[{index}].available_at_output_col",
            )
        source_collisions = sorted(
            set(fields) & {*key_columns, available_at_column},
            key=str.casefold,
        )
        if source_collisions:
            raise ValueError(
                f"auxiliary_data[{index}] fields overlap source keys: {source_collisions}"
            )
        if available_at_column in key_columns:
            raise ValueError(
                f"auxiliary_data[{index}].available_at_column cannot also be a key column"
            )
        if available_at_output_col in fields:
            raise ValueError(
                f"auxiliary_data[{index}].available_at_output_col overlaps a data field"
            )
        return cls(
            dataset=dataset,
            fields=fields,
            key_columns=key_columns,
            available_at_column=available_at_column,
            available_at_output_col=available_at_output_col,
            join_policy=join_policy,
            required=required,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "fields": list(self.fields),
            "key_columns": list(self.key_columns),
            "available_at_column": self.available_at_column,
            "available_at_output_col": self.available_at_output_col,
            "join_policy": self.join_policy,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ReferenceSeriesSpec:
    name: str
    symbol: str
    fields: tuple[str, ...] = ("close",)
    output_prefix: str = "reference_"
    join_policy: str = "asof_backward"

    @classmethod
    def from_value(cls, value: object, *, index: int = 0) -> ReferenceSeriesSpec:
        raw = _require_mapping(value, field_name=f"reference_series[{index}]")
        name = str(raw.get("name", "") or "").strip()
        symbol = str(raw.get("symbol", "") or "").strip()
        if not name or not symbol:
            raise ValueError(f"reference_series[{index}] requires name and symbol")
        join_policy = str(raw.get("join_policy", "asof_backward") or "asof_backward").strip().casefold()
        if join_policy not in _JOIN_POLICIES:
            raise ValueError(f"unsupported reference join_policy: {join_policy!r}")
        fields = _string_tuple(raw.get("fields", ("close",)), field_name=f"reference_series[{index}].fields")
        if not fields:
            raise ValueError(f"reference_series[{index}].fields is required")
        output_prefix = str(raw.get("output_prefix", f"{name}_") or "").strip()
        if not output_prefix:
            raise ValueError(f"reference_series[{index}].output_prefix is required")
        for field_name in fields:
            _validate_output_name(
                f"{output_prefix}{field_name}",
                field_name=f"reference_series[{index}].output_prefix",
            )
        _validate_output_name(
            f"{output_prefix}available_at",
            field_name=f"reference_series[{index}].output_prefix",
        )
        return cls(
            name=name,
            symbol=symbol,
            fields=fields,
            output_prefix=output_prefix,
            join_policy=join_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "fields": list(self.fields),
            "output_prefix": self.output_prefix,
            "join_policy": self.join_policy,
        }


@dataclass(frozen=True, slots=True)
class CrossSectionTransformSpec:
    source_col: str
    output_col: str
    method: str
    group_by: tuple[str, ...] = ()
    exposure_cols: tuple[str, ...] = ()
    ascending: bool = False
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99

    @classmethod
    def from_value(cls, value: object, *, index: int = 0) -> CrossSectionTransformSpec:
        raw = _require_mapping(value, field_name=f"cross_section_transforms[{index}]")
        source = str(raw.get("source_col", "") or "").strip()
        output = str(raw.get("output_col", "") or "").strip()
        method = str(raw.get("method", "") or "").strip().casefold()
        if not source or not output or method not in _CROSS_SECTION_METHODS:
            raise ValueError(
                f"cross_section_transforms[{index}] requires source_col/output_col and a supported method"
            )
        output = _validate_output_name(
            output,
            field_name=f"cross_section_transforms[{index}].output_col",
        )
        ascending = raw.get("ascending", False)
        if not isinstance(ascending, bool):
            raise ValueError(f"cross_section_transforms[{index}].ascending must be bool")
        lower = float(raw.get("lower_quantile", 0.01))
        upper = float(raw.get("upper_quantile", 0.99))
        if not 0 <= lower < upper <= 1:
            raise ValueError("cross-section quantiles must satisfy 0 <= lower < upper <= 1")
        exposure_cols = _string_tuple(
            raw.get("exposure_cols"),
            field_name=f"cross_section_transforms[{index}].exposure_cols",
        )
        group_by = _string_tuple(
            raw.get("group_by"),
            field_name=f"cross_section_transforms[{index}].group_by",
        )
        forbidden_groups = sorted(
            set(group_by) & {"trade_date", "stock_code"},
            key=str.casefold,
        )
        if forbidden_groups:
            raise ValueError(
                f"cross_section_transforms[{index}].group_by contains implicit or "
                f"non-cross-sectional keys: {forbidden_groups}"
            )
        if source == output:
            raise ValueError(
                f"cross_section_transforms[{index}].output_col must differ from source_col"
            )
        if method == "neutralize" and not exposure_cols:
            raise ValueError(f"cross_section_transforms[{index}] neutralize requires exposure_cols")
        if method != "neutralize" and exposure_cols:
            raise ValueError(
                f"cross_section_transforms[{index}].exposure_cols is only valid for neutralize"
            )
        if source in exposure_cols:
            raise ValueError(
                f"cross_section_transforms[{index}].source_col cannot be an exposure_col"
            )
        return cls(
            source_col=source,
            output_col=output,
            method=method,
            group_by=group_by,
            exposure_cols=exposure_cols,
            ascending=ascending,
            lower_quantile=lower,
            upper_quantile=upper,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_col": self.source_col,
            "output_col": self.output_col,
            "method": self.method,
            "group_by": list(self.group_by),
            "exposure_cols": list(self.exposure_cols),
            "ascending": self.ascending,
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
        }


@dataclass(frozen=True, slots=True)
class StrategyParameterSpec:
    name: str
    kind: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] = ()
    description: str = ""

    @classmethod
    def from_value(cls, name: str, value: object) -> StrategyParameterSpec:
        raw = _require_mapping(value, field_name=f"strategy_parameters[{name!r}]")
        normalized_name = str(name or "").strip()
        kind = str(raw.get("type", raw.get("kind", "")) or "").strip().casefold()
        if not normalized_name or kind not in _PARAMETER_KINDS:
            raise ValueError(f"strategy parameter {name!r} has an unsupported type")
        minimum = _optional_finite_float(raw.get("min"), field_name=f"{name}.min")
        maximum = _optional_finite_float(raw.get("max"), field_name=f"{name}.max")
        if kind not in {"float", "int"} and (minimum is not None or maximum is not None):
            raise ValueError(
                f"strategy parameter {name!r} min/max are only valid for numeric types"
            )
        if kind == "int" and any(
            bound is not None and not bound.is_integer()
            for bound in (minimum, maximum)
        ):
            raise ValueError(f"strategy parameter {name!r} integer bounds must be whole numbers")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"strategy parameter {name!r} has min > max")
        choices_raw = raw.get("choices", ())
        if choices_raw is None:
            choices = ()
        elif isinstance(choices_raw, (list, tuple)):
            choices = tuple(choices_raw)
        else:
            raise ValueError(f"strategy parameter {name!r} choices must be a sequence")
        if kind == "choice":
            if not choices:
                raise ValueError(f"strategy parameter {name!r} choices must not be empty")
        elif choices:
            raise ValueError(
                f"strategy parameter {name!r} choices are only valid for type='choice'"
            )
        if "default" not in raw:
            raise ValueError(f"strategy parameter {name!r} default is required")
        default = raw.get("default")
        if kind == "choice" and (not choices or default not in choices):
            raise ValueError(f"strategy parameter {name!r} default must be one of choices")
        spec = cls(
            name=normalized_name,
            kind=kind,
            default=default,
            minimum=minimum,
            maximum=maximum,
            choices=choices,
            description=str(raw.get("description", "") or "").strip(),
        )
        spec.validate_value(default)
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "choices": list(self.choices),
            "description": self.description,
        }

    def validate_value(self, value: Any) -> Any:
        parsed: Any
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"strategy parameter {self.name!r} must be bool")
            parsed = value
        elif self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError(f"strategy parameter {self.name!r} must be int")
            parsed = int(value)
        elif self.kind == "float":
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"strategy parameter {self.name!r} must be numeric")
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError(f"strategy parameter {self.name!r} must be finite")
        elif self.kind == "str":
            if not isinstance(value, str):
                raise ValueError(f"strategy parameter {self.name!r} must be str")
            parsed = value
        else:
            parsed = value
        if self.choices and parsed not in self.choices:
            raise ValueError(f"strategy parameter {self.name!r} must be one of {list(self.choices)}")
        if self.minimum is not None and parsed < self.minimum:
            raise ValueError(f"strategy parameter {self.name!r} must be >= {self.minimum}")
        if self.maximum is not None and parsed > self.maximum:
            raise ValueError(f"strategy parameter {self.name!r} must be <= {self.maximum}")
        return parsed


@dataclass(frozen=True, slots=True)
class IndexTimingSpec:
    enabled: bool = False
    index_code: str = "399006.SZ"
    columns: tuple[str, ...] = _DEFAULT_INDEX_TIMING_COLUMNS

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> IndexTimingSpec:
        enabled = meta.get("use_index_timing", False)
        if not isinstance(enabled, bool):
            raise ValueError("use_index_timing must be bool")
        code = str(meta.get("index_code", "399006.SZ") or "").strip()
        if enabled and not code:
            raise ValueError("index_code is required when use_index_timing=True")
        columns = _string_tuple(
            meta.get("index_timing_columns", _DEFAULT_INDEX_TIMING_COLUMNS),
            field_name="index_timing_columns",
        )
        if enabled and ("trade_date" not in columns or "close" not in columns):
            raise ValueError("index_timing_columns must contain trade_date and close")
        return cls(enabled=enabled, index_code=code, columns=columns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "index_code": self.index_code,
            "columns": list(self.columns),
        }


@dataclass(frozen=True, slots=True)
class BoardTimingSpec:
    enabled: bool = False
    board_index_codes: tuple[str, ...] = ()
    columns: tuple[str, ...] = _DEFAULT_BOARD_TIMING_COLUMNS
    require_board_member: bool = False

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> BoardTimingSpec:
        enabled = meta.get("use_board_timing", False)
        codes = _string_tuple(
            meta.get("board_index_code", meta.get("board_index_codes")),
            field_name="board_index_codes",
        )
        membership = meta.get("require_board_member", bool(codes))
        if not isinstance(enabled, bool) or not isinstance(membership, bool):
            raise ValueError("use_board_timing and require_board_member must be bool")
        columns = _string_tuple(
            meta.get("board_timing_columns", _DEFAULT_BOARD_TIMING_COLUMNS),
            field_name="board_timing_columns",
        )
        if enabled and not codes:
            raise ValueError("board_index_codes is required when use_board_timing=True")
        if enabled and ("trade_date" not in columns or "close" not in columns):
            raise ValueError("board_timing_columns must contain trade_date and close")
        return cls(
            enabled=enabled,
            board_index_codes=codes,
            columns=columns,
            require_board_member=membership,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "board_index_codes": list(self.board_index_codes),
            "columns": list(self.columns),
            "require_board_member": self.require_board_member,
        }


@dataclass(frozen=True, slots=True)
class AdvancedStrategySpec:
    contract_version: str = ADVANCED_CONTRACT_VERSION
    index_timing: IndexTimingSpec = field(default_factory=IndexTimingSpec)
    board_timing: BoardTimingSpec = field(default_factory=BoardTimingSpec)
    position_policy: PositionPolicySpec = field(default_factory=PositionPolicySpec)
    portfolio_construction: PortfolioConstructionSpec = field(default_factory=PortfolioConstructionSpec)
    auxiliary_data: tuple[AuxiliaryDataSpec, ...] = ()
    reference_series: tuple[ReferenceSeriesSpec, ...] = ()
    cross_section_transforms: tuple[CrossSectionTransformSpec, ...] = ()
    parameters: Mapping[str, StrategyParameterSpec] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any] | None) -> AdvancedStrategySpec:
        raw = _require_mapping(meta, field_name="STRATEGY_META")
        auxiliary_raw = raw.get("auxiliary_data_specs", ()) or ()
        reference_raw = raw.get("reference_series_specs", ()) or ()
        transforms_raw = raw.get("cross_section_transform_specs", ()) or ()
        parameters_raw = _require_mapping(
            raw.get("strategy_parameter_specs"),
            field_name="strategy_parameter_specs",
        )
        for field_name, value in (
            ("auxiliary_data_specs", auxiliary_raw),
            ("reference_series_specs", reference_raw),
            ("cross_section_transform_specs", transforms_raw),
        ):
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{field_name} must be a sequence")
        parameters = {
            str(name): StrategyParameterSpec.from_value(str(name), value)
            for name, value in sorted(parameters_raw.items(), key=lambda item: str(item[0]).casefold())
        }
        auxiliary_data = tuple(
            AuxiliaryDataSpec.from_value(value, index=index)
            for index, value in enumerate(auxiliary_raw)
        )
        reference_series = tuple(
            ReferenceSeriesSpec.from_value(value, index=index)
            for index, value in enumerate(reference_raw)
        )
        cross_section_transforms = tuple(
            CrossSectionTransformSpec.from_value(value, index=index)
            for index, value in enumerate(transforms_raw)
        )
        declared_outputs = [
            *(
                field_name
                for item in auxiliary_data
                for field_name in (*item.fields, item.available_at_output_col)
                if field_name
            ),
            *(
                f"{item.output_prefix}{field_name}"
                for item in reference_series
                for field_name in (*item.fields, "available_at")
            ),
            *(item.output_col for item in cross_section_transforms),
        ]
        duplicates = sorted(
            {
                name
                for name in declared_outputs
                if declared_outputs.count(name) > 1
            },
            key=str.casefold,
        )
        if duplicates:
            raise ValueError(f"advanced strategy output columns must be unique: {duplicates}")
        contract_version = str(raw.get("strategy_contract_version") or "").strip()
        if contract_version != ADVANCED_CONTRACT_VERSION:
            raise ValueError(
                "unsupported strategy contract: "
                f"expected {ADVANCED_CONTRACT_VERSION!r}, got {contract_version or '<missing>'!r}"
            )
        return cls(
            contract_version=contract_version,
            index_timing=IndexTimingSpec.from_meta(raw),
            board_timing=BoardTimingSpec.from_meta(raw),
            position_policy=PositionPolicySpec.from_value(raw.get("position_policy")),
            portfolio_construction=PortfolioConstructionSpec.from_value(
                raw.get("portfolio_construction")
            ),
            auxiliary_data=auxiliary_data,
            reference_series=reference_series,
            cross_section_transforms=cross_section_transforms,
            parameters=MappingProxyType(parameters),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_contract_version": self.contract_version,
            "index_timing": self.index_timing.to_dict(),
            "board_timing": self.board_timing.to_dict(),
            "position_policy": self.position_policy.to_dict(),
            "portfolio_construction": self.portfolio_construction.to_dict(),
            "auxiliary_data_specs": [item.to_dict() for item in self.auxiliary_data],
            "reference_series_specs": [item.to_dict() for item in self.reference_series],
            "cross_section_transform_specs": [
                item.to_dict() for item in self.cross_section_transforms
            ],
            "strategy_parameter_specs": {
                name: spec.to_dict() for name, spec in self.parameters.items()
            },
        }


def resolve_strategy_parameters(
    specs: Mapping[str, StrategyParameterSpec],
    overrides: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    raw_overrides = dict(overrides or {})
    unknown = sorted(set(raw_overrides) - set(specs), key=str.casefold)
    if unknown:
        raise ValueError(f"unknown strategy parameter overrides: {unknown}")
    resolved = {
        name: spec.validate_value(raw_overrides.get(name, spec.default))
        for name, spec in sorted(specs.items(), key=lambda item: item[0].casefold())
    }
    return MappingProxyType(resolved)


def resolve_declared_parameters(meta: Mapping[str, Any]) -> Mapping[str, Any]:
    """Resolve one strategy's declared, typed runtime parameter values."""
    if not isinstance(meta, Mapping):
        raise TypeError("STRATEGY_META must be a mapping")
    raw_overrides = meta.get("runtime_parameters", {})
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("runtime_parameters must be a mapping")
    advanced = AdvancedStrategySpec.from_meta(meta)
    if not advanced.parameters:
        if raw_overrides:
            unknown = sorted((str(name) for name in raw_overrides), key=str.casefold)
            raise ValueError(f"unknown strategy parameter overrides: {unknown}")
        return MappingProxyType({})
    return resolve_strategy_parameters(advanced.parameters, raw_overrides)


__all__ = [
    "ADVANCED_CONTRACT_VERSION",
    "AdvancedStrategySpec",
    "AuxiliaryDataSpec",
    "BoardTimingSpec",
    "CrossSectionTransformSpec",
    "IndexTimingSpec",
    "PortfolioConstructionSpec",
    "PositionPolicySpec",
    "ReferenceSeriesSpec",
    "StrategyParameterSpec",
    "resolve_declared_parameters",
    "resolve_strategy_parameters",
]
