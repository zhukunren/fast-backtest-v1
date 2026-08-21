"""Point-in-time-safe joins for preloaded auxiliary daily data."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pandas as pd

from core.invariants import assert_price_frame_preserved, capture_price_frame_invariants

from .advanced import AdvancedStrategySpec, AuxiliaryDataSpec


_PIT_AVAILABLE_COLUMN = "__pit_available_at__"


def _frozen_frames(
    value: Mapping[str, pd.DataFrame] | None,
    *,
    uppercase_keys: bool = False,
) -> Mapping[str, pd.DataFrame]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("strategy data inputs must be mappings")
    frames: dict[str, pd.DataFrame] = {}
    for raw_name, frame in value.items():
        name = str(raw_name or "").strip()
        if uppercase_keys:
            name = name.upper()
        if not name:
            raise ValueError("strategy data input contains an empty key")
        if name in frames:
            raise ValueError(f"duplicate strategy data input: {name}")
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"strategy data input {name!r} must be a pandas.DataFrame")
        frames[name] = frame
    return MappingProxyType(frames)


@dataclass(frozen=True, slots=True)
class StrategyDataInputs:
    """Preloaded data frames keyed by declared dataset and reference symbol."""

    auxiliary: Mapping[str, pd.DataFrame] = field(
        default_factory=lambda: MappingProxyType({})
    )
    references: Mapping[str, pd.DataFrame] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def from_value(
        cls,
        value: StrategyDataInputs | Mapping[str, Any] | None,
    ) -> StrategyDataInputs:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("strategy data inputs must be a mapping or StrategyDataInputs")
        return cls(
            auxiliary=_frozen_frames(value.get("auxiliary")),
            references=_frozen_frames(
                value.get("references"),
                uppercase_keys=True,
            ),
        )


def _coerce_spec(value: AuxiliaryDataSpec | Mapping[str, object]) -> AuxiliaryDataSpec:
    raw = value.to_dict() if isinstance(value, AuxiliaryDataSpec) else value
    return AuxiliaryDataSpec.from_value(raw)


def _normalize_dates(frame: pd.DataFrame, column: str, *, source: str) -> pd.DataFrame:
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="raise").dt.normalize()
    if out[column].isna().any():
        raise ValueError(f"{source}.{column} contains null dates")
    return out


def _group_key(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else (value,)


def _group_indices(frame: pd.DataFrame, columns: tuple[str, ...]):
    if not columns:
        return {(): list(range(len(frame)))}
    return frame.groupby(list(columns), sort=False, dropna=False).indices


def _require_non_null_keys(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    source: str,
) -> None:
    if not columns:
        return
    null_rows = frame.loc[:, list(columns)].isna().any(axis=1)
    if null_rows.any():
        raise ValueError(f"{source} contains null point-in-time keys: {list(columns)}")


def _validate_point_in_time_contract(
    panel: pd.DataFrame,
    auxiliary: pd.DataFrame,
    parsed: AuxiliaryDataSpec,
) -> None:
    if not isinstance(auxiliary, pd.DataFrame):
        raise TypeError("auxiliary data must be a pandas.DataFrame")
    panel_required = {"trade_date", *parsed.key_columns}
    auxiliary_required = {
        parsed.available_at_column,
        *parsed.key_columns,
        *parsed.fields,
    }
    missing_panel = sorted(panel_required - set(panel.columns), key=str.casefold)
    missing_auxiliary = sorted(auxiliary_required - set(auxiliary.columns), key=str.casefold)
    if missing_panel:
        raise ValueError(f"price panel is missing point-in-time keys: {missing_panel}")
    if missing_auxiliary:
        raise ValueError(f"auxiliary data is missing columns: {missing_auxiliary}")
    output_columns = [*parsed.fields]
    if parsed.available_at_output_col:
        output_columns.append(parsed.available_at_output_col)
    overlap = sorted(set(output_columns) & set(panel.columns), key=str.casefold)
    if overlap:
        raise ValueError(f"point-in-time output columns already exist: {overlap}")
    if parsed.required and auxiliary.empty and not panel.empty:
        raise ValueError(f"required auxiliary dataset is empty: {parsed.dataset}")


def _empty_point_in_time_result(
    panel: pd.DataFrame,
    auxiliary: pd.DataFrame,
    parsed: AuxiliaryDataSpec,
) -> pd.DataFrame:
    out = panel.copy()
    for name in parsed.fields:
        out[name] = pd.Series(index=out.index, dtype=auxiliary[name].dtype)
    if parsed.available_at_output_col:
        out[parsed.available_at_output_col] = pd.Series(
            index=out.index,
            dtype="datetime64[ns]",
        )
    out.attrs.update(getattr(panel, "attrs", {}) or {})
    return out


def _join_point_in_time_group(
    left_part: pd.DataFrame,
    right_part: pd.DataFrame | None,
    parsed: AuxiliaryDataSpec,
) -> pd.DataFrame:
    if right_part is None or right_part.empty:
        part = left_part
        for name in parsed.fields:
            part[name] = pd.NA
        part[_PIT_AVAILABLE_COLUMN] = pd.NaT
        return part
    available = right_part.loc[:, [_PIT_AVAILABLE_COLUMN, *parsed.fields]].sort_values(
        _PIT_AVAILABLE_COLUMN,
        kind="mergesort",
    )
    ordered_left = left_part.sort_values("trade_date", kind="mergesort")
    if parsed.join_policy == "exact":
        return ordered_left.merge(
            available,
            how="left",
            left_on="trade_date",
            right_on=_PIT_AVAILABLE_COLUMN,
            sort=False,
            validate="many_to_one",
        )
    return pd.merge_asof(
        ordered_left,
        available,
        left_on="trade_date",
        right_on=_PIT_AVAILABLE_COLUMN,
        direction="backward",
        allow_exact_matches=True,
    )


def _join_point_in_time_groups(
    left: pd.DataFrame,
    right: pd.DataFrame,
    parsed: AuxiliaryDataSpec,
) -> pd.DataFrame:
    right_groups = {
        _group_key(key): right.iloc[list(positions)].copy()
        for key, positions in _group_indices(right, parsed.key_columns).items()
    }
    joined_parts: list[pd.DataFrame] = []
    for key, positions in _group_indices(left, parsed.key_columns).items():
        pos = list(positions)
        left_part = left.iloc[pos].loc[:, ["trade_date"]].copy()
        left_part["__pit_row_order__"] = pos
        joined_parts.append(
            _join_point_in_time_group(
                left_part,
                right_groups.get(_group_key(key)),
                parsed,
            )
        )
    return pd.concat(joined_parts, ignore_index=True).sort_values(
        "__pit_row_order__",
        kind="mergesort",
    )


def point_in_time_join(
    panel: pd.DataFrame,
    auxiliary: pd.DataFrame,
    spec: AuxiliaryDataSpec | Mapping[str, object],
) -> pd.DataFrame:
    """Join the latest record available no later than each signal date."""
    parsed = _coerce_spec(spec)
    _validate_point_in_time_contract(panel, auxiliary, parsed)
    expected = capture_price_frame_invariants(panel, error_cls=ValueError)

    left = _normalize_dates(panel, "trade_date", source="panel")
    right = _normalize_dates(auxiliary, parsed.available_at_column, source=parsed.dataset)
    _require_non_null_keys(left, parsed.key_columns, source="price panel")
    _require_non_null_keys(right, parsed.key_columns, source=parsed.dataset)
    if right.duplicated(
        subset=[*parsed.key_columns, parsed.available_at_column],
        keep=False,
    ).any():
        raise ValueError("auxiliary data has duplicate normalized key+available_at rows")

    if left.empty:
        return _empty_point_in_time_result(panel, auxiliary, parsed)

    right_columns = [*parsed.key_columns, parsed.available_at_column, *parsed.fields]
    right = right.loc[:, right_columns].rename(
        columns={parsed.available_at_column: _PIT_AVAILABLE_COLUMN}
    )
    joined = _join_point_in_time_groups(left, right, parsed)
    available_values = joined[_PIT_AVAILABLE_COLUMN]
    invalid_availability = available_values.notna() & available_values.gt(joined["trade_date"])
    if invalid_availability.any():
        raise ValueError("point-in-time join produced available_at after trade_date")
    if parsed.required and available_values.isna().all():
        raise ValueError(
            f"required auxiliary dataset has no records available to the panel: {parsed.dataset}"
        )
    out = panel.copy()
    for name in parsed.fields:
        out[name] = joined[name].to_numpy(copy=False)
    if parsed.available_at_output_col:
        out[parsed.available_at_output_col] = available_values.to_numpy(copy=False)
    out.attrs.update(getattr(panel, "attrs", {}) or {})
    assert_price_frame_preserved(
        expected,
        out,
        source="point_in_time_join",
        error_cls=ValueError,
    )
    return out


def point_in_time_join_many(
    panel: pd.DataFrame,
    requests: Iterable[
        tuple[AuxiliaryDataSpec | Mapping[str, object], pd.DataFrame]
    ],
) -> pd.DataFrame:
    out = panel
    for spec, auxiliary in requests:
        out = point_in_time_join(out, auxiliary, spec)
    return out


def apply_declared_data_inputs(
    panel: pd.DataFrame,
    advanced: AdvancedStrategySpec,
    inputs: StrategyDataInputs | Mapping[str, Any] | None,
) -> pd.DataFrame:
    """Apply every declared PIT and reference input in contract order."""
    if not isinstance(advanced, AdvancedStrategySpec):
        raise TypeError("advanced must be AdvancedStrategySpec")
    resolved = StrategyDataInputs.from_value(inputs)
    out = panel
    for spec in advanced.auxiliary_data:
        frame = resolved.auxiliary.get(spec.dataset)
        if frame is None:
            if spec.required:
                raise ValueError(
                    f"required auxiliary dataset was not supplied: {spec.dataset}"
                )
            continue
        out = point_in_time_join(out, frame, spec)
    for spec in advanced.reference_series:
        frame = resolved.references.get(spec.symbol.strip().upper())
        if frame is None:
            raise ValueError(
                f"required reference series was not supplied: {spec.symbol}"
            )
        out = align_reference_series(out, frame, spec)
    return out


def align_reference_series(
    panel: pd.DataFrame,
    reference: pd.DataFrame,
    spec,
) -> pd.DataFrame:
    """Align one market/reference series to every panel row by trade date."""
    from .advanced import ReferenceSeriesSpec

    parsed = ReferenceSeriesSpec.from_value(
        spec.to_dict() if isinstance(spec, ReferenceSeriesSpec) else spec
    )
    work = reference
    symbol_column = next(
        (
            name
            for name in ("symbol", "ts_code", "index_code", "stock_code")
            if name in work.columns
        ),
        None,
    )
    if symbol_column is not None:
        expected_symbol = parsed.symbol.strip().upper()
        actual_symbols = work[symbol_column].astype(str).str.strip().str.upper()
        work = work.loc[actual_symbols.eq(expected_symbol)].copy()
        if work.empty:
            raise ValueError(
                f"reference series contains no rows for {parsed.symbol!r} in {symbol_column!r}"
            )
    missing = sorted({"trade_date", *parsed.fields} - set(work.columns), key=str.casefold)
    if missing:
        raise ValueError(f"reference series is missing columns: {missing}")
    renamed = work.loc[:, ["trade_date", *parsed.fields]].rename(
        columns={name: f"{parsed.output_prefix}{name}" for name in parsed.fields}
    )
    auxiliary_spec = AuxiliaryDataSpec(
        dataset=parsed.name,
        fields=tuple(f"{parsed.output_prefix}{name}" for name in parsed.fields),
        key_columns=(),
        available_at_column="trade_date",
        available_at_output_col=f"{parsed.output_prefix}available_at",
        join_policy=parsed.join_policy,
        required=True,
    )
    return point_in_time_join(panel, renamed, auxiliary_spec)


__all__ = [
    "StrategyDataInputs",
    "align_reference_series",
    "apply_declared_data_inputs",
    "point_in_time_join",
    "point_in_time_join_many",
]
