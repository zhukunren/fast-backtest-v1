from __future__ import annotations

import pytest

import web.server as server
from web.library_summary_views import (
    summarize_daily_holdings_payload,
    summarize_trades_payload,
)


pytestmark = pytest.mark.unit


def test_trade_summary_is_bounded_and_keeps_schema_columns_and_counts() -> None:
    payload = {
        "artifact_type": "trades",
        "schema_version": 2,
        "columns": ["stock_code", "买入日", "卖出日"],
        "records": [
            {"stock_code": f"A{i:02d}", "买入日": f"2025-01-{i + 1:02d}", "卖出日": f"2025-02-{i + 1:02d}"}
            for i in range(7)
        ],
        "execution_columns": ["日期", "方向"],
        "execution_records": [
            {"日期": f"2025-03-{i + 1:02d}", "方向": "买入"}
            for i in range(7)
        ],
        "execution_meta": {"count": 7},
        "order_columns": ["日期", "状态"],
        "order_records": [
            {"日期": f"2025-03-{i + 1:02d}", "状态": "已成"}
            for i in range(7)
        ],
        "order_meta": {"count": 7},
        "diagnostics": {"source": "test"},
        "fills_ledger": {"rows": [{"x": 1}]},
    }

    summary = summarize_trades_payload(payload, preview_limit=3)

    assert summary is not None
    assert summary["view"] == "summary"
    assert summary["columns"] == payload["columns"]
    assert summary["execution_columns"] == payload["execution_columns"]
    assert summary["order_columns"] == payload["order_columns"]
    assert len(summary["records_preview"]) == 3
    assert len(summary["execution_records_preview"]) == 3
    assert len(summary["order_records_preview"]) == 3
    assert summary["records_preview"][-1]["stock_code"] == "A06"
    assert summary["summary"] == {
        "record_count": 7,
        "execution_record_count": 7,
        "order_record_count": 7,
        "record_date_range": {"first": "2025-02-01", "last": "2025-02-07"},
        "execution_date_range": {"first": "2025-03-01", "last": "2025-03-07"},
        "order_date_range": {"first": "2025-03-01", "last": "2025-03-07"},
        "fills_ledger_present": True,
    }
    assert summary["execution_meta"] == {"count": 7}
    assert summary["diagnostics"] == {"source": "test"}
    assert "records" not in summary
    assert "execution_records" not in summary
    assert "fills_ledger" not in summary


def test_daily_holdings_summary_keeps_dates_and_latest_preview_only() -> None:
    payload = {
        "artifact_type": "daily_holdings",
        "schema_version": 2,
        "dates": ["2025-01-01", "2025-01-02"],
        "holdings_by_date": {
            "2025-01-01": [{"code": "A"}],
            "2025-01-02": [{"code": str(index)} for index in range(25)],
        },
        "summary_by_date": {
            "2025-01-01": {"asset": 100},
            "2025-01-02": {"asset": 101},
        },
    }

    summary = summarize_daily_holdings_payload(payload, preview_limit=20)

    assert summary is not None
    assert summary["view"] == "summary"
    assert summary["dates"] == payload["dates"]
    assert summary["summary_by_date"] == payload["summary_by_date"]
    assert summary["latest_date"] == "2025-01-02"
    assert summary["latest_summary"] == {"asset": 101}
    assert len(summary["latest_holdings_preview"]) == 20
    assert summary["summary"] == {
        "date_count": 2,
        "populated_date_count": 2,
        "holding_row_count": 26,
        "latest_holding_count": 25,
        "latest_preview_truncated": True,
    }
    assert "holdings_by_date" not in summary


def test_daily_holdings_summary_uses_latest_populated_date() -> None:
    payload = {
        "dates": ["2025-01-01", "2025-01-02", "2025-01-03"],
        "holdings_by_date": {
            "2025-01-01": [{"code": "A"}],
            "2025-01-02": [{"code": "B"}],
            "2025-01-03": [],
        },
        "summary_by_date": {
            "2025-01-01": {"holding_count": 1},
            "2025-01-02": {"holding_count": 1},
            "2025-01-03": {"holding_count": 0},
        },
    }

    summary = summarize_daily_holdings_payload(payload, preview_limit=10)

    assert summary is not None
    assert summary["latest_date"] == "2025-01-02"
    assert summary["latest_summary"] == {"holding_count": 1}
    assert summary["latest_holdings_preview"] == [{"code": "B"}]
    assert summary["summary"]["latest_holding_count"] == 1


def test_trade_summary_exposes_latest_signals_without_relying_on_preview() -> None:
    payload = {
        "columns": ["stock_code", "股票名称", "买入日", "卖出日"],
        "records": [
            {"stock_code": "A", "股票名称": "甲", "买入日": "2025-01-01", "卖出日": "2025-01-02"},
            {"stock_code": "B", "股票名称": "乙", "买入日": "2025-01-03", "卖出日": "2025-01-04"},
            {"stock_code": "C", "股票名称": "丙", "买入日": "2025-01-03", "卖出日": "2025-01-04"},
        ],
    }

    summary = summarize_trades_payload(payload, preview_limit=1)

    assert summary is not None
    assert summary["latest_signals"] == {
        "missing_code": False,
        "buy": {
            "date": "2025-01-03",
            "items": [
                {"code": "B", "name": "乙"},
                {"code": "C", "name": "丙"},
            ],
        },
        "sell": {
            "date": "2025-01-04",
            "items": [
                {"code": "B", "name": "乙"},
                {"code": "C", "name": "丙"},
            ],
        },
    }


def test_trade_and_holdings_summary_have_explicit_routes(monkeypatch) -> None:
    full_trades = {"records": [{"id": 1}]}
    summary_trades = {"view": "summary", "summary": {"record_count": 1}}
    full_holdings = {
        "dates": [],
        "holdings_by_date": {},
        "summary_by_date": {},
    }
    summary_holdings = {"view": "summary", "dates": []}
    calls: list[tuple[str, str]] = []

    def full_trades_loader(name: str):
        calls.append(("trades", f"full:{name}"))
        return full_trades

    def summary_trades_loader(name: str):
        calls.append(("trades", f"summary:{name}"))
        return summary_trades

    def full_holdings_loader(name: str):
        calls.append(("holdings", f"full:{name}"))
        return full_holdings

    def summary_holdings_loader(name: str):
        calls.append(("holdings", f"summary:{name}"))
        return summary_holdings

    monkeypatch.setattr(server, "get_library_trades", full_trades_loader)
    monkeypatch.setattr(server, "get_library_trades_summary", summary_trades_loader)
    monkeypatch.setattr(server, "get_library_daily_holdings", full_holdings_loader)
    monkeypatch.setattr(server, "get_library_daily_holdings_summary", summary_holdings_loader)

    with server.app.test_client() as client:
        full_trade_response = client.get("/api/library/demo/trades/records")
        summary_trade_path_response = client.get("/api/library/demo/trades/summary")
        full_holdings_response = client.get("/api/library/demo/holdings/history")
        summary_holdings_path_response = client.get("/api/library/demo/holdings/summary")
        legacy_trade_response = client.get("/api/library/demo/trades")
        legacy_holdings_response = client.get("/api/library/demo/holdings")

    assert full_trade_response.get_json() == full_trades
    assert summary_trade_path_response.get_json() == summary_trades
    assert full_holdings_response.get_json() == full_holdings
    assert summary_holdings_path_response.get_json() == summary_holdings
    assert legacy_trade_response.status_code == 404
    assert legacy_holdings_response.status_code == 404
    assert calls == [
        ("trades", "full:demo"),
        ("trades", "summary:demo"),
        ("holdings", "full:demo"),
        ("holdings", "summary:demo"),
    ]
