"""Export a curated, installable V1 full-workspace public repository.

This exporter reads committed workspace content from ``HEAD``, applies an explicit
allow/deny policy, and writes a standalone candidate under the disposable workspace
root. It deliberately does not rewrite Git history or remove files from the internal
checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.local_artifact_paths import public_repo_workspace_root

DEFAULT_TARGET = public_repo_workspace_root("full_public_repo")

ROOT_FILES = {
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "conftest.py",
    "constraints-ci.txt",
    "mypy-v1-contracts.ini",
    "mypy-v1-progressive.ini",
    "pyproject.toml",
    "pytest.ini",
    "python-dependency-audit-policy.json",
    "requirements.txt",
    "main.py",
}

SOURCE_ROOTS = {
    "app",
    "backtest",
    "core",
    "runtime_lifecycle",
    "strategies",
    "trading",
    "web",
}

DOC_PREFIXES = (
    "docs/full/",
    "docs/validation/",
)

TOOL_EXCLUDES = (
    "tools/benchmarks/",
    "tools/paper_experiments/",
    "tools/benchmark_v1_panel_native.py",
    "tools/benchmark_v1_financial_digest.py",
    "tools/agent_strategy_backtest.py",
    "tools/codex_campaign.py",
    "tools/codex_strategy.py",
    "tools/compare_experiments.py",
    "tools/list_experiments.py",
    "tools/run_paper_experiments.py",
    "tools/v1_d4_",
)

DOC_EXCLUDES = (
    "docs/agent/",
    "docs/archive/",
    "docs/architecture/",
    "docs/chinese_journal_submission/",
    "docs/paper_submission/",
    "docs/paper_",
)

WEB_EXCLUDES = (
    "web/.codex_tmp",
    "web/.pytest_tmp",
    "web/.vite/",
    "web/data/",
    "web/user_workspaces/",
    "web/strategy_library/",
    "web/docs/archive/",
    "web/frontend/dist/",
    "web/frontend/node_modules/",
    "web/frontend/output/",
)

PUBLIC_WEB_STRATEGY_FILES = {
    "web/strategy_library/public_offline_smoke/config.ini",
    "web/strategy_library/public_offline_smoke/strategy_description.json",
}

PUBLIC_STRATEGY_FILES = {
    "strategy_library/__init__.py",
    "strategy_library/builtin/__init__.py",
    "strategy_library/builtin/breakout_20.py",
    "strategy_library/builtin/common.py",
    "strategy_library/builtin/offline_smoke.py",
    "strategy_library/custom/__init__.py",
    "strategy_library/generated/__init__.py",
    "strategy_library/validated/__init__.py",
}

PUBLIC_TEST_FILES = {
    "tests/test_basic.py",
    "tests/test_offline_smoke.py",
}

PUBLIC_DOC_FILES = {
    "docs/README.md",
    "docs/PRODUCT_BOUNDARY.md",
    "docs/strategy_field_dictionary.md",
}

PUBLIC_WORKFLOW_FILES = {
    ".github/dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".github/workflows/full-public-ci.yml",
}

EXTRA_FILES = {
    "tools/export_full_repo.py",
    "tools/audit_full_public_surface.py",
    "tools/smoke_full_public_repo.py",
    "docs/full/public_release.md",
    "docs/full/public_readme.md",
    "docs/full/public_readme_zh.md",
    ".github/workflows/full-public-ci.yml",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a curated V1 full-edition public repository.")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    target = args.target if args.target.is_absolute() else ROOT / args.target
    target = target.resolve()
    failures = export_full_repo(target=target, clean=args.clean)
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    if not args.quiet:
        print(f"[OK] full public repository: {target}")
    return 0


def export_full_repo(*, target: Path, clean: bool = False) -> list[str]:
    failures: list[str] = []
    if target.exists():
        if not clean:
            return [f"target already exists: {target}"]
        if not _is_safe_target(target):
            return [f"refusing to clean target outside allowed workspace roots: {target}"]
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    committed = _committed_paths()
    if committed is None:
        failures.append("full public export requires a Git checkout")
        return failures

    committed_set = set(committed)
    copied: list[str] = []
    untracked_release_inputs: list[str] = []
    for relative in sorted(committed_set | EXTRA_FILES, key=str.casefold):
        if not _include(relative):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative in committed_set:
            contents = _committed_file_bytes(relative)
            if contents is None:
                failures.append(f"cannot read committed exporter source: {relative}")
                continue
            destination.write_bytes(contents)
        else:
            source = ROOT / relative
            if not source.is_file():
                continue
            untracked_release_inputs.append(relative)
            shutil.copy2(source, destination)
        copied.append(relative)

    _copy_release_text(
        "docs/full/public_release.md",
        target / "docs" / "full" / "public_release.md",
        committed_set,
        failures,
    )
    _copy_release_text("docs/full/public_readme.md", target / "README.md", committed_set, failures)
    _copy_release_text("docs/full/public_readme_zh.md", target / "README_CN.md", committed_set, failures)
    _copy_release_text(
        "config/config.example.ini",
        target / "config" / "config.ini",
        committed_set,
        failures,
    )
    _write_public_config(target, failures)
    _write_public_web_strategy(target, failures)
    _write_manifest(
        target,
        copied,
        failures,
        untracked_release_inputs=untracked_release_inputs,
    )
    failures.extend(check_export(target))
    return failures


def check_export(target: Path) -> list[str]:
    failures: list[str] = []
    required = (
        "README.md",
        "README_CN.md",
        "LICENSE",
        "requirements.txt",
        "config/config.ini",
        "config/config.example.ini",
        "main.py",
        "web/__main__.py",
        "web/server.py",
        "web/frontend/package.json",
        "web/frontend/package-lock.json",
        "strategy_library/builtin/offline_smoke.py",
        "web/strategy_library/public_offline_smoke/config.ini",
        "web/strategy_library/public_offline_smoke/strategy_description.json",
        "docs/full/public_release.md",
        "tools/audit_full_public_surface.py",
        "tools/smoke_full_public_repo.py",
        "FULL_PUBLIC_RELEASE_MANIFEST.json",
    )
    for relative in required:
        if not (target / relative).is_file():
            failures.append(f"missing required full public file: {relative}")

    forbidden_top_level = {
        "projects",
        "output",
        "research",
        "plugins",
        "libreoffice1.cab",
        "New-upload-Zenodo.png",
    }
    actual_top_level = {path.name for path in target.iterdir()}
    for name in sorted(actual_top_level & forbidden_top_level, key=str.casefold):
        failures.append(f"forbidden top-level entry in full public export: {name}")

    for path in sorted(target.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(target).as_posix()
        if relative in PUBLIC_WEB_STRATEGY_FILES:
            continue
        if any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in WEB_EXCLUDES):
            failures.append(f"runtime artifact leaked into full public export: {relative}")
    return failures


def _include(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    if normalized in ROOT_FILES or normalized in PUBLIC_WORKFLOW_FILES:
        return normalized != "README.md"
    if normalized in {"config/config.ini", "config/config.local.ini", "config/users.json"}:
        return False
    if normalized == "libreoffice1.cab" or normalized == "New-upload-Zenodo.png":
        return False
    if normalized.startswith(".github/workflows/"):
        return False
    if normalized.startswith(".github/"):
        return normalized in PUBLIC_WORKFLOW_FILES
    if normalized.startswith("docs/"):
        if any(normalized.startswith(prefix) for prefix in DOC_EXCLUDES):
            return False
        return normalized.startswith(DOC_PREFIXES) or normalized in PUBLIC_DOC_FILES
    if normalized.startswith("data/"):
        return normalized.endswith(".py") or normalized.endswith("/README.md")
    if normalized.startswith("config/"):
        return normalized not in {"config/config.ini", "config/config.local.ini", "config/users.json"}
    if normalized.startswith("strategy_library/"):
        return normalized in PUBLIC_STRATEGY_FILES
    if normalized.startswith("tests/"):
        return normalized in PUBLIC_TEST_FILES or normalized.startswith("tests/test_public_")
    if normalized.startswith("tests_support/"):
        return normalized.endswith(".py") or normalized.endswith(".md")
    if normalized.startswith("tools/"):
        if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in TOOL_EXCLUDES):
            return False
        return True
    if normalized.startswith("web/"):
        if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in WEB_EXCLUDES):
            return False
        return True
    return any(normalized == root or normalized.startswith(f"{root}/") for root in SOURCE_ROOTS)


def _committed_paths() -> list[str] | None:
    try:
        completed = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--name-only", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(
        [item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item],
        key=str.casefold,
    )


def _committed_file_bytes(relative: str) -> bytes | None:
    try:
        return subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def _copy_release_text(
    relative: str,
    destination: Path,
    committed_set: set[str],
    failures: list[str],
) -> None:
    if relative in committed_set:
        contents = _committed_file_bytes(relative)
        if contents is None:
            failures.append(f"cannot read committed exporter source: {relative}")
            return
    else:
        source = ROOT / relative
        if not source.is_file():
            failures.append(f"missing exporter source: {relative}")
            return
        contents = source.read_bytes()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        failures.append(f"exporter source is not UTF-8 text: {relative}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8", newline="\n")


def _write_public_config(target: Path, failures: list[str]) -> None:
    config = target / "config" / "config.ini"
    if not config.is_file():
        return
    text = config.read_text(encoding="utf-8")
    text = text.replace(
        "module = strategy_library\\builtin\\offline_smoke.py",
        "module = strategy_library\\builtin\\offline_smoke.py",
    )
    if "TUSHARE_TOKEN" not in text:
        failures.append("public config is missing the TUSHARE_TOKEN environment contract")
    config.write_text(text, encoding="utf-8", newline="\n")


def _write_public_web_strategy(target: Path, failures: list[str]) -> None:
    """Expose the public smoke strategy through its canonical module only."""
    source = target / "strategy_library" / "builtin" / "offline_smoke.py"
    destination = target / "web" / "strategy_library" / "public_offline_smoke"
    if not source.is_file():
        failures.append("cannot create Web public strategy without builtin/offline_smoke.py")
        return
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "config.ini").write_text(
        "[strategy]\n"
        "module = strategy_library.builtin.offline_smoke\n\n"
        "[backtest]\n"
        "start_time = 20240101\n"
        "end_time = 20241231\n"
        "offset_days = 0\n\n"
        "[panel]\n"
        "source = parquet\n",
        encoding="utf-8",
        newline="\n",
    )
    description = {
        "schema_version": 2,
        "authoring": {"method": "llm_source_review", "language": "zh-CN"},
        "source": {
            "file": "strategy_library/builtin/offline_smoke.py",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "profile": {
            "title": "离线冒烟策略",
            "summary": "用于验证完整 V1 工作区安装、信号、执行和 Web 展示链的确定性合成策略。",
            "buy_logic": "每只股票在样本中的首个可用交易日产生一次买入意图。",
            "sell_logic": "买入意图出现后的第 5 个交易日产生卖出意图。",
            "position_text": "1*1",
            "style_label": "验证策略",
        },
        "runtime": {"top_n": 1, "portfolio_num_j": 1, "portfolio_num_i": 1},
        "buy": ["每只股票的首个样本交易日产生买入意图"],
        "sell": ["首个样本交易日后的第 5 个交易日产生卖出意图"],
        "investor_explanation": {
            "buy": {
                "summary": "仅用于确定性验证，不表达投资判断。",
                "groups": [
                    {
                        "title": "验证入口",
                        "items": [
                            {
                                "title": "首行买入",
                                "description": "每只股票在合成样本中的首个交易日产生一次买入意图。",
                            }
                        ],
                    }
                ],
            },
            "sell": {
                "summary": "按固定样本持有期退出。",
                "groups": [
                    {
                        "title": "验证退出",
                        "items": [
                            {
                                "title": "固定第 5 日",
                                "description": "从首个样本交易日计数，第 5 个交易日产生卖出意图。",
                            }
                        ],
                    }
                ],
            },
        },
    }
    (destination / "strategy_description.json").write_text(
        json.dumps(description, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_manifest(
    target: Path,
    copied: list[str],
    failures: list[str],
    *,
    untracked_release_inputs: list[str],
) -> None:
    files: list[dict[str, int | str]] = []
    for path in sorted(target.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.name == "FULL_PUBLIC_RELEASE_MANIFEST.json":
            continue
        relative = path.relative_to(target).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
    try:
        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        source_commit = ""
    try:
        source_worktree_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        source_worktree_dirty = True
    payload = {
        "schema_version": 1,
        "edition": "jshs-v1-full-public",
        "source_commit": source_commit,
        "generated_from_tracked_files": not untracked_release_inputs,
        "source_worktree_dirty": source_worktree_dirty,
        "untracked_release_inputs": sorted(untracked_release_inputs, key=str.casefold),
        "excluded": [
            "projects/",
            "output/",
            "real/vendor market data",
            "generated and validated private strategy candidates",
            "paper and research evidence",
            "local credentials and runtime caches",
        ],
        "files": files,
    }
    (target / "FULL_PUBLIC_RELEASE_MANIFEST.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _is_safe_target(target: Path) -> bool:
    resolved = Path(target).resolve()
    allowed_roots = ((ROOT / "output").resolve(), public_repo_workspace_root().resolve())
    for allowed_root in allowed_roots:
        try:
            resolved.relative_to(allowed_root)
        except ValueError:
            continue
        return resolved != allowed_root
    return False


if __name__ == "__main__":
    raise SystemExit(main())
