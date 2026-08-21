"""Parquet-backed price panel reader used by the runtime."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from collections.abc import Callable, Iterable
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.exceptions import ConfigurationError, DataSourceError, DataValidationError
from core.invariants import PANEL_SORTED_ATTR as _PANEL_SORTED_ATTR
from .incomplete_ohlcv import (
    DEFAULT_INCOMPLETE_OHLCV_POLICY,
    apply_incomplete_ohlcv_policy,
    attach_incomplete_ohlcv_diagnostic,
    configured_incomplete_ohlcv_policy,
    normalize_incomplete_ohlcv_policy,
)
from .local_source import _coerce_trade_date_series
from .mysql_support import _quote_sql_identifier
from .trade_calendar import _parse_date_yyyymmdd_or_iso


ConfigLoader = Callable[[], object]

DEFAULT_PANEL_PARQUET_PATH = "data/panel_cache/stock_daily.parquet"
PANEL_SOURCE_PARQUET = "parquet"

_BASE_PANEL_COLUMNS = ("trade_date", "stock_code", "open", "high", "low", "close", "volume")
_PARQUET_FILTERED_ATTR = "_codex_parquet_filter_applied"


@dataclass(frozen=True)
class PanelPreloadResult:
    panel: pd.DataFrame
    frames_by_stock: dict[str, pd.DataFrame]
    stage_timings_sec: dict[str, float] | None = None


def _cfg_panel(cfg=None):
    return getattr(cfg, "panel", None) if cfg is not None else None


def panel_source_from_env(cfg=None) -> str:
    panel = _cfg_panel(cfg)
    if panel is not None and hasattr(panel, "source"):
        raw = str(getattr(panel, "source", "") or "").strip().lower()
    else:
        raw = str(os.environ.get("PRICE_PANEL_SOURCE") or "parquet").strip().lower()
    if not raw:
        raw = PANEL_SOURCE_PARQUET
    if raw != PANEL_SOURCE_PARQUET:
        raise DataValidationError("PRICE_PANEL_SOURCE only supports parquet")
    return PANEL_SOURCE_PARQUET


def panel_path_from_env(default: str = DEFAULT_PANEL_PARQUET_PATH, cfg=None) -> str:
    panel = _cfg_panel(cfg)
    if panel is not None and hasattr(panel, "path"):
        return str(getattr(panel, "path", "") or default).strip() or default
    return str(os.environ.get("PRICE_PANEL_PATH") or default).strip() or default


def panel_incomplete_ohlcv_policy_from_env(
    default: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
    cfg=None,
) -> str:
    panel = _cfg_panel(cfg)
    if panel is not None and hasattr(panel, "incomplete_ohlcv_policy"):
        return configured_incomplete_ohlcv_policy(cfg)
    raw = str(os.environ.get("PRICE_PANEL_INCOMPLETE_OHLCV_POLICY") or default).strip()
    try:
        return normalize_incomplete_ohlcv_policy(raw)
    except ValueError as exc:
        raise DataValidationError(str(exc)) from exc


def _resolve_cfg(*, cfg, config_loader: ConfigLoader | None = None):
    if cfg is not None:
        return cfg
    if config_loader is not None:
        return config_loader()
    raise ConfigurationError("data.market_panel 需要显式传入 cfg。")


def _clean_stock_codes(stock_codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in stock_codes:
        code = str(raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _normalize_requested_columns(columns: Iterable[str] | None) -> list[str]:
    requested = list(_BASE_PANEL_COLUMNS)
    if columns is not None:
        requested.extend(str(col).strip() for col in columns if str(col).strip())

    out: list[str] = []
    seen: set[str] = set()
    for col in requested:
        name = str(col).strip()
        if name.lower() == "ts_code":
            name = "stock_code"
        key = name.casefold()
        if not name or key in seen:
            continue
        _quote_sql_identifier(name, kind="column")
        seen.add(key)
        out.append(name)
    return out


def _stock_date_keys_are_sorted_unique(df: pd.DataFrame) -> bool:
    """Check canonical stock-major keys without pandas hash/sort overhead."""
    if len(df) < 2:
        return True
    try:
        codes = df["stock_code"].to_numpy(copy=False)
        dates = df["trade_date"].to_numpy(dtype="datetime64[ns]", copy=False)
        same_stock = codes[1:] == codes[:-1]
        if bool(np.any(same_stock & (dates[1:] <= dates[:-1]))):
            return False
        # Canonical symbols are upper-case strings, so their normal lexical
        # order is the same as the documented case-folded order.
        if bool(np.any(codes[1:] < codes[:-1])):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _normalize_panel_frame(
    df: pd.DataFrame,
    *,
    copy_frame: bool = True,
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> pd.DataFrame:
    if df.empty:
        return df
    # Readers pass newly materialized frames that are owned by this module, so
    # they can opt out of a second full-column copy.  The default remains a
    # detached copy for direct callers and compatibility helpers.
    out = df.copy() if copy_frame else df
    if "stock_code" not in out.columns and "ts_code" in out.columns:
        out["stock_code"] = out["ts_code"]
    required = {"trade_date", "stock_code"}
    missing = sorted(required - set(out.columns), key=str.casefold)
    if missing:
        raise DataValidationError(f"panel 行情缺少列: {missing}")

    stock_codes = out["stock_code"]
    # Parquet panels are emitted with canonical upper-case symbols.  Validate
    # that invariant over the distinct values first; this avoids applying two
    # pandas string kernels to every row on the hot path.  Non-canonical input
    # still takes the full normalization path below, including mixed-case and
    # whitespace handling used by legacy/fixture sources.
    canonical_codes = True
    for value in pd.unique(stock_codes):
        normalized = str(value).strip().upper()
        if not isinstance(value, str) or value != normalized:
            canonical_codes = False
            break
    if not canonical_codes:
        values = stock_codes.to_numpy(copy=False)
        out["stock_code"] = np.asarray(
            [str(value).strip().upper() for value in values],
            dtype=object,
        )
    elif not pd.api.types.is_object_dtype(stock_codes.dtype):
        # Keep the historical object-backed public frame shape while avoiding
        # the per-row string normalization work above.
        out["stock_code"] = stock_codes.to_numpy(dtype=object, copy=False)
    try:
        out["trade_date"] = _coerce_trade_date_series(out["trade_date"])
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"panel 行情 trade_date 无法解析: {exc}") from exc
    if out["trade_date"].isna().any():
        bad = out.loc[out["trade_date"].isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"panel 行情 trade_date 存在 NaT: {bad}")
    keys_are_sorted = _stock_date_keys_are_sorted_unique(out)
    if not keys_are_sorted:
        duplicate_mask = out.duplicated(["stock_code", "trade_date"], keep=False)
        if bool(duplicate_mask.any()):
            duplicates = (
                out.loc[duplicate_mask, ["stock_code", "trade_date"]]
                .drop_duplicates()
                .head(5)
                .to_dict(orient="records")
            )
            raise DataValidationError(
                f"panel 行情存在重复主键 (stock_code, trade_date): {duplicates}"
            )

    if "volume" not in out.columns and "vol" in out.columns:
        out["volume"] = out["vol"]
    numeric_cols = [
        col
        for col in ("open", "high", "low", "close", "volume", "vol", "amount")
        if col in out.columns
        and not pd.api.types.is_numeric_dtype(out[col].dtype)
    ]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out, diagnostic = apply_incomplete_ohlcv_policy(
        out,
        policy=incomplete_ohlcv_policy,
        source="data.market_panel",
    )
    if out.empty:
        out.attrs[_PANEL_SORTED_ATTR] = True
        empty = out.reset_index(drop=True)
        empty.attrs[_PANEL_SORTED_ATTR] = True
        return attach_incomplete_ohlcv_diagnostic(empty, diagnostic)

    if not keys_are_sorted:
        out.sort_values(["stock_code", "trade_date"], kind="mergesort", inplace=True)
    out = out.reset_index(drop=True)
    out.attrs[_PANEL_SORTED_ATTR] = True
    return attach_incomplete_ohlcv_diagnostic(out, diagnostic)


def _resolve_panel_path(raw_path: str | os.PathLike[str] | None, *, cfg) -> Path:
    raw = str(raw_path or panel_path_from_env(cfg=cfg)).strip()
    path = Path(raw)
    if not path.is_absolute():
        repo_root = getattr(cfg, "repo_root", None)
        if repo_root is None:
            paths = getattr(cfg, "paths", None)
            repo_root = getattr(paths, "repo_root", None)
        base = Path(repo_root) if repo_root is not None else Path.cwd()
        path = base / path
    resolved = path.resolve()
    if resolved.is_dir() and (resolved / "current.json").is_file():
        from core.panel_snapshot import resolve_panel_snapshot

        return resolve_panel_snapshot(resolved).panel_path
    return resolved


def _panel_manifest_path(panel_path: Path) -> Path:
    if panel_path.suffix.lower() == ".parquet":
        return panel_path.with_suffix(".manifest.json")
    return panel_path.with_name(f"{panel_path.name}.manifest.json")


def _load_panel_manifest(panel_path: Path) -> dict[str, object] | None:
    manifest_path = _panel_manifest_path(panel_path)
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataValidationError(f"Parquet panel manifest 无法读取: {manifest_path}; {exc}") from exc
    if not isinstance(payload, dict):
        raise DataValidationError(f"Parquet panel manifest 格式无效: {manifest_path}")
    return payload


def _validate_panel_manifest(
    panel_path: Path,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    required_columns: list[str],
) -> None:
    manifest = _load_panel_manifest(panel_path)
    if manifest is None:
        raise DataValidationError(f"Parquet panel 缺少 manifest: {_panel_manifest_path(panel_path)}")
    duplicate_keys = int(manifest.get("duplicate_keys") or 0)
    if duplicate_keys:
        raise DataValidationError(f"Parquet panel manifest 显示存在重复主键: {duplicate_keys}")
    rows = int(manifest.get("rows") or 0)
    stocks = int(manifest.get("stocks") or 0)
    if rows <= 0 or stocks <= 0:
        raise DataValidationError(f"Parquet panel manifest 行数/股票数无效: rows={rows} stocks={stocks}")
    columns = {str(col).strip().casefold() for col in manifest.get("columns") or []}
    missing = [col for col in required_columns if col.casefold() not in columns]
    if missing:
        raise DataValidationError(f"Parquet panel manifest 缺少请求列: {missing}")

    min_ts = _parse_date_yyyymmdd_or_iso(manifest.get("min_trade_date"))
    max_ts = _parse_date_yyyymmdd_or_iso(manifest.get("max_trade_date"))
    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    if start_ts is not None and min_ts is not None and min_ts > start_ts:
        raise DataValidationError(
            f"Parquet panel manifest 起始日期不足: min_trade_date={min_ts.date()} start_date={start_ts.date()}"
        )
    if start_ts is not None and max_ts is not None and max_ts < start_ts:
        raise DataValidationError(
            f"Parquet panel manifest 已过期: max_trade_date={max_ts.date()} start_date={start_ts.date()}"
        )


def _filter_panel_frame(
    panel: pd.DataFrame,
    *,
    stock_codes: list[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    if panel.empty:
        return panel
    out = panel
    if stock_codes:
        code_set = set(stock_codes)
        out = out[out["stock_code"].isin(code_set)]
    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    if start_ts is not None:
        out = out[out["trade_date"].ge(start_ts)]
    if end_ts is not None:
        out = out[out["trade_date"].le(end_ts)]
    return out


def _read_parquet_with_pyarrow_dataset(
    *,
    panel_path: Path,
    columns: list[str],
    stock_codes: list[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.dataset as ds

    dataset = ds.dataset(str(panel_path), format="parquet", partitioning="hive")
    schema_names = set(dataset.schema.names)
    requested = _resolve_pyarrow_requested_columns(columns, schema_names=schema_names)
    filter_expr, filter_complete = _build_pyarrow_dataset_filter(
        dataset=dataset,
        schema_names=schema_names,
        stock_codes=stock_codes,
        start_date=start_date,
        end_date=end_date,
        pa=pa,
        ds=ds,
    )

    table = dataset.to_table(columns=requested, filter=filter_expr)
    if table.num_rows == 0:
        return pd.DataFrame(columns=columns)
    frame = table.to_pandas()
    # The dataset scanner already applied the complete stock/date predicate.
    # Keep a private marker so the caller does not scan and allocate the same
    # million-row boolean masks a second time.
    if filter_complete:
        frame.attrs[_PARQUET_FILTERED_ATTR] = True
    return frame


def _resolve_pyarrow_requested_columns(columns: list[str], *, schema_names: set[str]) -> list[str]:
    requested = [col for col in columns if col in schema_names]
    if len(requested) == len(columns):
        return requested
    if "volume" not in columns or "vol" not in schema_names:
        missing = sorted(set(columns) - schema_names, key=str.casefold)
        raise DataValidationError(f"Parquet panel 缺少请求列: {missing}")
    return ["vol" if col == "volume" and col not in schema_names else col for col in columns]


def _build_pyarrow_dataset_filter(
    *,
    dataset: Any,
    schema_names: set[str],
    stock_codes: list[str],
    start_date: Optional[str],
    end_date: Optional[str],
    pa: Any,
    ds: Any,
) -> tuple[Any, bool]:
    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    filters = _pyarrow_year_filters(schema_names, start_ts=start_ts, end_ts=end_ts, ds=ds)
    date_filters = _pyarrow_trade_date_filters(
        dataset,
        schema_names=schema_names,
        start_ts=start_ts,
        end_ts=end_ts,
        pa=pa,
        ds=ds,
    )
    filters.extend(date_filters)
    if "stock_code" in schema_names and stock_codes:
        filters.append(ds.field("stock_code").isin(_stock_code_filter_values(stock_codes)))
    requested_date_filters = int(start_ts is not None) + int(end_ts is not None)
    filter_complete = (
        (not stock_codes or "stock_code" in schema_names)
        and len(date_filters) == requested_date_filters
    )
    return _combine_pyarrow_filters(filters), filter_complete


def _pyarrow_year_filters(
    schema_names: set[str],
    *,
    start_ts: pd.Timestamp | None,
    end_ts: pd.Timestamp | None,
    ds: Any,
) -> list[Any]:
    filters: list[Any] = []
    if "year" in schema_names and (start_ts is not None or end_ts is not None):
        if start_ts is not None:
            filters.append(ds.field("year") >= int(start_ts.year))
        if end_ts is not None:
            filters.append(ds.field("year") <= int(end_ts.year))
    return filters


def _pyarrow_trade_date_filters(
    dataset: Any,
    *,
    schema_names: set[str],
    start_ts: pd.Timestamp | None,
    end_ts: pd.Timestamp | None,
    pa: Any,
    ds: Any,
) -> list[Any]:
    if "trade_date" not in schema_names or (start_ts is None and end_ts is None):
        return []

    trade_date_type = dataset.schema.field("trade_date").type
    if pa.types.is_timestamp(trade_date_type):
        start_value = start_ts.to_pydatetime() if start_ts is not None else None
        end_value = end_ts.to_pydatetime() if end_ts is not None else None
    elif pa.types.is_date(trade_date_type):
        start_value = start_ts.date() if start_ts is not None else None
        end_value = end_ts.date() if end_ts is not None else None
    elif pa.types.is_string(trade_date_type):
        start_value = start_ts.strftime("%Y%m%d") if start_ts is not None else None
        end_value = end_ts.strftime("%Y%m%d") if end_ts is not None else None
        return _pyarrow_range_filters("trade_date", start_value, end_value, ds=ds)
    else:
        return []
    return _pyarrow_range_filters("trade_date", start_value, end_value, ds=ds, pa=pa, value_type=trade_date_type)


def _pyarrow_range_filters(
    field_name: str,
    start_value: Any,
    end_value: Any,
    *,
    ds: Any,
    pa: Any = None,
    value_type: Any = None,
) -> list[Any]:
    def _scalar(value: Any) -> Any:
        return pa.scalar(value, type=value_type) if pa is not None else value

    filters: list[Any] = []
    if start_value is not None:
        filters.append(ds.field(field_name) >= _scalar(start_value))
    if end_value is not None:
        filters.append(ds.field(field_name) <= _scalar(end_value))
    return filters


def _stock_code_filter_values(stock_codes: list[str]) -> list[str]:
    return sorted(
        {variant for code in stock_codes for variant in (str(code), str(code).upper(), str(code).lower())},
        key=str.casefold,
    )


def _combine_pyarrow_filters(filters: list[Any]) -> Any:
    filter_expr = None
    for expr in filters:
        filter_expr = expr if filter_expr is None else filter_expr & expr
    return filter_expr


def _read_parquet_with_pandas(
    *,
    panel_path: Path,
    columns: list[str],
) -> pd.DataFrame:
    try:
        return pd.read_parquet(panel_path, columns=columns)
    except ValueError as exc:
        if "volume" not in columns:
            raise DataValidationError(f"Parquet panel 缺少请求列或无法读取: {panel_path}; {exc}") from exc
        fallback_columns = ["vol" if col == "volume" else col for col in columns]
        try:
            return pd.read_parquet(panel_path, columns=fallback_columns)
        except Exception as fallback_exc:
            raise DataValidationError(
                f"Parquet panel 缺少请求列或无法读取: {panel_path}; {fallback_exc}"
            ) from fallback_exc


def read_price_panel_from_parquet(
    stock_codes: Iterable[str],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Iterable[str] | None = None,
    path: str | os.PathLike[str] | None = None,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    codes = _clean_stock_codes(stock_codes)
    if not codes:
        return pd.DataFrame(columns=list(_BASE_PANEL_COLUMNS))

    panel_path = _resolve_panel_path(path, cfg=cfg)
    if not panel_path.exists():
        raise DataSourceError(f"Parquet panel 路径不存在: {panel_path}")

    requested = _normalize_requested_columns(columns)
    _validate_panel_manifest(
        panel_path,
        start_date=start_date,
        end_date=end_date,
        required_columns=requested,
    )
    try:
        raw = _read_parquet_with_pyarrow_dataset(
            panel_path=panel_path,
            columns=requested,
            stock_codes=codes,
            start_date=start_date,
            end_date=end_date,
        )
    except ImportError as exc:
        raise DataSourceError(f"读取 Parquet panel 需要 pyarrow 或 fastparquet: {exc}") from exc
    except DataValidationError:
        raise
    except Exception:
        try:
            raw = _read_parquet_with_pandas(panel_path=panel_path, columns=requested)
        except ImportError as import_exc:
            raise DataSourceError(f"读取 Parquet panel 需要 pyarrow 或 fastparquet: {import_exc}") from import_exc
        except DataValidationError:
            raise
        except Exception as fallback_exc:
            raise DataSourceError(f"读取 Parquet panel 失败: {panel_path}; {fallback_exc}") from fallback_exc

    if raw.empty:
        return pd.DataFrame(columns=list(_BASE_PANEL_COLUMNS))
    panel = _normalize_panel_frame(
        raw,
        copy_frame=False,
        incomplete_ohlcv_policy=panel_incomplete_ohlcv_policy_from_env(cfg=cfg),
    )
    filtered = (
        panel
        if bool(raw.attrs.get(_PARQUET_FILTERED_ATTR, False))
        else _filter_panel_frame(
            panel,
            stock_codes=codes,
            start_date=start_date,
            end_date=end_date,
        )
    )
    if filtered is not panel or not isinstance(filtered.index, pd.RangeIndex):
        filtered = filtered.reset_index(drop=True)
    return filtered


def read_price_panel(
    stock_codes: Iterable[str],
    *,
    source: str | None = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Iterable[str] | None = None,
    path: str | os.PathLike[str] | None = None,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    panel_source = panel_source_from_env(cfg) if source is None else str(source).strip().lower()
    if panel_source != PANEL_SOURCE_PARQUET:
        raise DataValidationError("panel source only supports parquet")
    return read_price_panel_from_parquet(
        stock_codes,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        path=path,
        cfg=cfg,
    )


def split_panel_by_stock(panel: pd.DataFrame, *, assume_normalized: bool = False) -> dict[str, pd.DataFrame]:
    if panel is None or panel.empty:
        return {}
    if "stock_code" not in panel.columns or "trade_date" not in panel.columns:
        raise DataValidationError("split_panel_by_stock 需要 stock_code/trade_date 列")

    out: dict[str, pd.DataFrame] = {}
    work = panel if assume_normalized else _normalize_panel_frame(panel)
    for code, grp in work.groupby("stock_code", sort=False):
        code_text = str(code).strip().upper()
        if not code_text:
            continue
        out[code_text] = grp.reset_index(drop=True)
    return out


def preload_panel_ohlcv_result(
    stock_codes: Iterable[str],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Iterable[str] | None = None,
    split_frames: bool = True,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> PanelPreloadResult:
    timings: dict[str, float] = {}
    started = time.perf_counter()
    panel = read_price_panel(
        stock_codes,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        cfg=cfg,
        config_loader=config_loader,
    )
    timings["panel_read_price_panel"] = time.perf_counter() - started

    frames: dict[str, pd.DataFrame]
    if split_frames:
        started = time.perf_counter()
        frames = split_panel_by_stock(panel, assume_normalized=True)
        timings["panel_split_by_stock"] = time.perf_counter() - started
    else:
        frames = {}
        timings["panel_split_by_stock"] = 0.0

    return PanelPreloadResult(panel=panel, frames_by_stock=frames, stage_timings_sec=timings)


def preload_panel_ohlcv_frames(
    stock_codes: Iterable[str],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Iterable[str] | None = None,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> dict[str, pd.DataFrame]:
    return preload_panel_ohlcv_result(
        stock_codes,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        split_frames=True,
        cfg=cfg,
        config_loader=config_loader,
    ).frames_by_stock


__all__ = [
    "DEFAULT_PANEL_PARQUET_PATH",
    "PANEL_SOURCE_PARQUET",
    "PanelPreloadResult",
    "_panel_manifest_path",
    "panel_path_from_env",
    "panel_source_from_env",
    "preload_panel_ohlcv_frames",
    "preload_panel_ohlcv_result",
    "read_price_panel",
    "read_price_panel_from_parquet",
    "split_panel_by_stock",
]
