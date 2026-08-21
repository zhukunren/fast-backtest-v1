from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    DIRECTION_BUY,
    SIGNAL_COL_DIRECTION,
)


def validate_sort_spec_columns(
    df: pd.DataFrame,
    sort_spec: object,
    *,
    candidate_mask: pd.Series | None = None,
) -> None:
    """Validate sort columns and finite real values on buy-candidate rows."""
    if sort_spec is None:
        raise ValueError("sort_spec 不能为空（需要至少一个排序字段）")

    if isinstance(sort_spec, str):
        tokens = [item.strip() for item in sort_spec.split(",")]
    elif isinstance(sort_spec, (list, tuple)):
        tokens = list(sort_spec)
    else:
        raise ValueError(f"sort_spec type not supported: {type(sort_spec).__name__}")

    cols: list[str] = []
    for tok in tokens:
        if tok is None:
            continue
        if isinstance(tok, (list, tuple)) and len(tok) == 2:
            col = str(tok[0]).strip()
        else:
            raw = str(tok).strip()
            if raw.startswith("-") or raw.startswith("+"):
                raw = raw[1:].strip()
            col = raw
        if not col or col.lower() == COL_TRADE_DATE:
            continue
        cols.append(col)

    if not cols:
        raise ValueError("sort_spec 不能为空（需要至少一个排序字段）")

    missing = [col for col in cols if col not in df.columns]
    if missing:
        raise ValueError(f"sort_spec columns missing: {missing}")

    if candidate_mask is not None:
        if not isinstance(candidate_mask, pd.Series):
            raise ValueError("candidate_mask must be a pandas Series")
        if not candidate_mask.index.equals(df.index):
            raise ValueError("candidate_mask index must exactly match the DataFrame index")
        if not pd.api.types.is_bool_dtype(candidate_mask.dtype):
            raise ValueError("candidate_mask must have boolean dtype")
        validated_candidate_mask = candidate_mask.fillna(False).astype(bool)
    elif SIGNAL_COL_DIRECTION in df.columns:
        validated_candidate_mask = (
            df[SIGNAL_COL_DIRECTION]
            .eq(DIRECTION_BUY)
            .fillna(False)
            .astype(bool)
        )
    else:
        validated_candidate_mask = pd.Series(True, index=df.index, dtype=bool)

    for col in cols:
        values = df.loc[validated_candidate_mask, col]
        if values.empty:
            continue
        if (
            pd.api.types.is_bool_dtype(values.dtype)
            or pd.api.types.is_complex_dtype(values.dtype)
            or not pd.api.types.is_numeric_dtype(values.dtype)
        ):
            raise ValueError(
                f"sort_spec column {col!r} must contain finite real numbers "
                f"on buy-candidate rows; bool and dtype {values.dtype} are not allowed"
            )

        numeric_values = values.to_numpy(dtype="float64", na_value=np.nan)
        invalid_mask = ~np.isfinite(numeric_values)
        if invalid_mask.any():
            sample_columns = [
                column
                for column in (COL_TRADE_DATE, COL_STOCK_CODE, col)
                if column in df.columns
            ]
            bad_rows = (
                df.loc[values.index[invalid_mask], sample_columns]
                .head(5)
                .to_dict(orient="records")
            )
            raise ValueError(
                f"sort_spec column {col!r} must contain finite real numbers "
                f"on buy-candidate rows; invalid examples: {bad_rows}"
            )


def _issue(*, severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _status_from_issues(issues: list[dict[str, Any]], *, strict: bool) -> str:
    if any(str(issue.get("severity", "")).casefold() == "error" for issue in issues):
        return "fail"
    if strict and any(str(issue.get("severity", "")).casefold() == "warning" for issue in issues):
        return "fail"
    if any(str(issue.get("severity", "")).casefold() == "warning" for issue in issues):
        return "warn"
    return "pass"


def validate_strategy_module(
    module_path: str,
    *,
    strict: bool = False,
    include_static: bool = True,
    allow_get_config: bool = False,
    behavioral_inputs=None,
    runtime=None,
) -> dict[str, Any]:
    """Validate one strategy without making ordinary loader imports behavioral."""
    from .strategy_loader import load_strategy_descriptor

    issues: list[dict[str, Any]] = []
    try:
        descriptor = load_strategy_descriptor(str(module_path).strip())
    except Exception as exc:
        issues.append(
            _issue(
                severity="error",
                code="strategy_load_failed",
                message=f"{type(exc).__name__}: {exc}",
            )
        )
        return {
            "ok": False,
            "status": "fail",
            "strict": bool(strict),
            "module": str(module_path),
            "strategy_spec": None,
            "risk_control": None,
            "advanced": None,
            "requirements": None,
            "static_check": None,
            "behavioral_check": None,
            "contract_check": {"ok": False, "status": "fail", "issues": issues},
            "issues": issues,
            "summary": {"error": 1, "warning": 0},
        }

    from .authoring.requirements import resolve_strategy_requirements
    from .spec import validate_strategy_contract

    contract_check = validate_strategy_contract(descriptor.spec, strict=strict)
    issues.extend(contract_check.get("issues") or [])
    requirements = (
        descriptor.requirements
        if runtime is None
        else resolve_strategy_requirements(
            spec=descriptor.spec,
            strategy=descriptor.strategy,
            advanced_spec=descriptor.advanced,
            runtime=runtime,
        )
    )
    issues.extend(item.to_dict() for item in requirements.issues)

    static_check = None
    if include_static:
        source_path = Path(descriptor.source_path) if descriptor.source_path else None
        if source_path is None or not source_path.is_file():
            issues.append(
                _issue(
                    severity="warning",
                    code="strategy_source_unavailable",
                    message="Strategy source path is unavailable; static source checks were skipped.",
                )
            )
        else:
            from .static_check import check_strategy_source

            static_check = check_strategy_source(
                source_path.read_text(encoding="utf-8"),
                path=str(source_path),
                allow_get_config=bool(allow_get_config),
            )
            issues.extend(static_check.get("issues") or [])

    behavioral_check = None
    if behavioral_inputs is not None:
        from .authoring.behavioral import probe_strategy_behavior

        behavioral_report = probe_strategy_behavior(descriptor, behavioral_inputs)
        behavioral_check = behavioral_report.to_dict()
        for check in behavioral_report.checks:
            if check.status == "fail":
                issues.append(
                    _issue(
                        severity="error",
                        code=f"behavioral_{check.code}",
                        message=check.message,
                    )
                )

    status = _status_from_issues(issues, strict=strict)
    summary = {
        "error": sum(str(issue.get("severity", "")).casefold() == "error" for issue in issues),
        "warning": sum(
            str(issue.get("severity", "")).casefold() == "warning" for issue in issues
        ),
    }
    return {
        "ok": status != "fail",
        "status": status,
        "strict": bool(strict),
        "module": str(module_path),
        "strategy_spec": descriptor.spec.to_dict(),
        "risk_control": descriptor.risk_control.to_dict(),
        "advanced": descriptor.advanced.to_dict(),
        "requirements": requirements.to_dict(),
        "static_check": static_check,
        "behavioral_check": behavioral_check,
        "contract_check": contract_check,
        "issues": issues,
        "summary": summary,
    }

__all__ = [
    "validate_sort_spec_columns",
    "validate_strategy_module",
]
