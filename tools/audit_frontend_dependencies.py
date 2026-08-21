from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_ROOT = ROOT / "web" / "frontend"
DEFAULT_POLICY_PATH = DEFAULT_FRONTEND_ROOT / "dependency-audit-policy.json"
SEVERITIES = ("info", "low", "moderate", "high", "critical")
NPM_AUDIT_TIMEOUT_SECONDS = 300


class DependencyAuditError(RuntimeError):
    """Raised when npm does not return a trustworthy audit report."""


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DependencyAuditError(f"{label} must be a JSON object")
    return value


def parse_npm_audit_report(raw_report: object, *, scope: str) -> dict[str, Any]:
    report = _require_mapping(raw_report, label=f"npm audit report for {scope}")
    if report.get("error"):
        raise DependencyAuditError(f"npm audit failed for {scope}: {report['error']}")
    if report.get("auditReportVersion") != 2:
        raise DependencyAuditError(f"npm audit report version for {scope} must be 2")

    metadata = _require_mapping(report.get("metadata"), label=f"metadata for {scope}")
    raw_counts = _require_mapping(
        metadata.get("vulnerabilities"),
        label=f"vulnerability counts for {scope}",
    )
    counts: dict[str, int] = {}
    for severity in SEVERITIES:
        value = raw_counts.get(severity)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DependencyAuditError(
                f"invalid {severity} vulnerability count for {scope}: {value!r}"
            )
        counts[severity] = value
    expected_total = sum(counts.values())
    if raw_counts.get("total") != expected_total:
        raise DependencyAuditError(
            f"inconsistent vulnerability total for {scope}: "
            f"expected {expected_total}, got {raw_counts.get('total')!r}"
        )

    raw_vulnerabilities = _require_mapping(
        report.get("vulnerabilities"),
        label=f"vulnerability records for {scope}",
    )
    packages: list[dict[str, Any]] = []
    package_counts = dict.fromkeys(SEVERITIES, 0)
    for package_name in sorted(raw_vulnerabilities, key=str.casefold):
        item = _require_mapping(
            raw_vulnerabilities[package_name],
            label=f"vulnerability record {package_name!r}",
        )
        via = item.get("via")
        if not isinstance(via, list):
            raise DependencyAuditError(f"invalid via list for {package_name!r}")
        record_severity = item.get("severity")
        if not isinstance(record_severity, str) or record_severity not in SEVERITIES:
            raise DependencyAuditError(
                f"invalid severity for {package_name!r}: {record_severity!r}"
            )
        direct = item.get("isDirect")
        if not isinstance(direct, bool):
            raise DependencyAuditError(
                f"invalid isDirect flag for {package_name!r}: {direct!r}"
            )
        advisory_sources = sorted(
            {
                source
                for entry in via
                if isinstance(entry, dict)
                and isinstance((source := entry.get("source")), int)
                and not isinstance(source, bool)
            }
        )
        via_packages = sorted(
            {entry for entry in via if isinstance(entry, str)},
            key=str.casefold,
        )
        packages.append(
            {
                "name": package_name,
                "severity": record_severity,
                "direct": direct,
                "advisory_sources": advisory_sources,
                "via_packages": via_packages,
            }
        )
        package_counts[record_severity] += 1
    if package_counts != counts:
        raise DependencyAuditError(
            f"inconsistent vulnerability records for {scope}: "
            f"expected {counts}, got {package_counts}"
        )
    return {
        "scope": scope,
        "counts": counts,
        "total": expected_total,
        "packages": packages,
    }


def evaluate_policy(
    reports: dict[str, dict[str, Any]],
    policy: object,
) -> list[dict[str, Any]]:
    policy_root = _require_mapping(policy, label="dependency audit policy")
    if set(policy_root) != {"schema_version", "scopes"}:
        raise DependencyAuditError("dependency audit policy has unexpected fields")
    if policy_root.get("schema_version") != 1:
        raise DependencyAuditError("dependency audit policy schema_version must be 1")
    policy_scopes = _require_mapping(policy_root.get("scopes"), label="policy scopes")
    violations: list[dict[str, Any]] = []
    if set(policy_scopes) != set(reports):
        raise DependencyAuditError(
            "policy scopes must exactly match audited scopes: "
            f"policy={sorted(policy_scopes)} reports={sorted(reports)}"
        )
    for scope in sorted(reports, key=str.casefold):
        scope_policy = _require_mapping(policy_scopes[scope], label=f"policy scope {scope}")
        if set(scope_policy) != {"maximum_vulnerabilities"}:
            raise DependencyAuditError(f"policy scope {scope} has unexpected fields")
        maximums = _require_mapping(
            scope_policy.get("maximum_vulnerabilities"),
            label=f"maximum vulnerabilities for {scope}",
        )
        if set(maximums) != set(SEVERITIES):
            raise DependencyAuditError(
                f"maximum vulnerabilities for {scope} must define {list(SEVERITIES)}"
            )
        counts = reports[scope]["counts"]
        for severity in SEVERITIES:
            maximum = maximums[severity]
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum != 0:
                raise DependencyAuditError(
                    f"policy maximum for {scope}/{severity} must be exactly zero"
                )
            actual = int(counts[severity])
            if actual > maximum:
                violations.append(
                    {
                        "kind": "vulnerability_budget_exceeded",
                        "scope": scope,
                        "severity": severity,
                        "actual": actual,
                        "maximum": maximum,
                    }
                )
    return violations


def _run_npm_audit(
    npm_executable: str,
    frontend_root: Path,
    *,
    scope: str,
) -> dict[str, Any]:
    command = [npm_executable, "audit", "--json", "--audit-level=info"]
    if scope == "production":
        command.append("--omit=dev")
    try:
        completed = subprocess.run(
            command,
            cwd=frontend_root,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=NPM_AUDIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise DependencyAuditError(
            f"npm audit timed out for {scope} after {NPM_AUDIT_TIMEOUT_SECONDS} seconds"
        ) from exc
    except OSError as exc:
        raise DependencyAuditError(f"could not start npm audit for {scope}: {exc}") from exc
    try:
        raw_report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise DependencyAuditError(
            f"npm audit returned invalid JSON for {scope}: {detail}"
        ) from exc
    report = parse_npm_audit_report(raw_report, scope=scope)
    expected_returncode = 1 if report["total"] else 0
    if completed.returncode != expected_returncode:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise DependencyAuditError(
            f"unexpected npm audit status for {scope}: {detail}"
        )
    return report


def audit_frontend_dependencies(
    frontend_root: Path = DEFAULT_FRONTEND_ROOT,
    policy_path: Path = DEFAULT_POLICY_PATH,
    *,
    npm_executable: str | None = None,
) -> dict[str, Any]:
    executable = npm_executable or shutil.which("npm.cmd") or shutil.which("npm")
    if not executable:
        raise DependencyAuditError("npm executable not found")
    root = Path(frontend_root).resolve()
    if not (root / "package-lock.json").is_file():
        raise DependencyAuditError(f"frontend lockfile not found: {root / 'package-lock.json'}")
    try:
        policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyAuditError(f"invalid dependency audit policy: {exc}") from exc

    reports = {
        scope: _run_npm_audit(executable, root, scope=scope)
        for scope in ("all", "production")
    }
    violations = evaluate_policy(reports, policy)
    return {
        "schema_version": 1,
        "ok": not violations,
        "frontend_root": str(root),
        "policy_path": str(Path(policy_path).resolve()),
        "reports": reports,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit full and production frontend dependency scopes against policy."
    )
    parser.add_argument("--frontend-root", type=Path, default=DEFAULT_FRONTEND_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--npm", dest="npm_executable", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        payload = audit_frontend_dependencies(
            args.frontend_root,
            args.policy,
            npm_executable=args.npm_executable,
        )
    except DependencyAuditError as exc:
        print(f"[ERROR] frontend dependency audit: {exc}")
        return 2
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    for scope in ("all", "production"):
        report = payload["reports"][scope]
        print(f"[{scope}] total={report['total']} counts={report['counts']}")
    if payload["ok"]:
        print("[OK] frontend dependency audit policy satisfied")
        return 0
    for violation in payload["violations"]:
        print(
            f"[FAIL] {violation['scope']}/{violation['severity']}: "
            f"actual={violation['actual']} maximum={violation['maximum']}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
