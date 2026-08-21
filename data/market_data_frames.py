"""Frame normalization and SQL query helpers for market-data readers."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from core.exceptions import DataValidationError

from .incomplete_ohlcv import (
    DEFAULT_INCOMPLETE_OHLCV_POLICY,
    apply_incomplete_ohlcv_policy,
    attach_incomplete_ohlcv_diagnostic,
)
from .local_source import _coerce_trade_date_series
from .mysql_support import _quote_sql_identifier
from .trade_calendar import _parse_date_yyyymmdd_or_iso

def _normalize_ths_index_code_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ths_index_code" in df.columns:
        return df
    if "ts_code" in df.columns:
        return df.rename(columns={"ts_code": "ths_index_code"})
    if "stock_code" in df.columns:
        return df.rename(columns={"stock_code": "ths_index_code"})
    return df


def _thsindex_member_table_name(ths_index_code: str) -> str:
    return str(ths_index_code or "").strip().lower().replace(".", "_")


def _ths_index_daily_table_candidates(ths_index_code: str) -> list[str]:
    primary_name = str(ths_index_code)
    candidates = [primary_name]
    alt_name = primary_name.replace(".", "_")
    if alt_name != primary_name:
        candidates.append(alt_name)
    return candidates


def _dedupe_requested_columns(raw_cols: list[str]) -> list[str]:
    seen = set()
    wanted_cols: list[str] = []
    for c in raw_cols:
        cl = str(c).lower()
        if cl in seen:
            continue
        seen.add(cl)
        wanted_cols.append(c)
    return wanted_cols


def _ensure_trade_date_requested(raw_cols: list[str]) -> list[str]:
    if any(str(c).lower() == "trade_date" for c in raw_cols):
        return raw_cols
    return ["trade_date", *raw_cols]


_DEFAULT_OHLCV_BASE_COLS = ("trade_date", "stock_code", "open", "high", "low", "close")


def _clean_requested_columns(columns: Optional[list[str]]) -> list[str]:
    raw_cols: list[str] = []
    for c in columns or []:
        if c is None:
            continue
        cs = str(c).strip()
        if cs:
            raw_cols.append(cs)
    return raw_cols


def _normalize_ohlcv_output_column_name(column: str) -> str:
    folded = str(column).strip().lower()
    if folded in {"ts_code", "stock_code"}:
        return "stock_code"
    if folded in {"vol", "volume"}:
        return "volume"
    return str(column).strip()


def _resolve_ohlcv_base_columns(cfg) -> list[str]:
    base_cols = list(_DEFAULT_OHLCV_BASE_COLS)
    raw_fields = tuple(getattr(getattr(cfg, "mysql", None), "daily_fields", ()) or ())
    for raw_col in _clean_requested_columns(list(raw_fields)):
        col = _normalize_ohlcv_output_column_name(raw_col)
        if col:
            base_cols.append(col)
    return _dedupe_requested_columns(base_cols)


def _mysql_daily_db_name(cfg, symbol_type: str) -> str:
    value = str(symbol_type or "stock").strip().lower()
    mysql_cfg = getattr(cfg, "mysql", None)
    if value == "stock":
        return str(getattr(mysql_cfg, "stock_db", "stock_data"))
    if value == "index":
        return str(getattr(mysql_cfg, "index_db", "index_data"))
    if value == "etf":
        return str(getattr(mysql_cfg, "etf_db", "etf_data"))
    raise DataValidationError(f"symbol_type must be stock/index/etf, got {symbol_type!r}")


def _normalize_daily_requested_columns(columns: Optional[list[str]]) -> list[str]:
    if columns is None:
        return []

    raw_cols: list[str] = []
    for cs in _clean_requested_columns(columns):
        if cs.lower() == "stock_code":
            cs = "ts_code"
        raw_cols.append(cs)

    return _dedupe_requested_columns(_ensure_trade_date_requested(raw_cols))


def _normalize_ths_index_requested_columns(
    columns: Optional[list[str]],
    *,
    code_column: str,
) -> list[str]:
    if columns is None:
        return []

    raw_cols: list[str] = []
    for cs in _clean_requested_columns(columns):
        cl = cs.lower()
        if cl in {"index_code", "ths_index_code", "ts_code", "stock_code"}:
            cs = code_column
        elif cl in {"volume", "vol"}:
            cs = "volume"
        raw_cols.append(cs)

    return _dedupe_requested_columns(_ensure_trade_date_requested(raw_cols))


def _build_ths_index_mysql_column_candidates(
    columns: Optional[list[str]],
) -> list[list[str] | None]:
    if columns is None:
        return [None]

    raw_cols = _normalize_ths_index_requested_columns(columns, code_column="ts_code")
    candidates: list[list[str]] = [raw_cols, *_build_ths_index_volume_fallback_candidates(raw_cols)]

    seen_candidates: set[tuple[str, ...]] = set()
    out: list[list[str] | None] = []
    for cols in candidates:
        deduped_cols = _dedupe_requested_columns(cols)
        key = tuple(str(c).lower() for c in deduped_cols)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        out.append(deduped_cols)
    return out


def _build_ths_index_volume_fallback_candidates(raw_cols: list[str]) -> list[list[str]]:
    has_volume = any(str(c).lower() == "volume" for c in raw_cols)
    has_vol = any(str(c).lower() == "vol" for c in raw_cols)

    candidates: list[list[str]] = []
    if has_volume and not has_vol:
        candidates.append(
            ["vol" if str(c).lower() == "volume" else c for c in raw_cols]
        )
    if has_volume:
        candidates.append([c for c in raw_cols if str(c).lower() != "volume"])
    if has_vol:
        candidates.append([c for c in raw_cols if str(c).lower() != "vol"])
    return candidates


def _select_ths_index_columns(df: pd.DataFrame, columns: Optional[list[str]]) -> pd.DataFrame:
    if columns is None:
        return df

    wanted = _normalize_ths_index_requested_columns(columns, code_column="ths_index_code")
    missing = [c for c in wanted if c not in df.columns]
    if missing:
        raise DataValidationError(f"行情缺少列: {missing}")

    return df.loc[:, wanted].copy()


def _finalize_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    if "ts_code" in df.columns:
        df.rename(columns={"ts_code": "stock_code"}, inplace=True)
    return _promote_volume_alias_column(df)


def _finalize_ths_index_daily_frame(
    df: pd.DataFrame,
    columns: Optional[list[str]],
) -> pd.DataFrame:
    df = _normalize_ths_index_code_column(df)
    df = _promote_volume_alias_column(df)
    return _select_ths_index_columns(df, columns)


def _build_ohlcv_keep_columns(df: pd.DataFrame, base_cols: list[str]) -> list[str]:
    keep_cols = [c for c in base_cols if c in df.columns]
    for name in ("stock_name", "volume", "vol"):
        if name in df.columns and name not in keep_cols:
            keep_cols.append(name)
    return keep_cols


def _require_ohlcv_base_columns(df: pd.DataFrame, base_cols: list[str]) -> None:
    missing = []
    for col in base_cols:
        if col in df.columns:
            continue
        if col == "volume" and "vol" in df.columns:
            continue
        missing.append(col)
    if missing:
        raise DataValidationError(f"行情缺少配置列: {missing}")


def _finalize_ohlcv_frame(
    df: pd.DataFrame,
    base_cols: list[str],
    *,
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> pd.DataFrame:
    df = _promote_volume_alias_column(df)
    _require_ohlcv_base_columns(df, base_cols)
    cleaned, diagnostic = apply_incomplete_ohlcv_policy(
        df,
        policy=incomplete_ohlcv_policy,
        source="data.market_data_frames",
    )
    out = cleaned.loc[:, _build_ohlcv_keep_columns(cleaned, base_cols)]
    return attach_incomplete_ohlcv_diagnostic(out, diagnostic)


def _promote_volume_alias_column(df: pd.DataFrame) -> pd.DataFrame:
    if "volume" not in df.columns and "vol" in df.columns:
        df["volume"] = df["vol"]
    return df


def _normalize_mysql_trade_date_frame(df: pd.DataFrame) -> pd.DataFrame:
    trade_date = _coerce_trade_date_series(df["trade_date"])
    df["trade_date"] = trade_date
    if trade_date.isna().any():
        bad = df.loc[trade_date.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"MySQL 返回的 trade_date 存在 NaT: 示例={bad}")

    if not trade_date.is_monotonic_increasing:
        df.sort_values(by="trade_date", kind="mergesort", inplace=True)
        df.reset_index(drop=True, inplace=True)
    elif not isinstance(df.index, pd.RangeIndex):
        df.reset_index(drop=True, inplace=True)
    return df


def _build_trade_date_where_clause(
    start_ts: pd.Timestamp | None,
    end_ts: pd.Timestamp | None,
) -> tuple[list[str], dict[str, str]]:
    where: list[str] = []
    params: dict[str, str] = {}
    if start_ts is not None:
        where.append("`trade_date` >= :start_date")
        params["start_date"] = start_ts.strftime("%Y%m%d")
    if end_ts is not None:
        where.append("`trade_date` < :end_excl")
        params["end_excl"] = (end_ts + pd.Timedelta(days=1)).strftime("%Y%m%d")
    return where, params


def _build_trade_date_query_window(
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[list[str], dict[str, str]]:
    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    return _build_trade_date_where_clause(start_ts, end_ts)


def _build_mysql_select_query(
    *,
    table_ident: str,
    cols_sql: str,
    where: list[str],
) -> str:
    query = f"SELECT {cols_sql} FROM {table_ident}"
    if where:
        query += " WHERE " + " AND ".join(where)
    return query


def _resolve_mysql_select_columns(
    columns: Optional[list[str]],
) -> tuple[list[str] | None, str]:
    if columns is None:
        return None, "*"
    wanted_cols = _dedupe_requested_columns(columns)
    return wanted_cols, _quoted_column_list_sql(wanted_cols)


def _quoted_column_list_sql(wanted_cols: list[str]) -> str:
    return ", ".join(_quote_sql_identifier(c, kind="column") for c in wanted_cols)


def _normalize_symbol_table_name(symbol_code: str) -> str:
    return str(symbol_code or "").strip().replace(".", "_")


def _build_daily_mysql_query_context(
    symbol_code: str,
    *,
    columns: Optional[list[str]],
    where: list[str],
) -> tuple[str, list[str] | None, str]:
    table_name = _normalize_symbol_table_name(symbol_code)
    table_ident = _quote_sql_identifier(table_name, kind="table")

    wanted_cols: list[str] | None = None
    cols_sql = "*"
    if columns is not None:
        wanted_cols, cols_sql = _resolve_mysql_select_columns(
            _normalize_daily_requested_columns(columns)
        )

    query = _build_mysql_select_query(
        table_ident=table_ident,
        cols_sql=cols_sql,
        where=where,
    )
    return table_name, wanted_cols, query


def _build_ohlcv_mysql_probe_columns(base_cols: list[str]) -> list[list[str]]:
    candidates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for name_col in ["stock_name", None]:
        for vol_col in ("volume", "vol"):
            req_cols: list[str] = []
            volume_requested = False
            for base_col in base_cols:
                if str(base_col).lower() in {"volume", "vol"}:
                    if not volume_requested:
                        req_cols.append(vol_col)
                        volume_requested = True
                    continue
                req_cols.append(base_col)
            if name_col and not any(str(c).lower() == "stock_name" for c in req_cols):
                req_cols.append(name_col)
            if not volume_requested:
                req_cols.append(vol_col)
            deduped_cols = _dedupe_requested_columns(req_cols)
            key = tuple(str(c).lower() for c in deduped_cols)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(deduped_cols)
    return candidates


def _build_mysql_coverage_query_context(
    symbol_code: str,
    *,
    where: list[str],
) -> tuple[str, str]:
    table_name = _normalize_symbol_table_name(symbol_code)
    table_ident = _quote_sql_identifier(table_name, kind="table")
    query = _build_mysql_select_query(
        table_ident=table_ident,
        cols_sql="COUNT(*) AS row_count",
        where=where,
    )
    return table_name, query
