"""
Universe and generic time-slice helpers.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable

import pandas as pd

from core.exceptions import ConfigurationError, DataSourceError, DataValidationError

from .local_source import _get_local_data_dir
from .tushare_client import create_tushare_client, redact_tushare_error, resolve_required_tushare_token


ConfigLoader = Callable[[], object]


def _resolve_cfg(*, cfg, config_loader: ConfigLoader | None = None):
    if cfg is not None:
        return cfg
    if config_loader is not None:
        return config_loader()
    raise ConfigurationError("data.universe_utils 需要显式传入 cfg。")


def _promote_local_stock_basic_aliases(data: pd.DataFrame) -> pd.DataFrame:
    if "ts_code" in data.columns and "stock_code" not in data.columns:
        data.rename(columns={"ts_code": "stock_code"}, inplace=True)
    if "name" in data.columns and "stock_name" not in data.columns:
        data.rename(columns={"name": "stock_name"}, inplace=True)
    return data


def _finalize_stock_basic_aliases(data: pd.DataFrame) -> pd.DataFrame:
    data.columns = data.columns.astype(str)
    return _promote_local_stock_basic_aliases(data)


def _normalize_local_stock_basic_frame(data: pd.DataFrame, *, path) -> pd.DataFrame:
    data = data.copy()
    data = _finalize_stock_basic_aliases(data)
    missing = [c for c in ["stock_code", "stock_name"] if c not in data.columns]
    if missing:
        raise DataValidationError(f"本地 stock_basic.csv 缺少列: {missing} file={path}")
    return data


def _parse_select_time_boundary(value):
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(value)
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):
        return pd.to_datetime(s, format="%Y%m%d")
    try:
        ts_val = pd.to_datetime(s, errors="raise")
    except Exception:
        raise DataValidationError(f"无法解析日期: {value!r}") from None
    if pd.isna(ts_val):
        raise DataValidationError(f"无法解析日期: {value!r}")
    return ts_val


def _coerce_select_time_trade_dates(td: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(td):
        return pd.to_datetime(td, errors="raise")

    td_str = td.astype(str).str.strip()
    if td_str.str.fullmatch(r"\d{8}").all():
        return pd.to_datetime(td_str, format="%Y%m%d", errors="raise")
    if td_str.str.fullmatch(r"\d{2}/\d{2}/\d{2}").all():
        return pd.to_datetime(td_str, format="%y/%m/%d", errors="raise")
    return pd.to_datetime(td_str, errors="raise")


def _select_time_from_trade_date_column(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    df2 = df.copy()
    td_dt = _coerce_select_time_trade_dates(df2["trade_date"])

    if pd.isna(td_dt).any():
        bad = df2.loc[pd.isna(td_dt)].head(5)
        sample = bad.to_dict(orient="records")
        raise DataValidationError(f"trade_date 存在无法解析的日期: {sample}")

    df2["trade_date"] = td_dt
    mask = (df2["trade_date"] >= start) & (df2["trade_date"] <= end)
    return df2.loc[mask]


def _select_time_from_datetime_index(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    idx = df.index
    if pd.isna(idx).any():
        raise DataValidationError("索引包含 NaT，无法按时间筛选。")
    return df.loc[(idx >= start) & (idx <= end)]


def _read_local_stock_basic(local_dir) -> pd.DataFrame:
    path = local_dir / "stock_basic.csv"
    if not path.exists():
        raise DataSourceError(f"本地 stock_basic.csv 不存在: {path}")
    return _normalize_local_stock_basic_frame(pd.read_csv(path), path=path)


def _resolve_select_time_window(
    start_time,
    end_time,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = _parse_select_time_boundary(start_time)
    end = _parse_select_time_boundary(end_time)
    if start > end:
        raise DataValidationError(f"start_time 不能晚于 end_time: start_time={start_time!r} end_time={end_time!r}")
    return start, end


def _read_tushare_stock_basic(*, cfg) -> pd.DataFrame:
    token = resolve_required_tushare_token(cfg=cfg)
    pro = create_tushare_client(token)

    try:
        data = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,list_date",
        )
    except Exception as exc:
        message = redact_tushare_error(exc, token=token)
        raise DataSourceError(
            f"Tushare stock_basic 获取失败: token=set err={message}"
        ) from None

    if cfg.performance.debug_stock_list:
        print(data)

    return _finalize_stock_basic_aliases(data)


def _select_time_by_frame_structure(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if "trade_date" in df.columns:
        return _select_time_from_trade_date_column(df, start=start, end=end)

    idx = df.index
    if pd.api.types.is_datetime64_any_dtype(idx):
        return _select_time_from_datetime_index(df, start=start, end=end)

    raise DataValidationError("既不存在 'trade_date' 列，且索引不是 DatetimeIndex，无法按时间筛选。")


def get_all_stocks(*, cfg, config_loader: ConfigLoader | None = None) -> pd.DataFrame:
    """
    Get all A-share stocks from local source or Tushare.
    """
    local_dir = _get_local_data_dir()
    if local_dir is not None:
        return _read_local_stock_basic(local_dir)

    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    return _read_tushare_stock_basic(cfg=cfg)


def select_time(
    df: pd.DataFrame,
    start_time: str = "20230101",
    end_time: str = "20240910",
) -> pd.DataFrame:
    """
    Filter dataframe rows by [start_time, end_time].
    """
    start, end = _resolve_select_time_window(start_time, end_time)
    return _select_time_by_frame_structure(df, start=start, end=end)


__all__ = [
    "_coerce_select_time_trade_dates",
    "_parse_select_time_boundary",
    "_select_time_from_datetime_index",
    "_select_time_from_trade_date_column",
    "get_all_stocks",
    "_normalize_local_stock_basic_frame",
    "select_time",
]
