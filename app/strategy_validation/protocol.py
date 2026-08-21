from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .common import ensure_dict, serialize_dataclass, to_float
from .models import (
    StrategyValidationCase,
    StrategyValidationGate,
    StrategyValidationProtocol,
)


def _parse_stock_code_list(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    else:
        raise TypeError("validation case.stock_code_list must be a list or comma-separated string")
    codes = tuple(item for item in values if item)
    return codes or None


def load_protocol(path: str | Path) -> StrategyValidationProtocol:
    protocol_path = Path(path)
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("validation protocol root must be a JSON object")

    cases_raw = payload.get("cases") or []
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError("validation protocol requires non-empty cases")
    gates_raw = payload.get("gates") or []
    if not isinstance(gates_raw, list):
        raise TypeError("validation protocol gates must be a list")

    cases: list[StrategyValidationCase] = []
    seen_case_names: set[str] = set()
    for item in cases_raw:
        if not isinstance(item, Mapping):
            raise TypeError("validation case must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("validation case.name is required")
        if name in seen_case_names:
            raise ValueError(f"duplicate validation case: {name}")
        seen_case_names.add(name)
        cases.append(
            StrategyValidationCase(
                name=name,
                description=str(item.get("description") or "").strip(),
                start_time=str(item.get("start_time") or "").strip(),
                end_time=str(item.get("end_time") or "").strip(),
                gate_required=bool(item.get("gate_required", True)),
                overrides=ensure_dict(item.get("overrides"), label=f"case[{name}].overrides"),
                stock_code_list=_parse_stock_code_list(item.get("stock_code_list")),
            )
        )

    gates: list[StrategyValidationGate] = []
    for item in gates_raw:
        if not isinstance(item, Mapping):
            raise TypeError("validation gate must be an object")
        name = str(item.get("name") or "").strip()
        case_name = str(item.get("case") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        path_str = str(item.get("path") or "").strip()
        level = str(item.get("level") or "hard").strip().lower() or "hard"
        if not name:
            raise ValueError("validation gate.name is required")
        is_group_gate = case_name.endswith("*")
        if not is_group_gate and case_name not in seen_case_names:
            raise ValueError(f"validation gate case not found: {case_name}")
        if kind not in {"threshold", "max_relative_drop", "min_fraction_pass", "worst_case_threshold"}:
            raise ValueError(f"unsupported validation gate kind: {kind}")
        if level not in {"hard", "warn"}:
            raise ValueError(f"unsupported validation gate level: {level}")
        if not path_str:
            raise ValueError(f"validation gate path is required: {name}")
        raw_value = to_float(item.get("value"))
        if raw_value is None:
            raise ValueError(f"validation gate value must be numeric: {name}")
        base_case = str(item.get("base_case") or "").strip()
        if kind == "max_relative_drop" and base_case not in seen_case_names:
            raise ValueError(f"validation gate base_case not found: {name}")
        min_fraction = to_float(item.get("min_fraction", 1.0))
        if min_fraction is None:
            raise ValueError(f"validation gate min_fraction must be numeric: {name}")
        if kind == "min_fraction_pass" and not case_name.endswith("*"):
            raise ValueError(f"min_fraction_pass gate case must end with '*': {name}")
        if kind == "worst_case_threshold" and not case_name.endswith("*"):
            raise ValueError(f"worst_case_threshold gate case must end with '*': {name}")
        gates.append(
            StrategyValidationGate(
                name=name,
                kind=kind,
                case=case_name,
                path=path_str,
                value=raw_value,
                op=str(item.get("op") or ">=").strip(),
                base_case=base_case,
                level=level,
                description=str(item.get("description") or "").strip(),
                min_fraction=float(min_fraction),
            )
        )

    return StrategyValidationProtocol(
        version=str(payload.get("version") or "v1").strip() or "v1",
        description=str(payload.get("description") or "").strip(),
        promotion_target=str(payload.get("promotion_target") or "strategy_library.validated").strip()
        or "strategy_library.validated",
        defaults=ensure_dict(payload.get("defaults"), label="protocol.defaults"),
        cases=tuple(cases),
        gates=tuple(gates),
        research_governance=ensure_dict(
            payload.get("research_governance"),
            label="protocol.research_governance",
        ),
    )


def protocol_to_dict(protocol: StrategyValidationProtocol) -> dict[str, Any]:
    return {
        "version": protocol.version,
        "description": protocol.description,
        "promotion_target": protocol.promotion_target,
        "defaults": dict(protocol.defaults),
        "cases": [serialize_dataclass(item) for item in protocol.cases],
        "gates": [serialize_dataclass(item) for item in protocol.gates],
        "research_governance": dict(protocol.research_governance),
    }


__all__ = ["load_protocol", "protocol_to_dict"]
