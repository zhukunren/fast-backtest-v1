from __future__ import annotations

from pathlib import Path

from web.task_coordinator import resolve_task_state_path


def test_default_task_state_path_stays_inside_writable_web_data_directory() -> None:
    web_root = Path(__file__).resolve().parents[1]

    assert resolve_task_state_path({}) == web_root / "data" / "task_state.sqlite3"


def test_explicit_task_state_database_override_is_preserved() -> None:
    project_root = Path(__file__).resolve().parents[2]

    assert resolve_task_state_path({"WEB_TASK_STATE_DB": "output/custom-state.sqlite3"}) == (
        project_root / "output" / "custom-state.sqlite3"
    )
