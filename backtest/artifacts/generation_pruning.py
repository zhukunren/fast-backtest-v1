"""Protected retention planning for immutable artifact generations."""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .generation import (
    GENERATION_PROTOCOL_VERSION,
    RELEASE_ID,
    ArtifactGenerationError,
    _generation_dir,
    _load_json,
    _release_root,
    _validate_generation_id,
    current_pointer_path,
)


_PENDING_NAME = ".pending.json"
_READY_NAME = "READY.json"
_PIN_NAME = "PIN"
_ACTIVE_NAME = "ACTIVE"


def _parse_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _path_is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _tree_size(path: Path) -> int:
    """Measure a candidate without following links or leaving its tree."""

    if _path_is_link(path):
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        directories[:] = sorted(
            [name for name in directories if not _path_is_link(Path(current) / name)],
            key=str.casefold,
        )
        for name in sorted(files, key=str.casefold):
            child = Path(current) / name
            if _path_is_link(child):
                continue
            try:
                total += int(child.stat().st_size)
            except OSError:
                continue
    return total


def _current_generation_id_for_prune(output_root: Path) -> tuple[str, str]:
    pointer_path = current_pointer_path(output_root)
    if not pointer_path.exists():
        return "", ""
    try:
        if _path_is_link(pointer_path):
            raise ArtifactGenerationError("current pointer must not be a link")
        payload = _load_json(pointer_path)
        if payload.get("artifact_type") != "artifact_generation_current":
            raise ArtifactGenerationError("current pointer has an invalid artifact_type")
        if payload.get("protocol_version") != GENERATION_PROTOCOL_VERSION:
            raise ArtifactGenerationError("current pointer protocol version is unsupported")
        if payload.get("release_id") != RELEASE_ID:
            raise ArtifactGenerationError("current pointer release id is unsupported")
        generation_id = _validate_generation_id(str(payload.get("generation_id") or ""))
        manifest_sha256 = str(payload.get("manifest_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ArtifactGenerationError("current pointer has no valid manifest hash")
        ready = _load_json(_generation_dir(output_root, generation_id) / _READY_NAME)
        if (
            ready.get("artifact_type") != "artifact_generation_ready"
            or ready.get("protocol_version") != GENERATION_PROTOCOL_VERSION
            or ready.get("release_id") != RELEASE_ID
            or ready.get("generation_id") != generation_id
        ):
            raise ArtifactGenerationError("current READY marker identity or protocol mismatch")
        if str(ready.get("manifest_sha256") or "") != manifest_sha256:
            raise ArtifactGenerationError("current pointer and READY manifest hashes differ")
        if _parse_timestamp(ready.get("ready_at")) is None:
            raise ArtifactGenerationError("current READY marker has no valid ready_at timestamp")
        return generation_id, ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def _generation_prune_entry(
    generation_dir: Path,
    *,
    generation_id: str,
) -> dict[str, Any]:
    ready_path = generation_dir / _READY_NAME
    entry: dict[str, Any] = {
        "kind": "generation",
        "generation_id": generation_id,
        "path": str(generation_dir),
        "size_bytes": _tree_size(generation_dir),
        "ready_at": "",
        "ready_epoch": None,
        "reasons": [],
    }
    try:
        ready = _load_json(ready_path)
        if (
            ready.get("artifact_type") != "artifact_generation_ready"
            or ready.get("protocol_version") != GENERATION_PROTOCOL_VERSION
            or ready.get("release_id") != RELEASE_ID
            or ready.get("generation_id") != generation_id
        ):
            raise ArtifactGenerationError("READY marker identity or protocol mismatch")
        ready_at = str(ready.get("ready_at") or "").strip()
        ready_epoch = _parse_timestamp(ready_at)
        if ready_epoch is None:
            raise ArtifactGenerationError("READY marker has no valid ready_at timestamp")
        entry["ready_at"] = ready_at
        entry["ready_epoch"] = ready_epoch
    except Exception as exc:
        entry["reasons"].append(f"invalid_ready_marker:{type(exc).__name__}")
    if (generation_dir / _PIN_NAME).exists():
        entry["reasons"].append("pin_marker")
    return entry


def _staging_prune_entry(staging_dir: Path, *, now: float) -> dict[str, Any]:
    try:
        modified = float(staging_dir.stat().st_mtime)
    except OSError:
        modified = now
    for marker in (staging_dir / _PENDING_NAME, staging_dir / _ACTIVE_NAME):
        try:
            modified = max(modified, float(marker.stat().st_mtime))
        except OSError:
            continue
    return {
        "kind": "staging",
        "generation_id": staging_dir.name,
        "path": str(staging_dir),
        "size_bytes": _tree_size(staging_dir),
        "modified_epoch": modified,
        "age_seconds": max(0.0, now - modified),
        "reasons": [],
    }


def _collect_staging_prune_entries(
    release_root: Path,
    *,
    now: float,
    stale_staging_seconds: float,
) -> list[dict[str, Any]]:
    staging: list[dict[str, Any]] = []
    staging_root = release_root / ".staging"
    if not staging_root.is_dir():
        return staging
    for path in sorted(staging_root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_dir() or _path_is_link(path):
            continue
        item = _staging_prune_entry(path, now=now)
        try:
            _validate_generation_id(path.name)
        except ArtifactGenerationError:
            item["reasons"].append("invalid_generation_id")
        if item["age_seconds"] < float(stale_staging_seconds):
            item["reasons"].append("staging_not_stale")
        if (path / _ACTIVE_NAME).exists():
            item["reasons"].append("active_marker")
        staging.append(item)
    return staging


def plan_generation_prune(
    output_root: Path,
    *,
    keep_last: int = 3,
    pinned_generation_ids: Sequence[str] = (),
    stale_staging_seconds: float = 24 * 60 * 60,
    now: float | None = None,
) -> dict[str, Any]:
    """Build a deterministic, protected prune plan without changing files."""

    if type(keep_last) is not int or keep_last < 0:
        raise ValueError("keep_last must be a non-negative integer")
    if stale_staging_seconds < 0:
        raise ValueError("stale_staging_seconds must be non-negative")
    output_root = Path(output_root).resolve()
    release_root = _release_root(output_root).resolve()
    current_id, current_error = _current_generation_id_for_prune(output_root)
    pinned = {_validate_generation_id(str(value)) for value in pinned_generation_ids if str(value or "").strip()}
    now_epoch = float(time.time() if now is None else now)

    generation_root = release_root / "generations"
    generations: list[dict[str, Any]] = []
    if generation_root.is_dir():
        for path in sorted(generation_root.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_dir() or _path_is_link(path):
                continue
            try:
                generation_id = _validate_generation_id(path.name)
            except ArtifactGenerationError:
                generations.append(
                    {
                        "kind": "generation",
                        "generation_id": path.name,
                        "path": str(path),
                        "size_bytes": _tree_size(path),
                        "ready_at": "",
                        "ready_epoch": None,
                        "reasons": ["invalid_generation_id"],
                    }
                )
                continue
            generations.append(_generation_prune_entry(path, generation_id=generation_id))

    valid_for_keep = [item for item in generations if item["ready_epoch"] is not None and not item["reasons"]]
    valid_for_keep.sort(key=lambda item: (-float(item["ready_epoch"]), str(item["generation_id"]).casefold()))
    keep_ids = {str(item["generation_id"]) for item in valid_for_keep[:keep_last]}
    if current_error:
        for item in generations:
            item["reasons"].append("current_pointer_unreadable")
    elif current_id and current_id not in {str(item["generation_id"]) for item in generations}:
        for item in generations:
            item["reasons"].append("current_pointer_target_missing")
    for item in generations:
        generation_id = str(item["generation_id"])
        if generation_id == current_id:
            item["reasons"].append("current")
        if generation_id in pinned:
            item["reasons"].append("configured_pin")
        if generation_id in keep_ids:
            item["reasons"].append("keep_last")

    staging = _collect_staging_prune_entries(
        release_root,
        now=now_epoch,
        stale_staging_seconds=stale_staging_seconds,
    )

    all_entries = generations + staging
    candidates = [item for item in all_entries if not item["reasons"]]
    protected = [item for item in all_entries if item["reasons"]]
    candidates.sort(key=lambda item: (str(item["kind"]), str(item["path"]).casefold()))
    protected.sort(key=lambda item: (str(item["kind"]), str(item["path"]).casefold()))
    return {
        "schema_version": 1,
        "dry_run": True,
        "output_root": str(output_root),
        "release_root": str(release_root),
        "current_generation_id": current_id,
        "current_pointer_error": current_error,
        "keep_last": keep_last,
        "pinned_generation_ids": sorted(pinned, key=str.casefold),
        "stale_staging_seconds": float(stale_staging_seconds),
        "candidates": candidates,
        "protected": protected,
        "removed": [],
        "summary": {
            "candidate_count": len(candidates),
            "candidate_size_bytes": sum(int(item["size_bytes"]) for item in candidates),
            "protected_count": len(protected),
            "generation_count": len(generations),
            "staging_count": len(staging),
        },
    }


def _safe_prune_target(path: Path, *, parent: Path) -> Path:
    resolved = Path(path).resolve()
    expected_parent = Path(parent).resolve()
    if resolved.parent != expected_parent or resolved == expected_parent:
        raise ArtifactGenerationError(f"refusing to prune unexpected path: {path}")
    if _path_is_link(path):
        raise ArtifactGenerationError(f"refusing to prune a link: {path}")
    return path


def prune_artifact_generations(
    output_root: Path,
    *,
    keep_last: int = 3,
    pinned_generation_ids: Sequence[str] = (),
    stale_staging_seconds: float = 24 * 60 * 60,
    apply: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Plan and optionally remove stale immutable generations."""

    report = plan_generation_prune(
        output_root,
        keep_last=keep_last,
        pinned_generation_ids=pinned_generation_ids,
        stale_staging_seconds=stale_staging_seconds,
        now=now,
    )
    if not apply:
        report["dry_run"] = True
        return report

    release_root = Path(report["release_root"])
    removed: list[dict[str, Any]] = []
    newly_protected: list[dict[str, Any]] = []
    for item in report["candidates"]:
        path = Path(str(item["path"]))
        parent = release_root / ("generations" if item["kind"] == "generation" else ".staging")
        try:
            _safe_prune_target(path, parent=parent)
            if item["kind"] == "generation":
                live_current, live_error = _current_generation_id_for_prune(Path(report["output_root"]))
                if live_error:
                    newly_protected.append(
                        {
                            **item,
                            "reasons": ["current_pointer_unreadable_at_apply"],
                        }
                    )
                    continue
                if live_current and not _generation_dir(Path(report["output_root"]), live_current).is_dir():
                    newly_protected.append(
                        {
                            **item,
                            "reasons": ["current_pointer_target_missing_at_apply"],
                        }
                    )
                    continue
                if live_current == str(item["generation_id"]):
                    newly_protected.append({**item, "reasons": ["current_at_apply"]})
                    continue
            else:
                live_staging = _staging_prune_entry(path, now=time.time())
                if live_staging["age_seconds"] < float(report["stale_staging_seconds"]):
                    newly_protected.append({**item, "reasons": ["staging_not_stale_at_apply"]})
                    continue
            if (path / _PIN_NAME).exists() or (path / _ACTIVE_NAME).exists():
                newly_protected.append({**item, "reasons": ["marker_at_apply"]})
                continue
            if path.exists():
                shutil.rmtree(path)
                removed.append(item)
        except (OSError, ArtifactGenerationError) as exc:
            newly_protected.append({**item, "reasons": [f"apply_failed:{type(exc).__name__}:{exc}"]})
    report["dry_run"] = False
    report["removed"] = removed
    report["protected"] = sorted(
        [*report["protected"], *newly_protected],
        key=lambda item: (str(item["kind"]), str(item["path"]).casefold()),
    )
    report["summary"]["removed_count"] = len(removed)
    report["summary"]["removed_size_bytes"] = sum(int(item["size_bytes"]) for item in removed)
    report["summary"]["apply_protected_count"] = len(newly_protected)
    return report


__all__ = ["plan_generation_prune", "prune_artifact_generations"]
