"""Runtime bridge from reporting output paths to generation publication."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .generation import (
    ArtifactSource,
    GenerationSnapshot,
    publish_artifact_generation,
    stage_artifact_generation,
)


def stage_run_result_artifacts(
    *,
    cfg,
    run_id: str,
    sources: Iterable[ArtifactSource],
    defer_publish: bool,
) -> GenerationSnapshot | None:
    sources = tuple(sources)
    if not sources:
        return None
    output_root = Path(cfg.paths.signal_data).parent
    stage_artifact_generation(output_root, run_id, sources)
    if defer_publish:
        return None
    return publish_artifact_generation(output_root, run_id)


def write_run_result_artifacts(*, cfg, result, defer_publish: bool) -> GenerationSnapshot | None:
    from .output_lock import output_run_lock
    from backtest.reporting.artifact_outputs import write_run_result_outputs

    output_root = Path(cfg.paths.signal_data).parent
    with output_run_lock(
        output_root,
        run_id=result.identity.run_id,
        purpose="artifact_projection",
    ):
        sources = write_run_result_outputs(cfg=cfg, result=result)
        return stage_run_result_artifacts(
            cfg=cfg,
            run_id=result.identity.run_id,
            sources=sources,
            defer_publish=defer_publish,
        )


__all__ = ["stage_run_result_artifacts", "write_run_result_artifacts"]
