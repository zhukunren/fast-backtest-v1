from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from core.exceptions import DataValidationError
_WEIGHT_SUM_TOL = 1e-8


@dataclass(frozen=True, slots=True)
class StaticAllocationCostConfig:
    enable: bool = True
    commission_rate: float = 0.0003
    min_commission: float = 0.0
    stamp_duty_rate: float = 0.0
    slippage_bps: float = 0.0
    include_buy_commission: bool = True
    include_sell_commission: bool = True


@dataclass(frozen=True, slots=True)
class StaticAllocationConfig:
    target_weights: Mapping[str, float]
    target_weight_schedule: Mapping[object, Mapping[str, float]] | None = None
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    cash_buffer_pct: float = 0.0
    rebalance_frequency: str = "month_start"
    rebalance_dates: tuple[object, ...] = ()
    buy_price_col: str = "open"
    sell_price_col: str = "open"
    mark_price_col: str = "close"
    start_date: str | pd.Timestamp | None = None
    end_date: str | pd.Timestamp | None = None
    cost: StaticAllocationCostConfig = StaticAllocationCostConfig()
    # The standalone allocation kernel can be used without framework
    # constraints, while V1 engine/CLI callers inject the shared configuration.
    trade_constraints: object | None = None
    normalize_weights: bool = False
    # The execution axis is deliberately supplied by the caller.  It must come
    # from the configured market calendar rather than from the available quote rows.
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | tuple[pd.Timestamp, ...] | None = None


@dataclass(frozen=True, slots=True)
class StaticAllocationResult:
    merged_df: pd.DataFrame
    orders_df: pd.DataFrame
    fills_df: pd.DataFrame
    positions_df: pd.DataFrame
    target_weights: dict[str, float]


def validate_explicit_trade_days(
    trade_days: object,
    *,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    name: str = "trade_days",
) -> pd.DatetimeIndex:
    """Validate a caller-owned execution calendar without repairing its order."""
    if trade_days is None:
        raise DataValidationError(
            f"{name} must be explicitly supplied from the configured trade calendar."
        )
    if isinstance(trade_days, (str, bytes, pd.DataFrame)):
        raise DataValidationError(f"{name} must be an ordered sequence of dates.")
    try:
        raw_days = list(trade_days)
    except TypeError as exc:
        raise DataValidationError(f"{name} must be an ordered sequence of dates.") from exc
    if not raw_days:
        raise DataValidationError(f"{name} must be non-empty.")

    try:
        index = pd.DatetimeIndex(pd.to_datetime(raw_days, errors="raise"))
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} contains an invalid date.") from exc
    if index.tz is not None:
        raise DataValidationError(f"{name} must use timezone-naive trading dates.")
    if bool(index.isna().any()):
        raise DataValidationError(f"{name} contains an empty date.")

    index = index.normalize()
    if not index.is_monotonic_increasing:
        raise DataValidationError(f"{name} must be sorted in ascending order.")
    if index.has_duplicates:
        raise DataValidationError(f"{name} must not contain duplicate dates.")

    start = _normalize_bound(start_date)
    end = _normalize_bound(end_date)
    if start is not None and end is not None and start > end:
        raise DataValidationError(f"{name} has an invalid requested window: start_date > end_date.")
    outside = np.zeros(len(index), dtype=bool)
    if start is not None:
        outside |= index < start
    if end is not None:
        outside |= index > end
    if bool(outside.any()):
        sample = [pd.Timestamp(day).strftime("%Y-%m-%d") for day in index[outside][:5]]
        raise DataValidationError(
            f"{name} contains dates outside the requested window: {sample}"
        )
    return index


def normalize_target_weights(
    target_weights: Mapping[str, float],
    *,
    normalize: bool = False,
) -> dict[str, float]:
    if not isinstance(target_weights, Mapping) or not target_weights:
        raise DataValidationError("target_weights must be a non-empty mapping.")

    out: dict[str, float] = {}
    for raw_code, raw_weight in target_weights.items():
        code = str(raw_code or "").strip().upper()
        if not code:
            raise DataValidationError("target_weights contains an empty stock_code.")
        if code in out:
            raise DataValidationError(
                f"target_weights contains duplicate stock_code after normalization: {code}"
            )
        try:
            weight = float(raw_weight)
        except Exception as exc:
            raise DataValidationError(
                f"target weight for {code} is not numeric: {raw_weight!r}"
            ) from exc
        if not np.isfinite(weight) or weight < 0:
            raise DataValidationError(
                f"target weight for {code} must be finite and non-negative."
            )
        if weight > 0:
            out[code] = weight

    if not out:
        raise DataValidationError("target_weights must contain at least one positive weight.")
    total = float(sum(out.values()))
    if total <= 0:
        raise DataValidationError("target_weights sum must be positive.")
    sorted_weights = sorted(out.items(), key=lambda item: item[0].casefold())
    if normalize:
        return {code: weight / total for code, weight in sorted_weights}
    if abs(total - 1.0) > _WEIGHT_SUM_TOL:
        raise DataValidationError(f"target_weights must sum to 1.0, got {total:.12f}.")
    return dict(sorted_weights)


def normalize_target_weight_schedule(
    target_weight_schedule: Mapping[object, Mapping[str, float]] | None,
    *,
    normalize: bool = False,
) -> tuple[tuple[pd.Timestamp, dict[str, float]], ...]:
    if not target_weight_schedule:
        return ()
    if not isinstance(target_weight_schedule, Mapping):
        raise DataValidationError(
            "target_weight_schedule must be a mapping of date -> target_weights."
        )

    out: list[tuple[pd.Timestamp, dict[str, float]]] = []
    seen_dates: set[pd.Timestamp] = set()
    for raw_date, raw_weights in target_weight_schedule.items():
        date = _normalize_bound(raw_date)
        if date is None:
            raise DataValidationError(
                "target_weight_schedule contains an empty effective date."
            )
        if date in seen_dates:
            raise DataValidationError(
                f"target_weight_schedule contains duplicate effective date: {date.date()}"
            )
        seen_dates.add(date)
        out.append((date, normalize_target_weights(raw_weights, normalize=normalize)))
    return tuple(sorted(out, key=lambda item: item[0]))


def _target_weights_for_date(
    base_weights: Mapping[str, float],
    schedule: tuple[tuple[pd.Timestamp, dict[str, float]], ...],
    date: pd.Timestamp,
) -> Mapping[str, float]:
    active: Mapping[str, float] = base_weights
    current = pd.Timestamp(date).normalize()
    for effective_date, weights in schedule:
        if current < effective_date:
            break
        active = weights
    return active


def _collect_target_codes(
    base_weights: Mapping[str, float],
    schedule: tuple[tuple[pd.Timestamp, dict[str, float]], ...],
) -> tuple[str, ...]:
    codes = set(base_weights)
    for _effective_date, weights in schedule:
        codes.update(weights)
    return tuple(sorted(codes, key=str.casefold))


def _normalize_bound(value: str | pd.Timestamp | None) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    return pd.Timestamp(value).normalize()


__all__ = [
    "StaticAllocationConfig",
    "StaticAllocationCostConfig",
    "StaticAllocationResult",
    "_collect_target_codes",
    "_normalize_bound",
    "_target_weights_for_date",
    "normalize_target_weight_schedule",
    "normalize_target_weights",
    "validate_explicit_trade_days",
]
