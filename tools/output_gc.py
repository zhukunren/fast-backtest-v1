"""Manage legacy output migrations and disposable local storage.

Evidence under ``output/paper_experiments`` and ``output/paper_release`` is
deliberately outside the default migration set. This command only handles
reconstructible caches and disposable workspaces, and is dry-run by default.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.local_artifact_paths import (  # noqa: E402
    cache_root,
    codex_worker_cache_root,
    comparison_workspace_root,
    document_tool_cache_dir,
    formal_worktree_root,
    log_root,
    model_cache_dir,
    operational_log_dir,
    performance_workspace_root,
    public_repo_workspace_root,
    temp_root,
)
from tools.output_gc_migration import (  # noqa: E402
    OUTPUT_ROOT,
    apply_migration,
    plan_migration,
)
from tools.output_gc_logs import apply_log_cleanup, plan_log_cleanup  # noqa: E402
from tools.output_gc_pruning import prune_temporary_storage  # noqa: E402


def paths_payload() -> dict[str, Any]:
    model_revision = "2e1fd397ee46e1388853d2af2c993145b0f1098a"
    return {
        "repository_root": str(ROOT.resolve()),
        "output_root": str(OUTPUT_ROOT.resolve()),
        "cache_root": str(cache_root()),
        "temp_root": str(temp_root()),
        "log_root": str(log_root()),
        "operational_logs": {
            "web": str(operational_log_dir("web")),
            "backtest": str(operational_log_dir("backtest")),
        },
        "model_cache": str(model_cache_dir("Qwen2.5-Coder-1.5B-Instruct", model_revision)),
        "document_tools": {
            "libreoffice": str(document_tool_cache_dir("libreoffice", "26.2.5.2")),
            "poppler": str(document_tool_cache_dir("poppler", "26.02.0")),
            "python_packages": str(
                document_tool_cache_dir("python-packages", f"cp{sys.version_info.major}{sys.version_info.minor}")
            ),
        },
        "codex_worker": str(codex_worker_cache_root()),
        "temporary_workspaces": {
            "formal_worktrees": str(formal_worktree_root()),
            "public_repos": str(public_repo_workspace_root()),
            "comparisons": str(comparison_workspace_root()),
            "perf_smoke": str(performance_workspace_root()),
        },
    }


def _emit(payload: Any, json_out: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(text + "\n", encoding="utf-8")
    print(text)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    paths = commands.add_parser("paths", help="Print resolved cache and temporary roots.")
    paths.add_argument("--json-out", type=Path)
    migrate = commands.add_parser("migrate", help="Plan legacy output migrations; default is dry-run.")
    migrate.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    migrate.add_argument("--older-than-hours", type=float, default=24.0)
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--json-out", type=Path)
    prune = commands.add_parser("prune", help="Prune stale temporary/cache files; default is dry-run.")
    prune.add_argument("--older-than-hours", type=float, default=7 * 24.0)
    prune.add_argument("--apply", action="store_true")
    prune.add_argument("--json-out", type=Path)
    logs = commands.add_parser(
        "logs",
        help="Consolidate scattered operational logs and prune expired files.",
    )
    logs.add_argument("--retention-days", type=float, default=30.0)
    logs.add_argument("--apply", action="store_true")
    logs.add_argument("--json-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "paths":
            _emit(paths_payload(), args.json_out)
        elif args.command == "migrate":
            report = plan_migration(
                args.output_root,
                stale_seconds=max(0.0, float(args.older_than_hours) * 3600.0),
            )
            if args.apply:
                report = apply_migration(report)
            _emit(report, args.json_out)
        elif args.command == "prune":
            _emit(
                prune_temporary_storage(
                    older_than_seconds=max(0.0, float(args.older_than_hours) * 3600.0),
                    apply=bool(args.apply),
                    repository_root=ROOT,
                ),
                args.json_out,
            )
        else:
            report = plan_log_cleanup(
                retention_seconds=max(0.0, float(args.retention_days) * 24 * 3600.0),
            )
            if args.apply:
                report = apply_log_cleanup(report)
            _emit(report, args.json_out)
    except Exception as exc:
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, None)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
