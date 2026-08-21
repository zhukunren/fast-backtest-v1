from __future__ import annotations

import time
from pathlib import Path

from config import AppConfig
from core.config_validator import validate_config
from core.execution_policy import get_execution_policy
from core.logging import close_run_logger, get_run_logger, log_kv
from core.output_utils import clear_output_files
from core.run_events import JsonlRunEventLogger, default_run_events_path, run_events_enabled_from_env
from core.run_metadata import create_run_id
from backtest.engines import ENGINE_REGISTRY
from strategies.api import (
    RuntimeEnvironment,
    load_strategy_descriptor,
    preflight_strategy_requirements,
)

from .console import WarningCollector
from .runtime import RuntimeContext
from .session_metadata import write_failed_metadata


def _build_runtime_layout(cfg: AppConfig, *, run_id: str) -> tuple[Path, Path, Path]:
    output_root = Path(cfg.paths.signal_data).parent
    run_meta_dir = output_root / "run_meta"
    # Run logs are evidence and must not share the retention-managed
    # operational log namespace.
    log_path = run_meta_dir / f"backtest_{run_id}.log"
    return output_root, run_meta_dir, log_path


def _emit_config_validation_messages(ctx: RuntimeContext, *, warnings: list[str], errors: list[str]) -> None:
    for msg in warnings:
        ctx.raw_print(f"[WARN] {msg}")
        ctx.logger.warning("config_warning: %s", msg)
    for msg in errors:
        ctx.raw_print(f"[ERROR] {msg}")
        ctx.logger.error("config_error: %s", msg)


def _mark_bootstrap_failed(ctx: RuntimeContext, *, reason: str) -> None:
    ctx.meta = write_failed_metadata(
        ctx,
        reason=reason,
        strategy_meta=None,
        risk_cfg=None,
    )
    ctx.warn_collector.emit_summary(ctx.raw_print)


def _risk_threshold_order_warning(risk_cfg) -> str:
    warn_dd = float(risk_cfg.warn_dd)
    danger_dd = float(risk_cfg.danger_dd)
    lock_dd = float(risk_cfg.lock_dd)
    if lock_dd <= danger_dd <= warn_dd <= 0:
        return ""
    return (
        "risk_control thresholds not ordered: "
        f"lock={lock_dd}, danger={danger_dd}, warn={warn_dd} "
        "(expected lock<=danger<=warn<=0)"
    )


def _strategy_contract_summary(spec) -> str:
    return (
        f"[STRATEGY] TopN={int(spec.top_n)} "
        f"sort_spec={list(spec.sort_spec)} "
        f"portfolio={int(spec.portfolio_num_j)}x{int(spec.portfolio_num_i)}"
    )


def _run_flag_summary(cfg: AppConfig) -> str:
    return "[RUN] compute_metrics=%s topn_trade_segments_report=%s" % (
        bool(cfg.run.compute_metrics),
        bool(cfg.run.build_topn_trade_segments_report),
    )


def _universe_prefix_summary(cfg: AppConfig) -> str:
    return ",".join(cfg.backtest.universe_prefixes) or "ALL"


def _emit_strategy_load_failure(
    ctx: RuntimeContext,
    exc: Exception,
    *,
    stage: str,
) -> None:
    ctx.logger.exception(
        "strategy_load_failed module=%s stage=%s",
        ctx.cfg.strategy.module,
        stage,
    )
    ctx.raw_print(f"[ERROR] 无法加载策略: {exc}")
    ctx.raw_print("请检查 config.ini 的 [strategy] module 配置或 STRATEGY_MODULE 环境变量")
    _mark_bootstrap_failed(ctx, reason=f"load_strategy.{stage}: {exc}")


def _warn_risk_threshold_order(ctx: RuntimeContext, risk_cfg) -> None:
    if not risk_cfg.enabled:
        return
    msg = _risk_threshold_order_warning(risk_cfg)
    if not msg:
        return
    ctx.raw_print(f"[WARN] {msg}")
    ctx.logger.warning("risk_control_config %s", msg)


class RuntimeBootstrapService:
    """Build runtime context and handle pre-run validation/load steps."""

    @staticmethod
    def print_info(ctx: RuntimeContext, msg: str) -> None:
        if bool(ctx.cfg.run.quiet):
            return
        ctx.raw_print(msg)

    def build_runtime(
        self,
        cfg: AppConfig,
        *,
        raw_print,
        warn_collector: WarningCollector,
    ) -> RuntimeContext:
        run_id = create_run_id()
        output_root, run_meta_dir, log_path = _build_runtime_layout(cfg, run_id=run_id)
        logger = get_run_logger(run_id, log_path.parent)
        try:
            event_logger = JsonlRunEventLogger(
                path=default_run_events_path(run_meta_dir, run_id=run_id),
                run_id=run_id,
                enabled=run_events_enabled_from_env(),
                common={"strategy_module": cfg.strategy.module},
            )
            warn_collector.set_logger(logger)

            return RuntimeContext(
                cfg=cfg,
                start_ts=time.time(),
                run_id=run_id,
                output_root=output_root,
                run_meta_dir=run_meta_dir,
                tag_log_path=log_path,
                logger=logger,
                raw_print=raw_print,
                warn_collector=warn_collector,
                event_logger=event_logger,
            )
        except Exception:
            close_run_logger(logger)
            raise

    def log_execution_policy(self, ctx: RuntimeContext):
        try:
            policy = get_execution_policy(ctx.cfg)
            log_kv(
                ctx.logger,
                "execution_policy",
                buy_timing=policy.buy_timing,
                sell_timing=policy.sell_timing,
                shift_t1_sell=policy.shift_t1_sell,
                allow_intraday=policy.allow_intraday,
            )
            return policy
        except Exception as exc:
            ctx.logger.warning("execution_policy_invalid error=%s", exc)
            return None

    def validate_and_log_config(self, ctx: RuntimeContext) -> bool:
        errors, warnings = validate_config(ctx.cfg, engine_catalog=ENGINE_REGISTRY)
        _emit_config_validation_messages(ctx, warnings=warnings, errors=errors)
        if not errors:
            return True

        _mark_bootstrap_failed(ctx, reason="config_validation")
        return False

    def log_run_start(self, ctx: RuntimeContext) -> None:
        self.print_info(ctx, _run_flag_summary(ctx.cfg))
        prefixes = _universe_prefix_summary(ctx.cfg)
        ctx.logger.info(
            "run_start run_id=%s module=%s universe_prefixes=%s exclude_bj=%s exclude_st=%s",
            ctx.run_id,
            ctx.cfg.strategy.module,
            prefixes,
            ctx.cfg.backtest.exclude_bj,
            ctx.cfg.backtest.exclude_st,
        )

    def clear_outputs_if_needed(self, ctx: RuntimeContext) -> None:
        if ctx.cfg.run.clear_all_tables:
            self.print_info(ctx, "开始清理输出目录（已限定为 config.paths.* 输出路径，避免误删源码）...")
            clear_output_files(ctx.cfg)
            self.print_info(ctx, "清理完成")
            return
        if ctx.cfg.run.clear_outputs:
            self.print_info(ctx, "开始清理输出目录...")
            clear_output_files(ctx.cfg)

    def load_strategy_or_fail(self, ctx: RuntimeContext):
        try:
            descriptor = load_strategy_descriptor(
                ctx.cfg.strategy.module,
                engine="v1",
                reload_module=True,
            )
            ctx.strategy_descriptor = descriptor
            strategy = descriptor.strategy
            self.print_info(ctx, f"[STRATEGY] 已加载策略: {strategy}")
            return strategy
        except Exception as exc:
            _emit_strategy_load_failure(ctx, exc, stage="loader")
            return None

    def log_strategy_contract(
        self,
        ctx: RuntimeContext,
        strategy,
    ):
        descriptor = ctx.strategy_descriptor
        if descriptor is None or descriptor.strategy is not strategy:
            raise RuntimeError("strategy descriptor is unavailable for the loaded strategy")
        spec = descriptor.spec
        risk_cfg = descriptor.risk_control
        panel = getattr(ctx.cfg, "panel", None)
        runtime_environment = RuntimeEnvironment.standard_v1(
            panel_source=str(getattr(panel, "source", "parquet") or "parquet")
            .strip()
            .casefold(),
        )
        ctx.strategy_runtime_requirements = preflight_strategy_requirements(
            spec=spec,
            strategy=strategy,
            advanced_spec=descriptor.advanced,
            runtime=runtime_environment,
        )
        _warn_risk_threshold_order(ctx, risk_cfg)

        self.print_info(ctx, _strategy_contract_summary(spec))
        ctx.logger.info(
            "strategy_loaded run_id=%s module=%s contract_hash=%s",
            ctx.run_id,
            ctx.cfg.strategy.module,
            descriptor.contract_hash,
        )
        return spec, risk_cfg
