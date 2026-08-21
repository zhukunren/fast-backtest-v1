"""
Trading and portfolio config section loaders.
"""
from __future__ import annotations

import configparser

from .loader_utils import pick_bool, pick_float, pick_int, pick_text
from .models import FixedRebalanceConfig, PortfolioConfig, TradeConstraintConfig, TransactionCostConfig

_PORTFOLIO_SECTION = "portfolio"
_PORTFOLIO_SIGNAL_ALLOCATION_SECTION = "portfolio_signal_allocation"


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


def _pick_section_text(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    *,
    env: str,
    fallback: str,
    lower: bool = False,
) -> str:
    return pick_text(parser, section, option, env=env, fallback=fallback, default=fallback, lower=lower)


def _portfolio_signal_allocation_section(
    parser: configparser.ConfigParser,
    option: str,
) -> str:
    if parser.has_option(_PORTFOLIO_SIGNAL_ALLOCATION_SECTION, option):
        return _PORTFOLIO_SIGNAL_ALLOCATION_SECTION
    return _PORTFOLIO_SECTION


def build_transaction_cost(parser: configparser.ConfigParser) -> TransactionCostConfig:
    return TransactionCostConfig(
        enable=_pick_section_bool(parser, "transaction_cost", "enable", env="TC_ENABLE", fallback="0", default=False),
        commission_rate=_pick_section_float(
            parser,
            "transaction_cost",
            "commission_rate",
            env="TC_COMMISSION_RATE",
            fallback="0.0003",
            default=0.0003,
        ),
        min_commission=_pick_section_float(
            parser,
            "transaction_cost",
            "min_commission",
            env="TC_MIN_COMMISSION",
            fallback="5",
            default=5.0,
        ),
        stamp_duty_rate=_pick_section_float(
            parser,
            "transaction_cost",
            "stamp_duty_rate",
            env="TC_STAMP_DUTY_RATE",
            fallback="0.001",
            default=0.001,
        ),
        slippage_bps=_pick_section_float(
            parser,
            "transaction_cost",
            "slippage_bps",
            env="TC_SLIPPAGE_BPS",
            fallback="5",
            default=5.0,
        ),
        include_buy_commission=_pick_section_bool(
            parser,
            "transaction_cost",
            "include_buy_commission",
            env="TC_INCLUDE_BUY_COMMISSION",
            fallback="1",
            default=True,
        ),
        include_sell_commission=_pick_section_bool(
            parser,
            "transaction_cost",
            "include_sell_commission",
            env="TC_INCLUDE_SELL_COMMISSION",
            fallback="1",
            default=True,
        ),
    )


def build_portfolio(parser: configparser.ConfigParser) -> PortfolioConfig:
    return PortfolioConfig(
        engine=_pick_section_text(
            parser,
            _PORTFOLIO_SECTION,
            "engine",
            env="PORTFOLIO_ENGINE",
            fallback="slot_broker",
            lower=True,
        ),
        empty_reentry_mode=_pick_section_text(
            parser,
            _PORTFOLIO_SECTION,
            "empty_reentry_mode",
            env="PORTFOLIO_EMPTY_REENTRY_MODE",
            fallback="fill_all",
            lower=True,
        ),
        slot_assignment_mode=_pick_section_text(
            parser,
            _portfolio_signal_allocation_section(parser, "slot_assignment_mode"),
            "slot_assignment_mode",
            env="PORTFOLIO_SLOT_ASSIGNMENT_MODE",
            fallback="fixed",
            lower=True,
        ),
        enable_group_diversify=_pick_section_bool(
            parser,
            _portfolio_signal_allocation_section(parser, "enable_group_diversify"),
            "enable_group_diversify",
            env="PORTFOLIO_ENABLE_GROUP_DIVERSIFY",
            fallback="0",
            default=False,
        ),
        diversify_group_by=_pick_section_text(
            parser,
            _portfolio_signal_allocation_section(parser, "diversify_group_by"),
            "diversify_group_by",
            env="PORTFOLIO_DIVERSIFY_GROUP_BY",
            fallback="none",
        ),
        max_per_group_per_day=_pick_section_int(
            parser,
            _portfolio_signal_allocation_section(parser, "max_per_group_per_day"),
            "max_per_group_per_day",
            env="PORTFOLIO_MAX_PER_GROUP_PER_DAY",
            fallback="0",
            default=0,
        ),
        group_cap_fallback=_pick_section_text(
            parser,
            _portfolio_signal_allocation_section(parser, "group_cap_fallback"),
            "group_cap_fallback",
            env="PORTFOLIO_GROUP_CAP_FALLBACK",
            fallback="relax",
            lower=True,
        ),
        initial_capital=_pick_section_float(
            parser,
            _PORTFOLIO_SECTION,
            "initial_capital",
            env="PORTFOLIO_INITIAL_CAPITAL",
            fallback="1000000",
            default=1000000.0,
        ),
        position_size_method=_pick_section_text(
            parser,
            _PORTFOLIO_SECTION,
            "position_size_method",
            env="PORTFOLIO_POSITION_SIZE_METHOD",
            fallback="equal",
            lower=True,
        ),
        max_positions=_pick_section_int(
            parser,
            _PORTFOLIO_SECTION,
            "max_positions",
            env="PORTFOLIO_MAX_POSITIONS",
            fallback="0",
            default=0,
        ),
        max_single_position_pct=_pick_section_float(
            parser,
            _PORTFOLIO_SECTION,
            "max_single_position_pct",
            env="PORTFOLIO_MAX_SINGLE_POSITION_PCT",
            fallback="0.25",
            default=0.25,
        ),
        max_gross_exposure=_pick_section_float(
            parser,
            _PORTFOLIO_SECTION,
            "max_gross_exposure",
            env="PORTFOLIO_MAX_GROSS_EXPOSURE",
            fallback="1.0",
            default=1.0,
        ),
        cash_buffer_pct=_pick_section_float(
            parser,
            _PORTFOLIO_SECTION,
            "cash_buffer_pct",
            env="PORTFOLIO_CASH_BUFFER_PCT",
            fallback="0.05",
            default=0.05,
        ),
        allow_partial_fill=_pick_section_bool(
            parser,
            _PORTFOLIO_SECTION,
            "allow_partial_fill",
            env="PORTFOLIO_ALLOW_PARTIAL_FILL",
            fallback="1",
            default=True,
        ),
        lot_size=_pick_section_int(
            parser,
            _PORTFOLIO_SECTION,
            "lot_size",
            env="PORTFOLIO_LOT_SIZE",
            fallback="100",
            default=100,
        ),
        force_close_mode=_pick_section_text(
            parser,
            _PORTFOLIO_SECTION,
            "force_close_mode",
            env="PORTFOLIO_FORCE_CLOSE_MODE",
            fallback="mark_to_market",
            lower=True,
        ),
    )


def build_fixed_rebalance(parser: configparser.ConfigParser) -> FixedRebalanceConfig:
    section = "fixed_rebalance"
    return FixedRebalanceConfig(
        target_weights=_pick_section_text(
            parser,
            section,
            "target_weights",
            env="FIXED_REBALANCE_TARGET_WEIGHTS",
            fallback="",
        ),
        symbol_type=_pick_section_text(
            parser,
            section,
            "symbol_type",
            env="FIXED_REBALANCE_SYMBOL_TYPE",
            fallback="stock",
            lower=True,
        ),
        rebalance_frequency=_pick_section_text(
            parser,
            section,
            "rebalance_frequency",
            env="FIXED_REBALANCE_FREQUENCY",
            fallback="month_start",
            lower=True,
        ),
        rebalance_dates=_pick_section_text(
            parser,
            section,
            "rebalance_dates",
            env="FIXED_REBALANCE_DATES",
            fallback="",
        ),
        buy_price_col=_pick_section_text(
            parser,
            section,
            "buy_price_col",
            env="FIXED_REBALANCE_BUY_PRICE_COL",
            fallback="open",
            lower=True,
        ),
        sell_price_col=_pick_section_text(
            parser,
            section,
            "sell_price_col",
            env="FIXED_REBALANCE_SELL_PRICE_COL",
            fallback="open",
            lower=True,
        ),
        mark_price_col=_pick_section_text(
            parser,
            section,
            "mark_price_col",
            env="FIXED_REBALANCE_MARK_PRICE_COL",
            fallback="close",
            lower=True,
        ),
        normalize_weights=_pick_section_bool(
            parser,
            section,
            "normalize_weights",
            env="FIXED_REBALANCE_NORMALIZE_WEIGHTS",
            fallback="0",
            default=False,
        ),
    )


def build_trade_constraints(parser: configparser.ConfigParser) -> TradeConstraintConfig:
    return TradeConstraintConfig(
        enable_no_trade_day=_pick_section_bool(
            parser,
            "trade_constraints",
            "enable_no_trade_day",
            env="TC_NO_TRADE_DAY",
            fallback="1",
            default=True,
        ),
        enable_limit=_pick_section_bool(
            parser,
            "trade_constraints",
            "enable_limit",
            env="TC_LIMIT_ENABLE",
            fallback="1",
            default=True,
        ),
        limit_up_pct=_pick_section_float(
            parser,
            "trade_constraints",
            "limit_up_pct",
            env="TC_LIMIT_UP_PCT",
            fallback="0.098",
            default=0.098,
        ),
        limit_down_pct=_pick_section_float(
            parser,
            "trade_constraints",
            "limit_down_pct",
            env="TC_LIMIT_DOWN_PCT",
            fallback="0.098",
            default=0.098,
        ),
        enable_volume_limit=_pick_section_bool(
            parser,
            "trade_constraints",
            "enable_volume_limit",
            env="TC_VOLUME_LIMIT",
            fallback="0",
            default=False,
        ),
        max_participation_rate=_pick_section_float(
            parser,
            "trade_constraints",
            "max_participation_rate",
            env="TC_MAX_PARTICIPATION",
            fallback="0.05",
            default=0.05,
        ),
        require_next_bar=_pick_section_bool(
            parser,
            "trade_constraints",
            "require_next_bar",
            env="TC_REQUIRE_NEXT_BAR",
            fallback="1",
            default=True,
        ),
    )


__all__ = [
    "build_fixed_rebalance",
    "build_portfolio",
    "build_trade_constraints",
    "build_transaction_cost",
]
