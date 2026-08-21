"""Immutable registry for the five built-in V1 portfolio engines."""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Callable, Iterable, Literal, Mapping, cast

from core.exceptions import DataValidationError
from core.portfolio_engine import normalize_portfolio_engine

from .contracts import PortfolioEngine


DecisionKind = Literal["signal", "target_weight"]
EngineFactory = Callable[[], PortfolioEngine]

_SUPPORTED_MODES = frozenset({"curve", "assess"})
_EXECUTION_KINDS = frozenset({"vectorized", "ledger", "rebalance"})
_ENGINE_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True, slots=True)
class EngineRegistration:
    engine_id: str
    api_version: int
    factory: EngineFactory
    supported_modes: frozenset[str]
    decision_kind: DecisionKind
    execution_kinds: frozenset[str]
    requires_strategy: bool

    def __post_init__(self) -> None:
        normalized = normalize_portfolio_engine(self.engine_id)
        if normalized != self.engine_id or not _ENGINE_ID_RE.fullmatch(self.engine_id):
            raise ValueError(f"engine id must be normalized: {self.engine_id!r}")
        if not isinstance(self.api_version, int) or isinstance(self.api_version, bool):
            raise ValueError(f"engine {self.engine_id!r} has an invalid API version")
        if self.api_version <= 0:
            raise ValueError(f"engine {self.engine_id!r} has a non-positive API version")
        if not callable(self.factory):
            raise ValueError(f"engine {self.engine_id!r} has no callable factory")
        if not self.supported_modes or not self.supported_modes <= _SUPPORTED_MODES:
            raise ValueError(f"engine {self.engine_id!r} has invalid supported modes")
        if self.decision_kind not in {"signal", "target_weight"}:
            raise ValueError(f"engine {self.engine_id!r} has an invalid decision kind")
        if not self.execution_kinds or not self.execution_kinds <= _EXECUTION_KINDS:
            raise ValueError(f"engine {self.engine_id!r} has invalid execution kinds")
        if not isinstance(self.requires_strategy, bool):
            raise ValueError(f"engine {self.engine_id!r} has an invalid requires_strategy flag")
        if self.decision_kind == "target_weight":
            if self.requires_strategy or self.supported_modes != frozenset({"curve"}):
                raise ValueError(f"target-weight engine {self.engine_id!r} has inconsistent capabilities")
            if self.execution_kinds != frozenset({"rebalance"}):
                raise ValueError(f"target-weight engine {self.engine_id!r} has inconsistent execution kinds")
        elif not self.requires_strategy or "rebalance" in self.execution_kinds:
            raise ValueError(f"signal engine {self.engine_id!r} has inconsistent capabilities")


def compose_legacy_preset() -> PortfolioEngine:
    from .vectorized import VectorizedPreset

    return cast(PortfolioEngine, VectorizedPreset("legacy"))


def compose_slot_preset() -> PortfolioEngine:
    from .vectorized import VectorizedPreset

    return cast(PortfolioEngine, VectorizedPreset("slot"))


def compose_slot_broker_preset() -> PortfolioEngine:
    from .vectorized import VectorizedPreset

    return cast(PortfolioEngine, VectorizedPreset("slot_broker"))


def compose_broker_preset() -> PortfolioEngine:
    from .broker import BrokerPreset

    return cast(PortfolioEngine, BrokerPreset())


def compose_fixed_rebalance_preset() -> PortfolioEngine:
    from .rebalance import FixedRebalancePreset

    return cast(PortfolioEngine, FixedRebalancePreset())


def build_engine_registry(
    registrations: Iterable[EngineRegistration],
) -> Mapping[str, EngineRegistration]:
    registry: dict[str, EngineRegistration] = {}
    for registration in registrations:
        if not isinstance(registration, EngineRegistration):
            raise ValueError(f"engine registry contains an invalid registration: {registration!r}")
        if registration.engine_id in registry:
            raise ValueError(f"duplicate engine id: {registration.engine_id!r}")
        registry[registration.engine_id] = registration
    if not registry:
        raise ValueError("engine registry cannot be empty")
    return MappingProxyType(registry)


_REGISTRATIONS = (
    EngineRegistration(
        engine_id="legacy",
        api_version=1,
        factory=compose_legacy_preset,
        supported_modes=frozenset({"curve", "assess"}),
        decision_kind="signal",
        execution_kinds=frozenset({"vectorized"}),
        requires_strategy=True,
    ),
    EngineRegistration(
        engine_id="slot",
        api_version=1,
        factory=compose_slot_preset,
        supported_modes=frozenset({"curve", "assess"}),
        decision_kind="signal",
        execution_kinds=frozenset({"vectorized"}),
        requires_strategy=True,
    ),
    EngineRegistration(
        engine_id="slot_broker",
        api_version=1,
        factory=compose_slot_broker_preset,
        supported_modes=frozenset({"curve", "assess"}),
        decision_kind="signal",
        execution_kinds=frozenset({"ledger"}),
        requires_strategy=True,
    ),
    EngineRegistration(
        engine_id="broker",
        api_version=1,
        factory=compose_broker_preset,
        supported_modes=frozenset({"curve", "assess"}),
        decision_kind="signal",
        execution_kinds=frozenset({"ledger"}),
        requires_strategy=True,
    ),
    EngineRegistration(
        engine_id="fixed_rebalance",
        api_version=1,
        factory=compose_fixed_rebalance_preset,
        supported_modes=frozenset({"curve"}),
        decision_kind="target_weight",
        execution_kinds=frozenset({"rebalance"}),
        requires_strategy=False,
    ),
)

ENGINE_REGISTRY = build_engine_registry(_REGISTRATIONS)
ENGINE_IDS = tuple(ENGINE_REGISTRY)


def engine_registration(engine_id: object) -> EngineRegistration:
    normalized = normalize_portfolio_engine(engine_id)
    try:
        return ENGINE_REGISTRY[normalized]
    except KeyError as exc:
        raise DataValidationError(
            f"unsupported portfolio engine {normalized!r}; expected one of {ENGINE_IDS}"
        ) from exc


def compose_engine(engine_id: object) -> PortfolioEngine:
    registration = engine_registration(engine_id)
    engine = registration.factory()
    if normalize_portfolio_engine(getattr(engine, "engine_id", None)) != registration.engine_id:
        raise DataValidationError(
            f"engine factory identity mismatch for {registration.engine_id!r}"
        )
    return engine


__all__ = [
    "ENGINE_IDS",
    "ENGINE_REGISTRY",
    "DecisionKind",
    "EngineFactory",
    "EngineRegistration",
    "build_engine_registry",
    "compose_broker_preset",
    "compose_engine",
    "compose_fixed_rebalance_preset",
    "compose_legacy_preset",
    "compose_slot_broker_preset",
    "compose_slot_preset",
    "engine_registration",
]
