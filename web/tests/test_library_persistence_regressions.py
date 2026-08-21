from __future__ import annotations

import configparser
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_support.library_owners import library
from backtest.result import CurveMetrics
from tests_support.run_result_factory import curve_result, ledger_execution
from web.cache_generation import resolve_current_web_cache
from web import library_cache_publication, library_metadata, library_runtime


def _snapshot_progress() -> dict:
    return library.get_progress()


def _assert_llm_description_is_source_bound(strategy_dir: Path, entry: dict) -> None:
    manifest_path = strategy_dir / "strategy_description.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = library_metadata._parse_strategy_config(strategy_dir / "config.ini")
    source_path = library_metadata._resolve_library_strategy_file(
        strategy_dir,
        config.get("strategy.module", ""),
    )

    assert source_path is not None
    assert entry["description_status"] == "verified"
    assert entry["strategy_description"] == manifest
    assert manifest["authoring"]["method"] == "llm_source_review"
    assert manifest["source"]["sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert entry["buy_logic"] == manifest["profile"]["buy_logic"]
    assert entry["sell_logic"] == manifest["profile"]["sell_logic"]


def test_run_single_writes_result_metrics_into_cached_meta(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    fake_result = curve_result(
        equity_curve=pd.DataFrame({"sum_net_value": [1.23]}),
        metrics=CurveMetrics(
            final_net_value=1.23,
            max_drawdown=0.12,
            actual_segment_trade_count=7,
            actual_segment_avg_trade_return=0.015,
            actual_segment_win_rate=0.571,
        ),
        duration_sec=8.5,
    )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "build_library_run_overrides", lambda strat_cfg, output_root, date_overrides=None: {})
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="completed",
            error=None,
            meta={"run_id": "demo_run", "extra": {"status": "running"}},
            run_result=fake_result,
        ),
    )
    monkeypatch.setattr(
        library,
        "_publish_cache",
        lambda run_result, meta, strategy_dir: captured.update({
            "run_result": run_result,
            "meta": meta,
            "strategy_dir": strategy_dir,
        }),
    )

    result = library._run_single("demo")

    assert result["status"] == "completed"
    assert captured["run_result"] is fake_result
    meta = captured["meta"]
    assert meta["extra"]["status"] == "completed"
    assert meta["result"]["final_net_value"] == 1.23
    assert round(meta["result"]["cum_return"], 6) == 0.23
    assert meta["result"]["max_drawdown"] == 0.12
    assert meta["result"]["actual_segment_trade_count"] == 7
    assert meta["result"]["actual_segment_avg_trade_return"] == 0.015
    assert meta["result"]["actual_segment_win_rate"] == 0.571


def test_run_single_records_duration_for_missing_strategy_module(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "missing_module"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text("[strategy]\n", encoding="utf-8")

    first_time = True

    def fake_time() -> float:
        nonlocal first_time
        if first_time:
            first_time = False
            return 100.0
        return 101.234

    monkeypatch.setattr(library_runtime.time, "time", fake_time)
    monkeypatch.setattr(
        library_cache_publication,
        "_write_failure_cache",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    result = library._run_single("missing_module")

    assert result["status"] == "failed"
    assert result["error"] == "config.ini missing strategy.module"
    assert result["duration_sec"] == 1.23


def test_export_cache_skips_malformed_curve_without_sum_net_value(tmp_path):
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-02"],
            "utilization_rate": [0.0],
        }),
    )
    cache_dir = tmp_path / "cache"

    library_cache_publication._export_cache(
        result,
        {"extra": {"status": "completed"}},
        cache_dir,
    )

    assert (cache_dir / "meta.json").is_file()
    assert not (cache_dir / "net_value.json").exists()


def test_export_cache_does_not_materialize_nonfinite_sparkline(tmp_path):
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-02"],
            "sum_net_value": [float("nan")],
        }),
    )
    cache_dir = tmp_path / "cache"

    library_cache_publication._export_cache(
        result,
        {"extra": {"status": "no_signal"}},
        cache_dir,
    )

    meta_text = (cache_dir / "meta.json").read_text(encoding="utf-8")
    assert "NaN" not in meta_text
    assert "Infinity" not in meta_text
    assert json.loads(meta_text)["sparkline"] is None
    assert not (cache_dir / "net_value.json").exists()


# Failure cache/current lifecycle coverage lives in
# test_library_failure_cache_lifecycle.py.
#
# Keep this legacy persistence file focused on successful publication paths.


def test_run_single_publishes_schema_v2_generation_readable_by_catalog_and_api(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240103\n",
        encoding="utf-8",
    )
    equity_curve = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "sum_net_value": [1.0, 1.1],
        }
    )
    fake_result = curve_result(
        run_id="web-generation-run",
        equity_curve=equity_curve,
        execution=ledger_execution(equity_curve=equity_curve),
    )
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "_resolve_latest_backtest_end_time", lambda start_time: "20240103")
    monkeypatch.setattr(library, "build_library_run_overrides", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="completed",
            error=None,
            meta={"run_id": "web-generation-run", "extra": {"status": "running"}},
            run_result=fake_result,
            outcome=None,
        ),
    )
    monkeypatch.setattr(library, "_persist_library_outputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    result = library._run_single("demo")
    snapshot = resolve_current_web_cache(strategy_dir)
    catalog_entry = library.get_library_catalog()["items"][0]
    net_value = library.get_library_net_value("demo")

    assert result["status"] == "completed"
    assert snapshot.generation_id == "web-generation-run"
    assert snapshot.manifest["summary"]["artifact_count"] >= 2
    assert catalog_entry["cache_status"] == "valid"
    assert catalog_entry["cache_generation_id"] == "web-generation-run"
    assert net_value is not None
    assert net_value["schema_version"] == 2
    assert net_value["net_value"] == [1.0, 1.1]


def test_run_single_treats_config_equivalent_date_overrides_as_default_generation(
    tmp_path,
    monkeypatch,
):
    """A dialog-filled configured window must activate the new current cache."""
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text(
        "STRATEGY_META = {}\n",
        encoding="utf-8",
    )
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n"
        "[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    equity_curve = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-30", "2024-01-31"]),
            "sum_net_value": [1.0, 1.1],
        }
    )
    fake_result = curve_result(
        run_id="configured-window-run",
        equity_curve=equity_curve,
        execution=ledger_execution(equity_curve=equity_curve),
    )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(
        library,
        "_resolve_latest_backtest_end_time",
        lambda start_time: "20240131",
    )
    monkeypatch.setattr(library, "_persist_library_outputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="completed",
            error=None,
            meta={"run_id": "configured-window-run", "extra": {"status": "running"}},
            run_result=fake_result,
            outcome=None,
        ),
    )

    result = library._run_single(
        "demo",
        date_overrides={"start_time": "20240101", "end_time": "20240131"},
    )

    assert result["status"] == "completed"
    snapshot = resolve_current_web_cache(strategy_dir)
    assert snapshot.generation_id == "configured-window-run"
    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "valid"
    assert entry["status"] == "completed"


def test_export_cache_removes_stale_payload_files_when_new_run_has_no_payloads(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for filename in (
        "net_value.json",
        "trades.json",
        "daily_holdings.json",
        "positions.json",
        "position_changes.json",
    ):
        (cache_dir / filename).write_text('{"stale": true}', encoding="utf-8")

    run_result = curve_result()

    monkeypatch.setattr(library, "build_net_value_payload", lambda artifacts_arg: None)
    monkeypatch.setattr(library, "build_trades_payload", lambda artifacts_arg: {})
    monkeypatch.setattr(
        library,
        "build_daily_holdings_payload",
        lambda run_result_arg, *, initial_asset_hint=None: None,
    )
    monkeypatch.setattr(library, "build_positions_cache_payload", lambda artifacts_arg: None)
    monkeypatch.setattr(library, "build_position_changes_payload", lambda artifacts_arg: None)
    library._export_cache(run_result, {"strategy_module": "strategy_library.builtin.demo"}, cache_dir)

    assert (cache_dir / "meta.json").is_file()
    assert not (cache_dir / "net_value.json").exists()
    assert not (cache_dir / "trades.json").exists()
    assert not (cache_dir / "daily_holdings.json").exists()
    assert not (cache_dir / "positions.json").exists()
    assert not (cache_dir / "position_changes.json").exists()


def test_run_single_persists_library_outputs_when_artifacts_exist(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )

    cfg = SimpleNamespace(run=SimpleNamespace())
    fake_result = curve_result(
        equity_curve=pd.DataFrame({"sum_net_value": [1.0]}),
        duration_sec=1.0,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "build_library_run_overrides", lambda strat_cfg, output_root, date_overrides=None: {})
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="completed",
            error=None,
            meta={"extra": {"status": "running"}},
            run_result=fake_result,
            outcome=SimpleNamespace(ctx=SimpleNamespace(cfg=cfg)),
        ),
    )
    monkeypatch.setattr(library, "_publish_cache", lambda artifacts, meta, strategy_dir: None)
    monkeypatch.setattr(
        library,
        "_persist_library_outputs",
        lambda run_result, cfg_arg: captured.update({"run_result": run_result, "cfg": cfg_arg}),
    )

    result = library._run_single("demo")

    assert result["status"] == "completed"
    assert captured["run_result"] is fake_result
    assert captured["cfg"] is cfg


def test_persist_library_outputs_writes_broker_fill_files(tmp_path):
    fills_df = pd.DataFrame([
        {
            "order_id": "order_2",
            "trade_date": "2024-01-03",
            "stock_code": "AAA.SZ",
            "side": "buy",
            "qty": 100,
            "source": "strategy",
        }
    ])
    risk_fills_df = pd.DataFrame([
        {
            "order_id": "risk_1",
            "trade_date": "2024-01-04",
            "stock_code": "AAA.SZ",
            "side": "sell",
            "qty": 20,
            "source": "risk",
        }
    ])
    equity_curve = pd.DataFrame(
        {"trade_date": pd.to_datetime(["2024-01-02"]), "sum_net_value": [1.0]}
    )
    run_result = curve_result(
        engine_id="broker",
        equity_curve=equity_curve,
        execution=ledger_execution(
            equity_curve=equity_curve,
            fills_df=fills_df,
            risk_fills_df=risk_fills_df,
        ),
    )
    cfg = SimpleNamespace(
        run=SimpleNamespace(topn_candidate_factor=1.0),
        portfolio=SimpleNamespace(engine="broker"),
        paths=SimpleNamespace(
            signal_data=tmp_path / "signal_data",
            return_date=tmp_path / "return_date",
            return_date_with_returns=tmp_path / "return_date_with_returns",
            net_value_unique_date=tmp_path / "net_value_unique_date",
        ),
    )

    library._persist_library_outputs(run_result, cfg)

    fills_path = tmp_path / "net_value_unique_date" / "portfolio_fills.csv"
    assert fills_path.exists()
    out_df = pd.read_csv(fills_path)
    assert out_df["order_id"].tolist() == ["order_2", "risk_1"]
    assert out_df["source"].tolist() == ["strategy", "risk"]


def test_run_single_clips_end_time_before_building_run_overrides(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "get_config", lambda reload=True: object())
    monkeypatch.setattr(library, "_resolve_latest_backtest_end_time", lambda start_time: "20240228")
    monkeypatch.setattr(
        library,
        "build_library_run_overrides",
        lambda strat_cfg, output_root, date_overrides=None: captured.setdefault("date_overrides", date_overrides) or {},
    )
    monkeypatch.setattr(
        library,
        "execute_web_backtest",
        lambda **kwargs: SimpleNamespace(
            status="completed",
            error=None,
            meta={},
            run_result=None,
            artifacts=None,
        ),
    )

    result = library._run_single(
        "demo",
        date_overrides={"start_time": "20240201", "end_time": "20240315"},
    )

    assert result["status"] == "completed"
    assert captured["date_overrides"] == {"start_time": "20240201", "end_time": "20240228"}


def test_start_library_backtest_passes_per_strategy_date_overrides(monkeypatch):
    release = threading.Event()
    original_progress = _snapshot_progress()
    captured: list[tuple[str, object]] = []

    def fake_run_single(name: str, date_overrides=None) -> dict[str, object]:
        captured.append((name, date_overrides))
        release.wait(timeout=2)
        return {"name": name, "status": "completed", "duration_sec": 0.0}

    monkeypatch.setattr(library, "_run_single", fake_run_single)
    monkeypatch.setattr(library, "list_library", lambda: [{"name": "demo"}])
    library.reset_progress_state()

    try:
        started = library.start_library_backtest(
            ["demo"],
            date_overrides={"demo": {"start_time": "20240201", "end_time": "20240229"}},
        )
        assert started is True

        deadline = time.time() + 2
        while not captured and time.time() < deadline:
            time.sleep(0.01)

        assert captured == [("demo", {"start_time": "20240201", "end_time": "20240229"})]
    finally:
        release.set()
        deadline = time.time() + 2
        while library.is_running() and time.time() < deadline:
            time.sleep(0.01)
        library._progress_state.replace(original_progress)


def test_codex_focus_medium_frontier_ge2_is_registered_in_web_library():
    strategy_dir = library.LIBRARY_ROOT / "codex_focus_medium_frontier_ge2"
    cfg_path = strategy_dir / "config.ini"
    strategy_path = strategy_dir / "strategy.py"

    assert cfg_path.is_file()
    assert not strategy_path.exists()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    module = "strategy_library.builtin.codex_focus_medium_frontier_ge2"
    assert parser.get("strategy", "module") == module
    assert parser.get("backtest", "end_time") == "20261231"

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == "codex_focus_medium_frontier_ge2")
    assert entry["module"] == module
    assert entry["family"] == "builtin"
    assert entry["source_file"] == "strategy_library/builtin/codex_focus_medium_frontier_ge2.py"
    assert entry["local_source_file"] is None


@pytest.mark.parametrize(
    ("name", "expected_end", "expected_title"),
    [
        (
            "codex_2024_2026_qualified_reclaim_chase",
            "20260602",
            "双段追击修复",
        ),
        (
            "codex_2024_now_ret400_win55_2x2",
            "20261231",
            "广度脉冲区间扩张",
        ),
        (
            "codex_chase_dd_control_breakout",
            "20260506",
            "回撤受控追涨突破",
        ),
        (
            "codex_chase_momentum_breakout",
            "20261231",
            "动量追涨突破",
        ),
        (
            "codex_fresh_accel_chase_breakout",
            "20261231",
            "新高加速追涨",
        ),
        (
            "codex_post_burst_quiet_reclaim",
            "20261231",
            "爆发后静默修复",
        ),
    ],
)
def test_unique_generated_strategies_are_registered_in_web_library(
    name: str,
    expected_end: str,
    expected_title: str,
):
    strategy_dir = library.LIBRARY_ROOT / name
    cfg_path = strategy_dir / "config.ini"
    strategy_path = strategy_dir / "strategy.py"
    generated_path = ROOT / "strategy_library" / "generated" / f"{name}.py"

    assert cfg_path.is_file()
    assert not strategy_path.exists()
    assert generated_path.is_file()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    module = f"strategy_library.generated.{name}"
    assert parser.get("strategy", "module") == module
    assert parser.get("backtest", "start_time") == "20240101"
    assert parser.get("backtest", "end_time") == expected_end

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == name)
    assert entry["module"] == module
    assert entry["family"] == "generated"
    assert entry["source_file"] == f"strategy_library/generated/{name}.py"
    assert entry["local_source_file"] is None
    assert entry["title"] == expected_title
    _assert_llm_description_is_source_bound(strategy_dir, entry)


def test_duplicate_generated_strategy_wrapper_is_cleaned_from_library():
    duplicate_name = "codex_v1_dd25_win53_2026_boost"

    assert not (ROOT / "strategy_library" / "generated" / f"{duplicate_name}.py").exists()
    assert not (library.LIBRARY_ROOT / duplicate_name).exists()

    from strategy_library import generated

    assert duplicate_name not in generated.__all__
    catalog = library.get_library_catalog()
    assert duplicate_name not in {item["name"] for item in catalog["items"]}


def test_bulk_0215_rsi_reclaim_is_registered_as_generated_strategy():
    strategy_dir = library.LIBRARY_ROOT / "bulk_0215_rsi_reclaim"
    cfg_path = strategy_dir / "config.ini"

    assert cfg_path.is_file()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    assert parser.get("strategy", "module") == "strategy.py"
    assert parser.get("backtest", "end_time") == "20261231"

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == "bulk_0215_rsi_reclaim")
    assert entry["module"] == "strategy.py"
    assert entry["family"] == "library"
    assert entry["local_source_file"] == "strategy.py"
    assert entry["title"] == "RSI回踩修复"
    assert entry["style_label"] == "超跌修复型"
    _assert_llm_description_is_source_bound(strategy_dir, entry)


@pytest.mark.parametrize(
    "name",
    ("bulk_0212_rsi_reclaim", "codex_net_mf_amount_pulse"),
)
def test_deleted_web_strategies_are_not_registered(name: str):
    assert not (library.LIBRARY_ROOT / name).exists()
    catalog = library.get_library_catalog()

    assert name not in {item["name"] for item in catalog["items"]}


def test_bulk_0312_rsi_reclaim_is_registered_as_generated_strategy():
    strategy_dir = library.LIBRARY_ROOT / "bulk_0312_rsi_reclaim"
    cfg_path = strategy_dir / "config.ini"

    assert cfg_path.is_file()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    assert parser.get("strategy", "module") == "strategy.py"
    assert parser.get("backtest", "end_time") == "20261231"

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == "bulk_0312_rsi_reclaim")
    assert entry["module"] == "strategy.py"
    assert entry["family"] == "library"
    assert entry["local_source_file"] == "strategy.py"
    assert entry["title"] == "RSI回踩修复-沪指突破择时"
    assert entry["style_label"] == "指数择时修复型"
    _assert_llm_description_is_source_bound(strategy_dir, entry)


def test_bulk_0315_rsi_reclaim_is_registered_as_generated_strategy():
    strategy_dir = library.LIBRARY_ROOT / "bulk_0315_rsi_reclaim"
    cfg_path = strategy_dir / "config.ini"

    assert cfg_path.is_file()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    assert parser.get("strategy", "module") == "strategy.py"
    assert parser.get("backtest", "end_time") == "20261231"

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == "bulk_0315_rsi_reclaim")
    assert entry["module"] == "strategy.py"
    assert entry["family"] == "library"
    assert entry["local_source_file"] == "strategy.py"
    assert entry["title"] == "RSI回踩修复-创业板择时"
    assert entry["style_label"] == "指数择时修复型"
    _assert_llm_description_is_source_bound(strategy_dir, entry)


def test_bulk_0316_rsi_reclaim_is_registered_as_generated_strategy():
    strategy_dir = library.LIBRARY_ROOT / "bulk_0316_rsi_reclaim"
    cfg_path = strategy_dir / "config.ini"

    assert cfg_path.is_file()

    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(cfg_path), encoding="utf-8")
    assert parser.get("strategy", "module") == "strategy.py"
    assert parser.get("backtest", "end_time") == "20261231"
    assert parser.get("portfolio", "engine") == "slot_broker"

    catalog = library.get_library_catalog()
    entry = next(item for item in catalog["items"] if item["name"] == "bulk_0316_rsi_reclaim")
    assert entry["module"] == "strategy.py"
    assert entry["family"] == "library"
    assert entry["local_source_file"] == "strategy.py"
    assert entry["title"] == "RSI回踩修复-双持仓快切"
    assert entry["style_label"] == "超跌修复型"
    _assert_llm_description_is_source_bound(strategy_dir, entry)
