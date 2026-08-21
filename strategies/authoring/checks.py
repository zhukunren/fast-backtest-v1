"""Profiled strategy-source checks for authoring and promotion workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from strategies.code_security import validate_strategy_code_security


DRAFT_CHECK_PROFILE = "draft"
PROMOTION_CHECK_PROFILE = "promotion"
SKIPPED_CHECK_PROFILE = "skipped"
CHECK_PROFILES = (
    DRAFT_CHECK_PROFILE,
    PROMOTION_CHECK_PROFILE,
    SKIPPED_CHECK_PROFILE,
)


def normalize_check_profile(profile: str) -> str:
    value = str(profile or DRAFT_CHECK_PROFILE).strip().casefold()
    if value not in CHECK_PROFILES:
        raise ValueError(
            f"unsupported strategy check profile {profile!r}; expected one of {CHECK_PROFILES}"
        )
    return value


def _compile_source(source: str, *, path: Path) -> dict[str, Any]:
    try:
        compile(source, str(path), "exec", dont_inherit=True)
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {"ok": True, "status": "pass", "error_type": "", "error": ""}


def _check_source_security(source: str, *, path: Path) -> dict[str, Any]:
    try:
        validate_strategy_code_security(source, source=str(path))
    except Exception as exc:
        message = str(exc)
        if message.startswith("strategy_syntax_invalid:"):
            return {
                "ok": None,
                "status": "not_evaluated",
                "error_type": type(exc).__name__,
                "error": message,
                "reason": "source must compile before AST security screening can run",
            }
        return {
            "ok": False,
            "status": "fail",
            "error_type": type(exc).__name__,
            "error": message,
        }
    return {"ok": True, "status": "pass", "error_type": "", "error": ""}


def check_strategy_path(path: str | Path, *, profile: str = DRAFT_CHECK_PROFILE) -> dict[str, Any]:
    """Check one source file while keeping draft diagnostics separate from hard gates."""
    resolved = Path(path).resolve()
    selected = normalize_check_profile(profile)
    source = resolved.read_text(encoding="utf-8")
    compile_check = _compile_source(source, path=resolved)
    security_check = _check_source_security(source, path=resolved)

    skipped = selected == SKIPPED_CHECK_PROFILE
    if skipped:
        static_payload: dict[str, Any] = {
            "ok": True,
            "strategy_count": 0,
            "reports": [],
        }
        static_diagnostics_ok: bool | None = None
    else:
        from strategies.static_check import check_strategy_files

        static_payload = check_strategy_files(
            (resolved,),
            strict_authoring=selected == PROMOTION_CHECK_PROFILE,
        )
        static_diagnostics_ok = bool(static_payload.get("ok"))

    blocking_issues: list[dict[str, str]] = []
    if security_check.get("ok") is False:
        blocking_issues.append(
            {
                "severity": "error",
                "code": "strategy_security_failed",
                "message": str(security_check.get("error") or "strategy source security failed"),
            }
        )

    static_is_blocking = selected == PROMOTION_CHECK_PROFILE
    if static_is_blocking:
        for report in static_payload.get("reports", []) or []:
            if not isinstance(report, dict):
                continue
            for issue in report.get("issues", []) or []:
                if (
                    isinstance(issue, dict)
                    and str(issue.get("severity") or "").casefold() == "error"
                ):
                    blocking_issues.append(dict(issue))

    if selected == PROMOTION_CHECK_PROFILE and not compile_check["ok"]:
        blocking_issues.append(
            {
                "severity": "error",
                "code": "strategy_compile_failed",
                "message": str(compile_check.get("error") or "strategy source did not compile"),
            }
        )

    diagnostics_ok = bool(compile_check["ok"]) and security_check.get("ok") is not False
    if static_diagnostics_ok is False:
        diagnostics_ok = False

    payload = dict(static_payload)
    payload.update(
        {
            "ok": not blocking_issues,
            "blocking_ok": not blocking_issues,
            "diagnostics_ok": diagnostics_ok,
            "profile": selected,
            "strict_authoring": selected == PROMOTION_CHECK_PROFILE,
            "static_diagnostics_blocking": static_is_blocking,
            "skipped": skipped,
            "compile": compile_check,
            "security": security_check,
            "blocking_issues": blocking_issues,
            "promotion_ready": selected == PROMOTION_CHECK_PROFILE and not blocking_issues,
        }
    )
    return payload


__all__ = [
    "CHECK_PROFILES",
    "DRAFT_CHECK_PROFILE",
    "PROMOTION_CHECK_PROFILE",
    "SKIPPED_CHECK_PROFILE",
    "check_strategy_path",
    "normalize_check_profile",
]
