"""Shared helpers for classifying price-data read failures."""
from __future__ import annotations

from .exceptions import PriceDataError


_EXPECTED_MISSING_DATA_HINTS = (
    "查询结果为空",
    "stock_data 文件不存在",
    "本地行情文件不存在",
    "表不存在",
    "no such table",
    "mysql 查询列不存在",
    "行情缺少配置列",
    "unknown column",
)


def is_expected_missing_price_data_error(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, PriceDataError)):
        return True
    msg = str(exc).lower()
    return any(hint.lower() in msg for hint in _EXPECTED_MISSING_DATA_HINTS)


__all__ = ["is_expected_missing_price_data_error"]
