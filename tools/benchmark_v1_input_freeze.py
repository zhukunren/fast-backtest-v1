"""Atomic materialization of the fixed V1 benchmark input bundle."""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

if __package__:
    from .benchmark_v1_financial_digest import (
        BenchmarkError,
        _case_config_hash,
        _read_json,
        _write_json,
        sha256_file,
    )
    from .benchmark_v1_input_support import (
        REQUIRED_INPUT_NAMES,
        dependency_versions,
        resolve_input_path,
    )
else:
    from benchmark_v1_financial_digest import (
        BenchmarkError,
        _case_config_hash,
        _read_json,
        _write_json,
        sha256_file,
    )
    from benchmark_v1_input_support import (
        REQUIRED_INPUT_NAMES,
        dependency_versions,
        resolve_input_path,
    )

from core.panel_identity import (  # noqa: E402
    canonical_manifest_hash,
    dataset_id_for_manifest,
    semantic_fingerprint,
    source_config_hash,
    validate_manifest_identity_contract,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class _FreezeContext:
    spec_path: Path
    spec: dict[str, object]
    source: dict[str, object]
    codes: list[str]
    inputs: dict[str, object]
    panel_path: Path
    manifest_path: Path
    manifest_payload: dict[str, object]
    panel_sha256: str
    universe_path: Path
    calendar_path: Path
    board_path: Path


@dataclass(frozen=True, slots=True)
class _FreezeMaterialized:
    staged_universe: Path
    staged_calendar: Path
    staged_board: Path
    staged_spec: Path
    staged_verify_spec: Path
    calendar: pd.DataFrame
    industry_board: pd.DataFrame


def _freeze_codes(source: dict[str, object], spec: dict[str, object]) -> list[str]:
    universe = source.get("universe")
    if not isinstance(universe, dict) or not isinstance(universe.get("stock_code_list"), list):
        raise BenchmarkError("compiled plan does not contain universe.stock_code_list")
    codes = sorted(
        {str(code).strip().upper() for code in universe["stock_code_list"] if str(code).strip()},
        key=str.casefold,
    )
    expected_count = int(spec.get("universe_count", 1952))
    if len(codes) != expected_count:
        raise BenchmarkError(f"compiled plan universe count mismatch: expected={expected_count} actual={len(codes)}")
    return codes


def _freeze_context(
    spec_path: Path,
    *,
    source_compiled_plan: Path,
    panel_path: Path,
) -> _FreezeContext:
    spec_path = Path(spec_path).resolve()
    spec = _read_json(spec_path)
    source = _read_json(Path(source_compiled_plan))
    codes = _freeze_codes(source, spec)
    inputs = spec.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchmarkError("spec.inputs must be an object")
    missing_inputs = [name for name in REQUIRED_INPUT_NAMES if name not in inputs]
    if missing_inputs:
        raise BenchmarkError(f"spec.inputs missing required entries: {missing_inputs}")
    if not isinstance(inputs.get("panel"), dict):
        raise BenchmarkError("spec.inputs.panel must be an object")
    if not isinstance(inputs.get("panel_manifest"), dict):
        raise BenchmarkError("spec.inputs.panel_manifest must be an object")

    panel_path = Path(panel_path).resolve()
    manifest_path = panel_path.with_suffix(".manifest.json")
    if not panel_path.is_file() or not manifest_path.is_file():
        raise BenchmarkError("frozen panel and adjacent manifest must both exist")
    try:
        manifest_payload = _read_json(manifest_path)
        validate_manifest_identity_contract(manifest_payload)
    except Exception as exc:
        raise BenchmarkError(f"panel manifest is not eligible for freeze-inputs: {exc}") from None
    panel_sha256 = sha256_file(panel_path)
    declared_content = str(manifest_payload.get("file_sha256") or "").strip().casefold()
    if not declared_content or declared_content != panel_sha256:
        raise BenchmarkError(
            "panel content does not match its manifest before freeze-inputs: "
            f"declared={declared_content or '<missing>'} actual={panel_sha256}"
        )

    return _FreezeContext(
        spec_path=spec_path,
        spec=spec,
        source=source,
        codes=codes,
        inputs=inputs,
        panel_path=panel_path,
        manifest_path=manifest_path,
        manifest_payload=manifest_payload,
        panel_sha256=panel_sha256,
        universe_path=resolve_input_path(spec_path, inputs["universe"]["path"]),
        calendar_path=resolve_input_path(spec_path, inputs["trade_calendar"]["path"]),
        board_path=resolve_input_path(spec_path, inputs["industry_board"]["path"]),
    )


def _freeze_calendar(panel_path: Path) -> pd.DataFrame:
    dates = pd.read_parquet(panel_path, columns=["trade_date"])["trade_date"]
    dates = pd.to_datetime(dates, errors="raise").dt.normalize()
    start = pd.Timestamp("2021-01-04")
    end = pd.Timestamp("2026-06-23")
    selected = dates.loc[dates.between(start, end)].drop_duplicates()
    return pd.DataFrame({"trade_date": sorted(selected.tolist())})


def _empty_industry_board() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_code": pd.Series([], dtype="string"),
            "board_code": pd.Series([], dtype="string"),
            "effective_from": pd.Series([], dtype="datetime64[ns]"),
            "effective_to": pd.Series([], dtype="datetime64[ns]"),
        }
    )


def _materialize_freeze_inputs(
    context: _FreezeContext,
    staging: Path,
    *,
    calendar_factory: Callable[[Path], pd.DataFrame] = _freeze_calendar,
) -> _FreezeMaterialized:
    staged_universe = staging / context.universe_path.name
    staged_calendar = staging / context.calendar_path.name
    staged_board = staging / context.board_path.name
    staged_spec = staging / context.spec_path.name
    staged_verify_spec = staging / "_verify-spec.json"
    staged_universe.write_text("\n".join(context.codes) + "\n", encoding="utf-8", newline="\n")

    calendar = calendar_factory(context.panel_path)
    calendar.to_parquet(staged_calendar, index=False, engine="pyarrow")
    industry_board = _empty_industry_board()
    industry_board.to_parquet(staged_board, index=False, engine="pyarrow")
    return _FreezeMaterialized(
        staged_universe=staged_universe,
        staged_calendar=staged_calendar,
        staged_board=staged_board,
        staged_spec=staged_spec,
        staged_verify_spec=staged_verify_spec,
        calendar=calendar,
        industry_board=industry_board,
    )


def _update_freeze_spec(
    context: _FreezeContext,
    materialized: _FreezeMaterialized,
    *,
    source_compiled_plan: Path,
) -> None:
    inputs = context.inputs
    panel_record = inputs["panel"]
    manifest_record = inputs["panel_manifest"]
    panel_record["path"] = os.path.relpath(context.panel_path, context.spec_path.parent).replace("\\", "/")
    panel_record["sha256"] = context.panel_sha256
    manifest_record["path"] = os.path.relpath(context.manifest_path, context.spec_path.parent).replace("\\", "/")
    manifest_record["sha256"] = sha256_file(context.manifest_path)
    manifest_record["canonical_sha256"] = canonical_manifest_hash(context.manifest_payload)
    manifest_record["dataset_id"] = dataset_id_for_manifest(context.manifest_payload)
    manifest_record["semantic_fingerprint"] = semantic_fingerprint(context.manifest_payload)
    manifest_record["source_config_hash"] = source_config_hash(context.manifest_payload)
    for name, path, row_count in (
        ("universe", materialized.staged_universe, len(context.codes)),
        ("trade_calendar", materialized.staged_calendar, len(materialized.calendar)),
        ("industry_board", materialized.staged_board, len(materialized.industry_board)),
    ):
        record = inputs[name]
        record["sha256"] = sha256_file(path)
        record["row_count"] = row_count
    context.spec["dependencies"] = dependency_versions()
    source_path = Path(source_compiled_plan)
    context.spec["source_provenance"] = {
        "compiled_plan": os.path.relpath(source_path.resolve(), ROOT).replace("\\", "/"),
        "compiled_plan_sha256": sha256_file(source_path),
        "run_id": context.source.get("run_id"),
    }
    cases = context.spec.get("cases")
    if not isinstance(cases, list):
        raise BenchmarkError("spec.cases must be a list")
    for case in cases:
        if not isinstance(case, dict):
            continue
        strategy_path = ROOT / str(case["strategy_path"])
        case["strategy_sha256"] = sha256_file(strategy_path)
        case["config_sha256"] = _case_config_hash(case)


def _raise_if_verification_failed(report: dict[str, object], prefix: str) -> None:
    if not report["ok"]:
        raise BenchmarkError(f"{prefix}: " + " | ".join(report["errors"]))


def _verify_staged_freeze(
    context: _FreezeContext,
    materialized: _FreezeMaterialized,
    *,
    verify_inputs: Callable[..., dict[str, object]],
) -> None:
    staged_spec_payload = copy.deepcopy(context.spec)
    staged_inputs = staged_spec_payload["inputs"]
    for name, staged_path in (
        ("universe", materialized.staged_universe),
        ("trade_calendar", materialized.staged_calendar),
        ("industry_board", materialized.staged_board),
    ):
        staged_inputs[name]["path"] = str(staged_path)
    staged_inputs["panel"]["path"] = str(context.panel_path)
    staged_inputs["panel_manifest"]["path"] = str(context.manifest_path)
    _write_json(materialized.staged_verify_spec, staged_spec_payload)
    staged_report = verify_inputs(materialized.staged_verify_spec, panel_path_override=context.panel_path)
    _raise_if_verification_failed(staged_report, "staged freeze verification failed")
    _write_json(materialized.staged_spec, context.spec)


def _publish_freeze(
    context: _FreezeContext,
    materialized: _FreezeMaterialized,
    staging: Path,
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    for final_path, staged_path in (
        (context.universe_path, materialized.staged_universe),
        (context.calendar_path, materialized.staged_calendar),
        (context.board_path, materialized.staged_board),
        (context.spec_path, materialized.staged_spec),
    ):
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            backup = staging / f"backup-{len(backups)}-{final_path.name}"
            os.replace(final_path, backup)
            backups.append((final_path, backup))
        os.replace(staged_path, final_path)
        installed.append(final_path)
    return backups, installed


def _rollback_freeze(installed: list[Path], backups: list[tuple[Path, Path]]) -> None:
    for path in reversed(installed):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    for destination, backup in reversed(backups):
        if backup.exists():
            os.replace(backup, destination)


def freeze_inputs(
    spec_path: Path,
    *,
    source_compiled_plan: Path,
    panel_path: Path,
) -> dict[str, object]:
    """Freeze a complete benchmark input bundle with rollback on failure."""

    if __package__:
        from . import benchmark_v1_input_contracts as contracts
    else:
        import benchmark_v1_input_contracts as contracts

    context = _freeze_context(
        spec_path,
        source_compiled_plan=source_compiled_plan,
        panel_path=panel_path,
    )
    staging = Path(tempfile.mkdtemp(prefix=".freeze-inputs-", dir=str(context.spec_path.parent)))
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        materialized = _materialize_freeze_inputs(
            context,
            staging,
            calendar_factory=_freeze_calendar,
        )
        _update_freeze_spec(context, materialized, source_compiled_plan=source_compiled_plan)
        _verify_staged_freeze(
            context,
            materialized,
            verify_inputs=contracts.verify_inputs,
        )
        backups, installed = _publish_freeze(context, materialized, staging)
        report = contracts.verify_inputs(context.spec_path, panel_path_override=context.panel_path)
        _raise_if_verification_failed(report, "committed freeze verification failed")
        report["materialized"] = {
            "universe_count": len(context.codes),
            "calendar_rows": len(materialized.calendar),
            "industry_board_rows": len(materialized.industry_board),
        }
        return report
    except Exception:
        _rollback_freeze(installed, backups)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
