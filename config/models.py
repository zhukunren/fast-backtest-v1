"""
Configuration datamodels.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


@dataclass(frozen=True)
class PathConfig:
    repo_root: Path
    data_root: Path
    signal_data: Path
    stock_data: Path
    trade_data: Path
    return_date: Path
    return_date_with_returns: Path
    net_value_unique_date: Path


@dataclass(frozen=True)
class BacktestConfig:
    default_stock_code: str
    start_time: str
    end_time: str
    offset_days: int
    universe_prefixes: tuple[str, ...]
    exclude_bj: bool
    exclude_st: bool
    benchmark_code: str
    benchmark_name: str
    benchmark_source_codes: tuple[str, ...]


@dataclass(frozen=True)
class RunConfig:
    run_mode: str
    clear_outputs: bool
    clear_all_tables: bool
    plot_net_value: bool
    compute_metrics: bool
    build_topn_trade_segments_report: bool
    profile_timing: bool
    quiet: bool
    precheck_tradable_topn: bool
    topn_candidate_factor: float
    signal_read_fail_ratio: float
    signal_available_at: str
    exec_buy_timing: str
    exec_sell_timing: str
    exec_shift_t1: bool
    exec_allow_intraday: bool
    check_trade_calendar: bool
    trade_calendar_missing_warn_ratio: float
    pydantic_validate: bool
    pydantic_validate_sample: int
    allow_missing_stock_data: bool


@dataclass(frozen=True)
class PerformanceConfig:
    debug_stock_list: bool
    state_machine_jit: bool
    net_value_jit: bool


@dataclass(frozen=True)
class PanelConfig:
    source: str
    path: str
    incomplete_ohlcv_policy: str = "drop_warn"


@dataclass(frozen=True)
class StrategyConfig:
    module: str


@dataclass(frozen=True)
class MysqlConfig:
    host: str
    port: int
    user: str
    password: str
    charset: str
    stock_db: str
    index_db: str
    etf_db: str
    ths_index_db: str
    ths_index_member_db: str
    driver: str
    daily_fields: tuple[str, ...] = ()
    allow_local_auth_fallback: bool = False

    def sqlalchemy_url(self, db_name: str) -> str:
        driver = (self.driver or "").strip() or "pymysql"
        user_q = quote_plus(self.user or "")
        pwd = self.password or ""
        pwd_q = quote_plus(pwd) if pwd else ""
        auth = f"{user_q}:{pwd_q}" if pwd else f"{user_q}"
        return f"mysql+{driver}://{auth}@{self.host}:{self.port}/{db_name}?charset={self.charset}"


@dataclass(frozen=True)
class TushareConfig:
    token: str
    exchange: str
    trade_cal_start: str
    trade_cal_end: str


@dataclass(frozen=True)
class DiagnosticsConfig:
    enabled: bool = True
    multi_start_enabled: bool = False
    multi_start_step_days: int = 20
    multi_start_window_days: int = 250
    multi_start_max_starts: int = 8
    robust_lambda: float = 0.5
    robust_gamma: float = 1.0
    max_top1_profit_share: float = 1.0
    max_trade_return_hhi: float = 1.0
    min_multi_start_robust_score: float = -1e9


@dataclass(frozen=True)
class TransactionCostConfig:
    enable: bool
    commission_rate: float
    min_commission: float
    stamp_duty_rate: float
    slippage_bps: float
    include_buy_commission: bool
    include_sell_commission: bool


@dataclass(frozen=True)
class PortfolioConfig:
    engine: str
    empty_reentry_mode: str
    slot_assignment_mode: str
    enable_group_diversify: bool
    diversify_group_by: str
    max_per_group_per_day: int
    group_cap_fallback: str
    initial_capital: float
    position_size_method: str
    max_positions: int
    max_single_position_pct: float
    max_gross_exposure: float
    cash_buffer_pct: float
    allow_partial_fill: bool
    lot_size: int
    force_close_mode: str = "mark_to_market"


@dataclass(frozen=True)
class FixedRebalanceConfig:
    target_weights: str
    symbol_type: str = "stock"
    rebalance_frequency: str = "month_start"
    rebalance_dates: str = ""
    buy_price_col: str = "open"
    sell_price_col: str = "open"
    mark_price_col: str = "close"
    normalize_weights: bool = False


@dataclass(frozen=True)
class TradeConstraintConfig:
    enable_no_trade_day: bool
    enable_limit: bool
    limit_up_pct: float
    limit_down_pct: float
    enable_volume_limit: bool
    max_participation_rate: float
    require_next_bar: bool


@dataclass(frozen=True)
class AppConfig:
    paths: PathConfig
    backtest: BacktestConfig
    run: RunConfig
    performance: PerformanceConfig
    panel: PanelConfig
    strategy: StrategyConfig
    mysql: MysqlConfig
    tushare: TushareConfig
    diagnostics: DiagnosticsConfig
    transaction_cost: TransactionCostConfig
    portfolio: PortfolioConfig
    fixed_rebalance: FixedRebalanceConfig
    trade_constraints: TradeConstraintConfig


__all__ = [
    "AppConfig",
    "BacktestConfig",
    "DiagnosticsConfig",
    "FixedRebalanceConfig",
    "MysqlConfig",
    "PanelConfig",
    "PathConfig",
    "PerformanceConfig",
    "PortfolioConfig",
    "RunConfig",
    "StrategyConfig",
    "TradeConstraintConfig",
    "TransactionCostConfig",
    "TushareConfig",
]
