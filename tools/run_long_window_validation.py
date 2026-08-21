from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "tools" / "long_window_validation"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _resolve_output_path(raw: str | None, *, default_name: str) -> Path:
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else ROOT / path
    return DEFAULT_OUTPUT_DIR / default_name


def build_profile_command(args: argparse.Namespace, *, profile_out: Path) -> list[str]:
    command = [
        sys.executable,
        "tools/profile_backtest.py",
        "--case",
        str(args.case),
        "--json-out",
        str(profile_out),
    ]
    optional_pairs = (
        ("--start-date", args.start_date),
        ("--end-date", args.end_date),
        ("--stock-limit", args.stock_limit),
        ("--stock-codes", args.stock_codes),
        ("--offset-days", args.offset_days),
        ("--panel-source", args.panel_source),
        ("--panel-path", args.panel_path),
        ("--strategy-module", args.strategy_module),
        ("--jit", args.jit),
    )
    for flag, value in optional_pairs:
        if str(value or "").strip():
            command.extend([flag, str(value)])
    if args.build_topn_trade_segments:
        command.append("--build-topn-trade-segments")
    if args.no_build_topn_trade_segments:
        command.append("--no-build-topn-trade-segments")
    return command


def build_budget_command(args: argparse.Namespace, *, profile_out: Path, budget_out: Path) -> list[str] | None:
    if not str(args.budget or "").strip():
        return None
    command = [
        sys.executable,
        "tools/performance_budget.py",
        "--report",
        str(profile_out),
        "--budget",
        str(_resolve_output_path(args.budget, default_name="")),
        "--json-out",
        str(budget_out),
    ]
    if str(args.baseline or "").strip():
        command.extend(["--baseline", str(_resolve_output_path(args.baseline, default_name=""))])
    return command


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    stamp = _timestamp()
    profile_out = _resolve_output_path(args.profile_out, default_name=f"profile_{stamp}.json")
    report_out = _resolve_output_path(args.json_out, default_name=f"long_window_{stamp}.json")
    budget_out = _resolve_output_path(args.budget_out, default_name=f"budget_{stamp}.json")
    profile_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.parent.mkdir(parents=True, exist_ok=True)
    budget_out.parent.mkdir(parents=True, exist_ok=True)

    profile_command = build_profile_command(args, profile_out=profile_out)
    budget_command = build_budget_command(args, profile_out=profile_out, budget_out=budget_out)
    report: dict[str, Any] = {
        "ok": False,
        "status": "pending",
        "case": args.case,
        "profile_out": str(profile_out),
        "budget_out": str(budget_out) if budget_command else "",
        "profile_command": profile_command,
        "budget_command": budget_command or [],
        "allow_skip_on_error": bool(args.allow_skip_on_error),
    }

    if args.dry_run:
        report["ok"] = True
        report["status"] = "dry_run"
        _write_json(report_out, report)
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        return 0

    profile_completed = subprocess.run(profile_command, cwd=str(ROOT), check=False)
    report["profile_returncode"] = int(profile_completed.returncode)
    if profile_completed.returncode != 0:
        report["status"] = "skipped" if args.allow_skip_on_error else "failed"
        report["ok"] = bool(args.allow_skip_on_error)
        _write_json(report_out, report)
        print(f"[{ 'SKIP' if args.allow_skip_on_error else 'FAIL' }] long-window profile exited {profile_completed.returncode}")
        return 0 if args.allow_skip_on_error else int(profile_completed.returncode)

    if budget_command is not None:
        budget_completed = subprocess.run(budget_command, cwd=str(ROOT), check=False)
        report["budget_returncode"] = int(budget_completed.returncode)
        if budget_completed.returncode != 0:
            report["status"] = "budget_failed"
            report["ok"] = False
            _write_json(report_out, report)
            print(f"[FAIL] long-window budget exited {budget_completed.returncode}")
            return int(budget_completed.returncode)

    report["ok"] = True
    report["status"] = "completed"
    _write_json(report_out, report)
    print(f"[OK] long-window validation report: {report_out}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a structured long-window backtest profile and optional performance budget check."
    )
    parser.add_argument("--case", default="mysql_real", choices=["offline_smoke", "mysql_real"])
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20251231")
    parser.add_argument("--stock-limit", default="500")
    parser.add_argument("--stock-codes", default="")
    parser.add_argument("--offset-days", default="")
    parser.add_argument("--panel-source", default="", choices=["", "parquet"])
    parser.add_argument("--panel-path", default="")
    parser.add_argument("--strategy-module", default="")
    parser.add_argument("--build-topn-trade-segments", action="store_true")
    parser.add_argument("--no-build-topn-trade-segments", action="store_true")
    parser.add_argument("--jit", choices=["", "on", "off"], default="")
    parser.add_argument("--budget", default="", help="Optional performance budget JSON path.")
    parser.add_argument("--baseline", default="", help="Optional baseline JSON path for budget comparison.")
    parser.add_argument("--profile-out", default="", help="Optional profile JSON output path.")
    parser.add_argument("--budget-out", default="", help="Optional budget JSON output path.")
    parser.add_argument("--json-out", default="", help="Optional long-window report JSON output path.")
    parser.add_argument(
        "--allow-skip-on-error",
        action="store_true",
        help="Return success with status=skipped if the profile command fails, useful on machines without DB access.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write and print the planned command without running it.")
    return parser


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
