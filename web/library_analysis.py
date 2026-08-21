"""Pure analysis helpers for the Web strategy library."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from backtest.reporting.market_state import compute_market_state_from_net_value_payload


def _normalize_yyyymmdd_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).strftime("%Y%m%d")
    except ValueError:
        return text.replace("-", "")

def _coerce_finite_float(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None

def _display_yyyymmdd(date_key: str) -> str:
    text = _normalize_yyyymmdd_text(date_key)
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(date_key or "")

def _extract_net_value_points(nv_data: dict[str, Any]) -> list[dict[str, Any]]:
    dates = nv_data.get("dates")
    values = nv_data.get("net_value")
    if not isinstance(dates, list) or not isinstance(values, list):
        return []

    by_date: dict[str, dict[str, Any]] = {}
    for raw_date, raw_value in zip(dates, values):
        date_key = _normalize_yyyymmdd_text(raw_date)
        value = _coerce_finite_float(raw_value)
        if not date_key or value is None or value <= 0:
            continue
        by_date[date_key] = {
            "date_key": date_key,
            "date": _display_yyyymmdd(date_key),
            "net_value": value,
        }
    return [by_date[key] for key in sorted(by_date, key=str.casefold)]

def _curve_max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            max_drawdown = max(max_drawdown, 1.0 - value / peak)
    return max_drawdown

def _drawdown_series_from_values(
    values: list[float],
    *,
    initial_value: float | None = None,
) -> list[float]:
    if not values:
        return []
    drawdowns: list[float] = []
    peak = initial_value if initial_value is not None and initial_value > 0 else values[0]
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            drawdowns.append(value / peak - 1.0)
        else:
            drawdowns.append(0.0)
    return drawdowns

def _daily_returns_from_points(
    points: list[dict[str, Any]],
    *,
    initial_value: float | None = None,
) -> list[float]:
    returns: list[float] = []
    if points and initial_value is not None and initial_value > 0:
        first = _coerce_finite_float(points[0].get("net_value"))
        if first is not None:
            returns.append(first / initial_value - 1.0)
    for index in range(1, len(points)):
        prev = _coerce_finite_float(points[index - 1].get("net_value"))
        cur = _coerce_finite_float(points[index].get("net_value"))
        if prev is None or cur is None or prev <= 0:
            continue
        returns.append(cur / prev - 1.0)
    return returns

def _period_key(date_key: str, period: str) -> str:
    year = date_key[:4]
    month = int(date_key[4:6] or "1")
    if period == "year":
        return year
    if period == "quarter":
        quarter = (month - 1) // 3 + 1
        return f"{year}Q{quarter}"
    return f"{year}-{month:02d}"

def _build_period_stats(
    points: list[dict[str, Any]],
    *,
    period: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        date_key = str(point.get("date_key") or "")
        if len(date_key) < 8:
            continue
        buckets.setdefault(_period_key(date_key, period), []).append(point)

    stats: list[dict[str, Any]] = []
    previous_value = 1.0
    for key in sorted(buckets, key=str.casefold):
        rows = buckets[key]
        if not rows:
            continue
        values = [
            value
            for value in (_coerce_finite_float(row.get("net_value")) for row in rows)
            if value is not None
        ]
        if not values:
            continue
        start_value = previous_value
        end_value = values[-1]
        period_return = end_value / start_value - 1.0 if start_value > 0 else 0.0
        daily_returns = []
        prior_value = start_value
        for value in values:
            if prior_value > 0:
                daily_returns.append(value / prior_value - 1.0)
            prior_value = value
        positive_days = sum(1 for value in daily_returns if value > 0)
        negative_days = sum(1 for value in daily_returns if value < 0)
        stats.append({
            "period": key,
            "start_date": rows[0]["date"],
            "end_date": rows[-1]["date"],
            "day_count": len(rows),
            "return": round(period_return, 6),
            "max_drawdown": round(_curve_max_drawdown([start_value, *values]), 6),
            "positive_days": positive_days,
            "negative_days": negative_days,
        })
        previous_value = end_value

    if limit is not None and limit > 0:
        return stats[-limit:]
    return stats

def _extract_trade_pnl_values(trades_data: dict[str, Any] | None) -> list[float]:
    if not isinstance(trades_data, dict):
        return []
    records = trades_data.get("records")
    if not isinstance(records, list):
        return []

    values: list[float] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("盈亏", "pnl", "return_pct", "profit_loss", "收益率", "return"):
            if key not in record:
                continue
            value = _coerce_finite_float(record.get(key))
            if value is not None:
                values.append(value)
                break
    return values

def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0

def _build_trade_profile(trades_data: dict[str, Any] | None) -> dict[str, Any]:
    records = trades_data.get("records") if isinstance(trades_data, dict) else []
    record_count = len(records) if isinstance(records, list) else 0
    pnl_values = _extract_trade_pnl_values(trades_data)
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_concentration = None
    if gross_profit > 0 and wins:
        top_count = max(1, math.ceil(len(wins) * 0.2))
        profit_concentration = sum(sorted(wins, reverse=True)[:top_count]) / gross_profit

    return {
        "record_count": record_count,
        "pnl_sample_count": len(pnl_values),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(pnl_values), 6) if pnl_values else None,
        "avg_pnl": round(sum(pnl_values) / len(pnl_values), 6) if pnl_values else None,
        "median_pnl": round(_median(pnl_values), 6) if pnl_values else None,
        "best_pnl": round(max(pnl_values), 6) if pnl_values else None,
        "worst_pnl": round(min(pnl_values), 6) if pnl_values else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "profit_concentration": round(profit_concentration, 6) if profit_concentration is not None else None,
    }

def _trade_date_key(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key not in record:
            continue
        date_key = _normalize_yyyymmdd_text(record.get(key))
        if date_key:
            return date_key
    return ""

def _trade_holding_days(record: dict[str, Any]) -> int | None:
    explicit = _record_number(record, ("holding_days", "hold_days", "持仓天数", "持仓日"))
    if explicit is not None and explicit >= 0:
        return int(round(explicit))

    buy_date = _trade_date_key(record, ("buy_date", "entry_date", "买入日", "买入日期", "入场日", "入场日期"))
    sell_date = _trade_date_key(record, ("sell_date", "exit_date", "卖出日", "卖出日期", "出场日", "出场日期"))
    if not buy_date or not sell_date:
        return None
    try:
        start = datetime.strptime(buy_date, "%Y%m%d")
        end = datetime.strptime(sell_date, "%Y%m%d")
    except ValueError:
        return None
    return max(0, (end - start).days)

def _extract_trade_holding_days(trades_data: dict[str, Any] | None) -> list[int]:
    if not isinstance(trades_data, dict):
        return []
    records = trades_data.get("records")
    if not isinstance(records, list):
        return []

    values: list[int] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        days = _trade_holding_days(record)
        if days is not None:
            values.append(days)
    return values

def _analysis_warning(code: str, label: str) -> dict[str, str]:
    return {"code": code, "label": label}

def _analysis_diagnostic(code: str, label: str, severity: str, detail: str) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "severity": severity,
        "detail": detail,
    }

def _score_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"

def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))

def _round_optional(value: Any, digits: int = 6) -> float | None:
    num = _coerce_finite_float(value)
    return round(num, digits) if num is not None else None

def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) if variance > 0 else 0.0

def _compound_return(returns: list[float]) -> float:
    value = 1.0
    for item in returns:
        value *= 1.0 + item
    return value - 1.0

def _stats_from_returns(
    returns: list[float],
    *,
    benchmark_returns: list[float] | None = None,
) -> dict[str, Any]:
    clean_returns = [value for value in returns if math.isfinite(value)]
    if not clean_returns:
        return {
            "day_count": 0,
            "return": None,
            "benchmark_return": None,
            "excess_return": None,
            "ann_return": None,
            "max_drawdown": None,
            "positive_day_ratio": None,
            "avg_daily_return": None,
            "volatility": None,
        }
    curve = [1.0]
    for value in clean_returns:
        curve.append(curve[-1] * (1.0 + value))
    total_return = curve[-1] - 1.0
    benchmark_return = None
    if benchmark_returns is not None:
        clean_benchmark = [value for value in benchmark_returns if math.isfinite(value)]
        if clean_benchmark:
            benchmark_return = _compound_return(clean_benchmark)
    ann_return = (1.0 + total_return) ** (250.0 / len(clean_returns)) - 1.0 if total_return > -1.0 else None
    volatility = _stddev(clean_returns) * math.sqrt(250.0)
    return {
        "day_count": len(clean_returns),
        "return": round(total_return, 6),
        "benchmark_return": round(benchmark_return, 6) if benchmark_return is not None else None,
        "excess_return": round(total_return - benchmark_return, 6) if benchmark_return is not None else None,
        "ann_return": round(ann_return, 6) if ann_return is not None else None,
        "max_drawdown": round(_curve_max_drawdown(curve), 6),
        "positive_day_ratio": round(sum(1 for value in clean_returns if value > 0) / len(clean_returns), 6),
        "avg_daily_return": round(sum(clean_returns) / len(clean_returns), 6),
        "volatility": round(volatility, 6),
    }

def _market_state_bucket(
    observations: list[dict[str, Any]],
    *,
    key: str,
    value: str,
    label: str,
) -> dict[str, Any]:
    rows = [item for item in observations if item.get(key) == value]
    stats = _stats_from_returns(
        [float(item["strategy_return"]) for item in rows],
        benchmark_returns=[float(item["benchmark_return"]) for item in rows],
    )
    return {
        "state": value,
        "label": label,
        **stats,
    }

def _compute_market_state_from_nv_data(nv_data: dict[str, Any], *, name: str) -> dict[str, Any] | None:
    return compute_market_state_from_net_value_payload(nv_data, name=name)

def _build_strategy_score(
    *,
    cum_return: float,
    max_drawdown: float,
    ann_return: float | None,
    sharpe: float | None,
    calmar: float | None,
    win_rate: float | None,
    concentration: float | None,
    positive_day_ratio: float | None,
    positive_month_ratio: float | None,
    date_count: int,
    trade_count: int,
    holding_coverage: float,
    market_state: dict[str, Any] | None,
) -> dict[str, Any]:
    return_quality = _clamp_score(50.0 + cum_return * 80.0 + (ann_return or 0.0) * 25.0)
    risk_control = _clamp_score(100.0 - abs(max_drawdown) * 180.0 + (calmar or 0.0) * 5.0)
    stability_inputs = [
        (positive_day_ratio or 0.0) * 100.0,
        (positive_month_ratio or 0.0) * 100.0 if positive_month_ratio is not None else 50.0,
        _clamp_score(50.0 + (sharpe or 0.0) * 12.0),
    ]
    stability = sum(stability_inputs) / len(stability_inputs)
    if win_rate is None and trade_count == 0:
        trade_quality = 45.0
    else:
        trade_quality = _clamp_score(35.0 + (win_rate or 0.0) * 70.0)
        if concentration is not None and concentration > 0.7:
            trade_quality -= min(25.0, (concentration - 0.7) * 80.0)
    sample_quality = _clamp_score(
        min(1.0, date_count / 250.0) * 55.0
        + min(1.0, trade_count / 50.0) * 25.0
        + min(1.0, holding_coverage) * 20.0
    )

    regime_robustness = 50.0
    trend_states = []
    if isinstance(market_state, dict):
        states = market_state.get("states")
        if isinstance(states, dict) and isinstance(states.get("trend"), list):
            trend_states = [
                item for item in states["trend"]
                if isinstance(item, dict) and (item.get("day_count") or 0) > 0
            ]
    if trend_states:
        positive_state_ratio = sum(1 for item in trend_states if (item.get("return") or 0) > 0) / len(trend_states)
        bear_state = next((item for item in trend_states if item.get("state") == "bear"), None)
        bear_return = _coerce_finite_float(bear_state.get("return")) if isinstance(bear_state, dict) else None
        regime_robustness = 45.0 + positive_state_ratio * 45.0
        if bear_return is not None:
            regime_robustness += max(-20.0, min(10.0, bear_return * 100.0))
        regime_robustness = _clamp_score(regime_robustness)

    components = {
        "return_quality": round(return_quality, 1),
        "risk_control": round(risk_control, 1),
        "stability": round(stability, 1),
        "trade_quality": round(_clamp_score(trade_quality), 1),
        "sample_quality": round(sample_quality, 1),
        "regime_robustness": round(regime_robustness, 1),
    }
    weights = {
        "return_quality": 0.22,
        "risk_control": 0.22,
        "stability": 0.18,
        "trade_quality": 0.14,
        "sample_quality": 0.12,
        "regime_robustness": 0.12,
    }
    score = round(sum(components[key] * weights[key] for key in weights), 1)
    score = max(0.0, min(100.0, score))
    return {
        "value": score,
        "grade": _score_grade(score),
        "components": components,
        "weights": weights,
    }

def _record_text(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""

def _record_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in record:
            continue
        value = _coerce_finite_float(record.get(key))
        if value is not None:
            return value
    return None

def _record_date_key(record: dict[str, Any]) -> str:
    for key in (
        "trade_date",
        "date",
        "日期",
        "成交日期",
        "卖出日",
        "卖出日期",
        "买入日",
        "买入日期",
    ):
        value = record.get(key)
        date_key = _normalize_yyyymmdd_text(value)
        if date_key:
            return date_key
    return ""

def _correlation(left: list[float], right: list[float]) -> float | None:
    pairs = [(a, b) for a, b in zip(left, right) if math.isfinite(a) and math.isfinite(b)]
    if len(pairs) < 2:
        return None
    left_values = [item[0] for item in pairs]
    right_values = [item[1] for item in pairs]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in pairs)
    left_denom = math.sqrt(sum((a - left_mean) ** 2 for a in left_values))
    right_denom = math.sqrt(sum((b - right_mean) ** 2 for b in right_values))
    denom = left_denom * right_denom
    return round(numerator / denom, 6) if denom > 1e-12 else None
