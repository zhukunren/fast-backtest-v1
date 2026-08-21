"""Shared validation primitives for hot-path timing reports."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


HOTPATH_REPORT_SCHEMA_VERSION = 2

_MEASUREMENT_PROTOCOL_FIXED_FIELDS = {
    "batching": "adaptive_until_minimum_wall_ms",
    "cpu_diagnostic_timer": "time.process_time_ns",
    "cyclic_gc": "collect_then_disable_per_batch",
    "sample_statistic": "wall_ms_per_call",
    "summary_statistic": "minimum",
    "timer": "time.perf_counter_ns",
}
ROUND_CAPTURE_AGGREGATION = "minimum_of_round_minima"


def _measurement_protocol(*, warmup: int, min_sample_ms: float) -> dict[str, object]:
    return {
        **_MEASUREMENT_PROTOCOL_FIXED_FIELDS,
        "minimum_sample_ms": float(min_sample_ms),
        "warmup_calls": int(warmup),
    }


def _failed_comparison(status: str, *, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "failures": [{"reason": reason, **details}],
    }


def _positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value if value > 0 else 0


def _non_negative_int(payload: dict[str, Any], field: str) -> int | None:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _non_negative_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _validated_tolerance(value: object) -> float | None:
    try:
        tolerance = float(value)
    except (TypeError, ValueError):
        return None
    return tolerance if math.isfinite(tolerance) and tolerance >= 0 else None


def _validate_measurement_protocol(
    report: dict[str, Any],
    *,
    label: str,
) -> tuple[dict[str, object] | None, dict[str, Any] | None]:
    protocol = report.get("measurement_protocol")
    expected_fields = {*_MEASUREMENT_PROTOCOL_FIXED_FIELDS, "minimum_sample_ms", "warmup_calls"}
    if not isinstance(protocol, dict) or set(protocol) != expected_fields:
        return None, _failed_comparison(
            f"invalid_{label}",
            reason="invalid_measurement_protocol",
            report=label,
        )
    for field, expected in _MEASUREMENT_PROTOCOL_FIXED_FIELDS.items():
        if protocol.get(field) != expected:
            return None, _failed_comparison(
                f"invalid_{label}",
                reason="unsupported_measurement_protocol",
                report=label,
                field=field,
                observed=protocol.get(field),
                expected=expected,
            )
    warmup = _non_negative_int(protocol, "warmup_calls")
    minimum_ms = _positive_finite(protocol.get("minimum_sample_ms"))
    if warmup is None or minimum_ms is None:
        return None, _failed_comparison(
            f"invalid_{label}",
            reason="invalid_measurement_protocol_parameters",
            report=label,
            warmup_calls=protocol.get("warmup_calls"),
            minimum_sample_ms=protocol.get("minimum_sample_ms"),
        )
    return _measurement_protocol(warmup=warmup, min_sample_ms=minimum_ms), None


def _validate_case_measurement(
    result: object,
    *,
    case: str,
    label: str,
    repeat: int,
    minimum_sample_ms: float,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_case_measurement",
            report=label,
            case=case,
            field="case",
        )
    elapsed_ms = _positive_finite(result.get("elapsed_ms"))
    result_rows = result.get("result_rows")
    samples = result.get("samples_ms")
    batches = result.get("batch_elapsed_ms")
    cpu_batches = result.get("batch_process_cpu_ms")
    iterations = result.get("batch_iterations")
    if (
        elapsed_ms is None
        or not isinstance(result_rows, int)
        or isinstance(result_rows, bool)
        or result_rows < 0
        or not isinstance(samples, list)
        or not isinstance(batches, list)
        or not isinstance(cpu_batches, list)
        or not isinstance(iterations, list)
        or len(samples) != repeat
        or len(batches) != repeat
        or len(cpu_batches) != repeat
        or len(iterations) != repeat
    ):
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_case_measurement",
            report=label,
            case=case,
            field="sample_shape",
        )

    normalized_samples: list[float] = []
    for index, (sample, batch, cpu_batch, calls) in enumerate(
        zip(samples, batches, cpu_batches, iterations, strict=True)
    ):
        sample_ms = _positive_finite(sample)
        batch_ms = _positive_finite(batch)
        cpu_batch_ms = _non_negative_finite(cpu_batch)
        if (
            sample_ms is None
            or batch_ms is None
            or cpu_batch_ms is None
            or not isinstance(calls, int)
            or isinstance(calls, bool)
            or calls <= 0
            or batch_ms + 1e-6 < minimum_sample_ms
            or not math.isclose(sample_ms, batch_ms / calls, rel_tol=1e-6, abs_tol=2e-6)
        ):
            return _failed_comparison(
                f"invalid_{label}",
                reason="invalid_case_measurement",
                report=label,
                case=case,
                field="sample",
                sample_index=index,
            )
        normalized_samples.append(sample_ms)

    if not math.isclose(
        elapsed_ms,
        min(normalized_samples),
        rel_tol=1e-6,
        abs_tol=2e-6,
    ):
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_case_measurement",
            report=label,
            case=case,
            field="elapsed_ms",
        )
    return None


def _validate_round_capture(
    report: dict[str, Any],
    *,
    label: str,
    case_names: set[str],
    benchmark_rounds: int,
) -> dict[str, Any] | None:
    capture = report.get("round_capture")
    if benchmark_rounds == 1:
        if capture is None:
            return None
        return _failed_comparison(
            f"invalid_{label}",
            reason="unexpected_round_capture",
            report=label,
        )
    if benchmark_rounds < 3 or benchmark_rounds % 2 == 0 or not isinstance(capture, dict):
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_round_capture",
            report=label,
        )
    if set(capture) != {"aggregation", "case_elapsed_ms", "rounds"}:
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_round_capture",
            report=label,
        )
    elapsed_by_case = capture.get("case_elapsed_ms")
    if (
        capture.get("aggregation") != ROUND_CAPTURE_AGGREGATION
        or capture.get("rounds") != benchmark_rounds
        or not isinstance(elapsed_by_case, dict)
        or set(elapsed_by_case) != case_names
    ):
        return _failed_comparison(
            f"invalid_{label}",
            reason="invalid_round_capture",
            report=label,
        )
    cases = report.get("cases")
    assert isinstance(cases, dict)
    for case in sorted(case_names, key=str.casefold):
        values = elapsed_by_case.get(case)
        if not isinstance(values, list) or len(values) != benchmark_rounds:
            return _failed_comparison(
                f"invalid_{label}",
                reason="invalid_round_capture",
                report=label,
                case=case,
            )
        normalized = [_positive_finite(value) for value in values]
        elapsed_ms = _positive_finite(cases[case].get("elapsed_ms"))
        if (
            elapsed_ms is None
            or any(value is None for value in normalized)
            or not math.isclose(
                elapsed_ms,
                min(value for value in normalized if value is not None),
                rel_tol=1e-6,
                abs_tol=2e-6,
            )
        ):
            return _failed_comparison(
                f"invalid_{label}",
                reason="invalid_round_capture",
                report=label,
                case=case,
            )
    return None


def _validate_hotpath_report(
    report: dict[str, Any],
    *,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    schema_version = report.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != HOTPATH_REPORT_SCHEMA_VERSION
    ):
        return None, _failed_comparison(
            f"invalid_{label}",
            reason="schema_version_mismatch",
            report=label,
            observed=schema_version,
            expected=HOTPATH_REPORT_SCHEMA_VERSION,
        )
    rows = _positive_int(report, "rows")
    repeat = _positive_int(report, "repeat")
    benchmark_rounds = _positive_int(report, "benchmark_rounds")
    if rows <= 0 or repeat <= 0 or benchmark_rounds <= 0:
        return None, _failed_comparison(
            f"invalid_{label}",
            reason="invalid_benchmark_parameters",
            report=label,
            rows=report.get("rows"),
            repeat=report.get("repeat"),
            benchmark_rounds=report.get("benchmark_rounds"),
        )
    protocol, protocol_failure = _validate_measurement_protocol(report, label=label)
    if protocol_failure is not None:
        return None, protocol_failure
    assert protocol is not None
    cases = report.get("cases")
    if not isinstance(cases, dict) or not cases:
        return None, _failed_comparison(f"invalid_{label}", reason="missing_cases")
    if not all(isinstance(case, str) and case.strip() for case in cases):
        return None, _failed_comparison(f"invalid_{label}", reason="invalid_case_name")
    for case, result in cases.items():
        measurement_failure = _validate_case_measurement(
            result,
            case=case,
            label=label,
            repeat=repeat,
            minimum_sample_ms=float(protocol["minimum_sample_ms"]),
        )
        if measurement_failure is not None:
            return None, measurement_failure
    capture_failure = _validate_round_capture(
        report,
        label=label,
        case_names=set(cases),
        benchmark_rounds=benchmark_rounds,
    )
    if capture_failure is not None:
        return None, capture_failure
    return {
        "rows": rows,
        "repeat": repeat,
        "benchmark_rounds": benchmark_rounds,
        "measurement_protocol": protocol,
        "cases": cases,
        "case_names": set(cases),
    }, None


def _case_elapsed(payload: dict[str, Any], case: str) -> float:
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        return 0.0
    result = cases.get(case)
    if not isinstance(result, dict):
        return 0.0
    value = _positive_finite(result.get("elapsed_ms"))
    return value or 0.0


def _compare_case_timings(
    payload: dict[str, Any],
    baseline: dict[str, Any],
    *,
    case_names: set[str],
    tolerance_pct: float,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in sorted(case_names, key=str.casefold):
        current_ms = _case_elapsed(payload, case)
        baseline_ms = _case_elapsed(baseline, case)
        if current_ms <= 0 or baseline_ms <= 0:
            failures.append(
                {
                    "reason": "invalid_elapsed_ms",
                    "case": case,
                    "current_ms": current_ms,
                    "baseline_ms": baseline_ms,
                }
            )
            continue
        allowed_ms = baseline_ms * (1.0 + tolerance_pct / 100.0)
        if current_ms > allowed_ms:
            failures.append(
                {
                    "reason": "regression",
                    "case": case,
                    "current_ms": current_ms,
                    "baseline_ms": baseline_ms,
                    "allowed_ms": allowed_ms,
                }
            )
    return failures


def _aggregate_benchmark_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if len(reports) < 3 or len(reports) % 2 == 0:
        raise ValueError("benchmark capture requires an odd number of at least three reports")

    reference_signature: tuple[object, ...] | None = None
    case_names: set[str] | None = None
    for index, report in enumerate(reports):
        rows = _positive_int(report, "rows")
        repeat = _positive_int(report, "repeat")
        benchmark_rounds = _positive_int(report, "benchmark_rounds")
        protocol, protocol_failure = _validate_measurement_protocol(
            report,
            label=f"capture_round_{index + 1}",
        )
        cases = report.get("cases")
        if (
            report.get("schema_version") != HOTPATH_REPORT_SCHEMA_VERSION
            or rows <= 0
            or repeat <= 0
            or benchmark_rounds != 1
            or protocol_failure is not None
            or protocol is None
            or not isinstance(cases, dict)
            or not cases
            or not all(isinstance(case, str) and case.strip() for case in cases)
        ):
            raise ValueError(f"invalid benchmark capture report at round {index + 1}")

        round_case_names = set(cases)
        signature = (
            rows,
            repeat,
            tuple(sorted(protocol.items())),
            tuple(sorted(round_case_names, key=str.casefold)),
        )
        if reference_signature is None:
            reference_signature = signature
            case_names = round_case_names
        elif signature != reference_signature:
            raise ValueError("benchmark capture reports must use identical parameters and cases")

        for case, result in cases.items():
            failure = _validate_case_measurement(
                result,
                case=case,
                label=f"capture_round_{index + 1}",
                repeat=repeat,
                minimum_sample_ms=float(protocol["minimum_sample_ms"]),
            )
            if failure is not None:
                raise ValueError(f"invalid benchmark capture measurement: round={index + 1} case={case}")

    assert case_names is not None
    payload = deepcopy(reports[-1])
    aggregate_cases: dict[str, Any] = {}
    case_elapsed_ms: dict[str, list[float]] = {}
    for case in sorted(case_names, key=str.casefold):
        values = [float(report["cases"][case]["elapsed_ms"]) for report in reports]
        representative_index = min(range(len(values)), key=lambda item: (values[item], item))
        aggregate_cases[case] = deepcopy(reports[representative_index]["cases"][case])
        case_elapsed_ms[case] = values

    payload["benchmark_rounds"] = len(reports)
    payload["cases"] = aggregate_cases
    payload["round_capture"] = {
        "aggregation": ROUND_CAPTURE_AGGREGATION,
        "case_elapsed_ms": case_elapsed_ms,
        "rounds": len(reports),
    }
    return payload


__all__ = [
    "ROUND_CAPTURE_AGGREGATION",
    "HOTPATH_REPORT_SCHEMA_VERSION",
    "_aggregate_benchmark_reports",
    "_compare_case_timings",
    "_failed_comparison",
    "_measurement_protocol",
    "_positive_int",
    "_validate_case_measurement",
    "_validate_hotpath_report",
    "_validate_measurement_protocol",
    "_validate_round_capture",
    "_validated_tolerance",
]
