from __future__ import annotations

import datetime as dt
import re
from typing import List, Mapping, Tuple

from core.portfolio_engine import normalize_portfolio_engine

VALID_FORCE_CLOSE_MODES = ("mark_to_market", "tradable_only", "force")
VALID_SIGNAL_AVAILABLE_AT = ("after_close", "before_close")
_FORCE_CLOSE_ALIASES = {
    "mark": "mark_to_market",
    "mtm": "mark_to_market",
    "none": "mark_to_market",
    "strict": "tradable_only",
    "tradable": "tradable_only",
}


def _parse_date(value: str) -> dt.date:
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):
        return dt.datetime.strptime(s, "%Y%m%d").date()
    return dt.datetime.fromisoformat(s).date()


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _validate_fixed_rebalance_config(cfg, errors: List[str]) -> None:
    fixed = getattr(cfg, "fixed_rebalance", None)
    if not str(getattr(fixed, "target_weights", "") or "").strip():
        errors.append("fixed_rebalance.target_weights is required when portfolio.engine=fixed_rebalance.")
    symbol_type = str(getattr(fixed, "symbol_type", "stock") or "stock").strip().lower()
    if symbol_type not in {"stock", "index", "etf"}:
        errors.append("fixed_rebalance.symbol_type invalid: expected stock/index/etf.")
    frequency = str(getattr(fixed, "rebalance_frequency", "month_start") or "month_start").strip().lower()
    custom_dates = str(getattr(fixed, "rebalance_dates", "") or "").strip()
    valid_freq = {
        "month_start", "monthly", "month",
        "quarter_start", "quarterly", "quarter", "q",
        "year_start", "yearly", "annual", "year", "y",
        "daily", "day", "every_day",
        "custom", "fixed_dates", "dates",
    }
    if frequency not in valid_freq:
        errors.append("fixed_rebalance.rebalance_frequency invalid.")
    if frequency in {"custom", "fixed_dates", "dates"} and not custom_dates:
        errors.append("fixed_rebalance.rebalance_dates is required for custom fixed rebalance dates.")
    for field_name in ("buy_price_col", "sell_price_col"):
        price_col = str(getattr(fixed, field_name, "") or "").strip().lower()
        if price_col not in {"open", "close"}:
            errors.append(
                f"fixed_rebalance.{field_name} invalid: expected open/close; "
                "high/low do not define a causal execution phase."
            )
    mark_price_col = str(getattr(fixed, "mark_price_col", "") or "").strip().lower()
    if mark_price_col not in {"open", "high", "low", "close"}:
        errors.append("fixed_rebalance.mark_price_col invalid: expected open/high/low/close.")


def _validate_portfolio_config(
    cfg,
    errors: List[str],
    warnings: List[str],
    *,
    engine_catalog: Mapping[str, object],
) -> None:
    try:
        engine = normalize_portfolio_engine(cfg.portfolio.engine)
        empty_reentry_mode = str(getattr(cfg.portfolio, "empty_reentry_mode", "fill_all") or "").strip().lower() or "fill_all"
        slot_assignment_mode = str(getattr(cfg.portfolio, "slot_assignment_mode", "fixed") or "").strip().lower() or "fixed"
        enable_group_diversify = _parse_bool(getattr(cfg.portfolio, "enable_group_diversify", False), default=False)
        diversify_group_by = str(getattr(cfg.portfolio, "diversify_group_by", "none") or "").strip()
        diversify_group_by_norm = diversify_group_by.lower()
        group_cap_fallback = str(getattr(cfg.portfolio, "group_cap_fallback", "relax") or "").strip().lower() or "relax"
        force_close_mode = (
            str(getattr(cfg.portfolio, "force_close_mode", "mark_to_market") or "")
            .strip()
            .lower()
            .replace("-", "_")
            or "mark_to_market"
        )
        force_close_mode = _FORCE_CLOSE_ALIASES.get(force_close_mode, force_close_mode)
        max_per_group_per_day = int(getattr(cfg.portfolio, "max_per_group_per_day", 0) or 0)
        if engine not in engine_catalog:
            expected = "/".join(engine_catalog)
            errors.append(f"portfolio.engine invalid: {engine!r} (expected {expected})")
        if empty_reentry_mode not in {"fill_all", "staggered"}:
            errors.append(
                "portfolio.empty_reentry_mode invalid: "
                f"{empty_reentry_mode!r} (expected fill_all/staggered)"
            )
        if slot_assignment_mode not in {"fixed", "round_robin"}:
            errors.append(
                "portfolio.slot_assignment_mode invalid: "
                f"{slot_assignment_mode!r} (expected fixed/round_robin)"
            )
        if group_cap_fallback not in {"relax", "strict"}:
            errors.append(
                "portfolio.group_cap_fallback invalid: "
                f"{group_cap_fallback!r} (expected relax/strict)"
            )
        if force_close_mode not in VALID_FORCE_CLOSE_MODES:
            expected = "/".join(VALID_FORCE_CLOSE_MODES)
            errors.append(f"portfolio.force_close_mode invalid: {force_close_mode!r} (expected {expected})")
        elif force_close_mode == "force":
            warnings.append(
                "portfolio.force_close_mode=force bypasses window-end tradability checks; "
                "use mark_to_market or tradable_only for strict A-share semantics."
            )
        if diversify_group_by_norm in {"code_prefix", "market"}:
            errors.append(
                "portfolio.diversify_group_by no longer supports "
                f"{diversify_group_by!r}; use a custom signal column name such as cluster_id."
            )
        if max_per_group_per_day < 0:
            errors.append("portfolio.max_per_group_per_day must be >= 0.")
        if enable_group_diversify and max_per_group_per_day > 0 and diversify_group_by_norm == "none":
            warnings.append(
                "portfolio.enable_group_diversify=1 but diversify_group_by=none; daily group cap is disabled."
            )
        if enable_group_diversify and max_per_group_per_day == 0:
            warnings.append(
                "portfolio.enable_group_diversify=1 but max_per_group_per_day=0; daily group cap is disabled."
            )
        if (not enable_group_diversify) and (
            diversify_group_by_norm != "none" or max_per_group_per_day > 0
        ):
            warnings.append(
                "portfolio.enable_group_diversify=0; diversify_group_by/max_per_group_per_day/group_cap_fallback are ignored."
            )
        if cfg.portfolio.initial_capital <= 0:
            errors.append("portfolio.initial_capital must be positive.")
        if not (0 <= cfg.portfolio.cash_buffer_pct < 1):
            errors.append("portfolio.cash_buffer_pct must be in [0,1).")
        if not (0 < cfg.portfolio.max_gross_exposure <= 1.0):
            errors.append("portfolio.max_gross_exposure must be in (0,1].")
        if not (0 < cfg.portfolio.max_single_position_pct <= 1.0):
            errors.append("portfolio.max_single_position_pct must be in (0,1].")
        if engine == "fixed_rebalance":
            _validate_fixed_rebalance_config(cfg, errors)
    except Exception as e:
        errors.append(f"portfolio config invalid: {e}")


def _validate_backtest_dates(cfg, errors: List[str]) -> None:
    try:
        start = _parse_date(cfg.backtest.start_time)
        end = _parse_date(cfg.backtest.end_time)
        if start > end:
            errors.append(
                f"start_time > end_time ({cfg.backtest.start_time} > {cfg.backtest.end_time})"
            )
    except Exception as e:
        errors.append(f"invalid backtest date range: {e}")


def _append_runtime_warnings(cfg, errors: List[str], warnings: List[str]) -> None:
    token = str(getattr(getattr(cfg, "tushare", None), "token", "") or "").strip()
    if not token:
        errors.append(
            "tushare.token is required for runtime execution; "
            "the official trade calendar has no local fallback."
        )

    if cfg.run.plot_net_value:
        try:
            import plotly  # noqa: F401
        except Exception:
            warnings.append("plot_net_value=1 but plotly is not installed; plotting will fail.")

    driver = str(cfg.mysql.driver or "").strip()
    if any(ch in driver for ch in ("#", ";")) or (" " in driver):
        warnings.append(
            f"mysql.driver contains whitespace or comment markers: {driver!r}; "
            "ensure it is a clean value like 'pymysql' or 'mysqldb'."
        )

    if cfg.run.build_topn_trade_segments_report:
        warnings.append(
            "build_topn_trade_segments_report=1 adds per-signal TopN segment extraction and slows down runs."
        )
    try:
        candidate_factor = float(getattr(cfg.run, "topn_candidate_factor", 1.0))
        if candidate_factor < 1.0:
            errors.append("run.topn_candidate_factor must be >= 1.0.")
    except Exception as e:
        errors.append(f"run.topn_candidate_factor invalid: {e}")


def _validate_transaction_cost(cfg, warnings: List[str], errors: List[str]) -> None:
    try:
        tc = cfg.transaction_cost
        if tc.commission_rate < 0 or tc.stamp_duty_rate < 0 or tc.slippage_bps < 0:
            errors.append("transaction_cost rates must be non-negative.")
        if tc.min_commission < 0:
            errors.append("transaction_cost min_commission must be non-negative.")
        engine = normalize_portfolio_engine(getattr(cfg.portfolio, "engine", "legacy"))
        if tc.enable and engine == "legacy" and tc.min_commission > 0:
            warnings.append(
                "transaction_cost enabled with engine=legacy; min_commission will be approximated."
            )
    except Exception as e:
        warnings.append(f"transaction_cost config invalid: {e}")


def _validate_trade_constraints(cfg, warnings: List[str], errors: List[str]) -> None:
    try:
        constraints = cfg.trade_constraints
        if constraints.limit_up_pct < 0 or constraints.limit_down_pct < 0:
            errors.append("trade_constraints limit pct must be non-negative.")
        if not 0 <= constraints.max_participation_rate <= 1:
            warnings.append("trade_constraints max_participation_rate should be in [0,1].")
    except Exception as e:
        warnings.append(f"trade_constraints config invalid: {e}")


def _validate_execution_policy(cfg, errors: List[str]) -> None:
    try:
        from core.execution_policy import get_execution_policy

        get_execution_policy(cfg)
        signal_available_at = (
            str(getattr(cfg.run, "signal_available_at", "after_close") or "")
            .strip()
            .lower()
            .replace("-", "_")
            or "after_close"
        )
        if signal_available_at not in VALID_SIGNAL_AVAILABLE_AT:
            expected = "/".join(VALID_SIGNAL_AVAILABLE_AT)
            errors.append(
                f"run.signal_available_at invalid: {signal_available_at!r} (expected {expected})"
            )
        elif signal_available_at != "before_close":
            buy_timing = str(getattr(cfg.run, "exec_buy_timing", "")).strip().lower()
            sell_timing = str(getattr(cfg.run, "exec_sell_timing", "")).strip().lower()
            if buy_timing == "signal_close" or sell_timing == "signal_close":
                errors.append(
                    "exec_buy_timing/exec_sell_timing=signal_close requires "
                    "run.signal_available_at=before_close; after-close signals must use "
                    "next_open on both sides to avoid lookahead."
                )
    except Exception as e:
        errors.append(f"invalid execution policy: {e}")


def validate_config(
    cfg,
    *,
    engine_catalog: Mapping[str, object],
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    _validate_backtest_dates(cfg, errors)
    _append_runtime_warnings(cfg, errors, warnings)
    _validate_transaction_cost(cfg, warnings, errors)
    _validate_portfolio_config(
        cfg,
        errors,
        warnings,
        engine_catalog=engine_catalog,
    )
    _validate_trade_constraints(cfg, warnings, errors)
    _validate_execution_policy(cfg, errors)
    return errors, warnings
