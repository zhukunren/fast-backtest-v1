"""Build static, pre-run, and post-run research audit evidence."""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any

from core.artifact_schema import (
    RESEARCH_AUDIT_SCHEMA_VERSION,
    artifact_schema_fields,
)

from ..runtime import RuntimeContext
from .experiment_record import _run_result_metrics


def _strategy_source_path(ctx: RuntimeContext) -> Path | None:
    module_name = str(
        getattr(getattr(ctx.cfg, "strategy", None), "module", "") or ""
    ).strip()
    if not module_name:
        return None
    raw_path = Path(module_name)
    if raw_path.suffix.casefold() == ".py":
        candidates = [raw_path]
        if not raw_path.is_absolute():
            candidates = [
                Path(ctx.output_root) / raw_path,
                Path(getattr(ctx.cfg.paths, "repo_root", Path.cwd())) / raw_path,
            ]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved.is_file():
                return resolved
        return None
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        return None
    origin = getattr(spec, "origin", None)
    if not origin or origin in {"built-in", "frozen"}:
        return None
    path = Path(str(origin))
    return path if path.is_file() else None


def _static_strategy_audit(ctx: RuntimeContext) -> dict[str, Any] | None:
    engine = str(
        getattr(getattr(ctx.cfg, "portfolio", None), "engine", "") or ""
    ).strip().lower()
    if engine == "fixed_rebalance":
        return None
    source_path = _strategy_source_path(ctx)
    if source_path is None:
        return None
    try:
        source = source_path.read_text(encoding="utf-8")
        from strategies.static_check import check_strategy_source

        return check_strategy_source(source, path=str(source_path))
    except Exception as exc:
        return {
            "ok": False,
            "path": str(source_path),
            "issues": [
                {
                    "severity": "warning",
                    "code": "static_strategy_audit_failed",
                    "message": str(exc),
                }
            ],
        }


def _strategy_contract_audit(ctx: RuntimeContext) -> dict[str, Any] | None:
    engine = str(
        getattr(getattr(ctx.cfg, "portfolio", None), "engine", "") or ""
    ).strip().lower()
    if engine == "fixed_rebalance":
        return None
    strategy_spec = getattr(ctx, "strategy_spec", None)
    if strategy_spec is None:
        return None
    try:
        from strategies.spec import validate_strategy_contract

        return validate_strategy_contract(strategy_spec)
    except Exception as exc:
        return {
            "ok": False,
            "status": "warn",
            "issues": [
                {
                    "severity": "warning",
                    "code": "strategy_contract_audit_failed",
                    "message": str(exc),
                }
            ],
        }


def _normalize_symbol_set(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        raw_items = [item.strip() for item in values.split(",")]
    elif isinstance(values, (list, tuple, set)):
        raw_items = list(values)
    else:
        return set()
    return {
        str(item).strip().upper() for item in raw_items if str(item).strip()
    }


def _assumption_filters(assumption: dict[str, Any]) -> dict[str, Any]:
    filters = assumption.get("filters")
    return filters if isinstance(filters, dict) else {}


def _assumption_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return ()
    return tuple(str(item).strip() for item in raw_items if str(item).strip())


def _universe_mismatch_severity(assumption: dict[str, Any]) -> str:
    return "error" if bool(
        assumption.get("universe_locked")
        or assumption.get("locked")
        or assumption.get("require_exact_universe")
    ) else "warning"


def _strategy_universe_audit(ctx: RuntimeContext) -> dict[str, Any] | None:
    strategy_spec = getattr(ctx, "strategy_spec", None)
    assumption = getattr(strategy_spec, "universe_assumption", None)
    if not isinstance(assumption, dict) or not assumption:
        return None
    resolution = getattr(ctx, "universe_resolution", None)
    if resolution is None:
        return {
            "ok": True,
            "status": "warn",
            "issues": [
                {
                    "severity": "warning",
                    "code": "runtime_universe_missing",
                    "message": (
                        "Strategy declares universe_assumption, but runtime universe "
                        "resolution is unavailable."
                    ),
                }
            ],
        }

    issues: list[dict[str, Any]] = []
    severity = _universe_mismatch_severity(assumption)
    runtime_spec = getattr(resolution, "spec", None)
    runtime_codes = _normalize_symbol_set(getattr(resolution, "stock_code_list", ()) or ())

    expected_symbols = _normalize_symbol_set(assumption.get("symbols"))
    if expected_symbols and expected_symbols != runtime_codes:
        _append_issue(
            issues,
            severity=severity,
            code="universe_symbols_mismatch",
            message=(
                "Strategy universe_assumption.symbols does not match runtime universe: "
                f"expected={len(expected_symbols)} actual={len(runtime_codes)}"
            ),
        )

    expected_prefixes = _assumption_string_tuple(assumption.get("prefixes"))
    runtime_prefixes = tuple(getattr(runtime_spec, "prefixes", ()) or ())
    if expected_prefixes and expected_prefixes != runtime_prefixes:
        _append_issue(
            issues,
            severity=severity,
            code="universe_prefixes_mismatch",
            message=(
                "Strategy universe_assumption.prefixes does not match runtime universe "
                f"prefixes: expected={list(expected_prefixes)} "
                f"actual={list(runtime_prefixes)}"
            ),
        )

    filters = _assumption_filters(assumption)
    for key, runtime_attr in (
        ("exclude_bj", "exclude_bj"),
        ("exclude_st", "exclude_st"),
    ):
        if key not in filters:
            continue
        expected = bool(filters.get(key))
        actual = bool(getattr(runtime_spec, runtime_attr, False))
        if expected != actual:
            _append_issue(
                issues,
                severity=severity,
                code=f"universe_filter_{key}_mismatch",
                message=(
                    f"Strategy universe_assumption.filters.{key}={expected} "
                    f"does not match runtime universe {runtime_attr}={actual}."
                ),
            )

    if bool(assumption.get("universe_locked") or assumption.get("locked")) and not (
        expected_symbols
        or expected_prefixes
        or {"exclude_bj", "exclude_st"} & set(filters)
    ):
        _append_issue(
            issues,
            severity="warning",
            code="universe_locked_without_verifiable_fields",
            message=(
                "Strategy locks universe assumptions, but no supported "
                "symbols/prefixes/filter fields were declared."
            ),
        )

    return {
        "ok": not any(str(issue.get("severity")) == "error" for issue in issues),
        "status": _audit_status(issues),
        "issues": issues,
        "runtime_universe": resolution.to_dict()
        if callable(getattr(resolution, "to_dict", None))
        else None,
    }


def _append_issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    code: str,
    message: str,
) -> None:
    issues.append(
        {
            "severity": str(severity),
            "code": str(code),
            "message": str(message),
        }
    )


def _issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    error_count = sum(
        1
        for issue in issues
        if str(issue.get("severity") or "").casefold() == "error"
    )
    warning_count = sum(
        1
        for issue in issues
        if str(issue.get("severity") or "").casefold() == "warning"
    )
    return {
        "issue_count": len(issues),
        "warning_count": warning_count,
        "error_count": error_count,
    }


def _audit_status(issues: list[dict[str, Any]]) -> str:
    counts = _issue_counts(issues)
    if counts["error_count"]:
        return "fail"
    if counts["warning_count"]:
        return "warn"
    return "pass"


def _audit_section(
    name: str,
    issues: list[dict[str, Any]],
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = _issue_counts(issues)
    section = {
        "name": str(name),
        "status": _audit_status(issues),
        **counts,
        "issues": issues,
    }
    if isinstance(details, dict) and details:
        section["details"] = details
    return section


def _tag_issue(
    issue: dict[str, Any],
    *,
    phase: str,
    source: str,
) -> dict[str, Any]:
    out = dict(issue)
    out.setdefault("phase", phase)
    out.setdefault("source", source)
    return out


def build_research_audit(
    ctx: RuntimeContext,
    meta: dict[str, Any],
    result: object | None,
) -> dict[str, Any]:
    static_issues: list[dict[str, Any]] = []
    pre_run_issues: list[dict[str, Any]] = []
    post_run_issues: list[dict[str, Any]] = []
    static_report = _static_strategy_audit(ctx)
    if isinstance(static_report, dict):
        for issue in static_report.get("issues", []) or []:
            if isinstance(issue, dict):
                static_issues.append(
                    _tag_issue(
                        issue,
                        phase="static",
                        source="strategy_static_check",
                    )
                )
    strategy_contract_report = _strategy_contract_audit(ctx)
    if isinstance(strategy_contract_report, dict):
        for issue in strategy_contract_report.get("issues", []) or []:
            if isinstance(issue, dict):
                pre_run_issues.append(
                    _tag_issue(
                        issue,
                        phase="pre_run",
                        source="strategy_contract",
                    )
                )
    strategy_universe_report = _strategy_universe_audit(ctx)
    if isinstance(strategy_universe_report, dict):
        for issue in strategy_universe_report.get("issues", []) or []:
            if isinstance(issue, dict):
                pre_run_issues.append(
                    _tag_issue(
                        issue,
                        phase="pre_run",
                        source="strategy_universe",
                    )
                )

    git = meta.get("git") if isinstance(meta, dict) else None
    if isinstance(git, dict) and bool(git.get("dirty")):
        _append_issue(
            pre_run_issues,
            severity="warning",
            code="git_worktree_dirty",
            message="Run was produced from a dirty git worktree.",
        )

    tc = getattr(ctx.cfg, "transaction_cost", None)
    if tc is not None and not bool(getattr(tc, "enable", False)):
        _append_issue(
            pre_run_issues,
            severity="warning",
            code="transaction_cost_disabled",
            message="Transaction costs are disabled for this run.",
        )
    elif tc is not None:
        costs = [
            float(getattr(tc, "commission_rate", 0.0) or 0.0),
            float(getattr(tc, "min_commission", 0.0) or 0.0),
            float(getattr(tc, "stamp_duty_rate", 0.0) or 0.0),
            float(getattr(tc, "slippage_bps", 0.0) or 0.0),
        ]
        if all(value == 0.0 for value in costs):
            _append_issue(
                pre_run_issues,
                severity="warning",
                code="zero_transaction_cost",
                message="All transaction cost parameters are zero.",
            )

    trade_constraints = getattr(ctx.cfg, "trade_constraints", None)
    if trade_constraints is not None and not bool(
        getattr(trade_constraints, "require_next_bar", True)
    ):
        _append_issue(
            pre_run_issues,
            severity="warning",
            code="require_next_bar_disabled",
            message=(
                "require_next_bar is disabled; missing next-bar data can pass "
                "with warnings."
            ),
        )

    if "data_manifest" not in meta:
        _append_issue(
            pre_run_issues,
            severity="warning",
            code="data_manifest_missing",
            message="Run metadata does not include a data manifest.",
        )

    if not bool(
        getattr(getattr(ctx.cfg, "diagnostics", None), "multi_start_enabled", False)
    ):
        _append_issue(
            pre_run_issues,
            severity="warning",
            code="multi_start_robustness_disabled",
            message="Multi-start robustness diagnostics are disabled.",
        )

    diagnostics = meta.get("diagnostics") if isinstance(meta, dict) else None
    if not isinstance(diagnostics, dict):
        _append_issue(
            post_run_issues,
            severity="warning",
            code="diagnostics_missing",
            message="No diagnostics snapshot was attached to run metadata.",
        )
    elif int(diagnostics.get("issue_count") or 0) > 0:
        _append_issue(
            post_run_issues,
            severity="warning",
            code="diagnostics_reported_issues",
            message=(
                f"Diagnostics reported {int(diagnostics.get('issue_count') or 0)} "
                "issue(s)."
            ),
        )

    if result is not None and _run_result_metrics(result)["final_net_value"] is None:
        _append_issue(
            post_run_issues,
            severity="warning",
            code="final_net_value_missing",
            message="Run result did not produce final_net_value.",
        )

    pre_run_issues = [
        _tag_issue(issue, phase="pre_run", source="run_protocol")
        for issue in pre_run_issues
    ]
    post_run_issues = [
        _tag_issue(issue, phase="post_run", source="run_protocol")
        for issue in post_run_issues
    ]
    issues = [*static_issues, *pre_run_issues, *post_run_issues]
    counts = _issue_counts(issues)
    sections = {
        "static": _audit_section(
            "static",
            static_issues,
            details=(
                {"strategy_static_check": static_report}
                if isinstance(static_report, dict)
                else None
            ),
        ),
        "pre_run": _audit_section("pre_run", pre_run_issues),
        "post_run": _audit_section("post_run", post_run_issues),
    }
    return {
        **artifact_schema_fields("research_audit", RESEARCH_AUDIT_SCHEMA_VERSION),
        "run_id": ctx.run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": _audit_status(issues),
        **counts,
        "issues": issues,
        "sections": sections,
        "section_summary": [
            {
                "name": name,
                "status": section["status"],
                "issue_count": section["issue_count"],
                "warning_count": section["warning_count"],
                "error_count": section["error_count"],
            }
            for name, section in sorted(
                sections.items(),
                key=lambda item: item[0].casefold(),
            )
        ],
        "static_strategy_check": static_report,
        "strategy_contract_check": strategy_contract_report,
        "strategy_universe_check": strategy_universe_report,
    }


__all__ = ["build_research_audit"]
