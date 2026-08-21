from __future__ import annotations

import pandas as pd

from core.constants import COL_STOCK_CODE, COL_TRADE_DATE
from core.invariants import ensure_trade_date_normalized


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common OHLCV column aliases to the project's canonical names."""
    df.columns = df.columns.astype(str).str.lower()
    if "ts_code" in df.columns and COL_STOCK_CODE not in df.columns:
        df = df.rename(columns={"ts_code": COL_STOCK_CODE})
    if "vol" in df.columns and "volume" not in df.columns:
        df = df.rename(columns={"vol": "volume"})
    return df


def parse_trade_date(df: pd.DataFrame) -> pd.DataFrame:
    """Parse trade_date/date/datetime or the first index-like column into trade_date."""
    date_col = None
    for col in (COL_TRADE_DATE, "date", "datetime"):
        if col in df.columns:
            date_col = col
            break

    if date_col is not None:
        df = ensure_trade_date_normalized(df, col=date_col)
        if df[date_col].isna().any():
            bad = df.loc[df[date_col].isna()].head(5).to_dict(orient="records")
            raise ValueError(f"{date_col} has invalid dates: {bad}")
        df[COL_TRADE_DATE] = df[date_col]
        return df

    df = df.reset_index(drop=False)
    idx_col = df.columns[0]
    df[COL_TRADE_DATE] = df[idx_col]
    df = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
    if df[COL_TRADE_DATE].isna().any():
        bad = df.loc[df[COL_TRADE_DATE].isna()].head(5).to_dict(orient="records")
        raise ValueError(f"index dates invalid: {bad}")
    return df.drop(columns=[idx_col])


def validate_required_columns(df: pd.DataFrame) -> None:
    """Ensure the standard OHLCV + stock_code columns exist and are numerically valid."""
    required = ["open", "high", "low", "close", "volume", COL_STOCK_CODE]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
        if df[col].isna().any():
            bad = df.loc[df[col].isna(), [COL_TRADE_DATE, COL_STOCK_CODE]].head(5).to_dict(orient="records")
            raise ValueError(f"{col} contains NaN: {bad}")

    empty_mask = df[COL_STOCK_CODE].isna() | (df[COL_STOCK_CODE].astype(str).str.strip() == "")
    if empty_mask.any():
        bad = df.loc[empty_mask, [COL_TRADE_DATE, COL_STOCK_CODE]].head(5)
        raise ValueError(f"stock_code contains empty values: {bad.to_dict(orient='records')}")


def validate_no_duplicate_dates(df: pd.DataFrame) -> None:
    """Reject duplicate trade_date rows before shift/rolling logic is applied."""
    dup_mask = df[COL_TRADE_DATE].duplicated(keep=False)
    if dup_mask.any():
        sample = df.loc[dup_mask, COL_TRADE_DATE].head(10).astype(str).tolist()
        raise ValueError(f"trade_date has duplicates: {sample}")


__all__ = [
    "normalize_columns",
    "parse_trade_date",
    "validate_no_duplicate_dates",
    "validate_required_columns",
]
