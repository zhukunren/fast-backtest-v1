from __future__ import annotations

import argparse
import json
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONSTRAINTS = ROOT / "constraints-ci.txt"
DEFAULT_REQUIREMENTS = ROOT / "requirements.txt"
QUALITY_ROOTS = (
    "build",
    "mypy",
    "pip",
    "pip-audit",
    "pytest",
    "pytest-cov",
    "ruff",
    "setuptools",
    "wheel",
)


def parse_exact_pins(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    pins: dict[str, str] = {}
    violations: list[dict[str, str]] = []
    for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except ValueError as exc:
            violations.append(
                {"kind": "invalid_constraint", "line": str(line_no), "value": line, "error": str(exc)}
            )
            continue
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            violations.append(
                {"kind": "constraint_not_exact", "line": str(line_no), "value": line}
            )
            continue
        name = canonicalize_name(requirement.name)
        if name in pins:
            violations.append(
                {"kind": "duplicate_constraint", "line": str(line_no), "value": line}
            )
            continue
        pins[name] = specifiers[0].version
    return pins, violations


def _requirement_roots(path: Path) -> list[str]:
    roots = list(QUALITY_ROOTS)
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            roots.append(Requirement(line).name)
    return sorted({canonicalize_name(name) for name in roots}, key=str.casefold)


def audit_ci_constraints(
    constraints_path: Path = DEFAULT_CONSTRAINTS,
    requirements_path: Path = DEFAULT_REQUIREMENTS,
) -> dict[str, Any]:
    pins, violations = parse_exact_pins(constraints_path)
    pending = _requirement_roots(requirements_path)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            violations.append({"kind": "required_distribution_missing", "name": name})
            continue
        installed_name = canonicalize_name(str(distribution.metadata.get("Name") or name))
        pinned_version = pins.get(installed_name)
        if pinned_version is None:
            violations.append(
                {"kind": "transitive_constraint_missing", "name": installed_name, "installed": distribution.version}
            )
        elif distribution.version != pinned_version:
            violations.append(
                {
                    "kind": "installed_version_mismatch",
                    "name": installed_name,
                    "installed": distribution.version,
                    "pinned": pinned_version,
                }
            )
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency not in visited:
                pending.append(dependency)
    return {
        "ok": not violations,
        "constraints_path": str(constraints_path),
        "requirements_path": str(requirements_path),
        "pin_count": len(pins),
        "closure_count": len(visited),
        "closure": sorted(visited, key=str.casefold),
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify exact pins for the installed CI dependency closure.")
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = audit_ci_constraints(args.constraints, args.requirements)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if payload["ok"]:
        print(
            f"[OK] CI constraints: pins={payload['pin_count']} "
            f"active_closure={payload['closure_count']}"
        )
        return 0
    for violation in payload["violations"]:
        print(f"[FAIL] {violation['kind']}: {violation.get('name') or violation.get('value')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
