from __future__ import annotations

import json
from pathlib import Path

import pytest

import web.log_service as log_service
import web.run_event_service as run_event_service
from core.run_events import JsonlRunEventLogger
from web.server import app


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_log_event_writes_jsonl_and_tail_reads_recent_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_LOG_DIR", str(tmp_path))

    log_service.log_event("INFO", "unit", "first event", value=1, password="secret")
    log_service.log_event("ERROR", "unit", "second event", values=["A", "B"])

    files = log_service.list_log_files()
    assert len(files) == 1
    assert files[0]["name"].startswith("web-")
    assert files[0]["name"].endswith(".jsonl")

    raw_events = _read_jsonl(tmp_path / files[0]["name"])
    assert raw_events[0]["message"] == "first event"
    assert raw_events[0]["password"] == "<redacted>"
    assert raw_events[1]["level"] == "ERROR"

    tail = log_service.read_log_tail(lines=1)
    assert tail["file"] == files[0]["name"]
    assert tail["lines"] == 1
    assert [entry["message"] for entry in tail["entries"]] == ["second event"]


def test_default_log_dir_uses_central_operational_root(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_LOG_DIR", raising=False)
    monkeypatch.setenv("JSHS_LOG_ROOT", str(tmp_path / "logs"))

    assert log_service.get_log_dir() == (tmp_path / "logs" / "web").resolve()


def test_read_log_tail_rejects_unsafe_file_names(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_LOG_DIR", str(tmp_path))
    log_service.log_event("INFO", "unit", "safe event")

    with pytest.raises(ValueError):
        log_service.read_log_tail("../web-20260101.jsonl")
    with pytest.raises(ValueError):
        log_service.read_log_tail("web-20260101.log")


def test_log_api_lists_files_reads_tail_and_accepts_client_events(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_LOG_DIR", str(tmp_path))
    log_service.log_event("INFO", "unit", "api seed")

    with app.test_client() as client:
        list_response = client.get("/api/logs")
        assert list_response.status_code == 200
        list_payload = list_response.get_json()
        assert list_payload["latest"].endswith(".jsonl")
        assert list_payload["files"][0]["name"] == list_payload["latest"]

        tail_response = client.get("/api/logs/tail?lines=20")
        assert tail_response.status_code == 200
        tail_payload = tail_response.get_json()
        assert tail_payload["file"] == list_payload["latest"]
        assert any(entry["message"] == "api seed" for entry in tail_payload["entries"])

        client_log_response = client.post(
            "/api/logs/client",
            json={
                "level": "WARNING",
                "event": "catalog_error",
                "page": "catalog",
                "message": "client side warning",
                "fields": {"strategy": "demo"},
            },
        )
        assert client_log_response.status_code == 200
        assert client_log_response.get_json() == {"ok": True}

        invalid_response = client.get("/api/logs/tail?file=../secret.jsonl")
        assert invalid_response.status_code == 400

        missing_response = client.get("/api/logs/tail?file=web-19990101.jsonl")
        assert missing_response.status_code == 404

    events = _read_jsonl(tmp_path / list_payload["latest"])
    assert any(event["message"] == "client side warning" for event in events)


def test_run_event_service_lists_and_reads_safe_event_files(tmp_path: Path) -> None:
    event_path = tmp_path / "run_events_run-1.jsonl"
    logger = JsonlRunEventLogger(path=event_path, run_id="run-1")
    logger.emit("run_start")
    with logger.span("load_data"):
        pass
    logger.emit("run_complete", status="completed")

    files = run_event_service.list_run_event_files(roots=[tmp_path])
    payload = run_event_service.read_run_events_for_web("run-1", lines=20, roots=[tmp_path])

    assert [item["run_id"] for item in files] == ["run-1"]
    assert payload["run_id"] == "run-1"
    assert payload["summary"]["status"] == "completed"
    assert payload["summary"]["stage_count"] == 1

    with pytest.raises(ValueError):
        run_event_service.read_run_events_for_web("../run-1", roots=[tmp_path])


def test_run_event_api_lists_and_reads_tail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_RUN_EVENT_ROOTS", str(tmp_path))
    logger = JsonlRunEventLogger(path=tmp_path / "run_events_api-run.jsonl", run_id="api-run")
    logger.emit("run_start")
    logger.emit("run_complete", status="completed")

    with app.test_client() as client:
        list_response = client.get("/api/run_events")
        assert list_response.status_code == 200
        list_payload = list_response.get_json()
        assert list_payload["latest"] == "api-run"
        assert list_payload["files"][0]["run_id"] == "api-run"

        detail_response = client.get("/api/run_events/api-run?lines=5")
        assert detail_response.status_code == 200
        detail_payload = detail_response.get_json()
        assert detail_payload["summary"]["status"] == "completed"

        invalid_response = client.get("/api/run_events/bad$id")
        assert invalid_response.status_code == 400

        missing_response = client.get("/api/run_events/missing-run")
        assert missing_response.status_code == 404
