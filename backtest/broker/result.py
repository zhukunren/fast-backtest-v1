"""
Structured result for broker portfolio execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True, slots=True)
class BrokerPortfolioResult:
    merged_df: pd.DataFrame
    orders_df: pd.DataFrame
    fills_df: pd.DataFrame
    risk_fills_df: pd.DataFrame
    daily_position_snapshots: tuple[dict[str, object], ...]
    strategy_rejections_df: pd.DataFrame = field(default_factory=pd.DataFrame)


__all__ = ["BrokerPortfolioResult"]
