"""
Trade state machine and T+1 shift statistics.
"""
from __future__ import annotations

import threading
from typing import Tuple

import numpy as np

from core.exceptions import DataValidationError
from core.logging import log_info


class TradeStateRuntime:
    """Own trade-state-machine counters and acceleration cache for one runtime scope."""

    def __init__(self) -> None:
        self._stats = threading.local()
        self._state_machine_jit_resolved = False
        self._apply_trade_state_machine_jit = None

    def get_stats_obj(self):
        if not hasattr(self._stats, "shift_total"):
            self._stats.shift_total = 0
            self._stats.shift_fail_total = 0
        return self._stats

    def reset_t1_stats(self) -> None:
        stats = self.get_stats_obj()
        stats.shift_total = 0
        stats.shift_fail_total = 0

    def get_t1_stats(self, *, clear: bool = True) -> dict:
        stats = self.get_stats_obj()
        out = {"shift": int(stats.shift_total), "shift_fail": int(stats.shift_fail_total)}
        if clear:
            stats.shift_total = 0
            stats.shift_fail_total = 0
        return out

    def reset_state_machine_cache(self) -> None:
        self._state_machine_jit_resolved = False
        self._apply_trade_state_machine_jit = None

    def should_use_jit(self, *, cfg=None) -> bool:
        return _should_use_state_machine_jit(cfg=cfg)

    def get_state_machine_jit(self):
        if self._state_machine_jit_resolved:
            return self._apply_trade_state_machine_jit
        self._state_machine_jit_resolved = True

        try:
            from numba import njit
        except Exception:  # pragma: no cover
            self._apply_trade_state_machine_jit = None
            return None

        @njit(cache=True)
        def _compiled_trade_state_machine(
            buy_raw: np.ndarray,
            sell_raw: np.ndarray,
            shift_t1_sell: bool,
            allow_terminal_buy: bool,
        ) -> tuple[np.ndarray, np.ndarray, int, int]:
            n = len(buy_raw)
            buy_trade = np.zeros(n, dtype=np.bool_)
            sell_trade = np.zeros(n, dtype=np.bool_)

            in_trade = False
            buy_pos = -1
            blocked_buy_pos = -1
            shift_cnt = 0
            shift_fail = 0
            for i in range(n):
                if i == blocked_buy_pos:
                    blocked_buy_pos = -1
                    if buy_raw[i]:
                        continue

                if not in_trade:
                    if buy_raw[i] and ((i < n - 1) or allow_terminal_buy):
                        buy_trade[i] = True
                        in_trade = True
                        buy_pos = i
                    continue

                if buy_pos < 0:
                    in_trade = False
                    continue

                if sell_raw[i] and (i > buy_pos):
                    if shift_t1_sell and i == buy_pos + 1:
                        shift_pos = i + 1
                        if shift_pos < n:
                            sell_trade[shift_pos] = True
                            blocked_buy_pos = shift_pos
                            shift_cnt += 1
                        else:
                            shift_fail += 1
                    else:
                        sell_trade[i] = True
                    in_trade = False
                    buy_pos = -1
                    continue

            return buy_trade, sell_trade, shift_cnt, shift_fail

        self._apply_trade_state_machine_jit = _compiled_trade_state_machine
        return self._apply_trade_state_machine_jit


_TRADE_STATE_RUNTIME = TradeStateRuntime()


def create_trade_state_runtime() -> TradeStateRuntime:
    """Create an isolated trade-state-machine runtime for tests or embedding."""
    return TradeStateRuntime()


def reset_t1_stats() -> None:
    _TRADE_STATE_RUNTIME.reset_t1_stats()


def get_t1_stats(*, clear: bool = True) -> dict:
    return _TRADE_STATE_RUNTIME.get_t1_stats(clear=clear)


def _apply_trade_state_machine_py(
    buy_raw: np.ndarray,
    sell_raw: np.ndarray,
    trade_dates: np.ndarray | None = None,
    *,
    print_on_shift: bool = True,
    shift_t1_sell: bool = False,
    allow_terminal_buy: bool = False,
    return_stats: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(buy_raw)
    buy_trade = np.zeros(n, dtype=np.bool_)
    sell_trade = np.zeros(n, dtype=np.bool_)

    orphan_sell = 0
    shift_fail = 0
    shift_cnt = 0

    in_trade = False
    buy_pos = -1
    blocked_buy_pos = -1
    for i in range(n):
        if i == blocked_buy_pos:
            blocked_buy_pos = -1
            if buy_raw[i]:
                continue

        if not in_trade:
            if buy_raw[i] and ((i < n - 1) or allow_terminal_buy):
                buy_trade[i] = True
                in_trade = True
                buy_pos = i
            elif sell_raw[i]:
                orphan_sell += 1
            continue

        if buy_pos < 0:
            in_trade = False
            continue

        if sell_raw[i] and (i > buy_pos):
            if shift_t1_sell and i == buy_pos + 1:
                shift_pos = i + 1
                if shift_pos < n:
                    sell_trade[shift_pos] = True
                    blocked_buy_pos = shift_pos
                    shift_cnt += 1
                    if print_on_shift and trade_dates is not None and len(trade_dates) == n:
                        try:
                            log_info(
                                f"[T+1 SHIFT] {trade_dates[i]} buy@{trade_dates[buy_pos]} -> sell shifted to {trade_dates[shift_pos]}"
                            )
                        except Exception:
                            pass
                else:
                    shift_fail += 1
                    if print_on_shift and trade_dates is not None and len(trade_dates) == n:
                        try:
                            log_info(
                                f"[T+1 SHIFT FAIL] {trade_dates[i]} buy@{trade_dates[buy_pos]} cannot shift sell beyond series; holding remains open"
                            )
                        except Exception:
                            pass
            else:
                sell_trade[i] = True
            in_trade = False
            buy_pos = -1
            continue
    if return_stats:
        return buy_trade, sell_trade, shift_cnt, shift_fail
    return buy_trade, sell_trade


def _should_use_state_machine_jit(cfg=None) -> bool:
    try:
        if cfg is None:
            return False
        return bool(getattr(cfg.performance, "state_machine_jit", False))
    except Exception:
        return False


def reset_state_machine_cache() -> None:
    """Reset cached state-machine acceleration flag between runs."""
    _TRADE_STATE_RUNTIME.reset_state_machine_cache()


def apply_trade_state_machine(
    buy_raw: np.ndarray,
    sell_raw: np.ndarray,
    trade_dates: np.ndarray | None = None,
    *,
    print_on_shift: bool = True,
    stock_code: str | None = None,
    shift_t1_sell: bool = False,
    allow_terminal_buy: bool = False,
    aggregate_t1_stats: bool = False,
    cfg=None,
    runtime: TradeStateRuntime | None = None,
) -> Tuple[np.ndarray, np.ndarray]:
    if buy_raw.shape != sell_raw.shape:
        raise DataValidationError(f"buy/sell shape mismatch: {buy_raw.shape} vs {sell_raw.shape}")
    n = len(buy_raw)
    if trade_dates is not None and len(trade_dates) != n:
        raise DataValidationError(f"trade_dates length mismatch: {len(trade_dates)} vs signals {n}")
    state_runtime = _TRADE_STATE_RUNTIME if runtime is None else runtime
    stats = state_runtime.get_stats_obj()
    use_jit = state_runtime.should_use_jit(cfg=cfg)

    collect_stats = bool(shift_t1_sell and aggregate_t1_stats)

    if use_jit:
        apply_trade_state_machine_jit = state_runtime.get_state_machine_jit()
    else:
        apply_trade_state_machine_jit = None

    if apply_trade_state_machine_jit is not None:
        try:
            buy_trade, sell_trade, shift_cnt, shift_fail_cnt = apply_trade_state_machine_jit(
                buy_raw,
                sell_raw,
                bool(shift_t1_sell),
                bool(allow_terminal_buy),
            )
            if collect_stats:
                stats.shift_total += int(shift_cnt)
                stats.shift_fail_total += int(shift_fail_cnt)
            return buy_trade, sell_trade
        except Exception:
            pass

    out = _apply_trade_state_machine_py(
        buy_raw,
        sell_raw,
        trade_dates=trade_dates,
        print_on_shift=print_on_shift,
        shift_t1_sell=bool(shift_t1_sell),
        allow_terminal_buy=bool(allow_terminal_buy),
        return_stats=collect_stats,
    )
    if collect_stats:
        buy_trade, sell_trade, shift_cnt, shift_fail_cnt = out
        stats.shift_total += int(shift_cnt)
        stats.shift_fail_total += int(shift_fail_cnt)
        return buy_trade, sell_trade
    return out


__all__ = [
    "TradeStateRuntime",
    "apply_trade_state_machine",
    "create_trade_state_runtime",
    "get_t1_stats",
    "reset_state_machine_cache",
    "reset_t1_stats",
]
