from __future__ import annotations

from collections.abc import Callable as AbcCallable
from typing import Callable, Set, Tuple

import pandas as pd

from core.constants import COL_STOCK_CODE, COL_TRADE_DATE
from core.exceptions import ConfigurationError
from core.invariants import ensure_trade_date_normalized
from core.timing_flags import coerce_timing_flag_series as _coerce_timing_flag_series


class BoardMemberCache:
    """Own cached board-member dictionary and lookup results."""

    def __init__(self, member_dict: dict | None = None) -> None:
        self._member_dict: dict | None = member_dict
        self._members_by_code: dict[str, set[str]] = {}
        self._all_codes: Tuple[str, ...] | None = None

    def _load_member_dict(self) -> dict:
        if self._member_dict is None:
            raise ConfigurationError("BoardMemberCache 需要显式注入 member_dict。")
        return self._member_dict

    def get_members(self, ths_index_code: str) -> Set[str]:
        code = str(ths_index_code or "").strip()
        if not code:
            return set()
        cached = self._members_by_code.get(code)
        if cached is not None:
            return set(cached)

        data = self._load_member_dict()
        members = data.get(code) or data.get(code.upper()) or data.get(code.lower())
        if members is None:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "board_members_not_found: %s (not in ths_index_member_dict.json), returning empty set",
                code,
            )
            cached_members: set[str] = set()
        else:
            if not isinstance(members, (list, tuple)):
                raise TypeError(f"板块成分应为列表，{code} 实际为 {type(members).__name__}")
            cached_members = {str(member).strip().upper() for member in members if str(member).strip()}

        self._members_by_code[code] = cached_members
        return set(cached_members)

    def get_all_codes(self) -> Tuple[str, ...]:
        if self._all_codes is None:
            data = self._load_member_dict()
            self._all_codes = tuple(sorted(data.keys(), key=str.casefold))
        return self._all_codes

    def clear(self) -> None:
        self._member_dict = None
        self._members_by_code.clear()
        self._all_codes = None

    def size(self) -> dict[str, int]:
        return {
            "member_dict_loaded": int(self._member_dict is not None),
            "members_by_code": len(self._members_by_code),
            "all_codes_loaded": int(self._all_codes is not None),
        }


class IndexTimingCache:
    """Own cached index timing frames for one runtime scope."""

    def __init__(self) -> None:
        self._cache: dict[tuple[object, str, str, str, int], pd.DataFrame] = {}

    def get_or_build(
        self,
        build_time_ok: AbcCallable[[str], pd.DataFrame],
        stock_code: str,
        *,
        cache_key_scope: tuple[str, str, int] | None = None,
    ) -> pd.DataFrame:
        cache_key = _build_timing_cache_key(
            build_time_ok,
            stock_code,
            cache_key_scope=cache_key_scope,
        )
        cached = self._cache.get(cache_key)
        if cached is None:
            cached = _normalize_timing_frame(build_time_ok(stock_code))
            self._cache[cache_key] = cached
        return _normalize_timing_frame(cached.copy())

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        return len(self._cache)

def create_index_timing_cache() -> IndexTimingCache:
    """Create an isolated index-timing cache for tests or explicit embedding."""
    return IndexTimingCache()


def create_board_member_cache(member_dict: dict | None = None) -> BoardMemberCache:
    """Create an isolated board-member cache for tests or explicit embedding."""
    return BoardMemberCache(member_dict=member_dict)


def _normalize_timing_frame(index_df: pd.DataFrame) -> pd.DataFrame:
    if index_df is None or index_df.empty:
        raise ValueError("index data empty for timing filter")
    if COL_TRADE_DATE not in index_df.columns or "time_ok" not in index_df.columns:
        raise ValueError(f"index data missing columns: {index_df.columns.tolist()}")

    normalized = index_df.loc[:, [COL_TRADE_DATE, "time_ok"]].copy()
    normalized = ensure_trade_date_normalized(normalized, col=COL_TRADE_DATE)
    normalized = normalized.sort_values(COL_TRADE_DATE, kind="mergesort")
    normalized["time_ok"] = _coerce_timing_flag_series(normalized["time_ok"], column_name="time_ok").to_numpy(
        dtype=bool
    )
    normalized = normalized.drop_duplicates(subset=[COL_TRADE_DATE], keep="last").reset_index(drop=True)
    return normalized


def clear_timing_cache(*, cache: IndexTimingCache) -> None:
    """Clear an explicit timing cache."""
    cache.clear()


def get_timing_cache_size(*, cache: IndexTimingCache) -> int:
    return cache.size()


def clear_board_member_cache(*, cache: BoardMemberCache) -> None:
    cache.clear()


def get_board_member_cache_size(*, cache: BoardMemberCache) -> dict[str, int]:
    return cache.size()


def align_timing_signal(timing_ok: pd.Series, df: pd.DataFrame) -> pd.Series:
    """Align a timing signal series to the target DataFrame index."""
    if not isinstance(timing_ok, pd.Series):
        timing_ok = pd.Series(timing_ok, index=df.index)

    if not timing_ok.index.equals(df.index):
        if isinstance(timing_ok.index, pd.DatetimeIndex) and COL_TRADE_DATE in df.columns:
            timing_ok = timing_ok[~timing_ok.index.duplicated(keep="last")]
            timing_ok = pd.Series(df[COL_TRADE_DATE].map(timing_ok), index=df.index)
        elif len(timing_ok) == len(df):
            timing_ok = timing_ok.reset_index(drop=True)
            timing_ok.index = df.index
        else:
            raise ValueError(
                "timing_ok must align with df: "
                f"len(timing_ok)={len(timing_ok)} len(df)={len(df)}"
            )

    return _coerce_timing_flag_series(timing_ok, column_name="time_ok")


def _resolve_timing_cache_key_scope(
    cache_key_scope: tuple[str, str, int] | None = None,
) -> tuple[str, str, int]:
    if cache_key_scope is None:
        raise ConfigurationError("timing cache 需要显式传入 cache_key_scope。")
    start_time, end_time, offset_days = cache_key_scope
    return start_time, end_time, offset_days


def build_timing_cache_scope(cfg) -> tuple[str, str, int]:
    backtest_cfg = getattr(cfg, "backtest", None)
    return (
        str(getattr(backtest_cfg, "start_time", "") or ""),
        str(getattr(backtest_cfg, "end_time", "") or ""),
        int(getattr(backtest_cfg, "offset_days", 0) or 0),
    )


def _build_timing_cache_key(
    build_time_ok: Callable[[str], pd.DataFrame],
    stock_code: str,
    *,
    cache_key_scope: tuple[str, str, int] | None = None,
) -> tuple[object, str, str, str, int]:
    """
    Build the per-process timing cache key.

    By default the cache is scoped to the timing builder plus backtest window.
    Callables that truly vary by stock can opt into per-stock cache separation
    by setting `_timing_cache_by_stock = True`.
    """
    start_time, end_time, offset_days = _resolve_timing_cache_key_scope(cache_key_scope)
    cache_by_stock = bool(getattr(build_time_ok, "_timing_cache_by_stock", False))
    stock_key = str(stock_code or "") if cache_by_stock else ""
    return (build_time_ok, stock_key, start_time, end_time, offset_days)


def _resolve_cached_timing_index_frame(
    build_time_ok: Callable[[str], pd.DataFrame],
    stock_code: str,
    *,
    cache: IndexTimingCache,
    cache_key_scope: tuple[str, str, int],
) -> pd.DataFrame:
    return cache.get_or_build(
        build_time_ok,
        stock_code,
        cache_key_scope=cache_key_scope,
    )


def apply_timing_filter(
    df: pd.DataFrame,
    *,
    build_time_ok: Callable[[str], pd.DataFrame] | None = None,
    use_index_timing: bool = False,
    apply_filter: bool | None = None,
    timing_cache: IndexTimingCache | None = None,
    cache_key_scope: tuple[str, str, int] | None = None,
) -> pd.Series:
    """
    Align timing signal and optionally apply it immediately.

    When apply_filter is False, this returns an all-True series and leaves the
    real timing decision to the later merge/topn stage.
    """
    if apply_filter is None:
        raise ConfigurationError("apply_timing_filter 需要显式传入 apply_filter。")
    apply_now = bool(apply_filter)
    timing_ok = pd.Series(True, index=df.index, dtype=bool)

    if use_index_timing and apply_now:
        if build_time_ok is None:
            raise ValueError("build_time_ok is required when use_index_timing=True")
        if timing_cache is None:
            raise ConfigurationError("apply_timing_filter 需要显式传入 timing_cache。")
        if cache_key_scope is None:
            raise ConfigurationError("apply_timing_filter 需要显式传入 cache_key_scope。")

        stock_code = ""
        if COL_STOCK_CODE in df.columns:
            codes = df[COL_STOCK_CODE].dropna().astype(str).str.strip().unique()
            if len(codes) == 1:
                stock_code = codes[0]

        index_df = _resolve_cached_timing_index_frame(
            build_time_ok,
            stock_code,
            cache=timing_cache,
            cache_key_scope=cache_key_scope,
        )

        idx_series = pd.Series(
            index_df["time_ok"].to_numpy(dtype=bool),
            index=index_df[COL_TRADE_DATE],
        )
        timing_ok = align_timing_signal(idx_series, df)
        df["time_ok"] = timing_ok
        return timing_ok.astype(bool)

    return pd.Series(True, index=df.index, dtype=bool)


__all__ = [
    "BoardMemberCache",
    "IndexTimingCache",
    "align_timing_signal",
    "apply_timing_filter",
    "build_timing_cache_scope",
    "clear_board_member_cache",
    "clear_timing_cache",
    "create_board_member_cache",
    "create_index_timing_cache",
    "get_board_member_cache_size",
    "get_timing_cache_size",
]
