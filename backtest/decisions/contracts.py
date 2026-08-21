"""Decision and market-data contracts for V1 portfolio engines."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias

import pandas as pd

from backtest.pipeline_result import TradeSegmentsBuildResult
from backtest.panel_native import QuoteStore, SelectionBatch

DECISION_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class DecisionIdentity:
    source: str
    source_hash: str | None = None
    schema_version: int = DECISION_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SignalDecisionArtifacts:
    backtest_summary_df: pd.DataFrame | None = None
    input_report_df: pd.DataFrame | None = None
    selected_df: pd.DataFrame | None = None
    candidate_df: pd.DataFrame | None = None
    daily_summary_df: pd.DataFrame | None = None
    topn_segments: TradeSegmentsBuildResult | None = None


@dataclass(frozen=True, slots=True)
class TargetWeightDecisionArtifacts:
    target_weights: Mapping[str, float]
    weight_schedule: Mapping[pd.Timestamp, Mapping[str, float]] | None = None


@dataclass(frozen=True, slots=True)
class SignalDecision:
    identity: DecisionIdentity
    selection: SelectionBatch
    quote_store: QuoteStore
    trade_days: pd.DatetimeIndex


@dataclass(frozen=True, slots=True)
class TargetWeightDecision:
    identity: DecisionIdentity
    target_weights: Mapping[str, float]
    symbol_type: str
    rebalance_frequency: str
    rebalance_dates: tuple[str, ...]
    buy_price_col: str
    sell_price_col: str
    mark_price_col: str
    normalize_weights: bool
    quote_store: QuoteStore
    artifacts: TargetWeightDecisionArtifacts
    # This is the official calendar execution axis, not QuoteStore.trade_days.
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | tuple[pd.Timestamp, ...] | None = None


Decision: TypeAlias = SignalDecision | TargetWeightDecision


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "Decision",
    "DecisionIdentity",
    "SignalDecision",
    "SignalDecisionArtifacts",
    "TargetWeightDecision",
    "TargetWeightDecisionArtifacts",
]
