"""Shared labels for synthetic risk-ledger fills."""
from __future__ import annotations


def resolve_risk_reason(
    *,
    before_ratio: float,
    after_ratio: float,
    risk_state: str,
    dd_state: str,
) -> str:
    normalized_risk_state = str(risk_state or "").strip().upper()
    normalized_dd_state = str(dd_state or "").strip().upper()
    if after_ratio > before_ratio + 1e-12:
        return "风控恢复"
    if (
        normalized_risk_state
        and normalized_risk_state != "NORMAL"
        and normalized_dd_state
        and normalized_dd_state != "NORMAL"
    ):
        return "净值/回撤风控触发"
    if normalized_dd_state and normalized_dd_state != "NORMAL":
        return "回撤风控触发"
    if normalized_risk_state and normalized_risk_state != "NORMAL":
        return "净值风控触发"
    return "风控调仓"


__all__ = ["resolve_risk_reason"]
