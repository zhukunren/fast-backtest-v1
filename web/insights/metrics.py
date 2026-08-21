"""Derived performance metrics for cached library runs."""
from __future__ import annotations

import math
from typing import Any

from web.cache_generation import WebCacheSnapshot
from web.library_analysis import (
    _build_trade_profile,
    _daily_returns_from_points,
    _display_yyyymmdd,
    _drawdown_series_from_values,
    _extract_net_value_points,
    _extract_trade_holding_days,
    _extract_trade_pnl_values,
    _median,
)
from web.library_cache import _resolve_library_cache_snapshot
from web.library_curves import get_library_net_value, get_library_trades


def _add_trade_metrics(result: dict[str, Any], trades_data: Any) -> list[float]:
    trade_profile = _build_trade_profile(trades_data)
    if trade_profile:
        result["total_trades"] = trade_profile.get("record_count")
        result["win_rate"] = trade_profile.get("win_rate")
        result["profit_factor"] = trade_profile.get("profit_factor")
        result["avg_trade_return"] = trade_profile.get("avg_pnl")

    pnl_values = _extract_trade_pnl_values(trades_data)
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    if wins and losses:
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        if avg_loss > 1e-12:
            result["profit_loss_ratio"] = round(avg_win / avg_loss, 6)

    holding_days = _extract_trade_holding_days(trades_data)
    if holding_days:
        result["avg_holding_days"] = round(sum(holding_days) / len(holding_days), 4)
        result["median_holding_days"] = round(
            _median([float(day) for day in holding_days]) or 0.0,
            4,
        )
    return pnl_values


def _drawdown_period_metrics(
    dates: list[str],
    nv_arr: list[float],
    dd_arr: list[float],
) -> dict[str, Any]:
    if not dd_arr:
        return {}

    max_dd_index = min(range(len(dd_arr)), key=lambda index: dd_arr[index])
    peak_index = 0
    peak_value = 1.0
    for index in range(max_dd_index + 1):
        if nv_arr[index] >= peak_value:
            peak_index = index
            peak_value = nv_arr[index]

    recovery_days: int | None = None
    recovery_date = ""
    for index in range(max_dd_index + 1, len(nv_arr)):
        if nv_arr[index] >= peak_value:
            recovery_days = index - max_dd_index
            recovery_date = _display_yyyymmdd(dates[index])
            break

    max_dd_days = 0
    current_dd_days = 0
    for drawdown in dd_arr:
        if drawdown < 0:
            current_dd_days += 1
            max_dd_days = max(max_dd_days, current_dd_days)
        else:
            current_dd_days = 0

    return {
        "max_drawdown_start_date": _display_yyyymmdd(dates[peak_index]),
        "max_drawdown_end_date": _display_yyyymmdd(dates[max_dd_index]),
        "max_dd_recovery_days": recovery_days,
        "max_dd_recovery_date": recovery_date,
        "max_dd_days": max_dd_days,
    }


def _add_return_metrics(
    result: dict[str, Any],
    points: list[dict[str, Any]],
    dates: list[str],
    nv_arr: list[float],
    dd_arr: list[float],
) -> None:
    interval_count = len(nv_arr)
    if interval_count <= 0 or nv_arr[0] <= 0:
        return

    total_return_ratio = nv_arr[-1]
    result["cum_return"] = round(total_return_ratio - 1.0, 6)
    ann_return = (total_return_ratio ** (250.0 / interval_count)) - 1.0
    result["ann_return"] = round(ann_return, 6)

    daily_rets = _daily_returns_from_points(points, initial_value=1.0)
    if daily_rets:
        mean_r = sum(daily_rets) / len(daily_rets)
        variance = sum((value - mean_r) ** 2 for value in daily_rets) / len(daily_rets)
        std_r = math.sqrt(variance) if variance > 0 else 0.0
        if std_r > 1e-12:
            result["sharpe"] = round((mean_r / std_r) * math.sqrt(250), 4)
        else:
            result["sharpe"] = 0.0 if abs(mean_r) <= 1e-12 else None

    max_dd = abs(min(dd_arr)) if dd_arr else 0.0
    result["max_drawdown"] = round(max_dd, 6)
    result["calmar"] = round(ann_return / max_dd, 4) if max_dd > 0 else None
    result.update(_drawdown_period_metrics(dates, nv_arr, dd_arr))


def _monthly_return_metrics(dates: list[str], nv_arr: list[float]) -> dict[str, Any]:
    if len(nv_arr) <= 20:
        return {}

    monthly: dict[str, dict[str, float]] = {}
    previous_nv = 1.0
    previous_month = dates[0][:6]
    for index in range(1, len(dates)):
        current_month = dates[index][:6]
        if current_month == previous_month:
            continue
        value = (nv_arr[index - 1] / previous_nv - 1.0) if previous_nv > 0 else 0.0
        monthly.setdefault(previous_month[:4], {})[previous_month[4:6]] = round(value, 6)
        previous_nv = nv_arr[index - 1]
        previous_month = current_month

    value = (nv_arr[-1] / previous_nv - 1.0) if previous_nv > 0 else 0.0
    monthly.setdefault(previous_month[:4], {})[previous_month[4:6]] = round(value, 6)
    monthly_trend = [
        {"period": f"{year}-{month}", "return": monthly_return}
        for year, month_map in sorted(monthly.items(), key=lambda item: item[0])
        for month, monthly_return in sorted(month_map.items(), key=lambda item: item[0])
    ]
    return {"monthly_returns": monthly, "monthly_trend": monthly_trend[-12:]}


def _pnl_metrics(pnl_values: list[float]) -> dict[str, Any]:
    if not pnl_values:
        return {}

    edges = [-1e9, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 1e9]
    labels = ["<-10%", "-10~-5%", "-5~-2%", "-2~0%", "0~2%", "2~5%", "5~10%", ">10%"]
    counts = [0] * len(labels)
    for value in pnl_values:
        for index in range(len(edges) - 1):
            if edges[index] <= value < edges[index + 1]:
                counts[index] += 1
                break

    max_win_streak = max_loss_streak = 0
    current_win = current_loss = 0
    for value in pnl_values:
        if value > 0:
            current_win += 1
            current_loss = 0
        elif value < 0:
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        max_win_streak = max(max_win_streak, current_win)
        max_loss_streak = max(max_loss_streak, current_loss)
    return {
        "pnl_distribution": {"labels": labels, "counts": counts},
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
    }


def _compute_derived_metrics_from_payloads(
    nv_data: dict[str, Any] | None,
    trades_data: Any,
) -> dict[str, Any] | None:
    if not nv_data or "net_value" not in nv_data:
        return None

    points = _extract_net_value_points(nv_data)
    if len(points) < 2:
        return None
    dates = [str(point["date_key"]) for point in points]
    nv_arr = [float(point["net_value"]) for point in points]
    dd_arr = _drawdown_series_from_values(nv_arr, initial_value=1.0)
    result: dict[str, Any] = {}
    pnl_values = _add_trade_metrics(result, trades_data)
    _add_return_metrics(result, points, dates, nv_arr, dd_arr)
    result.update(_monthly_return_metrics(dates, nv_arr))
    result.update(_pnl_metrics(pnl_values))

    return result


def compute_derived_metrics(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    """Compute return, drawdown, monthly, and trade metrics from one cache snapshot."""
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    nv_data = get_library_net_value(
        name,
        snapshot=snapshot,
        include_benchmark=False,
    )
    trades_data = get_library_trades(name, snapshot=snapshot)
    return _compute_derived_metrics_from_payloads(nv_data, trades_data)
