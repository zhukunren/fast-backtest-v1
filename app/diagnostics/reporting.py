from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..runtime import RuntimeContext
from .snapshot import build_diagnostic_snapshot


def diagnostic_output_path(ctx: RuntimeContext) -> Path:
    return ctx.run_meta_dir / f"{ctx.run_id}_diagnostics.json"


def write_diagnostic_report(
    *,
    ctx: RuntimeContext,
    report: dict[str, Any],
    ensure_meta: Callable[[], dict[str, Any]] | None = None,
) -> Path:
    out_path = diagnostic_output_path(ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.diagnostic_report = report
    ctx.diagnostic_path = out_path

    meta = ctx.meta or (ensure_meta() if ensure_meta is not None else {})
    artifacts = meta.setdefault("artifacts", {})
    artifacts["diagnostics_path"] = str(out_path)
    meta["diagnostics"] = build_diagnostic_snapshot(report)
    ctx.meta = meta
    return out_path


__all__ = [
    "diagnostic_output_path",
    "write_diagnostic_report",
]
