"""Parquet L2 cache policy and I/O helpers for market-data readers."""
from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from core.caching.price_l2 import (
    PriceParquetL2Cache,
    default_price_l2_cache_dir,
    price_l2_cache_enabled_from_env,
    price_l2_cache_write_enabled_from_env,
)
from core.dataframe_schema import normalize_price_frame
from core.exceptions import ConfigurationError, DataValidationError, PriceCacheError

from .local_source import _coerce_trade_date_series, _read_local_daily
from .incomplete_ohlcv import DEFAULT_INCOMPLETE_OHLCV_POLICY, IncompleteOhlcvDataError
from .market_data_frames import _finalize_ohlcv_frame
from .trade_calendar import _parse_date_yyyymmdd_or_iso


logger = logging.getLogger(__name__)
ConfigLoader = Callable[[], object]

_MYSQL_L2_MODE_OFF = "off"
_MYSQL_L2_MODE_WRITE_THROUGH = "write_through"
_MYSQL_L2_MODE_READ_STALE = "read_stale"
_MYSQL_L2_MODE_ALIASES = {
    "0": _MYSQL_L2_MODE_OFF,
    "false": _MYSQL_L2_MODE_OFF,
    "no": _MYSQL_L2_MODE_OFF,
    "off": _MYSQL_L2_MODE_OFF,
    "none": _MYSQL_L2_MODE_OFF,
    "write": _MYSQL_L2_MODE_WRITE_THROUGH,
    "write_through": _MYSQL_L2_MODE_WRITE_THROUGH,
    "write-through": _MYSQL_L2_MODE_WRITE_THROUGH,
    "read": _MYSQL_L2_MODE_READ_STALE,
    "read_stale": _MYSQL_L2_MODE_READ_STALE,
    "read-stale": _MYSQL_L2_MODE_READ_STALE,
    "allow_stale": _MYSQL_L2_MODE_READ_STALE,
    "allow-stale": _MYSQL_L2_MODE_READ_STALE,
}
_SERIOUS_L2_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    errno.ENOSPC,
    errno.EROFS,
}
if hasattr(errno, "EIO"):
    _SERIOUS_L2_ERRNOS.add(errno.EIO)


def _mysql_l2_mode_from_env() -> str:
    raw = str(os.environ.get("PRICE_L2_MYSQL_MODE") or _MYSQL_L2_MODE_OFF).strip().lower()
    return _MYSQL_L2_MODE_ALIASES.get(raw, _MYSQL_L2_MODE_OFF)


def _safe_l2_path_part(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "default"
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)


def _default_mysql_l2_cache_dir(cfg, *, db_name: str) -> Path:
    configured = str(os.environ.get("PRICE_L2_MYSQL_CACHE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    base_dir = default_price_l2_cache_dir(cfg).parent
    return base_dir / "mysql_ohlcv" / _safe_l2_path_part(db_name)


def _build_local_l2_cache_bundle(
    cfg,
    *,
    config_loader: ConfigLoader | None = None,
) -> tuple[PriceParquetL2Cache | None, bool, bool]:
    use_l2 = price_l2_cache_enabled_from_env()
    write_l2 = price_l2_cache_write_enabled_from_env(default=use_l2)
    if not use_l2 and not write_l2:
        return None, False, False
    if cfg is None:
        if config_loader is None:
            raise ConfigurationError("data.market_data 需要显式传入 cfg。")
        cfg = config_loader()
    return PriceParquetL2Cache(default_price_l2_cache_dir(cfg)), use_l2, write_l2


def _build_mysql_l2_cache_bundle(
    cfg,
    *,
    db_name: str,
) -> tuple[PriceParquetL2Cache | None, str, bool]:
    mode = _mysql_l2_mode_from_env()
    if mode == _MYSQL_L2_MODE_OFF or not price_l2_cache_enabled_from_env():
        return None, _MYSQL_L2_MODE_OFF, False
    write_l2 = price_l2_cache_write_enabled_from_env(default=True)
    return PriceParquetL2Cache(_default_mysql_l2_cache_dir(cfg, db_name=db_name)), mode, write_l2


def _l2_frame_covers_window(
    df: pd.DataFrame,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> bool:
    if not start_date or not end_date or "trade_date" not in df.columns or df.empty:
        return False
    try:
        trade_dates = _coerce_trade_date_series(df["trade_date"])
        start_ts = _parse_date_yyyymmdd_or_iso(start_date)
        end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    except Exception:
        return False
    if start_ts is None or end_ts is None or trade_dates.isna().any():
        return False
    return bool(trade_dates.min() <= start_ts and trade_dates.max() >= end_ts)


def _is_serious_l2_os_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in _SERIOUS_L2_ERRNOS


def _handle_l2_read_error(exc: Exception, *, symbol_code: str, operation: str) -> None:
    if _is_serious_l2_os_error(exc):
        logger.error("L2 cache %s failed for %s: %s", operation, symbol_code, exc)
        raise PriceCacheError(f"{operation} L2 缓存读取失败 {symbol_code}: {exc}") from exc
    logger.warning("L2 cache %s failed for %s; falling back: %s", operation, symbol_code, exc)
    logger.debug("L2 cache %s traceback for %s", operation, symbol_code, exc_info=True)


def _handle_l2_write_error(exc: Exception, *, symbol_code: str, operation: str) -> None:
    if _is_serious_l2_os_error(exc):
        logger.error("L2 cache %s write failed for %s; skipped: %s", operation, symbol_code, exc)
    else:
        logger.warning("L2 cache %s write failed for %s; skipped: %s", operation, symbol_code, exc)
    logger.debug("L2 cache %s write traceback for %s", operation, symbol_code, exc_info=True)


def _slice_l2_price_window(
    df: pd.DataFrame,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    if "trade_date" not in df.columns:
        raise DataValidationError("Parquet L2 行情缺少 trade_date 列")
    out = df.copy()
    trade_dates = _coerce_trade_date_series(out["trade_date"])
    if trade_dates.isna().any():
        bad = out.loc[trade_dates.isna()].head(5).to_dict(orient="records")
        raise DataValidationError(f"Parquet L2 行情 trade_date 存在 NaT: {bad}")

    start_ts = _parse_date_yyyymmdd_or_iso(start_date)
    end_ts = _parse_date_yyyymmdd_or_iso(end_date)
    if start_ts is not None:
        mask = trade_dates.ge(start_ts)
        if not bool(mask.all()):
            out = out.loc[mask].copy()
            trade_dates = trade_dates.loc[mask]
    if end_ts is not None:
        mask = trade_dates.le(end_ts)
        if not bool(mask.all()):
            out = out.loc[mask].copy()
            trade_dates = trade_dates.loc[mask]
    out["trade_date"] = trade_dates
    if not trade_dates.is_monotonic_increasing:
        out.sort_values(by="trade_date", kind="mergesort", inplace=True)
    return out.reset_index(drop=True)


def _try_read_local_ohlcv_from_l2(
    symbol_code: str,
    *,
    l2_cache: PriceParquetL2Cache | None,
    source_path: Path | None,
    start_date: Optional[str],
    end_date: Optional[str],
    base_cols: list[str],
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> pd.DataFrame | None:
    if l2_cache is None:
        return None
    try:
        cached = l2_cache.read_if_fresh(symbol_code, source_csv_path=source_path)
    except Exception as exc:
        _handle_l2_read_error(exc, symbol_code=symbol_code, operation="local_ohlcv")
        return None
    if cached is None:
        return None
    try:
        df = _slice_l2_price_window(cached.frame, start_date=start_date, end_date=end_date)
        return _finalize_ohlcv_frame(
            df,
            base_cols,
            incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        )
    except IncompleteOhlcvDataError:
        raise
    except Exception as exc:
        _handle_l2_read_error(exc, symbol_code=symbol_code, operation="local_ohlcv_normalize")
        return None


def _write_local_ohlcv_to_l2(
    symbol_code: str,
    *,
    local_dir: Path,
    l2_cache: PriceParquetL2Cache | None,
    source_path: Path | None,
) -> None:
    if l2_cache is None or source_path is None:
        return
    try:
        cache_df = _read_local_daily(
            local_dir,
            symbol_code,
            start_date=None,
            end_date=None,
            columns=None,
        )
        if cache_df.empty:
            return
        if "direction" not in cache_df.columns:
            cache_df["direction"] = ""
        normalized = normalize_price_frame(cache_df, stock_code=symbol_code, source=str(source_path))
        l2_cache.write(symbol_code, normalized, source_csv_path=source_path)
    except Exception as exc:
        _handle_l2_write_error(exc, symbol_code=symbol_code, operation="local_ohlcv")


def _try_read_mysql_ohlcv_from_l2(
    symbol_code: str,
    *,
    l2_cache: PriceParquetL2Cache | None,
    mode: str,
    start_date: Optional[str],
    end_date: Optional[str],
    base_cols: list[str],
    incomplete_ohlcv_policy: str = DEFAULT_INCOMPLETE_OHLCV_POLICY,
) -> pd.DataFrame | None:
    if l2_cache is None or mode != _MYSQL_L2_MODE_READ_STALE:
        return None
    try:
        cached = l2_cache.read_if_fresh(symbol_code, source_csv_path=None)
    except Exception as exc:
        _handle_l2_read_error(exc, symbol_code=symbol_code, operation="mysql_ohlcv")
        return None
    if cached is None or not _l2_frame_covers_window(
        cached.frame,
        start_date=start_date,
        end_date=end_date,
    ):
        return None
    try:
        df = _slice_l2_price_window(cached.frame, start_date=start_date, end_date=end_date)
        return _finalize_ohlcv_frame(
            df,
            base_cols,
            incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        )
    except IncompleteOhlcvDataError:
        raise
    except Exception as exc:
        _handle_l2_read_error(exc, symbol_code=symbol_code, operation="mysql_ohlcv_normalize")
        return None


def _write_mysql_ohlcv_to_l2(
    symbol_code: str,
    df: pd.DataFrame,
    *,
    l2_cache: PriceParquetL2Cache | None,
    write_l2: bool,
) -> None:
    if not write_l2 or l2_cache is None or df.empty:
        return
    try:
        cache_df = df.copy()
        if "direction" not in cache_df.columns:
            cache_df["direction"] = ""
        normalized = normalize_price_frame(cache_df, stock_code=symbol_code, source="mysql_ohlcv")
        l2_cache.write(symbol_code, normalized, source_csv_path=None)
    except Exception as exc:
        _handle_l2_write_error(exc, symbol_code=symbol_code, operation="mysql_ohlcv")


__all__ = [
    "_build_local_l2_cache_bundle",
    "_build_mysql_l2_cache_bundle",
    "_try_read_local_ohlcv_from_l2",
    "_try_read_mysql_ohlcv_from_l2",
    "_write_local_ohlcv_to_l2",
    "_write_mysql_ohlcv_to_l2",
]
