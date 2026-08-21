from __future__ import annotations

from collections.abc import Callable


ResetCallback = Callable[[], object]


def _runtime_reset_callbacks(
    *,
    clear_strategy_cache: bool,
) -> tuple[tuple[str, ResetCallback], ...]:
    """Resolve the explicit reset surface only when a reset is requested."""
    from backtest.netvalue.positions import reset_net_value_jit_warning
    from backtest.trade_state_machine import reset_state_machine_cache, reset_t1_stats
    from core.dataframe_schema import clear_dataframe_schema_cache
    from core.validation import clear_validation_cache
    from data.trade_calendar import clear_trade_calendar_cache

    callbacks: list[tuple[str, ResetCallback]] = [
        (
            "backtest.netvalue.positions.reset_net_value_jit_warning",
            reset_net_value_jit_warning,
        ),
        (
            "core.dataframe_schema.clear_dataframe_schema_cache",
            clear_dataframe_schema_cache,
        ),
        (
            "backtest.trade_state_machine.reset_state_machine_cache",
            reset_state_machine_cache,
        ),
        ("backtest.trade_state_machine.reset_t1_stats", reset_t1_stats),
        ("core.validation.clear_validation_cache", clear_validation_cache),
        ("data.trade_calendar.clear_trade_calendar_cache", clear_trade_calendar_cache),
    ]
    if clear_strategy_cache:
        from strategies.runtime_resolver import clear_loaded_strategy_cache

        callbacks.append(
            (
                "strategies.runtime_resolver.clear_loaded_strategy_cache",
                clear_loaded_strategy_cache,
            )
        )
    return tuple(callbacks)


def _reset_strategy_runtime(strategy: object | None) -> None:
    if strategy is None:
        return
    reset_fn = getattr(strategy, "reset_runtime_state", None)
    if callable(reset_fn):
        reset_fn()


def reset_runtime_state(
    *,
    strategy: object | None = None,
    clear_strategy_cache: bool = True,
) -> tuple[str, ...]:
    """
    Reset run-scoped process globals between tests, batches, or worker runs.

    ``clear_strategy_cache`` only controls the loaded strategy-module cache.
    Timing, board, state-machine, and JIT warning caches are always cleared so
    callers can opt out of strategy re-imports without keeping other run-owned
    globals alive across runs. Process-owned resources such as database pools
    are intentionally excluded and have explicit shutdown hooks.
    """
    executed: list[str] = []
    for name, callback in _runtime_reset_callbacks(
        clear_strategy_cache=clear_strategy_cache,
    ):
        callback()
        executed.append(name)

    _reset_strategy_runtime(strategy)
    if strategy is not None:
        executed.append("strategy.reset_runtime_state")
    return tuple(executed)


__all__ = ["reset_runtime_state"]
