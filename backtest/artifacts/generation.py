"""Atomic READY/current publication for immutable backend artifact generations."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

from .catalog import ArtifactSpec, validate_catalog_path


GENERATION_PROTOCOL_VERSION = 1
RELEASE_ID = "v1-schema-v2"
_GENERATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PENDING_NAME = ".pending.json"
_MANIFEST_NAME = "manifest.json"
_READY_NAME = "READY.json"
_CURRENT_NAME = "current.json"


class ArtifactGenerationError(RuntimeError):
    """Raised when a generation cannot be staged, verified, or activated."""


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    path: Path
    relative_path: str
    catalog_key: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class GenerationSnapshot:
    output_root: Path
    release_id: str
    generation_id: str
    generation_dir: Path
    manifest: dict[str, Any]

    def artifact_path(self, relative_path: str) -> Path:
        normalized = _validate_relative_path(relative_path)
        entries = {
            str(item["relative_path"]): item
            for item in self.manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("relative_path")
        }
        if normalized not in entries:
            raise KeyError(normalized)
        return self.generation_dir / Path(*PurePosixPath(normalized).parts)

    def paths_for_catalog(self, catalog_key: str) -> tuple[Path, ...]:
        paths = [
            self.generation_dir / Path(*PurePosixPath(str(item["relative_path"])).parts)
            for item in self.manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("catalog_key") == catalog_key
        ]
        return tuple(sorted(paths, key=lambda path: str(path).casefold()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_generation_id(generation_id: str) -> str:
    value = str(generation_id or "").strip()
    if not _GENERATION_ID_RE.fullmatch(value):
        raise ArtifactGenerationError(f"invalid artifact generation id: {generation_id!r}")
    return value


def _validate_relative_path(relative_path: str) -> str:
    raw = str(relative_path or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactGenerationError(f"invalid artifact relative path: {relative_path!r}")
    return path.as_posix()


def _release_root(output_root: Path) -> Path:
    return Path(output_root) / "artifact_generations" / RELEASE_ID


def _staging_dir(output_root: Path, generation_id: str) -> Path:
    return _release_root(output_root) / ".staging" / _validate_generation_id(generation_id)


def _generation_dir(output_root: Path, generation_id: str) -> Path:
    return _release_root(output_root) / "generations" / _validate_generation_id(generation_id)


def current_pointer_path(output_root: Path) -> Path:
    return _release_root(output_root) / _CURRENT_NAME


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(_json_bytes(payload))
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArtifactGenerationError(f"cannot read artifact generation JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ArtifactGenerationError(f"artifact generation JSON root must be an object: {path}")
    return payload


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _pending_payload(generation_id: str, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "artifact_type": "artifact_generation_pending",
        "protocol_version": GENERATION_PROTOCOL_VERSION,
        "release_id": RELEASE_ID,
        "generation_id": generation_id,
        "updated_at": _utc_now(),
        "artifacts": artifacts,
    }


def _validate_pending(payload: dict[str, Any], generation_id: str) -> list[dict[str, Any]]:
    if payload.get("artifact_type") != "artifact_generation_pending":
        raise ArtifactGenerationError("pending generation has an invalid artifact_type")
    if payload.get("protocol_version") != GENERATION_PROTOCOL_VERSION:
        raise ArtifactGenerationError("pending generation protocol version is unsupported")
    if payload.get("release_id") != RELEASE_ID or payload.get("generation_id") != generation_id:
        raise ArtifactGenerationError("pending generation identity does not match its directory")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ArtifactGenerationError("pending generation artifacts must be a list")
    return [dict(item) for item in artifacts if isinstance(item, dict)]


def _artifact_spec(catalog_key: str, relative_path: str) -> ArtifactSpec:
    try:
        return validate_catalog_path(catalog_key, relative_path)
    except ValueError as exc:
        raise ArtifactGenerationError(str(exc)) from exc


def _validate_catalog_entries(artifacts: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    paths: set[str] = set()
    for item in artifacts:
        relative_path = _validate_relative_path(str(item.get("relative_path") or ""))
        if relative_path in paths:
            raise ArtifactGenerationError(
                f"artifact generation contains a duplicate path: {relative_path}"
            )
        paths.add(relative_path)
        catalog_key = str(item.get("catalog_key") or "")
        spec = _artifact_spec(catalog_key, relative_path)
        counts[catalog_key] = counts.get(catalog_key, 0) + 1
        if spec.cardinality != "many" and counts[catalog_key] > 1:
            raise ArtifactGenerationError(
                f"artifact catalog cardinality exceeded for {catalog_key!r}: "
                f"expected {spec.cardinality}"
            )


def stage_artifact_generation(
    output_root: Path,
    generation_id: str,
    artifacts: Iterable[ArtifactSource],
    *,
    allow_existing: bool = False,
) -> Path:
    generation_id = _validate_generation_id(generation_id)
    staging_dir = _staging_dir(output_root, generation_id)
    pending_path = staging_dir / _PENDING_NAME
    if staging_dir.exists() and not allow_existing:
        raise ArtifactGenerationError(f"artifact generation staging directory already exists: {staging_dir}")
    staging_dir.mkdir(parents=True, exist_ok=allow_existing)

    try:
        existing: list[dict[str, Any]] = []
        if pending_path.is_file():
            existing = _validate_pending(_load_json(pending_path), generation_id)
        elif allow_existing and any(staging_dir.iterdir()):
            raise ArtifactGenerationError(f"staging directory has no valid pending manifest: {staging_dir}")

        _validate_catalog_entries(existing)
        by_path = {str(item.get("relative_path")): item for item in existing}
        for source in sorted(artifacts, key=lambda item: item.relative_path.casefold()):
            relative_path = _validate_relative_path(source.relative_path)
            entry = _artifact_spec(source.catalog_key, relative_path)
            if relative_path in by_path:
                raise ArtifactGenerationError(
                    f"artifact relative path is staged more than once: {relative_path}"
                )
            source_path = Path(source.path)
            if not source_path.is_file():
                if source.required:
                    raise ArtifactGenerationError(f"required artifact source is missing: {source_path}")
                continue
            destination = staging_dir / Path(*PurePosixPath(relative_path).parts)
            _copy_atomic(source_path, destination)
            next_entry = {
                "name": relative_path,
                "relative_path": relative_path,
                "catalog_key": source.catalog_key,
                "owner": entry.owner,
                "schema_version": entry.schema_version,
                "required": bool(source.required),
                "size_bytes": int(destination.stat().st_size),
                "sha256": _hash_file(destination),
            }
            by_path[relative_path] = next_entry

        ordered = [by_path[key] for key in sorted(by_path, key=str.casefold)]
        _validate_catalog_entries(ordered)
        _write_json_atomic(pending_path, _pending_payload(generation_id, ordered))
        return staging_dir
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def has_pending_generation(output_root: Path, generation_id: str) -> bool:
    return (_staging_dir(output_root, generation_id) / _PENDING_NAME).is_file()


def abort_pending_generation(output_root: Path, generation_id: str) -> None:
    shutil.rmtree(_staging_dir(output_root, generation_id), ignore_errors=True)


def _verify_artifact_entries(generation_dir: Path, artifacts: list[dict[str, Any]]) -> None:
    _validate_catalog_entries(artifacts)
    for item in artifacts:
        relative_path = _validate_relative_path(str(item.get("relative_path") or ""))
        entry = _artifact_spec(str(item.get("catalog_key") or ""), relative_path)
        if item.get("owner") != entry.owner or item.get("schema_version") != entry.schema_version:
            raise ArtifactGenerationError(f"artifact catalog contract mismatch: {relative_path}")
        path = generation_dir / Path(*PurePosixPath(relative_path).parts)
        if not path.is_file():
            raise ArtifactGenerationError(f"artifact generation file is missing: {relative_path}")
        if int(item.get("size_bytes") or -1) != path.stat().st_size:
            raise ArtifactGenerationError(f"artifact generation size mismatch: {relative_path}")
        if str(item.get("sha256") or "") != _hash_file(path):
            raise ArtifactGenerationError(f"artifact generation hash mismatch: {relative_path}")


def _manifest_payload(generation_id: str, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "artifact_type": "artifact_generation_manifest",
        "protocol_version": GENERATION_PROTOCOL_VERSION,
        "release_id": RELEASE_ID,
        "generation_id": generation_id,
        "generated_at": _utc_now(),
        "summary": {
            "artifact_count": len(artifacts),
            "required_count": sum(1 for item in artifacts if bool(item.get("required"))),
            "size_bytes": sum(int(item.get("size_bytes") or 0) for item in artifacts),
        },
        "artifacts": artifacts,
    }


def _validate_generation_dir(
    generation_dir: Path,
    *,
    expected_generation_id: str,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    ready_path = generation_dir / _READY_NAME
    manifest_path = generation_dir / _MANIFEST_NAME
    ready = _load_json(ready_path)
    manifest = _load_json(manifest_path)
    manifest_hash = _hash_file(manifest_path)
    if ready.get("artifact_type") != "artifact_generation_ready":
        raise ArtifactGenerationError("READY marker has an invalid artifact_type")
    if ready.get("protocol_version") != GENERATION_PROTOCOL_VERSION:
        raise ArtifactGenerationError("READY marker protocol version is unsupported")
    if ready.get("release_id") != RELEASE_ID or ready.get("generation_id") != expected_generation_id:
        raise ArtifactGenerationError("READY marker identity mismatch")
    if ready.get("manifest_sha256") != manifest_hash:
        raise ArtifactGenerationError("READY marker manifest hash mismatch")
    if expected_manifest_sha256 is not None and manifest_hash != expected_manifest_sha256:
        raise ArtifactGenerationError("current pointer manifest hash mismatch")
    if manifest.get("artifact_type") != "artifact_generation_manifest":
        raise ArtifactGenerationError("generation manifest has an invalid artifact_type")
    if manifest.get("protocol_version") != GENERATION_PROTOCOL_VERSION:
        raise ArtifactGenerationError("generation manifest protocol version is unsupported")
    if manifest.get("release_id") != RELEASE_ID or manifest.get("generation_id") != expected_generation_id:
        raise ArtifactGenerationError("generation manifest identity mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ArtifactGenerationError("generation manifest must contain at least one artifact")
    normalized = [dict(item) for item in artifacts if isinstance(item, dict)]
    if len(normalized) != len(artifacts):
        raise ArtifactGenerationError("generation manifest contains a non-object artifact entry")
    _verify_artifact_entries(generation_dir, normalized)
    return manifest


def _activate_validated_generation(
    output_root: Path,
    generation_id: str,
    manifest_sha256: str,
) -> Path:
    pointer = {
        "artifact_type": "artifact_generation_current",
        "protocol_version": GENERATION_PROTOCOL_VERSION,
        "release_id": RELEASE_ID,
        "generation_id": generation_id,
        "manifest_sha256": manifest_sha256,
        "activated_at": _utc_now(),
    }
    path = current_pointer_path(output_root)
    _write_json_atomic(path, pointer)
    return path


def publish_artifact_generation(
    output_root: Path,
    generation_id: str,
    *,
    additional_artifacts: Iterable[ArtifactSource] = (),
) -> GenerationSnapshot:
    output_root = Path(output_root)
    generation_id = _validate_generation_id(generation_id)
    staging_dir = _staging_dir(output_root, generation_id)
    final_dir = _generation_dir(output_root, generation_id)
    if final_dir.exists():
        raise ArtifactGenerationError(f"artifact generation is immutable and already exists: {final_dir}")
    try:
        additional = tuple(additional_artifacts)
        if additional:
            stage_artifact_generation(
                output_root,
                generation_id,
                additional,
                allow_existing=True,
            )
        pending_path = staging_dir / _PENDING_NAME
        artifacts = _validate_pending(_load_json(pending_path), generation_id)
        if not artifacts:
            raise ArtifactGenerationError("cannot publish an empty artifact generation")
        _verify_artifact_entries(staging_dir, artifacts)
        manifest_path = staging_dir / _MANIFEST_NAME
        _write_json_atomic(manifest_path, _manifest_payload(generation_id, artifacts))
        manifest_sha256 = _hash_file(manifest_path)
        _write_json_atomic(
            staging_dir / _READY_NAME,
            {
                "artifact_type": "artifact_generation_ready",
                "protocol_version": GENERATION_PROTOCOL_VERSION,
                "release_id": RELEASE_ID,
                "generation_id": generation_id,
                "manifest_sha256": manifest_sha256,
                "ready_at": _utc_now(),
            },
        )
        pending_path.unlink()
        _validate_generation_dir(
            staging_dir,
            expected_generation_id=generation_id,
            expected_manifest_sha256=manifest_sha256,
        )
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_dir, final_dir)
        manifest = _validate_generation_dir(
            final_dir,
            expected_generation_id=generation_id,
            expected_manifest_sha256=manifest_sha256,
        )
        _activate_validated_generation(output_root, generation_id, manifest_sha256)
        return GenerationSnapshot(output_root, RELEASE_ID, generation_id, final_dir, manifest)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def resolve_current_generation(output_root: Path) -> GenerationSnapshot:
    output_root = Path(output_root)
    pointer_path = current_pointer_path(output_root)
    pointer = _load_json(pointer_path)
    if pointer.get("artifact_type") != "artifact_generation_current":
        raise ArtifactGenerationError("current generation pointer has an invalid artifact_type")
    if pointer.get("protocol_version") != GENERATION_PROTOCOL_VERSION:
        raise ArtifactGenerationError("current generation pointer protocol version is unsupported")
    if pointer.get("release_id") != RELEASE_ID:
        raise ArtifactGenerationError("current generation pointer release id is unsupported")
    generation_id = _validate_generation_id(str(pointer.get("generation_id") or ""))
    manifest_sha256 = str(pointer.get("manifest_sha256") or "")
    if len(manifest_sha256) != 64:
        raise ArtifactGenerationError("current generation pointer has no valid manifest hash")
    generation_dir = _generation_dir(output_root, generation_id)
    manifest = _validate_generation_dir(
        generation_dir,
        expected_generation_id=generation_id,
        expected_manifest_sha256=manifest_sha256,
    )
    return GenerationSnapshot(output_root, RELEASE_ID, generation_id, generation_dir, manifest)


def resolve_generation(output_root: Path, generation_id: str) -> GenerationSnapshot:
    output_root = Path(output_root)
    generation_id = _validate_generation_id(generation_id)
    generation_dir = _generation_dir(output_root, generation_id)
    manifest = _validate_generation_dir(generation_dir, expected_generation_id=generation_id)
    return GenerationSnapshot(output_root, RELEASE_ID, generation_id, generation_dir, manifest)


def activate_generation(output_root: Path, generation_id: str) -> GenerationSnapshot:
    snapshot = resolve_generation(output_root, generation_id)
    output_root = snapshot.output_root
    generation_id = snapshot.generation_id
    generation_dir = snapshot.generation_dir
    manifest_sha256 = _hash_file(generation_dir / _MANIFEST_NAME)
    _activate_validated_generation(output_root, generation_id, manifest_sha256)
    return snapshot


def list_ready_generations(output_root: Path) -> tuple[str, ...]:
    root = _release_root(Path(output_root)) / "generations"
    if not root.is_dir():
        return ()
    generation_ids = [
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / _READY_NAME).is_file()
    ]
    return tuple(sorted(generation_ids, key=str.casefold))


__all__ = [
    "ArtifactGenerationError",
    "ArtifactSource",
    "GENERATION_PROTOCOL_VERSION",
    "GenerationSnapshot",
    "RELEASE_ID",
    "abort_pending_generation",
    "activate_generation",
    "current_pointer_path",
    "has_pending_generation",
    "list_ready_generations",
    "publish_artifact_generation",
    "resolve_generation",
    "resolve_current_generation",
    "stage_artifact_generation",
]
