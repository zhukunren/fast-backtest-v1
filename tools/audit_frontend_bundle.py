from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST_ROOT = ROOT / "web" / "frontend" / "dist"
DEFAULT_MAX_JS_BYTES = 500_000


def audit_frontend_bundle(
    dist_root: Path,
    *,
    max_js_bytes: int = DEFAULT_MAX_JS_BYTES,
) -> dict[str, Any]:
    root = Path(dist_root)
    manifest_path = root / ".vite" / "manifest.json"
    violations: list[dict[str, Any]] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        violations.append(
            {
                "kind": "frontend_manifest_invalid",
                "manifest_path": str(manifest_path),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    referenced = sorted(
        {
            str(item.get("file") or "")
            for item in manifest.values()
            if isinstance(item, dict) and str(item.get("file") or "").endswith(".js")
        },
        key=str.casefold,
    )
    records: list[dict[str, Any]] = []
    for relative in referenced:
        path = root / relative
        if not path.is_file():
            violations.append(
                {
                    "kind": "frontend_manifest_asset_missing",
                    "name": relative,
                    "manifest_path": str(manifest_path),
                }
            )
            continue
        records.append({"name": relative, "size_bytes": path.stat().st_size})
    violations.extend(
        {
            "kind": "javascript_chunk_too_large",
            "name": record["name"],
            "size_bytes": record["size_bytes"],
            "max_js_bytes": int(max_js_bytes),
        }
        for record in records
        if int(record["size_bytes"]) > int(max_js_bytes)
    )
    if not referenced and not violations:
        violations.append({"kind": "frontend_bundle_missing", "dist_root": str(root)})
    return {
        "ok": not violations,
        "dist_root": str(root),
        "manifest_path": str(manifest_path),
        "max_js_bytes": int(max_js_bytes),
        "chunk_count": len(records),
        "largest_chunk_bytes": max((int(record["size_bytes"]) for record in records), default=0),
        "chunks": records,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject oversized production JavaScript chunks.")
    parser.add_argument("--dist-root", type=Path, default=DEFAULT_DIST_ROOT)
    parser.add_argument("--max-js-bytes", type=int, default=DEFAULT_MAX_JS_BYTES)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = audit_frontend_bundle(args.dist_root, max_js_bytes=args.max_js_bytes)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if payload["ok"]:
        print(
            f"[OK] frontend bundle: chunks={payload['chunk_count']} "
            f"largest={payload['largest_chunk_bytes']} max={payload['max_js_bytes']}"
        )
        return 0
    for violation in payload["violations"]:
        print(
            f"[FAIL] {violation['kind']}: "
            f"{violation.get('name') or violation.get('dist_root') or violation.get('manifest_path')}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
