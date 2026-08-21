"""用途:
- 运行轻量性能 smoke case，并与基线文件比较，判断是否出现明显性能回退。
- 默认调用 `tools/profile_backtest.py` 的 `offline_smoke` case，并输出结构化 JSON 记录。
- 当 profile JSON 提供 `backtest_elapsed_sec` 时，比较该回测内核耗时；端到端耗时仍保留在记录里。

用法:
- 使用默认基线检查：`python tools/perf_smoke.py`
- 使用本地快速诊断（不比较或更新正式基线）：`python tools/perf_smoke.py --quick`
- 更新基线：`python tools/perf_smoke.py --update-baseline`
- 指定自定义命令：`python tools/perf_smoke.py --cmd python tools/profile_backtest.py --case offline_smoke`"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median

import psutil


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.local_artifact_paths import performance_workspace_root  # noqa: E402


DEFAULT_RSS_TOLERANCE_PCT = 5.0
RSS_POLL_INTERVAL_SEC = 0.01


def _process_tree_rss_bytes(
    root_process: psutil.Process,
    known_processes: dict[int, psutil.Process],
) -> int:
    """Return the current RSS sum for the root process and observed descendants."""
    try:
        descendants = root_process.children(recursive=True)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        descendants = []
    for process in descendants:
        known_processes[process.pid] = process

    total = 0
    for pid, process in list(known_processes.items()):
        try:
            total += int(process.memory_info().rss)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            known_processes.pop(pid, None)
    return total


def _run(cmd: list[str], *, env: dict | None = None) -> tuple[int, float, int]:
    t0 = time.perf_counter()
    child = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    root_process = psutil.Process(child.pid)
    known_processes = {root_process.pid: root_process}
    peak_rss_bytes = 0
    while True:
        peak_rss_bytes = max(
            peak_rss_bytes,
            _process_tree_rss_bytes(root_process, known_processes),
        )
        returncode = child.poll()
        if returncode is not None:
            peak_rss_bytes = max(
                peak_rss_bytes,
                _process_tree_rss_bytes(root_process, known_processes),
            )
            break
        time.sleep(RSS_POLL_INTERVAL_SEC)
    elapsed = time.perf_counter() - t0
    return int(returncode), elapsed, peak_rss_bytes


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _coerce_positive_float(value: object) -> float:
    try:
        out = float(value)
    except Exception:
        return 0.0
    return out if out > 0 else 0.0


def _resolve_comparison_elapsed(record: dict | None) -> tuple[float, str]:
    if isinstance(record, dict):
        comparison_elapsed = _coerce_positive_float(record.get("comparison_elapsed_sec"))
        if comparison_elapsed > 0:
            return comparison_elapsed, "comparison_elapsed_sec"
        profile = record.get("profile")
        if isinstance(profile, dict):
            backtest_elapsed = _coerce_positive_float(profile.get("backtest_elapsed_sec"))
            if backtest_elapsed > 0:
                return backtest_elapsed, "profile.backtest_elapsed_sec"
            profile_elapsed = _coerce_positive_float(profile.get("elapsed_sec"))
            if profile_elapsed > 0:
                return profile_elapsed, "profile.elapsed_sec"
        outer_elapsed = _coerce_positive_float(record.get("elapsed_sec"))
        if outer_elapsed > 0:
            return outer_elapsed, "elapsed_sec"
    return 0.0, "elapsed_sec"


def _resolve_comparison_peak_rss(record: dict | None) -> tuple[int, str]:
    if isinstance(record, dict):
        for field in ("comparison_peak_rss_bytes", "peak_rss_bytes"):
            value = _coerce_positive_float(record.get(field))
            if value > 0:
                return int(value), field
    return 0, "comparison_peak_rss_bytes"


def _default_profile_command(*, profile_json_path: Path, case: str = "offline_smoke") -> list[str]:
    return [
        sys.executable,
        "tools/profile_backtest.py",
        "--case",
        str(case or "offline_smoke"),
        "--json-out",
        str(profile_json_path),
    ]


def _resolve_perf_tmp_root() -> Path:
    override = str(os.environ.get("PERF_SMOKE_TMP_ROOT") or "").strip()
    if override:
        return Path(override)
    return performance_workspace_root()


def _resolve_perf_output_root() -> Path:
    override = str(os.environ.get("PERF_SMOKE_OUTPUT_ROOT") or "").strip()
    if override:
        return Path(override)
    return ROOT / "output" / "tools" / "perf_smoke"


def _run_with_optional_warmup(
    cmd: list[str],
    *,
    env: dict[str, str],
    warmup_runs: int,
) -> tuple[int, float, int]:
    for _ in range(max(int(warmup_runs), 0)):
        rc, _, _ = _run(cmd, env=env)
        if rc != 0:
            return rc, 0.0, 0
    return _run(cmd, env=env)


def _sample_comparison_elapsed(sample: dict) -> float:
    record = {
        "elapsed_sec": sample.get("elapsed_sec"),
        "profile": sample.get("profile"),
    }
    value, _source = _resolve_comparison_elapsed(record)
    return value


def _median_positive(values: list[float]) -> float:
    positives = [float(value) for value in values if float(value) > 0]
    return float(median(positives)) if positives else 0.0


def _build_sample_record(
    *,
    index: int,
    cmd: list[str],
    elapsed: float,
    peak_rss_bytes: int,
    profile_json_path: Path,
) -> dict:
    profile_payload = _load_json(profile_json_path)
    sample = {
        "index": int(index),
        "command": cmd,
        "elapsed_sec": float(elapsed),
        "peak_rss_bytes": int(peak_rss_bytes),
        "profile_json": str(profile_json_path),
    }
    if profile_payload is not None:
        sample["profile"] = profile_payload
    comparison_elapsed, comparison_source = _resolve_comparison_elapsed(
        {
            "elapsed_sec": elapsed,
            "profile": profile_payload,
        }
    )
    sample["comparison_elapsed_sec"] = comparison_elapsed
    sample["comparison_source"] = comparison_source
    return sample


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="运行性能 smoke case，并与基线比较是否出现明显回退。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/perf_smoke.py\n"
            "  python tools/perf_smoke.py --update-baseline\n"
            "  python tools/perf_smoke.py --cmd python tools/profile_backtest.py --case offline_smoke"
        ),
    )
    parser.add_argument(
        "--baseline",
        default=str(ROOT / "tools" / "perf_baseline.json"),
        help="性能基线 JSON 路径。",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="用本次运行结果更新基线。",
    )
    parser.add_argument(
        "--tolerance-pct",
        type=float,
        default=float(os.environ.get("PERF_TOLERANCE_PCT", "20") or "20"),
        help="相对基线允许的性能回退百分比。",
    )
    parser.add_argument(
        "--rss-tolerance-pct",
        type=float,
        default=float(os.environ.get("PERF_RSS_TOLERANCE_PCT", str(DEFAULT_RSS_TOLERANCE_PCT))),
        help="相对基线允许的子进程树峰值 RSS 增长百分比。",
    )
    parser.add_argument(
        "--skip-rss-check",
        action="store_true",
        help="仅用于诊断自定义命令；默认质量门禁不得跳过 RSS 比较。",
    )
    parser.add_argument(
        "--cmd",
        nargs="+",
        default=None,
        help="可选：自定义要执行的性能命令。",
    )
    parser.add_argument(
        "--case",
        default=os.environ.get("PERF_SMOKE_CASE", "offline_smoke"),
        help="默认 profile 命令使用的 case 名称。",
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=int(os.environ.get("PERF_SMOKE_WARMUP_RUNS", "1") or "1"),
        help="正式计时前先做多少次预热运行并丢弃结果。",
    )
    parser.add_argument(
        "--repeat-runs",
        type=int,
        default=int(os.environ.get("PERF_SMOKE_REPEAT_RUNS", "3") or "3"),
        help="正式采样次数；>1 时用中位数与基线比较。",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速诊断：0 次预热、1 次采样，只验证离线性能命令成功，不读取或更新正式基线。",
    )
    args = parser.parse_args(argv)

    if args.quick and args.update_baseline:
        print("[FAIL] --quick cannot update the canonical performance baseline")
        return 2
    if args.quick:
        args.warmup_runs = 0
        args.repeat_runs = 1

    env = os.environ.copy()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    perf_tmp_root = _resolve_perf_tmp_root()
    perf_tmp_root.mkdir(parents=True, exist_ok=True)
    run_tmp_root = perf_tmp_root / run_id
    run_tmp_root.mkdir(parents=True, exist_ok=False)
    fixture_tmp_root = run_tmp_root / "offline_smoke"
    fixture_tmp_root.mkdir(parents=True, exist_ok=False)
    env["OFFLINE_SMOKE_TMP_ROOT"] = str(fixture_tmp_root)
    env.setdefault("RUN_QUIET", "1")
    env.setdefault("PROFILE_TIMING", "0")
    repeat_runs = max(int(args.repeat_runs or 1), 1)
    samples: list[dict] = []
    for index in range(repeat_runs):
        profile_json_path = run_tmp_root / f"profile_{index + 1}.json"
        cmd = list(args.cmd) if args.cmd else _default_profile_command(
            profile_json_path=profile_json_path,
            case=args.case,
        )
        if index == 0:
            rc, elapsed, peak_rss_bytes = _run_with_optional_warmup(
                cmd,
                env=env,
                warmup_runs=args.warmup_runs,
            )
        else:
            rc, elapsed, peak_rss_bytes = _run(cmd, env=env)
        if rc != 0:
            print(f"[FAIL] command failed: {' '.join(cmd)}")
            return rc
        samples.append(
            _build_sample_record(
                index=index + 1,
                cmd=cmd,
                elapsed=elapsed,
                peak_rss_bytes=peak_rss_bytes,
                profile_json_path=profile_json_path,
            )
        )

    elapsed = _median_positive([float(sample["elapsed_sec"]) for sample in samples])
    comparison_elapsed = _median_positive([_sample_comparison_elapsed(sample) for sample in samples])
    comparison_peak_rss_bytes = int(
        _median_positive([float(sample["peak_rss_bytes"]) for sample in samples])
    )
    peak_rss_sample_max_bytes = max(int(sample["peak_rss_bytes"]) for sample in samples)
    comparison_source = "median_sample" if repeat_runs > 1 else str(samples[0].get("comparison_source") or "elapsed_sec")
    profile_payload = samples[-1].get("profile")
    cmd = list(samples[-1]["command"])

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "command": cmd,
        "elapsed_sec": elapsed,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "warmup_runs": max(int(args.warmup_runs), 0),
        "repeat_runs": repeat_runs,
        "samples": samples,
        "comparison_peak_rss_bytes": comparison_peak_rss_bytes,
        "peak_rss_sample_max_bytes": peak_rss_sample_max_bytes,
        "rss_comparison_source": "median_sample_peak" if repeat_runs > 1 else "sample_peak",
        "quick": bool(args.quick),
    }
    if profile_payload is not None:
        record["profile"] = profile_payload
    record["comparison_elapsed_sec"] = comparison_elapsed
    record["comparison_source"] = comparison_source

    out_dir = _resolve_perf_output_root()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"perf_smoke_{run_id}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        f"[OK] perf_smoke elapsed={elapsed:.3f}s compare={comparison_elapsed:.3f}s "
        f"rss={comparison_peak_rss_bytes / (1024 * 1024):.1f}MiB "
        f"source={comparison_source} output={out_path}"
    )

    if args.quick:
        print("[OK] quick diagnostic completed; canonical performance baseline was not read or updated")
        return 0

    baseline_path = Path(args.baseline)
    baseline = _load_json(baseline_path)

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"[OK] baseline updated: {baseline_path}")
        return 0

    if baseline is None:
        print(f"[FAIL] performance baseline missing or invalid: {baseline_path}")
        return 1

    base_elapsed, base_source = _resolve_comparison_elapsed(baseline)
    if base_elapsed <= 0:
        print("[FAIL] performance baseline has no valid elapsed measurement")
        return 1

    allowed = base_elapsed * (1 + args.tolerance_pct / 100.0)
    if comparison_elapsed > allowed:
        print(
            f"[FAIL] regression detected: compare={comparison_elapsed:.3f}s "
            f"baseline={base_elapsed:.3f}s tol={args.tolerance_pct:.1f}% "
            f"current_source={comparison_source} baseline_source={base_source}"
        )
        return 1

    if not args.skip_rss_check:
        base_peak_rss_bytes, base_rss_source = _resolve_comparison_peak_rss(baseline)
        if base_peak_rss_bytes <= 0:
            print("[FAIL] performance baseline has no valid process-tree peak RSS measurement")
            return 1
        if comparison_peak_rss_bytes <= 0:
            print("[FAIL] current run has no valid process-tree peak RSS measurement")
            return 1
        rss_allowed = base_peak_rss_bytes * (1 + args.rss_tolerance_pct / 100.0)
        if comparison_peak_rss_bytes > rss_allowed:
            print(
                "[FAIL] RSS regression detected: "
                f"compare={comparison_peak_rss_bytes / (1024 * 1024):.1f}MiB "
                f"baseline={base_peak_rss_bytes / (1024 * 1024):.1f}MiB "
                f"tol={args.rss_tolerance_pct:.1f}% "
                f"current_source={record['rss_comparison_source']} "
                f"baseline_source={base_rss_source}"
            )
            return 1

    print(
        f"[OK] within tolerance: compare={comparison_elapsed:.3f}s "
        f"baseline={base_elapsed:.3f}s tol={args.tolerance_pct:.1f}% "
        f"current_source={comparison_source} baseline_source={base_source}"
    )
    if not args.skip_rss_check:
        print(
            "[OK] RSS within tolerance: "
            f"compare={comparison_peak_rss_bytes / (1024 * 1024):.1f}MiB "
            f"baseline={base_peak_rss_bytes / (1024 * 1024):.1f}MiB "
            f"tol={args.rss_tolerance_pct:.1f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
