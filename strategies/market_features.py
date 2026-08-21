"""Position-aligned market ranks, aggregates, and cross-section transforms."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
import time

import numpy as np
import pandas as pd

from core.exceptions import DataValidationError, StrategyError
from strategies.advanced import CrossSectionTransformSpec
from strategies.authoring.fields import is_reserved_field
from strategies.spec import MarketAggregateSpec, MarketRankSpec

from core.panel_native import ColumnStore, PanelView, PricePanel


_AGGREGATES = frozenset({"mean", "sum", "median", "count", "min", "max", "std"})


def _coerce_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value).strip().casefold()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    raise StrategyError(f"{field} cannot be parsed as bool: {value!r}")


def _coerce_rank_spec(raw: object, *, index: int) -> MarketRankSpec:
    if isinstance(raw, MarketRankSpec):
        return raw
    getter = raw.get if isinstance(raw, Mapping) else lambda name, default=None: getattr(raw, name, default)
    required = ("feature_col", "rank_col", "pct_col", "count_col", "ascending")
    missing = [name for name in required if getter(name, None) is None or not str(getter(name, "")).strip()]
    if missing:
        raise StrategyError(f"market_rank_specs[{index}] is missing fields: {missing}")
    return MarketRankSpec(
        feature_col=str(getter("feature_col")).strip(),
        rank_col=str(getter("rank_col")).strip(),
        pct_col=str(getter("pct_col")).strip(),
        count_col=str(getter("count_col")).strip(),
        ascending=_coerce_bool(
            getter("ascending"),
            field=f"market_rank_specs[{index}].ascending",
        ),
    )


def _coerce_aggregate_spec(raw: object, *, index: int) -> MarketAggregateSpec:
    if isinstance(raw, MarketAggregateSpec):
        return raw
    getter = raw.get if isinstance(raw, Mapping) else lambda name, default=None: getattr(raw, name, default)
    required = ("source_col", "output_col", "agg")
    missing = [name for name in required if getter(name, None) is None or not str(getter(name, "")).strip()]
    if missing:
        raise StrategyError(f"market_aggregate_specs[{index}] is missing fields: {missing}")
    agg = str(getter("agg")).strip().casefold()
    if agg not in _AGGREGATES:
        raise StrategyError(f"market_aggregate_specs[{index}].agg is unsupported: {agg!r}")
    return MarketAggregateSpec(
        source_col=str(getter("source_col")).strip(),
        output_col=str(getter("output_col")).strip(),
        agg=agg,
    )


def _numeric_array(values: np.ndarray) -> np.ndarray:
    series = pd.to_numeric(pd.Series(values, copy=False), errors="coerce")
    return series.to_numpy(dtype=np.float64, na_value=math.nan, copy=False)


def _market_specs(spec: object) -> tuple[tuple[MarketRankSpec, ...], tuple[MarketAggregateSpec, ...]]:
    raw_ranks = getattr(spec, "market_rank_specs", ()) or ()
    raw_aggregates = getattr(spec, "market_aggregate_specs", ()) or ()
    if not isinstance(raw_ranks, (list, tuple)):
        raise StrategyError("market_rank_specs must be a list or tuple")
    if not isinstance(raw_aggregates, (list, tuple)):
        raise StrategyError("market_aggregate_specs must be a list or tuple")
    ranks = tuple(_coerce_rank_spec(value, index=index) for index, value in enumerate(raw_ranks))
    aggregates = tuple(
        _coerce_aggregate_spec(value, index=index)
        for index, value in enumerate(raw_aggregates)
    )
    if not ranks and not aggregates:
        raise StrategyError(
            "use_market_features=True requires market_rank_specs or market_aggregate_specs"
        )
    return ranks, aggregates


def _require_columns(view: PanelView, names: Iterable[str], *, stage: str) -> None:
    available = set(view.column_names)
    missing = sorted(set(names) - available, key=str.casefold)
    if missing:
        raise DataValidationError(f"{stage} is missing columns: {missing}")


def _rank_columns(
    panel: PricePanel,
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    index = panel.index
    numeric = _numeric_array(values)
    ranks_float = np.full(index.row_count, np.nan, dtype=np.float64)
    counts_float = np.full(index.row_count, np.nan, dtype=np.float64)
    pct = np.full(index.row_count, np.nan, dtype=np.float64)
    all_valid = True
    for date_id in range(len(index.date_offsets) - 1):
        rows = index.date_rows(date_id)
        date_values = numeric[rows]
        valid = ~np.isnan(date_values)
        if not bool(valid.all()):
            all_valid = False
        valid_rows = rows[valid]
        if not len(valid_rows):
            continue
        valid_values = numeric[valid_rows]
        # date_rows already use normalized stock-code order, so stable value sort
        # preserves the existing stock_code tie-break.
        order = np.argsort(valid_values, kind="stable")
        count = len(valid_rows)
        ranked_rows = valid_rows[order]
        rank_values = np.arange(1, count + 1, dtype=np.int64)
        ranks_float[ranked_rows] = rank_values
        counts_float[valid_rows] = count
        pct[ranked_rows] = rank_values / count
    if all_valid:
        return ranks_float.astype(np.int64), pct, counts_float.astype(np.int64)
    return ranks_float, pct, counts_float


def apply_market_features(
    panel: PricePanel,
    store: ColumnStore,
    *,
    spec: object,
) -> dict[str, object]:
    """Register declared market columns without key merges or stock splits."""
    if not bool(getattr(spec, "use_market_features", False)):
        return {"enabled": False, "row_count": panel.row_count}
    started = time.perf_counter()
    ranks, aggregates = _market_specs(spec)
    view = PanelView(panel, store)
    _require_columns(
        view,
        [
            *(item.feature_col for item in ranks),
            *(item.source_col for item in aggregates),
        ],
        stage="panel market features",
    )
    for item in ranks:
        values = _numeric_array(view.column(item.feature_col))
        if item.ascending:
            sortable = values
        else:
            sortable = -values
        rank, pct, count = _rank_columns(panel, sortable)
        store.register(item.rank_col, rank, owner="market_rank")
        store.register(item.pct_col, pct, owner="market_rank")
        store.register(item.count_col, count, owner="market_rank")

    date_ids = panel.index.date_id_by_row
    for item in aggregates:
        source = pd.to_numeric(pd.Series(view.column(item.source_col), copy=False), errors="coerce")
        grouped = source.groupby(date_ids, sort=False)
        values = grouped.transform(item.agg).to_numpy(copy=False)
        store.register(item.output_col, values, owner="market_aggregate")
    return {
        "enabled": True,
        "row_count": panel.row_count,
        "stock_count": len(panel.index.stock_codes),
        "date_count": len(panel.index.date_offsets) - 1,
        "elapsed_sec": time.perf_counter() - started,
    }


def _coerce_cross_section_specs(
    specs: Iterable[CrossSectionTransformSpec | Mapping[str, object]],
) -> tuple[CrossSectionTransformSpec, ...]:
    return tuple(
        CrossSectionTransformSpec.from_value(
            item.to_dict() if isinstance(item, CrossSectionTransformSpec) else item,
            index=index,
        )
        for index, item in enumerate(specs)
    )


def _group_keys(view: PanelView, group_by: tuple[str, ...]) -> list[pd.Series]:
    return [
        pd.Series(view.date_id_by_row, copy=False),
        *(pd.Series(view.column(name), copy=False) for name in group_by),
    ]


def _grouped(
    view: PanelView,
    values: pd.Series,
    group_by: tuple[str, ...],
):
    return values.groupby(_group_keys(view, group_by), sort=False, dropna=False)


def _winsorize(
    view: PanelView,
    values: pd.Series,
    spec: CrossSectionTransformSpec,
) -> pd.Series:
    grouped = _grouped(view, values, spec.group_by)
    lower = grouped.transform(lambda item: item.quantile(spec.lower_quantile))
    upper = grouped.transform(lambda item: item.quantile(spec.upper_quantile))
    return values.clip(lower=lower, upper=upper)


def _zscore(
    view: PanelView,
    values: pd.Series,
    group_by: tuple[str, ...],
) -> pd.Series:
    grouped = _grouped(view, values, group_by)
    mean = grouped.transform("mean")
    std = grouped.transform(lambda item: item.std(ddof=0))
    return ((values - mean) / std.where(std.ne(0))).replace([np.inf, -np.inf], np.nan)


def _neutralize(
    view: PanelView,
    values: pd.Series,
    spec: CrossSectionTransformSpec,
) -> pd.Series:
    output = np.full(view.row_count, np.nan, dtype=np.float64)
    key_frame = pd.DataFrame(
        {
            "__date_id": view.date_id_by_row,
            **{name: view.column(name) for name in spec.group_by},
        }
    )
    groups = key_frame.groupby(
        ["__date_id", *spec.group_by],
        sort=False,
        dropna=False,
    ).indices
    exposures_all = np.column_stack(
        [_numeric_array(view.column(name)) for name in spec.exposure_cols]
    )
    for positions in groups.values():
        pos = np.asarray(positions, dtype=np.int64)
        y = values.iloc[pos].to_numpy(dtype=np.float64, copy=False)
        x = exposures_all[pos]
        valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
        if int(valid.sum()) <= x.shape[1] + 1:
            continue
        design = np.column_stack((np.ones(int(valid.sum()), dtype=float), x[valid]))
        coefficients, *_ = np.linalg.lstsq(design, y[valid], rcond=None)
        group_output = np.full(len(pos), np.nan, dtype=np.float64)
        group_output[valid] = y[valid] - design @ coefficients
        output[pos] = group_output
    return pd.Series(output)


def apply_cross_section_transforms(
    panel: PricePanel,
    store: ColumnStore,
    specs: Iterable[CrossSectionTransformSpec | Mapping[str, object]],
) -> dict[str, object]:
    """Register declared cross-section outputs on their original row positions."""
    parsed = _coerce_cross_section_specs(specs)
    if not parsed:
        return {"enabled": False, "output_columns": ()}
    view = PanelView(panel, store)
    outputs: list[str] = []
    for spec in parsed:
        _require_columns(
            view,
            [spec.source_col, *spec.group_by, *spec.exposure_cols],
            stage="cross-section transform",
        )
        if is_reserved_field(spec.output_col):
            raise StrategyError(
                f"cross-section output column is framework-owned: {spec.output_col}"
            )
        values = pd.to_numeric(pd.Series(view.column(spec.source_col), copy=False), errors="coerce")
        if spec.method == "rank":
            transformed = _grouped(view, values, spec.group_by).rank(
                method="average",
                ascending=spec.ascending,
                na_option="keep",
            )
        elif spec.method == "pct_rank":
            transformed = _grouped(view, values, spec.group_by).rank(
                method="average",
                ascending=spec.ascending,
                na_option="keep",
                pct=True,
            )
        elif spec.method == "winsorize":
            transformed = _winsorize(view, values, spec)
        elif spec.method == "zscore":
            transformed = _zscore(view, values, spec.group_by)
        elif spec.method == "winsorized_zscore":
            transformed = _zscore(view, _winsorize(view, values, spec), spec.group_by)
        elif spec.method == "neutralize":
            transformed = _neutralize(view, values, spec)
        else:
            raise StrategyError(f"unsupported cross-section method: {spec.method!r}")
        store.register(
            spec.output_col,
            transformed.to_numpy(copy=False),
            owner="cross_section",
        )
        outputs.append(spec.output_col)
        view = PanelView(panel, store)
    return {"enabled": True, "output_columns": tuple(outputs)}


__all__ = ["apply_cross_section_transforms", "apply_market_features"]
