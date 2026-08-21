"""
Build an all-stock daily table from per-stock MySQL tables.

The updater stores one table per stock, for example ``000001_sz``. This tool
materializes those rows into one table keyed by ``(stock_code, trade_date)`` so
later full-market reads and cross-sectional features can scan one table.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402
from core.exceptions import DataSourceError, DataValidationError  # noqa: E402
from data.mysql_support import (  # noqa: E402
    _format_sa_error,
    _get_mysql_engine,
    _is_access_denied,
    _load_mysql_cfg_from_files,
    _mysql_credentials_match,
    _quote_sql_identifier,
)
from data.tushare_client import (  # noqa: E402
    create_tushare_client,
    redact_tushare_error,
    resolve_required_tushare_token,
)


DEFAULT_TARGET_TABLE = "stock_daily_all"
TUSHARE_STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,list_date"
_STOCK_TABLE_RE = re.compile(r"^(?P<code>\d{6})_(?P<market>sz|sh|bj)$", re.IGNORECASE)
_STOCK_CODE_RE = re.compile(r"^(?P<code>\d{6})\.(?P<market>SZ|SH|BJ)$", re.IGNORECASE)
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
_FRONT_COLUMNS = (
    "stock_code",
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "list_date",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "volume",
    "vol",
    "amount",
)
_KEY_COLUMNS = {"stock_code", "trade_date"}
_INTEGER_TYPES = ("tinyint", "smallint", "mediumint", "int", "bigint")
_TEXT_TYPES = {"tinytext", "text", "mediumtext", "longtext"}
_CANONICAL_ALIASES = {
    "volume": ("volume", "vol"),
    "vol": ("vol", "volume"),
}
_TUSHARE_STOCK_BASIC_COLUMN_TYPES = {
    "ts_code": "VARCHAR(20)",
    "symbol": "VARCHAR(20)",
    "name": "VARCHAR(100)",
    "area": "VARCHAR(100)",
    "industry": "VARCHAR(100)",
    "list_date": "DATE",
}


@dataclass(frozen=True)
class SourceColumn:
    name: str
    source_name: str
    sql_type: str


@dataclass(frozen=True)
class SourceTable:
    table_name: str
    stock_code: str
    columns: Mapping[str, SourceColumn]
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True)
class TargetColumn:
    name: str
    sql_type: str


@dataclass(frozen=True)
class BuildResult:
    source_table_count: int
    target_column_count: int
    affected_rows: int
    elapsed_sec: float


@dataclass(frozen=True)
class InsertResult:
    source_table: SourceTable
    affected_rows: int
    elapsed_sec: float


def _split_csv(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _validate_identifier(identifier: str, *, kind: str) -> str:
    raw = str(identifier or "").strip()
    if not _SQL_IDENTIFIER_RE.fullmatch(raw):
        raise DataValidationError(f"非法 SQL {kind}: {identifier!r}")
    return raw


def stock_code_from_table(table_name: str) -> str | None:
    match = _STOCK_TABLE_RE.fullmatch(str(table_name or "").strip())
    if match is None:
        return None
    return f"{match.group('code')}.{match.group('market').upper()}"


def table_name_from_stock_code(stock_code: str) -> str:
    raw = str(stock_code or "").strip()
    match = _STOCK_CODE_RE.fullmatch(raw)
    if match is None:
        raise DataValidationError(f"非法股票代码: {stock_code!r}")
    return f"{match.group('code')}_{match.group('market').lower()}"


def _parse_include_tables(raw: str | None) -> set[str] | None:
    values = _split_csv(raw)
    if not values:
        return None

    tables: set[str] = set()
    for value in values:
        if stock_code_from_table(value) is not None:
            tables.add(value.lower())
            continue
        tables.add(table_name_from_stock_code(value).lower())
    return tables


def _parse_date(value: str | None, *, field: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise DataValidationError(f"{field} 必须是 YYYYMMDD 或 YYYY-MM-DD: {value!r}")


def _parse_tushare_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _clean_tushare_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _normalize_tushare_stock_basic_frame(df: pd.DataFrame) -> dict[str, Mapping[str, object]]:
    if df is None or df.empty:
        raise DataSourceError("TuShare stock_basic 返回为空")
    if "ts_code" not in df.columns:
        raise DataValidationError("TuShare stock_basic 缺少 ts_code 列")

    work = df.copy()
    work["ts_code"] = work["ts_code"].astype(str).str.strip().str.upper()
    work = work.loc[work["ts_code"].map(lambda value: _STOCK_CODE_RE.fullmatch(value) is not None)].copy()
    if work.empty:
        raise DataValidationError("TuShare stock_basic 未返回合法 A 股 ts_code")
    work = work.drop_duplicates(subset=["ts_code"], keep="last").sort_values("ts_code", key=lambda s: s.str.casefold())

    out: dict[str, Mapping[str, object]] = {}
    for row in work.to_dict(orient="records"):
        ts_code = str(row["ts_code"]).strip().upper()
        meta: dict[str, object] = {"ts_code": ts_code}
        for col in ("symbol", "name", "area", "industry"):
            text = _clean_tushare_text(row.get(col))
            if text is not None:
                meta[col] = text
        list_date = _parse_tushare_date(row.get("list_date"))
        if list_date is not None:
            meta["list_date"] = list_date
        out[ts_code] = meta
    return out


def _load_tushare_stock_basic(*, cfg) -> dict[str, Mapping[str, object]]:
    token = resolve_required_tushare_token(cfg=cfg)
    try:
        pro = create_tushare_client(token)
        data = pro.stock_basic(
            exchange="",
            list_status="L",
            fields=TUSHARE_STOCK_BASIC_FIELDS,
        )
    except Exception as exc:
        message = redact_tushare_error(exc, token=token)
        raise DataSourceError(
            "TuShare stock_basic 获取失败: "
            f"token=set err={message}"
        ) from None
    return _normalize_tushare_stock_basic_frame(data)


def _metadata_include_tables(metadata_by_stock: Mapping[str, Mapping[str, object]]) -> set[str]:
    return {
        table_name_from_stock_code(stock_code).lower()
        for stock_code in metadata_by_stock
    }


def _attach_source_metadata(
    source_tables: Sequence[SourceTable],
    metadata_by_stock: Mapping[str, Mapping[str, object]],
) -> list[SourceTable]:
    if not metadata_by_stock:
        return list(source_tables)
    out: list[SourceTable] = []
    for source in source_tables:
        out.append(
            SourceTable(
                table_name=source.table_name,
                stock_code=source.stock_code,
                columns=source.columns,
                metadata=metadata_by_stock.get(source.stock_code),
            )
        )
    return out


def _parse_requested_columns(raw: str | None) -> tuple[str, ...] | None:
    values = _split_csv(raw)
    if not values:
        return None

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        col = _validate_identifier(value, kind="column").lower()
        if col in seen:
            continue
        seen.add(col)
        out.append(col)
    return tuple(out)


def _to_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _mapping_get_ci(row: Mapping[str, object], key: str) -> object:
    if key in row:
        return row[key]
    key_lower = key.lower()
    for row_key, value in row.items():
        if str(row_key).lower() == key_lower:
            return value
    return None


def _normalize_sql_type(row: Mapping[str, object], *, target_name: str) -> str:
    name = target_name.lower()
    if name in {"stock_code", "ts_code"}:
        return "VARCHAR(20)"
    if name in {"name", "industry", "area", "suspend_type", "suspend_timing"}:
        return "VARCHAR(100)"
    if name == "trade_date":
        return "DATE"

    data_type = str(_mapping_get_ci(row, "data_type") or "").strip().lower()
    char_len = _to_int(_mapping_get_ci(row, "character_maximum_length"), 0)
    precision = _to_int(_mapping_get_ci(row, "numeric_precision"), 20)
    scale = _to_int(_mapping_get_ci(row, "numeric_scale"), 4)

    if data_type in {"varchar", "char"}:
        return f"{data_type.upper()}({min(max(char_len, 1), 16383)})"
    if data_type in _TEXT_TYPES:
        return data_type.upper()
    if data_type in {"date", "datetime", "timestamp", "time"}:
        return data_type.upper()
    if data_type in _INTEGER_TYPES:
        return data_type.upper()
    if data_type in {"decimal", "numeric"}:
        precision = min(max(precision, 1), 65)
        scale = min(max(scale, 0), min(30, precision))
        return f"DECIMAL({precision},{scale})"
    if data_type in {"float", "double", "real"}:
        return "DOUBLE"
    return "TEXT"


def _parse_varchar_length(sql_type: str) -> int | None:
    match = re.search(r"\b(?:var)?char\((\d+)\)", str(sql_type).lower())
    return int(match.group(1)) if match else None


def _parse_decimal_type(sql_type: str) -> tuple[int, int] | None:
    match = re.search(r"\bdecimal\((\d+),(\d+)\)", str(sql_type).lower())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _integer_rank(sql_type: str) -> int | None:
    lowered = str(sql_type).lower()
    for idx, name in enumerate(_INTEGER_TYPES):
        if re.search(rf"\b{name}\b", lowered):
            return idx
    return None


def merge_sql_types(left: str, right: str) -> str:
    left_l = str(left).lower()
    right_l = str(right).lower()
    if left_l == right_l:
        return str(left).upper()

    if left_l in _TEXT_TYPES or right_l in _TEXT_TYPES:
        return "TEXT"

    left_varchar = _parse_varchar_length(left)
    right_varchar = _parse_varchar_length(right)
    if left_varchar is not None or right_varchar is not None:
        return f"VARCHAR({min(max(left_varchar or 0, right_varchar or 0, 1), 16383)})"

    left_decimal = _parse_decimal_type(left)
    right_decimal = _parse_decimal_type(right)
    left_int_rank = _integer_rank(left)
    right_int_rank = _integer_rank(right)
    if left_decimal or right_decimal:
        precision = max((left_decimal or (20, 4))[0], (right_decimal or (20, 4))[0])
        scale = max((left_decimal or (20, 4))[1], (right_decimal or (20, 4))[1])
        return f"DECIMAL({min(precision, 65)},{min(scale, 30)})"
    if left_int_rank is not None and right_int_rank is not None:
        return _INTEGER_TYPES[max(left_int_rank, right_int_rank)].upper()
    if any(kind in left_l or kind in right_l for kind in ("double", "float", "real")):
        return "DOUBLE"
    if left_l == "date" and right_l == "date":
        return "DATE"
    if left_l in {"date", "datetime", "timestamp"} or right_l in {"date", "datetime", "timestamp"}:
        return "DATETIME"
    return "TEXT"


def _front_sorted_columns(columns: Sequence[str]) -> list[str]:
    column_set = set(columns)
    front = [col for col in _FRONT_COLUMNS if col in column_set]
    rest = sorted((col for col in column_set if col not in front), key=str.casefold)
    return [*front, *rest]


def _quote_columns(columns: Sequence[str]) -> str:
    return ", ".join(_quote_sql_identifier(col, kind="column") for col in columns)


def _build_engine_for_workers(db_name: str, *, cfg, workers: int):
    if workers <= 1:
        return _get_mysql_engine(db_name, cfg=cfg)

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError
    except Exception as exc:
        raise DataSourceError(f"SQLAlchemy 未安装，无法连接 MySQL: {exc}") from exc

    pool_size = max(1, int(workers))
    max_overflow = max(0, min(pool_size, 8))

    def _new_engine(mysql_cfg):
        return create_engine(
            mysql_cfg.sqlalchemy_url(db_name),
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )

    def _probe(engine_obj) -> None:
        with engine_obj.connect() as conn:
            conn.execute(text("SELECT 1"))

    try:
        engine = _new_engine(cfg.mysql)
        _probe(engine)
        return engine
    except SQLAlchemyError as exc:
        err_msg = _format_sa_error(exc)
        if not _is_access_denied(err_msg):
            raise DataSourceError(f"创建多线程 SQLAlchemy engine 失败: db={db_name!r} err={err_msg}") from exc

        fallback_cfg = _load_mysql_cfg_from_files()
        if fallback_cfg is None or _mysql_credentials_match(cfg.mysql, fallback_cfg):
            raise DataSourceError(f"SQL 登录被拒绝: db={db_name!r} user={cfg.mysql.user!r} err={err_msg}") from exc

        try:
            engine = _new_engine(fallback_cfg)
            _probe(engine)
            return engine
        except SQLAlchemyError as exc2:
            err_msg2 = _format_sa_error(exc2)
            raise DataSourceError(
                "主凭据与回退凭据均认证失败: "
                f"primary_user={cfg.mysql.user!r} fallback_user={fallback_cfg.user!r} "
                f"db={db_name!r} err_primary={err_msg} err_fallback={err_msg2}"
            ) from exc2


def _discover_source_table_names(
    engine,
    *,
    db_name: str,
    target_table: str,
    include_tables: set[str] | None,
    limit: int,
) -> list[str]:
    from sqlalchemy import text

    query = text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = :db_name
          AND table_type = 'BASE TABLE'
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"db_name": db_name}).fetchall()

    target_lower = target_table.lower()
    names: list[str] = []
    for row in rows:
        table_name = str(row[0])
        table_lower = table_name.lower()
        if table_lower == target_lower:
            continue
        if include_tables is not None and table_lower not in include_tables:
            continue
        if stock_code_from_table(table_name) is None:
            continue
        names.append(table_name)

    names = sorted(names, key=str.casefold)
    return names[:limit] if limit > 0 else names


def _load_source_tables(engine, *, db_name: str, table_names: Sequence[str]) -> list[SourceTable]:
    if not table_names:
        return []

    from sqlalchemy import bindparam, text

    table_name_set = {name.lower() for name in table_names}
    table_name_by_lower = {name.lower(): name for name in table_names}
    query = text(
        """
        SELECT
            table_name AS table_name,
            column_name AS column_name,
            data_type AS data_type,
            character_maximum_length AS character_maximum_length,
            numeric_precision AS numeric_precision,
            numeric_scale AS numeric_scale,
            ordinal_position AS ordinal_position
        FROM information_schema.columns
        WHERE table_schema = :db_name
          AND table_name IN :table_names
        ORDER BY table_name, ordinal_position
        """
    ).bindparams(bindparam("table_names", expanding=True))

    rows_by_table: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {"db_name": db_name, "table_names": list(table_names)},
        ).mappings()
        for row in rows:
            table_lower = str(_mapping_get_ci(row, "table_name")).lower()
            if table_lower in table_name_set:
                rows_by_table[table_lower].append(row)

    out: list[SourceTable] = []
    for table_lower in sorted(table_name_set, key=str.casefold):
        table_name = table_name_by_lower[table_lower]
        stock_code = stock_code_from_table(table_name)
        if stock_code is None:
            continue

        columns: dict[str, SourceColumn] = {}
        for row in rows_by_table.get(table_lower, []):
            source_name = str(_mapping_get_ci(row, "column_name")).strip()
            if not _SQL_IDENTIFIER_RE.fullmatch(source_name):
                continue
            target_name = source_name.lower()
            if target_name in columns:
                continue
            columns[target_name] = SourceColumn(
                name=target_name,
                source_name=source_name,
                sql_type=_normalize_sql_type(row, target_name=target_name),
            )

        if "trade_date" not in columns:
            continue
        out.append(SourceTable(table_name=table_name, stock_code=stock_code, columns=columns))

    return out


def _build_target_columns(
    source_tables: Sequence[SourceTable],
    *,
    requested_columns: tuple[str, ...] | None,
) -> list[TargetColumn]:
    requested = set(requested_columns or ())
    sql_types: dict[str, str] = {
        "stock_code": "VARCHAR(20)",
        "ts_code": "VARCHAR(20)",
        "trade_date": "DATE",
    }

    for source in source_tables:
        for col_name, sql_type in _TUSHARE_STOCK_BASIC_COLUMN_TYPES.items():
            if col_name == "ts_code":
                continue
            if not source.metadata or col_name not in source.metadata:
                continue
            if requested_columns is not None and col_name not in requested:
                continue
            existing = sql_types.get(col_name)
            sql_types[col_name] = sql_type if existing is None else merge_sql_types(existing, sql_type)

        for col_name, col in source.columns.items():
            if col_name == "stock_code":
                continue
            if requested_columns is not None and col_name not in requested and col_name not in {"ts_code", "trade_date"}:
                continue
            existing = sql_types.get(col_name)
            sql_types[col_name] = col.sql_type if existing is None else merge_sql_types(existing, col.sql_type)

        volume_col = source.columns.get("volume") or source.columns.get("vol")
        if volume_col is not None and (requested_columns is None or "volume" in requested):
            existing = sql_types.get("volume")
            sql_types["volume"] = volume_col.sql_type if existing is None else merge_sql_types(existing, volume_col.sql_type)

    return [TargetColumn(name=name, sql_type=sql_types[name]) for name in _front_sorted_columns(tuple(sql_types))]


def _source_column_for_target(source_table: SourceTable, target_col: str) -> SourceColumn | None:
    source_col = source_table.columns.get(target_col)
    if source_col is not None:
        return source_col
    for alias in _CANONICAL_ALIASES.get(target_col, ()):
        source_col = source_table.columns.get(alias)
        if source_col is not None:
            return source_col
    return None


def build_create_table_sql(target_table: str, columns: Sequence[TargetColumn]) -> str:
    target_ident = _quote_sql_identifier(target_table, kind="table")
    column_defs: list[str] = []
    column_names = {col.name for col in columns}
    for col in columns:
        col_ident = _quote_sql_identifier(col.name, kind="column")
        if col.name == "stock_code":
            column_defs.append(f"{col_ident} VARCHAR(20) NOT NULL")
        elif col.name == "trade_date":
            column_defs.append(f"{col_ident} DATE NOT NULL")
        else:
            column_defs.append(f"{col_ident} {col.sql_type}")

    column_defs.append("PRIMARY KEY (`stock_code`, `trade_date`)")
    column_defs.append("KEY `idx_trade_date_stock_code` (`trade_date`, `stock_code`)")
    if "ts_code" in column_names:
        column_defs.append("KEY `idx_ts_code_trade_date` (`ts_code`, `trade_date`)")

    body = ",\n    ".join(column_defs)
    return f"CREATE TABLE IF NOT EXISTS {target_ident} (\n    {body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"


def _load_existing_target_columns(engine, *, db_name: str, target_table: str) -> set[str]:
    from sqlalchemy import text

    query = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :db_name
          AND table_name = :target_table
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"db_name": db_name, "target_table": target_table}).fetchall()
    return {str(row[0]).lower() for row in rows}


def _target_has_expected_primary_key(engine, *, db_name: str, target_table: str) -> bool:
    from sqlalchemy import text

    query = text(
        """
        SELECT column_name
        FROM information_schema.statistics
        WHERE table_schema = :db_name
          AND table_name = :target_table
          AND index_name = 'PRIMARY'
        ORDER BY seq_in_index
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"db_name": db_name, "target_table": target_table}).fetchall()
    return [str(row[0]).lower() for row in rows] == ["stock_code", "trade_date"]


def _ensure_target_table(
    engine,
    *,
    db_name: str,
    target_table: str,
    columns: Sequence[TargetColumn],
    replace: bool,
) -> None:
    from sqlalchemy import text

    target_ident = _quote_sql_identifier(target_table, kind="table")
    if replace:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {target_ident}"))

    with engine.begin() as conn:
        conn.execute(text(build_create_table_sql(target_table, columns)))

    existing = _load_existing_target_columns(engine, db_name=db_name, target_table=target_table)
    required = {"stock_code", "trade_date"}
    if not required.issubset(existing):
        missing = sorted(required - existing, key=str.casefold)
        raise DataValidationError(f"目标表 {target_table!r} 缺少主键列 {missing}；请使用 --replace 重建。")
    if not _target_has_expected_primary_key(engine, db_name=db_name, target_table=target_table):
        raise DataValidationError(f"目标表 {target_table!r} 的主键不是 (stock_code, trade_date)；请使用 --replace 重建。")

    missing_columns = [col for col in columns if col.name not in existing]
    if not missing_columns:
        return
    with engine.begin() as conn:
        for col in missing_columns:
            col_ident = _quote_sql_identifier(col.name, kind="column")
            conn.execute(text(f"ALTER TABLE {target_ident} ADD COLUMN {col_ident} {col.sql_type}"))


def _build_where_clause(start_date: date | None, end_date: date | None) -> tuple[str, dict[str, object]]:
    clauses = ["`trade_date` IS NOT NULL"]
    params: dict[str, object] = {}
    if start_date is not None:
        clauses.append("`trade_date` >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        clauses.append("`trade_date` <= :end_date")
        params["end_date"] = end_date
    return " WHERE " + " AND ".join(clauses), params


def build_insert_select_sql(
    *,
    source_table: SourceTable,
    target_table: str,
    target_columns: Sequence[TargetColumn],
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, dict[str, object]]:
    target_ident = _quote_sql_identifier(target_table, kind="table")
    source_ident = _quote_sql_identifier(source_table.table_name, kind="table")
    target_col_names = [col.name for col in target_columns]
    insert_cols = _quote_columns(target_col_names)

    select_exprs: list[str] = []
    metadata = dict(source_table.metadata or {})
    metadata["ts_code"] = metadata.get("ts_code") or source_table.stock_code
    meta_params: dict[str, object] = {}
    for col_name in target_col_names:
        col_ident = _quote_sql_identifier(col_name, kind="column")
        if col_name == "stock_code":
            select_exprs.append(f":stock_code AS {col_ident}")
            continue
        if col_name == "ts_code":
            meta_params["meta_ts_code"] = metadata.get("ts_code") or source_table.stock_code
            select_exprs.append(f":meta_ts_code AS {col_ident}")
            continue
        if col_name in metadata:
            param_name = f"meta_{col_name}"
            meta_params[param_name] = metadata[col_name]
            select_exprs.append(f":{param_name} AS {col_ident}")
            continue

        source_col = _source_column_for_target(source_table, col_name)
        if source_col is None:
            select_exprs.append(f"NULL AS {col_ident}")
        else:
            select_exprs.append(f"{_quote_sql_identifier(source_col.source_name, kind='column')} AS {col_ident}")

    update_cols = [name for name in target_col_names if name not in _KEY_COLUMNS]
    update_sql = ", ".join(
        f"{_quote_sql_identifier(name, kind='column')} = VALUES({_quote_sql_identifier(name, kind='column')})"
        for name in update_cols
    ) or "`stock_code` = `stock_code`"
    where_sql, params = _build_where_clause(start_date, end_date)
    params["stock_code"] = source_table.stock_code
    params.update(meta_params)
    select_sql = ",\n       ".join(select_exprs)
    sql = (
        f"INSERT INTO {target_ident} ({insert_cols})\n"
        f"SELECT {select_sql}\n"
        f"FROM {source_ident}"
        f"{where_sql}\n"
        f"ON DUPLICATE KEY UPDATE {update_sql}"
    )
    return sql, params


def _is_retryable_mysql_write_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "deadlock" in text
        or "lock wait timeout" in text
        or "try restarting transaction" in text
        or "1213" in text
        or "1205" in text
    )


def _insert_one_source_table(
    *,
    engine,
    source_table: SourceTable,
    target_table: str,
    target_columns: Sequence[TargetColumn],
    start_date: date | None,
    end_date: date | None,
    retries: int,
    retry_delay: float,
) -> InsertResult:
    from sqlalchemy import text

    sql, params = build_insert_select_sql(
        source_table=source_table,
        target_table=target_table,
        target_columns=target_columns,
        start_date=start_date,
        end_date=end_date,
    )
    started = time.perf_counter()
    attempts = max(1, int(retries) + 1)
    for attempt in range(1, attempts + 1):
        try:
            with engine.begin() as conn:
                result = conn.execute(text(sql), params)
            return InsertResult(
                source_table=source_table,
                affected_rows=int(result.rowcount or 0),
                elapsed_sec=time.perf_counter() - started,
            )
        except Exception as exc:
            if attempt >= attempts or not _is_retryable_mysql_write_error(exc):
                raise
            time.sleep(max(0.0, float(retry_delay)) * attempt)

    raise RuntimeError(f"{source_table.table_name}: unexpected insert retry state")


def _run_insert_serial(
    *,
    engine,
    source_tables: Sequence[SourceTable],
    target_table: str,
    target_columns: Sequence[TargetColumn],
    start_date: date | None,
    end_date: date | None,
    retries: int,
    retry_delay: float,
    progress_every: int,
    quiet: bool,
) -> int:
    affected_total = 0
    for idx, source in enumerate(source_tables, start=1):
        result = _insert_one_source_table(
            engine=engine,
            source_table=source,
            target_table=target_table,
            target_columns=target_columns,
            start_date=start_date,
            end_date=end_date,
            retries=retries,
            retry_delay=retry_delay,
        )
        affected_total += result.affected_rows
        if not quiet and (idx == 1 or idx == len(source_tables) or idx % progress_every == 0):
            print(
                f"[{idx}/{len(source_tables)}] {source.table_name} -> {target_table} "
                f"affected={result.affected_rows} elapsed={result.elapsed_sec:.2f}s"
            )
    return affected_total


def _run_insert_threaded(
    *,
    engine,
    source_tables: Sequence[SourceTable],
    target_table: str,
    target_columns: Sequence[TargetColumn],
    start_date: date | None,
    end_date: date | None,
    workers: int,
    retries: int,
    retry_delay: float,
    progress_every: int,
    quiet: bool,
) -> int:
    affected_total = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _insert_one_source_table,
                engine=engine,
                source_table=source,
                target_table=target_table,
                target_columns=target_columns,
                start_date=start_date,
                end_date=end_date,
                retries=retries,
                retry_delay=retry_delay,
            ): source
            for source in source_tables
        }
        for future in as_completed(futures):
            source = futures[future]
            result = future.result()
            completed += 1
            affected_total += result.affected_rows
            if not quiet and (
                completed == 1
                or completed == len(source_tables)
                or completed % progress_every == 0
            ):
                print(
                    f"[{completed}/{len(source_tables)}] {source.table_name} -> {target_table} "
                    f"affected={result.affected_rows} elapsed={result.elapsed_sec:.2f}s"
                )
    return affected_total


def _read_target_counts(engine, *, target_table: str) -> Mapping[str, object]:
    from sqlalchemy import text

    target_ident = _quote_sql_identifier(target_table, kind="table")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT COUNT(*) AS row_count,
                       COUNT(DISTINCT stock_code) AS stock_count,
                       MIN(trade_date) AS min_trade_date,
                       MAX(trade_date) AS max_trade_date
                FROM {target_ident}
                """
            )
        ).mappings().one()
    return dict(row)


def build_summary_table(args: argparse.Namespace) -> BuildResult:
    cfg = get_config()
    db_name = str(args.db or cfg.mysql.stock_db).strip()
    target_table = _validate_identifier(args.target_table, kind="table")
    start_date = _parse_date(args.start_date, field="--start-date")
    end_date = _parse_date(args.end_date, field="--end-date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise DataValidationError("--start-date 不能晚于 --end-date")

    include_tables = _parse_include_tables(args.include)
    requested_columns = _parse_requested_columns(args.columns)
    workers = max(1, int(args.workers or 1))
    engine = _build_engine_for_workers(db_name, cfg=cfg, workers=workers)
    stock_metadata: dict[str, Mapping[str, object]] = {}
    stock_list_source = str(getattr(args, "stock_list_source", "tushare") or "tushare").strip().lower()
    if stock_list_source == "tushare":
        stock_metadata = _load_tushare_stock_basic(cfg=cfg)
        online_tables = _metadata_include_tables(stock_metadata)
        include_tables = online_tables if include_tables is None else include_tables & online_tables
        if not include_tables:
            raise DataValidationError("TuShare 在线股票列表与 --include 没有交集，无法构建总表")

    table_names = _discover_source_table_names(
        engine,
        db_name=db_name,
        target_table=target_table,
        include_tables=include_tables,
        limit=max(0, int(args.limit or 0)),
    )
    source_tables = _load_source_tables(engine, db_name=db_name, table_names=table_names)
    if not source_tables:
        raise DataValidationError(f"未发现可聚合的单股表: db={db_name!r}")
    source_tables = _attach_source_metadata(source_tables, stock_metadata)

    target_columns = _build_target_columns(source_tables, requested_columns=requested_columns)
    started = time.perf_counter()

    print(
        "summary_table_plan "
        f"db={db_name} target={target_table} sources={len(source_tables)} "
        f"columns={len(target_columns)} workers={workers} dry_run={bool(args.dry_run)} "
        f"replace={bool(args.replace)} "
        f"stock_list_source={stock_list_source} stock_list_count={len(stock_metadata) or 'mysql_tables'}"
    )
    print(
        "summary_table_sources_head "
        + ", ".join(f"{src.table_name}:{src.stock_code}" for src in source_tables[:8])
    )
    if args.dry_run:
        print("DRY RUN: 未写入数据库；加 --execute 才会创建/更新目标表。")
        return BuildResult(len(source_tables), len(target_columns), 0, time.perf_counter() - started)

    _ensure_target_table(
        engine,
        db_name=db_name,
        target_table=target_table,
        columns=target_columns,
        replace=bool(args.replace),
    )

    progress_every = max(1, int(args.progress_every or 1))
    insert_kwargs = {
        "engine": engine,
        "source_tables": source_tables,
        "target_table": target_table,
        "target_columns": target_columns,
        "start_date": start_date,
        "end_date": end_date,
        "retries": max(0, int(args.retries or 0)),
        "retry_delay": max(0.0, float(args.retry_delay or 0.0)),
        "progress_every": progress_every,
        "quiet": bool(args.quiet),
    }
    if workers <= 1:
        affected_total = _run_insert_serial(**insert_kwargs)
    else:
        affected_total = _run_insert_threaded(
            **insert_kwargs,
            workers=workers,
        )

    if args.verify:
        counts = _read_target_counts(engine, target_table=target_table)
        print(
            "summary_table_verify "
            f"rows={int(counts.get('row_count') or 0)} "
            f"stocks={int(counts.get('stock_count') or 0)} "
            f"min_trade_date={counts.get('min_trade_date')} "
            f"max_trade_date={counts.get('max_trade_date')}"
        )

    elapsed = time.perf_counter() - started
    print(
        "summary_table_done "
        f"sources={len(source_tables)} columns={len(target_columns)} "
        f"affected={affected_total} elapsed={elapsed:.2f}s"
    )
    return BuildResult(len(source_tables), len(target_columns), affected_total, elapsed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐个读取 MySQL 单股表，聚合为 stock_code/trade_date 主键的全市场总表。",
    )
    parser.add_argument("--db", default="", help="源库/目标库，默认读取 config.ini 的 mysql.stock_db。")
    parser.add_argument("--target-table", default=DEFAULT_TARGET_TABLE, help=f"目标总表名，默认 {DEFAULT_TARGET_TABLE}。")
    parser.add_argument(
        "--stock-list-source",
        choices=["tushare", "mysql_tables"],
        default="tushare",
        help=(
            "股票清单来源：tushare=每次在线获取当前正常上市股票列表并过滤单股表；"
            "mysql_tables=旧行为，直接扫描库内单股表。默认 tushare。"
        ),
    )
    parser.add_argument("--include", default="", help="只处理这些股票代码或表名，逗号分隔，例如 000001.SZ,600000.SH。")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少张单股表；0 表示不限。")
    parser.add_argument("--start-date", default="", help="可选起始交易日，YYYYMMDD 或 YYYY-MM-DD。")
    parser.add_argument("--end-date", default="", help="可选结束交易日，YYYYMMDD 或 YYYY-MM-DD。")
    parser.add_argument("--columns", default="", help="可选字段白名单，逗号分隔；stock_code/ts_code/trade_date 会自动保留。")
    parser.add_argument("--replace", dest="replace", action="store_true", help="写入前先 DROP 目标表并重建（默认）。")
    parser.add_argument("--no-replace", dest="replace", action="store_false", help="不重建目标表，按主键增量 upsert。")
    parser.add_argument("--execute", dest="dry_run", action="store_false", help="实际创建/更新目标表。")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="只打印计划，不写数据库。")
    parser.set_defaults(dry_run=True, replace=True)
    parser.add_argument("--verify", action="store_true", help="写入后统计目标表行数、股票数和日期范围。")
    parser.add_argument("--workers", type=int, default=1, help="并发写入线程数；默认 1。建议从 4 开始试。")
    parser.add_argument("--retries", type=int, default=2, help="遇到 MySQL 死锁/锁等待超时时的重试次数；默认 2。")
    parser.add_argument("--retry-delay", type=float, default=0.5, help="重试退避基准秒数；默认 0.5。")
    parser.add_argument("--progress-every", type=int, default=100, help="每处理多少张表输出一次进度。")
    parser.add_argument("--quiet", action="store_true", help="减少逐表进度输出。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_summary_table(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
