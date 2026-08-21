"""Build the immutable run protocol compiled plan."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.artifact_schema import (
    COMPILED_PLAN_SCHEMA_VERSION,
    artifact_schema_fields,
)
from core.panel_identity import panel_data_identity
from core.research_identity import strategy_source_identity

from ..runtime import RuntimeContext


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(
                value.items(),
                key=lambda item: str(item[0]).casefold(),
            )
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _stable_hash(payload: Any) -> str:
    text = json.dumps(
        _json_safe(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _runtime_universe_payload(ctx: RuntimeContext) -> dict[str, Any]:
    resolution = getattr(ctx, "universe_resolution", None)
    to_dict = getattr(resolution, "to_dict", None)
    if not callable(to_dict):
        raise RuntimeError("compiled plan requires a resolved universe")
    payload = to_dict()
    if not isinstance(payload, dict):
        raise TypeError("universe resolution must serialize to an object")
    return payload


def build_compiled_plan(
    ctx: RuntimeContext,
    meta: dict[str, Any],
) -> dict[str, Any]:
    config = meta.get("config") if isinstance(meta, dict) else {}
    strategy_spec = meta.get("strategy_meta") if isinstance(meta, dict) else None
    strategy_module = getattr(getattr(ctx.cfg, "strategy", None), "module", None)
    repo_root = Path(
        getattr(getattr(ctx.cfg, "paths", None), "repo_root", Path.cwd())
    )
    plan = {
        **artifact_schema_fields("compiled_plan", COMPILED_PLAN_SCHEMA_VERSION),
        "run_id": ctx.run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_module": strategy_module,
        "strategy_source": strategy_source_identity(
            str(strategy_module or ""),
            repo_root=repo_root,
        ),
        "strategy_spec": strategy_spec,
        "risk_control": meta.get("risk_control") if isinstance(meta, dict) else None,
        "execution_policy": (
            meta.get("execution_policy") if isinstance(meta, dict) else None
        ),
        "universe": _runtime_universe_payload(ctx),
        "window": {
            "start_time": getattr(
                getattr(ctx.cfg, "backtest", None),
                "start_time",
                None,
            ),
            "end_time": getattr(
                getattr(ctx.cfg, "backtest", None),
                "end_time",
                None,
            ),
            "offset_days": getattr(
                getattr(ctx.cfg, "backtest", None),
                "offset_days",
                None,
            ),
        },
        "data": {
            "panel": (
                _json_safe(config.get("panel", {}))
                if isinstance(config, dict)
                else {}
            ),
            "mysql": (
                _json_safe(config.get("mysql", {}))
                if isinstance(config, dict)
                else {}
            ),
            "data_manifest": (
                meta.get("data_manifest") if isinstance(meta, dict) else None
            ),
            "price_l2_cache": (
                meta.get("price_l2_cache") if isinstance(meta, dict) else None
            ),
            "panel_identity": panel_data_identity(ctx.cfg, verify_content=False),
        },
        "portfolio": (
            _json_safe(config.get("portfolio", {}))
            if isinstance(config, dict)
            else {}
        ),
        "transaction_cost": (
            _json_safe(config.get("transaction_cost", {}))
            if isinstance(config, dict)
            else {}
        ),
        "trade_constraints": (
            _json_safe(config.get("trade_constraints", {}))
            if isinstance(config, dict)
            else {}
        ),
    }
    plan["strategy_spec_hash"] = _stable_hash(strategy_spec or {})
    plan["strategy_source_hash"] = str(plan["strategy_source"].get("sha256") or "")
    plan["config_hash"] = _stable_hash(config or {})
    plan["compiled_plan_hash"] = _stable_hash(
        {key: value for key, value in plan.items() if key != "compiled_plan_hash"}
    )
    return plan


__all__ = ["build_compiled_plan"]
