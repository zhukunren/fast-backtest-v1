from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def frontend_manifest_path(frontend_dist_root: Path) -> Path:
    return frontend_dist_root / ".vite" / "manifest.json"


def resolve_vue_entry(
    *,
    frontend_dist_root: Path,
    dev_server: str,
    asset_url: Callable[[str], str],
) -> dict[str, object] | None:
    if dev_server:
        return {
            "mode": "dev",
            "css_urls": [],
            "favicon_url": f"{dev_server}/favicon.svg",
            "js_url": f"{dev_server}/src/main.ts",
        }

    manifest_path = frontend_manifest_path(frontend_dist_root)
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(manifest, dict) or not manifest:
        return None

    entry = manifest.get("index.html")
    if not isinstance(entry, dict):
        return None

    js_file = entry.get("file")
    if not isinstance(js_file, str) or not js_file.strip():
        return None
    css_urls = [
        asset_url(css_name)
        for css_name in entry.get("css", []) or []
        if isinstance(css_name, str) and css_name.strip()
    ]
    return {
        "mode": "prod",
        "css_urls": css_urls,
        "favicon_url": asset_url("favicon.svg"),
        "js_url": asset_url(js_file),
    }


__all__ = ["frontend_manifest_path", "resolve_vue_entry"]
