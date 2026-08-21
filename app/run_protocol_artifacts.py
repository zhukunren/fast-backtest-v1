"""Build and persist the completed run protocol artifact set."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest.artifacts import ArtifactSource, artifact_source_from_output_path

from .run_protocol.artifact_manifest import _write_json, write_artifact_manifest
from .run_protocol.compiled_plan import build_compiled_plan
from .run_protocol.experiment_record import (
    build_robustness_report,
    record_run_experiment,
)
from .run_protocol.research_audit import build_research_audit
from .runtime import RuntimeContext


RUN_PROTOCOL_SCHEMA_VERSION = 2


def write_completed_run_protocol_artifacts(
    ctx: RuntimeContext,
    *,
    meta: dict[str, Any],
    result: object | None,
) -> dict[str, ArtifactSource]:
    run_meta_dir = Path(ctx.run_meta_dir)
    output_paths = {
        "compiled_plan": run_meta_dir / f"{ctx.run_id}_compiled_plan.json",
        "audit_research": run_meta_dir / f"{ctx.run_id}_audit_research.json",
        "robustness": run_meta_dir / f"{ctx.run_id}_robustness.json",
        "artifact_manifest": run_meta_dir / f"{ctx.run_id}_artifact_manifest.json",
    }

    compiled_plan = build_compiled_plan(ctx, meta)
    audit_report = build_research_audit(ctx, meta, result)
    robustness_report = build_robustness_report(ctx, result)

    _write_json(output_paths["compiled_plan"], compiled_plan)
    _write_json(output_paths["audit_research"], audit_report)
    _write_json(output_paths["robustness"], robustness_report)

    experiment_result = record_run_experiment(
        ctx,
        result,
        audit_report,
        compiled_plan=compiled_plan,
        robustness_report=robustness_report,
    )
    if isinstance(experiment_result, dict):
        experiments_jsonl = experiment_result.get("experiments_jsonl")
        best_candidates_json = experiment_result.get("best_candidates_json")
        if experiments_jsonl:
            output_paths["experiments_jsonl"] = Path(str(experiments_jsonl))
        if best_candidates_json:
            output_paths["best_candidates"] = Path(str(best_candidates_json))

    catalog_keys = {
        "compiled_plan": "compiled_plan",
        "audit_research": "research_audit",
        "robustness": "robustness_report",
        "artifact_manifest": "artifact_manifest",
        "experiments_jsonl": "experiments",
        "best_candidates": "experiments",
    }
    sources = {
        name: artifact_source_from_output_path(
            ctx.cfg,
            path,
            catalog_key=catalog_keys[name],
        )
        for name, path in output_paths.items()
    }

    artifacts = meta.setdefault("artifacts", {})
    artifacts["compiled_plan_path"] = str(sources["compiled_plan"].path)
    artifacts["audit_research_path"] = str(sources["audit_research"].path)
    artifacts["robustness_path"] = str(sources["robustness"].path)
    artifacts["artifact_manifest_path"] = str(sources["artifact_manifest"].path)
    if "experiments_jsonl" in sources:
        artifacts["experiments_jsonl"] = str(sources["experiments_jsonl"].path)
    if "best_candidates" in sources:
        artifacts["best_candidates_json"] = str(sources["best_candidates"].path)

    meta["audit"] = {
        "status": audit_report.get("status"),
        "issue_count": audit_report.get("issue_count"),
        "warning_count": audit_report.get("warning_count"),
        "error_count": audit_report.get("error_count"),
        "sections": {
            str(item.get("name")): {
                "status": item.get("status"),
                "issue_count": item.get("issue_count"),
                "warning_count": item.get("warning_count"),
                "error_count": item.get("error_count"),
            }
            for item in audit_report.get("section_summary", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        },
    }
    meta["robustness"] = {
        "status": robustness_report.get("status"),
        "multi_start_enabled": robustness_report.get("multi_start_enabled"),
        "robust_score": (
            robustness_report.get("multi_start", {}).get("robust_score")
            if isinstance(robustness_report.get("multi_start"), dict)
            else None
        ),
    }
    if isinstance(experiment_result, dict):
        meta["experiment_registry"] = {
            "ok": bool(experiment_result.get("ok")),
            "experiments_jsonl": experiment_result.get("experiments_jsonl", ""),
            "best_candidates_json": experiment_result.get(
                "best_candidates_json",
                "",
            ),
            "error": experiment_result.get("error", ""),
        }
    return sources


__all__ = [
    "RUN_PROTOCOL_SCHEMA_VERSION",
    "build_compiled_plan",
    "build_research_audit",
    "build_robustness_report",
    "write_artifact_manifest",
    "write_completed_run_protocol_artifacts",
]
