"""
Signal/state-machine helpers for the single-stock backtest pipeline.
"""
from __future__ import annotations

from collections.abc import Mapping
import time

import numpy as np
import pandas as pd

from config import session_config_scope
from core.dataframe_schema import normalize_signal_output
from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_BUY,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
    SIGNAL_COL_SELL_RAW,
)
from core.exceptions import DataValidationError, StrategyError
from core.invariants import (
    assert_price_frame_preserved,
    capture_price_frame_invariants,
    ensure_trade_date_normalized,
)
from core.schema import SIGNAL_NORMALIZED_ATTR, SIGNAL_SORTED_DATE_CODE_ATTR
from strategies.spec import StrategySpec
from backtest.raw_signal_events import filter_raw_signal_events


def _canonicalize_raw_signal_cols(df_in: pd.DataFrame) -> pd.DataFrame:
    signal_cols = {SIGNAL_COL_BUY_RAW, SIGNAL_COL_SELL_RAW}
    out_df = df_in
    for col in list(out_df.columns):
        low = str(col).lower()
        if low in signal_cols and col != low:
            if low in out_df.columns:
                out_df = out_df.drop(columns=[col])
            else:
                out_df = out_df.rename(columns={col: low})
    return out_df


def _validate_trade_frame_contract(df: pd.DataFrame, *, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise DataValidationError(f"{stock_code}: build_single_signals 返回空 DataFrame。")

    required_cols = {COL_TRADE_DATE, COL_STOCK_CODE, "open", "high", "low", "close"}
    missing = required_cols - set(df.columns)
    if missing:
        raise DataValidationError(f"{stock_code}: build_single_signals 输出缺少价格列 {sorted(missing)}。")

    out = df
    td = out[COL_TRADE_DATE]
    if not pd.api.types.is_datetime64_any_dtype(td):
        out = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
        td = out[COL_TRADE_DATE]

    if td.isna().any():
        bad = out.loc[td.isna(), [COL_TRADE_DATE, COL_STOCK_CODE]].head(5).to_dict(orient="records")
        raise DataValidationError(f"{stock_code}: trade_date 存在 NaT（示例：{bad}）。")
    if not td.is_monotonic_increasing:
        raise DataValidationError(f"{stock_code}: build_single_signals 输出必须按 trade_date 升序。")
    if td.duplicated().any():
        dup = out.loc[td.duplicated(), [COL_TRADE_DATE]].head(5).to_dict(orient="records")
        raise DataValidationError(f"{stock_code}: trade_date 存在重复（示例：{dup}）。")
    return out


def _attach_signal_payload_industry(
    signal_payload: pd.DataFrame,
    *,
    stock_industry_map: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    if (
        stock_industry_map is None
        or signal_payload.empty
        or "industry" in signal_payload.columns
        or COL_STOCK_CODE not in signal_payload.columns
    ):
        return signal_payload

    stock_codes = signal_payload[COL_STOCK_CODE].astype(str).str.strip().str.upper()
    out = signal_payload.copy()
    out["industry"] = stock_codes.map(stock_industry_map)
    return out


def _build_signal_payload(
    df: pd.DataFrame,
    spec: StrategySpec,
    *,
    stock_industry_map: Mapping[str, object] | None = None,
    include_signal_df: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    empty_df = df.iloc[0:0]
    if {SIGNAL_COL_BUY, SIGNAL_COL_SELL}.issubset(df.columns):
        buy_mask = df[SIGNAL_COL_BUY].fillna(False).to_numpy(dtype=bool)
        sell_mask = df[SIGNAL_COL_SELL].fillna(False).to_numpy(dtype=bool)
    else:
        dir_series = df[SIGNAL_COL_DIRECTION]
        buy_mask = dir_series.eq(DIRECTION_BUY).to_numpy(dtype=bool, copy=False)
        sell_mask = dir_series.eq(DIRECTION_SELL).to_numpy(dtype=bool, copy=False)

    signal_mask = buy_mask | sell_mask
    signal_pos = np.flatnonzero(signal_mask)
    if signal_pos.size == 0:
        return empty_df, empty_df

    signal_df = df.iloc[signal_pos] if include_signal_df else df.iloc[signal_pos[:1]]

    buy_pos = np.flatnonzero(buy_mask)
    if buy_pos.size == 0:
        return empty_df, empty_df
    buy_payload_df = df.iloc[buy_pos] if buy_pos.size else empty_df

    sig_cols: list[str] = []
    for col in [COL_TRADE_DATE, COL_STOCK_CODE, SIGNAL_COL_DIRECTION]:
        if col in buy_payload_df.columns and col not in sig_cols:
            sig_cols.append(col)
    if "time_ok" in buy_payload_df.columns and "time_ok" not in sig_cols:
        sig_cols.append("time_ok")
    for col, _asc in spec.sort_spec:
        if col in buy_payload_df.columns and col not in sig_cols:
            sig_cols.append(col)
    extra_cols = [col for col in buy_payload_df.columns if col not in sig_cols]
    signal_payload = buy_payload_df.loc[:, sig_cols + extra_cols]
    if SIGNAL_COL_DIRECTION in signal_payload.columns:
        signal_payload = signal_payload.copy()
        signal_payload[SIGNAL_COL_DIRECTION] = DIRECTION_BUY
    signal_payload = _attach_signal_payload_industry(
        signal_payload,
        stock_industry_map=stock_industry_map,
    )
    signal_payload.attrs[SIGNAL_NORMALIZED_ATTR] = True
    signal_payload.attrs[SIGNAL_SORTED_DATE_CODE_ATTR] = True
    return signal_df, signal_payload


def _apply_single_signal_builder(
    df: pd.DataFrame,
    *,
    signal_builder: callable,
    spec,
    execution_policy=None,
    shift_t1_sell: bool | None = None,
    allow_terminal_buy: bool | None = None,
    cfg=None,
    stock_code: str,
    offset_days: int,
    start_eval: pd.Timestamp | None,
    end_eval: pd.Timestamp | None,
    aggregate_t1_stats: bool = True,
) -> tuple[pd.DataFrame | None, float, float, dict, str | None]:
    t1_stats = {"shift": 0, "shift_fail": 0}
    expected_price_frame = capture_price_frame_invariants(df)
    signal_started = time.perf_counter()
    try:
        with session_config_scope(cfg):
            df = signal_builder(df)
    except Exception as exc:
        raise StrategyError(f"{stock_code}: build_single_signals 执行失败: {exc}") from exc
    t_signal_builder_sec = time.perf_counter() - signal_started
    assert_price_frame_preserved(expected_price_frame, df, source=f"{stock_code}: build_single_signals")
    df = _canonicalize_raw_signal_cols(df)
    df = _validate_trade_frame_contract(df, stock_code=stock_code)

    eval_mask = None
    if offset_days > 0 and COL_TRADE_DATE in df.columns and start_eval is not None:
        eval_mask = (df[COL_TRADE_DATE] >= start_eval) & (df[COL_TRADE_DATE] <= end_eval)

    if not {SIGNAL_COL_BUY_RAW, SIGNAL_COL_SELL_RAW}.issubset(df.columns):
        raise DataValidationError(f"{stock_code}: 策略必须输出 {SIGNAL_COL_BUY_RAW}/{SIGNAL_COL_SELL_RAW}（小写）")

    if execution_policy is not None:
        resolved_policy = execution_policy
    else:
        from types import SimpleNamespace

        resolved_policy = SimpleNamespace(
            buy_timing="signal_close" if bool(allow_terminal_buy) else "next_open",
            sell_timing="signal_close",
            shift_t1_sell=bool(shift_t1_sell),
            allow_intraday=False,
        )

    t_state_start = time.perf_counter()
    raw_events = filter_raw_signal_events(
        df,
        spec=spec,
        execution_policy=resolved_policy,
        start_eval=start_eval if eval_mask is not None else None,
        end_eval=end_eval if eval_mask is not None else None,
        error_cls=DataValidationError,
    )
    t_state_machine_sec = time.perf_counter() - t_state_start

    df[SIGNAL_COL_BUY_RAW] = raw_events.buy
    df[SIGNAL_COL_SELL_RAW] = raw_events.sell
    df[SIGNAL_COL_BUY] = raw_events.buy
    df[SIGNAL_COL_SELL] = raw_events.sell
    direction_arr = np.full(len(df), "", dtype=object)
    direction_arr[raw_events.buy] = DIRECTION_BUY
    direction_arr[raw_events.sell] = DIRECTION_SELL
    df[SIGNAL_COL_DIRECTION] = direction_arr

    if eval_mask is not None:
        df = df.loc[eval_mask].copy()
        if df.empty:
            return None, t_signal_builder_sec, t_state_machine_sec, t1_stats, "single_signals_empty"

    df = normalize_signal_output(
        df,
        sort_spec=spec.sort_spec,
        assume_signal_normalized=True,
    )

    sort_cols = [col for col, _asc in spec.sort_spec if col and str(col).lower() not in {COL_TRADE_DATE}]
    missing_cols = [col for col in sort_cols if col not in df.columns]
    if missing_cols:
        raise DataValidationError(f"{stock_code}: 缺少 TopN 排序列 {missing_cols}")
    df.attrs[SIGNAL_NORMALIZED_ATTR] = True
    df.attrs[SIGNAL_SORTED_DATE_CODE_ATTR] = True

    return df, t_signal_builder_sec, t_state_machine_sec, t1_stats, None


__all__ = [
    "_apply_single_signal_builder",
    "_build_signal_payload",
]
