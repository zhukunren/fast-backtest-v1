"""
MySQL support helpers for data layer.
"""
from __future__ import annotations

import atexit
import configparser
from dataclasses import dataclass
import logging
import re
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from urllib.parse import quote_plus

from core.exceptions import ConfigurationError, DataSourceError, DataValidationError


class TableNotFoundError(Exception):
    """MySQL table missing error."""


logger = logging.getLogger(__name__)
_MYSQL_ENGINE_CACHE: dict[str, object] = {}
_MYSQL_ENGINE_LOCK = RLock()
_SQL_TABLE_IDENT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_SQL_COLUMN_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MYSQL_URL_PASSWORD_RE = re.compile(r"((?:mysql(?:\+[A-Za-z0-9_]+)?://)[^:\s/@]+:)([^@\s]+)(@)")
ConfigLoader = Callable[[], object]


@dataclass(frozen=True)
class MysqlConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    charset: str
    stock_db: str
    index_db: str
    ths_index_db: str
    ths_index_member_db: str
    driver: str
    daily_fields: tuple[str, ...] = ()

    def sqlalchemy_url(self, db_name: str) -> str:
        driver = (self.driver or "").strip() or "pymysql"
        user_q = quote_plus(self.user or "")
        pwd = self.password or ""
        pwd_q = quote_plus(pwd) if pwd else ""
        auth = f"{user_q}:{pwd_q}" if pwd else f"{user_q}"
        return f"mysql+{driver}://{auth}@{self.host}:{self.port}/{db_name}?charset={self.charset}"


def _resolve_cfg(*, cfg, config_loader: ConfigLoader | None = None):
    if cfg is not None:
        return cfg
    if config_loader is not None:
        return config_loader()
    raise ConfigurationError("data.mysql_support 需要显式传入 cfg。")


def _dispose_mysql_engine(engine: object | None) -> None:
    dispose_fn = getattr(engine, "dispose", None)
    if not callable(dispose_fn):
        return
    try:
        dispose_fn()
    except Exception:
        pass


def clear_mysql_engine_cache(*, dispose: bool = False) -> int:
    """
    Clear cached SQLAlchemy engines and return the number of removed entries.

    The default only drops references. Passing ``dispose=True`` also calls
    ``engine.dispose()`` when available, which is useful for explicit shutdown
    hooks but too heavy for the common runtime-reset path.
    """
    with _MYSQL_ENGINE_LOCK:
        engines = list(_MYSQL_ENGINE_CACHE.values())
        removed = len(_MYSQL_ENGINE_CACHE)
        _MYSQL_ENGINE_CACHE.clear()
    if dispose:
        seen: set[int] = set()
        for engine in engines:
            identity = id(engine)
            if identity in seen:
                continue
            seen.add(identity)
            _dispose_mysql_engine(engine)
    return removed


def shutdown_mysql_engine_cache() -> int:
    """Dispose all process-owned MySQL pools during explicit shutdown."""
    return clear_mysql_engine_cache(dispose=True)


atexit.register(shutdown_mysql_engine_cache)


def mysql_engine_cache_snapshot() -> dict[str, Any]:
    """Return a diagnostic snapshot of cached MySQL engines without connecting."""
    with _MYSQL_ENGINE_LOCK:
        entries = list(_MYSQL_ENGINE_CACHE.items())
    engines = [engine for _, engine in entries]
    return {
        "size": len(entries),
        "urls": sorted(
            (_redact_mysql_url_passwords(url) for url, _ in entries),
            key=str.casefold,
        ),
        "pool_policy": {
            "pool_size": 1,
            "max_overflow": 0,
            "pool_pre_ping": True,
        },
        "dispose_supported_count": sum(1 for engine in engines if callable(getattr(engine, "dispose", None))),
    }


def mysql_config_diagnostics(
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
) -> dict[str, Any]:
    """Return a redacted MySQL config diagnostic payload without opening a connection."""
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    mysql = cfg.mysql
    return {
        "host": str(getattr(mysql, "host", "")),
        "port": int(getattr(mysql, "port", 0) or 0),
        "user": str(getattr(mysql, "user", "")),
        "driver": str(getattr(mysql, "driver", "")),
        "allow_local_auth_fallback": bool(getattr(mysql, "allow_local_auth_fallback", False)),
        "databases": {
            "stock_db": str(getattr(mysql, "stock_db", "")),
            "index_db": str(getattr(mysql, "index_db", "")),
            "ths_index_db": str(getattr(mysql, "ths_index_db", "")),
            "ths_index_member_db": str(getattr(mysql, "ths_index_member_db", "")),
        },
        "pool_policy": mysql_engine_cache_snapshot()["pool_policy"],
        "cache": mysql_engine_cache_snapshot(),
    }


def _quote_sql_identifier(identifier: str, *, kind: str, allow_dot: bool = False) -> str:
    """
    Validate dynamic SQL identifiers with allow-list and quote safely.
    """
    raw = str(identifier or "").strip()
    if raw == "":
        raise DataValidationError(f"SQL {kind} 不能为空")
    pattern = _SQL_TABLE_IDENT_RE if allow_dot else _SQL_COLUMN_IDENT_RE
    if not pattern.fullmatch(raw):
        raise DataValidationError(f"非法 SQL {kind}: {identifier!r}")
    return f"`{raw}`"


def _redact_mysql_url_passwords(text: object) -> str:
    return _MYSQL_URL_PASSWORD_RE.sub(r"\1***\3", str(text))


def _format_sa_error(exc) -> str:
    orig = getattr(exc, "__dict__", {}).get("orig")
    if orig is None:
        return _redact_mysql_url_passwords(exc)
    orig_text = str(orig)
    return _redact_mysql_url_passwords(orig_text or str(exc))


def _is_access_denied(err_msg: str) -> bool:
    return "access denied" in err_msg.lower()


def _mysql_credentials_match(primary_cfg: object, fallback_cfg: object) -> bool:
    return (
        str(getattr(primary_cfg, "user", "")) == str(getattr(fallback_cfg, "user", ""))
        and str(getattr(primary_cfg, "password", "")) == str(getattr(fallback_cfg, "password", ""))
    )


def _load_mysql_cfg_from_files() -> MysqlConnectionConfig | None:
    """
    Load MySQL config directly from config.ini/config.local.ini.
    Used as fallback when env-overridden credentials are denied.
    """
    config_dir = Path(__file__).resolve().parent.parent / "config"
    repo_root = config_dir.parent
    parser = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
    )
    parser.read(
        [
            str(config_dir / "config.ini"),
            str(config_dir / "config.local.ini"),
            str(repo_root / "config.ini"),
            str(repo_root / "config.local.ini"),
        ],
        encoding="utf-8",
    )
    if not parser.has_section("mysql"):
        return None

    def _get(name: str, default: str) -> str:
        return parser.get("mysql", name, fallback=default).strip() or default

    host = _get("host", "localhost")
    port = int(_get("port", "3306") or "3306")
    user = _get("user", "root")
    password = parser.get("mysql", "password", fallback="")
    charset = _get("charset", "utf8mb4")
    stock_db = _get("stock_db", "stock_data")
    index_db = _get("index_db", "index_data")
    ths_index_db = _get("ths_index_db", "ths_index_daily")
    ths_index_member_db = _get("ths_index_member_db", "ths_index_member")
    driver = _get("driver", "pymysql")

    return MysqlConnectionConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        charset=charset,
        stock_db=stock_db,
        index_db=index_db,
        ths_index_db=ths_index_db,
        ths_index_member_db=ths_index_member_db,
        driver=driver,
    )


def _get_mysql_engine(
    db_name: str,
    *,
    cfg,
    config_loader: ConfigLoader | None = None,
):
    """
    Return a cached SQLAlchemy engine for ``db_name``.

    The helper intentionally keeps a conservative single-connection pool
    (``pool_size=1`` / ``max_overflow=0``). This data path favors predictable
    process-level reuse and avoids silently multiplying concurrent MySQL
    connections when batch runners fan out. Authentication failures can use
    the local-config fallback path only when the resolved config explicitly
    enables it; other SQLAlchemy errors fail fast with contextual db/url
    information.
    """
    cfg = _resolve_cfg(cfg=cfg, config_loader=config_loader)
    url = cfg.mysql.sqlalchemy_url(db_name)
    safe_url = _redact_mysql_url_passwords(url)

    with _MYSQL_ENGINE_LOCK:
        cached_engine = _MYSQL_ENGINE_CACHE.get(url)
    if cached_engine is not None:
        return cached_engine

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError
    except Exception as exc:
        raise DataSourceError(f"SQLAlchemy 未安装，无法连接 MySQL: {exc}") from exc

    def _build_engine(mysql_cfg: object):
        return create_engine(
            mysql_cfg.sqlalchemy_url(db_name),
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
        )

    def _probe_engine(engine_obj):
        with engine_obj.connect() as conn:
            conn.execute(text("SELECT 1"))

    with _MYSQL_ENGINE_LOCK:
        cached_engine = _MYSQL_ENGINE_CACHE.get(url)
        if cached_engine is not None:
            return cached_engine

        engine = None
        try:
            engine = _build_engine(cfg.mysql)
            _probe_engine(engine)
            _MYSQL_ENGINE_CACHE[url] = engine
            return engine
        except SQLAlchemyError as exc:
            _dispose_mysql_engine(engine)
            err_msg = _format_sa_error(exc)
            if not _is_access_denied(err_msg):
                raise DataSourceError(
                    f"创建/验证 SQLAlchemy engine 失败: db={db_name!r} url={safe_url!r} err={err_msg}"
                ) from exc

            if not bool(getattr(cfg.mysql, "allow_local_auth_fallback", False)):
                raise DataSourceError(
                    "SQL 登录被拒绝，且 mysql.allow_local_auth_fallback 未启用: "
                    f"db={db_name!r} user={cfg.mysql.user!r} err={err_msg}"
                ) from exc

            fallback_cfg = _load_mysql_cfg_from_files()
            if fallback_cfg is None:
                raise DataSourceError(
                    f"SQL 登录被拒绝，且无法加载本地配置进行回退: "
                    f"db={db_name!r} user={cfg.mysql.user!r}"
                ) from exc
            if _mysql_credentials_match(cfg.mysql, fallback_cfg):
                raise DataSourceError(
                    f"SQL 登录被拒绝: db={db_name!r} user={cfg.mysql.user!r} err={err_msg}"
                ) from exc

            fallback_url = fallback_cfg.sqlalchemy_url(db_name)
            cached_fallback_engine = _MYSQL_ENGINE_CACHE.get(fallback_url)
            if cached_fallback_engine is not None:
                logger.warning(
                    "mysql_login_failed_fallback_used db=%s primary_url=%s fallback_url=%s",
                    db_name,
                    safe_url,
                    _redact_mysql_url_passwords(fallback_url),
                )
                _MYSQL_ENGINE_CACHE[url] = cached_fallback_engine
                return cached_fallback_engine

            fallback_engine = None
            try:
                fallback_engine = _build_engine(fallback_cfg)
                _probe_engine(fallback_engine)
                logger.warning(
                    "mysql_login_failed_fallback_used db=%s primary_url=%s fallback_url=%s",
                    db_name,
                    safe_url,
                    _redact_mysql_url_passwords(fallback_url),
                )
                _MYSQL_ENGINE_CACHE[url] = fallback_engine
                _MYSQL_ENGINE_CACHE[fallback_url] = fallback_engine
                return fallback_engine
            except SQLAlchemyError as exc2:
                _dispose_mysql_engine(fallback_engine)
                err_msg2 = _format_sa_error(exc2)
                raise DataSourceError(
                    "主凭据与回退凭据均认证失败: "
                    f"primary_user={cfg.mysql.user!r} fallback_user={fallback_cfg.user!r} "
                    f"db={db_name!r} err_primary={err_msg} err_fallback={err_msg2}"
                ) from exc2


__all__ = [
    "MysqlConnectionConfig",
    "TableNotFoundError",
    "clear_mysql_engine_cache",
    "mysql_config_diagnostics",
    "mysql_engine_cache_snapshot",
    "shutdown_mysql_engine_cache",
    "_get_mysql_engine",
    "_is_access_denied",
    "_load_mysql_cfg_from_files",
    "_mysql_credentials_match",
    "_quote_sql_identifier",
]
