from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.utils import canonicalize_name

if TYPE_CHECKING or __package__:
    from tools.audit_ci_constraints import parse_exact_pins
else:
    from audit_ci_constraints import parse_exact_pins

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONSTRAINTS = ROOT / "constraints-ci.txt"
DEFAULT_POLICY = ROOT / "python-dependency-audit-policy.json"


class PythonDependencyAuditError(RuntimeError):
    """Raised when pip-audit does not provide a complete, trustworthy report."""


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PythonDependencyAuditError(f"{label} must be a JSON object")
    return value


def _require_string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PythonDependencyAuditError(f"{label} must be a list of non-empty strings")
    return sorted(set(value), key=str.casefold)


def parse_pip_audit_report(
    raw_report: object,
    *,
    expected_pins: dict[str, str],
) -> dict[str, Any]:
    report = _require_mapping(raw_report, label="pip-audit report")
    raw_dependencies = report.get("dependencies")
    if not isinstance(raw_dependencies, list):
        raise PythonDependencyAuditError("pip-audit dependencies must be a JSON array")
    fixes = report.get("fixes")
    if not isinstance(fixes, list) or fixes:
        raise PythonDependencyAuditError("pip-audit fixes must be an empty JSON array")

    dependencies: list[dict[str, Any]] = []
    observed: dict[str, str] = {}
    vulnerability_count = 0
    vulnerable_package_count = 0
    for index, raw_dependency in enumerate(raw_dependencies):
        dependency = _require_mapping(raw_dependency, label=f"dependency record {index}")
        raw_name = dependency.get("name")
        version = dependency.get("version")
        if not isinstance(raw_name, str) or not raw_name:
            raise PythonDependencyAuditError(f"dependency record {index} has an invalid name")
        if not isinstance(version, str) or not version:
            raise PythonDependencyAuditError(f"dependency {raw_name!r} has an invalid version")
        name = canonicalize_name(raw_name)
        if name in observed:
            raise PythonDependencyAuditError(f"duplicate dependency record: {name}")
        observed[name] = version

        if "skip_reason" in dependency:
            skip_reason = dependency["skip_reason"]
            if skip_reason is not None:
                if not isinstance(skip_reason, str) or not skip_reason.strip():
                    raise PythonDependencyAuditError(
                        f"dependency {name!r} has an invalid skip_reason"
                    )
                raise PythonDependencyAuditError(
                    f"dependency {name!r} was not audited: {skip_reason.strip()}"
                )

        raw_vulnerabilities = dependency.get("vulns")
        if not isinstance(raw_vulnerabilities, list):
            raise PythonDependencyAuditError(f"vulnerabilities for {name} must be a JSON array")
        vulnerabilities: list[dict[str, Any]] = []
        for vulnerability_index, raw_vulnerability in enumerate(raw_vulnerabilities):
            vulnerability = _require_mapping(
                raw_vulnerability,
                label=f"vulnerability {vulnerability_index} for {name}",
            )
            vulnerability_id = vulnerability.get("id")
            if not isinstance(vulnerability_id, str) or not vulnerability_id:
                raise PythonDependencyAuditError(f"vulnerability for {name} has an invalid id")
            vulnerabilities.append(
                {
                    "id": vulnerability_id,
                    "aliases": _require_string_list(
                        vulnerability.get("aliases"),
                        label=f"aliases for {name}/{vulnerability_id}",
                    ),
                    "fix_versions": _require_string_list(
                        vulnerability.get("fix_versions"),
                        label=f"fix versions for {name}/{vulnerability_id}",
                    ),
                }
            )
        vulnerabilities.sort(key=lambda item: str(item["id"]).casefold())
        vulnerability_count += len(vulnerabilities)
        vulnerable_package_count += bool(vulnerabilities)
        dependencies.append(
            {
                "name": name,
                "version": version,
                "vulnerabilities": vulnerabilities,
            }
        )

    if observed != expected_pins:
        missing = sorted(set(expected_pins) - set(observed), key=str.casefold)
        unexpected = sorted(set(observed) - set(expected_pins), key=str.casefold)
        mismatched = [
            {
                "name": name,
                "expected": expected_pins[name],
                "observed": observed[name],
            }
            for name in sorted(set(expected_pins) & set(observed), key=str.casefold)
            if expected_pins[name] != observed[name]
        ]
        raise PythonDependencyAuditError(
            "pip-audit dependency set does not match constraints: "
            f"missing={missing} unexpected={unexpected} mismatched={mismatched}"
        )

    dependencies.sort(key=lambda item: str(item["name"]).casefold())
    return {
        "dependencies": dependencies,
        "dependency_count": len(dependencies),
        "vulnerability_count": vulnerability_count,
        "vulnerable_package_count": vulnerable_package_count,
    }


def load_zero_vulnerability_policy(path: Path) -> dict[str, int]:
    try:
        raw_policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PythonDependencyAuditError(f"invalid Python dependency audit policy: {exc}") from exc
    policy = _require_mapping(raw_policy, label="Python dependency audit policy")
    if set(policy) != {"schema_version", "maximum_vulnerabilities"}:
        raise PythonDependencyAuditError("Python dependency audit policy has unexpected fields")
    if policy.get("schema_version") != 1:
        raise PythonDependencyAuditError("Python dependency audit policy schema_version must be 1")
    maximum = policy.get("maximum_vulnerabilities")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum != 0:
        raise PythonDependencyAuditError("maximum_vulnerabilities must be exactly zero")
    return {"schema_version": 1, "maximum_vulnerabilities": maximum}


def _run_pip_audit(
    constraints_path: Path,
    *,
    python_executable: str,
    expected_pins: dict[str, str],
) -> dict[str, Any]:
    command = [
        python_executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(constraints_path),
        "--no-deps",
        "--disable-pip",
        "--format",
        "json",
        "--vulnerability-service",
        "osv",
        "--progress-spinner",
        "off",
        "--desc",
        "off",
        "--aliases",
        "on",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise PythonDependencyAuditError("pip-audit timed out after 300 seconds") from exc
    except OSError as exc:
        raise PythonDependencyAuditError(f"could not start pip-audit: {exc}") from exc
    try:
        raw_report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise PythonDependencyAuditError(f"pip-audit returned invalid JSON: {detail}") from exc
    report = parse_pip_audit_report(raw_report, expected_pins=expected_pins)
    expected_returncode = 1 if report["vulnerability_count"] else 0
    if completed.returncode != expected_returncode:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise PythonDependencyAuditError(f"unexpected pip-audit status: {detail}")
    return report


def audit_python_dependencies(
    constraints_path: Path = DEFAULT_CONSTRAINTS,
    policy_path: Path = DEFAULT_POLICY,
    *,
    python_executable: str | None = None,
) -> dict[str, Any]:
    constraints = Path(constraints_path).resolve()
    pins, constraint_violations = parse_exact_pins(constraints)
    if constraint_violations:
        raise PythonDependencyAuditError(
            f"constraints are not exact and unique: {constraint_violations}"
        )
    if not pins:
        raise PythonDependencyAuditError("constraints contain no dependency pins")
    policy = load_zero_vulnerability_policy(Path(policy_path))
    report = _run_pip_audit(
        constraints,
        python_executable=python_executable or sys.executable,
        expected_pins=pins,
    )
    violations: list[dict[str, int | str]] = []
    if report["vulnerability_count"] > policy["maximum_vulnerabilities"]:
        violations.append(
            {
                "kind": "vulnerability_budget_exceeded",
                "actual": report["vulnerability_count"],
                "maximum": policy["maximum_vulnerabilities"],
            }
        )
    return {
        "schema_version": 1,
        "ok": not violations,
        "constraints_path": str(constraints),
        "policy_path": str(Path(policy_path).resolve()),
        "vulnerability_service": "osv",
        "report": report,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the exact Python CI dependency closure against a zero-vulnerability policy."
    )
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--python", dest="python_executable", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        payload = audit_python_dependencies(
            args.constraints,
            args.policy,
            python_executable=args.python_executable,
        )
    except PythonDependencyAuditError as exc:
        print(f"[ERROR] Python dependency audit: {exc}")
        return 2
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    report = payload["report"]
    print(
        "[python] "
        f"dependencies={report['dependency_count']} "
        f"vulnerable_packages={report['vulnerable_package_count']} "
        f"vulnerabilities={report['vulnerability_count']}"
    )
    if payload["ok"]:
        print("[OK] Python dependency audit policy satisfied")
        return 0
    for violation in payload["violations"]:
        print(
            f"[FAIL] vulnerabilities: actual={violation['actual']} "
            f"maximum={violation['maximum']}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
