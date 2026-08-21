"""Process-level guards for the optional backtest JIT kernels.

The core backtest package creates its Numba dispatchers on each runtime
instance.  A Web batch creates one runtime per strategy, so that pattern can
recompile the same kernels repeatedly and contend on Numba's disk cache.  Web
execution keeps the core package untouched and installs these wrappers once
per process instead.
"""
from __future__ import annotations

import threading
from typing import Any


_INSTALL_LOCK = threading.Lock()
_CLOSE_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_PAR_LOCK = threading.Lock()
_NUMBA_PATCH_LOCK = threading.Lock()
_INSTALLED = False

_CLOSE_RESOLVED = False
_CLOSE_COMPILER: Any = None
_CLOSE_DISPATCHER: Any = None

_STATE_RESOLVED = False
_STATE_COMPILER: Any = None
_STATE_DISPATCHER: Any = None


def _compile_without_disk_cache(compiler, *args):
    """Run a core lazy compiler while disabling its dynamic disk cache.

    The core compilers import ``numba.njit`` inside their compiler functions.
    Temporarily replacing that symbol keeps the existing kernel definition and
    optional-import fallback while preventing a per-process cache lock.  The
    original symbol is restored even if compilation fails.
    """
    with _NUMBA_PATCH_LOCK:
        try:
            import numba
            original_njit = numba.njit
        except Exception:
            return compiler(*args)

        def uncached_njit(*decorator_args, **decorator_kwargs):
            decorator_kwargs["cache"] = False
            return original_njit(*decorator_args, **decorator_kwargs)

        numba.njit = uncached_njit
        try:
            return compiler(*args)
        finally:
            numba.njit = original_njit


def _get_close_dispatcher(positions_module):
    global _CLOSE_RESOLVED, _CLOSE_COMPILER, _CLOSE_DISPATCHER

    compiler = positions_module._compile_close_return_jit
    if _CLOSE_RESOLVED and _CLOSE_COMPILER is compiler:
        return _CLOSE_DISPATCHER
    with _CLOSE_LOCK:
        compiler = positions_module._compile_close_return_jit
        if _CLOSE_RESOLVED and _CLOSE_COMPILER is compiler:
            return _CLOSE_DISPATCHER
        _CLOSE_DISPATCHER = _compile_without_disk_cache(compiler)
        _CLOSE_COMPILER = compiler
        _CLOSE_RESOLVED = True
        return _CLOSE_DISPATCHER


def _get_state_dispatcher(seed_runtime, compiler):
    global _STATE_RESOLVED, _STATE_COMPILER, _STATE_DISPATCHER

    if _STATE_RESOLVED and _STATE_COMPILER is compiler:
        return _STATE_DISPATCHER
    with _STATE_LOCK:
        if _STATE_RESOLVED and _STATE_COMPILER is compiler:
            return _STATE_DISPATCHER
        # The core runtime owns the optional-import fallback and the kernel
        # definition, so use a throwaway seed only for the one compilation.
        _STATE_DISPATCHER = _compile_without_disk_cache(compiler, seed_runtime)
        _STATE_COMPILER = compiler
        _STATE_RESOLVED = True
        return _STATE_DISPATCHER


def install_shared_jit_dispatchers() -> None:
    """Install process-wide dispatcher wrappers for Web backtest sessions."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _INSTALL_LOCK:
        if _INSTALLED:
            return

        from backtest.netvalue import positions
        from backtest.trade_state_machine import TradeStateRuntime
        from core import returns_calculator

        net_value_runtime = positions.NetValueRuntime
        original_close_getter = net_value_runtime.get_close_return_jit

        def shared_close_getter(runtime):
            if runtime.close_jit_resolved:
                return runtime.calc_position_returns_close_jit
            dispatcher = _get_close_dispatcher(positions)
            runtime.close_jit_resolved = True
            runtime.calc_position_returns_close_jit = dispatcher
            return dispatcher

        shared_close_getter._web_shared_jit = True
        if not getattr(original_close_getter, "_web_shared_jit", False):
            net_value_runtime.get_close_return_jit = shared_close_getter

        original_state_getter = TradeStateRuntime.get_state_machine_jit

        def shared_state_getter(runtime):
            if runtime._state_machine_jit_resolved:
                return runtime._apply_trade_state_machine_jit
            # Keep the original bound method as the compiler.  It is called
            # with a seed runtime exactly once and retains its Numba fallback.
            seed = TradeStateRuntime()
            dispatcher = _get_state_dispatcher(seed, original_state_getter)
            runtime._state_machine_jit_resolved = True
            runtime._apply_trade_state_machine_jit = dispatcher
            return dispatcher

        shared_state_getter._web_shared_jit = True
        if not getattr(original_state_getter, "_web_shared_jit", False):
            TradeStateRuntime.get_state_machine_jit = shared_state_getter

        original_par_getter = returns_calculator._get_par_jit

        def shared_par_getter():
            if returns_calculator._NUMBA_JIT_RESOLVED:
                return returns_calculator._par_jit
            with _PAR_LOCK:
                if returns_calculator._NUMBA_JIT_RESOLVED:
                    return returns_calculator._par_jit
                return _compile_without_disk_cache(original_par_getter)

        shared_par_getter._web_shared_jit = True
        if not getattr(original_par_getter, "_web_shared_jit", False):
            returns_calculator._get_par_jit = shared_par_getter

        _INSTALLED = True


__all__ = ["install_shared_jit_dispatchers"]
