"""Inspect protocol artifacts for one completed backtest run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backtest.artifacts import GenerationSnapshot, resolve_current_generation, resolve_generation


_PROTOCOL_META_KEYS = {
    "compiled_plan_path": ("compiled_plan", "compiled_plan"),
    "audit_research_path": ("audit_research", "research_audit"),
    "robustness_path": ("robustness", "robustness_report"),
    "artifact_manifest_path": ("artifact_manifest", "artifact_manifest"),
}
_RUN_META_SKIP_SUFFIXES = (
    "_artifact_manifest.json",
    "_audit_research.json",
    "_compiled_plan.json",
    "_diagnostics.json",
    "_robustness.json",
)
_DEFAULT_RUN_META_DIR = Path("output") / "pipeline" / "run_meta"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]).casefold())}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _stable_hash(payload: Any) -> str:
    text = json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _check(checks: list[dict[str, Any]], *, name: str, status: str, message: str, detail: Any = None) -> None:
    row: dict[str, Any] = {
        "name": str(name),
        "status": str(status),
        "message": str(message),
    }
    if detail is not None:
        row["detail"] = detail
    checks.append(row)


def _is_run_metadata_candidate(path: Path) -> bool:
    name = path.name
    if name.startswith("run_events_"):
        return False
    return not any(name.endswith(suffix) for suffix in _RUN_META_SKIP_SUFFIXES)


def _latest_run_meta_path(run_meta_dir: Path) -> Path:
    candidates = sorted(
        (path for path in run_meta_dir.glob("*.json") if _is_run_metadata_candidate(path)),
        key=lambda path: (path.stat().st_mtime, path.name.casefold()),
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no run metadata JSON found in {run_meta_dir}")
    return candidates[0]


def resolve_run_meta_path(target: str | Path | None = None, *, run_meta_dir: str | Path | None = None) -> Path:
    if target is None or str(target).strip() == "":
        root = Path(run_meta_dir) if run_meta_dir is not None else _DEFAULT_RUN_META_DIR
        return _latest_run_meta_path(root)

    raw = Path(str(target))
    if raw.is_file():
        return raw
    if raw.is_dir():
        return _latest_run_meta_path(raw)

    root = Path(run_meta_dir) if run_meta_dir is not None else _DEFAULT_RUN_META_DIR
    candidate = root / f"{raw.name}.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"cannot resolve run metadata target: {target}")


def _inspect_compiled_plan(path: Path, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    observed = str(payload.get("compiled_plan_hash") or "")
    expected = _stable_hash({key: value for key, value in payload.items() if key != "compiled_plan_hash"})
    if not observed:
        _check(checks, name="compiled_plan_hash", status="fail", message=f"missing compiled_plan_hash: {path}")
        return
    if observed != expected:
        _check(
            checks,
            name="compiled_plan_hash",
            status="fail",
            message=f"compiled_plan_hash mismatch: {path}",
            detail={"observed": observed, "expected": expected},
        )
        return
    _check(checks, name="compiled_plan_hash", status="pass", message="compiled_plan_hash matches payload")


def _inspect_manifest(
    path: Path,
    payload: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    verify_file_paths: bool = True,
) -> None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        _check(checks, name="artifact_manifest.artifacts", status="fail", message="manifest artifacts is not a list")
        return

    by_name = {
        str(item.get("name")): item
        for item in artifacts
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    required = payload.get("required_artifacts")
    if not isinstance(required, list):
        required = ["audit_research", "compiled_plan", "robustness", "run_metadata"]
        _check(checks, name="artifact_manifest.required_artifacts", status="warn", message="required_artifacts missing; using defaults")

    for name in sorted((str(item) for item in required), key=str.casefold):
        entry = by_name.get(name)
        if not isinstance(entry, dict):
            _check(checks, name=f"artifact_manifest.{name}", status="fail", message=f"required artifact missing from manifest: {name}")
            continue
        if not bool(entry.get("exists")):
            _check(checks, name=f"artifact_manifest.{name}", status="fail", message=f"required artifact does not exist: {entry.get('path')}")
            continue
        _check(checks, name=f"artifact_manifest.{name}", status="pass", message="required artifact exists")

    for name, entry in sorted(by_name.items(), key=lambda item: item[0].casefold()):
        if not verify_file_paths:
            continue
        path_text = str(entry.get("path") or "").strip()
        if not path_text:
            continue
        artifact_path = Path(path_text)
        if not bool(entry.get("exists")):
            if bool(entry.get("required")):
                _check(checks, name=f"artifact_manifest.{name}.exists", status="fail", message=f"required artifact missing: {path_text}")
            continue
        if not artifact_path.is_file():
            _check(checks, name=f"artifact_manifest.{name}.exists", status="fail", message=f"manifest says exists but file is missing: {path_text}")
            continue
        expected_hash = entry.get("sha256")
        if not expected_hash:
            continue
        observed_hash = _hash_file(artifact_path)
        if observed_hash != expected_hash:
            _check(
                checks,
                name=f"artifact_manifest.{name}.sha256",
                status="fail",
                message=f"manifest hash mismatch: {path_text}",
                detail={"observed": observed_hash, "expected": expected_hash},
            )
        else:
            _check(checks, name=f"artifact_manifest.{name}.sha256", status="pass", message="manifest hash matches file")


def _inspect_audit(payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    audit_status = str(payload.get("status") or "").casefold()
    if audit_status == "fail":
        _check(checks, name="audit_research.status", status="warn", message="research audit status is fail")
    elif audit_status == "warn":
        _check(checks, name="audit_research.status", status="warn", message="research audit status is warn")
    elif audit_status == "pass":
        _check(checks, name="audit_research.status", status="pass", message="research audit status is pass")
    else:
        _check(checks, name="audit_research.status", status="warn", message=f"unknown research audit status: {audit_status}")

    sections = payload.get("sections")
    if not isinstance(sections, dict):
        _check(checks, name="audit_research.sections", status="warn", message="research audit has no section details")
        return
    for name in ("static", "pre_run", "post_run"):
        section = sections.get(name)
        if not isinstance(section, dict):
            _check(checks, name=f"audit_research.sections.{name}", status="warn", message=f"missing audit section: {name}")
            continue
        status = str(section.get("status") or "").casefold()
        issue_count = int(section.get("issue_count") or 0)
        if status == "fail":
            _check(
                checks,
                name=f"audit_research.sections.{name}",
                status="warn",
                message=f"{name} audit section failed with {issue_count} issue(s)",
            )
        elif status == "warn":
            _check(
                checks,
                name=f"audit_research.sections.{name}",
                status="warn",
                message=f"{name} audit section has {issue_count} warning issue(s)",
            )
        elif status == "pass":
            _check(
                checks,
                name=f"audit_research.sections.{name}",
                status="pass",
                message=f"{name} audit section passed",
            )
        else:
            _check(
                checks,
                name=f"audit_research.sections.{name}",
                status="warn",
                message=f"{name} audit section has unknown status: {status}",
            )


def inspect_run_artifacts(
    run_meta_path: str | Path,
    *,
    protocol_paths: dict[str, Path] | None = None,
    verify_protocol_manifest_paths: bool = True,
) -> dict[str, Any]:
    run_meta_path = Path(run_meta_path)
    checks: list[dict[str, Any]] = []
    run_meta = _load_json(run_meta_path)
    run_id = str(run_meta.get("run_id") or run_meta_path.stem)

    artifacts = run_meta.get("artifacts")
    if not isinstance(artifacts, dict):
        _check(checks, name="run_metadata.artifacts", status="fail", message="run metadata has no artifacts object")
        artifacts = {}
    else:
        _check(checks, name="run_metadata.artifacts", status="pass", message="run metadata artifacts object exists")

    loaded_protocol_artifacts: dict[str, dict[str, Any]] = {}
    for meta_key, (logical_name, artifact_type) in sorted(_PROTOCOL_META_KEYS.items(), key=lambda item: item[0].casefold()):
        override_path = (protocol_paths or {}).get(logical_name)
        raw_path = str(override_path or artifacts.get(meta_key) or "").strip()
        if not raw_path:
            _check(checks, name=meta_key, status="fail", message=f"missing {meta_key} in run metadata artifacts")
            continue
        path = Path(raw_path)
        if not path.is_file():
            _check(checks, name=meta_key, status="fail", message=f"artifact path does not exist: {path}")
            continue
        try:
            payload = _load_json(path)
        except Exception as exc:
            _check(checks, name=meta_key, status="fail", message=f"cannot load artifact JSON: {exc}")
            continue
        observed_type = payload.get("artifact_type")
        if observed_type != artifact_type:
            _check(
                checks,
                name=meta_key,
                status="fail",
                message=f"artifact_type mismatch for {path}",
                detail={"observed": observed_type, "expected": artifact_type},
            )
            continue
        _check(checks, name=meta_key, status="pass", message=f"{logical_name} artifact exists and has expected type")
        loaded_protocol_artifacts[logical_name] = payload
        if logical_name == "compiled_plan":
            _inspect_compiled_plan(path, payload, checks)
        elif logical_name == "artifact_manifest":
            _inspect_manifest(
                path,
                payload,
                checks,
                verify_file_paths=verify_protocol_manifest_paths,
            )

    audit = loaded_protocol_artifacts.get("audit_research")
    if isinstance(audit, dict):
        _inspect_audit(audit, checks)

    robustness = loaded_protocol_artifacts.get("robustness")
    if isinstance(robustness, dict):
        status = str(robustness.get("status") or "").casefold()
        if status in {"pass"}:
            _check(checks, name="robustness.status", status="pass", message="robustness status is pass")
        elif status in {"warn", "missing", "not_run"}:
            _check(checks, name="robustness.status", status="warn", message=f"robustness status is {status}")
        else:
            _check(checks, name="robustness.status", status="warn", message=f"unknown robustness status: {status}")

    fail_count = sum(1 for check in checks if check["status"] == "fail")
    warn_count = sum(1 for check in checks if check["status"] == "warn")
    pass_count = sum(1 for check in checks if check["status"] == "pass")
    return {
        "ok": fail_count == 0,
        "run_id": run_id,
        "run_meta_path": str(run_meta_path),
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
        },
        "checks": checks,
    }


def _single_generation_path(snapshot: GenerationSnapshot, catalog_key: str) -> Path:
    paths = snapshot.paths_for_catalog(catalog_key)
    if len(paths) != 1:
        raise ValueError(
            f"generation {snapshot.generation_id!r} expected one {catalog_key!r} artifact, found {len(paths)}"
        )
    return paths[0]


def inspect_artifact_generation(
    output_root: str | Path,
    generation_id: str | None = None,
) -> dict[str, Any]:
    snapshot = (
        resolve_generation(Path(output_root), generation_id)
        if str(generation_id or "").strip()
        else resolve_current_generation(Path(output_root))
    )
    generation_details = {
        "release_id": snapshot.release_id,
        "generation_id": snapshot.generation_id,
        "generation_dir": str(snapshot.generation_dir),
        "artifact_count": len(snapshot.manifest.get("artifacts", [])),
    }
    run_metadata_paths = snapshot.paths_for_catalog("run_metadata")
    if not run_metadata_paths:
        return {
            "ok": True,
            "run_id": snapshot.generation_id,
            "summary": {"pass": 1, "warn": 1, "fail": 0},
            "checks": [
                {
                    "name": "artifact_generation.integrity",
                    "status": "pass",
                    "message": "generation manifest, READY marker, pointer, and file hashes are valid",
                },
                {
                    "name": "artifact_generation.protocol",
                    "status": "warn",
                    "message": "run-result-only generation has no session protocol artifacts",
                },
            ],
            "artifact_generation": {**generation_details, "scope": "run_result_only"},
        }
    protocol_paths = {
        "artifact_manifest": _single_generation_path(snapshot, "artifact_manifest"),
        "audit_research": _single_generation_path(snapshot, "research_audit"),
        "compiled_plan": _single_generation_path(snapshot, "compiled_plan"),
        "robustness": _single_generation_path(snapshot, "robustness_report"),
    }
    payload = inspect_run_artifacts(
        _single_generation_path(snapshot, "run_metadata"),
        protocol_paths=protocol_paths,
        verify_protocol_manifest_paths=False,
    )
    payload["artifact_generation"] = {**generation_details, "scope": "session_complete"}
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect standard protocol artifacts for a completed backtest run.")
    parser.add_argument("target", nargs="?", default="", help="Run metadata JSON, run_meta directory, or run_id.")
    parser.add_argument(
        "--run-meta-dir",
        default="",
        help="Directory used when target is a run_id. Defaults to output/pipeline/run_meta.",
    )
    parser.add_argument(
        "--generation-root",
        default="",
        help="Read an atomic artifact generation from this output root. Target is an optional generation id; blank/current uses current.json.",
    )
    parser.add_argument("--json-out", default="", help="Optional path to write the full inspection JSON.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when warnings are present.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if str(args.generation_root or "").strip():
            generation_id = str(args.target or "").strip()
            payload = inspect_artifact_generation(
                args.generation_root,
                None if generation_id.casefold() in {"", "current"} else generation_id,
            )
        else:
            run_meta_path = resolve_run_meta_path(
                args.target,
                run_meta_dir=args.run_meta_dir or None,
            )
            payload = inspect_run_artifacts(run_meta_path)
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "summary": {"pass": 0, "warn": 0, "fail": 1},
            "checks": [],
        }
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    if str(args.json_out or "").strip():
        out = Path(str(args.json_out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not payload.get("ok"):
        return 1
    if bool(args.strict) and int((payload.get("summary") or {}).get("warn") or 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
