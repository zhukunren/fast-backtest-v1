"""Compare structured performance reports against a small JSON budget."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


DEFAULT_BUDGET: dict[str, Any] = {
    "max_elapsed_sec": None,
    "max_regression_pct": 20.0,
    "stages": {},
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_float(value: object) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _comparison_elapsed(payload: Mapping[str, Any]) -> float | None:
    for key in ("comparison_elapsed_sec", "elapsed_sec"):
        value = _to_float(payload.get(key))
        if value is not None:
            return value
    profile = payload.get("profile")
    if isinstance(profile, Mapping):
        return _to_float(profile.get("elapsed_sec"))
    return None


def _stage_timings(payload: Mapping[str, Any]) -> dict[str, float]:
    profile = payload.get("profile")
    candidates: list[object] = []
    if isinstance(profile, Mapping):
        candidates.append(profile.get("stage_timings_sec"))
    candidates.append(payload.get("stage_timings_sec"))
    for raw in candidates:
        if isinstance(raw, Mapping):
            out: dict[str, float] = {}
            for key, value in raw.items():
                number = _to_float(value)
                if number is not None:
                    out[str(key)] = number
            return out
    return {}


def evaluate_performance_budget(
    *,
    report: Mapping[str, Any],
    budget: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    budget = dict(DEFAULT_BUDGET | dict(budget or {}))
    elapsed = _comparison_elapsed(report)
    baseline_elapsed = _comparison_elapsed(baseline or {}) if baseline else None
    max_elapsed = _to_float(budget.get("max_elapsed_sec"))
    max_regression_pct = _to_float(budget.get("max_regression_pct"))
    violations: list[dict[str, Any]] = []

    if elapsed is None:
        violations.append({"kind": "missing_elapsed", "message": "report does not contain elapsed seconds"})
    elif max_elapsed is not None and elapsed > max_elapsed:
        violations.append(
            {
                "kind": "elapsed_budget_exceeded",
                "elapsed_sec": elapsed,
                "max_elapsed_sec": max_elapsed,
            }
        )

    if elapsed is not None and baseline_elapsed is not None and baseline_elapsed > 0 and max_regression_pct is not None:
        regression_pct = (elapsed / baseline_elapsed - 1.0) * 100.0
        if regression_pct > max_regression_pct:
            violations.append(
                {
                    "kind": "elapsed_regression_exceeded",
                    "elapsed_sec": elapsed,
                    "baseline_elapsed_sec": baseline_elapsed,
                    "regression_pct": regression_pct,
                    "max_regression_pct": max_regression_pct,
                }
            )

    stages = _stage_timings(report)
    stage_budget = budget.get("stages")
    if isinstance(stage_budget, Mapping):
        for name, raw_max in sorted(stage_budget.items(), key=lambda item: str(item[0]).casefold()):
            max_stage = _to_float(raw_max)
            if max_stage is None:
                continue
            observed = stages.get(str(name))
            if observed is None:
                violations.append({"kind": "missing_stage", "stage": str(name), "max_elapsed_sec": max_stage})
            elif observed > max_stage:
                violations.append(
                    {
                        "kind": "stage_budget_exceeded",
                        "stage": str(name),
                        "elapsed_sec": observed,
                        "max_elapsed_sec": max_stage,
                    }
                )

    return {
        "ok": not violations,
        "elapsed_sec": elapsed,
        "baseline_elapsed_sec": baseline_elapsed,
        "stage_timings_sec": stages,
        "budget": budget,
        "violations": violations,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare a perf JSON report against elapsed/stage budgets.")
    parser.add_argument("--report", required=True, help="Performance report JSON path.")
    parser.add_argument("--budget", default="", help="Optional budget JSON path.")
    parser.add_argument("--baseline", default="", help="Optional baseline JSON path.")
    parser.add_argument("--json-out", default="", help="Optional output JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = _load_json(Path(args.report))
    budget = _load_json(Path(args.budget)) if str(args.budget or "").strip() else None
    baseline = _load_json(Path(args.baseline)) if str(args.baseline or "").strip() else None
    payload = evaluate_performance_budget(report=report, budget=budget, baseline=baseline)
    if str(args.json_out or "").strip():
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "violations": payload["violations"]}, ensure_ascii=True, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
