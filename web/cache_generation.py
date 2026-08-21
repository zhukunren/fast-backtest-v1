"""Immutable schema-v2 generations for strategy-library Web caches."""
from __future__ import annotations

import json

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from backtest.artifacts import (
    ArtifactGenerationError,
    ArtifactSource,
    GENERATION_PROTOCOL_VERSION,
    GenerationSnapshot,
    RELEASE_ID,
    activate_generation,
    current_pointer_path,
    list_ready_generations,
    publish_artifact_generation,
    resolve_current_generation,
    resolve_generation,
    stage_artifact_generation,
)
from core.artifact_schema import ARTIFACT_SCHEMA_VERSION
from web.cache_payload_contracts import (
    manifest_entries as _manifest_entries,
    meta_status as _meta_status,
    read_payload as _read_payload,
    validate_payload as _validate_payload,
)


WEB_CACHE_LAYOUT_VERSION = 1
WEB_CACHE_RELEASE_ID = (
    f"v{WEB_CACHE_LAYOUT_VERSION}-schema-v{ARTIFACT_SCHEMA_VERSION}"
)
WEB_CACHE_OUTPUT_DIR = "web_cache"
WEB_CACHE_ARTIFACT_DIR = "cache"
WEB_CACHE_LATEST_ATTEMPT_FILE = "latest_attempt.json"
WEB_CACHE_ATTEMPT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class WebCacheArtifactSpec:
    filename: str
    catalog_key: str
    artifact_type: str
    required: bool = False


_SPECS: tuple[WebCacheArtifactSpec, ...] = (
    WebCacheArtifactSpec("meta.json", "web_cache_meta", "run_metadata", required=True),
    WebCacheArtifactSpec("net_value.json", "web_cache_net_value", "net_value"),
    WebCacheArtifactSpec("trades.json", "web_cache_trades", "trades"),
    WebCacheArtifactSpec(
        "daily_holdings.json",
        "web_cache_daily_holdings",
        "daily_holdings",
    ),
    WebCacheArtifactSpec("market_state.json", "web_cache_market_state", "market_state"),
    WebCacheArtifactSpec("positions.json", "web_cache_positions", "positions"),
    WebCacheArtifactSpec(
        "position_changes.json",
        "web_cache_position_changes",
        "position_changes",
    ),
)
WEB_CACHE_ARTIFACT_SPECS: Mapping[str, WebCacheArtifactSpec] = MappingProxyType(
    {spec.filename: spec for spec in _SPECS}
)


@dataclass(frozen=True, slots=True)
class WebCacheSnapshot:
    strategy_dir: Path
    generation: GenerationSnapshot

    @property
    def generation_id(self) -> str:
        return self.generation.generation_id

    @property
    def cache_dir(self) -> Path:
        return self.generation.generation_dir / WEB_CACHE_ARTIFACT_DIR

    @property
    def manifest(self) -> dict[str, Any]:
        return self.generation.manifest

    def artifact_path(self, filename: str) -> Path:
        spec = _artifact_spec(filename)
        return self.generation.artifact_path(_relative_path(spec.filename))


@dataclass(frozen=True, slots=True)
class _ValidatedCurrentSnapshot:
    pointer_identity: tuple[str, int, str, str, str]
    snapshot: WebCacheSnapshot


_VALIDATED_CURRENT_SNAPSHOTS: dict[Path, _ValidatedCurrentSnapshot] = {}
_VALIDATED_CURRENT_SNAPSHOTS_LOCK = threading.RLock()


def web_cache_output_root(strategy_dir: Path) -> Path:
    return Path(strategy_dir) / WEB_CACHE_OUTPUT_DIR


def web_cache_current_pointer_path(strategy_dir: Path) -> Path:
    return current_pointer_path(web_cache_output_root(strategy_dir))


def web_cache_latest_attempt_pointer_path(strategy_dir: Path) -> Path:
    return web_cache_output_root(strategy_dir) / WEB_CACHE_LATEST_ATTEMPT_FILE


def _strategy_cache_key(strategy_dir: Path) -> Path:
    return Path(strategy_dir).resolve(strict=False)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _current_pointer_identity(strategy_dir: Path) -> tuple[str, int, str, str, str]:
    pointer = _read_payload(web_cache_current_pointer_path(strategy_dir))
    artifact_type = str(pointer.get("artifact_type") or "")
    protocol_version = pointer.get("protocol_version")
    release_id = str(pointer.get("release_id") or "")
    generation_id = str(pointer.get("generation_id") or "")
    manifest_sha256 = str(pointer.get("manifest_sha256") or "")
    if artifact_type != "artifact_generation_current":
        raise ArtifactGenerationError("current generation pointer has an invalid artifact_type")
    if protocol_version != GENERATION_PROTOCOL_VERSION:
        raise ArtifactGenerationError("current generation pointer protocol version is unsupported")
    if release_id != WEB_CACHE_RELEASE_ID:
        raise ArtifactGenerationError("current generation pointer release id is unsupported")
    if not generation_id or len(manifest_sha256) != 64:
        raise ArtifactGenerationError("current generation pointer has an invalid identity")
    return (
        artifact_type,
        int(protocol_version),
        release_id,
        generation_id,
        manifest_sha256,
    )


def _cache_validated_current_snapshot(
    strategy_dir: Path,
    snapshot: WebCacheSnapshot,
) -> None:
    identity = _current_pointer_identity(strategy_dir)
    if identity[3] != snapshot.generation_id:
        raise ArtifactGenerationError("current generation changed during validation")
    with _VALIDATED_CURRENT_SNAPSHOTS_LOCK:
        _VALIDATED_CURRENT_SNAPSHOTS[_strategy_cache_key(strategy_dir)] = (
            _ValidatedCurrentSnapshot(identity, snapshot)
        )


def invalidate_web_cache_snapshot_cache(strategy_dir: Path | None = None) -> None:
    with _VALIDATED_CURRENT_SNAPSHOTS_LOCK:
        if strategy_dir is None:
            _VALIDATED_CURRENT_SNAPSHOTS.clear()
        else:
            _VALIDATED_CURRENT_SNAPSHOTS.pop(_strategy_cache_key(strategy_dir), None)


def _relative_path(filename: str) -> str:
    return f"{WEB_CACHE_ARTIFACT_DIR}/{filename}"


def _artifact_spec(filename: str) -> WebCacheArtifactSpec:
    try:
        return WEB_CACHE_ARTIFACT_SPECS[str(filename)]
    except KeyError as exc:
        raise ArtifactGenerationError(f"unsupported Web cache artifact: {filename!r}") from exc




def _validate_snapshot(strategy_dir: Path, generation: GenerationSnapshot) -> WebCacheSnapshot:
    if generation.release_id != WEB_CACHE_RELEASE_ID or RELEASE_ID != WEB_CACHE_RELEASE_ID:
        raise ArtifactGenerationError(
            "Web cache release identity does not match its layout/schema contract"
        )
    entries = _manifest_entries(generation)
    allowed_paths = {_relative_path(spec.filename): spec for spec in _SPECS}
    unknown = sorted(set(entries) - set(allowed_paths), key=str.casefold)
    if unknown:
        raise ArtifactGenerationError(f"Web cache manifest contains unsupported paths: {unknown}")

    for spec in _SPECS:
        relative_path = _relative_path(spec.filename)
        entry = entries.get(relative_path)
        if entry is None:
            if spec.required:
                raise ArtifactGenerationError(f"required Web cache artifact is missing: {spec.filename}")
            continue
        if entry.get("catalog_key") != spec.catalog_key:
            raise ArtifactGenerationError(f"Web cache catalog key mismatch: {spec.filename}")
        if bool(entry.get("required")) != spec.required:
            raise ArtifactGenerationError(f"Web cache required flag mismatch: {spec.filename}")
        _validate_payload(generation.artifact_path(relative_path), spec)

    meta = _validate_payload(
        generation.artifact_path(_relative_path("meta.json")),
        _artifact_spec("meta.json"),
    )
    status = _meta_status(meta)
    if status == "completed" and _relative_path("net_value.json") not in entries:
        raise ArtifactGenerationError("completed Web cache generation is missing net_value.json")
    return WebCacheSnapshot(strategy_dir=Path(strategy_dir), generation=generation)


def _publish_unactivated_generation(
    strategy_dir: Path,
    generation_id: str,
    sources: list[ArtifactSource],
) -> GenerationSnapshot:
    """Finalize a READY generation without ever changing the default pointer."""
    strategy_root = Path(strategy_dir)
    strategy_root.mkdir(parents=True, exist_ok=True)
    output_root = web_cache_output_root(strategy_root)
    with tempfile.TemporaryDirectory(
        prefix=".web-cache-history-",
        dir=str(strategy_root),
    ) as temp_dir:
        temporary_output_root = Path(temp_dir) / WEB_CACHE_OUTPUT_DIR
        stage_artifact_generation(temporary_output_root, generation_id, sources)
        staged = publish_artifact_generation(temporary_output_root, generation_id)
        relative_generation_dir = staged.generation_dir.relative_to(
            temporary_output_root
        )
        destination = output_root / relative_generation_dir
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ArtifactGenerationError(
                f"artifact generation is immutable and already exists: {destination}"
            )
        os.replace(staged.generation_dir, destination)

    try:
        return resolve_generation(output_root, generation_id)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _attempt_error(meta: Mapping[str, Any]) -> str:
    for key in ("error", "reason", "message"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    extra = meta.get("extra")
    if isinstance(extra, Mapping):
        for key in ("error", "reason", "message"):
            value = str(extra.get(key) or "").strip()
            if value:
                return value
    return ""


def _record_latest_attempt(
    strategy_dir: Path,
    *,
    generation_id: str,
    meta: Mapping[str, Any],
    activated: bool,
) -> None:
    evaluation = meta.get("evaluation_identity")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    current_generation_id = ""
    try:
        current_generation_id = _current_pointer_identity(strategy_dir)[3]
    except ArtifactGenerationError:
        pass
    _write_json_atomic(
        web_cache_latest_attempt_pointer_path(strategy_dir),
        {
            "artifact_type": "web_cache_latest_attempt",
            "schema_version": WEB_CACHE_ATTEMPT_SCHEMA_VERSION,
            "release_id": WEB_CACHE_RELEASE_ID,
            "generation_id": generation_id,
            "status": _meta_status(meta),
            "error": _attempt_error(meta),
            "activated": bool(activated),
            "current_generation_id": current_generation_id,
            "evaluation_id": str(
                meta.get("evaluation_id") or evaluation.get("id") or ""
            ),
            "evaluation_context_id": str(
                meta.get("evaluation_context_id")
                or evaluation.get("context_id")
                or ""
            ),
            "run_kind": str(evaluation.get("run_kind") or ""),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def publish_web_cache_generation(
    strategy_dir: Path,
    generation_id: str,
    source_dir: Path,
    *,
    activate: bool = True,
) -> WebCacheSnapshot:
    """Validate and publish a cache generation, optionally without activation."""
    source_root = Path(source_dir)
    sources: list[ArtifactSource] = []
    payloads: dict[str, dict[str, Any]] = {}
    for spec in _SPECS:
        path = source_root / spec.filename
        if not path.is_file():
            if spec.required:
                raise ArtifactGenerationError(f"required Web cache source is missing: {path}")
            continue
        payloads[spec.filename] = _validate_payload(path, spec)
        sources.append(
            ArtifactSource(
                path=path,
                relative_path=_relative_path(spec.filename),
                catalog_key=spec.catalog_key,
                required=spec.required,
            )
        )

    meta = payloads["meta.json"]
    status = _meta_status(meta)
    if status == "completed" and "net_value.json" not in payloads:
        raise ArtifactGenerationError("completed Web cache generation is missing net_value.json")

    strategy_path = Path(strategy_dir)
    output_root = web_cache_output_root(strategy_path)
    if activate:
        stage_artifact_generation(output_root, generation_id, sources)
        generation = publish_artifact_generation(output_root, generation_id)
    else:
        generation = _publish_unactivated_generation(
            strategy_path,
            generation_id,
            sources,
        )
    snapshot = _validate_snapshot(strategy_path, generation)
    if activate:
        _cache_validated_current_snapshot(strategy_path, snapshot)
    _record_latest_attempt(
        strategy_path,
        generation_id=generation_id,
        meta=meta,
        activated=activate,
    )
    return snapshot


def resolve_current_web_cache(
    strategy_dir: Path,
    *,
    force_validate: bool = False,
) -> WebCacheSnapshot:
    strategy_path = Path(strategy_dir)
    cache_key = _strategy_cache_key(strategy_path)
    for _attempt in range(3):
        identity = _current_pointer_identity(strategy_path)
        if not force_validate:
            with _VALIDATED_CURRENT_SNAPSHOTS_LOCK:
                cached = _VALIDATED_CURRENT_SNAPSHOTS.get(cache_key)
            if cached is not None and cached.pointer_identity == identity:
                return cached.snapshot
        try:
            generation = resolve_current_generation(
                web_cache_output_root(strategy_path)
            )
            snapshot = _validate_snapshot(strategy_path, generation)
            after = _current_pointer_identity(strategy_path)
        except Exception:
            invalidate_web_cache_snapshot_cache(strategy_path)
            raise
        if after != identity:
            invalidate_web_cache_snapshot_cache(strategy_path)
            continue
        with _VALIDATED_CURRENT_SNAPSHOTS_LOCK:
            _VALIDATED_CURRENT_SNAPSHOTS[cache_key] = _ValidatedCurrentSnapshot(
                after,
                snapshot,
            )
        return snapshot
    raise ArtifactGenerationError("current generation changed repeatedly during validation")


def resolve_latest_web_cache_attempt(
    strategy_dir: Path,
) -> tuple[WebCacheSnapshot, dict[str, Any]] | None:
    pointer_path = web_cache_latest_attempt_pointer_path(strategy_dir)
    if not pointer_path.is_file():
        return None
    pointer = _read_payload(pointer_path)
    if pointer.get("artifact_type") != "web_cache_latest_attempt":
        raise ArtifactGenerationError("latest Web cache attempt pointer has an invalid artifact_type")
    if pointer.get("schema_version") != WEB_CACHE_ATTEMPT_SCHEMA_VERSION:
        raise ArtifactGenerationError("latest Web cache attempt pointer schema is unsupported")
    if pointer.get("release_id") != WEB_CACHE_RELEASE_ID:
        raise ArtifactGenerationError("latest Web cache attempt pointer release is unsupported")
    generation_id = str(pointer.get("generation_id") or "")
    snapshot = resolve_web_cache_generation(strategy_dir, generation_id)
    return snapshot, pointer


def resolve_web_cache_generation(strategy_dir: Path, generation_id: str) -> WebCacheSnapshot:
    generation = resolve_generation(web_cache_output_root(strategy_dir), generation_id)
    return _validate_snapshot(Path(strategy_dir), generation)


def activate_web_cache_generation(strategy_dir: Path, generation_id: str) -> WebCacheSnapshot:
    output_root = web_cache_output_root(strategy_dir)
    generation = resolve_generation(output_root, generation_id)
    snapshot = _validate_snapshot(Path(strategy_dir), generation)
    activate_generation(output_root, generation_id)
    _cache_validated_current_snapshot(strategy_dir, snapshot)
    return snapshot


def list_web_cache_generations(strategy_dir: Path) -> tuple[str, ...]:
    return list_ready_generations(web_cache_output_root(strategy_dir))




def clear_web_cache_generations(strategy_dir: Path) -> bool:
    root = web_cache_output_root(strategy_dir)
    if not root.is_dir():
        return False
    invalidate_web_cache_snapshot_cache(strategy_dir)
    shutil.rmtree(root)
    return True




__all__ = [
    "WEB_CACHE_ARTIFACT_SPECS",
    "WEB_CACHE_ATTEMPT_SCHEMA_VERSION",
    "WEB_CACHE_LAYOUT_VERSION",
    "WEB_CACHE_LATEST_ATTEMPT_FILE",
    "WEB_CACHE_RELEASE_ID",
    "WebCacheArtifactSpec",
    "WebCacheSnapshot",
    "activate_web_cache_generation",
    "clear_web_cache_generations",
    "invalidate_web_cache_snapshot_cache",
    "list_web_cache_generations",
    "publish_web_cache_generation",
    "resolve_current_web_cache",
    "resolve_latest_web_cache_attempt",
    "resolve_web_cache_generation",
    "web_cache_current_pointer_path",
    "web_cache_latest_attempt_pointer_path",
    "web_cache_output_root",
]
