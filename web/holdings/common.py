"""Shared value and accounting primitives for daily-holdings projections."""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.result import CurvePayload, RunResult
from core.artifact_schema import DAILY_HOLDINGS_SCHEMA_VERSION, artifact_schema_fields
from web.export_common import (
    _clean_float,
    _clean_int,
    _normalize_holding_code_text,
)


def net_value_frame(result: RunResult) -> pd.DataFrame | None:
    payload = result.payload
    return payload.equity_curve if isinstance(payload, CurvePayload) else None


def normalize_holding_value(
    *,
    position_value_abs: Any,
    equity: Any,
    effective_ratio: Any,
    shares: Any,
    close: Any,
) -> float | None:
    effective_num = _clean_float(effective_ratio)
    equity_num = _clean_float(equity)
    if effective_num is not None and equity_num is not None:
        return round(equity_num * effective_num, 2)

    position_value_num = _clean_float(position_value_abs)
    if position_value_num is not None:
        return round(position_value_num, 2)

    shares_num = _clean_int(shares)
    close_num = _clean_float(close)
    if shares_num is not None and close_num is not None:
        return round(float(shares_num) * float(close_num), 2)
    return None


def append_daily_holding_row(
    *,
    rows: list[dict[str, Any]],
    slot: str,
    code: Any,
    name: Any = None,
    direction: Any = None,
    close: Any = None,
    shares: Any = None,
    original_shares: Any = None,
    shares_scaled: Any = False,
    holding_value: Any = None,
    cost_price: Any = None,
    cost_value: Any = None,
    unrealized_pnl: Any = None,
    unrealized_pnl_pct: Any = None,
    net_value: Any = None,
    utilization_rate: Any = None,
    effective_position_ratio: Any = None,
) -> None:
    code_text = str(code or "").strip()
    if not code_text:
        return
    holding_value_clean = _clean_float(holding_value, digits=2)
    shares_clean = _clean_int(shares)
    original_shares_clean = _clean_int(original_shares)
    close_num = _clean_float(close)
    close_clean = _clean_float(close, digits=2)
    if (
        (shares_clean is None or int(shares_clean) <= 0)
        and holding_value_clean is not None
        and float(holding_value_clean) > 0.0
        and close_num is not None
        and abs(float(close_num)) > 1e-12
    ):
        scaled_shares = round(float(holding_value_clean) / float(close_num))
        if scaled_shares > 0:
            shares_clean = int(scaled_shares)
    if holding_value_clean is not None and abs(float(holding_value_clean)) <= 1e-12:
        if shares_clean is None or int(shares_clean) <= 0:
            return
    rows.append(
        {
            "slot": str(slot or ""),
            "code": code_text,
            "name": str(name or "").strip(),
            "dir": str(direction or "").strip(),
            "close": close_clean,
            "shares": shares_clean,
            "original_shares": original_shares_clean,
            "shares_scaled": bool(shares_scaled),
            "holding_value": holding_value_clean,
            "cost_price": _clean_float(cost_price, digits=4),
            "cost_value": _clean_float(cost_value, digits=2),
            "unrealized_pnl": _clean_float(unrealized_pnl, digits=2),
            "unrealized_pnl_pct": _clean_float(unrealized_pnl_pct, digits=6),
            "nv": _clean_float(net_value, digits=4),
            "utilization_rate": _clean_float(utilization_rate, digits=4),
            "effective_position_ratio": _clean_float(effective_position_ratio, digits=4),
        }
    )


def normalize_trade_date_key(value: Any) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).normalize().strftime("%Y-%m-%d")


def resolve_holding_dates(
    *,
    artifacts: RunResult,
    holdings_by_date: dict[str, list[dict[str, Any]]],
    summary_by_date: dict[str, dict[str, Any]],
) -> list[str]:
    dates: list[str] = []
    seen: set[str] = set()
    merged_df = net_value_frame(artifacts)
    if isinstance(merged_df, pd.DataFrame) and "trade_date" in merged_df.columns:
        for value in pd.to_datetime(merged_df["trade_date"], errors="coerce"):
            if pd.isna(value):
                continue
            date_key = pd.Timestamp(value).normalize().strftime("%Y-%m-%d")
            if date_key in seen:
                continue
            seen.add(date_key)
            dates.append(date_key)
    for date_key in sorted(set(summary_by_date) | set(holdings_by_date)):
        if date_key in seen:
            continue
        seen.add(date_key)
        dates.append(date_key)
    return dates


def build_daily_summary_row(
    *,
    net_value: Any = None,
    asset: Any = None,
    cash: Any = None,
    position_value: Any = None,
    cum_pnl: Any = None,
    daily_pnl: Any = None,
    realized_pnl: Any = None,
    unrealized_pnl: Any = None,
    daily_return: Any = None,
    holding_count: Any = None,
) -> dict[str, Any]:
    return {
        "net_value": _clean_float(net_value, digits=4),
        "asset": _clean_float(asset, digits=2),
        "cash": _clean_float(cash, digits=2),
        "position_value": _clean_float(position_value, digits=2),
        "cum_pnl": _clean_float(cum_pnl, digits=2),
        "daily_pnl": _clean_float(daily_pnl, digits=2),
        "realized_pnl": _clean_float(realized_pnl, digits=2),
        "unrealized_pnl": _clean_float(unrealized_pnl, digits=2),
        "daily_return": _clean_float(daily_return, digits=6),
        "holding_count": _clean_int(holding_count),
    }


def sum_holding_value(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        value = _clean_float(row.get("holding_value"))
        if value is None:
            continue
        total += float(value)
        found = True
    if not found:
        return None
    return round(total, 2)


def sum_row_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        value = _clean_float(row.get(field))
        if value is None:
            continue
        total += float(value)
        found = True
    if not found:
        return None
    return round(total, 2)


def average_holding_net_value(rows: list[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for row in rows:
        value = _clean_float(row.get("nv"))
        if value is None:
            continue
        values.append(float(value))
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def holding_state_key(slot: Any, code: Any) -> tuple[str, str]:
    return str(slot or ""), str(code or "")


def _lookup_unique_state_by_code(
    state_map: dict[tuple[str, str], dict[str, Any]],
    code: Any,
) -> dict[str, Any]:
    code_text = _normalize_holding_code_text(code)
    if not code_text or not isinstance(state_map, dict) or not state_map:
        return {}
    matches = [
        value
        for (_slot_name, stock_code), value in state_map.items()
        if stock_code == code_text
    ]
    if len(matches) != 1:
        return {}
    return matches[0] if isinstance(matches[0], dict) else {}


def enrich_holdings_rows(
    *,
    holdings_by_date: dict[str, list[dict[str, Any]]],
    ordered_dates: list[str],
    states_by_date: dict[str, dict[tuple[str, str], dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    enriched: dict[str, list[dict[str, Any]]] = {}
    for date_key in ordered_dates:
        rows = holdings_by_date.get(date_key, [])
        if not rows:
            continue
        day_rows: list[dict[str, Any]] = []
        state_map = states_by_date.get(date_key, {})
        for row in rows:
            item = dict(row)
            key = holding_state_key(item.get("slot"), item.get("code"))
            state = (
                state_map.get(key)
                or _lookup_unique_state_by_code(state_map, item.get("code"))
                or {}
            )
            close = _clean_float(item.get("close"))
            holding_value = _clean_float(item.get("holding_value"))
            shares = _clean_int(item.get("shares"))
            state_qty = _clean_int(state.get("qty"))
            if (
                shares is None
                and state_qty is not None
                and (
                    close is None
                    or holding_value is None
                    or abs(float(holding_value) - float(state_qty) * float(close)) <= 0.01
                )
            ):
                shares = state_qty
                item["shares"] = shares
            avg_cost = _clean_float(state.get("avg_cost"))
            cost_basis = _clean_float(state.get("cost_basis"))
            cost_value: float | None = None
            if (
                cost_basis is not None
                and state_qty is not None
                and close is not None
                and holding_value is not None
            ):
                market_value = float(state_qty) * float(close)
                scale = float(holding_value) / market_value if market_value > 1e-12 else 1.0
                cost_value = round(float(cost_basis) * float(scale), 2)
            elif avg_cost is not None:
                qty_for_cost = state_qty if state_qty is not None else shares
                if qty_for_cost is not None:
                    cost_value = round(float(avg_cost) * float(qty_for_cost), 2)
                    if close is not None and holding_value is not None:
                        market_value = float(qty_for_cost) * float(close)
                        if market_value > 1e-12:
                            scale = float(holding_value) / market_value
                            cost_value = round(float(cost_value) * float(scale), 2)
            unrealized_pnl = (
                round(float(holding_value) - float(cost_value), 2)
                if holding_value is not None and cost_value is not None
                else None
            )
            unrealized_pnl_pct = (
                float(unrealized_pnl) / float(cost_value)
                if (
                    unrealized_pnl is not None
                    and cost_value is not None
                    and abs(float(cost_value)) > 1e-12
                )
                else None
            )
            item["cost_price"] = _clean_float(avg_cost, digits=4)
            item["cost_value"] = _clean_float(cost_value, digits=2)
            item["unrealized_pnl"] = _clean_float(unrealized_pnl, digits=2)
            item["unrealized_pnl_pct"] = _clean_float(unrealized_pnl_pct, digits=6)
            day_rows.append(item)
        enriched[date_key] = day_rows
    return enriched


def assemble_daily_holdings_payload(
    artifacts: RunResult,
    *,
    holdings_by_date: dict[str, list[dict[str, Any]]],
    summary_by_date: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        **artifact_schema_fields("daily_holdings", DAILY_HOLDINGS_SCHEMA_VERSION),
        "dates": resolve_holding_dates(
            artifacts=artifacts,
            holdings_by_date=holdings_by_date,
            summary_by_date=summary_by_date,
        ),
        "holdings_by_date": holdings_by_date,
        "summary_by_date": summary_by_date,
    }


__all__ = [
    "append_daily_holding_row",
    "assemble_daily_holdings_payload",
    "average_holding_net_value",
    "build_daily_summary_row",
    "enrich_holdings_rows",
    "holding_state_key",
    "net_value_frame",
    "normalize_holding_value",
    "normalize_trade_date_key",
    "resolve_holding_dates",
    "sum_holding_value",
    "sum_row_metric",
]
