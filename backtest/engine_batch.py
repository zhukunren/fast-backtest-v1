"""Panel-only batch signal execution."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from backtest.panel_native import (
    ColumnStore,
    PanelView,
    PricePanel,
)
from strategies.market_features import apply_cross_section_transforms, apply_market_features
from core.exceptions import ConfigurationError, DataValidationError
from core.logging import log_info, log_warn
from core.universe import UniverseResolution, build_config_universe_spec
from data.trade_calendar import compute_trade_date_offset

from .engine_batch_support import (
    _backtest_stock_code_list,
    _load_stock_industry_map,
    _should_attach_industry_to_signals,
    build_backtest_universe_df,
    resolve_backtest_universe,
    resolve_backtest_stock_codes,
)
from .pipeline_result import BatchBacktestResult


_PANEL_SOURCES = frozenset({"parquet"})


def panel_source_from_env(cfg=None) -> str:
    from data.market_panel import panel_source_from_env as _impl

    return _impl(cfg)


def panel_path_from_env(*, cfg=None) -> str:
    from data.market_panel import panel_path_from_env as _impl

    return _impl(cfg=cfg)


def preload_panel_ohlcv_result(*args, **kwargs):
    from data.market_panel import preload_panel_ohlcv_result as _impl

    return _impl(*args, **kwargs)


def _record_stage(
    timings: dict[str, float],
    *,
    enabled: bool,
    name: str,
    started: float,
) -> None:
    if enabled:
        timings[name] = timings.get(name, 0.0) + (time.perf_counter() - started)


def _resolve_batch_window(start_time: str, end_time: str, *, cfg, trade_calendar_cache):
    offset_days = int(getattr(cfg.backtest, "offset_days", 0) or 0)
    if offset_days <= 0:
        return None
    try:
        start_read, end_read = compute_trade_date_offset(
            start_time,
            end_time,
            offset_days,
            cfg=cfg,
            exchange=cfg.tushare.exchange,
            cal_start=cfg.tushare.trade_cal_start,
            cal_end=cfg.tushare.trade_cal_end,
            allow_clamp=True,
            stock_code="ALL",
            offset_clamp_log=False,
            calendar_cache=trade_calendar_cache,
        )
        start_eval = pd.to_datetime(start_time, format="%Y%m%d", errors="raise").normalize()
        end_eval = pd.to_datetime(end_time, format="%Y%m%d", errors="raise").normalize()
    except Exception as exc:
        raise ConfigurationError(f"offset_days 总览计算失败: {exc}") from exc
    return start_read, end_read, start_eval, end_eval


def _resolve_batch_universe(ctx, *, cfg) -> tuple[UniverseResolution, pd.DataFrame | None]:
    universe_resolution = getattr(ctx, "universe_resolution", None)
    if universe_resolution is not None:
        return universe_resolution, None
    stock_code_list = getattr(ctx, "stock_code_list", None)
    if stock_code_list is not None:
        return (
            resolve_backtest_universe(
                cfg=cfg,
                stock_code_list=stock_code_list,
                strategy_spec=getattr(ctx, "spec", None),
            ),
            None,
        )

    universe_df = build_backtest_universe_df(cfg=cfg)
    codes = tuple(_backtest_stock_code_list(universe_df))
    return (
        UniverseResolution(
            spec=build_config_universe_spec(
                cfg=cfg,
                strategy_spec=getattr(ctx, "spec", None),
            ),
            stock_code_list=codes,
            source="config.backtest",
            metadata={
                "stock_basic_rows_after_filters": int(len(universe_df)),
                "resolved_count": len(codes),
            },
        ),
        universe_df,
    )


def _validate_panel_context(ctx):
    if ctx is None:
        raise ConfigurationError("run_backtest_all_stocks 需要显式传入 ctx。")
    descriptor = getattr(ctx, "strategy_descriptor", None)
    strategy = getattr(ctx, "strategy", None)
    if descriptor is None or strategy is None:
        raise ConfigurationError("panel batch execution requires strategy and descriptor")
    if getattr(descriptor, "strategy", None) is not strategy:
        raise ConfigurationError(
            "run context strategy must be the strategy owned by strategy_descriptor"
        )
    missing_hooks = [
        name
        for name in ("compute_features", "compute_signals")
        if not callable(getattr(strategy, name, None))
    ]
    if missing_hooks:
        raise ConfigurationError(
            "panel batch execution requires callable hooks: " + ", ".join(missing_hooks)
        )
    return descriptor, strategy


def _apply_strategy_panel_stages(
    panel: PricePanel,
    *,
    descriptor,
    strategy,
    spec,
    profile_timing: bool,
    timings: dict[str, float],
) -> tuple[ColumnStore, np.ndarray, np.ndarray]:
    advanced = descriptor.advanced
    store = ColumnStore(panel)

    started = time.perf_counter()
    feature_batch = strategy.compute_features(PanelView(panel, store))
    store.register_features(feature_batch)
    _record_stage(
        timings,
        enabled=profile_timing,
        name="batch_panel_build_features",
        started=started,
    )

    if bool(getattr(spec, "use_market_features", False)):
        started = time.perf_counter()
        apply_market_features(panel, store, spec=spec)
        _record_stage(
            timings,
            enabled=profile_timing,
            name="batch_panel_prepare_market_features",
            started=started,
        )

    if advanced.cross_section_transforms:
        started = time.perf_counter()
        apply_cross_section_transforms(
            panel,
            store,
            advanced.cross_section_transforms,
        )
        _record_stage(
            timings,
            enabled=profile_timing,
            name="batch_panel_cross_section_transforms",
            started=started,
        )

    started = time.perf_counter()
    signal_batch = strategy.compute_signals(PanelView(panel, store))
    buy_raw, sell_raw = store.register_signals(signal_batch)
    _record_stage(
        timings,
        enabled=profile_timing,
        name="batch_panel_build_signals",
        started=started,
    )
    return store, buy_raw, sell_raw


def run_backtest_all_stocks(
    start_time: str,
    end_time: str,
    *,
    execution_policy=None,
    compute_metrics_override: bool | None = None,
    ctx,
) -> BatchBacktestResult:
    """Build every stock signal from one normalized market panel."""
    descriptor, strategy = _validate_panel_context(ctx)
    cfg = ctx.cfg
    source = panel_source_from_env(cfg)
    if source not in _PANEL_SOURCES:
        raise ConfigurationError(
            "panel batch execution requires panel.source=parquet"
        )

    profile_timing = bool(getattr(cfg.run, "profile_timing", False))
    timings: dict[str, float] = {}
    resolved_window = _resolve_batch_window(
        start_time,
        end_time,
        cfg=cfg,
        trade_calendar_cache=getattr(ctx, "trade_calendar_cache", None),
    )
    start_read = resolved_window[0] if resolved_window is not None else start_time
    end_read = resolved_window[1] if resolved_window is not None else end_time

    universe_resolution, universe_df = _resolve_batch_universe(ctx, cfg=cfg)
    requested_codes = list(universe_resolution.stock_code_list)
    stock_industry_map = None
    if _should_attach_industry_to_signals(cfg):
        stock_industry_map = _load_stock_industry_map(
            cfg=cfg,
            stock_code_list=requested_codes,
            universe_df=universe_df,
        )

    from data.market_panel import read_price_panel

    preload_started = time.perf_counter()
    panel_frame = read_price_panel(
        requested_codes,
        start_date=start_read,
        end_date=end_read,
        columns=getattr(ctx.spec, "price_columns", None),
        cfg=cfg,
    )
    _record_stage(
        timings,
        enabled=profile_timing,
        name="panel_load",
        started=preload_started,
    )
    if panel_frame is None or panel_frame.empty:
        raise DataValidationError(f"panel.source={source} 未读取到任何行情")

    advanced = descriptor.advanced
    if advanced.auxiliary_data or advanced.reference_series:
        from strategies.point_in_time import apply_declared_data_inputs

        started = time.perf_counter()
        panel_frame = apply_declared_data_inputs(
            panel_frame,
            advanced,
            getattr(ctx, "strategy_data_inputs", None),
        )
        _record_stage(
            timings,
            enabled=profile_timing,
            name="panel_align_declared_inputs",
            started=started,
        )

    if stock_industry_map is not None and "industry" not in panel_frame.columns:
        panel_frame = panel_frame.copy()
        panel_frame["industry"] = panel_frame["stock_code"].map(stock_industry_map)

    seal_started = time.perf_counter()
    panel = PricePanel.from_frame(panel_frame)
    _record_stage(
        timings,
        enabled=profile_timing,
        name="panel_seal",
        started=seal_started,
    )
    panel_codes = list(panel.index.stock_codes)
    if not panel_codes:
        raise DataValidationError(f"panel.source={source} 未读取到任何股票")
    missing_codes = sorted(set(requested_codes) - set(panel_codes), key=str.casefold)
    if missing_codes:
        log_warn(
            "panel 未覆盖全部请求股票，已过滤缺行情股票: "
            f"missing={len(missing_codes)} sample={', '.join(missing_codes[:5])}"
        )
    if stock_industry_map is not None:
        stock_industry_map = {
            code: stock_industry_map[code]
            for code in panel_codes
            if code in stock_industry_map
        }

    log_info(
        f"panel.source={source} 已加载批量行情: "
        f"stocks={len(panel_codes)} rows={panel.row_count}"
    )
    column_store, buy_raw, sell_raw = _apply_strategy_panel_stages(
        panel,
        descriptor=descriptor,
        strategy=strategy,
        spec=ctx.spec,
        profile_timing=profile_timing,
        timings=timings,
    )
    return BatchBacktestResult(
        summaries=[],
        price_panel=panel,
        column_store=column_store,
        buy_raw=buy_raw,
        sell_raw=sell_raw,
        resolved_window=resolved_window,
        summary_df=None,
        stage_timings_sec=timings or None,
    )


__all__ = [
    "build_backtest_universe_df",
    "panel_path_from_env",
    "panel_source_from_env",
    "preload_panel_ohlcv_result",
    "resolve_backtest_stock_codes",
    "run_backtest_all_stocks",
]
