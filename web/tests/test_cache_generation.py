from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtest.artifacts import ArtifactGenerationError
from core.artifact_schema import ARTIFACT_SCHEMA_VERSION, artifact_schema_fields
from web.cache_generation import (
    WEB_CACHE_LAYOUT_VERSION,
    WEB_CACHE_RELEASE_ID,
    activate_web_cache_generation,
    list_web_cache_generations,
    publish_web_cache_generation,
    resolve_current_web_cache,
    resolve_latest_web_cache_attempt,
    web_cache_current_pointer_path,
)
from web import cache_management
from web.cache_management import (
    inspect_current_web_cache,
    plan_web_cache_prune,
    prune_web_cache_generations,
)
from web import execution as web_execution
from web import (
    library_cache,
    library_cache_publication,
    library_catalog,
    library_curves,
    library_metadata,
    library_runtime,
)
from web.insights import metrics as library_metrics
from web.tools.manage_cache_generations import (
    activate_cache_generation,
    list_cache_generations,
)
from web.tools import rebuild_cache_generations as rebuild_tool


pytestmark = pytest.mark.unit


def _write_payloads(
    root: Path,
    *,
    status: str = "completed",
    net_value: float = 1.1,
    include_net_value: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        **artifact_schema_fields("run_metadata", ARTIFACT_SCHEMA_VERSION),
        "run_id": root.name,
        "strategy_module": "strategy.py",
        "extra": {"status": status},
    }
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if include_net_value:
        payload = {
            **artifact_schema_fields("net_value", ARTIFACT_SCHEMA_VERSION),
            "dates": ["2024-01-02"],
            "net_value": [net_value],
        }
        (root / "net_value.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def test_web_runtime_delegates_strategy_loading_to_session_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    library_root = project_root / "web" / "strategy_library"
    managed_name = "managed_product"
    unmanaged_name = "unmanaged_strategy"
    for name in (managed_name, unmanaged_name):
        strategy_dir = library_root / name
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "strategy.py").write_text("VALUE = 1\n", encoding="utf-8")
        (strategy_dir / "config.ini").write_text(
            "[strategy]\nmodule = strategy.py\n"
            "[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
            encoding="utf-8",
        )
    executions: list[dict[str, object]] = []

    def _execute(**kwargs):
        executions.append(dict(kwargs))
        return SimpleNamespace(
            status="failed",
            error="expected test stop",
            meta={},
            run_result=None,
        )

    monkeypatch.setattr(library_runtime, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library_runtime, "get_config", lambda reload=True: object())
    monkeypatch.setattr(
        library_runtime,
        "_resolve_effective_backtest_window",
        lambda *args, **kwargs: {"start_time": "20240101", "end_time": "20240131"},
    )
    monkeypatch.setattr(library_runtime, "build_library_run_overrides", lambda *args, **kwargs: {})
    monkeypatch.setattr(library_runtime, "execute_web_backtest", _execute)
    monkeypatch.setattr(
        library_cache_publication,
        "_write_failure_cache",
        lambda *args, **kwargs: None,
    )

    assert library_runtime._run_single(managed_name)["status"] == "failed"
    assert library_runtime._run_single(unmanaged_name)["status"] == "failed"
    assert executions
    assert all("strategy" not in execution for execution in executions)
    assert all("strategy_descriptor" not in execution for execution in executions)
    assert all("trade_func" not in execution for execution in executions)


def test_web_execution_does_not_prepare_strategy_or_trade_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def _run_session(_base_cfg, _overrides, **kwargs):
        calls.append(dict(kwargs))
        return SimpleNamespace(
            result=None,
            ctx=SimpleNamespace(meta={"extra": {"status": "failed"}}),
        )

    monkeypatch.setattr(web_execution, "run_backtest_session_with_overrides", _run_session)

    web_execution.execute_web_backtest(base_cfg=object(), overrides={})

    assert len(calls) == 1
    assert "prepared_strategy" not in calls[0]
    assert "prepared_strategy_descriptor" not in calls[0]
    assert "trade_func" not in calls[0]


def test_web_cache_generation_publishes_verified_current_snapshot(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(tmp_path / "source-a")

    snapshot = publish_web_cache_generation(strategy_dir, "run-a", source)

    assert web_cache_current_pointer_path(strategy_dir).is_file()
    assert snapshot.generation_id == "run-a"
    assert WEB_CACHE_LAYOUT_VERSION == 1
    assert snapshot.generation.release_id == WEB_CACHE_RELEASE_ID == "v1-schema-v2"
    assert snapshot.artifact_path("meta.json").is_file()
    assert resolve_current_web_cache(strategy_dir).generation_id == "run-a"
    assert [item["relative_path"] for item in snapshot.manifest["artifacts"]] == [
        "cache/meta.json",
        "cache/net_value.json",
    ]
    assert [
        (
            item["catalog_key"],
            item["owner"],
            item["schema_version"],
            item["required"],
        )
        for item in snapshot.manifest["artifacts"]
    ] == [
        ("web_cache_meta", "web.strategy_library", 2, True),
        ("web_cache_net_value", "web.strategy_library", 2, False),
    ]


def test_web_cache_generation_rejects_wrong_schema_without_replacing_current(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    publish_web_cache_generation(strategy_dir, "run-a", _write_payloads(tmp_path / "source-a"))
    invalid = _write_payloads(tmp_path / "source-b")
    payload = json.loads((invalid / "net_value.json").read_text(encoding="utf-8"))
    payload["schema_version"] = ARTIFACT_SCHEMA_VERSION - 1
    (invalid / "net_value.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactGenerationError, match="schema version"):
        publish_web_cache_generation(strategy_dir, "run-b", invalid)

    assert resolve_current_web_cache(strategy_dir).generation_id == "run-a"
    assert list_web_cache_generations(strategy_dir) == ("run-a",)


def test_completed_web_cache_requires_net_value_before_publication(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(tmp_path / "source", include_net_value=False)

    with pytest.raises(ArtifactGenerationError, match="missing net_value"):
        publish_web_cache_generation(strategy_dir, "run-a", source)

    assert not web_cache_current_pointer_path(strategy_dir).exists()


def test_top_level_completed_status_cannot_bypass_required_net_value(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(
        tmp_path / "source",
        status="failed",
        include_net_value=False,
    )
    meta_path = source / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "completed"
    meta["extra"] = {}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ArtifactGenerationError, match="missing net_value"):
        publish_web_cache_generation(strategy_dir, "run-a", source)


@pytest.mark.parametrize(
    ("filename", "artifact_type", "body", "message"),
    [
        ("trades.json", "trades", {}, "supported data field"),
        ("daily_holdings.json", "daily_holdings", {"dates": []}, "holdings_by_date"),
        ("market_state.json", "market_state", {"states": {}}, "observations"),
        ("positions.json", "positions", {"records": {}}, "requires list field"),
        (
            "position_changes.json",
            "position_changes",
            {"records": {}},
            "requires list field",
        ),
    ],
)
def test_web_cache_generation_rejects_invalid_artifact_shapes(
    tmp_path: Path,
    filename: str,
    artifact_type: str,
    body: dict[str, object],
    message: str,
) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(tmp_path / "source")
    payload = {
        **artifact_schema_fields(artifact_type, ARTIFACT_SCHEMA_VERSION),
        **body,
    }
    (source / filename).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactGenerationError, match=message):
        publish_web_cache_generation(strategy_dir, "run-a", source)


def test_web_cache_generation_rejects_misaligned_optional_net_value_series(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(tmp_path / "source")
    net_value_path = source / "net_value.json"
    payload = json.loads(net_value_path.read_text(encoding="utf-8"))
    payload["drawdown"] = [0.0, -0.1]
    net_value_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactGenerationError, match="length mismatch"):
        publish_web_cache_generation(strategy_dir, "run-a", source)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dates", ["not-a-date"], "invalid date"),
        ("net_value", [0.0], "must be positive"),
        ("net_value", [-0.1], "must be positive"),
    ],
)
def test_web_cache_generation_rejects_invalid_net_value_contract(
    tmp_path: Path,
    field: str,
    value: list[object],
    message: str,
) -> None:
    strategy_dir = tmp_path / "strategy"
    source = _write_payloads(tmp_path / "source")
    payload = json.loads((source / "net_value.json").read_text(encoding="utf-8"))
    payload[field] = value
    (source / "net_value.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ArtifactGenerationError, match=message):
        publish_web_cache_generation(strategy_dir, "run-a", source)


def test_failed_web_cache_can_atomically_hide_previous_success(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    publish_web_cache_generation(strategy_dir, "run-a", _write_payloads(tmp_path / "source-a"))
    failed = _write_payloads(
        tmp_path / "source-b",
        status="failed",
        include_net_value=False,
    )

    snapshot = publish_web_cache_generation(strategy_dir, "run-b", failed)

    assert snapshot.generation_id == "run-b"
    assert not (snapshot.cache_dir / "net_value.json").exists()
    assert resolve_current_web_cache(strategy_dir).generation_id == "run-b"


def test_web_cache_reader_rejects_tamper_and_can_activate_rollback(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "strategy"
    publish_web_cache_generation(strategy_dir, "run-a", _write_payloads(tmp_path / "source-a", net_value=1.1))
    current = publish_web_cache_generation(
        strategy_dir,
        "run-b",
        _write_payloads(tmp_path / "source-b", net_value=1.2),
    )
    current.artifact_path("net_value.json").write_text("{}", encoding="utf-8")

    # Normal catalog reads reuse the validated snapshot keyed by current.json;
    # an explicit health check opts into re-hashing the immutable artifacts.
    assert resolve_current_web_cache(strategy_dir).generation_id == "run-b"
    with pytest.raises(ArtifactGenerationError, match=r"(?:size|hash) mismatch"):
        resolve_current_web_cache(strategy_dir, force_validate=True)
    inspection = inspect_current_web_cache(strategy_dir)
    assert inspection["ok"] is False
    assert inspection["has_pointer"] is True

    rollback = activate_web_cache_generation(strategy_dir, "run-a")
    assert rollback.generation_id == "run-a"
    assert resolve_current_web_cache(strategy_dir).generation_id == "run-a"


def test_unactivated_generation_preserves_current_and_records_latest_attempt(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategy"
    publish_web_cache_generation(
        strategy_dir,
        "run-a",
        _write_payloads(tmp_path / "source-a"),
    )
    failed_source = _write_payloads(
        tmp_path / "source-b",
        status="failed",
        include_net_value=False,
    )
    failed_meta = json.loads((failed_source / "meta.json").read_text(encoding="utf-8"))
    failed_meta["error"] = "temporary source failure"
    failed_meta["evaluation_id"] = "eval-failed"
    (failed_source / "meta.json").write_text(
        json.dumps(failed_meta),
        encoding="utf-8",
    )

    published = publish_web_cache_generation(
        strategy_dir,
        "run-b",
        failed_source,
        activate=False,
    )

    assert published.generation_id == "run-b"
    assert resolve_current_web_cache(strategy_dir).generation_id == "run-a"
    latest = resolve_latest_web_cache_attempt(strategy_dir)
    assert latest is not None
    latest_snapshot, latest_pointer = latest
    assert latest_snapshot.generation_id == "run-b"
    assert latest_pointer["status"] == "failed"
    assert latest_pointer["activated"] is False
    assert latest_pointer["evaluation_id"] == "eval-failed"


def test_reader_does_not_serve_optional_file_outside_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    snapshot = publish_web_cache_generation(
        strategy_dir,
        "run-a",
        _write_payloads(tmp_path / "source"),
    )
    injected = {
        **artifact_schema_fields("trades", ARTIFACT_SCHEMA_VERSION),
        "records": [{"source": "injected"}],
    }
    (snapshot.cache_dir / "trades.json").write_text(
        json.dumps(injected),
        encoding="utf-8",
    )
    monkeypatch.setattr(library_cache, "LIBRARY_ROOT", library_root)

    assert library_curves.get_library_trades("demo") is None


def test_derived_metrics_pin_one_generation_snapshot(monkeypatch) -> None:
    marker = object()
    observed: list[object] = []

    monkeypatch.setattr(
        library_metrics,
        "_resolve_library_cache_snapshot",
        lambda name: marker,
    )

    def _net_value(name, *, snapshot=None, include_benchmark=True):
        observed.append(snapshot)
        return {
            "dates": ["2024-01-02", "2024-01-03"],
            "net_value": [1.0, 1.1],
        }

    def _trades(name, *, snapshot=None):
        observed.append(snapshot)
        return {"records": []}

    monkeypatch.setattr(library_metrics, "get_library_net_value", _net_value)
    monkeypatch.setattr(library_metrics, "get_library_trades", _trades)

    assert library_metrics.compute_derived_metrics("demo") is not None
    assert observed == [marker, marker]


def test_web_cache_management_lists_and_activates_generations(tmp_path: Path) -> None:
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True)
    publish_web_cache_generation(strategy_dir, "run-a", _write_payloads(tmp_path / "source-a"))
    publish_web_cache_generation(strategy_dir, "run-b", _write_payloads(tmp_path / "source-b"))
    listed = list_cache_generations(library_root)
    activated = activate_cache_generation(library_root, "demo", "run-a")

    assert listed["summary"]["valid_current"] == 1
    assert listed["items"][0]["ready_generations"] == ["run-a", "run-b"]
    assert activated["verified"] is True


def test_web_cache_prune_is_dry_run_by_default_and_invalidates_only_on_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_dir = tmp_path / "strategy"
    for generation_id in ("run-a", "run-b", "run-c"):
        publish_web_cache_generation(
            strategy_dir,
            generation_id,
            _write_payloads(tmp_path / f"source-{generation_id}"),
        )

    invalidations: list[Path] = []
    monkeypatch.setattr(
        cache_management,
        "invalidate_web_cache_snapshot_cache",
        lambda path: invalidations.append(Path(path)),
    )
    plan = plan_web_cache_prune(strategy_dir, keep_last=0)
    dry_run = prune_web_cache_generations(strategy_dir, keep_last=0)

    assert plan["dry_run"] is True
    assert [item["generation_id"] for item in plan["candidates"]] == ["run-a", "run-b"]
    assert dry_run["removed"] == []
    assert invalidations == []

    applied = prune_web_cache_generations(strategy_dir, keep_last=0, apply=True)
    assert [item["generation_id"] for item in applied["removed"]] == ["run-a", "run-b"]
    assert invalidations == [strategy_dir]
    assert list_web_cache_generations(strategy_dir) == ("run-c",)


def test_full_web_cache_rebuild_report_is_sorted_and_measured(tmp_path: Path, monkeypatch) -> None:
    library_root = tmp_path / "strategy_library"
    for name in ("beta", "alpha"):
        strategy_dir = library_root / name
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "config.ini").write_text(
            "[strategy]\nmodule = strategy.py\n",
            encoding="utf-8",
        )

    def _fake_run(name: str, *, clear_outputs: bool = False):
        assert clear_outputs is True
        strategy_dir = library_root / name
        publish_web_cache_generation(
            strategy_dir,
            f"run-{name}",
            _write_payloads(tmp_path / f"source-{name}"),
        )
        return {"name": name, "status": "completed", "error": ""}

    monkeypatch.setattr(rebuild_tool.library_runtime, "_run_single", _fake_run)

    report = rebuild_tool.rebuild_cache_generations(
        library_root,
    )

    assert [item["name"] for item in report["results"]] == ["alpha", "beta"]
    assert report["summary"]["strategy_count"] == 2
    assert report["summary"]["succeeded"] == 2
    assert report["summary"]["failed"] == 0
    assert report["summary"]["peak_rss_bytes"] > 0
    assert report["summary"]["generation_size_bytes"] > 0


def test_full_web_cache_rebuild_switches_all_library_roots_temporarily(
    tmp_path: Path,
    monkeypatch,
) -> None:
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )

    original_roots = {
        "cache": library_cache.LIBRARY_ROOT,
        "publication": library_cache_publication.LIBRARY_ROOT,
        "catalog": library_catalog.LIBRARY_ROOT,
        "metadata": library_metadata.LIBRARY_ROOT,
        "runtime": library_runtime.LIBRARY_ROOT,
    }

    def _fake_run(name: str, *, clear_outputs: bool = False):
        assert name == "demo"
        assert library_cache.LIBRARY_ROOT == library_root.resolve()
        assert library_cache_publication.LIBRARY_ROOT == library_root.resolve()
        assert library_catalog.LIBRARY_ROOT == library_root.resolve()
        assert library_metadata.LIBRARY_ROOT == library_root.resolve()
        assert library_runtime.LIBRARY_ROOT == library_root.resolve()
        publish_web_cache_generation(
            strategy_dir,
            "run-demo",
            _write_payloads(tmp_path / "source-demo"),
        )
        return {"name": name, "status": "completed", "error": ""}

    monkeypatch.setattr(rebuild_tool.library_runtime, "_run_single", _fake_run)

    report = rebuild_tool.rebuild_cache_generations(library_root)

    assert report["summary"]["failed"] == 0
    assert {
        "cache": library_cache.LIBRARY_ROOT,
        "publication": library_cache_publication.LIBRARY_ROOT,
        "catalog": library_catalog.LIBRARY_ROOT,
        "metadata": library_metadata.LIBRARY_ROOT,
        "runtime": library_runtime.LIBRARY_ROOT,
    } == original_roots
