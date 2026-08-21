"""用途:
- 检查当前分支或显式轨道允许提交的路径范围，防止 V2、V3 与 integration 研发线互相串改。
- 既可手工执行，也可给 Git hook 调用。

用法:
- 校验当前 staged 变更：`python tools/git_path_guard.py --track v2`
- 校验指定路径：`python tools/git_path_guard.py --track integration --paths projects/engine_v3/src/foo.py`"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


TRACK_ALLOWED_PREFIXES: dict[str, tuple[str, ...]] = {
    "v2": ("projects/engine_v2/",),
    "v3": ("projects/engine_v3/",),
    "integration": (),
}

TRACK_BRANCH_PREFIXES: dict[str, tuple[str, ...]] = {
    "v2": ("track/v2", "v2/", "engine-v2/", "engine_v2/"),
    "v3": ("track/v3", "v3/", "engine-v3/", "engine_v3/"),
    "integration": ("integration/", "track/integration"),
}


@dataclass(frozen=True, slots=True)
class GuardResult:
    track: str | None
    allowed: bool
    invalid_paths: tuple[str, ...]
    message: str


def normalize_repo_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def classify_track(branch_name: str, explicit_track: str | None = None) -> str | None:
    if explicit_track is not None:
        track = str(explicit_track or "").strip().lower()
        if track not in TRACK_ALLOWED_PREFIXES:
            raise ValueError(f"unsupported track: {explicit_track}")
        return track
    branch = str(branch_name or "").strip().lower()
    if not branch:
        return None
    for track, prefixes in TRACK_BRANCH_PREFIXES.items():
        if any(branch.startswith(prefix) for prefix in prefixes):
            return track
    return None


def validate_paths(track: str | None, paths: Iterable[str]) -> GuardResult:
    normalized_paths = tuple(path for path in (normalize_repo_path(item) for item in paths) if path)
    if track is None:
        return GuardResult(
            track=None,
            allowed=True,
            invalid_paths=(),
            message="branch is unclassified; staged-path guard skipped",
        )
    if track == "integration":
        return GuardResult(
            track=track,
            allowed=True,
            invalid_paths=(),
            message="integration track allows mixed staged paths",
        )
    allowed_prefixes = TRACK_ALLOWED_PREFIXES[track]
    invalid_paths = tuple(path for path in normalized_paths if not path.startswith(allowed_prefixes))
    if invalid_paths:
        return GuardResult(
            track=track,
            allowed=False,
            invalid_paths=invalid_paths,
            message=f"{track} track only allows staged paths under {', '.join(allowed_prefixes)}",
        )
    return GuardResult(
        track=track,
        allowed=True,
        invalid_paths=(),
        message=f"{track} track staged paths are within allowed scope",
    )


def _git_output(args: Sequence[str], *, repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def get_repo_root(start: Path | None = None) -> Path:
    if start is not None:
        return Path(start).resolve()
    return Path(__file__).resolve().parents[1]


def get_current_branch(repo_root: Path) -> str:
    return _git_output(["branch", "--show-current"], repo_root=repo_root)


def get_staged_paths(repo_root: Path) -> tuple[str, ...]:
    output = _git_output(["diff", "--cached", "--name-only", "--diff-filter=ACMRD"], repo_root=repo_root)
    if not output:
        return ()
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验当前 staged 路径是否符合 V2 / V3 / integration 轨道隔离规则。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/git_path_guard.py --track v2\n"
            "  python tools/git_path_guard.py --track integration --paths projects/engine_v3/src/foo.py"
        ),
    )
    parser.add_argument("--branch", default="", help="可选：手工覆盖当前分支名。")
    parser.add_argument("--track", default="", help="可选：显式指定轨道，支持 `v2` / `v3` / `integration`。")
    parser.add_argument("--repo-root", default="", help="可选：覆盖仓库根目录。")
    parser.add_argument("--paths", nargs="*", default=None, help="要校验的路径列表；默认读取 staged paths。")
    parser.add_argument("--hook", action="store_true", help="hook 模式：只打印简洁错误信息。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    repo_root = get_repo_root(Path(args.repo_root) if str(args.repo_root).strip() else None)
    branch_name = str(args.branch or "").strip() or get_current_branch(repo_root)
    explicit_track = str(args.track or "").strip() or None
    track = classify_track(branch_name, explicit_track=explicit_track)
    paths = tuple(args.paths) if args.paths is not None else get_staged_paths(repo_root)
    result = validate_paths(track, paths)

    if result.allowed:
        if not args.hook:
            print(result.message)
        return 0

    sys.stderr.write(f"[git_path_guard] blocked: {result.message}\n")
    for path in result.invalid_paths:
        sys.stderr.write(f"  - {path}\n")
    sys.stderr.write(
        "[git_path_guard] switch to an integration branch/worktree if this change intentionally crosses V2/V3 boundaries.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
