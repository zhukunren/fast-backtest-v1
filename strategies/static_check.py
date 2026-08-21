"""Static checks for the V1 strategy contract."""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from strategies.advanced import ADVANCED_CONTRACT_VERSION


EXECUTION_HOOKS = {
    "panel": ("compute_features", "compute_signals"),
    "single": ("build_single_signals",),
    "fixed_rebalance": (),
}
PANEL_HOOKS = EXECUTION_HOOKS["panel"]
FORBIDDEN_CALLS = frozenset({"get_config"})
STANDARD_SIGNAL_COLUMNS = frozenset(
    {
        "direction",
        "buy_signal_raw",
        "sell_signal_raw",
        "buy_signal",
        "sell_signal",
        "next_up_limit_ok",
    }
)
REMOVED_META_KEYS = {
    "use_market_rank": "use_market_features",
    "market_feature_source_col": "market_rank_specs",
    "market_rank_feature_col": "market_rank_specs",
    "market_rank_passthrough_cols": "market_feature_passthrough_cols",
    "market_rank_col": "market_rank_specs",
    "market_rank_ascending": "market_rank_specs",
    "market_rank_output_col": "market_rank_specs",
    "market_rank_pct_col": "market_rank_specs",
    "market_rank_count_col": "market_rank_specs",
}
RETIRED_PANEL_SYMBOLS = frozenset(
    {
        "FEATURE_BUILDER",
        "SIGNAL_BUILDER",
        "StockSliceStrategy",
        "TemplateRuntime",
        "build_market_features_hook",
        "build_panel_feature_sources",
        "build_panel_features",
        "build_panel_signals",
        "build_strategy_signals",
        "create_strategy_class",
        "create_template_runtime",
    }
)
RETIRED_PANEL_MODULES = frozenset(
    {
        "strategies.factory",
        "strategies.templates.runtime",
    }
)
AUTHORING_PLACEHOLDER_RE = re.compile(
    r"__REQUIRED__|<required(?:\s+[^>]*)?>",
    re.IGNORECASE,
)
EDITABLE_OPEN_RE = re.compile(r"<editable\s+id=[\"']([^\"']+)[\"']")
COMPLETED_STRATEGY_NAMESPACES = (
    "strategy_library.builtin",
    "strategy_library.generated",
    "strategy_library.validated",
    "web.strategy_library",
)
ALPHA_LOGIC_MARKERS = (
    "buy",
    "candidate",
    "entry",
    "priority",
    "rank",
    "score",
    "sell",
    "signal",
)
ALPHA_WRAPPER_NAMES = frozenset(
    {
        "build_board_time_ok",
        "build_board_timing_signal",
        "build_index_timing_signal",
        "build_time_ok",
        "compute_features",
        "compute_signals",
        "prepare_panel",
        "restore_panel_order",
    }
)
STRATEGY_CLUSTER_SIMILARITY_LIMIT = 0.92
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INDEPENDENCE_REFERENCE_ROOTS = (
    REPOSITORY_ROOT / "strategy_library" / "builtin",
    REPOSITORY_ROOT / "strategy_library" / "generated",
    REPOSITORY_ROOT / "strategy_library" / "validated",
    REPOSITORY_ROOT / "web" / "strategy_library",
)


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _editable_ids(source: str) -> list[str]:
    return EDITABLE_OPEN_RE.findall(source)


def _all_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in getattr(tree, "body", ())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _strategy_classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [
        node
        for node in getattr(tree, "body", ())
        if isinstance(node, ast.ClassDef) and node.name == "Strategy"
    ]


def _class_functions(node: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        item.name: item
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_assignments(tree: ast.AST, name: str) -> list[ast.AST | None]:
    values: list[ast.AST | None] = []
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            values.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                values.append(node.value)
    return values


def _literal_meta(tree: ast.AST) -> tuple[str, dict[str, Any]]:
    values = _module_assignments(tree, "STRATEGY_META")
    if not values:
        return "missing", {}
    if len(values) != 1 or values[0] is None:
        return "nonliteral", {}
    try:
        value = ast.literal_eval(values[0])
    except Exception:
        return "nonliteral", {}
    return ("ok", value) if isinstance(value, dict) else ("nonliteral", {})


def _execution_mode(meta: dict[str, Any]) -> str:
    return str(meta.get("execution_mode", "panel") or "panel").strip().casefold()


def _bool_meta(meta: dict[str, Any], key: str) -> bool | None:
    if key not in meta:
        return False
    value = meta.get(key)
    if isinstance(value, bool):
        return value
    token = str(value).strip().casefold()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _required_hooks(meta: dict[str, Any]) -> set[str]:
    required = set(EXECUTION_HOOKS.get(_execution_mode(meta), ()))
    if _bool_meta(meta, "use_index_timing") is True:
        required.add("build_time_ok")
    if _bool_meta(meta, "use_board_timing") is True:
        required.add("build_board_time_ok")
    return required


def _imported_base_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "strategies.api",
            "strategies.base",
        }:
            aliases.update(
                item.asname or item.name
                for item in node.names
                if item.name == "BaseStrategy"
            )
        elif isinstance(node, ast.Import):
            for item in node.names:
                if item.name in {"strategies.api", "strategies.base"}:
                    aliases.add(f"{item.asname or item.name}.BaseStrategy")
    return aliases


def _attribute_path(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attribute_path(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _class_assignment(node: ast.ClassDef, name: str) -> ast.AST | None:
    matches: list[ast.AST | None] = []
    for item in node.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in item.targets
        ):
            matches.append(item.value)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.target.id == name:
                matches.append(item.value)
    return matches[0] if len(matches) == 1 else None


def _class_callable_names(node: ast.ClassDef, module_funcs: set[str]) -> set[str]:
    names = set(_class_functions(node))
    for item in node.body:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(item, ast.Assign):
            targets = list(item.targets)
            value = item.value
        elif isinstance(item, ast.AnnAssign):
            targets = [item.target]
            value = item.value
        if value is None:
            continue
        target_names = {
            target.id for target in targets if isinstance(target, ast.Name)
        }
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"staticmethod", "classmethod"}
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id in module_funcs
            and not value.keywords
        ):
            names.update(target_names)
    return names


def _has_zero_arg_constructor(node: ast.ClassDef) -> bool:
    init = _class_functions(node).get("__init__")
    if init is None:
        return True
    positional = [*init.args.posonlyargs, *init.args.args]
    required_count = len(positional) - len(init.args.defaults)
    if positional and positional[0].arg in {"self", "cls"}:
        required_count -= 1
    required_kwonly = sum(
        default is None for default in init.args.kw_defaults
    )
    return required_count <= 0 and required_kwonly == 0


def _literal_number(node: ast.AST | None) -> int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    return None


def _call_arg(node: ast.Call, name: str, position: int) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return node.args[position] if len(node.args) > position else None


def _future_data_issues(tree: ast.AST, *, path: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        name = node.func.attr
        if name in {"shift", "pct_change", "diff"}:
            period = _literal_number(_call_arg(node, "periods", 0))
            if period is not None and period < 0:
                issues.append(
                    _issue(
                        "error",
                        f"future_data_{name}",
                        f"{path}:{node.lineno} uses {name}({period})",
                    )
                )
        if name == "rolling":
            center = _call_arg(node, "center", 99)
            if isinstance(center, ast.Constant) and center.value is True:
                issues.append(
                    _issue(
                        "error",
                        "future_data_centered_rolling",
                        f"{path}:{node.lineno} uses rolling(center=True)",
                    )
                )
    return issues


def _completed_strategy_import_issues(
    tree: ast.AST,
    *,
    path: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if node.level:
                modules = [f"{'.' * node.level}{module}"]
            elif any(
                module == namespace or module.startswith(f"{namespace}.")
                for namespace in COMPLETED_STRATEGY_NAMESPACES
            ):
                modules = [module]
        for module in modules:
            is_relative = module.startswith(".")
            is_completed = any(
                module == namespace or module.startswith(f"{namespace}.")
                for namespace in COMPLETED_STRATEGY_NAMESPACES
            )
            if not (is_relative or is_completed):
                continue
            issues.append(
                _issue(
                    "error",
                    "completed_strategy_source_reuse",
                    (
                        f"{path}:{getattr(node, 'lineno', '?')} imports {module!r}; "
                        "independent research may use framework APIs and raw inputs, "
                        "but not completed strategy modules or their private kernels"
                    ),
                )
            )
    return issues


def _independence_meta(
    tree: ast.AST,
    *,
    path: str,
) -> tuple[str, list[dict[str, str]]]:
    status, meta = _literal_meta(tree)
    if status != "ok":
        return "", []
    independence = meta.get("independence")
    if not isinstance(independence, dict):
        return "", [
            _issue(
                "error",
                "missing_independence_metadata",
                f"{path}: STRATEGY_META.independence must declare signal_family and research_basis",
            )
        ]
    family = str(independence.get("signal_family") or "").strip()
    basis = str(independence.get("research_basis") or "").strip().casefold()
    issues: list[dict[str, str]] = []
    if not family:
        issues.append(
            _issue(
                "error",
                "missing_signal_family",
                f"{path}: STRATEGY_META.independence.signal_family is required",
            )
        )
    if basis != "raw_market_data":
        issues.append(
            _issue(
                "error",
                "invalid_independence_research_basis",
                (
                    f"{path}: STRATEGY_META.independence.research_basis must be "
                    "'raw_market_data'"
                ),
            )
        )
    return family.casefold(), issues


def _contains_alpha_marker(node: ast.AST) -> bool:
    for child in ast.walk(node):
        values: tuple[str, ...] = ()
        if isinstance(child, ast.Name):
            values = (child.id,)
        elif isinstance(child, ast.Attribute):
            values = (child.attr,)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            values = (child.value,)
        if any(
            marker in value.casefold()
            for value in values
            for marker in ALPHA_LOGIC_MARKERS
        ):
            return True
    return False


def _logic_tokens(tree: ast.AST) -> tuple[str, ...]:
    tokens: list[str] = []
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name not in ALPHA_WRAPPER_NAMES
        and _contains_alpha_marker(node)
    ]
    for function in sorted(functions, key=lambda node: int(node.lineno)):
        for node in ast.walk(function):
            tokens.append(type(node).__name__)
            if isinstance(node, ast.Attribute):
                tokens.append(f"attr:{node.attr.casefold()}")
            elif isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    tokens.append(f"str:{node.value.casefold()}")
                elif isinstance(node.value, bool) or node.value is None:
                    tokens.append(f"literal:{node.value!r}")
                elif isinstance(node.value, (int, float, complex)):
                    tokens.append("number")
            elif isinstance(node, ast.keyword):
                tokens.append(f"keyword:{str(node.arg).casefold()}")
    return tuple(tokens)


def _strategy_independence_issues(
    parsed: list[tuple[str, ast.AST]],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    families: dict[str, str] = {}
    logic: dict[str, tuple[str, ...]] = {}
    for path, tree in parsed:
        family, _ = _independence_meta(tree, path=path)
        if family:
            previous = families.get(family)
            if previous is not None:
                issues.append(
                    _issue(
                        "error",
                        "duplicate_signal_family",
                        f"{path} and {previous} declare the same signal family {family!r}",
                    )
                )
            else:
                families[family] = path
        logic[path] = _logic_tokens(tree)

    ordered_paths = sorted(logic, key=str.casefold)
    for index, left_path in enumerate(ordered_paths):
        left = logic[left_path]
        if len(left) < 40:
            issues.append(
                _issue(
                    "error",
                    "insufficient_independent_signal_logic",
                    f"{left_path}: could not identify enough local signal/ranking logic",
                )
            )
            continue
        for right_path in ordered_paths[index + 1 :]:
            right = logic[right_path]
            if len(right) < 40:
                continue
            similarity = difflib.SequenceMatcher(
                None,
                left,
                right,
                autojunk=False,
            ).ratio()
            if similarity >= STRATEGY_CLUSTER_SIMILARITY_LIMIT:
                issues.append(
                    _issue(
                        "error",
                        "strategy_cluster_variant",
                        (
                            f"{left_path} and {right_path} have normalized signal-logic "
                            f"similarity {similarity:.3f} >= "
                            f"{STRATEGY_CLUSTER_SIMILARITY_LIMIT:.2f}"
                        ),
                    )
                )
    return issues


def _strategy_identity(path: Path) -> str:
    return path.parent.name if path.name.casefold() == "strategy.py" else path.stem


def _repository_independence_references(
    targets: Iterable[Path],
) -> tuple[Path, ...]:
    target_paths = {path.resolve() for path in targets}
    target_names = {_strategy_identity(path).casefold() for path in targets}
    references: list[Path] = []
    for root in INDEPENDENCE_REFERENCE_ROOTS:
        if not root.is_dir():
            continue
        candidates = (
            root.glob("*/strategy.py")
            if root == REPOSITORY_ROOT / "web" / "strategy_library"
            else root.glob("*.py")
        )
        for path in sorted(candidates, key=lambda item: str(item).casefold()):
            if path.name == "__init__.py" or path.resolve() in target_paths:
                continue
            if _strategy_identity(path).casefold() in target_names:
                continue
            references.append(path)
    return tuple(references)


def _completed_strategy_reference_issues(
    targets: list[tuple[str, ast.AST]],
    references: Iterable[Path],
) -> tuple[list[dict[str, str]], int]:
    issues: list[dict[str, str]] = []
    reference_count = 0
    target_families = {
        path: _independence_meta(tree, path=path)[0]
        for path, tree in targets
    }
    target_logic = {path: _logic_tokens(tree) for path, tree in targets}
    for reference in sorted((Path(path) for path in references), key=lambda item: str(item).casefold()):
        try:
            source = _read_source(reference)
            tree = ast.parse(source, filename=str(reference))
        except (OSError, SyntaxError, UnicodeError) as exc:
            issues.append(
                _issue(
                    "warning",
                    "independence_reference_unavailable",
                    f"{reference}: could not inspect independence reference: {exc}",
                )
            )
            continue
        reference_count += 1
        reference_path = str(reference)
        reference_family, _ = _independence_meta(tree, path=reference_path)
        reference_logic = _logic_tokens(tree)
        for target_path, _ in targets:
            family = target_families[target_path]
            if family and reference_family and family == reference_family:
                issues.append(
                    _issue(
                        "error",
                        "completed_strategy_signal_family_reuse",
                        (
                            f"{target_path} and completed strategy {reference_path} "
                            f"declare the same signal family {family!r}"
                        ),
                    )
                )
            logic = target_logic[target_path]
            if len(logic) < 40 or len(reference_logic) < 40:
                continue
            similarity = difflib.SequenceMatcher(
                None,
                logic,
                reference_logic,
                autojunk=False,
            ).ratio()
            if similarity >= STRATEGY_CLUSTER_SIMILARITY_LIMIT:
                issues.append(
                    _issue(
                        "error",
                        "completed_strategy_cluster_reuse",
                        (
                            f"{target_path} and completed strategy {reference_path} have "
                            f"normalized signal-logic similarity {similarity:.3f} >= "
                            f"{STRATEGY_CLUSTER_SIMILARITY_LIMIT:.2f}"
                        ),
                    )
                )
    return issues, reference_count


def _subscript_name(node: ast.Subscript) -> str | None:
    value = node.slice
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _standard_signal_write_issues(
    tree: ast.AST,
    *,
    path: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for child in ast.walk(target):
                if not isinstance(child, ast.Subscript):
                    continue
                name = _subscript_name(child)
                if name in STANDARD_SIGNAL_COLUMNS:
                    issues.append(
                        _issue(
                            "error",
                            "direct_standard_signal_write",
                            f"{path}:{getattr(node, 'lineno', '?')} writes framework-owned column {name!r}",
                        )
                    )
    return issues


def _retired_symbol_issues(
    tree: ast.AST,
    *,
    path: str,
    mode: str,
) -> list[dict[str, str]]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in RETIRED_PANEL_SYMBOLS:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in RETIRED_PANEL_SYMBOLS:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module in RETIRED_PANEL_MODULES:
            found.add(str(node.module))
        elif isinstance(node, ast.Import):
            found.update(
                item.name
                for item in node.names
                if item.name in RETIRED_PANEL_MODULES
            )
    if mode == "panel":
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name == "finalize_signals":
                    found.add(name)
    return [
        _issue(
            "error",
            "retired_panel_symbol",
            f"{path}: retired panel symbol is not supported: {name}",
        )
        for name in sorted(found, key=str.casefold)
    ]


def _meta_issues(
    meta_status: str,
    meta: dict[str, Any],
    *,
    path: str,
    strict: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if meta_status != "ok":
        code = "missing_strategy_meta" if meta_status == "missing" else "nonliteral_strategy_meta"
        issues.append(
            _issue(
                "error",
                code,
                f"{path}: STRATEGY_META must be one literal dictionary",
            )
        )
        return issues
    version = str(meta.get("strategy_contract_version") or "").strip()
    if version != ADVANCED_CONTRACT_VERSION:
        issues.append(
            _issue(
                "error",
                "unsupported_strategy_contract",
                f"{path}: expected strategy_contract_version={ADVANCED_CONTRACT_VERSION!r}; got {version or '<missing>'!r}",
            )
        )
    mode = _execution_mode(meta)
    if mode not in EXECUTION_HOOKS:
        issues.append(
            _issue(
                "error",
                "invalid_execution_mode",
                f"{path}: unsupported execution_mode {mode!r}",
            )
        )
    for old, replacement in REMOVED_META_KEYS.items():
        if old in meta:
            issues.append(
                _issue(
                    "error",
                    "removed_strategy_meta_key",
                    f"{path}: STRATEGY_META.{old} was removed; use {replacement}",
                )
            )
    for flag in ("use_market_features", "use_index_timing", "use_board_timing", "require_board_member"):
        if flag in meta and _bool_meta(meta, flag) is None:
            issues.append(
                _issue(
                    "error",
                    "invalid_capability_flag",
                    f"{path}: STRATEGY_META.{flag} must be boolean",
                )
            )
    if strict:
        try:
            from types import SimpleNamespace

            from strategies.spec import get_complete_strategy_spec, validate_strategy_contract

            contract = get_complete_strategy_spec(
                module=SimpleNamespace(STRATEGY_META=meta)
            )
            report = validate_strategy_contract(contract.strategy, strict=True)
            issues.extend(
                _issue(
                    "error",
                    str(item.get("code") or "invalid_strategy_contract"),
                    f"{path}: {item.get('message') or 'invalid strategy contract'}",
                )
                for item in report.get("issues", ())
            )
        except Exception as exc:
            issues.append(
                _issue(
                    "error",
                    "invalid_strategy_meta_schema",
                    f"{path}: {exc}",
                )
            )
    return issues


def _hook_placeholder_reason(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if not body or all(isinstance(item, ast.Pass) for item in body):
        return "pass"
    for item in ast.walk(node):
        if not isinstance(item, ast.Raise):
            continue
        exc = item.exc
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return "NotImplementedError"
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            if exc.func.id == "NotImplementedError":
                return "NotImplementedError"
    return ""


def _strategy_export_issues(
    tree: ast.AST,
    meta_status: str,
    meta: dict[str, Any],
    *,
    path: str,
    strict: bool,
) -> list[dict[str, str]]:
    classes = _strategy_classes(tree)
    if len(classes) != 1:
        code = "missing_strategy_export" if not classes else "multiple_strategy_exports"
        return [
            _issue(
                "error",
                code,
                f"{path}: define exactly one module-level class Strategy(BaseStrategy)",
            )
        ]
    node = classes[0]
    issues: list[dict[str, str]] = []
    base_aliases = _imported_base_aliases(tree)
    if not any(_attribute_path(base) in base_aliases for base in node.bases):
        issues.append(
            _issue(
                "error",
                "invalid_strategy_class_base",
                f"{path}: class Strategy must directly inherit imported BaseStrategy",
            )
        )
    if not _has_zero_arg_constructor(node):
        issues.append(
            _issue(
                "error",
                "strategy_constructor_requires_arguments",
                f"{path}: Strategy must be instantiable without arguments",
            )
        )
    meta_value = _class_assignment(node, "STRATEGY_META")
    if meta_value is None:
        issues.append(
            _issue(
                "error",
                "missing_strategy_class_meta",
                f"{path}: Strategy must expose STRATEGY_META",
            )
        )
    elif meta_status == "ok":
        matches = isinstance(meta_value, ast.Name) and meta_value.id == "STRATEGY_META"
        if not matches:
            try:
                matches = ast.literal_eval(meta_value) == meta
            except Exception:
                matches = False
        if not matches:
            issues.append(
                _issue(
                    "error",
                    "invalid_strategy_class_meta",
                    f"{path}: Strategy.STRATEGY_META must match module STRATEGY_META",
                )
            )
    required = _required_hooks(meta) if meta_status == "ok" else set(PANEL_HOOKS)
    module_funcs = _module_functions(tree)
    callable_names = _class_callable_names(node, set(module_funcs))
    missing = sorted(required - callable_names, key=str.casefold)
    if missing:
        issues.append(
            _issue(
                "error",
                "strategy_class_hook_missing",
                f"{path}: Strategy missing required hooks: {missing}",
            )
        )
    if _execution_mode(meta) == "panel":
        class_funcs = _class_functions(node)
        missing_methods = sorted(set(PANEL_HOOKS) - set(class_funcs), key=str.casefold)
        if missing_methods:
            issues.append(
                _issue(
                    "error",
                    "panel_hook_must_be_method",
                    f"{path}: panel hooks must be explicit Strategy methods: {missing_methods}",
                )
            )
    if strict:
        for name, function in sorted(_all_functions(tree).items(), key=lambda item: item[0].casefold()):
            reason = _hook_placeholder_reason(function)
            if reason:
                issues.append(
                    _issue(
                        "error",
                        "unimplemented_authoring_hook",
                        f"{path}:{function.lineno} {name}() contains {reason}",
                    )
                )
    return issues


def check_strategy_source(
    source: str,
    *,
    path: str = "<source>",
    allow_get_config: bool = False,
    expected_editables: tuple[str, ...] = (),
    strict_authoring: bool = False,
    enforce_independence: bool = False,
    independence_reference_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return {
            "ok": False,
            "path": path,
            "functions": [],
            "has_panel_features": False,
            "has_panel_signals": False,
            "issues": [_issue("error", "syntax_error", str(exc))],
        }

    meta_status, meta = _literal_meta(tree)
    mode = _execution_mode(meta) if meta_status == "ok" else "panel"
    issues: list[dict[str, str]] = []
    if expected_editables:
        observed = _editable_ids(source)
        if observed != list(expected_editables):
            issues.append(
                _issue(
                    "error",
                    "editable_layout_mismatch",
                    f"{path}: editable layout {observed} does not match {list(expected_editables)}",
                )
            )
    if not allow_get_config:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                if name in FORBIDDEN_CALLS:
                    issues.append(
                        _issue(
                            "error",
                            "forbidden_global_config",
                            f"{path}:{node.lineno} calls {name}()",
                        )
                    )
    issues.extend(_meta_issues(meta_status, meta, path=path, strict=strict_authoring))
    issues.extend(_retired_symbol_issues(tree, path=path, mode=mode))
    issues.extend(_future_data_issues(tree, path=path))
    issues.extend(_standard_signal_write_issues(tree, path=path))
    if enforce_independence:
        issues.extend(_completed_strategy_import_issues(tree, path=path))
        _, independence_issues = _independence_meta(tree, path=path)
        issues.extend(independence_issues)
    issues.extend(
        _strategy_export_issues(
            tree,
            meta_status,
            meta,
            path=path,
            strict=strict_authoring,
        )
    )
    if strict_authoring and AUTHORING_PLACEHOLDER_RE.search(source):
        issues.append(
            _issue(
                "error",
                "unresolved_authoring_placeholder",
                f"{path}: unresolved authoring placeholder",
            )
        )
    functions = _all_functions(tree)
    return {
        "ok": not any(item["severity"] == "error" for item in issues),
        "path": path,
        "functions": sorted(functions, key=str.casefold),
        "has_panel_features": "compute_features" in functions,
        "has_panel_signals": "compute_signals" in functions,
        "issues": issues,
    }


def check_strategy_files(
    paths: Iterable[Path],
    *,
    allow_get_config: bool = False,
    expected_editables: tuple[str, ...] = (),
    strict_authoring: bool = False,
    enforce_independence: bool = False,
    independence_reference_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    ordered = sorted((Path(path) for path in paths), key=lambda item: str(item).casefold())
    sources = [(path, _read_source(path)) for path in ordered]
    reports = [
        check_strategy_source(
            source,
            path=str(path),
            allow_get_config=allow_get_config,
            expected_editables=expected_editables,
            strict_authoring=strict_authoring,
            enforce_independence=enforce_independence,
        )
        for path, source in sources
    ]
    independence_issues: list[dict[str, str]] = []
    independence_reference_count = 0
    if enforce_independence:
        parsed: list[tuple[str, ast.AST]] = []
        for path, source in sources:
            try:
                parsed.append((str(path), ast.parse(source, filename=str(path))))
            except SyntaxError:
                continue
        independence_issues = _strategy_independence_issues(parsed)
        reference_issues, independence_reference_count = (
            _completed_strategy_reference_issues(
                parsed,
                independence_reference_paths,
            )
        )
        independence_issues.extend(reference_issues)
    return {
        "ok": (
            all(bool(report["ok"]) for report in reports)
            and not any(issue["severity"] == "error" for issue in independence_issues)
        ),
        "strategy_count": len(reports),
        "reports": reports,
        "independence_issues": independence_issues,
        "independence_reference_count": independence_reference_count,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check V1 strategy source contracts.")
    parser.add_argument("paths", nargs="+", help="Strategy .py files to check.")
    parser.add_argument(
        "--allow-get-config",
        action="store_true",
        help="Allow a strategy to read global config during a diagnostic run.",
    )
    parser.add_argument(
        "--expected-editables",
        default="",
        help="Comma-separated editable id layout to require.",
    )
    parser.add_argument(
        "--strict-authoring",
        action="store_true",
        help="Enable promotion metadata and placeholder checks.",
    )
    parser.add_argument(
        "--enforce-independence",
        action="store_true",
        help=(
            "Reject completed-strategy imports, missing independent-research metadata, "
            "duplicate signal families, and normalized signal-logic variants."
        ),
    )
    parser.add_argument("--json-out", default="", help="Optional output JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    expected = tuple(
        item.strip()
        for item in str(args.expected_editables or "").split(",")
        if item.strip()
    )
    target_paths = tuple(Path(item) for item in args.paths)
    reference_paths = (
        _repository_independence_references(target_paths)
        if args.enforce_independence
        else ()
    )
    payload = check_strategy_files(
        target_paths,
        allow_get_config=bool(args.allow_get_config),
        expected_editables=expected,
        strict_authoring=bool(args.strict_authoring),
        enforce_independence=bool(args.enforce_independence),
        independence_reference_paths=reference_paths,
    )
    encoded = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if payload["ok"] else 1


__all__ = [
    "EXECUTION_HOOKS",
    "PANEL_HOOKS",
    "build_arg_parser",
    "check_strategy_files",
    "check_strategy_source",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
