"""Small quote helpers shared by legacy and cash net-value paths.

This module intentionally has no execution-engine imports.  Keeping quote
validation and valuation-mark construction here lets the legacy curve remain
lazy with respect to the optional cash execution engine.
"""

from __future__ import annotations

import numpy as np

from core.constants import FLOAT_TOLERANCE


def is_valid_quote_price(value: object) -> bool:
    """Return whether a quote can be used for a fill or a mark."""
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(np.isfinite(price) and price > FLOAT_TOLERANCE)


def build_last_valid_mark_prices(
    open_arr: np.ndarray,
    close_arr: np.ndarray,
) -> np.ndarray:
    """Build end-of-row marks without turning invalid quotes into fills.

    A valid close advances the mark. Before the first valid close, a valid open
    can seed it so a valid open fill is not immediately valued at zero merely
    because that day's close is unavailable.
    """
    if len(open_arr) != len(close_arr):
        raise ValueError("open_arr and close_arr must have the same length")

    marks = np.full(len(close_arr), np.nan, dtype="float64")
    last_mark = np.nan
    for idx in range(len(close_arr)):
        close_price = close_arr[idx]
        if is_valid_quote_price(close_price):
            last_mark = float(close_price)
        elif not is_valid_quote_price(last_mark) and is_valid_quote_price(open_arr[idx]):
            last_mark = float(open_arr[idx])
        marks[idx] = last_mark
    return marks


__all__ = ["build_last_valid_mark_prices", "is_valid_quote_price"]
