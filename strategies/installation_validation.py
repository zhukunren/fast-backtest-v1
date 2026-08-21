"""Validation contract for installing a complete strategy package."""
from __future__ import annotations

import tokenize
from pathlib import Path
from typing import Any

from .code_security import validate_strategy_code_security
from .source_policy import find_strategy_dependency_issues
from .static_check import check_strategy_source


class StrategyInstallationValidationError(ValueError):
    """Raised when an uploaded strategy package violates the V1 contract."""


def strategy_dependency_diagnostics(source: str) -> list[str]:
    """Return source-policy diagnostics for health/reporting owners."""
    return find_strategy_dependency_issues(source)


def _read_source(path: Path) -> str:
    try:
        with tokenize.open(path) as handle:
            return handle.read()
    except (OSError, UnicodeError, tokenize.TokenError) as exc:
        raise StrategyInstallationValidationError(f"策略源码读取失败：{exc}") from exc


def _validate_source(path: Path, *, require_strategy_contract: bool) -> dict[str, Any]:
    source = _read_source(path)
    try:
        compile(source, str(path), "exec")
        validate_strategy_code_security(source, source=str(path))
    except (SyntaxError, ValueError) as exc:
        raise StrategyInstallationValidationError(f"策略源码校验失败：{exc}") from exc

    dependency_issues = find_strategy_dependency_issues(source)
    if dependency_issues:
        raise StrategyInstallationValidationError(
            "策略必须自包含，指标与信号计算不得依赖外部策略逻辑："
            + "；".join(dependency_issues[:5])
        )
    if not require_strategy_contract:
        return {"ok": True, "issues": []}

    report = check_strategy_source(source, path=str(path))
    if not report.get("ok"):
        errors = [
            str(issue.get("message") or issue.get("code") or "unknown error")
            for issue in report.get("issues", [])
            if isinstance(issue, dict) and issue.get("severity") == "error"
        ]
        raise StrategyInstallationValidationError("策略合同校验失败：" + "；".join(errors[:5]))
    return report


def validate_strategy_package(directory: Path, source_path: Path) -> dict[str, Any]:
    """Validate every Python file and the package's canonical strategy module."""
    python_files = sorted(
        (path for path in directory.rglob("*.py") if path.is_file()),
        key=lambda path: str(path.relative_to(directory)).casefold(),
    )
    source_resolved = source_path.resolve()
    auxiliary_files = [path for path in python_files if path.resolve() != source_resolved]
    if auxiliary_files:
        names = "、".join(str(path.relative_to(directory)) for path in auxiliary_files[:5])
        raise StrategyInstallationValidationError(
            f"每个策略只能包含一个自包含的 Python 模块：{names}"
        )
    if not source_path.is_file():
        raise StrategyInstallationValidationError("策略源码不存在")

    canonical_report: dict[str, Any] | None = None
    for path in python_files:
        report = _validate_source(path, require_strategy_contract=path.resolve() == source_resolved)
        if path.resolve() == source_resolved:
            canonical_report = report
    if canonical_report is None:
        raise StrategyInstallationValidationError("策略源码必须是包内 Python 文件")
    return canonical_report


__all__ = [
    "StrategyInstallationValidationError",
    "strategy_dependency_diagnostics",
    "validate_strategy_package",
]
