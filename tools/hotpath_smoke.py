"""Small local hot-path timing checks for core price-frame operations."""
from __future__ import annotations

import argparse
import gc
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dataframe_schema import normalize_price_frame
from core.caching.price_l2 import PriceParquetL2Cache
from core.caching.price_frame import slice_price_frame_by_date
from tools.hotpath_protocol import (
    HOTPATH_REPORT_SCHEMA_VERSION,
    _aggregate_benchmark_reports,
    _compare_case_timings,
    _failed_comparison,
    _measurement_protocol,
    _validate_hotpath_report,
    _validated_tolerance,
)

DEFAULT_CASES = ("price_normalize", "price_slice", "parquet_l2_roundtrip")
DEFAULT_BASELINE_PATH = ROOT / "tools" / "hotpath_baseline.json"
DEFAULT_ROWS = 5000
DEFAULT_REPEAT = 5
DEFAULT_WARMUP = 3
DEFAULT_MIN_SAMPLE_MS = 100.0
DEFAULT_BENCHMARK_ROUNDS = 3

def _build_price_frame(*, rows: int, code: str = "AAA.SZ", include_direction: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=max(int(rows), 1))
    payload: dict[str, object] = {
        "trade_date": dates,
        "stock_code": [code] * len(dates),
        "open": [10.0 + (idx % 17) * 0.01 for idx in range(len(dates))],
        "high": [10.5 + (idx % 17) * 0.01 for idx in range(len(dates))],
        "low": [9.5 + (idx % 17) * 0.01 for idx in range(len(dates))],
        "close": [10.2 + (idx % 17) * 0.01 for idx in range(len(dates))],
        "volume": [1000.0 + idx for idx in range(len(dates))],
    }
    if include_direction:
        payload["direction"] = [""] * len(dates)
    return pd.DataFrame(payload)


def _time_call(
    fn: Callable[[], object],
    *,
    repeat: int,
    warmup: int,
    min_sample_ms: float,
) -> dict[str, object]:
    repeat_count = int(repeat)
    warmup_count = int(warmup)
    minimum_ms = float(min_sample_ms)
    if repeat_count <= 0:
        raise ValueError("repeat must be positive")
    if warmup_count < 0:
        raise ValueError("warmup must be non-negative")
    if not math.isfinite(minimum_ms) or minimum_ms <= 0:
        raise ValueError("min_sample_ms must be finite and positive")

    last_result: object = None
    for _ in range(warmup_count):
        last_result = fn()

    samples: list[float] = []
    batch_elapsed_ms: list[float] = []
    batch_process_cpu_ms: list[float] = []
    batch_iterations: list[int] = []
    minimum_ns = int(math.ceil(minimum_ms * 1_000_000.0))
    for _ in range(repeat_count):
        gc.collect()
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        started_ns = time.perf_counter_ns()
        process_started_ns = time.process_time_ns()
        calls = 0
        try:
            while True:
                last_result = fn()
                calls += 1
                elapsed_ns = time.perf_counter_ns() - started_ns
                if elapsed_ns >= minimum_ns:
                    break
        finally:
            process_elapsed_ns = time.process_time_ns() - process_started_ns
            if gc_was_enabled:
                gc.enable()
        elapsed_ms = elapsed_ns / 1_000_000.0
        samples.append(elapsed_ms / calls)
        batch_elapsed_ms.append(elapsed_ms)
        batch_process_cpu_ms.append(process_elapsed_ns / 1_000_000.0)
        batch_iterations.append(calls)
    return {
        "elapsed_ms": float(min(samples)),
        "samples_ms": [round(value, 6) for value in samples],
        "batch_elapsed_ms": [round(value, 6) for value in batch_elapsed_ms],
        "batch_process_cpu_ms": [round(value, 6) for value in batch_process_cpu_ms],
        "batch_iterations": batch_iterations,
        "result_rows": int(len(last_result)) if hasattr(last_result, "__len__") else None,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def compare_hotpath_baseline(
    payload: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    tolerance_pct: float,
) -> dict[str, Any]:
    tolerance = _validated_tolerance(tolerance_pct)
    if tolerance is None:
        return _failed_comparison(
            "invalid_tolerance",
            reason="invalid_tolerance_pct",
            observed=tolerance_pct,
        )
    if baseline is None:
        return _failed_comparison("missing_baseline", reason="baseline_not_found")
    current_report, current_failure = _validate_hotpath_report(payload, label="current")
    if current_failure is not None:
        return current_failure
    baseline_report, baseline_failure = _validate_hotpath_report(baseline, label="baseline")
    if baseline_failure is not None:
        return baseline_failure
    assert current_report is not None and baseline_report is not None

    current_parameters = {
        "rows": current_report["rows"],
        "repeat": current_report["repeat"],
        "benchmark_rounds": current_report["benchmark_rounds"],
    }
    baseline_parameters = {
        "rows": baseline_report["rows"],
        "repeat": baseline_report["repeat"],
        "benchmark_rounds": baseline_report["benchmark_rounds"],
    }
    if current_parameters != baseline_parameters:
        return _failed_comparison(
            "parameter_mismatch",
            reason="benchmark_parameters_mismatch",
            current=current_parameters,
            baseline=baseline_parameters,
        )

    current_protocol = current_report["measurement_protocol"]
    baseline_protocol = baseline_report["measurement_protocol"]
    if current_protocol != baseline_protocol:
        return _failed_comparison(
            "protocol_mismatch",
            reason="measurement_protocol_mismatch",
            current=current_protocol,
            baseline=baseline_protocol,
        )

    current_case_names = current_report["case_names"]
    baseline_case_names = baseline_report["case_names"]
    if current_case_names != baseline_case_names:
        return _failed_comparison(
            "case_set_mismatch",
            reason="case_set_mismatch",
            missing_from_baseline=sorted(current_case_names - baseline_case_names, key=str.casefold),
            unexpected_in_baseline=sorted(baseline_case_names - current_case_names, key=str.casefold),
        )

    failures = _compare_case_timings(
        payload,
        baseline,
        case_names=current_case_names,
        tolerance_pct=tolerance,
    )
    return {
        "ok": not failures,
        "status": (
            "completed"
            if not failures
            else "invalid_measurement"
            if any(failure["reason"] == "invalid_elapsed_ms" for failure in failures)
            else "regression"
        ),
        "failures": failures,
    }


def run_hotpath_cases(
    *,
    cases: tuple[str, ...],
    rows: int,
    repeat: int,
    warmup: int = DEFAULT_WARMUP,
    min_sample_ms: float = DEFAULT_MIN_SAMPLE_MS,
) -> dict[str, object]:
    base = _build_price_frame(rows=rows)
    normalized = normalize_price_frame(base, stock_code="AAA.SZ", source="hotpath_smoke_seed")
    results: dict[str, object] = {}
    for case in cases:
        if case == "price_normalize":
            results[case] = _time_call(
                lambda: normalize_price_frame(
                    base.copy(),
                    stock_code="AAA.SZ",
                    source="hotpath_smoke",
                ),
                repeat=repeat,
                warmup=warmup,
                min_sample_ms=min_sample_ms,
            )
        elif case == "price_slice":
            results[case] = _time_call(
                lambda: slice_price_frame_by_date(
                    normalized,
                    stock_code="AAA.SZ",
                    start_date="20240115",
                    end_date="20240215",
                ),
                repeat=repeat,
                warmup=warmup,
                min_sample_ms=min_sample_ms,
            )
        elif case == "parquet_l2_roundtrip":
            temp_root = ROOT / "_pytest_tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="hotpath_l2_", dir=temp_root) as cache_dir:
                cache = PriceParquetL2Cache(cache_dir)
                cache.write("AAA.SZ", normalized)
                results[case] = _time_call(
                    lambda: cache.read_if_fresh("AAA.SZ").frame,
                    repeat=repeat,
                    warmup=warmup,
                    min_sample_ms=min_sample_ms,
                )
        else:
            raise ValueError(f"unsupported hotpath case: {case!r}")
    return {
        "schema_version": HOTPATH_REPORT_SCHEMA_VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "rows": int(rows),
        "repeat": int(repeat),
        "benchmark_rounds": 1,
        "measurement_protocol": _measurement_protocol(
            warmup=warmup,
            min_sample_ms=min_sample_ms,
        ),
        "cases": results,
    }


def run_hotpath_benchmark(
    *,
    cases: tuple[str, ...],
    rows: int,
    repeat: int,
    warmup: int,
    min_sample_ms: float,
    benchmark_rounds: int,
) -> dict[str, object]:
    reports = [
        run_hotpath_cases(
            cases=cases,
            rows=rows,
            repeat=repeat,
            warmup=warmup,
            min_sample_ms=min_sample_ms,
        )
        for _ in range(benchmark_rounds)
    ]
    if len(reports) == 1:
        return reports[0]
    return _aggregate_benchmark_reports(reports)


def _parse_cases(raw: str) -> tuple[str, ...]:
    normalized = str(raw or "all").strip().lower()
    if normalized == "all":
        return DEFAULT_CASES
    cases = tuple(item for item in (part.strip() for part in normalized.split(",")) if item)
    return cases or DEFAULT_CASES


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行本地核心 hot-path smoke timing。")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速诊断：运行两个轻量 case，不读取或更新 canonical baseline。",
    )
    parser.add_argument("--case", default="all", help="逗号分隔 case；默认 all。")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="生成的本地价格行数。")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help="每个 case 的正式批量样本数。")
    parser.add_argument("--rounds", type=int, default=DEFAULT_BENCHMARK_ROUNDS, help="完整 case 集的采样轮数。")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="每个 case 正式采样前的预热调用数。")
    parser.add_argument(
        "--min-sample-ms",
        type=float,
        default=DEFAULT_MIN_SAMPLE_MS,
        help="每个正式批量样本的最低计时时长（毫秒）。",
    )
    parser.add_argument("--max-ms", type=float, default=0.0, help="可选：任一 case 的汇总耗时超过该值则失败。")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH), help="hotpath baseline JSON 路径。")
    parser.add_argument("--update-baseline", action="store_true", help="用本次结果更新 baseline。")
    parser.add_argument("--tolerance-pct", type=float, default=50.0, help="相对 baseline 允许的回退百分比。")
    parser.add_argument("--json-out", default="", help="可选：写出 JSON 报告。")
    return parser.parse_args(argv)


def _enforce_max_ms(payload: dict[str, object], max_ms: float) -> bool:
    if max_ms <= 0:
        return True
    slow = {
        name: result
        for name, result in payload["cases"].items()
        if isinstance(result, dict) and float(result.get("elapsed_ms") or 0.0) > max_ms
    }
    if slow:
        print(f"[FAIL] hotpath case exceeded max_ms={max_ms}: {sorted(slow)}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.quick and args.update_baseline:
        print("[FAIL] --quick cannot update the canonical hotpath baseline")
        return 2
    tolerance_pct = float(args.tolerance_pct)
    if not math.isfinite(tolerance_pct) or tolerance_pct < 0:
        print("[FAIL] hotpath tolerance_pct must be finite and non-negative")
        return 2
    if args.quick:
        selected_cases = ("price_normalize", "price_slice")
        rows = 500
        repeat = 1
        benchmark_rounds = 1
        warmup = 0
        min_sample_ms = 1.0
    else:
        selected_cases = _parse_cases(args.case)
        rows = int(args.rows)
        repeat = int(args.repeat)
        benchmark_rounds = int(args.rounds)
        warmup = int(args.warmup)
        min_sample_ms = float(args.min_sample_ms)
    if (
        rows <= 0
        or repeat <= 0
        or benchmark_rounds <= 0
        or benchmark_rounds % 2 == 0
        or warmup < 0
        or not math.isfinite(min_sample_ms)
        or min_sample_ms <= 0
    ):
        print("[FAIL] hotpath rows/repeat/min_sample_ms must be positive; rounds must be odd; warmup non-negative")
        return 2
    baseline_path = Path(args.baseline)
    out_path = Path(args.json_out) if args.json_out else None
    if (
        out_path is not None
        and out_path.resolve() == baseline_path.resolve()
        and not args.update_baseline
    ):
        print("[FAIL] hotpath json-out cannot overwrite baseline without --update-baseline")
        return 2
    canonical_update = args.update_baseline and baseline_path.resolve() == DEFAULT_BASELINE_PATH.resolve()
    if canonical_update and (
        selected_cases != DEFAULT_CASES
        or rows != DEFAULT_ROWS
        or repeat != DEFAULT_REPEAT
        or benchmark_rounds != DEFAULT_BENCHMARK_ROUNDS
        or warmup != DEFAULT_WARMUP
        or min_sample_ms != DEFAULT_MIN_SAMPLE_MS
    ):
        print("[FAIL] canonical hotpath baseline requires the default cases and measurement protocol")
        return 2

    payload = run_hotpath_benchmark(
        cases=selected_cases,
        rows=rows,
        repeat=repeat,
        warmup=warmup,
        min_sample_ms=min_sample_ms,
        benchmark_rounds=benchmark_rounds,
    )
    if out_path is not None:
        _write_json(out_path, payload)
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))

    max_ms = float(args.max_ms or 0.0)
    if args.quick:
        if not _enforce_max_ms(payload, max_ms):
            return 1
        print("[OK] quick diagnostic completed; canonical hotpath baseline was not read or updated")
        return 0

    if args.update_baseline:
        _write_json(baseline_path, payload)
        print(f"[OK] hotpath baseline updated: {baseline_path}")
        return 0

    baseline = _load_json(baseline_path)
    comparison = compare_hotpath_baseline(payload, baseline, tolerance_pct=tolerance_pct)
    if baseline is None and baseline_path.exists():
        comparison = _failed_comparison("invalid_baseline", reason="baseline_json_is_invalid")
    if not comparison["ok"]:
        if comparison["status"] != "regression":
            print(
                f"[FAIL] hotpath baseline check failed: status={comparison['status']} "
                + json.dumps(comparison["failures"], ensure_ascii=True, sort_keys=True)
            )
            return 1
        print(
            "[FAIL] hotpath regression detected: "
            + json.dumps(comparison["failures"], ensure_ascii=True, sort_keys=True)
        )
        return 1
    print(f"[OK] hotpath within tolerance: tol={tolerance_pct:.1f}% status={comparison['status']}")

    if not _enforce_max_ms(payload, max_ms):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
