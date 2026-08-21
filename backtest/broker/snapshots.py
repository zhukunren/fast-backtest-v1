"""End-of-day broker position snapshots used by risk reconstruction."""
from __future__ import annotations

import math
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .engine import SimpleBroker


def _finite_at(values: np.ndarray | None, idx: int) -> float | None:
    if values is None or idx < 0 or idx >= len(values):
        return None
    try:
        value = float(values[idx])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _execution_pre_close(meta: dict[str, Any], idx: int) -> float | None:
    supplied = _finite_at(meta.get("pre_close"), idx)
    if supplied is not None and supplied > 0:
        return supplied
    fallback = _finite_at(meta.get("close"), idx - 1)
    return fallback if fallback is not None and fallback > 0 else None


def build_current_quote_validity(
    price_meta: dict[str, dict[str, Any]],
    *,
    codes: list[str] | tuple[str, ...],
    date_np: np.datetime64,
) -> dict[str, bool]:
    validity: dict[str, bool] = {}
    for code in codes:
        meta = price_meta.get(code)
        if meta is None:
            validity[code] = False
            continue
        dates = meta["dates"]
        idx = int(np.searchsorted(dates, date_np, side="left"))
        if idx >= len(dates) or dates[idx] != date_np:
            validity[code] = False
            continue
        close = _finite_at(meta.get("close"), idx)
        valid = close is not None and close > 0
        volume_values = meta.get("volume")
        if volume_values is not None:
            volume = _finite_at(volume_values, idx)
            valid = valid and volume is not None and volume > 0
        validity[code] = bool(valid)
    return validity


def build_execution_state(
    price_meta: dict[str, dict[str, Any]],
    *,
    codes: list[str] | tuple[str, ...],
    date_np: np.datetime64,
) -> dict[str, dict[str, Any]]:
    """Return the raw bar fields required to validate a synthetic risk fill."""
    states: dict[str, dict[str, Any]] = {}
    for code in codes:
        meta = price_meta.get(code)
        if meta is None:
            continue
        dates = meta["dates"]
        idx = int(np.searchsorted(dates, date_np, side="left"))
        if idx >= len(dates) or dates[idx] != date_np:
            continue
        stock_name_values = meta.get("stock_name")
        stock_name = None
        if stock_name_values is not None and 0 <= idx < len(stock_name_values):
            value = str(stock_name_values[idx]).strip()
            stock_name = value or None
        states[code] = {
            "open": _finite_at(meta.get("open"), idx),
            "high": _finite_at(meta.get("high"), idx),
            "low": _finite_at(meta.get("low"), idx),
            "execution_close": _finite_at(meta.get("close"), idx),
            "volume": _finite_at(meta.get("volume"), idx),
            "pre_close": _execution_pre_close(meta, idx),
            "stock_name": stock_name,
        }
    return states


def snapshot_positions(
    broker: SimpleBroker,
    *,
    close_map: dict[str, float],
    current_quote_validity: dict[str, bool] | None = None,
    execution_state_by_code: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, position in sorted(
        broker.positions.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        qty = int(getattr(position, "qty", 0) or 0)
        if qty <= 0:
            continue
        try:
            current_price = float(close_map.get(code, math.nan))
        except (TypeError, ValueError):
            current_price = math.nan
        current_quote_valid = (
            bool(current_quote_validity.get(code, False))
            if current_quote_validity is not None
            else math.isfinite(current_price) and current_price > 0
        )
        price = current_price if math.isfinite(current_price) and current_price > 0 else getattr(
            position,
            "last_price",
            None,
        )
        row: dict[str, Any] = {
            "stock_code": str(code),
            "qty": qty,
            # ``close`` is the valuation mark. It can be valid even when the
            # bar is not eligible for a new execution, for example on zero volume.
            "close": float(price) if price is not None else None,
            "current_quote_valid": current_quote_valid,
        }
        if execution_state_by_code is not None:
            row.update(execution_state_by_code.get(str(code), {}))
        rows.append(row)
    return rows


__all__ = ["build_current_quote_validity", "build_execution_state", "snapshot_positions"]
