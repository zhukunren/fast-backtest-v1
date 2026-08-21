from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PARTS = {
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
    "node_modules",
    "output",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse Python files without writing .pyc files.")
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    failures = check_paths([Path(path) for path in args.paths])
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    if not args.quiet:
        print("[OK] syntax check")
    return 0


def check_paths(paths: list[Path], *, root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    for path in collect_python_files(paths, root=root):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
        except OSError as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
    return failures


def collect_python_files(paths: list[Path], *, root: Path = ROOT) -> list[Path]:
    files: set[Path] = set()
    for raw in paths:
        path = raw if raw.is_absolute() else root / raw
        if path.is_file() and path.suffix == ".py":
            files.add(path)
        elif path.is_dir():
            files.update(item for item in path.rglob("*.py") if not _is_excluded(item, root=root))
    if paths == [Path(".")]:
        files.update(_git_python_files(root))
    return sorted((path for path in files if path.is_file()), key=lambda path: str(path.relative_to(root)).casefold())


def _git_python_files(root: Path) -> set[Path]:
    if not (root / ".git").exists():
        return set()
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],
            cwd=str(root),
            capture_output=True,
            check=True,
        )
    except Exception:
        return set()
    paths = [Path(item.decode("utf-8", errors="surrogateescape")) for item in completed.stdout.split(b"\0") if item]
    return {root / path for path in paths if not _is_excluded(root / path, root=root)}


def _is_excluded(path: Path, *, root: Path = ROOT) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part in EXCLUDED_PARTS for part in relative.parts)


if __name__ == "__main__":
    raise SystemExit(main())
