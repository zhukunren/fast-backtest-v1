"""Run one complete-panel formula and project its derived arrays.

The public strategy contract is array-native.  These adapters keep formula-heavy
strategies readable while ensuring a formula is invoked exactly once for the
sealed panel.  Time-series formulas must enforce their own ``stock_code``
boundaries with grouped operations.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from core.panel_contracts import FeatureBatch, SignalBatch
from core.panel_native import PanelView
from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL_RAW,
    SIGNAL_COLS_ALL,
)
from core.exceptions import StrategyError
from strategies.signals import coerce_signal_bool_array


FrameBuilder = Callable[[pd.DataFrame], object]


def frame_from_view(
    panel: PanelView,
    *,
    columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    names = tuple(columns) if columns is not None else panel.column_names
    missing = sorted(set(names) - set(panel.column_names), key=str.casefold)
    if missing:
        raise StrategyError(f"strategy frame input is missing columns: {missing}")
    return pd.DataFrame(
        {name: panel.column(name) for name in names},
        copy=False,
    )


def _framework_output_columns(meta: Mapping[str, object]) -> frozenset[str]:
    output: set[str] = set(SIGNAL_COLS_ALL)
    for raw in meta.get("market_rank_specs", ()) or ():
        getter = raw.get if isinstance(raw, Mapping) else lambda name, default=None: getattr(raw, name, default)
        output.update(
            str(getter(name, "") or "").strip()
            for name in ("rank_col", "pct_col", "count_col")
        )
    for raw in meta.get("market_aggregate_specs", ()) or ():
        getter = raw.get if isinstance(raw, Mapping) else lambda name, default=None: getattr(raw, name, default)
        output.add(str(getter("output_col", "") or "").strip())
    for raw in meta.get("cross_section_transform_specs", ()) or ():
        getter = raw.get if isinstance(raw, Mapping) else lambda name, default=None: getattr(raw, name, default)
        output.add(str(getter("output_col", "") or "").strip())
    return frozenset(name for name in output if name)


def _validate_result_alignment(
    source: pd.DataFrame,
    result: object,
    *,
    stage: str,
) -> pd.DataFrame:
    if not isinstance(result, pd.DataFrame):
        raise StrategyError(f"{stage} formula must return pandas.DataFrame")
    if len(result) != len(source):
        raise StrategyError(
            f"{stage} formula must preserve row count ({len(source)} vs {len(result)})"
        )
    normalized = result.reset_index(drop=True)
    source = source.reset_index(drop=True)
    missing_keys = sorted(
        {COL_TRADE_DATE, COL_STOCK_CODE} - set(normalized.columns),
        key=str.casefold,
    )
    if missing_keys:
        raise StrategyError(f"{stage} formula removed panel keys: {missing_keys}")
    shared_columns = sorted(
        set(source.columns).intersection(normalized.columns),
        key=str.casefold,
    )
    for name in shared_columns:
        if not source[name].equals(normalized[name]):
            ownership = "panel key" if name in {COL_TRADE_DATE, COL_STOCK_CODE} else "input"
            raise StrategyError(f"{stage} formula changed {ownership} column {name!r}")
    return normalized


def _feature_columns(
    source: pd.DataFrame,
    result: pd.DataFrame,
    *,
    forbidden: frozenset[str],
) -> dict[str, np.ndarray]:
    return {
        str(name): result[name].to_numpy(copy=False)
        for name in result.columns
        if name not in source.columns and name not in forbidden
    }


def _signal_result(
    source: pd.DataFrame,
    built: object,
) -> tuple[pd.DataFrame, object | None, object | None]:
    if isinstance(built, tuple) and len(built) == 3:
        buy_raw, sell_raw, result = built
        return (
            _validate_result_alignment(source, result, stage="compute_signals"),
            buy_raw,
            sell_raw,
        )
    return (
        _validate_result_alignment(source, built, stage="compute_signals"),
        None,
        None,
    )


def _coerce_raw_signals(
    result: pd.DataFrame,
    buy_raw: object | None,
    sell_raw: object | None,
) -> tuple[np.ndarray, np.ndarray]:
    if buy_raw is not None or sell_raw is not None:
        if buy_raw is None or sell_raw is None:
            raise StrategyError(
                "compute_signals formula must return both buy and sell conditions"
            )
        return (
            coerce_signal_bool_array(
                buy_raw,
                column_name=SIGNAL_COL_BUY_RAW,
                expected_index=result.index,
                error_cls=StrategyError,
            ),
            coerce_signal_bool_array(
                sell_raw,
                column_name=SIGNAL_COL_SELL_RAW,
                expected_index=result.index,
                error_cls=StrategyError,
            ),
        )
    if SIGNAL_COL_BUY_RAW in result.columns and SIGNAL_COL_SELL_RAW in result.columns:
        return (
            coerce_signal_bool_array(
                result[SIGNAL_COL_BUY_RAW],
                column_name=SIGNAL_COL_BUY_RAW,
                expected_index=result.index,
                error_cls=StrategyError,
            ),
            coerce_signal_bool_array(
                result[SIGNAL_COL_SELL_RAW],
                column_name=SIGNAL_COL_SELL_RAW,
                expected_index=result.index,
                error_cls=StrategyError,
            ),
        )
    if SIGNAL_COL_DIRECTION in result.columns:
        directions = result[SIGNAL_COL_DIRECTION].astype(str).str.strip().str.casefold()
        return (
            directions.eq(DIRECTION_BUY).to_numpy(dtype=bool, copy=False),
            directions.eq(DIRECTION_SELL).to_numpy(dtype=bool, copy=False),
        )
    raise StrategyError(
        "compute_signals formula must return raw conditions or produce "
        "buy_signal_raw and sell_signal_raw"
    )


def compute_feature_batch(
    panel: PanelView,
    builder: FrameBuilder,
    *,
    strategy_meta: Mapping[str, object],
    input_columns: Iterable[str] | None = None,
) -> FeatureBatch:
    """Build aligned feature columns from one complete panel invocation."""
    source = frame_from_view(panel, columns=input_columns)
    result = _validate_result_alignment(
        source,
        builder(source.copy(deep=True)),
        stage="compute_features",
    )
    return FeatureBatch(
        columns=_feature_columns(
            source,
            result,
            forbidden=_framework_output_columns(strategy_meta),
        )
    )


def compute_signal_batch(
    panel: PanelView,
    builder: FrameBuilder,
    *,
    input_columns: Iterable[str] | None = None,
) -> SignalBatch:
    """Build aligned signal arrays from one complete panel invocation."""
    source = frame_from_view(panel, columns=input_columns)
    result, buy_source, sell_source = _signal_result(
        source,
        builder(source.copy(deep=True)),
    )
    buy_raw, sell_raw = _coerce_raw_signals(result, buy_source, sell_source)
    columns = {
        str(name): result[name].to_numpy(copy=False)
        for name in result.columns
        if name not in source.columns and name not in SIGNAL_COLS_ALL
    }
    return SignalBatch(
        buy_raw=buy_raw,
        sell_raw=sell_raw,
        columns=columns,
    )


__all__ = [
    "compute_feature_batch",
    "compute_signal_batch",
    "frame_from_view",
]
