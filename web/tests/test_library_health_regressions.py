from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_support.library_owners import library
from tests_support.web_cache import publish_web_cache_fixture
from web.server import app
from strategies.source_policy import find_strategy_dependency_issues
from web.tools import check_strategy_library_health as health_tool


def test_strategy_library_health_reports_invalid_cache_warning(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\n",
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "backtest": {"start_time": "20210101"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, with_cache_config=False)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(health_tool, "LIBRARY_ROOT", library_root)

    entries = health_tool.check_strategy_library_health()

    assert len(entries) == 1
    assert entries[0].status == "warning"
    assert entries[0].cache_status == "invalid"
    assert entries[0].issues == ["invalid_cache"]


def test_all_web_strategy_modules_are_self_contained():
    library_root = ROOT / "web" / "strategy_library"
    strategy_paths = sorted(library_root.glob("*/strategy.py"))
    assert strategy_paths

    for strategy_path in strategy_paths:
        strategy_dir = strategy_path.parent
        python_files = sorted(strategy_dir.rglob("*.py"))
        assert python_files == [strategy_path], strategy_dir.name
        source = strategy_path.read_text(encoding="utf-8")
        assert find_strategy_dependency_issues(source) == [], strategy_dir.name


def test_strategy_library_health_rejects_cross_strategy_dependency(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "dependent_strategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text(
        "from strategy_library.generated import another_strategy\nSTRATEGY_META = {}\n",
        encoding="utf-8",
    )
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(health_tool, "LIBRARY_ROOT", library_root)

    entries = health_tool.check_strategy_library_health()

    assert len(entries) == 1
    assert entries[0].status == "error"
    assert "strategy_import:strategy_library.generated" in entries[0].issues


def test_strategy_dependency_policy_rejects_externalized_project_logic(tmp_path):
    project_root = tmp_path / "project"
    shared_module = project_root / "shared_strategy_features.py"
    shared_module.parent.mkdir(parents=True)
    shared_module.write_text("def market_state():\n    return True\n", encoding="utf-8")

    issues = find_strategy_dependency_issues(
        "from shared_strategy_features import market_state\n",
        project_root=project_root,
    )

    assert issues == ["non_framework_project_import:shared_strategy_features"]


@pytest.mark.parametrize(
    "source, expected_issue",
    [
        ("from .helpers import market_state\n", "relative_import"),
        ("from importlib import import_module\n", "dynamic_import:importlib"),
        ("runpy.run_path('other_strategy.py')\n", "dynamic_import:run_path"),
    ],
)
def test_strategy_dependency_policy_rejects_indirect_loading(source, expected_issue):
    assert expected_issue in find_strategy_dependency_issues(source)


def test_catalog_and_health_survive_malformed_config(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    ok_dir = library_root / "ok_strategy"
    ok_dir.mkdir(parents=True, exist_ok=True)
    (ok_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (ok_dir / "config.ini").write_text("[strategy]\nmodule = strategy.py\n", encoding="utf-8")
    bad_dir = library_root / "bad_strategy"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "config.ini").write_text("[strategy\nmodule = strategy.py\n", encoding="utf-8")

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    catalog = library.get_library_catalog()
    assert {item["name"] for item in catalog["items"]} == {"bad_strategy", "ok_strategy"}

    health = library.get_library_health()
    bad_item = next(item for item in health["items"] if item["name"] == "bad_strategy")
    assert bad_item["status"] == "error"
    assert "invalid_config" in bad_item["issues"]
    assert health["overview"]["error"] == 1


def test_library_strategy_export_health_and_report_api(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    library_root = project_root / "web" / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {'top_n': 3}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "\n".join([
            "# Strategy: Demo Strategy",
            "#",
            "# Purpose: research analysis API regression",
            "",
            "[strategy]",
            "module = strategy.py",
            "",
            "[backtest]",
            "start_time = 20240101",
            "end_time = 20240108",
            "offset_days = 120",
            "universe_prefixes = 300,301",
            "exclude_bj = 1",
            "exclude_st = 1",
        ]),
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "timestamp": "2026-04-25T10:00:00",
                "backtest": {
                    "start_time": "20240101",
                    "end_time": "20240108",
                    "offset_days": 120,
                    "universe_prefixes": ["300", "301"],
                    "exclude_bj": True,
                    "exclude_st": True,
                },
                "extra": {"status": "completed"},
                "result": {
                    "cum_return": 0.08,
                    "max_drawdown": 0.02,
                    "final_net_value": 1.08,
                    "actual_segment_trade_count": 4,
                    "actual_segment_win_rate": 0.75,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps(
            {
                "dates": [
                    "2024-01-02",
                    "2024-01-03",
                    "2024-01-04",
                    "2024-01-05",
                    "2024-01-08",
                ],
                "net_value": [1.0, 1.02, 1.01, 1.05, 1.08],
                "drawdown": [0.0, 0.0, -0.009804, 0.0, 0.0],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "trades.json").write_text(
        json.dumps(
            {
                "records": [
                    {"code": "300001.SZ", "pnl": 0.03},
                    {"code": "300002.SZ", "pnl": -0.01},
                    {"code": "300003.SZ", "pnl": 0.02},
                    {"code": "300004.SZ", "pnl": 0.01},
                ]
            }
        ),
        encoding="utf-8",
    )
    (cache_dir / "daily_holdings.json").write_text(
        json.dumps(
            {
                "dates": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"],
                "holdings_by_date": {"2024-01-02": []},
                "summary_by_date": {"2024-01-02": {"holding_count": 0}},
            }
        ),
        encoding="utf-8",
    )

    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir)
    monkeypatch.setattr(library, "_project_root", project_root)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    analysis = library.compute_library_strategy_analysis("demo_strategy")
    assert analysis is not None
    assert analysis["return_profile"]["cum_return"] == 0.08
    assert analysis["trade_profile"]["win_rate"] == 0.75
    assert analysis["period_stats"]["yearly"][0]["period"] == "2024"

    health = library.get_library_health()
    assert health["overview"]["ok"] == 1
    assert health["overview"]["valid_cache"] == 1

    csv_text = library.build_library_catalog_csv()
    assert csv_text.startswith("\ufeffname,title,status")
    assert "strategy_version" in csv_text.splitlines()[0]
    assert "demo_strategy" in csv_text

    report = library.build_strategy_markdown_report("demo_strategy")
    assert report is not None
    assert "# Demo Strategy" in report
    assert "## 研究诊断" in report

    with app.test_client() as client:
        health_response = client.get("/api/library/health")
        assert health_response.status_code == 200
        assert health_response.get_json()["overview"]["valid_cache"] == 1

        analysis_response = client.get("/api/library/demo_strategy/analysis")
        assert analysis_response.status_code == 200
        assert analysis_response.get_json()["name"] == "demo_strategy"

        export_response = client.get("/api/library/export?format=csv")
        assert export_response.status_code == 200
        assert "demo_strategy" in export_response.get_data(as_text=True)

        report_response = client.get("/api/library/demo_strategy/report?format=json")
        assert report_response.status_code == 200
        assert "## 研究诊断" in report_response.get_json()["content"]
