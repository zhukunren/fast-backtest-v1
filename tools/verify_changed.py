"""Plan and optionally run validation for the current change surface.

The path-to-gate mapping lives in ``config/change-impact.json``.  This tool is
deliberately conservative: an unmapped path requires the local full gate and
always reports the CI full gate.  Planning is side-effect free; ``--execute``
is explicit because several gates are intentionally expensive.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "change-impact.json"

FULL_CI_GATE = "full_ci"
GATE_ORDER = (
    "ruff",
    "changed_tests",
    "docs_contract",
    "config_contract",
    "offline_smoke",
    "web_tests",
    "frontend_unit",
    "authoring",
    "perf",
    "hotpath",
    "topn",
    "strategy_backtest",
    "governance_tests",
    "full_local",
)


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    label: str
    patterns: tuple[str, ...]
    local_gates: tuple[str, ...]
    notes: str
    authority_docs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    changed_paths: tuple[str, ...]
    matched_rules: dict[str, tuple[str, ...]]
    local_gates: tuple[str, ...]
    ci_gates: tuple[str, ...]
    unknown_paths: tuple[str, ...]
    authority_docs: tuple[str, ...]
    guidance: tuple[str, ...]

    @property
    def requires_full_ci(self) -> bool:
        return bool(self.changed_paths)

    @property
    def requires_local_full_gate(self) -> bool:
        return "full_local" in self.local_gates


def normalize_repo_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _sorted_unique(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({normalize_repo_path(path) for path in paths if normalize_repo_path(path)}, key=str.casefold))


def load_manifest(path: Path = MANIFEST_PATH) -> tuple[tuple[Rule, ...], str, dict[str, tuple[str, ...]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("change-impact manifest schema_version must be 1")
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("change-impact manifest must contain non-empty rules")

    raw_authority_docs = payload.get("authority_docs", {})
    if not isinstance(raw_authority_docs, dict):
        raise ValueError("change-impact authority_docs must be an object")
    authority_docs: dict[str, tuple[str, ...]] = {}
    for rule_id, docs in raw_authority_docs.items():
        if not isinstance(docs, list):
            raise ValueError(f"authority_docs entry {rule_id!r} must be a list")
        authority_docs[str(rule_id)] = _sorted_unique(str(item) for item in docs if str(item).strip())

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError("change-impact rule must be an object")
        rule_id = str(raw.get("id") or "").strip()
        if not rule_id or rule_id in seen_ids:
            raise ValueError(f"duplicate or empty change-impact rule id: {rule_id!r}")
        patterns = tuple(str(item).strip() for item in raw.get("patterns", ()) if str(item).strip())
        local_gates = tuple(str(item).strip() for item in raw.get("local_gates", ()) if str(item).strip())
        if not patterns or not local_gates:
            raise ValueError(f"change-impact rule {rule_id!r} needs patterns and local_gates")
        unknown_gates = set(local_gates) - set(GATE_ORDER)
        if unknown_gates:
            raise ValueError(f"change-impact rule {rule_id!r} has unknown gates: {sorted(unknown_gates)}")
        seen_ids.add(rule_id)
        rules.append(
            Rule(
                rule_id=rule_id,
                label=str(raw.get("label") or rule_id),
                patterns=patterns,
                local_gates=local_gates,
                notes=str(raw.get("notes") or ""),
                authority_docs=authority_docs.get(rule_id, ()),
            )
        )

    default_rule = str(payload.get("default_rule") or "").strip()
    if default_rule != "unknown":
        raise ValueError("change-impact manifest default_rule must be 'unknown'")
    rule_ids = {rule.rule_id for rule in rules}
    unknown_authority_ids = set(authority_docs) - rule_ids - {default_rule}
    if unknown_authority_ids:
        raise ValueError(f"authority_docs has unknown rule ids: {sorted(unknown_authority_ids)}")
    missing_authority_ids = rule_ids - set(authority_docs)
    if missing_authority_ids:
        raise ValueError(f"authority_docs is missing rule ids: {sorted(missing_authority_ids)}")
    return tuple(rules), default_rule, authority_docs


def _matches(path: str, pattern: str) -> bool:
    normalized_path = normalize_repo_path(path)
    normalized_pattern = normalize_repo_path(pattern)
    if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
        return True
    # ``fnmatch`` requires an explicit slash for ``foo/**/bar`` to match a
    # direct child on some platforms; support the intuitive recursive form.
    if "/**/" in normalized_pattern:
        prefix, suffix = normalized_pattern.split("/**/", 1)
        if fnmatch.fnmatchcase(normalized_path, f"{prefix}/{suffix}"):
            return True
    return False


def build_plan(paths: Iterable[str], *, manifest_path: Path = MANIFEST_PATH) -> ValidationPlan:
    rules, default_rule, authority_docs = load_manifest(manifest_path)
    changed_paths = _sorted_unique(paths)
    matched_rules: dict[str, tuple[str, ...]] = {}
    unknown_paths: list[str] = []
    gate_set: set[str] = set()
    docs_set: set[str] = set()
    guidance_set: set[str] = set()

    for path in changed_paths:
        matches = tuple(rule for rule in rules if any(_matches(path, pattern) for pattern in rule.patterns))
        if not matches:
            unknown_paths.append(path)
            matched_rules[path] = (default_rule,)
            gate_set.add("full_local")
            docs_set.update(authority_docs.get(default_rule, ()))
            guidance_set.add(f"{path}: no manifest rule matched; local full gate is required")
            continue
        matched_rules[path] = tuple(rule.rule_id for rule in matches)
        for rule in matches:
            gate_set.update(rule.local_gates)
            docs_set.update(rule.authority_docs)
            if rule.notes:
                guidance_set.add(f"{path}: {rule.label}: {rule.notes}")

    if not _python_paths(changed_paths, existing_only=True):
        gate_set.discard("ruff")
    if not _changed_test_paths(changed_paths):
        gate_set.discard("changed_tests")
    ordered_gates = tuple(gate for gate in GATE_ORDER if gate in gate_set)
    if changed_paths:
        ci_gates = (FULL_CI_GATE,)
    else:
        ci_gates = ()
    return ValidationPlan(
        changed_paths=changed_paths,
        matched_rules=matched_rules,
        local_gates=ordered_gates,
        ci_gates=ci_gates,
        unknown_paths=tuple(unknown_paths),
        authority_docs=_sorted_unique(docs_set),
        guidance=tuple(sorted(guidance_set, key=str.casefold)),
    )


def _git_output(repo_root: Path, args: Sequence[str]) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(line for line in completed.stdout.splitlines() if line.strip())


def _is_known_runtime_artifact(path: str) -> bool:
    parts = tuple(
        part.casefold()
        for part in normalize_repo_path(path).split("/")
        if part
    )
    if any(part.startswith(".pytest_tmp") for part in parts):
        return True
    return "strategy_library" in parts and "web_cache" in parts


def collect_changed_paths(repo_root: Path = ROOT, *, base: str = "", staged: bool = False) -> tuple[str, ...]:
    paths: list[str] = []
    if staged:
        paths.extend(_git_output(repo_root, ["diff", "--cached", "--name-only", "--diff-filter=ACMRD"]))
    else:
        paths.extend(_git_output(repo_root, ["diff", "HEAD", "--name-only", "--diff-filter=ACMRD"]))
        if base:
            paths.extend(_git_output(repo_root, ["diff", f"{base}...HEAD", "--name-only", "--diff-filter=ACMRD"]))
        paths.extend(_git_output(repo_root, ["ls-files", "--others", "--exclude-standard"]))
    return _sorted_unique(path for path in paths if not _is_known_runtime_artifact(path))


def _is_file_or_unreadable(path: str) -> bool:
    try:
        return (ROOT / Path(path)).is_file()
    except OSError:
        # Keep unexpected inaccessible paths in the validation command so it
        # fails visibly instead of making the change surface look smaller.
        return True


def _python_paths(paths: Iterable[str], *, existing_only: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    for path in _sorted_unique(paths):
        if not path.casefold().endswith(".py"):
            continue
        if existing_only and not _is_file_or_unreadable(path):
            continue
        result.append(path)
    return tuple(result)


def _changed_test_paths(paths: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for path in _sorted_unique(paths):
        lowered = path.casefold()
        if (lowered.startswith("tests/") or lowered.startswith("web/tests/")) and lowered.endswith(".py"):
            if _is_file_or_unreadable(path):
                result.append(path)
    return tuple(result)


def _is_generated_strategy(path: str) -> bool:
    lowered = normalize_repo_path(path).casefold()
    return lowered.startswith("strategy_library/generated/") or lowered.startswith("web/strategy_library/")


def gate_description(gate: str, paths: Iterable[str]) -> str:
    path_tuple = tuple(paths)
    python_paths = _python_paths(path_tuple, existing_only=True)
    test_paths = _changed_test_paths(path_tuple)
    descriptions = {
        "ruff": "python -m ruff check <changed Python files>",
        "changed_tests": "python -m pytest <changed test files> -q",
        "docs_contract": "python -m pytest tests/test_documentation_contracts.py -q",
        "config_contract": "python -m pytest tests/test_app_config.py tests/test_config_sections.py tests/test_config_validation.py -q",
        "offline_smoke": "python tests/test_offline_smoke.py",
        "web_tests": "python -m pytest web/tests -q",
        "frontend_unit": "npm --prefix web/frontend run test:unit",
        "authoring": "python tools/llm_authoring_gate.py",
        "perf": "python tools/perf_smoke.py --quick",
        "hotpath": "python tools/hotpath_smoke.py --quick",
        "topn": "python tools/check_topn_slice.py --date 2025-05-16 --top 10",
        "strategy_backtest": "python tools/codex_strategy.py backtest ... (use the current target window)",
        "governance_tests": "python -m pytest tests/test_verify_changed.py tests/test_tool_entrypoints.py tests/test_full_quality_gate_runtime.py -q",
        "full_local": "python tools/full_quality_gate.py --profile quick",
    }
    if gate == "ruff" and python_paths:
        return "python -m ruff check " + " ".join(python_paths)
    if gate == "changed_tests" and test_paths:
        return "python -m pytest " + " ".join(test_paths) + " -q"
    if gate == "ruff" and not python_paths:
        return "(no changed Python files)"
    if gate == "changed_tests" and not test_paths:
        return "(no changed test files)"
    if gate == "strategy_backtest":
        concrete = tuple(path for path in path_tuple if _is_generated_strategy(path) and path.casefold().endswith(".py"))
        if concrete:
            return f"python tools/codex_strategy.py backtest ... (target: {', '.join(concrete)})"
    return descriptions[gate]


def _runnable_gate(gate: str, paths: Iterable[str]) -> tuple[list[str], Path] | None:
    python = sys.executable
    path_tuple = tuple(paths)
    if gate == "ruff":
        python_paths = _python_paths(path_tuple, existing_only=True)
        if not python_paths:
            return None
        return [python, "-m", "ruff", "check", *python_paths], ROOT
    if gate == "changed_tests":
        test_paths = _changed_test_paths(path_tuple)
        if not test_paths:
            return None
        return [python, "-m", "pytest", *test_paths, "-q"], ROOT
    commands: dict[str, tuple[list[str], Path]] = {
        "docs_contract": ([python, "-m", "pytest", "tests/test_documentation_contracts.py", "-q"], ROOT),
        "config_contract": (
            [
                python,
                "-m",
                "pytest",
                "tests/test_app_config.py",
                "tests/test_config_sections.py",
                "tests/test_config_validation.py",
                "-q",
            ],
            ROOT,
        ),
        "offline_smoke": ([python, "tests/test_offline_smoke.py"], ROOT),
        "web_tests": ([python, "-m", "pytest", "web/tests", "-q"], ROOT),
        "frontend_unit": (["npm.cmd" if sys.platform == "win32" else "npm", "run", "test:unit"], ROOT / "web" / "frontend"),
        "authoring": ([python, "tools/llm_authoring_gate.py"], ROOT),
        "perf": ([python, "tools/perf_smoke.py", "--quick"], ROOT),
        "hotpath": ([python, "tools/hotpath_smoke.py", "--quick"], ROOT),
        "topn": ([python, "tools/check_topn_slice.py", "--date", "2025-05-16", "--top", "10"], ROOT),
        "governance_tests": (
            [
                python,
                "-m",
                "pytest",
                "tests/test_verify_changed.py",
                "tests/test_tool_entrypoints.py",
                "tests/test_full_quality_gate_runtime.py",
                "-q",
            ],
            ROOT,
        ),
        "full_local": ([python, "tools/full_quality_gate.py", "--profile", "quick"], ROOT),
    }
    return commands.get(gate)


def execute_plan(plan: ValidationPlan) -> int:
    manual = [gate for gate in plan.local_gates if _runnable_gate(gate, plan.changed_paths) is None]
    for gate in plan.local_gates:
        command = _runnable_gate(gate, plan.changed_paths)
        if command is None:
            continue
        argv, cwd = command
        print(f"[RUN] {' '.join(argv)}")
        completed = subprocess.run(argv, cwd=cwd, check=False)
        if completed.returncode != 0:
            print(f"[FAIL] local gate: {gate}")
            return completed.returncode or 1
    if manual:
        print("[MANUAL] complete these required validations:")
        for gate in manual:
            print(f"  - {gate_description(gate, plan.changed_paths)}")
        return 2
    return 0


def plan_payload(plan: ValidationPlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "changed_paths": list(plan.changed_paths),
        "matched_rules": {path: list(rules) for path, rules in plan.matched_rules.items()},
        "unknown_paths": list(plan.unknown_paths),
        "authority_docs": list(plan.authority_docs),
        "guidance": list(plan.guidance),
        "local_gates": list(plan.local_gates),
        "ci_gates": list(plan.ci_gates),
        "requires_local_full_gate": plan.requires_local_full_gate,
        "requires_full_ci": plan.requires_full_ci,
        "status": "unmapped" if plan.unknown_paths else "mapped",
    }


def print_plan(plan: ValidationPlan) -> None:
    if not plan.changed_paths:
        print("[OK] no changed paths detected")
        return
    print(f"Changed paths ({len(plan.changed_paths)}):")
    for path in plan.changed_paths:
        print(f"  - {path} [{', '.join(plan.matched_rules[path])}]")
    print("\nLocal validation:")
    for gate in plan.local_gates:
        print(f"  - {gate_description(gate, plan.changed_paths)}")
    print("\nRead first:")
    for path in plan.authority_docs:
        print(f"  - {path}")
    if plan.guidance:
        print("\nGuidance:")
        for item in plan.guidance:
            print(f"  - {item}")
    print("\nCI validation:")
    print("  - full CI: .github/workflows/full-ci.yml (push / pull request)")
    if plan.unknown_paths:
        print("\n[FAIL-CLOSED] unmapped paths require the local full gate:")
        for path in plan.unknown_paths:
            print(f"  - {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="根据变更面规划并可执行最小充分验证。")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--paths", nargs="*", help="显式提供仓库相对路径；用于脚本和测试。")
    source.add_argument("--staged", action="store_true", help="只读取 staged 路径。")
    parser.add_argument("--base", default="", help="把 base...HEAD 的已提交差异也纳入规划。")
    parser.add_argument("--execute", action="store_true", help="执行可自动化的本地 gate；默认只输出计划。")
    parser.add_argument("--ruff-only", action="store_true", help="仅对 staged Python 文件运行 Ruff，供 pre-commit 使用。")
    parser.add_argument("--json-out", type=Path, default=None, help="写出结构化验证计划。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.paths is not None:
        paths = _sorted_unique(args.paths)
    else:
        paths = collect_changed_paths(staged=bool(args.staged), base=str(args.base or ""))

    if args.ruff_only:
        python_paths = _python_paths(paths, existing_only=True)
        if not python_paths:
            return 0
        command = [sys.executable, "-m", "ruff", "check", *python_paths]
        return subprocess.run(command, cwd=ROOT, check=False).returncode

    plan = build_plan(paths)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(plan_payload(plan), ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print_plan(plan)
    if args.execute:
        return execute_plan(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
