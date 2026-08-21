"""Strategy-level research diagnostics and comparisons."""
from __future__ import annotations

from typing import Any

from web.api_request_support import normalize_compare_curve
from web.cache_generation import WebCacheSnapshot
from web.insights.metrics import _compute_derived_metrics_from_payloads
from web.library_analysis import (
    _analysis_diagnostic,
    _analysis_warning,
    _build_period_stats,
    _build_strategy_score,
    _build_trade_profile,
    _coerce_finite_float,
    _compute_market_state_from_nv_data,
    _correlation,
    _curve_max_drawdown,
    _daily_returns_from_points,
    _display_yyyymmdd,
    _extract_net_value_points,
)
from web.library_cache import _resolve_library_cache_snapshot
from web.library_catalog import get_library_catalog
from web.library_curves import (
    get_library_daily_holdings,
    get_library_market_state,
    get_library_net_value,
    get_library_trades,
)


def compute_library_strategy_analysis(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    """基于本地缓存生成可复现的策略研究诊断，不依赖数据库。"""
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    nv_data = get_library_net_value(
        name,
        snapshot=snapshot,
        include_benchmark=False,
    )
    if not isinstance(nv_data, dict):
        return None
    points = _extract_net_value_points(nv_data)
    if len(points) < 2:
        return None

    values = [float(point["net_value"]) for point in points]
    daily_returns = _daily_returns_from_points(points, initial_value=1.0)
    cum_return = values[-1] - 1.0
    max_drawdown = _curve_max_drawdown([1.0, *values])
    trades_data = get_library_trades(name, snapshot=snapshot)
    metrics = _compute_derived_metrics_from_payloads(nv_data, trades_data) or {}
    holdings_data = get_library_daily_holdings(name, snapshot=snapshot)
    trade_profile = _build_trade_profile(trades_data)

    holding_dates = holdings_data.get("dates") if isinstance(holdings_data, dict) else []
    holding_date_count = len(holding_dates) if isinstance(holding_dates, list) else 0
    coverage_ratio = holding_date_count / len(points) if points else 0.0

    warnings: list[dict[str, str]] = []
    if len(points) < 60:
        warnings.append(_analysis_warning("sample_less_60_days", "净值样本少于 60 个交易日"))
    if trade_profile["record_count"] < 10:
        warnings.append(_analysis_warning("trade_count_less_10", "交易记录少于 10 条"))
    if trade_profile["record_count"] > 0 and trade_profile["pnl_sample_count"] == 0:
        warnings.append(_analysis_warning("missing_trade_pnl", "交易记录缺少可识别盈亏字段"))
    if holding_date_count == 0:
        warnings.append(_analysis_warning("missing_daily_holdings", "缺少每日持仓缓存"))
    elif coverage_ratio < 0.8:
        warnings.append(_analysis_warning("low_holding_coverage", "每日持仓覆盖率低于 80%"))

    monthly_stats = _build_period_stats(points, period="month", limit=24)
    yearly_stats = _build_period_stats(points, period="year")
    quarterly_stats = _build_period_stats(points, period="quarter", limit=12)
    positive_months = sum(1 for item in monthly_stats if (item.get("return") or 0) > 0)
    positive_month_ratio = positive_months / len(monthly_stats) if monthly_stats else None
    positive_day_ratio = (
        sum(1 for value in daily_returns if value > 0) / len(daily_returns)
        if daily_returns
        else None
    )
    market_state = get_library_market_state(
        name,
        snapshot=snapshot,
    ) or _compute_market_state_from_nv_data(nv_data, name=name)

    ann_return = _coerce_finite_float(metrics.get("ann_return"))
    sharpe = _coerce_finite_float(metrics.get("sharpe"))
    calmar = _coerce_finite_float(metrics.get("calmar"))
    win_rate = _coerce_finite_float(trade_profile.get("win_rate"))
    concentration = _coerce_finite_float(trade_profile.get("profit_concentration"))

    diagnostics: list[dict[str, str]] = []
    if warnings:
        diagnostics.append(
            _analysis_diagnostic(
                "small_sample",
                "样本偏少",
                "warning",
                "研究结论应以扩大回测区间或增加交易样本后再确认。",
            )
        )
    if cum_return < 0:
        diagnostics.append(
            _analysis_diagnostic(
                "negative_return",
                "累计收益为负",
                "danger",
                "当前有效缓存区间内策略净值低于起始水平。",
            )
        )
    if abs(max_drawdown) >= 0.25:
        diagnostics.append(
            _analysis_diagnostic(
                "high_drawdown",
                "回撤偏高",
                "danger",
                "最大回撤超过 25%，需要复核仓位、止损或择时约束。",
            )
        )
    if sharpe is not None and sharpe < 0.5:
        diagnostics.append(
            _analysis_diagnostic(
                "low_sharpe",
                "风险调整收益偏弱",
                "warning",
                "夏普比率低于 0.5，净值波动质量不足。",
            )
        )
    if win_rate is not None and win_rate < 0.45:
        diagnostics.append(
            _analysis_diagnostic(
                "low_win_rate",
                "胜率偏低",
                "warning",
                "可识别盈亏样本胜率低于 45%，建议复核买卖点与过滤条件。",
            )
        )
    if (
        concentration is not None
        and concentration >= 0.8
        and trade_profile["pnl_sample_count"] >= 5
    ):
        diagnostics.append(
            _analysis_diagnostic(
                "profit_concentration",
                "盈利集中",
                "warning",
                "前 20% 盈利交易贡献超过 80%，需要验证收益是否依赖少数样本。",
            )
        )
    if (
        positive_month_ratio is not None
        and positive_month_ratio < 0.45
        and len(monthly_stats) >= 4
    ):
        diagnostics.append(
            _analysis_diagnostic(
                "low_positive_month_ratio",
                "月度稳定性不足",
                "warning",
                "最近月度样本中正收益月份占比低于 45%。",
            )
        )
    if market_state:
        trend_states = market_state.get("states", {}).get("trend", [])
        bear_state = next(
            (
                item
                for item in trend_states
                if isinstance(item, dict) and item.get("state") == "bear"
            ),
            None,
        )
        if (
            isinstance(bear_state, dict)
            and (bear_state.get("day_count") or 0) >= 10
            and (bear_state.get("return") or 0) < -0.08
        ):
            diagnostics.append(
                _analysis_diagnostic(
                    "bear_market_weakness",
                    "下跌市承压",
                    "warning",
                    "市场状态切片显示下跌市样本收益低于 -8%，需复核防守条件。",
                )
            )

    score = _build_strategy_score(
        cum_return=cum_return,
        max_drawdown=max_drawdown,
        ann_return=ann_return,
        sharpe=sharpe,
        calmar=calmar,
        win_rate=win_rate,
        concentration=concentration,
        positive_day_ratio=positive_day_ratio,
        positive_month_ratio=positive_month_ratio,
        date_count=len(points),
        trade_count=trade_profile["record_count"],
        holding_coverage=coverage_ratio,
        market_state=market_state,
    )

    return {
        "name": name,
        "score": score,
        "sample_quality": {
            "start_date": points[0]["date"],
            "end_date": points[-1]["date"],
            "date_count": len(points),
            "daily_return_count": len(daily_returns),
            "trade_count": trade_profile["record_count"],
            "trade_pnl_sample_count": trade_profile["pnl_sample_count"],
            "holding_date_count": holding_date_count,
            "holding_coverage": round(coverage_ratio, 6),
            "warnings": warnings,
        },
        "return_profile": {
            "cum_return": round(cum_return, 6),
            "ann_return": round(ann_return, 6) if ann_return is not None else None,
            "max_drawdown": round(max_drawdown, 6),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "calmar": round(calmar, 4) if calmar is not None else None,
            "positive_day_ratio": (
                round(positive_day_ratio, 6) if positive_day_ratio is not None else None
            ),
            "positive_month_ratio": (
                round(positive_month_ratio, 6) if positive_month_ratio is not None else None
            ),
        },
        "trade_profile": trade_profile,
        "period_stats": {
            "yearly": yearly_stats,
            "quarterly": quarterly_stats,
            "monthly": monthly_stats,
        },
        "market_state": market_state,
        "diagnostics": diagnostics,
    }


def compute_library_market_state_analysis(name: str) -> dict[str, Any] | None:
    snapshot = _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    cached = get_library_market_state(name, snapshot=snapshot)
    if isinstance(cached, dict):
        return cached
    nv_data = get_library_net_value(name, snapshot=snapshot)
    if not isinstance(nv_data, dict):
        return None
    return _compute_market_state_from_nv_data(nv_data, name=name)


def compute_enhanced_compare(names: list[str]) -> dict[str, Any] | None:
    normalized = [str(raw or "").strip() for raw in names]
    if (
        not 2 <= len(normalized) <= 4
        or any(not name for name in normalized)
        or len({name.casefold() for name in normalized}) != len(normalized)
    ):
        return None

    catalog_items = {
        str(item.get("name")): item
        for item in get_library_catalog().get("items", [])
        if isinstance(item, dict)
    }
    return_maps: dict[str, dict[str, float]] = {}
    strategies: list[dict[str, Any]] = []
    strategy_windows: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for name in normalized:
        snapshot = _resolve_library_cache_snapshot(name)
        if snapshot is None:
            errors.append(
                {
                    "name": name,
                    "code": "cache_unavailable",
                    "message": "no current cache generation is available",
                }
            )
            continue
        nv_data = get_library_net_value(name, snapshot=snapshot)
        if not isinstance(nv_data, dict):
            errors.append(
                {
                    "name": name,
                    "code": "net_value_unavailable",
                    "message": "net-value data is unavailable",
                }
            )
            continue
        curve = normalize_compare_curve(nv_data)
        if curve is None:
            errors.append(
                {
                    "name": name,
                    "code": "invalid_curve",
                    "message": "net-value curve needs at least 2 valid observations",
                }
            )
            continue
        curve_points = list(curve.items())
        strategy_windows[name] = {
            "start_date": curve_points[0][0],
            "end_date": curve_points[-1][0],
            "date_count": len(curve_points),
        }
        returns_by_date: dict[str, float] = {}
        # The first observed point has no prior close.  It is a level, not a
        # daily return; including it would mix a strategy's full warm-up gain
        # into comparisons when strategies start on different dates.
        for index in range(1, len(curve_points)):
            previous_value = curve_points[index - 1][1]
            date_key, current_value = curve_points[index]
            if previous_value > 0:
                returns_by_date[date_key] = current_value / previous_value - 1.0
        analysis = compute_library_strategy_analysis(name, snapshot=snapshot) or {}
        score = analysis.get("score") if isinstance(analysis, dict) else {}
        return_profile = analysis.get("return_profile") if isinstance(analysis, dict) else {}
        trade_profile = analysis.get("trade_profile") if isinstance(analysis, dict) else {}
        market_state = analysis.get("market_state") if isinstance(analysis, dict) else None
        trend_states = (
            market_state.get("states", {}).get("trend", [])
            if isinstance(market_state, dict)
            else []
        )
        item = catalog_items.get(name, {})
        row = {
            "name": name,
            "title": item.get("title") or name,
            "score": score.get("value") if isinstance(score, dict) else None,
            "grade": score.get("grade") if isinstance(score, dict) else "",
            "cum_return": (
                return_profile.get("cum_return")
                if isinstance(return_profile, dict)
                else item.get("cum_return")
            ),
            "ann_return": (
                return_profile.get("ann_return")
                if isinstance(return_profile, dict)
                else None
            ),
            "max_drawdown": (
                return_profile.get("max_drawdown")
                if isinstance(return_profile, dict)
                else item.get("max_drawdown")
            ),
            "sharpe": (
                return_profile.get("sharpe") if isinstance(return_profile, dict) else None
            ),
            "calmar": (
                return_profile.get("calmar") if isinstance(return_profile, dict) else None
            ),
            "positive_month_ratio": (
                return_profile.get("positive_month_ratio")
                if isinstance(return_profile, dict)
                else None
            ),
            "win_rate": (
                trade_profile.get("win_rate")
                if isinstance(trade_profile, dict)
                else item.get("win_rate")
            ),
            "trade_count": (
                trade_profile.get("record_count")
                if isinstance(trade_profile, dict)
                else item.get("trade_count")
            ),
            "market_states": trend_states,
        }
        strategies.append(row)
        return_maps[name] = returns_by_date

    matrix: list[list[float | None]] = []
    for left in strategies:
        row: list[float | None] = []
        for right in strategies:
            left_map = return_maps.get(str(left["name"]), {})
            right_map = return_maps.get(str(right["name"]), {})
            common_dates = sorted(set(left_map) & set(right_map), key=str.casefold)
            corr = _correlation(
                [left_map[date] for date in common_dates],
                [right_map[date] for date in common_dates],
            )
            row.append(corr)
        matrix.append(row)

    common_dates = (
        sorted(
            set.intersection(*(set(return_maps[name]) for name in return_maps)),
            key=str.casefold,
        )
        if len(return_maps) >= 2
        else []
    )
    equal_weight_values = [1.0]
    for date in common_dates:
        day_returns = [
            return_maps[name][date] for name in return_maps if date in return_maps[name]
        ]
        if day_returns:
            equal_weight_values.append(
                equal_weight_values[-1] * (1.0 + sum(day_returns) / len(day_returns))
            )
    rankings = sorted(
        strategies,
        key=lambda item: (
            -float(item.get("score") if item.get("score") is not None else -1.0),
            -float(
                item.get("cum_return")
                if item.get("cum_return") is not None
                else -999.0
            ),
            str(item.get("name") or "").casefold(),
        ),
    )
    return {
        "requested_names": normalized,
        "names": [item["name"] for item in strategies],
        "omitted": [error["name"] for error in errors],
        "errors": errors,
        "evaluation_window": {
            "basis": "shared_daily_returns",
            "start_date": _display_yyyymmdd(common_dates[0]) if common_dates else None,
            "end_date": _display_yyyymmdd(common_dates[-1]) if common_dates else None,
            "date_count": len(common_dates),
        },
        "strategy_windows": strategy_windows,
        "strategies": strategies,
        "rankings": rankings,
        "correlation_matrix": {
            "names": [item["name"] for item in strategies],
            "values": matrix,
        },
        "equal_weight": {
            "dates": [_display_yyyymmdd(date) for date in common_dates],
            "net_value": [round(value, 6) for value in equal_weight_values[1:]],
        },
        "best": {
            "score": rankings[0] if rankings else None,
            "return": (
                max(
                    strategies,
                    key=lambda item: float(
                        item.get("cum_return")
                        if item.get("cum_return") is not None
                        else -999.0
                    ),
                )
                if strategies
                else None
            ),
            "drawdown": (
                min(
                    strategies,
                    key=lambda item: abs(
                        float(
                            item.get("max_drawdown")
                            if item.get("max_drawdown") is not None
                            else 999.0
                        )
                    ),
                )
                if strategies
                else None
            ),
        },
    }
