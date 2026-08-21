from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Mapping

from core.research_identity import sha256_file, stable_hash


_VOLATILE_MANIFEST_KEYS = frozenset(
    {
        "created_at_utc",
        "updated_at_utc",
        "generated_at",
        "output",
        "path",
        "manifest_path",
        "provenance",
        "identity_hash",
        "manifest_sha256",
        "canonical_manifest_sha256",
        "dataset_id",
        "semantic_fingerprint",
        "content_verified",
        "declared_matches_content",
    }
)

PANEL_MANIFEST_SCHEMA_VERSION = 3


class DataIdentityError(RuntimeError):
    """Raised when strict content verification contradicts the manifest."""


def _validate_manifest_schema(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise DataIdentityError("panel manifest must be an object")
    if payload.get("schema_version") != PANEL_MANIFEST_SCHEMA_VERSION:
        raise DataIdentityError(
            "panel manifest schema is unsupported for stable identity: "
            f"expected={PANEL_MANIFEST_SCHEMA_VERSION} actual={payload.get('schema_version')!r}; "
            "rebuild the manifest with tools/build_panel_parquet.py"
        )
    if not isinstance(payload.get("source_config"), Mapping):
        raise DataIdentityError(
            "panel manifest source_config is required for stable identity; "
            "rebuild the manifest with tools/build_panel_parquet.py"
        )


def _strip_volatile(value: Any) -> Any:
    """Return a deterministic JSON-safe value without provenance fields."""

    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]).casefold())
            if str(key).casefold() not in _VOLATILE_MANIFEST_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_strip_volatile(item) for item in sorted(value, key=lambda item: repr(item).casefold())]
    if isinstance(value, Path):
        return str(value)
    return value


def canonical_source_config(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize the current manifest's nested source configuration."""

    source = dict(payload or {})
    nested = source.get("source_config")
    if not isinstance(nested, Mapping):
        return {}
    raw = dict(nested)
    normalized = _strip_volatile(raw)
    if not isinstance(normalized, dict):
        return {}
    return normalized


def validate_manifest_identity_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the current source config or reject an obsolete manifest schema."""

    _validate_manifest_schema(payload)
    for key in (
        "file_sha256",
        "source_config_hash",
        "semantic_fingerprint",
        "dataset_id",
        "canonical_manifest_sha256",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DataIdentityError(
                f"panel manifest {key} is required for stable identity; "
                "rebuild the manifest with tools/build_panel_parquet.py"
            )
    config = canonical_source_config(payload)
    if not config:
        raise DataIdentityError("panel manifest source_config must not be empty")
    expected_source_config = stable_hash(config)
    expected_semantic = semantic_fingerprint(payload)
    expected_dataset_id = dataset_id_for_manifest(payload)
    expected_canonical = canonical_manifest_hash(payload)
    declared_values = {
        "source_config_hash": expected_source_config,
        "semantic_fingerprint": expected_semantic,
        "dataset_id": expected_dataset_id,
        "canonical_manifest_sha256": expected_canonical,
    }
    mismatches = [
        key
        for key, expected in declared_values.items()
        if str(payload.get(key) or "").strip().casefold() != expected
    ]
    if mismatches:
        raise DataIdentityError(
            "panel manifest stable identity fields disagree with canonical values: "
            + ", ".join(sorted(mismatches, key=str.casefold))
        )
    return config


def source_config_hash(payload: Mapping[str, Any] | None) -> str:
    """Hash only stable source configuration fields."""

    return stable_hash(canonical_source_config(payload))


def canonical_manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable portion of a current-schema panel manifest."""

    if not isinstance(payload, Mapping):
        raise TypeError("panel manifest must be a mapping")
    _validate_manifest_schema(payload)
    config = canonical_source_config(payload)
    normalized = _strip_volatile(payload)
    if not isinstance(normalized, dict):
        raise TypeError("panel manifest must normalize to an object")
    normalized.pop("source_config", None)
    normalized.pop("source_config_hash", None)
    normalized["source_config_hash"] = stable_hash(config)
    return normalized


def canonical_manifest_hash(payload: Mapping[str, Any]) -> str:
    """Hash the canonical, non-volatile manifest representation."""

    return stable_hash(canonical_manifest_payload(payload))


def semantic_fingerprint(payload: Mapping[str, Any]) -> str:
    """Hash logical schema/content metadata without the physical file hash."""

    normalized = canonical_manifest_payload(payload)
    normalized.pop("file_sha256", None)
    normalized.pop("content_sha256", None)
    return stable_hash(normalized)


def dataset_id_for_manifest(payload: Mapping[str, Any]) -> str:
    """Return a stable version id, independent of path and creation time."""

    semantic = semantic_fingerprint(payload)
    content = str(payload.get("file_sha256") or payload.get("content_sha256") or "").strip().casefold()
    return stable_hash({"semantic_fingerprint": semantic, "content_sha256": content})


def _panel_manifest_path(panel_path: Path) -> Path:
    if panel_path.suffix.casefold() == ".parquet":
        return panel_path.with_suffix(".manifest.json")
    return panel_path.with_name(f"{panel_path.name}.manifest.json")


def _resolve_snapshot_current_path(path: Path) -> Path:
    pointer_path = path / "current.json"
    if not path.is_dir() or not pointer_path.is_file():
        return path
    try:
        from core.panel_snapshot import resolve_panel_snapshot

        return resolve_panel_snapshot(path).panel_path
    except Exception as exc:
        raise DataIdentityError(f"panel snapshot current pointer is invalid: {path}: {exc}") from None


def _panel_identity_context(cfg: Any) -> tuple[str, str, Path, Path]:
    panel_cfg = getattr(cfg, "panel", None)
    source = str(getattr(panel_cfg, "source", "") or "").strip().casefold()
    raw_path = str(getattr(panel_cfg, "path", "") or "").strip()
    repo_root = Path(getattr(getattr(cfg, "paths", None), "repo_root", Path.cwd())).resolve()
    panel_path = Path(raw_path)
    configured_path = panel_path
    if not raw_path:
        return source, raw_path, configured_path, panel_path
    if panel_path.is_absolute():
        panel_path = panel_path.resolve()
    else:
        panel_path = (repo_root / panel_path).resolve()
    configured_path = panel_path
    return source, raw_path, configured_path, _resolve_snapshot_current_path(panel_path)


def _new_panel_identity(source: str, raw_path: str, panel_path: Path) -> dict[str, Any]:
    return {
        "source": source,
        "path": str(panel_path) if raw_path else "",
        "exists": bool(raw_path and panel_path.exists()),
        "dataset_id": "",
        "semantic_fingerprint": "",
        "source_config_hash": "",
        "content_sha256": "",
        "content_verified": False,
        "manifest_path": "",
        "manifest_sha256": "",
        "manifest_consistent": None,
        "declared_content_sha256": "",
        "declared_matches_content": None,
    }


def _validate_identity_request(source: str, raw_path: str, *, verify_content: bool) -> None:
    if not verify_content:
        return
    if source != "parquet":
        raise DataIdentityError(
            "strict panel identity verification requires panel.source=parquet; "
            f"configured source={source or '<empty>'}"
        )
    if not raw_path:
        raise DataIdentityError("strict panel identity verification requires a configured panel path")


def _metadata_panel_identity(identity: dict[str, Any], source: str) -> dict[str, Any]:
    identity["dataset_id"] = stable_hash({"source": source, "config": {"source": source}})
    identity["semantic_fingerprint"] = stable_hash({"source": source})
    identity["identity_hash"] = stable_hash(
        {
            "dataset_id": identity["dataset_id"],
            "semantic_fingerprint": identity["semantic_fingerprint"],
            "source_config_hash": identity["source_config_hash"],
        }
    )
    return identity


def _validate_panel_files(panel_path: Path, manifest_path: Path, *, verify_content: bool) -> None:
    if not verify_content:
        return
    if not panel_path.is_file():
        raise DataIdentityError(f"panel file is missing or is not a file: {panel_path}")
    if not manifest_path.is_file():
        raise DataIdentityError(f"panel manifest is missing or invalid: {manifest_path}")


def _read_panel_manifest(manifest_path: Path, *, verify_content: bool) -> dict[str, Any]:
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if verify_content:
            raise DataIdentityError(f"panel manifest is invalid: {manifest_path}: {exc}") from None
        warnings.warn(
            f"panel manifest unavailable for stable identity: {manifest_path}: {exc}",
            RuntimeWarning,
            stacklevel=3,
        )
        return {}
    if isinstance(payload, dict):
        return payload
    if verify_content:
        raise DataIdentityError(f"panel manifest root must be an object: {manifest_path}")
    return {}


def _manifest_identity_values(manifest_payload: Mapping[str, Any]) -> dict[str, str]:
    validate_manifest_identity_contract(manifest_payload)
    manifest_hash = canonical_manifest_hash(manifest_payload)
    semantic = semantic_fingerprint(manifest_payload)
    source_hash = source_config_hash(manifest_payload)
    dataset_id = dataset_id_for_manifest(manifest_payload)
    declared = str(manifest_payload.get("file_sha256") or "").strip()
    return {
        "manifest_sha256": manifest_hash,
        "semantic_fingerprint": semantic,
        "source_config_hash": source_hash,
        "dataset_id": dataset_id,
        "declared_content_sha256": declared,
        "content_sha256": declared,
    }


def _declared_manifest_identity(manifest_payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "canonical_manifest_sha256": str(manifest_payload.get("canonical_manifest_sha256") or "")
        .strip()
        .casefold(),
        "semantic_fingerprint": str(manifest_payload.get("semantic_fingerprint") or "").strip().casefold(),
        "source_config_hash": str(manifest_payload.get("source_config_hash") or "").strip().casefold(),
        "dataset_id": str(manifest_payload.get("dataset_id") or "").strip().casefold(),
    }


def _apply_manifest_identity(
    identity: dict[str, Any],
    manifest_payload: Mapping[str, Any],
    *,
    verify_content: bool,
) -> None:
    try:
        values = _manifest_identity_values(manifest_payload)
    except DataIdentityError as exc:
        if verify_content:
            raise
        declared = str(manifest_payload.get("file_sha256") or "").strip().casefold()
        identity["declared_content_sha256"] = declared
        identity["content_sha256"] = declared
        identity["manifest_consistent"] = False
        warnings.warn(
            f"panel manifest cannot provide stable identity: {exc}",
            RuntimeWarning,
            stacklevel=3,
        )
        return
    identity.update(values)
    declared_stable = _declared_manifest_identity(manifest_payload)
    actual_stable = {
        "canonical_manifest_sha256": values["manifest_sha256"],
        "semantic_fingerprint": values["semantic_fingerprint"],
        "source_config_hash": values["source_config_hash"],
        "dataset_id": values["dataset_id"],
    }
    inconsistent = [
        key
        for key in sorted(declared_stable, key=str.casefold)
        if declared_stable[key] and declared_stable[key] != actual_stable[key]
    ]
    identity["manifest_consistent"] = not inconsistent
    if not inconsistent:
        return
    message = (
        "panel manifest stable identity fields disagree with canonical values: "
        + ", ".join(inconsistent)
    )
    if verify_content:
        raise DataIdentityError(message)
    warnings.warn(message, RuntimeWarning, stacklevel=3)


def _warn_empty_manifest(manifest_path: Path) -> None:
    if manifest_path.exists():
        warnings.warn(
            f"panel manifest has no object payload: {manifest_path}",
            RuntimeWarning,
            stacklevel=3,
        )
        return
    warnings.warn(
        f"panel manifest is missing; stable identity is metadata-only: {manifest_path}",
        RuntimeWarning,
        stacklevel=3,
    )


def _apply_identity_fallback(identity: dict[str, Any], source: str) -> None:
    if identity["semantic_fingerprint"]:
        return
    fallback = {"source": source, "identity_contract": "metadata_fallback"}
    semantic = stable_hash(fallback)
    identity["semantic_fingerprint"] = semantic
    identity["dataset_id"] = stable_hash(
        {
            "semantic_fingerprint": semantic,
            "content_sha256": identity["content_sha256"],
        }
    )


def _verify_panel_content(
    identity: dict[str, Any],
    panel_path: Path,
    manifest_path: Path,
    manifest_payload: Mapping[str, Any],
) -> None:
    if not manifest_payload:
        raise DataIdentityError(f"panel manifest is missing or invalid: {manifest_path}")
    declared = str(identity.get("declared_content_sha256") or "")
    if not declared:
        raise DataIdentityError(f"panel manifest has no file_sha256: {manifest_path}")
    actual = sha256_file(panel_path)
    identity["content_sha256"] = actual
    identity["content_verified"] = True
    identity["declared_matches_content"] = declared == actual
    if declared != actual:
        raise DataIdentityError(
            "panel manifest file_sha256 does not match panel content: "
            f"declared={declared} actual={actual}"
        )


def _finalize_panel_identity(
    identity: dict[str, Any],
    configured_path: Path,
    raw_path: str,
) -> dict[str, Any]:
    identity["identity_hash"] = stable_hash(
        {
            "dataset_id": identity["dataset_id"],
            "semantic_fingerprint": identity["semantic_fingerprint"],
            "source_config_hash": identity["source_config_hash"],
            "manifest_sha256": identity["manifest_sha256"],
        }
    )
    identity["provenance"] = {
        "configured_path": str(configured_path) if raw_path else "",
        "path": identity["path"],
        "manifest_path": identity["manifest_path"],
        "exists": identity["exists"],
    }
    return identity


def panel_data_identity(cfg: Any, *, verify_content: bool) -> dict[str, Any]:
    source, raw_path, configured_path, panel_path = _panel_identity_context(cfg)
    identity = _new_panel_identity(source, raw_path, panel_path)
    _validate_identity_request(source, raw_path, verify_content=verify_content)
    if source != "parquet" or not raw_path:
        return _metadata_panel_identity(identity, source)

    manifest_path = _panel_manifest_path(panel_path)
    identity["manifest_path"] = str(manifest_path)
    _validate_panel_files(panel_path, manifest_path, verify_content=verify_content)
    manifest_payload = _read_panel_manifest(manifest_path, verify_content=verify_content)
    if manifest_payload:
        _apply_manifest_identity(identity, manifest_payload, verify_content=verify_content)
    else:
        _warn_empty_manifest(manifest_path)
    _apply_identity_fallback(identity, source)
    if verify_content:
        _verify_panel_content(identity, panel_path, manifest_path, manifest_payload)
    return _finalize_panel_identity(identity, configured_path, raw_path)


__all__ = [
    "PANEL_MANIFEST_SCHEMA_VERSION",
    "DataIdentityError",
    "canonical_manifest_hash",
    "canonical_manifest_payload",
    "canonical_source_config",
    "dataset_id_for_manifest",
    "panel_data_identity",
    "semantic_fingerprint",
    "source_config_hash",
    "validate_manifest_identity_contract",
]
