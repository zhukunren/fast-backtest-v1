"""
Local CSV data source helpers.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from core.exceptions import DataSourceError, DataValidationError, PriceDataError

from .trade_calendar import _parse_date_yyyymmdd_or_iso

_YYYYMMDD_RE = re.compile(r"\d{8}")


def _get_local_data_dir() -> Path | None:
    """
    Resolve local sample/data directory from environment.

    This helper intentionally stays local to the module instead of importing a
    shared runtime resolver from a sibling data module. The tiny duplication
    keeps local-price reads decoupled from trade-calendar imports and avoids
    adding another cross-module dependency on the hot path.
    """
    env_val = os.environ.get("LOCAL_DATA_DIR") or os.environ.get("SAMPLE_DATA_DIR")
    if env_val:
        return Path(env_val).resolve()
    return None


def _local_price_path_candidates(local_dir: Path, symbol_code: str) -> list[Path]:
    symbol_alias = symbol_code.replace(".", "_")
    return [
        local_dir / "prices" / f"{symbol_code}.csv",
        local_dir / f"{symbol_code}.csv",
        local_dir / "prices" / f"{symbol_alias}.csv",
        local_dir / f"{symbol_alias}.csv",
    ]


def _resolve_local_price_path(local_dir: Path, symbol_code: str) -> Path | None:
    """
    Resolve the first supported local price file for `symbol_code`.

    Preference stays deterministic: `prices/<symbol>.csv` first, then the local
    root, then underscore-normalized aliases for historical sample layouts.
    """
    for path in _local_price_path_candidates(local_dir, symbol_code):
        if path.exists():
            return path
    return None


def _format_missing_local_price_message(local_dir: Path, symbol_code: str) -> str:
    candidates = [str(path) for path in _local_price_path_candidates(local_dir, symbol_code)]
    return f"本地行情文件不存在: {symbol_code} dir={local_dir} candidates={'; '.join(candidates)}"


def _clean_local_requested_columns(columns: Optional[list[str]]) -> list[str]:
    raw_cols: list[str] = []
    for raw_col in columns or []:
        if raw_col is None:
            continue
        col = str(raw_col).strip()
        if col:
            raw_cols.append(col)
    return raw_cols


def _dedupe_local_requested_columns(raw_cols: list[str]) -> list[str]:
    seen = set()
    wanted: list[str] = []
    for col in raw_cols:
        folded = str(col).lower()
        if folded in seen:
            continue
        seen.add(folded)
        wanted.append(col)
    return wanted


def _normalize_local_requested_columns(columns: Optional[list[str]]) -> list[str]:
    if columns is None:
        return []

    raw_cols: list[str] = []
    for col in _clean_local_requested_columns(columns):
        lower_col = col.lower()
        if lower_col in {"stock_code", "ts_code"}:
            col = "stock_code"
        elif lower_col in {"volume", "vol"}:
            col = "volume"
        raw_cols.append(col)

    if not any(str(col).lower() == "trade_date" for col in raw_cols):
        raw_cols.insert(0, "trade_date")
    return _dedupe_local_requested_columns(raw_cols)


def _build_local_read_usecols_folded(wanted_cols: list[str]) -> set[str]:
    selected_folded = {str(col).casefold() for col in wanted_cols}
    if "stock_code" in selected_folded:
        selected_folded.add("ts_code")
    if "volume" in selected_folded:
        selected_folded.add("vol")
    return selected_folded


def _promote_local_alias_columns(df: pd.DataFrame, *, symbol_code: str) -> None:
    if "stock_code" not in df.columns:
        if "ts_code" in df.columns:
            df["stock_code"] = df["ts_code"]
        else:
            df["stock_code"] = symbol_code
    if "volume" not in df.columns and "vol" in df.columns:
        df["volume"] = df["vol"]


def _normalize_local_trade_date_order(df: pd.DataFrame) -> pd.DataFrame:
    if not df["trade_date"].is_monotonic_increasing:
        return df.sort_values(by="trade_date", kind="mergesort").reset_index(drop=True)
    if not isinstance(df.index, pd.RangeIndex):
        return df.reset_index(drop=True)
    return df


def _filter_local_trade_date_window(
    df: pd.DataFrame,
    trade_dates: pd.Series,
    *,
    start_ts: pd.Timestamp | None,
    end_ts: pd.Timestamp | None,
) -> tuple[pd.DataFrame, pd.Series]:
    if start_ts is not None:
        mask = trade_dates.ge(start_ts)
        if not bool(mask.all()):
            df = df.loc[mask].copy()
            trade_dates = trade_dates.loc[mask]
    if end_ts is not None:
        mask = trade_dates.le(end_ts)
        if not bool(mask.all()):
            df = df.loc[mask].copy()
            trade_dates = trade_dates.loc[mask]
    return df, trade_dates


def _read_local_daily(
    local_dir: Path,
    symbol_code: str,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    columns: Optional[list[str]],
) -> pd.DataFrame:
    path = _resolve_local_price_path(local_dir, symbol_code)
    if path is None:
        raise PriceDataError(_format_missing_local_price_message(local_dir, symbol_code))

    wanted_cols = _normalize_local_requested_columns(columns)
    read_kwargs: dict[str, object] = {"memory_map": True}
    if wanted_cols:
        selected_folded = _build_local_read_usecols_folded(wanted_cols)
        read_kwargs["usecols"] = lambda col: str(col).casefold() in selected_folded

    df = pd.read_csv(path, **read_kwargs)
    if df.empty:
        raise DataSourceError(f"本地行情文件为空: {path}")

    df.columns = df.columns.astype(str)
    if "trade_date" not in df.columns:
        raise DataValidationError(f"本地行情文件缺少 trade_date 列: {path}")

    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)

    td_raw = df["trade_date"].astype(str).str.strip()
    digits_only = bool(td_raw.str.fullmatch(_YYYYMMDD_RE).all())
    if digits_only:
        mask = pd.Series(True, index=df.index)
        if start_ts is not None:
            mask &= td_raw.ge(start_ts.strftime("%Y%m%d"))
        if end_ts is not None:
            mask &= td_raw.le(end_ts.strftime("%Y%m%d"))
        if not bool(mask.all()):
            df = df.loc[mask].copy()
            td_raw = td_raw.loc[mask]
        trade_dates = pd.to_datetime(td_raw, format="%Y%m%d", errors="raise")
        df, trade_dates = _filter_local_trade_date_window(
            df,
            trade_dates,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    else:
        trade_dates = pd.to_datetime(td_raw, errors="raise")
        df, trade_dates = _filter_local_trade_date_window(
            df,
            trade_dates,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    if trade_dates.isna().any():
        bad = df.loc[trade_dates.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"本地行情 trade_date 存在 NaT: {bad}")
    df["trade_date"] = trade_dates

    _promote_local_alias_columns(df, symbol_code=symbol_code)

    if wanted_cols:
        missing = [c for c in wanted_cols if c not in df.columns]
        if missing:
            raise DataValidationError(f"本地行情缺少列: {missing} file={path}")
        df = df.loc[:, wanted_cols].copy()

    return _normalize_local_trade_date_order(df)


def _probe_local_ohlcv_availability(
    symbol_codes: tuple[str, ...],
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> dict[str, bool]:
    local_dir = _get_local_data_dir()
    if local_dir is None:
        return {symbol: False for symbol in symbol_codes}
    out: dict[str, bool] = {}
    for symbol in symbol_codes:
        try:
            frame = _read_local_daily(
                local_dir,
                symbol,
                start_date=start_date,
                end_date=end_date,
                columns=["trade_date"],
            )
        except (FileNotFoundError, PriceDataError):
            out[symbol] = False
            continue
        out[symbol] = not frame.empty
    return out


def _coerce_trade_date_series(td: pd.Series) -> pd.Series:
    """
    Fast trade_date parsing:
    - datetime64 series: keep as-is
    - first try YYYYMMDD, then generic parse fallback
    """
    if pd.api.types.is_datetime64_any_dtype(td):
        return td
    try:
        return pd.to_datetime(td, format="%Y%m%d", errors="raise")
    except Exception:
        return pd.to_datetime(td, errors="raise")


__all__ = [
    "_coerce_trade_date_series",
    "_format_missing_local_price_message",
    "_get_local_data_dir",
    "_probe_local_ohlcv_availability",
    "_read_local_daily",
]
