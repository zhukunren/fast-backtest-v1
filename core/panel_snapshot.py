"""Immutable panel versions with an atomic ``current.json`` pointer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.panel_identity import (
    DataIdentityError,
    canonical_manifest_hash,
    dataset_id_for_manifest,
    semantic_fingerprint,
    source_config_hash,
    validate_manifest_identity_contract,
)


PANEL_SNAPSHOT_PROTOCOL_VERSION = 1
_DATASET_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_PANEL_NAME = "stock_daily.parquet"
_MANIFEST_NAME = "stock_daily.manifest.json"
_READY_NAME = "READY.json"
_CURRENT_NAME = "current.json"
_REQUIRED_MANIFEST_COLUMNS = frozenset(
    {"trade_date", "stock_code", "open", "high", "low", "close", "volume"}
)


class PanelSnapshotError(RuntimeError):
    """Raised when a panel version cannot be published or verified."""


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    root: Path
    dataset_id: str
    version_dir: Path
    panel_path: Path
    manifest_path: Path
    manifest: dict[str, Any]
    ready: dict[str, Any]
    content_verified: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_dataset_id(value: object) -> str:
    dataset_id = str(value or "").strip().casefold()
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise PanelSnapshotError(f"invalid panel dataset_id: {value!r}")
    return dataset_id


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
        raise PanelSnapshotError(f"cannot read panel snapshot JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PanelSnapshotError(f"panel snapshot JSON root must be an object: {path}")
    return payload


def _validate_manifest_metadata(manifest: dict[str, Any]) -> None:
    if str(manifest.get("format") or "").strip().casefold() != "parquet":
        raise PanelSnapshotError("panel manifest format must be parquet")
    try:
        validate_manifest_identity_contract(manifest)
    except DataIdentityError as exc:
        raise PanelSnapshotError(str(exc)) from None
    for key in ("source", "output", "min_trade_date", "max_trade_date"):
        if not str(manifest.get(key) or "").strip():
            raise PanelSnapshotError(f"panel manifest {key} is missing")


def _validate_manifest_counts(manifest: dict[str, Any]) -> None:
    for key in ("rows", "stocks"):
        value = manifest.get(key)
        if isinstance(value, bool):
            raise PanelSnapshotError(f"panel manifest {key} is invalid")
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            raise PanelSnapshotError(f"panel manifest {key} is invalid") from None
        if numeric <= 0:
            raise PanelSnapshotError(f"panel manifest {key} must be positive")
    duplicate_keys = manifest.get("duplicate_keys")
    if isinstance(duplicate_keys, bool):
        raise PanelSnapshotError("panel manifest duplicate_keys is invalid")
    try:
        duplicate_count = int(duplicate_keys)
    except (TypeError, ValueError):
        raise PanelSnapshotError("panel manifest duplicate_keys is invalid") from None
    if duplicate_count != 0:
        raise PanelSnapshotError(f"panel manifest reports duplicate keys: {duplicate_count}")


def _validate_manifest_columns_and_dates(manifest: dict[str, Any]) -> None:
    columns = manifest.get("columns")
    if not isinstance(columns, (list, tuple)):
        raise PanelSnapshotError("panel manifest columns must be a list")
    normalized_columns = {str(column).strip().casefold() for column in columns}
    missing = sorted(_REQUIRED_MANIFEST_COLUMNS - normalized_columns, key=str.casefold)
    if missing:
        raise PanelSnapshotError(f"panel manifest missing required columns: {missing}")
    try:
        min_date = date.fromisoformat(str(manifest["min_trade_date"])[:10])
        max_date = date.fromisoformat(str(manifest["max_trade_date"])[:10])
    except (TypeError, ValueError):
        raise PanelSnapshotError("panel manifest date range is invalid") from None
    if min_date > max_date:
        raise PanelSnapshotError("panel manifest date range is reversed")


def _validate_manifest_contract(manifest: dict[str, Any]) -> None:
    """Reject structurally complete but unusable panel manifests early."""

    _validate_manifest_metadata(manifest)
    _validate_manifest_counts(manifest)
    _validate_manifest_columns_and_dates(manifest)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_and_hash(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        for chunk in iter(lambda: src.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
            dst.write(chunk)
    shutil.copystat(source, destination)
    return digest.hexdigest()


def current_pointer_path(root: str | Path) -> Path:
    return Path(root) / _CURRENT_NAME


def version_path(root: str | Path, dataset_id: str) -> Path:
    return Path(root) / "versions" / _validate_dataset_id(dataset_id)


def _version_manifest(
    source: dict[str, Any],
    *,
    content_sha256: str,
    source_panel_path: Path,
    source_manifest_path: Path,
) -> dict[str, Any]:
    payload = dict(source)
    payload["file_sha256"] = content_sha256
    payload["output"] = _PANEL_NAME
    payload["source_config_hash"] = source_config_hash(payload)
    payload["semantic_fingerprint"] = semantic_fingerprint(payload)
    payload["dataset_id"] = dataset_id_for_manifest(payload)
    payload["canonical_manifest_sha256"] = canonical_manifest_hash(payload)
    payload["provenance"] = {
        "path": str(source_panel_path.resolve()),
        "manifest_path": str(source_manifest_path.resolve()),
        "created_at_utc": str(source.get("created_at_utc") or ""),
        "frozen_at_utc": _utc_now(),
    }
    return payload


def _ready_payload(manifest: dict[str, Any], *, panel_path: Path, manifest_path: Path) -> dict[str, Any]:
    return {
        "artifact_type": "panel_snapshot_ready",
        "protocol_version": PANEL_SNAPSHOT_PROTOCOL_VERSION,
        "dataset_id": str(manifest["dataset_id"]),
        "content_sha256": str(manifest["file_sha256"]),
        "canonical_manifest_sha256": canonical_manifest_hash(manifest),
        "manifest_file_sha256": _hash_file(manifest_path),
        "panel_size_bytes": int(panel_path.stat().st_size),
        "ready_at_utc": _utc_now(),
    }


def _resolve_snapshot_dataset_id(
    snapshot_root: Path,
    dataset_id: str | None,
) -> tuple[str, dict[str, Any] | None]:
    if dataset_id is not None:
        return _validate_dataset_id(dataset_id), None
    pointer = _load_json(current_pointer_path(snapshot_root))
    if pointer.get("artifact_type") != "panel_snapshot_current":
        raise PanelSnapshotError("panel current pointer has an invalid artifact_type")
    if pointer.get("protocol_version") != PANEL_SNAPSHOT_PROTOCOL_VERSION:
        raise PanelSnapshotError("panel current pointer protocol version is unsupported")
    return _validate_dataset_id(pointer.get("dataset_id")), pointer


def _validate_ready_marker(ready: dict[str, Any]) -> None:
    if ready.get("artifact_type") != "panel_snapshot_ready":
        raise PanelSnapshotError("panel READY marker has an invalid artifact_type")
    if ready.get("protocol_version") != PANEL_SNAPSHOT_PROTOCOL_VERSION:
        raise PanelSnapshotError("panel READY protocol version is unsupported")


def _validate_snapshot_identity(
    manifest: dict[str, Any],
    ready: dict[str, Any],
    dataset_id: str,
) -> str:
    if str(ready.get("dataset_id") or "") != dataset_id or str(manifest.get("dataset_id") or "") != dataset_id:
        raise PanelSnapshotError("panel snapshot dataset identity does not match its directory")
    expected_dataset_id = dataset_id_for_manifest(manifest)
    if expected_dataset_id != dataset_id:
        raise PanelSnapshotError("panel snapshot dataset_id does not match canonical manifest identity")
    expected_semantic = semantic_fingerprint(manifest)
    declared_semantic = str(manifest.get("semantic_fingerprint") or "").strip().casefold()
    if declared_semantic and declared_semantic != expected_semantic:
        raise PanelSnapshotError("panel snapshot semantic fingerprint mismatch")
    expected_source_config = source_config_hash(manifest)
    declared_source_config = str(manifest.get("source_config_hash") or "").strip().casefold()
    if declared_source_config and declared_source_config != expected_source_config:
        raise PanelSnapshotError("panel snapshot source config hash mismatch")
    canonical_sha256 = canonical_manifest_hash(manifest)
    declared_canonical = str(manifest.get("canonical_manifest_sha256") or "").strip().casefold()
    if declared_canonical and declared_canonical != canonical_sha256:
        raise PanelSnapshotError("panel snapshot declared canonical manifest hash mismatch")
    if canonical_sha256 != str(ready.get("canonical_manifest_sha256") or ""):
        raise PanelSnapshotError("panel snapshot canonical manifest hash mismatch")
    return canonical_sha256


def _validate_snapshot_files(
    panel_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    ready: dict[str, Any],
    *,
    verify_content: bool,
) -> str:
    if _hash_file(manifest_path) != str(ready.get("manifest_file_sha256") or ""):
        raise PanelSnapshotError("panel snapshot manifest file hash mismatch")
    if int(panel_path.stat().st_size) != int(ready.get("panel_size_bytes") or -1):
        raise PanelSnapshotError("panel snapshot size mismatch")
    declared_content = str(ready.get("content_sha256") or "")
    if declared_content != str(manifest.get("file_sha256") or ""):
        raise PanelSnapshotError("panel snapshot content hash declarations disagree")
    if verify_content and _hash_file(panel_path) != declared_content:
        raise PanelSnapshotError("panel snapshot content hash mismatch")
    return declared_content


def _validate_current_pointer(
    pointer: dict[str, Any] | None,
    *,
    canonical_sha256: str,
    content_sha256: str,
) -> None:
    if pointer is None:
        return
    if str(pointer.get("canonical_manifest_sha256") or "") != canonical_sha256:
        raise PanelSnapshotError("panel current pointer manifest hash mismatch")
    if str(pointer.get("content_sha256") or "") != content_sha256:
        raise PanelSnapshotError("panel current pointer content hash mismatch")


def resolve_panel_snapshot(
    root: str | Path,
    dataset_id: str | None = None,
    *,
    verify_content: bool = False,
) -> PanelSnapshot:
    snapshot_root = Path(root).resolve()
    dataset_id, pointer = _resolve_snapshot_dataset_id(snapshot_root, dataset_id)
    directory = version_path(snapshot_root, dataset_id)
    panel_path = directory / _PANEL_NAME
    manifest_path = directory / _MANIFEST_NAME
    ready_path = directory / _READY_NAME
    if not panel_path.is_file() or not manifest_path.is_file() or not ready_path.is_file():
        raise PanelSnapshotError(f"panel version is incomplete: {directory}")

    manifest = _load_json(manifest_path)
    ready = _load_json(ready_path)
    _validate_manifest_contract(manifest)
    _validate_ready_marker(ready)
    canonical_sha256 = _validate_snapshot_identity(manifest, ready, dataset_id)
    declared_content = _validate_snapshot_files(
        panel_path,
        manifest_path,
        manifest,
        ready,
        verify_content=verify_content,
    )
    _validate_current_pointer(
        pointer,
        canonical_sha256=canonical_sha256,
        content_sha256=declared_content,
    )
    return PanelSnapshot(
        root=snapshot_root,
        dataset_id=dataset_id,
        version_dir=directory,
        panel_path=panel_path,
        manifest_path=manifest_path,
        manifest=manifest,
        ready=ready,
        content_verified=bool(verify_content),
    )


def activate_panel_snapshot(root: str | Path, dataset_id: str) -> PanelSnapshot:
    snapshot = resolve_panel_snapshot(root, dataset_id, verify_content=True)
    pointer = {
        "artifact_type": "panel_snapshot_current",
        "protocol_version": PANEL_SNAPSHOT_PROTOCOL_VERSION,
        "dataset_id": snapshot.dataset_id,
        "canonical_manifest_sha256": str(snapshot.ready["canonical_manifest_sha256"]),
        "content_sha256": str(snapshot.ready["content_sha256"]),
        "activated_at_utc": _utc_now(),
    }
    _write_json_atomic(current_pointer_path(snapshot.root), pointer)
    resolved = resolve_panel_snapshot(snapshot.root)
    return replace(resolved, content_verified=True)


def publish_panel_snapshot(
    panel_path: str | Path,
    manifest_path: str | Path,
    root: str | Path,
    *,
    activate: bool = True,
) -> PanelSnapshot:
    source_panel = Path(panel_path).resolve()
    source_manifest = Path(manifest_path).resolve()
    snapshot_root = Path(root).resolve()
    if not source_panel.is_file() or not source_manifest.is_file():
        raise PanelSnapshotError("panel snapshot source panel and manifest must both exist")
    source_payload = _load_json(source_manifest)
    _validate_manifest_contract(source_payload)
    staging = snapshot_root / ".staging" / uuid4().hex
    staged_panel = staging / _PANEL_NAME
    staged_manifest = staging / _MANIFEST_NAME
    try:
        content_sha256 = _copy_and_hash(source_panel, staged_panel)
        declared = str(source_payload.get("file_sha256") or "").strip().casefold()
        if declared and declared != content_sha256:
            raise PanelSnapshotError(
                f"source panel content hash mismatch: expected={declared} actual={content_sha256}"
            )
        manifest = _version_manifest(
            source_payload,
            content_sha256=content_sha256,
            source_panel_path=source_panel,
            source_manifest_path=source_manifest,
        )
        dataset_id = _validate_dataset_id(manifest["dataset_id"])
        staged_manifest.write_bytes(_json_bytes(manifest))
        ready = _ready_payload(manifest, panel_path=staged_panel, manifest_path=staged_manifest)
        (staging / _READY_NAME).write_bytes(_json_bytes(ready))
        destination = version_path(snapshot_root, dataset_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = resolve_panel_snapshot(snapshot_root, dataset_id, verify_content=True)
            if str(existing.ready.get("content_sha256")) != content_sha256:
                raise PanelSnapshotError(f"panel version collision for dataset_id={dataset_id}")
        else:
            os.replace(staging, destination)
        if activate:
            return activate_panel_snapshot(snapshot_root, dataset_id)
        resolved = resolve_panel_snapshot(snapshot_root, dataset_id)
        return replace(resolved, content_verified=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


__all__ = [
    "PANEL_SNAPSHOT_PROTOCOL_VERSION",
    "PanelSnapshot",
    "PanelSnapshotError",
    "activate_panel_snapshot",
    "current_pointer_path",
    "publish_panel_snapshot",
    "resolve_panel_snapshot",
    "version_path",
]
