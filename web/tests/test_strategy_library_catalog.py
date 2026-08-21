from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tests_support.library_owners import library
import web.server as server_module
from tests_support.web_cache import publish_web_cache_fixture
from web.server import app


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_default_library_root_is_inside_web_subsystem():
    assert library.LIBRARY_ROOT == ROOT / "web" / "strategy_library"


def _write_strategy_module(module_path: Path, body: str) -> None:
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")


def _write_library_strategy(
    root: Path,
    *,
    name: str,
    module: str,
    title: str,
    summary: str,
    strategy_body: str,
    cached_meta: dict | None = None,
    description_runtime: dict | None = None,
) -> None:
    strategy_dir = root / name
    strategy_dir.mkdir(parents=True, exist_ok=True)
    config_text = f"""# 策略: {title}
#
# 说明: {summary}

[strategy]
module = {module}

[backtest]
start_time = 20240101
end_time = 20241231
offset_days = 120
universe_prefixes = 300,301
exclude_bj = 1
exclude_st = 1
"""
    (strategy_dir / "config.ini").write_text(config_text, encoding="utf-8")
    strategy_path = strategy_dir / "strategy.py"
    strategy_path.write_text(strategy_body.strip() + "\n", encoding="utf-8")
    strategy_meta = cached_meta.get("strategy_meta", {}) if cached_meta else {}
    top_n = strategy_meta.get("top_n")
    pj = strategy_meta.get("portfolio_num_j", 1)
    pi = strategy_meta.get("portfolio_num_i", 1)
    if top_n is None and isinstance(pj, int) and isinstance(pi, int):
        top_n = pj * pi
    runtime = {"top_n": top_n, "portfolio_num_j": pj, "portfolio_num_i": pi}
    if description_runtime:
        runtime.update(description_runtime)
    manifest = {
        "schema_version": 2,
        "authoring": {"method": "llm_source_review", "language": "zh-CN"},
        "source": {
            "file": "strategy.py",
            "sha256": hashlib.sha256(strategy_path.read_bytes()).hexdigest(),
        },
        "profile": {
            "title": title,
            "summary": summary,
            "buy_logic": "测试买入说明",
            "sell_logic": "测试卖出说明",
            "position_text": f"{runtime.get('portfolio_num_j', pj)}*{runtime.get('portfolio_num_i', pi)}",
            "style_label": "测试策略",
        },
        "runtime": runtime,
        "buy": ["测试买入条件"],
        "sell": ["测试卖出条件"],
        "investor_explanation": {
            "buy": {
                "summary": "测试买入说明",
                "groups": [{"title": "买入", "items": [{"title": "条件", "description": "测试买入条件"}]}],
            },
            "sell": {
                "summary": "测试卖出说明",
                "groups": [{"title": "卖出", "items": [{"title": "条件", "description": "测试卖出条件"}]}],
            },
        },
    }
    (strategy_dir / "strategy_description.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if cached_meta is not None:
        cache_dir = strategy_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "meta.json").write_text(
            json.dumps(cached_meta, ensure_ascii=False),
            encoding="utf-8",
        )
        final_net_value = cached_meta.get("result", {}).get("final_net_value", 1.0)
        (cache_dir / "net_value.json").write_text(
            json.dumps(
                {
                    "dates": ["2024-01-02"],
                    "net_value": [final_net_value],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        publish_web_cache_fixture(
            strategy_dir,
            flat_cache_dir=cache_dir,
            generation_id=str(cached_meta.get("run_id") or "fixture-run"),
        )


def test_library_catalog_and_api_return_structured_catalog(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    library_root = project_root / "web" / "strategy_library"

    alpha_body = """
class Strategy:
    STRATEGY_META = {
        "top_n": 8,
        "sort_spec": ["-score"],
        "portfolio_num_j": 4,
        "portfolio_num_i": 2,
    }
"""
    beta_body = """
STRATEGY_META = {
    "top_n": 12,
    "sort_spec": ["-score"],
    "use_index_timing": True,
    "portfolio_num_j": 6,
    "portfolio_num_i": 2,
}
"""

    _write_library_strategy(
        library_root,
        name="demo_alpha",
        module="strategy.py",
        title="demo_alpha",
        summary="内置候选，已有缓存",
        strategy_body=alpha_body,
        cached_meta={
            "run_id": "20260314_220000_00001",
            "timestamp": "2026-03-14T22:00:00",
            "duration_sec": 6.5,
            "strategy_module": "strategy.py",
            "backtest": {
                "start_time": "20240101",
                "end_time": "20241231",
                "offset_days": 120,
                "universe_prefixes": ["300", "301"],
                "exclude_bj": True,
                "exclude_st": True,
            },
            "extra": {"status": "completed"},
            "strategy_meta": {
                "top_n": 8,
                "sort_spec": [["score", False]],
                "portfolio_num_j": 4,
                "portfolio_num_i": 2,
            },
            "result": {
                "cum_return": 0.32,
                "max_drawdown": 0.11,
                "final_net_value": 1.32,
                "actual_segment_trade_count": 42,
                "actual_segment_avg_trade_return": 0.013,
                "actual_segment_win_rate": 0.57,
            },
            "sparkline": [1.0, 1.08, 1.18, 1.32],
        },
    )
    _write_library_strategy(
        library_root,
        name="demo_beta",
        module="strategy.py",
        title="Demo Beta",
        summary="候选函数，等待回测",
        strategy_body=beta_body,
        cached_meta=None,
        description_runtime={
            "top_n": 12,
            "portfolio_num_j": 6,
            "portfolio_num_i": 2,
            "use_index_timing": True,
        },
    )

    monkeypatch.setattr(library, "_project_root", project_root)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    frontend_dist = tmp_path / "frontend_dist"
    frontend_manifest = frontend_dist / ".vite" / "manifest.json"
    frontend_manifest.parent.mkdir(parents=True, exist_ok=True)
    frontend_manifest.write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app.js",
                    "css": ["assets/app.css"],
                    "isEntry": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "_frontend_dist_root", frontend_dist)

    catalog = library.get_library_catalog()
    assert sorted(catalog.keys()) == ["filters", "items", "overview"]
    assert catalog["overview"]["total"] == 2
    assert catalog["overview"]["cached"] == 1
    assert catalog["overview"]["completed"] == 1
    assert catalog["overview"]["positive"] == 1

    items = catalog["items"]
    assert [item["name"] for item in items] == ["demo_alpha", "demo_beta"]
    assert items[0]["family"] == "library"
    assert items[0]["status"] == "completed"
    assert items[0]["start_time"] == "20240101"
    assert items[0]["end_time"] == "20241231"
    assert items[0]["summary"] == "内置候选，已有缓存"
    assert items[0]["buy_logic"] == "测试买入说明"
    assert items[0]["sell_logic"] == "测试卖出说明"
    assert items[0]["position_text"] == "4*2"
    assert items[0]["style_label"] == "测试策略"
    assert items[0]["portfolio_label"] == "4x2"
    assert items[0]["local_source_file"] == "strategy.py"
    assert items[0]["strategy_version"].startswith("v-")
    assert items[0]["version_status"] == "verified"
    assert items[0]["version_status_label"] == "已验证"
    assert len(items[0]["source_hash"]) == 12
    assert len(items[0]["config_hash"]) == 12
    assert items[0]["run_id"] == "20260314_220000_00001"
    assert items[0]["rank"] == 1
    assert items[0]["sparkline"] == [1.0, 1.08, 1.18, 1.32]
    assert items[0]["description_status"] == "verified"
    assert items[0]["strategy_description"]["schema_version"] == 2
    assert items[1]["family"] == "library"
    assert items[1]["status"] == "idle"
    assert items[1]["start_time"] == "20240101"
    assert items[1]["end_time"] == "20241231"
    assert items[1]["title"] == "Demo Beta"
    assert items[1]["summary"] == "候选函数，等待回测"
    assert items[1]["buy_logic"] == "测试买入说明"
    assert items[1]["sell_logic"] == "测试卖出说明"
    assert items[1]["position_text"] == "6*2"
    assert items[1]["style_label"] == "测试策略"
    assert items[1]["local_source_file"] == "strategy.py"
    assert items[1]["strategy_version"].startswith("v-")
    assert items[1]["version_status"] == "current"
    assert items[1]["version_status_label"] == "当前版本"
    assert len(items[1]["source_hash"]) == 12
    assert len(items[1]["config_hash"]) == 12

    assert (library_root / "demo_alpha" / "strategy.py").read_text(encoding="utf-8").strip().startswith("class Strategy:")
    assert (library_root / "demo_beta" / "strategy.py").read_text(encoding="utf-8").strip().startswith("STRATEGY_META = {")

    families = {item["value"] for item in catalog["filters"]["families"]}
    statuses = {item["value"] for item in catalog["filters"]["statuses"]}
    assert {"all", "library"} <= families
    assert {"all", "completed", "idle"} <= statuses

    with app.test_client() as client:
        response = client.get("/api/library")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["items"][0]["sparkline"] == [1.0, 1.08, 1.18, 1.32]
        assert payload["items"][0]["version_status"] == "verified"
        assert "strategy_description" not in payload["items"][0]
        assert "buy_logic" not in payload["items"][0]
        assert response.headers["ETag"]
        conditional = client.get(
            "/api/library",
            headers={"If-None-Match": response.headers["ETag"]},
        )
        assert conditional.status_code == 304
        detail_payload = client.get("/api/library/demo_alpha/detail").get_json()
        assert detail_payload["catalog_entry"]["strategy_description"]["schema_version"] == 2
        assert detail_payload["catalog_entry"]["description_status"] == "verified"

        index_page = client.get("/")
        assert index_page.status_code == 200
        index_html = index_page.get_data(as_text=True)
        assert 'id="app"' in index_html
        assert "/__frontend/" in index_html

        detail_page = client.get("/strategy/demo_alpha")
        assert detail_page.status_code == 200
        assert 'id="app"' in detail_page.get_data(as_text=True)

        compare_page = client.get("/compare")
        assert compare_page.status_code == 200
        assert 'id="app"' in compare_page.get_data(as_text=True)

        for legacy_asset in (
            "/static/library.js",
            "/static/legacy-auth.js",
            "/static/legacy-auth.css",
        ):
            assert client.get(legacy_asset).status_code == 404

    assert payload["overview"]["total"] == 2
    assert payload["items"][0]["name"] == "demo_alpha"


def test_catalog_and_detail_normalize_legacy_negative_drawdown(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    _write_library_strategy(
        library_root,
        name="legacy_drawdown",
        module="strategy.py",
        title="Legacy Drawdown",
        summary="legacy cache",
        strategy_body="STRATEGY_META = {}",
        cached_meta={
            "run_id": "20260314_220000_00002",
            "strategy_module": "strategy.py",
            "backtest": {
                "start_time": "20240101",
                "end_time": "20241231",
                "offset_days": 120,
                "universe_prefixes": ["300", "301"],
                "exclude_bj": True,
                "exclude_st": True,
            },
            "extra": {"status": "completed"},
            "result": {
                "cum_return": 0.2,
                "max_drawdown": -0.12,
                "final_net_value": 1.2,
            },
        },
    )
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(
        library,
        "_build_benchmark_curves",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("strategy detail must not query benchmark data")
        ),
    )

    entry = library.get_library_catalog()["items"][0]
    detail = library.get_library_detail("legacy_drawdown")

    assert entry["max_drawdown"] == 0.12
    assert detail is not None
    assert detail["result"]["max_drawdown"] == 0.12


def test_catalog_metrics_follow_net_value_curve_baseline(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    _write_library_strategy(
        library_root,
        name="curve_metrics",
        module="strategy.py",
        title="Curve Metrics",
        summary="curve cache",
        strategy_body="STRATEGY_META = {}",
        cached_meta={
            "run_id": "20260314_220000_00003",
            "strategy_module": "strategy.py",
            "backtest": {
                "start_time": "20240101",
                "end_time": "20241231",
                "offset_days": 120,
                "universe_prefixes": ["300", "301"],
                "exclude_bj": True,
                "exclude_st": True,
            },
            "extra": {"status": "completed"},
            "result": {
                "cum_return": 0.2,
                "max_drawdown": -0.12,
                "final_net_value": 1.2,
            },
        },
    )
    strategy_dir = library_root / "curve_metrics"
    cache_dir = strategy_dir / "cache"
    (cache_dir / "net_value.json").write_text(
        json.dumps({
            "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "net_value": [0.99, 1.01, 0.98],
        }),
        encoding="utf-8",
    )
    publish_web_cache_fixture(
        strategy_dir,
        flat_cache_dir=cache_dir,
        generation_id="20260314_220000_00004",
    )
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    entry = library.get_library_catalog()["items"][0]
    detail = library.get_library_detail("curve_metrics")

    # Catalog metrics are persisted run metadata; it no longer reopens the
    # curve artifact to repair incomplete legacy metadata.
    assert entry["cum_return"] == 0.2
    assert entry["final_net_value"] == 1.2
    assert entry["max_drawdown"] == 0.12
    assert detail is not None
    assert detail["result"]["cum_return"] == -0.02
    assert detail["result"]["final_net_value"] == 0.98
    assert detail["result"]["max_drawdown"] == 0.029703


def test_index_prefers_vue_template_when_manifest_exists(tmp_path, monkeypatch):
    dist_root = tmp_path / "frontend_dist"
    (dist_root / "favicon.svg").parent.mkdir(parents=True, exist_ok=True)
    (dist_root / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    manifest_path = dist_root / ".vite" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app.js",
                    "css": ["assets/app.css"],
                    "isEntry": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server_module, "_frontend_dist_root", dist_root)

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert 'id="app"' in text
        assert 'href="/__frontend/favicon.svg"' in text
        assert "/__frontend/assets/app.js" in text
        assert "/__frontend/assets/app.css" in text
        favicon = client.get("/__frontend/favicon.svg")
        assert favicon.status_code == 200


def test_index_ignores_legacy_selector_when_vue_manifest_exists(tmp_path, monkeypatch):
    dist_root = tmp_path / "frontend_dist"
    manifest_path = dist_root / ".vite" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app.js",
                    "isEntry": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server_module, "_frontend_dist_root", dist_root)

    with app.test_client() as client:
        response = client.get("/?legacy=1")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert 'id="app"' in text
    assert 'id="compare-view"' not in text


def test_index_returns_service_unavailable_without_vue_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_frontend_dist_root", tmp_path / "missing-dist")
    monkeypatch.delenv("WEB_VUE_DEV_SERVER", raising=False)

    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 503
    assert response.get_data(as_text=True) == "frontend build not found"


def test_frontend_visualization_panels_register_required_echarts_modules_and_payload_fallbacks():
    root = Path(__file__).resolve().parents[1]
    echarts_source = (root / "frontend" / "src" / "lib" / "echarts.ts").read_text(encoding="utf-8")
    detail_source = (root / "frontend" / "src" / "features" / "detail" / "StrategyDetailPage.vue").read_text(encoding="utf-8")
    performance_source = (root / "frontend" / "src" / "features" / "detail" / "components" / "DetailPerformanceSection.vue").read_text(encoding="utf-8")
    compare_source = (root / "frontend" / "src" / "features" / "compare" / "StrategyComparePage.vue").read_text(encoding="utf-8")
    heatmap_source = (root / "frontend" / "src" / "components" / "HoldingsHeatmapPanel.vue").read_text(encoding="utf-8")
    distribution_source = (root / "frontend" / "src" / "components" / "TradeDistributionPanel.vue").read_text(encoding="utf-8")
    return_heatmap_source = (root / "frontend" / "src" / "components" / "ReturnHeatmapPanel.vue").read_text(encoding="utf-8")
    return_heatmap_chart_source = (root / "frontend" / "src" / "components" / "ReturnHeatmapChart.vue").read_text(encoding="utf-8")
    compare_return_heatmap_source = (root / "frontend" / "src" / "components" / "CompareReturnHeatmapPanel.vue").read_text(encoding="utf-8")
    return_heatmap_utils_source = (root / "frontend" / "src" / "utils" / "returnHeatmap.ts").read_text(encoding="utf-8")
    card_source = (root / "frontend" / "src" / "features" / "catalog" / "components" / "StrategyCard.vue").read_text(encoding="utf-8")
    catalog_page_source = (root / "frontend" / "src" / "features" / "catalog" / "StrategyLibraryPage.vue").read_text(encoding="utf-8")
    catalog_polling_source = (root / "frontend" / "src" / "features" / "catalog" / "useCatalogBacktestPolling.ts").read_text(encoding="utf-8")
    workbench_style_source = (root / "frontend" / "src" / "features" / "detail" / "strategy-detail-workbench.css").read_text(encoding="utf-8")
    server_source = (root / "server.py").read_text(encoding="utf-8")
    server_runtime_source = (root / "server_runtime.py").read_text(encoding="utf-8")

    assert "BarChart" in echarts_source
    assert "HeatmapChart" in echarts_source
    assert "CalendarComponent" in echarts_source
    assert "VisualMapComponent" in echarts_source
    assert "type: 'heatmap'" in heatmap_source
    assert "isWeekendDate" in heatmap_source
    assert "['周一', '周二', '周三', '周四', '周五']" in heatmap_source
    assert "position: 'top'" in heatmap_source
    assert "xAxisIndex: [0, 1]" in heatmap_source
    assert "markLine" in heatmap_source
    assert "yearBoundaryWeeks" in heatmap_source
    assert "type: 'bar'" in distribution_source
    assert "type: 'line'" in distribution_source
    assert "monthIndexToKey" in distribution_source
    assert "zeroTradeMonthLabels" in distribution_source
    assert "0 交易月份" in distribution_source
    assert "interval: viewMode.value === 'month' ? 0 : 'auto'" in distribution_source
    assert "ReturnHeatmapChart" in return_heatmap_source
    assert "buildReturnHeatmapData" in return_heatmap_source
    assert "hasReturnHeatmapExcessSeries" in return_heatmap_source
    assert "DEFAULT_RETURN_HEATMAP_GRANULARITY" in return_heatmap_source
    assert "DEFAULT_RETURN_HEATMAP_METRIC_MODE" in return_heatmap_source
    assert "RETURN_HEATMAP_GRANULARITY_OPTIONS" in return_heatmap_source
    assert "RETURN_HEATMAP_METRIC_OPTIONS" in return_heatmap_source
    assert "type: 'heatmap'" in return_heatmap_chart_source
    assert "dimension: 2" in return_heatmap_chart_source
    assert "#dcfce7" in return_heatmap_chart_source
    assert "#fee2e2" in return_heatmap_chart_source
    assert "sharedMaxAbs" in compare_return_heatmap_source
    assert "ReturnHeatmapChart" in compare_return_heatmap_source
    assert "DEFAULT_RETURN_HEATMAP_GRANULARITY" in compare_return_heatmap_source
    assert "DEFAULT_RETURN_HEATMAP_METRIC_MODE" in compare_return_heatmap_source
    assert "RETURN_HEATMAP_GRANULARITY_OPTIONS" in compare_return_heatmap_source
    assert "RETURN_HEATMAP_METRIC_OPTIONS" in compare_return_heatmap_source
    assert "resolveXAxisLabels" in return_heatmap_utils_source
    assert "resolveIsoWeek" in return_heatmap_utils_source
    assert "excess_net_value" in return_heatmap_utils_source
    assert "byDate.set(dateText" in return_heatmap_utils_source
    assert "left.date.getTime() - right.date.getTime()" in return_heatmap_utils_source
    assert "previousPointValue ?? pointValue" in return_heatmap_utils_source
    assert "(end / base) - 1" in return_heatmap_utils_source
    assert "buildReturnHeatmapPanelNote" in return_heatmap_utils_source
    assert "Promise.allSettled" in compare_source
    assert "fetchLibraryCatalog" in catalog_page_source
    assert "searchQuery" in catalog_page_source
    assert "familyFilter" in catalog_page_source
    assert "statusFilter" in catalog_page_source
    assert "sortMode" in catalog_page_source
    assert "v-for=\"(item, index) in filteredItems\"" in catalog_page_source
    assert "fetchLogTail" not in catalog_page_source
    assert "startBacktestRun" in catalog_polling_source
    assert "fetchBacktestStatus" in catalog_polling_source
    assert 'class="catalog-run-all"' in catalog_page_source
    assert '@run="openBacktestDialog(item)"' in catalog_page_source
    assert 'class="backtest-dialog"' in catalog_page_source
    assert catalog_page_source.count('type="date"') == 2
    assert "date_overrides" in catalog_page_source
    assert "stopPolling" in catalog_polling_source
    assert "CompareToolbar" in catalog_page_source
    assert "log-service-card" not in catalog_page_source
    assert "strategy-record" in card_source
    assert "record-metric" in card_source
    assert "library-run-button" in card_source
    assert "emit('run', props.item.name)" in card_source
    assert "详情" in card_source
    assert "backtest-card-panel" not in card_source
    assert "查看最近日志" not in card_source
    assert 'class="library-result-link"' in card_source
    assert "research-sidebar" not in detail_source
    assert "right-rail" not in detail_source
    assert "detail-links-panel" not in detail_source
    assert "strategy_description" in detail_source
    assert "execution_rules" not in detail_source
    assert "humanizeRuleText" not in detail_source
    assert ".strategy-workbench-v2" in workbench_style_source
    assert ".strategy-logic-panel" in workbench_style_source
    assert 'WEB_DEBUG", "0"' in server_runtime_source
    assert "WEB_SERVE_MODE" in server_runtime_source
    assert "DEFAULT_WEB_THREADS = 4" in server_runtime_source
    assert "threads=web_threads" in server_runtime_source
    assert "WEB_TASK_STATE_DB" in (
        root / "task_coordinator.py"
    ).read_text(encoding="utf-8")
    assert "WEB_CORS_ORIGINS" in server_source
    assert "/api/logs/tail" in server_source

    assert "summary_by_date?.[date]" in detail_source
    assert "summary?.position_value" in detail_source
    assert "summary?.asset" in detail_source
    assert "row['持仓日']" in detail_source
    assert "row['hold_days']" in detail_source
    assert "row['盈亏']" in detail_source
    assert "inferHoldingDays(buyDate, sellDate)" in detail_source
    assert "readRateValue(returnRateValue)" in detail_source
    assert "ReturnHeatmapPanel" in performance_source
    assert ':baseline-value="activeRange === \'all\' ? 1 : null"' in detail_source
    assert "CompareReturnHeatmapPanel" in compare_source
    assert '<CompareReturnHeatmapPanel :strategies="heatmapStrategies" />' in compare_source


def test_library_summary_prefers_strategy_logic_over_freeform_notes(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    library_root = project_root / "web" / "strategy_library"

    logic_body = """
STRATEGY_META = {
    "portfolio_num_j": 2,
    "portfolio_num_i": 2,
    "use_index_timing": True,
    "use_board_timing": True,
}
STRATEGY_SEMANTICS = {
    "strategy_type": "mean_reversion",
    "holding_period": "short",
    "market": "A_share",
    "timeframe": "daily",
}
STRATEGY_DESCRIPTION = {
    "entry": "mean reversion entry",
    "exit": "mean reversion exit",
    "ranking": "rank by reclaim score",
}
STRATEGY_LIMITS = {
    "max_indicators": 30,
    "max_conditions": 10,
    "max_rolling_window": 250,
}
"""

    _write_library_strategy(
        library_root,
        name="demo_logic",
        module="strategy.py",
        title="Demo Logic",
        summary="这里是一段不应该出现在卡片上的技术说明",
        strategy_body=logic_body,
        cached_meta=None,
    )
    manifest_path = library_root / "demo_logic" / "strategy_description.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profile"].update(
        {
            "summary": "LLM authored summary",
            "buy_logic": "LLM authored entry",
            "sell_logic": "LLM authored exit",
        }
    )
    manifest["semantics"] = {
        "strategy_type": "mean_reversion",
        "holding_period": "short",
        "market": "A_share",
        "timeframe": "daily",
    }
    manifest["description"] = {
        "entry": "mean reversion entry",
        "exit": "mean reversion exit",
        "ranking": "rank by reclaim score",
    }
    manifest["limits"] = {"max_conditions": 10}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(library, "_project_root", project_root)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    item = library.get_library_catalog()["items"][0]
    assert item["summary"] == "LLM authored summary"
    assert item["buy_logic"] == "LLM authored entry"
    assert item["strategy_description"]["semantics"]["strategy_type"] == "mean_reversion"
    assert item["strategy_description"]["description"]["entry"] == "mean reversion entry"
    assert item["strategy_description"]["description"]["ranking"] == "rank by reclaim score"
    assert item["strategy_description"]["limits"]["max_conditions"] == 10


def test_api_backtest_run_accepts_per_strategy_date_overrides(monkeypatch):
    captured: dict[str, object] = {}

    def fake_start_library_backtest(names=None, *, date_overrides=None):
        captured["names"] = names
        captured["date_overrides"] = date_overrides
        return True

    monkeypatch.setattr(server_module, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server_module, "get_library_catalog", lambda: {"items": [{"name": "demo_alpha"}]})
    monkeypatch.setattr(server_module, "start_library_backtest", fake_start_library_backtest)
    monkeypatch.setattr(server_module, "get_backtest_progress", lambda: {"running": True, "total": 1, "completed": 0, "current": "demo_alpha"})

    with app.test_client() as client:
        response = client.post(
            "/api/backtest/run",
            json={
                "names": ["demo_alpha"],
                "date_overrides": {
                    "demo_alpha": {
                        "start_time": "2024-02-01",
                        "end_time": "20240229",
                    }
                },
            },
        )

    assert response.status_code == 200
    assert captured == {
        "names": ["demo_alpha"],
        "date_overrides": {"demo_alpha": {"start_time": "20240201", "end_time": "20240229"}},
    }


def test_api_backtest_run_drops_overrides_equal_to_strategy_config(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_alpha"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_start_library_backtest(names=None, *, date_overrides=None):
        captured["names"] = names
        captured["date_overrides"] = date_overrides
        return True

    monkeypatch.setattr(server_module, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(server_module, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server_module, "get_library_catalog", lambda: {"items": [{"name": "demo_alpha"}]})
    monkeypatch.setattr(server_module, "start_library_backtest", fake_start_library_backtest)
    monkeypatch.setattr(server_module, "get_backtest_progress", lambda: {"running": True, "total": 1, "completed": 0})

    with app.test_client() as client:
        response = client.post(
            "/api/backtest/run",
            json={
                "names": ["demo_alpha"],
                "date_overrides": {
                    "demo_alpha": {"start_time": "2024-01-01", "end_time": "20240131"},
                },
            },
        )

    assert response.status_code == 200
    assert captured == {"names": ["demo_alpha"], "date_overrides": {}}


def test_api_backtest_run_rejects_unknown_strategy_name(monkeypatch):
    monkeypatch.setattr(server_module, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server_module, "get_library_catalog", lambda: {"items": [{"name": "demo_alpha"}]})

    with app.test_client() as client:
        response = client.post("/api/backtest/run", json={"names": ["missing_demo"]})

    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown strategy: missing_demo"


def test_api_backtest_run_validates_single_sided_override_against_strategy_config(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_alpha"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(server_module, "is_backtest_running", lambda: False)
    monkeypatch.setattr(server_module, "get_library_catalog", lambda: {"items": [{"name": "demo_alpha"}]})

    with app.test_client() as client:
        response = client.post(
            "/api/backtest/run",
            json={
                "names": ["demo_alpha"],
                "date_overrides": {"demo_alpha": {"end_time": "20231231"}},
            },
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid date range for demo_alpha"


def test_api_library_config_redacts_sensitive_values(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_alpha"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text(
        "[mysql]\npassword = actual-secret\nhost = 127.0.0.1\n[tushare]\ntoken: actual-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "LIBRARY_ROOT", library_root)

    with app.test_client() as client:
        response = client.get("/api/library/demo_alpha/config")

    assert response.status_code == 200
    content = response.get_json()["content"]
    assert "actual-secret" not in content
    assert "actual-token" not in content
    assert "password = <redacted>" in content
    assert "token: <redacted>" in content
    assert "host = 127.0.0.1" in content
