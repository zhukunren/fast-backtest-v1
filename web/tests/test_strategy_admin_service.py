from __future__ import annotations

import io
import hashlib
import json
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

import web.library_catalog as library_catalog
import web.library_cache as library_cache
import web.strategy_admin_service as strategy_admin_service
from web.maintenance_service import MaintenanceRequestError


SAFE_STRATEGY = b'''\
import pandas as pd
from backtest.panel_native import FeatureBatch, PanelView, SignalBatch
from strategies.api import BaseStrategy, compute_feature_batch, compute_signal_batch

STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "sort_spec": ["-score"],
    "portfolio_num_j": 1,
    "portfolio_num_i": 1,
}

def _compute_features(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.copy()

def _compute_signals(panel: pd.DataFrame):
    out = panel.copy()
    out["score"] = 1.0
    buy_cond = out["close"].notna().fillna(False)
    sell_cond = out["close"].isna().fillna(False)
    return buy_cond, sell_cond, out

class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META

    def compute_features(self, panel: PanelView) -> FeatureBatch:
        return compute_feature_batch(
            panel,
            _compute_features,
            strategy_meta=self.STRATEGY_META,
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        return compute_signal_batch(panel, _compute_signals)
'''


def _upload(filename: str, data: bytes) -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename)


def _description_manifest(source: bytes) -> dict[str, object]:
    digest = hashlib.sha256(source).hexdigest()
    return {
        "schema_version": 2,
        "authoring": {"method": "llm_source_review", "language": "zh-CN"},
        "source": {"file": "strategy.py", "sha256": digest},
        "profile": {
            "title": "上传测试策略",
            "summary": "由 LLM 生成并绑定当前源码的测试摘要。",
            "buy_logic": "满足测试买入条件时建立仓位。",
            "sell_logic": "满足测试卖出条件时退出仓位。",
            "position_text": "1*1",
            "style_label": "测试策略",
        },
        "runtime": {"top_n": 1, "portfolio_num_j": 1, "portfolio_num_i": 1},
        "semantics": {"timeframe": "daily"},
        "buy": ["满足测试买入条件"],
        "sell": ["满足测试卖出条件"],
        "investor_explanation": {
            "buy": {
                "summary": "满足测试买入条件时建立仓位。",
                "groups": [
                    {
                        "title": "买入条件",
                        "items": [
                            {
                                "title": "测试条件",
                                "description": "满足测试买入条件。",
                            }
                        ],
                    }
                ],
            },
            "sell": {
                "summary": "满足测试卖出条件时退出仓位。",
                "groups": [
                    {
                        "title": "卖出条件",
                        "items": [
                            {
                                "title": "测试条件",
                                "description": "满足测试卖出条件。",
                            }
                        ],
                    }
                ],
            },
        },
    }


def _strategy_package(
    source: bytes = SAFE_STRATEGY,
    *,
    config: str | None = None,
    manifest: dict[str, object] | None = None,
    include_config: bool = True,
    include_manifest: bool = True,
) -> bytes:
    config_text = config or """# 策略: 上传测试策略
[strategy]
module = strategy.py

[backtest]
start_time = 20240101
end_time = 20261231
"""
    description = manifest or _description_manifest(source)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("strategy.py", source)
        if include_config:
            archive.writestr("config.ini", config_text)
        if include_manifest:
            archive.writestr(
                "strategy_description.json",
                json.dumps(description, ensure_ascii=False),
            )
    return payload.getvalue()


def _strategy_upload(
    filename: str = "strategy.zip",
    source: bytes = SAFE_STRATEGY,
    *,
    config: str | None = None,
    manifest: dict[str, object] | None = None,
    include_config: bool = True,
    include_manifest: bool = True,
) -> FileStorage:
    return _upload(
        filename,
        _strategy_package(
            source,
            config=config,
            manifest=manifest,
            include_config=include_config,
            include_manifest=include_manifest,
        ),
    )


@pytest.fixture
def admin_paths(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    monkeypatch.setattr(library_cache, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library_catalog, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(strategy_admin_service, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(strategy_admin_service, "_UPLOAD_TEMP_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(strategy_admin_service, "_DELETED_ROOT", tmp_path / "deleted")
    monkeypatch.setattr(strategy_admin_service, "log_event", lambda *args, **kwargs: None)
    return library_root, tmp_path / "deleted"


def test_install_complete_strategy_package_is_visible_and_delete_is_recoverable(admin_paths):
    library_root, deleted_root = admin_paths

    installed = strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("local_alpha.zip"),
        {"strategy_name": "local_alpha"},
    )

    strategy_dir = library_root / "local_alpha"
    assert installed["name"] == "local_alpha"
    assert installed["source_file"] == "strategy.py"
    assert (strategy_dir / "strategy.py").is_file()
    config_text = (strategy_dir / "config.ini").read_text(encoding="utf-8")
    assert "module = strategy.py" in config_text
    assert "start_time = 20240101" in config_text
    assert (strategy_dir / "strategy_description.json").is_file()

    deleted = strategy_admin_service.delete_strategy("local_alpha")

    assert deleted["deleted"] is True
    assert deleted["recoverable"] is True
    assert not strategy_dir.exists()
    assert (deleted_root / deleted["archive_id"] / "strategy.py").is_file()


def test_overwrite_archives_previous_strategy(admin_paths):
    library_root, deleted_root = admin_paths
    form = {"strategy_name": "local_alpha"}
    strategy_admin_service.install_uploaded_strategy(_strategy_upload("alpha.zip"), form)

    updated_source = SAFE_STRATEGY.replace(b'1.0', b'2.0')
    installed = strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip", updated_source),
        {**form, "overwrite": "1"},
    )

    assert installed["overwritten"] is True
    assert installed["archive_id"]
    assert (deleted_root / installed["archive_id"] / "strategy.py").is_file()
    assert b'2.0' in (library_root / "local_alpha" / "strategy.py").read_bytes()


def test_admin_cannot_mutate_repository_owned_strategy(admin_paths):
    library_root, _ = admin_paths
    managed_dir = library_root / "managed_strategy"
    managed_dir.mkdir(parents=True)
    (managed_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy_library.generated.managed_strategy\n",
        encoding="utf-8",
    )

    with pytest.raises(MaintenanceRequestError, match="canonical"):
        strategy_admin_service.delete_strategy("managed_strategy")
    with pytest.raises(MaintenanceRequestError, match="canonical"):
        strategy_admin_service.rename_strategy("managed_strategy", "renamed_strategy")
    with pytest.raises(MaintenanceRequestError, match="canonical"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("managed_strategy.zip"),
            {"strategy_name": "managed_strategy", "overwrite": "1"},
        )
    assert managed_dir.is_dir()


def test_rename_strategy_preserves_cache_and_updates_admin_display_title(admin_paths):
    library_root, _ = admin_paths
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "local_alpha"},
    )
    cache_marker = library_root / "local_alpha" / "web_cache" / "current.json"
    cache_marker.parent.mkdir(parents=True)
    cache_marker.write_text('{"generation_id":"fixture"}', encoding="utf-8")

    renamed = strategy_admin_service.rename_strategy(
        "local_alpha",
        new_name="renamed_alpha",
        title="新的策略标题",
    )

    renamed_dir = library_root / "renamed_alpha"
    assert renamed == {
        "old_name": "local_alpha",
        "name": "renamed_alpha",
        "title": "新的策略标题",
        "renamed": True,
        "identifier_changed": True,
        "title_changed": True,
        "cache_preserved": True,
    }
    assert not (library_root / "local_alpha").exists()
    assert (renamed_dir / "strategy.py").is_file()
    assert (renamed_dir / "web_cache" / "current.json").read_text(encoding="utf-8") == (
        '{"generation_id":"fixture"}'
    )
    config_text = (renamed_dir / "config.ini").read_text(encoding="utf-8")
    assert "# 策略: 新的策略标题" in config_text
    assert "# 显示标题: 新的策略标题" in config_text


def test_rename_strategy_rejects_conflict_without_mutating_source(admin_paths):
    library_root, _ = admin_paths
    for name in ("alpha", "beta"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload(f"{name}.zip"),
            {"strategy_name": name},
        )
    original_config = (library_root / "alpha" / "config.ini").read_text(encoding="utf-8")

    with pytest.raises(MaintenanceRequestError, match="目标策略名称已存在") as exc_info:
        strategy_admin_service.rename_strategy("alpha", new_name="BETA", title="冲突标题")

    assert exc_info.value.status_code == 409
    assert (library_root / "alpha" / "config.ini").read_text(encoding="utf-8") == original_config
    assert (library_root / "beta" / "strategy.py").is_file()


def test_rename_strategy_can_keep_existing_title_when_title_is_omitted(admin_paths):
    library_root, _ = admin_paths
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "alpha"},
    )
    original_config = (library_root / "alpha" / "config.ini").read_text(encoding="utf-8")

    renamed = strategy_admin_service.rename_strategy("alpha", "beta")

    assert renamed["name"] == "beta"
    assert renamed["title"] == "上传测试策略"
    assert renamed["title_changed"] is False
    renamed_config = (library_root / "beta" / "config.ini").read_text(encoding="utf-8")
    assert renamed_config != original_config
    assert "# 策略: 上传测试策略" in renamed_config
    assert "# 显示标题: 上传测试策略" in renamed_config


def test_rename_strategy_can_change_title_without_moving_directory(admin_paths):
    library_root, _ = admin_paths
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "alpha"},
    )

    renamed = strategy_admin_service.rename_strategy(
        "alpha",
        new_name="alpha",
        title="新标题",
    )

    assert renamed["identifier_changed"] is False
    assert renamed["title_changed"] is True
    assert (library_root / "alpha" / "strategy.py").is_file()
    config_text = (library_root / "alpha" / "config.ini").read_text(encoding="utf-8")
    assert "# 显示标题: 新标题" in config_text


def test_rename_strategy_rolls_back_directory_when_config_write_fails(admin_paths, monkeypatch):
    library_root, _ = admin_paths
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "alpha"},
    )
    monkeypatch.setattr(
        strategy_admin_service,
        "_atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(OSError, match="write failed"):
        strategy_admin_service.rename_strategy("alpha", new_name="gamma", title="Gamma")

    assert (library_root / "alpha" / "strategy.py").is_file()
    assert not (library_root / "gamma").exists()


def test_case_only_rename_rolls_back_directory_when_config_write_fails(
    admin_paths,
    monkeypatch,
):
    library_root, _ = admin_paths
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "alpha"},
    )
    monkeypatch.setattr(
        strategy_admin_service,
        "_atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    with pytest.raises(OSError, match="write failed"):
        strategy_admin_service.rename_strategy("alpha", new_name="ALPHA", title="Alpha 2")

    directory_names = {path.name for path in library_root.iterdir() if path.is_dir()}
    assert "alpha" in directory_names
    assert "ALPHA" not in directory_names
    assert (library_root / "alpha" / "strategy.py").is_file()


@pytest.mark.parametrize("new_name", ("", "bad name", "../escape", "中文"))
def test_rename_strategy_rejects_invalid_new_name(admin_paths, new_name):
    strategy_admin_service.install_uploaded_strategy(
        _strategy_upload("alpha.zip"),
        {"strategy_name": "alpha"},
    )

    with pytest.raises(MaintenanceRequestError, match="策略名称只能包含"):
        strategy_admin_service.rename_strategy("alpha", new_name=new_name, title="Alpha")


def test_zip_upload_rejects_parent_directory_path(admin_paths):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.py", SAFE_STRATEGY)
    payload.seek(0)

    with pytest.raises(MaintenanceRequestError, match="非法路径"):
        strategy_admin_service.install_uploaded_strategy(
            FileStorage(stream=payload, filename="bad.zip"),
            {"strategy_name": "bad"},
        )


def test_upload_rejects_legacy_single_python_file(admin_paths):
    with pytest.raises(MaintenanceRequestError, match=r"仅支持上传包含完整策略包的 \.zip"):
        strategy_admin_service.install_uploaded_strategy(
            _upload("legacy.py", SAFE_STRATEGY),
            {"strategy_name": "legacy"},
        )


def test_upload_requires_llm_description_manifest(admin_paths):
    with pytest.raises(MaintenanceRequestError, match=r"缺少 strategy_description\.json"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("missing-manifest.zip", include_manifest=False),
            {"strategy_name": "missing_manifest"},
        )


def test_upload_rejects_manifest_with_stale_source_hash(admin_paths):
    manifest = _description_manifest(SAFE_STRATEGY)
    source = SAFE_STRATEGY.replace(b"1.0", b"2.0")
    with pytest.raises(MaintenanceRequestError, match="源码绑定的 LLM 策略描述"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("stale.zip", source, manifest=manifest),
            {"strategy_name": "stale"},
        )


def test_upload_requires_config_file(admin_paths):
    with pytest.raises(MaintenanceRequestError, match=r"缺少 config\.ini"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("missing-config.zip", include_config=False),
            {"strategy_name": "missing_config"},
        )


def test_upload_rejects_unsafe_strategy_source(admin_paths):
    unsafe = b"import os\n\ndef build_panel_features(panel):\n    return panel.copy()\n\ndef build_panel_signals(panel):\n    return panel.copy()\n"

    with pytest.raises(MaintenanceRequestError, match="unsafe_import"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("unsafe.zip", unsafe),
            {"strategy_name": "unsafe"},
        )


def test_upload_rejects_strategy_module_dependencies(admin_paths):
    dependent_source = (
        b"from strategy_library.generated import another_strategy\n" + SAFE_STRATEGY
    )

    with pytest.raises(MaintenanceRequestError, match="策略必须自包含"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("dependent.zip", dependent_source),
            {"strategy_name": "dependent"},
        )


def test_upload_rejects_project_shared_strategy_logic(admin_paths):
    dependent_source = b"from web.library_metadata import LIBRARY_ROOT\n" + SAFE_STRATEGY

    with pytest.raises(MaintenanceRequestError, match="策略必须自包含"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("dependent.zip", dependent_source),
            {"strategy_name": "dependent"},
        )


def test_upload_rejects_dynamic_strategy_loading(admin_paths):
    dependent_source = b"from importlib import import_module\n" + SAFE_STRATEGY

    with pytest.raises(MaintenanceRequestError, match="策略源码校验失败"):
        strategy_admin_service.install_uploaded_strategy(
            _strategy_upload("dependent.zip", dependent_source),
            {"strategy_name": "dependent"},
        )
