"""
Structured result for net-value merge outputs.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class PositionAccountingResult:
    frame: pd.DataFrame
    cost_summary: dict[str, object] | None = None
    orders_df: pd.DataFrame | None = None
    fills_df: pd.DataFrame | None = None


@dataclass(frozen=True, slots=True)
class NetValueMergeResult:
    merged_df: pd.DataFrame
    processed_dfs: dict[str, pd.DataFrame]
    trade_segments_df: pd.DataFrame
    trade_segments_summary_df: pd.DataFrame
    risk_control_summary_df: pd.DataFrame | None = None
    risk_control_events_df: pd.DataFrame | None = None
    cost_summary_df: pd.DataFrame | None = None
    portfolio_orders_df: pd.DataFrame | None = None
    portfolio_fills_df: pd.DataFrame | None = None
    risk_orders_df: pd.DataFrame | None = None
    risk_fills_df: pd.DataFrame | None = None
    strategy_rejections_df: pd.DataFrame | None = None


__all__ = ["NetValueMergeResult", "PositionAccountingResult"]
