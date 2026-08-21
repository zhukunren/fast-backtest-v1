from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.panel_native import (
    ColumnStore,
    PanelView,
    PricePanel,
    QuoteStore,
    SelectionBatch,
    apply_cross_section_transforms,
    apply_market_features,
)
from core.constants import (
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL_RAW,
)


@dataclass(frozen=True, slots=True)
class StrategyPanelRun:
    panel: PricePanel
    store: ColumnStore
    buy_raw: np.ndarray
    sell_raw: np.ndarray
    feature_names: tuple[str, ...]
    pre_signal_names: tuple[str, ...]

    def feature_frame(self) -> pd.DataFrame:
        names = [*self.panel.columns, *self.feature_names]
        return self.panel.quote_store(self.store.columns).to_frame(columns=names)

    def signal_frame(self) -> pd.DataFrame:
        frame = self.panel.quote_store(self.store.columns).to_frame()
        frame[SIGNAL_COL_BUY_RAW] = self.buy_raw
        frame[SIGNAL_COL_SELL_RAW] = self.sell_raw
        direction = np.full(len(frame), "", dtype=object)
        direction[self.buy_raw] = DIRECTION_BUY
        direction[self.sell_raw] = DIRECTION_SELL
        frame[SIGNAL_COL_DIRECTION] = direction
        return frame


def run_panel_strategy(
    strategy: object,
    frame: pd.DataFrame,
    *,
    spec: object | None = None,
    cross_section_specs: object = (),
) -> StrategyPanelRun:
    normalized = frame.copy(deep=True)
    normalized["stock_code"] = normalized["stock_code"].astype(str).str.strip().str.upper()
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"], errors="raise"
    ).dt.normalize()
    normalized.sort_values(
        ["stock_code", "trade_date"],
        kind="mergesort",
        inplace=True,
    )
    normalized.reset_index(drop=True, inplace=True)
    panel = PricePanel.from_frame(normalized)
    store = ColumnStore(panel)
    feature_hook = getattr(strategy, "compute_features")
    signal_hook = getattr(strategy, "compute_signals")
    store.register_features(feature_hook(PanelView(panel, store)))
    feature_names = store.names
    if spec is not None and bool(getattr(spec, "use_market_features", False)):
        apply_market_features(panel, store, spec=spec)
    specs = tuple(cross_section_specs or ())
    if specs:
        apply_cross_section_transforms(panel, store, specs)
    pre_signal_names = store.names
    buy_raw, sell_raw = store.register_signals(signal_hook(PanelView(panel, store)))
    return StrategyPanelRun(
        panel=panel,
        store=store,
        buy_raw=buy_raw,
        sell_raw=sell_raw,
        feature_names=feature_names,
        pre_signal_names=pre_signal_names,
    )


def make_quote_store(price_frames: Mapping[str, pd.DataFrame]) -> QuoteStore:
    frames: list[pd.DataFrame] = []
    for code in sorted(price_frames, key=str.casefold):
        frame = price_frames[code].copy()
        frame["stock_code"] = str(code).strip().upper()
        frame["trade_date"] = pd.to_datetime(
            frame["trade_date"], errors="raise"
        ).dt.normalize()
        frames.append(frame.sort_values("trade_date", kind="mergesort"))
    if not frames:
        raise ValueError("price_frames must be non-empty")
    return PricePanel.from_frame(
        pd.concat(frames, ignore_index=True, sort=False)
    ).quote_store()


def make_panel_selection(
    price_frames: Mapping[str, pd.DataFrame],
    signal_df: pd.DataFrame,
    *,
    candidate_df: pd.DataFrame | None = None,
) -> tuple[PricePanel, SelectionBatch]:
    frames: list[pd.DataFrame] = []
    for code in sorted(price_frames, key=str.casefold):
        frame = price_frames[code].copy()
        frame["stock_code"] = str(code).strip().upper()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
        frames.append(frame.sort_values("trade_date", kind="mergesort"))
    panel_frame = pd.concat(frames, ignore_index=True, sort=False)

    sources = [signal_df]
    if candidate_df is not None:
        sources.append(candidate_df)
    value_columns = sorted(
        {
            str(column)
            for source in sources
            for column in source.columns
            if column not in {"trade_date", "stock_code", SIGNAL_COL_DIRECTION}
        },
        key=str.casefold,
    )
    key_to_values: dict[tuple[pd.Timestamp, str], dict[str, object]] = {}
    for source in reversed(sources):
        normalized = source.copy()
        normalized["trade_date"] = pd.to_datetime(
            normalized["trade_date"], errors="raise"
        ).dt.normalize()
        normalized["stock_code"] = normalized["stock_code"].astype(str).str.strip().str.upper()
        for row in normalized.itertuples(index=False):
            key = (
                pd.Timestamp(getattr(row, "trade_date")).normalize(),
                str(getattr(row, "stock_code")).strip().upper(),
            )
            key_to_values[key] = {
                column: getattr(row, column)
                for column in value_columns
                if hasattr(row, column)
            }
    panel_keys = list(
        zip(
            pd.to_datetime(panel_frame["trade_date"]).dt.normalize(),
            panel_frame["stock_code"].astype(str).str.strip().str.upper(),
        )
    )
    for column in value_columns:
        panel_frame[column] = [
            key_to_values.get(key, {}).get(column, np.nan)
            for key in panel_keys
        ]

    panel = PricePanel.from_frame(panel_frame)
    row_by_key = {
        (pd.Timestamp(date).normalize(), str(code).strip().upper()): row
        for row, (date, code) in enumerate(
            zip(panel.index.trade_dates, panel.stock_code_by_row)
        )
    }

    def _positions(source: pd.DataFrame | None) -> np.ndarray:
        if source is None or source.empty:
            return np.empty(0, dtype=np.int64)
        rows: list[int] = []
        seen: set[int] = set()
        for item in source.itertuples(index=False):
            key = (
                pd.Timestamp(getattr(item, "trade_date")).normalize(),
                str(getattr(item, "stock_code")).strip().upper(),
            )
            row = row_by_key.get(key)
            if row is None:
                continue
            if row not in seen:
                rows.append(row)
                seen.add(row)
        return np.asarray(rows, dtype=np.int64)

    selected_rows = _positions(signal_df)
    candidate_only = _positions(candidate_df)
    candidate_rows = np.asarray(
        list(dict.fromkeys(selected_rows.tolist() + candidate_only.tolist())),
        dtype=np.int64,
    )
    directions = panel.columns.get(SIGNAL_COL_DIRECTION)
    if directions is None:
        final_buy = np.zeros(panel.row_count, dtype=bool)
        final_sell = np.zeros(panel.row_count, dtype=bool)
    else:
        normalized_direction = pd.Series(directions, dtype=object).fillna("").astype(str).str.lower()
        final_buy = normalized_direction.eq(DIRECTION_BUY).to_numpy(dtype=bool)
        final_sell = normalized_direction.eq(DIRECTION_SELL).to_numpy(dtype=bool)
    for name in ("buy_signal", "buy_raw"):
        values = panel.columns.get(name)
        if values is not None:
            final_buy |= pd.Series(values).fillna(False).astype(bool).to_numpy()
    for name in ("sell_signal", "sell_raw"):
        values = panel.columns.get(name)
        if values is not None:
            final_sell |= pd.Series(values).fillna(False).astype(bool).to_numpy()
    final_buy[selected_rows] = True
    final_buy[candidate_rows] = True
    selection = SelectionBatch(
        buy_rows=candidate_rows,
        tradable_rows=candidate_rows,
        candidate_rows=candidate_rows,
        selected_rows=selected_rows,
        final_buy=final_buy,
        final_sell=final_sell,
        diagnostics={},
    )
    return panel, selection


def _order_signal_fixture(
    signal_df: pd.DataFrame,
    *,
    spec: object | None,
) -> pd.DataFrame:
    sort_spec = tuple(getattr(spec, "sort_spec", ()) or ())
    if not sort_spec:
        return signal_df
    normalized = signal_df.copy()
    normalized["trade_date"] = pd.to_datetime(
        normalized["trade_date"], errors="raise"
    ).dt.normalize()
    normalized["stock_code"] = (
        normalized["stock_code"].astype(str).str.strip().str.upper()
    )
    columns = ["trade_date"]
    ascending = [True]
    for name, is_ascending in sort_spec:
        column = str(name)
        if column in normalized.columns and column not in columns:
            columns.append(column)
            ascending.append(bool(is_ascending))
    if "stock_code" not in columns:
        columns.append("stock_code")
        ascending.append(True)
    return normalized.sort_values(
        columns,
        ascending=ascending,
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)


def make_panel_decision_inputs(
    price_frames: Mapping[str, pd.DataFrame],
    signal_df: pd.DataFrame,
    *,
    candidate_df: pd.DataFrame | None = None,
) -> tuple[SelectionBatch, QuoteStore]:
    """Convert compact DataFrame fixtures at the test boundary only."""
    panel, selection = make_panel_selection(
        price_frames,
        signal_df,
        candidate_df=candidate_df,
    )
    return selection, panel.quote_store()


def build_trade_segments_from_frames(
    *,
    signal_df: pd.DataFrame,
    price_frames: Mapping[str, pd.DataFrame],
    **kwargs: object,
):
    from backtest.allocation.allocator import build_trade_segments

    ordered = _order_signal_fixture(signal_df, spec=kwargs.get("spec"))
    selection, quotes = make_panel_decision_inputs(price_frames, ordered)
    return build_trade_segments(
        selection=selection,
        quote_store=quotes,
        **kwargs,
    )


def run_broker_portfolio_from_frames(
    signal_df: pd.DataFrame,
    trade_days: object,
    *,
    price_frames: Mapping[str, pd.DataFrame],
    candidate_df: pd.DataFrame | None = None,
    **kwargs: object,
):
    from backtest.broker.runner import run_broker_portfolio

    ordered = _order_signal_fixture(signal_df, spec=kwargs.get("spec"))
    ordered_candidates = (
        _order_signal_fixture(candidate_df, spec=kwargs.get("spec"))
        if candidate_df is not None
        else None
    )
    selection, quotes = make_panel_decision_inputs(
        price_frames,
        ordered,
        candidate_df=ordered_candidates,
    )
    return run_broker_portfolio(
        selection,
        quotes,
        trade_days,
        **kwargs,
    )


def build_trade_plan_from_frames(
    signal_df: pd.DataFrame,
    *,
    price_frames: Mapping[str, pd.DataFrame],
    **kwargs: object,
):
    from backtest.broker.plan import build_trade_plan

    ordered = _order_signal_fixture(signal_df, spec=kwargs.get("spec"))
    selection, quotes = make_panel_decision_inputs(price_frames, ordered)
    return build_trade_plan(selection, quotes, **kwargs)


def evaluate_topn_signal_trade_segments_from_frames(
    topn_df: pd.DataFrame,
    *,
    price_frames: Mapping[str, pd.DataFrame],
    **kwargs: object,
):
    from backtest.evaluation.topn_signal_segments import (
        evaluate_topn_signal_trade_segments,
    )

    selection, quotes = make_panel_decision_inputs(price_frames, topn_df)
    return evaluate_topn_signal_trade_segments(selection, quotes, **kwargs)


def build_all_trade_segments_from_frames(
    signal_df: pd.DataFrame,
    *,
    price_frames: Mapping[str, pd.DataFrame],
    **kwargs: object,
):
    from backtest.allocation.trade_extraction import build_all_trade_segments

    selection, quotes = make_panel_decision_inputs(price_frames, signal_df)
    return build_all_trade_segments(selection, quotes, **kwargs)


__all__ = [
    "StrategyPanelRun",
    "build_all_trade_segments_from_frames",
    "build_trade_plan_from_frames",
    "build_trade_segments_from_frames",
    "evaluate_topn_signal_trade_segments_from_frames",
    "make_panel_decision_inputs",
    "make_panel_selection",
    "make_quote_store",
    "run_broker_portfolio_from_frames",
    "run_panel_strategy",
]
