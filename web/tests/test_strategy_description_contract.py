from __future__ import annotations

import hashlib
import ast
import json
import os
from pathlib import Path

import web.library_metadata as metadata


ROOT = Path(__file__).resolve().parents[2]


def _web_production_python_paths() -> list[Path]:
    web_root = ROOT / "web"
    excluded_dirs = {
        "__pycache__",
        "data",
        "dist",
        "node_modules",
        "strategy_library",
        "tests",
        "user_workspaces",
    }
    paths: list[Path] = []
    for current, dirnames, filenames in os.walk(web_root):
        dirnames[:] = sorted(
            (
                name
                for name in dirnames
                if name not in excluded_dirs and not name.startswith(".")
            ),
            key=str.casefold,
        )
        for filename in sorted(filenames, key=str.casefold):
            if filename.casefold().endswith(".py"):
                paths.append(Path(current) / filename)
    return paths


def test_web_production_python_has_no_strategy_semantic_execution() -> None:
    forbidden_text = (
        "spec_from_file_location",
        "exec_module",
        "load_strategy_descriptor",
        "check_strategy_source",
        "validate_strategy_code_security",
        "find_strategy_dependency_issues",
        "build_time_ok",
        "include_timing",
        "TimingPayload",
        "execution_rules",
    )
    violations: dict[str, list[str]] = {}
    for path in _web_production_python_paths():
        text = path.read_text(encoding="utf-8")
        matched = [token for token in forbidden_text if token in text]
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in {"compile", "eval", "exec"}:
                    matched.append(f"builtin:{node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "spec_from_file_location",
                    "exec_module",
                }:
                    matched.append(f"dynamic:{node.func.attr}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if any(name in forbidden_text for name in names):
                    matched.append("forbidden-import")
        if matched:
            violations[path.relative_to(ROOT).as_posix()] = sorted(set(matched))

    assert not violations, violations
    assert not (ROOT / "web" / "library.py").exists()


def test_web_description_readers_have_no_source_semantic_compiler() -> None:
    paths = (
        ROOT / "web" / "library_metadata.py",
        ROOT / "web" / "library_catalog.py",
        ROOT / "web" / "frontend" / "src" / "features" / "detail" / "StrategyDetailPage.vue",
    )
    forbidden = ("import ast", "ast.parse", "_StrategyExecutionAnalyzer", "humanizeRuleText", "execution_rules")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path


def test_all_checked_in_strategy_descriptions_are_v2_and_source_bound() -> None:
    paths = sorted(
        (ROOT / "web" / "strategy_library").glob("*/strategy_description.json"),
        key=lambda path: path.parent.name.casefold(),
    )
    assert paths
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 2
        assert payload["authoring"]["method"] == "llm_source_review"
        profile = payload["profile"]
        assert all(isinstance(profile[field], str) and profile[field].strip() for field in (
            "title", "summary", "buy_logic", "sell_logic", "position_text", "style_label"
        ))
        config = metadata._parse_strategy_config(path.parent / "config.ini")
        source = metadata._resolve_library_strategy_file(
            path.parent,
            config.get("strategy.module", ""),
        )
        assert source is not None, path
        assert hashlib.sha256(source.read_bytes()).hexdigest() == payload["source"]["sha256"]


def test_checked_in_web_strategies_have_one_source_owner() -> None:
    canonical: list[str] = []
    web_owned: list[str] = []
    library_root = ROOT / "web" / "strategy_library"
    for directory in sorted(library_root.iterdir(), key=lambda path: path.name.casefold()):
        config_path = directory / "config.ini"
        if not directory.is_dir() or not config_path.is_file():
            continue
        config = metadata._parse_strategy_config(config_path)
        module = config.get("strategy.module", "")
        source = metadata._resolve_library_strategy_file(directory, module)
        assert source is not None, directory
        if module.startswith("strategy_library."):
            canonical.append(directory.name)
            assert not (directory / "strategy.py").exists(), directory
            assert source.is_relative_to(ROOT / "strategy_library")
        else:
            web_owned.append(directory.name)
            assert source.is_relative_to(directory)

    assert len(canonical) == 22
    assert len(web_owned) == 5
    assert not (ROOT / "web" / "strategy_publication_manifest.json").exists()
    assert not (ROOT / "tools" / "publish_web_strategy_snapshots.py").exists()


def test_checked_in_indicator_descriptions_are_investor_complete() -> None:
    paths = sorted(
        (ROOT / "web" / "strategy_library").glob("*/strategy_description.json"),
        key=lambda path: path.parent.name.casefold(),
    )
    assert paths
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        indicators = payload.get("indicators")
        assert isinstance(indicators, list) and indicators, path
        for index, indicator in enumerate(indicators):
            assert isinstance(indicator, dict), (path, index)
            for field in ("name", "purpose", "formula", "usage"):
                value = indicator.get(field)
                assert value, (path, index, field)


def test_every_catalog_strategy_has_a_verified_rule_description() -> None:
    """A catalog entry must never silently fall back to inferred rules."""
    strategy_dirs = sorted(
        (
            directory
            for directory in (ROOT / "web" / "strategy_library").iterdir()
            if directory.is_dir()
            and not directory.name.startswith(("_", "."))
            and (directory / "config.ini").is_file()
        ),
        key=lambda path: path.name.casefold(),
    )
    assert strategy_dirs

    missing: list[str] = []
    invalid: dict[str, list[str]] = {}
    for directory in strategy_dirs:
        config = metadata._parse_strategy_config(directory / "config.ini")
        source = metadata._resolve_library_strategy_file(
            directory,
            config.get("strategy.module", ""),
        )
        result = metadata._read_strategy_description_manifest(directory, source)
        if result["status"] != "verified":
            invalid[directory.name] = [
                str(item) for item in result.get("diagnostics", [])
            ]
            continue
        manifest = result.get("manifest")
        if not isinstance(manifest, dict):
            missing.append(directory.name)
            continue
        if not manifest.get("buy") or not manifest.get("sell"):
            invalid[directory.name] = ["buy/sell rule descriptions must be non-empty"]
        explanation = manifest.get("investor_explanation")
        if not isinstance(explanation, dict) or not explanation.get("buy") or not explanation.get("sell"):
            invalid.setdefault(directory.name, []).append(
                "investor_explanation.buy/sell must be present"
            )

    assert not missing, missing
    assert not invalid, invalid


def test_missing_or_invalid_description_is_explicit_and_never_inferred(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "demo"
    strategy_dir.mkdir()
    source = strategy_dir / "strategy.py"
    source.write_text("STRATEGY_META = {'top_n': 99}\n", encoding="utf-8")

    missing = metadata._read_strategy_description_manifest(strategy_dir, source)
    assert missing["status"] == "missing"
    assert missing["manifest"] is None

    (strategy_dir / "strategy_description.json").write_text("{broken", encoding="utf-8")
    invalid = metadata._read_strategy_description_manifest(strategy_dir, source)
    assert invalid["status"] == "invalid"
    assert invalid["manifest"] is None


def test_profile_title_is_manifest_owned_and_admin_label_is_separate(monkeypatch, tmp_path: Path) -> None:
    manifest = {
        "profile": {
            "title": "LLM 语义标题",
            "summary": "LLM 摘要",
            "buy_logic": "LLM 买入逻辑",
            "sell_logic": "LLM 卖出逻辑",
            "position_text": "1x1",
            "style_label": "LLM 风格",
        }
    }
    monkeypatch.setattr(
        metadata,
        "_read_strategy_description_manifest",
        lambda directory, source_path: {
            "status": "verified",
            "diagnostics": [],
            "manifest": manifest,
        },
    )

    source = tmp_path / "strategy.py"
    source.write_text("", encoding="utf-8")
    profile = metadata._build_strategy_profile(
        directory=tmp_path,
        notes={"display_title": ""},
        source_path=source,
        fallback_title="Directory Fallback",
    )
    assert profile["title"] == "LLM 语义标题"
    assert profile["display_title"] == ""

    profile = metadata._build_strategy_profile(
        directory=tmp_path,
        notes={"display_title": "管理员标题"},
        source_path=source,
        fallback_title="Directory Fallback",
    )
    assert profile["title"] == "LLM 语义标题"
    assert profile["display_title"] == "管理员标题"


def test_stale_description_is_not_served_as_current(tmp_path: Path) -> None:
    strategy_dir = tmp_path / "demo"
    strategy_dir.mkdir()
    source = strategy_dir / "strategy.py"
    source.write_text("def build_signals(df): return df\n", encoding="utf-8")
    payload = {
        "schema_version": 2,
        "authoring": {"method": "llm_source_review", "language": "zh-CN"},
        "source": {"file": "strategy.py", "sha256": "0" * 64},
        "profile": {
            "title": "Demo",
            "summary": "摘要",
            "buy_logic": "买入",
            "sell_logic": "卖出",
            "position_text": "1*1",
            "style_label": "测试",
        },
        "runtime": {},
        "buy": ["买入"],
        "sell": ["卖出"],
        "investor_explanation": {
            "buy": {"summary": "买入", "groups": [{"title": "买入", "items": [{"title": "条件", "description": "买入"}]}]},
            "sell": {"summary": "卖出", "groups": [{"title": "卖出", "items": [{"title": "条件", "description": "卖出"}]}]},
        },
    }
    (strategy_dir / "strategy_description.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    result = metadata._read_strategy_description_manifest(strategy_dir, source)
    assert result["status"] == "stale"
    assert result["manifest"] is None
