"""Resolve strategy declarations into machine-readable runtime requirements."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from strategies.advanced import AdvancedStrategySpec

from .fields import BASE_INPUT_FIELDS, normalize_field_names, validate_declared_fields


REQUIREMENTS_SCHEMA_VERSION = "2.0"
FULL_PANEL_SOURCES = ("parquet",)
STANDARD_RUNTIME_ID = "v1"
STANDARD_RUNTIME_CAPABILITIES = frozenset({"cross_section_transforms"})
PANEL_EXECUTION_STAGES = (
    "parameter_resolution",
    "data_load",
    "point_in_time_data",
    "reference_series_injection",
    "panel_features",
    "market_feature_injection",
    "cross_section_transforms",
    "panel_signals",
    "single_signals",
    "signal_filters",
    "position_policy",
    "portfolio_construction",
    "portfolio_risk",
)
_STAGE_ORDER = {name: index for index, name in enumerate(PANEL_EXECUTION_STAGES)}


@dataclass(frozen=True, slots=True)
class RequirementIssue:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    engine: str = "v1"
    panel_source: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    available_input_columns: frozenset[str] | None = None
    available_auxiliary_datasets: frozenset[str] | None = None
    available_reference_symbols: frozenset[str] | None = None
    supported_capabilities: frozenset[str] = frozenset()

    @classmethod
    def standard_v1(
        cls,
        *,
        panel_source: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        available_input_columns: frozenset[str] | None = None,
        available_auxiliary_datasets: frozenset[str] | None = None,
        available_reference_symbols: frozenset[str] | None = None,
    ) -> RuntimeEnvironment:
        return cls(
            engine=STANDARD_RUNTIME_ID,
            panel_source=panel_source,
            overrides=MappingProxyType(dict(overrides or {})),
            available_input_columns=available_input_columns,
            available_auxiliary_datasets=available_auxiliary_datasets,
            available_reference_symbols=available_reference_symbols,
            supported_capabilities=STANDARD_RUNTIME_CAPABILITIES,
        )

    @classmethod
    def from_value(cls, value: RuntimeEnvironment | Mapping[str, Any] | None) -> RuntimeEnvironment | None:
        if value is None or isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("runtime environment must be a mapping or RuntimeEnvironment")
        return cls(
            engine=str(value.get("engine", "v1") or "v1").strip().casefold(),
            panel_source=(
                None
                if value.get("panel_source") is None
                else str(value.get("panel_source") or "").strip().casefold()
            ),
            overrides=MappingProxyType(dict(value.get("overrides") or {})),
            available_input_columns=(
                None
                if "available_input_columns" not in value
                else frozenset(normalize_field_names(value.get("available_input_columns") or ()))
            ),
            available_auxiliary_datasets=(
                None
                if "available_auxiliary_datasets" not in value
                else frozenset(
                    str(item).strip()
                    for item in (value.get("available_auxiliary_datasets") or ())
                    if str(item).strip()
                )
            ),
            available_reference_symbols=(
                None
                if "available_reference_symbols" not in value
                else frozenset(
                    str(item).strip().upper()
                    for item in (value.get("available_reference_symbols") or ())
                    if str(item).strip()
                )
            ),
            supported_capabilities=frozenset(
                str(item).strip().casefold()
                for item in (value.get("supported_capabilities") or ())
                if str(item).strip()
            ),
        )


def standard_runtime_payload(runtime_id: str = STANDARD_RUNTIME_ID) -> dict[str, Any]:
    normalized = str(runtime_id or "").strip().casefold()
    if normalized != STANDARD_RUNTIME_ID:
        raise ValueError(
            f"unsupported runtime {runtime_id!r}; expected {STANDARD_RUNTIME_ID!r}"
        )
    return {
        "id": STANDARD_RUNTIME_ID,
        "supported_capabilities": sorted(
            STANDARD_RUNTIME_CAPABILITIES,
            key=str.casefold,
        ),
    }


@dataclass(frozen=True, slots=True)
class StrategyRequirements:
    capabilities: tuple[str, ...]
    required_hooks: tuple[str, ...]
    required_runtime_capabilities: tuple[str, ...]
    required_runtime_overrides: Mapping[str, Any]
    accepted_panel_sources: tuple[str, ...]
    requires_full_panel: bool
    required_input_columns: tuple[str, ...]
    produced_columns: tuple[str, ...]
    execution_stages: tuple[str, ...]
    issues: tuple[RequirementIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REQUIREMENTS_SCHEMA_VERSION,
            "ready": self.ready,
            "capabilities": list(self.capabilities),
            "required_hooks": list(self.required_hooks),
            "required_runtime_capabilities": list(self.required_runtime_capabilities),
            "required_runtime_overrides": dict(self.required_runtime_overrides),
            "accepted_panel_sources": list(self.accepted_panel_sources),
            "requires_full_panel": self.requires_full_panel,
            "required_input_columns": list(self.required_input_columns),
            "produced_columns": list(self.produced_columns),
            "execution_stages": list(self.execution_stages),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _meta_for(strategy: object | None, meta: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if meta is not None:
        if not isinstance(meta, Mapping):
            raise TypeError("meta must be a mapping")
        return meta
    candidate = getattr(strategy, "STRATEGY_META", {})
    if not isinstance(candidate, Mapping):
        raise TypeError("STRATEGY_META must be a mapping")
    return candidate


def _nested_bool(meta: Mapping[str, Any], group: str, key: str) -> bool:
    nested = meta.get(group, {})
    return bool(nested.get(key, False)) if isinstance(nested, Mapping) else False


def _runtime_override(overrides: Mapping[str, Any], dotted_key: str) -> object:
    if dotted_key in overrides:
        return overrides[dotted_key]
    current: object = overrides
    for token in dotted_key.split("."):
        if not isinstance(current, Mapping) or token not in current:
            return None
        current = current[token]
    return current


def _sorted_stages(stages: set[str]) -> tuple[str, ...]:
    return tuple(sorted(stages, key=lambda item: (_STAGE_ORDER.get(item, 999), item.casefold())))


def resolve_strategy_requirements(
    *,
    spec: object,
    strategy: object | None = None,
    meta: Mapping[str, Any] | None = None,
    advanced_spec: AdvancedStrategySpec | None = None,
    runtime: RuntimeEnvironment | Mapping[str, Any] | None = None,
) -> StrategyRequirements:
    raw_meta = _meta_for(strategy, meta)
    advanced = advanced_spec or AdvancedStrategySpec.from_meta(raw_meta)
    execution_mode = str(raw_meta.get("execution_mode", "panel") or "panel").strip().casefold()
    if execution_mode not in {"panel", "single", "fixed_rebalance"}:
        issues = [
            RequirementIssue(
                "error",
                "invalid_execution_mode",
                f"execution_mode must be panel, single, or fixed_rebalance; got {execution_mode!r}",
            )
        ]
    else:
        issues = []
    runtime_env = RuntimeEnvironment.from_value(runtime)
    capabilities = set() if execution_mode == "fixed_rebalance" else {"time_series_rules"}
    hooks: set[str] = set()
    runtime_capabilities: set[str] = set()
    overrides: dict[str, Any] = {}
    stages = {"data_load"}
    produced: set[str] = set()

    def require_hook(name: str, capability: str) -> None:
        capabilities.add(capability)
        hooks.add(name)
        if strategy is not None and not callable(getattr(strategy, name, None)):
            issues.append(
                RequirementIssue("error", "required_hook_missing", f"{capability} requires callable {name}")
            )

    if bool(getattr(spec, "use_index_timing", False)):
        require_hook("build_time_ok", "index_timing")
        stages.add("signal_filters")
    if bool(getattr(spec, "use_board_timing", False)):
        require_hook("build_board_time_ok", "board_timing")
        stages.add("signal_filters")
    if bool(getattr(spec, "require_board_member", False)):
        capabilities.add("board_membership")
        stages.add("signal_filters")
    if bool(getattr(spec, "use_market_features", False)):
        capabilities.add("market_features")
        stages.add("market_feature_injection")
        if execution_mode != "panel":
            issues.append(
                RequirementIssue(
                    "error",
                    "market_features_require_panel",
                    "use_market_features requires execution_mode=panel",
                )
            )
    rank_specs = tuple(getattr(spec, "market_rank_specs", ()) or ())
    aggregate_specs = tuple(getattr(spec, "market_aggregate_specs", ()) or ())
    passthrough = tuple(getattr(spec, "market_feature_passthrough_cols", ()) or ())
    if rank_specs:
        capabilities.add("market_rank")
        for item in rank_specs:
            produced.update(
                str(getattr(item, name, "") or "").strip()
                for name in ("rank_col", "pct_col", "count_col")
            )
    if aggregate_specs:
        capabilities.add("market_breadth")
        produced.update(str(getattr(item, "output_col", "") or "").strip() for item in aggregate_specs)
    if passthrough:
        capabilities.add("market_passthrough")
        produced.update(str(item).strip() for item in passthrough)

    has_panel_features = strategy is not None and callable(getattr(strategy, "compute_features", None))
    has_panel_signals = strategy is not None and callable(getattr(strategy, "compute_signals", None))
    if execution_mode == "panel":
        require_hook("compute_features", "panel_features")
        require_hook("compute_signals", "panel_signals")
        stages.update({"panel_features", "panel_signals"})
    elif execution_mode == "single":
        require_hook("build_single_signals", "single_signals")
        stages.add("single_signals")

    if _nested_bool(raw_meta, "risk_control", "enable_nv"):
        capabilities.add("nav_risk")
        stages.add("portfolio_risk")
    if _nested_bool(raw_meta, "risk_control", "enable_dd"):
        capabilities.add("drawdown_risk")
        stages.add("portfolio_risk")

    price_columns = normalize_field_names(getattr(spec, "price_columns", ()) or ())
    required_fields = normalize_field_names(getattr(spec, "required_fields", ()) or ())
    if any(name not in BASE_INPUT_FIELDS for name in price_columns):
        capabilities.add("extra_price_columns")
    for issue in validate_declared_fields(
        required_fields=required_fields,
        price_columns=price_columns,
    ):
        issues.append(RequirementIssue(**issue))

    if advanced.position_policy.enabled:
        capabilities.add("position_policy")
        runtime_capabilities.add("position_policy")
        stages.add("position_policy")
    if advanced.portfolio_construction.enabled:
        capabilities.add("portfolio_construction")
        runtime_capabilities.add("portfolio_construction")
        stages.add("portfolio_construction")
    if advanced.portfolio_construction.requires_target_weight_engine:
        require_hook("build_target_weights", "target_weight_decision")
        runtime_capabilities.add("target_weight_decision")
    if advanced.auxiliary_data:
        capabilities.add("point_in_time_data")
        runtime_capabilities.add("point_in_time_data")
        stages.add("point_in_time_data")
        produced.update(
            field_name
            for item in advanced.auxiliary_data
            for field_name in item.fields
        )
        produced.update(
            item.available_at_output_col
            for item in advanced.auxiliary_data
            if item.available_at_output_col
        )
    if advanced.reference_series:
        capabilities.add("reference_series")
        runtime_capabilities.add("reference_series_injection")
        stages.add("reference_series_injection")
        produced.update(
            f"{item.output_prefix}{field_name}"
            for item in advanced.reference_series
            for field_name in item.fields
        )
    if advanced.cross_section_transforms:
        capabilities.add("cross_section_transforms")
        runtime_capabilities.add("cross_section_transforms")
        stages.add("cross_section_transforms")
        produced.update(item.output_col for item in advanced.cross_section_transforms)
        hooks.add("compute_signals")
        if not has_panel_signals:
            issues.append(
                RequirementIssue(
                    "error",
                    "cross_section_stage_unavailable",
                    "declared cross-section transforms require callable compute_signals",
                )
            )

    if advanced.parameters:
        capabilities.add("typed_parameters")
        stages.add("parameter_resolution")

    requires_full_panel = execution_mode == "panel"
    if execution_mode == "single" and (
        has_panel_features
        or has_panel_signals
        or bool(advanced.auxiliary_data)
        or bool(advanced.reference_series)
        or bool(advanced.cross_section_transforms)
    ):
        issues.append(
            RequirementIssue(
                "error",
                "single_strategy_declares_panel_stage",
                "execution_mode=single cannot declare panel execution hooks or stages",
            )
        )
    accepted_sources = FULL_PANEL_SOURCES if requires_full_panel else ()
    if runtime_env is not None:
        if requires_full_panel and runtime_env.panel_source not in accepted_sources:
            issues.append(
                RequirementIssue(
                    "error",
                    "full_panel_source_required",
                    f"panel hooks require one of {list(accepted_sources)}; got {runtime_env.panel_source!r}",
                )
            )
        for key, expected in overrides.items():
            if _runtime_override(runtime_env.overrides, key) != expected:
                issues.append(
                    RequirementIssue(
                        "error",
                        "runtime_override_required",
                        f"strategy requires runtime override {key}={expected!r}",
                    )
                )
        missing_runtime = sorted(
            runtime_capabilities - set(runtime_env.supported_capabilities),
            key=str.casefold,
        )
        for capability in missing_runtime:
            issues.append(
                RequirementIssue(
                    "error",
                    "runtime_capability_unavailable",
                    f"runtime does not provide required capability {capability!r}",
                )
            )
        if runtime_env.available_input_columns is not None:
            required_runtime_inputs = set(
                normalize_field_names((*BASE_INPUT_FIELDS, *required_fields, *price_columns))
            )
            missing_inputs = sorted(
                required_runtime_inputs - set(runtime_env.available_input_columns),
                key=str.casefold,
            )
            if missing_inputs:
                issues.append(
                    RequirementIssue(
                        "error",
                        "input_columns_unavailable",
                        f"runtime input source is missing required columns: {missing_inputs}",
                    )
                )
        if advanced.auxiliary_data:
            if runtime_env.available_auxiliary_datasets is None:
                issues.append(
                    RequirementIssue(
                        "warning",
                        "auxiliary_dataset_inventory_unknown",
                        "runtime did not provide available_auxiliary_datasets for preflight",
                    )
                )
            else:
                missing_datasets = sorted(
                    {
                        item.dataset
                        for item in advanced.auxiliary_data
                        if item.required
                    }
                    - set(runtime_env.available_auxiliary_datasets),
                    key=str.casefold,
                )
                for dataset in missing_datasets:
                    issues.append(
                        RequirementIssue(
                            "error",
                            "auxiliary_dataset_unavailable",
                            f"required auxiliary dataset is unavailable: {dataset}",
                        )
                    )
        if advanced.reference_series:
            if runtime_env.available_reference_symbols is None:
                issues.append(
                    RequirementIssue(
                        "warning",
                        "reference_symbol_inventory_unknown",
                        "runtime did not provide available_reference_symbols for preflight",
                    )
                )
            else:
                missing_symbols = sorted(
                    {
                        item.symbol.strip().upper()
                        for item in advanced.reference_series
                    }
                    - set(runtime_env.available_reference_symbols),
                    key=str.casefold,
                )
                for symbol in missing_symbols:
                    issues.append(
                        RequirementIssue(
                            "error",
                            "reference_symbol_unavailable",
                            f"required reference symbol is unavailable: {symbol}",
                        )
                    )

    required_input = normalize_field_names((*BASE_INPUT_FIELDS, *required_fields, *price_columns))
    return StrategyRequirements(
        capabilities=tuple(sorted(capabilities, key=str.casefold)),
        required_hooks=tuple(sorted(hooks, key=str.casefold)),
        required_runtime_capabilities=tuple(sorted(runtime_capabilities, key=str.casefold)),
        required_runtime_overrides=MappingProxyType(dict(sorted(overrides.items()))),
        accepted_panel_sources=accepted_sources,
        requires_full_panel=requires_full_panel,
        required_input_columns=required_input,
        produced_columns=tuple(sorted((item for item in produced if item), key=str.casefold)),
        execution_stages=_sorted_stages(stages),
        issues=tuple(issues),
    )


def preflight_strategy_requirements(
    *,
    spec: object,
    strategy: object | None = None,
    meta: Mapping[str, Any] | None = None,
    advanced_spec: AdvancedStrategySpec | None = None,
    runtime: RuntimeEnvironment | Mapping[str, Any],
) -> StrategyRequirements:
    requirements = resolve_strategy_requirements(
        spec=spec,
        strategy=strategy,
        meta=meta,
        advanced_spec=advanced_spec,
        runtime=runtime,
    )
    if not requirements.ready:
        details = "; ".join(
            f"{issue.code}: {issue.message}"
            for issue in requirements.issues
            if issue.severity == "error"
        )
        raise ValueError(f"strategy runtime requirements are not satisfied: {details}")
    return requirements


__all__ = [
    "FULL_PANEL_SOURCES",
    "PANEL_EXECUTION_STAGES",
    "REQUIREMENTS_SCHEMA_VERSION",
    "RequirementIssue",
    "RuntimeEnvironment",
    "STANDARD_RUNTIME_CAPABILITIES",
    "STANDARD_RUNTIME_ID",
    "StrategyRequirements",
    "preflight_strategy_requirements",
    "resolve_strategy_requirements",
    "standard_runtime_payload",
]
