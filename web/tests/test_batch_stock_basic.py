from __future__ import annotations

from contextlib import contextmanager
import time

import pandas as pd
import pytest

from backtest import engine_batch_support
from core.exceptions import DataSourceError
from web.batch_stock_basic import batch_stock_basic_scope
from web import library_runtime
from web.progress_state import ProgressState


def test_batch_scope_reuses_one_source_read_and_returns_isolated_frames(monkeypatch) -> None:
    calls: list[object] = []
    source_frame = pd.DataFrame(
        {
            "stock_code": ["300001.SZ", "688001.SH"],
            "stock_name": ["Alpha", "Beta"],
        }
    )

    def fake_get_all_stocks(*, cfg, config_loader=None):
        calls.append(cfg)
        return source_frame

    monkeypatch.setattr(engine_batch_support, "get_all_stocks", fake_get_all_stocks)
    first_cfg = object()
    second_cfg = object()

    with batch_stock_basic_scope() as cache:
        first = engine_batch_support.get_all_stocks(cfg=first_cfg)
        first.loc[0, "stock_name"] = "mutated"
        second = engine_batch_support.get_all_stocks(cfg=second_cfg)

        assert second.loc[0, "stock_name"] == "Alpha"
        assert cache.stats().to_dict() == {
            "source_reads": 1,
            "cache_hits": 1,
            "rows": 2,
            "error": None,
        }

    assert calls == [first_cfg]
    assert engine_batch_support.get_all_stocks is fake_get_all_stocks


def test_batch_scope_caches_source_failure_without_retrying_source_read(monkeypatch) -> None:
    calls: list[object] = []

    def fake_get_all_stocks(*, cfg, config_loader=None):
        calls.append(cfg)
        raise DataSourceError("Tushare stock_basic 获取失败: official service unavailable")

    monkeypatch.setattr(engine_batch_support, "get_all_stocks", fake_get_all_stocks)
    first_cfg = object()
    second_cfg = object()

    with batch_stock_basic_scope() as cache:
        with pytest.raises(DataSourceError, match="official service unavailable"):
            engine_batch_support.get_all_stocks(cfg=first_cfg)
        with pytest.raises(DataSourceError, match="official service unavailable"):
            engine_batch_support.get_all_stocks(cfg=second_cfg)

        assert cache.stats().to_dict() == {
            "source_reads": 1,
            "cache_hits": 1,
            "rows": None,
            "error": "Tushare stock_basic 获取失败: official service unavailable",
        }

    assert calls == [first_cfg]
    assert engine_batch_support.get_all_stocks is fake_get_all_stocks


def test_library_worker_reuses_stock_basic_across_strategies(monkeypatch) -> None:
    calls: list[object] = []
    progress_state = ProgressState(current_key="current", include_task_metadata=True)

    def fake_get_all_stocks(*, cfg, config_loader=None):
        calls.append(cfg)
        return pd.DataFrame({"stock_code": ["300001.SZ"], "stock_name": ["Alpha"]})

    def fake_run_single(name: str, date_overrides=None) -> dict[str, object]:
        frame = engine_batch_support.get_all_stocks(cfg=object())
        return {
            "name": name,
            "status": "completed",
            "duration_sec": 0.0,
            "stock_count": len(frame),
        }

    monkeypatch.setattr(engine_batch_support, "get_all_stocks", fake_get_all_stocks)
    monkeypatch.setattr(library_runtime, "_run_single", fake_run_single)
    monkeypatch.setattr(library_runtime, "_progress_state", progress_state)
    monkeypatch.setattr(
        library_runtime,
        "list_library",
        lambda: [{"name": "alpha"}, {"name": "beta"}],
    )

    assert library_runtime.start_library_backtest(["alpha", "beta"]) is True
    deadline = time.time() + 2
    while progress_state.is_running() and time.time() < deadline:
        time.sleep(0.01)

    progress = progress_state.snapshot(deep=True)
    assert progress["running"] is False
    assert progress["completed"] == 2
    assert [item["stock_count"] for item in progress["results"]] == [1, 1]
    assert len(calls) == 1


def test_library_worker_marks_all_strategies_failed_when_batch_scope_cannot_start(monkeypatch) -> None:
    progress_state = ProgressState(current_key="current", include_task_metadata=True)
    run_calls: list[str] = []

    @contextmanager
    def failing_scope():
        raise RuntimeError("stock_basic cache setup failed")
        yield

    def fake_run_single(name: str, date_overrides=None) -> dict[str, object]:
        run_calls.append(name)
        return {"name": name, "status": "completed", "duration_sec": 0.0}

    monkeypatch.setattr(library_runtime, "batch_stock_basic_scope", failing_scope)
    monkeypatch.setattr(library_runtime, "_progress_state", progress_state)
    monkeypatch.setattr(library_runtime, "_run_single", fake_run_single)
    monkeypatch.setattr(
        library_runtime,
        "list_library",
        lambda: [{"name": "alpha"}, {"name": "beta"}],
    )

    assert library_runtime.start_library_backtest(["alpha", "beta"]) is True
    deadline = time.time() + 2
    while progress_state.is_running() and time.time() < deadline:
        time.sleep(0.01)

    progress = progress_state.snapshot(deep=True)
    assert progress["running"] is False
    assert progress["completed"] == 2
    assert run_calls == []
    assert [item["name"] for item in progress["results"]] == ["alpha", "beta"]
    assert [item["status"] for item in progress["results"]] == ["failed", "failed"]
    assert {item["error"] for item in progress["results"]} == {"stock_basic cache setup failed"}
