"""Stable typing contracts for strategy authors and runtime consumers."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

if TYPE_CHECKING:
    from core.panel_contracts import FeatureBatch, SignalBatch
    from core.panel_native import PanelView
    from .portfolio import TargetWeightContext


SingleSignalFunction = Callable[[pd.DataFrame], pd.DataFrame]
PanelFeatureFunction = Callable[["PanelView"], "FeatureBatch"]
PanelSignalFunction = Callable[["PanelView"], "SignalBatch"]
TimingSignalFunction = Callable[[pd.DataFrame], pd.Series]
IndexTimingFunction = Callable[[str], pd.DataFrame]
BoardTimingFunction = Callable[[str], pd.DataFrame]
ResetFunction = Callable[[], None]
TargetWeightFunction = Callable[["TargetWeightContext"], Mapping[str, float]]


@runtime_checkable
class StrategyLike(Protocol):
    """Minimum strategy contract required by the V1 runtime."""

    STRATEGY_META: Mapping[str, object]


@runtime_checkable
class PanelFeatureStrategyLike(StrategyLike, Protocol):
    def compute_features(self, panel: PanelView) -> FeatureBatch:
        ...


@runtime_checkable
class PanelSignalStrategyLike(StrategyLike, Protocol):
    def compute_signals(self, panel: PanelView) -> SignalBatch:
        ...


@runtime_checkable
class SingleSignalStrategyLike(StrategyLike, Protocol):
    def build_single_signals(self, frame: pd.DataFrame) -> pd.DataFrame:
        ...


@runtime_checkable
class IndexTimingStrategyLike(StrategyLike, Protocol):
    def build_time_ok(self, stock_code: str) -> pd.DataFrame:
        ...


@runtime_checkable
class BoardTimingStrategyLike(StrategyLike, Protocol):
    def build_board_time_ok(self, board_code: str) -> pd.DataFrame:
        ...


@runtime_checkable
class ResettableStrategyLike(Protocol):
    def reset_runtime_state(self) -> None:
        ...


@runtime_checkable
class TargetWeightStrategyLike(StrategyLike, Protocol):
    """Optional target-weight decision hook for compatible portfolio engines."""

    def build_target_weights(self, context: TargetWeightContext) -> Mapping[str, float]:
        ...


__all__ = [
    "BoardTimingFunction",
    "BoardTimingStrategyLike",
    "IndexTimingFunction",
    "IndexTimingStrategyLike",
    "PanelFeatureFunction",
    "PanelFeatureStrategyLike",
    "PanelSignalFunction",
    "PanelSignalStrategyLike",
    "ResetFunction",
    "ResettableStrategyLike",
    "SingleSignalFunction",
    "SingleSignalStrategyLike",
    "StrategyLike",
    "TargetWeightFunction",
    "TargetWeightStrategyLike",
    "TimingSignalFunction",
]
