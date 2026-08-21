"""
运行时数据访问 facade。

本包只保留主回测/因子链所需的数据读取、交易日历与股票池辅助能力。
研究策略、参数搜索脚本与因子研究辅助模块已从主项目移除。
"""
from __future__ import annotations

_EXPORTS: dict[str, str] = {
    "TableNotFoundError": ".market_data",
    "PANEL_SOURCE_PARQUET": ".market_panel",
    "PanelPreloadResult": ".market_panel",
    "compute_trade_date_offset": ".trade_calendar",
    "get_all_stocks": ".universe_utils",
    "get_all_board_codes": ".timing_readers",
    "get_board_members": ".timing_readers",
    "get_trade_calendar": ".trade_calendar",
    "get_trade_days_cached": ".trade_calendar",
    "preload_ohlcv_frames": ".market_data",
    "preload_panel_ohlcv_frames": ".market_panel",
    "preload_panel_ohlcv_result": ".market_panel",
    "probe_ohlcv_availability": ".market_data",
    "panel_path_from_env": ".market_panel",
    "panel_source_from_env": ".market_panel",
    "read_daily_from_mysql": ".market_data",
    "read_board_index_for_timing": ".timing_readers",
    "read_board_member_dict": ".timing_readers",
    "read_index_for_timing": ".timing_readers",
    "read_ohlcv_from_mysql": ".market_data",
    "read_price_panel": ".market_panel",
    "read_price_panel_from_parquet": ".market_panel",
    "read_thsindex_daily_from_mysql": ".market_data",
    "read_thsindex_member_from_mysql": ".market_data",
    "select_time": ".universe_utils",
    "split_panel_by_stock": ".market_panel",
    "_get_mysql_engine": ".mysql_support",
    "_is_access_denied": ".mysql_support",
    "_load_mysql_cfg_from_files": ".mysql_support",
    "_parse_date_yyyymmdd_or_iso": ".trade_calendar",
    "_quote_sql_identifier": ".mysql_support",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
