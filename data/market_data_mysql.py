"""Private MySQL execution helpers for the market-data reader facade."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import NoReturn

import pandas as pd

from core.exceptions import DataSourceError, DataValidationError, PriceDataError

from .market_data_frames import (
    _build_daily_mysql_query_context,
    _build_mysql_coverage_query_context,
    _build_mysql_select_query,
    _build_ohlcv_mysql_probe_columns,
    _build_ths_index_mysql_column_candidates,
    _build_trade_date_query_window,
    _finalize_daily_frame,
    _finalize_ths_index_daily_frame,
    _normalize_mysql_trade_date_frame,
    _resolve_mysql_select_columns,
    _ths_index_daily_table_candidates,
    _thsindex_member_table_name,
)
from .mysql_support import (
    TableNotFoundError,
    _format_sa_error,
    _get_mysql_engine,
    _quote_sql_identifier,
)


ConfigLoader = Callable[[], object]


@dataclass(frozen=True, slots=True)
class _MysqlReadContext:
    db_name: str
    code_label: str
    code_value: str
    start_date: str | None
    end_date: str | None


def _is_mysql_table_missing_error(err_msg: str) -> bool:
    return "doesn't exist" in err_msg or "Unknown table" in err_msg


def _is_mysql_unknown_column_error(err_msg: str) -> bool:
    return "Unknown column" in err_msg or "unknown column" in err_msg


def _is_missing_column_validation_error(exc: Exception) -> bool:
    msg = str(exc)
    return ("查询列不存在" in msg) or ("缺少列" in msg)


def _missing_mysql_engine_message(context: _MysqlReadContext) -> str:
    return (
        f"MySQL engine 为空: db={context.db_name!r} "
        f"{context.code_label}={context.code_value!r}"
    )


def _require_mysql_engine(
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
    engine_loader: Callable[..., object | None] = _get_mysql_engine,
):
    engine = (
        engine_loader(context.db_name, cfg=cfg)
        if config_loader is None
        else engine_loader(context.db_name, cfg=cfg, config_loader=config_loader)
    )
    if engine is None:
        raise DataSourceError(_missing_mysql_engine_message(context))
    return engine


def _build_mysql_read_runtime(
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
    require_engine: Callable[..., object] = _require_mysql_engine,
    window_builder: Callable[..., tuple[list[str], dict[str, str]]] = _build_trade_date_query_window,
):
    engine = (
        require_engine(context, cfg=cfg)
        if config_loader is None
        else require_engine(context, cfg=cfg, config_loader=config_loader)
    )
    where, params = window_builder(context.start_date, context.end_date)
    return engine, where, params


def _empty_mysql_result_message(
    context: _MysqlReadContext,
    *,
    table_name: str,
) -> str:
    return (
        f"MySQL 查询结果为空: db={context.db_name!r} table={table_name!r} "
        f"{context.code_label}={context.code_value!r} "
        f"start_date={context.start_date!r} end_date={context.end_date!r}"
    )


def _raise_empty_mysql_result(
    context: _MysqlReadContext,
    *,
    table_name: str,
) -> None:
    raise PriceDataError(_empty_mysql_result_message(context, table_name=table_name))


def _ensure_nonempty_mysql_result(
    df: pd.DataFrame,
    context: _MysqlReadContext,
    *,
    table_name: str,
) -> pd.DataFrame:
    if df.empty:
        _raise_empty_mysql_result(context, table_name=table_name)
    return df


def _finalize_nonempty_mysql_trade_date_frame(
    df: pd.DataFrame,
    context: _MysqlReadContext,
    *,
    table_name: str,
) -> pd.DataFrame:
    df = _ensure_nonempty_mysql_result(df, context, table_name=table_name)
    return _normalize_mysql_trade_date_frame(df)


def _raise_mysql_query_error(
    context: _MysqlReadContext,
    *,
    exc: Exception,
    table_name: str,
    wanted_cols: Sequence[str] | None,
) -> NoReturn:
    err_msg = _format_sa_error(exc)
    if _is_mysql_table_missing_error(err_msg):
        raise FileNotFoundError(
            f"MySQL 表不存在: db={context.db_name!r} table={table_name!r} "
            f"{context.code_label}={context.code_value!r}"
        ) from exc
    if wanted_cols is not None and _is_mysql_unknown_column_error(err_msg):
        raise DataValidationError(
            f"MySQL 查询列不存在: db={context.db_name!r} "
            f"table={table_name!r} wanted_cols={wanted_cols}"
        ) from exc
    raise DataSourceError(
        f"MySQL 查询失败: db={context.db_name!r} table={table_name!r} "
        f"{context.code_label}={context.code_value!r} err={err_msg}"
    ) from exc


def _read_mysql_query_frame(
    context: _MysqlReadContext,
    *,
    query: str,
    engine,
    params: dict[str, str] | None,
    table_name: str,
    wanted_cols: Sequence[str] | None,
) -> pd.DataFrame:
    try:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        return pd.read_sql(text(query), con=engine, params=params or None)
    except SQLAlchemyError as exc:
        _raise_mysql_query_error(
            context,
            exc=exc,
            table_name=table_name,
            wanted_cols=wanted_cols,
        )


def _probe_ths_index_mysql_candidates(
    context: _MysqlReadContext,
    *,
    engine,
    table_candidates: list[str],
    col_candidates: list[list[str] | None],
    where: list[str],
    params: dict[str, str],
    query_reader: Callable[..., pd.DataFrame] = _read_mysql_query_frame,
) -> tuple[pd.DataFrame, str, list[str] | None]:
    last_key_error: KeyError | None = None
    last_col_error: DataValidationError | None = None
    last_table_not_found: FileNotFoundError | None = None

    for table_name in table_candidates:
        table_ident = _quote_sql_identifier(table_name, kind="table", allow_dot=True)
        for raw_cols in col_candidates:
            wanted_cols, cols_sql = _resolve_mysql_select_columns(raw_cols)
            query = _build_mysql_select_query(
                table_ident=table_ident,
                cols_sql=cols_sql,
                where=where,
            )
            try:
                df = query_reader(
                    context=context,
                    query=query,
                    engine=engine,
                    params=params,
                    table_name=table_name,
                    wanted_cols=wanted_cols,
                )
                return df, table_name, raw_cols
            except KeyError as exc:
                last_key_error = exc
                continue
            except DataValidationError as exc:
                if not _is_missing_column_validation_error(exc):
                    raise
                last_col_error = exc
                continue
            except FileNotFoundError as exc:
                last_table_not_found = exc
                break

    if last_key_error is not None:
        raise last_key_error
    if last_col_error is not None:
        raise last_col_error
    if last_table_not_found is not None:
        primary_name = table_candidates[0] if table_candidates else context.code_value
        alt_table_name = table_candidates[1] if len(table_candidates) > 1 else None
        if alt_table_name and alt_table_name != primary_name:
            raise FileNotFoundError(
                f"MySQL 表不存在: db={context.db_name!r} table={primary_name!r} "
                f"or {alt_table_name!r} {context.code_label}={context.code_value!r}"
            ) from last_table_not_found
        raise last_table_not_found
    raise DataSourceError(
        f"MySQL 查询失败: db={context.db_name!r} "
        f"{context.code_label}={context.code_value!r} 未知原因"
    )


def _read_daily_mysql(
    context: _MysqlReadContext,
    *,
    columns: list[str] | None,
    cfg,
    config_loader: ConfigLoader | None,
    runtime_builder: Callable[..., tuple[object, list[str], dict[str, str]]],
    query_reader: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    engine, where, params = runtime_builder(context, cfg=cfg, config_loader=config_loader)
    table_name, wanted_cols, query = _build_daily_mysql_query_context(
        context.code_value,
        columns=columns,
        where=where,
    )
    df = query_reader(
        context=context,
        query=query,
        engine=engine,
        params=params,
        table_name=table_name,
        wanted_cols=wanted_cols,
    )
    df = _finalize_nonempty_mysql_trade_date_frame(df, context, table_name=table_name)
    return _finalize_daily_frame(df)


def _read_thsindex_daily_mysql(
    context: _MysqlReadContext,
    *,
    columns: list[str] | None,
    cfg,
    config_loader: ConfigLoader | None,
    runtime_builder: Callable[..., tuple[object, list[str], dict[str, str]]],
    query_reader: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    engine, where, params = runtime_builder(context, cfg=cfg, config_loader=config_loader)
    table_candidates = _ths_index_daily_table_candidates(context.code_value)
    col_candidates = _build_ths_index_mysql_column_candidates(columns)
    df, used_table_name, used_columns = _probe_ths_index_mysql_candidates(
        context,
        engine=engine,
        table_candidates=table_candidates,
        col_candidates=col_candidates,
        where=where,
        params=params,
        query_reader=query_reader,
    )
    df = _finalize_nonempty_mysql_trade_date_frame(
        df,
        context,
        table_name=used_table_name,
    )
    return _finalize_ths_index_daily_frame(df, used_columns)


def _ensure_thsindex_member_mysql_table_exists(
    context: _MysqlReadContext,
    *,
    engine,
    table_name: str,
) -> None:
    with engine.connect() as conn:
        from sqlalchemy import text

        exists = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :db
                  AND table_name = :tbl
            """
            ),
            {"db": context.db_name, "tbl": table_name},
        ).fetchone()
        if not exists:
            raise TableNotFoundError(
                f"表不存在: {context.db_name}.{table_name} (由 {context.code_value} 映射)"
            )


def _read_thsindex_member_mysql(
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None,
    require_engine: Callable[..., object],
) -> pd.DataFrame:
    table_name = _thsindex_member_table_name(context.code_value)
    table_ident = _quote_sql_identifier(table_name, kind="table")
    engine = require_engine(context, cfg=cfg, config_loader=config_loader)
    _ensure_thsindex_member_mysql_table_exists(context, engine=engine, table_name=table_name)
    return pd.read_sql(f"SELECT * FROM {table_ident}", con=engine)


def _probe_ohlcv_mysql_candidates(
    context: _MysqlReadContext,
    *,
    symbol_type: str,
    base_cols: list[str],
    cfg,
    daily_reader: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    last_key_error: KeyError | None = None
    last_col_error: DataValidationError | None = None

    for req_cols in _build_ohlcv_mysql_probe_columns(base_cols):
        try:
            return daily_reader(
                context.code_value,
                symbol_type=symbol_type,
                start_date=context.start_date,
                end_date=context.end_date,
                columns=req_cols,
                cfg=cfg,
            )
        except KeyError as exc:
            last_key_error = exc
            continue
        except DataValidationError as exc:
            if not _is_missing_column_validation_error(exc):
                raise
            last_col_error = exc
            continue

    if last_key_error is not None:
        raise last_key_error
    if last_col_error is not None:
        raise last_col_error
    raise PriceDataError(f"读取行情失败: symbol_code={context.code_value!r}")


def _probe_mysql_ohlcv_availability(
    symbol_codes: tuple[str, ...],
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None,
    runtime_builder: Callable[..., tuple[object, list[str], dict[str, str]]],
    query_reader: Callable[..., pd.DataFrame],
) -> dict[str, bool]:
    engine, where, params = runtime_builder(context, cfg=cfg, config_loader=config_loader)
    out: dict[str, bool] = {}
    for symbol in symbol_codes:
        symbol_context = replace(context, code_value=symbol)
        table_name, query = _build_mysql_coverage_query_context(symbol, where=where)
        try:
            df = query_reader(
                context=symbol_context,
                query=query,
                engine=engine,
                params=params,
                table_name=table_name,
                wanted_cols=("trade_date",),
            )
        except FileNotFoundError:
            out[symbol] = False
            continue
        if df.empty or "row_count" not in df.columns:
            out[symbol] = False
            continue
        out[symbol] = int(df["row_count"].iloc[0] or 0) > 0
    return out
