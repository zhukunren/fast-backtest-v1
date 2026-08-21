"""
配置缓存模块 - 线程安全且支持环境变量变更检测
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from typing import Optional

from .models import AppConfig


TRACKED_CONFIG_ENV_KEYS: tuple[str, ...] = (
    "ALLOW_MISSING_STOCK_DATA",
    "BENCHMARK_CODE",
    "BENCHMARK_NAME",
    "BENCHMARK_SOURCE_CODES",
    "BUILD_TOPN_TRADE_SEGMENTS_REPORT",
    "CHECK_TRADE_CALENDAR",
    "CLEAR_ALL_TABLES",
    "CLEAR_OUTPUTS",
    "COMPUTE_METRICS",
    "DATA_MANIFEST_HASH_MAX_MB",
    "DATA_MANIFEST_MAX_FILES",
    "DATA_MANIFEST_ROWS_MAX_MB",
    "DATA_MANIFEST_WRITE",
    "DATA_ROOT",
    "DAILY_FIELDS",
    "DEBUG_STOCK_LIST",
    "DEFAULT_STOCK_CODE",
    "DIAGNOSTICS_ENABLED",
    "DIAGNOSTICS_MAX_TOP1_PROFIT_SHARE",
    "DIAGNOSTICS_MAX_TRADE_RETURN_HHI",
    "DIAGNOSTICS_MIN_MULTI_START_ROBUST_SCORE",
    "DIAGNOSTICS_MULTI_START_ENABLED",
    "DIAGNOSTICS_MULTI_START_MAX_STARTS",
    "DIAGNOSTICS_MULTI_START_STEP_DAYS",
    "DIAGNOSTICS_MULTI_START_WINDOW_DAYS",
    "DIAGNOSTICS_ROBUST_GAMMA",
    "DIAGNOSTICS_ROBUST_LAMBDA",
    "END_TIME",
    "EXCLUDE_BJ",
    "EXCLUDE_ST",
    "EXEC_ALLOW_INTRADAY",
    "EXEC_BUY_TIMING",
    "EXEC_SELL_TIMING",
    "EXEC_SHIFT_T1",
    "FIXED_REBALANCE_BUY_PRICE_COL",
    "FIXED_REBALANCE_DATES",
    "FIXED_REBALANCE_FREQUENCY",
    "FIXED_REBALANCE_MARK_PRICE_COL",
    "FIXED_REBALANCE_NORMALIZE_WEIGHTS",
    "FIXED_REBALANCE_SELL_PRICE_COL",
    "FIXED_REBALANCE_SYMBOL_TYPE",
    "FIXED_REBALANCE_TARGET_WEIGHTS",
    "JOBLIB_BACKEND",
    "LOCAL_DATA_DIR",
    "LOOKBACK_DAYS",
    "MYSQL_ALLOW_LOCAL_AUTH_FALLBACK",
    "MYSQL_CHARSET",
    "MYSQL_DRIVER",
    "MYSQL_DAILY_FIELDS",
    "MYSQL_ETF_DB",
    "MYSQL_HOST",
    "MYSQL_PASSWORD",
    "MYSQL_PORT",
    "MYSQL_USER",
    "NET_VALUE_JIT",
    "NET_VALUE_UNIQUE_DATE_DIR",
    "PLOT_NET_VALUE",
    "OFFSET_DAYS",
    "PORTFOLIO_ALLOW_PARTIAL_FILL",
    "PORTFOLIO_CASH_BUFFER_PCT",
    "PORTFOLIO_DIVERSIFY_GROUP_BY",
    "PORTFOLIO_EMPTY_REENTRY_MODE",
    "PORTFOLIO_ENABLE_GROUP_DIVERSIFY",
    "PORTFOLIO_ENGINE",
    "PORTFOLIO_FORCE_CLOSE_MODE",
    "PORTFOLIO_GROUP_CAP_FALLBACK",
    "PORTFOLIO_INITIAL_CAPITAL",
    "PORTFOLIO_LOT_SIZE",
    "PORTFOLIO_MAX_GROSS_EXPOSURE",
    "PORTFOLIO_MAX_PER_GROUP_PER_DAY",
    "PORTFOLIO_MAX_POSITIONS",
    "PORTFOLIO_MAX_SINGLE_POSITION_PCT",
    "PORTFOLIO_POSITION_SIZE_METHOD",
    "PORTFOLIO_SLOT_ASSIGNMENT_MODE",
    "PRECHECK_TRADABLE_TOPN",
    "PRICE_PANEL_INCOMPLETE_OHLCV_POLICY",
    "PRICE_PANEL_PATH",
    "PRICE_PANEL_SOURCE",
    "PRICE_L2_CACHE",
    "PRICE_L2_CACHE_DIR",
    "PRICE_L2_CACHE_WRITE",
    "PRICE_L2_MYSQL_CACHE_DIR",
    "PRICE_L2_MYSQL_MODE",
    "PROFILE_TIMING",
    "PYDANTIC_VALIDATE",
    "PYDANTIC_VALIDATE_SAMPLE",
    "RETURN_DATE_DIR",
    "RETURN_DATE_WITH_RETURNS_DIR",
    "RUN_META_DATA_MANIFEST",
    "RUN_META_GIT",
    "RUN_META_LEVEL",
    "RUN_META_PRICE_L2_CACHE",
    "RUN_MODE",
    "RUN_QUIET",
    "SAMPLE_DATA_DIR",
    "SIGNAL_DATA_DIR",
    "SIGNAL_AVAILABLE_AT",
    "SIGNAL_READ_FAIL_RATIO",
    "START_TIME",
    "STATE_MACHINE_JIT",
    "STOCK_DATA_DIR",
    "STRATEGY_MODULE",
    "TC_COMMISSION_RATE",
    "TC_ENABLE",
    "TC_INCLUDE_BUY_COMMISSION",
    "TC_INCLUDE_SELL_COMMISSION",
    "TC_LIMIT_DOWN_PCT",
    "TC_LIMIT_ENABLE",
    "TC_LIMIT_UP_PCT",
    "TC_MAX_PARTICIPATION",
    "TC_MIN_COMMISSION",
    "TC_NO_TRADE_DAY",
    "TC_REQUIRE_NEXT_BAR",
    "TC_SLIPPAGE_BPS",
    "TC_STAMP_DUTY_RATE",
    "TC_VOLUME_LIMIT",
    "THS_INDEX_DB",
    "THS_INDEX_MEMBER_DB",
    "TOPN_CANDIDATE_FACTOR",
    "TRADE_CAL_END",
    "TRADE_CAL_START",
    "TRADE_CALENDAR_MISSING_WARN_RATIO",
    "TRADE_DATA_DIR",
    "TS_TOKEN",
    "TUSHARE_EXCHANGE",
    "TUSHARE_TOKEN",
)


def compute_env_fingerprint(extra_keys: tuple[str, ...] = ()) -> str:
    """计算关键环境变量的指纹，用于检测配置变更。"""
    dynamic_keys = {key for key in extra_keys if key in os.environ}
    env_keys = sorted({*TRACKED_CONFIG_ENV_KEYS, *dynamic_keys}, key=str.casefold)
    values = [f"{key}={os.environ.get(key, '')}" for key in env_keys]
    content = "|".join(values)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _compute_env_fingerprint() -> str:
    """兼容旧调用：仅使用静态环境变量集合。"""
    return compute_env_fingerprint()


class ConfigCache:
    """
    配置缓存 - 支持环境变量变更自动失效

    特性：
    1. 线程安全（单进程内）
    2. 环境变量变更自动检测
    3. 显式 reload 支持
    4. 测试友好（支持 replace/clear）
    """

    def __init__(self) -> None:
        self._cache: Optional[AppConfig] = None
        self._env_fingerprint: Optional[str] = None
        self._lock = threading.RLock()

    def get(
        self,
        loader: Callable[[], AppConfig],
        *,
        reload: bool = False,
        fingerprint_loader: Callable[[], str] | None = None,
    ) -> AppConfig:
        """
        获取配置，自动检测环境变量变更

        Args:
            loader: 配置加载函数
            reload: 强制重新加载
            fingerprint_loader: 环境变量指纹加载函数

        Returns:
            AppConfig 实例
        """
        with self._lock:
            current_fingerprint = (fingerprint_loader or _compute_env_fingerprint)()

            # 三种情况需要重新加载：
            # 1. 显式要求 reload
            # 2. 缓存为空
            # 3. 环境变量指纹变化
            if reload or self._cache is None or self._env_fingerprint != current_fingerprint:
                self._cache = loader()
                self._env_fingerprint = current_fingerprint

            return self._cache

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache = None
            self._env_fingerprint = None

    def peek(self) -> Optional[AppConfig]:
        """查看当前缓存值（不触发加载）"""
        with self._lock:
            return self._cache

    def replace(
        self,
        config: Optional[AppConfig],
        *,
        fingerprint_loader: Callable[[], str] | None = None,
    ) -> Optional[AppConfig]:
        """
        替换缓存值（用于测试）

        Args:
            config: 新的配置对象
            fingerprint_loader: 环境变量指纹加载函数

        Returns:
            之前的配置对象
        """
        with self._lock:
            previous = self._cache
            self._cache = config
            # 更新指纹，避免下次 get 时被环境变量检测覆盖
            if config is not None:
                self._env_fingerprint = (fingerprint_loader or _compute_env_fingerprint)()
            else:
                self._env_fingerprint = None
            return previous

    def is_stale(self, *, fingerprint_loader: Callable[[], str] | None = None) -> bool:
        """检查缓存是否过期（环境变量已变更）"""
        with self._lock:
            if self._cache is None:
                return True
            current_fingerprint = (fingerprint_loader or _compute_env_fingerprint)()
            return self._env_fingerprint != current_fingerprint


# 全局单例（每个进程独立）
_CONFIG_CACHE_STATE = ConfigCache()


def create_config_cache() -> ConfigCache:
    """创建独立的配置缓存实例（用于测试或显式隔离）"""
    return ConfigCache()


def get_global_config_cache() -> ConfigCache:
    """获取全局配置缓存实例"""
    return _CONFIG_CACHE_STATE


__all__ = [
    "ConfigCache",
    "TRACKED_CONFIG_ENV_KEYS",
    "compute_env_fingerprint",
    "create_config_cache",
    "get_global_config_cache",
]
