from __future__ import annotations

import numpy as np

_NUMBA_JIT_RESOLVED = False
_par_jit = None


def defer_entry_day_signal_close_exits(
    entry: np.ndarray,
    exit_: np.ndarray,
    *,
    allow_intraday: bool,
    shift_t1_sell: bool = False,
) -> np.ndarray:
    """
    Defer signal-close exits only when they collide with an actual fresh entry.

    Slot-level frames may contain repeated buy markers while a position is
    already open. Treating every ``entry & exit`` row as a T+1 conflict can
    move an exit onto the next row, which may be another symbol in the same
    slot. The simulation below mirrors the position state and only shifts when
    the row would really open a new position.
    """
    entry_arr = np.asarray(entry, dtype=bool)
    exit_arr = np.asarray(exit_, dtype=bool).copy()
    if entry_arr.shape != exit_arr.shape:
        raise ValueError(f"entry/exit shape mismatch: {entry_arr.shape} vs {exit_arr.shape}")
    if not (bool(shift_t1_sell) or (not bool(allow_intraday))):
        return exit_arr

    n = len(entry_arr)
    prev_pos = 0
    for i in range(n):
        e = bool(entry_arr[i])
        x = bool(exit_arr[i])
        buy_now = prev_pos == 0 and e
        terminal_flat = i == n - 1 and buy_now and x and (not bool(allow_intraday))

        if buy_now and x and i + 1 < n:
            exit_arr[i] = False
            exit_arr[i + 1] = True
            x = False

        if prev_pos == 0 and e:
            if x and (not bool(allow_intraday)):
                curr_pos = 0 if terminal_flat else 1
            else:
                curr_pos = 0 if x else 1
        elif prev_pos == 1 and x:
            curr_pos = 0
        else:
            curr_pos = prev_pos
        prev_pos = curr_pos

    return exit_arr


def _get_par_jit():
    global _NUMBA_JIT_RESOLVED
    global _par_jit

    if _NUMBA_JIT_RESOLVED:
        return _par_jit
    _NUMBA_JIT_RESOLVED = True

    try:
        from numba import njit as _njit
    except Exception:
        _par_jit = None
        return None

    # buy_timing flag: 0=next_open(default), 1=signal_close
    # sell_timing flag: 0=close(default), 1=next_open
    @_njit(cache=True)
    def _compiled_positions_and_returns(
        open_arr: np.ndarray,
        close_arr: np.ndarray,
        entry: np.ndarray,
        exit_: np.ndarray,
        allow_intraday: bool,
        buy_flag: int,
        sell_flag: int,
    ):
        n = len(open_arr)
        positions = np.zeros(n, dtype=np.int64)
        buy_exec = np.zeros(n, dtype=np.bool_)
        sell_exec = np.zeros(n, dtype=np.bool_)
        daily_returns = np.zeros(n, dtype=np.float64)
        prev_pos = 0
        for i in range(n):
            e = bool(entry[i])
            x = bool(exit_[i])
            terminal_flat = (i == n - 1) and (prev_pos == 0) and e and x and (not allow_intraday)
            buy_now = (prev_pos == 0) and e
            if allow_intraday:
                sell_now = ((prev_pos == 1) and x) or (buy_now and x)
            else:
                sell_now = ((prev_pos == 1) and x) or terminal_flat
            buy_exec[i] = buy_now
            sell_exec[i] = sell_now
            if prev_pos == 0 and e:
                if x and (not allow_intraday):
                    curr_pos = 0 if terminal_flat else 1
                else:
                    curr_pos = 0 if x else 1
            elif prev_pos == 1 and x:
                curr_pos = 0
            else:
                curr_pos = prev_pos
            positions[i] = curr_pos
            if prev_pos == 0:
                if e and buy_flag != 1:
                    open_price = open_arr[i]
                    if open_price != 0.0:
                        daily_returns[i] = close_arr[i] / open_price - 1.0
            else:
                prev_close = close_arr[i - 1] if i > 0 else 0.0
                if prev_close != 0.0:
                    if x and sell_flag == 1:
                        daily_returns[i] = open_arr[i] / prev_close - 1.0
                    else:
                        daily_returns[i] = close_arr[i] / prev_close - 1.0
            prev_pos = curr_pos
        return positions, buy_exec, sell_exec, daily_returns

    _par_jit = _compiled_positions_and_returns
    return _par_jit


def compute_positions(
    entry: np.ndarray,
    exit_: np.ndarray,
    *,
    allow_intraday: bool,
) -> np.ndarray:
    n = len(entry)
    positions = np.zeros(n, dtype=int)
    prev = 0
    for i in range(n):
        e = bool(entry[i])
        x = bool(exit_[i])
        if prev == 0 and e:
            if x and (not allow_intraday):
                curr = 0 if i == n - 1 else 1
            else:
                curr = 0 if x else 1
        elif prev == 1 and x:
            curr = 0
        else:
            curr = prev
        positions[i] = curr
        prev = curr
    return positions


def compute_exec_flags(
    entry: np.ndarray,
    exit_: np.ndarray,
    positions: np.ndarray,
    *,
    allow_intraday: bool,
) -> tuple[np.ndarray, np.ndarray]:
    prev_pos = np.roll(positions, 1)
    if len(prev_pos) > 0:
        prev_pos[0] = 0
    buy_exec = (prev_pos == 0) & entry
    if allow_intraday:
        sell_exec = ((prev_pos == 1) & exit_) | ((prev_pos == 0) & entry & exit_)
    else:
        sell_exec = (prev_pos == 1) & exit_
        if len(sell_exec) > 0:
            sell_exec[-1] = bool(sell_exec[-1] or ((prev_pos[-1] == 0) and entry[-1] and exit_[-1]))
    return buy_exec, sell_exec


def compute_daily_returns(
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    entry: np.ndarray,
    exit_: np.ndarray,
    positions: np.ndarray,
    *,
    allow_intraday: bool,
    buy_timing: str,
    sell_timing: str,
) -> np.ndarray:
    n = len(open_arr)
    prev_close = np.roll(close_arr, 1)
    if n > 0:
        prev_close[0] = 0.0

    oc_return = np.zeros(n, dtype="float64")
    oc_mask = open_arr != 0
    if oc_mask.any():
        oc_return[oc_mask] = close_arr[oc_mask] / open_arr[oc_mask] - 1.0

    cc_return = np.zeros(n, dtype="float64")
    cc_mask = prev_close != 0
    if cc_mask.any():
        cc_return[cc_mask] = close_arr[cc_mask] / prev_close[cc_mask] - 1.0
    if n > 0:
        cc_return[0] = 0.0

    prev_pos = np.roll(positions, 1)
    if n > 0:
        prev_pos[0] = 0

    buy_timing = str(buy_timing).strip().lower() or "next_open"
    if allow_intraday:
        mask_intraday = (prev_pos == 0) & entry & exit_
        mask_open_only = (prev_pos == 0) & entry & (~exit_)
    else:
        mask_intraday = np.zeros(n, dtype=bool)
        if n > 0:
            mask_intraday[-1] = bool((prev_pos[-1] == 0) and entry[-1] and exit_[-1])
        mask_open_only = (prev_pos == 0) & entry
    mask_hold = (prev_pos == 1) & (~exit_)
    mask_close = (prev_pos == 1) & exit_

    daily_returns = np.zeros(n, dtype="float64")
    if buy_timing == "signal_close":
        daily_returns[mask_open_only | mask_intraday] = 0.0
    else:
        daily_returns[mask_open_only | mask_intraday] = oc_return[mask_open_only | mask_intraday]
    if str(sell_timing).lower() == "next_open":
        daily_returns[mask_hold] = cc_return[mask_hold]
        oc_prev = np.zeros(n, dtype="float64")
        if cc_mask.any():
            oc_prev[cc_mask] = open_arr[cc_mask] / prev_close[cc_mask] - 1.0
        daily_returns[mask_close] = oc_prev[mask_close]
    else:
        daily_returns[mask_hold | mask_close] = cc_return[mask_hold | mask_close]

    return daily_returns


def compute_positions_and_returns(
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    entry: np.ndarray,
    exit_: np.ndarray,
    *,
    allow_intraday: bool,
    buy_timing: str,
    sell_timing: str,
    prefer_jit: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    buy_timing = str(buy_timing).strip().lower() or "next_open"
    sell_timing = str(sell_timing).strip().lower()

    par_jit = _get_par_jit() if prefer_jit else None
    if par_jit is not None:
        try:
            buy_flag = 1 if buy_timing == "signal_close" else 0
            sell_flag = 1 if sell_timing == "next_open" else 0
            return par_jit(
                np.asarray(open_arr, dtype=np.float64),
                np.asarray(close_arr, dtype=np.float64),
                np.asarray(entry, dtype=np.bool_),
                np.asarray(exit_, dtype=np.bool_),
                bool(allow_intraday),
                buy_flag,
                sell_flag,
            )
        except Exception:
            pass

    n = len(open_arr)
    positions = np.zeros(n, dtype=int)
    buy_exec = np.zeros(n, dtype=bool)
    sell_exec = np.zeros(n, dtype=bool)
    daily_returns = np.zeros(n, dtype="float64")

    prev_pos = 0
    for i in range(n):
        e = bool(entry[i])
        x = bool(exit_[i])

        terminal_flat = i == n - 1 and prev_pos == 0 and e and x and (not allow_intraday)
        buy_now = prev_pos == 0 and e
        if allow_intraday:
            sell_now = ((prev_pos == 1) and x) or (buy_now and x)
        else:
            sell_now = ((prev_pos == 1) and x) or terminal_flat
        buy_exec[i] = buy_now
        sell_exec[i] = sell_now

        if prev_pos == 0 and e:
            if x and (not allow_intraday):
                curr_pos = 0 if terminal_flat else 1
            else:
                curr_pos = 0 if x else 1
        elif prev_pos == 1 and x:
            curr_pos = 0
        else:
            curr_pos = prev_pos
        positions[i] = curr_pos

        if prev_pos == 0:
            if e and buy_timing != "signal_close":
                open_price = open_arr[i]
                if open_price != 0:
                    daily_returns[i] = close_arr[i] / open_price - 1.0
        else:
            prev_close = close_arr[i - 1] if i > 0 else 0.0
            if prev_close != 0:
                if x and sell_timing == "next_open":
                    daily_returns[i] = open_arr[i] / prev_close - 1.0
                else:
                    daily_returns[i] = close_arr[i] / prev_close - 1.0

        prev_pos = curr_pos

    return positions, buy_exec, sell_exec, daily_returns

