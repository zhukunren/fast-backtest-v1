"""Durable gate for Codex strategy authoring and tool workflows."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUTHORING_TESTS = tuple(
    sorted(
        (
            "tests/test_agent_strategy_backtest_tool.py",
            "tests/test_codex_plugin_bundle.py",
            "tests/test_codex_strategy_runtime.py",
            "tests/test_retired_panel_paths.py",
            "tests/test_strategy_authoring.py",
            "tests/test_strategy_authoring_llm_gate.py",
            "tests/test_strategy_independence_gate.py",
            "tests/test_strategy_static_check_entrypoints.py",
            "tests/test_strategy_template_contract.py",
            "tests/test_strategy_template_examples.py",
            "tests/test_web_strategy_panel_migration.py",
        ),
        key=str.casefold,
    )
)


@dataclass(frozen=True, slots=True)
class GateStep:
    name: str
    command: tuple[str, ...]
    display_command: str


def build_gate_steps(*, skip_offline_smoke: bool = False) -> tuple[GateStep, ...]:
    pytest_args = ("-B", "-m", "pytest", *AUTHORING_TESTS, "-q")
    steps = [
        GateStep(
            name="authoring_contracts",
            command=(sys.executable, *pytest_args),
            display_command=" ".join(("python", *pytest_args)),
        )
    ]
    if not skip_offline_smoke:
        offline_args = ("-B", "tests/test_offline_smoke.py")
        steps.append(
            GateStep(
                name="offline_backtest",
                command=(sys.executable, *offline_args),
                display_command=" ".join(("python", *offline_args)),
            )
        )
    return tuple(steps)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that current templates can still produce loadable V1 strategies "
            "and that the canonical Codex authoring tools remain callable."
        )
    )
    parser.add_argument(
        "--skip-offline-smoke",
        action="store_true",
        help="Skip the offline engine run when a parent gate runs it separately.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the fixed commands without running them.",
    )
    parser.add_argument("--json-out", default="", help="Optional structured report path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    steps = build_gate_steps(skip_offline_smoke=bool(args.skip_offline_smoke))
    report: dict[str, Any] = {
        "schema_version": 1,
        "ok": False,
        "status": "pending",
        "steps": [],
    }

    if bool(args.dry_run):
        for step in steps:
            print(step.display_command)
            report["steps"].append(
                {
                    "name": step.name,
                    "command": step.display_command,
                    "returncode": None,
                    "ok": None,
                }
            )
        report["ok"] = True
        report["status"] = "dry_run"
    else:
        for step in steps:
            print(f"[RUN] {step.name}: {step.display_command}")
            completed = subprocess.run(
                list(step.command),
                cwd=str(ROOT),
                check=False,
            )
            ok = completed.returncode == 0
            report["steps"].append(
                {
                    "name": step.name,
                    "command": step.display_command,
                    "returncode": int(completed.returncode),
                    "ok": ok,
                }
            )
            if not ok:
                report["status"] = "failed"
                break
        else:
            report["ok"] = True
            report["status"] = "completed"

    if args.json_out:
        _write_json(Path(args.json_out), report)
    if report["ok"]:
        print("[OK] LLM authoring gate passed")
        return 0
    print("[FAIL] LLM authoring gate failed")
    failed = next(
        (item for item in report["steps"] if item.get("ok") is False),
        None,
    )
    return int(failed["returncode"]) if failed is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
