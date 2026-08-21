"""
策略动态加载器
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .advanced import ADVANCED_CONTRACT_VERSION
from .base import BaseStrategy
from .descriptor import LoadedStrategyDescriptor
from .spec import CompleteStrategySpec, get_complete_strategy_spec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module resolution (merged from module_resolver.py)
# ---------------------------------------------------------------------------

def _looks_like_file_path(module_path: str) -> bool:
    return (
        "/" in module_path
        or "\\" in module_path
        or module_path.endswith(".py")
    )


def _allow_unsafe_strategy_path_enabled() -> bool:
    return os.environ.get("ALLOW_UNSAFE_STRATEGY_PATH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _is_path_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_repo_scoped_strategy_path(path: Path, *, root: Path) -> bool:
    return _is_path_within_root(path, root)


def import_strategy_module(module_path: str, *, reload_module: bool = False) -> ModuleType:
    """
    Import strategy from Python module path or file path.

    File-path loading is repo-scoped by default. Absolute or relative paths
    outside the current project root remain blocked unless the caller opts in
    via ``ALLOW_UNSAFE_STRATEGY_PATH=1``.
    """
    try:
        if reload_module and module_path in sys.modules:
            return importlib.reload(sys.modules[module_path])
        return importlib.import_module(module_path)
    except ImportError:
        path = Path(module_path)
        if not _looks_like_file_path(module_path) and (not path.exists()):
            raise
        if path.suffix == "":
            path = path.with_suffix(".py")
        if not path.exists():
            cwd_path = Path.cwd() / path.name
            if cwd_path.exists():
                path = cwd_path
            else:
                raise FileNotFoundError(f"策略模块不存在: {module_path}")

        resolved = path.resolve()
        root = Path.cwd().resolve()
        allow_unsafe = _allow_unsafe_strategy_path_enabled()
        if not _is_repo_scoped_strategy_path(
            resolved,
            root=root,
        ):
            if not allow_unsafe:
                raise PermissionError(
                    f"策略模块路径不在项目目录内：{resolved}。"
                    "如需加载外部路径，请设置 ALLOW_UNSAFE_STRATEGY_PATH=1。"
                )
            logger.warning("loading_strategy_outside_repo path=%s", resolved)

        module_name = f"strategy_module_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载策略模块: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# ---------------------------------------------------------------------------
# Strategy loading & validation
# ---------------------------------------------------------------------------

def load_strategy(
    module_path: str,
    *,
    engine: str = "v1",
    reload_module: bool = False,
) -> BaseStrategy:
    """
    动态加载策略模块

    支持:
    1. 模块路径: 'strategy_library.builtin.rsi30_rsi75d'
    2. 文件路径: './my_strategy.py'

    返回:
        BaseStrategy 实例
    """
    return load_strategy_descriptor(
        module_path,
        engine=engine,
        reload_module=reload_module,
    ).strategy


def load_strategy_descriptor(
    module_path: str,
    *,
    engine: str = "v1",
    reload_module: bool = False,
) -> LoadedStrategyDescriptor:
    """Load and parse one strategy module into a reusable descriptor."""
    if str(engine or "v1").strip().lower() != "v1":
        raise ValueError(f"不支持的策略引擎: {engine}")
    module: Any = import_strategy_module(module_path, reload_module=reload_module)
    strategy = _load_v1_strategy(module, module_path=module_path)
    contract = validate_strategy(strategy)
    return LoadedStrategyDescriptor.build(
        module_path=module_path,
        strategy=strategy,
        module=module,
        contract=contract,
    )


def _load_v1_strategy(module: ModuleType, *, module_path: str) -> BaseStrategy:
    strategy_cls = getattr(module, "Strategy", None)
    if isinstance(strategy_cls, type) and issubclass(strategy_cls, BaseStrategy):
        return strategy_cls()

    raise ValueError(
        f"策略模块格式不正确: {module_path}\n"
        "需要定义 class Strategy(BaseStrategy)"
    )


def validate_strategy(strategy: BaseStrategy) -> CompleteStrategySpec:
    """
    Validate the static strategy contract exposed by ``STRATEGY_META``.

    This loader intentionally does not execute strategy signal hooks. Behavioral
    validation stays in the dedicated strategy-validation flow so ordinary
    imports and worker warmup do not become heavier or more side-effect-prone.
    """
    meta = strategy.STRATEGY_META
    if not isinstance(meta, dict):
        raise ValueError("STRATEGY_META 必须为 dict。")
    contract_version = str(meta.get("strategy_contract_version") or "").strip()
    if contract_version != ADVANCED_CONTRACT_VERSION:
        mode = str(meta.get("execution_mode", "panel") or "panel").strip().casefold()
        label = "panel contract" if mode == "panel" else "strategy contract"
        raise ValueError(
            f"unsupported {label}: expected {ADVANCED_CONTRACT_VERSION!r}, "
            f"got {contract_version or '<missing>'!r}"
        )
    semantics = getattr(strategy, "STRATEGY_SEMANTICS", {})
    if semantics is not None and not isinstance(semantics, dict):
        raise ValueError("STRATEGY_SEMANTICS 必须为 dict。")
    description = getattr(strategy, "STRATEGY_DESCRIPTION", {})
    if description is not None and not isinstance(description, dict):
        raise ValueError("STRATEGY_DESCRIPTION 必须为 dict。")
    limits = getattr(strategy, "STRATEGY_LIMITS", {})
    if limits is not None and not isinstance(limits, dict):
        raise ValueError("STRATEGY_LIMITS 必须为 dict。")
    contract = get_complete_strategy_spec(module=strategy)
    from .authoring.requirements import resolve_strategy_requirements

    requirements = resolve_strategy_requirements(
        spec=contract.strategy,
        strategy=strategy,
        advanced_spec=contract.advanced,
    )
    if not requirements.ready:
        details = "; ".join(
            f"{issue.code}: {issue.message}"
            for issue in requirements.issues
            if issue.severity == "error"
        )
        raise ValueError(f"invalid strategy requirements: {details}")
    return contract


__all__ = [
    "import_strategy_module",
    "load_strategy",
    "load_strategy_descriptor",
    "validate_strategy",
]
