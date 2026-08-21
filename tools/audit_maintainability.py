from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from maintainability_scope import collect_gated_python_files, scope_policy
except ModuleNotFoundError:
    from tools.maintainability_scope import collect_gated_python_files, scope_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LINE_LIMIT = 1200
DEFAULT_LARGE_FILE_THRESHOLD = 500
# Exact baseline for the unified full V1 workspace (2026-08-20).  The scope is
# fixed by ``maintainability_scope``; subsequent runs may only reduce these
# values.  Codex strategy production paths remain outside this ratchet.
LARGE_FILE_COUNT_BUDGET = 48
LARGE_FILE_LINES_BUDGET = 36_603
ACTIVE_SOURCE_ROOTS = {
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
}

OVERSIZE_FILE_BUDGETS: dict[str, int] = {}


@dataclass(frozen=True, slots=True)
class FileSizeRecord:
    path: str
    lines: int


def collect_python_files(root: Path = ROOT) -> list[Path]:
    return collect_gated_python_files(root, active_roots=ACTIVE_SOURCE_ROOTS)


def measure_files(paths: Iterable[Path], *, root: Path = ROOT) -> list[FileSizeRecord]:
    records: list[FileSizeRecord] = []
    for path in paths:
        try:
            line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        records.append(FileSizeRecord(path=_relative(root, path), lines=line_count))
    return sorted(records, key=lambda item: (-item.lines, item.path.casefold()))


def evaluate_file_sizes(
    records: Iterable[FileSizeRecord],
    *,
    line_limit: int = DEFAULT_LINE_LIMIT,
    budgets: Mapping[str, int] | None = None,
    large_file_threshold: int | None = None,
    large_file_count_budget: int | None = None,
    large_file_lines_budget: int | None = None,
) -> dict[str, Any]:
    budgets = dict(OVERSIZE_FILE_BUDGETS if budgets is None else budgets)
    records = list(records)
    oversize = [record for record in records if record.lines > line_limit]
    records_by_path = {record.path: record for record in records}
    violations: list[dict[str, Any]] = []
    for record in oversize:
        budget = budgets.get(record.path)
        if budget is None:
            violations.append(
                {
                    "kind": "unbudgeted_oversize_file",
                    "path": record.path,
                    "lines": record.lines,
                    "line_limit": line_limit,
                }
            )
        elif record.lines > budget:
            violations.append(
                {
                    "kind": "oversize_file_budget_exceeded",
                    "path": record.path,
                    "lines": record.lines,
                    "budget": budget,
                }
            )

    missing_budget_paths = sorted(path for path in budgets if path not in records_by_path)
    for path in missing_budget_paths:
        violations.append({"kind": "missing_budget_file", "path": path, "budget": budgets[path]})
    stale_budget_paths = sorted(
        path for path in budgets if path in records_by_path and records_by_path[path].lines <= line_limit
    )
    for path in stale_budget_paths:
        violations.append(
            {
                "kind": "stale_oversize_budget",
                "path": path,
                "lines": records_by_path[path].lines,
                "line_limit": line_limit,
                "budget": budgets[path],
            }
        )

    large_files = (
        [record for record in records if record.lines > int(large_file_threshold)]
        if large_file_threshold is not None
        else []
    )
    large_file_lines = sum(record.lines for record in large_files)
    for metric, actual, budget in (
        ("count", len(large_files), large_file_count_budget),
        ("lines", large_file_lines, large_file_lines_budget),
    ):
        if budget is None:
            continue
        if actual > int(budget):
            violations.append(
                {
                    "kind": f"large_file_{metric}_budget_exceeded",
                    "actual": actual,
                    "budget": int(budget),
                    "large_file_threshold": large_file_threshold,
                }
            )
        elif actual < int(budget):
            violations.append(
                {
                    "kind": f"stale_large_file_{metric}_budget",
                    "actual": actual,
                    "budget": int(budget),
                    "large_file_threshold": large_file_threshold,
                }
            )
    return {
        "ok": not violations,
        "scope_policy": scope_policy(),
        "line_limit": int(line_limit),
        "oversize": [{"path": record.path, "lines": record.lines} for record in oversize],
        "budgets": budgets,
        "missing_budget_paths": missing_budget_paths,
        "stale_budget_paths": stale_budget_paths,
        "large_file_threshold": large_file_threshold,
        "large_files": [{"path": record.path, "lines": record.lines} for record in large_files],
        "large_file_count": len(large_files),
        "large_file_lines": large_file_lines,
        "large_file_count_budget": large_file_count_budget,
        "large_file_lines_budget": large_file_lines_budget,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit active Python files for maintainability size budgets.")
    parser.add_argument("--line-limit", type=int, default=DEFAULT_LINE_LIMIT)
    parser.add_argument("--large-file-threshold", type=int, default=DEFAULT_LARGE_FILE_THRESHOLD)
    parser.add_argument("--large-file-count-budget", type=int, default=LARGE_FILE_COUNT_BUDGET)
    parser.add_argument("--large-file-lines-budget", type=int, default=LARGE_FILE_LINES_BUDGET)
    parser.add_argument("--json-out", default="", help="Optional JSON report output path.")
    parser.add_argument("--quiet", action="store_true", help="Only print failures.")
    args = parser.parse_args(argv)

    records = measure_files(collect_python_files(ROOT), root=ROOT)
    payload = evaluate_file_sizes(
        records,
        line_limit=args.line_limit,
        large_file_threshold=args.large_file_threshold,
        large_file_count_budget=args.large_file_count_budget,
        large_file_lines_budget=args.large_file_lines_budget,
    )
    if args.json_out:
        out = Path(args.json_out)
        out = out if out.is_absolute() else ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    if payload["violations"]:
        for violation in payload["violations"]:
            target = violation.get("path") or "aggregate"
            actual = violation.get("lines", violation.get("actual", ""))
            print(f"[FAIL] {violation['kind']}: {target} actual={actual}")
        return 1
    if not args.quiet:
        print(
            f"[OK] maintainability audit: oversize={len(payload['oversize'])} "
            f"limit={args.line_limit} large={payload['large_file_count']} "
            f"large_lines={payload['large_file_lines']} threshold={args.large_file_threshold}"
        )
    return 0


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
