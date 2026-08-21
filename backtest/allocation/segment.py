"""
Single-trade segment extraction.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
)
from core.exceptions import TradeSegmentError
from core.invariants import assert_sorted_unique, ensure_trade_date_normalized
from core.normalizers import normalize_direction_series

from backtest.trade_state_machine import apply_trade_state_machine
from ..execution_semantics import (
    iterate_trade_windows_from_flags,
    resolve_execution_semantics,
)


_WINDOW_COLS = [
    "signal_date",
    COL_STOCK_CODE,
    "signal_pos",
    "entry_idx",
    "exit_idx",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "entry_price_type",
    "exit_price_type",
    "trade_return",
    "segment_rows",
    "exit_is_forced",
]


def _normalize_segment_price_df(
    price_df: pd.DataFrame,
    *,
    stock_code: str,
    preprocessed: bool,
) -> pd.DataFrame:
    if price_df is None or len(price_df) == 0:
        raise TradeSegmentError(f"{stock_code}: price_df 为空，无法提取交易段落。")
    if COL_TRADE_DATE not in price_df.columns:
        raise TradeSegmentError(f"{stock_code}: price_df 缺少 trade_date 列，无法提取交易段落。")

    df = price_df
    if preprocessed:
        if not pd.api.types.is_datetime64_any_dtype(df[COL_TRADE_DATE]):
            raise TradeSegmentError(f"{stock_code}: preprocessed=True 但 price_df['trade_date'] 不是 datetime64 类型。")
        if df[COL_TRADE_DATE].isna().any():
            raise TradeSegmentError(f"{stock_code}: preprocessed=True 但 price_df['trade_date'] 存在 NaT。")
        if not df[COL_TRADE_DATE].is_monotonic_increasing:
            raise TradeSegmentError(f"{stock_code}: preprocessed=True 但 price_df 未按 trade_date 升序排列。")
        if df[COL_TRADE_DATE].duplicated().any():
            raise TradeSegmentError(f"{stock_code}: preprocessed=True 但 price_df trade_date 存在重复值。")
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index(drop=True)
    else:
        df = price_df.copy()
        df = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
        df = df.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
        assert_sorted_unique(df, [COL_TRADE_DATE], name="price_df trade_date")

    required = [COL_TRADE_DATE, COL_STOCK_CODE, "open", "close", "high", "low", SIGNAL_COL_DIRECTION]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        raise TradeSegmentError(f"{stock_code}: price_df 缺少列 {missing_cols}，无法提取交易段落。")
    return df


def _normalize_signal_dates(signal_dates: Sequence[pd.Timestamp] | pd.Series | pd.Index) -> list[pd.Timestamp]:
    if signal_dates is None:
        return []
    dates = pd.to_datetime(list(signal_dates), errors="raise").normalize()
    if len(dates) == 0:
        return []
    return sorted(pd.Index(dates).unique().tolist())


def _calc_trade_return(entry_price: float, exit_price: float) -> float:
    entry = float(entry_price)
    exit_val = float(exit_price)
    if not np.isfinite(entry) or not np.isfinite(exit_val) or abs(entry) <= 1e-12:
        return 0.0
    return float(exit_val / entry - 1.0)


def build_trade_windows_from_signals(
    price_df: pd.DataFrame,
    signal_dates: Sequence[pd.Timestamp] | pd.Series | pd.Index,
    *,
    execution_policy,
    stock_code: str = "",
    preprocessed: bool = False,
    window_end: pd.Timestamp | None = None,
    cfg=None,
) -> tuple[pd.DataFrame, list[pd.Timestamp], list[pd.Timestamp], list[pd.Timestamp]]:
    if execution_policy is None:
        raise TradeSegmentError(f"{stock_code or 'UNKNOWN'}: execution_policy 不能为空。")

    code = str(stock_code or "").strip()
    norm = _normalize_segment_price_df(price_df, stock_code=code or "UNKNOWN", preprocessed=preprocessed)
    if window_end is not None:
        window_end_ts = pd.Timestamp(window_end).normalize()
        date_mask = norm[COL_TRADE_DATE].le(window_end_ts)
        if not bool(date_mask.all()):
            norm = norm.loc[date_mask].reset_index(drop=True)
    dates = norm[COL_TRADE_DATE]
    date_to_idx = {pd.Timestamp(d).normalize(): idx for idx, d in enumerate(dates.tolist())}
    semantics = resolve_execution_semantics(execution_policy)

    signal_dates_norm = _normalize_signal_dates(signal_dates)
    missing_dates: list[pd.Timestamp] = []
    signal_positions: list[int] = []
    buy_raw = np.zeros(len(norm), dtype=bool)
    for signal_date in signal_dates_norm:
        pos = date_to_idx.get(pd.Timestamp(signal_date).normalize())
        if pos is None:
            missing_dates.append(pd.Timestamp(signal_date).normalize())
            continue
        if not buy_raw[pos]:
            buy_raw[pos] = True
            signal_positions.append(pos)

    if not signal_positions:
        return pd.DataFrame(columns=_WINDOW_COLS), missing_dates, [], []

    if SIGNAL_COL_SELL in norm.columns:
        sell_raw = norm[SIGNAL_COL_SELL].fillna(False).to_numpy(dtype=bool, copy=False)
    else:
        directions = normalize_direction_series(norm[SIGNAL_COL_DIRECTION]).to_numpy(dtype=object, copy=False)
        sell_raw = directions == DIRECTION_SELL
    trade_dates = dates.dt.strftime("%Y-%m-%d").to_numpy()
    buy_trade, sell_trade = apply_trade_state_machine(
        buy_raw,
        sell_raw,
        trade_dates=trade_dates,
        print_on_shift=False,
        shift_t1_sell=semantics.shift_t1_sell,
        allow_terminal_buy=semantics.allow_terminal_buy,
        cfg=cfg,
    )

    executed_positions = set(np.flatnonzero(buy_trade).tolist())
    last_idx = len(norm) - 1
    no_next_bar_dates: list[pd.Timestamp] = []
    suppressed_dates: list[pd.Timestamp] = []
    for pos in signal_positions:
        signal_date = pd.Timestamp(dates.iloc[pos]).normalize()
        if pos in executed_positions:
            continue
        if (not semantics.allow_terminal_buy) and pos >= last_idx:
            no_next_bar_dates.append(signal_date)
        else:
            suppressed_dates.append(signal_date)

    opens = pd.to_numeric(norm["open"], errors="coerce").to_numpy(dtype="float64", copy=False)
    closes = pd.to_numeric(norm["close"], errors="coerce").to_numpy(dtype="float64", copy=False)
    windows = iterate_trade_windows_from_flags(
        buy_trade=buy_trade,
        sell_trade=sell_trade,
        dates=dates,
        opens=opens,
        closes=closes,
        execution_policy=execution_policy,
        stock_code=code or "UNKNOWN",
    )
    rows: list[dict[str, object]] = []
    for window in windows:
        rows.append(
            {
                "signal_date": pd.Timestamp(dates.iloc[window.signal_pos]).normalize(),
                COL_STOCK_CODE: str(norm[COL_STOCK_CODE].iloc[window.signal_pos]).strip(),
                "signal_pos": int(window.signal_pos),
                "entry_idx": int(window.entry_idx),
                "exit_idx": int(window.exit_idx),
                "entry_date": window.entry_date,
                "exit_date": window.exit_date,
                "entry_price": float(window.entry_price),
                "exit_price": float(window.exit_price),
                "entry_price_type": window.entry_price_type,
                "exit_price_type": window.exit_price_type,
                "trade_return": _calc_trade_return(window.entry_price, window.exit_price),
                "segment_rows": int(window.exit_idx - window.signal_pos + 1),
                "exit_is_forced": bool(window.exit_is_forced),
            }
        )

    windows_df = pd.DataFrame(rows, columns=_WINDOW_COLS) if rows else pd.DataFrame(columns=_WINDOW_COLS)
    return windows_df, missing_dates, no_next_bar_dates, suppressed_dates


def build_trade_segment_frame(
    price_df: pd.DataFrame,
    trade_window: dict[str, object] | pd.Series,
    *,
    preprocessed: bool = False,
) -> pd.DataFrame:
    stock_code = str(trade_window.get(COL_STOCK_CODE, "")).strip()
    norm = _normalize_segment_price_df(price_df, stock_code=stock_code or "UNKNOWN", preprocessed=preprocessed)
    start_idx = int(trade_window["signal_pos"])
    end_idx = int(trade_window["exit_idx"])
    cols = [COL_TRADE_DATE, COL_STOCK_CODE, "open", "close", "high", "low", SIGNAL_COL_DIRECTION]
    if "volume" in norm.columns:
        cols.append("volume")
    segment = norm.iloc[start_idx : end_idx + 1][cols].copy().reset_index(drop=True)
    if "volume" not in segment.columns:
        segment["volume"] = np.nan
    segment["buy_price"] = np.nan
    segment["sell_price"] = np.nan
    segment["trade_return"] = np.nan
    segment.loc[int(trade_window["entry_idx"]) - start_idx, "buy_price"] = float(trade_window["entry_price"])
    segment.loc[int(trade_window["exit_idx"]) - start_idx, "sell_price"] = float(trade_window["exit_price"])
    segment.loc[int(trade_window["exit_idx"]) - start_idx, "trade_return"] = float(trade_window["trade_return"])
    return segment


def extract_trade_segment(
    price_df: pd.DataFrame,
    start_date: pd.Timestamp,
    *,
    preprocessed: bool = False,
) -> pd.DataFrame:
    """
    从价格序列中提取单笔交易段落。
    """
    base_cols = [COL_TRADE_DATE, COL_STOCK_CODE, "open", "close", "high", "low", SIGNAL_COL_DIRECTION]
    optional_cols = ["volume"]

    if price_df is None or len(price_df) == 0:
        raise TradeSegmentError("price_df 为空，无法提取交易段落。")

    if COL_TRADE_DATE not in price_df.columns:
        raise TradeSegmentError("price_df 缺少 trade_date 列，无法提取交易段落。")

    df = price_df
    if preprocessed:
        if not pd.api.types.is_datetime64_any_dtype(df[COL_TRADE_DATE]):
            raise TradeSegmentError("preprocessed=True 但 price_df['trade_date'] 不是 datetime64 类型。")

    if not preprocessed:
        df = price_df.copy()
        df = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
        df = df.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
        assert_sorted_unique(df, [COL_TRADE_DATE], name="price_df trade_date")
    else:
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index(drop=True)

    missing_cols = [c for c in base_cols if c not in df.columns]
    if missing_cols:
        raise TradeSegmentError(f"price_df 缺少列 {missing_cols}，无法提取交易段落。")
    cols_in = base_cols + [c for c in optional_cols if c in df.columns]

    start_date = pd.Timestamp(start_date).normalize()
    buy_mask = (df[SIGNAL_COL_DIRECTION] == DIRECTION_BUY) & (df[COL_TRADE_DATE] == start_date)
    buy_idxs = df.index[buy_mask]

    if buy_idxs.empty:
        raise TradeSegmentError(f"提取交易段落失败：没有找到买入信号 trade_date={start_date}（price_df 内无对应 buy）。")

    start_pos = int(buy_idxs[0])
    if start_pos >= len(df) - 1:
        raise TradeSegmentError(f"提取交易段落失败：买入信号位于样本最后一天 trade_date={start_date}，无法 next-bar-open 成交。")

    tail = df.iloc[start_pos + 1:]
    sell_idxs = tail.index[tail[SIGNAL_COL_DIRECTION] == DIRECTION_SELL]

    force_sell_at_end = False
    extra_day_included = False

    if sell_idxs.empty:
        end_pos = len(df) - 1
        force_sell_at_end = True
    else:
        sell_pos = int(sell_idxs[0])
        extra_pos = sell_pos + 1
        extra_day_included = extra_pos <= (len(df) - 1)
        end_pos = extra_pos if extra_day_included else sell_pos

    segment = df.iloc[start_pos:end_pos + 1][cols_in].copy()

    for c in base_cols:
        if c not in segment.columns:
            segment[c] = np.nan
    for c in optional_cols:
        if c not in segment.columns:
            segment[c] = np.nan
    segment = segment[base_cols + optional_cols]
    if isinstance(segment[SIGNAL_COL_DIRECTION].dtype, pd.CategoricalDtype):
        segment[SIGNAL_COL_DIRECTION] = segment[SIGNAL_COL_DIRECTION].astype(object)

    if force_sell_at_end and not segment.empty:
        segment.loc[segment.index[-1], SIGNAL_COL_DIRECTION] = DIRECTION_SELL

    if (not force_sell_at_end) and extra_day_included and (not segment.empty):
        segment.loc[segment.index[-1], SIGNAL_COL_DIRECTION] = np.nan

    return segment


__all__ = [
    "build_trade_segment_frame",
    "build_trade_windows_from_signals",
    "extract_trade_segment",
]
