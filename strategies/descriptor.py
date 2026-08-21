"""One parsed description of a loaded strategy module."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Mapping

from .advanced import AdvancedStrategySpec
from .authoring.requirements import StrategyRequirements, resolve_strategy_requirements
from .protocol import StrategyLike
from .spec import (
    CompleteStrategySpec,
    RiskControlSpec,
    StrategySpec,
    get_complete_strategy_spec,
)


_HOOK_NAMES = (
    "build_board_time_ok",
    "build_single_signals",
    "build_target_weights",
    "build_time_ok",
    "compute_features",
    "compute_signals",
    "reset_runtime_state",
)


def _frozen_mapping(value: object) -> Mapping[str, Any]:
    return MappingProxyType(dict(value)) if isinstance(value, Mapping) else MappingProxyType({})


@dataclass(frozen=True, slots=True)
class LoadedStrategyDescriptor:
    module_path: str
    module_name: str
    source_path: str | None
    strategy: StrategyLike
    contract: CompleteStrategySpec
    hooks: tuple[str, ...]
    requirements: StrategyRequirements
    semantics: Mapping[str, Any]
    description: Mapping[str, Any]
    limits: Mapping[str, Any]

    @property
    def spec(self) -> StrategySpec:
        return self.contract.strategy

    @property
    def risk_control(self) -> RiskControlSpec:
        return self.contract.risk_control

    @property
    def advanced(self) -> AdvancedStrategySpec:
        return self.contract.advanced

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self.contract.resolved_parameters

    @property
    def contract_hash(self) -> str:
        return self.contract.contract_hash

    @classmethod
    def build(
        cls,
        *,
        module_path: str,
        strategy: StrategyLike,
        module: ModuleType | None = None,
        contract: CompleteStrategySpec | None = None,
    ) -> LoadedStrategyDescriptor:
        resolved_contract = contract or get_complete_strategy_spec(module=strategy)
        hooks = tuple(name for name in _HOOK_NAMES if callable(getattr(strategy, name, None)))
        requirements = resolve_strategy_requirements(
            spec=resolved_contract.strategy,
            strategy=strategy,
            advanced_spec=resolved_contract.advanced,
        )
        if not requirements.ready:
            details = "; ".join(
                f"{issue.code}: {issue.message}"
                for issue in requirements.issues
                if issue.severity == "error"
            )
            raise ValueError(f"invalid strategy requirements: {details}")
        source = getattr(module, "__file__", None)
        source_path = str(Path(source).resolve()) if source else None
        return cls(
            module_path=str(module_path),
            module_name=str(getattr(module, "__name__", type(strategy).__module__)),
            source_path=source_path,
            strategy=strategy,
            contract=resolved_contract,
            hooks=hooks,
            requirements=requirements,
            semantics=_frozen_mapping(getattr(strategy, "STRATEGY_SEMANTICS", {})),
            description=_frozen_mapping(getattr(strategy, "STRATEGY_DESCRIPTION", {})),
            limits=_frozen_mapping(getattr(strategy, "STRATEGY_LIMITS", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_path": self.module_path,
            "module_name": self.module_name,
            "source_path": self.source_path,
            "contract_hash": self.contract_hash,
            "contract": self.contract.to_dict(),
            "hooks": list(self.hooks),
            "requirements": self.requirements.to_dict(),
            "semantics": dict(self.semantics),
            "description": dict(self.description),
            "limits": dict(self.limits),
        }


__all__ = ["LoadedStrategyDescriptor"]
