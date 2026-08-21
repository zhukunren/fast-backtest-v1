"""Pure projections used by the direct broker risk overlay."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE


def daily_return_from_net_value(net_value: pd.Series) -> pd.Series:
    nv = pd.to_numeric(net_value, errors="coerce")
    daily_return = nv.pct_change().replace([np.inf, -np.inf], np.nan)
    if len(daily_return) > 0:
        first_value = float(nv.iloc[0]) if pd.notna(nv.iloc[0]) else np.nan
        daily_return.iloc[0] = first_value - 1.0 if np.isfinite(first_value) else 0.0
    return daily_return.fillna(0.0)


def risk_execution_flags(
    df_out: pd.DataFrame,
    fills_df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    position = (
        pd.to_numeric(df_out["position_value"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype="float64", copy=False)
        > 1e-12
    ).astype(int)
    buy_exec: np.ndarray = np.zeros(len(df_out), dtype=bool)
    sell_exec: np.ndarray = np.zeros(len(df_out), dtype=bool)
    if fills_df.empty or not {"trade_date", "side"}.issubset(fills_df.columns):
        return position, buy_exec, sell_exec
    fill_dates = pd.to_datetime(fills_df["trade_date"], errors="coerce").dt.normalize()
    fill_side = fills_df["side"].astype(str).str.strip().str.lower()
    buy_dates = set(fill_dates.loc[fill_side.eq("buy")].dropna().tolist())
    sell_dates = set(fill_dates.loc[fill_side.eq("sell")].dropna().tolist())
    previous_position = np.roll(position, 1)
    if len(previous_position) > 0:
        previous_position[0] = 0
    trade_dates = df_out[COL_TRADE_DATE]
    if buy_dates:
        buy_exec = trade_dates.isin(buy_dates).to_numpy(dtype=bool) & (previous_position == 0)
    if sell_dates:
        sell_exec = trade_dates.isin(sell_dates).to_numpy(dtype=bool) & (position == 0)
    return position, buy_exec, sell_exec


__all__ = ["daily_return_from_net_value", "risk_execution_flags"]
