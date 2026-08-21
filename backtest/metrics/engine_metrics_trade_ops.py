"""
Trade/equity helpers for backtest metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from core.constants import (
    COL_TRADE_DATE,
    DIRECTION_SELL,
    SIGNAL_COL_DIRECTION,
    STOP_LOSS_THRESHOLD,
)
from core.exceptions import DataValidationError
from core.execution_policy import ExecutionPolicy
from core.invariants import assert_sorted_unique_index
from core.logging import log_warn
from core.returns_calculator import compute_positions_and_returns
from core.validation import maybe_validate_trade_records

from ..execution_semantics import (
    ResolvedExecutionSemantics,
    ResolvedTradeFlags,
    iterate_trade_windows_from_flags,
    normalize_direction_array,
    resolve_trade_flags_from_frames,
    resolve_execution_semantics,
    resolve_exit_execution,
)
from .engine_metrics_stats import (
    _build_trade_row,
    _prefer_returns_jit,
    _summarize_equity_from_exec_flags,
    _summarize_trades_from_exec_flags,
)


@dataclass(frozen=True, slots=True)
class TradeBuildResult:
    trades_df: pd.DataFrame
    direction_override: pd.Series | None = None


@dataclass(frozen=True, slots=True)
class _PreparedTradeInputs:
    frame: pd.DataFrame
    opens: np.ndarray
    closes: np.ndarray
    resolved_flags: ResolvedTradeFlags
    semantics: ResolvedExecutionSemantics


@dataclass(frozen=True, slots=True)
class _StopLossBuildResult:
    rows: list[dict[str, Any]]
    direction_override: np.ndarray | None
    skipped_last_buy: int
    orphan_sell: int


def _prepare_trade_inputs(
    df: pd.DataFrame,
    *,
    signal_df: pd.DataFrame | None,
    stock_code: str | None,
    use_stop_loss: bool,
    execution_policy: ExecutionPolicy | None,
    buy_trade: np.ndarray | None,
    sell_trade: np.ndarray | None,
    assume_sorted: bool,
    cfg,
    trade_state_runtime: object | None,
) -> _PreparedTradeInputs:
    frame = df.copy()
    frame.columns = frame.columns.astype(str).str.lower()
    if execution_policy is None:
        raise DataValidationError("build_trades_from_signals 需要显式传入 execution_policy。")
    semantics = resolve_execution_semantics(execution_policy)

    if COL_TRADE_DATE in frame.columns:
        frame = frame.set_index(COL_TRADE_DATE)
    frame.index = pd.to_datetime(frame.index)
    if not assume_sorted:
        frame.sort_index(inplace=True)
    assert_sorted_unique_index(frame, name="trade_date")

    signal = None
    if signal_df is not None:
        signal = signal_df.copy()
        signal.columns = signal.columns.astype(str).str.lower()
        if COL_TRADE_DATE in signal.columns:
            signal = signal.set_index(COL_TRADE_DATE)
        signal.index = pd.to_datetime(signal.index)
        signal.sort_index(inplace=True)
        assert_sorted_unique_index(signal, name="trade_date")
        if SIGNAL_COL_DIRECTION not in signal.columns:
            raise DataValidationError("signal_df 必须包含 'direction' 列。")
        frame[SIGNAL_COL_DIRECTION] = signal[SIGNAL_COL_DIRECTION].reindex(frame.index)
    elif SIGNAL_COL_DIRECTION not in frame.columns and (
        use_stop_loss or buy_trade is None or sell_trade is None
    ):
        raise DataValidationError("df 必须包含 'direction' 列，或提供 signal_df（含 direction）。")

    if use_stop_loss:
        frame[SIGNAL_COL_DIRECTION] = normalize_direction_array(frame[SIGNAL_COL_DIRECTION])

    opens = frame["open"].to_numpy(dtype="float64", copy=False)
    closes = frame["close"].to_numpy(dtype="float64", copy=False)
    resolved_flags = resolve_trade_flags_from_frames(
        frame,
        execution_policy=execution_policy,
        signal_df=signal,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        stock_code=stock_code,
        aggregate_t1_stats=False,
        cfg=cfg,
        runtime=trade_state_runtime,
    )
    return _PreparedTradeInputs(
        frame=frame,
        opens=opens,
        closes=closes,
        resolved_flags=resolved_flags,
        semantics=semantics,
    )


def _build_regular_trade_rows(
    prepared: _PreparedTradeInputs,
    *,
    execution_policy: ExecutionPolicy | None,
    stock_code: str | None,
) -> list[dict[str, Any]]:
    windows = iterate_trade_windows_from_flags(
        buy_trade=prepared.resolved_flags.buy_trade,
        sell_trade=prepared.resolved_flags.sell_trade,
        dates=prepared.frame.index,
        opens=prepared.opens,
        closes=prepared.closes,
        execution_policy=execution_policy,
        stock_code=stock_code or "UNKNOWN",
    )
    rows: list[dict[str, Any]] = []
    last_idx = len(prepared.frame) - 1
    for window in windows:
        sell_reason = "signal"
        if window.sell_pos is None:
            sell_reason = "forced_final_exit" if prepared.semantics.sell_timing == "next_open" else "final_exit"
        elif prepared.semantics.sell_timing == "next_open" and window.sell_pos >= last_idx:
            sell_reason = "forced_final_exit"
        rows.append(
            _build_trade_row(
                entry_date=window.entry_date,
                entry_price=window.entry_price,
                exit_date=window.exit_date,
                exit_price=window.exit_price,
                hold_days=window.exit_idx - window.entry_idx,
                sell_reason=sell_reason,
            )
        )
    return rows


def _build_stop_loss_rows(
    prepared: _PreparedTradeInputs,
    *,
    execution_policy: ExecutionPolicy | None,
) -> _StopLossBuildResult:
    frame = prepared.frame
    dates = frame.index
    date_series = pd.Series(dates, copy=False)
    opens = prepared.opens
    closes = prepared.closes
    buy_exec = prepared.resolved_flags.buy_trade
    sell_exec = prepared.resolved_flags.sell_trade
    n = len(frame)

    direction_override = np.asarray(frame[SIGNAL_COL_DIRECTION], dtype=object).copy()
    stop_loss_fired = False
    rows: list[dict[str, Any]] = []
    skipped_last_buy = 0
    orphan_sell = 0
    holding = False
    entry_idx: int | None = None
    entry_price: float | None = None
    for i in range(n):
        if not holding and buy_exec[i]:
            if prepared.semantics.buy_timing == "signal_close":
                entry_idx = i
                entry_price = cast(float, closes[entry_idx])
                holding = True
            elif i < n - 1:
                entry_idx = i + 1
                entry_price = cast(float, opens[entry_idx])
                holding = True
            else:
                skipped_last_buy += 1
            continue

        if not holding and sell_exec[i]:
            orphan_sell += 1
            continue

        if not holding:
            continue

        active_entry_idx = cast(int, entry_idx)
        active_entry_price = cast(float, entry_price)
        current_price = closes[i]
        drawdown = current_price / active_entry_price - 1
        if drawdown <= STOP_LOSS_THRESHOLD:
            exit_idx, exit_date, exit_price, _exit_price_type = resolve_exit_execution(
                sell_pos=i,
                entry_idx=active_entry_idx,
                last_idx=n - 1,
                dates=date_series,
                opens=opens,
                closes=closes,
                execution_policy=execution_policy,
            )
            rows.append(
                _build_trade_row(
                    entry_date=dates[active_entry_idx],
                    entry_price=active_entry_price,
                    exit_date=exit_date,
                    exit_price=exit_price,
                    hold_days=exit_idx - active_entry_idx,
                    sell_reason="stop_loss",
                )
            )
            direction_override[i] = DIRECTION_SELL
            stop_loss_fired = True
            holding = False
            entry_idx = entry_price = None
            continue

        if not sell_exec[i]:
            continue

        sell_reason = "signal"
        exit_idx, exit_date, exit_price, _exit_price_type = resolve_exit_execution(
            sell_pos=i,
            entry_idx=active_entry_idx,
            last_idx=n - 1,
            dates=date_series,
            opens=opens,
            closes=closes,
            execution_policy=execution_policy,
        )
        if prepared.semantics.sell_timing == "next_open" and i >= n - 1:
            sell_reason = "forced_final_exit"
        rows.append(
            _build_trade_row(
                entry_date=dates[active_entry_idx],
                entry_price=active_entry_price,
                exit_date=exit_date,
                exit_price=exit_price,
                hold_days=exit_idx - active_entry_idx,
                sell_reason=sell_reason,
            )
        )
        holding = False
        entry_idx = entry_price = None

    if holding:
        active_entry_idx = cast(int, entry_idx)
        active_entry_price = cast(float, entry_price)
        exit_idx = n - 1
        exit_price = closes[exit_idx]
        sell_reason = "forced_final_exit" if prepared.semantics.sell_timing == "next_open" else "final_exit"
        rows.append(
            _build_trade_row(
                entry_date=dates[active_entry_idx],
                entry_price=active_entry_price,
                exit_date=dates[exit_idx],
                exit_price=exit_price,
                hold_days=exit_idx - active_entry_idx,
                sell_reason=sell_reason,
            )
        )

    return _StopLossBuildResult(
        rows=rows,
        direction_override=direction_override if stop_loss_fired else None,
        skipped_last_buy=skipped_last_buy,
        orphan_sell=orphan_sell,
    )


def _project_trade_result(
    *,
    frame: pd.DataFrame,
    rows: list[dict[str, Any]],
    direction_override: np.ndarray | None,
    skipped_last_buy: int,
    orphan_sell: int,
    stock_code: str | None,
) -> TradeBuildResult:
    trades_df = pd.DataFrame(rows).rename(
        columns={
            "entry_date": "买入日",
            "buy_reason": "买入原因",
            "entry_price": "买入价",
            "exit_date": "卖出日",
            "sell_reason": "卖出原因",
            "exit_price": "卖出价",
            "hold_days": "持仓日",
            "return": "盈亏",
        }
    )

    direction_override_series = None
    if direction_override is not None and len(trades_df) > 0:
        direction_override_series = pd.Series(
            direction_override,
            index=frame.index,
            name=SIGNAL_COL_DIRECTION,
        )

    maybe_validate_trade_records(
        trades_df,
        source=f"engine.build_trades_from_signals:{stock_code or ''}".strip(":"),
    )

    if skipped_last_buy or orphan_sell:
        msg = []
        if skipped_last_buy:
            msg.append(f"末日买信号无法T+1执行 {skipped_last_buy}")
        if orphan_sell:
            msg.append(f"无持仓卖信号 {orphan_sell}")
        log_warn(f"[WARN] build_trades_from_signals: {'; '.join(msg)} stock={stock_code or 'UNKNOWN'}")

    return TradeBuildResult(
        trades_df=trades_df,
        direction_override=direction_override_series,
    )


def build_trades_result_from_signals(
    df: pd.DataFrame,
    signal_df: pd.DataFrame | None = None,
    stock_code: str | None = None,
    use_stop_loss: bool = False,
    execution_policy: ExecutionPolicy | None = None,
    buy_trade: np.ndarray | None = None,
    sell_trade: np.ndarray | None = None,
    assume_sorted: bool = False,
    cfg=None,
    trade_state_runtime: object | None = None,
) -> TradeBuildResult:
    """
    从买卖信号构建交易详细记录。
    """
    prepared = _prepare_trade_inputs(
        df,
        signal_df=signal_df,
        stock_code=stock_code,
        use_stop_loss=use_stop_loss,
        execution_policy=execution_policy,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        assume_sorted=assume_sorted,
        cfg=cfg,
        trade_state_runtime=trade_state_runtime,
    )
    if use_stop_loss:
        stop_loss_result = _build_stop_loss_rows(
            prepared,
            execution_policy=execution_policy,
        )
    else:
        stop_loss_result = _StopLossBuildResult(
            rows=_build_regular_trade_rows(
                prepared,
                execution_policy=execution_policy,
                stock_code=stock_code,
            ),
            direction_override=None,
            skipped_last_buy=0,
            orphan_sell=0,
        )

    return _project_trade_result(
        frame=prepared.frame,
        rows=stop_loss_result.rows,
        direction_override=stop_loss_result.direction_override,
        skipped_last_buy=stop_loss_result.skipped_last_buy,
        orphan_sell=stop_loss_result.orphan_sell,
        stock_code=stock_code,
    )


def build_trades_from_signals(
    df: pd.DataFrame,
    signal_df: pd.DataFrame | None = None,
    stock_code: str | None = None,
    use_stop_loss: bool = False,
    execution_policy: ExecutionPolicy | None = None,
    buy_trade: np.ndarray | None = None,
    sell_trade: np.ndarray | None = None,
    assume_sorted: bool = False,
    cfg=None,
    trade_state_runtime: object | None = None,
) -> pd.DataFrame:
    return build_trades_result_from_signals(
        df,
        signal_df=signal_df,
        stock_code=stock_code,
        use_stop_loss=use_stop_loss,
        execution_policy=execution_policy,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        assume_sorted=assume_sorted,
        cfg=cfg,
        trade_state_runtime=trade_state_runtime,
    ).trades_df


def build_daily_equity_curve(
    df: pd.DataFrame,
    trades_df: pd.DataFrame,
    initial_capital: float = 1,
    execution_policy: ExecutionPolicy | None = None,
    buy_trade: np.ndarray | None = None,
    sell_trade: np.ndarray | None = None,
    assume_sorted: bool = False,
    cfg=None,
    trade_state_runtime: object | None = None,
) -> pd.DataFrame:
    """
    从信号数据构建日度净值曲线。
    """
    df = df.copy()
    df.columns = df.columns.astype(str).str.lower()
    if execution_policy is None:
        raise DataValidationError("build_daily_equity_curve 需要显式传入 execution_policy。")
    policy = execution_policy

    if COL_TRADE_DATE in df.columns:
        df = df.set_index(COL_TRADE_DATE)

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if not assume_sorted:
        df.sort_index(inplace=True)
    assert_sorted_unique_index(df, name="trade_date")

    resolved_flags = resolve_trade_flags_from_frames(
        df,
        execution_policy=policy,
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        stock_code=None,
        aggregate_t1_stats=False,
        cfg=cfg,
        runtime=trade_state_runtime,
    )
    semantics = resolve_execution_semantics(policy)

    open_arr = df["open"].to_numpy(dtype="float64")
    close_arr = df["close"].to_numpy(dtype="float64")
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
    return pd.DataFrame(
        {"equity": equity_curve, "position": positions, "daily_return": daily_returns},
        index=df.index,
    )


__all__ = [
    "_prefer_returns_jit",
    "_summarize_equity_from_exec_flags",
    "_summarize_trades_from_exec_flags",
    "build_daily_equity_curve",
    "build_trades_result_from_signals",
    "build_trades_from_signals",
    "TradeBuildResult",
]
