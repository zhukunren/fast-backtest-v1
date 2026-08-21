"""Internal signal-pipeline result contracts."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.panel_native import ColumnStore, PricePanel, SelectionBatch


@dataclass(frozen=True, slots=True)
class BatchBacktestResult:
    summaries: list[object]
    price_panel: PricePanel
    column_store: ColumnStore
    buy_raw: object
    sell_raw: object
    resolved_window: tuple[object, object, object, object] | None = None
    summary_df: pd.DataFrame | None = None
    skipped: list[dict[str, object]] | None = None
    stage_timings_sec: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class AllocationBuildResult:
    trading_dfs: dict[str, pd.DataFrame]
    diagnostics: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class TradeSegmentsBuildResult:
    all_trade_dates_df: pd.DataFrame
    report_df: pd.DataFrame
    topn_trade_segments_df: pd.DataFrame
    topn_trade_segments_summary_df: pd.DataFrame

    @classmethod
    def empty(cls) -> TradeSegmentsBuildResult:
        return cls(
            all_trade_dates_df=pd.DataFrame(),
            report_df=pd.DataFrame(),
            topn_trade_segments_df=pd.DataFrame(),
            topn_trade_segments_summary_df=pd.DataFrame(),
        )

    @classmethod
    def from_frames(
        cls,
        *,
        all_trade_dates_df: pd.DataFrame | None = None,
        report_df: pd.DataFrame | None = None,
        topn_trade_segments_df: pd.DataFrame | None = None,
        topn_trade_segments_summary_df: pd.DataFrame | None = None,
    ) -> TradeSegmentsBuildResult:
        return cls(
            all_trade_dates_df=all_trade_dates_df if isinstance(all_trade_dates_df, pd.DataFrame) else pd.DataFrame(),
            report_df=report_df if isinstance(report_df, pd.DataFrame) else pd.DataFrame(),
            topn_trade_segments_df=(
                topn_trade_segments_df if isinstance(topn_trade_segments_df, pd.DataFrame) else pd.DataFrame()
            ),
            topn_trade_segments_summary_df=(
                topn_trade_segments_summary_df
                if isinstance(topn_trade_segments_summary_df, pd.DataFrame)
                else pd.DataFrame()
            ),
        )


@dataclass(frozen=True, slots=True)
class SignalPipelineResult:
    """Panel-native decision state before portfolio execution.

    Report DataFrames deliberately do not belong to this contract. They are
    projected from these arrays only at the result/artifact boundary.
    """

    selection: SelectionBatch
    price_panel: PricePanel
    column_store: ColumnStore
    trade_days: pd.DatetimeIndex
    diagnostics: dict[str, object] | None = None


__all__ = [
    "AllocationBuildResult",
    "BatchBacktestResult",
    "SignalPipelineResult",
    "TradeSegmentsBuildResult",
]
