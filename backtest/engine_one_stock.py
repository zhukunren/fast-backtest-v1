"""
Single-stock backtest pipeline orchestration.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional

import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_BUY,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
    SIGNAL_COL_SELL_RAW,
)
from core.exceptions import ConfigurationError
from core.logging import log_info, log_warn
from core.normalizers import is_empty_value
from core.schema import PRICE_NORMALIZED_ATTR
from strategies.runtime_resolver import resolve_single_signal_builder, resolve_strategy

from .metrics.engine_metrics import backtest_results, build_trades_from_signals
from .reporting.single_stock_outputs import write_single_stock_outputs
from .engine_one_stock_data import (
    _check_trade_calendar,
    _collect_warmup_shortfall,
    _is_expected_missing_data_error,
    _read_price_df,
    _resolve_trade_window,
)
from .engine_one_stock_signals import _apply_single_signal_builder, _build_signal_payload


def _skip_payload(
    *,
    return_status: bool,
    reason: str,
    stock_code: str,
    t_read_sec: float,
    t_signal_builder_sec: float,
    t_state_machine_sec: float,
    warmup_shortfall_info: Any,
    error: Exception | None = None,
) -> Optional[Dict[str, Any]]:
    if not return_status:
        return None
    payload: dict[str, Any] = {"_skip_reason": str(reason), "stock_code": stock_code}
    if t_read_sec > 0:
        payload["_t_read_sec"] = float(t_read_sec)
    if t_signal_builder_sec > 0:
        payload["_t_signal_builder_sec"] = float(t_signal_builder_sec)
    if t_state_machine_sec > 0:
        payload["_t_state_machine_sec"] = float(t_state_machine_sec)
    if warmup_shortfall_info is not None:
        payload["_warmup_shortfall"] = warmup_shortfall_info
    if error is not None:
        payload["_skip_error"] = str(error)
    return payload


def _resolve_single_stock_window(
    *,
    cfg: Any,
    stock_code: str,
    start_time: str,
    end_time: str,
    offset_days: int,
    offset_clamp_log: bool,
    resolved_window: tuple[str, str, pd.Timestamp | None, pd.Timestamp | None] | None,
    trade_calendar_cache=None,
) -> tuple[str, str, pd.Timestamp | None, pd.Timestamp | None]:
    if resolved_window is not None:
        return resolved_window
    try:
        return _resolve_trade_window(
            cfg=cfg,
            stock_code=stock_code,
            start_time=start_time,
            end_time=end_time,
            offset_days=offset_days,
            offset_clamp_log=offset_clamp_log,
            calendar_cache=trade_calendar_cache,
        )
    except Exception as exc:
        raise ConfigurationError(f"{stock_code}: 计算预热交易日失败: {exc}") from exc


def _prepare_price_projection(df: pd.DataFrame) -> pd.DataFrame:
    price_cols = [
        col
        for col in (
            "trade_date",
            "stock_code",
            "stock_name",
            "open",
            "close",
            "high",
            "low",
            "direction",
            SIGNAL_COL_BUY,
            SIGNAL_COL_SELL,
            SIGNAL_COL_BUY_RAW,
            SIGNAL_COL_SELL_RAW,
            "volume",
        )
        if col in df.columns
    ]
    df_price = df.loc[:, price_cols].copy()
    if not isinstance(df_price.index, pd.RangeIndex):
        df_price = df_price.reset_index(drop=True)
    if COL_STOCK_CODE in df_price.columns and not isinstance(
        df_price[COL_STOCK_CODE].dtype,
        pd.CategoricalDtype,
    ):
        df_price[COL_STOCK_CODE] = df_price[COL_STOCK_CODE].astype("category")
    if SIGNAL_COL_DIRECTION in df_price.columns and not isinstance(
        df_price[SIGNAL_COL_DIRECTION].dtype,
        pd.CategoricalDtype,
    ):
        df_price[SIGNAL_COL_DIRECTION] = df_price[SIGNAL_COL_DIRECTION].astype("category")
    df_price.attrs[PRICE_NORMALIZED_ATTR] = True
    return df_price


def _attach_run_diagnostics(
    summary: dict[str, Any],
    *,
    t1_stats: Mapping[str, Any],
    t_read_sec: float,
    t_signal_builder_sec: float,
    t_state_machine_sec: float,
    warmup_shortfall_info: Any,
) -> None:
    summary["_t1_shift_cnt"] = int(t1_stats.get("shift", 0))
    summary["_t1_shift_fail_cnt"] = int(t1_stats.get("shift_fail", 0))
    if t_read_sec > 0:
        summary["_t_read_sec"] = float(t_read_sec)
    if t_signal_builder_sec > 0:
        summary["_t_signal_builder_sec"] = float(t_signal_builder_sec)
    if t_state_machine_sec > 0:
        summary["_t_state_machine_sec"] = float(t_state_machine_sec)
    if warmup_shortfall_info is not None:
        summary["_warmup_shortfall"] = warmup_shortfall_info


def _build_metric_result(
    *,
    df_price: pd.DataFrame,
    signal_payload: pd.DataFrame,
    stock_code: str,
    execution_policy: Any,
    cfg: Any,
    return_payload: bool,
    include_detail_outputs: bool,
    t1_stats: Mapping[str, Any],
    t_read_sec: float,
    t_signal_builder_sec: float,
    t_state_machine_sec: float,
    warmup_shortfall_info: Any,
    trade_state_runtime=None,
) -> Dict[str, Any]:
    return_details = bool(return_payload and include_detail_outputs)
    summary, trades_df, equity_curve = backtest_results(
        df_price,
        initial_capital=1,
        stock_code=None,
        execution_policy=execution_policy,
        return_details=return_details,
        cfg=cfg,
        trade_state_runtime=trade_state_runtime,
    )
    summary["stock_code"] = stock_code
    _attach_run_diagnostics(
        summary,
        t1_stats=t1_stats,
        t_read_sec=t_read_sec,
        t_signal_builder_sec=t_signal_builder_sec,
        t_state_machine_sec=t_state_machine_sec,
        warmup_shortfall_info=warmup_shortfall_info,
    )
    if is_empty_value(summary.get("代码")):
        summary["代码"] = stock_code
    if not return_payload:
        return summary
    if SIGNAL_COL_DIRECTION in signal_payload.columns:
        has_buy = bool((signal_payload[SIGNAL_COL_DIRECTION] == DIRECTION_BUY).any())
    else:
        has_buy = bool(not signal_payload.empty)
    payload: dict[str, Any] = {
        "summary": summary,
        "stock_code": stock_code,
        "signal_df": signal_payload,
        "price_df": df_price if has_buy else None,
    }
    if return_details:
        payload["trades_df"] = trades_df
        payload["equity_curve"] = equity_curve
    return payload


def _build_signal_count_result(
    *,
    df: pd.DataFrame,
    df_price: pd.DataFrame,
    signal_df: pd.DataFrame,
    signal_payload: pd.DataFrame,
    stock_code: str,
    return_payload: bool,
    t1_stats: Mapping[str, Any],
    t_read_sec: float,
    t_signal_builder_sec: float,
    t_state_machine_sec: float,
    warmup_shortfall_info: Any,
) -> Dict[str, Any]:
    buy_count = (
        int(df[SIGNAL_COL_BUY].fillna(False).sum())
        if SIGNAL_COL_BUY in df.columns
        else int(len(signal_payload))
    )
    if SIGNAL_COL_SELL in df.columns:
        sell_count = int(df[SIGNAL_COL_SELL].fillna(False).sum())
    elif SIGNAL_COL_DIRECTION in signal_df.columns:
        sell_count = int((signal_df[SIGNAL_COL_DIRECTION] == DIRECTION_SELL).sum())
    else:
        sell_count = 0
    summary: dict[str, Any] = {
        "stock_code": stock_code,
        "代码": stock_code,
        "买入信号数": buy_count,
        "卖出信号数": sell_count,
    }
    _attach_run_diagnostics(
        summary,
        t1_stats=t1_stats,
        t_read_sec=t_read_sec,
        t_signal_builder_sec=t_signal_builder_sec,
        t_state_machine_sec=t_state_machine_sec,
        warmup_shortfall_info=warmup_shortfall_info,
    )
    if return_payload:
        has_buy = bool(buy_count > 0 and not signal_payload.empty)
        return {
            "summary": summary,
            "stock_code": stock_code,
            "signal_df": signal_payload,
            "price_df": df_price if has_buy else None,
        }
    return summary


def backtest_one_stock(
    stock_code: str,
    start_time: str,
    end_time: str,
    strategy=None,
    verbose: bool = False,
    compute_metrics: bool = True,
    return_payload: bool = False,
    return_status: bool = False,
    execution_policy=None,
    resolved_window: tuple[str, str, pd.Timestamp | None, pd.Timestamp | None] | None = None,
    strategy_module_name: str | None = None,
    aggregate_t1_stats: bool = True,
    offset_clamp_log: bool = True,
    include_detail_outputs: bool = True,
    cfg=None,
    spec=None,
    price_cache: object | None = None,
    stock_industry_map: Mapping[str, object] | None = None,
    trade_state_runtime=None,
    trade_calendar_cache=None,
    write_outputs: bool = False,
) -> Optional[Dict[str, Any]]:
    if cfg is None:
        raise ConfigurationError("backtest_one_stock 需要显式传入 cfg。")
    if execution_policy is None:
        raise ConfigurationError("backtest_one_stock 需要显式传入 execution_policy。")
    if spec is None:
        raise ConfigurationError("backtest_one_stock 需要显式传入 spec。")
    strategy = resolve_strategy(
        strategy=strategy,
        strategy_module_name=strategy_module_name,
        cfg=cfg,
    )
    signal_builder = resolve_single_signal_builder(
        strategy=strategy,
        strategy_module_name=strategy_module_name,
        cfg=cfg,
    )

    offset_days = int(getattr(cfg.backtest, "offset_days", 0) or 0)
    collect_warmup_shortfall = bool(offset_days > 0 and not bool(cfg.run.quiet))
    t_read_sec = 0.0
    t_signal_builder_sec = 0.0
    t_state_machine_sec = 0.0
    warmup_shortfall_info = None
    start_time_read, end_time_read, start_eval, end_eval = _resolve_single_stock_window(
        cfg=cfg,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        offset_days=offset_days,
        offset_clamp_log=offset_clamp_log,
        resolved_window=resolved_window,
        trade_calendar_cache=trade_calendar_cache,
    )
    try:
        df, t_read_sec = _read_price_df(
            stock_code=stock_code,
            start_time_read=start_time_read,
            end_time_read=end_time_read,
            cfg=cfg,
            price_cache=price_cache,
        )
    except Exception as exc:
        if not _is_expected_missing_data_error(exc):
            raise
        if verbose:
            log_warn(f"{stock_code}: 读取数据失败（可跳过） - {exc}")
        return _skip_payload(
            return_status=return_status,
            reason="read_fail",
            stock_code=stock_code,
            t_read_sec=t_read_sec,
            t_signal_builder_sec=t_signal_builder_sec,
            t_state_machine_sec=t_state_machine_sec,
            warmup_shortfall_info=warmup_shortfall_info,
            error=exc,
        )
    if df is None or df.empty:
        return _skip_payload(
            return_status=return_status,
            reason="empty_price_df",
            stock_code=stock_code,
            t_read_sec=t_read_sec,
            t_signal_builder_sec=t_signal_builder_sec,
            t_state_machine_sec=t_state_machine_sec,
            warmup_shortfall_info=warmup_shortfall_info,
        )
    if bool(cfg.run.check_trade_calendar) and not bool(cfg.run.quiet):
        _check_trade_calendar(
            df,
            stock_code=stock_code,
            start_eval=start_eval,
            end_eval=end_eval,
            start_ts=pd.to_datetime(start_time_read, format="%Y%m%d", errors="raise").normalize(),
            end_ts=pd.to_datetime(end_time_read, format="%Y%m%d", errors="raise").normalize(),
            cfg=cfg,
            calendar_cache=trade_calendar_cache,
        )
    if collect_warmup_shortfall:
        warmup_shortfall_info = _collect_warmup_shortfall(
            df,
            stock_code=stock_code,
            start_eval=start_eval,
            offset_days=offset_days,
        )
    df, t_signal_builder_sec, t_state_machine_sec, t1_stats, skip_reason = _apply_single_signal_builder(
        df,
        signal_builder=signal_builder,
        execution_policy=execution_policy,
        spec=spec,
        cfg=cfg,
        stock_code=stock_code,
        offset_days=offset_days,
        start_eval=start_eval,
        end_eval=end_eval,
        aggregate_t1_stats=aggregate_t1_stats,
    )
    if skip_reason:
        return _skip_payload(
            return_status=return_status,
            reason=skip_reason,
            stock_code=stock_code,
            t_read_sec=t_read_sec,
            t_signal_builder_sec=t_signal_builder_sec,
            t_state_machine_sec=t_state_machine_sec,
            warmup_shortfall_info=warmup_shortfall_info,
        )
    signal_df, signal_payload = _build_signal_payload(
        df,
        spec,
        stock_industry_map=stock_industry_map,
        include_signal_df=not compute_metrics,
    )
    if signal_df.empty:
        if verbose:
            log_info(f"{stock_code}: 没有交易信号,跳过回测")
        return _skip_payload(
            return_status=return_status,
            reason="no_signal",
            stock_code=stock_code,
            t_read_sec=t_read_sec,
            t_signal_builder_sec=t_signal_builder_sec,
            t_state_machine_sec=t_state_machine_sec,
            warmup_shortfall_info=warmup_shortfall_info,
        )

    df_price = _prepare_price_projection(df)
    if write_outputs:
        trades_df = build_trades_from_signals(
            df_price,
            stock_code=stock_code,
            execution_policy=execution_policy,
            cfg=cfg,
            trade_state_runtime=trade_state_runtime,
        )
        write_single_stock_outputs(
            cfg=cfg,
            stock_code=stock_code,
            signal_payload=signal_payload,
            price_df=df_price,
            trades_df=trades_df,
        )
    diagnostics = {
        "t1_stats": t1_stats,
        "t_read_sec": t_read_sec,
        "t_signal_builder_sec": t_signal_builder_sec,
        "t_state_machine_sec": t_state_machine_sec,
        "warmup_shortfall_info": warmup_shortfall_info,
    }
    if compute_metrics:
        return _build_metric_result(
            df_price=df_price,
            signal_payload=signal_payload,
            stock_code=stock_code,
            execution_policy=execution_policy,
            cfg=cfg,
            return_payload=return_payload,
            include_detail_outputs=include_detail_outputs,
            trade_state_runtime=trade_state_runtime,
            **diagnostics,
        )
    return _build_signal_count_result(
        df=df,
        df_price=df_price,
        signal_df=signal_df,
        signal_payload=signal_payload,
        stock_code=stock_code,
        return_payload=return_payload,
        **diagnostics,
    )


__all__ = ["backtest_one_stock"]
