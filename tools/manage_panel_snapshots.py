"""Freeze, inspect, and atomically activate immutable panel snapshots."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.panel_snapshot import (  # noqa: E402
    PanelSnapshot,
    PanelSnapshotError,
    activate_panel_snapshot,
    publish_panel_snapshot,
    resolve_panel_snapshot,
)


def _snapshot_payload(snapshot: PanelSnapshot) -> dict[str, object]:
    return {
        "ok": True,
        "dataset_id": snapshot.dataset_id,
        "snapshot_root": str(snapshot.root),
        "version_dir": str(snapshot.version_dir),
        "panel_path": str(snapshot.panel_path),
        "manifest_path": str(snapshot.manifest_path),
        "content_sha256": str(snapshot.ready.get("content_sha256") or ""),
        "canonical_manifest_sha256": str(snapshot.ready.get("canonical_manifest_sha256") or ""),
        "content_verified": snapshot.content_verified,
    }


def _default_manifest(panel_path: Path) -> Path:
    if panel_path.suffix.casefold() == ".parquet":
        return panel_path.with_suffix(".manifest.json")
    return panel_path.with_name(f"{panel_path.name}.manifest.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze", help="Hash and publish one immutable panel version.")
    freeze.add_argument("--panel", required=True)
    freeze.add_argument("--manifest", default="")
    freeze.add_argument("--snapshot-root", required=True)
    freeze.add_argument("--no-activate", action="store_true")

    inspect = subparsers.add_parser("inspect", help="Validate current or one explicit version.")
    inspect.add_argument("--snapshot-root", required=True)
    inspect.add_argument("--dataset-id", default="")
    inspect.add_argument("--verify-content", action="store_true")

    activate = subparsers.add_parser("activate", help="Fully verify and atomically switch current.")
    activate.add_argument("--snapshot-root", required=True)
    activate.add_argument("--dataset-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            panel_path = Path(args.panel)
            manifest_path = Path(args.manifest) if args.manifest else _default_manifest(panel_path)
            snapshot = publish_panel_snapshot(
                panel_path,
                manifest_path,
                Path(args.snapshot_root),
                activate=not bool(args.no_activate),
            )
        elif args.command == "activate":
            snapshot = activate_panel_snapshot(Path(args.snapshot_root), args.dataset_id)
        else:
            snapshot = resolve_panel_snapshot(
                Path(args.snapshot_root),
                args.dataset_id or None,
                verify_content=bool(args.verify_content),
            )
        print(json.dumps(_snapshot_payload(snapshot), ensure_ascii=True, sort_keys=True))
        return 0
    except PanelSnapshotError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
