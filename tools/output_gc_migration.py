"""Plan and apply safe migrations from the legacy repository output tree."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from core.local_artifact_paths import (
    cache_root,
    comparison_workspace_root,
    document_tool_cache_dir,
    formal_worktree_root,
    model_cache_dir,
    public_repo_workspace_root,
    temp_root,
    temporary_workspace_dir,
)
from tools.output_gc_safety import (
    has_pinned_ancestor,
    is_inside,
    is_link,
    modification_time,
    path_size,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output"
_PROTECTED_EVIDENCE_ROOTS = {"paper_experiments", "paper_release"}


def _relative_output_name(path: Path, output_root: Path) -> str:
    return Path(path).resolve().relative_to(Path(output_root).resolve()).as_posix()


def _allowed_destination(path: Path) -> bool:
    return is_inside(path, cache_root()) or is_inside(path, temp_root())


def _migration_item(
    *,
    source: Path,
    destination: Path | None,
    action: str,
    reason: str,
    output_root: Path,
    stale_seconds: float,
    now: float,
) -> dict[str, Any]:
    source = Path(source)
    item: dict[str, Any] = {
        "action": action,
        "source": str(source.resolve()),
        "destination": str(destination.resolve()) if destination is not None else "",
        "reason": reason,
        "relative_source": _relative_output_name(source, output_root),
        "exists": source.exists(),
        "size_bytes": path_size(source),
        "age_seconds": max(0.0, now - modification_time(source)),
        "status": "candidate" if source.exists() else "missing",
        "reasons": [],
    }
    if not source.exists():
        return item
    if not is_inside(source, output_root):
        item["status"] = "protected"
        item["reasons"].append("source_outside_output")
    elif is_link(source):
        item["status"] = "protected"
        item["reasons"].append("source_is_link")
    elif has_pinned_ancestor(source, stop=output_root):
        item["status"] = "protected"
        item["reasons"].append("pin_marker")
    elif item["age_seconds"] < stale_seconds:
        item["status"] = "protected"
        item["reasons"].append("not_stale")
    if destination is not None and item["status"] == "candidate":
        # Keep the plan itself safe. The apply path repeats this check in case
        # an environment variable or report changes between steps.
        if not _allowed_destination(destination):
            item["status"] = "protected"
            item["reasons"].append("invalid_destination")
        elif destination.exists():
            item["status"] = "conflict"
            item["reasons"].append("destination_exists")
    return item


MigrationSpec = tuple[Path, Path | None, str, str]


def _fixed_migration_specs(output_root: Path) -> list[MigrationSpec]:
    """Return the small set of named cache migrations."""
    return [
        (
            output_root / "npm-cache",
            cache_root() / "npm" / "legacy-v1",
            "move",
            "shared npm cache",
        ),
        (
            output_root / "tools" / "perf_smoke" / "_tmp",
            temporary_workspace_dir("perf-smoke", "migrated-output"),
            "move",
            "performance smoke scratch",
        ),
        (
            output_root / "paper_models" / "Qwen2.5-Coder-1.5B-Instruct",
            model_cache_dir("Qwen2.5-Coder-1.5B-Instruct", "2e1fd397ee46e1388853d2af2c993145b0f1098a"),
            "move",
            "versioned paper model cache",
        ),
    ]


def _document_migration_specs(output_root: Path) -> list[MigrationSpec]:
    document_root = output_root / "document_qa_tools"
    if not document_root.is_dir():
        return []
    known_versions = {
        "libreoffice_portable_26_2_5": document_tool_cache_dir("libreoffice", "26.2.5.2") / "portable",
        "libreoffice_26_2_5": document_tool_cache_dir("libreoffice", "26.2.5.2") / "extracted",
        "poppler_26_02": document_tool_cache_dir("poppler", "26.02.0") / "distribution",
        "poppler_26_02.zip": document_tool_cache_dir("poppler", "26.02.0") / "poppler-26.02.0.zip",
        "lessmsi_2_12_9": document_tool_cache_dir("lessmsi", "2.12.9") / "distribution",
        "python_packages": document_tool_cache_dir(
            "python-packages", f"cp{sys.version_info.major}{sys.version_info.minor}"
        )
        / "packages",
    }
    return [
        (
            child,
            known_versions.get(child.name, document_tool_cache_dir("document-qa", "legacy-v1") / child.name),
            "move",
            "shared versioned document QA cache",
        )
        for child in sorted(document_root.iterdir(), key=lambda item: item.name.casefold())
    ]


def _empty_cache_specs(output_root: Path) -> list[MigrationSpec]:
    specs: list[MigrationSpec] = []
    for container in (output_root / "document_qa_tools", output_root / "paper_models"):
        try:
            is_empty = container.is_dir() and not any(container.iterdir())
        except OSError:
            is_empty = False
        if is_empty:
            specs.append((container, None, "remove", "empty legacy cache container"))
    return specs


def _workspace_destination(name: str) -> tuple[Path, str] | None:
    folded = name.casefold()
    if folded.startswith("public_repo_smoke"):
        return public_repo_workspace_root(name), "public-repo smoke workspace"
    if folded.startswith("public_repo_release"):
        return public_repo_workspace_root(name), "public-repo release workspace"
    if folded.startswith("temp_compare"):
        return comparison_workspace_root(name), "comparison workspace"
    return None


def _workspace_migration_specs(output_root: Path) -> list[MigrationSpec]:
    # The legacy source ``_compare_results`` is intentionally renamed so that
    # migrated temporary components have visible, safe names.
    workspace_moves = {
        "formal_worktrees": formal_worktree_root("migrated-output-formal-worktrees"),
        "full_public_repo": public_repo_workspace_root("full_public_repo"),
        "web_ui_smoke": public_repo_workspace_root("web_ui_smoke"),
        "_compare_results": comparison_workspace_root("compare-results"),
        "root_compare": comparison_workspace_root("root_compare"),
    }
    specs: list[MigrationSpec] = [
        (output_root / name, destination, "move", "disposable workspace")
        for name, destination in workspace_moves.items()
    ]
    if output_root.is_dir():
        for child in sorted(output_root.iterdir(), key=lambda item: item.name.casefold()):
            resolved = _workspace_destination(child.name)
            if resolved is not None:
                destination, reason = resolved
                specs.append((child, destination, "move", reason))
    return specs


def _worker_cache_specs(output_root: Path) -> list[MigrationSpec]:
    specs: list[MigrationSpec] = []
    for current, directories, _files in os.walk(output_root, topdown=True, followlinks=False):
        directories[:] = sorted(directories, key=str.casefold)
        for name in list(directories):
            if name != ".worker_cache":
                continue
            source = Path(current) / name
            try:
                relative_parts = source.resolve().relative_to(output_root).parts
            except ValueError:
                continue
            if relative_parts and relative_parts[0].casefold() in _PROTECTED_EVIDENCE_ROOTS:
                continue
            specs.append((source, None, "remove", "legacy per-run Codex worker cache"))
    return specs


def _materialize_migration_specs(
    specs: list[MigrationSpec],
    *,
    output_root: Path,
    stale_seconds: float,
    now: float,
) -> list[dict[str, Any]]:
    return [
        _migration_item(
            source=source,
            destination=destination,
            action=action,
            reason=reason,
            output_root=output_root,
            stale_seconds=stale_seconds,
            now=now,
        )
        for source, destination, action, reason in sorted(specs, key=lambda item: str(item[0]).casefold())
        if source.exists()
    ]


def _known_migration_specs(
    output_root: Path,
    *,
    stale_seconds: float,
    now: float,
) -> list[dict[str, Any]]:
    output_root = Path(output_root).resolve()
    specs = [
        *_fixed_migration_specs(output_root),
        *_document_migration_specs(output_root),
        *_empty_cache_specs(output_root),
        *_workspace_migration_specs(output_root),
        *_worker_cache_specs(output_root),
    ]
    return _materialize_migration_specs(
        specs,
        output_root=output_root,
        stale_seconds=stale_seconds,
        now=now,
    )


def plan_migration(
    output_root: Path = OUTPUT_ROOT,
    *,
    stale_seconds: float = 24 * 60 * 60,
    now: float | None = None,
) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    if output_root == Path(output_root).anchor or not output_root.name:
        raise ValueError(f"refusing broad output root: {output_root}")
    if not is_inside(output_root, ROOT):
        raise ValueError(f"output root must be inside repository: {output_root}")
    now_epoch = float(time.time() if now is None else now)
    report_items = _known_migration_specs(
        output_root,
        stale_seconds=float(stale_seconds),
        now=now_epoch,
    )
    candidates = [item for item in report_items if item["status"] == "candidate"]
    protected = [item for item in report_items if item["status"] != "candidate"]
    return {
        "schema_version": 1,
        "output_root": str(output_root),
        "dry_run": True,
        "planned_at": now_epoch,
        "stale_seconds": float(stale_seconds),
        "items": report_items,
        "summary": {
            "item_count": len(report_items),
            "candidate_count": len(candidates),
            "candidate_size_bytes": sum(int(item["size_bytes"]) for item in candidates),
            "protected_count": len(protected),
        },
    }


def apply_migration(report: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(str(report["output_root"])).resolve()
    if not is_inside(output_root, ROOT):
        raise ValueError(f"output root must be inside repository: {output_root}")
    stale_seconds = float(report.get("stale_seconds") or 0.0)
    apply_now = max(time.time(), float(report.get("planned_at") or 0.0))
    moved: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    for item in report.get("items", []):
        if item.get("status") != "candidate":
            continue
        source = Path(str(item["source"])).resolve()
        destination_text = str(item.get("destination") or "").strip()
        destination = Path(destination_text).resolve() if destination_text else None
        try:
            if not is_inside(source, output_root) or is_link(source):
                raise ValueError("source is outside the output tree or is a link")
            if has_pinned_ancestor(source, stop=output_root):
                raise ValueError("PIN marker appeared before apply")
            if max(0.0, apply_now - modification_time(source)) < stale_seconds:
                raise ValueError("source became active before apply")
            if destination is not None:
                if not _allowed_destination(destination):
                    raise ValueError("destination is outside cache/temp roots")
                if destination.exists():
                    raise FileExistsError(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append({**item, "status": "moved"})
            else:
                shutil.rmtree(source)
                removed.append({**item, "status": "removed"})
        except (OSError, ValueError) as exc:
            protected.append({**item, "status": "protected", "reasons": [f"apply:{type(exc).__name__}:{exc}"]})
    report["dry_run"] = False
    report["moved"] = moved
    report["removed"] = removed
    report["protected"] = sorted(
        [*report.get("protected", []), *protected],
        key=lambda item: str(item.get("source", "")).casefold(),
    )
    report["summary"]["moved_count"] = len(moved)
    report["summary"]["removed_count"] = len(removed)
    report["summary"]["apply_protected_count"] = len(protected)
    return report
