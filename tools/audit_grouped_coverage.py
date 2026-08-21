from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from coverage_grouping import (
        DEFAULT_MANIFEST,
        METRIC_NAMES,
        ROOT,
        _classification as _classification,
        _coverage_relative_path,
        _normalized_relative_path,
        _sorted_strings,
        collect_tracked_product_files as collect_tracked_product_files,
        load_json_object,
        validate_manifest as validate_manifest,
    )
except ModuleNotFoundError:
    from tools.coverage_grouping import (
        DEFAULT_MANIFEST,
        METRIC_NAMES,
        ROOT,
        _classification as _classification,
        _coverage_relative_path,
        _normalized_relative_path,
        _sorted_strings,
        collect_tracked_product_files as collect_tracked_product_files,
        load_json_object,
        validate_manifest as validate_manifest,
    )


@dataclass(frozen=True, slots=True)
class CoverageCounts:
    covered: int = 0
    total: int = 0

    def add(self, other: CoverageCounts) -> CoverageCounts:
        return CoverageCounts(covered=self.covered + other.covered, total=self.total + other.total)

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else (100.0 * self.covered / self.total)

    def as_dict(self) -> dict[str, int | float]:
        return {
            "covered": self.covered,
            "total": self.total,
            "percent": round(self.percent, 2),
        }


@dataclass(frozen=True, slots=True)
class FileCoverage:
    statement: CoverageCounts
    branch: CoverageCounts

    @property
    def combined(self) -> CoverageCounts:
        return self.statement.add(self.branch)

    def metric(self, name: str) -> CoverageCounts:
        if name == "statement":
            return self.statement
        if name == "branch":
            return self.branch
        if name == "combined":
            return self.combined
        raise KeyError(name)


def _coverage_files(
    coverage_payload: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, FileCoverage], list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []
    meta = coverage_payload.get("meta")
    if not isinstance(meta, Mapping) or meta.get("branch_coverage") is not True:
        violations.append({"kind": "branch_coverage_not_enabled"})
    raw_files = coverage_payload.get("files")
    if not isinstance(raw_files, Mapping) or not raw_files:
        return {}, [*violations, {"kind": "coverage_files_missing"}]

    files: dict[str, FileCoverage] = {}
    folded_paths: dict[str, str] = {}
    for raw_path, raw_record in raw_files.items():
        try:
            path = _coverage_relative_path(str(raw_path), root=root)
        except ValueError as exc:
            violations.append({"kind": "invalid_coverage_path", "path": str(raw_path), "detail": str(exc)})
            continue
        folded = path.casefold()
        if folded in folded_paths:
            violations.append(
                {
                    "kind": "duplicate_coverage_path",
                    "path": path,
                    "other_path": folded_paths[folded],
                }
            )
            continue
        folded_paths[folded] = path
        if not isinstance(raw_record, Mapping) or not isinstance(raw_record.get("summary"), Mapping):
            violations.append({"kind": "invalid_coverage_record", "path": path})
            continue
        summary = raw_record["summary"]
        try:
            statement = CoverageCounts(
                covered=int(summary["covered_lines"]),
                total=int(summary["num_statements"]),
            )
            branch = CoverageCounts(
                covered=int(summary["covered_branches"]),
                total=int(summary["num_branches"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            violations.append(
                {"kind": "invalid_coverage_counts", "path": path, "detail": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if (
            statement.covered < 0
            or statement.total < 0
            or statement.covered > statement.total
            or branch.covered < 0
            or branch.total < 0
            or branch.covered > branch.total
        ):
            violations.append({"kind": "invalid_coverage_counts", "path": path})
            continue
        files[path] = FileCoverage(statement=statement, branch=branch)
    return files, violations


def _aggregate(paths: Sequence[str], coverage: Mapping[str, FileCoverage]) -> FileCoverage:
    statement = CoverageCounts()
    branch = CoverageCounts()
    for path in paths:
        record = coverage.get(path)
        if record is None:
            continue
        statement = statement.add(record.statement)
        branch = branch.add(record.branch)
    return FileCoverage(statement=statement, branch=branch)


def _metrics(record: FileCoverage) -> dict[str, dict[str, int | float]]:
    return {metric: record.metric(metric).as_dict() for metric in METRIC_NAMES}


def _floor_violations(
    *,
    scope: str,
    record: FileCoverage,
    floors: Mapping[str, Any],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for metric in METRIC_NAMES:
        actual = record.metric(metric).percent
        floor = float(floors[metric])
        if actual + 1e-12 < floor:
            violations.append(
                {
                    "kind": "coverage_floor_not_met",
                    "scope": scope,
                    "metric": metric,
                    "actual_percent": round(actual, 6),
                    "floor_percent": floor,
                }
            )
    return violations


def audit_grouped_coverage(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    coverage_payload: Mapping[str, Any],
    tracked_paths: Sequence[str] | None = None,
    enforce_floor_ratchets: bool = True,
) -> dict[str, Any]:
    manifest_errors = validate_manifest(
        manifest,
        enforce_floor_ratchets=enforce_floor_ratchets,
    )
    if manifest_errors:
        return {
            "ok": False,
            "schema_version": 1,
            "violations": [
                {"kind": "invalid_manifest", "detail": detail} for detail in manifest_errors
            ],
        }

    inventory = manifest["inventory"]
    roots = [_normalized_relative_path(value) for value in inventory["product_roots"]]
    try:
        product_paths = (
            collect_tracked_product_files(root=root, product_roots=roots)
            if tracked_paths is None
            else _sorted_strings(_normalized_relative_path(path) for path in tracked_paths)
        )
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        return {
            "ok": False,
            "schema_version": 1,
            "violations": [
                {"kind": "inventory_collection_failed", "detail": f"{type(exc).__name__}: {exc}"}
            ],
        }

    grouped, exclusions, violations = _classification(manifest, product_paths)
    coverage, coverage_violations = _coverage_files(coverage_payload, root=root)
    violations.extend(coverage_violations)
    tracked_by_folded = {path.casefold(): path for path in product_paths}
    coverage_by_folded = {path.casefold(): path for path in coverage}
    excluded_paths = {str(item["path"]).casefold() for item in exclusions}
    assigned_paths = [path for paths in grouped.values() for path in paths]
    for path in assigned_paths:
        if path.casefold() not in coverage_by_folded:
            violations.append({"kind": "grouped_file_missing_from_coverage", "path": path})
    root_names = {root_name.casefold() for root_name in roots}
    for folded, path in coverage_by_folded.items():
        first = path.split("/", 1)[0].casefold()
        if first in root_names and folded not in tracked_by_folded:
            violations.append({"kind": "coverage_product_file_not_tracked", "path": path})
        if folded in excluded_paths:
            violations.append({"kind": "excluded_file_present_in_coverage", "path": path})

    normalized_coverage = {
        tracked_by_folded.get(path.casefold(), path): record for path, record in coverage.items()
    }
    group_policies = {str(group["name"]): group for group in manifest["groups"]}
    group_results: list[dict[str, Any]] = []
    overall_paths: list[str] = []
    for name in _sorted_strings(grouped):
        paths = grouped[name]
        overall_paths.extend(paths)
        aggregate = _aggregate(paths, normalized_coverage)
        policy = group_policies[name]
        violations.extend(_floor_violations(scope=name, record=aggregate, floors=policy["floors"]))
        group_results.append(
            {
                "name": name,
                "file_count": len(paths),
                "files": paths,
                "metrics": _metrics(aggregate),
                "floors": {metric: float(policy["floors"][metric]) for metric in METRIC_NAMES},
                "next_targets": {
                    metric: float(policy["next_targets"][metric]) for metric in METRIC_NAMES
                },
            }
        )

    overall_record = _aggregate(_sorted_strings(overall_paths), normalized_coverage)
    overall_policy = manifest["overall"]
    violations.extend(
        _floor_violations(scope="overall", record=overall_record, floors=overall_policy["floors"])
    )
    violations.sort(
        key=lambda item: (
            str(item.get("kind") or "").casefold(),
            str(item.get("scope") or "").casefold(),
            str(item.get("path") or item.get("pattern") or "").casefold(),
            str(item.get("metric") or "").casefold(),
        )
    )
    return {
        "ok": not violations,
        "schema_version": 1,
        "inventory": {
            "tracked_product_file_count": len(product_paths),
            "grouped_file_count": len(overall_paths),
            "excluded_file_count": len(exclusions),
            "exclusions": exclusions,
        },
        "overall": {
            "file_count": len(overall_paths),
            "metrics": _metrics(overall_record),
            "floors": {metric: float(overall_policy["floors"][metric]) for metric in METRIC_NAMES},
            "next_targets": {
                metric: float(overall_policy["next_targets"][metric]) for metric in METRIC_NAMES
            },
        },
        "groups": group_results,
        "violations": violations,
    }


def _print_scope(scope: Mapping[str, Any]) -> None:
    metrics = scope["metrics"]
    print(
        "[OK] grouped coverage: "
        f"group={scope['name']} files={scope['file_count']} "
        f"statement={metrics['statement']['covered']}/{metrics['statement']['total']} "
        f"({metrics['statement']['percent']:.2f}%) "
        f"branch={metrics['branch']['covered']}/{metrics['branch']['total']} "
        f"({metrics['branch']['percent']:.2f}%) "
        f"combined={metrics['combined']['covered']}/{metrics['combined']['total']} "
        f"({metrics['combined']['percent']:.2f}%)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit manifest-grouped statement and branch coverage for tracked and "
            "non-ignored untracked V1 product source."
        )
    )
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_json_object(args.manifest, label="coverage grouping manifest")
        coverage_payload = load_json_object(args.coverage_json, label="coverage report")
        payload = audit_grouped_coverage(root=ROOT, manifest=manifest, coverage_payload=coverage_payload)
    except ValueError as exc:
        payload = {
            "ok": False,
            "schema_version": 1,
            "violations": [{"kind": "invalid_input", "detail": str(exc)}],
        }

    if args.json_out is not None:
        output = args.json_out if args.json_out.is_absolute() else ROOT / args.json_out
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if payload["ok"]:
        if not args.quiet:
            _print_scope({"name": "overall", **payload["overall"]})
            for group in payload["groups"]:
                _print_scope(group)
        return 0
    for violation in payload["violations"]:
        target = violation.get("path") or violation.get("scope") or violation.get("pattern") or "manifest"
        print(f"[FAIL] {violation['kind']}: {target}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
