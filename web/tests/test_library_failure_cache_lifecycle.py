from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_support.library_owners import library
from tests_support.run_result_factory import curve_result, ledger_execution
from backtest.artifacts import ArtifactGenerationError
from web.cache_generation import (
    resolve_current_web_cache,
    resolve_latest_web_cache_attempt,
)


def _write_strategy(strategy_dir: Path, config: str = "[strategy]\nmodule = strategy.py\n") -> None:
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(config, encoding="utf-8")


def test_first_failure_is_inactive_and_catalog_reports_latest_attempt(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_strategy(strategy_dir)
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02"], "net_value": [1.23]}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "build_library_run_overrides", lambda strat_cfg, output_root, date_overrides=None: {})
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="failed",
            error="database unavailable",
            meta={"run_id": "demo_failed", "extra": {"status": "running"}},
            run_result=None,
            artifacts=None,
        ),
    )

    result = library._run_single("demo")

    assert result["status"] == "failed"
    assert result["error"] == "database unavailable"
    with pytest.raises(ArtifactGenerationError):
        resolve_current_web_cache(strategy_dir)
    latest = resolve_latest_web_cache_attempt(strategy_dir)
    assert latest is not None
    latest_snapshot, latest_pointer = latest
    assert latest_snapshot.generation_id == "demo_failed"
    assert latest_pointer["status"] == "failed"
    assert latest_pointer["error"] == "database unavailable"
    assert latest_pointer["activated"] is False
    failure_meta = json.loads(
        latest_snapshot.artifact_path("meta.json").read_text(encoding="utf-8")
    )
    assert failure_meta["strategy_module"] == "strategy.py"
    assert failure_meta["extra"]["status"] == "failed"
    assert failure_meta["extra"]["error"] == "database unavailable"
    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "missing"
    assert entry["status"] == "failed"
    assert entry["status_label"] == "回测失败"
    assert entry["error"] == "database unavailable"
    assert entry["latest_attempt_generation_id"] == "demo_failed"
    assert entry["latest_attempt_status"] == "failed"
    assert entry["latest_attempt_error"] == "database unavailable"
    assert entry["latest_attempt_activated"] is False
    assert library.get_library_net_value("demo") is None


def test_failed_rerun_preserves_last_known_good_current(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    _write_strategy(
        strategy_dir,
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240103\n",
    )
    equity_curve = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "sum_net_value": [1.0, 1.1],
        }
    )
    fake_result = curve_result(
        run_id="successful-run",
        equity_curve=equity_curve,
        execution=ledger_execution(equity_curve=equity_curve),
    )
    calls = 0

    def fake_execute_web_backtest(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                status="completed",
                error=None,
                meta={"run_id": "successful-run", "extra": {"status": "running"}},
                run_result=fake_result,
                outcome=None,
            )
        return SimpleNamespace(
            status="failed",
            error="temporary data source failure",
            meta={"run_id": "failed-run", "extra": {"status": "running"}},
            run_result=None,
            outcome=None,
        )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "_resolve_latest_backtest_end_time", lambda start_time: "20240103")
    monkeypatch.setattr(library, "build_library_run_overrides", lambda *args, **kwargs: {})
    monkeypatch.setattr(library, "execute_web_backtest", fake_execute_web_backtest)
    monkeypatch.setattr(library, "_persist_library_outputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    first = library._run_single("demo")
    second = library._run_single("demo")

    snapshot = resolve_current_web_cache(strategy_dir)
    latest = resolve_latest_web_cache_attempt(strategy_dir)
    catalog_entry = library.get_library_catalog()["items"][0]

    assert first["status"] == "completed"
    assert second["status"] == "failed"
    assert snapshot.generation_id == "successful-run"
    assert latest is not None
    latest_snapshot, latest_pointer = latest
    assert latest_snapshot.generation_id == "failed-run"
    assert latest_pointer["status"] == "failed"
    assert latest_pointer["error"] == "temporary data source failure"
    assert latest_pointer["activated"] is False
    assert catalog_entry["cache_status"] == "valid"
    assert catalog_entry["status"] == "completed"
    assert catalog_entry["error"] == ""
    assert catalog_entry["latest_attempt_generation_id"] == "failed-run"
    assert catalog_entry["latest_attempt_status"] == "failed"
    assert catalog_entry["latest_attempt_error"] == "temporary data source failure"
    assert catalog_entry["latest_attempt_activated"] is False
    net_value = library.get_library_net_value("demo")
    assert net_value is not None
    assert net_value["net_value"][-1] == pytest.approx(1.1)
