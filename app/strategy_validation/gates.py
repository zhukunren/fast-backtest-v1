from __future__ import annotations

from typing import Any, Mapping

from .common import get_nested_value, to_float
from .models import StrategyValidationGate, StrategyValidationProtocol


def _threshold_passes(*, actual: Any, op: str, expected: float) -> tuple[bool, str]:
    actual_num = to_float(actual)
    if actual_num is None:
        return False, "value_unavailable"
    if op == ">=":
        return actual_num >= expected, ""
    if op == ">":
        return actual_num > expected, ""
    if op == "<=":
        return actual_num <= expected, ""
    if op == "<":
        return actual_num < expected, ""
    if op == "==":
        return actual_num == expected, ""
    if op == "!=":
        return actual_num != expected, ""
    return False, f"unsupported_op:{op}"


def _relative_drop(base_value: Any, actual_value: Any) -> tuple[float | None, str]:
    base_num = to_float(base_value)
    actual_num = to_float(actual_value)
    if base_num is None or actual_num is None:
        return None, "value_unavailable"
    if base_num == 0:
        if actual_num == 0:
            return 0.0, ""
        return None, "base_value_zero"
    drop = max(0.0, (base_num - actual_num) / abs(base_num))
    return float(drop), ""


def _case_items_for_gate(
    case_results: Mapping[str, dict[str, Any]],
    pattern: str,
) -> list[tuple[str, dict[str, Any]]]:
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return [
            (name, payload)
            for name, payload in sorted(case_results.items(), key=lambda item: str(item[0]).casefold())
            if str(name).startswith(prefix)
        ]
    payload = case_results.get(pattern)
    return [(pattern, payload)] if isinstance(payload, dict) else []


def _group_threshold_passes(values: list[float], *, op: str, expected: float) -> tuple[bool, float | None, str]:
    if not values:
        return False, None, "no_matching_cases"
    if op in {">=", ">"}:
        actual = min(values)
    elif op in {"<=", "<"}:
        actual = max(values)
    elif op == "==":
        actual = values[0]
    elif op == "!=":
        actual = values[0]
    else:
        return False, None, f"unsupported_op:{op}"
    passed, error = _threshold_passes(actual=actual, op=op, expected=expected)
    return passed, actual, error


def evaluate_gates(
    case_results: Mapping[str, dict[str, Any]],
    gates: tuple[StrategyValidationGate, ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for gate in gates:
        case_payload = case_results.get(gate.case) or {}
        actual_value = get_nested_value(case_payload, gate.path)
        record: dict[str, Any] = {
            "name": gate.name,
            "kind": gate.kind,
            "level": gate.level,
            "description": gate.description,
            "case": gate.case,
            "path": gate.path,
            "display_path": f"{gate.case}.{gate.path}",
            "status": "FAIL",
            "passed": False,
            "actual": actual_value,
            "expected": gate.value,
            "op": gate.op,
            "base_case": gate.base_case,
            "base_value": None,
            "relative_drop": None,
            "error": "",
        }
        if gate.kind == "threshold":
            passed, error = _threshold_passes(actual=actual_value, op=gate.op, expected=gate.value)
            record["passed"] = passed
            record["status"] = "PASS" if passed else "FAIL"
            record["error"] = error
            results.append(record)
            continue

        if gate.kind == "min_fraction_pass":
            matched = _case_items_for_gate(case_results, gate.case)
            fraction_rows: list[dict[str, Any]] = []
            pass_count = 0
            for case_name, payload in matched:
                value = get_nested_value(payload, gate.path)
                passed, error = _threshold_passes(actual=value, op=gate.op, expected=gate.value)
                fraction_rows.append(
                    {
                        "case": case_name,
                        "actual": value,
                        "passed": bool(passed),
                        "error": error,
                    }
                )
                if passed:
                    pass_count += 1
            matched_count = len(matched)
            fraction = float(pass_count / matched_count) if matched_count else None
            passed = fraction is not None and fraction >= float(gate.min_fraction)
            record.update(
                {
                    "actual": fraction,
                    "expected": float(gate.min_fraction),
                    "threshold_value": gate.value,
                    "matched_case_count": matched_count,
                    "pass_count": pass_count,
                    "case_results": fraction_rows,
                    "error": "" if matched_count else "no_matching_cases",
                    "passed": passed,
                    "status": "PASS" if passed else "FAIL",
                }
            )
            results.append(record)
            continue

        if gate.kind == "worst_case_threshold":
            matched = _case_items_for_gate(case_results, gate.case)
            worst_case_rows: list[dict[str, Any]] = []
            values: list[float] = []
            for case_name, payload in matched:
                value = get_nested_value(payload, gate.path)
                value_num = to_float(value)
                worst_case_rows.append({"case": case_name, "actual": value, "numeric": value_num})
                if value_num is not None:
                    values.append(float(value_num))
            passed, worst_value, error = _group_threshold_passes(values, op=gate.op, expected=gate.value)
            record.update(
                {
                    "actual": worst_value,
                    "matched_case_count": len(matched),
                    "case_results": worst_case_rows,
                    "error": error,
                    "passed": passed,
                    "status": "PASS" if passed else "FAIL",
                }
            )
            results.append(record)
            continue

        base_payload = case_results.get(gate.base_case) or {}
        base_value = get_nested_value(base_payload, gate.path)
        relative_drop, error = _relative_drop(base_value, actual_value)
        record["base_value"] = base_value
        record["relative_drop"] = relative_drop
        record["error"] = error
        passed = relative_drop is not None and relative_drop <= gate.value
        record["passed"] = passed
        record["status"] = "PASS" if passed else "FAIL"
        results.append(record)
    return results


def build_verdict(
    case_results: Mapping[str, dict[str, Any]],
    gate_results: list[dict[str, Any]],
    protocol: StrategyValidationProtocol,
    *,
    preflight: Mapping[str, Any] | None = None,
    require_strict_contract: bool = False,
) -> dict[str, Any]:
    preflight_failures = [
        dict(item)
        for item in (preflight or {}).get("issues", []) or []
        if isinstance(item, dict) and str(item.get("severity") or "error").casefold() == "error"
    ]
    failed_required_cases = [
        {
            "case": name,
            "status": str(case_result.get("status") or ""),
            "error": str(case_result.get("error") or ""),
        }
        for name, case_result in case_results.items()
        if bool(case_result.get("gate_required", True)) and str(case_result.get("status") or "") != "completed"
    ]
    failed_hard_gates = [
        item
        for item in gate_results
        if str(item.get("level") or "hard") == "hard" and not bool(item.get("passed"))
    ]
    failed_warn_gates = [
        item
        for item in gate_results
        if str(item.get("level") or "hard") == "warn" and not bool(item.get("passed"))
    ]
    runtime_contract_failures: list[dict[str, Any]] = []
    if require_strict_contract:
        for name, case_result in case_results.items():
            contract_check = case_result.get("contract_check")
            contract_check = contract_check if isinstance(contract_check, Mapping) else {}
            evaluated = contract_check.get("evaluated") is True
            completed = str(case_result.get("status") or "").casefold() == "completed"
            if not evaluated and not completed:
                continue
            if (
                evaluated
                and contract_check.get("strict") is True
                and contract_check.get("ok") is True
            ):
                continue
            issues = [
                dict(item)
                for item in contract_check.get("issues", []) or []
                if isinstance(item, Mapping)
            ]
            runtime_contract_failures.append(
                {
                    "case": name,
                    "kind": "runtime_contract",
                    "status": str(contract_check.get("status") or "not_evaluated"),
                    "error": (
                        str(issues[0].get("message") or "")
                        if issues
                        else "promotion case did not produce a strict runtime contract pass"
                    ),
                    "issues": issues,
                }
            )
    trust_failures: list[dict[str, Any]] = []
    for name, case_result in case_results.items():
        if not bool(case_result.get("gate_required", True)):
            continue
        meta = case_result.get("meta")
        meta = meta if isinstance(meta, Mapping) else {}
        audit = meta.get("audit")
        audit = audit if isinstance(audit, Mapping) else {}
        audit_status = str(audit.get("status") or "missing").casefold()
        if audit_status not in {"pass", "warn"}:
            trust_failures.append(
                {
                    "case": name,
                    "kind": "research_audit",
                    "status": audit_status or "missing",
                    "error": "required case research audit did not pass",
                }
            )
    passed = (
        not preflight_failures
        and not failed_required_cases
        and not failed_hard_gates
        and not runtime_contract_failures
        and not trust_failures
    )
    return {
        "passed": passed,
        "decision": "PASS" if passed else "REJECT",
        "promotion_target": protocol.promotion_target,
        "required_case_failures": failed_required_cases,
        "hard_gate_failures": failed_hard_gates,
        "warn_gate_failures": failed_warn_gates,
        "preflight_failures": preflight_failures,
        "runtime_contract_failures": runtime_contract_failures,
        "trust_failures": trust_failures,
        "required_case_count": sum(1 for item in case_results.values() if bool(item.get("gate_required", True))),
        "hard_gate_count": sum(1 for item in gate_results if str(item.get("level") or "hard") == "hard"),
        "warn_gate_count": sum(1 for item in gate_results if str(item.get("level") or "hard") == "warn"),
    }


__all__ = ["build_verdict", "evaluate_gates"]
