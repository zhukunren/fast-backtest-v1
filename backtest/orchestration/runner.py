"""Run-mode router and static portfolio-engine composition root."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pandas as pd

from core.exceptions import DataValidationError
from core.logging import log_info, log_metric, log_warn
from strategies.protocol import StrategyLike

from backtest.artifacts import ArtifactPolicy
from backtest.context import BacktestContext, create_backtest_context
from backtest.decisions import DecisionIdentity, SignalDecision, SignalDecisionArtifacts
from backtest.engines import (
    EngineRequest,
    EngineRuntimeConfig,
    EngineServices,
    EngineSpec,
    LedgerAssessmentSource,
    RunSpec,
    SegmentAssessmentSource,
    compose_engine,
    engine_registration,
)
from backtest.pipeline_result import SignalPipelineResult, TradeSegmentsBuildResult
from backtest.result import (
    RUN_RESULT_SCHEMA_VERSION,
    AssessPayload,
    CurvePayload,
    LedgerExecutionArtifacts,
    RebalanceExecutionArtifacts,
    RunDiagnostics,
    RunIdentity,
    RunProvenance,
    RunRequestSummary,
    RunResult,
    SinglePayload,
    VectorizedExecutionArtifacts,
    compute_curve_metrics,
)

from .signal_pipeline import SignalDecisionPipeline, validate_panel_signal_context
from .timing import StageTimer


def _execution_equity_curve(execution) -> pd.DataFrame | None:
    if isinstance(
        execution,
        (VectorizedExecutionArtifacts, LedgerExecutionArtifacts, RebalanceExecutionArtifacts),
    ):
        return execution.equity_curve
    return None


def _execution_trade_summary(execution) -> pd.DataFrame | None:
    if isinstance(execution, (VectorizedExecutionArtifacts, LedgerExecutionArtifacts)):
        return execution.trade_segments_summary_df
    return None


def _build_assess_stock_scope(selection, quote_store) -> tuple[set[str] | None, int]:
    rows = np.asarray(selection.selected_rows, dtype=np.int64)
    if not len(rows):
        return None, 0
    stock_ids = np.unique(quote_store.index.stock_id_by_row[rows])
    codes = {quote_store.stock_codes[int(stock_id)] for stock_id in stock_ids}
    return codes or None, len(codes)


class BacktestOrchestrator:
    def __init__(
        self,
        *,
        ctx: BacktestContext,
        start_time: str,
        end_time: str,
    ) -> None:
        self.ctx = ctx
        self.start_time = start_time
        self.end_time = end_time
        self.cfg = ctx.cfg
        self.mode = str(getattr(self.cfg.run, "run_mode", "curve") or "curve").strip().lower()
        self.engine_id = str(getattr(self.cfg.portfolio, "engine", "legacy") or "legacy").strip().lower()
        self.logger = ctx.logger
        self.output_root = Path(self.cfg.paths.signal_data).parent
        self.write_outputs = self.ctx.artifact_policy.writes_outputs
        self.timer = StageTimer(enabled=bool(self.cfg.run.profile_timing))
        self._engine_registration = None
        self._validate_route()

    def _validate_route(self) -> None:
        if self.mode not in {"single", "curve", "assess"}:
            raise DataValidationError(f"unsupported run mode: {self.mode!r}")
        if self.mode == "single":
            strategy = self.ctx.strategy
            if self.ctx.spec is None or not callable(
                getattr(strategy, "build_single_signals", None)
            ):
                raise DataValidationError(
                    "single run requires strategy spec and callable build_single_signals"
                )
            return
        registration = engine_registration(self.engine_id)
        self._engine_registration = registration
        if self.mode not in registration.supported_modes:
            expected = "/".join(sorted(registration.supported_modes, key=str.casefold))
            raise DataValidationError(f"{self.engine_id} only supports {expected} mode")
        if self.mode != "single" and self.ctx.risk_cfg is None:
            raise DataValidationError("portfolio engine requires explicit risk policy")
        if registration.requires_strategy and self.ctx.spec is None:
            raise DataValidationError("signal portfolio engine requires explicit strategy spec")
        if registration.requires_strategy:
            validate_panel_signal_context(self.ctx)

    def run(self) -> RunResult:
        from backtest.artifacts import output_run_lock

        with output_run_lock(
            self.output_root,
            run_id=self.ctx.run_id,
            purpose="backtest_run",
        ):
            return self._run_locked()

    def _run_locked(self) -> RunResult:
        started_at = time.time()
        self._log_run_header()
        self._write_strategy_contract()
        if self.mode == "single":
            result = self._run_single()
        elif self._engine_registration.decision_kind == "target_weight":
            result = self._run_fixed_rebalance()
        elif self.mode == "assess":
            result = self._run_assess()
        else:
            result = self._run_curve()
        # The live read-only view keeps the writer and caller on the same RunResult instance.
        result = replace(
            result,
            timings_sec=MappingProxyType(self.timer.times),
            duration_sec=time.time() - started_at,
        )
        if self.write_outputs:
            from backtest.artifacts import write_run_result_artifacts
            with self.timer.measure("artifact_projection"):
                write_run_result_artifacts(
                    cfg=self.cfg,
                    result=result,
                    defer_publish=self.ctx.defer_artifact_publish,
                )
            result = replace(
                result,
                timings_sec=MappingProxyType(self.timer.times),
                duration_sec=time.time() - started_at,
            )
        self._print_timing_summary()
        return result

    @staticmethod
    def _quote_store(pipeline: SignalPipelineResult):
        return pipeline.price_panel.quote_store(pipeline.column_store.columns)

    @staticmethod
    def _signal_stock_count(pipeline: SignalPipelineResult) -> int:
        rows = np.asarray(pipeline.selection.buy_rows, dtype=np.int64)
        if not len(rows):
            return 0
        stock_ids = pipeline.price_panel.index.stock_id_by_row[rows]
        return int(len(np.unique(stock_ids)))

    def _project_stock_results(self, pipeline: SignalPipelineResult) -> list[object]:
        from backtest.metrics.engine_metrics import backtest_results
        from backtest.panel_native import (
            build_basic_stock_summaries,
            project_selection_from_quotes,
        )

        selection = pipeline.selection
        quote_store = self._quote_store(pipeline)
        summaries = build_basic_stock_summaries(pipeline.price_panel, selection)
        if not bool(self.cfg.run.compute_metrics):
            return summaries

        output: list[object] = []
        for summary in summaries:
            code = str(summary["stock_code"])
            stock_slice = quote_store.stock_slice(code)
            rows = np.arange(stock_slice.start, stock_slice.stop, dtype=np.int64)
            price_frame = project_selection_from_quotes(
                quote_store,
                selection,
                rows,
            )
            metrics, _trades, _equity = backtest_results(
                price_frame,
                initial_capital=1,
                stock_code=None,
                execution_policy=self.ctx.execution_policy,
                return_details=False,
                cfg=self.cfg,
                trade_state_runtime=self.ctx.trade_state_runtime,
            )
            metrics.update(summary)
            output.append(metrics)
        return output

    def _project_signal_artifacts(
        self,
        pipeline: SignalPipelineResult,
        *,
        topn_segments: TradeSegmentsBuildResult | None = None,
    ) -> SignalDecisionArtifacts:
        if not self.write_outputs:
            return SignalDecisionArtifacts()

        from backtest.panel_native import project_daily_summary, project_selection_frame

        with self.timer.measure("decision_artifact_projection"):
            stock_results = self._project_stock_results(pipeline)
            if bool(self.cfg.run.compute_metrics):
                self._print_backtest_summary_stats(stock_results)
            candidate_factor = float(
                getattr(self.cfg.run, "topn_candidate_factor", 1.0) or 1.0
            )
            return SignalDecisionArtifacts(
                backtest_summary_df=(
                    pd.DataFrame(stock_results) if stock_results else None
                ),
                selected_df=project_selection_frame(
                    pipeline.price_panel,
                    pipeline.column_store,
                    pipeline.selection,
                    pipeline.selection.selected_rows,
                ),
                candidate_df=(
                    project_selection_frame(
                        pipeline.price_panel,
                        pipeline.column_store,
                        pipeline.selection,
                        pipeline.selection.candidate_rows,
                    )
                    if candidate_factor > 1.0
                    else None
                ),
                daily_summary_df=project_daily_summary(
                    pipeline.price_panel,
                    pipeline.selection,
                ),
                topn_segments=topn_segments,
            )

    def _build_topn_artifact_segments(
        self,
        pipeline: SignalPipelineResult,
    ) -> TradeSegmentsBuildResult | None:
        if not (
            self.write_outputs
            and bool(self.cfg.run.build_topn_trade_segments_report)
            and len(pipeline.selection.selected_rows)
        ):
            return None
        from backtest.allocation.trade_extraction import build_all_trade_segments

        with self.timer.measure("build_topn_trade_segments"):
            return build_all_trade_segments(
                pipeline.selection,
                self._quote_store(pipeline),
                execution_policy=self.ctx.execution_policy,
                ctx=self.ctx,
                trade_days=pipeline.trade_days,
            )

    def _base_result(
        self,
        *,
        payload,
        signal_stock_count: int,
        diagnostics: Mapping[str, object] | None = None,
    ) -> RunResult:
        strategy = self.ctx.strategy
        strategy_name = type(strategy).__name__ if strategy is not None else None
        return RunResult(
            schema_version=RUN_RESULT_SCHEMA_VERSION,
            identity=RunIdentity(
                run_id=str(self.ctx.run_id or ""),
                strategy_name=strategy_name,
            ),
            request=RunRequestSummary(
                mode=self.mode,
                engine_id=None if self.mode == "single" else self.engine_id,
                start_time=self.start_time,
                end_time=self.end_time,
                signal_stock_count=int(signal_stock_count),
            ),
            payload=payload,
            diagnostics=RunDiagnostics(values=MappingProxyType(dict(diagnostics or {}))),
            timings_sec=MappingProxyType({}),
            provenance=RunProvenance(),
        )

    def _run_single(self) -> RunResult:
        from backtest.single_runner import run_single_stock

        if self.ctx.spec is None:
            raise DataValidationError("single run requires strategy spec")
        frame = run_single_stock(
            cfg=self.cfg,
            spec=self.ctx.spec,
            start_time=self.start_time,
            end_time=self.end_time,
            strategy=self.ctx.strategy,
            execution_policy=self.ctx.execution_policy,
            write_outputs=self.write_outputs,
            logger=self.logger,
        )
        count = 1 if isinstance(frame, pd.DataFrame) and not frame.empty else 0
        return self._base_result(payload=SinglePayload(frame=frame), signal_stock_count=count)

    def _run_fixed_rebalance(self) -> RunResult:
        from backtest.fixed_rebalance_runner import build_target_weight_decision
        from backtest.signal.inputs import get_trade_days

        with self.timer.measure("decision_build"):
            with self.timer.measure("fixed_rebalance_trade_calendar"):
                trade_days = get_trade_days(
                    self.start_time,
                    self.end_time,
                    exchange=self.cfg.tushare.exchange or "",
                    cfg=self.cfg,
                    calendar_cache=self.ctx.trade_calendar_cache,
                )
            with self.timer.measure("build_target_weight_decision"):
                decision = build_target_weight_decision(
                    cfg=self.cfg,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    trade_days=trade_days,
                    logger=self.logger,
                )
        with self.timer.measure("engine_run"):
            with self.timer.measure("fixed_rebalance"):
                engine_result = compose_engine(self.engine_id).run(self._engine_request(decision))
        with self.timer.measure("result_projection"):
            execution = engine_result.execution
            equity_curve = _execution_equity_curve(execution)
            metrics = compute_curve_metrics(equity_curve)
            self._maybe_plot(equity_curve)
            self._log_curve_metrics(metrics)
            payload = CurvePayload(
                engine_id=self.engine_id,
                decision_artifacts=decision.artifacts,
                equity_curve=equity_curve,
                metrics=metrics,
                execution=execution,
            )
        return self._base_result(
            payload=payload,
            signal_stock_count=len(decision.target_weights),
            diagnostics=engine_result.diagnostics,
        )

    def _run_curve(self) -> RunResult:
        with self.timer.measure("decision_build"):
            pipeline = self._run_signal_pipeline(
                no_signal_warning="本次回测未产生任何信号，跳过后续流程",
                topn_empty_warning="Top10 信号为空，跳过组合净值计算",
                log_empty_info=True,
            )
            signal_stock_count = self._signal_stock_count(pipeline)
            if not len(pipeline.selection.selected_rows):
                artifacts = self._project_signal_artifacts(pipeline)
                payload = CurvePayload(
                    engine_id=self.engine_id,
                    decision_artifacts=artifacts,
                    equity_curve=None,
                    metrics=None,
                    execution=None,
                )
                return self._base_result(
                    payload=payload,
                    signal_stock_count=signal_stock_count,
                )
            decision = self._signal_decision(pipeline)

        with self.timer.measure("engine_run"):
            engine_result = compose_engine(self.engine_id).run(self._engine_request(decision))
        with self.timer.measure("result_projection"):
            execution = engine_result.execution
            equity_curve = _execution_equity_curve(execution)
            metrics = compute_curve_metrics(
                equity_curve,
                trade_segments_summary_df=_execution_trade_summary(execution),
            )
            self._maybe_plot(equity_curve)
            self._log_curve_metrics(metrics)
            topn_segments = self._build_topn_artifact_segments(pipeline)
            artifacts = self._project_signal_artifacts(
                pipeline,
                topn_segments=topn_segments,
            )
            payload = CurvePayload(
                engine_id=self.engine_id,
                decision_artifacts=artifacts,
                equity_curve=equity_curve,
                metrics=metrics,
                execution=execution,
            )
        return self._base_result(
            payload=payload,
            signal_stock_count=signal_stock_count,
            diagnostics=engine_result.diagnostics,
        )

    def _run_assess(self) -> RunResult:
        with self.timer.measure("decision_build"):
            pipeline = self._run_signal_pipeline(
                no_signal_warning="assess: 未产生任何信号，无法评估平均每笔收益率",
                topn_empty_warning="assess: TopN 信号为空，无法评估平均每笔收益率",
                log_empty_info=False,
            )
            if not len(pipeline.selection.selected_rows):
                payload = AssessPayload(
                    engine_id=self.engine_id,
                    summary_df=None,
                    decision_artifacts=self._project_signal_artifacts(pipeline),
                    execution=None,
                )
                return self._base_result(payload=payload, signal_stock_count=0)
            decision = self._signal_decision(pipeline)

        with self.timer.measure("engine_run"):
            engine_result = compose_engine(self.engine_id).run(self._engine_request(decision))

        with self.timer.measure("result_projection"):
            topn_segments_df, topn_segments_summary_df = self._evaluate_topn_segments(pipeline)
            topn_segments = TradeSegmentsBuildResult.from_frames(
                topn_trade_segments_df=topn_segments_df,
                topn_trade_segments_summary_df=topn_segments_summary_df,
            )
            artifacts = self._project_signal_artifacts(
                pipeline,
                topn_segments=topn_segments,
            )
            _assess_stock_codes, assess_stock_count = _build_assess_stock_scope(
                pipeline.selection,
                self._quote_store(pipeline),
            )

            from backtest.evaluation.assess import (
                attach_topn_trade_segments_summary,
                build_assess_summary_from_broker,
                build_assess_summary_from_segments,
            )

            assessment_source = engine_result.assessment_source
            if isinstance(assessment_source, SegmentAssessmentSource):
                summary_df = build_assess_summary_from_segments(
                    self.ctx,
                    dict(assessment_source.trading_frames),
                    assess_stock_count=assess_stock_count,
                    accounting_mode=assessment_source.accounting_mode,
                )
            elif isinstance(assessment_source, LedgerAssessmentSource):
                summary_df = build_assess_summary_from_broker(
                    self.ctx,
                    merged_df=assessment_source.equity_curve,
                    assess_stock_count=assess_stock_count,
                    fills_df=assessment_source.fills_df,
                    risk_fills_df=assessment_source.risk_fills_df,
                )
            else:
                raise DataValidationError(
                    f"engine {engine_result.engine_id!r} did not provide an assessment source"
                )
            summary_df = attach_topn_trade_segments_summary(
                summary_df,
                topn_segments.topn_trade_segments_summary_df,
            )
            payload = AssessPayload(
                engine_id=self.engine_id,
                summary_df=(
                    summary_df
                    if isinstance(summary_df, pd.DataFrame) and not summary_df.empty
                    else None
                ),
                decision_artifacts=artifacts,
                execution=engine_result.execution,
            )
        return self._base_result(
            payload=payload,
            signal_stock_count=assess_stock_count,
            diagnostics=engine_result.diagnostics,
        )

    def _run_signal_pipeline(
        self,
        *,
        no_signal_warning: str,
        topn_empty_warning: str,
        log_empty_info: bool,
    ) -> SignalPipelineResult:
        pipeline = SignalDecisionPipeline(
            ctx=self.ctx,
            start_time=self.start_time,
            end_time=self.end_time,
            timer=self.timer,
        )
        return pipeline.run(
            no_signal_warning=no_signal_warning,
            topn_empty_warning=topn_empty_warning,
            log_empty_info=log_empty_info,
        )

    def _signal_decision(
        self,
        pipeline: SignalPipelineResult,
    ) -> SignalDecision:
        if not len(pipeline.selection.selected_rows):
            raise DataValidationError("SignalDecision requires selected signals and trade days")
        source = type(self.ctx.strategy).__name__ if self.ctx.strategy is not None else "strategy"
        return SignalDecision(
            identity=DecisionIdentity(source=source),
            selection=pipeline.selection,
            quote_store=self._quote_store(pipeline),
            trade_days=pipeline.trade_days,
        )

    def _engine_request(self, decision) -> EngineRequest:
        if self.ctx.risk_cfg is None:
            raise DataValidationError("engine request requires risk policy")
        return EngineRequest(
            run_spec=RunSpec(
                mode=self.mode,
                start_time=self.start_time,
                end_time=self.end_time,
            ),
            engine_spec=EngineSpec(
                engine_id=self.engine_id,
                runtime_config=EngineRuntimeConfig.from_app_config(self.cfg),
                strategy_spec=self.ctx.spec,
            ),
            decision=decision,
            execution_policy=self.ctx.execution_policy,
            risk_policy=self.ctx.risk_cfg,
            services=EngineServices(
                net_value_runtime=self.ctx.net_value_runtime,
                logger=self.logger,
                run_id=self.ctx.run_id,
                output_root=self.output_root,
            ),
        )

    def _evaluate_topn_segments(
        self,
        pipeline: SignalPipelineResult,
    ) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        from backtest.evaluation.topn_signal_segments import evaluate_topn_signal_trade_segments

        try:
            return evaluate_topn_signal_trade_segments(
                pipeline.selection,
                self._quote_store(pipeline),
                execution_policy=self.ctx.execution_policy,
                cfg=self.cfg,
                logger=self.logger,
                trade_days=pipeline.trade_days,
            )
        except Exception as exc:
            log_warn(f"assess: TopN后交易段评估失败，已跳过附加统计: {exc}", self.logger)
            return pd.DataFrame(), pd.DataFrame()

    def _maybe_plot(self, equity_curve: pd.DataFrame | None) -> None:
        if not (
            self.write_outputs
            and
            bool(self.cfg.run.plot_net_value)
            and isinstance(equity_curve, pd.DataFrame)
            and not equity_curve.empty
        ):
            return
        from backtest.netvalue.plot import plot_net_value

        with self.timer.measure("plot_net_value"):
            plot_net_value(equity_curve)

    def _log_curve_metrics(self, metrics) -> None:
        if self.logger is None or metrics is None:
            return
        self.logger.info(
            "run_backtest summary run_id=%s return=%.6f drawdown=%.6f",
            self.ctx.run_id,
            metrics.final_net_value - 1.0,
            metrics.max_drawdown,
        )

    def _log_run_header(self) -> None:
        if (
            self._engine_registration is not None
            and self._engine_registration.decision_kind == "target_weight"
        ):
            fixed_cfg = self.cfg.fixed_rebalance
            log_info(
                "[STRATEGY] fixed_rebalance "
                f"symbol_type={fixed_cfg.symbol_type} frequency={fixed_cfg.rebalance_frequency}",
                self.logger,
            )
            return
        if self.ctx.spec is None:
            return
        log_info(
            f"[STRATEGY] TopN={int(self.ctx.spec.top_n)} "
            f"sort_spec={list(self.ctx.spec.sort_spec)} "
            f"portfolio={int(self.ctx.spec.portfolio_num_j)}x{int(self.ctx.spec.portfolio_num_i)}",
            self.logger,
        )

    def _write_strategy_contract(self) -> None:
        if not self.write_outputs or self.ctx.spec is None:
            return
        descriptor = self.ctx.strategy_descriptor
        if descriptor is None:
            raise DataValidationError(
                "strategy output requires the loaded strategy descriptor"
            )
        from strategies.spec import write_complete_strategy_spec

        Path(self.cfg.paths.signal_data).mkdir(parents=True, exist_ok=True)
        write_complete_strategy_spec(
            Path(self.cfg.paths.signal_data) / "strategy_spec.json",
            descriptor.contract,
        )

    def _print_backtest_summary_stats(self, items: list[object]) -> None:
        rows = [item for item in items if isinstance(item, dict)]
        if not rows:
            return
        frame = pd.DataFrame(rows)

        def _sum(name: str) -> float:
            if name not in frame.columns:
                return 0.0
            return float(pd.to_numeric(frame[name], errors="coerce").fillna(0.0).sum())

        trade_count = _sum("交易笔数")
        positive_count = _sum("收益率为正的交易笔数")
        log_metric("signal_stock_count", logger=self.logger, count=len(frame))
        log_metric("trade_count", logger=self.logger, count=int(trade_count))
        if trade_count:
            average = _sum("单笔平均收益率*交易笔数") / trade_count
            log_metric("avg_trade_return", logger=self.logger, value=f"{average:.2%}")
            log_metric("win_rate", logger=self.logger, value=f"{positive_count / trade_count:.2%}")

    def _print_timing_summary(self) -> None:
        if not (self.timer.enabled and self.timer.times):
            return
        summary = ", ".join(self.timer.summary())
        log_info(f"[PROFILE] stage timings: {summary}", self.logger)


def run_backtest(
    start_time: str,
    end_time: str,
    *,
    cfg,
    spec,
    execution_policy,
    risk_cfg,
    artifact_policy: ArtifactPolicy | str = ArtifactPolicy.NONE,
    stock_code_list: list[str] | tuple[str, ...] | None = None,
    universe_resolution=None,
    strategy: StrategyLike | None = None,
    strategy_descriptor=None,
    strategy_data_inputs=None,
    logger=None,
    run_id: str | None = None,
    defer_artifact_publish: bool = False,
    board_member_cache=None,
) -> RunResult:
    ctx = create_backtest_context(
        cfg=cfg,
        spec=spec,
        execution_policy=execution_policy,
        risk_cfg=risk_cfg,
        artifact_policy=artifact_policy,
        stock_code_list=tuple(stock_code_list) if stock_code_list is not None else None,
        universe_resolution=universe_resolution,
        strategy=strategy,
        strategy_descriptor=strategy_descriptor,
        strategy_data_inputs=strategy_data_inputs,
        logger=logger,
        run_id=run_id,
        defer_artifact_publish=defer_artifact_publish,
        board_member_cache=board_member_cache,
    )
    orchestrator = BacktestOrchestrator(ctx=ctx, start_time=start_time, end_time=end_time)
    return orchestrator.run()


__all__ = ["BacktestOrchestrator", "run_backtest"]
