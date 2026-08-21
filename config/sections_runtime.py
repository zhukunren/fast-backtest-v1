"""
Runtime-oriented config section loaders.
"""
from __future__ import annotations

import configparser
from pathlib import Path

from .loader_utils import pick_bool, pick_env_aliases, pick_float, pick_int, pick_text, split_csv_items
from .models import BacktestConfig, PanelConfig, PathConfig, PerformanceConfig, RunConfig, StrategyConfig
from .parsing import parse_int, resolve_path


def _pick_section_text(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    default: str | None = None,
    lower: bool = False,
) -> str:
    return pick_text(
        parser,
        section,
        option,
        env=env,
        fallback=fallback,
        default=fallback if default is None else default,
        lower=lower,
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
) -> int:
    return pick_int(parser, section, option, env=env, fallback=fallback, default=default)


def _pick_section_float(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    default: float,
) -> float:
    return pick_float(parser, section, option, env=env, fallback=fallback, default=default)


def _pick_path_text(
    parser: configparser.ConfigParser,
    option: str,
    *,
    env: str,
    fallback: str,
) -> str:
    return _pick_section_text(parser, "paths", option, env=env, fallback=fallback)


def build_paths(parser: configparser.ConfigParser, *, repo_root: Path) -> PathConfig:
    data_root_raw = _pick_path_text(parser, "data_root", env="DATA_ROOT", fallback=".")
    data_root = resolve_path(repo_root, data_root_raw or ".")

    signal_dir_raw = _pick_path_text(
        parser, "signal_data", env="SIGNAL_DATA_DIR", fallback="output/pipeline/signal_data"
    )
    stock_dir_raw = _pick_path_text(
        parser, "stock_data", env="STOCK_DATA_DIR", fallback="output/pipeline/stock_data"
    )
    trade_dir_raw = _pick_path_text(
        parser, "trade_data", env="TRADE_DATA_DIR", fallback="output/pipeline/trade_data"
    )
    return_dir_raw = _pick_path_text(
        parser, "return_date", env="RETURN_DATE_DIR", fallback="output/pipeline/return_date"
    )
    return_wr_raw = _pick_path_text(
        parser,
        "return_date_with_returns",
        env="RETURN_DATE_WITH_RETURNS_DIR",
        fallback="output/pipeline/return_date_with_returns",
    )
    net_value_raw = _pick_path_text(
        parser,
        "net_value_unique_date",
        env="NET_VALUE_UNIQUE_DATE_DIR",
        fallback="output/pipeline/net_value_unique_date",
    )

    return PathConfig(
        repo_root=repo_root,
        data_root=data_root,
        signal_data=resolve_path(data_root, signal_dir_raw),
        stock_data=resolve_path(data_root, stock_dir_raw),
        trade_data=resolve_path(data_root, trade_dir_raw),
        return_date=resolve_path(data_root, return_dir_raw),
        return_date_with_returns=resolve_path(data_root, return_wr_raw),
        net_value_unique_date=resolve_path(data_root, net_value_raw),
    )


def build_backtest(parser: configparser.ConfigParser) -> BacktestConfig:
    default_stock_code = _pick_section_text(
        parser,
        "backtest",
        "default_stock_code",
        env="DEFAULT_STOCK_CODE",
        fallback="688766.SH",
    )
    start_time = _pick_section_text(parser, "backtest", "start_time", env="START_TIME", fallback="20240101")
    end_time = _pick_section_text(parser, "backtest", "end_time", env="END_TIME", fallback="20251231")
    offset_raw = pick_env_aliases(
        "OFFSET_DAYS",
        "LOOKBACK_DAYS",
        fallback=parser.get("backtest", "offset_days", fallback="0"),
    )
    offset_days = parse_int(str(offset_raw).strip() or "0", 0)

    prefixes = split_csv_items(parser.get("backtest", "universe_prefixes", fallback="300,301,688"))
    exclude_bj = _pick_section_bool(parser, "backtest", "exclude_bj", env="EXCLUDE_BJ", fallback="1", default=True)
    exclude_st = _pick_section_bool(parser, "backtest", "exclude_st", env="EXCLUDE_ST", fallback="1", default=True)
    benchmark_code = _pick_section_text(
        parser,
        "backtest",
        "benchmark_code",
        env="BENCHMARK_CODE",
        fallback="399006.SZ",
    )
    benchmark_name = _pick_section_text(
        parser,
        "backtest",
        "benchmark_name",
        env="BENCHMARK_NAME",
        fallback="创业板指",
    )
    benchmark_source_codes = split_csv_items(
        pick_env_aliases(
            "BENCHMARK_SOURCE_CODES",
            fallback=parser.get("backtest", "benchmark_source_codes", fallback="399006.SZ,399006.SH"),
        )
    )

    return BacktestConfig(
        default_stock_code=default_stock_code,
        start_time=start_time,
        end_time=end_time,
        offset_days=offset_days,
        universe_prefixes=prefixes,
        exclude_bj=exclude_bj,
        exclude_st=exclude_st,
        benchmark_code=benchmark_code,
        benchmark_name=benchmark_name,
        benchmark_source_codes=benchmark_source_codes,
    )


def build_run(parser: configparser.ConfigParser) -> RunConfig:
    run_mode = _pick_section_text(parser, "run", "run_mode", env="RUN_MODE", fallback="curve", lower=True)
    if run_mode in {"signal", "single_stock"}:
        run_mode = "single"
    if run_mode not in {"curve", "assess", "single"}:
        run_mode = "curve"
    return RunConfig(
        run_mode=run_mode,
        clear_outputs=_pick_section_bool(parser, "run", "clear_outputs", env="CLEAR_OUTPUTS", fallback="0", default=False),
        clear_all_tables=_pick_section_bool(
            parser, "run", "clear_all_tables", env="CLEAR_ALL_TABLES", fallback="0", default=False
        ),
        plot_net_value=_pick_section_bool(parser, "run", "plot_net_value", env="PLOT_NET_VALUE", fallback="1", default=True),
        compute_metrics=_pick_section_bool(
            parser, "run", "compute_metrics", env="COMPUTE_METRICS", fallback="1", default=True
        ),
        build_topn_trade_segments_report=_pick_section_bool(
            parser,
            "run",
            "build_topn_trade_segments_report",
            env="BUILD_TOPN_TRADE_SEGMENTS_REPORT",
            fallback="0",
            default=False,
        ),
        profile_timing=_pick_section_bool(
            parser, "run", "profile_timing", env="PROFILE_TIMING", fallback="0", default=False
        ),
        quiet=_pick_section_bool(parser, "run", "quiet", env="RUN_QUIET", fallback="0", default=False),
        precheck_tradable_topn=_pick_section_bool(
            parser, "run", "precheck_tradable_topn", env="PRECHECK_TRADABLE_TOPN", fallback="0", default=False
        ),
        topn_candidate_factor=_pick_section_float(
            parser, "run", "topn_candidate_factor", env="TOPN_CANDIDATE_FACTOR", fallback="1.0", default=1.0
        ),
        signal_read_fail_ratio=_pick_section_float(
            parser, "run", "signal_read_fail_ratio", env="SIGNAL_READ_FAIL_RATIO", fallback="0.0", default=0.0
        ),
        signal_available_at=_pick_section_text(
            parser,
            "run",
            "signal_available_at",
            env="SIGNAL_AVAILABLE_AT",
            fallback="after_close",
            lower=True,
        ),
        exec_buy_timing=_pick_section_text(
            parser, "run", "exec_buy_timing", env="EXEC_BUY_TIMING", fallback="next_open", lower=True
        ),
        exec_sell_timing=_pick_section_text(
            parser, "run", "exec_sell_timing", env="EXEC_SELL_TIMING", fallback="next_open", lower=True
        ),
        exec_shift_t1=_pick_section_bool(parser, "run", "exec_shift_t1", env="EXEC_SHIFT_T1", fallback="1", default=True),
        exec_allow_intraday=_pick_section_bool(
            parser, "run", "exec_allow_intraday", env="EXEC_ALLOW_INTRADAY", fallback="0", default=False
        ),
        check_trade_calendar=_pick_section_bool(
            parser, "run", "check_trade_calendar", env="CHECK_TRADE_CALENDAR", fallback="0", default=False
        ),
        trade_calendar_missing_warn_ratio=_pick_section_float(
            parser,
            "run",
            "trade_calendar_missing_warn_ratio",
            env="TRADE_CALENDAR_MISSING_WARN_RATIO",
            fallback="0.05",
            default=0.05,
        ),
        pydantic_validate=_pick_section_bool(
            parser, "run", "pydantic_validate", env="PYDANTIC_VALIDATE", fallback="0", default=False
        ),
        pydantic_validate_sample=_pick_section_int(
            parser, "run", "pydantic_validate_sample", env="PYDANTIC_VALIDATE_SAMPLE", fallback="200", default=200
        ),
        allow_missing_stock_data=_pick_section_bool(
            parser, "run", "allow_missing_stock_data", env="ALLOW_MISSING_STOCK_DATA", fallback="0", default=False
        ),
    )


def build_performance(parser: configparser.ConfigParser) -> PerformanceConfig:
    return PerformanceConfig(
        debug_stock_list=_pick_section_bool(
            parser, "performance", "debug_stock_list", env="DEBUG_STOCK_LIST", fallback="0", default=False
        ),
        state_machine_jit=_pick_section_bool(
            parser, "performance", "state_machine_jit", env="STATE_MACHINE_JIT", fallback="1", default=True
        ),
        net_value_jit=_pick_section_bool(
            parser, "performance", "net_value_jit", env="NET_VALUE_JIT", fallback="1", default=True
        ),
    )


def _normalize_panel_source(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return "parquet"
    if value != "parquet":
        raise ValueError("panel.source only supports parquet")
    return value


def _normalize_incomplete_ohlcv_policy(raw: str) -> str:
    value = str(raw or "drop_warn").strip().casefold()
    if value not in {"drop_warn", "reject"}:
        raise ValueError("panel.incomplete_ohlcv_policy must be one of: drop_warn, reject")
    return value


def build_panel(parser: configparser.ConfigParser) -> PanelConfig:
    source = _normalize_panel_source(
        _pick_section_text(
            parser,
            "panel",
            "source",
            env="PRICE_PANEL_SOURCE",
            fallback="parquet",
            lower=True,
        )
    )
    path = _pick_section_text(
        parser,
        "panel",
        "path",
        env="PRICE_PANEL_PATH",
            fallback="data/panel_cache/stock_daily.parquet",
            default="data/panel_cache/stock_daily.parquet",
    )
    incomplete_ohlcv_policy = _normalize_incomplete_ohlcv_policy(
        _pick_section_text(
            parser,
            "panel",
            "incomplete_ohlcv_policy",
            env="PRICE_PANEL_INCOMPLETE_OHLCV_POLICY",
            fallback="drop_warn",
            default="drop_warn",
            lower=True,
        )
    )
    return PanelConfig(
        source=source,
        path=path,
        incomplete_ohlcv_policy=incomplete_ohlcv_policy,
    )


def build_strategy(parser: configparser.ConfigParser) -> StrategyConfig:
    return StrategyConfig(
        module=_pick_section_text(
            parser,
            "strategy",
            "module",
            env="STRATEGY_MODULE",
            fallback="",
            default="strategy_library.builtin.breakout_20",
        )
    )


__all__ = [
    "build_backtest",
    "build_panel",
    "build_paths",
    "build_performance",
    "build_run",
    "build_strategy",
]
