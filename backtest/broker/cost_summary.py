"""Auditable transaction-cost summary for the direct broker engine."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError

from ..costs import is_cost_enabled


def _sum_column(frame: pd.DataFrame, column: str) -> float:
    if not isinstance(frame, pd.DataFrame) or frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _side_count(frame: pd.DataFrame, side: str) -> int:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "side" not in frame.columns:
        return 0
    normalized = frame["side"].astype(str).str.strip().str.lower()
    return int(normalized.eq(side).sum())


def _strategy_totals(
    merged_df: pd.DataFrame,
    fills_df: pd.DataFrame,
) -> dict[str, float | int]:
    shadow_columns = {
        "strategy_buy_count",
        "strategy_sell_count",
        "strategy_commission",
        "strategy_stamp_tax",
        "strategy_slippage_cost",
        "strategy_fee_cost",
        "strategy_total_cost",
    }
    if shadow_columns.issubset(merged_df.columns):
        return {
            "buy_count": int(_sum_column(merged_df, "strategy_buy_count")),
            "sell_count": int(_sum_column(merged_df, "strategy_sell_count")),
            "commission": _sum_column(merged_df, "strategy_commission"),
            "stamp_tax": _sum_column(merged_df, "strategy_stamp_tax"),
            "slippage_cost": _sum_column(merged_df, "strategy_slippage_cost"),
            "fee_cost": _sum_column(merged_df, "strategy_fee_cost"),
            "total_cost": _sum_column(merged_df, "strategy_total_cost"),
        }
    fee_cost = _sum_column(fills_df, "total_cost")
    slippage_cost = _sum_column(fills_df, "slippage_cost")
    return {
        "buy_count": _side_count(fills_df, "buy"),
        "sell_count": _side_count(fills_df, "sell"),
        "commission": _sum_column(fills_df, "commission"),
        "stamp_tax": _sum_column(fills_df, "stamp_tax"),
        "slippage_cost": slippage_cost,
        "fee_cost": fee_cost,
        "total_cost": fee_cost + slippage_cost,
    }


def _risk_totals(
    merged_df: pd.DataFrame,
    risk_fills_df: pd.DataFrame,
) -> dict[str, float | int]:
    if {"risk_fee_cost", "risk_total_cost"}.issubset(merged_df.columns):
        return {
            "buy_count": int(_sum_column(merged_df, "risk_buy_count")),
            "sell_count": int(_sum_column(merged_df, "risk_sell_count")),
            "commission": _sum_column(merged_df, "risk_commission"),
            "stamp_tax": _sum_column(merged_df, "risk_stamp_tax"),
            "slippage_cost": _sum_column(merged_df, "risk_slippage_cost"),
            "fee_cost": _sum_column(merged_df, "risk_fee_cost"),
            "total_cost": _sum_column(merged_df, "risk_total_cost"),
        }
    fee_cost = _sum_column(risk_fills_df, "total_cost")
    slippage_cost = _sum_column(risk_fills_df, "slippage_cost")
    return {
        "buy_count": _side_count(risk_fills_df, "buy"),
        "sell_count": _side_count(risk_fills_df, "sell"),
        "commission": _sum_column(risk_fills_df, "commission"),
        "stamp_tax": _sum_column(risk_fills_df, "stamp_tax"),
        "slippage_cost": slippage_cost,
        "fee_cost": fee_cost,
        "total_cost": fee_cost + slippage_cost,
    }


def _terminal_net_value(merged_df: pd.DataFrame) -> float:
    if not isinstance(merged_df, pd.DataFrame) or merged_df.empty or "net_value" not in merged_df.columns:
        return math.nan
    work = merged_df
    if COL_TRADE_DATE in work.columns:
        work = work.sort_values(COL_TRADE_DATE, kind="mergesort")
    value = pd.to_numeric(work["net_value"], errors="coerce").iloc[-1]
    return float(value) if pd.notna(value) else math.nan


def build_broker_cost_summary(
    *,
    merged_df: pd.DataFrame,
    fills_df: pd.DataFrame,
    risk_fills_df: pd.DataFrame,
    initial_capital: float,
    cost_cfg: Any,
) -> pd.DataFrame | None:
    """Build one broker row using costs actually applied to the final ledger.

    The pre-cost terminal NAV holds executed quantities fixed and adds back all
    economic costs. This isolates cost drag without changing the trade path.
    """
    if not is_cost_enabled(cost_cfg):
        return None
    capital = float(initial_capital)
    if not math.isfinite(capital) or capital <= 0:
        raise DataValidationError("broker 成本摘要要求 initial_capital 为正有限数。")

    strategy = _strategy_totals(merged_df, fills_df)
    risk = _risk_totals(merged_df, risk_fills_df)
    total_fee = float(strategy["fee_cost"]) + float(risk["fee_cost"])
    total_slippage = float(strategy["slippage_cost"]) + float(risk["slippage_cost"])
    total_cost = total_fee + total_slippage
    post_net_value = _terminal_net_value(merged_df)
    pre_net_value = post_net_value + total_cost / capital
    cost_drag_pct = (
        (1.0 - post_net_value / pre_net_value) * 100.0
        if math.isfinite(pre_net_value) and pre_net_value
        else math.nan
    )

    row = {
        "portfolio": "broker",
        "cost_counterfactual_basis": "fixed_fill_quantities",
        "buy_cnt": int(strategy["buy_count"]) + int(risk["buy_count"]),
        "sell_cnt": int(strategy["sell_count"]) + int(risk["sell_count"]),
        "strategy_buy_cnt": int(strategy["buy_count"]),
        "strategy_sell_cnt": int(strategy["sell_count"]),
        "risk_buy_cnt": int(risk["buy_count"]),
        "risk_sell_cnt": int(risk["sell_count"]),
        "net_value_pre_cost": pre_net_value,
        "net_value_post_cost": post_net_value,
        "cost_drag_pct": cost_drag_pct,
        "cost_rate_sum": total_cost / capital,
        "commission": float(strategy["commission"]) + float(risk["commission"]),
        "stamp_tax": float(strategy["stamp_tax"]) + float(risk["stamp_tax"]),
        "slippage_cost": total_slippage,
        "fee_cost": total_fee,
        "total_cost": total_cost,
        "strategy_commission": float(strategy["commission"]),
        "strategy_stamp_tax": float(strategy["stamp_tax"]),
        "strategy_slippage_cost": float(strategy["slippage_cost"]),
        "strategy_fee_cost": float(strategy["fee_cost"]),
        "strategy_total_cost": float(strategy["total_cost"]),
        "strategy_cost_rate_sum": float(strategy["total_cost"]) / capital,
        "risk_commission": float(risk["commission"]),
        "risk_stamp_tax": float(risk["stamp_tax"]),
        "risk_slippage_cost": float(risk["slippage_cost"]),
        "risk_fee_cost": float(risk["fee_cost"]),
        "risk_total_cost": float(risk["total_cost"]),
        "risk_cost_rate_sum": float(risk["total_cost"]) / capital,
    }
    return pd.DataFrame([row])


__all__ = ["build_broker_cost_summary"]
