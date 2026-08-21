"""Audit a curated V1 full-edition public repository candidate."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5_000_000
TEXT_SUFFIXES = {"", ".cfg", ".csv", ".ini", ".json", ".md", ".py", ".toml", ".txt", ".yml", ".yaml"}

REQUIRED_FILES = (
    "README.md",
    "README_CN.md",
    "LICENSE",
    "requirements.txt",
    "config/config.example.ini",
    "config/config.ini",
    "main.py",
    "web/__main__.py",
    "web/server.py",
    "web/frontend/package.json",
    "web/frontend/package-lock.json",
    "strategy_library/builtin/offline_smoke.py",
    "web/strategy_library/public_offline_smoke/config.ini",
    "web/strategy_library/public_offline_smoke/strategy_description.json",
    "docs/full/public_release.md",
    "tools/export_full_repo.py",
    "tools/smoke_full_public_repo.py",
    "FULL_PUBLIC_RELEASE_MANIFEST.json",
)

FORBIDDEN_PREFIXES = (
    "projects/",
    "output/",
    "research/",
    "docs/paper_submission/",
    "docs/chinese_journal_submission/",
    "tools/paper_experiments/",
    "strategy_library/generated/",
    "strategy_library/custom/",
    "strategy_library/validated/",
    "web/data/",
    "web/user_workspaces/",
    "web/frontend/node_modules/",
    "web/frontend/dist/",
)

PRIVATE_PATH_PATTERNS = (
    re.compile(r"\b[A-Z]:\\Users\\[^\\\s'\"]+"),
    re.compile(r"\b/(?:Users|home)/[A-Za-z0-9._-]+"),
    re.compile(r"(?i)\b(?:mysql|mariadb|postgres|postgresql)://[^:\s/@]+:[^@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
)

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|token|password|passwd|pwd)\b"
    r"\s*[:=]\s*['\"](?P<value>[A-Za-z0-9][A-Za-z0-9_./+=:@-]{11,})['\"]"
)
SECRET_PLACEHOLDERS = (
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
    "secret",
    "password",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a full public repository candidate.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures = audit(root)
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    if not args.quiet:
        print(f"[OK] full public surface: {root}")
    return 0


def audit(root: Path) -> list[str]:
    failures: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            failures.append(f"missing required file: {relative}")

    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: str(path).casefold())
    for path in files:
        relative = path.relative_to(root).as_posix()
        if _is_forbidden(relative):
            failures.append(f"forbidden public path: {relative}")
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            failures.append(f"file exceeds {MAX_FILE_BYTES} bytes: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            failures.extend(_check_text(root, path))

    config = root / "config" / "config.ini"
    if config.is_file():
        for line_no, line in enumerate(config.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if re.match(r"^\s*(?:token|password|passwd|pwd|secret|api[_-]?key)\s*=\s*\S+", line, re.IGNORECASE):
                failures.append(f"config/config.ini:{line_no}: credential must be empty or environment-backed")

    failures.extend(_audit_release_manifest(root))
    return failures


def _audit_release_manifest(root: Path) -> list[str]:
    path = root / "FULL_PUBLIC_RELEASE_MANIFEST.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"FULL_PUBLIC_RELEASE_MANIFEST.json is invalid: {exc}"]
    if not isinstance(payload, dict):
        return ["FULL_PUBLIC_RELEASE_MANIFEST.json must be an object"]
    failures: list[str] = []
    dirty = payload.get("source_worktree_dirty")
    untracked = payload.get("untracked_release_inputs")
    tracked_only = payload.get("generated_from_tracked_files")
    if not isinstance(dirty, bool):
        failures.append("FULL_PUBLIC_RELEASE_MANIFEST.json has invalid source_worktree_dirty")
    if not isinstance(untracked, list) or not all(isinstance(item, str) for item in untracked):
        failures.append("FULL_PUBLIC_RELEASE_MANIFEST.json has invalid untracked_release_inputs")
    if not isinstance(tracked_only, bool):
        failures.append("FULL_PUBLIC_RELEASE_MANIFEST.json has invalid generated_from_tracked_files")
    elif isinstance(untracked, list) and tracked_only is bool(not untracked):
        pass
    elif isinstance(untracked, list):
        failures.append("FULL_PUBLIC_RELEASE_MANIFEST.json has inconsistent tracked-file provenance")
    return failures


def _is_forbidden(relative: str) -> bool:
    if relative == "libreoffice1.cab" or relative == "New-upload-Zenodo.png":
        return True
    if relative == "config/config.local.ini":
        return True
    if relative.startswith("web/strategy_library/"):
        allowed = {
            "web/strategy_library/public_offline_smoke/config.ini",
            "web/strategy_library/public_offline_smoke/strategy_description.json",
        }
        return relative not in allowed
    if relative in {
        "strategy_library/custom/__init__.py",
        "strategy_library/generated/__init__.py",
        "strategy_library/validated/__init__.py",
    }:
        return False
    return any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def _check_text(root: Path, path: Path) -> list[str]:
    relative = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    failures: list[str] = []
    for pattern in PRIVATE_PATH_PATTERNS:
        for line_no, line in _matching_lines(text, pattern):
            failures.append(f"{relative}:{line_no}: possible private value")
    for line_no, line in _matching_lines(text, SECRET_ASSIGNMENT):
        match = SECRET_ASSIGNMENT.search(line)
        value = match.group("value").casefold() if match else ""
        if value and not value.startswith(SECRET_PLACEHOLDERS):
            failures.append(f"{relative}:{line_no}: possible credential assignment")
    return failures


def _matching_lines(text: str, pattern: re.Pattern[str]):
    for line_no, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line) or pattern.search(line.replace("\\\\", "\\")):
            yield line_no, line


if __name__ == "__main__":
    raise SystemExit(main())
