"""Inspect and atomically activate immutable V1 artifact generations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.artifacts import (
    activate_generation,
    list_ready_generations,
    resolve_current_generation,
    resolve_generation,
)
from backtest.artifacts.generation_pruning import prune_artifact_generations
from tools.inspect_run_artifacts import inspect_artifact_generation


def _generation_summary(output_root: Path, generation_id: str) -> dict[str, Any]:
    snapshot = resolve_generation(output_root, generation_id)
    summary = snapshot.manifest.get("summary")
    return {
        "generation_id": generation_id,
        "generation_dir": str(snapshot.generation_dir),
        "summary": dict(summary) if isinstance(summary, dict) else {},
    }


def _list_payload(output_root: Path) -> dict[str, Any]:
    generation_ids = list_ready_generations(output_root)
    try:
        current_id = resolve_current_generation(output_root).generation_id
    except Exception:
        current_id = ""
    return {
        "ok": True,
        "output_root": str(output_root),
        "current_generation_id": current_id,
        "generations": [_generation_summary(output_root, generation_id) for generation_id in generation_ids],
    }


def _write_payload(payload: dict[str, Any], json_out: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(text + "\n", encoding="utf-8")
    print(text)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage immutable V1 artifact generations.")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--json-out", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List READY generations and the current pointer.")
    inspect_parser = commands.add_parser("inspect", help="Verify current or one named generation.")
    inspect_parser.add_argument("generation_id", nargs="?", default="current")
    activate_parser = commands.add_parser("activate", help="Atomically point current at a READY generation.")
    activate_parser.add_argument("generation_id")
    prune_parser = commands.add_parser(
        "prune",
        help="Plan protected generation pruning; no files are changed without --apply.",
    )
    prune_parser.add_argument("--keep-last", type=int, default=3)
    prune_parser.add_argument("--pin", dest="pinned_generation_ids", action="append", default=[])
    prune_parser.add_argument("--stale-staging-hours", type=float, default=24.0)
    prune_parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_root = Path(args.output_root)
    try:
        if args.command == "list":
            payload = _list_payload(output_root)
        elif args.command == "inspect":
            generation_id = str(args.generation_id or "").strip()
            payload = inspect_artifact_generation(
                output_root,
                None if generation_id.casefold() in {"", "current"} else generation_id,
            )
        elif args.command == "activate":
            snapshot = activate_generation(output_root, str(args.generation_id))
            payload = {
                "ok": True,
                "activated_generation_id": snapshot.generation_id,
                "current_pointer_verified": resolve_current_generation(output_root).generation_id
                == snapshot.generation_id,
            }
        else:
            payload = prune_artifact_generations(
                output_root,
                keep_last=args.keep_last,
                pinned_generation_ids=tuple(args.pinned_generation_ids),
                stale_staging_seconds=max(0.0, args.stale_staging_hours * 3600.0),
                apply=bool(args.apply),
            )
            payload["ok"] = True
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _write_payload(payload, args.json_out)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
