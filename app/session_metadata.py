from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from backtest.artifacts import (
    RELEASE_ID,
    ArtifactGenerationError,
    ArtifactSource,
    abort_pending_generation,
    artifact_source_from_output_path,
    current_pointer_path,
    has_pending_generation,
    publish_artifact_generation,
)
from core.run_events import build_run_event_snapshot
from core.run_metadata import build_run_metadata, write_run_metadata

from .runtime import RuntimeContext
from .run_protocol_artifacts import write_artifact_manifest, write_completed_run_protocol_artifacts


_REQUIRED_GENERATION_PROTOCOL_PATHS = (
    "artifact_manifest",
    "audit_research",
    "compiled_plan",
    "robustness",
)


def read_session_meta_extra(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    extra = meta.get("extra")
    return extra if isinstance(extra, dict) else {}


def read_session_meta_status(meta: dict[str, Any] | None, *, default: str = "") -> str:
    return str(read_session_meta_extra(meta).get("status") or default)


def read_session_meta_reason(meta: dict[str, Any] | None, *, default: str = "backtest_failed") -> str:
    return str(read_session_meta_extra(meta).get("reason") or default)


def _run_event_snapshot(ctx: RuntimeContext) -> dict[str, Any] | None:
    return build_run_event_snapshot(getattr(ctx, "event_logger", None))


def _universe_snapshot(ctx: RuntimeContext) -> dict[str, Any] | None:
    resolution = getattr(ctx, "universe_resolution", None)
    to_dict = getattr(resolution, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _strategy_contract_snapshot(ctx: RuntimeContext) -> dict[str, Any] | None:
    descriptor = getattr(ctx, "strategy_descriptor", None)
    if descriptor is None:
        return None
    return {
        "contract_hash": descriptor.contract_hash,
        "contract": descriptor.contract.to_dict(),
        "requirements": (
            ctx.strategy_runtime_requirements or descriptor.requirements
        ).to_dict(),
    }


def _log_metadata_warning(ctx: RuntimeContext, message: str, *args: Any) -> None:
    warning = getattr(getattr(ctx, "logger", None), "warning", None)
    if callable(warning):
        warning(message, *args)


def _generation_extra_sources(
    ctx: RuntimeContext,
    protocol_sources: dict[str, ArtifactSource],
) -> tuple[ArtifactSource, ...]:
    sources = list(protocol_sources.values())
    sources.append(
        artifact_source_from_output_path(
            ctx.cfg,
            Path(ctx.run_meta_dir) / f"{ctx.run_id}.json",
            catalog_key="run_metadata",
        )
    )
    diagnostic_path = getattr(ctx, "diagnostic_path", None)
    if diagnostic_path:
        sources.append(
            artifact_source_from_output_path(
                ctx.cfg,
                Path(diagnostic_path),
                catalog_key="diagnostics",
            )
        )
    by_path = {
        source.relative_path: source
        for source in sources
        if source.path.is_file()
    }
    return tuple(by_path[key] for key in sorted(by_path, key=str.casefold))


def _publish_completed_generation(
    ctx: RuntimeContext,
    *,
    protocol_sources: dict[str, ArtifactSource],
) -> None:
    output_root = Path(ctx.output_root)
    missing = [
        name
        for name in _REQUIRED_GENERATION_PROTOCOL_PATHS
        if name not in protocol_sources or not protocol_sources[name].path.is_file()
    ]
    run_meta_path = Path(ctx.run_meta_dir) / f"{ctx.run_id}.json"
    if not run_meta_path.is_file():
        missing.append("run_metadata")
    if missing:
        raise ArtifactGenerationError(
            "completed artifact generation is missing required protocol files: "
            + ", ".join(sorted(missing, key=str.casefold))
        )
    sources = _generation_extra_sources(ctx, protocol_sources)
    publish_artifact_generation(
        output_root,
        ctx.run_id,
        additional_artifacts=sources,
    )


def _ensure_session_metadata(
    ctx: RuntimeContext,
    *,
    strategy_meta,
    risk_cfg,
) -> dict[str, Any]:
    if isinstance(ctx.meta, dict):
        return ctx.meta

    meta = build_run_metadata(
        ctx.cfg,
        run_id=ctx.run_id,
        start_time=ctx.cfg.backtest.start_time,
        end_time=ctx.cfg.backtest.end_time,
        strategy_module=ctx.cfg.strategy.module,
        strategy_meta=strategy_meta,
        execution_policy=ctx.execution_policy,
        risk_cfg=risk_cfg,
        extra={"status": "running"},
    )
    universe = _universe_snapshot(ctx)
    if universe:
        meta["universe"] = universe
    strategy_contract = _strategy_contract_snapshot(ctx)
    if strategy_contract:
        meta["strategy_contract"] = strategy_contract
    ctx.meta = meta
    return meta


def write_running_metadata(
    ctx: RuntimeContext,
    *,
    strategy_meta,
    risk_cfg=None,
) -> dict[str, Any]:
    meta = _ensure_session_metadata(
        ctx,
        strategy_meta=strategy_meta,
        risk_cfg=risk_cfg,
    )
    extra = dict(meta.get("extra") or {})
    extra["status"] = "running"
    extra.pop("reason", None)
    extra.pop("stage_timings_sec", None)
    meta["extra"] = extra
    write_run_metadata(ctx.run_meta_dir, meta, filename=f"{ctx.run_id}.json")
    ctx.meta = meta
    return meta


def write_failed_metadata(
    ctx: RuntimeContext,
    *,
    reason: str,
    strategy_meta=None,
    risk_cfg=None,
) -> dict[str, Any]:
    meta = _ensure_session_metadata(
        ctx,
        strategy_meta=strategy_meta,
        risk_cfg=risk_cfg,
    )
    extra = dict(meta.get("extra") or {})
    extra["status"] = "failed"
    extra["reason"] = reason
    extra.pop("stage_timings_sec", None)
    meta["extra"] = extra
    meta["duration_sec"] = float(time.time() - ctx.start_ts)
    write_run_metadata(ctx.run_meta_dir, meta, filename=f"{ctx.run_id}.json")
    ctx.meta = meta
    return meta


def write_completed_metadata(
    ctx: RuntimeContext,
    *,
    result: object | None,
    strategy_meta=None,
    risk_cfg=None,
) -> dict[str, Any]:
    meta = _ensure_session_metadata(
        ctx,
        strategy_meta=strategy_meta,
        risk_cfg=risk_cfg,
    )
    extra = dict(meta.get("extra") or {})
    extra["status"] = "completed"
    extra.pop("reason", None)
    stage_timings = dict(getattr(result, "timings_sec", {}) or {})
    if stage_timings:
        extra["stage_timings_sec"] = stage_timings
    else:
        extra.pop("stage_timings_sec", None)
    run_events = _run_event_snapshot(ctx)
    if run_events:
        extra["run_events"] = run_events
    else:
        extra.pop("run_events", None)
    universe = _universe_snapshot(ctx)
    if universe:
        meta["universe"] = universe
    meta["extra"] = extra
    meta["duration_sec"] = float(time.time() - ctx.start_ts)
    ctx.meta = meta
    raw_output_root = getattr(ctx, "output_root", None)
    output_root = Path(raw_output_root) if raw_output_root is not None else None
    generation_pending = bool(
        output_root is not None and has_pending_generation(output_root, ctx.run_id)
    )
    if generation_pending:
        meta["artifact_generation"] = {
            "release_id": RELEASE_ID,
            "generation_id": ctx.run_id,
            "current_pointer_path": str(current_pointer_path(output_root)),
            "publication_protocol": "manifest_ready_current",
        }
    protocol_sources: dict[str, ArtifactSource] = {}
    protocol_error: Exception | None = None
    if getattr(ctx, "cfg", None) is not None:
        try:
            protocol_sources = write_completed_run_protocol_artifacts(
                ctx,
                meta=meta,
                result=result,
            )
        except Exception as exc:
            protocol_error = exc
            _log_metadata_warning(ctx, "run_protocol_artifacts_failed run_id=%s error=%s", ctx.run_id, exc)
    write_run_metadata(ctx.run_meta_dir, meta, filename=f"{ctx.run_id}.json")
    if protocol_sources:
        try:
            write_artifact_manifest(
                ctx,
                paths={name: source.path for name, source in protocol_sources.items()},
            )
        except Exception as exc:
            protocol_error = exc
            _log_metadata_warning(ctx, "artifact_manifest_write_failed run_id=%s error=%s", ctx.run_id, exc)
    if generation_pending:
        if protocol_error is not None:
            abort_pending_generation(output_root, ctx.run_id)
            raise ArtifactGenerationError(
                f"required protocol artifacts failed before generation publication: {protocol_error}"
            ) from protocol_error
        try:
            _publish_completed_generation(ctx, protocol_sources=protocol_sources)
        except Exception:
            abort_pending_generation(output_root, ctx.run_id)
            raise
    ctx.meta = meta
    return meta


def write_completed_metadata_or_fail(
    ctx: RuntimeContext,
    *,
    result: object | None,
    strategy_meta=None,
    risk_cfg=None,
) -> dict[str, Any]:
    try:
        return write_completed_metadata(
            ctx,
            result=result,
            strategy_meta=strategy_meta,
            risk_cfg=risk_cfg,
        )
    except Exception as exc:
        ctx.logger.exception("artifact_generation_publish_failed run_id=%s", ctx.run_id)
        ctx.meta = write_failed_metadata(
            ctx,
            reason=f"artifact_generation: {exc}",
            strategy_meta=strategy_meta,
            risk_cfg=risk_cfg,
        )
        event_logger = getattr(ctx, "event_logger", None)
        if event_logger is not None:
            event_logger.emit(
                "run_failed",
                stage="artifact_generation",
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        raise


__all__ = [
    "read_session_meta_extra",
    "read_session_meta_reason",
    "read_session_meta_status",
    "write_completed_metadata",
    "write_completed_metadata_or_fail",
    "write_failed_metadata",
    "write_running_metadata",
]
