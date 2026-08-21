from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyValidationCase:
    name: str
    description: str
    start_time: str
    end_time: str
    gate_required: bool
    overrides: dict[str, Any]
    stock_code_list: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class StrategyValidationGate:
    name: str
    kind: str
    case: str
    path: str
    value: float
    op: str = ">="
    base_case: str = ""
    level: str = "hard"
    description: str = ""
    min_fraction: float = 1.0


@dataclass(frozen=True, slots=True)
class StrategyValidationProtocol:
    version: str
    description: str
    promotion_target: str
    defaults: dict[str, Any]
    cases: tuple[StrategyValidationCase, ...]
    gates: tuple[StrategyValidationGate, ...]
    research_governance: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "StrategyValidationCase",
    "StrategyValidationGate",
    "StrategyValidationProtocol",
]
