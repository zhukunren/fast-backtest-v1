"""Stable strategy-facing adapters for index and board timing inputs."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from core.timing_flags import coerce_timing_flag_series
from data.timing_readers import read_board_index_for_timing, read_index_for_timing

from .protocol import TimingSignalFunction


TimingSignalHook = TimingSignalFunction
INDEX_TIMING_COLUMNS = ("trade_date", "close", "volume", "amount", "high", "low", "open")
BOARD_TIMING_COLUMNS = ("trade_date", "close", "volume", "open", "high", "low")


def resolve_backtest_window(symbol_code: str) -> tuple[Any, str, str]:
    from config import get_session_config
    from data import compute_trade_date_offset

    cfg = get_session_config()
    start_date = str(cfg.backtest.start_time)
    end_date = str(cfg.backtest.end_time)
    offset_days = int(getattr(cfg.backtest, "offset_days", 0) or 0)
    if offset_days > 0:
        start_date, end_date = compute_trade_date_offset(
            start_date,
            end_date,
            offset_days,
            exchange=cfg.tushare.exchange,
            cal_start=cfg.tushare.trade_cal_start,
            cal_end=cfg.tushare.trade_cal_end,
            allow_clamp=True,
            stock_code=str(symbol_code or ""),
            cfg=cfg,
        )
    return cfg, start_date, end_date


def read_index_timing_frame(
    index_code: str,
    symbol_code: str,
    *,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    cfg, start_date, end_date = resolve_backtest_window(symbol_code)
    return read_index_for_timing(
        str(index_code),
        pd.DataFrame(),
        start_date=start_date,
        end_date=end_date,
        columns=list(columns) if columns is not None else None,
        cfg=cfg,
    )


def read_board_timing_frame(
    board_code: str,
    *,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    cfg, start_date, end_date = resolve_backtest_window(board_code)
    return read_board_index_for_timing(
        str(board_code),
        pd.DataFrame(),
        start_date=start_date,
        end_date=end_date,
        columns=list(columns) if columns is not None else None,
        cfg=cfg,
    )


def resolve_main_limit_percent_for_strategy() -> float:
    from config import get_session_config
    from trading.market_rules import resolve_main_limit_percent

    return resolve_main_limit_percent(cfg=get_session_config())


def normalize_timing_input(df: pd.DataFrame, *, code: str, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError(f"{source} data empty for timing filter: {code}")
    if "trade_date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"{source} data missing columns: {df.columns.tolist()}")
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.normalize()
    out = out.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    out["close"] = pd.to_numeric(out["close"], errors="raise")
    return out


def coerce_timing_signal(signal: object, *, index: pd.Index, column_name: str) -> pd.Series:
    series = pd.Series(signal, index=index)
    return pd.Series(
        coerce_timing_flag_series(series, column_name=column_name).to_numpy(dtype=bool),
        index=index,
    )


def build_index_time_ok(
    *,
    strategy_meta: dict[str, Any],
    build_index_timing_signal: TimingSignalHook,
    stock_code: str,
) -> pd.DataFrame:
    code = str(stock_code or "").strip().upper() or "UNKNOWN"
    index_code = str(strategy_meta.get("index_code", "399006.SZ") or "399006.SZ").strip()
    columns = list(strategy_meta.get("index_timing_columns") or INDEX_TIMING_COLUMNS)
    frame = read_index_timing_frame(index_code, code, columns=columns)
    out = normalize_timing_input(frame, code=index_code, source="index")
    out["time_ok"] = coerce_timing_signal(
        build_index_timing_signal(out.copy()),
        index=out.index,
        column_name="time_ok",
    )
    return out


def build_board_time_ok(
    *,
    strategy_meta: dict[str, Any],
    build_board_timing_signal: TimingSignalHook,
    board_code: str,
) -> pd.DataFrame:
    code = str(board_code or "").strip()
    if not code:
        raise ValueError("board_code must be configured in STRATEGY_META['board_index_codes']")
    columns = list(strategy_meta.get("board_timing_columns") or BOARD_TIMING_COLUMNS)
    frame = read_board_timing_frame(code, columns=columns)
    out = normalize_timing_input(frame, code=code, source="board index")
    board_signal = coerce_timing_signal(
        build_board_timing_signal(out.copy()),
        index=out.index,
        column_name="board_time_ok",
    )
    out["board_time_ok"] = board_signal
    out["time_ok"] = board_signal
    return out.loc[:, ["trade_date", "time_ok", "board_time_ok"]].copy()


__all__ = [
    "BOARD_TIMING_COLUMNS",
    "INDEX_TIMING_COLUMNS",
    "TimingSignalHook",
    "build_board_time_ok",
    "build_index_time_ok",
    "coerce_timing_signal",
    "normalize_timing_input",
    "read_board_timing_frame",
    "read_index_timing_frame",
    "resolve_backtest_window",
    "resolve_main_limit_percent_for_strategy",
]
