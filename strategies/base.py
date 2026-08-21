"""
策略基类定义
"""
from abc import ABC
from typing import Dict, Any


class BaseStrategy(ABC):
    """策略元数据基类；执行 hook 由具体运行模式定义。"""

    STRATEGY_META: Dict[str, Any] = {}
    STRATEGY_SEMANTICS: Dict[str, Any] = {}
    STRATEGY_DESCRIPTION: Dict[str, Any] = {}
    STRATEGY_LIMITS: Dict[str, Any] = {}

    def reset_runtime_state(self) -> None:
        """Optional per-run cache reset hook for strategy-local globals."""
        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"
