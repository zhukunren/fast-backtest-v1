from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_RELEASE_FILE_BYTES = 5_000_000
GENERATED_BUILD_PREFIXES = ("dist", "dist_public")

REQUIRED_FILES = (
    "README.md",
    "requirements.txt",
    "config/config.example.ini",
    "docs/full/README.md",
    "docs/full/data_onboarding.md",
    "docs/full/web.md",
    "docs/full/engine_boundary.md",
    "docs/full/release_candidate.md",
    "tools/audit_full_release.py",
    "tools/audit_frontend_bundle.py",
    "tools/audit_ci_constraints.py",
    "tools/full_quality_gate.py",
    "tools/llm_authoring_gate.py",
    "tools/syntax_check.py",
    "tools/validate_full_gate_report.py",
)

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".ini",
    ".in",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}

EXCLUDED_PREFIXES = (
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-wsl",
    "__pycache__",
    "build",
    "dist",
    "dist_public",
    "docs/archive",
    "output",
    "data/panel_cache",
    "projects/engine_v2",
    "projects/engine_v3",
    "web/data",
    "web/frontend/dist",
    "web/frontend/node_modules",
    "web/user_workspaces",
)

EXCLUDED_PARTS = {
    "__pycache__",
    "cache",
    "node_modules",
    "run_meta",
}

BINARY_SUFFIXES = {
    ".docx",
    ".gif",
    ".gz",
    ".jpg",
    ".jpeg",
    ".parquet",
    ".png",
    ".whl",
    ".xls",
    ".xlsx",
    ".zip",
}

TEXT_SCAN_ALLOWLIST = {
    "tests/test_full_release_audit.py",
    "tests/test_mysql_support.py",
    "tests/test_run_metadata_git.py",
}

SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b")),
    (
        "generic secret assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|token|password|passwd|pwd)\b"
            r"\s*[:=]\s*['\"]"
            r"(?!<|\$|%|\.\.\.|changeme|example|fake-|loader-|your[_-]|xxx|none|null|set|missing)"
            r"[A-Za-z0-9][A-Za-z0-9_./+=:@-]{11,}"
        ),
    ),
)

CONFIG_SECRET_ASSIGNMENT = re.compile(
    r"(?i)^\s*(?:api[_-]?key|access[_-]?token|secret|token|password|passwd|pwd)"
    r"\s*[:=]\s*(?P<value>.*)$"
)

SECRET_PLACEHOLDER_PREFIXES = (
    "<",
    "$",
    "%",
    "...",
    "changeme",
    "example",
    "fake-",
    "loader-",
    "your_",
    "your-",
    "xxx",
    "none",
    "null",
    "set",
    "missing",
)

REPOSITORY_ABSOLUTE_PATH = str(ROOT.resolve())

PRIVATE_PATH_PATTERNS = (
    ("windows user path", re.compile(r"\b[A-Z]:\\Users\\[^\\\s'\"]+")),
    ("repository absolute path", re.compile(re.escape(REPOSITORY_ABSOLUTE_PATH))),
    ("unix home path", re.compile(r"\b/(?:Users|home)/[A-Za-z0-9._-]+")),
)

CONNECTION_PATTERNS = (
    (
        "database url with credentials",
        re.compile(r"(?i)\b(?:mysql|mariadb|postgres|postgresql)://[^:\s/@]+:[^@\s]+@"),
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a full workspace release candidate.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    payload = audit_full_release(root)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    failures = payload["failures"]
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    if not args.quiet:
        print(f"[OK] full release audit: files={payload['files_checked']}")
    return 0


def audit_full_release(root: Path = ROOT) -> dict[str, Any]:
    files = collect_candidate_files(root)
    failures: list[str] = []
    failures.extend(check_required_files(root))
    failures.extend(check_default_strategy(root))
    failures.extend(check_tracked_build_artifacts(root))
    for path in files:
        failures.extend(check_file(root, path))
    return {
        "ok": not failures,
        "root": str(root),
        "files_checked": len(files),
        "failures": failures,
    }


def check_tracked_build_artifacts(root: Path = ROOT) -> list[str]:
    """Reject generated build outputs that still exist as tracked files."""
    if not (root / ".git").exists():
        return []
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=str(root),
            capture_output=True,
            check=True,
        )
    except Exception as exc:
        return [f"could not inspect tracked build artifacts: {type(exc).__name__}: {exc}"]
    tracked = [
        Path(item.decode("utf-8", errors="surrogateescape"))
        for item in completed.stdout.split(b"\0")
        if item
    ]
    offenders = [
        path.as_posix()
        for path in tracked
        if any(
            path.as_posix() == prefix or path.as_posix().startswith(f"{prefix}/")
            for prefix in GENERATED_BUILD_PREFIXES
        )
        and (root / path).exists()
    ]
    return [f"tracked generated build artifact: {path}" for path in sorted(offenders, key=str.casefold)]


def collect_candidate_files(root: Path = ROOT) -> list[Path]:
    raw_paths = _git_candidate_paths(root)
    if raw_paths is None:
        raw_paths = [path.relative_to(root) for path in root.rglob("*") if _is_file(path)]
    files = [root / path for path in raw_paths if not _is_excluded(path) and _is_file(root / path)]
    return sorted(files, key=lambda path: str(path.relative_to(root)).casefold())


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def check_required_files(root: Path = ROOT) -> list[str]:
    return [f"missing required full release file: {relative}" for relative in REQUIRED_FILES if not (root / relative).is_file()]


def check_default_strategy(root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    for relative in ("config/config.ini", "config/config.example.ini"):
        path = root / relative
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip().lower().startswith("module"):
                continue
            normalized = line.partition("=")[2].strip().replace("\\", "/")
            normalized = normalized.replace(".", "/")
            if "strategy_library/generated/" in normalized:
                failures.append(f"{relative}: default strategy must not point to generated strategies")
    return failures


def check_file(root: Path, path: Path) -> list[str]:
    relative = path.relative_to(root).as_posix()
    failures: list[str] = []
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [
            f"{relative}: unable to inspect full release candidate "
            f"({type(exc).__name__})"
        ]
    if size > MAX_RELEASE_FILE_BYTES:
        failures.append(f"{relative}: file too large for full release candidate ({size} bytes)")
    if path.suffix.lower() in BINARY_SUFFIXES:
        return failures
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return failures
    if relative in TEXT_SCAN_ALLOWLIST:
        return failures
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except OSError as exc:
        failures.append(
            f"{relative}: unable to read full release candidate "
            f"({type(exc).__name__})"
        )
        return failures
    failures.extend(check_text(text, relative))
    return failures


def check_text(text: str, label: str) -> list[str]:
    failures: list[str] = []
    for pattern_label, pattern in (*SECRET_PATTERNS, *PRIVATE_PATH_PATTERNS, *CONNECTION_PATTERNS):
        for line_no, line in _matching_lines(text, pattern):
            failures.append(f"{label}:{line_no}: possible {pattern_label}")
    if Path(label).suffix.lower() in {".cfg", ".ini"}:
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = CONFIG_SECRET_ASSIGNMENT.match(line)
            if match is None or not _is_unquoted_secret_value(match.group("value")):
                continue
            failures.append(f"{label}:{line_no}: possible generic secret assignment")
    return failures


def _is_unquoted_secret_value(raw_value: str) -> bool:
    value = re.sub(r"\s+[;#].*$", "", raw_value).strip()
    if not value or value.startswith(("'", '"')) or len(value) < 12:
        return False
    normalized = value.casefold()
    return not normalized.startswith(SECRET_PLACEHOLDER_PREFIXES)


def _git_candidate_paths(root: Path) -> list[Path] | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            check=True,
        )
    except Exception:
        return None
    raw_items = completed.stdout.split(b"\0")
    return [Path(item.decode("utf-8", errors="surrogateescape")) for item in raw_items if item]


def _is_excluded(relative: Path) -> bool:
    normalized = relative.as_posix()
    if normalized == ".coverage" or normalized.startswith(".coverage."):
        return True
    if len(relative.parts) >= 2 and relative.parts[0].casefold() == "web":
        runtime_root = relative.parts[1].casefold()
        if runtime_root.startswith((".pytest_tmp", ".codex_tmp")):
            return True
    if any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in EXCLUDED_PREFIXES):
        return True
    return any(part in EXCLUDED_PARTS for part in relative.parts)


def _matching_lines(text: str, pattern: re.Pattern[str]) -> Iterable[tuple[int, str]]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line) or pattern.search(line.replace("\\\\", "\\")):
            yield line_no, line


if __name__ == "__main__":
    raise SystemExit(main())
