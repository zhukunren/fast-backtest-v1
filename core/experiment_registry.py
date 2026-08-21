from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENT_MEMORY_DIR = ROOT / "output" / "agent_memory"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _memory_paths(memory_root: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(memory_root) if memory_root is not None else DEFAULT_AGENT_MEMORY_DIR
    return root / "experiments.jsonl", root / "best_candidates.json"


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_short_id(*values: Any) -> str:
    raw = "|".join(str(value or "") for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _build_protocol_fields(
    *,
    backtest_result: dict[str, Any],
    validation_result: dict[str, Any],
    request: dict[str, Any],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    summary = _nested_dict(validation_result.get("summary"))
    audit = _nested_dict(protocol.get("audit"))
    robustness = _nested_dict(protocol.get("robustness"))
    hashes = _nested_dict(protocol.get("hashes"))

    audit_status = _first_non_empty(
        protocol.get("audit_status"),
        audit.get("status"),
        summary.get("audit_status"),
    )
    robustness_status = _first_non_empty(
        protocol.get("robustness_status"),
        robustness.get("status"),
        summary.get("robustness_status"),
    )
    decision = _first_non_empty(
        protocol.get("decision"),
        validation_result.get("decision"),
        summary.get("decision"),
    )
    strategy_spec_hash = _first_non_empty(
        protocol.get("strategy_spec_hash"),
        hashes.get("strategy_spec_hash"),
        request.get("strategy_spec_hash"),
        backtest_result.get("strategy_spec_hash"),
    )
    compiled_plan_hash = _first_non_empty(
        protocol.get("compiled_plan_hash"),
        hashes.get("compiled_plan_hash"),
        request.get("compiled_plan_hash"),
        backtest_result.get("compiled_plan_hash"),
    )
    config_hash = _first_non_empty(
        protocol.get("config_hash"),
        hashes.get("config_hash"),
        request.get("config_hash"),
        backtest_result.get("config_hash"),
    )
    data_manifest_hash = _first_non_empty(
        protocol.get("data_manifest_hash"),
        hashes.get("data_manifest_hash"),
        request.get("data_manifest_hash"),
        backtest_result.get("data_manifest_hash"),
    )
    return {
        "strategy_spec_hash": strategy_spec_hash or "",
        "compiled_plan_hash": compiled_plan_hash or "",
        "config_hash": config_hash or "",
        "data_manifest_hash": data_manifest_hash or "",
        "audit_status": str(audit_status or ""),
        "audit_issue_count": int(_first_non_empty(protocol.get("audit_issue_count"), audit.get("issue_count"), summary.get("issue_count"), 0) or 0),
        "robustness_status": str(robustness_status or ""),
        "decision": str(decision or ""),
        "decision_reason": str(_first_non_empty(protocol.get("decision_reason"), summary.get("reason"), backtest_result.get("reason"), "") or ""),
        "audit": audit,
        "robustness": robustness,
    }


def build_experiment_record(
    *,
    backtest_result: dict[str, Any],
    validation_result: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    pack_result: dict[str, Any] | None = None,
    protocol: dict[str, Any] | None = None,
    next_hypothesis: str = "",
) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    request = request if isinstance(request, dict) else {}
    pack_result = pack_result if isinstance(pack_result, dict) else {}
    protocol = protocol if isinstance(protocol, dict) else {}
    protocol_fields = _build_protocol_fields(
        backtest_result=backtest_result,
        validation_result=validation_result,
        request=request,
        protocol=protocol,
    )
    module = backtest_result.get("module")
    run_id = backtest_result.get("run_id")
    experiment_id = _stable_short_id(
        module,
        run_id,
        protocol_fields["strategy_spec_hash"],
        protocol_fields["compiled_plan_hash"],
    )
    return {
        "experiment_id": experiment_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "module": module,
        "strategy_module": module,
        "run_id": run_id,
        "request": request,
        "status": backtest_result.get("status"),
        "reason": backtest_result.get("reason", ""),
        "strategy_spec_hash": protocol_fields["strategy_spec_hash"],
        "compiled_plan_hash": protocol_fields["compiled_plan_hash"],
        "config_hash": protocol_fields["config_hash"],
        "data_manifest_hash": protocol_fields["data_manifest_hash"],
        "audit_status": protocol_fields["audit_status"],
        "robustness_status": protocol_fields["robustness_status"],
        "decision": protocol_fields["decision"],
        "decision_reason": protocol_fields["decision_reason"],
        "metrics": {
            "final_net_value": backtest_result.get("final_net_value"),
            "max_drawdown": backtest_result.get("max_drawdown"),
            "signal_stock_count": backtest_result.get("signal_stock_count"),
            "duration_sec": backtest_result.get("duration_sec"),
        },
        "validation": {
            "ok": bool(validation_result.get("ok")),
            "decision": validation_result.get("decision"),
            "summary": validation_result.get("summary", {}),
        },
        "protocol": {
            "strategy_spec_hash": protocol_fields["strategy_spec_hash"],
            "compiled_plan_hash": protocol_fields["compiled_plan_hash"],
            "config_hash": protocol_fields["config_hash"],
            "data_manifest_hash": protocol_fields["data_manifest_hash"],
            "audit_status": protocol_fields["audit_status"],
            "audit_issue_count": protocol_fields["audit_issue_count"],
            "robustness_status": protocol_fields["robustness_status"],
            "decision": protocol_fields["decision"],
            "decision_reason": protocol_fields["decision_reason"],
            "audit": protocol_fields["audit"],
            "robustness": protocol_fields["robustness"],
        },
        "artifacts": {
            "pack_run_dir": pack_result.get("run_dir", ""),
            "log_path": backtest_result.get("log_path", ""),
            "run_meta_dir": backtest_result.get("run_meta_dir", ""),
            "validation_json_report": validation_result.get("json_report", ""),
        },
        "next_hypothesis": str(next_hypothesis or ""),
    }


def append_experiment_record(
    record: dict[str, Any],
    *,
    memory_root: str | Path | None = None,
) -> Path:
    experiments_path, _ = _memory_paths(memory_root)
    experiments_path.parent.mkdir(parents=True, exist_ok=True)
    with experiments_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_json_safe(record), ensure_ascii=True, sort_keys=True) + "\n")
    return experiments_path


def update_best_candidates(
    record: dict[str, Any],
    *,
    memory_root: str | Path | None = None,
    top_k: int = 20,
) -> Path:
    _, best_candidates_path = _memory_paths(memory_root)
    best_candidates_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if best_candidates_path.exists():
        existing = json.loads(best_candidates_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []

    filtered = [
        item
        for item in existing
        if not (
            str(item.get("module") or "") == str(record.get("module") or "")
            and str(item.get("run_id") or "") == str(record.get("run_id") or "")
        )
    ]
    filtered.append(record)
    filtered.sort(
        key=lambda item: (
            1 if str((item.get("status") or "")).strip().lower() == "completed" else 0,
            float(((item.get("metrics") or {}).get("final_net_value") or 0.0)),
        ),
        reverse=True,
    )
    trimmed = filtered[: max(1, int(top_k))]
    best_candidates_path.write_text(
        json.dumps(_json_safe(trimmed), ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return best_candidates_path


def record_experiment(
    *,
    backtest_result: dict[str, Any],
    validation_result: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    pack_result: dict[str, Any] | None = None,
    protocol: dict[str, Any] | None = None,
    next_hypothesis: str = "",
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    record = build_experiment_record(
        backtest_result=backtest_result,
        validation_result=validation_result,
        request=request,
        pack_result=pack_result,
        protocol=protocol,
        next_hypothesis=next_hypothesis,
    )
    experiments_path = append_experiment_record(record, memory_root=memory_root)
    best_candidates_path = update_best_candidates(record, memory_root=memory_root)
    return {
        "ok": True,
        "record": record,
        "experiments_jsonl": str(experiments_path),
        "best_candidates_json": str(best_candidates_path),
    }


__all__ = [
    "DEFAULT_AGENT_MEMORY_DIR",
    "append_experiment_record",
    "build_experiment_record",
    "record_experiment",
    "update_best_candidates",
]
