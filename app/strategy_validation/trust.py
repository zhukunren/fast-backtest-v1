from __future__ import annotations

from pathlib import Path
from typing import Any

from core.research_identity import resolve_strategy_source


def _is_generated_source(path: Path, *, repo_root: Path) -> bool:
    generated_root = (repo_root / "strategy_library" / "generated").resolve()
    try:
        path.resolve().relative_to(generated_root)
    except ValueError:
        return False
    return True


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def build_validation_preflight(
    module: str,
    *,
    repo_root: Path,
    profile: str = "draft",
) -> dict[str, Any]:
    source_path = resolve_strategy_source(module, repo_root=repo_root)
    issues: list[dict[str, str]] = []
    static_check: dict[str, Any] = {}
    security_check: dict[str, Any] = {"required": False, "ok": True, "status": "not_required"}

    if source_path is None:
        issues.append(
            {
                "severity": "error",
                "code": "strategy_source_unresolved",
                "message": f"strategy source could not be resolved: {module}",
            }
        )
    else:
        from strategies.authoring.checks import check_strategy_path

        in_repo = _is_within(source_path, repo_root)
        in_generated = _is_generated_source(source_path, repo_root=repo_root)
        declared_generated = str(module or "").strip().startswith("strategy_library.generated.")
        static_check = check_strategy_path(source_path, profile=profile)
        security_check = {
            "required": True,
            **dict(static_check.get("security") or {}),
        }
        for item in static_check.get("blocking_issues", []) or []:
            if isinstance(item, dict):
                issues.append(dict(item))

        if not in_repo:
            issues.append(
                {
                    "severity": "error",
                    "code": "strategy_source_outside_repo",
                    "message": f"strategy source must resolve inside repository: {source_path}",
                }
            )
        if declared_generated and not in_generated:
            issues.append(
                {
                    "severity": "error",
                    "code": "generated_strategy_scope_invalid",
                    "message": f"generated strategy resolved outside strategy_library/generated: {source_path}",
                }
            )

    return {
        "ok": not issues,
        "status": "pass" if not issues else "fail",
        "module": str(module or "").strip(),
        "profile": str(static_check.get("profile") or profile),
        "source_path": str(source_path) if source_path is not None else "",
        "static_check": static_check,
        "security_check": security_check,
        "issues": issues,
    }


__all__ = ["build_validation_preflight", "resolve_strategy_source"]
