"""Static dependency policy for strategy source submitted by users."""
from __future__ import annotations

import ast
from pathlib import Path


_STRATEGY_MODULE_PREFIXES = ("strategy_library", "web.strategy_library")
_ALLOWED_FRAMEWORK_MODULES = {
    "backtest.panel_native",
    "core.dataframe_utils",
    "data.timing_readers",
    "strategies.api",
}
_DYNAMIC_IMPORT_MODULES = {"importlib", "runpy"}
_DYNAMIC_IMPORT_NAMES = {
    "__import__",
    "import_module",
    "run_module",
    "run_path",
    "spec_from_file_location",
    "SourceFileLoader",
}
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _is_strategy_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _STRATEGY_MODULE_PREFIXES
    )


def _is_project_module(module: str, project_root: Path) -> bool:
    parts = [part for part in module.split(".") if part]
    if not parts:
        return False
    module_path = project_root.joinpath(*parts)
    return module_path.with_suffix(".py").is_file() or (module_path / "__init__.py").is_file()


def _import_issue(module: str, project_root: Path) -> str | None:
    if _is_strategy_module(module):
        return f"strategy_import:{module}"
    root = module.split(".", 1)[0]
    if root in _DYNAMIC_IMPORT_MODULES:
        return f"dynamic_import:{module}"
    if module not in _ALLOWED_FRAMEWORK_MODULES and _is_project_module(module, project_root):
        return f"non_framework_project_import:{module}"
    return None


def find_strategy_dependency_issues(
    source: str,
    *,
    project_root: Path | None = None,
) -> list[str]:
    """Return stable issue codes for strategy dependencies that violate policy."""
    resolved_project_root = (project_root or _DEFAULT_PROJECT_ROOT).resolve()
    tree = ast.parse(source)
    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                issue = _import_issue(alias.name, resolved_project_root)
                if issue:
                    issues.append(issue)
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level:
                issues.append("relative_import")
            else:
                module = node.module or ""
                issue = _import_issue(module, resolved_project_root)
                if issue:
                    issues.append(issue)
                if any(alias.name in _DYNAMIC_IMPORT_NAMES for alias in node.names):
                    issues.append(f"dynamic_import:{module}")
            continue
        if not isinstance(node, ast.Call):
            continue
        function_name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else ""
        )
        if function_name in _DYNAMIC_IMPORT_NAMES:
            issues.append(f"dynamic_import:{function_name}")
    return list(dict.fromkeys(issues))


__all__ = ["find_strategy_dependency_issues"]
