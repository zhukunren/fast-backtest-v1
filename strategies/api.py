"""Lazy, stable public API for strategy modules."""
from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "ADVANCED_CONTRACT_VERSION": ("strategies.advanced", "ADVANCED_CONTRACT_VERSION"),
    "AdvancedStrategySpec": ("strategies.advanced", "AdvancedStrategySpec"),
    "AuxiliaryDataSpec": ("strategies.advanced", "AuxiliaryDataSpec"),
    "BaseStrategy": ("strategies.base", "BaseStrategy"),
    "BehavioralCheck": ("strategies.authoring.behavioral", "BehavioralCheck"),
    "BehavioralProbeInputs": ("strategies.authoring.behavioral", "BehavioralProbeInputs"),
    "BehavioralReport": ("strategies.authoring.behavioral", "BehavioralReport"),
    "CrossSectionTransformSpec": ("strategies.advanced", "CrossSectionTransformSpec"),
    "BoardTimingSpec": ("strategies.advanced", "BoardTimingSpec"),
    "COMPLETE_SPEC_SCHEMA_VERSION": ("strategies.spec", "COMPLETE_SPEC_SCHEMA_VERSION"),
    "CompleteStrategySpec": ("strategies.spec", "CompleteStrategySpec"),
    "IndexTimingSpec": ("strategies.advanced", "IndexTimingSpec"),
    "LoadedStrategyDescriptor": ("strategies.descriptor", "LoadedStrategyDescriptor"),
    "LoadedStrategyCache": ("strategies.runtime_resolver", "LoadedStrategyCache"),
    "MarketAggregateSpec": ("strategies.spec", "MarketAggregateSpec"),
    "MarketRankSpec": ("strategies.spec", "MarketRankSpec"),
    "FeatureBatch": ("core.panel_contracts", "FeatureBatch"),
    "ColumnStore": ("core.panel_native", "ColumnStore"),
    "PanelView": ("core.panel_native", "PanelView"),
    "PricePanel": ("core.panel_native", "PricePanel"),
    "QuoteStore": ("core.panel_native", "QuoteStore"),
    "SelectionBatch": ("core.panel_contracts", "SelectionBatch"),
    "SignalBatch": ("core.panel_contracts", "SignalBatch"),
    "PanelFeatureFunction": ("strategies.protocol", "PanelFeatureFunction"),
    "PANEL_EXECUTION_STAGES": (
        "strategies.authoring.requirements",
        "PANEL_EXECUTION_STAGES",
    ),
    "PANEL_SORTED_ATTR": ("core.invariants", "PANEL_SORTED_ATTR"),
    "PanelSignalFunction": ("strategies.protocol", "PanelSignalFunction"),
    "PortfolioConstructionSpec": ("strategies.advanced", "PortfolioConstructionSpec"),
    "PositionPolicySpec": ("strategies.advanced", "PositionPolicySpec"),
    "ReferenceSeriesSpec": ("strategies.advanced", "ReferenceSeriesSpec"),
    "RiskControlSpec": ("strategies.spec", "RiskControlSpec"),
    "RuntimeEnvironment": ("strategies.authoring.requirements", "RuntimeEnvironment"),
    "STANDARD_RUNTIME_CAPABILITIES": (
        "strategies.authoring.requirements",
        "STANDARD_RUNTIME_CAPABILITIES",
    ),
    "STANDARD_RUNTIME_ID": (
        "strategies.authoring.requirements",
        "STANDARD_RUNTIME_ID",
    ),
    "StrategyLike": ("strategies.protocol", "StrategyLike"),
    "StrategyParameterSpec": ("strategies.advanced", "StrategyParameterSpec"),
    "StrategyRequirements": ("strategies.authoring.requirements", "StrategyRequirements"),
    "StrategyField": ("strategies.authoring.fields", "StrategyField"),
    "StrategySpec": ("strategies.spec", "StrategySpec"),
    "StrategyDataInputs": ("strategies.point_in_time", "StrategyDataInputs"),
    "TargetWeightStrategyLike": ("strategies.protocol", "TargetWeightStrategyLike"),
    "TargetWeightFunction": ("strategies.protocol", "TargetWeightFunction"),
    "TargetWeightContext": ("strategies.portfolio", "TargetWeightContext"),
    "TargetWeightPlan": ("strategies.portfolio", "TargetWeightPlan"),
    "TimingSignalFunction": ("strategies.protocol", "TimingSignalFunction"),
    "SingleSignalFunction": ("strategies.protocol", "SingleSignalFunction"),
    "SingleSignalStrategyLike": ("strategies.protocol", "SingleSignalStrategyLike"),
    "apply_guardrails": ("strategies.templates.guardrails", "apply_guardrails"),
    "coerce_signal_bool_array": ("strategies.signals", "coerce_signal_bool_array"),
    "compute_feature_batch": ("strategies.frame_compute", "compute_feature_batch"),
    "compute_signal_batch": ("strategies.frame_compute", "compute_signal_batch"),
    "apply_cross_section_transforms": (
        "strategies.market_features",
        "apply_cross_section_transforms",
    ),
    "apply_declared_data_inputs": (
        "strategies.point_in_time",
        "apply_declared_data_inputs",
    ),
    "build_board_time_ok": ("strategies.timing", "build_board_time_ok"),
    "build_index_time_ok": ("strategies.timing", "build_index_time_ok"),
    "clear_loaded_strategy_cache": (
        "strategies.runtime_resolver",
        "clear_loaded_strategy_cache",
    ),
    "create_loaded_strategy_cache": (
        "strategies.runtime_resolver",
        "create_loaded_strategy_cache",
    ),
    "field_catalog_payload": ("strategies.authoring.fields", "field_catalog_payload"),
    "field_definition": ("strategies.authoring.fields", "field_definition"),
    "finalize_signals": ("strategies.signals", "finalize_signals"),
    "get_complete_strategy_spec": ("strategies.spec", "get_complete_strategy_spec"),
    "get_complete_strategy_specs": ("strategies.spec", "get_complete_strategy_specs"),
    "get_strategy_meta": ("strategies.spec", "get_strategy_meta"),
    "get_strategy_risk_config": ("strategies.spec", "get_strategy_risk_config"),
    "get_strategy_runtime_specs": ("strategies.spec", "get_strategy_runtime_specs"),
    "get_strategy_spec": ("strategies.spec", "get_strategy_spec"),
    "load_strategy": ("strategies.strategy_loader", "load_strategy"),
    "load_strategy_descriptor": ("strategies.strategy_loader", "load_strategy_descriptor"),
    "load_strategy_cached": ("strategies.runtime_resolver", "load_strategy_cached"),
    "load_strategy_descriptor_cached": (
        "strategies.runtime_resolver",
        "load_strategy_descriptor_cached",
    ),
    "preprocess_ohlcv": ("strategies.signals", "preprocess_ohlcv"),
    "preflight_strategy_requirements": (
        "strategies.authoring.requirements",
        "preflight_strategy_requirements",
    ),
    "point_in_time_join": ("strategies.point_in_time", "point_in_time_join"),
    "point_in_time_join_many": ("strategies.point_in_time", "point_in_time_join_many"),
    "align_reference_series": ("strategies.point_in_time", "align_reference_series"),
    "probe_strategy_behavior": (
        "strategies.authoring.behavioral",
        "probe_strategy_behavior",
    ),
    "read_board_timing_frame": ("strategies.timing", "read_board_timing_frame"),
    "read_index_timing_frame": ("strategies.timing", "read_index_timing_frame"),
    "resolve_backtest_window": ("strategies.timing", "resolve_backtest_window"),
    "resolve_declared_parameters": (
        "strategies.advanced",
        "resolve_declared_parameters",
    ),
    "resolve_main_limit_percent_for_strategy": (
        "strategies.timing",
        "resolve_main_limit_percent_for_strategy",
    ),
    "resolve_strategy_requirements": (
        "strategies.authoring.requirements",
        "resolve_strategy_requirements",
    ),
    "resolve_strategy_parameters": (
        "strategies.advanced",
        "resolve_strategy_parameters",
    ),
    "standard_runtime_payload": (
        "strategies.authoring.requirements",
        "standard_runtime_payload",
    ),
    "resolve_strategy": ("strategies.runtime_resolver", "resolve_strategy"),
    "resolve_single_signal_builder": (
        "strategies.runtime_resolver",
        "resolve_single_signal_builder",
    ),
    "validate_sort_spec_columns": ("strategies.validation", "validate_sort_spec_columns"),
    "validate_target_weights": ("strategies.portfolio", "validate_target_weights"),
    "validate_strategy_module": ("strategies.validation", "validate_strategy_module"),
    "validate_strategy_contract": ("strategies.spec", "validate_strategy_contract"),
    "write_complete_strategy_spec": ("strategies.spec", "write_complete_strategy_spec"),
}

__all__ = tuple(sorted(_EXPORTS, key=str.casefold))


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__), key=str.casefold)
