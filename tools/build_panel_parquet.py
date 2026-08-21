"""Build the default local Parquet panel from TuShare stock_basic and MySQL stock tables."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_config  # noqa: E402
from core.exceptions import DataSourceError, DataValidationError  # noqa: E402
from core.panel_identity import (  # noqa: E402
    PANEL_MANIFEST_SCHEMA_VERSION,
    canonical_manifest_hash,
    dataset_id_for_manifest,
    semantic_fingerprint,
    source_config_hash,
)
from core.research_identity import sha256_file  # noqa: E402
from data.incomplete_ohlcv import (  # noqa: E402
    DEFAULT_INCOMPLETE_OHLCV_POLICY,
    INCOMPLETE_OHLCV_DIAGNOSTIC_ATTR,
    IncompleteOhlcvAuditAccumulator,
    apply_incomplete_ohlcv_policy,
    attach_incomplete_ohlcv_diagnostic,
    configured_incomplete_ohlcv_policy,
)
from data.local_source import _coerce_trade_date_series  # noqa: E402
from data.mysql_support import _get_mysql_engine, _quote_sql_identifier  # noqa: E402
from data.tushare_client import (  # noqa: E402
    create_tushare_client,
    redact_tushare_error,
    resolve_required_tushare_token,
)
from tools.panel_build_config import (  # noqa: E402
    resolve_tushare_timeout as _resolve_tushare_timeout,
    resolve_workers as _resolve_workers,
)


DEFAULT_OUTPUT = "data/panel_cache/stock_daily.parquet"
DEFAULT_COLUMNS = (
    "trade_date",
    "stock_code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)
REQUIRED_COLUMNS = ("trade_date", "stock_code")
TUSHARE_STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,list_date"
TUSHARE_META_COLUMNS = {"symbol", "name", "area", "industry", "list_date"}
NUMERIC_COLUMNS = {"open", "high", "low", "close", "volume", "vol", "amount"}
_STOCK_CODE_RE = re.compile(r"^(?P<code>\d{6})\.(?P<market>SZ|SH|BJ)$", re.IGNORECASE)
_STOCK_TABLE_RE = re.compile(r"^(?P<code>\d{6})_(?P<market>sz|sh|bj)$", re.IGNORECASE)


@dataclass(frozen=True)
class VerifyStats:
    rows: int
    stocks: int
    min_trade_date: str | None
    max_trade_date: str | None
    duplicate_keys: int
    columns: list[str]
    symbol_coverage: list[dict[str, object]]
    incomplete_ohlcv: dict[str, object]


@dataclass(frozen=True)
class BuildStats:
    source_table: str
    output: str
    rows: int
    stocks: int
    min_trade_date: str | None
    max_trade_date: str | None
    duplicate_keys: int
    mode: str
    manifest: str | None
    elapsed_sec: float
    verify: VerifyStats | None = None
    incomplete_ohlcv: dict[str, object] | None = None


class _StatsAccumulator:
    def __init__(
        self,
        columns: list[str],
        *,
        incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
        audit_source: str = "tools.build_panel_parquet",
    ) -> None:
        self.rows = 0
        self.stocks: set[str] = set()
        self.min_trade_date: pd.Timestamp | None = None
        self.max_trade_date: pd.Timestamp | None = None
        self.duplicate_keys = 0
        self.columns = list(columns)
        self.symbol_coverage: dict[str, dict[str, object]] = {}
        self.incomplete_ohlcv = IncompleteOhlcvAuditAccumulator(
            incomplete_ohlcv_policy,
            audit_source,
        )

    def add_incomplete_ohlcv(self, frame: pd.DataFrame) -> None:
        diagnostic = frame.attrs.get(INCOMPLETE_OHLCV_DIAGNOSTIC_ATTR)
        if isinstance(diagnostic, dict):
            self.incomplete_ohlcv.add(diagnostic)

    def add(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        dates = _coerce_trade_date_series(frame["trade_date"])
        self.rows += int(len(frame))
        self.stocks.update(str(code) for code in frame["stock_code"].dropna().unique())
        self.duplicate_keys += int(frame.duplicated(["stock_code", "trade_date"]).sum())
        batch_min = dates.min()
        batch_max = dates.max()
        self.min_trade_date = batch_min if self.min_trade_date is None else min(self.min_trade_date, batch_min)
        self.max_trade_date = batch_max if self.max_trade_date is None else max(self.max_trade_date, batch_max)
        self.columns = list(frame.columns)
        for code, group in frame.assign(trade_date=dates).groupby("stock_code", sort=False):
            code_text = str(code).strip().upper()
            if not code_text:
                continue
            group_dates = pd.to_datetime(group["trade_date"], errors="coerce").dropna()
            if group_dates.empty:
                continue
            duplicate_count = int(group.duplicated(["stock_code", "trade_date"]).sum())
            prior = self.symbol_coverage.get(code_text)
            rows = int(len(group))
            min_date = group_dates.min()
            max_date = group_dates.max()
            if prior is None:
                self.symbol_coverage[code_text] = {
                    "stock_code": code_text,
                    "rows": rows,
                    "min_trade_date": min_date,
                    "max_trade_date": max_date,
                    "duplicate_keys": duplicate_count,
                }
            else:
                prior["rows"] = int(prior.get("rows") or 0) + rows
                prior["min_trade_date"] = min(prior["min_trade_date"], min_date)
                prior["max_trade_date"] = max(prior["max_trade_date"], max_date)
                prior["duplicate_keys"] = int(prior.get("duplicate_keys") or 0) + duplicate_count

    def snapshot(self) -> VerifyStats:
        coverage: list[dict[str, object]] = []
        for code, row in sorted(self.symbol_coverage.items(), key=lambda item: str(item[0]).casefold()):
            min_date = row.get("min_trade_date")
            max_date = row.get("max_trade_date")
            coverage.append(
                {
                    "stock_code": code,
                    "rows": int(row.get("rows") or 0),
                    "min_trade_date": (
                        pd.Timestamp(min_date).strftime("%Y-%m-%d") if min_date is not None else None
                    ),
                    "max_trade_date": (
                        pd.Timestamp(max_date).strftime("%Y-%m-%d") if max_date is not None else None
                    ),
                    "duplicate_keys": int(row.get("duplicate_keys") or 0),
                }
            )
        return VerifyStats(
            rows=self.rows,
            stocks=len(self.stocks),
            min_trade_date=self.min_trade_date.strftime("%Y-%m-%d") if self.min_trade_date is not None else None,
            max_trade_date=self.max_trade_date.strftime("%Y-%m-%d") if self.max_trade_date is not None else None,
            duplicate_keys=self.duplicate_keys,
            columns=list(self.columns),
            symbol_coverage=coverage,
            incomplete_ohlcv=self.incomplete_ohlcv.snapshot().to_dict(),
        )


def _split_csv(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _resolve_output_path(raw: str, *, cfg) -> Path:
    path = Path(str(raw or DEFAULT_OUTPUT).strip())
    if path.is_absolute():
        return path
    repo_root = getattr(cfg, "repo_root", None) or getattr(getattr(cfg, "paths", None), "repo_root", None)
    base = Path(repo_root) if repo_root is not None else Path.cwd()
    return (base / path).resolve()


def _dataset_path(output: Path) -> Path:
    return output if output.suffix.lower() == ".parquet" else output.with_suffix(".parquet")


def _manifest_path(output: Path) -> Path:
    return _dataset_path(output).with_suffix(".manifest.json")


def _normalize_columns(columns: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in [*REQUIRED_COLUMNS, *columns]:
        col = str(raw or "").strip()
        if not col:
            continue
        if col.lower() == "ts_code":
            col = "stock_code"
        key = col.casefold()
        if key in seen:
            continue
        _quote_sql_identifier(col, kind="column")
        seen.add(key)
        out.append(col)
    return out


def _parse_include_stock_codes(raw: str | None) -> set[str] | None:
    values = _split_csv(raw)
    if not values:
        return None
    out: set[str] = set()
    for value in values:
        token = value.strip().upper()
        code_match = _STOCK_CODE_RE.fullmatch(token)
        if code_match is not None:
            out.add(f"{code_match.group('code')}.{code_match.group('market').upper()}")
            continue
        table_match = _STOCK_TABLE_RE.fullmatch(value.strip())
        if table_match is not None:
            out.add(f"{table_match.group('code')}.{table_match.group('market').upper()}")
            continue
        raise DataValidationError(f"非法股票代码或表名: {value!r}")
    return out


def _table_name_from_stock_code(stock_code: str) -> str:
    match = _STOCK_CODE_RE.fullmatch(str(stock_code or "").strip().upper())
    if match is None:
        raise DataValidationError(f"非法股票代码: {stock_code!r}")
    return f"{match.group('code')}_{match.group('market').lower()}"


def _get_tushare_client(cfg, *, timeout: float | None = None, token: str | None = None):
    token = token or resolve_required_tushare_token(cfg=cfg)
    try:
        return create_tushare_client(token, timeout=timeout)
    except Exception as exc:
        message = redact_tushare_error(exc, token=token)
        raise DataSourceError(
            f"TuShare pro_api 初始化失败: token=set err={message}"
        ) from None


def _load_tushare_stock_basic(
    pro,
    *,
    include: set[str] | None,
    stock_limit: int,
    token: str,
) -> list[dict[str, object]]:
    try:
        df = pro.stock_basic(exchange="", list_status="L", fields=TUSHARE_STOCK_BASIC_FIELDS)
    except Exception as exc:
        error_text = redact_tushare_error(exc, token=token)
        raise DataSourceError(f"TuShare stock_basic 获取失败: {error_text}") from None
    if df is None or df.empty:
        raise DataSourceError("TuShare stock_basic 返回为空")
    if "ts_code" not in df.columns:
        raise DataValidationError("TuShare stock_basic 缺少 ts_code 列")

    work = df.copy()
    work["ts_code"] = work["ts_code"].astype(str).str.strip().str.upper()
    work = work.loc[work["ts_code"].map(lambda value: _STOCK_CODE_RE.fullmatch(value) is not None)].copy()
    if include is not None:
        work = work.loc[work["ts_code"].isin(include)].copy()
    if work.empty:
        raise DataValidationError("TuShare stock_basic 没有匹配的正常上市股票")
    work = work.drop_duplicates(subset=["ts_code"], keep="last")
    work = work.sort_values("ts_code", key=lambda series: series.str.casefold()).reset_index(drop=True)
    if stock_limit > 0:
        work = work.head(stock_limit)
    return work.to_dict(orient="records")


def _table_columns(engine, *, db_name: str, table_name: str) -> set[str]:
    from sqlalchemy import text

    query = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :db_name
          AND table_name = :table_name
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"db_name": db_name, "table_name": table_name}).fetchall()
    return {str(row[0]).lower() for row in rows}


def _build_stock_table_query(
    *,
    table_name: str,
    stock_meta: dict[str, object],
    columns: list[str],
    table_columns: set[str],
) -> tuple[str, dict[str, object]]:
    params: dict[str, object] = {}
    stock_code = str(stock_meta.get("ts_code") or "").strip().upper()
    select_exprs: list[str] = []

    for col in columns:
        ident = _quote_sql_identifier(col, kind="column")
        if col == "stock_code":
            params["stock_code"] = stock_code
            select_exprs.append(f":stock_code AS {ident}")
        elif col in TUSHARE_META_COLUMNS:
            params[f"meta_{col}"] = stock_meta.get(col)
            select_exprs.append(f":meta_{col} AS {ident}")
        elif col == "volume" and "volume" not in table_columns and "vol" in table_columns:
            select_exprs.append(f"`vol` AS {ident}")
        elif col in table_columns:
            select_exprs.append(f"{_quote_sql_identifier(col, kind='column')} AS {ident}")
        else:
            select_exprs.append(f"NULL AS {ident}")

    return (
        f"SELECT {', '.join(select_exprs)} "
        f"FROM {_quote_sql_identifier(table_name, kind='table')} "
        "WHERE `trade_date` IS NOT NULL "
        "ORDER BY `trade_date`",
        params,
    )


def _read_stock_table(
    *,
    engine,
    db_name: str,
    stock_meta: dict[str, object],
    columns: list[str],
) -> pd.DataFrame:
    stock_code = str(stock_meta.get("ts_code") or "").strip().upper()
    table_name = _table_name_from_stock_code(stock_code)
    table_cols = _table_columns(engine, db_name=db_name, table_name=table_name)
    if not table_cols:
        return pd.DataFrame(columns=columns)

    required = {"trade_date"}
    for col in ("open", "high", "low", "close"):
        if col in columns:
            required.add(col)
    if required - table_cols:
        return pd.DataFrame(columns=columns)

    from sqlalchemy import text

    query, params = _build_stock_table_query(
        table_name=table_name,
        stock_meta=stock_meta,
        columns=columns,
        table_columns=table_cols,
    )
    return pd.read_sql(text(query), con=engine, params=params)


def _get_panel_mysql_engine(db_name: str, *, cfg, workers: int):
    if workers <= 1:
        return _get_mysql_engine(db_name, cfg=cfg)
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError
    except Exception as exc:
        raise DataSourceError(f"SQLAlchemy 未安装，无法连接 MySQL: {exc}") from exc

    try:
        engine = create_engine(
            cfg.mysql.sqlalchemy_url(db_name),
            pool_pre_ping=True,
            pool_size=max(1, int(workers)),
            max_overflow=0,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except SQLAlchemyError as exc:
        raise DataSourceError(f"创建 MySQL panel 连接池失败: db={db_name!r} workers={workers} err={exc}") from exc


def _normalize_panel_frame(
    df: pd.DataFrame,
    *,
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS) - set(df.columns), key=str.casefold)
    if missing:
        raise DataValidationError(f"panel 缺少必要列: {missing}")
    out = df.copy()
    out["stock_code"] = out["stock_code"].astype(str).str.strip().str.upper()
    out["trade_date"] = _coerce_trade_date_series(out["trade_date"])
    if out["trade_date"].isna().any():
        bad = out.loc[out["trade_date"].isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"panel trade_date 存在 NaT: {bad}")
    for col in [col for col in out.columns if col in NUMERIC_COLUMNS]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    out, diagnostic = apply_incomplete_ohlcv_policy(
        out,
        policy=incomplete_ohlcv_policy,
        source="tools.build_panel_parquet",
    )
    for col in [col for col in out.columns if col in TUSHARE_META_COLUMNS or col == "stock_code"]:
        out[col] = out[col].astype("string")
    if out.empty:
        empty = out.reset_index(drop=True)
        return attach_incomplete_ohlcv_diagnostic(empty, diagnostic)
    sorted_out = out.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    return attach_incomplete_ohlcv_diagnostic(sorted_out, diagnostic)


def _stats(
    df: pd.DataFrame,
    *,
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> VerifyStats:
    acc = _StatsAccumulator(
        list(df.columns),
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        audit_source="tools.build_panel_parquet.verify",
    )
    if not df.empty:
        normalized = _normalize_panel_frame(
            df,
            incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        )
        acc.add_incomplete_ohlcv(normalized)
        acc.add(normalized)
    return acc.snapshot()


def verify_panel_parquet(
    path: str | Path,
    *,
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> VerifyStats:
    panel_path = Path(path)
    if not panel_path.exists():
        raise DataSourceError(f"Parquet panel 路径不存在: {panel_path}")
    try:
        df = pd.read_parquet(panel_path)
    except ImportError as exc:
        raise DataSourceError(f"读取 Parquet panel 需要 pyarrow 或 fastparquet: {exc}") from exc
    except Exception as exc:
        raise DataSourceError(f"读取 Parquet panel 失败: {panel_path}; {exc}") from exc
    return _stats(df, incomplete_ohlcv_policy=incomplete_ohlcv_policy)


def write_panel_manifest(
    *,
    manifest_path: Path,
    source_db: str,
    output: Path,
    columns: list[str],
    stats: VerifyStats,
    incomplete_ohlcv: dict[str, object],
) -> dict[str, object]:
    dataset_path = _dataset_path(output)
    source_config = {
        "source": "mysql_tables",
        "source_table": "mysql_stock_tables",
        "source_db": source_db,
        "columns": list(columns),
        "price_adjustment": "source_native",
        "partition_by_year": False,
        "mode": "full",
        "refresh_start_date": None,
        "refresh_end_date": None,
        "incomplete_ohlcv_policy": incomplete_ohlcv.get("policy"),
    }
    payload = {
        "format": "parquet",
        "schema_version": PANEL_MANIFEST_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset_type": "daily_a_share_price_panel",
        "source": "mysql_tables",
        "source_table": "mysql_stock_tables",
        "source_db": source_db,
        "source_config": source_config,
        "source_config_hash": source_config_hash({"source_config": source_config}),
        "output": str(dataset_path),
        "file_sha256": sha256_file(dataset_path) if dataset_path.exists() else "",
        "price_adjustment": "source_native",
        "partition_by_year": False,
        "mode": "full",
        "refresh_start_date": None,
        "refresh_end_date": None,
        "rows": stats.rows,
        "stocks": stats.stocks,
        "min_trade_date": stats.min_trade_date,
        "max_trade_date": stats.max_trade_date,
        "duplicate_keys": stats.duplicate_keys,
        "columns": list(columns),
        "symbol_coverage": list(stats.symbol_coverage),
        "incomplete_ohlcv": dict(incomplete_ohlcv),
    }
    payload["semantic_fingerprint"] = semantic_fingerprint(payload)
    payload["dataset_id"] = dataset_id_for_manifest(payload)
    payload["canonical_manifest_sha256"] = canonical_manifest_hash(payload)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_name(
        f".{manifest_path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    )
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, manifest_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return payload


def _replace_output(tmp_path: Path, dataset_path: Path) -> None:
    if dataset_path.is_dir():
        raise DataValidationError(f"默认 panel 输出必须是单文件，当前路径是目录: {dataset_path}")
    os.replace(tmp_path, dataset_path)


class _PanelBuildSink:
    def __init__(
        self,
        *,
        pa,
        pq,
        args: argparse.Namespace,
        dataset_path: Path,
        tmp_path: Path,
        stock_count: int,
        stats: _StatsAccumulator,
        incomplete_ohlcv_policy: str,
    ) -> None:
        self.pa = pa
        self.pq = pq
        self.args = args
        self.dataset_path = dataset_path
        self.tmp_path = tmp_path
        self.stock_count = stock_count
        self.stats = stats
        self.incomplete_ohlcv_policy = incomplete_ohlcv_policy
        self.writer = None
        self.schema = None
        self.written = False
        self.started = time.perf_counter()
        self.progress_every = max(1, int(args.progress_every or 100))

    def handle(self, completed: int, stock_code: str, frame: pd.DataFrame) -> None:
        if not frame.empty:
            panel = _normalize_panel_frame(
                frame,
                incomplete_ohlcv_policy=self.incomplete_ohlcv_policy,
            )
            self.stats.add_incomplete_ohlcv(panel)
            self.stats.add(panel)
            table = (
                self.pa.Table.from_pandas(panel, schema=self.schema, preserve_index=False)
                if self.schema is not None
                else None
            )
            if not self.args.dry_run:
                if self.schema is None:
                    table = self.pa.Table.from_pandas(panel, preserve_index=False)
                    self.schema = table.schema
                    self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
                    self.writer = self.pq.ParquetWriter(
                        str(self.tmp_path),
                        self.schema,
                        compression="snappy",
                    )
                self.writer.write_table(table)
                self.written = True
        should_report = (
            completed == 1
            or completed == self.stock_count
            or completed % self.progress_every == 0
        )
        if not self.args.quiet and should_report:
            elapsed = time.perf_counter() - self.started
            print(
                f"panel_build_progress {completed}/{self.stock_count} "
                f"stock={stock_code} rows={len(frame)} "
                f"total_rows={self.stats.rows} elapsed_sec={elapsed:.1f}"
            )

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None


def _load_serial_stock_frames(
    *,
    stock_rows: list[dict[str, object]],
    engine,
    db_name: str,
    columns: list[str],
    sink: _PanelBuildSink,
) -> None:
    for completed, stock_meta in enumerate(stock_rows, start=1):
        stock_code = str(stock_meta.get("ts_code") or "").strip().upper()
        frame = _read_stock_table(
            engine=engine,
            db_name=db_name,
            stock_meta=stock_meta,
            columns=columns,
        )
        sink.handle(completed, stock_code, frame)


def _load_parallel_stock_frames(
    *,
    stock_rows: list[dict[str, object]],
    engine,
    db_name: str,
    columns: list[str],
    workers: int,
    sink: _PanelBuildSink,
) -> None:
    stock_iter = iter(stock_rows)
    pending: dict[object, tuple[int, dict[str, object]]] = {}
    ready: dict[int, tuple[dict[str, object], pd.DataFrame]] = {}
    next_index = 0
    next_submit_index = 0
    completed = 0

    def _submit_next(executor: ThreadPoolExecutor) -> bool:
        try:
            stock_meta = next(stock_iter)
        except StopIteration:
            return False
        future = executor.submit(
            _read_stock_table,
            engine=engine,
            db_name=db_name,
            stock_meta=stock_meta,
            columns=columns,
        )
        nonlocal next_submit_index
        pending[future] = (next_submit_index, stock_meta)
        next_submit_index += 1
        return True

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        for _ in range(workers):
            if not _submit_next(executor):
                break
        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                index, stock_meta = pending.pop(future)
                stock_code = str(stock_meta.get("ts_code") or "").strip().upper()
                ready[index] = (stock_meta, future.result())
                _submit_next(executor)
            while next_index in ready:
                stock_meta, frame = ready.pop(next_index)
                stock_code = str(stock_meta.get("ts_code") or "").strip().upper()
                completed += 1
                sink.handle(completed, stock_code, frame)
                next_index += 1
    except Exception:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def _load_stock_frames(
    *,
    stock_rows: list[dict[str, object]],
    engine,
    db_name: str,
    columns: list[str],
    workers: int,
    sink: _PanelBuildSink,
) -> None:
    stock_rows = sorted(
        stock_rows,
        key=lambda row: str(row.get("ts_code") or "").strip().casefold(),
    )
    if workers <= 1:
        _load_serial_stock_frames(
            stock_rows=stock_rows,
            engine=engine,
            db_name=db_name,
            columns=columns,
            sink=sink,
        )
        return
    _load_parallel_stock_frames(
        stock_rows=stock_rows,
        engine=engine,
        db_name=db_name,
        columns=columns,
        workers=workers,
        sink=sink,
    )


def _validate_and_commit_panel(
    *,
    stats: _StatsAccumulator,
    sink: _PanelBuildSink,
    db_name: str,
    dry_run: bool,
    tmp_path: Path,
    dataset_path: Path,
) -> VerifyStats:
    snapshot = stats.snapshot()
    if snapshot.rows <= 0:
        raise DataSourceError(f"MySQL 单股表未读取到任何行情: db={db_name}")
    if snapshot.duplicate_keys:
        raise DataValidationError(f"源数据存在重复 stock_code+trade_date: {snapshot.duplicate_keys}")
    sink.close()
    if not dry_run:
        if not sink.written:
            raise DataSourceError("没有写入任何 Parquet row group")
        _replace_output(tmp_path, dataset_path)
    return snapshot


def _build_mysql_tables_panel(
    *,
    cfg,
    args: argparse.Namespace,
    db_name: str,
    columns: list[str],
    output: Path,
) -> VerifyStats:
    incomplete_ohlcv_policy = configured_incomplete_ohlcv_policy(cfg)
    pa = None
    pq = None
    if not args.dry_run:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise DataSourceError(f"写入 Parquet panel 需要 pyarrow: {exc}") from exc

    token = resolve_required_tushare_token(cfg=cfg)
    pro = _get_tushare_client(
        cfg,
        timeout=_resolve_tushare_timeout(args.tushare_timeout),
        token=token,
    )
    stock_rows = _load_tushare_stock_basic(
        pro,
        include=_parse_include_stock_codes(args.include),
        stock_limit=max(0, int(args.stock_limit or 0)),
        token=token,
    )
    workers = _resolve_workers(args.workers, stock_count=len(stock_rows))
    engine = _get_panel_mysql_engine(db_name, cfg=cfg, workers=workers)
    dispose_engine = workers > 1
    dataset_path = _dataset_path(output)
    tmp_path = dataset_path.with_name(f".{dataset_path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    stats = _StatsAccumulator(
        columns,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )
    sink = _PanelBuildSink(
        pa=pa,
        pq=pq,
        args=args,
        dataset_path=dataset_path,
        tmp_path=tmp_path,
        stock_count=len(stock_rows),
        stats=stats,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )

    try:
        if not args.quiet:
            print(f"panel_build_start db={db_name} stocks={len(stock_rows)} workers={workers} output={dataset_path}")
        _load_stock_frames(
            stock_rows=stock_rows,
            engine=engine,
            db_name=db_name,
            columns=columns,
            workers=workers,
            sink=sink,
        )
        return _validate_and_commit_panel(
            stats=stats,
            sink=sink,
            db_name=db_name,
            dry_run=args.dry_run,
            tmp_path=tmp_path,
            dataset_path=dataset_path,
        )
    finally:
        sink.close()
        if dispose_engine:
            dispose_fn = getattr(engine, "dispose", None)
            if callable(dispose_fn):
                dispose_fn()
        if tmp_path.exists():
            tmp_path.unlink()


def build_panel_parquet(args: argparse.Namespace) -> BuildStats:
    started = time.perf_counter()
    cfg = get_config(reload=True)
    incomplete_ohlcv_policy = configured_incomplete_ohlcv_policy(cfg)
    db_name = str(args.db or getattr(cfg.mysql, "stock_db", "") or "").strip()
    if not db_name:
        raise DataSourceError("mysql.stock_db 为空，无法读取 MySQL 单股表；也可以传 --db 指定库名")

    columns = _normalize_columns(_split_csv(args.columns) if args.columns else DEFAULT_COLUMNS)
    output = _resolve_output_path(args.output, cfg=cfg)
    dataset_path = _dataset_path(output)
    stats = _build_mysql_tables_panel(cfg=cfg, args=args, db_name=db_name, columns=columns, output=output)
    verify = (
        verify_panel_parquet(
            dataset_path,
            incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        )
        if args.verify and not args.dry_run
        else None
    )
    manifest = None
    if not args.dry_run:
        manifest_path = _manifest_path(output)
        write_panel_manifest(
            manifest_path=manifest_path,
            source_db=db_name,
            output=output,
            columns=(verify or stats).columns,
            stats=verify or stats,
            incomplete_ohlcv=stats.incomplete_ohlcv,
        )
        manifest = str(manifest_path)
    return BuildStats(
        source_table="mysql_stock_tables",
        output=str(dataset_path),
        rows=stats.rows,
        stocks=stats.stocks,
        min_trade_date=stats.min_trade_date,
        max_trade_date=stats.max_trade_date,
        duplicate_keys=stats.duplicate_keys,
        mode="full",
        manifest=manifest,
        elapsed_sec=float(time.perf_counter() - started),
        verify=verify,
        incomplete_ohlcv=stats.incomplete_ohlcv,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="默认构建：TuShare stock_basic 股票列表 + MySQL 单股表全量数据 -> data/panel_cache/stock_daily.parquet。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/build_panel_parquet.py\n"
            "  python tools/build_panel_parquet.py --db stock_data --verify\n"
            "  python tools/build_panel_parquet.py --include 000001.SZ,600000.SH --dry-run\n"
        ),
    )
    parser.add_argument("--db", default="", help="MySQL 股票行情库；默认 config.mysql.stock_db。")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"输出 Parquet 文件；默认 {DEFAULT_OUTPUT}。")
    parser.add_argument("--columns", default=",".join(DEFAULT_COLUMNS), help="输出字段，逗号分隔。")
    parser.add_argument("--include", default="", help="仅处理这些 TuShare 股票代码或单票表名，逗号分隔。")
    parser.add_argument("--stock-limit", type=int, default=0, help="调试用：最多处理多少只股票；0 表示不限。")
    parser.add_argument(
        "--workers",
        default="auto",
        help="MySQL 单股表读取线程数；默认 auto（上限默认 8，可用 PANEL_MYSQL_AUTO_WORKERS_MAX 调整）。",
    )
    parser.add_argument("--progress-every", type=int, default=100, help="每处理多少只股票输出一次进度。")
    parser.add_argument("--tushare-timeout", default=None, help="TuShare stock_basic 请求超时秒数；默认 30。")
    parser.add_argument("--quiet", action="store_true", help="减少进度输出。")
    parser.add_argument("--verify", action="store_true", help="写入后重新读取 Parquet 校验统计。")
    parser.add_argument("--dry-run", action="store_true", help="只读取并统计，不写 Parquet，不覆盖旧 panel。")
    parser.add_argument("--json-out", default="", help="可选：写出 BuildStats JSON。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = build_panel_parquet(args)
    payload = asdict(stats)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "panel_parquet_done "
        f"source_table={stats.source_table} mode={stats.mode} output={stats.output} rows={stats.rows} "
        f"stocks={stats.stocks} min_trade_date={stats.min_trade_date} "
        f"max_trade_date={stats.max_trade_date} duplicate_keys={stats.duplicate_keys} "
        f"manifest={stats.manifest} elapsed_sec={stats.elapsed_sec:.3f}"
    )
    if stats.verify is not None:
        print(
            "panel_parquet_verify "
            f"rows={stats.verify.rows} stocks={stats.verify.stocks} "
            f"min_trade_date={stats.verify.min_trade_date} max_trade_date={stats.verify.max_trade_date} "
            f"duplicate_keys={stats.verify.duplicate_keys}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
