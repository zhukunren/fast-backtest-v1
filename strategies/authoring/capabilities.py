"""Machine-readable capability registry for Codex-authored strategies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .requirements import STANDARD_RUNTIME_ID, standard_runtime_payload


REGISTRY_VERSION = "2.0"
SCHEMA_VERSION = "2.0"
SUPPORTED_PROFILES = ("factor", "panel", "rule")
COMMON_FAILURES_RECIPE = "strategies/authoring/recipes/common_failures.md"


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    title: str
    summary: str
    profiles: tuple[str, ...]
    default_profiles: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    author_hooks: tuple[str, ...] = ()
    author_hooks_by_profile: tuple[tuple[str, tuple[str, ...]], ...] = ()
    framework_surfaces: tuple[str, ...] = ()
    meta_keys: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    required_runtime_overrides: tuple[tuple[str, Any], ...] = ()
    required_runtime_capabilities: tuple[str, ...] = ()
    accepted_panel_sources: tuple[str, ...] = ()
    requires_full_panel: bool = False
    required_input_columns: tuple[str, ...] = ()
    produced_columns: tuple[str, ...] = ()
    execution_stage: str = ""
    recipe: str = ""

    def to_payload(self, *, runtime_id: str | None = None) -> dict[str, Any]:
        recipes = sorted(
            {item for item in (self.recipe, COMMON_FAILURES_RECIPE) if item},
            key=str.casefold,
        )
        payload = {
            "id": self.capability_id,
            "title": self.title,
            "summary": self.summary,
            "profiles": list(self.profiles),
            "default_profiles": list(self.default_profiles),
            "hooks": list(self.hooks),
            "author_hooks": list(self.author_hooks),
            "author_hooks_by_profile": {
                profile: list(hooks)
                for profile, hooks in self.author_hooks_by_profile
            },
            "framework_surfaces": list(self.framework_surfaces),
            "meta_keys": list(self.meta_keys),
            "depends_on": list(self.depends_on),
            "requires_any": list(self.requires_any),
            "conflicts_with": list(self.conflicts_with),
            "prerequisites": list(self.prerequisites),
            "required_runtime_overrides": dict(self.required_runtime_overrides),
            "required_runtime_capabilities": list(self.required_runtime_capabilities),
            "accepted_panel_sources": list(self.accepted_panel_sources),
            "requires_full_panel": bool(self.requires_full_panel),
            "required_input_columns": list(self.required_input_columns),
            "produced_columns": list(self.produced_columns),
            "execution_stage": self.execution_stage,
            "recipe": self.recipe,
            "recipes": recipes,
        }
        if runtime_id:
            payload["runtime"] = _runtime_availability(self, runtime_id)
        return payload


_ALL_PROFILES = SUPPORTED_PROFILES
_CAPABILITIES = (
    Capability(
        capability_id="board_membership",
        title="Board membership",
        summary="Restrict new-buy candidates to explicit board constituents.",
        profiles=_ALL_PROFILES,
        hooks=(),
        meta_keys=("require_board_member", "board_index_codes"),
        required_input_columns=("stock_code",),
        execution_stage="signal_filters",
        prerequisites=("Set at least one explicit board_index_codes value.",),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="board_timing",
        title="Board timing",
        summary="Filter new buys with a causal signal built from explicit board indices.",
        profiles=_ALL_PROFILES,
        hooks=("build_board_timing_signal", "build_board_time_ok"),
        author_hooks=("build_board_timing_signal",),
        framework_surfaces=("build_board_time_ok",),
        meta_keys=("use_board_timing", "board_index_codes", "board_timing_columns"),
        required_input_columns=("trade_date",),
        produced_columns=("board_time_ok", "time_ok"),
        execution_stage="signal_filters",
        prerequisites=(
            "Set at least one explicit board_index_codes value.",
            "Return a boolean Series aligned to the board input index.",
        ),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="cross_section_transforms",
        title="Declared cross-sectional transforms",
        summary="Apply ranks, winsorization, z-scores, or neutralization after market-feature injection.",
        profiles=("panel",),
        hooks=("compute_signals",),
        framework_surfaces=(
            "strategies.api.CrossSectionTransformSpec",
            "strategies.api.apply_cross_section_transforms",
            "strategies.api.BaseStrategy",
        ),
        meta_keys=("cross_section_transform_specs",),
        depends_on=("panel_signals",),
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        required_input_columns=("trade_date", "stock_code"),
        produced_columns=("<declared output_col>",),
        execution_stage="cross_section_transforms",
        required_runtime_capabilities=("cross_section_transforms",),
        prerequisites=(
            "Declare unique author-owned output columns.",
            "Compute time-series source columns before this stage.",
            "Use neutralize only with explicit exposure_cols.",
        ),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="drawdown_risk",
        title="Drawdown risk scaling",
        summary="Enable engine-owned drawdown-based scaling for new positions.",
        profiles=_ALL_PROFILES,
        meta_keys=(
            "risk_control.enable_dd",
            "risk_control.warn_dd",
            "risk_control.danger_dd",
            "risk_control.lock_dd",
            "risk_control.dd_ratios",
        ),
        execution_stage="portfolio_risk",
        prerequisites=(
            "Choose finite thresholds with lock_dd <= danger_dd <= warn_dd <= 0.",
            "Provide four non-negative dd_ratios in normal, warning, danger, lock order.",
        ),
        recipe="strategies/authoring/recipes/risk_controls.md",
    ),
    Capability(
        capability_id="extra_price_columns",
        title="Extra price columns",
        summary="Request optional source columns in addition to the base daily price frame.",
        profiles=_ALL_PROFILES,
        meta_keys=("price_columns", "required_fields"),
        execution_stage="data_load",
        prerequisites=("Confirm every requested column exists in the configured data source.",),
        recipe="strategies/authoring/recipes/data_fields.md",
    ),
    Capability(
        capability_id="index_timing",
        title="Index timing",
        summary="Filter new buys with a causal index-level timing signal.",
        profiles=_ALL_PROFILES,
        hooks=("build_index_timing_signal", "build_time_ok"),
        author_hooks=("build_index_timing_signal",),
        framework_surfaces=("build_time_ok",),
        meta_keys=("use_index_timing", "index_code", "index_timing_columns"),
        required_input_columns=("trade_date",),
        produced_columns=("time_ok",),
        execution_stage="signal_filters",
        prerequisites=("Choose an index code and return a boolean Series aligned to its input index.",),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="market_breadth",
        title="Market breadth",
        summary="Aggregate stock-local source features by date and inject market-level columns.",
        profiles=_ALL_PROFILES,
        hooks=("compute_features",),
        author_hooks=("compute_features", "market_feature_sources"),
        author_hooks_by_profile=(
            ("factor", ("market_feature_sources",)),
            ("panel", ("compute_features",)),
            ("rule", ("market_feature_sources",)),
        ),
        framework_surfaces=("compute_features", "compute_signals"),
        meta_keys=("use_market_features", "market_aggregate_specs"),
        required_input_columns=("trade_date", "stock_code"),
        execution_stage="market_feature_injection",
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        prerequisites=(
            "Each source_col must be causal; rule/factor profiles compute it with stock_code-grouped panel operations, while panel profiles produce it in compute_features().",
            "Use a derived source name; keys, OHLCV, standard signals, and framework-internal columns are reserved.",
            "Each output_col is unique and is not a source or rank output column.",
            "Choose agg from mean, sum, median, count, min, max, or std.",
            "Aggregates coerce to numeric and exclude NaN; count counts non-NaN rows. Emit explicit 0/1 values when a breadth ratio needs the full available-row denominator.",
        ),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="market_passthrough",
        title="Market feature passthrough",
        summary="Inject declared per-stock market-source columns without ranking or aggregation.",
        profiles=_ALL_PROFILES,
        hooks=("compute_features",),
        author_hooks=("compute_features", "market_feature_sources"),
        author_hooks_by_profile=(
            ("factor", ("market_feature_sources",)),
            ("panel", ("compute_features",)),
            ("rule", ("market_feature_sources",)),
        ),
        framework_surfaces=("compute_features", "compute_signals"),
        meta_keys=("use_market_features", "market_feature_passthrough_cols"),
        required_input_columns=("trade_date", "stock_code"),
        execution_stage="market_feature_injection",
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        requires_any=("market_breadth", "market_rank"),
        prerequisites=(
            "Declare only columns produced by the profile-specific market source hook.",
            "Use author-owned derived names; keys, OHLCV, standard signals, and framework-internal columns are reserved.",
        ),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="market_rank",
        title="Cross-sectional market rank",
        summary="Rank causal stock-local source features across the same trade date.",
        profiles=_ALL_PROFILES,
        hooks=("compute_features",),
        author_hooks=("compute_features", "market_feature_sources"),
        author_hooks_by_profile=(
            ("factor", ("market_feature_sources",)),
            ("panel", ("compute_features",)),
            ("rule", ("market_feature_sources",)),
        ),
        framework_surfaces=("compute_features", "compute_signals"),
        meta_keys=("use_market_features", "market_rank_specs"),
        required_input_columns=("trade_date", "stock_code"),
        execution_stage="market_feature_injection",
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        prerequisites=(
            "Use a source feature rather than the final TopN priority column.",
            "Use derived source/output names; keys, OHLCV, standard signals, and framework-internal columns are reserved.",
            "Do not compute a cross-sectional rank inside a stock-local transform; use the declared panel market-feature stage.",
        ),
        recipe="strategies/authoring/recipes/capability_selection.md",
    ),
    Capability(
        capability_id="nav_risk",
        title="NAV risk scaling",
        summary="Enable engine-owned NAV-level scaling for new positions.",
        profiles=_ALL_PROFILES,
        meta_keys=(
            "risk_control.enable_nv",
            "risk_control.nv_warn",
            "risk_control.nv_danger",
            "risk_control.nv_clear",
            "risk_control.nv_ratios",
        ),
        execution_stage="portfolio_risk",
        prerequisites=(
            "Choose finite thresholds with 0 <= nv_clear <= nv_danger <= nv_warn.",
            "Provide four non-negative nv_ratios in normal, warning, danger, clear order.",
        ),
        recipe="strategies/authoring/recipes/risk_controls.md",
    ),
    Capability(
        capability_id="panel_features",
        title="Panel features",
        summary="Build causal aligned feature arrays without mutating immutable panel storage.",
        profiles=("panel",),
        default_profiles=("panel",),
        hooks=("compute_features",),
        author_hooks=("compute_features",),
        framework_surfaces=(
            "strategies.api.BaseStrategy",
            "strategies.api.FeatureBatch",
        ),
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        required_input_columns=("trade_date", "stock_code", "open", "high", "low", "close", "volume"),
        execution_stage="panel_features",
        prerequisites=(
            "Return one value per panel row for every declared feature column.",
            "Use stock_id_by_row or stock_code-grouped operations for shift and rolling; do not split the panel into DataFrame slices.",
        ),
        recipe="strategies/authoring/recipes/signal_lifecycle.md",
    ),
    Capability(
        capability_id="panel_signals",
        title="Panel signals",
        summary="Generate raw signal intent on a full panel as aligned raw boolean arrays.",
        profiles=("panel",),
        default_profiles=("panel",),
        hooks=("compute_signals",),
        author_hooks=("compute_signals",),
        framework_surfaces=("strategies.api.BaseStrategy", "strategies.api.SignalBatch"),
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        required_input_columns=("trade_date", "stock_code", "open", "high", "low", "close", "volume"),
        produced_columns=("buy_signal_raw", "sell_signal_raw", "direction"),
        execution_stage="panel_signals",
        prerequisites=(
            "Return one raw boolean value for every immutable panel row.",
            "Return a SignalBatch with aligned raw boolean arrays instead of writing standard signal columns.",
            "Provide a full price-panel source; execution fails when the declared panel path is unavailable.",
        ),
        recipe="strategies/authoring/recipes/signal_lifecycle.md",
    ),
    Capability(
        capability_id="point_in_time_data",
        title="Point-in-time auxiliary data",
        summary="Join auxiliary observations only after their declared availability date.",
        profiles=_ALL_PROFILES,
        framework_surfaces=(
            "strategies.api.AuxiliaryDataSpec",
            "strategies.api.point_in_time_join",
        ),
        meta_keys=("auxiliary_data_specs",),
        required_runtime_capabilities=("point_in_time_data",),
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        required_input_columns=("trade_date", "stock_code"),
        produced_columns=("<declared fields>", "<declared available_at_output_col>"),
        execution_stage="point_in_time_data",
        prerequisites=(
            "Use an actual publication/availability timestamp, not the report period alone.",
            "Ensure key plus normalized available_at is unique.",
            "Provide a full price-panel source and StrategyDataInputs at runtime.",
        ),
        recipe="strategies/authoring/recipes/data_fields.md",
    ),
    Capability(
        capability_id="portfolio_construction",
        title="Declarative portfolio construction",
        summary="Declare rebalance, cash, concentration, normalization, and turnover policy.",
        profiles=_ALL_PROFILES,
        framework_surfaces=(
            "strategies.api.PortfolioConstructionSpec",
            "strategies.api.validate_target_weights",
        ),
        meta_keys=("portfolio_construction",),
        required_runtime_capabilities=("portfolio_construction",),
        execution_stage="portfolio_construction",
        prerequisites=(
            "Keep weights non-negative and finite.",
            "Pass previous weights when max_turnover is configured.",
        ),
        recipe="strategies/authoring/recipes/risk_controls.md",
    ),
    Capability(
        capability_id="position_policy",
        title="Engine-owned position policy",
        summary="Declare holding-period, stop, take-profit, trailing, and cooldown rules.",
        profiles=_ALL_PROFILES,
        framework_surfaces=("strategies.api.PositionPolicySpec",),
        meta_keys=("position_policy",),
        required_runtime_capabilities=("position_policy",),
        execution_stage="position_policy",
        prerequisites=(
            "Keep stateful holding and exit enforcement in the engine.",
            "Use non-positive returns for stop_loss_return and trailing_drawdown.",
        ),
        recipe="strategies/authoring/recipes/risk_controls.md",
    ),
    Capability(
        capability_id="reference_series",
        title="Point-in-time reference series",
        summary="Align declared benchmark, index, macro, or asset series by availability date.",
        profiles=_ALL_PROFILES,
        framework_surfaces=(
            "strategies.api.ReferenceSeriesSpec",
            "strategies.api.align_reference_series",
        ),
        meta_keys=("reference_series_specs",),
        required_runtime_capabilities=("reference_series_injection",),
        accepted_panel_sources=("parquet",),
        requires_full_panel=True,
        required_input_columns=("trade_date",),
        produced_columns=("<output_prefix><field>", "<output_prefix>available_at"),
        execution_stage="reference_series_injection",
        prerequisites=(
            "Declare a stable symbol and unique output prefix.",
            "Use backward as-of alignment unless exact-date behavior is required.",
            "Provide a full price-panel source and StrategyDataInputs at runtime.",
        ),
        recipe="strategies/authoring/recipes/data_fields.md",
    ),
    Capability(
        capability_id="research_contract",
        title="Research contract",
        summary="Record a falsifiable hypothesis, data assumptions, regimes, and an IS/OOS plan.",
        profiles=_ALL_PROFILES,
        default_profiles=(),
        meta_keys=(
            "name",
            "hypothesis",
            "required_fields",
            "universe_assumption",
            "validation",
            "expected_market_regimes",
            "sort_spec",
            "portfolio_num_j",
            "portfolio_num_i",
        ),
        prerequisites=(
            "Complete promotion placeholders before promotion or sealed validation.",
            "Choose positive integer portfolio_num_j and portfolio_num_i values; TopN is their product.",
            "Complete STRATEGY_SEMANTICS and STRATEGY_DESCRIPTION so they describe the implemented candidate.",
        ),
        recipe="strategies/authoring/recipes/data_fields.md",
    ),
    Capability(
        capability_id="target_weight_decision",
        title="Target-weight decision hook",
        summary="Return per-symbol target weights for a compatible portfolio engine.",
        profiles=_ALL_PROFILES,
        hooks=("build_target_weights",),
        author_hooks=("build_target_weights",),
        framework_surfaces=(
            "strategies.api.TargetWeightContext",
            "strategies.api.BaseStrategy",
        ),
        meta_keys=("portfolio_construction",),
        depends_on=("portfolio_construction",),
        required_runtime_capabilities=("target_weight_decision",),
        execution_stage="portfolio_construction",
        prerequisites=(
            "Use a non-top_n portfolio mode.",
            "Return symbol-to-weight intent and leave fills, cash, costs, and lots to the engine.",
        ),
        recipe="strategies/authoring/recipes/risk_controls.md",
    ),
    Capability(
        capability_id="time_series_rules",
        title="Daily time-series rules",
        summary="Build causal stock-local grouped or full-panel features and signals according to the selected authoring profile.",
        profiles=_ALL_PROFILES,
        default_profiles=_ALL_PROFILES,
        hooks=("compute_features", "compute_signals"),
        author_hooks=(
            "compute_features",
            "compute_signals",
            "feature_engine",
            "entry_exit_rules",
            "priority_score",
            "factor_engine",
            "factor_entry_exit_rules",
        ),
        author_hooks_by_profile=(
            ("factor", ("factor_engine", "factor_entry_exit_rules")),
            ("panel", ("compute_features", "compute_signals")),
            ("rule", ("feature_engine", "entry_exit_rules", "priority_score")),
        ),
        framework_surfaces=("compute_features", "compute_signals"),
        required_input_columns=("trade_date", "stock_code", "open", "high", "low", "close", "volume"),
        produced_columns=("buy_signal_raw", "sell_signal_raw"),
        execution_stage="panel_signals",
        prerequisites=("Stable-sort by stock_code and trade_date before shift, rolling, or pct_change operations.",),
        recipe="strategies/authoring/recipes/signal_lifecycle.md",
    ),
    Capability(
        capability_id="typed_parameters",
        title="Typed strategy parameters",
        summary="Declare validated defaults, bounds, choices, and reproducible runtime overrides.",
        profiles=_ALL_PROFILES,
        framework_surfaces=(
            "strategies.api.StrategyParameterSpec",
            "strategies.api.resolve_strategy_parameters",
        ),
        meta_keys=("strategy_parameter_specs", "runtime_parameters"),
        execution_stage="parameter_resolution",
        prerequisites=(
            "Declare every runtime override and provide a valid default.",
            "Use min/max only for int or float parameters.",
        ),
        recipe="strategies/authoring/recipes/data_fields.md",
    ),
)

_REGISTRY = {item.capability_id: item for item in _CAPABILITIES}
if tuple(sorted(_REGISTRY, key=str.casefold)) != tuple(item.capability_id for item in _CAPABILITIES):
    raise RuntimeError("Capability registry must be sorted by capability id.")

_PROFILE_DEFAULTS = {
    profile: tuple(
        item.capability_id
        for item in _CAPABILITIES
        if profile in item.default_profiles
    )
    for profile in SUPPORTED_PROFILES
}


def _runtime_availability(capability: Capability, runtime_id: str) -> dict[str, Any]:
    runtime = standard_runtime_payload(runtime_id)
    supported = set(runtime["supported_capabilities"])
    missing = sorted(
        set(capability.required_runtime_capabilities) - supported,
        key=str.casefold,
    )
    status = "available" if not missing else "unavailable"
    reason = (
        "runtime contract is available; data and configuration prerequisites still apply"
        if not missing
        else f"runtime does not provide required capabilities: {missing}"
    )
    return {
        "id": runtime["id"],
        "status": status,
        "missing_capabilities": missing,
        "reason": reason,
    }


def _require_profile(profile: str) -> str:
    normalized = str(profile or "").strip().casefold()
    if normalized not in SUPPORTED_PROFILES:
        raise ValueError(
            f"Unknown strategy profile {profile!r}; expected one of {list(SUPPORTED_PROFILES)}"
        )
    return normalized


def _requested_ids(requested: Iterable[str] | str | None) -> tuple[str, ...]:
    if requested is None:
        return ()
    raw_items = requested.split(",") if isinstance(requested, str) else requested
    normalized = {
        str(item).strip().casefold()
        for item in raw_items
        if str(item).strip()
    }
    unknown = sorted(normalized - set(_REGISTRY), key=str.casefold)
    if unknown:
        raise ValueError(f"Unknown strategy capabilities: {unknown}")
    return tuple(sorted(normalized, key=str.casefold))


def list_capabilities() -> tuple[str, ...]:
    """Return stable, sorted capability identifiers."""
    return tuple(sorted(_REGISTRY, key=str.casefold))


def capability_payload(
    capability_id: str | None = None,
    *,
    runtime_id: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable registry payload or one capability record."""
    if capability_id is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "registry_version": REGISTRY_VERSION,
            "profiles": {
                profile: {"default_capabilities": list(_PROFILE_DEFAULTS[profile])}
                for profile in SUPPORTED_PROFILES
            },
            "capabilities": [
                _REGISTRY[item].to_payload(runtime_id=runtime_id)
                for item in list_capabilities()
            ],
        }
        if runtime_id:
            payload["runtime"] = standard_runtime_payload(runtime_id)
        return payload

    normalized = str(capability_id or "").strip().casefold()
    capability = _REGISTRY.get(normalized)
    if capability is None:
        raise ValueError(f"Unknown strategy capability: {capability_id!r}")
    payload = capability.to_payload(runtime_id=runtime_id)
    payload["registry_version"] = REGISTRY_VERSION
    return payload


def unavailable_runtime_capabilities(
    capability_ids: Iterable[str] | str | None,
    *,
    runtime_id: str = STANDARD_RUNTIME_ID,
) -> dict[str, tuple[str, ...]]:
    unavailable: dict[str, tuple[str, ...]] = {}
    for capability_id in _requested_ids(capability_ids):
        availability = _runtime_availability(_REGISTRY[capability_id], runtime_id)
        missing = tuple(availability["missing_capabilities"])
        if missing:
            unavailable[capability_id] = missing
    return dict(sorted(unavailable.items(), key=lambda item: item[0].casefold()))


def resolve_capabilities(
    profile: str,
    requested: Iterable[str] | str | None = (),
    excluded: Iterable[str] | str | None = (),
) -> tuple[str, ...]:
    """Resolve profile defaults and hard dependencies, then validate constraints."""
    normalized_profile = _require_profile(profile)
    requested_ids = set(_requested_ids(requested))
    excluded_ids = set(_requested_ids(excluded))
    overlap = sorted(requested_ids & excluded_ids, key=str.casefold)
    if overlap:
        raise ValueError(f"Capabilities cannot be both requested and excluded: {overlap}")
    selected = set(_PROFILE_DEFAULTS[normalized_profile])
    selected.update(requested_ids)
    unavailable_exclusions = sorted(excluded_ids - selected, key=str.casefold)
    if unavailable_exclusions:
        raise ValueError(
            "Cannot exclude capabilities that are not selected by the profile or request: "
            f"{unavailable_exclusions}"
        )
    required_defaults = {"time_series_rules"}
    if normalized_profile == "panel":
        required_defaults.update({"panel_features", "panel_signals"})
    required_exclusions = sorted(excluded_ids & required_defaults, key=str.casefold)
    if required_exclusions:
        raise ValueError(f"Cannot exclude required authoring capabilities: {required_exclusions}")
    selected.difference_update(excluded_ids)

    changed = True
    while changed:
        changed = False
        for capability_id in tuple(selected):
            capability = _REGISTRY[capability_id]
            if normalized_profile not in capability.profiles:
                raise ValueError(
                    f"Capability {capability_id!r} is not available for profile "
                    f"{normalized_profile!r}; supported profiles: {list(capability.profiles)}"
                )
            for dependency in capability.depends_on:
                if dependency in excluded_ids:
                    raise ValueError(
                        f"Capability {capability_id!r} requires excluded dependency {dependency!r}"
                    )
                if dependency not in selected:
                    selected.add(dependency)
                    changed = True

    for capability_id in sorted(selected, key=str.casefold):
        capability = _REGISTRY[capability_id]
        if normalized_profile not in capability.profiles:
            raise ValueError(
                f"Dependency {capability_id!r} is not available for profile {normalized_profile!r}"
            )
        if capability.requires_any and not selected.intersection(capability.requires_any):
            raise ValueError(
                f"Capability {capability_id!r} requires at least one of "
                f"{list(capability.requires_any)}"
            )
        conflicts = sorted(selected.intersection(capability.conflicts_with), key=str.casefold)
        if conflicts:
            raise ValueError(f"Capability {capability_id!r} conflicts with {conflicts}")

    return tuple(sorted(selected, key=str.casefold))


__all__ = [
    "REGISTRY_VERSION",
    "SCHEMA_VERSION",
    "SUPPORTED_PROFILES",
    "Capability",
    "capability_payload",
    "list_capabilities",
    "resolve_capabilities",
    "unavailable_runtime_capabilities",
]
