"""Array-aligned index and board timing filters for the sealed panel."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.exceptions import DataValidationError
from core.logging import log_warn
from core.market_timing import build_timing_cache_scope
from core.timing_flags import coerce_timing_flag_series
from data.timing_readers import get_all_board_codes, get_board_members
from strategies.spec import validate_board_timing_config

from backtest.signal.board_cache import (
    build_board_time_cache_scope,
    get_board_time_series_cached,
)

from core.panel_native import ColumnStore, PanelView, PricePanel


def _readonly_bool(values: np.ndarray) -> np.ndarray:
    out = np.asarray(values, dtype=bool)
    if not out.flags.c_contiguous:
        out = np.ascontiguousarray(out)
    out.setflags(write=False)
    return out


def _coerce_flag_array(values: np.ndarray, *, name: str) -> np.ndarray:
    series = coerce_timing_flag_series(
        pd.Series(values, copy=False),
        column_name=name,
        error_cls=DataValidationError,
    )
    return _readonly_bool(series.to_numpy(dtype=bool, copy=False))


def _align_date_series(series: pd.Series, dates: np.ndarray) -> np.ndarray:
    normalized = series.copy()
    normalized.index = pd.DatetimeIndex(normalized.index).normalize()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    aligned = normalized.reindex(
        pd.DatetimeIndex(dates),
        fill_value=False,
    )
    return _readonly_bool(aligned.to_numpy(dtype=bool, copy=False))


def resolve_index_timing_mask(
    panel: PricePanel,
    store: ColumnStore,
    *,
    strategy,
    cfg,
    timing_cache,
) -> np.ndarray:
    view = PanelView(panel, store)
    if "time_ok" in view.column_names:
        return _coerce_flag_array(view.column("time_ok"), name="time_ok")
    build_time_ok = getattr(strategy, "build_time_ok", None)
    if not callable(build_time_ok):
        raise AttributeError("策略模块缺少 build_time_ok，无法应用指数择时过滤。")
    if timing_cache is None:
        raise DataValidationError("指数择时过滤需要显式传入 timing_cache。")
    timing_frame = timing_cache.get_or_build(
        build_time_ok,
        "",
        cache_key_scope=build_timing_cache_scope(cfg),
    )
    series = pd.Series(
        timing_frame["time_ok"].to_numpy(dtype=bool, copy=False),
        index=timing_frame["trade_date"],
    )
    return _align_date_series(series, panel.index.trade_dates)


def resolve_board_keep_mask(
    panel: PricePanel,
    store: ColumnStore,
    *,
    spec,
    strategy,
    cfg,
    board_time_cache,
    board_member_cache,
    logger=None,
) -> np.ndarray:
    use_timing = bool(getattr(spec, "use_board_timing", False))
    require_member = bool(getattr(spec, "require_board_member", False))
    board_codes = tuple(getattr(spec, "board_index_codes", ()) or ())
    validate_board_timing_config(
        use_board_timing=use_timing,
        board_index_codes=board_codes,
        require_board_member=require_member,
    )
    if not board_codes and (use_timing or require_member):
        if board_member_cache is None:
            raise DataValidationError("板块过滤需要显式传入 board_member_cache。")
        board_codes = get_all_board_codes(cache=board_member_cache)
    if not board_codes:
        return _readonly_bool(np.ones(panel.row_count, dtype=bool))

    view = PanelView(panel, store)
    has_precomputed = "board_time_ok" in view.column_names
    reuse_precomputed = has_precomputed and len(board_codes) <= 1
    precomputed = (
        _coerce_flag_array(view.column("board_time_ok"), name="board_time_ok")
        if reuse_precomputed
        else None
    )
    build_board_time_ok = getattr(strategy, "build_board_time_ok", None)
    if use_timing and not reuse_precomputed and not callable(build_board_time_ok):
        if has_precomputed:
            raise DataValidationError(
                "多板块配置不能复用单列 board_time_ok，且策略缺少 build_board_time_ok。"
            )
        raise AttributeError("策略模块缺少 build_board_time_ok，无法应用板块择时。")
    if require_member and board_member_cache is None:
        raise DataValidationError("板块成分过滤需要显式传入 board_member_cache。")
    if use_timing and not reuse_precomputed and board_time_cache is None:
        raise DataValidationError("板块择时过滤需要显式传入 board_time_cache。")

    dates = panel.index.trade_dates
    stock_codes = np.asarray(
        [str(code).strip().upper() for code in panel.stock_code_by_row],
        dtype=object,
    )
    keep = np.zeros(panel.row_count, dtype=bool)
    failures: list[str] = []
    succeeded = 0
    cache_scope = build_board_time_cache_scope(cfg)
    for code in board_codes:
        try:
            member_mask = (
                np.isin(
                    stock_codes,
                    tuple(sorted(get_board_members(code, cache=board_member_cache), key=str.casefold)),
                )
                if require_member
                else np.ones(panel.row_count, dtype=bool)
            )
            if use_timing:
                if precomputed is not None:
                    time_mask = precomputed
                else:
                    series = get_board_time_series_cached(
                        build_board_time_ok,
                        code,
                        cache=board_time_cache,
                        cache_key_scope=cache_scope,
                    )
                    time_mask = _align_date_series(series, dates)
            else:
                time_mask = np.ones(panel.row_count, dtype=bool)
            keep |= member_mask & time_mask
            succeeded += 1
            if bool(keep.all()):
                break
        except Exception as exc:
            failures.append(f"{code}: {exc}")
            log_warn(f"board_filter_skip: {code}: {exc}", logger)

    if succeeded == 0:
        raise DataValidationError(
            "所有板块代码均无法生成有效过滤结果；"
            f"共尝试 {len(board_codes)} 个板块，失败示例：{failures[:3]}"
        )
    if failures:
        log_warn(
            f"板块过滤部分失败：成功 {succeeded} / {len(board_codes)}；"
            f"失败示例：{failures[:3]}",
            logger,
        )
    return _readonly_bool(keep)


__all__ = ["resolve_board_keep_mask", "resolve_index_timing_mask"]
