from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import tools.audit_full_public_surface as public_audit
import tools.export_full_repo as public_export


def test_full_public_include_policy_excludes_private_boundaries() -> None:
    assert not public_export._include("projects/private/notes.md")
    assert not public_export._include("config/config.local.ini")
    assert not public_export._include("tools/benchmark_v1_panel_native.py")
    assert not public_export._include("web/strategy_library/private_candidate/strategy.py")
    assert public_audit._is_forbidden("web/strategy_library/private_candidate/strategy.py")
    assert public_audit._is_forbidden("web/strategy_library/public_offline_smoke/strategy.py")
    assert not public_audit._is_forbidden("web/strategy_library/public_offline_smoke/config.ini")
    assert public_export._include("web/server.py")
    assert public_export._include("web/frontend/package-lock.json")


def test_committed_file_bytes_ignore_dirty_worktree(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "tracked.txt"
    source.write_text("committed\n", encoding="utf-8")

    for command in (
        ["git", "init", "-q"],
        ["git", "add", "tracked.txt"],
        [
            "git",
            "-c",
            "user.name=Public Export Test",
            "-c",
            "user.email=public-export-test@example.invalid",
            "commit",
            "-qm",
            "add tracked file",
        ],
    ):
        subprocess.run(command, cwd=repository, check=True)
    source.write_text("dirty working copy\n", encoding="utf-8")

    monkeypatch.setattr(public_export, "ROOT", repository)

    assert public_export._committed_paths() == ["tracked.txt"]
    assert public_export._committed_file_bytes("tracked.txt") == b"committed\n"


def test_full_public_export_is_redacted_and_manifested(tmp_path: Path) -> None:
    target = tmp_path / "full_public_repo"

    failures = public_export.export_full_repo(target=target)

    assert failures == []
    assert public_audit.audit(target) == []
    assert (target / "README_CN.md").read_text(encoding="utf-8").startswith("# JSHS V1 完整公共版")
    assert not (target / "config" / "config.local.ini").exists()
    assert not (target / "libreoffice1.cab").exists()

    config_text = (target / "config" / "config.ini").read_text(encoding="utf-8")
    assert "token_env = TUSHARE_TOKEN" in config_text
    assert any(line.strip() == "token =" for line in config_text.splitlines())

    web_config = target / "web" / "strategy_library" / "public_offline_smoke" / "config.ini"
    assert "module = strategy_library.builtin.offline_smoke" in web_config.read_text(encoding="utf-8")
    assert not web_config.with_name("strategy.py").exists()
    strategy_path = target / "strategy_library" / "builtin" / "offline_smoke.py"
    description = json.loads(
        (web_config.parent / "strategy_description.json").read_text(encoding="utf-8")
    )
    assert description["schema_version"] == 2
    assert description["authoring"]["method"] == "llm_source_review"
    assert description["source"] == {
        "file": "strategy_library/builtin/offline_smoke.py",
        "sha256": hashlib.sha256(strategy_path.read_bytes()).hexdigest(),
    }

    project = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "jshs-v1"
    assert project["project"]["readme"] == "README.md"
    assert "scripts" not in project["project"]

    release_manifest = json.loads(
        (target / "FULL_PUBLIC_RELEASE_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert release_manifest["source_commit"]
    assert isinstance(release_manifest["source_worktree_dirty"], bool)
    untracked_inputs = release_manifest["untracked_release_inputs"]
    assert release_manifest["generated_from_tracked_files"] is (not bool(untracked_inputs))
    assert all("node_modules/" not in item["path"] for item in release_manifest["files"])
