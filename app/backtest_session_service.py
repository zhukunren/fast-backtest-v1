from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, Mapping

from config import AppConfig, apply_config_overrides, session_config_scope
from backtest.artifacts import ArtifactPolicy, OutputRunLockTimeout, output_run_lock
from backtest.engines import engine_registration
from backtest.orchestration import run_backtest
from backtest.result import CurvePayload, RunResult
from core.logging import close_run_logger
from core.runtime_flags import runtime_flags_scope
from strategies.spec import RiskControlSpec
from core.universe import build_fixed_rebalance_universe_resolution

from .console import WarningCollector
from .diagnostics_service import BacktestDiagnosticsService
from .run_summary_service import RunSummaryService
from .runtime import RuntimeContext
from .runtime_bootstrap_service import RuntimeBootstrapService
from .session_metadata import (
    read_session_meta_status,
    write_completed_metadata_or_fail as write_completed_metadata,
    write_failed_metadata,
    write_running_metadata,
)


@dataclass(frozen=True, slots=True)
class BacktestSessionOutcome:
    ctx: RuntimeContext
    strategy: object | None
    result: RunResult | None


def _outcome(
    ctx: RuntimeContext,
    *,
    strategy: object | None = None,
    result: RunResult | None = None,
) -> BacktestSessionOutcome:
    return BacktestSessionOutcome(ctx=ctx, strategy=strategy, result=result)


def _session_output_lock(ctx: RuntimeContext):
    """Hold the shared output root through the session's final publication."""
    output_root = getattr(ctx, "output_root", None)
    if output_root is None:
        # Lightweight service tests may use a context stub without filesystem
        # paths. Production RuntimeContext instances always carry this root.
        return nullcontext()
    return output_run_lock(
        Path(output_root),
        run_id=str(getattr(ctx, "run_id", "") or ""),
        purpose="backtest_session",
    )


def _write_session_running_metadata(ctx: RuntimeContext) -> dict[str, Any]:
    return write_running_metadata(
        ctx,
        strategy_meta=ctx.strategy_spec,
        risk_cfg=ctx.risk_cfg,
    )


def _make_running_meta_callback(ctx: RuntimeContext) -> Callable[[], dict[str, Any]]:
    return lambda: _write_session_running_metadata(ctx)


def _bind_strategy_runtime_context(
    ctx: RuntimeContext,
    *,
    spec,
    risk_cfg,
) -> Callable[[], dict[str, Any]]:
    ctx.strategy_spec = spec
    ctx.risk_cfg = risk_cfg
    ensure_meta = _make_running_meta_callback(ctx)
    ctx.meta = ensure_meta()
    return ensure_meta


def _can_resolve_config_universe(cfg: AppConfig) -> bool:
    backtest = getattr(cfg, "backtest", None)
    return all(
        hasattr(backtest, attr)
        for attr in ("universe_prefixes", "exclude_bj", "exclude_st")
    )


def _attach_universe_to_meta(ctx: RuntimeContext) -> None:
    resolution = getattr(ctx, "universe_resolution", None)
    if resolution is None or not isinstance(ctx.meta, dict):
        return
    to_dict = getattr(resolution, "to_dict", None)
    if callable(to_dict):
        ctx.meta["universe"] = to_dict()


def _bind_runtime_universe(
    ctx: RuntimeContext,
    *,
    stock_code_list: Sequence[str] | None,
    fixed_rebalance: bool,
) -> tuple[str, ...] | None:
    if fixed_rebalance:
        ctx.universe_resolution = build_fixed_rebalance_universe_resolution(cfg=ctx.cfg)
        _attach_universe_to_meta(ctx)
        return None

    if stock_code_list is None and not _can_resolve_config_universe(ctx.cfg):
        return None

    from backtest.engine_batch_support import resolve_backtest_universe

    resolution = resolve_backtest_universe(
        cfg=ctx.cfg,
        stock_code_list=stock_code_list,
        strategy_spec=ctx.strategy_spec,
    )
    ctx.universe_resolution = resolution
    _attach_universe_to_meta(ctx)
    return tuple(resolution.stock_code_list)


def _emit_session_progress(ctx: RuntimeContext, *, stage: str, message: str) -> None:
    """Keep long synchronous CLI runs visibly alive without changing quiet mode."""
    try:
        ctx.logger.info("session_progress run_id=%s stage=%s", ctx.run_id, stage)
    except Exception:
        pass
    if bool(getattr(ctx.cfg.run, "quiet", False)):
        return
    ctx.raw_print(message)


def _record_unhandled_session_failure(
    ctx: RuntimeContext,
    exc: Exception,
    *,
    stage: str,
) -> None:
    """Persist failures that occur before the backtest execution wrapper."""
    if read_session_meta_status(getattr(ctx, "meta", None)) == "failed":
        return

    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    reason = f"{stage}: {detail}"
    try:
        ctx.logger.exception(
            "backtest_session_failed run_id=%s stage=%s error=%s",
            ctx.run_id,
            stage,
            detail,
        )
    except Exception:
        pass
    ctx.raw_print(f"[ERROR] 回测失败 [{stage}]: {detail}")
    ctx.raw_print(f"详情见日志: {ctx.tag_log_path}")

    event_logger = getattr(ctx, "event_logger", None)
    if event_logger is not None:
        try:
            event_logger.emit(
                "run_failed",
                stage=stage,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        except Exception:
            pass
    try:
        # If acquiring the shared root itself timed out, retrying the same lock
        # here would double the wait. The run metadata filename is run-id
        # scoped, so persist the failure without another blocking acquisition.
        failure_lock = nullcontext() if isinstance(exc, OutputRunLockTimeout) else _session_output_lock(ctx)
        with failure_lock:
            ctx.meta = write_failed_metadata(
                ctx,
                reason=reason,
                strategy_meta=ctx.strategy_spec,
                risk_cfg=ctx.risk_cfg,
            )
    except Exception:
        try:
            ctx.logger.exception(
                "backtest_session_failure_metadata_failed run_id=%s stage=%s",
                ctx.run_id,
                stage,
            )
        except Exception:
            pass
    emit_summary = getattr(getattr(ctx, "warn_collector", None), "emit_summary", None)
    if callable(emit_summary):
        emit_summary(ctx.raw_print)


def _disabled_risk_policy() -> RiskControlSpec:
    return RiskControlSpec(
        enable_nv=False,
        enable_dd=False,
        warn_dd=-0.10,
        danger_dd=-0.20,
        lock_dd=-0.30,
        nv_warn=0.9,
        nv_danger=0.8,
        nv_clear=0.5,
        nv_ratios=(1.0, 0.8, 0.5, 0.3),
        dd_ratios=(1.0, 0.8, 0.5, 0.3),
    )


def _complete_successful_session(
    ctx: RuntimeContext,
    *,
    strategy: object | None,
    result: RunResult,
) -> BacktestSessionOutcome:
    meta = write_completed_metadata(
        ctx,
        result=result,
        strategy_meta=ctx.strategy_spec,
        risk_cfg=ctx.risk_cfg,
    )
    ctx.logger.info("run_complete run_id=%s duration=%.2f", ctx.run_id, meta["duration_sec"])
    event_logger = getattr(ctx, "event_logger", None)
    if event_logger is not None:
        payload = result.payload if isinstance(result, RunResult) else None
        metrics = payload.metrics if isinstance(payload, CurvePayload) else None
        event_logger.emit(
            "run_complete",
            status="completed",
            duration_sec=round(float(meta["duration_sec"]), 6),
            final_net_value=metrics.final_net_value if metrics is not None else None,
            max_drawdown=metrics.max_drawdown if metrics is not None else None,
        )
    return _outcome(ctx, strategy=strategy, result=result)


def _run_backtest_execution(
    ctx: RuntimeContext,
    strategy,
    *,
    spec,
    execution_policy,
    risk_cfg,
    artifact_policy: ArtifactPolicy,
    stock_code_list: Sequence[str] | None = None,
) -> RunResult:
    failure_stage = "run_backtest"
    try:
        return run_backtest(
            ctx.cfg.backtest.start_time,
            ctx.cfg.backtest.end_time,
            logger=ctx.logger,
            run_id=ctx.run_id,
            cfg=ctx.cfg,
            spec=spec,
            execution_policy=execution_policy,
            risk_cfg=risk_cfg,
            artifact_policy=artifact_policy,
            stock_code_list=stock_code_list,
            universe_resolution=getattr(ctx, "universe_resolution", None),
            strategy=strategy,
            strategy_descriptor=ctx.strategy_descriptor,
            defer_artifact_publish=True,
        )
    except Exception as exc:
        ctx.logger.exception("portfolio_backtest_failed run_id=%s", ctx.run_id)
        ctx.raw_print(f"[ERROR] 回测失败: {exc}")
        ctx.raw_print(f"详情见日志: {ctx.tag_log_path}")
        event_logger = getattr(ctx, "event_logger", None)
        if event_logger is not None:
            event_logger.emit(
                "run_failed",
                stage=failure_stage,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        ctx.meta = write_failed_metadata(
            ctx,
            reason=f"{failure_stage}: {exc}",
            strategy_meta=ctx.strategy_spec,
            risk_cfg=ctx.risk_cfg,
        )
        ctx.warn_collector.emit_summary(ctx.raw_print)
        raise


def run_backtest_session(
    cfg: AppConfig,
    *,
    raw_print: Callable[..., None] = print,
    print_summary: bool,
    stock_code_list: Sequence[str] | None = None,
    bootstrap: RuntimeBootstrapService | None = None,
    summary: RunSummaryService | None = None,
    diagnostics: BacktestDiagnosticsService | None = None,
    prepared_strategy: object | None = None,
    prepared_strategy_descriptor: object | None = None,
    artifact_policy: ArtifactPolicy | str = ArtifactPolicy.NONE,
) -> BacktestSessionOutcome:
    bootstrap = bootstrap or RuntimeBootstrapService()
    summary = summary or RunSummaryService()
    diagnostics = diagnostics or BacktestDiagnosticsService(
        session_runner=lambda *, cfg, overrides: run_backtest_session_with_overrides(
            cfg,
            overrides,
            raw_print=lambda *args, **kwargs: None,
            print_summary=False,
        )
    )
    artifact_policy = ArtifactPolicy.coerce(artifact_policy)

    warn_collector = WarningCollector()
    ctx = bootstrap.build_runtime(
        cfg,
        raw_print=raw_print,
        warn_collector=warn_collector,
    )
    strategy = None
    result = None
    failure_stage = "runtime_scope"
    try:
        with (
            _session_output_lock(ctx),
            session_config_scope(cfg),
            runtime_flags_scope(
                quiet=bool(cfg.run.quiet),
                tag_log_path=ctx.tag_log_path,
                sync_env=False,
            ),
        ):
            failure_stage = "run_start"
            event_logger = getattr(ctx, "event_logger", None)
            if event_logger is not None:
                event_logger.emit(
                    "run_start",
                    status="starting",
                    start_time=cfg.backtest.start_time,
                    end_time=cfg.backtest.end_time,
                    artifact_policy=artifact_policy.value,
                    run_mode=getattr(cfg.run, "run_mode", None),
                    engine="v1",
                )
            failure_stage = "execution_policy"
            execution_policy = bootstrap.log_execution_policy(ctx)
            ctx.execution_policy = execution_policy
            failure_stage = "config_validation"
            if not bootstrap.validate_and_log_config(ctx):
                if event_logger is not None:
                    event_logger.emit("run_failed", stage="config_validation", status="failed")
                return _outcome(ctx)

            failure_stage = "run_start_logging"
            bootstrap.log_run_start(ctx)
            failure_stage = "engine_registration"
            registration = engine_registration(
                getattr(getattr(cfg, "portfolio", None), "engine", "legacy")
            )
            fixed_rebalance = registration.decision_kind == "target_weight"
            if not registration.requires_strategy:
                failure_stage = "clear_outputs"
                clear_span = event_logger.span("clear_outputs") if event_logger is not None else nullcontext()
                with clear_span:
                    bootstrap.clear_outputs_if_needed(ctx)
                strategy = None
                spec = None
                risk_cfg = _disabled_risk_policy()
                ctx.logger.info("strategy_load_skipped engine=fixed_rebalance")
            else:
                if (prepared_strategy is None) != (prepared_strategy_descriptor is None):
                    raise ValueError(
                        "prepared strategy and descriptor must be provided together"
                    )
                if prepared_strategy is not None:
                    if getattr(prepared_strategy_descriptor, "strategy", None) is not prepared_strategy:
                        raise ValueError(
                            "prepared strategy must be owned by prepared strategy descriptor"
                        )
                    strategy = prepared_strategy
                    ctx.strategy_descriptor = prepared_strategy_descriptor
                else:
                    failure_stage = "load_strategy"
                    load_span = (
                        event_logger.span("load_strategy")
                        if event_logger is not None
                        else nullcontext()
                    )
                    with load_span:
                        strategy = bootstrap.load_strategy_or_fail(ctx)
                if strategy is None:
                    if event_logger is not None:
                        event_logger.emit("run_failed", stage="load_strategy", status="failed")
                    return _outcome(ctx)
                failure_stage = "clear_outputs"
                clear_span = event_logger.span("clear_outputs") if event_logger is not None else nullcontext()
                with clear_span:
                    bootstrap.clear_outputs_if_needed(ctx)
                failure_stage = "strategy_contract"
                contract_span = event_logger.span("strategy_contract") if event_logger is not None else nullcontext()
                with contract_span:
                    spec, risk_cfg = bootstrap.log_strategy_contract(ctx, strategy)
            failure_stage = "running_metadata"
            ensure_meta = _bind_strategy_runtime_context(ctx, spec=spec, risk_cfg=risk_cfg)
            failure_stage = "universe_resolution"
            _emit_session_progress(
                ctx,
                stage=failure_stage,
                message="[RUN] 正在解析回测股票池...",
            )
            resolved_stock_code_list = _bind_runtime_universe(
                ctx,
                stock_code_list=stock_code_list,
                fixed_rebalance=fixed_rebalance,
            )
            if resolved_stock_code_list is not None:
                _emit_session_progress(
                    ctx,
                    stage="universe_resolved",
                    message=(
                        "[RUN] 股票池解析完成: "
                        f"stocks={len(resolved_stock_code_list)}"
                    ),
                )

            failure_stage = "run_backtest"
            _emit_session_progress(
                ctx,
                stage=failure_stage,
                message="[RUN] 正在执行回测...",
            )
            backtest_span = event_logger.span(failure_stage) if event_logger is not None else nullcontext()
            with backtest_span:
                result = _run_backtest_execution(
                    ctx,
                    strategy,
                    spec=spec,
                    execution_policy=execution_policy,
                    risk_cfg=risk_cfg,
                    artifact_policy=artifact_policy,
                    stock_code_list=resolved_stock_code_list,
                )
            failure_stage = "diagnostics"
            _emit_session_progress(
                ctx,
                stage=failure_stage,
                message="[RUN] 回测计算完成，正在整理结果...",
            )
            diagnostics_span = event_logger.span("diagnostics") if event_logger is not None else nullcontext()
            with diagnostics_span:
                diagnostics.collect_and_write(
                    ctx,
                    result,
                    ensure_meta=ensure_meta,
                )
            if print_summary:
                failure_stage = "summary"
                summary_span = event_logger.span("summary") if event_logger is not None else nullcontext()
                with summary_span:
                    summary.print_final_summary(
                        ctx,
                        result,
                        ensure_meta=ensure_meta,
                    )
            failure_stage = "complete_metadata"
            return _complete_successful_session(ctx, strategy=strategy, result=result)
    except Exception as exc:
        _record_unhandled_session_failure(ctx, exc, stage=failure_stage)
        raise
    finally:
        try:
            close_run_logger(ctx.logger)
        except Exception:
            pass


def run_backtest_session_with_overrides(
    base_cfg: AppConfig,
    overrides: Mapping[str, Any] | None = None,
    *,
    raw_print: Callable[..., None] = print,
    print_summary: bool,
    stock_code_list: Sequence[str] | None = None,
    bootstrap: RuntimeBootstrapService | None = None,
    summary: RunSummaryService | None = None,
    diagnostics: BacktestDiagnosticsService | None = None,
    prepared_strategy: object | None = None,
    prepared_strategy_descriptor: object | None = None,
    artifact_policy: ArtifactPolicy | str = ArtifactPolicy.NONE,
) -> BacktestSessionOutcome:
    cfg = apply_config_overrides(base_cfg, overrides or {})
    return run_backtest_session(
        cfg,
        raw_print=raw_print,
        print_summary=print_summary,
        stock_code_list=stock_code_list,
        bootstrap=bootstrap,
        summary=summary,
        diagnostics=diagnostics,
        prepared_strategy=prepared_strategy,
        prepared_strategy_descriptor=prepared_strategy_descriptor,
        artifact_policy=artifact_policy,
    )

__all__ = [
    "BacktestSessionOutcome",
    "run_backtest_session",
    "run_backtest_session_with_overrides",
]
