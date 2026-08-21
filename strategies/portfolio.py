"""Strategy-facing target-weight contracts and validation helpers."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any

import pandas as pd

from .advanced import PortfolioConstructionSpec


@dataclass(frozen=True, slots=True)
class TargetWeightContext:
    trade_date: pd.Timestamp
    market_slice: pd.DataFrame
    previous_weights: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({})
    )
    parameters: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class TargetWeightPlan:
    weights: Mapping[str, float]
    construction: PortfolioConstructionSpec

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": dict(self.weights),
            "portfolio_construction": self.construction.to_dict(),
        }


def _parse_target_weight_mapping(weights: Mapping[str, object]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for raw_symbol, raw_weight in sorted(weights.items(), key=lambda item: str(item[0]).casefold()):
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol:
            raise ValueError("target weight contains an empty symbol")
        if isinstance(raw_weight, bool):
            raise ValueError(f"target weight for {symbol} must be numeric, not bool")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"target weight for {symbol} must be finite and non-negative")
        if symbol in parsed:
            raise ValueError(f"duplicate target-weight symbol: {symbol}")
        parsed[symbol] = weight
    return parsed


def _parse_previous_weight_mapping(
    previous_weights: Mapping[str, object],
) -> dict[str, float]:
    previous: dict[str, float] = {}
    for raw_symbol, raw_weight in sorted(
        previous_weights.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or symbol in previous:
            raise ValueError("previous_weights contains an empty or duplicate symbol")
        if isinstance(raw_weight, bool):
            raise ValueError(f"previous target weight for {symbol} must be numeric, not bool")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(
                f"previous target weight for {symbol} must be finite and non-negative"
            )
        previous[symbol] = weight
    return previous


def _validate_turnover(
    parsed: Mapping[str, float],
    previous_weights: Mapping[str, object] | None,
    *,
    max_turnover: float,
) -> None:
    if previous_weights is None:
        raise ValueError("previous_weights is required when max_turnover is configured")
    if not isinstance(previous_weights, Mapping):
        raise ValueError("previous_weights must be a mapping")
    previous = _parse_previous_weight_mapping(previous_weights)
    previous_total = sum(previous.values())
    if previous_total > 1.0 + 1e-12:
        raise ValueError("previous target weights must not exceed total exposure 1")
    symbols = set(parsed) | set(previous)
    asset_turnover = sum(
        abs(parsed.get(symbol, 0.0) - previous.get(symbol, 0.0))
        for symbol in symbols
    )
    cash_turnover = abs((1.0 - sum(parsed.values())) - (1.0 - previous_total))
    turnover = 0.5 * (asset_turnover + cash_turnover)
    if turnover > max_turnover + 1e-12:
        raise ValueError(
            f"target-weight turnover {turnover:.12g} exceeds max_turnover={max_turnover}"
        )


def validate_target_weights(
    weights: Mapping[str, object],
    *,
    construction: PortfolioConstructionSpec | Mapping[str, object] | None = None,
    previous_weights: Mapping[str, object] | None = None,
) -> TargetWeightPlan:
    if not isinstance(weights, Mapping) or not weights:
        raise ValueError("target weights must be a non-empty mapping")
    spec = (
        construction
        if isinstance(construction, PortfolioConstructionSpec)
        else PortfolioConstructionSpec.from_value(construction)
    )
    parsed = _parse_target_weight_mapping(weights)
    total = sum(parsed.values())
    investable = 1.0 - spec.cash_buffer
    if total <= 0:
        raise ValueError("target weights must contain positive total exposure")
    if spec.normalize_weights:
        parsed = {symbol: weight / total * investable for symbol, weight in parsed.items()}
    elif total > investable + 1e-12:
        raise ValueError(
            f"target weights sum to {total:.12g}, above investable exposure {investable:.12g}"
        )
    if spec.max_single_weight is not None:
        excessive = {
            symbol: weight
            for symbol, weight in parsed.items()
            if weight > spec.max_single_weight + 1e-12
        }
        if excessive:
            raise ValueError(
                "normalized target weights exceed "
                f"max_single_weight={spec.max_single_weight}: {excessive}"
            )
    if spec.max_turnover is not None:
        _validate_turnover(
            parsed,
            previous_weights,
            max_turnover=spec.max_turnover,
        )
    return TargetWeightPlan(
        weights=MappingProxyType(parsed),
        construction=spec,
    )


__all__ = [
    "TargetWeightContext",
    "TargetWeightPlan",
    "validate_target_weights",
]
