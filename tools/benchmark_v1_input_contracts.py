"""Fixed input, spec, and dependency contracts for the V1 benchmark."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

if __package__:
    from .benchmark_v1_financial_digest import (
        BenchmarkError,
        _case_config_hash,
        _read_json,
        sha256_file,
    )
else:
    from benchmark_v1_financial_digest import (
        BenchmarkError,
        _case_config_hash,
        _read_json,
        sha256_file,
    )


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.panel_identity import (  # noqa: E402
    DataIdentityError,
    canonical_manifest_hash,
    dataset_id_for_manifest,
    semantic_fingerprint,
    source_config_hash,
    validate_manifest_identity_contract,
)

DEFAULT_SPEC = ROOT / "tools" / "benchmarks" / "v1_panel_native" / "spec.json"
BENCHMARK_SCHEMA_VERSION = 1

if __package__:
    from .benchmark_v1_input_support import (
        REQUIRED_INPUT_NAMES,
        dependency_versions as _dependency_versions,
        resolve_input_path as _resolve_input_path,
    )
else:
    from benchmark_v1_input_support import (
        REQUIRED_INPUT_NAMES,
        dependency_versions as _dependency_versions,
        resolve_input_path as _resolve_input_path,
    )


def _spec_path(raw: object) -> Path:
    path = Path(str(raw or DEFAULT_SPEC))
    return path if path.is_absolute() else ROOT / path


def _verify_declared_inputs(
    spec_path: Path,
    inputs: Mapping[str, object],
    *,
    panel_path_override: Path | None,
) -> tuple[dict[str, object], list[str]]:
    checked: dict[str, object] = {}
    errors: list[str] = []
    for name in sorted(inputs, key=str.casefold):
        record = inputs[name]
        if not isinstance(record, dict) or "path" not in record or "sha256" not in record:
            errors.append(f"input {name!r} must declare path and sha256")
            continue
        path = _resolve_input_path(spec_path, record["path"])
        if name == "panel" and panel_path_override is not None:
            path = Path(panel_path_override).resolve()
        if not path.is_file():
            errors.append(f"input {name!r} is missing: {path}")
            continue
        actual = sha256_file(path)
        expected = str(record.get("sha256") or "").casefold()
        if actual != expected:
            errors.append(f"input {name!r} hash mismatch: expected={expected} actual={actual}")
        checked[name] = {
            "path": str(path),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
    return checked, errors


def _verify_universe_manifest(
    spec_path: Path,
    inputs: Mapping[str, object],
) -> list[str]:
    universe_record = inputs.get("universe")
    if not isinstance(universe_record, dict) or "path" not in universe_record:
        return []
    universe_path = _resolve_input_path(spec_path, universe_record["path"])
    if not universe_path.is_file():
        return []
    codes = [line.strip() for line in universe_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors: list[str] = []
    if codes != sorted(codes, key=str.casefold):
        errors.append("universe manifest is not sorted by str.casefold")
    if len(codes) != len(set(codes)):
        errors.append("universe manifest contains duplicates")
    expected_count = int(universe_record.get("row_count", 0))
    if len(codes) != expected_count:
        errors.append(f"universe row_count mismatch: expected={expected_count} actual={len(codes)}")
    return errors


def _verify_panel_manifest_contract(
    spec_path: Path,
    inputs: Mapping[str, object],
    checked: dict[str, object],
) -> list[str]:
    panel_record = checked.get("panel")
    manifest_spec = inputs.get("panel_manifest")
    if not isinstance(panel_record, dict) or not isinstance(manifest_spec, dict):
        return []
    manifest_path = _resolve_input_path(spec_path, manifest_spec.get("path"))
    if not manifest_path.is_file():
        return []
    try:
        payload = _read_json(manifest_path)
    except Exception as exc:
        return [f"panel manifest cannot be read: {manifest_path}: {exc}"]

    actual_content = str(panel_record.get("sha256") or "")
    declared_content = str(payload.get("file_sha256") or "").strip().casefold()
    errors: list[str] = []
    if not declared_content:
        errors.append("panel manifest does not declare file_sha256")
    elif declared_content != actual_content:
        errors.append(f"panel manifest content hash mismatch: declared={declared_content} actual={actual_content}")

    try:
        validate_manifest_identity_contract(payload)
        canonical_sha256 = canonical_manifest_hash(payload)
    except DataIdentityError as exc:
        errors.append(f"panel manifest stable identity is unsupported: {exc}")
        checked_manifest = checked.get("panel_manifest")
        if isinstance(checked_manifest, dict):
            checked_manifest.update(
                {
                    "declared_content_sha256": declared_content,
                    "declared_matches_content": declared_content == actual_content,
                }
            )
        return errors
    actual_stable = {
        "canonical_sha256": canonical_sha256,
        "dataset_id": dataset_id_for_manifest(payload),
        "semantic_fingerprint": semantic_fingerprint(payload),
        "source_config_hash": source_config_hash(payload),
    }
    for key in sorted(actual_stable, key=str.casefold):
        expected = str(manifest_spec.get(key) or "").strip().casefold()
        actual = actual_stable[key]
        if not expected:
            errors.append(f"panel manifest spec must declare {key}")
        elif expected != actual:
            errors.append(f"panel manifest {key} mismatch: expected={expected} actual={actual}")
    checked_manifest = checked.get("panel_manifest")
    if isinstance(checked_manifest, dict):
        checked_manifest.update(
            {
                **actual_stable,
                "declared_content_sha256": declared_content,
                "declared_matches_content": declared_content == actual_content,
            }
        )
    return errors


def _verify_case(case: Mapping[str, object]) -> tuple[str, list[str]]:
    case_id = str(case.get("id") or "")
    errors: list[str] = []
    expected_config_hash = str(case.get("config_sha256") or "")
    actual_config_hash = _case_config_hash(case)
    if expected_config_hash != actual_config_hash:
        errors.append(
            f"case {case_id} config hash mismatch: expected={expected_config_hash} actual={actual_config_hash}"
        )
    strategy_path_raw = case.get("strategy_path")
    if not strategy_path_raw:
        return case_id, errors
    strategy_path = (ROOT / str(strategy_path_raw)).resolve()
    if not strategy_path.is_file():
        errors.append(f"case {case_id} strategy source is missing: {strategy_path}")
        return case_id, errors
    actual_strategy_hash = sha256_file(strategy_path)
    expected_strategy_hash = str(case.get("strategy_sha256") or "")
    if actual_strategy_hash != expected_strategy_hash:
        errors.append(
            f"case {case_id} strategy hash mismatch: expected={expected_strategy_hash} actual={actual_strategy_hash}"
        )
    return case_id, errors


def _verify_cases(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["spec.cases must be a non-empty list"]
    case_ids: list[str] = []
    errors: list[str] = []
    for case in value:
        if not isinstance(case, dict):
            errors.append("case record must be an object")
            continue
        case_id, case_errors = _verify_case(case)
        case_ids.append(case_id)
        errors.extend(case_errors)
    if case_ids != sorted(case_ids, key=str.casefold) or len(case_ids) != len(set(case_ids)):
        errors.append("case ids must be unique and sorted")
    return errors


def _verify_dependencies(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["spec.dependencies must be an object"]
    actual_dependencies = _dependency_versions()
    errors: list[str] = []
    for name in sorted(value, key=str.casefold):
        expected = str(value[name])
        actual = str(actual_dependencies.get(name, "unavailable"))
        if expected != actual:
            errors.append(f"dependency version mismatch for {name}: expected={expected} actual={actual}")
    return errors


def verify_inputs(spec_path: Path, *, panel_path_override: Path | None = None) -> dict[str, object]:
    spec_path = Path(spec_path).resolve()
    spec = _read_json(spec_path)
    errors: list[str] = []
    if int(spec.get("schema_version", 0)) != BENCHMARK_SCHEMA_VERSION:
        errors.append(f"unsupported spec schema_version={spec.get('schema_version')!r}")
    inputs = spec.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchmarkError("spec.inputs must be an object")
    missing_inputs = [name for name in REQUIRED_INPUT_NAMES if name not in inputs]
    if missing_inputs:
        errors.append(f"spec.inputs missing required entries: {missing_inputs}")
    checked, input_errors = _verify_declared_inputs(
        spec_path,
        inputs,
        panel_path_override=panel_path_override,
    )
    errors.extend(input_errors)
    errors.extend(_verify_panel_manifest_contract(spec_path, inputs, checked))
    errors.extend(_verify_universe_manifest(spec_path, inputs))
    errors.extend(_verify_cases(spec.get("cases")))
    errors.extend(_verify_dependencies(spec.get("dependencies")))

    return {
        "ok": not errors,
        "spec": str(spec_path),
        "spec_sha256": sha256_file(spec_path),
        "checked_inputs": checked,
        "errors": errors,
    }


def freeze_inputs(
    spec_path: Path,
    *,
    source_compiled_plan: Path,
    panel_path: Path,
) -> dict[str, object]:
    if __package__:
        from .benchmark_v1_input_freeze import freeze_inputs as _freeze_inputs
    else:
        from benchmark_v1_input_freeze import freeze_inputs as _freeze_inputs
    return _freeze_inputs(
        spec_path,
        source_compiled_plan=source_compiled_plan,
        panel_path=panel_path,
    )
