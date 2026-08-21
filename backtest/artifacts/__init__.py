"""Artifact catalog and atomic generation publication."""

from .catalog import (
    ARTIFACT_CATALOG,
    ArtifactPathRule,
    ArtifactSpec,
    build_artifact_catalog,
    catalog_entry,
    validate_catalog_path,
)
from .generation import (
    GENERATION_PROTOCOL_VERSION,
    RELEASE_ID,
    ArtifactGenerationError,
    ArtifactSource,
    GenerationSnapshot,
    abort_pending_generation,
    activate_generation,
    current_pointer_path,
    has_pending_generation,
    list_ready_generations,
    publish_artifact_generation,
    resolve_generation,
    resolve_current_generation,
    stage_artifact_generation,
)
from .generation_pruning import plan_generation_prune, prune_artifact_generations
from .policy import ArtifactPolicy
from .output_lock import OutputRunLockTimeout, output_run_lock
from .runtime import stage_run_result_artifacts, write_run_result_artifacts
from .sources import artifact_source_from_output_path

__all__ = [
    "ARTIFACT_CATALOG",
    "GENERATION_PROTOCOL_VERSION",
    "RELEASE_ID",
    "ArtifactGenerationError",
    "ArtifactPathRule",
    "ArtifactPolicy",
    "ArtifactSource",
    "ArtifactSpec",
    "OutputRunLockTimeout",
    "GenerationSnapshot",
    "abort_pending_generation",
    "activate_generation",
    "artifact_source_from_output_path",
    "build_artifact_catalog",
    "catalog_entry",
    "current_pointer_path",
    "has_pending_generation",
    "list_ready_generations",
    "plan_generation_prune",
    "publish_artifact_generation",
    "prune_artifact_generations",
    "output_run_lock",
    "resolve_generation",
    "resolve_current_generation",
    "stage_artifact_generation",
    "stage_run_result_artifacts",
    "validate_catalog_path",
    "write_run_result_artifacts",
]
