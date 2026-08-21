"""Build robustness evidence and record completed experiments."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.result import CurvePayload, RunResult
from core.artifact_schema import (
    ROBUSTNESS_REPORT_SCHEMA_VERSION,
    artifact_schema_fields,
)
from core.experiment_registry import record_experiment

from ..runtime import RuntimeContext
from .compiled_plan import _stable_hash


def _run_result_metrics(result: object | None) -> dict[str, object | None]:
    if not isinstance(result, RunResult) or not isinstance(result.payload, CurvePayload):
        return {
            "final_net_value": None,
            "max_drawdown": None,
            "signal_stock_count": None,
            "actual_segment_trade_count": None,
        }
    metrics = result.payload.metrics
    return {
        "final_net_value": metrics.final_net_value if metrics is not None else None,
        "max_drawdown": metrics.max_drawdown if metrics is not None else None,
        "signal_stock_count": result.request.signal_stock_count,
        "actual_segment_trade_count": (
            metrics.actual_segment_trade_count if metrics is not None else None
        ),
    }


def build_robustness_report(
    ctx: RuntimeContext,
    result: object | None,
) -> dict[str, Any]:
    diagnostic_report = (
        ctx.diagnostic_report if isinstance(ctx.diagnostic_report, dict) else {}
    )
    multi_start = (
        diagnostic_report.get("multi_start")
        if isinstance(diagnostic_report, dict)
        else None
    )
    diagnostics_cfg = getattr(ctx.cfg, "diagnostics", None)
    enabled = bool(getattr(diagnostics_cfg, "multi_start_enabled", False))
    status = "not_run"
    if isinstance(multi_start, dict):
        status = "pass"
        robust_score = multi_start.get("robust_score")
        floor = float(
            getattr(diagnostics_cfg, "min_multi_start_robust_score", -1e9)
        )
        try:
            if robust_score is not None and float(robust_score) < floor:
                status = "warn"
        except (TypeError, ValueError):
            status = "warn"
    elif enabled:
        status = "missing"

    result_metrics = _run_result_metrics(result)
    return {
        **artifact_schema_fields(
            "robustness_report",
            ROBUSTNESS_REPORT_SCHEMA_VERSION,
        ),
        "run_id": ctx.run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "multi_start_enabled": enabled,
        "multi_start": multi_start if isinstance(multi_start, dict) else None,
        "result": result_metrics,
        "config": {
            "multi_start_step_days": getattr(
                diagnostics_cfg,
                "multi_start_step_days",
                None,
            ),
            "multi_start_window_days": getattr(
                diagnostics_cfg,
                "multi_start_window_days",
                None,
            ),
            "multi_start_max_starts": getattr(
                diagnostics_cfg,
                "multi_start_max_starts",
                None,
            ),
            "robust_lambda": getattr(diagnostics_cfg, "robust_lambda", None),
            "robust_gamma": getattr(diagnostics_cfg, "robust_gamma", None),
            "min_multi_start_robust_score": getattr(
                diagnostics_cfg,
                "min_multi_start_robust_score",
                None,
            ),
        },
    }


def _result_payload(ctx: RuntimeContext, result: object | None) -> dict[str, Any]:
    extra = (ctx.meta or {}).get("extra") if isinstance(ctx.meta, dict) else {}
    metrics = _run_result_metrics(result)
    return {
        "ok": result is not None,
        "module": getattr(getattr(ctx.cfg, "strategy", None), "module", ""),
        "run_id": ctx.run_id,
        "status": (
            str(extra.get("status") if isinstance(extra, dict) else "completed")
            or "completed"
        ),
        "reason": str(extra.get("reason") if isinstance(extra, dict) else ""),
        "final_net_value": metrics["final_net_value"],
        "max_drawdown": metrics["max_drawdown"],
        "signal_stock_count": metrics["signal_stock_count"],
        "duration_sec": getattr(result, "duration_sec", None),
        "log_path": str(ctx.tag_log_path),
        "run_meta_dir": str(ctx.run_meta_dir),
    }


def _decision_from_protocol(*, audit_status: str, robustness_status: str) -> str:
    audit_status = str(audit_status or "").casefold()
    robustness_status = str(robustness_status or "").casefold()
    if audit_status == "fail":
        return "reject"
    if audit_status == "warn" or robustness_status in {"warn", "missing"}:
        return "watch"
    return "promote_candidate"


def _data_manifest_hash(compiled_plan: dict[str, Any] | None) -> str:
    if not isinstance(compiled_plan, dict):
        return ""
    data = compiled_plan.get("data")
    if not isinstance(data, dict):
        return ""
    manifest = data.get("data_manifest")
    if not manifest:
        return ""
    return _stable_hash(manifest)


def _experiment_protocol_payload(
    *,
    compiled_plan: dict[str, Any] | None,
    audit_report: dict[str, Any],
    robustness_report: dict[str, Any] | None,
) -> dict[str, Any]:
    audit_status = str(audit_report.get("status") or "")
    robustness_status = str((robustness_report or {}).get("status") or "")
    return {
        "strategy_spec_hash": (compiled_plan or {}).get("strategy_spec_hash", ""),
        "compiled_plan_hash": (compiled_plan or {}).get("compiled_plan_hash", ""),
        "config_hash": (compiled_plan or {}).get("config_hash", ""),
        "data_manifest_hash": _data_manifest_hash(compiled_plan),
        "audit_status": audit_status,
        "audit_issue_count": int(audit_report.get("issue_count") or 0),
        "robustness_status": robustness_status,
        "decision": _decision_from_protocol(
            audit_status=audit_status,
            robustness_status=robustness_status,
        ),
        "decision_reason": (
            f"audit={audit_status or 'unknown'}; "
            f"robustness={robustness_status or 'unknown'}"
        ),
        "audit": {
            "status": audit_report.get("status"),
            "issue_count": audit_report.get("issue_count"),
            "warning_count": audit_report.get("warning_count"),
            "error_count": audit_report.get("error_count"),
            "sections": audit_report.get("section_summary", []),
        },
        "robustness": {
            "status": (robustness_report or {}).get("status"),
            "multi_start_enabled": (robustness_report or {}).get(
                "multi_start_enabled"
            ),
            "robust_score": (
                (robustness_report or {}).get("multi_start", {}).get("robust_score")
                if isinstance((robustness_report or {}).get("multi_start"), dict)
                else None
            ),
        },
    }


def record_run_experiment(
    ctx: RuntimeContext,
    result: object | None,
    audit_report: dict[str, Any],
    *,
    compiled_plan: dict[str, Any] | None = None,
    robustness_report: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    status = str(audit_report.get("status") or "").casefold()
    protocol = _experiment_protocol_payload(
        compiled_plan=compiled_plan,
        audit_report=audit_report,
        robustness_report=robustness_report,
    )
    validation_result = {
        "ok": status != "fail",
        "decision": protocol["decision"],
        "summary": {
            "audit_status": audit_report.get("status"),
            "robustness_status": protocol["robustness_status"],
            "decision": protocol["decision"],
            "reason": protocol["decision_reason"],
            "issue_count": audit_report.get("issue_count"),
            "warning_count": audit_report.get("warning_count"),
            "error_count": audit_report.get("error_count"),
        },
    }
    try:
        return record_experiment(
            backtest_result=_result_payload(ctx, result),
            validation_result=validation_result,
            request={
                "command": "backtest_session",
                "module": getattr(getattr(ctx.cfg, "strategy", None), "module", ""),
                "start_time": getattr(
                    getattr(ctx.cfg, "backtest", None),
                    "start_time",
                    "",
                ),
                "end_time": getattr(
                    getattr(ctx.cfg, "backtest", None),
                    "end_time",
                    "",
                ),
            },
            pack_result={"run_dir": str(ctx.output_root)},
            protocol=protocol,
            memory_root=Path(ctx.output_root) / "experiments",
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["build_robustness_report", "record_run_experiment"]
