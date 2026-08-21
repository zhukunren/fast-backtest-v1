"""Static misuse screening for trusted locally authored strategy modules.

This denylist is defense in depth for authoring mistakes, not an execution
sandbox or a boundary for hostile Python code.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BANNED_IMPORT_ROOTS = {
    "builtins",
    "ctypes",
    "http",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "sqlite3",
    "socket",
    "subprocess",
    "sys",
    "threading",
    "urllib",
}
_BANNED_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
_BANNED_ATTR_CALLS = {
    "os.popen",
    "os.remove",
    "os.rmdir",
    "os.system",
    "os.unlink",
    "shutil.rmtree",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
}
_BANNED_NAME_IDS = {"__builtins__"}
_BANNED_ATTR_NAMES = {
    "__bases__",
    "__base__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__subclasses__",
}


def _get_attr_chain(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def validate_strategy_code_security(code: str, *, source: str = "<strategy>") -> None:
    """Reject common dangerous constructs in trusted code without claiming isolation."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"strategy_syntax_invalid: {source}: {exc}") from None

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in _BANNED_NAME_IDS or node.id.startswith("__"):
                raise ValueError(f"strategy_unsafe_name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _BANNED_ATTR_NAMES or node.attr.startswith("__"):
                raise ValueError(f"strategy_unsafe_attr: {node.attr}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _BANNED_IMPORT_ROOTS:
                    raise ValueError(f"strategy_unsafe_import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module_name = str(node.module or "")
            root = module_name.split(".", 1)[0] if module_name else ""
            if root in _BANNED_IMPORT_ROOTS:
                raise ValueError(f"strategy_unsafe_import: {module_name}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in _BANNED_CALL_NAMES:
                    raise ValueError(f"strategy_unsafe_call: {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                chain = _get_attr_chain(node.func)
                if chain in _BANNED_ATTR_CALLS:
                    raise ValueError(f"strategy_unsafe_call: {chain}")


def validate_strategy_path_security(path: Path) -> None:
    p = Path(path).resolve()
    validate_strategy_code_security(p.read_text(encoding="utf-8"), source=str(p))


__all__ = ["validate_strategy_code_security", "validate_strategy_path_security"]
