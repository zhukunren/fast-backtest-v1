"""Stable facade for market-data readers."""
from __future__ import annotations

from typing import Callable, Iterable, Optional, TypeVar

import pandas as pd

from core.exceptions import (  # noqa: F401 - compatibility exports
    ConfigurationError,
    DataSourceError,
    DataValidationError,
    PriceDataError,
)

from . import market_data_frames as _market_data_frames
from . import market_data_mysql as _market_data_mysql
from .local_source import (
    _get_local_data_dir,
    _probe_local_ohlcv_availability,
    _read_local_daily,
    _resolve_local_price_path,
)
from .market_data_frames import (
    _build_trade_date_query_window,
    _finalize_ohlcv_frame,
    _finalize_ths_index_daily_frame,
    _mysql_daily_db_name,
    _resolve_ohlcv_base_columns,
    _thsindex_member_table_name,
)
from .incomplete_ohlcv import configured_incomplete_ohlcv_policy
from .market_data_l2 import (
    _build_local_l2_cache_bundle,
    _build_mysql_l2_cache_bundle,
    _try_read_local_ohlcv_from_l2,
    _try_read_mysql_ohlcv_from_l2,
    _write_local_ohlcv_to_l2,
    _write_mysql_ohlcv_to_l2,
)
from .mysql_support import TableNotFoundError, _get_mysql_engine


ConfigLoader = Callable[[], object]
_ItemT = TypeVar("_ItemT")
_MysqlReadContext = _market_data_mysql._MysqlReadContext
_read_mysql_query_frame = _market_data_mysql._read_mysql_query_frame


def __getattr__(name: str):
    if name.startswith("_"):
        for module in (_market_data_frames, _market_data_mysql):
            if hasattr(module, name):
                return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resolve_cfg(*, cfg, config_loader: ConfigLoader | None = None):
    if cfg is not None:
        return cfg
    if config_loader is not None:
        return config_loader()
    raise ConfigurationError("data.market_data 需要显式传入 cfg。")


def _try_read_local_market_daily(
    symbol_code: str,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    columns: Optional[list[str]],
) -> pd.DataFrame | None:
    local_dir = _get_local_data_dir()
    if local_dir is None:
        return None
    return _read_local_daily(
        local_dir,
        symbol_code,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
    )


def _try_read_local_thsindex_daily(
    ths_index_code: str,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    columns: Optional[list[str]],
) -> pd.DataFrame | None:
    local_df = _try_read_local_market_daily(
        ths_index_code,
        start_date=start_date,
        end_date=end_date,
        columns=None,
    )
    if local_df is None:
        return None
    return _finalize_ths_index_daily_frame(local_df, columns)


def _try_read_local_ohlcv_frame(
    symbol_code: str,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    base_cols: list[str],
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame | None:
    incomplete_ohlcv_policy = configured_incomplete_ohlcv_policy(cfg)
    local_dir = _get_local_data_dir()
    if local_dir is None:
        return None
    source_path = _resolve_local_price_path(local_dir, symbol_code)
    l2_cache, use_l2, write_l2 = _build_local_l2_cache_bundle(
        cfg,
        config_loader=config_loader,
    )
    if use_l2:
        l2_df = _try_read_local_ohlcv_from_l2(
            symbol_code,
            l2_cache=l2_cache,
            source_path=source_path,
            start_date=start_date,
            end_date=end_date,
            base_cols=base_cols,
            incomplete_ohlcv_policy=incomplete_ohlcv_policy,
        )
        if l2_df is not None:
            return l2_df

    local_df = _read_local_daily(
        local_dir,
        symbol_code,
        start_date=start_date,
        end_date=end_date,
        columns=None,
    )
    if local_df is None:
        return None
    if write_l2:
        _write_local_ohlcv_to_l2(
            symbol_code,
            local_dir=local_dir,
            l2_cache=l2_cache,
            source_path=source_path,
        )
    return _finalize_ohlcv_frame(
        local_df,
        base_cols,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )


def _require_mysql_engine(
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
):
    return _market_data_mysql._require_mysql_engine(
        context,
        cfg=cfg,
        config_loader=config_loader,
        engine_loader=_get_mysql_engine,
    )


def _build_mysql_read_runtime(
    context: _MysqlReadContext,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
):
    return _market_data_mysql._build_mysql_read_runtime(
        context,
        cfg=cfg,
        config_loader=config_loader,
        require_engine=_require_mysql_engine,
        window_builder=_build_trade_date_query_window,
    )


def _iter_unique_nonempty_symbol_codes(symbol_codes: Iterable[object]) -> Iterable[str]:
    seen: set[str] = set()
    for raw_code in symbol_codes:
        code = str(raw_code or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        yield code


def _build_preload_ohlcv_iterator(
    symbol_codes: Iterable[_ItemT],
    *,
    progress_desc: str | None,
    show_progress: bool,
) -> Iterable[_ItemT]:
    if not show_progress:
        return symbol_codes

    from tqdm import tqdm

    return tqdm(symbol_codes, desc=progress_desc or "Preload", unit="stock", dynamic_ncols=True)


def read_daily_from_mysql(
    symbol_code: str,
    symbol_type: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Optional[list[str]] = None,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    """Read daily bars from MySQL/local source for a stock, index, or ETF."""
    local_df = _try_read_local_market_daily(
        symbol_code,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
    )
    if local_df is not None:
        return local_df

    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    context = _MysqlReadContext(
        db_name=_mysql_daily_db_name(cfg, symbol_type),
        code_label="symbol_code",
        code_value=symbol_code,
        start_date=start_date,
        end_date=end_date,
    )
    return _market_data_mysql._read_daily_mysql(
        context,
        columns=columns,
        cfg=cfg,
        config_loader=config_loader,
        runtime_builder=_build_mysql_read_runtime,
        query_reader=_read_mysql_query_frame,
    )


def read_thsindex_daily_from_mysql(
    ths_index_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Optional[list[str]] = None,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    """Read THS index daily bars from MySQL/local source."""
    local_df = _try_read_local_thsindex_daily(
        ths_index_code,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
    )
    if local_df is not None:
        return local_df

    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    context = _MysqlReadContext(
        db_name=cfg.mysql.ths_index_db,
        code_label="ths_index_code",
        code_value=ths_index_code,
        start_date=start_date,
        end_date=end_date,
    )
    return _market_data_mysql._read_thsindex_daily_mysql(
        context,
        columns=columns,
        cfg=cfg,
        config_loader=config_loader,
        runtime_builder=_build_mysql_read_runtime,
        query_reader=_read_mysql_query_frame,
    )


def read_thsindex_member_from_mysql(
    ths_index_code: str,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    context = _MysqlReadContext(
        db_name=cfg.mysql.ths_index_member_db,
        code_label="ths_index_code",
        code_value=ths_index_code,
        start_date=None,
        end_date=None,
    )
    return _market_data_mysql._read_thsindex_member_mysql(
        context,
        cfg=cfg,
        config_loader=config_loader,
        require_engine=_require_mysql_engine,
    )


def read_ohlcv_from_mysql(
    symbol_code: str,
    symbol_type: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> pd.DataFrame:
    """Read the minimum OHLCV columns required by strategies."""
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    base_cols = _resolve_ohlcv_base_columns(cfg)
    incomplete_ohlcv_policy = configured_incomplete_ohlcv_policy(cfg)

    local_df = _try_read_local_ohlcv_frame(
        symbol_code,
        start_date=start_date,
        end_date=end_date,
        base_cols=base_cols,
        cfg=cfg,
        config_loader=config_loader,
    )
    if local_df is not None:
        return local_df

    db_name = _mysql_daily_db_name(cfg, symbol_type)
    l2_cache, l2_mode, write_l2 = _build_mysql_l2_cache_bundle(cfg, db_name=db_name)
    l2_df = _try_read_mysql_ohlcv_from_l2(
        symbol_code,
        l2_cache=l2_cache,
        mode=l2_mode,
        start_date=start_date,
        end_date=end_date,
        base_cols=base_cols,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )
    if l2_df is not None:
        return l2_df

    context = _MysqlReadContext(
        db_name=db_name,
        code_label="symbol_code",
        code_value=symbol_code,
        start_date=start_date,
        end_date=end_date,
    )
    df = _market_data_mysql._probe_ohlcv_mysql_candidates(
        context,
        symbol_type=symbol_type,
        base_cols=base_cols,
        cfg=cfg,
        daily_reader=read_daily_from_mysql,
    )
    out = _finalize_ohlcv_frame(
        df,
        base_cols,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )
    _write_mysql_ohlcv_to_l2(
        symbol_code,
        out,
        l2_cache=l2_cache,
        write_l2=write_l2,
    )
    return out


def probe_ohlcv_availability(
    symbol_codes: Iterable[str],
    symbol_type: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> dict[str, bool]:
    """Return whether each symbol has a price row in the requested window."""
    symbols = tuple(_iter_unique_nonempty_symbol_codes(symbol_codes))
    if not symbols:
        return {}
    if _get_local_data_dir() is not None:
        return _probe_local_ohlcv_availability(
            symbols,
            start_date=start_date,
            end_date=end_date,
        )

    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    context = _MysqlReadContext(
        db_name=_mysql_daily_db_name(cfg, symbol_type),
        code_label="symbol_code",
        code_value=",".join(symbols[:5]),
        start_date=start_date,
        end_date=end_date,
    )
    return _market_data_mysql._probe_mysql_ohlcv_availability(
        symbols,
        context,
        cfg=cfg,
        config_loader=config_loader,
        runtime_builder=_build_mysql_read_runtime,
        query_reader=_read_mysql_query_frame,
    )


def preload_ohlcv_frames(
    symbol_codes: Iterable[str],
    symbol_type: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
    allow_missing: bool = True,
    progress_desc: str | None = None,
    show_progress: bool = False,
) -> dict[str, pd.DataFrame]:
    """Preload de-duplicated OHLCV frames for a batch session."""
    requested = tuple(_iter_unique_nonempty_symbol_codes(symbol_codes))
    normalized_symbol_type = str(symbol_type or "stock").strip().lower()
    if normalized_symbol_type == "stock" and _get_local_data_dir() is None:
        from .market_panel import panel_source_from_env, preload_panel_ohlcv_frames

        if panel_source_from_env(cfg) == "parquet":
            return preload_panel_ohlcv_frames(
                requested,
                start_date=start_date,
                end_date=end_date,
                cfg=cfg,
                config_loader=config_loader,
            )
    cache: dict[str, pd.DataFrame] = {}
    iterator = _build_preload_ohlcv_iterator(
        requested,
        progress_desc=progress_desc,
        show_progress=show_progress,
    )
    for code in _iter_unique_nonempty_symbol_codes(iterator):
        frame = _read_preload_ohlcv_frame(
            code,
            symbol_type=symbol_type,
            start_date=start_date,
            end_date=end_date,
            cfg=cfg,
            config_loader=config_loader,
            allow_missing=allow_missing,
        )
        if frame is not None:
            cache[code] = frame
    return cache


def _read_preload_ohlcv_frame(
    symbol_code: str,
    *,
    symbol_type: str,
    start_date: Optional[str],
    end_date: Optional[str],
    cfg,
    config_loader: ConfigLoader | None = None,
    allow_missing: bool,
) -> pd.DataFrame | None:
    try:
        return read_ohlcv_from_mysql(
            symbol_code,
            symbol_type=symbol_type,
            start_date=start_date,
            end_date=end_date,
            cfg=cfg,
            config_loader=config_loader,
        )
    except (FileNotFoundError, PriceDataError):
        if not allow_missing:
            raise
        return None


__all__ = [
    "TableNotFoundError",
    "_thsindex_member_table_name",
    "preload_ohlcv_frames",
    "probe_ohlcv_availability",
    "read_daily_from_mysql",
    "read_ohlcv_from_mysql",
    "read_thsindex_daily_from_mysql",
    "read_thsindex_member_from_mysql",
]
