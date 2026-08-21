from __future__ import annotations

from pathlib import Path

import pytest

import web.maintenance_common as maintenance_common
import web.maintenance_data as maintenance_data
import web.repository_update as repository_update
from web.maintenance_service import MaintenanceRequestError


@pytest.fixture(autouse=True)
def _reset_progress():
    maintenance_common.MAINTENANCE_PROGRESS.reset(deep=True)
    maintenance_common.set_last_task_kind("")
    yield
    maintenance_common.MAINTENANCE_PROGRESS.reset(deep=True)
    maintenance_common.set_last_task_kind("")


def _materialize_update_scripts(root: Path) -> None:
    tools_dir = root / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "updata_data.py").write_text("", encoding="utf-8")
    (tools_dir / "build_panel_parquet.py").write_text("", encoding="utf-8")


def test_build_data_update_tasks_uses_fixed_scripts_and_order(tmp_path, monkeypatch):
    _materialize_update_scripts(tmp_path)
    monkeypatch.setattr(maintenance_data, "PROJECT_ROOT", tmp_path)

    tasks = maintenance_data.build_data_update_tasks({
        "start_date": "2026-07-01",
        "end_date": "20260724",
        "update_stock": True,
        "update_index": True,
        "update_etf": False,
        "rebuild_panel": True,
        "max_workers": 4,
    })

    assert [task.kind for task in tasks] == ["update_stock", "update_index", "rebuild_panel"]
    assert tasks[0].command[-1] == "--stock"
    assert tasks[1].command[-1] == "--index"
    assert tasks[2].command[-1] == "--verify"
    assert tasks[2].command[-2].endswith("build_panel_parquet.py")
    assert tasks[0].command[tasks[0].command.index("--start-date") + 1] == "20260701"
    assert tasks[0].command[tasks[0].command.index("--max-workers") + 1] == "4"


def test_build_data_update_tasks_scopes_codes_to_selected_type(tmp_path, monkeypatch):
    _materialize_update_scripts(tmp_path)
    monkeypatch.setattr(maintenance_data, "PROJECT_ROOT", tmp_path)

    tasks = maintenance_data.build_data_update_tasks({
        "start_date": "20260701",
        "end_date": "20260724",
        "symbol_type": "etf",
        "codes": "510300.SH, 510300.sh;159915.SZ",
        "dry_run": True,
        "rebuild_panel": True,
    })

    assert [task.kind for task in tasks] == ["update_etf"]
    assert tasks[0].command[-2:] == ("510300.SH", "159915.SZ")
    assert "--dry-run" in tasks[0].command


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"start_date": "bad", "end_date": "20260724"}, "日期格式"),
        ({"start_date": "20260725", "end_date": "20260724"}, "不能晚于"),
        ({"start_date": "20260701", "end_date": "20260724", "max_workers": 0}, "1 到 32"),
        ({
            "start_date": "20260701",
            "end_date": "20260724",
            "update_stock": False,
            "update_index": False,
            "update_etf": False,
        }, "至少选择"),
    ),
)
def test_build_data_update_tasks_rejects_invalid_payload(tmp_path, monkeypatch, payload, message):
    _materialize_update_scripts(tmp_path)
    monkeypatch.setattr(maintenance_data, "PROJECT_ROOT", tmp_path)

    with pytest.raises(MaintenanceRequestError, match=message):
        maintenance_data.build_data_update_tasks(payload)


def test_repository_status_reports_github_target_and_redacts_credentials(monkeypatch):
    responses = {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("symbolic-ref", "--quiet", "--short", "HEAD"): "main",
        ("rev-parse", "HEAD"): "a" * 40,
        ("rev-parse", "--short=12", "HEAD"): "a" * 12,
        ("log", "-1", "--format=%s"): "release",
        ("remote", "get-url", "origin"): "https://user:secret@github.com/acme/repo.git",
        ("status", "--porcelain=v1"): "",
        ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"): "origin/main",
        ("rev-parse", "refs/remotes/origin/main"): "b" * 40,
        ("rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/main"): "0\t3",
    }

    def _fake_git_text(*args, required=True):
        del required
        return responses.get(tuple(args), "")

    monkeypatch.setattr(repository_update, "_git_text", _fake_git_text)

    status = repository_update.get_repository_status()

    assert status["available"] is True
    assert status["can_update"] is True
    assert status["update_available"] is True
    assert status["behind"] == 3
    assert status["target_ref"] == "origin/main"
    assert status["remote_url"] == "https://github.com/acme/repo.git"
    assert "secret" not in status["remote_url"]


def test_repository_status_refuses_dirty_worktree(monkeypatch):
    def _fake_git_text(*args, required=True):
        del required
        values = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "main",
            ("remote", "get-url", "origin"): "git@github.com:acme/repo.git",
            ("status", "--porcelain=v1"): " M web/server.py",
            ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"): "origin/main",
        }
        return values.get(tuple(args), "")

    monkeypatch.setattr(repository_update, "_git_text", _fake_git_text)

    status = repository_update.get_repository_status()

    assert status["dirty"] is True
    assert status["can_update"] is False
    with pytest.raises(MaintenanceRequestError, match="未提交改动"):
        repository_update._validate_version_preflight()


def test_version_update_fetches_then_fast_forwards(monkeypatch):
    preflight = {
        "available": True,
        "can_update": True,
        "dirty": False,
        "diverged": False,
        "branch": "main",
        "update_branch": "main",
        "revision": "a" * 40,
        "ahead": 0,
        "behind": 2,
    }
    commands: list[tuple[str, ...]] = []

    class _ImmediateThread:
        def __init__(self, *, target, **kwargs):
            del kwargs
            self.target = target

        def start(self):
            self.target()

    def _run(task, *, timeout=None):
        del timeout
        commands.append(task.command)
        return {
            "task": task.kind,
            "label": task.label,
            "status": "completed",
            "return_code": 0,
            "duration_sec": 0.1,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    monkeypatch.setattr(repository_update, "_validate_version_preflight", lambda: dict(preflight))
    monkeypatch.setattr(repository_update, "run_command", _run)
    monkeypatch.setattr(repository_update, "_git_text", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(repository_update, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(maintenance_common, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(repository_update.threading, "Thread", _ImmediateThread)

    assert repository_update.start_version_update() is True

    progress = maintenance_common.get_maintenance_progress()
    assert progress["running"] is False
    assert progress["task_kind"] == "version_update"
    assert [result["status"] for result in progress["results"]] == ["completed", "completed"]
    assert commands == [
        ("git", "fetch", "--prune", "origin", "main"),
        ("git", "merge", "--ff-only", "refs/remotes/origin/main"),
    ]
    assert progress["results"][-1]["restart_required"] is True
