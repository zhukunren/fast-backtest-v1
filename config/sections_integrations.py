"""
Integration-oriented config section loaders.
"""
from __future__ import annotations

import configparser
import os

from .loader_utils import pick_bool, pick_env_aliases, pick_float, pick_int, pick_text, split_csv_items
from .models import BacktestConfig, DiagnosticsConfig, MysqlConfig, TushareConfig


def _pick_section_text(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    fallback: str,
    env: str | None = None,
    default: str | None = None,
) -> str:
    return pick_text(
        parser,
        section,
        option,
        env=env,
        fallback=fallback,
        default=fallback if default is None else default,
    )


def _pick_section_bool(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    default: bool,
) -> bool:
    return pick_bool(parser, section, option, env=env, fallback=fallback, default=default)


def _pick_section_int(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    default: int,
    minimum: int | None = None,
) -> int:
    return pick_int(parser, section, option, env=env, fallback=fallback, default=default, minimum=minimum)


def _pick_section_float(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    return pick_float(
        parser,
        section,
        option,
        env=env,
        fallback=fallback,
        default=default,
        minimum=minimum,
        maximum=maximum,
    )


def build_mysql(parser: configparser.ConfigParser) -> MysqlConfig:
    password_env = _pick_section_text(
        parser,
        "mysql",
        "password_env",
        fallback="MYSQL_PASSWORD",
        default="MYSQL_PASSWORD",
    )
    mysql_password = _pick_section_text(parser, "mysql", "password", env=password_env, fallback="", default="")

    return MysqlConfig(
        host=_pick_section_text(parser, "mysql", "host", env="MYSQL_HOST", fallback="localhost"),
        port=_pick_section_int(parser, "mysql", "port", env="MYSQL_PORT", fallback="3306", default=3306),
        user=_pick_section_text(parser, "mysql", "user", env="MYSQL_USER", fallback="root"),
        password=mysql_password,
        charset=_pick_section_text(parser, "mysql", "charset", env="MYSQL_CHARSET", fallback="utf8mb4"),
        stock_db=_pick_section_text(parser, "mysql", "stock_db", fallback="stock_data"),
        index_db=_pick_section_text(parser, "mysql", "index_db", fallback="index_data"),
        etf_db=_pick_section_text(parser, "mysql", "etf_db", env="MYSQL_ETF_DB", fallback="etf_data"),
        ths_index_db=_pick_section_text(
            parser, "mysql", "ths_index_db", env="THS_INDEX_DB", fallback="ths_index_daily"
        ),
        ths_index_member_db=_pick_section_text(
            parser,
            "mysql",
            "ths_index_member_db",
            env="THS_INDEX_MEMBER_DB",
            fallback="ths_index_member",
        ),
        driver=_pick_section_text(parser, "mysql", "driver", env="MYSQL_DRIVER", fallback="pymysql"),
        daily_fields=split_csv_items(
            pick_env_aliases(
                "MYSQL_DAILY_FIELDS",
                "DAILY_FIELDS",
                fallback=parser.get("mysql", "daily_fields", fallback=""),
            )
        ),
        allow_local_auth_fallback=_pick_section_bool(
            parser,
            "mysql",
            "allow_local_auth_fallback",
            env="MYSQL_ALLOW_LOCAL_AUTH_FALLBACK",
            fallback="0",
            default=False,
        ),
    )


def build_tushare(parser: configparser.ConfigParser, *, backtest: BacktestConfig) -> TushareConfig:
    token_cfg = _pick_section_text(parser, "tushare", "token", fallback="", default="")
    token_env = _pick_section_text(parser, "tushare", "token_env", fallback="TUSHARE_TOKEN")
    token_env_alt = _pick_section_text(parser, "tushare", "token_env_alt", fallback="TS_TOKEN")
    token = ""
    for env_name in (token_env, token_env_alt):
        env_token = str(os.environ.get(env_name) or "").strip()
        if env_token:
            token = env_token
            break
    token = token or token_cfg

    exchange = _pick_section_text(parser, "tushare", "exchange", env="TUSHARE_EXCHANGE", fallback="", default="")
    cal_start = _pick_section_text(
        parser,
        "tushare",
        "trade_cal_start",
        env="TRADE_CAL_START",
        fallback="",
        default=backtest.start_time,
    )
    cal_end = _pick_section_text(
        parser,
        "tushare",
        "trade_cal_end",
        env="TRADE_CAL_END",
        fallback="",
        default=backtest.end_time,
    )

    return TushareConfig(
        token=token,
        exchange=exchange,
        trade_cal_start=cal_start,
        trade_cal_end=cal_end,
    )


def build_diagnostics(parser: configparser.ConfigParser) -> DiagnosticsConfig:
    return DiagnosticsConfig(
        enabled=_pick_section_bool(parser, "diagnostics", "enabled", env="DIAGNOSTICS_ENABLED", fallback="1", default=True),
        multi_start_enabled=_pick_section_bool(
            parser,
            "diagnostics",
            "multi_start_enabled",
            env="DIAGNOSTICS_MULTI_START_ENABLED",
            fallback="0",
            default=False,
        ),
        multi_start_step_days=_pick_section_int(
            parser,
            "diagnostics",
            "multi_start_step_days",
            env="DIAGNOSTICS_MULTI_START_STEP_DAYS",
            fallback="20",
            default=20,
            minimum=1,
        ),
        multi_start_window_days=_pick_section_int(
            parser,
            "diagnostics",
            "multi_start_window_days",
            env="DIAGNOSTICS_MULTI_START_WINDOW_DAYS",
            fallback="250",
            default=250,
            minimum=2,
        ),
        multi_start_max_starts=_pick_section_int(
            parser,
            "diagnostics",
            "multi_start_max_starts",
            env="DIAGNOSTICS_MULTI_START_MAX_STARTS",
            fallback="8",
            default=8,
            minimum=1,
        ),
        robust_lambda=_pick_section_float(
            parser, "diagnostics", "robust_lambda", env="DIAGNOSTICS_ROBUST_LAMBDA", fallback="0.5", default=0.5
        ),
        robust_gamma=_pick_section_float(
            parser, "diagnostics", "robust_gamma", env="DIAGNOSTICS_ROBUST_GAMMA", fallback="1.0", default=1.0
        ),
        max_top1_profit_share=_pick_section_float(
            parser,
            "diagnostics",
            "max_top1_profit_share",
            env="DIAGNOSTICS_MAX_TOP1_PROFIT_SHARE",
            fallback="1.0",
            default=1.0,
            minimum=0.0,
            maximum=1.0,
        ),
        max_trade_return_hhi=_pick_section_float(
            parser,
            "diagnostics",
            "max_trade_return_hhi",
            env="DIAGNOSTICS_MAX_TRADE_RETURN_HHI",
            fallback="1.0",
            default=1.0,
            minimum=0.0,
        ),
        min_multi_start_robust_score=_pick_section_float(
            parser,
            "diagnostics",
            "min_multi_start_robust_score",
            env="DIAGNOSTICS_MIN_MULTI_START_ROBUST_SCORE",
            fallback="-1000000000",
            default=-1e9,
        ),
    )


__all__ = [
    "build_diagnostics",
    "build_mysql",
    "build_tushare",
]
