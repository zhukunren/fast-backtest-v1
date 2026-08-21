from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_support.library_owners import library
import web.run_config as run_config
import web.server as server_module
from web import library_metadata
from tests_support.run_result_factory import curve_result, ledger_execution, vectorized_execution
from tests_support.web_cache import publish_web_cache_fixture
from web.exporter import build_trades_payload
from web.server import app


def _snapshot_progress() -> dict:
    return library.get_progress()


def _vectorized_run_result(
    *,
    equity_curve: pd.DataFrame | None = None,
    processed_frames: dict[str, pd.DataFrame] | None = None,
    trade_segments_df: pd.DataFrame | None = None,
):
    execution = vectorized_execution(
        equity_curve=equity_curve,
        processed_frames=processed_frames,
        trade_segments_df=trade_segments_df,
    )
    return curve_result(equity_curve=equity_curve, execution=execution)


def _ledger_run_result(
    *,
    equity_curve: pd.DataFrame | None = None,
    fills_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
    orders_df: pd.DataFrame | None = None,
    risk_orders_df: pd.DataFrame | None = None,
    trade_segments_df: pd.DataFrame | None = None,
):
    execution = ledger_execution(
        equity_curve=equity_curve,
        fills_df=fills_df,
        risk_fills_df=risk_fills_df,
        orders_df=orders_df,
        risk_orders_df=risk_orders_df,
        trade_segments_df=trade_segments_df,
    )
    return curve_result(equity_curve=equity_curve, engine_id="broker", execution=execution)

def test_market_state_score_review_and_enhanced_compare_api(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"

    def write_strategy(name: str, net_values: list[float]) -> None:
        strategy_dir = library_root / name
        cache_dir = strategy_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (strategy_dir / "strategy.py").write_text("STRATEGY_META = {'top_n': 2}\n", encoding="utf-8")
        (strategy_dir / "config.ini").write_text(
            "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240108\n",
            encoding="utf-8",
        )
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "strategy_module": "strategy.py",
                    "backtest": {"start_time": "20240101", "end_time": "20240108"},
                    "extra": {"status": "completed"},
                    "result": {"cum_return": net_values[-1] / net_values[0] - 1.0},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
        (cache_dir / "net_value.json").write_text(
            json.dumps(
                {
                    "dates": dates,
                    "net_value": net_values,
                    "benchmark_net_value": [1.0, 1.04, 1.02, 1.08, 1.01],
                    "benchmark_label": "Demo Benchmark",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (cache_dir / "trades.json").write_text(
            json.dumps(
                {
                    "records": [
                        {"trade_date": "2024-01-03", "stock_code": "AAA.SZ", "pnl": 0.04, "side": "sell"},
                        {"trade_date": "2024-01-05", "stock_code": "BBB.SZ", "pnl": -0.02, "side": "sell"},
                    ],
                    "execution_records": [
                        {"日期": "2024-01-03", "股票代码": "AAA.SZ", "方向": "卖出", "来源": "策略"},
                        {"日期": "2024-01-05", "股票代码": "BBB.SZ", "方向": "卖出", "来源": "风控"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (cache_dir / "daily_holdings.json").write_text(
            json.dumps(
                {
                    "dates": dates,
                    "holdings_by_date": {
                        "2024-01-03": [
                            {
                                "code": "AAA.SZ",
                                "name": "Alpha",
                                "holding_value": 2000.0,
                                "unrealized_pnl": 120.0,
                                "effective_position_ratio": 0.2,
                            }
                        ],
                        "2024-01-05": [
                            {
                                "code": "BBB.SZ",
                                "name": "Beta",
                                "holding_value": 1500.0,
                                "unrealized_pnl": -80.0,
                                "effective_position_ratio": 0.15,
                            }
                        ],
                    },
                    "summary_by_date": {
                        "2024-01-03": {"asset": 10000.0, "position_value": 2000.0},
                        "2024-01-05": {"asset": 10000.0, "position_value": 1500.0},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (cache_dir / "position_changes.json").write_text(
            json.dumps([{"trade_date": "2024-01-05", "reason": "risk"}], ensure_ascii=False),
            encoding="utf-8",
        )
        publish_web_cache_fixture(
            strategy_dir,
            flat_cache_dir=cache_dir,
            generation_id=f"fixture-{name}",
        )

    write_strategy("demo_a", [1.0, 1.03, 1.01, 1.06, 1.04])
    write_strategy("demo_b", [1.0, 0.99, 1.0, 1.01, 1.02])
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    market_state = library.compute_library_market_state_analysis("demo_a")
    assert market_state is not None
    assert market_state["states"]["trend"]
    analysis = library.compute_library_strategy_analysis("demo_a")
    assert analysis is not None
    assert analysis["score"]["components"]["regime_robustness"] >= 0
    assert analysis["market_state"]["benchmark_label"] == "Demo Benchmark"
    review = library.compute_library_holding_trade_review("demo_a")
    assert review is not None
    assert review["overview"]["unique_stock_count"] == 2
    assert review["overview"]["risk_event_count"] == 1
    alpha_review = next(item for item in review["stocks"] if item["code"] == "AAA.SZ")
    assert alpha_review["latest_holding_value"] is None
    assert alpha_review["latest_unrealized_pnl"] is None
    assert alpha_review["realized_pnl"] is None
    assert alpha_review["realized_return_sum"] == 0.04
    assert alpha_review["total_pnl"] is None
    assert review["overview"]["position_change_count"] == 1
    assert "top_trades" not in review
    assert "daily_trades" not in review
    assert "position_changes" not in review
    compare = library.compute_enhanced_compare(["demo_a", "missing", "demo_b"])
    assert compare is not None
    assert compare["requested_names"] == ["demo_a", "missing", "demo_b"]
    assert compare["names"] == ["demo_a", "demo_b"]
    assert compare["omitted"] == ["missing"]
    assert compare["errors"] == [
        {
            "name": "missing",
            "code": "cache_unavailable",
            "message": "no current cache generation is available",
        }
    ]
    assert compare["evaluation_window"] == {
        "basis": "shared_daily_returns",
        "start_date": "2024-01-03",
        "end_date": "2024-01-08",
        "date_count": 4,
    }
    assert compare["correlation_matrix"]["names"] == ["demo_a", "demo_b"]
    assert len(compare["rankings"]) == 2

    with app.test_client() as client:
        response = client.get("/api/library/demo_a/review")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["overview"]["unique_stock_count"] == 2

        compare_response = client.get(
            "/api/compare/enhanced?names=demo_a,missing,demo_b"
        )
        assert compare_response.status_code == 200
        compare_payload = compare_response.get_json()
        assert compare_payload["omitted"] == ["missing"]
        assert compare_payload["errors"][0]["code"] == "cache_unavailable"
        assert compare_payload["correlation_matrix"]["names"] == ["demo_a", "demo_b"]
        assert len(compare_payload["rankings"]) == 2


def test_enhanced_compare_keeps_zero_return_and_drawdown_as_real_values(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    for name, values in (("zero", [1.0, 1.0]), ("loss", [1.0, 0.9])):
        strategy_dir = library_root / name
        cache_dir = strategy_dir / "cache"
        cache_dir.mkdir(parents=True)
        (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
        (strategy_dir / "config.ini").write_text(
            "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240103\n",
            encoding="utf-8",
        )
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "strategy_module": "strategy.py",
                    "backtest": {"start_time": "20240101", "end_time": "20240103"},
                    "extra": {"status": "completed"},
                    "result": {
                        "cum_return": values[-1] - 1.0,
                        "max_drawdown": min(0.0, values[-1] - 1.0),
                    },
                }
            ),
            encoding="utf-8",
        )
        (cache_dir / "net_value.json").write_text(
            json.dumps(
                {
                    "dates": ["2024-01-02", "2024-01-03"],
                    "net_value": values,
                }
            ),
            encoding="utf-8",
        )
        publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, generation_id=f"run-{name}")

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    result = library.compute_enhanced_compare(["zero", "loss"])

    assert result is not None
    assert result["best"]["return"]["name"] == "zero"
    assert result["best"]["drawdown"]["name"] == "zero"


def test_enhanced_compare_excludes_unpaired_first_observation_from_daily_returns(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    cases = {
        "early": (
            ["2024-01-02", "2024-01-03", "2024-01-04"],
            [1.0, 1.0, 2.0],
        ),
        "late": (
            ["2024-01-03", "2024-01-04", "2024-01-05"],
            [2.0, 2.0, 2.0],
        ),
    }
    for name, (dates, values) in cases.items():
        strategy_dir = library_root / name
        cache_dir = strategy_dir / "cache"
        cache_dir.mkdir(parents=True)
        (strategy_dir / "strategy.py").write_text(
            "STRATEGY_META = {}\n",
            encoding="utf-8",
        )
        (strategy_dir / "config.ini").write_text(
            "[strategy]\nmodule = strategy.py\n"
            "[backtest]\nstart_time = 20240101\nend_time = 20240105\n",
            encoding="utf-8",
        )
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "strategy_module": "strategy.py",
                    "backtest": {
                        "start_time": "20240101",
                        "end_time": "20240105",
                    },
                    "extra": {"status": "completed"},
                }
            ),
            encoding="utf-8",
        )
        (cache_dir / "net_value.json").write_text(
            json.dumps({"dates": dates, "net_value": values}),
            encoding="utf-8",
        )
        publish_web_cache_fixture(
            strategy_dir,
            flat_cache_dir=cache_dir,
            generation_id=f"run-{name}",
        )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    result = library.compute_enhanced_compare(["early", "late"])

    assert result is not None
    assert result["equal_weight"]["dates"] == ["2024-01-04"]
    assert result["equal_weight"]["net_value"] == [1.5]
    assert result["evaluation_window"] == {
        "basis": "shared_daily_returns",
        "start_date": "2024-01-04",
        "end_date": "2024-01-04",
        "date_count": 1,
    }
    assert result["correlation_matrix"]["values"][0][1] is None


def test_resolve_latest_backtest_end_time_clips_to_latest_stock_data(monkeypatch):
    monkeypatch.setattr(library, "_today_yyyymmdd", lambda: "20260319")
    monkeypatch.setattr(library, "_latest_trade_calendar_day", lambda start_date, end_date: "20260319")
    monkeypatch.setattr(library, "_probe_latest_stock_data_date", lambda start_date, end_date: "20260318")

    end_time = library._resolve_latest_backtest_end_time("20240101")

    assert end_time == "20260318"


def test_resolve_latest_backtest_end_time_uses_trade_calendar_without_stock_data(monkeypatch):
    monkeypatch.setattr(library, "_today_yyyymmdd", lambda: "20260319")
    monkeypatch.setattr(library, "_latest_trade_calendar_day", lambda start_date, end_date: "20260319")
    monkeypatch.setattr(library, "_probe_latest_stock_data_date", lambda start_date, end_date: "")

    end_time = library._resolve_latest_backtest_end_time("20240101")

    assert end_time == "20260319"


def test_latest_trade_calendar_day_passes_resolved_config(monkeypatch) -> None:
    import config
    import data

    cfg = object()
    captured: dict[str, object] = {}

    def _calendar(start_date, end_date, *, cfg):
        captured.update({"start_date": start_date, "end_date": end_date, "cfg": cfg})
        return pd.DataFrame({"cal_date": pd.to_datetime(["2026-03-19"])})

    monkeypatch.setattr(config, "get_config", lambda reload=True: cfg)
    monkeypatch.setattr(data, "get_trade_calendar", _calendar)

    assert library_metadata._latest_trade_calendar_day("20240101", "20260319") == "20260319"
    assert captured == {
        "start_date": "20240101",
        "end_date": "20260319",
        "cfg": cfg,
    }


def test_resolve_module_file_rejects_path_traversal_and_private_parts(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    module_path = project_root / "strategy_library" / "builtin" / "demo.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("STRATEGY_META = {}\n", encoding="utf-8")
    monkeypatch.setattr(library, "_project_root", project_root)

    assert library._resolve_module_file("strategy_library.builtin.demo") == module_path.resolve()
    assert library._resolve_module_file("strategy_library.builtin..../../config") is None
    assert library._resolve_module_file("strategy_library.builtin._demo") is None
    assert library._resolve_module_file("strategy_library/builtin/demo") is None


def test_get_progress_returns_isolated_snapshot():
    original_progress = _snapshot_progress()
    try:
        library._progress_state.replace(
            {
                "running": False,
                "total": 1,
                "completed": 0,
                "current": "demo",
                "results": [{"name": "demo", "detail": {"codes": ["AAA.SZ"]}}],
                "elapsed_sec": 0.0,
                "start_time": 123.0,
            }
        )

        snapshot = library.get_progress()

        live = library._progress_state.snapshot(deep=True)
        live["results"][0]["detail"]["codes"].append("BBB.SZ")
        live["results"].append({"name": "later"})
        library._progress_state.replace(live)

        assert snapshot["results"] == [{"name": "demo", "detail": {"codes": ["AAA.SZ"]}}]
    finally:
        library._progress_state.replace(original_progress)


def test_reset_progress_state_clears_library_progress():
    original_progress = _snapshot_progress()
    try:
        library._progress_state.replace(
            {
                "running": True,
                "total": 1,
                "completed": 1,
                "current": "demo",
                "results": [{"name": "demo"}],
                "elapsed_sec": 0.0,
                "start_time": 123.0,
            }
        )

        snapshot = library.reset_progress_state()
        snapshot["results"].append({"name": "mutated"})

        assert library.get_progress() == library._empty_progress()
        assert snapshot["running"] is False
        assert snapshot["results"] == [{"name": "mutated"}]
    finally:
        library._progress_state.replace(original_progress)


def test_write_json_atomic_replaces_target_and_cleans_temp_files(tmp_path):
    target = tmp_path / "meta.json"
    target.write_text("{\"stale\": true}", encoding="utf-8")

    library._write_json_atomic(target, {"fresh": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"fresh": True}
    assert [path.name for path in tmp_path.iterdir() if path.name.endswith(".tmp")] == []


def test_export_cache_writes_all_json_outputs_via_atomic_helper(tmp_path, monkeypatch):
    run_result = curve_result(
        equity_curve=pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2024-01-02"]),
                "sum_net_value": [1.0],
            }
        )
    )
    cache_dir = tmp_path / "cache"
    writes: list[str] = []

    def fake_write_json_atomic(path: Path, payload, *, default=None) -> None:
        writes.append(path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, default=default), encoding="utf-8")

    monkeypatch.setattr(library, "_write_json_atomic", fake_write_json_atomic)
    monkeypatch.setattr(library, "build_net_value_payload", lambda artifacts_arg: {"dates": ["2024-01-02"], "net_value": [1.0]})
    monkeypatch.setattr(library, "build_trades_payload", lambda artifacts_arg: {"records": []})
    monkeypatch.setattr(
        library,
        "build_daily_holdings_payload",
        lambda artifacts_arg, **kwargs: {
            "dates": ["2024-01-02"],
            "holdings_by_date": {},
            "summary_by_date": {},
        },
    )
    monkeypatch.setattr(
        library,
        "build_positions_cache_payload",
        lambda artifacts_arg: {"artifact_type": "positions", "schema_version": 2, "records": [{"name": "slot"}]},
    )
    monkeypatch.setattr(
        library,
        "build_position_changes_payload",
        lambda artifacts_arg: {
            "artifact_type": "position_changes",
            "schema_version": 2,
            "records": [{"trade_date": "2024-01-02"}],
        },
    )
    library._export_cache(run_result, {"name": "demo"}, cache_dir)

    assert writes == [
        "meta.json",
        "net_value.json",
        "trades.json",
        "daily_holdings.json",
        "positions.json",
        "position_changes.json",
    ]


def test_start_library_backtest_rejects_concurrent_requests(monkeypatch):
    release = threading.Event()
    original_progress = _snapshot_progress()

    def fake_run_single(name: str, date_overrides=None) -> dict[str, object]:
        release.wait(timeout=2)
        return {"name": name, "status": "completed", "duration_sec": 0.0}

    monkeypatch.setattr(library, "_run_single", fake_run_single)
    monkeypatch.setattr(library, "list_library", lambda: [{"name": "demo"}])
    library.reset_progress_state()

    barrier = threading.Barrier(3)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        results.append(library.start_library_backtest(["demo"]))

    threads = [threading.Thread(target=worker) for _ in range(2)]

    try:
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        assert sorted(results) == [False, True]
    finally:
        release.set()
        deadline = time.time() + 2
        while library.is_running() and time.time() < deadline:
            time.sleep(0.01)
        library._progress_state.replace(original_progress)


def test_backtest_run_rejects_non_string_strategy_names():
    original_progress = _snapshot_progress()
    library.reset_progress_state()
    try:
        with app.test_client() as client:
            response = client.post("/api/backtest/run", json={"names": [123]})
    finally:
        library._progress_state.replace(original_progress)

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid name: 123"


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("not-json", "text/plain"),
        ("null", "application/json"),
        ("[]", "application/json"),
        ('{"other": true}', "application/json"),
        ('{"names": []}', "application/json"),
    ],
)
def test_backtest_run_rejects_non_object_or_empty_payload(body: str, content_type: str, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(server_module, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server_module, "start_library_backtest", lambda names, **kwargs: calls.append(names) or True)

    with app.test_client() as client:
        response = client.post("/api/backtest/run", data=body, content_type=content_type)

    assert response.status_code == 400
    assert calls == []


def test_build_library_run_overrides_applies_explicit_date_overrides(tmp_path):
    overrides = run_config.build_library_run_overrides(
        {
            "strategy.module": "strategy_library.builtin.demo",
            "backtest.start_time": "20240101",
            "backtest.end_time": "20241231",
        },
        output_root=tmp_path,
        date_overrides={
            "start_time": "20240201",
            "end_time": "20240229",
        },
    )

    assert overrides["backtest.start_time"] == "20240201"
    assert overrides["backtest.end_time"] == "20240229"
    assert overrides["run.run_mode"] == "curve"


def test_build_library_run_overrides_preserves_library_data_source_keys(tmp_path):
    overrides = run_config.build_library_run_overrides(
        {
            "strategy.module": "strategy.py",
            "panel.source": "parquet",
            "mysql.daily_fields": "amount,net_mf_amount,pre_close,pct_chg",
            "mysql.password": "do-not-forward",
        },
        output_root=tmp_path,
    )

    assert overrides["panel.source"] == "parquet"
    assert overrides["mysql.daily_fields"] == "amount,net_mf_amount,pre_close,pct_chg"
    assert "mysql.password" not in overrides


def test_cache_static_signature_tracks_library_data_source_keys():
    signature = library._cache_static_signature(
        {
            "strategy.module": "strategy.py",
            "backtest.offset_days": "250",
            "backtest.universe_prefixes": "300,301,688",
            "backtest.exclude_bj": "1",
            "backtest.exclude_st": "1",
            "portfolio.engine": "legacy",
            "transaction_cost.enable": "1",
            "panel.source": "parquet",
            "mysql.daily_fields": "amount,net_mf_amount,pre_close,pct_chg",
        }
    )

    assert signature["panel"] == {"source": "parquet"}
    assert signature["mysql"] == {
        "daily_fields": ["amount", "net_mf_amount", "pre_close", "pct_chg"]
    }


def test_resolve_effective_backtest_window_clips_requested_end_time(monkeypatch):
    monkeypatch.setattr(library, "_resolve_latest_backtest_end_time", lambda start_time: "20240228")

    window = library._resolve_effective_backtest_window(
        {"backtest.start_time": "20240101", "backtest.end_time": "20241231"},
        date_overrides={"start_time": "20240201", "end_time": "20240315"},
    )

    assert window == {"start_time": "20240201", "end_time": "20240228"}


def test_build_trades_payload_includes_execution_and_risk_rows_for_broker():
    equity_curve = pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "sum_net_value": [1.0, 0.98, 1.01],
            "final_position_ratio": [1.0, 0.8, 1.0],
            "risk_state": ["normal", "warning", "normal"],
            "dd_state": ["normal", "warning", "normal"],
        })
    fills_df = pd.DataFrame([
                {
                    "order_id": "O1",
                    "trade_date": "2024-01-02",
                    "stock_code": "300001.SZ",
                    "side": "buy",
                    "qty": 100,
                    "price": 10.12,
                    "commission": 5.0,
                    "stamp_tax": 0.0,
                    "slippage_cost": 0.32,
                    "total_cost": 5.32,
                    "cash_delta": -1017.32,
                    "note": "",
                },
                {
                    "order_id": "O2",
                    "trade_date": "2024-01-04",
                    "stock_code": "300001.SZ",
                    "side": "sell",
                    "qty": 100,
                    "price": 10.55,
                    "commission": 5.0,
                    "stamp_tax": 1.06,
                    "slippage_cost": 0.28,
                    "total_cost": 6.34,
                    "cash_delta": 1048.66,
                    "note": "止盈卖出",
                },
                {
                    "order_id": "risk_1",
                    "trade_date": "2024-01-03",
                    "stock_code": "300001.SZ",
                    "side": "sell",
                    "qty": 20,
                    "price": 9.8,
                    "commission": 5.0,
                    "stamp_tax": 0.196,
                    "slippage_cost": 0.04,
                    "total_cost": 5.196,
                    "cash_delta": 190.804,
                    "note": "回撤风控触发；风控调仓 组合仓位 1.0000 -> 0.8000；回撤风控 WARNING",
                    "source": "risk",
                    "before_ratio": 1.0,
                    "after_ratio": 0.8,
                    "risk_reason": "回撤风控触发",
                    "risk_state": "NORMAL",
                    "dd_state": "WARNING",
                },
            ])
    trade_segments_df = pd.DataFrame({
                "股票代码": ["300001.SZ"],
                "买入日期": ["20240102"],
                "卖出日期": ["20240104"],
            })
    run_result = _ledger_run_result(
        equity_curve=equity_curve,
        fills_df=fills_df,
        trade_segments_df=trade_segments_df,
    )

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["columns"] == ["股票代码", "买入日期", "卖出日期"]
    assert payload["execution_columns"][0] == "日期"
    assert "手续费+印花税" in payload["execution_columns"]
    assert "风控原因" in payload["execution_columns"]
    assert payload["execution_meta"] == {"count": 3, "fill_count": 2, "risk_count": 1}
    assert [row["方向"] for row in payload["execution_records"]] == ["卖出", "卖出", "买入"]
    assert payload["execution_records"][0]["股数"] == 100
    assert payload["execution_records"][0]["手续费"] == 5.0
    assert payload["execution_records"][0]["滑点"] == 0.28
    assert payload["execution_records"][1]["来源"] == "风控"
    assert payload["execution_records"][1]["原仓位"] == 1.0
    assert payload["execution_records"][1]["目标仓位"] == 0.8
    assert payload["execution_records"][1]["风控原因"] == "回撤风控触发"
    assert payload["execution_records"][1]["回撤风控"] == "WARNING"
    assert "组合仓位 1.0000 -> 0.8000" in payload["execution_records"][1]["备注"]


def test_build_trades_payload_builds_vectorized_execution_rows():
    run_result = _vectorized_run_result(
        processed_frames={
                "df_2_1_with_returns": pd.DataFrame({
                    "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                    "stock_code": ["300001.SZ", "300001.SZ", "300001.SZ"],
                    "shares": [0, 100, 0],
                    "buy_price": [None, 10.0, None],
                    "sell_price": [None, None, 10.8],
                    "commission": [0.0, 5.0, 5.4],
                    "stamp_tax": [0.0, 0.0, 1.08],
                    "slippage_cost": [0.0, 0.2, 0.3],
                    "total_cost": [0.0, 5.2, 6.78],
                })
            },
    )

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["execution_meta"] == {"count": 2, "fill_count": 2, "risk_count": 0}
    assert [row["方向"] for row in payload["execution_records"]] == ["卖出", "买入"]
    assert payload["execution_records"][0]["组合"] == "2-1"
    assert payload["execution_records"][0]["股数"] == 100
    assert payload["execution_records"][0]["成交价"] == 10.8
    assert payload["execution_records"][0]["手续费+印花税"] == 6.78
    assert payload["execution_records"][1]["股数"] == 100
    assert payload["execution_records"][1]["手续费"] == 5.0


def test_build_trades_payload_builds_vectorized_execution_rows_without_shares():
    run_result = _vectorized_run_result(
        processed_frames={
                "df_1_1_with_returns": pd.DataFrame({
                    "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                    "stock_code": ["300001.SZ", "300001.SZ", "300001.SZ"],
                    "buy_price": [None, 10.0, None],
                    "sell_price": [None, None, 10.8],
                })
            },
    )

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["execution_meta"] == {"count": 2, "fill_count": 2, "risk_count": 0}
    assert [row["方向"] for row in payload["execution_records"]] == ["卖出", "买入"]
    assert payload["execution_records"][0]["股数"] is None
    assert payload["execution_records"][0]["成交价"] == 10.8
    assert "legacy 路径未提供股数与费用明细" in payload["execution_records"][0]["备注"]


def test_build_trades_payload_uses_per_stock_risk_fills_when_present():
    fills_df = pd.DataFrame([
        {
            "order_id": "order_1",
            "trade_date": "2024-01-02",
            "stock_code": "300001.SZ",
            "side": "buy",
            "qty": 100,
            "price": 10.0,
            "commission": 5.0,
            "stamp_tax": 0.0,
            "slippage_cost": 0.2,
            "total_cost": 5.0,
            "cash_delta": -1005.0,
            "note": "",
            "source": "strategy",
        }
    ])
    risk_fills_df = pd.DataFrame([
        {
            "order_id": "risk_1",
            "trade_date": "2024-01-03",
            "stock_code": "300001.SZ",
            "side": "sell",
            "qty": 20,
            "price": 9.8,
            "commission": 5.0,
            "stamp_tax": 0.196,
            "slippage_cost": 0.04,
            "total_cost": 5.196,
            "cash_delta": 190.804,
            "note": "回撤风控触发；风控调仓 组合仓位 1.0000 -> 0.8000",
            "source": "risk",
            "before_ratio": 1.0,
            "after_ratio": 0.8,
            "risk_reason": "回撤风控触发",
            "risk_state": "NORMAL",
            "dd_state": "WARNING",
        }
    ])

    equity_curve = pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03"],
            "sum_net_value": [1.0, 0.99],
            "final_position_ratio": [1.0, 0.8],
            "effective_position_ratio": [1.0, 0.8],
            "utilization_rate_raw": [1.0, 1.0],
            "risk_state": ["normal", "warning"],
            "dd_state": ["normal", "warning"],
        })
    run_result = _ledger_run_result(
        equity_curve=equity_curve,
        fills_df=fills_df,
        risk_fills_df=risk_fills_df,
    )

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["execution_meta"] == {"count": 2, "fill_count": 1, "risk_count": 1}
    assert [row["来源"] for row in payload["execution_records"]] == ["风控", "策略"]
    assert payload["execution_records"][0]["股数"] == 20
    assert payload["execution_records"][0]["风控原因"] == "回撤风控触发"
    assert payload["execution_records"][0]["原仓位"] == 1.0
    assert payload["execution_records"][0]["目标仓位"] == 0.8
    assert "组合仓位 1.0000 -> 0.8000" in payload["execution_records"][0]["备注"]


def test_build_trades_payload_uses_slot_labels_for_broker_execution_rows():
    fills_df = pd.DataFrame([
        {
            "order_id": "order_1",
            "slot_id": "df_2_1",
            "trade_date": "2024-01-03",
            "stock_code": "300001.SZ",
            "side": "buy",
            "qty": 100,
            "price": 10.0,
            "commission": 5.0,
            "stamp_tax": 0.0,
            "slippage_cost": 0.2,
            "total_cost": 5.2,
            "cash_delta": -1005.2,
            "source": "strategy",
        }
    ])
    run_result = _ledger_run_result(fills_df=fills_df)

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["execution_records"][0]["组合"] == "2-1"


def test_build_trades_payload_sorts_broker_execution_rows_by_numeric_order_suffix():
    fills_df = pd.DataFrame([
                {
                    "order_id": "order_10",
                    "trade_date": "2024-01-03",
                    "stock_code": "300010.SZ",
                    "side": "buy",
                    "qty": 100,
                    "price": 10.0,
                    "note": "later",
                    "source": "strategy",
                },
                {
                    "order_id": "order_2",
                    "trade_date": "2024-01-03",
                    "stock_code": "300002.SZ",
                    "side": "buy",
                    "qty": 100,
                    "price": 9.5,
                    "note": "earlier",
                    "source": "strategy",
                },
            ])
    run_result = _ledger_run_result(fills_df=fills_df)

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert [row["股票代码"] for row in payload["execution_records"]] == ["300002.SZ", "300010.SZ"]
    assert [row["备注"] for row in payload["execution_records"]] == ["earlier", "later"]


def test_build_trades_payload_exports_order_records_for_ledger_engines():
    orders_df = pd.DataFrame(
                [
                    {
                        "order_id": "order_3",
                        "slot_id": "df_2_1",
                        "trade_date": "2024-01-03",
                        "stock_code": "300003.SZ",
                        "side": "buy",
                        "qty": 100,
                        "filled_qty": 0,
                        "price": 10.0,
                        "status": "rejected",
                        "reason": "insufficient_cash",
                        "source": "strategy",
                        "exec_price_type": "open",
                        "note": "",
                    },
                    {
                        "order_id": "order_2",
                        "slot_id": "df_2_1",
                        "trade_date": "2024-01-03",
                        "stock_code": "300002.SZ",
                        "side": "buy",
                        "qty": 100,
                        "filled_qty": 0,
                        "price": 9.9,
                        "status": "skipped",
                        "reason": "limit_locked",
                        "source": "strategy",
                        "exec_price_type": "open",
                        "note": "",
                    },
                    {
                        "order_id": "order_1",
                        "slot_id": "df_2_1",
                        "trade_date": "2024-01-02",
                        "stock_code": "300001.SZ",
                        "side": "buy",
                        "qty": 100,
                        "filled_qty": 100,
                        "price": 9.8,
                        "status": "filled",
                        "reason": "",
                        "source": "strategy",
                        "exec_price_type": "open",
                        "note": "filled",
                    },
                ]
            )
    run_result = _ledger_run_result(orders_df=orders_df)

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["order_columns"][0] == "日期"
    assert payload["order_meta"] == {
        "count": 3,
        "filled_count": 1,
        "rejected_count": 1,
        "skipped_count": 1,
        "cancelled_count": 0,
        "risk_count": 0,
    }
    assert [row["状态"] for row in payload["order_records"]] == ["已拒绝", "已跳过", "已成交"]
    assert [row["组合"] for row in payload["order_records"]] == ["2-1", "2-1", "2-1"]
    assert payload["order_records"][0]["原因"] == "insufficient_cash"
    assert payload["order_records"][0]["成交股数"] == 0
    assert payload["order_records"][2]["成交股数"] == 100


def test_build_trades_payload_sorts_order_records_by_numeric_suffix_across_sources():
    orders_df = pd.DataFrame(
        [
            {
                "order_id": "order_10",
                "slot_id": "df_1_1",
                "trade_date": "2024-01-03",
                "stock_code": "300010.SZ",
                "side": "buy",
                "qty": 100,
                "filled_qty": 100,
                "price": 10.0,
                "status": "filled",
                "reason": "",
                "source": "strategy",
                "exec_price_type": "open",
                "note": "later",
            },
            {
                "order_id": "order_2",
                "slot_id": "df_1_1",
                "trade_date": "2024-01-03",
                "stock_code": "300002.SZ",
                "side": "buy",
                "qty": 100,
                "filled_qty": 100,
                "price": 9.5,
                "status": "filled",
                "reason": "",
                "source": "strategy",
                "exec_price_type": "open",
                "note": "earlier",
            },
        ]
    )
    risk_orders_df = pd.DataFrame(
        [
            {
                "order_id": "risk_11",
                "slot_id": "df_1_1",
                "trade_date": "2024-01-03",
                "stock_code": "300011.SZ",
                "side": "sell",
                "qty": 50,
                "filled_qty": 50,
                "price": 10.8,
                "status": "filled",
                "reason": "回撤风控触发",
                "source": "risk",
                "exec_price_type": "close",
                "note": "risk",
            }
        ]
    )
    run_result = _ledger_run_result(orders_df=orders_df, risk_orders_df=risk_orders_df)

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert [row["订单号"] for row in payload["order_records"]] == ["risk_11", "order_10", "order_2"]
    assert [row["来源"] for row in payload["order_records"]] == ["风控", "策略", "策略"]
    assert [row["组合"] for row in payload["order_records"]] == ["1-1", "1-1", "1-1"]


def test_build_trades_payload_merges_risk_orders_into_order_records():
    orders_df = pd.DataFrame(
        [
            {
                "order_id": "order_1",
                "slot_id": "df_1_1",
                "trade_date": "2024-01-02",
                "stock_code": "300001.SZ",
                "side": "buy",
                "qty": 100,
                "filled_qty": 100,
                "price": 10.0,
                "status": "filled",
                "reason": "",
                "source": "strategy",
                "exec_price_type": "open",
                "note": "",
            }
        ]
    )
    risk_orders_df = pd.DataFrame(
        [
            {
                "order_id": "risk_1",
                "slot_id": "df_1_1",
                "trade_date": "2024-01-03",
                "stock_code": "300001.SZ",
                "side": "sell",
                "qty": 20,
                "filled_qty": 20,
                "price": 9.8,
                "status": "filled",
                "reason": "回撤风控触发",
                "source": "risk",
                "exec_price_type": "close",
                "note": "回撤风控触发；风控调仓 槽位仓位 1.0000 -> 0.8000",
            }
        ]
    )
    run_result = _ledger_run_result(orders_df=orders_df, risk_orders_df=risk_orders_df)

    payload = build_trades_payload(run_result)

    assert payload is not None
    assert payload["order_meta"] == {
        "count": 2,
        "filled_count": 2,
        "rejected_count": 0,
        "skipped_count": 0,
        "cancelled_count": 0,
        "risk_count": 1,
    }
    assert [row["来源"] for row in payload["order_records"]] == ["风控", "策略"]
    assert payload["order_records"][0]["订单号"] == "risk_1"
    assert payload["order_records"][0]["原因"] == "回撤风控触发"
