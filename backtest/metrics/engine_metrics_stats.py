"""
Shared stats helpers for backtest metrics.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.constants import FLOAT_TOLERANCE
from core.execution_policy import ExecutionPolicy
from core.returns_calculator import compute_positions_and_returns

from ..execution_semantics import (
    iterate_trade_windows_from_flags,
    resolve_execution_semantics,
    resolve_trade_flags,
)


def _prefer_returns_jit(cfg=None) -> bool:
    performance = getattr(cfg, "performance", None)
    return bool(getattr(performance, "net_value_jit", False))


def _calc_trade_return(entry_val: float, exit_val: float) -> float:
    entry_f = float(entry_val)
    if abs(entry_f) <= FLOAT_TOLERANCE:
        return 0.0
    return float(float(exit_val) / entry_f - 1.0)


def _build_trade_row(
    *,
    entry_date,
    entry_price,
    exit_date,
    exit_price,
    hold_days,
    sell_reason: str,
) -> dict[str, Any]:
    return {
        "entry_date": entry_date,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "hold_days": hold_days,
        "return": _calc_trade_return(entry_price, exit_price),
        "buy_reason": "signal",
        "sell_reason": sell_reason,
    }


def _empty_trade_stats() -> dict[str, Any]:
    return {
        "max_trade": None,
        "min_trade": None,
        "avg_trade": None,
        "num_trades": 0,
        "pos_trades": 0,
        "neg_trades": 0,
        "zero_trades": 0,
        "win_rate": None,
        "trade_mul": None,
    }


def _resolve_trade_stats(
    *,
    trade_stats: dict[str, Any] | None = None,
    trades_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if trade_stats is not None:
        return trade_stats
    if trades_df is None or trades_df.empty:
        return _empty_trade_stats()

    eps = 1e-8
    max_trade = trades_df["盈亏"].max()
    min_trade = trades_df["盈亏"].min()
    avg_trade = trades_df["盈亏"].mean()
    num_trades = len(trades_df)
    pos_trades = int((trades_df["盈亏"] > 0).sum())
    neg_trades = int((trades_df["盈亏"] < 0).sum())
    zero_trades = int((trades_df["盈亏"].abs() <= eps).sum())
    win_rate = pos_trades / num_trades if num_trades else None
    trade_mul = avg_trade * num_trades if avg_trade is not None else None
    return {
        "max_trade": max_trade,
        "min_trade": min_trade,
        "avg_trade": avg_trade,
        "num_trades": int(num_trades),
        "pos_trades": pos_trades,
        "neg_trades": neg_trades,
        "zero_trades": zero_trades,
        "win_rate": win_rate,
        "trade_mul": trade_mul,
    }


def _summarize_trades_from_exec_flags(
    *,
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    buy_trade: np.ndarray,
    sell_trade: np.ndarray,
    execution_policy: ExecutionPolicy,
) -> dict[str, Any]:
    windows = iterate_trade_windows_from_flags(
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        dates=pd.date_range("2000-01-01", periods=len(open_arr), freq="D"),
        opens=open_arr,
        closes=close_arr,
        execution_policy=execution_policy,
        stock_code="SUMMARY",
    )
    returns: list[float] = []
    for window in windows:
        if abs(window.entry_price) <= FLOAT_TOLERANCE:
            returns.append(0.0)
        else:
            returns.append(window.exit_price / window.entry_price - 1.0)

    if not returns:
        return _empty_trade_stats()

    returns_arr = np.asarray(returns, dtype="float64")
    eps = 1e-8
    num_trades = int(len(returns_arr))
    pos_trades = int((returns_arr > 0).sum())
    neg_trades = int((returns_arr < 0).sum())
    zero_trades = int((np.abs(returns_arr) <= eps).sum())
    avg_trade = float(returns_arr.mean())
    return {
        "max_trade": float(returns_arr.max()),
        "min_trade": float(returns_arr.min()),
        "avg_trade": avg_trade,
        "num_trades": num_trades,
        "pos_trades": pos_trades,
        "neg_trades": neg_trades,
        "zero_trades": zero_trades,
        "win_rate": (pos_trades / num_trades) if num_trades else None,
        "trade_mul": avg_trade * num_trades,
    }


def _summarize_equity_from_exec_flags(
    *,
    open_arr: np.ndarray,
    close_arr: np.ndarray,
    buy_trade: np.ndarray,
    sell_trade: np.ndarray,
    initial_capital: float,
    execution_policy: ExecutionPolicy,
    cfg=None,
) -> dict[str, Any]:
    semantics = resolve_execution_semantics(execution_policy)
    resolved_flags = resolve_trade_flags(
        execution_policy=execution_policy,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        stock_code=None,
        aggregate_t1_stats=False,
    )
    positions, _buy_exec, _sell_exec, daily_returns = compute_positions_and_returns(
        open_arr,
        close_arr,
        resolved_flags.entry_flags,
        resolved_flags.exit_flags,
        allow_intraday=semantics.allow_intraday,
        buy_timing=semantics.buy_timing,
        sell_timing=semantics.sell_timing,
        prefer_jit=_prefer_returns_jit(cfg),
    )
    growth = np.asarray(1.0 + daily_returns, dtype=np.longdouble)
    equity_curve = (
        np.cumprod(growth, dtype=np.longdouble) * np.longdouble(float(initial_capital))
    ).astype("float64")
    rolling_max = np.maximum.accumulate(equity_curve)
    drawdown = equity_curve / rolling_max - 1.0
    return {
        "equity_curve": equity_curve,
        "positions": positions,
        "daily_returns": daily_returns,
        "holding_days": int((positions == 1).sum()),
        "noholding_days": int((positions == 0).sum()),
        "max_drawdown": abs(float(drawdown.min())) if len(drawdown) else 0.0,
    }


def _compute_benchmark_return(close_arr: np.ndarray) -> float:
    if len(close_arr) == 0:
        return np.nan
    start_price = float(close_arr[0])
    end_price = float(close_arr[-1])
    return end_price / start_price - 1 if start_price != 0 else np.nan


def _build_benchmark_daily_from_close(close_arr: np.ndarray) -> np.ndarray:
    benchmark_daily_arr: np.ndarray = np.zeros(len(close_arr), dtype="float64")
    if len(close_arr) > 1:
        prev_close = close_arr[:-1]
        valid = prev_close != 0
        benchmark_daily_tail = benchmark_daily_arr[1:]
        benchmark_daily_tail[valid] = close_arr[1:][valid] / prev_close[valid] - 1.0
    return benchmark_daily_arr


def _sample_mean(values: np.ndarray) -> float:
    if len(values) == 0:
        return np.nan
    return float(values.mean())


def _sample_std(values: np.ndarray) -> float:
    if len(values) <= 1:
        return np.nan
    return float(values.std(ddof=1))


def _sample_var(values: np.ndarray) -> float:
    if len(values) <= 1:
        return np.nan
    return float(values.var(ddof=1))


def _sample_cov(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) <= 1 or len(right) <= 1:
        return np.nan
    left_mean = float(left.mean())
    right_mean = float(right.mean())
    return float(np.dot(left - left_mean, right - right_mean) / (len(left) - 1))


def _build_backtest_summary_result(
    *,
    stock_code: str | None,
    close_arr: np.ndarray,
    equity_values: np.ndarray,
    positions: np.ndarray | None,
    daily_returns_arr: np.ndarray,
    max_drawdown: float,
    trade_stats: dict[str, Any],
    benchmark_daily_arr: np.ndarray,
) -> dict[str, Any]:
    benchmark_return = _compute_benchmark_return(close_arr)
    start_equity = float(equity_values[0])
    end_equity = float(equity_values[-1])
    strategy_return = end_equity / start_equity - 1
    excess_return = strategy_return - benchmark_return if not np.isnan(benchmark_return) else np.nan

    if positions is not None:
        holding_days = int((positions == 1).sum())
        noholding_days = int((positions == 0).sum())
    else:
        holding_days = 0
        noholding_days = 0

    daily_returns = np.asarray(daily_returns_arr, dtype="float64")
    benchmark_daily = np.asarray(benchmark_daily_arr, dtype="float64")
    daily_std = _sample_std(daily_returns)
    daily_mean = _sample_mean(daily_returns)
    sharpe_ratio = (daily_mean / daily_std) * np.sqrt(252) if daily_std != 0 else np.nan

    annual_volatility = daily_std * np.sqrt(252)
    daily_excess = daily_returns - benchmark_daily
    daily_excess_std = _sample_std(daily_excess)
    daily_excess_mean = _sample_mean(daily_excess)
    info_ratio = (daily_excess_mean / daily_excess_std) * np.sqrt(252) if daily_excess_std != 0 else np.nan
    cov_val = _sample_cov(daily_returns, benchmark_daily)
    var_bench = _sample_var(benchmark_daily)
    beta = cov_val / var_bench if var_bench != 0 else np.nan

    return {
        "代码": stock_code,
        "同期标的涨跌幅": benchmark_return,
        "累计收益率": strategy_return,
        "超额收益率": excess_return,
        "单笔交易最大收益": trade_stats["max_trade"],
        "单笔交易最低收益": trade_stats["min_trade"],
        "单笔平均收益率": trade_stats["avg_trade"],
        "收益率为正的交易笔数": trade_stats["pos_trades"],
        "收益率为负的交易笔数": trade_stats["neg_trades"],
        "收益率为0的交易笔数": trade_stats["zero_trades"],
        "持仓天数": holding_days,
        "空仓天数": noholding_days,
        "交易笔数": trade_stats["num_trades"],
        "胜率": trade_stats["win_rate"],
        "最大回撤": max_drawdown,
        "年化夏普比率": sharpe_ratio,
        "年化波动率": annual_volatility,
        "信息比率": info_ratio,
        "贝塔系数": beta,
        "单笔平均收益率*交易笔数": trade_stats["trade_mul"],
    }


__all__ = [
    "_build_backtest_summary_result",
    "_build_benchmark_daily_from_close",
    "_build_trade_row",
    "_calc_trade_return",
    "_empty_trade_stats",
    "_prefer_returns_jit",
    "_resolve_trade_stats",
    "_summarize_equity_from_exec_flags",
    "_summarize_trades_from_exec_flags",
]
