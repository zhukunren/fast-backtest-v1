"""Per-batch reuse of the stock-basic universe for Web backtests.

The core batch engine imports ``get_all_stocks`` directly.  A Web library run
executes strategies sequentially in one worker thread, so this module installs
a short-lived dispatcher around that import while the worker is active.  It
keeps core behavior intact outside a Web batch and still lets every strategy
apply its own universe filters to an isolated frame copy.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from typing import Any

import pandas as pd


_ACTIVE_CACHE: ContextVar["BatchStockBasicCache | None"] = ContextVar(
    "web_batch_stock_basic_cache",
    default=None,
)
_PATCH_LOCK = threading.RLock()
_PATCH_DEPTH = 0
_PATCHED_MODULE: Any | None = None
_ORIGINAL_GET_ALL_STOCKS: Callable[..., pd.DataFrame] | None = None


@dataclass(frozen=True, slots=True)
class BatchStockBasicCacheStats:
    source_reads: int
    cache_hits: int
    rows: int | None
    error: str | None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "source_reads": self.source_reads,
            "cache_hits": self.cache_hits,
            "rows": self.rows,
            "error": self.error,
        }


class BatchStockBasicCache:
    """Cache the raw stock list once while preserving caller isolation."""

    def __init__(self, source: Callable[..., pd.DataFrame]) -> None:
        self._source = source
        self._lock = threading.Lock()
        self._frame: pd.DataFrame | None = None
        self._failure: tuple[type[Exception], str] | None = None
        self._source_reads = 0
        self._cache_hits = 0

    def get_all_stocks(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        with self._lock:
            if self._frame is not None:
                self._cache_hits += 1
                return self._frame.copy(deep=True)
            if self._failure is not None:
                self._cache_hits += 1
                error_type, message = self._failure
                try:
                    cached_error = error_type(message)
                except Exception:
                    cached_error = RuntimeError(message)
                raise cached_error

            self._source_reads += 1
            try:
                frame = self._source(*args, **kwargs)
                if not isinstance(frame, pd.DataFrame):
                    raise TypeError(
                        "get_all_stocks must return a pandas DataFrame, "
                        f"got {type(frame).__name__}"
                    )
            except Exception as exc:
                self._failure = (type(exc), str(exc))
                raise

            self._frame = frame.copy(deep=True)
            return self._frame.copy(deep=True)

    def stats(self) -> BatchStockBasicCacheStats:
        with self._lock:
            return BatchStockBasicCacheStats(
                source_reads=self._source_reads,
                cache_hits=self._cache_hits,
                rows=None if self._frame is None else int(len(self._frame)),
                error=None if self._failure is None else self._failure[1],
            )


def _dispatch_get_all_stocks(*args: Any, **kwargs: Any) -> pd.DataFrame:
    cache = _ACTIVE_CACHE.get()
    if cache is not None:
        return cache.get_all_stocks(*args, **kwargs)

    with _PATCH_LOCK:
        original = _ORIGINAL_GET_ALL_STOCKS
    if original is None:
        raise RuntimeError("Web batch stock_basic dispatcher is not initialized")
    return original(*args, **kwargs)


@contextmanager
def batch_stock_basic_scope() -> Iterator[BatchStockBasicCache]:
    """Reuse one raw stock list only for the current Web batch worker."""
    from backtest import engine_batch_support

    global _ORIGINAL_GET_ALL_STOCKS, _PATCH_DEPTH, _PATCHED_MODULE
    with _PATCH_LOCK:
        if _PATCH_DEPTH == 0:
            _PATCHED_MODULE = engine_batch_support
            _ORIGINAL_GET_ALL_STOCKS = engine_batch_support.get_all_stocks
            engine_batch_support.get_all_stocks = _dispatch_get_all_stocks
        elif _PATCHED_MODULE is not engine_batch_support:
            raise RuntimeError("Web batch stock_basic dispatcher changed unexpectedly")
        _PATCH_DEPTH += 1
        source = _ORIGINAL_GET_ALL_STOCKS

    if source is None:
        raise RuntimeError("Web batch stock_basic source is unavailable")

    cache = BatchStockBasicCache(source)
    token = _ACTIVE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE_CACHE.reset(token)
        with _PATCH_LOCK:
            _PATCH_DEPTH -= 1
            if _PATCH_DEPTH == 0:
                if _PATCHED_MODULE is not None and _ORIGINAL_GET_ALL_STOCKS is not None:
                    _PATCHED_MODULE.get_all_stocks = _ORIGINAL_GET_ALL_STOCKS
                _PATCHED_MODULE = None
                _ORIGINAL_GET_ALL_STOCKS = None


__all__ = [
    "BatchStockBasicCache",
    "BatchStockBasicCacheStats",
    "batch_stock_basic_scope",
]
