"""
配置管理入口（Facade）:
- 配置值模型: config.models
- 基础解析工具: config.parsing
- 各 section 组装: config.sections
"""
from __future__ import annotations

import configparser
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from collections.abc import Iterator
from typing import Callable, Optional, TypeVar

from core.exceptions import ConfigurationError

from .config_cache import ConfigCache, compute_env_fingerprint, get_global_config_cache
from .models import AppConfig, BacktestConfig
from .sections import (
    build_backtest,
    build_diagnostics,
    build_fixed_rebalance,
    build_mysql,
    build_panel,
    build_paths,
    build_performance,
    build_portfolio,
    build_run,
    build_strategy,
    build_trade_constraints,
    build_transaction_cost,
    build_tushare,
)


_SESSION_CONFIG: ContextVar[AppConfig | None] = ContextVar(
    "session_config",
    default=None,
)
_SectionT = TypeVar("_SectionT")


def _resolve_config_roots() -> tuple[Path, Path]:
    config_dir = Path(__file__).resolve().parent
    return config_dir, config_dir.parent


def _iter_config_candidate_paths(config_dir: Path, repo_root: Path) -> tuple[Path, ...]:
    return (
        config_dir / "config.ini",
        config_dir / "config.local.ini",
        repo_root / "config.ini",
        repo_root / "config.local.ini",
    )


def _build_parser(config_dir: Path, repo_root: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    parser.read([str(path) for path in _iter_config_candidate_paths(config_dir, repo_root)], encoding="utf-8")
    return parser


def _iter_dynamic_config_env_keys(parser: configparser.ConfigParser) -> tuple[str, ...]:
    keys: set[str] = set()
    for section, option, fallback in (
        ("mysql", "password_env", "MYSQL_PASSWORD"),
        ("tushare", "token_env", "TUSHARE_TOKEN"),
        ("tushare", "token_env_alt", "TS_TOKEN"),
    ):
        raw = str(parser.get(section, option, fallback=fallback) or "").strip()
        if raw:
            keys.add(raw)
    return tuple(sorted(keys, key=str.casefold))


def _config_env_fingerprint() -> str:
    config_dir, repo_root = _resolve_config_roots()
    parser = _build_parser(config_dir, repo_root)
    return str(compute_env_fingerprint(_iter_dynamic_config_env_keys(parser)))


def _build_section(
    name: str,
    fn: Callable[..., _SectionT],
    *args: object,
    **kwargs: object,
) -> _SectionT:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise ConfigurationError(f"load_config.{name} 解析失败: {exc}") from exc


def _iter_section_specs(
    *,
    repo_root: Path,
    backtest: BacktestConfig,
) -> tuple[tuple[str, Callable[..., object], dict[str, object]], ...]:
    return (
        ("paths", build_paths, {"repo_root": repo_root}),
        ("run", build_run, {}),
        ("performance", build_performance, {}),
        ("panel", build_panel, {}),
        ("strategy", build_strategy, {}),
        ("mysql", build_mysql, {}),
        ("tushare", build_tushare, {"backtest": backtest}),
        ("diagnostics", build_diagnostics, {}),
        ("transaction_cost", build_transaction_cost, {}),
        ("portfolio", build_portfolio, {}),
        ("fixed_rebalance", build_fixed_rebalance, {}),
        ("trade_constraints", build_trade_constraints, {}),
    )


def _load_sections(parser: configparser.ConfigParser, *, repo_root: Path) -> dict[str, object]:
    backtest = _build_section("backtest", build_backtest, parser)
    sections = {"backtest": backtest}
    for name, fn, kwargs in _iter_section_specs(repo_root=repo_root, backtest=backtest):
        sections[name] = _build_section(name, fn, parser, **kwargs)
    return sections


def load_config() -> AppConfig:
    try:
        config_dir, repo_root = _resolve_config_roots()
        parser = _build_parser(config_dir, repo_root)
        return AppConfig(**_load_sections(parser, repo_root=repo_root))
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"load_config 失败: {exc}") from exc


def get_config(*, reload: bool = False, cache: ConfigCache | None = None) -> AppConfig:
    """
    获取配置（自动检测环境变量变更）

    Args:
        reload: 强制重新加载配置
        cache: 自定义缓存实例（默认使用全局缓存）

    Returns:
        AppConfig 实例

    Note:
        - 环境变量变更会自动触发重新加载
        - 多进程环境下每个进程有独立缓存
        - 测试时建议使用独立的 cache 实例
    """
    cache_state = cache if cache is not None else get_global_config_cache()
    return cache_state.get(load_config, reload=reload, fingerprint_loader=_config_env_fingerprint)


def get_session_config() -> AppConfig:
    """Return the config bound to the current run, falling back to the process cache."""
    config = _SESSION_CONFIG.get()
    return config if config is not None else get_config()


@contextmanager
def session_config_scope(config: AppConfig | None) -> Iterator[AppConfig | None]:
    """Bind an immutable config to the current thread/task for strategy callbacks."""
    if config is None:
        yield _SESSION_CONFIG.get()
        return
    token = _SESSION_CONFIG.set(config)
    try:
        yield config
    finally:
        _SESSION_CONFIG.reset(token)


def clear_config_cache(*, cache: ConfigCache | None = None) -> None:
    """清空配置缓存（用于测试或显式重置）"""
    cache_state = cache if cache is not None else get_global_config_cache()
    cache_state.clear()


def replace_config_cache(
    config: Optional[AppConfig],
    *,
    cache: ConfigCache | None = None,
) -> Optional[AppConfig]:
    """替换配置缓存并返回之前的值（用于测试）"""
    cache_state = cache if cache is not None else get_global_config_cache()
    return cache_state.replace(config, fingerprint_loader=_config_env_fingerprint)


@contextmanager
def config_cache_scope(config: AppConfig, *, cache: ConfigCache | None = None) -> Iterator[None]:
    """临时替换配置缓存的上下文管理器（用于测试）"""
    cache_state = cache if cache is not None else get_global_config_cache()
    previous = cache_state.replace(config, fingerprint_loader=_config_env_fingerprint)
    try:
        yield
    finally:
        cache_state.replace(previous, fingerprint_loader=_config_env_fingerprint)


def create_config_cache() -> ConfigCache:
    """创建独立的配置缓存实例（用于测试隔离）"""
    from .config_cache import create_config_cache as _create
    return _create()
