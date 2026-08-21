from __future__ import annotations

import argparse
import json
import sys
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = WEB_ROOT / "strategy_library"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web import library_cache
from web.library_metadata import (
    _library_strategy_source_issue,
    _parse_strategy_config,
    _resolve_library_strategy_file,
    _source_file_signature,
)
from strategies.installation_validation import strategy_dependency_diagnostics


@dataclass(slots=True)
class HealthEntry:
    name: str
    status: str
    module: str
    cache_status: str
    cache_reason: str
    issues: list[str]
    source_file: str = ""
    source_sha256: str = ""


def _sync_library_root() -> None:
    library_cache.LIBRARY_ROOT = LIBRARY_ROOT


def _strategy_dirs() -> list[Path]:
    if not LIBRARY_ROOT.is_dir():
        return []
    return sorted(
        [
            path
            for path in LIBRARY_ROOT.iterdir()
            if path.is_dir() and not path.name.startswith(("_", "."))
        ],
        key=lambda path: path.name.casefold(),
    )


def _strategy_independence_issues(directory: Path, module: str) -> list[str]:
    source_path = _resolve_library_strategy_file(directory, module)
    if source_path is None or source_path.suffix.casefold() != ".py":
        return []
    # Auxiliary-file ownership and the Web upload dependency policy apply only
    # to Web-local packages. Canonical modules are governed by the root
    # strategy authoring gate and may share private strategy-library kernels.
    try:
        source_path.relative_to(directory.resolve())
    except (OSError, RuntimeError, ValueError):
        return []
    else:
        python_files = sorted(
            path for path in directory.rglob("*.py") if path.is_file()
        )
        issues = [
            f"strategy_auxiliary_python:{path.relative_to(directory).as_posix()}"
            for path in python_files
            if path.resolve() != source_path.resolve()
        ]
    try:
        with tokenize.open(source_path) as handle:
            source = handle.read()
        issues.extend(strategy_dependency_diagnostics(source))
    except (OSError, SyntaxError, UnicodeError, tokenize.TokenError):
        return issues
    return list(dict.fromkeys(issues))


def check_strategy_library_health() -> list[HealthEntry]:
    _sync_library_root()
    entries: list[HealthEntry] = []
    for directory in _strategy_dirs():
        issues: list[str] = []
        cfg_path = directory / "config.ini"
        cfg = _parse_strategy_config(cfg_path) if cfg_path.is_file() else {}
        module = str(cfg.get("strategy.module") or "").strip()
        if not cfg_path.is_file():
            issues.append("missing_config")
        else:
            source_issue = _library_strategy_source_issue(directory, module)
            if source_issue:
                issues.append(source_issue)
            else:
                issues.extend(_strategy_independence_issues(directory, module))

        source_path = _resolve_library_strategy_file(directory, module)
        source_signature = _source_file_signature(directory, source_path)

        diag: dict[str, Any] = library_cache.diagnose_library_cache(directory.name)
        cache_status = str(diag.get("cache_status") or "missing")
        cache_reason = str(diag.get("cache_reason") or "")
        if cache_status == "missing":
            issues.append("missing_cache")
        elif cache_status == "invalid":
            issues.append("invalid_cache")
        elif cache_status == "failed":
            issues.append("failed_cache")

        cache_issues = {"missing_cache", "invalid_cache", "failed_cache"}
        status = "ok" if not issues else ("warning" if set(issues) <= cache_issues else "error")
        entries.append(
            HealthEntry(
                name=directory.name,
                status=status,
                module=module,
                cache_status=cache_status,
                cache_reason=cache_reason,
                issues=issues,
                source_file=str(source_signature.get("file") or ""),
                source_sha256=str(source_signature.get("sha256") or ""),
            )
        )
    return entries


def _print_table(entries: list[HealthEntry]) -> None:
    if not entries:
        print("未找到策略目录。")
        return
    print("name\tstatus\tcache\tissues")
    for item in entries:
        issue_text = ",".join(item.issues) if item.issues else "-"
        cache_text = item.cache_status
        if item.cache_reason:
            cache_text = f"{cache_text}:{item.cache_reason}"
        print(f"{item.name}\t{item.status}\t{cache_text}\t{issue_text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 web 策略库 config、module 与 cache 健康状态")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于 CI 或脚本消费。")
    parser.add_argument("--strict", action="store_true", help="存在 warning 也返回非 0。默认仅 error 返回非 0。")
    args = parser.parse_args(argv)

    entries = check_strategy_library_health()
    if args.json:
        print(json.dumps([asdict(item) for item in entries], ensure_ascii=False, indent=2))
    else:
        _print_table(entries)

    has_error = any(item.status == "error" for item in entries)
    has_warning = any(item.status == "warning" for item in entries)
    if has_error or (args.strict and has_warning):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
