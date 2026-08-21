"""
Trade/equity/metrics orchestration.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE, SIGNAL_COL_DIRECTION
from core.exceptions import DataValidationError
from core.execution_policy import ExecutionPolicy

from ..execution_semantics import build_trade_flags_from_direction, resolve_trade_flags_from_frames
from .engine_metrics_fastpath import _backtest_results_summary_only_fastpath
from .engine_metrics_stats import (
    _build_backtest_summary_result,
    _build_benchmark_daily_from_close,
    _summarize_equity_from_exec_flags,
    _summarize_trades_from_exec_flags,
    _resolve_trade_stats,
)
from .engine_metrics_trade_ops import (
    build_daily_equity_curve,
    build_trades_result_from_signals,
    build_trades_from_signals,
)


def backtest_results(
    df: pd.DataFrame,
    initial_capital: float = 1,
    stock_code: str | None = None,
    signal_df: pd.DataFrame | None = None,
    execution_policy: ExecutionPolicy | None = None,
    use_stop_loss: bool = False,
    return_details: bool = True,
    cfg=None,
    trade_state_runtime: object | None = None,
) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """
    计算回测综合指标。
    """
    if execution_policy is None:
        raise DataValidationError("backtest_results 需要显式传入 execution_policy。")

    if (not return_details) and (not use_stop_loss) and signal_df is None:
        fast_result = _backtest_results_summary_only_fastpath(
            df,
            stock_code=stock_code,
            execution_policy=execution_policy,
            initial_capital=initial_capital,
            cfg=cfg,
            trade_state_runtime=trade_state_runtime,
        )
        if fast_result is not None:
            return fast_result

    df = df.copy()
    df.columns = df.columns.astype(str).str.lower()

    if COL_TRADE_DATE in df.columns:
        df[COL_TRADE_DATE] = pd.to_datetime(df[COL_TRADE_DATE], errors="raise")
        if df[COL_TRADE_DATE].isna().any():
            bad = df.loc[df[COL_TRADE_DATE].isna()].head(5).to_dict(orient="records")
            raise DataValidationError(f"df.trade_date 存在 NaT: {bad}")
        df = df.set_index(COL_TRADE_DATE)

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)

    sig = None
    if signal_df is not None:
        sig = signal_df.copy()
        sig.columns = sig.columns.astype(str).str.lower()
        if COL_TRADE_DATE in sig.columns:
            sig[COL_TRADE_DATE] = pd.to_datetime(sig[COL_TRADE_DATE], errors="raise")
            sig = sig.set_index(COL_TRADE_DATE)
        if not isinstance(sig.index, pd.DatetimeIndex):
            sig.index = pd.to_datetime(sig.index)
        sig.sort_index(inplace=True)
        if SIGNAL_COL_DIRECTION not in sig.columns:
            raise DataValidationError("signal_df 必须包含 'direction' 列。")
        df[SIGNAL_COL_DIRECTION] = sig[SIGNAL_COL_DIRECTION].reindex(df.index)

    resolved_flags = resolve_trade_flags_from_frames(
        df,
        execution_policy=execution_policy,
        signal_df=sig,
        stock_code=stock_code,
        aggregate_t1_stats=True,
        cfg=cfg,
        runtime=trade_state_runtime,
    )
    buy_trade = resolved_flags.buy_trade
    sell_trade = resolved_flags.sell_trade

    trades_df = pd.DataFrame()
    equity_curve = pd.DataFrame()
    if return_details or use_stop_loss:
        trade_result = build_trades_result_from_signals(
            df,
            stock_code=stock_code,
            execution_policy=execution_policy,
            use_stop_loss=bool(use_stop_loss),
            buy_trade=buy_trade,
            sell_trade=sell_trade,
            assume_sorted=True,
            cfg=cfg,
            trade_state_runtime=trade_state_runtime,
        )
        trades_df = trade_result.trades_df
        df_equity = df
        if use_stop_loss:
            override = trade_result.direction_override
            if override is not None and len(override) == len(df):
                df_equity = df.copy()
                df_equity[SIGNAL_COL_DIRECTION] = override
                buy_trade, sell_trade = build_trade_flags_from_direction(
                    df_equity[SIGNAL_COL_DIRECTION],
                    stock_code=stock_code,
                    execution_policy=execution_policy,
                    aggregate_t1_stats=False,
                    cfg=cfg,
                    runtime=trade_state_runtime,
                )
        equity_curve = build_daily_equity_curve(
            df_equity,
            trades_df,
            initial_capital=initial_capital,
            execution_policy=execution_policy,
            buy_trade=buy_trade,
            sell_trade=sell_trade,
            assume_sorted=True,
            cfg=cfg,
            trade_state_runtime=trade_state_runtime,
        )
        trade_stats = None
        equity_stats = None
    else:
        open_arr = df["open"].to_numpy(dtype="float64", copy=False)
        close_arr = df["close"].to_numpy(dtype="float64", copy=False)
        trade_stats = _summarize_trades_from_exec_flags(
            open_arr=open_arr,
            close_arr=close_arr,
            buy_trade=np.asarray(buy_trade, dtype=bool),
            sell_trade=np.asarray(sell_trade, dtype=bool),
            execution_policy=execution_policy,
        )
        equity_stats = _summarize_equity_from_exec_flags(
            open_arr=open_arr,
            close_arr=close_arr,
            buy_trade=np.asarray(buy_trade, dtype=bool),
            sell_trade=np.asarray(sell_trade, dtype=bool),
            initial_capital=initial_capital,
            execution_policy=execution_policy,
            cfg=cfg,
        )

    if equity_stats is None:
        equity_values = equity_curve["equity"].to_numpy(dtype="float64", copy=False)
        positions = equity_curve["position"].to_numpy(dtype=int, copy=False) if "position" in equity_curve.columns else None
        daily_returns_arr = (
            equity_curve["daily_return"].fillna(0.0).to_numpy(dtype="float64", copy=False)
            if "daily_return" in equity_curve.columns
            else None
        )
        max_drawdown = abs(float((equity_values / np.maximum.accumulate(equity_values) - 1.0).min())) if len(equity_values) else 0.0
    else:
        equity_values = equity_stats["equity_curve"]
        positions = equity_stats["positions"]
        daily_returns_arr = equity_stats["daily_returns"]
        max_drawdown = equity_stats["max_drawdown"]

    close_arr = df["close"].to_numpy(dtype="float64", copy=False)
    benchmark_daily = _build_benchmark_daily_from_close(close_arr)
    result = _build_backtest_summary_result(
        stock_code=stock_code,
        close_arr=close_arr,
        equity_values=equity_values,
        positions=positions,
        daily_returns_arr=daily_returns_arr,
        max_drawdown=max_drawdown,
        trade_stats=_resolve_trade_stats(trade_stats=trade_stats, trades_df=trades_df),
        benchmark_daily_arr=benchmark_daily,
    )

    return result, trades_df, equity_curve


__all__ = [
    "_backtest_results_summary_only_fastpath",
    "backtest_results",
    "build_daily_equity_curve",
    "build_trades_from_signals",
]
