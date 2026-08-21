"""Lazy public API for strategy-authoring tools."""
from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "BASE_INPUT_FIELDS": ("strategies.authoring.fields", "BASE_INPUT_FIELDS"),
    "BehavioralCheck": ("strategies.authoring.behavioral", "BehavioralCheck"),
    "BehavioralProbeInputs": (
        "strategies.authoring.behavioral",
        "BehavioralProbeInputs",
    ),
    "BehavioralReport": ("strategies.authoring.behavioral", "BehavioralReport"),
    "CHECK_PROFILES": ("strategies.authoring.checks", "CHECK_PROFILES"),
    "Capability": ("strategies.authoring.capabilities", "Capability"),
    "DRAFT_CHECK_PROFILE": ("strategies.authoring.checks", "DRAFT_CHECK_PROFILE"),
    "FIELD_CATALOG_VERSION": (
        "strategies.authoring.fields",
        "FIELD_CATALOG_VERSION",
    ),
    "PANEL_EXECUTION_STAGES": (
        "strategies.authoring.requirements",
        "PANEL_EXECUTION_STAGES",
    ),
    "PROMOTION_CHECK_PROFILE": (
        "strategies.authoring.checks",
        "PROMOTION_CHECK_PROFILE",
    ),
    "RuntimeEnvironment": (
        "strategies.authoring.requirements",
        "RuntimeEnvironment",
    ),
    "STANDARD_RUNTIME_CAPABILITIES": (
        "strategies.authoring.requirements",
        "STANDARD_RUNTIME_CAPABILITIES",
    ),
    "STANDARD_RUNTIME_ID": (
        "strategies.authoring.requirements",
        "STANDARD_RUNTIME_ID",
    ),
    "SKIPPED_CHECK_PROFILE": (
        "strategies.authoring.checks",
        "SKIPPED_CHECK_PROFILE",
    ),
    "StrategyRequirements": (
        "strategies.authoring.requirements",
        "StrategyRequirements",
    ),
    "capability_payload": (
        "strategies.authoring.capabilities",
        "capability_payload",
    ),
    "check_strategy_path": ("strategies.authoring.checks", "check_strategy_path"),
    "field_catalog_payload": (
        "strategies.authoring.fields",
        "field_catalog_payload",
    ),
    "field_definition": ("strategies.authoring.fields", "field_definition"),
    "list_capabilities": (
        "strategies.authoring.capabilities",
        "list_capabilities",
    ),
    "normalize_check_profile": (
        "strategies.authoring.checks",
        "normalize_check_profile",
    ),
    "preflight_strategy_requirements": (
        "strategies.authoring.requirements",
        "preflight_strategy_requirements",
    ),
    "probe_strategy_behavior": (
        "strategies.authoring.behavioral",
        "probe_strategy_behavior",
    ),
    "resolve_capabilities": (
        "strategies.authoring.capabilities",
        "resolve_capabilities",
    ),
    "resolve_strategy_requirements": (
        "strategies.authoring.requirements",
        "resolve_strategy_requirements",
    ),
    "scaffold_strategy": ("strategies.authoring.scaffold", "scaffold_strategy"),
    "standard_runtime_payload": (
        "strategies.authoring.requirements",
        "standard_runtime_payload",
    ),
    "unavailable_runtime_capabilities": (
        "strategies.authoring.capabilities",
        "unavailable_runtime_capabilities",
    ),
    "validate_declared_fields": (
        "strategies.authoring.fields",
        "validate_declared_fields",
    ),
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
