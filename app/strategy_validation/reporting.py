from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import to_float
from .models import StrategyValidationProtocol
from .protocol import protocol_to_dict


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _fmt_metric(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    num = to_float(value)
    if num is None:
        return str(value)
    return f"{num:.4f}"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = report.get("summary") or {}
    lines.append("# Strategy Validation Report")
    lines.append("")
    lines.append(f"- Module: `{report.get('module')}`")
    lines.append(f"- Protocol: `{report.get('protocol_version')}`")
    lines.append(f"- Generated At: `{report.get('generated_at')}`")
    lines.append(f"- Decision: `{summary.get('decision')}`")
    lines.append(f"- Promotion Target: `{summary.get('promotion_target')}`")
    lines.append("")

    preflight = report.get("preflight") or {}
    lines.append("## Trust Preflight")
    lines.append("")
    lines.append(f"- Status: `{preflight.get('status') or 'missing'}`")
    lines.append(f"- Source: `{preflight.get('source_path') or '-'}`")
    for item in preflight.get("issues") or []:
        lines.append(f"- `{item.get('code')}`: {item.get('message')}")
    lines.append("")

    lines.append("## Cases")
    lines.append("")
    lines.append(
        "| Case | Status | Required | Contract | Cum Return | Max Drawdown | Trades | Win Rate | Issues |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in report.get("cases") or []:
        result = item.get("result") or {}
        contract_check = item.get("contract_check") or {}
        diag = item.get("diagnostics_snapshot") or {}
        issue_types = ",".join(str(x) for x in diag.get("issue_types") or []) or "-"
        lines.append(
            "| {name} | {status} | {required} | {contract} | {cum_return} | {max_drawdown} | {trade_cnt} | {win_rate} | {issues} |".format(
                name=item.get("name"),
                status=item.get("status"),
                required="yes" if item.get("gate_required") else "no",
                contract=contract_check.get("status") or "not_evaluated",
                cum_return=_fmt_metric(result.get("cum_return")),
                max_drawdown=_fmt_metric(result.get("max_drawdown")),
                trade_cnt=_fmt_metric(result.get("actual_segment_trade_count")),
                win_rate=_fmt_metric(result.get("actual_segment_win_rate")),
                issues=issue_types,
            )
        )
    lines.append("")

    lines.append("## Gates")
    lines.append("")
    lines.append("| Gate | Level | Status | Rule | Actual | Base | Relative Drop |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for item in report.get("gates") or []:
        if item.get("kind") == "threshold":
            rule = f"{item.get('display_path')} {item.get('op')} {_fmt_metric(item.get('expected'))}"
        else:
            rule = (
                f"{item.get('display_path')} relative_drop <= {_fmt_metric(item.get('expected'))} "
                f"vs {item.get('base_case')}"
            )
        lines.append(
            "| {name} | {level} | {status} | {rule} | {actual} | {base} | {relative_drop} |".format(
                name=item.get("name"),
                level=item.get("level"),
                status=item.get("status"),
                rule=rule,
                actual=_fmt_metric(item.get("actual")),
                base=_fmt_metric(item.get("base_value")),
                relative_drop=_fmt_metric(item.get("relative_drop")),
            )
        )
    lines.append("")

    hard_failures = summary.get("hard_gate_failures") or []
    case_failures = summary.get("required_case_failures") or []
    warn_failures = summary.get("warn_gate_failures") or []
    contract_failures = summary.get("runtime_contract_failures") or []
    trust_failures = summary.get("trust_failures") or []
    preflight_failures = summary.get("preflight_failures") or []
    lines.append("## Verdict")
    lines.append("")
    if summary.get("passed"):
        lines.append("All required cases completed and all hard gates passed.")
    else:
        if case_failures:
            lines.append("Required case failures:")
            for item in case_failures:
                lines.append(f"- `{item.get('case')}`: {item.get('error') or item.get('status')}")
        if hard_failures:
            lines.append("Hard gate failures:")
            for item in hard_failures:
                lines.append(
                    f"- `{item.get('name')}`: actual={_fmt_metric(item.get('actual'))}, "
                    f"expected={_fmt_metric(item.get('expected'))}"
                )
        if warn_failures:
            lines.append("Warn gate failures:")
            for item in warn_failures:
                lines.append(
                    f"- `{item.get('name')}`: actual={_fmt_metric(item.get('actual'))}, "
                    f"expected={_fmt_metric(item.get('expected'))}"
                )
        if preflight_failures:
            lines.append("Preflight failures:")
            for item in preflight_failures:
                lines.append(f"- `{item.get('code')}`: {item.get('message')}")
        if contract_failures:
            lines.append("Runtime contract failures:")
            for item in contract_failures:
                lines.append(f"- `{item.get('case')}`: {item.get('error')}")
        if trust_failures:
            lines.append("Trust failures:")
            for item in trust_failures:
                lines.append(f"- `{item.get('case')}`: {item.get('error')}")
    return "\n".join(lines).strip() + "\n"


def write_validation_report(
    *,
    root_dir: Path,
    protocol: StrategyValidationProtocol,
    module: str,
    report: dict[str, Any],
) -> None:
    protocol_resolved = {
        **protocol_to_dict(protocol),
        "module": module,
        "generated_at": report["generated_at"],
        "output_root": str(root_dir),
        "identity": report.get("identity", {}),
    }
    _json_write(root_dir / "protocol_resolved.json", protocol_resolved)
    _json_write(root_dir / "validation_report.json", report)
    (root_dir / "validation_report.md").write_text(render_markdown(report), encoding="utf-8")


__all__ = ["render_markdown", "write_validation_report"]
