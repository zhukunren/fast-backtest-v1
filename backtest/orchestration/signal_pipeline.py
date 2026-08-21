"""Panel-native signal decision pipeline; engines never call strategy code."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from backtest.context import BacktestContext
from backtest.panel_native import (
    PanelView,
    build_selection,
)
from backtest.pipeline_result import SignalPipelineResult
from backtest.trade_calendar import normalize_trade_days_index
from core.exceptions import DataValidationError
from core.logging import log_warn

from .timing import StageTimer


@dataclass(frozen=True, slots=True)
class _SignalPipelineDeps:
    run_backtest_all_stocks: Callable[..., object]


def validate_panel_signal_context(ctx: BacktestContext) -> None:
    descriptor = ctx.strategy_descriptor
    if descriptor is None:
        raise DataValidationError(
            "panel signal run requires the loaded strategy descriptor"
        )
    strategy = ctx.strategy
    if strategy is None:
        raise DataValidationError("panel signal run requires an explicit strategy")
    if getattr(descriptor, "strategy", None) is not strategy:
        raise DataValidationError(
            "panel signal run requires descriptor and context to own the same strategy"
        )
    missing_hooks = [
        name
        for name in ("compute_features", "compute_signals")
        if not callable(getattr(strategy, name, None))
    ]
    if missing_hooks:
        raise DataValidationError(
            "unsupported panel contract; required callable hooks: "
            + ", ".join(missing_hooks)
        )


def _load_signal_pipeline_deps() -> _SignalPipelineDeps:
    from backtest.engine_batch import run_backtest_all_stocks

    return _SignalPipelineDeps(
        run_backtest_all_stocks=run_backtest_all_stocks,
    )


class SignalDecisionPipeline:
    def __init__(
        self,
        *,
        ctx: BacktestContext,
        start_time: str,
        end_time: str,
        timer: StageTimer,
    ) -> None:
        validate_panel_signal_context(ctx)
        self.ctx = ctx
        self.start_time = start_time
        self.end_time = end_time
        self.timer = timer
        self.logger = ctx.logger

    def _trade_days(self) -> pd.DatetimeIndex:
        from backtest.signal.inputs import get_trade_days

        cfg = self.ctx.cfg
        days = get_trade_days(
            self.start_time,
            self.end_time,
            exchange=cfg.tushare.exchange or "",
            cfg=cfg,
            calendar_cache=getattr(self.ctx, "trade_calendar_cache", None),
        )
        return normalize_trade_days_index(days, name="trade_days")

    def _ensure_index_timing(self, batch) -> None:
        spec = self.ctx.spec
        if not bool(getattr(spec, "use_index_timing", False)):
            return
        timing_cache = getattr(self.ctx, "timing_cache", None)
        if timing_cache is None:
            from core.market_timing import create_index_timing_cache

            timing_cache = create_index_timing_cache()
        from backtest.panel_native.filters import resolve_index_timing_mask

        mask = resolve_index_timing_mask(
            batch.price_panel,
            batch.column_store,
            strategy=self.ctx.strategy,
            cfg=self.ctx.cfg,
            timing_cache=timing_cache,
        )
        if "time_ok" not in PanelView(batch.price_panel, batch.column_store).column_names:
            batch.column_store.register(
                "time_ok",
                mask,
                owner="framework_timing",
            )

    def _board_keep_mask(self, batch) -> np.ndarray | None:
        spec = self.ctx.spec
        if not bool(
            getattr(spec, "use_board_timing", False)
            or getattr(spec, "require_board_member", False)
        ):
            return None
        from backtest.signal.board_cache import create_board_time_cache
        from backtest.panel_native.filters import resolve_board_keep_mask
        from data.timing_readers import create_board_member_cache
        board_time_cache = getattr(self.ctx, "board_time_cache", None)
        if board_time_cache is None and bool(getattr(spec, "use_board_timing", False)):
            board_time_cache = create_board_time_cache()
        board_member_cache = getattr(self.ctx, "board_member_cache", None)
        if board_member_cache is None:
            board_member_cache = create_board_member_cache()
        return resolve_board_keep_mask(
            batch.price_panel,
            batch.column_store,
            spec=spec,
            strategy=self.ctx.strategy,
            cfg=self.ctx.cfg,
            board_time_cache=board_time_cache,
            board_member_cache=board_member_cache,
            logger=self.logger,
        )

    def _evaluation_window(self, batch) -> tuple[pd.Timestamp, pd.Timestamp]:
        if batch.resolved_window is not None:
            return (
                pd.Timestamp(batch.resolved_window[2]).normalize(),
                pd.Timestamp(batch.resolved_window[3]).normalize(),
            )
        return (
            pd.to_datetime(self.start_time, format="%Y%m%d", errors="raise").normalize(),
            pd.to_datetime(self.end_time, format="%Y%m%d", errors="raise").normalize(),
        )

    def _empty_result(
        self,
        *,
        warning: str,
        event: str | None,
        log_empty_info: bool,
        batch,
        selection,
        trade_days: pd.DatetimeIndex,
    ) -> SignalPipelineResult:
        log_warn(warning, self.logger)
        if log_empty_info and event and self.logger is not None:
            self.logger.info("%s run_id=%s", event, self.ctx.run_id)
        return SignalPipelineResult(
            selection=selection,
            price_panel=batch.price_panel,
            column_store=batch.column_store,
            trade_days=trade_days,
            diagnostics=dict(selection.diagnostics),
        )

    def run(
        self,
        *,
        no_signal_warning: str,
        topn_empty_warning: str,
        log_empty_info: bool,
    ) -> SignalPipelineResult:
        deps = _load_signal_pipeline_deps()
        with self.timer.measure("backtest_all_stocks"):
            batch = deps.run_backtest_all_stocks(
                self.start_time,
                self.end_time,
                execution_policy=self.ctx.execution_policy,
                compute_metrics_override=False,
                ctx=self.ctx,
            )
        self.timer.add_many(getattr(batch, "stage_timings_sec", None))
        self._ensure_index_timing(batch)
        board_keep = self._board_keep_mask(batch)
        trade_days = self._trade_days()
        start_eval, end_eval = self._evaluation_window(batch)
        with self.timer.measure("panel_selection"):
            selection = build_selection(
                batch.price_panel,
                batch.column_store,
                buy_raw=batch.buy_raw,
                sell_raw=batch.sell_raw,
                spec=self.ctx.spec,
                cfg=self.ctx.cfg,
                execution_policy=self.ctx.execution_policy,
                start_eval=start_eval,
                end_eval=end_eval,
                trade_days=trade_days,
                board_keep_mask=board_keep,
            )
        if not len(selection.buy_rows):
            return self._empty_result(
                warning=no_signal_warning,
                event="no_signal",
                log_empty_info=log_empty_info,
                batch=batch,
                selection=selection,
                trade_days=trade_days,
            )
        if not len(selection.selected_rows):
            return self._empty_result(
                warning=topn_empty_warning,
                event="top10_empty",
                log_empty_info=log_empty_info,
                batch=batch,
                selection=selection,
                trade_days=trade_days,
            )
        return SignalPipelineResult(
            selection=selection,
            price_panel=batch.price_panel,
            column_store=batch.column_store,
            trade_days=trade_days,
            diagnostics=dict(selection.diagnostics),
        )


__all__ = ["SignalDecisionPipeline", "validate_panel_signal_context"]
