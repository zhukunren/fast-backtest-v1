"""Map configured output files to stable artifact-generation paths."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from .generation import ArtifactGenerationError, ArtifactSource


def _relative_output_path(cfg, artifact_path: Path) -> str:
    path = Path(artifact_path).resolve()
    categories = (
        ("signal_data", Path(cfg.paths.signal_data).resolve()),
        ("stock_data", Path(cfg.paths.stock_data).resolve()),
        ("trade_data", Path(cfg.paths.trade_data).resolve()),
        ("return_date", Path(cfg.paths.return_date).resolve()),
        ("return_date_with_returns", Path(cfg.paths.return_date_with_returns).resolve()),
        ("net_value_unique_date", Path(cfg.paths.net_value_unique_date).resolve()),
    )
    for category, root in categories:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        return PurePosixPath(category, *relative.parts).as_posix()
    output_root = Path(cfg.paths.signal_data).resolve().parent
    try:
        return PurePosixPath(*path.relative_to(output_root).parts).as_posix()
    except ValueError as exc:
        raise ArtifactGenerationError(f"artifact source is outside configured output roots: {path}") from exc


def artifact_source_from_output_path(
    cfg,
    path: Path,
    *,
    catalog_key: str,
    required: bool = True,
) -> ArtifactSource:
    artifact_path = Path(path)
    return ArtifactSource(
        path=artifact_path,
        relative_path=_relative_output_path(cfg, artifact_path),
        catalog_key=str(catalog_key),
        required=bool(required),
    )


__all__ = ["artifact_source_from_output_path"]
