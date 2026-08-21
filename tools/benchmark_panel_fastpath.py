"""Benchmark the canonical Parquet panel path on real backtest cases."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_STRATEGIES = (
    "strategy_library/generated/codex_2024_2026_qualified_reclaim_chase.py",
)


def _split_csv(raw: object) -> tuple[str, ...]:
    return tuple(item for item in (str(part).strip() for part in str(raw or "").split(",")) if item)


def _parse_strategy_offsets(raw: object) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in _split_csv(raw):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        key = name.strip()
        if not key:
            continue
        try:
            out[key] = max(0, int(value.strip()))
        except ValueError:
            continue
    return out


def _strategy_label(strategy: str) -> str:
    stem = Path(strategy.replace("\\", "/")).stem
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem) or "strategy"


def _mode_label(mode: str) -> str:
    return str(mode).strip().lower()


def _case_output_path(out_dir: Path, *, strategy: str, mode: str, repeat_index: int) -> Path:
    return out_dir / f"{_strategy_label(strategy)}__{_mode_label(mode)}__run{repeat_index}.json"


def _profile_command(
    args: argparse.Namespace,
    *,
    strategy: str,
    offset_days: int,
    json_out: Path,
    panel_source: str,
) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "profile_backtest.py"),
        "--case",
        "mysql_real",
        "--strategy-module",
        strategy,
        "--stock-limit",
        str(args.stock_limit),
        "--start-date",
        str(args.start_date),
        "--end-date",
        str(args.end_date),
        "--offset-days",
        str(offset_days),
        "--json-out",
        str(json_out),
        "--panel-source",
        str(panel_source),
        "--panel-path",
        str(args.panel_path),
    ]
    if args.no_build_topn_trade_segments:
        cmd.append("--no-build-topn-trade-segments")
    if args.jit:
        cmd.extend(["--jit", args.jit])
    return cmd


def _run_profile_case(
    args: argparse.Namespace,
    *,
    strategy: str,
    offset_days: int,
    mode: str,
    repeat_index: int,
    out_dir: Path,
) -> dict[str, Any]:
    json_out = _case_output_path(
        out_dir,
        strategy=strategy,
        mode=mode,
        repeat_index=repeat_index,
    )
    env = os.environ.copy()
    env["RUN_QUIET"] = "1"

    cmd = _profile_command(
        args,
        strategy=strategy,
        offset_days=offset_days,
        json_out=json_out,
        panel_source=mode,
    )
    started = perf_counter()
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    wall_sec = perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            "profile_backtest failed "
            f"strategy={strategy!r} mode={mode!r} repeat={repeat_index}: "
            f"returncode={completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    payload.update(
        {
            "panel_source": mode,
            "panel_path": args.panel_path,
            "strategy_label": _strategy_label(strategy),
            "repeat_index": int(repeat_index),
            "profile_wall_sec": float(wall_sec),
            "json_out": str(json_out),
        }
    )
    json_out.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return clean[int(pos)]
    return clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)


def _stats(values: list[float]) -> dict[str, float | None]:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None, "p95": None}
    return {
        "count": float(len(clean)),
        "mean": float(statistics.fmean(clean)),
        "median": float(statistics.median(clean)),
        "min": float(min(clean)),
        "max": float(max(clean)),
        "p95": _quantile(clean, 0.95),
    }


def _stage_value(payload: dict[str, Any], key: str) -> float | None:
    stages = payload.get("stage_timings_sec")
    if not isinstance(stages, dict):
        return None
    value = stages.get(key)
    if value is None:
        return None
    return float(value)


def _summarize_stage_timings(runs: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted(
        {
            str(key)
            for run in runs
            for key in (run.get("stage_timings_sec") or {}).keys()
            if isinstance(run.get("stage_timings_sec"), dict)
        },
        key=str.casefold,
    )
    return {
        key: _stats(
            [
                float(value)
                for run in runs
                for value in [_stage_value(run, key)]
                if value is not None
            ]
        )
        for key in keys
    }


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "elapsed_sec": _stats([float(run.get("elapsed_sec", 0.0) or 0.0) for run in runs]),
        "profile_wall_sec": _stats([float(run.get("profile_wall_sec", 0.0) or 0.0) for run in runs]),
        "backtest_all_stocks_sec": _stats(
            [
                float(value)
                for run in runs
                for value in [_stage_value(run, "backtest_all_stocks")]
                if value is not None
            ]
        ),
        "stage_timings_sec": _summarize_stage_timings(runs),
        "signal_stock_count_values": sorted({int(run.get("signal_stock_count", 0) or 0) for run in runs}),
        "final_net_value_values": sorted(
            {
                round(float(run["final_net_value"]), 12)
                for run in runs
                if run.get("final_net_value") is not None
            }
        ),
    }


def _first_by_mode(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for run in runs:
        mode = _mode_label(str(run.get("panel_source") or ""))
        out.setdefault(mode, run)
    return out


def _compare_to_baseline(runs: list[dict[str, Any]], *, tolerance: float) -> dict[str, Any]:
    first = _first_by_mode(runs)
    baseline = first.get("parquet") or next(iter(first.values()), None)
    if baseline is None:
        return {"ok": None, "reason": "no panel baseline"}

    checks: list[dict[str, Any]] = []
    baseline_count = int(baseline.get("signal_stock_count", 0) or 0)
    baseline_net = baseline.get("final_net_value")
    ok = True
    for mode, run in sorted(first.items()):
        if run is baseline:
            continue
        count = int(run.get("signal_stock_count", 0) or 0)
        net = run.get("final_net_value")
        count_ok = count == baseline_count
        if baseline_net is None or net is None:
            net_ok = baseline_net is None and net is None
            delta = None
        else:
            delta = abs(float(net) - float(baseline_net))
            net_ok = delta <= tolerance
        ok = ok and count_ok and net_ok
        checks.append(
            {
                "mode": mode,
                "signal_stock_count_ok": count_ok,
                "final_net_value_ok": net_ok,
                "final_net_value_abs_delta": delta,
            }
        )
    return {"ok": ok, "checks": checks, "tolerance": tolerance}


def _speedups(summary_by_mode: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {}


def _strategy_offset(strategy: str, *, default_offset: int, overrides: dict[str, int]) -> int:
    label = _strategy_label(strategy)
    return int(
        overrides.get(strategy)
        or overrides.get(strategy.replace("\\", "/"))
        or overrides.get(label)
        or default_offset
    )


def _mode_plan(args: argparse.Namespace) -> list[str]:
    modes: list[str] = []
    for mode in _split_csv(args.modes):
        normalized = mode.strip().lower()
        if normalized == "parquet":
            modes.append(normalized)
        else:
            raise ValueError(f"unsupported mode: {mode!r}")
    return modes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the canonical Parquet panel path on real backtest cases.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/benchmark_panel_fastpath.py --modes parquet --repeats 3 --stock-limit 500 --start-date 20260401 --end-date 20260429\n"
            "  python tools/benchmark_panel_fastpath.py --strategies strategy_library/generated/codex_2024_2026_qualified_reclaim_chase.py --offset-days 120\n"
        ),
    )
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES), help="Comma-separated strategy modules/paths.")
    parser.add_argument("--strategy-offsets", default="", help="Comma-separated label=offset overrides, e.g. codex_2024_2026_qualified_reclaim_chase=120.")
    parser.add_argument("--start-date", default="20260401")
    parser.add_argument("--end-date", default="20260429")
    parser.add_argument("--stock-limit", default="500")
    parser.add_argument("--offset-days", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--modes", default="parquet", help="Must be parquet; no alternate panel source is supported.")
    parser.add_argument("--panel-path", default="data/panel_cache/stock_daily.parquet")
    parser.add_argument("--out-dir", default="output/tools/panel_benchmark")
    parser.add_argument("--json-out", default="", help="Summary JSON path; defaults to <out-dir>/summary.json.")
    parser.add_argument("--tolerance", type=float, default=1e-10, help="final_net_value absolute tolerance.")
    parser.add_argument("--jit", choices=["", "on", "off"], default="")
    parser.add_argument("--no-build-topn-trade-segments", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    strategies = _split_csv(args.strategies)
    if not strategies:
        raise SystemExit("No strategies provided.")
    repeats = max(1, int(args.repeats))
    out_dir = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = (
        Path(args.json_out)
        if args.json_out
        else out_dir / "summary.json"
    )
    if not summary_path.is_absolute():
        summary_path = ROOT / summary_path
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    offset_overrides = _parse_strategy_offsets(args.strategy_offsets)
    modes = _mode_plan(args)
    all_runs: list[dict[str, Any]] = []
    strategy_summaries: dict[str, Any] = {}

    for strategy in strategies:
        offset_days = _strategy_offset(strategy, default_offset=int(args.offset_days), overrides=offset_overrides)
        strategy_runs: list[dict[str, Any]] = []
        for repeat_index in range(1, repeats + 1):
            for mode in modes:
                print(
                    "[BENCH] "
                    f"strategy={strategy} mode={_mode_label(mode)} "
                    f"repeat={repeat_index}/{repeats}"
                )
                run = _run_profile_case(
                    args,
                    strategy=strategy,
                    offset_days=offset_days,
                    mode=mode,
                    repeat_index=repeat_index,
                    out_dir=out_dir,
                )
                strategy_runs.append(run)
                all_runs.append(run)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for run in strategy_runs:
            label = _mode_label(str(run.get("panel_source") or ""))
            grouped.setdefault(label, []).append(run)
        by_mode = {mode: _summarize_runs(runs) for mode, runs in sorted(grouped.items())}
        strategy_summaries[strategy] = {
            "offset_days": offset_days,
            "by_mode": by_mode,
            "consistency": _compare_to_baseline(strategy_runs, tolerance=float(args.tolerance)),
            "speedups": _speedups(by_mode),
        }

    summary = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "runs": all_runs,
        "strategies": strategy_summaries,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"[OK] panel benchmark summary written: {summary_path}")
    for strategy, info in strategy_summaries.items():
        print(f"[SUMMARY] {strategy}")
        for mode, stats in info["by_mode"].items():
            median = stats["elapsed_sec"]["median"]
            backtest_median = stats["backtest_all_stocks_sec"]["median"]
            print(f"  {mode}: elapsed_median={median:.4f}s backtest_median={backtest_median:.4f}s")
        print(f"  consistency={info['consistency']}")
        print(f"  speedups={info['speedups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
