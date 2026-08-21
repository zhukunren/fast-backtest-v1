"""
Signal processing shared helpers.
"""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from core.exceptions import ConfigurationError
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.timing_flags import coerce_timing_flag_series

from core.constants import COL_TRADE_DATE

class BoardTimeCache:
    """Own cached board timing series for one runtime scope."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, str, str, str, int], pd.Series] = {}

    def get_or_build(
        self,
        build_board_time_ok: Callable[[str], pd.DataFrame],
        board_code: str,
        *,
        cache_key_scope: tuple[str, str, int] | None = None,
    ) -> pd.Series:
        key = _build_board_time_cache_key(
            build_board_time_ok,
            board_code,
            cache_key_scope=cache_key_scope,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        out = _build_board_time_series(build_board_time_ok(board_code), board_code=board_code)
        self._cache[key] = out
        return out

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        return len(self._cache)

def create_board_time_cache() -> BoardTimeCache:
    """Create an isolated board-time cache for tests or explicit embedding."""
    return BoardTimeCache()


def _resolve_board_time_cache_key_scope(
    cache_key_scope: tuple[str, str, int] | None = None,
) -> tuple[str, str, int]:
    if cache_key_scope is None:
        raise ConfigurationError("board timing cache 需要显式传入 cache_key_scope。")
    start_time, end_time, offset_days = cache_key_scope
    return start_time, end_time, offset_days


def build_board_time_cache_scope(cfg) -> tuple[str, str, int]:
    backtest_cfg = getattr(cfg, "backtest", None)
    return (
        str(getattr(backtest_cfg, "start_time", "") or ""),
        str(getattr(backtest_cfg, "end_time", "") or ""),
        int(getattr(backtest_cfg, "offset_days", 0) or 0),
    )


def _build_board_time_cache_key(
    build_board_time_ok,
    board_code: str,
    *,
    cache_key_scope: tuple[str, str, int] | None = None,
) -> tuple[int, str, str, str, int]:
    start_time, end_time, offset_days = _resolve_board_time_cache_key_scope(cache_key_scope)
    return (id(build_board_time_ok), str(board_code), start_time, end_time, offset_days)


def _build_board_time_series(board_df: pd.DataFrame, *, board_code: str) -> pd.Series:
    if board_df is None or board_df.empty:
        raise DataValidationError(f"板块指数数据为空: {board_code}")
    if COL_TRADE_DATE not in board_df.columns:
        raise DataValidationError(f"板块指数缺少列 {COL_TRADE_DATE}: {board_code}")

    value_col = "board_time_ok" if "board_time_ok" in board_df.columns else "time_ok"
    if value_col not in board_df.columns:
        raise DataValidationError(f"板块指数缺少列 board_time_ok/time_ok: {board_code}")

    normalized = ensure_trade_date_normalized(board_df, col=COL_TRADE_DATE)
    out = pd.Series(
        coerce_timing_flag_series(
            board_df[value_col],
            column_name=value_col,
            error_cls=DataValidationError,
        ).to_numpy(dtype=bool),
        index=normalized[COL_TRADE_DATE],
    )
    return out[~out.index.duplicated(keep="last")]


def get_board_time_series_cached(
    build_board_time_ok,
    board_code: str,
    *,
    cache: BoardTimeCache,
    cache_key_scope: tuple[str, str, int],
) -> pd.Series:
    """
    Cache board timing series by (callable identity, board code).

    The cache is intentionally scoped to the exact builder callable instead of
    only `board_code`, so different strategies can reuse their own board timing
    results without accidentally sharing precomputed frames across strategies.
    """
    return cache.get_or_build(
        build_board_time_ok,
        board_code,
        cache_key_scope=cache_key_scope,
    )


def clear_board_time_cache(*, cache: BoardTimeCache) -> None:
    cache.clear()


def get_board_time_cache_size(*, cache: BoardTimeCache) -> int:
    return cache.size()


__all__ = [
    "BoardTimeCache",
    "build_board_time_cache_scope",
    "clear_board_time_cache",
    "create_board_time_cache",
    "get_board_time_cache_size",
    "get_board_time_series_cached",
]
