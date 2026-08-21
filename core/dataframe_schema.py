from __future__ import annotations

import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    SIGNAL_COL_BUY,
    SIGNAL_COL_BUY_RAW,
    SIGNAL_COL_DIRECTION,
    SIGNAL_COL_SELL,
    SIGNAL_COL_SELL_RAW,
    VALID_DIRECTIONS,
)
from core.exceptions import DataValidationError
from core.invariants import assert_sorted_unique, ensure_trade_date_normalized
from core.normalizers import normalize_direction_series
from core.schema import PRICE_NORMALIZED_ATTR, SIGNAL_NORMALIZED_ATTR, _extract_sort_columns

_VALIDATION_HOOKS = None


def _get_validation_hooks():
    global _VALIDATION_HOOKS
    if _VALIDATION_HOOKS is not None:
        return _VALIDATION_HOOKS
    from core.validation import maybe_validate_price_frame, maybe_validate_signal_frame

    _VALIDATION_HOOKS = maybe_validate_signal_frame, maybe_validate_price_frame
    return _VALIDATION_HOOKS


def clear_dataframe_schema_cache() -> None:
    """Clear lazy validation hook imports for test/runtime isolation."""
    global _VALIDATION_HOOKS
    _VALIDATION_HOOKS = None


def _maybe_validate_signal_frame(df: pd.DataFrame, *, source: str) -> None:
    maybe_validate_signal_frame, _ = _get_validation_hooks()
    maybe_validate_signal_frame(df, source=source)


def _maybe_validate_price_frame(df: pd.DataFrame, *, source: str) -> None:
    _, maybe_validate_price_frame = _get_validation_hooks()
    maybe_validate_price_frame(df, source=source)


def assert_price_frame_contract(df: pd.DataFrame, *, source: str = "price_frame") -> None:
    """Lightweight public assertion for tests/tools that need price-frame shape only."""
    required = {COL_TRADE_DATE, COL_STOCK_CODE, "open", "high", "low", "close"}
    missing = required - set(getattr(df, "columns", ()))
    if missing:
        raise DataValidationError(f"{source} missing columns: {sorted(missing)}")
    if df.empty:
        raise DataValidationError(f"{source} is empty")
    ensure_trade_date_normalized(df, col=COL_TRADE_DATE)


def assert_signal_frame_contract(df: pd.DataFrame, *, source: str = "signal_frame") -> None:
    """Lightweight public assertion for tests/tools that need signal-frame shape only."""
    required = {COL_TRADE_DATE, COL_STOCK_CODE, SIGNAL_COL_DIRECTION}
    missing = required - set(getattr(df, "columns", ()))
    if missing:
        raise DataValidationError(f"{source} missing columns: {sorted(missing)}")
    if df.empty:
        raise DataValidationError(f"{source} is empty")
    ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
    invalid = set(normalize_direction_series(df[SIGNAL_COL_DIRECTION]).unique()) - VALID_DIRECTIONS
    if invalid:
        raise DataValidationError(f"{source} direction invalid values: {sorted(invalid)}")


def normalize_signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a signal frame (trade_date/stock_code/direction)."""
    if df is None or df.empty:
        raise DataValidationError("signal_df is empty")
    if bool(df.attrs.get(SIGNAL_NORMALIZED_ATTR, False)):
        return df

    required = {COL_TRADE_DATE, COL_STOCK_CODE, SIGNAL_COL_DIRECTION}
    missing = required - set(df.columns)
    if missing:
        raise DataValidationError(f"signal_df missing columns: {sorted(missing)}")

    out = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
    if out is df:
        out = df.copy()

    out[COL_STOCK_CODE] = out[COL_STOCK_CODE].astype(str).str.strip()
    if (out[COL_STOCK_CODE] == "").any():
        bad = out.loc[out[COL_STOCK_CODE] == "", [COL_TRADE_DATE, COL_STOCK_CODE]].head(5).to_dict(orient="records")
        raise DataValidationError(f"signal_df stock_code empty (sample): {bad}")

    out[SIGNAL_COL_DIRECTION] = normalize_direction_series(out[SIGNAL_COL_DIRECTION])
    invalid = set(out[SIGNAL_COL_DIRECTION].unique()) - VALID_DIRECTIONS
    if invalid:
        raise DataValidationError(f"signal_df direction invalid values: {sorted(invalid)}")

    out.attrs[SIGNAL_NORMALIZED_ATTR] = True
    _maybe_validate_signal_frame(out, source="normalize_signal_frame")
    return out


def normalize_signal_frame_strict(
    df: pd.DataFrame,
    *,
    require_unique: bool = True,
) -> pd.DataFrame:
    out = normalize_signal_frame(df)
    if require_unique and {COL_TRADE_DATE, COL_STOCK_CODE}.issubset(out.columns):
        assert_sorted_unique(out, [COL_TRADE_DATE, COL_STOCK_CODE], name="signal_df trade_date+stock_code")
    return out


def normalize_price_frame(
    df: pd.DataFrame,
    *,
    stock_code: str | None = None,
    source: str = "",
) -> pd.DataFrame:
    """Normalize a price frame (trade_date/stock_code/open/high/low/close/direction)."""
    if df is None or df.empty:
        prefix = f"{stock_code}: " if stock_code else ""
        raise DataValidationError(f"{prefix}stock_data 为空（source={source}）。")

    if bool(df.attrs.get(PRICE_NORMALIZED_ATTR, False)):
        return df

    out = df.copy()
    out.columns = out.columns.astype(str).str.lower()

    required_cols = {COL_TRADE_DATE, COL_STOCK_CODE, "open", "close", "high", "low", SIGNAL_COL_DIRECTION}
    missing = required_cols - set(out.columns)
    if missing:
        prefix = f"{stock_code}: " if stock_code else ""
        raise DataValidationError(f"{prefix}stock_data 缺少列 {sorted(missing)}（source={source}）。")

    out = ensure_trade_date_normalized(out, col=COL_TRADE_DATE)

    for col in ("open", "close", "high", "low"):
        out[col] = pd.to_numeric(out[col], errors="raise")
        if out[col].isna().any():
            prefix = f"{stock_code}: " if stock_code else ""
            bad = out.loc[out[col].isna(), [COL_TRADE_DATE, COL_STOCK_CODE]].head(5).to_dict(orient="records")
            raise DataValidationError(f"{prefix}stock_data 列 {col} 存在 NaN（示例：{bad}）：{source}")

    out = out.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    assert_sorted_unique(out, [COL_TRADE_DATE], name=f"{stock_code or 'price_df'} trade_date")
    if isinstance(out[SIGNAL_COL_DIRECTION].dtype, pd.CategoricalDtype):
        out[SIGNAL_COL_DIRECTION] = out[SIGNAL_COL_DIRECTION].astype(object)
    out[SIGNAL_COL_DIRECTION] = normalize_direction_series(out[SIGNAL_COL_DIRECTION])
    base_cols = [COL_TRADE_DATE, COL_STOCK_CODE, "open", "close", "high", "low", SIGNAL_COL_DIRECTION]
    if "volume" in out.columns:
        base_cols.append("volume")
    extra_cols = [c for c in out.columns if c not in base_cols]
    out = out[base_cols + extra_cols]
    if out.empty:
        prefix = f"{stock_code}: " if stock_code else ""
        raise DataValidationError(f"{prefix}stock_data 为空（标准化后为空，source={source}）。")
    out.attrs[PRICE_NORMALIZED_ATTR] = True
    _maybe_validate_price_frame(out, source=f"normalize_price_frame:{source or 'unknown'}")
    return out


def normalize_signal_output(
    df: pd.DataFrame,
    *,
    sort_spec: object,
    assume_signal_normalized: bool = False,
) -> pd.DataFrame:
    """Normalize a strategy signal frame and validate required columns."""
    out = df if assume_signal_normalized else normalize_signal_frame(df)

    signal_cols = {SIGNAL_COL_BUY_RAW, SIGNAL_COL_SELL_RAW, SIGNAL_COL_BUY, SIGNAL_COL_SELL}
    for col in list(out.columns):
        low = str(col).lower()
        if low in signal_cols and col != low:
            if low in out.columns:
                out = out.drop(columns=[col])
            else:
                out = out.rename(columns={col: low})

    raw_missing = {SIGNAL_COL_BUY_RAW, SIGNAL_COL_SELL_RAW} - set(out.columns)
    if raw_missing:
        raise DataValidationError("strategy signal output missing buy_signal_raw/sell_signal_raw")

    cols = _extract_sort_columns(sort_spec)
    missing = [c for c in cols if c not in out.columns]
    if missing:
        raise DataValidationError(f"strategy signal output missing sort_spec columns: {missing}")

    out.attrs[SIGNAL_NORMALIZED_ATTR] = True
    return out


__all__ = [
    "assert_price_frame_contract",
    "assert_signal_frame_contract",
    "clear_dataframe_schema_cache",
    "normalize_price_frame",
    "normalize_signal_frame",
    "normalize_signal_frame_strict",
    "normalize_signal_output",
]
