from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import web.library_cache as library_cache
import web.library_cache_identity as library_cache_identity
import web.library_catalog as library_catalog
import web.library_metadata as library_metadata
import web.library_runtime as library_runtime
from web.tools import check_strategy_library_health as health_tool


def _canonical_fixture(tmp_path: Path, *, source: str = "STRATEGY_META = {}\n") -> tuple[
    Path,
    Path,
    str,
]:
    project_root = tmp_path / "project"
    module_name = "strategy_library.generated.demo_strategy"
    source_path = project_root / "strategy_library" / "generated" / "demo_strategy.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    (project_root / "strategy_library" / "__init__.py").write_text("\n", encoding="utf-8")
    (project_root / "strategy_library" / "generated" / "__init__.py").write_text(
        "\n", encoding="utf-8"
    )
    library_root = project_root / "web" / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = " + module_name + "\n",
        encoding="utf-8",
    )
    return project_root, strategy_dir, module_name


def _patch_library_roots(monkeypatch, project_root: Path, library_root: Path) -> None:
    monkeypatch.setattr(library_metadata, "_project_root", project_root)
    monkeypatch.setattr(library_metadata, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library_catalog, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library_cache, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library_cache_identity, "_project_root", project_root)
    monkeypatch.setattr(library_runtime, "LIBRARY_ROOT", library_root)


def test_canonical_module_resolves_inside_repository_and_hashes_authoritative_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, strategy_dir, module_name = _canonical_fixture(tmp_path)
    monkeypatch.setattr(library_metadata, "_project_root", project_root)

    source_path = library_metadata._resolve_library_strategy_file(strategy_dir, module_name)

    assert source_path == (
        project_root / "strategy_library" / "generated" / "demo_strategy.py"
    ).resolve()
    assert library_metadata._library_strategy_source_issue(strategy_dir, module_name) == ""
    signature = library_metadata._expected_source_signature(
        strategy_dir,
        {"strategy.module": module_name},
    )
    source_bytes = (
        project_root / "strategy_library" / "generated" / "demo_strategy.py"
    ).read_bytes()
    assert signature == {
        "file": "strategy_library/generated/demo_strategy.py",
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size": len(source_bytes),
    }
    assert library_metadata._resolve_library_strategy_file(
        strategy_dir,
        "strategy_library.generated.demo_strategy../../outside",
    ) is None
    assert library_metadata._library_strategy_source_issue(strategy_dir, "other.package") == (
        "external_module_reference"
    )


def test_catalog_reports_canonical_source_and_invalidates_when_it_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, strategy_dir, module_name = _canonical_fixture(tmp_path)
    _patch_library_roots(monkeypatch, project_root, strategy_dir.parent)
    library_catalog.invalidate_library_catalog_cache()

    first = library_catalog.get_library_catalog()["items"][0]
    assert first["module"] == module_name
    assert first["source_file"] == "strategy_library/generated/demo_strategy.py"
    assert first["local_source_file"] is None
    source_path = project_root / "strategy_library" / "generated" / "demo_strategy.py"
    assert first["source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()

    source_path.write_text("STRATEGY_META = {'top_n': 2}\n", encoding="utf-8")
    second = library_catalog.get_library_catalog()["items"][0]
    assert second["source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert second["source_sha256"] != first["source_sha256"]


def test_runtime_keeps_canonical_dotted_module_for_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, strategy_dir, module_name = _canonical_fixture(tmp_path)
    _patch_library_roots(monkeypatch, project_root, strategy_dir.parent)
    monkeypatch.setattr(
        library_runtime,
        "_resolve_effective_backtest_window",
        lambda *args, **kwargs: {"start_time": "20240101", "end_time": "20240102"},
    )
    monkeypatch.setattr(library_runtime, "get_config", lambda reload=True: object())
    monkeypatch.setattr(
        library_runtime.library_evaluation_identity,
        "_resolved_execution_config",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        library_runtime.library_cache_publication,
        "_write_failure_cache",
        lambda *args, **kwargs: None,
    )
    captured: dict[str, object] = {}

    def _execute(*, base_cfg, overrides):
        captured.update(overrides)
        return SimpleNamespace(status="config_failed", error="fixture stop", meta={})

    monkeypatch.setattr(library_runtime, "execute_web_backtest", _execute)

    result = library_runtime._run_single("demo_strategy")

    assert result["status"] == "config_failed"
    assert captured["strategy.module"] == module_name


def test_health_check_uses_canonical_source_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, strategy_dir, module_name = _canonical_fixture(
        tmp_path,
        source="from strategy_library.generated import another_strategy\nSTRATEGY_META = {}\n",
    )
    _patch_library_roots(monkeypatch, project_root, strategy_dir.parent)
    monkeypatch.setattr(health_tool, "LIBRARY_ROOT", strategy_dir.parent)

    entries = health_tool.check_strategy_library_health()

    assert len(entries) == 1
    assert entries[0].module == module_name
    assert "strategy_file_not_found" not in entries[0].issues
    assert entries[0].source_sha256 == hashlib.sha256(
        (project_root / "strategy_library" / "generated" / "demo_strategy.py").read_bytes()
    ).hexdigest()
