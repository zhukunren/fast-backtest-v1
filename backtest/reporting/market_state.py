"""Market-state slicing utilities for portfolio backtest outputs."""
from __future__ import annotations

import math
from typing import Any


def _coerce_finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stddev(values: list[float]) -> float:
    clean = [value for value in values if math.isfinite(value)]
    if len(clean) < 2:
        return 0.0
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    return math.sqrt(max(variance, 0.0))


def _median(values: list[float]) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


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


def _net_value_points(nv_data: dict[str, Any]) -> list[dict[str, Any]]:
    dates = nv_data.get("dates")
    values = nv_data.get("net_value")
    if not isinstance(dates, list) or not isinstance(values, list):
        return []

    points: list[dict[str, Any]] = []
    for raw_date, raw_value in zip(dates, values, strict=False):
        value = _coerce_finite_float(raw_value)
        text = str(raw_date or "").strip()
        digits = "".join(ch for ch in text if ch.isdigit())
        if value is None or value <= 0 or len(digits) < 8:
            continue
        date_key = digits[:8]
        points.append({
            "date": f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}",
            "date_key": date_key,
            "net_value": value,
        })

    by_date: dict[str, dict[str, Any]] = {}
    for point in points:
        by_date[point["date_key"]] = point
    return [by_date[key] for key in sorted(by_date, key=str.casefold)]


def _benchmark_values(nv_data: dict[str, Any], strategy_values: list[float]) -> tuple[list[float], str, str]:
    raw_benchmark = nv_data.get("benchmark_net_value")
    if isinstance(raw_benchmark, list) and len(raw_benchmark) == len(strategy_values):
        values = [_coerce_finite_float(item) for item in raw_benchmark]
        if all(value is not None and value > 0 for value in values):
            return [float(value) for value in values if value is not None], "benchmark", str(
                nv_data.get("benchmark_label") or "benchmark"
            )
    return list(strategy_values), "strategy_fallback", "策略净值代用"


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


def compute_market_state_from_net_value_payload(nv_data: dict[str, Any], *, name: str = "") -> dict[str, Any] | None:
    """Compute trend and volatility slices from a net-value payload.

    The function is intentionally linear in the number of net-value points so
    it can run as part of the main artifact export path with negligible cost.
    """
    points = _net_value_points(nv_data)
    if len(points) < 3:
        return None

    strategy_values = [float(point["net_value"]) for point in points]
    benchmark_values, source, benchmark_label = _benchmark_values(nv_data, strategy_values)
    if len(benchmark_values) != len(strategy_values):
        return None

    benchmark_daily_returns: list[float] = []
    observations: list[dict[str, Any]] = []
    for index in range(1, len(points)):
        prev_strategy = strategy_values[index - 1]
        cur_strategy = strategy_values[index]
        prev_benchmark = benchmark_values[index - 1]
        cur_benchmark = benchmark_values[index]
        if prev_strategy <= 0 or prev_benchmark <= 0:
            continue

        strategy_return = cur_strategy / prev_strategy - 1.0
        benchmark_return = cur_benchmark / prev_benchmark - 1.0
        benchmark_daily_returns.append(benchmark_return)

        window_start = max(0, index - 20)
        window_base = benchmark_values[window_start]
        rolling_return = cur_benchmark / window_base - 1.0 if window_base > 0 else benchmark_return
        recent_returns = benchmark_daily_returns[-20:]
        rolling_volatility = _stddev(recent_returns) * math.sqrt(250.0)
        if rolling_return >= 0.03:
            trend_state = "bull"
            trend_label = "上涨市"
        elif rolling_return <= -0.03:
            trend_state = "bear"
            trend_label = "下跌市"
        else:
            trend_state = "range"
            trend_label = "震荡市"

        observations.append({
            "date": points[index]["date"],
            "date_key": points[index]["date_key"],
            "strategy_return": strategy_return,
            "benchmark_return": benchmark_return,
            "rolling_benchmark_return_20d": rolling_return,
            "rolling_benchmark_volatility_20d": rolling_volatility,
            "trend_state": trend_state,
            "trend_label": trend_label,
        })

    if not observations:
        return None

    vol_values = [
        float(item["rolling_benchmark_volatility_20d"])
        for item in observations
        if _coerce_finite_float(item.get("rolling_benchmark_volatility_20d")) is not None
    ]
    vol_cutoff = float(_median(vol_values) or 0.0)
    for item in observations:
        vol = _coerce_finite_float(item.get("rolling_benchmark_volatility_20d")) or 0.0
        item["volatility_state"] = "high_vol" if vol >= vol_cutoff else "low_vol"
        item["volatility_label"] = "高波动" if item["volatility_state"] == "high_vol" else "低波动"

    return {
        "name": name,
        "benchmark_label": benchmark_label,
        "source": source,
        "method": {
            "trend": "20日基准收益 >= 3% 为上涨市，<= -3% 为下跌市，其余为震荡市",
            "volatility": "20日基准年化波动率按样本中位数切分高/低波动",
            "volatility_cutoff": round(vol_cutoff, 6),
        },
        "states": {
            "trend": [
                _market_state_bucket(observations, key="trend_state", value="bull", label="上涨市"),
                _market_state_bucket(observations, key="trend_state", value="range", label="震荡市"),
                _market_state_bucket(observations, key="trend_state", value="bear", label="下跌市"),
            ],
            "volatility": [
                _market_state_bucket(observations, key="volatility_state", value="high_vol", label="高波动"),
                _market_state_bucket(observations, key="volatility_state", value="low_vol", label="低波动"),
            ],
        },
        "observations": [
            {
                "date": item["date"],
                "date_key": item["date_key"],
                "strategy_return": round(float(item["strategy_return"]), 6),
                "benchmark_return": round(float(item["benchmark_return"]), 6),
                "trend_state": item["trend_state"],
                "trend_label": item["trend_label"],
                "volatility_state": item["volatility_state"],
                "volatility_label": item["volatility_label"],
                "rolling_benchmark_return_20d": round(float(item["rolling_benchmark_return_20d"]), 6),
                "rolling_benchmark_volatility_20d": round(float(item["rolling_benchmark_volatility_20d"]), 6),
            }
            for item in observations
        ],
    }


def summarize_market_state_payload(payload: dict[str, Any] | None, *, name: str = "") -> str:
    if not isinstance(payload, dict):
        label = f" {name}" if name else ""
        return f"[MARKET_STATE]{label} unavailable"

    display_name = name or str(payload.get("name") or "").strip()
    prefix = f"[MARKET_STATE] {display_name}" if display_name else "[MARKET_STATE]"
    benchmark_label = str(payload.get("benchmark_label") or "-")
    source = str(payload.get("source") or "-")
    states = payload.get("states") if isinstance(payload.get("states"), dict) else {}
    trend_rows = states.get("trend") if isinstance(states.get("trend"), list) else []
    trend_parts = []
    for row in trend_rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("state") or "-")
        day_count = int(row.get("day_count") or 0)
        ret = row.get("return")
        ret_text = "-" if ret is None else f"{float(ret) * 100:.2f}%"
        trend_parts.append(f"{label}:{day_count}日/{ret_text}")
    trend_text = ", ".join(trend_parts) if trend_parts else "no trend states"
    method = payload.get("method") if isinstance(payload.get("method"), dict) else {}
    cutoff = method.get("volatility_cutoff")
    cutoff_text = "-" if cutoff is None else f"{float(cutoff) * 100:.2f}%"
    return f"{prefix} source={source} benchmark={benchmark_label} vol_cutoff={cutoff_text} trend=[{trend_text}]"


__all__ = ["compute_market_state_from_net_value_payload", "summarize_market_state_payload"]
