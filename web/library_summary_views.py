"""Bounded summary projections for large strategy-library artifacts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from web.cache_generation import WebCacheSnapshot
from web.library_curves import get_library_daily_holdings, get_library_trades


_TRADE_PREVIEW_DATE_KEYS: tuple[str, ...] = (
    "日期",
    "trade_date",
    "date",
    "买入日",
    "卖出日",
    "entry_date",
    "exit_date",
)
_SUMMARY_PREVIEW_LIMIT = 50
_HOLDING_SUMMARY_PREVIEW_LIMIT = 100


def _normalize_trade_column_key(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").strip().lower()
        if character not in {" ", "\t", "_", "-"}
    )


def _find_trade_column(
    columns: Sequence[Any],
    *,
    exact_keys: Sequence[str] = (),
    key_fragments: Sequence[str] = (),
    raw_fragments: Sequence[str] = (),
) -> str | None:
    normalized_exact = set(exact_keys)
    for column in columns:
        raw = str(column or "")
        normalized = _normalize_trade_column_key(raw)
        if (
            normalized in normalized_exact
            or any(fragment in normalized for fragment in key_fragments)
            or any(fragment in raw for fragment in raw_fragments)
        ):
            return raw
    return None


def _signal_date_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"-", "none", "nan", "nat"}:
        return ""
    digits = "".join(character for character in text if character.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def _format_signal_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _empty_signal_summary() -> dict[str, Any]:
    return {"date": "", "items": []}


def _build_latest_signal_summary(
    records: Any,
    columns: Any,
) -> dict[str, Any]:
    rows = (
        [row for row in records if isinstance(row, Mapping)]
        if isinstance(records, list)
        else []
    )
    if not rows:
        return {
            "missing_code": False,
            "buy": _empty_signal_summary(),
            "sell": _empty_signal_summary(),
        }

    raw_columns = [str(column) for column in columns] if isinstance(columns, list) else []
    if not raw_columns:
        raw_columns = [str(key) for key in rows[0]]
    code_column = _find_trade_column(
        raw_columns,
        exact_keys=("stockcode", "securitycode", "code", "tscode", "symbol", "ticker"),
        raw_fragments=("股票代码", "证券代码"),
    )
    if code_column is None:
        return {
            "missing_code": True,
            "buy": _empty_signal_summary(),
            "sell": _empty_signal_summary(),
        }
    name_column = _find_trade_column(
        raw_columns,
        exact_keys=("stockname", "securityname", "name"),
        raw_fragments=("股票名称", "名称"),
    )
    buy_column = _find_trade_column(
        raw_columns,
        exact_keys=("buydate", "entrydate", "buyday", "entryday"),
        key_fragments=("buy",),
        raw_fragments=("买入", "入场"),
    )
    sell_column = _find_trade_column(
        raw_columns,
        exact_keys=("selldate", "exitdate", "sellday", "exitday"),
        key_fragments=("sell", "exit"),
        raw_fragments=("卖出", "出场"),
    )

    def summarize_column(date_column: str | None) -> dict[str, Any]:
        if not date_column:
            return _empty_signal_summary()
        latest_key = max(
            (_signal_date_key(row.get(date_column)) for row in rows),
            default="",
        )
        if not latest_key:
            return _empty_signal_summary()

        items_by_code: dict[str, dict[str, str]] = {}
        for row in rows:
            if _signal_date_key(row.get(date_column)) != latest_key:
                continue
            code = str(row.get(code_column) or "").strip()
            if not code:
                continue
            name = str(row.get(name_column) or "").strip() if name_column else ""
            item = items_by_code.setdefault(code, {"code": code, "name": ""})
            if name:
                item["name"] = name
        return {
            "date": _format_signal_date(latest_key),
            "items": [
                items_by_code[code]
                for code in sorted(items_by_code, key=str.casefold)
            ],
        }

    return {
        "missing_code": False,
        "buy": summarize_column(buy_column),
        "sell": summarize_column(sell_column),
    }


def _record_date_sort_key(row: Mapping[str, Any], date_keys: Sequence[str]) -> str:
    values = [
        str(row[key]).strip()
        for key in date_keys
        if row.get(key) is not None
        and str(row[key]).strip()
        and str(row[key]).strip().casefold() not in {"-", "none", "nan", "nat"}
    ]
    return max(values) if values else ""


def _preview_records(
    value: Any,
    *,
    limit: int,
    date_keys: Sequence[str] = _TRADE_PREVIEW_DATE_KEYS,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or limit <= 0:
        return []
    indexed = [
        (index, dict(item))
        for index, item in enumerate(value)
        if isinstance(item, Mapping)
    ]
    indexed.sort(key=lambda pair: (_record_date_sort_key(pair[1], date_keys), pair[0]))
    return [row for _, row in indexed[-limit:]]


def _record_date_range(value: Any, *, date_keys: Sequence[str]) -> dict[str, str] | None:
    if not isinstance(value, list):
        return None
    dates = [
        _record_date_sort_key(row, date_keys)
        for row in value
        if isinstance(row, Mapping)
    ]
    dates = [date for date in dates if date]
    return {"first": min(dates), "last": max(dates)} if dates else None


def _copy_list_field(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return list(value) if isinstance(value, list) else []


def _copy_scalar_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if item is None or isinstance(item, (str, int, float, bool))
    }


def summarize_trades_payload(
    payload: Mapping[str, Any] | None,
    *,
    preview_limit: int = _SUMMARY_PREVIEW_LIMIT,
) -> dict[str, Any] | None:
    """Project a trades artifact into a bounded detail-page summary."""
    if not isinstance(payload, Mapping):
        return None

    records = payload.get("records")
    execution_records = payload.get("execution_records")
    order_records = payload.get("order_records")
    execution_date_keys = ("日期", "trade_date", "date")
    result: dict[str, Any] = {
        "artifact_type": payload.get("artifact_type"),
        "schema_version": payload.get("schema_version"),
        "view": "summary",
        "columns": _copy_list_field(payload, "columns"),
        "execution_columns": _copy_list_field(payload, "execution_columns"),
        "order_columns": _copy_list_field(payload, "order_columns"),
        "records_preview": _preview_records(records, limit=preview_limit),
        "execution_records_preview": _preview_records(
            execution_records,
            limit=preview_limit,
            date_keys=execution_date_keys,
        ),
        "order_records_preview": _preview_records(
            order_records,
            limit=preview_limit,
            date_keys=execution_date_keys,
        ),
        "latest_signals": _build_latest_signal_summary(records, payload.get("columns")),
        "summary": {
            "record_count": len(records) if isinstance(records, list) else 0,
            "execution_record_count": (
                len(execution_records) if isinstance(execution_records, list) else 0
            ),
            "order_record_count": len(order_records) if isinstance(order_records, list) else 0,
            "record_date_range": _record_date_range(
                records,
                date_keys=("买入日", "卖出日", "entry_date", "exit_date"),
            ),
            "execution_date_range": _record_date_range(
                execution_records,
                date_keys=execution_date_keys,
            ),
            "order_date_range": _record_date_range(
                order_records,
                date_keys=execution_date_keys,
            ),
            "fills_ledger_present": isinstance(payload.get("fills_ledger"), Mapping),
        },
    }
    for key in ("execution_meta", "order_meta"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            result[key] = _copy_scalar_mapping(value)
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        result["diagnostics"] = _copy_scalar_mapping(diagnostics)
    return result


def summarize_daily_holdings_payload(
    payload: Mapping[str, Any] | None,
    *,
    preview_limit: int = _HOLDING_SUMMARY_PREVIEW_LIMIT,
) -> dict[str, Any] | None:
    """Project daily holdings into summaries and the latest populated snapshot."""
    if not isinstance(payload, Mapping):
        return None
    dates = payload.get("dates")
    holdings_by_date = payload.get("holdings_by_date")
    summary_by_date = payload.get("summary_by_date")
    if (
        not isinstance(dates, list)
        or not isinstance(holdings_by_date, Mapping)
        or not isinstance(summary_by_date, Mapping)
    ):
        return None

    normalized_dates = [
        str(value).strip()
        for value in dates
        if value is not None and str(value).strip()
    ]
    if normalized_dates != sorted(set(normalized_dates)):
        return None
    holdings_keys = {str(key): value for key, value in holdings_by_date.items()}

    latest_date = ""
    for candidate in reversed(normalized_dates):
        rows = holdings_keys.get(candidate)
        if isinstance(rows, list) and rows:
            latest_date = candidate
            break
    if not latest_date and normalized_dates:
        latest_date = normalized_dates[-1]
    latest_rows = holdings_keys.get(latest_date, [])
    if not isinstance(latest_rows, list):
        latest_rows = []

    summary_dates = {
        str(key): _copy_scalar_mapping(summary_by_date[key])
        for key in sorted(summary_by_date, key=lambda value: str(value).casefold())
        if isinstance(summary_by_date[key], Mapping)
    }
    total_rows = sum(
        len(value) for value in holdings_by_date.values() if isinstance(value, list)
    )
    populated_dates = sum(
        1 for value in holdings_by_date.values() if isinstance(value, list) and value
    )
    return {
        "artifact_type": payload.get("artifact_type"),
        "schema_version": payload.get("schema_version"),
        "view": "summary",
        "dates": normalized_dates,
        "summary_by_date": summary_dates,
        "latest_date": latest_date,
        "latest_summary": summary_dates.get(latest_date),
        "latest_holdings_preview": _preview_records(
            latest_rows,
            limit=preview_limit,
            date_keys=("trade_date", "date"),
        ),
        "summary": {
            "date_count": len(normalized_dates),
            "populated_date_count": populated_dates,
            "holding_row_count": total_rows,
            "latest_holding_count": len(latest_rows),
            "latest_preview_truncated": len(latest_rows) > max(preview_limit, 0),
        },
    }


def get_library_trades_summary(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    payload = get_library_trades(name, snapshot=snapshot)
    return summarize_trades_payload(payload)


def get_library_daily_holdings_summary(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    payload = get_library_daily_holdings(name, snapshot=snapshot)
    return summarize_daily_holdings_payload(payload)


__all__ = [
    "get_library_daily_holdings_summary",
    "get_library_trades_summary",
    "summarize_daily_holdings_payload",
    "summarize_trades_payload",
]
