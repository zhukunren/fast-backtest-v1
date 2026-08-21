"""
Summary-only fastpath for backtest metrics.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE, SIGNAL_COL_BUY, SIGNAL_COL_DIRECTION, SIGNAL_COL_SELL
from core.exceptions import DataValidationError
from core.execution_policy import ExecutionPolicy

from ..execution_semantics import resolve_trade_flags
from .engine_metrics_stats import (
    _build_backtest_summary_result,
    _build_benchmark_daily_from_close,
    _summarize_equity_from_exec_flags,
    _summarize_trades_from_exec_flags,
)


def _backtest_results_summary_only_fastpath(
    df: pd.DataFrame,
    *,
    stock_code: str | None,
    execution_policy: ExecutionPolicy,
    initial_capital: float,
    cfg=None,
    trade_state_runtime: object | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame] | None:
    required_cols = {COL_TRADE_DATE, "open", "close"}
    if df is None or df.empty or not required_cols.issubset(df.columns):
        return None

    trade_date = df[COL_TRADE_DATE]
    if not pd.api.types.is_datetime64_any_dtype(trade_date):
        trade_date = pd.to_datetime(trade_date, errors="raise")
    if trade_date.isna().any():
        bad = df.loc[trade_date.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"df.trade_date 存在 NaT: {bad}")

    order = None
    if not trade_date.is_monotonic_increasing:
        order = np.argsort(trade_date.to_numpy(dtype="datetime64[ns]", copy=False), kind="mergesort")

    open_arr = df["open"].to_numpy(dtype="float64", copy=False)
    close_arr = df["close"].to_numpy(dtype="float64", copy=False)
    buy_trade: np.ndarray | None = None
    sell_trade: np.ndarray | None = None
    direction_series: pd.Series | None = None
    if SIGNAL_COL_BUY in df.columns and SIGNAL_COL_SELL in df.columns:
        buy_trade = df[SIGNAL_COL_BUY].fillna(False).to_numpy(dtype=bool, copy=False)
        sell_trade = df[SIGNAL_COL_SELL].fillna(False).to_numpy(dtype=bool, copy=False)
    elif SIGNAL_COL_DIRECTION in df.columns:
        direction_series = df[SIGNAL_COL_DIRECTION]
    else:
        return None

    if order is not None:
        open_arr = open_arr[order]
        close_arr = close_arr[order]
        if buy_trade is not None:
            buy_trade = buy_trade[order]
            sell_trade = sell_trade[order]
        elif direction_series is not None:
            direction_series = direction_series.iloc[order]

    resolved_flags = resolve_trade_flags(
        execution_policy=execution_policy,
        direction_series=direction_series,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        stock_code=stock_code,
        aggregate_t1_stats=True,
        cfg=cfg,
        runtime=trade_state_runtime,
    )

    trade_stats = _summarize_trades_from_exec_flags(
        open_arr=open_arr,
        close_arr=close_arr,
        buy_trade=resolved_flags.buy_trade,
        sell_trade=resolved_flags.sell_trade,
        execution_policy=execution_policy,
    )
    equity_stats = _summarize_equity_from_exec_flags(
        open_arr=open_arr,
        close_arr=close_arr,
        buy_trade=resolved_flags.buy_trade,
        sell_trade=resolved_flags.sell_trade,
        initial_capital=initial_capital,
        execution_policy=execution_policy,
        cfg=cfg,
    )
    benchmark_daily = _build_benchmark_daily_from_close(close_arr)
    result = _build_backtest_summary_result(
        stock_code=stock_code,
        close_arr=close_arr,
        equity_values=equity_stats["equity_curve"],
        positions=equity_stats["positions"],
        daily_returns_arr=equity_stats["daily_returns"],
        max_drawdown=equity_stats["max_drawdown"],
        trade_stats=trade_stats,
        benchmark_daily_arr=benchmark_daily,
    )
    return result, pd.DataFrame(), pd.DataFrame()


__all__ = ["_backtest_results_summary_only_fastpath"]
