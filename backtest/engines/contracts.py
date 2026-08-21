"""Explicit request/result contracts for statically composed V1 engines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol, TypeAlias

import pandas as pd

if TYPE_CHECKING:
    from config.models import (
        AppConfig,
        PathConfig,
        PerformanceConfig,
        PortfolioConfig,
        RunConfig,
        TradeConstraintConfig,
        TransactionCostConfig,
    )
    from core.execution_policy import ExecutionPolicy
    from strategies.spec import RiskControlSpec, StrategySpec

from backtest.decisions import Decision
from backtest.result import ExecutionArtifacts


@dataclass(frozen=True, slots=True)
class RunSpec:
    mode: str
    start_time: str
    end_time: str


@dataclass(frozen=True, slots=True)
class EngineRuntimeConfig:
    paths: PathConfig
    run: RunConfig
    performance: PerformanceConfig
    portfolio: PortfolioConfig
    transaction_cost: TransactionCostConfig
    trade_constraints: TradeConstraintConfig

    @classmethod
    def from_app_config(cls, cfg: AppConfig) -> EngineRuntimeConfig:
        return cls(
            paths=cfg.paths,
            run=cfg.run,
            performance=cfg.performance,
            portfolio=cfg.portfolio,
            transaction_cost=cfg.transaction_cost,
            trade_constraints=cfg.trade_constraints,
        )


@dataclass(frozen=True, slots=True)
class EngineSpec:
    engine_id: str
    runtime_config: EngineRuntimeConfig
    strategy_spec: StrategySpec | None


@dataclass(frozen=True, slots=True)
class EngineServices:
    net_value_runtime: object | None = None
    logger: Any = None
    run_id: str | None = None
    output_root: Path | None = None


@dataclass(frozen=True, slots=True)
class SegmentAssessmentSource:
    trading_frames: Mapping[str, pd.DataFrame]
    accounting_mode: Literal["close", "cash"]


@dataclass(frozen=True, slots=True)
class LedgerAssessmentSource:
    equity_curve: pd.DataFrame | None
    fills_df: pd.DataFrame
    risk_fills_df: pd.DataFrame


AssessmentSource: TypeAlias = SegmentAssessmentSource | LedgerAssessmentSource


@dataclass(frozen=True, slots=True)
class EngineRequest:
    run_spec: RunSpec
    engine_spec: EngineSpec
    decision: Decision
    execution_policy: ExecutionPolicy
    risk_policy: RiskControlSpec
    services: EngineServices


@dataclass(frozen=True, slots=True)
class EngineResult:
    engine_id: str
    execution: ExecutionArtifacts
    diagnostics: dict[str, object]
    assessment_source: AssessmentSource | None = None


class PortfolioEngine(Protocol):
    engine_id: str

    def run(self, request: EngineRequest) -> EngineResult: ...


__all__ = [
    "AssessmentSource",
    "EngineRequest",
    "EngineResult",
    "EngineRuntimeConfig",
    "EngineServices",
    "EngineSpec",
    "LedgerAssessmentSource",
    "PortfolioEngine",
    "RunSpec",
    "SegmentAssessmentSource",
]
