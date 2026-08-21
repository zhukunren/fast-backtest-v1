"""Runtime helpers for explicit strategy and descriptor resolution."""
from __future__ import annotations

from threading import RLock
from core.exceptions import StrategyError

from .descriptor import LoadedStrategyDescriptor
from .protocol import SingleSignalFunction, StrategyLike


class LoadedStrategyCache:
    """Own parsed strategy descriptors for one runtime scope."""

    def __init__(self) -> None:
        self._cache: dict[str, LoadedStrategyDescriptor] = {}
        self._lock = RLock()

    def clear(self, *, module_name: str | None = None) -> None:
        with self._lock:
            if module_name is None:
                self._cache.clear()
                return
            key = str(module_name or "").strip()
            if key:
                self._cache.pop(key, None)

    def load_descriptor(self, module_name: str) -> LoadedStrategyDescriptor:
        key = str(module_name or "").strip()
        if not key:
            raise StrategyError("strategy module must not be empty")
        with self._lock:
            descriptor = self._cache.get(key)
            if descriptor is None:
                descriptor = _load_descriptor(key)
                self._cache[key] = descriptor
            return descriptor

    def load(self, module_name: str) -> StrategyLike:
        return self.load_descriptor(module_name).strategy


_STRATEGY_CACHE = LoadedStrategyCache()


def create_loaded_strategy_cache() -> LoadedStrategyCache:
    return LoadedStrategyCache()


def clear_loaded_strategy_cache(
    *,
    module_name: str | None = None,
    cache: LoadedStrategyCache | None = None,
) -> None:
    (_STRATEGY_CACHE if cache is None else cache).clear(module_name=module_name)


def _load_descriptor(module_name: str) -> LoadedStrategyDescriptor:
    from .strategy_loader import load_strategy_descriptor

    return load_strategy_descriptor(module_name)


def load_strategy_descriptor_cached(
    module_name: str,
    *,
    cache: LoadedStrategyCache | None = None,
) -> LoadedStrategyDescriptor:
    return (_STRATEGY_CACHE if cache is None else cache).load_descriptor(module_name)


def load_strategy_cached(
    module_name: str,
    *,
    cache: LoadedStrategyCache | None = None,
) -> StrategyLike:
    return load_strategy_descriptor_cached(module_name, cache=cache).strategy


def resolve_strategy(
    *,
    strategy: StrategyLike | None = None,
    strategy_module_name: str | None = None,
    cfg=None,
) -> StrategyLike | None:
    if strategy is not None:
        return strategy
    module_name = str(
        strategy_module_name or getattr(getattr(cfg, "strategy", None), "module", "")
    ).strip()
    return load_strategy_cached(module_name) if module_name else None


def resolve_single_signal_builder(
    *,
    strategy: StrategyLike | None = None,
    strategy_module_name: str | None = None,
    cfg=None,
) -> SingleSignalFunction:
    strategy_obj = resolve_strategy(
        strategy=strategy,
        strategy_module_name=strategy_module_name,
        cfg=cfg,
    )
    fn = getattr(strategy_obj, "build_single_signals", None)
    if not callable(fn):
        raise StrategyError("single run requires callable strategy.build_single_signals")
    return fn


__all__ = [
    "LoadedStrategyCache",
    "clear_loaded_strategy_cache",
    "create_loaded_strategy_cache",
    "load_strategy_cached",
    "load_strategy_descriptor_cached",
    "resolve_strategy",
    "resolve_single_signal_builder",
]
