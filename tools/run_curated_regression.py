"""用途:
- 按预先整理好的分组运行回归测试，减少每次手写长串 `pytest` 命令的成本。
- 当前支持 `unit`、`integration`、`e2e`、`all`，以及 llm authoring/research trust/debt/audit/maintainability/perf/gate 守卫组。

用法:
- 跑全部 curated 回归：`python tools/run_curated_regression.py --group all`
- 仅查看底层命令：`python tools/run_curated_regression.py --group integration --dry-run`
- 跑研究可信度回归：`python tools/run_curated_regression.py --group research_trust`
- 跑 LLM 策略生产能力门禁：`python tools/run_curated_regression.py --group llm_authoring`
- 跑债务守卫组：`python tools/run_curated_regression.py --group gate`"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GROUP_ORDER = ("unit", "integration", "e2e")
SPECIAL_GROUP_ORDER = (
    "debt",
    "llm_authoring",
    "research_trust",
    "audit",
    "maintainability",
    "perf",
    "hotpath",
)
SELECTABLE_GROUPS = (*GROUP_ORDER, "all", *SPECIAL_GROUP_ORDER, "gate")
CURATED_GROUPS: dict[str, tuple[str, ...]] = {
    "unit": (
        "tests/test_strategy_authoring_security.py",
        "tests/test_application_assess_summary.py",
        "tests/test_config_validation.py",
        "tests/test_tool_entrypoints.py",
        "tests/test_web_execution.py",
        "tests/test_state_machine.py",
        "tests/test_signal_precheck.py",
        "tests/test_signal_payload_extra_columns.py",
        "tests/test_segment_contracts.py",
        "tests/test_segment_order.py",
        "tests/test_trade_return_consistency.py",
        "tests/test_net_value_positions_next_open.py",
        "tests/test_net_value_positions_close_jit.py",
        "tests/test_broker_portfolio_allocator.py",
        "tests/test_broker_slippage_cost.py",
        "tests/test_engine_metrics_summary_only.py",
        "tests/test_engine_one_stock.py",
        "tests/test_engine_helpers_copy_on_write.py",
    ),
    "integration": (
        "tests/test_strategy_validation_service.py",
        "tests/test_web_exporter.py",
        "web/tests/test_library_regressions.py",
        "tests/test_import_boundaries.py",
        "tests/test_runtime_import_footprint.py",
        "tests/test_application_diagnostics.py",
        "tests/test_signal_processor_topn.py",
        "tests/test_trading_allocator_basic.py",
        "tests/test_topn_signal_segments.py",
        "tests/test_net_value_merge.py",
        "tests/test_broker_plan.py",
        "tests/test_broker_weighting_merge.py",
        "tests/test_broker_same_day_rebuy.py",
        "tests/test_broker_edge_regressions.py",
        "tests/test_topn_trade_segments_summary.py",
    ),
    "e2e": (
        "tests/test_offline_smoke.py",
        "tests/test_e2e_smoke_single_mode.py",
    ),
    "debt": (
        "tests/test_documentation_contracts.py",
        "tests/test_runtime_reset.py",
        "tests/test_panel_native_contracts.py",
        "tests/test_panel_native_index.py",
        "tests/test_engine_batch_support.py",
        "tests/test_web_progress_state.py",
        "tests/test_local_source.py",
        "tests/test_trade_calendar_tushare.py",
        "tests/test_mysql_support.py",
    ),
    "research_trust": (
        "tests/test_strategy_validation_service.py",
        "tests/test_run_protocol_artifacts.py",
        "tests/test_agent_strategy_backtest_tool.py",
        "tests/test_strategy_authoring_security.py",
        "tests/test_experiment_registry_tools.py",
        "tests/test_data_quality.py",
        "tests/test_tool_entrypoints.py",
        "tests/test_research_campaign.py",
        "tests/test_codex_strategy_runtime.py",
    ),
}


@dataclass(frozen=True, slots=True)
class CuratedCommand:
    command: tuple[str, ...]
    display_command: str
    env: dict[str, str]
    kind: str = "pytest"


def _selected_groups(group: str) -> tuple[str, ...]:
    if group == "all":
        return GROUP_ORDER
    if group == "gate":
        return SPECIAL_GROUP_ORDER
    return (group,)


def build_pytest_command(group: str) -> list[str]:
    marker = group if group in GROUP_ORDER else None
    args = [sys.executable, "-m", "pytest", *CURATED_GROUPS[group]]
    if marker:
        args.extend(["-m", marker])
    args.append("-q")
    return args


def _build_command(group: str) -> CuratedCommand:
    if group == "llm_authoring":
        command = (sys.executable, "tools/llm_authoring_gate.py")
        return CuratedCommand(
            command=command,
            display_command="python tools/llm_authoring_gate.py",
            env={},
            kind="command",
        )
    if group == "perf":
        command = (sys.executable, "tools/perf_smoke.py")
        return CuratedCommand(
            command=command,
            display_command="python tools/perf_smoke.py",
            env={},
            kind="command",
        )
    if group == "hotpath":
        command = (sys.executable, "tools/hotpath_smoke.py")
        return CuratedCommand(
            command=command,
            display_command="python tools/hotpath_smoke.py",
            env={},
            kind="command",
        )
    if group == "audit":
        command = (
            sys.executable,
            "tools/data_quality_audit.py",
            "--target",
            "offline-smoke",
            "--sample-size",
            "200",
        )
        return CuratedCommand(
            command=command,
            display_command="python tools/data_quality_audit.py --target offline-smoke --sample-size 200",
            env={},
            kind="command",
        )
    if group == "maintainability":
        command = (sys.executable, "tools/audit_maintainability.py")
        return CuratedCommand(
            command=command,
            display_command="python tools/audit_maintainability.py",
            env={},
            kind="command",
        )
    env: dict[str, str] = {}
    command = tuple(build_pytest_command(group))
    return CuratedCommand(
        command=command,
        display_command=format_pytest_command(group),
        env=env,
    )


def _format_env_prefix(env: dict[str, str]) -> str:
    if not env:
        return ""
    return " ".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].casefold())) + " "


def format_curated_command(group: str) -> str:
    spec = _build_command(group)
    return f"{_format_env_prefix(spec.env)}{spec.display_command}"


def build_group_command(group: str) -> list[str]:
    return list(_build_command(group).command)


def format_pytest_command(group: str) -> str:
    marker = group if group in GROUP_ORDER else None
    args = ["python", "-m", "pytest", *CURATED_GROUPS[group]]
    if marker:
        args.extend(["-m", marker])
    args.append("-q")
    return " ".join(args)


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按预设分组运行固定快速回归测试。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/run_curated_regression.py --group all\n"
            "  python tools/run_curated_regression.py --group integration --dry-run"
        ),
    )
    parser.add_argument(
        "--group",
        choices=SELECTABLE_GROUPS,
        default="all",
        help="要运行的回归分组。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印底层 pytest 命令，不实际执行。",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="可选：把结构化回归结果写到一个 JSON 文件。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    groups = _selected_groups(args.group)
    report: dict[str, Any] = {
        "ok": False,
        "status": "pending",
        "selected_group": args.group,
        "groups": [],
    }
    json_out = getattr(args, "json_out", None)

    if args.dry_run:
        for name in groups:
            command_text = format_curated_command(name)
            print(command_text)
            report["groups"].append(
                {
                    "name": name,
                    "command": command_text,
                    "env": _build_command(name).env,
                    "returncode": None,
                    "ok": None,
                    "dry_run": True,
                }
            )
        report["ok"] = True
        report["status"] = "dry_run"
        if json_out:
            _write_json(Path(json_out), report)
        return 0

    for name in groups:
        spec = _build_command(name)
        run_env = None
        if spec.env:
            run_env = os.environ.copy()
            run_env.update(spec.env)
        completed = subprocess.run(list(spec.command), cwd=str(ROOT), check=False, env=run_env)
        report["groups"].append(
            {
                "name": name,
                "command": format_curated_command(name),
                "env": spec.env,
                "kind": spec.kind,
                "returncode": int(completed.returncode),
                "ok": completed.returncode == 0,
                "dry_run": False,
            }
        )
        if completed.returncode != 0:
            report["ok"] = False
            report["status"] = "failed"
            report["failed_group"] = name
            if json_out:
                _write_json(Path(json_out), report)
            return int(completed.returncode)
    report["ok"] = True
    report["status"] = "completed"
    if json_out:
        _write_json(Path(json_out), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
