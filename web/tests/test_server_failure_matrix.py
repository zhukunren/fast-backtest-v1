from __future__ import annotations

import json
from pathlib import Path

import pytest

import web.server as server
from web.frontend_entry import frontend_manifest_path, resolve_vue_entry

pytestmark = pytest.mark.integration


_LIBRARY_GETTERS = (
    ("detail", "get_library_detail", "not found"),
    ("net_value", "get_library_net_value", "no data"),
    ("trades/records", "get_library_trades", "no data"),
    ("trades/summary", "get_library_trades_summary", "no data"),
    ("holdings/history", "get_library_daily_holdings", "no data"),
    ("holdings/summary", "get_library_daily_holdings_summary", "no data"),
    ("metrics", "compute_derived_metrics", "no data"),
    ("analysis", "compute_library_strategy_analysis", "no data"),
    ("market_states", "compute_library_market_state_analysis", "no data"),
    ("review", "compute_library_holding_trade_review", "no data"),
    ("report", "build_strategy_markdown_report", "not found"),
)


def test_vue_entry_requires_canonical_manifest_entry(tmp_path: Path) -> None:
    dist_root = tmp_path / "dist"
    manifest_path = dist_root / ".vite" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{broken", encoding="utf-8")

    assert resolve_vue_entry(
        frontend_dist_root=dist_root,
        dev_server="",
        asset_url=lambda name: f"/assets/{name}",
    ) is None

    manifest_path.write_text(
        json.dumps({"fallback": {"isEntry": True, "file": "app.js"}}),
        encoding="utf-8",
    )

    assert resolve_vue_entry(
        frontend_dist_root=dist_root,
        dev_server="",
        asset_url=lambda name: f"/assets/{name}",
    ) is None

    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {
                    "isEntry": True,
                    "file": "app.js",
                    "css": ["app.css", "", 7],
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_vue_entry(
        frontend_dist_root=dist_root,
        dev_server="",
        asset_url=lambda name: f"/assets/{name}",
    ) == {
        "mode": "prod",
        "css_urls": ["/assets/app.css"],
        "favicon_url": "/assets/favicon.svg",
        "js_url": "/assets/app.js",
    }

    alternate_dist = tmp_path / "alternate-dist"
    alternate_dist.mkdir()
    root_manifest = alternate_dist / "manifest.json"
    root_manifest.write_text("{}", encoding="utf-8")
    assert frontend_manifest_path(alternate_dist) == alternate_dist / ".vite" / "manifest.json"


def test_removed_legacy_web_routes_return_not_found() -> None:
    with server.app.test_client() as client:
        responses = [
            client.get("/strategy/demo/modern"),
            client.get("/api/compare?names=alpha,beta"),
            client.get("/api/library/demo/position_changes"),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]


@pytest.mark.parametrize(("route", "function_name", "missing_error"), _LIBRARY_GETTERS)
def test_library_data_endpoints_reject_invalid_names_and_missing_data(
    route: str, function_name: str, missing_error: str, monkeypatch
) -> None:
    monkeypatch.setattr(server, function_name, lambda *args, **kwargs: None)

    with server.app.test_client() as client:
        invalid = client.get(f"/api/library/bad.name/{route}")
        missing = client.get(f"/api/library/missing/{route}")

    assert invalid.status_code == 400
    assert invalid.get_json() == {"error": "invalid name"}
    assert missing.status_code == 404
    assert missing.get_json() == {"error": missing_error}


def test_library_report_and_export_content_negotiation(monkeypatch) -> None:
    monkeypatch.setattr(server, "build_strategy_markdown_report", lambda name: "# Report\n")
    monkeypatch.setattr(server, "build_library_catalog_csv", lambda: "name\ndemo\n")
    monkeypatch.setattr(server, "build_library_export_payload", lambda: {"items": ["demo"]})

    with server.app.test_client() as client:
        markdown = client.get("/api/library/demo/report")
        report_json = client.get("/api/library/demo/report?format=json")
        csv_export = client.get("/api/library/export?format=csv")
        json_export = client.get("/api/library/export")
        invalid_export = client.get("/api/library/export?format=xml")

    assert markdown.status_code == 200
    assert markdown.get_data(as_text=True) == "# Report\n"
    assert report_json.get_json() == {"content": "# Report\n"}
    assert csv_export.get_data(as_text=True) == "name\ndemo\n"
    assert json_export.get_json() == {"items": ["demo"]}
    assert invalid_export.status_code == 400


def test_library_cache_and_config_failure_contracts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "clear_library_cache", lambda name: name == "demo")
    monkeypatch.setattr(server, "LIBRARY_ROOT", tmp_path)
    config_dir = tmp_path / "broken"
    config_dir.mkdir()
    (config_dir / "config.ini").write_text("[token]\nsecret=x\n", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_broken_config(path: Path, *args, **kwargs):
        if path == config_dir / "config.ini":
            raise OSError("unreadable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_broken_config)

    with server.app.test_client() as client:
        invalid_cache = client.delete("/api/library/bad.name/cache")
        cleared = client.delete("/api/library/demo/cache")
        missing_config = client.get("/api/library/missing/config")
        broken_config = client.get("/api/library/broken/config")

    assert invalid_cache.status_code == 400
    assert cleared.get_json() == {"cleared": True}
    assert missing_config.status_code == 404
    assert broken_config.status_code == 500
    assert broken_config.get_json() == {"error": "read error"}


def test_log_api_validates_limits_and_translates_reader_errors(monkeypatch) -> None:
    monkeypatch.setattr(server, "get_log_dir", lambda: Path("logs"))
    monkeypatch.setattr(
        server,
        "list_log_files",
        lambda *, limit: [{"name": f"log-{limit}.txt"}],
    )

    with server.app.test_client() as client:
        invalid_limit = client.get("/api/logs?limit=many")
        clamped_low = client.get("/api/logs?limit=0")
        clamped_high = client.get("/api/logs?limit=999")

        monkeypatch.setattr(server, "read_log_tail", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid log name")))
        invalid_file = client.get("/api/logs/tail?file=../bad")
        invalid_lines = client.get("/api/logs/tail?lines=many")
        monkeypatch.setattr(server, "read_log_tail", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
        missing_file = client.get("/api/logs/tail?file=missing.log")

    assert invalid_limit.status_code == 400
    assert clamped_low.get_json()["latest"] == "log-1.txt"
    assert clamped_high.get_json()["latest"] == "log-200.txt"
    assert invalid_file.status_code == 400
    assert invalid_lines.status_code == 400
    assert missing_file.status_code == 404


def test_client_log_normalizes_payload_and_optional_fields(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(server, "log_event", lambda *args, **kwargs: calls.append((args, kwargs)))

    with server.app.test_client() as client:
        invalid = client.post("/api/logs/client", json=["not", "an", "object"])
        accepted = client.post(
            "/api/logs/client",
            json={"message": " ", "level": " warn ", "fields": ["bad"]},
        )

    assert invalid.status_code == 400
    assert accepted.get_json() == {"ok": True}
    args, kwargs = calls[-1]
    assert args == ("WARN", "client", "")
    assert kwargs["fields"] == {}


def test_run_event_api_validates_limits_and_translates_reader_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "list_run_event_files",
        lambda *, limit: [{"run_id": f"run-{limit}"}],
    )

    with server.app.test_client() as client:
        invalid_limit = client.get("/api/run_events?limit=nope")
        clamped_low = client.get("/api/run_events?limit=0")
        clamped_high = client.get("/api/run_events?limit=999")
        invalid_lines = client.get("/api/run_events/demo?lines=nope")
        monkeypatch.setattr(server, "read_run_events_for_web", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid run id")))
        invalid_id = client.get("/api/run_events/bad.id")
        monkeypatch.setattr(server, "read_run_events_for_web", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
        missing = client.get("/api/run_events/missing")

    assert invalid_limit.status_code == 400
    assert clamped_low.get_json()["latest"] == "run-1"
    assert clamped_high.get_json()["latest"] == "run-200"
    assert invalid_lines.status_code == 400
    assert invalid_id.status_code == 400
    assert missing.status_code == 404


def test_compare_endpoint_rejects_bad_inputs_and_missing_data(monkeypatch) -> None:
    monkeypatch.setattr(server, "compute_enhanced_compare", lambda *args, **kwargs: None)
    path = "/api/compare/enhanced"

    with server.app.test_client() as client:
        too_few = client.get(f"{path}?names=one")
        too_many = client.get(f"{path}?names=one,two,three,four,five")
        duplicate = client.get(f"{path}?names=one,ONE")
        invalid = client.get(f"{path}?names=good,bad.name")
        missing = client.get(f"{path}?names=one,two")

    assert too_few.status_code == 400
    assert too_many.status_code == 400
    assert duplicate.status_code == 400
    assert invalid.status_code == 400
    assert missing.status_code == 404


def test_enhanced_compare_requires_two_usable_curves_and_preserves_errors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "compute_enhanced_compare",
        lambda names: {
            "requested_names": names,
            "names": ["ready"],
            "omitted": ["missing"],
            "errors": [
                {
                    "name": "missing",
                    "code": "cache_unavailable",
                    "message": "no current cache generation is available",
                }
            ],
        },
    )

    with server.app.test_client() as client:
        response = client.get("/api/compare/enhanced?names=ready,missing")

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["error"] == "at least 2 valid curves required"
    assert payload["requested_names"] == ["ready", "missing"]
    assert payload["omitted"] == ["missing"]
    assert payload["errors"][0]["code"] == "cache_unavailable"


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"names": ["demo"], "date_overrides": []}, "date_overrides must be an object"),
        ({"names": ["demo"], "date_overrides": {"bad.name": {}}}, "invalid name: bad.name"),
        ({"names": ["demo"], "date_overrides": {"other": {}}}, "date override is not selected: other"),
        ({"names": ["demo"], "date_overrides": {"demo": []}}, "date_overrides[demo] must be an object"),
        ({"names": ["demo"], "date_overrides": {"demo": {"start_time": "bad"}}}, "invalid date: bad"),
        (
            {"names": ["demo"], "date_overrides": {"demo": {"start_time": "2026-02-02", "end_time": "2026-02-01"}}},
            "invalid date range for demo",
        ),
    ],
)
def test_backtest_run_rejects_invalid_date_override_contracts(
    payload: dict[str, object], expected_error: str, monkeypatch
) -> None:
    monkeypatch.setattr(server, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server, "get_library_catalog", lambda: {"items": [{"name": "demo"}, {"name": "other"}]})
    monkeypatch.setattr(server, "_parse_strategy_config", lambda path: {})

    with server.app.test_client() as client:
        response = client.post("/api/backtest/run", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": expected_error}


def test_backtest_run_deduplicates_names_and_reports_start_rejection(monkeypatch) -> None:
    calls: list[tuple[list[str], object]] = []
    monkeypatch.setattr(server, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server, "get_library_catalog", lambda: {"items": [{"name": "demo"}]})
    monkeypatch.setattr(
        server,
        "start_library_backtest",
        lambda names, *, date_overrides: calls.append((names, date_overrides)) or False,
    )

    with server.app.test_client() as client:
        response = client.post("/api/backtest/run", json={"names": ["demo", "demo"]})

    assert response.status_code == 400
    assert response.get_json() == {"error": "no strategies or already running"}
    assert calls == [(["demo"], None)]
