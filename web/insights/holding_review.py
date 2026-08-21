"""Holding and trade review projections for cached library runs."""
from __future__ import annotations

from typing import Any

from web.library_analysis import (
    _coerce_finite_float,
    _display_yyyymmdd,
    _record_number,
    _record_text,
    _round_optional,
)
from web.library_cache import _resolve_library_cache_snapshot
from web.library_curves import (
    get_library_daily_holdings,
    get_library_position_changes,
    get_library_trades,
)
from web.library_metadata import _normalize_yyyymmdd_text


def _new_holding_review_stock(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "name": "",
        "holding_days": 0,
        "first_date": "",
        "last_date": "",
        "max_holding_value": 0.0,
        "latest_holding_value": None,
        "latest_unrealized_pnl": None,
        "avg_position_ratio_sum": 0.0,
        "avg_position_ratio_count": 0,
        "realized_pnl": None,
        "realized_return_sum": 0.0,
        "trade_count": 0,
    }


def _accumulate_holding_review_row(
    item: dict[str, Any],
    row: dict[str, Any],
    *,
    date_key: str,
    is_latest: bool,
) -> None:
    if not item["name"]:
        item["name"] = str(row.get("name") or row.get("stock_name") or "").strip()
    display_date = _display_yyyymmdd(date_key)
    item["holding_days"] += 1
    item["first_date"] = item["first_date"] or display_date
    item["last_date"] = display_date

    holding_value = _coerce_finite_float(row.get("holding_value"))
    if holding_value is not None:
        item["max_holding_value"] = max(float(item["max_holding_value"]), holding_value)
        if is_latest:
            item["latest_holding_value"] = holding_value

    unrealized = _coerce_finite_float(row.get("unrealized_pnl"))
    if unrealized is not None and is_latest:
        item["latest_unrealized_pnl"] = unrealized

    ratio_value = (
        row.get("effective_position_ratio")
        if row.get("effective_position_ratio") is not None
        else row.get("utilization_rate")
    )
    ratio = _coerce_finite_float(ratio_value)
    if ratio is not None:
        item["avg_position_ratio_sum"] += ratio
        item["avg_position_ratio_count"] += 1


def _collect_holding_review_holdings(
    holdings_by_date: Any,
    summary_by_date: Any,
    *,
    latest_portfolio_date: str,
) -> tuple[dict[str, dict[str, Any]], list[int], list[float]]:
    stock_map: dict[str, dict[str, Any]] = {}
    daily_holding_counts: list[int] = []
    daily_position_ratios: list[float] = []
    if not isinstance(holdings_by_date, dict):
        return stock_map, daily_holding_counts, daily_position_ratios

    for date_key in sorted(holdings_by_date, key=str.casefold):
        rows = holdings_by_date.get(date_key)
        if not isinstance(rows, list):
            continue
        daily_holding_counts.append(len(rows))
        summary = summary_by_date.get(date_key) if isinstance(summary_by_date, dict) else None
        if isinstance(summary, dict):
            asset = _coerce_finite_float(summary.get("asset"))
            position_value = _coerce_finite_float(summary.get("position_value"))
            if asset is not None and asset > 0 and position_value is not None:
                daily_position_ratios.append(position_value / asset)

        is_latest = _normalize_yyyymmdd_text(date_key) == latest_portfolio_date
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or row.get("stock_code") or "").strip()
            if not code:
                continue
            item = stock_map.setdefault(code, _new_holding_review_stock(code))
            _accumulate_holding_review_row(
                item,
                row,
                date_key=date_key,
                is_latest=is_latest,
            )
    return stock_map, daily_holding_counts, daily_position_ratios


def _collect_holding_review_trades(
    trade_records: list[Any],
    execution_records: list[Any],
    stock_map: dict[str, dict[str, Any]],
) -> int:
    risk_event_count = 0

    for record in trade_records + execution_records:
        if not isinstance(record, dict):
            continue
        code = _record_text(record, ("code", "stock_code", "股票代码", "证券代码", "代码"))
        side = _record_text(record, ("side", "方向", "买卖", "操作", "action")).lower()
        if not side and _record_text(record, ("卖出日", "卖出日期")):
            side = "sell"
        source = _record_text(record, ("source", "来源", "reason", "原因", "note", "备注"))
        is_risk_event = "risk" in source.lower() or "风控" in source
        risk_event_count += int(is_risk_event)

        realized_pnl = _record_number(
            record,
            ("realized_pnl", "realized_profit", "已实现盈亏", "现金盈亏"),
        )
        return_rate = _record_number(
            record,
            ("return_pct", "收益率", "收益", "盈亏", "pnl", "profit_loss"),
        )
        if code:
            item = stock_map.setdefault(code, _new_holding_review_stock(code))
            name_text = _record_text(
                record,
                ("name", "stock_name", "股票名称", "证券名称", "名称"),
            )
            if name_text and not item["name"]:
                item["name"] = name_text
            item["trade_count"] += 1
            if realized_pnl is not None:
                item["realized_pnl"] = float(item["realized_pnl"] or 0.0) + realized_pnl
            if return_rate is not None:
                item["realized_return_sum"] += return_rate

    return risk_event_count


def _build_holding_review_stocks(
    stock_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    stocks: list[dict[str, Any]] = []
    for item in stock_map.values():
        avg_ratio = (
            item["avg_position_ratio_sum"] / item["avg_position_ratio_count"]
            if item["avg_position_ratio_count"]
            else None
        )
        realized_pnl = _coerce_finite_float(item["realized_pnl"])
        unrealized_pnl = _coerce_finite_float(item["latest_unrealized_pnl"])
        total_pnl = (
            float(realized_pnl or 0.0) + float(unrealized_pnl or 0.0)
            if realized_pnl is not None or unrealized_pnl is not None
            else None
        )
        stocks.append(
            {
                "code": item["code"],
                "name": item["name"],
                "holding_days": item["holding_days"],
                "first_date": item["first_date"],
                "last_date": item["last_date"],
                "max_holding_value": round(float(item["max_holding_value"]), 2),
                "latest_holding_value": _round_optional(item["latest_holding_value"], 2),
                "latest_unrealized_pnl": _round_optional(
                    item["latest_unrealized_pnl"],
                    2,
                ),
                "realized_pnl": _round_optional(realized_pnl, 6),
                "realized_return_sum": round(float(item["realized_return_sum"]), 6),
                "total_pnl": _round_optional(total_pnl, 6),
                "trade_count": item["trade_count"],
                "avg_position_ratio": round(avg_ratio, 6) if avg_ratio is not None else None,
            }
        )
    stocks.sort(
        key=lambda item: (
            -abs(float(item.get("total_pnl") or 0.0)),
            str(item.get("code") or "").casefold(),
        )
    )
    return stocks


def compute_library_holding_trade_review(name: str) -> dict[str, Any] | None:
    snapshot = _resolve_library_cache_snapshot(name)
    if snapshot is None:
        return None
    holdings_data = get_library_daily_holdings(name, snapshot=snapshot)
    trades_data = get_library_trades(name, snapshot=snapshot)
    if not isinstance(holdings_data, dict) and not isinstance(trades_data, dict):
        return None

    holdings_by_date = (
        holdings_data.get("holdings_by_date") if isinstance(holdings_data, dict) else {}
    )
    summary_by_date = (
        holdings_data.get("summary_by_date") if isinstance(holdings_data, dict) else {}
    )
    dates = holdings_data.get("dates") if isinstance(holdings_data, dict) else []
    safe_dates = [str(item) for item in dates] if isinstance(dates, list) else []
    latest_portfolio_date = max(
        (_normalize_yyyymmdd_text(item) for item in safe_dates),
        default="",
    )
    if not latest_portfolio_date and isinstance(holdings_by_date, dict):
        latest_portfolio_date = max(
            (_normalize_yyyymmdd_text(item) for item in holdings_by_date),
            default="",
        )

    stock_map, daily_holding_counts, daily_position_ratios = (
        _collect_holding_review_holdings(
            holdings_by_date,
            summary_by_date,
            latest_portfolio_date=latest_portfolio_date,
        )
    )

    raw_trade_records = trades_data.get("records") if isinstance(trades_data, dict) else []
    raw_execution_records = (
        trades_data.get("execution_records") if isinstance(trades_data, dict) else []
    )
    trade_records = raw_trade_records if isinstance(raw_trade_records, list) else []
    execution_records = (
        raw_execution_records if isinstance(raw_execution_records, list) else []
    )
    risk_event_count = _collect_holding_review_trades(
        trade_records,
        execution_records,
        stock_map,
    )

    stocks = _build_holding_review_stocks(stock_map)
    position_changes = get_library_position_changes(name, snapshot=snapshot) or []
    return {
        "name": name,
        "overview": {
            "holding_date_count": len(safe_dates) or len(daily_holding_counts),
            "unique_stock_count": len(stock_map),
            "avg_holding_count": (
                round(sum(daily_holding_counts) / len(daily_holding_counts), 4)
                if daily_holding_counts
                else None
            ),
            "max_holding_count": (
                max(daily_holding_counts) if daily_holding_counts else None
            ),
            "avg_position_ratio": (
                round(sum(daily_position_ratios) / len(daily_position_ratios), 6)
                if daily_position_ratios
                else None
            ),
            "max_position_ratio": (
                round(max(daily_position_ratios), 6) if daily_position_ratios else None
            ),
            "trade_record_count": len(trade_records),
            "execution_record_count": len(execution_records),
            "risk_event_count": risk_event_count,
            "position_change_count": (
                len(position_changes) if isinstance(position_changes, list) else 0
            ),
        },
        "stocks": stocks[:200],
    }
