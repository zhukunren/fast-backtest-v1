from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


STRATEGY_IMPLEMENTATION_EXEMPTION_PATTERNS = (
    "strategy_library/{builtin,custom,generated,validated}/**",
    "web/strategy_library/**/strategy.py",
)
STRATEGY_IMPLEMENTATION_EXEMPTION_REASON = (
    "Concrete strategy implementations are not constrained by file-size or cyclomatic-complexity budgets."
)
_STRATEGY_FAMILIES = frozenset({"builtin", "custom", "generated", "validated"})

CODEX_STRATEGY_PRODUCTION_EXEMPTION_PATTERNS = (
    "app/strategy_validation/**",
    "app/strategy_validation_service.py",
    "strategies/authoring/**",
    "strategies/templates/**",
    "strategies/advanced.py",
    "strategies/api.py",
    "strategies/descriptor.py",
    "strategies/protocol.py",
    "strategies/spec.py",
    "strategies/static_check.py",
    "tools/strategy_authoring/**",
    "tools/agent_strategy_backtest.py",
    "tools/benchmark_panel_fastpath.py",
    "tools/codex_campaign.py",
    "tools/codex_strategy.py",
    "tools/llm_authoring_gate.py",
    "tools/multi_strategy_backtest.py",
    "tools/strategy_backtest_smoke.py",
    "tools/strategy_static_check.py",
    "tools/validate_strategy.py",
    "tools/validate_strategy_spec.py",
    "tests/test_agent_strategy_backtest*.py",
    "tests/test_codex_strategy*.py",
    "tests/test_strategy_authoring*.py",
    "tests/test_strategy_backtest_smoke_tool.py",
    "tests/test_strategy_static_check*.py",
    "tests/test_strategy_synthetic_semantics.py",
    "tests/test_strategy_template*.py",
    "tests/test_strategy_validation_service.py",
)
CODEX_STRATEGY_PRODUCTION_EXEMPTION_REASON = (
    "Codex strategy production surfaces prioritize expressive templates, composable capabilities, "
    "and thorough authoring and promotion checks over file-size and cyclomatic-complexity budgets. "
    "Source safety, coverage, and performance remain hard gates; causal, schema, signal, and runtime "
    "contracts are diagnosed by the framework and enforced for promotion."
)
_CODEX_STRATEGY_PRODUCTION_DIRS = (
    ("app", "strategy_validation"),
    ("strategies", "authoring"),
    ("strategies", "templates"),
    ("tools", "strategy_authoring"),
)
_CODEX_STRATEGY_PRODUCTION_FILES = frozenset(
    pattern.casefold()
    for pattern in CODEX_STRATEGY_PRODUCTION_EXEMPTION_PATTERNS
    if "*" not in pattern
)
_CODEX_STRATEGY_PRODUCTION_TEST_PREFIXES = (
    "test_agent_strategy_backtest",
    "test_codex_strategy",
    "test_strategy_authoring",
    "test_strategy_static_check",
    "test_strategy_template",
)


def normalize_repo_path(path: str | Path) -> str:
    raw = str(path).replace("\\", "/")
    return PurePosixPath(raw).as_posix().removeprefix("./")


def is_strategy_implementation_path(path: str | Path) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(normalize_repo_path(path)).parts)
    if len(parts) >= 3 and parts[0] == "strategy_library" and parts[1] in _STRATEGY_FAMILIES:
        return parts[-1].endswith(".py")
    return (
        len(parts) >= 4
        and parts[0] == "web"
        and parts[1] == "strategy_library"
        and parts[-1] == "strategy.py"
    )


def is_codex_strategy_production_path(path: str | Path) -> bool:
    normalized = normalize_repo_path(path)
    if is_strategy_implementation_path(normalized):
        return True
    lowered = normalized.casefold()
    parts = PurePosixPath(lowered).parts
    if not parts or not parts[-1].endswith(".py"):
        return False
    if lowered in _CODEX_STRATEGY_PRODUCTION_FILES:
        return True
    if any(parts[: len(prefix)] == prefix for prefix in _CODEX_STRATEGY_PRODUCTION_DIRS):
        return True
    return (
        len(parts) == 2
        and parts[0] == "tests"
        and any(parts[1].startswith(prefix) for prefix in _CODEX_STRATEGY_PRODUCTION_TEST_PREFIXES)
    )


def is_gated_python_path(path: str | Path, *, active_roots: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path)
    parts = PurePosixPath(normalized).parts
    if not parts or parts[0].casefold() not in {root.casefold() for root in active_roots}:
        return False
    lowered = tuple(part.casefold() for part in parts)
    if "__pycache__" in lowered or "node_modules" in lowered or not normalized.casefold().endswith(".py"):
        return False
    return not is_codex_strategy_production_path(normalized)


def collect_gated_python_files(root: Path, *, active_roots: Iterable[str]) -> list[Path]:
    active_roots = tuple(active_roots)
    try:
        raw_paths: set[str] = set()
        for command in (
            ["git", "ls-files", "*.py"],
            ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        ):
            completed = subprocess.run(
                command,
                cwd=str(root),
                capture_output=True,
                text=True,
                check=True,
            )
            raw_paths.update(line.strip() for line in completed.stdout.splitlines() if line.strip())
        paths = [root / raw_path for raw_path in raw_paths]
    except (OSError, subprocess.SubprocessError):
        paths = list(root.rglob("*.py"))

    root_resolved = root.resolve()
    collected: list[tuple[str, Path]] = []
    for path in paths:
        try:
            if not path.is_file():
                continue
            relative = path.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if is_gated_python_path(relative, active_roots=active_roots):
            collected.append((relative.as_posix().casefold(), path))
    return [path for _, path in sorted(collected, key=lambda item: item[0])]


def scope_policy() -> dict[str, object]:
    return {
        "strategy_implementation_exemptions": list(STRATEGY_IMPLEMENTATION_EXEMPTION_PATTERNS),
        "codex_strategy_production_exemptions": list(CODEX_STRATEGY_PRODUCTION_EXEMPTION_PATTERNS),
        "reason": STRATEGY_IMPLEMENTATION_EXEMPTION_REASON,
        "strategy_implementation_reason": STRATEGY_IMPLEMENTATION_EXEMPTION_REASON,
        "codex_strategy_production_reason": CODEX_STRATEGY_PRODUCTION_EXEMPTION_REASON,
    }
