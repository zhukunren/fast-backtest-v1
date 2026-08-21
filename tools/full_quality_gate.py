from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from typing import Any

try:
    from coverage_grouping import coverage_sources_from_manifest
    from quality_gate_output import ANSI_ESCAPE_RE, summarize_gate_step
    from quality_gate_output import summarize_command_output as summarize_command_output
    from quality_gate_runtime import (
        GateCommand,
        collect_run_identity,
        identity_collection_ok,
        json_out_preflight_error,
        portable_command,
    )
except ModuleNotFoundError:
    from tools.coverage_grouping import coverage_sources_from_manifest
    from tools.quality_gate_output import ANSI_ESCAPE_RE, summarize_gate_step
    from tools.quality_gate_output import summarize_command_output as summarize_command_output
    from tools.quality_gate_runtime import (
        GateCommand,
        collect_run_identity,
        identity_collection_ok,
        json_out_preflight_error,
        portable_command,
    )

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_LINT_PATHS = (
    "app",
    "backtest",
    "config",
    "core",
    "data",
    "runtime_lifecycle",
    "strategies",
    "strategy_library",
    "tests",
    "tests_support",
    "tools",
    "trading",
    "web",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _emit_process_output(stdout: str, stderr: str) -> None:
    for value in (stdout, stderr):
        if not value:
            continue
        output_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_value = value.encode(output_encoding, errors="replace").decode(
            output_encoding,
            errors="replace",
        )
        print(safe_value, end="" if safe_value.endswith("\n") else "\n")


def build_gate_commands(
    *,
    profile: str,
    skip_perf: bool = False,
    skip_hotpath: bool = False,
    include_topn_slice: bool = False,
    include_web_build: bool = False,
) -> list[GateCommand]:
    gate_tmp_root = ROOT / "_pytest_tmp" / "full_quality_gate" / uuid4().hex
    coverage_root = gate_tmp_root / "coverage"
    coverage_data = coverage_root / ".coverage"
    coverage_json = coverage_root / "coverage.json"
    grouped_coverage_json = coverage_root / "grouped-coverage.json"
    python_dependency_json = gate_tmp_root / "python-dependency-audit.json"
    frontend_dependency_json = gate_tmp_root / "frontend-dependency-audit.json"
    topn_json = gate_tmp_root / "topn-slice.json"
    commands = [
        GateCommand("full_release_audit", (sys.executable, "tools/audit_full_release.py")),
        GateCommand("ci_constraints_audit", (sys.executable, "tools/audit_ci_constraints.py")),
        GateCommand(
            "python_dependency_audit",
            (
                sys.executable,
                "tools/audit_python_dependencies.py",
                "--json-out",
                str(python_dependency_json),
            ),
        ),
        GateCommand(
            "syntax_check",
            (
                sys.executable,
                "tools/syntax_check.py",
                "app",
                "backtest",
                "config",
                "core",
                "data",
                "runtime_lifecycle",
                "strategies",
                "strategy_library",
                "tools",
                "trading",
                "web",
            ),
        ),
        GateCommand(
            "boundary_tests",
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/test_import_boundaries.py",
                "tests/test_runtime_import_footprint.py",
                "tests/test_layer_ownership_boundaries.py",
                "tests/test_panel_native_contracts.py",
                "tests/test_retired_panel_paths.py",
                "tests/test_runtime_artifact_boundaries.py",
                "tests/test_artifact_generation.py",
                "tests/test_run_protocol_artifacts.py",
                "tests/test_documentation_contracts.py",
                "-q",
            ),
        ),
        GateCommand(
            "workspace_contract_tests",
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/test_basic.py",
                "tests/test_offline_smoke.py",
                "web/tests/test_strategy_description_contract.py",
                "-q",
            ),
        ),
        GateCommand(
            "research_trust_tests",
            (
                sys.executable,
                "tools/run_curated_regression.py",
                "--group",
                "research_trust",
            ),
        ),
        GateCommand(
            "llm_authoring_gate",
            (
                sys.executable,
                "tools/llm_authoring_gate.py",
                "--skip-offline-smoke",
            ),
        ),
        GateCommand("active_source_lint", (sys.executable, "-m", "ruff", "check", *ACTIVE_LINT_PATHS)),
        GateCommand("offline_smoke", (sys.executable, "tests/test_offline_smoke.py")),
        GateCommand(
            "strategy_static",
            (
                sys.executable,
                "tools/strategy_static_check.py",
                "strategies/templates/template.py",
                "strategy_library/builtin/offline_smoke.py",
            ),
        ),
        GateCommand(
            "data_quality_audit",
            (sys.executable, "tools/data_quality_audit.py", "--target", "offline-smoke", "--sample-size", "200"),
        ),
        GateCommand("maintainability_audit", (sys.executable, "tools/audit_maintainability.py")),
        GateCommand("complexity_audit", (sys.executable, "tools/audit_complexity.py")),
    ]
    performance_commands: list[GateCommand] = []
    if not skip_perf:
        repeat_args = ("--repeat-runs", "3") if profile in {"ci", "full"} else ()
        performance_commands.append(
            GateCommand(
                "perf_smoke",
                (sys.executable, "tools/perf_smoke.py", *repeat_args),
                env={
                    "PERF_SMOKE_TMP_ROOT": str(gate_tmp_root / "perf_tmp"),
                    "PERF_SMOKE_OUTPUT_ROOT": str(gate_tmp_root / "perf_smoke"),
                },
            )
        )
    if not skip_hotpath:
        performance_commands.append(GateCommand("hotpath_smoke", (sys.executable, "tools/hotpath_smoke.py")))
    commands[3:3] = performance_commands
    if profile == "quick":
        commands.append(
            GateCommand(
                "web_backend_regressions",
                (sys.executable, "-m", "pytest", "web/tests", "-q"),
            )
        )
    if profile in {"ci", "full"}:
        coverage_args = tuple(f"--cov={source}" for source in coverage_sources_from_manifest())
        commands.extend(
            [
                GateCommand(
                    "full_v1_tests",
                    (
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests",
                        "web/tests",
                        "-q",
                        "--cov-config=config/coverage-v1.ini",
                        *coverage_args,
                        "--cov-report=term:skip-covered",
                        f"--cov-report=json:{coverage_json}",
                        "--cov-fail-under=75",
                    ),
                    env={"COVERAGE_FILE": str(coverage_data)},
                ),
                GateCommand(
                    "grouped_coverage_audit",
                    (
                        sys.executable,
                        "tools/audit_grouped_coverage.py",
                        "--manifest",
                        "config/coverage-groups-v1.json",
                        "--coverage-json",
                        str(coverage_json),
                        "--json-out",
                        str(grouped_coverage_json),
                    ),
                ),
                GateCommand(
                    "v1_progressive_type_check",
                    (sys.executable, "-m", "mypy", "--config-file", "mypy-v1-progressive.ini"),
                ),
                GateCommand(
                    "v1_contract_type_check",
                    (sys.executable, "-m", "mypy", "--config-file", "mypy-v1-contracts.ini"),
                ),
            ]
        )
    if include_topn_slice:
        commands.append(
            GateCommand(
                "topn_slice",
                (
                    sys.executable,
                    "tools/check_topn_slice.py",
                    "--date",
                    "2025-05-16",
                    "--top",
                    "10",
                    "--json-out",
                    str(topn_json),
                ),
            )
        )
    if include_web_build:
        npm_executable = "npm.cmd" if os.name == "nt" else "npm"
        frontend_cwd = "web/frontend"
        commands.extend(
            (
                GateCommand(
                    "web_frontend_dependency_audit",
                    (
                        sys.executable,
                        "tools/audit_frontend_dependencies.py",
                        "--json-out",
                        str(frontend_dependency_json),
                    ),
                ),
                GateCommand("web_frontend_unit_tests", (npm_executable, "run", "test:unit"), cwd=frontend_cwd),
                GateCommand(
                    "web_frontend_all_source_coverage",
                    (npm_executable, "run", "test:coverage:all"),
                    cwd=frontend_cwd,
                ),
                GateCommand("web_frontend_build", (npm_executable, "run", "build"), cwd=frontend_cwd),
                GateCommand("web_frontend_bundle_audit", (sys.executable, "tools/audit_frontend_bundle.py")),
            )
        )
    return commands


def run_full_quality_gate(
    *,
    profile: str,
    dry_run: bool = False,
    skip_perf: bool = False,
    skip_hotpath: bool = False,
    include_topn_slice: bool = False,
    include_web_build: bool = False,
) -> dict[str, Any]:
    gate_started_at = _utc_now()
    gate_started = perf_counter()
    identity = collect_run_identity()
    commands = build_gate_commands(
        profile=profile,
        skip_perf=skip_perf,
        skip_hotpath=skip_hotpath,
        include_topn_slice=include_topn_slice,
        include_web_build=include_web_build,
    )
    results: list[dict[str, Any]] = []

    def finish(*, ok: bool, status: str, failed_step: str = "") -> dict[str, Any]:
        identity_check_started_at = _utc_now()
        identity_check_started = perf_counter()
        identity_end = collect_run_identity()
        start_identity_ok = identity_collection_ok(identity)
        end_identity_ok = identity_collection_ok(identity_end)
        identity_stable = (
            start_identity_ok
            and end_identity_ok
            and identity.get("git", {}) == identity_end.get("git", {})
            and identity.get("python", {}) == identity_end.get("python", {})
        )
        if not dry_run:
            start_git = identity.get("git", {})
            end_git = identity_end.get("git", {})
            results.append(
                {
                    "name": "worktree_stability",
                    "command": [],
                    "cwd": ".",
                    "returncode": 0 if identity_stable else 1,
                    "ok": identity_stable,
                    "started_at": identity_check_started_at,
                    "duration_sec": round(perf_counter() - identity_check_started, 6),
                    "summary": {
                        "start_status_sha256": start_git.get("status_sha256", ""),
                        "end_status_sha256": end_git.get("status_sha256", ""),
                        "start_worktree_sha256": start_git.get("worktree_sha256", ""),
                        "end_worktree_sha256": end_git.get("worktree_sha256", ""),
                        "start_collection_ok": start_identity_ok,
                        "end_collection_ok": end_identity_ok,
                        "start_error": start_git.get("error", ""),
                        "end_error": end_git.get("error", ""),
                    },
                }
            )
        effective_ok = ok
        effective_status = status
        effective_failed_step = failed_step
        if not dry_run and ok and not identity_stable:
            effective_ok = False
            effective_status = "failed"
            effective_failed_step = "worktree_stability"
        payload: dict[str, Any] = {
            "schema_version": 2,
            "ok": effective_ok,
            "status": effective_status,
            "profile": profile,
            "started_at": gate_started_at,
            "finished_at": _utc_now(),
            "duration_sec": round(perf_counter() - gate_started, 6),
            "identity": identity,
            "identity_end": identity_end,
            "identity_stable": identity_stable,
            "steps": results,
        }
        if effective_failed_step:
            payload["failed_step"] = effective_failed_step
        return payload

    if not identity_collection_ok(identity):
        return finish(
            ok=False,
            status="failed",
            failed_step="worktree_stability",
        )
    python_identity = identity.get("python")
    python_token = str(python_identity.get("executable") or "") if isinstance(python_identity, dict) else ""

    for spec in commands:
        cwd = ROOT / spec.cwd if spec.cwd else ROOT
        print(f"[full-gate] {spec.name}: {spec.display}")
        if dry_run:
            results.append(
                {
                    "name": spec.name,
                    "command": portable_command(spec.command, python_token=python_token),
                    "cwd": spec.cwd or ".",
                    "returncode": None,
                    "ok": None,
                }
            )
            continue
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if spec.env:
            env.update(spec.env)
        coverage_file = str(env.get("COVERAGE_FILE") or "").strip()
        if coverage_file:
            Path(coverage_file).parent.mkdir(parents=True, exist_ok=True)
        step_started_at = _utc_now()
        step_started = perf_counter()
        try:
            completed = subprocess.run(
                list(spec.command),
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            item = {
                "name": spec.name,
                "command": portable_command(spec.command, python_token=python_token),
                "cwd": spec.cwd or ".",
                "returncode": None,
                "ok": False,
                "started_at": step_started_at,
                "duration_sec": round(perf_counter() - step_started, 6),
                "error": f"{type(exc).__name__}: {exc}",
            }
            results.append(item)
            if spec.required:
                return finish(ok=False, status="failed", failed_step=spec.name)
            continue
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        _emit_process_output(stdout, stderr)
        summary, evidence_error = summarize_gate_step(
            spec,
            returncode=int(completed.returncode),
            stdout=stdout,
            stderr=stderr,
        )
        step_ok = completed.returncode == 0 and not evidence_error
        item = {
            "name": spec.name,
            "command": portable_command(spec.command, python_token=python_token),
            "cwd": spec.cwd or ".",
            "returncode": int(completed.returncode),
            "ok": step_ok,
            "started_at": step_started_at,
            "duration_sec": round(perf_counter() - step_started, 6),
            "summary": summary,
        }
        if evidence_error:
            item["error"] = evidence_error
        if not step_ok:
            item["output_tail"] = ANSI_ESCAPE_RE.sub("", "\n".join((stdout, stderr)))[-4000:]
        results.append(item)
        if not step_ok and spec.required:
            return finish(ok=False, status="failed", failed_step=spec.name)
    return finish(ok=True, status="dry_run" if dry_run else "completed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run full workspace release and functionality gates.")
    parser.add_argument("--profile", choices=("quick", "ci", "full"), default="quick")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-perf", action="store_true")
    parser.add_argument("--skip-hotpath", action="store_true")
    parser.add_argument("--include-topn-slice", action="store_true")
    parser.add_argument("--include-web-build", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.json_out is not None:
        preflight_error = json_out_preflight_error(args.json_out)
        if preflight_error:
            print(f"[FAIL] unsafe_json_out: {preflight_error}")
            return 2

    payload = run_full_quality_gate(
        profile=args.profile,
        dry_run=args.dry_run,
        skip_perf=args.skip_perf,
        skip_hotpath=args.skip_hotpath,
        include_topn_slice=args.include_topn_slice,
        include_web_build=args.include_web_build,
    )
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "status": payload["status"]}, ensure_ascii=True, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
