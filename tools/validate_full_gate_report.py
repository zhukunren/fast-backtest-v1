from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from full_quality_gate import GateCommand, build_gate_commands

try:
    from quality_gate_output import load_json_object, validate_topn_slice_evidence
    from quality_gate_runtime import (
        commands_match,
        git_identity_shape_error,
        host_path_locations,
        is_python_executable,
        parse_gate_tmp_arg,
        portable_gate_tmp_arg,
        python_executable_identity,
        validate_python_identity,
    )
except ModuleNotFoundError:
    from tools.quality_gate_output import load_json_object, validate_topn_slice_evidence
    from tools.quality_gate_runtime import (
        commands_match,
        git_identity_shape_error,
        host_path_locations,
        is_python_executable,
        parse_gate_tmp_arg,
        portable_gate_tmp_arg,
        python_executable_identity,
        validate_python_identity,
    )


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def required_step_specs(
    *,
    profile: str,
    require_web_build: bool,
    require_topn_slice: bool = False,
) -> tuple[GateCommand, ...]:
    commands = build_gate_commands(
        profile=profile,
        skip_perf=False,
        skip_hotpath=False,
        include_topn_slice=require_topn_slice,
        include_web_build=require_web_build,
    )
    required = [command for command in commands if command.required]
    required.append(GateCommand("worktree_stability", (), cwd="."))
    return tuple(required)


def required_step_names(
    *,
    profile: str,
    require_web_build: bool,
    require_topn_slice: bool = False,
) -> tuple[str, ...]:
    commands = required_step_specs(
        profile=profile,
        require_topn_slice=require_topn_slice,
        require_web_build=require_web_build,
    )
    return tuple(command.name for command in commands)


def _add_error(errors: list[dict[str, str]], code: str, detail: str) -> None:
    errors.append({"code": code, "detail": detail})


def _git_identity(
    report: Mapping[str, Any],
    key: str,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    identity = report.get(key)
    if not isinstance(identity, Mapping):
        _add_error(errors, f"missing_{key}", f"report.{key} must be an object")
        return {}
    git_identity = identity.get("git")
    if not isinstance(git_identity, Mapping):
        _add_error(errors, f"missing_{key}_git", f"report.{key}.git must be an object")
        return {}
    return dict(git_identity)


def _validate_report_header(
    report: Mapping[str, Any],
    *,
    expected_profile: str,
    errors: list[dict[str, str]],
) -> None:
    if type(report.get("schema_version")) is not int or report.get("schema_version") != 2:
        _add_error(errors, "schema_version_mismatch", "report.schema_version must be 2")
    if report.get("profile") != expected_profile:
        _add_error(
            errors,
            "profile_mismatch",
            f"expected {expected_profile!r}, got {report.get('profile')!r}",
        )
    if report.get("status") != "completed":
        _add_error(
            errors,
            "status_not_completed",
            f"expected 'completed', got {report.get('status')!r}",
        )
    if report.get("ok") is not True:
        _add_error(errors, "report_failed", "report.ok must be true")
    if report.get("identity_stable") is not True:
        _add_error(errors, "identity_unstable", "report.identity_stable must be true")
    if report.get("failed_step"):
        _add_error(
            errors,
            "report_failed_step",
            f"report declares failed_step={report.get('failed_step')!r}",
        )


def _validate_git_identities(
    report: Mapping[str, Any],
    *,
    expected_commit: str,
    errors: list[dict[str, str]],
) -> None:
    start_git = _git_identity(report, "identity", errors)
    end_git = _git_identity(report, "identity_end", errors)
    for label, git_identity, mismatch_code, dirty_code in (
        ("start", start_git, "start_commit_mismatch", "start_identity_dirty"),
        ("end", end_git, "end_commit_mismatch", "end_identity_dirty"),
    ):
        observed_commit = str(git_identity.get("commit") or "").casefold()
        if observed_commit != expected_commit:
            _add_error(
                errors,
                mismatch_code,
                f"{label} commit {observed_commit!r} does not match {expected_commit!r}",
            )
        if git_identity.get("dirty") is not False:
            _add_error(errors, dirty_code, f"{label} Git identity must have dirty=false")
        if git_identity.get("collection_ok") is not True:
            _add_error(
                errors,
                f"{label}_identity_collection_failed",
                f"{label} Git identity must have collection_ok=true",
            )
        if shape_error := git_identity_shape_error(git_identity):
            _add_error(errors, f"{label}_identity_invalid", f"{label} Git identity {shape_error}")
    if start_git != end_git:
        _add_error(errors, "git_identity_mismatch", "start and end Git identities differ")


def _index_steps(
    raw_steps: Any,
    *,
    errors: list[dict[str, str]],
) -> dict[str, Mapping[str, Any]]:
    observed: dict[str, Mapping[str, Any]] = {}
    duplicate_steps: list[str] = []
    if not isinstance(raw_steps, list):
        _add_error(errors, "invalid_steps", "report.steps must be a list")
        return observed
    for index, step in enumerate(raw_steps):
        if not isinstance(step, Mapping) or not isinstance(step.get("name"), str):
            _add_error(errors, "invalid_step", f"step at index {index} is not a named object")
            continue
        name = str(step["name"])
        if name in observed:
            duplicate_steps.append(name)
            continue
        observed[name] = step
    if duplicate_steps:
        _add_error(
            errors,
            "duplicate_step",
            "duplicate steps: " + ", ".join(sorted(set(duplicate_steps), key=str.casefold)),
        )
    return observed


def _identity_python_executable(
    report: Mapping[str, Any],
    key: str,
    errors: list[dict[str, str]],
) -> str | None:
    identity = report.get(key)
    if not isinstance(identity, Mapping):
        return None
    python_identity = identity.get("python")
    if not isinstance(python_identity, Mapping):
        _add_error(errors, f"missing_{key}_python", f"report.{key}.python must be an object")
        return None
    normalized, identity_error = validate_python_identity(python_identity)
    error_contract = {
        "missing_executable": (f"missing_{key}_python_executable", "must be a non-empty string"),
        "invalid_executable": (f"invalid_{key}_python_executable", "must be a portable Python fingerprint token"),
        "sha256_mismatch": (f"invalid_{key}_python_sha256", "SHA256 metadata must match the executable token"),
        "size_mismatch": (f"invalid_{key}_python_size", "size metadata must match the executable token"),
    }
    if identity_error:
        code, detail = error_contract[identity_error]
        _add_error(errors, code, f"report.{key}.python {detail}")
        return None
    return normalized


def _validate_python_identities(
    report: Mapping[str, Any],
    *,
    errors: list[dict[str, str]],
) -> str | None:
    start_executable = _identity_python_executable(report, "identity", errors)
    end_executable = _identity_python_executable(report, "identity_end", errors)
    if start_executable is not None and end_executable is not None and start_executable != end_executable:
        _add_error(
            errors,
            "python_identity_mismatch",
            "start and end Python executable fingerprints differ",
        )
    return start_executable


def _command_option(command: tuple[str, ...], option: str) -> str:
    index = command.index(option)
    return command[index + 1]


def _validate_topn_summary(
    step: Mapping[str, Any],
    *,
    spec: GateCommand,
    errors: list[dict[str, str]],
) -> None:
    summary = step.get("summary")
    if not isinstance(summary, Mapping):
        _add_error(errors, "missing_topn_evidence", "topn_slice.summary must be an object")
        return
    evidence = summary.get("topn_slice")
    if not isinstance(evidence, Mapping):
        _add_error(
            errors,
            "missing_topn_evidence",
            "topn_slice.summary.topn_slice must be an object",
        )
        return
    trade_date = _command_option(spec.command, "--date")
    top = int(_command_option(spec.command, "--top"))
    evidence_errors = validate_topn_slice_evidence(
        evidence,
        expected_trade_date=trade_date,
        expected_top=top,
        verify_input=False,
    )
    for detail in evidence_errors:
        _add_error(errors, "invalid_topn_evidence", detail)


def _validate_report_command_consistency(
    observed: Mapping[str, Mapping[str, Any]],
    *,
    required_specs: tuple[GateCommand, ...],
    identity_python_executable: str | None,
    errors: list[dict[str, str]],
) -> None:
    python_executables: set[str] = set()
    gate_tmp_roots: set[str] = set()
    for spec in required_specs:
        step = observed.get(spec.name)
        if step is None:
            continue
        command = step.get("command")
        if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
            continue
        if command and spec.command and is_python_executable(spec.command[0]):
            if is_python_executable(command[0]):
                normalized_executable = python_executable_identity(command[0])
                if not normalized_executable:
                    _add_error(
                        errors,
                        "invalid_step_python_executable",
                        f"step {spec.name!r} must use a portable Python fingerprint token",
                    )
                else:
                    python_executables.add(normalized_executable)
                    if (
                        identity_python_executable is not None
                        and normalized_executable != identity_python_executable
                    ):
                        _add_error(
                            errors,
                            "step_python_executable_mismatch",
                            f"step {spec.name!r} Python executable does not match report.identity",
                        )
        for index, expected_arg in enumerate(spec.command):
            if parse_gate_tmp_arg(expected_arg) is None or index >= len(command):
                continue
            observed_tmp = parse_gate_tmp_arg(command[index])
            if observed_tmp is None:
                _add_error(
                    errors,
                    "invalid_gate_tmp_path",
                    f"step {spec.name!r} has an invalid gate temporary path",
                )
                continue
            if portable_gate_tmp_arg(command[index]) != command[index]:
                _add_error(
                    errors,
                    "nonportable_gate_tmp_path",
                    f"step {spec.name!r} must use a gate-tmp:// portable path",
                )
            gate_tmp_roots.add(observed_tmp[2])

    if len(python_executables) > 1:
        _add_error(
            errors,
            "inconsistent_python_executable",
            "all Python steps must use the same executable fingerprint",
        )
    if len(gate_tmp_roots) > 1:
        _add_error(
            errors,
            "inconsistent_gate_tmp_root",
            "all gate temporary command arguments must share one portable run id",
        )


def _validate_steps(
    observed: Mapping[str, Mapping[str, Any]],
    *,
    raw_steps: Any,
    required_specs: tuple[GateCommand, ...],
    identity_python_executable: str | None,
    errors: list[dict[str, str]],
) -> tuple[list[str], list[str]]:
    required_steps = tuple(spec.name for spec in required_specs)
    missing_steps = [name for name in required_steps if name not in observed]
    for name in missing_steps:
        _add_error(errors, "missing_required_step", f"missing required step {name!r}")
    unexpected_steps = [name for name in observed if name not in required_steps]
    for name in unexpected_steps:
        _add_error(errors, "unexpected_step", f"unexpected step {name!r}")

    observed_order = (
        [str(step.get("name")) for step in raw_steps if isinstance(step, Mapping)]
        if isinstance(raw_steps, list)
        else []
    )
    if observed_order != list(required_steps):
        _add_error(
            errors,
            "noncanonical_step_order",
            f"expected {list(required_steps)!r}, got {observed_order!r}",
        )

    failed_steps: list[str] = []
    for spec in required_specs:
        step = observed.get(spec.name)
        if step is None:
            continue
        returncode = step.get("returncode")
        valid_returncode = type(returncode) is int and returncode == 0
        if type(returncode) is not int:
            _add_error(
                errors,
                "invalid_step_returncode",
                f"step {spec.name!r} returncode must be an integer, not {type(returncode).__name__}",
            )
        if step.get("ok") is not True or not valid_returncode:
            failed_steps.append(spec.name)
            _add_error(
                errors,
                "failed_step",
                f"step {spec.name!r} did not complete successfully",
            )
        if not commands_match(step.get("command"), spec.command):
            _add_error(
                errors,
                "step_command_mismatch",
                f"step {spec.name!r} command does not match the canonical command",
            )
        expected_cwd = spec.cwd or "."
        cwd = step.get("cwd")
        if not isinstance(cwd, str) or cwd.replace("\\", "/") != expected_cwd.replace("\\", "/"):
            _add_error(
                errors,
                "step_cwd_mismatch",
                f"step {spec.name!r} cwd must be {expected_cwd!r}",
            )
        if spec.name == "topn_slice":
            _validate_topn_summary(step, spec=spec, errors=errors)
    _validate_report_command_consistency(
        observed,
        required_specs=required_specs,
        identity_python_executable=identity_python_executable,
        errors=errors,
    )
    return missing_steps, failed_steps


def validate_full_gate_report(
    report: Mapping[str, Any],
    *,
    expected_profile: str,
    expected_commit: str,
    require_web_build: bool,
    require_topn_slice: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    normalized_commit = str(expected_commit).strip().casefold()
    if not _FULL_SHA_RE.fullmatch(normalized_commit):
        _add_error(
            errors,
            "invalid_expected_commit",
            "expected commit must be a full 40-character hexadecimal Git SHA",
        )
    _validate_report_header(report, expected_profile=expected_profile, errors=errors)
    for location in host_path_locations(report):
        _add_error(
            errors,
            "host_path_in_report",
            f"{location} contains a host-specific absolute path",
        )
    _validate_git_identities(report, expected_commit=normalized_commit, errors=errors)
    identity_python_executable = _validate_python_identities(report, errors=errors)
    required_specs = required_step_specs(
        profile=expected_profile,
        require_web_build=require_web_build,
        require_topn_slice=require_topn_slice,
    )
    required_steps = tuple(spec.name for spec in required_specs)
    raw_steps = report.get("steps")
    observed = _index_steps(raw_steps, errors=errors)
    missing_steps, failed_steps = _validate_steps(
        observed,
        raw_steps=raw_steps,
        required_specs=required_specs,
        identity_python_executable=identity_python_executable,
        errors=errors,
    )

    return {
        "ok": not errors,
        "errors": errors,
        "expected_profile": expected_profile,
        "expected_commit": normalized_commit,
        "required_steps": list(required_steps),
        "observed_steps": list(observed),
        "missing_steps": missing_steps,
        "failed_steps": failed_steps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate that a full-gate JSON report is clean, complete, and bound to a Git commit."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--expected-profile", choices=("ci", "full"), required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--require-web-build", action="store_true")
    parser.add_argument("--require-topn-slice", action="store_true")
    args = parser.parse_args(argv)

    if args.report.name.casefold() == "latest.json":
        print("[FAIL] mutable_report_name: latest.json cannot be release evidence")
        return 1
    try:
        payload = load_json_object(args.report)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[FAIL] invalid_report: {type(exc).__name__}: {exc}")
        return 1

    result = validate_full_gate_report(
        payload,
        expected_profile=args.expected_profile,
        expected_commit=args.expected_commit,
        require_web_build=args.require_web_build,
        require_topn_slice=args.require_topn_slice,
    )
    if not result["ok"]:
        for error in result["errors"]:
            print(f"[FAIL] {error['code']}: {error['detail']}")
        return 1
    print(
        "[OK] full-gate release evidence: "
        f"profile={args.expected_profile} commit={result['expected_commit']} "
        f"required_steps={len(result['required_steps'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
