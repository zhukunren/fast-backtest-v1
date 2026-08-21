"""Static gate for retired per-stock panel compatibility paths.

The V1 panel contract invokes each formula once for the sealed panel.  This
module is intentionally small and dependency-free so CI can run it before any
data-backed test.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_PANEL_MODE_RE = re.compile(r"[\"']execution_mode[\"']\s*:\s*[\"']panel[\"']")
_FOR_STOCK_ID_RE = re.compile(r"\bfor\s+stock_id\b")
_RETIRED_ACTIVITY_HELPERS = (
    "_add_single_stock_features",
    "_compute_stock_features",
    "_compute_stock_signals",
    "_finalize_panel_signals",
    "_next_open_array",
    "_next_open_by_stock",
    "_next_up_limit_mask",
    "build_market_features",
)
_REMOVED_PRIVATE_HELPERS = frozenset(
    {
        "_add_features",
        "_add_market_source_features",
    }
)
_FRAMEWORK_SIGNAL_IDENTIFIERS = frozenset(
    {
        "RISK_COL_NEXT_UP_LIMIT_OK",
        "SIGNAL_COL_BUY",
        "SIGNAL_COL_BUY_RAW",
        "SIGNAL_COL_DIRECTION",
        "SIGNAL_COL_SELL",
        "SIGNAL_COL_SELL_RAW",
    }
)
_FRAMEWORK_SIGNAL_COLUMNS = frozenset(
    {
        "buy_signal",
        "buy_signal_raw",
        "direction",
        "next_up_limit_ok",
        "sell_signal",
        "sell_signal_raw",
    }
)
_META_OUTPUT_SPEC_KEYS = (
    "market_rank_specs",
    "market_aggregate_specs",
    "cross_section_transform_specs",
)


def _python_files(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for base in (
        root / "strategies" / "templates",
        root / "strategy_library",
        root / "web" / "strategy_library",
    ):
        if not base.is_dir():
            continue
        candidates.extend(
            path
            for path in base.rglob("*.py")
            if "/web_cache/" not in f"/{path.relative_to(root).as_posix()}/"
            and "/logs/" not in f"/{path.relative_to(root).as_posix()}/"
        )
    return tuple(sorted(set(candidates), key=lambda path: str(path).casefold()))


def _is_strategy_module(path: Path, source: str) -> bool:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    return any(isinstance(node, ast.ClassDef) and node.name == "Strategy" for node in tree.body)


def _is_panel_strategy(source: str) -> bool:
    # Every V1 Strategy without an explicit mode is treated as panel by the
    # loader; keep the fallback here so newly authored modules are covered.
    if _PANEL_MODE_RE.search(source):
        return True
    return '"execution_mode": "single"' not in source and "'execution_mode': 'single'" not in source


def _call_has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _meta_spec_value(meta_node: ast.AST, key: str) -> object:
    if isinstance(meta_node, ast.Dict):
        for key_node, value_node in zip(meta_node.keys, meta_node.values):
            if (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and key_node.value == key
            ):
                try:
                    return ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    return ()
        return ()
    try:
        meta = ast.literal_eval(meta_node)
    except (ValueError, TypeError):
        return ()
    return meta.get(key, ()) if isinstance(meta, dict) else ()


def _framework_output_columns(tree: ast.Module) -> frozenset[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "STRATEGY_META" for target in node.targets):
            continue
        output: set[str] = set(_FRAMEWORK_SIGNAL_COLUMNS)
        rank_specs = _meta_spec_value(node.value, "market_rank_specs")
        for raw in rank_specs or ():
            if isinstance(raw, dict):
                output.update(
                    str(raw.get(name, "") or "").strip()
                    for name in ("rank_col", "pct_col", "count_col")
                )
        for key in _META_OUTPUT_SPEC_KEYS[1:]:
            for raw in _meta_spec_value(node.value, key) or ():
                if isinstance(raw, dict):
                    output.add(str(raw.get("output_col", "") or "").strip())
        return frozenset(name for name in output if name)
    return _FRAMEWORK_SIGNAL_COLUMNS


def _assigned_column_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _simple_call_failure(relative: str, node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Name):
        return None
    if node.func.id == "preprocess_ohlcv":
        return (
            f"{relative}:{node.lineno}: panel strategy cannot call the "
            "single-stock preprocess_ohlcv helper"
        )
    if node.func.id in _REMOVED_PRIVATE_HELPERS:
        return (
            f"{relative}:{node.lineno}: removed private helper call "
            f"{node.func.id!r} is forbidden"
        )
    return None


def _cross_module_private_call_failure(relative: str, node: ast.Call) -> str | None:
    function = node.func
    if not isinstance(function, ast.Attribute):
        return None
    if not function.attr.startswith("_") or function.attr.startswith("__"):
        return None
    if not isinstance(function.value, ast.Name) or function.value.id in {"self", "cls"}:
        return None
    return (
        f"{relative}:{node.lineno}: cross-module private helper call "
        f"{function.value.id}.{function.attr} is forbidden"
    )


def _assigned_keyword_failures(
    relative: str,
    node: ast.Call,
    framework_outputs: frozenset[str],
) -> list[str]:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "assign":
        return []
    return [
        f"{relative}:{node.lineno}: framework-owned market column "
        f"{keyword.arg!r} cannot be assigned by a strategy"
        for keyword in node.keywords
        if keyword.arg in framework_outputs
    ]


def _call_failures(
    relative: str,
    node: ast.Call,
    framework_outputs: frozenset[str],
) -> list[str]:
    failures: list[str] = []
    if _call_has_keyword(node, "panel_mode"):
        failures.append(f"{relative}:{node.lineno}: panel_mode is retired")
    if isinstance(node.func, ast.Attribute) and node.func.attr == "stock_slice":
        failures.append(f"{relative}:{node.lineno}: stock_slice is retired")
    for failure in (
        _simple_call_failure(relative, node),
        _cross_module_private_call_failure(relative, node),
    ):
        if failure is not None:
            failures.append(failure)
    failures.extend(_assigned_keyword_failures(relative, node, framework_outputs))
    return failures


def _assignment_failures(
    relative: str,
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    framework_outputs: frozenset[str],
) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
    failures: list[str] = []
    for target in targets:
        column = _assigned_column_name(target)
        if column in framework_outputs:
            failures.append(
                f"{relative}:{node.lineno}: framework-owned market column "
                f"{column!r} cannot be assigned by a strategy"
            )
    return failures


def _signal_stage_failures(
    relative: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    defined_functions: set[str],
) -> list[str]:
    if "signal" not in node.name.casefold():
        return []
    failures: list[str] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id in defined_functions and "feature" in call.func.id.casefold():
            failures.append(
                f"{relative}:{call.lineno}: signal stage cannot call feature builder "
                f"{call.func.id!r}"
            )
    return failures


def _builder_call_count(tree: ast.AST, function_name: str) -> int | None:
    function = next(
        (
            node
            for node in getattr(tree, "body", ())
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return None
    return sum(
        1
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "builder"
    )


def _scan_frame_compute(repo: Path) -> list[str]:
    frame_path = repo / "strategies" / "frame_compute.py"
    if not frame_path.is_file():
        return []
    failures: list[str] = []
    frame_source = frame_path.read_text(encoding="utf-8")
    for token in (
        "panel_mode",
        "stock_slice",
        "_stock_count",
        "_allocate_aligned_columns",
        "_write_aligned_columns",
    ):
        if token in frame_source:
            failures.append(f"strategies/frame_compute.py contains retired token {token!r}")
    if _FOR_STOCK_ID_RE.search(frame_source):
        failures.append("strategies/frame_compute.py contains a stock_id loop")
    try:
        frame_tree = ast.parse(frame_source, filename=str(frame_path))
    except SyntaxError as exc:
        failures.append(f"strategies/frame_compute.py: syntax error: {exc}")
        return failures
    for function_name in ("compute_feature_batch", "compute_signal_batch"):
        count = _builder_call_count(frame_tree, function_name)
        if count != 1:
            failures.append(
                f"strategies/frame_compute.py:{function_name} "
                f"must call builder exactly once; got {count}"
            )
    return failures


def _scan_strategy_path(repo: Path, path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    relative = path.relative_to(repo).as_posix()
    failures = [
        f"{relative}: retired helper {retired_name!r} is present"
        for retired_name in _RETIRED_ACTIVITY_HELPERS
        if re.search(rf"\b{re.escape(retired_name)}\b", source)
    ]
    if not _is_strategy_module(path, source) or not _is_panel_strategy(source):
        return failures
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        failures.append(f"{relative}: syntax error: {exc}")
        return failures
    defined_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    framework_outputs = _framework_output_columns(tree)
    for name in sorted(defined_functions.intersection(_REMOVED_PRIVATE_HELPERS), key=str.casefold):
        function = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )
        failures.append(
            f"{relative}:{function.lineno}: removed private helper {name!r} cannot be defined"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            failures.extend(_call_failures(relative, node, framework_outputs))
        elif isinstance(node, ast.For):
            target = ast.unparse(node.target) if hasattr(ast, "unparse") else ""
            if "stock_id" in target:
                failures.append(f"{relative}:{node.lineno}: stock_id loop is retired")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            failures.extend(_assignment_failures(relative, node, framework_outputs))
        elif isinstance(node, ast.Name) and node.id in _FRAMEWORK_SIGNAL_IDENTIFIERS:
            failures.append(
                f"{relative}:{node.lineno}: panel strategy cannot own framework signal "
                f"identifier {node.id!r}"
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            failures.extend(_signal_stage_failures(relative, node, defined_functions))
    return failures


def scan(root: Path | None = None) -> tuple[str, ...]:
    """Return deterministic human-readable gate failures."""
    repo = Path(root or ROOT).resolve()
    failures = _scan_frame_compute(repo)

    for path in _python_files(repo):
        failures.extend(_scan_strategy_path(repo, path))

    return tuple(sorted(set(failures), key=str.casefold))


def main(argv: list[str] | None = None) -> int:
    _ = argv
    failures = scan()
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        return 1
    print("retired panel path gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ROOT", "main", "scan"]
