"""Generate strategy validation protocols with rolling walk-forward cases."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _parse_yyyymmdd(raw: str) -> pd.Timestamp:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("date is required")
    if len(value) == 8 and value.isdigit():
        return pd.Timestamp(datetime.strptime(value, "%Y%m%d"))
    return pd.Timestamp(value)


def _format_date(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y%m%d")


def build_walk_forward_cases(
    *,
    start: str,
    end: str,
    train_days: int,
    test_days: int,
    step_days: int,
    base_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    start_ts = _parse_yyyymmdd(start)
    end_ts = _parse_yyyymmdd(end)
    if end_ts < start_ts:
        raise ValueError("--end must be >= --start")
    test_days = max(1, int(test_days))
    step_days = max(1, int(step_days))
    train_days = max(0, int(train_days))
    overrides = dict(base_overrides or {})

    cases: list[dict[str, Any]] = [
        {
            "name": "base_full",
            "description": "Full frozen validation window.",
            "start_time": _format_date(start_ts),
            "end_time": _format_date(end_ts),
            "gate_required": True,
            "overrides": {
                **overrides,
                "diagnostics.multi_start_enabled": True,
            },
        }
    ]

    cursor = start_ts + pd.Timedelta(days=train_days)
    idx = 1
    while cursor <= end_ts:
        window_end = min(end_ts, cursor + pd.Timedelta(days=test_days - 1))
        cases.append(
            {
                "name": f"wf_{idx:03d}",
                "description": f"Walk-forward OOS window {idx:03d}.",
                "start_time": _format_date(cursor),
                "end_time": _format_date(window_end),
                "gate_required": True,
                "overrides": overrides,
            }
        )
        idx += 1
        cursor = cursor + pd.Timedelta(days=step_days)

    if len(cases) == 1:
        raise ValueError("no walk-forward windows generated; reduce --train-days or extend --end")
    return cases


def build_protocol_payload(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "run.clear_outputs": True,
        "run.clear_all_tables": False,
        "run.plot_net_value": False,
        "run.compute_metrics": False,
        "run.profile_timing": False,
        "run.quiet": True,
        "diagnostics.enabled": True,
    }
    if args.panel_source:
        overrides["panel.source"] = str(args.panel_source)
    cases = build_walk_forward_cases(
        start=args.start,
        end=args.end,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        base_overrides=overrides,
    )
    return {
        "version": "walk-forward.v1",
        "description": "Generated walk-forward validation protocol for frozen strategy promotion.",
        "promotion_target": "strategy_library.validated",
        "defaults": {"overrides": overrides},
        "cases": cases,
        "gates": [
            {
                "name": "base_full_return_floor",
                "kind": "threshold",
                "case": "base_full",
                "path": "result.cum_return",
                "op": ">=",
                "value": float(args.min_full_return),
                "level": "hard",
                "description": "Full frozen window cumulative return floor.",
            },
            {
                "name": "base_full_drawdown_cap",
                "kind": "threshold",
                "case": "base_full",
                "path": "result.max_drawdown",
                "op": "<=",
                "value": float(args.max_full_drawdown),
                "level": "hard",
                "description": "Full frozen window drawdown cap.",
            },
            {
                "name": "walk_forward_return_pass_fraction",
                "kind": "min_fraction_pass",
                "case": "wf_*",
                "path": "result.cum_return",
                "op": ">=",
                "value": float(args.min_window_return),
                "min_fraction": float(args.min_pass_fraction),
                "level": "hard",
                "description": "Minimum fraction of OOS windows that must meet the return floor.",
            },
            {
                "name": "walk_forward_worst_drawdown_cap",
                "kind": "worst_case_threshold",
                "case": "wf_*",
                "path": "result.max_drawdown",
                "op": "<=",
                "value": float(args.max_window_drawdown),
                "level": "hard",
                "description": "Worst OOS window drawdown cap.",
            },
            {
                "name": "walk_forward_trade_count_pass_fraction",
                "kind": "min_fraction_pass",
                "case": "wf_*",
                "path": "result.actual_segment_trade_count",
                "op": ">=",
                "value": float(args.min_window_trades),
                "min_fraction": float(args.min_pass_fraction),
                "level": "warn",
                "description": "Minimum fraction of OOS windows with enough realized trades.",
            },
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a walk-forward strategy validation protocol JSON.")
    parser.add_argument("--start", required=True, help="Full validation start date, YYYYMMDD or ISO.")
    parser.add_argument("--end", required=True, help="Full validation end date, YYYYMMDD or ISO.")
    parser.add_argument("--train-days", type=int, default=250, help="Initial frozen train/selection span before OOS.")
    parser.add_argument("--test-days", type=int, default=60, help="Each OOS window length in calendar days.")
    parser.add_argument("--step-days", type=int, default=60, help="Calendar days between OOS window starts.")
    parser.add_argument("--min-full-return", type=float, default=0.0)
    parser.add_argument("--max-full-drawdown", type=float, default=0.30)
    parser.add_argument("--min-window-return", type=float, default=0.0)
    parser.add_argument("--max-window-drawdown", type=float, default=0.20)
    parser.add_argument("--min-window-trades", type=float, default=5.0)
    parser.add_argument("--min-pass-fraction", type=float, default=0.70)
    parser.add_argument(
        "--panel-source",
        default="",
        choices=["", "parquet"],
        help="Optional panel.source override; only parquet is supported.",
    )
    parser.add_argument("--out", default="", help="Output JSON path. Defaults to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_protocol_payload(args)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"[OK] walk-forward protocol written: {out}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
