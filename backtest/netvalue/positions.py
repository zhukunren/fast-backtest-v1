"""
Position/return calculation helpers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.constants import (
    COL_TRADE_DATE,
    DEFAULT_LOT_SIZE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    FLOAT_TOLERANCE,
    SIGNAL_COL_DIRECTION,
)
from core.exceptions import ConfigurationError
from core.logging import log_warn
from core.invariants import ensure_trade_date_normalized
from core.returns_calculator import compute_positions_and_returns, defer_entry_day_signal_close_exits
from core.normalizers import normalize_direction_series

from ..costs import get_tc, is_cost_enabled
from .cost_helpers import (
    _require_portfolio_fraction,
    _resolve_slot_like_single_position_ratio,
    _trade_cost_rates_from_config,
)
from .quote_utils import build_last_valid_mark_prices, is_valid_quote_price
from .result import PositionAccountingResult


class NetValueRuntime:
    """Own net-value JIT warning and compiled-function state for one runtime scope."""

    def __init__(self) -> None:
        self.jit_warned = False
        self.close_jit_resolved = False
        self.calc_position_returns_close_jit = None

    def reset_jit_warning(self) -> None:
        self.jit_warned = False

    def warn_once(self, message: str) -> None:
        if not self.jit_warned:
            log_warn(message)
            self.jit_warned = True

    def get_close_return_jit(self):
        if self.close_jit_resolved:
            return self.calc_position_returns_close_jit
        self.close_jit_resolved = True
        self.calc_position_returns_close_jit = _compile_close_return_jit()
        return self.calc_position_returns_close_jit


_NET_VALUE_RUNTIME = NetValueRuntime()


def create_net_value_runtime() -> NetValueRuntime:
    """Create an isolated net-value runtime for tests or explicit embedding."""
    return NetValueRuntime()


def reset_net_value_jit_warning() -> None:
    """重置 JIT 告警标记（用于多次运行的全局状态隔离）。"""
    _NET_VALUE_RUNTIME.reset_jit_warning()


def _use_net_value_jit(cfg=None) -> bool:
    performance = getattr(cfg, "performance", None)
    return bool(getattr(performance, "net_value_jit", False))


def _compile_close_return_jit():
    try:
        from numba import njit
    except Exception:
        return None

    @njit(cache=True)
    def _compiled_close_returns(open_arr, close_arr, entry_arr, exit_arr, allow_intraday):
        n = len(open_arr)
        positions = np.zeros(n, dtype=np.int8)
        buy_price = np.empty(n, dtype=np.float64)
        sell_price = np.empty(n, dtype=np.float64)
        daily_return = np.zeros(n, dtype=np.float64)
        trade_return = np.empty(n, dtype=np.float64)
        net_value = np.empty(n, dtype=np.float64)

        buy_price.fill(np.nan)
        sell_price.fill(np.nan)
        trade_return.fill(np.nan)

        prev_pos = 0
        last_entry = np.nan
        net = 1.0

        for i in range(n):
            e = entry_arr[i]
            x = exit_arr[i]
            terminal_flat = (i == n - 1) and (prev_pos == 0) and e and x and (not allow_intraday)

            curr = prev_pos
            if prev_pos == 0 and e:
                if x and (not allow_intraday):
                    curr = 0 if terminal_flat else 1
                else:
                    curr = 0 if x else 1
            elif prev_pos == 1 and x:
                curr = 0

            positions[i] = curr

            buy_exec = (prev_pos == 0) and e
            if allow_intraday:
                sell_exec = ((prev_pos == 1) and x) or ((prev_pos == 0) and e and x)
            else:
                sell_exec = ((prev_pos == 1) and x) or terminal_flat

            if buy_exec:
                buy_price[i] = open_arr[i]
                last_entry = open_arr[i]
            if sell_exec:
                sell_price[i] = close_arr[i]

            dr = 0.0
            if buy_exec:
                o = open_arr[i]
                if o != 0:
                    dr = close_arr[i] / o - 1.0
            elif prev_pos == 1:
                if i > 0:
                    c_prev = close_arr[i - 1]
                    if c_prev != 0:
                        dr = close_arr[i] / c_prev - 1.0

            daily_return[i] = dr
            net = net * (1.0 + dr)
            net_value[i] = net

            if sell_exec:
                if (not np.isnan(last_entry)) and last_entry != 0:
                    trade_return[i] = sell_price[i] / last_entry - 1.0
                else:
                    trade_return[i] = 0.0

            prev_pos = curr

        return positions, buy_price, sell_price, daily_return, trade_return, net_value

    return _compiled_close_returns


def _get_close_return_jit(*, runtime: NetValueRuntime | None = None):
    state_runtime = _NET_VALUE_RUNTIME if runtime is None else runtime
    return state_runtime.get_close_return_jit()


def _stable_net_value(daily_returns: pd.Series | np.ndarray) -> np.ndarray:
    growth = np.asarray(1.0 + np.asarray(daily_returns, dtype="float64"), dtype=np.longdouble)
    return np.cumprod(growth, dtype=np.longdouble).astype("float64")


def _numeric_quote_array(df: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype="float64", copy=False)


def _filter_invalid_execution_prices(
    entry: np.ndarray,
    exit_: np.ndarray,
    *,
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    buy_timing: str,
    sell_timing: str,
    allow_intraday: bool,
 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Keep invalid prices out of legacy fills while carrying a pending exit.

    Legacy net-value accounting has no order book.  This small state machine
    gives it the same essential contract as cash accounting: an invalid entry
    is skipped, and an invalid exit leaves the holding open until a later
    valid execution price is available.
    """
    entry_arr = np.asarray(entry, dtype=bool)
    exit_arr = np.asarray(exit_, dtype=bool)
    if entry_arr.shape != exit_arr.shape:
        raise ValueError(f"entry/exit shape mismatch: {entry_arr.shape} vs {exit_arr.shape}")
    if len(entry_arr) != len(open_arr) or len(entry_arr) != len(close_arr):
        raise ValueError("entry/exit and quote arrays must have the same length")

    buy_prices = close_arr if str(buy_timing).strip().lower() == "signal_close" else open_arr
    default_sell_price_type = (
        "open"
        if str(sell_timing).strip().lower() == "next_open"
        else "close"
    )
    buy_valid = np.asarray([is_valid_quote_price(value) for value in buy_prices], dtype=bool)

    effective_entry = np.zeros(len(entry_arr), dtype=bool)
    effective_exit = np.zeros(len(exit_arr), dtype=bool)
    exit_price_type = np.full(len(exit_arr), "", dtype=object)
    held = False
    pending_exit = False
    pending_exit_price_type = default_sell_price_type
    for idx in range(len(entry_arr)):
        was_held = held
        buy_now = (not was_held) and bool(entry_arr[idx]) and bool(buy_valid[idx])
        effective_entry[idx] = buy_now
        explicit_exit_requested = bool(exit_arr[idx])
        exit_requested = explicit_exit_requested or pending_exit
        terminal_fresh_exit = idx == len(entry_arr) - 1 and buy_now and bool(exit_arr[idx]) and not allow_intraday
        can_sell = was_held or (buy_now and allow_intraday) or terminal_fresh_exit
        sell_now = False
        if can_sell and exit_requested:
            price_type = pending_exit_price_type if pending_exit else default_sell_price_type
            sell_price = open_arr[idx] if price_type == "open" else close_arr[idx]
            if is_valid_quote_price(sell_price):
                sell_now = True
                effective_exit[idx] = True
                exit_price_type[idx] = price_type
                pending_exit = False
            else:
                pending_exit = True
                pending_exit_price_type = "open"
        elif buy_now and exit_requested:
            # A non-intraday entry/exit collision must close on a later valid bar.
            pending_exit = True
            pending_exit_price_type = "open"

        if was_held:
            held = not sell_now
        elif buy_now:
            held = not sell_now

    return effective_entry, effective_exit, exit_price_type


def _apply_pending_open_exit_returns(
    daily_return: np.ndarray,
    *,
    sell_exec: np.ndarray,
    exit_price_types: np.ndarray,
    open_arr: np.ndarray,
    mark_close_arr: np.ndarray,
) -> np.ndarray:
    """Replace close returns for retry sells that actually filled at the open."""
    out = np.asarray(daily_return, dtype="float64").copy()
    for idx in np.flatnonzero(
        np.asarray(sell_exec, dtype=bool) & (np.asarray(exit_price_types, dtype=object) == "open")
    ):
        if idx <= 0:
            out[idx] = 0.0
            continue
        previous_mark = mark_close_arr[idx - 1]
        open_price = open_arr[idx]
        if is_valid_quote_price(previous_mark) and is_valid_quote_price(open_price):
            out[idx] = float(open_price) / float(previous_mark) - 1.0
        else:
            out[idx] = 0.0
    return out


def _prepare_trade_frame(df: pd.DataFrame, *, date_col: str = COL_TRADE_DATE) -> pd.DataFrame:
    out = df.copy()
    if date_col in out.columns:
        out = ensure_trade_date_normalized(out, col=date_col)
        date_series = out[date_col]
        if not date_series.is_monotonic_increasing:
            out = out.sort_values(date_col, kind="mergesort")
        if not isinstance(out.index, pd.RangeIndex):
            out = out.reset_index(drop=True)
        return _clip_trailing_price_nan_rows(out)
    if not isinstance(out.index, pd.RangeIndex):
        return _clip_trailing_price_nan_rows(out.reset_index(drop=True))
    return _clip_trailing_price_nan_rows(out)


def _clip_trailing_price_nan_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "open" not in df.columns or "close" not in df.columns:
        return df
    open_valid = pd.to_numeric(df["open"], errors="coerce").notna()
    close_valid = pd.to_numeric(df["close"], errors="coerce").notna()
    price_valid = open_valid | close_valid
    if not bool(price_valid.any()):
        return df
    last_valid_pos = int(np.flatnonzero(price_valid.to_numpy(dtype=bool, copy=False))[-1])
    if last_valid_pos >= len(df) - 1:
        return df
    return df.iloc[: last_valid_pos + 1].reset_index(drop=True)


def _build_entry_exit_from_direction(
    direction: pd.Series,
    *,
    buy_timing: str,
    sell_timing: str,
    allow_intraday: bool,
    shift_t1_sell: bool = False,
) -> tuple[np.ndarray, np.ndarray, bool]:
    buy_timing = str(buy_timing).strip().lower() or "next_open"
    if buy_timing == "signal_close":
        entry = direction.eq(DIRECTION_BUY).fillna(False).to_numpy(dtype=bool, copy=True)
    else:
        entry = direction.shift(1).eq(DIRECTION_BUY).fillna(False).to_numpy(dtype=bool, copy=True)
    if str(sell_timing).lower() == "next_open":
        exit_ = direction.shift(1).eq(DIRECTION_SELL).fillna(False).to_numpy(dtype=bool, copy=True)
        allow_intraday = False
    else:
        exit_ = direction.eq(DIRECTION_SELL).fillna(False).to_numpy(dtype=bool, copy=True)
        exit_ = defer_entry_day_signal_close_exits(
            entry,
            exit_,
            allow_intraday=allow_intraday,
            shift_t1_sell=shift_t1_sell,
        )
    return entry, exit_, allow_intraday


def _normalize_buy_timing(execution_policy) -> str:
    buy_timing = (
        str(getattr(execution_policy, "buy_timing", "")).strip().lower() if execution_policy is not None else ""
    )
    return buy_timing or "next_open"


def _buy_exec_price_series(
    df: pd.DataFrame,
    *,
    buy_exec: pd.Series | np.ndarray,
    buy_timing: str,
) -> pd.Series:
    price_col = "close" if str(buy_timing).strip().lower() == "signal_close" else "open"
    buy_exec_ser = pd.Series(buy_exec, index=df.index, dtype=bool)
    prices = pd.to_numeric(df[price_col], errors="coerce")
    return prices.where(buy_exec_ser, np.nan)


def _apply_trade_return_cost_approx(
    trade_return: pd.Series,
    *,
    buy_exec: pd.Series,
    sell_exec: pd.Series,
    cfg,
) -> pd.Series:
    buy_rate, sell_rate = _trade_cost_rates_from_config(cfg)
    if buy_rate <= FLOAT_TOLERANCE and sell_rate <= FLOAT_TOLERANCE:
        return trade_return

    gross = pd.to_numeric(trade_return, errors="coerce")
    out = pd.Series(np.nan, index=gross.index, dtype="float64")
    entry_factor = pd.Series(np.nan, index=gross.index, dtype="float64")
    entry_factor.loc[buy_exec.astype(bool)] = 1.0 - buy_rate
    entry_factor = entry_factor.ffill().fillna(1.0)

    mask = sell_exec.astype(bool)
    out.loc[mask] = (1.0 + gross.loc[mask].fillna(0.0)) * entry_factor.loc[mask] * (1.0 - sell_rate) - 1.0
    return out


def _apply_position_costs(
    df: pd.DataFrame,
    *,
    buy_exec: pd.Series,
    sell_exec: pd.Series,
    cfg,
) -> pd.DataFrame:
    if not is_cost_enabled(cfg):
        return df
    df = _apply_costs_rate(
        df,
        buy_exec=buy_exec,
        sell_exec=sell_exec,
        cfg=cfg,
    )
    df["trade_return"] = _apply_trade_return_cost_approx(
        df["trade_return"],
        buy_exec=buy_exec,
        sell_exec=sell_exec,
        cfg=cfg,
    )
    return df


def _apply_cash_accounting(
    *,
    shares_arr: np.ndarray,
    cash_arr: np.ndarray,
    close_arr: np.ndarray,
    initial_capital: float,
) -> dict:
    marks = np.asarray(close_arr, dtype="float64")
    position_value_arr = np.zeros(len(shares_arr), dtype="float64")
    held = shares_arr > 0
    if bool(held.any()):
        position_value_arr[held] = shares_arr[held] * marks[held]
    equity_arr = cash_arr + position_value_arr
    try:
        initial_capital_value = float(initial_capital)
    except Exception:
        initial_capital_value = np.nan
    init_cap = initial_capital_value if np.isfinite(initial_capital_value) and initial_capital_value > 0 else np.nan
    daily_return = np.zeros(len(equity_arr), dtype="float64")
    if len(equity_arr) > 0 and np.isfinite(init_cap):
        daily_return[0] = equity_arr[0] / init_cap - 1.0
    if len(equity_arr) > 1:
        prev = equity_arr[:-1]
        curr = equity_arr[1:]
        valid = prev != 0
        daily_return[1:][valid] = curr[valid] / prev[valid] - 1.0
    net_value = equity_arr / init_cap
    utilization_rate = np.zeros(len(equity_arr), dtype="float64")
    np.divide(
        position_value_arr,
        equity_arr,
        out=utilization_rate,
        where=equity_arr != 0,
    )
    position = (shares_arr > 0).astype(int)
    return {
        "position_value_abs": position_value_arr,
        "equity": equity_arr,
        "daily_return": daily_return,
        "net_value": net_value,
        "utilization_rate": utilization_rate,
        "position": position,
    }


def add_position_and_returns(
    df: pd.DataFrame,
    *,
    execution_policy=None,
    apply_costs: bool = True,
    cfg=None,
) -> pd.DataFrame:
    """
    计算日收益率，买入信号第二天开盘买，卖出信号第二天开盘卖。
    """
    df = _prepare_trade_frame(df, date_col=COL_TRADE_DATE)
    # Pre-compute normalized directions as array to avoid per-row normalize_direction()
    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    df[SIGNAL_COL_DIRECTION] = direction
    buy_timing = _normalize_buy_timing(execution_policy)
    entry, exit_, allow_intraday = _build_entry_exit_from_direction(
        direction,
        buy_timing=buy_timing,
        sell_timing="next_open",
        allow_intraday=False,
        shift_t1_sell=False,
    )

    raw_open_arr = _numeric_quote_array(df, "open")
    raw_close_arr = _numeric_quote_array(df, "close")
    entry, exit_, exit_price_types = _filter_invalid_execution_prices(
        entry,
        exit_,
        open_arr=raw_open_arr,
        close_arr=raw_close_arr,
        buy_timing=buy_timing,
        sell_timing="next_open",
        allow_intraday=allow_intraday,
    )
    close_arr = build_last_valid_mark_prices(raw_open_arr, raw_close_arr)
    positions, buy_exec, sell_exec, daily_returns = compute_positions_and_returns(
        raw_open_arr,
        close_arr,
        entry,
        exit_,
        allow_intraday=allow_intraday,
        buy_timing=buy_timing,
        sell_timing="next_open",
        prefer_jit=_use_net_value_jit(cfg),
    )
    daily_returns = _apply_pending_open_exit_returns(
        daily_returns,
        sell_exec=sell_exec,
        exit_price_types=exit_price_types,
        open_arr=raw_open_arr,
        mark_close_arr=close_arr,
    )

    df["position"] = positions
    df["buy_price"] = _buy_exec_price_series(df, buy_exec=buy_exec, buy_timing=buy_timing)
    sell_price_arr = np.full(len(df), np.nan, dtype="float64")
    sell_rows = np.flatnonzero(sell_exec)
    for idx in sell_rows:
        sell_price_arr[idx] = (
            raw_open_arr[idx] if exit_price_types[idx] == "open" else raw_close_arr[idx]
        )
    df["sell_price"] = sell_price_arr
    df["daily_return"] = daily_returns
    df["net_value"] = _stable_net_value(daily_returns)
    final_close = raw_close_arr[-1] if len(raw_close_arr) else None
    df = _force_final_exit_next_open(df, force_sell_price=final_close)
    buy_exec_ser = pd.Series(buy_exec, index=df.index, dtype=bool)
    sell_exec_ser = pd.Series(sell_exec, index=df.index, dtype=bool)
    if len(df) and pd.notna(df["sell_price"].iloc[-1]) and not bool(sell_exec_ser.iloc[-1]):
        sell_exec_ser.iloc[-1] = True
    df["trade_return"] = _build_trade_return(
        df["buy_price"],
        df["sell_price"],
        sell_exec=sell_exec_ser,
    )

    if apply_costs:
        if cfg is None:
            raise ConfigurationError("add_position_and_returns 需要显式传入 cfg（当 apply_costs=True）。")
        df = _apply_position_costs(
            df,
            buy_exec=buy_exec_ser,
            sell_exec=sell_exec_ser,
            cfg=cfg,
        )

    return df


def _apply_costs_rate(
    df: pd.DataFrame,
    *,
    buy_exec: pd.Series,
    sell_exec: pd.Series,
    cfg=None,
) -> pd.DataFrame:
    if cfg is None:
        raise ConfigurationError("_apply_costs_rate 需要显式传入 cfg。")
    tc = get_tc(cfg)
    if not is_cost_enabled(cfg):
        return df

    buy_rate = 0.0
    sell_rate = 0.0
    if bool(getattr(tc, "include_buy_commission", True)):
        buy_rate += float(getattr(tc, "commission_rate", 0.0))
    if bool(getattr(tc, "include_sell_commission", True)):
        sell_rate += float(getattr(tc, "commission_rate", 0.0))
    sell_rate += float(getattr(tc, "stamp_duty_rate", 0.0))
    slippage_rate = float(getattr(tc, "slippage_bps", 0.0)) / 10000.0
    if slippage_rate > 0:
        buy_rate += slippage_rate
        sell_rate += slippage_rate

    df["cost_rate"] = 0.0
    if buy_rate:
        df.loc[buy_exec, "cost_rate"] = df.loc[buy_exec, "cost_rate"] + buy_rate
    if sell_rate:
        df.loc[sell_exec, "cost_rate"] = df.loc[sell_exec, "cost_rate"] + sell_rate
    df["daily_return"] = (1 + df["daily_return"]) * (1 - df["cost_rate"]) - 1
    df["net_value"] = _stable_net_value(df["daily_return"])
    return df


def _safe_last_value(series: pd.Series) -> float:
    if series is None or len(series) == 0:
        return np.nan
    try:
        return float(series.iloc[-1])
    except Exception:
        return np.nan


def _force_final_exit_next_open(
    df: pd.DataFrame,
    *,
    force_sell_price: object | None = None,
) -> pd.DataFrame:
    if df.empty or "position" not in df.columns or "sell_price" not in df.columns:
        return df
    if int(pd.to_numeric(df["position"], errors="coerce").fillna(0).iloc[-1]) != 1:
        return df
    sell_price = pd.to_numeric(df["sell_price"], errors="coerce")
    if pd.notna(sell_price.iloc[-1]):
        return df
    close_price = (
        force_sell_price if force_sell_price is not None else pd.to_numeric(df["close"], errors="coerce").iloc[-1]
    )
    if not is_valid_quote_price(close_price):
        return df
    last_idx = df.index[-1]
    df.loc[last_idx, "position"] = 0
    df.loc[last_idx, "sell_price"] = float(close_price)
    return df


def _build_cost_summary(
    df: pd.DataFrame,
    *,
    buy_exec: pd.Series,
    sell_exec: pd.Series,
    pre_net_value: float,
    post_net_value: float,
) -> dict:
    buy_cnt = int(pd.to_numeric(buy_exec, errors="coerce").fillna(0).astype(int).sum())
    sell_cnt = int(pd.to_numeric(sell_exec, errors="coerce").fillna(0).astype(int).sum())
    cost_drag = np.nan
    if np.isfinite(pre_net_value) and pre_net_value:
        if np.isfinite(post_net_value):
            cost_drag = 1.0 - (post_net_value / pre_net_value)
    if "cost_rate" in df.columns:
        cost_rate_sum = float(pd.to_numeric(df["cost_rate"], errors="coerce").fillna(0.0).sum())
    else:
        cost_rate_sum = 0.0
    return {
        "buy_cnt": buy_cnt,
        "sell_cnt": sell_cnt,
        "net_value_pre_cost": pre_net_value,
        "net_value_post_cost": post_net_value,
        "cost_drag": cost_drag,
        "cost_rate_sum": cost_rate_sum,
    }


def _build_frame_cost_summary(
    df: pd.DataFrame,
    *,
    buy_exec: pd.Series,
    sell_exec: pd.Series,
    pre_net_value: float,
) -> dict[str, object]:
    return _build_cost_summary(
        df,
        buy_exec=buy_exec,
        sell_exec=sell_exec,
        pre_net_value=pre_net_value,
        post_net_value=_safe_last_value(df["net_value"]) if "net_value" in df.columns else np.nan,
    )


def _build_trade_return(
    buy_price: pd.Series,
    sell_price: pd.Series,
    *,
    sell_exec: pd.Series,
) -> pd.Series:
    numeric_buy_price = pd.to_numeric(buy_price, errors="coerce")
    numeric_sell_price = pd.to_numeric(sell_price, errors="coerce")
    entry_price_ffill = numeric_buy_price.ffill()
    trade_return = pd.Series(np.nan, index=numeric_buy_price.index, dtype="float64")
    valid_entry = entry_price_ffill.notna() & (entry_price_ffill.abs() > FLOAT_TOLERANCE)
    mask_sell_valid = sell_exec & valid_entry
    trade_return.loc[mask_sell_valid] = (
        numeric_sell_price.loc[mask_sell_valid] / entry_price_ffill.loc[mask_sell_valid] - 1
    )
    trade_return.loc[sell_exec & (~valid_entry)] = 0.0
    return trade_return


def _infer_exec_flags_from_trade_prices(df: pd.DataFrame) -> tuple[pd.Series, pd.Series] | None:
    if "buy_price" not in df.columns or "sell_price" not in df.columns:
        return None
    buy_exec = pd.to_numeric(df["buy_price"], errors="coerce").notna()
    sell_exec = pd.to_numeric(df["sell_price"], errors="coerce").notna()
    return buy_exec, sell_exec


def _infer_exec_flags_close(
    df: pd.DataFrame,
    *,
    allow_intraday: bool,
) -> tuple[pd.Series, pd.Series]:
    inferred = _infer_exec_flags_from_trade_prices(df)
    if inferred is not None:
        return inferred
    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    entry = direction.shift(1).eq(DIRECTION_BUY)
    exit_ = direction.eq(DIRECTION_SELL)
    prev_pos = df["position"].shift(1).fillna(0).astype(int)
    buy_exec = (prev_pos == 0) & entry
    if allow_intraday:
        sell_exec = ((prev_pos == 1) & exit_) | ((prev_pos == 0) & entry & exit_)
    else:
        sell_exec = (prev_pos == 1) & exit_
    return buy_exec, sell_exec


def _infer_exec_flags_next_open(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    inferred = _infer_exec_flags_from_trade_prices(df)
    if inferred is not None:
        return inferred
    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    prev_pos = df["position"].shift(1).fillna(0).astype(int)
    buy_exec = direction.shift(1).eq(DIRECTION_BUY) & (prev_pos == 0)
    sell_exec = direction.shift(1).eq(DIRECTION_SELL) & (prev_pos == 1)
    return buy_exec, sell_exec


def build_position_and_returns_cash_result(
    df: pd.DataFrame,
    *,
    initial_capital: float,
    portfolio_initial_capital: float | None = None,
    allow_intraday: bool | None = None,
    execution_policy=None,
    cfg=None,
    quote_store=None,
) -> PositionAccountingResult:
    """
    基于现金/持仓的净值计算（含交易成本与股数）。
    """
    from .cash_lifecycle import simulate_cash_execution
    from .cash_quotes import build_cash_quote_resolver

    if cfg is None:
        raise ConfigurationError("add_position_and_returns_cash 需要显式传入 cfg。")
    tc = get_tc(cfg)
    df = _prepare_trade_frame(df, date_col="trade_date")
    policy = execution_policy
    if policy is None:
        raise ConfigurationError("execution_policy 不能为空：请显式传入 execution_policy。")
    if allow_intraday is None:
        allow_intraday = bool(policy.allow_intraday)
    buy_timing = _normalize_buy_timing(policy)
    max_single_position_pct = _require_portfolio_fraction(
        getattr(cfg.portfolio, "max_single_position_pct", 1.0),
        name="portfolio.max_single_position_pct",
    )
    max_single_position_ratio = _resolve_slot_like_single_position_ratio(
        initial_capital=float(initial_capital),
        portfolio_initial_capital=portfolio_initial_capital,
        max_single_position_pct=max_single_position_pct,
    )

    quote_resolver = build_cash_quote_resolver(quote_store)
    copy_quotes = quote_resolver is not None
    open_arr = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype="float64", copy=copy_quotes)
    close_arr = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype="float64", copy=copy_quotes)
    high_arr = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype="float64", copy=copy_quotes)
    low_arr = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype="float64", copy=copy_quotes)
    trade_date_arr = (
        pd.to_datetime(df["trade_date"], errors="raise").dt.normalize().dt.strftime("%Y-%m-%d").to_numpy(copy=False)
    )
    stock_code_arr = (
        df["stock_code"].fillna("").astype(str).str.strip().to_numpy(copy=copy_quotes)
        if "stock_code" in df.columns
        else None
    )
    stock_name_arr = (
        df["stock_name"].fillna("").astype(str).str.strip().to_numpy(copy=copy_quotes)
        if "stock_name" in df.columns
        else None
    )
    vol_arr = None
    if "volume" in df.columns:
        vol_arr = pd.to_numeric(df["volume"], errors="coerce").to_numpy(dtype="float64", copy=copy_quotes)
    pre_close_arr = None
    if "pre_close" in df.columns:
        pre_close_arr = pd.to_numeric(df["pre_close"], errors="coerce").to_numpy(
            dtype="float64",
            copy=copy_quotes,
        )
    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    df[SIGNAL_COL_DIRECTION] = direction
    entry, exit_, allow_intraday = _build_entry_exit_from_direction(
        direction,
        buy_timing=buy_timing,
        sell_timing=str(getattr(policy, "sell_timing", "")),
        allow_intraday=allow_intraday,
        shift_t1_sell=bool(getattr(policy, "shift_t1_sell", False)),
    )

    lot_size = max(int(cfg.portfolio.lot_size or DEFAULT_LOT_SIZE), 1)
    cash_buffer_pct = float(getattr(cfg.portfolio, "cash_buffer_pct", 0.0) or 0.0)
    allow_partial_fill = bool(getattr(cfg.portfolio, "allow_partial_fill", True))
    cons = cfg.trade_constraints

    n = len(df)
    if n == 0:
        # A valuation-only mark.  Consumers such as the slot_broker shadow
        # ledger must not rebuild this from raw execution quotes.
        df["mark_price"] = np.array([], dtype="float64")
        df["position"] = np.zeros(0, dtype=int)
        df["shares"] = np.zeros(0, dtype=int)
        df["buy_price"] = np.array([], dtype="float64")
        df["sell_price"] = np.array([], dtype="float64")
        df["cash"] = np.array([], dtype="float64")
        df["position_value_abs"] = np.array([], dtype="float64")
        df["position_value"] = np.array([], dtype="float64")
        df["equity"] = np.array([], dtype="float64")
        df["daily_return"] = np.array([], dtype="float64")
        df["net_value"] = np.array([], dtype="float64")
        df["utilization_rate"] = np.array([], dtype="float64")
        df["commission"] = np.array([], dtype="float64")
        df["stamp_tax"] = np.array([], dtype="float64")
        df["slippage_cost"] = np.array([], dtype="float64")
        df["total_cost"] = np.array([], dtype="float64")
        return PositionAccountingResult(
            frame=df,
            orders_df=pd.DataFrame(),
            fills_df=pd.DataFrame(),
        )

    cost_enabled = is_cost_enabled(cfg)
    sim = simulate_cash_execution(
        open_arr=open_arr,
        high_arr=high_arr,
        low_arr=low_arr,
        close_arr=close_arr,
        vol_arr=vol_arr,
        trade_date_arr=trade_date_arr,
        stock_code_arr=stock_code_arr,
        stock_name_arr=stock_name_arr,
        entry=entry,
        exit_=exit_,
        initial_capital=initial_capital,
        allow_intraday=allow_intraday,
        buy_timing=buy_timing,
        sell_timing=str(getattr(policy, "sell_timing", "")),
        lot_size=lot_size,
        cash_buffer_pct=cash_buffer_pct,
        allow_partial_fill=allow_partial_fill,
        cons=cons,
        cfg=cfg,
        tc=tc,
        cost_enabled=cost_enabled,
        max_single_position_ratio=max_single_position_ratio,
        quote_resolver=quote_resolver,
        collect_arrays=True,
        pre_close_arr=pre_close_arr,
    )

    if quote_resolver is not None:
        df["open"] = open_arr
        df["high"] = high_arr
        df["low"] = low_arr
        df["close"] = close_arr
        if vol_arr is not None:
            df["volume"] = vol_arr
        effective_pre_close_arr = sim["prev_close_arr"]
        df["pre_close"] = effective_pre_close_arr
        if stock_code_arr is not None:
            df["stock_code"] = stock_code_arr
        if stock_name_arr is not None:
            df["stock_name"] = stock_name_arr
        suppressed = sim.get("suppressed_signal_indices", ())
        if suppressed:
            direction_values = direction.to_numpy(dtype=object, copy=True)
            direction_values[list(suppressed)] = None
            df[SIGNAL_COL_DIRECTION] = direction_values

    mark_close_arr = build_last_valid_mark_prices(open_arr, close_arr)
    # `mark_price` is the cash engine's end-of-row valuation mark.  It may be
    # carried from a prior valid quote and is deliberately not a trade price.
    # Publishing it keeps downstream shadow accounting on the same valuation
    # contract without relaxing their execution-price checks.
    df["mark_price"] = mark_close_arr
    accounting = _apply_cash_accounting(
        shares_arr=sim["shares"],
        cash_arr=sim["cash"],
        close_arr=mark_close_arr,
        initial_capital=initial_capital,
    )

    init_cap = float(initial_capital) if float(initial_capital) > 0 else np.nan
    df["position"] = accounting["position"]
    df["shares"] = sim["shares"]
    df["buy_price"] = sim["buy_price"]
    df["sell_price"] = sim["sell_price"]
    df["cash"] = sim["cash"]
    df["position_value_abs"] = accounting["position_value_abs"]
    df["position_value"] = accounting["position_value_abs"] / init_cap
    df["equity"] = accounting["equity"]
    df["daily_return"] = accounting["daily_return"]
    df["net_value"] = accounting["net_value"]
    df["utilization_rate"] = accounting["utilization_rate"]
    df["commission"] = sim["commission"]
    df["stamp_tax"] = sim["stamp_tax"]
    df["slippage_cost"] = sim["slippage_cost"]
    df["total_cost"] = sim["total_cost"]
    buy_exec = pd.Series(~np.isnan(sim["buy_price"]), index=df.index)
    sell_exec = pd.Series(~np.isnan(sim["sell_price"]), index=df.index)
    df["trade_return"] = sim["trade_return"]
    slot_fills_df = pd.DataFrame(sim.get("fill_events", []))
    if not slot_fills_df.empty:
        slot_fills_df = ensure_trade_date_normalized(slot_fills_df, col="trade_date")
    slot_orders_df = pd.DataFrame(sim.get("order_events", []))
    if not slot_orders_df.empty:
        slot_orders_df = ensure_trade_date_normalized(slot_orders_df, col="trade_date")
    cost_summary = None
    if cost_enabled:
        pre_run = simulate_cash_execution(
            open_arr=open_arr,
            high_arr=high_arr,
            low_arr=low_arr,
            close_arr=close_arr,
            vol_arr=vol_arr,
            trade_date_arr=trade_date_arr,
            stock_code_arr=stock_code_arr,
            stock_name_arr=stock_name_arr,
            entry=entry,
            exit_=exit_,
            initial_capital=initial_capital,
            allow_intraday=allow_intraday,
            buy_timing=buy_timing,
            sell_timing=str(getattr(policy, "sell_timing", "")),
            lot_size=lot_size,
            cash_buffer_pct=cash_buffer_pct,
            allow_partial_fill=allow_partial_fill,
            cons=cons,
            cfg=cfg,
            tc=tc,
            cost_enabled=False,
            max_single_position_ratio=max_single_position_ratio,
            quote_resolver=quote_resolver,
            collect_arrays=False,
            pre_close_arr=sim["prev_close_arr"],
        )
        pre_nv = float(pre_run.get("final_equity", 0.0)) / init_cap if init_cap else np.nan
        cost_summary = _build_frame_cost_summary(
            df,
            buy_exec=buy_exec,
            sell_exec=sell_exec,
            pre_net_value=pre_nv,
        )
    return PositionAccountingResult(
        frame=df,
        cost_summary=cost_summary,
        orders_df=slot_orders_df,
        fills_df=slot_fills_df,
    )


def add_position_and_returns_cash(
    df: pd.DataFrame,
    *,
    initial_capital: float,
    portfolio_initial_capital: float | None = None,
    allow_intraday: bool | None = None,
    execution_policy=None,
    cfg=None,
) -> pd.DataFrame:
    return build_position_and_returns_cash_result(
        df,
        initial_capital=initial_capital,
        portfolio_initial_capital=portfolio_initial_capital,
        allow_intraday=allow_intraday,
        execution_policy=execution_policy,
        cfg=cfg,
    ).frame


def add_position_and_returns_close_jit(
    df: pd.DataFrame,
    *,
    allow_intraday: bool = False,
    shift_t1_sell: bool = False,
    runtime: NetValueRuntime | None = None,
) -> pd.DataFrame:
    """
    add_position_and_returns_close 的 numba 版本（需要 numba）。
    """
    calc_position_returns_close_jit = _get_close_return_jit(runtime=runtime)
    if calc_position_returns_close_jit is None:
        raise ConfigurationError("numba not available")

    df = _prepare_trade_frame(df, date_col=COL_TRADE_DATE)

    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    df[SIGNAL_COL_DIRECTION] = direction
    entry, exit_, allow_intraday = _build_entry_exit_from_direction(
        direction,
        buy_timing="next_open",
        sell_timing="signal_close",
        allow_intraday=allow_intraday,
        shift_t1_sell=shift_t1_sell,
    )
    entry = entry.astype(np.bool_, copy=False)
    exit_ = exit_.astype(np.bool_, copy=False)

    raw_open_arr = _numeric_quote_array(df, "open")
    raw_close_arr = _numeric_quote_array(df, "close")
    entry, exit_, exit_price_types = _filter_invalid_execution_prices(
        entry,
        exit_,
        open_arr=raw_open_arr,
        close_arr=raw_close_arr,
        buy_timing="next_open",
        sell_timing="signal_close",
        allow_intraday=allow_intraday,
    )
    close_arr = build_last_valid_mark_prices(raw_open_arr, raw_close_arr)

    # A failed close exit is retried at the next valid open. The dedicated
    # close-only kernel cannot represent that execution price, so retain the
    # shared calculator for this exceptional state-machine path.
    has_pending_open_exit = bool(np.any(exit_price_types == "open"))
    if has_pending_open_exit:
        positions, buy_exec, sell_exec, daily_return = compute_positions_and_returns(
            raw_open_arr,
            close_arr,
            entry,
            exit_,
            allow_intraday=bool(allow_intraday),
            buy_timing="next_open",
            sell_timing="signal_close",
            prefer_jit=False,
        )
        daily_return = _apply_pending_open_exit_returns(
            daily_return,
            sell_exec=sell_exec,
            exit_price_types=exit_price_types,
            open_arr=raw_open_arr,
            mark_close_arr=close_arr,
        )
        buy_price = np.where(buy_exec, raw_open_arr, np.nan)
        sell_price = np.full(len(df), np.nan, dtype="float64")
        for idx in np.flatnonzero(sell_exec):
            sell_price[idx] = raw_open_arr[idx] if exit_price_types[idx] == "open" else raw_close_arr[idx]
        trade_return = np.full(len(df), np.nan, dtype="float64")
        last_entry = np.nan
        for idx in range(len(df)):
            if buy_exec[idx]:
                last_entry = buy_price[idx]
            if sell_exec[idx] and is_valid_quote_price(last_entry):
                trade_return[idx] = sell_price[idx] / last_entry - 1.0
        net_value = _stable_net_value(daily_return)
    else:
        positions, buy_price, sell_price, daily_return, trade_return, net_value = calc_position_returns_close_jit(
            raw_open_arr,
            close_arr,
            entry,
            exit_,
            bool(allow_intraday),
        )

    df["position"] = positions
    df["buy_price"] = buy_price
    df["sell_price"] = sell_price
    df["daily_return"] = daily_return
    df["net_value"] = net_value
    df["trade_return"] = trade_return
    return df


def build_position_and_returns_close_result(
    df: pd.DataFrame,
    *,
    execution_policy=None,
    cfg=None,
    runtime: NetValueRuntime | None = None,
) -> PositionAccountingResult:
    """
    根据配置选择 numba / 纯 Python 版本。
    """
    state_runtime = _NET_VALUE_RUNTIME if runtime is None else runtime
    if cfg is None:
        raise ConfigurationError("add_position_and_returns_close_auto 需要显式传入 cfg。")
    use_jit = _use_net_value_jit(cfg)
    if execution_policy is None:
        raise ConfigurationError("add_position_and_returns_close_auto 需要显式传入 execution_policy。")
    policy = execution_policy
    buy_timing = _normalize_buy_timing(policy)

    allow_intraday = bool(getattr(policy, "allow_intraday", False))
    if policy.sell_timing == "next_open":
        out = add_position_and_returns(df, execution_policy=policy, apply_costs=False, cfg=cfg)
        buy_exec, sell_exec = _infer_exec_flags_next_open(out)
        pre_nv = _safe_last_value(out["net_value"]) if "net_value" in out.columns else np.nan
        out = _apply_position_costs(
            out,
            buy_exec=buy_exec,
            sell_exec=sell_exec,
            cfg=cfg,
        )
        cost_summary = _build_frame_cost_summary(
            out,
            buy_exec=buy_exec,
            sell_exec=sell_exec,
            pre_net_value=pre_nv,
        )
        return PositionAccountingResult(frame=out, cost_summary=cost_summary)

    out: pd.DataFrame | None = None
    if use_jit and buy_timing == "next_open":
        try:
            out = add_position_and_returns_close_jit(
                df,
                allow_intraday=allow_intraday,
                shift_t1_sell=bool(getattr(policy, "shift_t1_sell", False)),
                runtime=state_runtime,
            )
        except Exception as e:
            state_runtime.warn_once(f"add_position_and_returns_close_jit 失败，已回退到纯 Python：{e}")
    elif use_jit and buy_timing != "next_open":
        state_runtime.warn_once("NET_VALUE_JIT=1 但 buy_timing=signal_close 尚未提供 JIT 版本，已回退到纯 Python。")
    if out is None:
        out = add_position_and_returns_close(
            df,
            allow_intraday=allow_intraday,
            execution_policy=policy,
            apply_costs=False,
            cfg=cfg,
        )

    buy_exec, sell_exec = _infer_exec_flags_close(out, allow_intraday=allow_intraday)
    pre_nv = _safe_last_value(out["net_value"]) if "net_value" in out.columns else np.nan
    out = _apply_position_costs(
        out,
        buy_exec=buy_exec,
        sell_exec=sell_exec,
        cfg=cfg,
    )
    cost_summary = _build_frame_cost_summary(
        out,
        buy_exec=buy_exec,
        sell_exec=sell_exec,
        pre_net_value=pre_nv,
    )

    return PositionAccountingResult(frame=out, cost_summary=cost_summary)


def add_position_and_returns_close_auto(
    df: pd.DataFrame,
    *,
    execution_policy=None,
    cfg=None,
    runtime: NetValueRuntime | None = None,
) -> pd.DataFrame:
    return build_position_and_returns_close_result(
        df,
        execution_policy=execution_policy,
        cfg=cfg,
        runtime=runtime,
    ).frame


def add_position_and_returns_close(
    df: pd.DataFrame,
    *,
    allow_intraday: bool | None = None,
    execution_policy=None,
    apply_costs: bool = True,
    cfg=None,
) -> pd.DataFrame:
    """
    计算日收益率：
    - 买入信号第二天开盘买
    - 卖出信号当日收盘卖
    """
    df = _prepare_trade_frame(df, date_col="trade_date")
    if cfg is None:
        raise ConfigurationError("add_position_and_returns_close 需要显式传入 cfg。")
    if execution_policy is None:
        raise ConfigurationError("add_position_and_returns_close 需要显式传入 execution_policy。")
    if allow_intraday is None:
        allow_intraday = bool(execution_policy.allow_intraday)
    buy_timing = _normalize_buy_timing(execution_policy)

    direction = normalize_direction_series(df[SIGNAL_COL_DIRECTION])
    df[SIGNAL_COL_DIRECTION] = direction
    entry, exit_, allow_intraday = _build_entry_exit_from_direction(
        direction,
        buy_timing=buy_timing,
        sell_timing=str(getattr(execution_policy, "sell_timing", "")),
        allow_intraday=allow_intraday,
        shift_t1_sell=bool(getattr(execution_policy, "shift_t1_sell", False)),
    )

    raw_open_arr = _numeric_quote_array(df, "open")
    raw_close_arr = _numeric_quote_array(df, "close")
    entry, exit_, exit_price_types = _filter_invalid_execution_prices(
        entry,
        exit_,
        open_arr=raw_open_arr,
        close_arr=raw_close_arr,
        buy_timing=buy_timing,
        sell_timing=str(getattr(execution_policy, "sell_timing", "")),
        allow_intraday=bool(allow_intraday),
    )
    close_arr = build_last_valid_mark_prices(raw_open_arr, raw_close_arr)
    positions, buy_exec, sell_exec, daily_return = compute_positions_and_returns(
        raw_open_arr,
        close_arr,
        entry,
        exit_,
        allow_intraday=bool(allow_intraday),
        buy_timing=buy_timing,
        sell_timing=str(getattr(execution_policy, "sell_timing", "")),
        prefer_jit=_use_net_value_jit(cfg),
    )
    daily_return = _apply_pending_open_exit_returns(
        daily_return,
        sell_exec=sell_exec,
        exit_price_types=exit_price_types,
        open_arr=raw_open_arr,
        mark_close_arr=close_arr,
    )

    df["position"] = positions
    sell_exec_ser = pd.Series(sell_exec, index=df.index, dtype=bool)
    buy_exec_ser = pd.Series(buy_exec, index=df.index, dtype=bool)
    df["buy_price"] = _buy_exec_price_series(df, buy_exec=buy_exec, buy_timing=buy_timing)
    sell_price_arr = np.full(len(df), np.nan, dtype="float64")
    for idx in np.flatnonzero(sell_exec):
        sell_price_arr[idx] = (
            raw_open_arr[idx] if exit_price_types[idx] == "open" else raw_close_arr[idx]
        )
    df["sell_price"] = sell_price_arr
    df["daily_return"] = daily_return
    df["net_value"] = _stable_net_value(daily_return)
    df["trade_return"] = _build_trade_return(
        df["buy_price"],
        df["sell_price"],
        sell_exec=sell_exec_ser,
    )

    if apply_costs:
        df = _apply_position_costs(
            df,
            buy_exec=buy_exec_ser,
            sell_exec=sell_exec_ser,
            cfg=cfg,
        )
    return df


__all__ = [
    "NetValueRuntime",
    "add_position_and_returns",
    "add_position_and_returns_cash",
    "add_position_and_returns_close",
    "add_position_and_returns_close_auto",
    "add_position_and_returns_close_jit",
    "build_position_and_returns_cash_result",
    "build_position_and_returns_close_result",
    "create_net_value_runtime",
]
