from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from maintainability_scope import collect_gated_python_files, scope_policy
except ModuleNotFoundError:
    from tools.maintainability_scope import collect_gated_python_files, scope_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THRESHOLD = 30
DEFAULT_ADVISORY_THRESHOLD = 15
# Exact baseline for the unified full V1 workspace (2026-08-20).  These are
# ratchets, not exemptions: later changes must keep all three values at or
# below this baseline, while hard C901 > 30 remains a failure.
ADVISORY_HOTSPOT_COUNT_BUDGET = 40
ADVISORY_EXCESS_BUDGET = 139
ADVISORY_MAX_COMPLEXITY_BUDGET = 26
ACTIVE_SOURCE_PATHS = (
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

# Keep this empty while the active source tree has no threshold exceptions.
COMPLEXITY_BUDGETS: dict[str, int] = {}

_MESSAGE_RE = re.compile(r"^`(?P<symbol>.+)` is too complex \((?P<complexity>\d+) > \d+\)$")


@dataclass(frozen=True, slots=True)
class ComplexityRecord:
    path: str
    symbol: str
    complexity: int
    line: int

    @property
    def key(self) -> str:
        return f"{self.path}::{self.symbol}"


def parse_ruff_diagnostics(payload: Iterable[Mapping[str, Any]], *, root: Path = ROOT) -> list[ComplexityRecord]:
    records: list[ComplexityRecord] = []
    root_resolved = root.resolve()
    for item in payload:
        if str(item.get("code") or "") != "C901":
            continue
        match = _MESSAGE_RE.match(str(item.get("message") or ""))
        if match is None:
            raise ValueError(f"unrecognized Ruff C901 message: {item.get('message')!r}")
        raw_path = Path(str(item.get("filename") or ""))
        resolved_path = raw_path.resolve() if raw_path.is_absolute() else (root_resolved / raw_path).resolve()
        relative_path = resolved_path.relative_to(root_resolved).as_posix()
        location = item.get("location")
        line = int(location.get("row") or 0) if isinstance(location, Mapping) else 0
        records.append(
            ComplexityRecord(
                path=relative_path,
                symbol=match.group("symbol"),
                complexity=int(match.group("complexity")),
                line=line,
            )
        )
    return sorted(records, key=lambda item: (item.path.casefold(), item.line, item.symbol.casefold()))


def evaluate_complexity(
    records: Iterable[ComplexityRecord],
    *,
    threshold: int = DEFAULT_THRESHOLD,
    budgets: Mapping[str, int] | None = None,
    advisory_threshold: int | None = None,
    advisory_count_budget: int | None = None,
    advisory_excess_budget: int | None = None,
    advisory_max_budget: int | None = None,
) -> dict[str, Any]:
    effective_budgets = dict(COMPLEXITY_BUDGETS if budgets is None else budgets)
    records = list(records)
    hotspots = sorted(
        (record for record in records if record.complexity > threshold),
        key=lambda item: (-item.complexity, item.path.casefold(), item.symbol.casefold()),
    )
    violations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in hotspots:
        seen.add(record.key)
        budget = effective_budgets.get(record.key)
        if budget is None:
            violations.append({"kind": "unbudgeted_complexity", **asdict(record), "threshold": threshold})
        elif record.complexity > int(budget):
            violations.append(
                {"kind": "complexity_budget_exceeded", **asdict(record), "budget": int(budget)}
            )

    stale_budgets = sorted(set(effective_budgets) - seen, key=str.casefold)
    for key in stale_budgets:
        violations.append(
            {
                "kind": "stale_complexity_budget",
                "key": key,
                "budget": int(effective_budgets[key]),
                "threshold": threshold,
            }
        )

    advisory_hotspots = sorted(
        (record for record in records if advisory_threshold is not None and record.complexity > advisory_threshold),
        key=lambda item: (-item.complexity, item.path.casefold(), item.symbol.casefold()),
    )
    advisory_excess = sum(record.complexity - int(advisory_threshold) for record in advisory_hotspots)
    advisory_max = max((record.complexity for record in advisory_hotspots), default=0)
    for metric, actual, budget in (
        ("count", len(advisory_hotspots), advisory_count_budget),
        ("excess", advisory_excess, advisory_excess_budget),
        ("max", advisory_max, advisory_max_budget),
    ):
        if budget is None:
            continue
        if actual > int(budget):
            violations.append(
                {
                    "kind": f"advisory_complexity_{metric}_budget_exceeded",
                    "actual": actual,
                    "budget": int(budget),
                    "advisory_threshold": advisory_threshold,
                }
            )
        elif actual < int(budget):
            violations.append(
                {
                    "kind": f"stale_advisory_complexity_{metric}_budget",
                    "actual": actual,
                    "budget": int(budget),
                    "advisory_threshold": advisory_threshold,
                }
            )
    return {
        "ok": not violations,
        "scope_policy": scope_policy(),
        "threshold": int(threshold),
        "hotspots": [asdict(record) for record in hotspots],
        "budgets": effective_budgets,
        "stale_budgets": stale_budgets,
        "advisory_threshold": advisory_threshold,
        "advisory_hotspots": [asdict(record) for record in advisory_hotspots],
        "advisory_count": len(advisory_hotspots),
        "advisory_excess": advisory_excess,
        "advisory_max": advisory_max,
        "advisory_count_budget": advisory_count_budget,
        "advisory_excess_budget": advisory_excess_budget,
        "advisory_max_budget": advisory_max_budget,
        "violations": violations,
    }


def collect_complexity(*, root: Path = ROOT, threshold: int = DEFAULT_THRESHOLD) -> list[ComplexityRecord]:
    paths = collect_gated_python_files(root, active_roots=ACTIVE_SOURCE_PATHS)
    if not paths:
        return []
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *(path.resolve().relative_to(root.resolve()).as_posix() for path in paths),
        "--select",
        "C901",
        "--config",
        f"lint.mccabe.max-complexity={int(threshold)}",
        "--output-format",
        "json",
    ]
    completed = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(completed.stderr.strip() or f"Ruff complexity scan failed: rc={completed.returncode}")
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise RuntimeError("Ruff complexity output must be a JSON list")
    return parse_ruff_diagnostics(payload, root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit cyclomatic-complexity debt ceilings for active Python source.")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--advisory-threshold", type=int, default=DEFAULT_ADVISORY_THRESHOLD)
    parser.add_argument("--advisory-count-budget", type=int, default=ADVISORY_HOTSPOT_COUNT_BUDGET)
    parser.add_argument("--advisory-excess-budget", type=int, default=ADVISORY_EXCESS_BUDGET)
    parser.add_argument("--advisory-max-budget", type=int, default=ADVISORY_MAX_COMPLEXITY_BUDGET)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    payload = evaluate_complexity(
        collect_complexity(root=ROOT, threshold=min(args.threshold, args.advisory_threshold)),
        threshold=args.threshold,
        advisory_threshold=args.advisory_threshold,
        advisory_count_budget=args.advisory_count_budget,
        advisory_excess_budget=args.advisory_excess_budget,
        advisory_max_budget=args.advisory_max_budget,
    )
    if args.json_out:
        out = Path(args.json_out)
        out = out if out.is_absolute() else ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    if payload["violations"]:
        for violation in payload["violations"]:
            target = violation.get("key") or violation.get("path") or "aggregate"
            print(f"[FAIL] {violation['kind']}: {target} actual={violation.get('actual', '')}")
        return 1
    if not args.quiet:
        print(
            f"[OK] complexity audit: hard_hotspots={len(payload['hotspots'])} "
            f"hard_threshold={payload['threshold']} advisory_hotspots={payload['advisory_count']} "
            f"advisory_excess={payload['advisory_excess']} "
            f"advisory_threshold={payload['advisory_threshold']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
