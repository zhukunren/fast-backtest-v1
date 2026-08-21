"""
价格数据异常 - 细化错误类型以支持精确捕获
"""
from __future__ import annotations


class BacktestError(Exception):
    """回测系统基础异常。"""


class DataValidationError(BacktestError, ValueError):
    """数据验证失败。"""


class PriceDataError(BacktestError, FileNotFoundError):
    """价格数据相关错误（基类）。"""


class PriceDataNotFoundError(PriceDataError):
    """价格数据文件不存在。"""


class PriceDataReadError(PriceDataError):
    """价格数据读取失败（文件损坏、格式错误等）。"""


class PriceCacheError(PriceDataError):
    """价格缓存操作失败（L2 缓存读写错误等）。"""


class DataSourceError(BacktestError, RuntimeError):
    """数据源/连接/查询异常。"""


class SignalExtractionError(BacktestError):
    """信号提取失败。"""


class TradeSegmentError(BacktestError):
    """交易段提取失败。"""


class ConfigurationError(BacktestError, ValueError):
    """配置错误。"""


class StrategyError(BacktestError):
    """策略加载/执行相关错误。"""


class ValidationError(BacktestError, ValueError):
    """Pydantic 校验失败。"""

    def __init__(self, message: str, *, errors: list | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []
